"""End-to-end tests for the deterministic library engine."""

import statistics
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from fim.engine import (
    FinalReport,
    GenerationalBackend,
    LinealBackend,
    RunResult,
    SequentialAdvancer,
    ThreadedAdvancer,
    VectorizedAdvancer,
    _build_replica_lane,
    build_engine_backend,
    fim,
    replicate_summary,
    report_for_state,
    run_batch,
)
from fim.model.allele import MINTED_ID_START, AlleleId
from fim.model.locus import LocusSpec, finite_allele_capacity
from fim.model.operators import _population_sizes
from fim.model.params import ConvergenceCombinator, SimulationParams
from fim.model.state import ModelState
from fim.persistence.store import InMemoryTrajectoryStore


def _clock() -> datetime:
    """Return a fixed manifest timestamp."""
    return datetime(2026, 8, 14, 20, 0, tzinfo=UTC)


def _tiny_config() -> dict[str, object]:
    """Return the `tiny_params` fixture's configuration as a plain mapping.

    For tests that construct several batch variants and would otherwise
    need to re-derive `tiny_params.to_dict()` from an injected fixture
    argument they don't otherwise use.
    """
    return {
        "N": 20,
        "m": 0.1,
        "mu": 0.01,
        "d": 2,
        "seed": 20260814,
        "loci": [{"locus_id": 1, "length": 200}],
        "convergence_window": 4,
        "convergence_tolerance": 1.0,
        "max_generations": 10,
    }


def _run(params: SimulationParams) -> RunResult:
    """Run one scalar configuration and narrow the output type."""
    result = fim(
        params.N,
        params.m,
        params.mu,
        params.d,
        params=params,
        clock=_clock,
    )
    assert isinstance(result, RunResult)
    return result


def test_seeded_run_is_bit_reproducible(tiny_params: SimulationParams) -> None:
    """Two runs persist exactly the same rows and final report."""
    first = _run(tiny_params)
    second = _run(tiny_params)

    assert list(first.store.read(first.run_id)) == list(
        second.store.read(second.run_id)
    )
    assert first.report == second.report
    assert first.final_state == second.final_state


def test_live_and_recomputed_reports_match(
    tiny_params: SimulationParams,
) -> None:
    """Statistics remain independent of the engine run loop."""
    result = _run(tiny_params)

    recomputed = report_for_state(
        result.final_state,
        tiny_params,
        run_id=result.run_id,
        converged=result.report["converged"],
        reason=result.report["reason"],
    )

    assert recomputed == result.report


def test_cap_is_a_valid_nonconverged_result(
    tiny_params: SimulationParams,
) -> None:
    """An exact-match tolerance a live drift process cannot satisfy hits the cap.

    `convergence_window=2` is the smallest legal window that still fits
    `max_generations=2 + 1` (validation rejects anything larger — see the
    `convergence_window` case in `test/model/test_params.py::
    test_post_init_validation_covers_all_scalar_contracts`);
    `convergence_tolerance=0.0` requires the two half-window means to
    match exactly, which a real drifting `D` trajectory essentially never
    does in two generations.
    """
    params = SimulationParams.from_mapping(
        {
            **tiny_params.to_dict(),
            "convergence_window": 2,
            "convergence_tolerance": 0.0,
            "max_generations": 2,
        }
    )

    result = _run(params)

    assert not result.report["converged"]
    assert result.report["reason"] == "hit the cap"
    assert result.report["generation"] == 2


def test_replicates_are_independently_reproducible(
    tiny_params: SimulationParams,
) -> None:
    """Batching derives stable per-replicate seeds without changing scalar runs."""
    scalar = _run(tiny_params)
    batched_params = SimulationParams.from_mapping(
        {**tiny_params.to_dict(), "n_replicates": 2}
    )
    store = InMemoryTrajectoryStore()

    output = fim(
        batched_params.N,
        batched_params.m,
        batched_params.mu,
        batched_params.d,
        params=batched_params,
        store=store,
        clock=_clock,
    )

    assert isinstance(output, tuple)
    assert len(output) == 2
    assert output[0].final_state == scalar.final_state
    assert output[0].params.seed == tiny_params.seed
    assert output[1].params.seed == tiny_params.seed + 1


def test_public_signature_mismatches_are_reported(
    tiny_params: SimulationParams,
) -> None:
    """The legacy positional arguments must agree with the parameter bag."""
    cases = (
        (21, tiny_params.m, tiny_params.mu, tiny_params.d, "N"),
        (tiny_params.N, 0.2, tiny_params.mu, tiny_params.d, "m"),
        (tiny_params.N, tiny_params.m, 0.2, tiny_params.d, "mu"),
        (tiny_params.N, tiny_params.m, tiny_params.mu, 3, "d"),
    )
    for population_size, migration, mutation, demes, message in cases:
        with pytest.raises(ValueError, match=message):
            fim(
                population_size,
                migration,
                mutation,
                demes,
                params=tiny_params,
                clock=_clock,
            )


def test_batch_run_uses_explicit_run_id_suffixes(
    tiny_params: SimulationParams,
) -> None:
    """Caller-provided batch IDs receive deterministic one-based suffixes."""
    params = SimulationParams.from_mapping({**tiny_params.to_dict(), "n_replicates": 2})
    output = fim(
        params.N,
        params.m,
        params.mu,
        params.d,
        params=params,
        run_id="batch",
        clock=_clock,
    )
    assert isinstance(output, tuple)
    assert [result.run_id for result in output] == ["batch-r001", "batch-r002"]


def test_replicate_tolerance_unset_is_unaffected_by_the_adaptive_machinery(
    tiny_params: SimulationParams,
) -> None:
    """Omitting `replicate_tolerance` keeps the fixed-count batch loop exact."""
    params = SimulationParams.from_mapping({**tiny_params.to_dict(), "n_replicates": 4})
    assert params.replicate_tolerance is None
    output = fim(params.N, params.m, params.mu, params.d, params=params, clock=_clock)
    assert isinstance(output, tuple)
    assert len(output) == 4


def test_replicate_tolerance_can_stop_before_the_cap() -> None:
    """A generous tolerance stops as soon as `replicate_minimum` is reached."""
    params = SimulationParams.from_mapping(
        {
            **_tiny_config(),
            "n_replicates": 10,
            "replicate_minimum": 3,
            # Any statistic this project reports is bounded in [0, 1], so a
            # tolerance this large is always satisfied once the minimum
            # sample is available — the stop is deterministic, not lucky.
            "replicate_tolerance": 1000.0,
        }
    )
    output = fim(params.N, params.m, params.mu, params.d, params=params, clock=_clock)
    assert isinstance(output, tuple)
    assert len(output) == 3


def test_replicate_minimum_exceeding_n_replicates_is_rejected() -> None:
    """`replicate_minimum` unreachable within `n_replicates` fails at construction.

    Regression test: `replicate_minimum=100` with `n_replicates=3`
    used to be accepted and silently fall back to the `n_replicates`
    hard cap every time (adaptive stopping could never even be
    evaluated, let alone fire) — a config that can never do what it
    describes is now rejected up front instead of running to completion
    and reporting an ordinary-looking "batch ended" result.
    `test_replicate_tolerance_never_stops_on_a_permanently_undefined_
    statistic` covers the *legal* way a batch falls back to the
    `n_replicates` cap (a criterion that is evaluable but never
    satisfied, rather than one that is never evaluable at all).
    """
    with pytest.raises(
        ValueError, match="replicate_minimum cannot exceed n_replicates"
    ):
        SimulationParams.from_mapping(
            {
                **_tiny_config(),
                "n_replicates": 3,
                "replicate_minimum": 100,
                "replicate_tolerance": 0.0,
            }
        )


def test_replicate_tolerance_never_stops_on_a_permanently_undefined_statistic() -> None:
    """A batch watching only an always-undefined `G_ST` runs to the full cap.

    Regression test: every replicate here is fully monomorphic at
    its one locus, so `G_ST` is undefined for every one of them and its
    stopping-criterion window never fills. The batch correctly falls back
    to the `n_replicates` cap rather than the prior behavior, where
    substituting `0.0` for every undefined replicate produced a constant
    zero history that satisfied an exact `replicate_tolerance=0.0`
    immediately at `replicate_minimum` — a fabricated "convergence" the
    run's actual (complete lack of) data never supported.
    """
    params = SimulationParams(
        N=10,
        m=0.0,
        mu=0.0,
        d=2,
        seed=7,
        loci=(LocusSpec(1, 100),),
        convergence_statistic="G_ST",
        convergence_window=2,
        convergence_tolerance=0.0,
        max_generations=2,
        n_replicates=5,
        replicate_minimum=2,
        replicate_tolerance=0.0,
        initial_frequencies=(
            ({AlleleId(0): 1.0},),
            ({AlleleId(0): 1.0},),
        ),
    )
    output = fim(params.N, params.m, params.mu, params.d, params=params, clock=_clock)
    assert isinstance(output, tuple)
    assert len(output) == 5
    assert all(result.report["G_ST"] is None for result in output)
    assert "G_ST" not in replicate_summary(output)


def test_replicate_summary_reports_a_confidence_interval_per_statistic(
    tiny_params: SimulationParams,
) -> None:
    """The batch summary covers every statistic with at least two samples."""
    params = SimulationParams.from_mapping({**tiny_params.to_dict(), "n_replicates": 5})
    output = fim(params.N, params.m, params.mu, params.d, params=params, clock=_clock)
    assert isinstance(output, tuple)

    summary = replicate_summary(output)

    assert set(summary) == {"D", "G_ST", "E_ST", "K_ST", "H_S", "H_T", "H_ST"}
    assert summary["D"]["sample_count"] == 5
    assert summary["D"]["low"] <= summary["D"]["mean"] <= summary["D"]["high"]
    assert summary["D"]["confidence"] == 0.95


def test_replicate_summary_covers_every_numeric_final_report_key(
    tiny_params: SimulationParams,
) -> None:
    """Every numeric `FinalReport` field has a `replicate_summary` entry.

    Regression test for S4: `H_ST` was computed into every replicate's
    `FinalReport` and printed in every `report.json`, but silently
    absent from the batch-level summary, with no test asserting the
    two stay in correspondence. Parses `FinalReport`'s own field
    annotations at test time (excluding the non-statistic identity and
    metadata fields) so a future statistic added to `FinalReport` and
    never propagated here fails this test immediately, rather than
    only being noticed by inspection.
    """
    non_statistic_fields = {
        "run_id",
        "generation",
        "converged",
        "converged_on",
        "reason",
    }
    numeric_fields = set(FinalReport.__annotations__) - non_statistic_fields
    params = SimulationParams.from_mapping({**tiny_params.to_dict(), "n_replicates": 5})
    output = fim(params.N, params.m, params.mu, params.d, params=params, clock=_clock)
    assert isinstance(output, tuple)

    summary = replicate_summary(output)

    assert set(summary) == numeric_fields


def test_sequential_batch_derives_valid_seeds_at_the_seed_zero_boundary() -> None:
    """`seed=0`, the lowest legal value, still derives valid replicate seeds.

    Regression boundary test: `seed >= 0` is the whole legal
    range (`fim.model.params.SimulationParams.__post_init__`), so
    `seed=0` is the boundary most likely to expose an off-by-one in the
    `seed + replicate_index` derivation. Every derived replicate seed
    must land in `0 .. n_replicates - 1` with no gap or reuse.
    """
    params = SimulationParams.from_mapping(
        {**_tiny_config(), "seed": 0, "n_replicates": 4}
    )
    output = fim(params.N, params.m, params.mu, params.d, params=params, clock=_clock)
    assert isinstance(output, tuple)
    assert [result.params.seed for result in output] == [0, 1, 2, 3]


def test_parallel_batch_derives_valid_seeds_at_the_seed_zero_boundary() -> None:
    """The `max_workers` batch path derives the same boundary seeds.

    Parallel replicates are constructed identically to the sequential
    loop (`fim.engine._run_batch_parallel`) but cross a process
    boundary, so this is checked independently rather than assumed to
    follow from the sequential case above.
    """
    params = SimulationParams.from_mapping(
        {**_tiny_config(), "seed": 0, "n_replicates": 4}
    )
    output = fim(params.N, params.m, params.mu, params.d, params=params, max_workers=2)
    assert isinstance(output, tuple)
    assert sorted(result.params.seed for result in output) == [0, 1, 2, 3]


def test_max_workers_produces_the_same_replicates_as_sequential_execution() -> None:
    """Parallel batching changes nothing about the computed results."""
    params = SimulationParams.from_mapping({**_tiny_config(), "n_replicates": 4})

    sequential = fim(params.N, params.m, params.mu, params.d, params=params)
    parallel = fim(
        params.N, params.m, params.mu, params.d, params=params, max_workers=2
    )

    assert isinstance(sequential, tuple)
    assert isinstance(parallel, tuple)
    assert len(sequential) == len(parallel) == 4
    for sequential_result, parallel_result in zip(sequential, parallel, strict=True):
        assert sequential_result.final_state == parallel_result.final_state
        assert sequential_result.report == parallel_result.report
        assert sequential_result.run_id == parallel_result.run_id


def test_store_factory_gives_every_sequential_replicate_its_own_store() -> None:
    """`store_factory` also works for the ordinary sequential batch loop."""
    params = SimulationParams.from_mapping({**_tiny_config(), "n_replicates": 2})
    output = fim(
        params.N,
        params.m,
        params.mu,
        params.d,
        params=params,
        store_factory=_in_memory_store_factory,
    )
    assert isinstance(output, tuple)
    assert output[0].store is not output[1].store


def test_store_and_store_factory_are_mutually_exclusive(
    tiny_params: SimulationParams,
) -> None:
    """Only one trajectory-store strategy may be given at a time."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        fim(
            tiny_params.N,
            tiny_params.m,
            tiny_params.mu,
            tiny_params.d,
            params=tiny_params,
            store=InMemoryTrajectoryStore(),
            store_factory=_in_memory_store_factory,
        )


def test_max_workers_rejects_a_shared_store() -> None:
    """A single store instance cannot cross worker-process boundaries."""
    params = SimulationParams.from_mapping({**_tiny_config(), "n_replicates": 2})
    with pytest.raises(ValueError, match="max_workers requires store=None"):
        fim(
            params.N,
            params.m,
            params.mu,
            params.d,
            params=params,
            store=InMemoryTrajectoryStore(),
            max_workers=2,
        )


def test_max_workers_rejects_a_non_positive_count() -> None:
    """`max_workers` must name at least one worker."""
    params = SimulationParams.from_mapping({**_tiny_config(), "n_replicates": 2})
    with pytest.raises(ValueError, match="max_workers must be at least 1"):
        fim(params.N, params.m, params.mu, params.d, params=params, max_workers=0)


def test_max_workers_rejects_an_unpicklable_clock() -> None:
    """A closure `clock` fails at the call site, not deep in worker spawn.

    Regression test: the prior behavior let an unpicklable
    `clock` reach `ProcessPoolExecutor`, where it failed as raw pickling
    noise from inside worker-process spawn machinery.
    """
    params = SimulationParams.from_mapping({**_tiny_config(), "n_replicates": 2})
    with pytest.raises(ValueError, match="clock must be picklable"):
        fim(
            params.N,
            params.m,
            params.mu,
            params.d,
            params=params,
            max_workers=2,
            # Deliberately unpicklable: the lambda closure is the point of
            # this test, not `_clock` itself (which is picklable on its own).
            clock=lambda: _clock(),  # noqa: PLW0108
        )


def test_max_workers_rejects_an_unpicklable_store_factory() -> None:
    """A closure `store_factory` fails at the call site, not in a worker.

    Regression test, the `store_factory` counterpart to
    `test_max_workers_rejects_an_unpicklable_clock` above.
    """
    params = SimulationParams.from_mapping({**_tiny_config(), "n_replicates": 2})
    with pytest.raises(ValueError, match="store_factory must be picklable"):
        fim(
            params.N,
            params.m,
            params.mu,
            params.d,
            params=params,
            max_workers=2,
            store_factory=lambda _run_id: InMemoryTrajectoryStore(),
        )


def test_max_workers_respects_adaptive_stopping_in_batches() -> None:
    """Batched parallel replicates still honor `replicate_tolerance`.

    A batch can overshoot the exact minimal replicate count by at most
    ``max_workers - 1``, since the stopping decision is only applied once
    a whole concurrent batch has completed.
    """
    params = SimulationParams.from_mapping(
        {
            **_tiny_config(),
            "n_replicates": 10,
            "replicate_minimum": 3,
            "replicate_tolerance": 1000.0,
        }
    )
    output = fim(params.N, params.m, params.mu, params.d, params=params, max_workers=2)
    assert isinstance(output, tuple)
    assert 3 <= len(output) <= 4


def _in_memory_store_factory(run_id: str) -> InMemoryTrajectoryStore:
    """Module-level `store_factory` — a worker process must be able to
    pickle a reference to it, which a closure or lambda cannot survive."""
    del run_id  # unused; signature-compatible with `fim`'s `store_factory`
    return InMemoryTrajectoryStore()


def test_max_workers_uses_store_factory_per_replicate() -> None:
    """Each worker gets its own store, built by `store_factory` in-process."""
    params = SimulationParams.from_mapping({**_tiny_config(), "n_replicates": 2})
    output = fim(
        params.N,
        params.m,
        params.mu,
        params.d,
        params=params,
        max_workers=2,
        store_factory=_in_memory_store_factory,
    )
    assert isinstance(output, tuple)
    assert output[0].store is not output[1].store
    assert list(output[0].store.read(output[0].run_id))
    assert list(output[1].store.read(output[1].run_id))


def test_replicate_summary_requires_at_least_two_results(
    tiny_params: SimulationParams,
) -> None:
    """A single result has no interval to compute."""
    with pytest.raises(ValueError, match="at least two"):
        replicate_summary(())
    with pytest.raises(ValueError, match="at least two"):
        replicate_summary((_run(tiny_params),))


def test_naive_manifest_clock_is_rejected(tiny_params: SimulationParams) -> None:
    """Manifest timestamps require an explicit timezone."""
    with pytest.raises(ValueError, match="timezone-aware"):
        fim(
            tiny_params.N,
            tiny_params.m,
            tiny_params.mu,
            tiny_params.d,
            params=tiny_params,
            clock=lambda: _clock().replace(tzinfo=None),
        )


def test_g_st_convergence_falls_back_to_the_cap_at_total_fixation() -> None:
    """A run whose only watched statistic never becomes defined hits the cap.

    Regression test: `G_ST` is undefined every generation here (the
    single locus is fixed for the same allele in both demes throughout,
    since `mu=0.0`), so its trailing window never fills and the criterion
    can never report stability — there is no data to judge stability
    from. The run correctly falls back to `max_generations` rather than
    reporting a spurious immediate "convergence" from a padded history of
    fabricated zeros, which is what the prior `0.0`-substitution behavior
    produced regardless of what the run was actually doing.
    """
    params = SimulationParams(
        N=10,
        m=0.0,
        mu=0.0,
        d=2,
        seed=7,
        loci=(LocusSpec(1, 100),),
        convergence_statistic="G_ST",
        convergence_window=2,
        convergence_tolerance=0.0,
        max_generations=2,
        initial_frequencies=(
            ({AlleleId(0): 1.0},),
            ({AlleleId(0): 1.0},),
        ),
    )
    result = fim(
        params.N,
        params.m,
        params.mu,
        params.d,
        params=params,
        clock=_clock,
    )
    assert isinstance(result, RunResult)
    assert not result.report["converged"]
    assert result.report["reason"] == "hit the cap"
    assert result.report["generation"] == 2
    assert result.report["G_ST"] is None
    assert result.convergence_histories["G_ST"] == ()


def test_adaptive_g_st_batch_survives_partial_monomorphism() -> None:
    """A replicate with one monomorphic and one polymorphic locus never crashes.

    Regression test for the multi-locus G_ST averaging defect: with two loci, one fixed
    for the same allele in every deme (undefined at that locus alone) and
    one polymorphic, the *replicate's* G_ST used to come out `None` (the
    old rule voided the whole multi-locus average on any single
    undefined locus) while the replicate's averaged H_T was still
    nonzero (the polymorphic locus's own contribution) — so the adaptive
    replicate-batch monitor's old "rescue only a fully-`H_T == 0`
    replicate" check missed this case and raised
    "G_ST is undefined for replicate ...". `G_ST` is now defined here (it
    drops the one undefined locus and averages the other), so this
    reaches neither the old crash nor even the drop path — it simply
    works, end to end through the adaptive stopping monitor.
    """
    params = SimulationParams(
        N=10,
        m=0.0,
        mu=0.0,
        d=2,
        seed=7,
        loci=(LocusSpec(1, 100), LocusSpec(2, 100)),
        convergence_statistic="G_ST",
        # convergence_window must fit within max_generations + 1;
        # this test is about replicate-batch behavior, not within-run
        # convergence, so the minimum legal window keeps max_generations=1
        # valid without changing what the test actually verifies.
        convergence_window=2,
        max_generations=1,
        n_replicates=3,
        replicate_minimum=2,
        replicate_tolerance=1000.0,
        initial_frequencies=(
            ({AlleleId(0): 1.0}, {AlleleId(0): 0.5, AlleleId(1): 0.5}),
            ({AlleleId(0): 1.0}, {AlleleId(0): 0.5, AlleleId(1): 0.5}),
        ),
    )
    output = fim(params.N, params.m, params.mu, params.d, params=params)
    assert isinstance(output, tuple)
    assert len(output) == 2
    for result in output:
        assert result.report["G_ST"] is not None
        assert result.report["H_T"] > 0.0


def test_adaptive_batch_drops_replicates_where_g_st_is_undefined() -> None:
    """A replicate-batch monitor never raises or fabricates a value for G_ST.

    Every replicate here is fully monomorphic at its one locus (`G_ST`
    undefined for all three), so its trailing window never fills and the
    batch runs to the full `n_replicates` cap rather than ever declaring
    a fabricated early stop — mirroring
    `test_g_st_convergence_falls_back_to_the_cap_at_total_fixation`'s
    single-run case, but through the replicate-batch monitor instead of
    the within-run one. `replicate_summary` then omits `G_ST` entirely
    (zero defined samples), while every always-defined statistic is
    unaffected.
    """
    params = SimulationParams(
        N=10,
        m=0.0,
        mu=0.0,
        d=2,
        seed=7,
        loci=(LocusSpec(1, 100),),
        convergence_statistic="G_ST",
        # convergence_window must fit within max_generations + 1;
        # this test is about replicate-batch behavior, not within-run
        # convergence, so the minimum legal window keeps max_generations=1
        # valid without changing what the test actually verifies.
        convergence_window=2,
        max_generations=1,
        n_replicates=3,
        replicate_minimum=2,
        replicate_tolerance=1000.0,
        initial_frequencies=(
            ({AlleleId(0): 1.0},),
            ({AlleleId(0): 1.0},),
        ),
    )
    output = fim(params.N, params.m, params.mu, params.d, params=params)
    assert isinstance(output, tuple)
    assert len(output) == 3
    assert all(result.report["G_ST"] is None for result in output)

    summary = replicate_summary(output)
    assert "G_ST" not in summary
    assert summary["D"]["sample_count"] == 3


def test_single_statistic_report_shape_is_the_multi_statistic_special_case(
    tiny_params: SimulationParams,
) -> None:
    """Watching one statistic still reports a bare string, not a one-item list.

    Design §9: the ordinary single-statistic run is the several-statistic
    combinator's one-element special case, not a differently shaped result.
    """
    result = _run(tiny_params)

    assert result.report["converged_on"] == "D"
    assert isinstance(result.report["converged_on"], str)
    assert set(result.convergence_histories) == {"D"}
    assert result.convergence_histories["D"] == result.convergence_history


def test_multi_statistic_run_watches_and_reports_every_statistic() -> None:
    """Watching several statistics is reproducible and reports every history."""
    params = SimulationParams(
        N=25,
        m=0.15,
        mu=0.03,
        d=3,
        seed=20260800,
        loci=(LocusSpec(1, 100),),
        convergence_statistic=("D", "G_ST"),
        convergence_combinator="all",
        convergence_window=6,
        convergence_tolerance=0.02,
        max_generations=60,
    )

    first = _run(params)
    second = _run(params)

    assert list(first.store.read(first.run_id)) == list(
        second.store.read(second.run_id)
    )
    assert first.report == second.report
    assert first.report["converged_on"] == ["D", "G_ST"]
    assert set(first.convergence_histories) == {"D", "G_ST"}
    assert (
        len(first.convergence_histories["D"])
        == len(first.convergence_histories["G_ST"])
        == len(first.convergence_generations)
    )


def test_any_combinator_can_stop_earlier_than_all() -> None:
    """The any combinator stops as soon as one statistic settles; all waits for both.

    Same seed and parameters, differing only in ``convergence_combinator`` —
    an exact, deterministic demonstration that the combinator changes when a
    real run stops, not just an isolated monitor unit's Boolean logic.
    """

    def _params(combinator: ConvergenceCombinator) -> SimulationParams:
        return SimulationParams(
            N=25,
            m=0.15,
            mu=0.03,
            d=3,
            seed=20260800,
            loci=(LocusSpec(1, 100),),
            convergence_statistic=("D", "G_ST"),
            convergence_combinator=combinator,
            convergence_window=6,
            convergence_tolerance=0.02,
            max_generations=60,
        )

    any_params = _params("any")
    all_params = _params("all")

    any_result = _run(any_params)
    all_result = _run(all_params)

    assert any_result.report["converged"]
    assert all_result.report["converged"]
    assert any_result.report["generation"] == 5
    # 20, not the pre-Stage-F8 value of 15: `drift` now draws via
    # `_inversion_binomial` in ascending-allele-id order rather than
    # `rng.multinomial` in dict-insertion order — a deliberate,
    # accepted change to this seed's own specific trajectory (design
    # doc §5.4's own "accept the break"), confirmed deterministic (not
    # flaky) by re-running this exact test in isolation before updating
    # the expected value.
    assert all_result.report["generation"] == 20
    assert any_result.report["generation"] < all_result.report["generation"]


def test_mutation_ids_follow_high_explicit_initial_id() -> None:
    """Mutations cannot collide with labels supplied through explicit p_0."""
    params = SimulationParams(
        N=1,
        m=0.0,
        mu=1.0,
        d=2,
        seed=7,
        loci=(LocusSpec(1, 100),),
        initial_allele_count=1,
        convergence_window=2,
        max_generations=1,
        initial_frequencies=(
            ({AlleleId(MINTED_ID_START): 1.0},),
            ({AlleleId(MINTED_ID_START): 1.0},),
        ),
    )

    result = _run(params)

    final_ids = {
        int(allele_id)
        for deme in result.final_state.frequencies
        for locus in deme
        for allele_id in locus
    }
    assert final_ids == {MINTED_ID_START + 1, MINTED_ID_START + 2}


def test_unequal_deme_sizes_run_is_reproducible_and_bounds_support() -> None:
    """A full run with per-deme N stays reproducible and honors each N_i."""
    sizes = (6, 30)
    params = SimulationParams(
        N=sizes,
        m=0.2,
        mu=0.05,
        d=2,
        seed=20260817,
        loci=(LocusSpec(1, 100),),
        convergence_window=4,
        convergence_tolerance=1.0,
        max_generations=8,
    )

    first = _run(params)
    second = _run(params)

    first_rows = list(first.store.read(first.run_id))
    assert first_rows == list(second.store.read(second.run_id))
    assert first.report == second.report

    support: dict[tuple[int, int], set[int]] = {}
    for row in first_rows:
        key = (int(row["generation"]), int(row["deme"]))
        support.setdefault(key, set()).add(int(row["allele_id"]))
    for (_generation, deme), alleles in support.items():
        assert len(alleles) <= sizes[deme - 1]


def test_report_size_weighting_reflects_actual_per_deme_sizes() -> None:
    """Engine-level reports thread each deme's own N through, not an equal split."""
    loci = (LocusSpec(1, 100),)
    state = ModelState(
        loci=loci,
        frequencies=(
            ({AlleleId(0): 1.0},),
            ({AlleleId(0): 0.2, AlleleId(1): 0.8},),
        ),
    )
    sized_params = SimulationParams(
        N=(10, 10_000),
        m=0.1,
        mu=0.0,
        d=2,
        seed=7,
        loci=loci,
        deme_weighting="size",
    )
    equal_params = SimulationParams(
        N=(10, 10_000),
        m=0.1,
        mu=0.0,
        d=2,
        seed=7,
        loci=loci,
        deme_weighting="equal",
    )

    sized_report = report_for_state(
        state,
        sized_params,
        run_id="run-a",
        converged=False,
        reason="test",
    )
    equal_report = report_for_state(
        state,
        equal_params,
        run_id="run-a",
        converged=False,
        reason="test",
    )

    # The 10,000-copy deme dominates the size-weighted pool, pulling E_ST
    # toward that deme's own diversity rather than the 50/50 equal split.
    assert sized_report["E_ST"] == pytest.approx(0.20326126045322912)
    assert equal_report["E_ST"] == pytest.approx(0.6099865470109876)


def test_asymmetric_migration_matrix_run_is_reproducible() -> None:
    """A full run with a genuinely asymmetric d x d matrix stays reproducible."""
    matrix = (
        (0.9, 0.05, 0.05),
        (0.1, 0.8, 0.1),
        (0.0, 0.2, 0.8),
    )
    params = SimulationParams(
        N=20,
        m=matrix,
        mu=0.05,
        d=3,
        seed=20260817,
        loci=(LocusSpec(1, 100),),
        convergence_window=4,
        convergence_tolerance=1.0,
        max_generations=8,
    )

    first = _run(params)
    second = _run(params)

    assert list(first.store.read(first.run_id)) == list(
        second.store.read(second.run_id)
    )
    assert first.report == second.report
    assert first.final_state.deme_count == 3


def test_report_for_state_supports_multiple_loci_and_equal_weighting() -> None:
    """Independent per-locus reports are averaged under equal deme weighting."""
    loci = (LocusSpec(1, 100), LocusSpec(2, 100))
    state = ModelState(
        loci=loci,
        frequencies=(
            (
                {AlleleId(0): 1.0},
                {AlleleId(0): 0.5, AlleleId(1): 0.5},
            ),
            (
                {AlleleId(1): 1.0},
                {AlleleId(0): 0.5, AlleleId(1): 0.5},
            ),
        ),
    )
    params = SimulationParams(
        N=10,
        m=0.1,
        mu=0.0,
        d=2,
        seed=7,
        loci=loci,
        deme_weighting="equal",
    )
    report = report_for_state(
        state,
        params,
        run_id="run-a",
        converged=False,
        reason="test",
    )
    assert report["run_id"] == "run-a"
    assert report["G_ST"] is not None


def test_report_for_state_drops_a_monomorphic_locus_from_the_g_st_average() -> None:
    """`G_ST` averages only the loci where it is defined, not zero-filled.

    Regression test: one locus fixed for the same allele in every
    deme (`G_ST` undefined there — `H_T == 0`) alongside one polymorphic
    locus. The reported `G_ST` must equal the polymorphic locus's own
    value exactly, not that value averaged against a fabricated `0.0` for
    the undefined locus (which would understate real differentiation),
    and not `None` (which would discard the polymorphic locus's real
    signal over one unrelated monomorphic locus).
    """
    loci = (LocusSpec(1, 100), LocusSpec(2, 100))
    state = ModelState(
        loci=loci,
        frequencies=(
            (
                {AlleleId(0): 1.0},
                {AlleleId(0): 0.7, AlleleId(1): 0.3},
            ),
            (
                {AlleleId(0): 1.0},
                {AlleleId(0): 0.2, AlleleId(1): 0.8},
            ),
        ),
    )
    params = SimulationParams(
        N=10,
        m=0.1,
        mu=0.0,
        d=2,
        seed=7,
        loci=loci,
    )
    report = report_for_state(
        state,
        params,
        run_id="run-a",
        converged=False,
        reason="test",
    )
    polymorphic_locus_only = report_for_state(
        ModelState(
            loci=(loci[1],),
            frequencies=tuple((deme[1],) for deme in state.frequencies),
        ),
        SimulationParams(N=10, m=0.1, mu=0.0, d=2, seed=7, loci=(loci[1],)),
        run_id="run-b",
        converged=False,
        reason="test",
    )
    assert report["G_ST"] == pytest.approx(polymorphic_locus_only["G_ST"])


def test_locus_length_does_not_affect_the_report() -> None:
    """Locus length is inert data — only the frequency vectors drive statistics.

    Design §3.2: length matters only through the mutation rate, which this
    project configures directly via ``mu`` rather than deriving from
    ``LocusSpec.length``; it plays no role in any statistic. Holding every
    frequency fixed and only swapping which locus carries which length must
    leave the report bit-for-bit unchanged.
    """
    frequencies = (
        (
            {AlleleId(0): 0.7, AlleleId(1): 0.3},
            {AlleleId(0): 0.7, AlleleId(1): 0.3},
        ),
        (
            {AlleleId(0): 0.2, AlleleId(1): 0.8},
            {AlleleId(0): 0.2, AlleleId(1): 0.8},
        ),
    )

    def _report(loci: tuple[LocusSpec, ...]) -> FinalReport:
        params = SimulationParams(N=20, m=0.1, mu=0.0, d=2, seed=7, loci=loci)
        state = ModelState(loci=loci, frequencies=frequencies)
        return report_for_state(
            state,
            params,
            run_id="run-a",
            converged=False,
            reason="test",
        )

    short_first = _report((LocusSpec(1, 50), LocusSpec(2, 5_000)))
    long_first = _report((LocusSpec(1, 5_000), LocusSpec(2, 50)))
    equal_lengths = _report((LocusSpec(1, 200), LocusSpec(2, 200)))

    assert short_first == long_first == equal_lengths


def test_multi_locus_run_with_unequal_lengths_is_reproducible() -> None:
    """A full run over loci with genuinely different lengths stays reproducible."""
    params = SimulationParams(
        N=20,
        m=0.1,
        mu=0.02,
        d=2,
        seed=20260818,
        loci=(LocusSpec(1, 50), LocusSpec(2, 8_000)),
        convergence_window=4,
        convergence_tolerance=1.0,
        max_generations=8,
    )

    first = _run(params)
    second = _run(params)

    assert list(first.store.read(first.run_id)) == list(
        second.store.read(second.run_id)
    )
    assert first.report == second.report
    assert first.final_state.locus_count == 2


def test_stepping_stone_topology_run_is_reproducible() -> None:
    """A full run configured via the ring topology-sugar `m` stays reproducible.

    The compact ``{topology, rate}`` form only exists at the config-parsing
    layer (matching how the compact ``n_loci``/``locus_lengths`` locus form
    also only exists there), so this run is built via ``from_mapping``
    rather than the ``SimulationParams`` constructor directly.
    """
    params = SimulationParams.from_mapping(
        {
            "N": 20,
            "d": 8,
            "m": {"topology": "ring", "rate": 0.2},
            "mu": 0.02,
            "seed": 20260821,
            "convergence_window": 4,
            "convergence_tolerance": 1.0,
            "max_generations": 8,
        }
    )

    first = _run(params)
    second = _run(params)

    assert list(first.store.read(first.run_id)) == list(
        second.store.read(second.run_id)
    )
    assert first.report == second.report
    assert first.final_state.deme_count == 8


def test_stochastic_migrant_sampling_run_is_reproducible() -> None:
    """Opting in to random migrant counts stays fully seed-reproducible.

    Randomizing *how many* gene copies migrate each generation does not
    reintroduce the nondeterminism this project forbids (see the "tests
    are a pure function of their commit" rule): the same seed must still
    drive the new binomial draws identically on every run.
    """
    params = SimulationParams.from_mapping(
        {
            "N": 40,
            "d": 3,
            "m": 0.2,
            "mu": 0.02,
            "seed": 20260818,
            "migrant_sampling": "stochastic",
            "convergence_window": 4,
            "convergence_tolerance": 1.0,
            "max_generations": 8,
        }
    )

    first = _run(params)
    second = _run(params)

    assert list(first.store.read(first.run_id)) == list(
        second.store.read(second.run_id)
    )
    assert first.report == second.report


def test_default_migrant_sampling_is_unaffected_by_the_stochastic_option() -> None:
    """Leaving `migrant_sampling` unset stays byte-identical to before it existed.

    The opt-in contract this feature must hold: a config with no opinion on
    `migrant_sampling` produces exactly the same run whether or not the
    "stochastic" option exists in the codebase, because `migrate()` is
    never handed an `rng` unless a config explicitly asks for it.
    """
    base_config = {
        "N": 40,
        "d": 3,
        "m": 0.2,
        "mu": 0.02,
        "seed": 20260818,
        "convergence_window": 4,
        "convergence_tolerance": 1.0,
        "max_generations": 8,
    }
    implicit = SimulationParams.from_mapping(base_config)
    explicit = SimulationParams.from_mapping(
        {**base_config, "migrant_sampling": "continuous"}
    )

    implicit_run = _run(implicit)
    explicit_run = _run(explicit)

    assert list(implicit_run.store.read(implicit_run.run_id)) == list(
        explicit_run.store.read(explicit_run.run_id)
    )
    assert implicit_run.report == explicit_run.report


def test_finite_alleles_run_is_reproducible_and_bounds_capacity() -> None:
    """Opting in to a bounded allele-state space stays fully reproducible.

    ``length: 1`` gives capacity ``4`` — small enough that recurrence is
    all but guaranteed within a handful of generations, so a real run also
    directly proves the global capacity bound holds end to end, not just
    within `FiniteAlleleSpace`'s own unit tests.
    """
    params = SimulationParams.from_mapping(
        {
            "N": 40,
            "d": 3,
            "m": 0.2,
            "mu": 0.1,
            "seed": 20260821,
            "loci": [{"locus_id": 1, "length": 1}],
            "mutation_model": "finite_alleles",
            "convergence_window": 4,
            "convergence_tolerance": 1.0,
            "max_generations": 10,
        }
    )

    first = _run(params)
    second = _run(params)

    first_rows = list(first.store.read(first.run_id))
    assert first_rows == list(second.store.read(second.run_id))
    assert first.report == second.report
    assert {int(row["allele_id"]) for row in first_rows} <= set(range(4))


def test_default_mutation_model_is_unaffected_by_the_finite_alleles_option() -> None:
    """Leaving `mutation_model` unset stays byte-identical to before it existed.

    The opt-in contract this feature must hold: a config with no opinion on
    `mutation_model` produces exactly the same run whether or not the
    "finite_alleles" option exists in the codebase, because `mutate()` is
    never handed a `finite_alleles` registry unless a config explicitly
    asks for it.
    """
    base_config = {
        "N": 40,
        "d": 3,
        "m": 0.2,
        "mu": 0.02,
        "seed": 20260821,
        "convergence_window": 4,
        "convergence_tolerance": 1.0,
        "max_generations": 8,
    }
    implicit = SimulationParams.from_mapping(base_config)
    explicit = SimulationParams.from_mapping(
        {**base_config, "mutation_model": "infinite_alleles"}
    )

    implicit_run = _run(implicit)
    explicit_run = _run(explicit)

    assert list(implicit_run.store.read(implicit_run.run_id)) == list(
        explicit_run.store.read(explicit_run.run_id)
    )
    assert implicit_run.report == explicit_run.report


def test_mu_b_run_matches_the_equivalent_explicit_per_locus_mu() -> None:
    """`mu_b` is genuine sugar: it must run identically to its expansion.

    Builds the same scenario two ways — once via `mu_b`, once via the
    exact per-locus `mu` list `mu_b` derives — and requires byte-identical
    output, not just equal `mutation_rates`. This is the strongest form of
    "sugar expands to canonical form" check: the derived config isn't just
    inspected, it is run.
    """
    mu_b = 0.001
    loci = [{"locus_id": 1, "length": 5}, {"locus_id": 2, "length": 50}]
    base_config = {
        "N": 40,
        "d": 3,
        "m": 0.2,
        "seed": 20260822,
        "loci": loci,
        "convergence_window": 4,
        "convergence_tolerance": 1.0,
        "max_generations": 8,
    }
    via_mu_b = SimulationParams.from_mapping({**base_config, "mu_b": mu_b})
    via_expanded_mu = SimulationParams.from_mapping(
        {**base_config, "mu": list(via_mu_b.mutation_rates)}
    )

    mu_b_run = _run(via_mu_b)
    expanded_run = _run(via_expanded_mu)

    assert list(mu_b_run.store.read(mu_b_run.run_id)) == list(
        expanded_run.store.read(expanded_run.run_id)
    )
    assert mu_b_run.report == expanded_run.report


def test_mu_b_combines_with_finite_alleles() -> None:
    """A per-base rate and a bounded allele space compose cleanly.

    The two features are orthogonal by design (one derives the mutation
    *rate* per locus, the other bounds the mutation *target* space per
    locus) but were built in separate sessions — this is the one place
    that actually exercises them together end to end.
    """
    params = SimulationParams.from_mapping(
        {
            "N": 40,
            "d": 3,
            "m": 0.2,
            "mu_b": 0.05,
            "seed": 20260822,
            "loci": [{"locus_id": 1, "length": 1}],
            "mutation_model": "finite_alleles",
            "convergence_window": 4,
            "convergence_tolerance": 1.0,
            "max_generations": 10,
        }
    )

    first = _run(params)
    second = _run(params)

    first_rows = list(first.store.read(first.run_id))
    assert first_rows == list(second.store.read(second.run_id))
    assert first.report == second.report
    assert {int(row["allele_id"]) for row in first_rows} <= set(range(4))


# Golden-parity tests for `GenerationalBackend`: proving the
# generation-first reframing (`ReplicaLane`/`run_batch`/`SequentialAdvancer`)
# computes exactly what `LinealBackend` already does, for the same seed —
# see `run_batch`'s own docstring for why reordering *when* a generation is
# computed never changes *what* it computes. `replicate_tolerance` is
# deliberately unset in both of these: with it set, the two backends' own
# cross-replicate stopping *decisions* can legitimately differ (event-driven
# vs. once-per-completed-replicate — see `test_run_batch_cross_replica_stop_
# fires_at_deterministic_ordinal`, below, for that behavior's own dedicated
# test), so parity is only claimed here for what both backends promise
# unconditionally: each individual replicate's own trajectory.


def test_generational_backend_matches_lineal_for_scalar_run(
    tiny_params: SimulationParams,
) -> None:
    """`GenerationalBackend` reproduces `LinealBackend`'s scalar trajectory exactly."""
    lineal_store = InMemoryTrajectoryStore()
    lineal_result = LinealBackend().run(tiny_params, lineal_store, None, _clock)
    assert isinstance(lineal_result, RunResult)

    generational_store = InMemoryTrajectoryStore()
    generational_result = GenerationalBackend().run(
        tiny_params, generational_store, None, _clock
    )
    assert isinstance(generational_result, RunResult)

    assert generational_result.run_id == lineal_result.run_id
    assert generational_result.report == lineal_result.report
    assert generational_result.final_state == lineal_result.final_state
    assert (
        generational_result.convergence_generations
        == lineal_result.convergence_generations
    )
    assert generational_result.convergence_history == lineal_result.convergence_history
    assert generational_result.manifest == lineal_result.manifest
    assert list(generational_store.read(generational_result.run_id)) == list(
        lineal_store.read(lineal_result.run_id)
    )


def test_generational_backend_matches_lineal_for_batch(
    tiny_params: SimulationParams,
) -> None:
    """Every replicate's own trajectory is bit-identical between backends,
    in the same order, for a multi-replicate batch with no adaptive stop.
    """
    params = replace(tiny_params, n_replicates=3)

    lineal_store = InMemoryTrajectoryStore()
    lineal_results = LinealBackend().run(params, lineal_store, None, _clock)
    assert isinstance(lineal_results, tuple)

    generational_store = InMemoryTrajectoryStore()
    generational_results = GenerationalBackend().run(
        params, generational_store, None, _clock
    )
    assert isinstance(generational_results, tuple)

    assert len(generational_results) == len(lineal_results) == 3
    for lineal_result, generational_result in zip(
        lineal_results, generational_results, strict=True
    ):
        assert generational_result.run_id == lineal_result.run_id
        assert generational_result.report == lineal_result.report
        assert generational_result.final_state == lineal_result.final_state
        assert generational_result.manifest == lineal_result.manifest
        assert list(generational_store.read(generational_result.run_id)) == list(
            lineal_store.read(lineal_result.run_id)
        )


def test_run_batch_cross_replica_stop_fires_at_deterministic_ordinal() -> None:
    """The adaptive replicate stop fires the instant enough lanes stop,
    with simultaneous stops broken by ascending `replica_index`,
    deterministically across repeated runs.

    Both `convergence_tolerance` and `replicate_tolerance` are set
    astronomically large so every criterion is satisfied the instant it
    has *enough* observations, regardless of their actual values — this
    makes every one of the five lanes stop on the identical tick
    (generation `convergence_window - 1 == 2`), simultaneously, by
    construction rather than by chance, so the tie-break itself is what
    is under test, not real convergence timing. With `replicate_minimum
    == 2`, the batch-wide stop then fires while processing the *second*
    lane in ascending order — exactly replicates 0 and 1 — leaving
    replicates 2-4 never even reached.
    """
    params = SimulationParams(
        N=20,
        m=0.1,
        mu=0.01,
        d=2,
        seed=20260901,
        loci=(LocusSpec(1, 200),),
        convergence_window=3,
        convergence_tolerance=1e12,
        max_generations=50,
        n_replicates=5,
        replicate_tolerance=1e12,
        replicate_minimum=2,
    )

    first = run_batch(
        params, InMemoryTrajectoryStore(), "batch", _clock, SequentialAdvancer()
    )
    second = run_batch(
        params, InMemoryTrajectoryStore(), "batch", _clock, SequentialAdvancer()
    )

    assert [result.run_id for result in first] == [result.run_id for result in second]
    assert len(first) == 2
    # An explicit `run_id` derives each lane's own id as `<run_id>-r<NNN>`
    # (`_build_replica_lane`) — asserting these exact ids doubles as
    # confirmation that ties broke in ascending `replica_index` order:
    # only replicates 0 and 1 (`-r001`/`-r002`) ever got processed.
    assert [result.run_id for result in first] == ["batch-r001", "batch-r002"]
    assert all(result.report["generation"] == 2 for result in first)


# `fim()`'s own `engine_backend`/`jit` keywords (Stage F2): the factory
# actually reachable by a real caller, not just `build_engine_backend`/
# `GenerationalBackend` constructed directly the way the tests above do.


def test_fim_engine_backend_generational_matches_default(
    tiny_params: SimulationParams,
) -> None:
    """`fim(..., engine_backend="generational")` matches the untouched default."""
    lineal_result = fim(
        tiny_params.N,
        tiny_params.m,
        tiny_params.mu,
        tiny_params.d,
        params=tiny_params,
        clock=_clock,
    )
    assert isinstance(lineal_result, RunResult)

    generational_result = fim(
        tiny_params.N,
        tiny_params.m,
        tiny_params.mu,
        tiny_params.d,
        params=tiny_params,
        clock=_clock,
        engine_backend="generational",
    )
    assert isinstance(generational_result, RunResult)

    assert generational_result.run_id == lineal_result.run_id
    assert generational_result.report == lineal_result.report
    assert generational_result.final_state == lineal_result.final_state


def test_fim_rejects_jit_on_lineal(tiny_params: SimulationParams) -> None:
    """`jit` is never offered on the lineal backend — a permanent restriction."""
    with pytest.raises(ValueError, match="lineal backend"):
        fim(
            tiny_params.N,
            tiny_params.m,
            tiny_params.mu,
            tiny_params.d,
            params=tiny_params,
            jit="numba",
        )


def test_fim_rejects_lineal_only_args_on_other_backends(
    tiny_params: SimulationParams,
) -> None:
    """`max_workers`/`store_factory` are lineal-only — never a silent no-op."""
    with pytest.raises(ValueError, match="lineal-backend-only"):
        fim(
            tiny_params.N,
            tiny_params.m,
            tiny_params.mu,
            tiny_params.d,
            params=tiny_params,
            engine_backend="generational",
            max_workers=2,
        )


def test_fim_generational_vector_rejects_infinite_alleles(
    tiny_params: SimulationParams,
) -> None:
    """`"generational-vector"` is scoped to `finite_alleles` — never a silent fallback.

    `tiny_params`'s own default `mutation_model` is `"infinite_alleles"`
    (unbounded, per-generation-ragged identity space — out of scope for
    `fim.model.vectorized`'s bounded-`K` representation), so this is the
    common case a caller is most likely to hit by accident.
    """
    with pytest.raises(ValueError, match="finite_alleles"):
        fim(
            tiny_params.N,
            tiny_params.m,
            tiny_params.mu,
            tiny_params.d,
            params=tiny_params,
            engine_backend="generational-vector",
        )


def test_fim_generational_vector_rejects_stochastic_migrant_sampling(
    tiny_params: SimulationParams,
) -> None:
    """`"generational-vector"` is also scoped to deterministic migration only."""
    params = replace(
        tiny_params, mutation_model="finite_alleles", migrant_sampling="stochastic"
    )
    with pytest.raises(ValueError, match="migrant_sampling"):
        fim(
            params.N,
            params.m,
            params.mu,
            params.d,
            params=params,
            engine_backend="generational-vector",
        )


def test_fim_generational_vector_rejects_jit(tiny_params: SimulationParams) -> None:
    """`jit` has no separate toggle under `"generational-vector"` — a `ValueError`.

    Numba is required internally, unconditionally, for its own mutate
    step; only `jit="off"` (the default) is accepted, so a caller who
    asks for `jit="numba"` explicitly gets an error, not a silent no-op.
    """
    params = replace(tiny_params, mutation_model="finite_alleles")
    with pytest.raises(ValueError, match="generational-vector"):
        fim(
            params.N,
            params.m,
            params.mu,
            params.d,
            params=params,
            engine_backend="generational-vector",
            jit="numba",
        )


def test_build_engine_backend_rejects_unknown_choice() -> None:
    """An unrecognized `engine_backend` is a `ValueError`, not silently ignored."""
    with pytest.raises(ValueError, match="unknown engine backend"):
        build_engine_backend("bogus")  # type: ignore[arg-type]


# `engine_backend="auto"` (Stage F7): picks between `"generational"` and
# `"generational-vector"` using `params.d`/`auto_vector_min_d` — the
# generation-first design's own Stage 4/vector design's own Stage V3
# deme-axis sweep found the real crossover, narrowed to `d≈35`
# (`DEFAULT_AUTO_VECTOR_MIN_D`). Never resolves to `"lineal"` — see
# `build_engine_backend`'s own docstring for why only this one axis is
# automated so far.


def test_build_engine_backend_auto_requires_params() -> None:
    """`"auto"` cannot decide anything without a real `SimulationParams`."""
    with pytest.raises(ValueError, match="params"):
        build_engine_backend("auto")


def test_build_engine_backend_auto_picks_vector_above_threshold() -> None:
    """Above the cutover, on an eligible config, `"auto"` picks Backend V."""
    params = _finite_alleles_vector_params(d=40)
    backend = build_engine_backend("auto", params=params, auto_vector_min_d=35)
    assert isinstance(backend, GenerationalBackend)
    assert isinstance(backend._advancer, VectorizedAdvancer)


def test_build_engine_backend_auto_picks_generational_below_threshold() -> None:
    """Below the cutover, `"auto"` picks Backend G, not Backend V."""
    params = _finite_alleles_vector_params(d=30)
    backend = build_engine_backend("auto", params=params, auto_vector_min_d=35)
    assert isinstance(backend, GenerationalBackend)
    assert isinstance(backend._advancer, ThreadedAdvancer)


def test_build_engine_backend_auto_picks_generational_when_vector_ineligible() -> None:
    """A large `d` alone is not enough — `"auto"` still checks V's own scope.

    `d=40` clears the default threshold, but `infinite_alleles` (the
    default `mutation_model`) is outside `VectorizedAdvancer`'s own
    scope — `"auto"` must fall back to Backend G here, not raise the
    `ValueError` a direct `"generational-vector"` choice would.
    """
    params = replace(
        _finite_alleles_vector_params(d=40), mutation_model="infinite_alleles"
    )
    backend = build_engine_backend("auto", params=params, auto_vector_min_d=35)
    assert isinstance(backend, GenerationalBackend)
    assert isinstance(backend._advancer, ThreadedAdvancer)


def test_build_engine_backend_auto_respects_custom_threshold() -> None:
    """The cutover is a real, configurable parameter, not a hidden constant."""
    params = _finite_alleles_vector_params(d=10)
    backend = build_engine_backend("auto", params=params, auto_vector_min_d=5)
    assert isinstance(backend, GenerationalBackend)
    assert isinstance(backend._advancer, VectorizedAdvancer)


def test_build_engine_backend_auto_rejects_jit_when_resolved_to_vector() -> None:
    """`jit="numba"` is still rejected once `"auto"` resolves to Backend V."""
    params = _finite_alleles_vector_params(d=40)
    with pytest.raises(ValueError, match="generational-vector"):
        build_engine_backend("auto", params=params, auto_vector_min_d=35, jit="numba")


def test_fim_engine_backend_auto_runs_end_to_end(
    tiny_params: SimulationParams,
) -> None:
    """`fim(..., engine_backend="auto")` works through the public entry point.

    `tiny_params`'s own `d=2` and default `infinite_alleles` model both
    put it outside Backend V's scope — `"auto"` must land on Backend G
    here, and still produce a normal, successful result.
    """
    result = fim(
        tiny_params.N,
        tiny_params.m,
        tiny_params.mu,
        tiny_params.d,
        params=tiny_params,
        clock=_clock,
        engine_backend="auto",
    )
    assert isinstance(result, RunResult)
    assert result.report["converged"] in (True, False)


def test_fim_engine_backend_auto_reaches_vector_end_to_end() -> None:
    """`fim(..., engine_backend="auto")` reaches Backend V when the config qualifies."""
    pytest.importorskip("numba")
    params = _finite_alleles_vector_params(d=40)

    result = fim(
        params.N,
        params.m,
        params.mu,
        params.d,
        params=params,
        clock=_clock,
        engine_backend="auto",
        auto_vector_min_d=35,
    )
    assert isinstance(result, RunResult)
    assert result.report["converged"] in (True, False)


# Manifest provenance (Stage F7): `fim()` stamps every returned result's
# own manifest with which engine actually ran it — real, load-bearing
# information for `engine_backend="auto"` specifically, since its own
# resolved choice is otherwise invisible in the persisted record (design
# doc §7.4). `LinealBackend`/`GenerationalBackend` constructed and run
# directly, bypassing `fim()`, never populate these fields themselves
# (`test_generational_backend_matches_lineal_for_scalar_run`'s own
# `manifest ==` comparison, above, depends on that staying true).


def test_fim_records_the_explicit_engine_backend_in_the_manifest(
    tiny_params: SimulationParams,
) -> None:
    """`fim(..., engine_backend=...)` stamps that exact choice, not `None`."""
    result = fim(
        tiny_params.N,
        tiny_params.m,
        tiny_params.mu,
        tiny_params.d,
        params=tiny_params,
        clock=_clock,
        engine_backend="generational",
    )
    assert isinstance(result, RunResult)
    assert result.manifest.engine_backend == "generational"
    assert result.manifest.jit == "off"


def test_fim_default_lineal_records_engine_backend_in_the_manifest(
    tiny_params: SimulationParams,
) -> None:
    """Even the untouched default (`"lineal"`) gets recorded, not left `None`."""
    result = fim(
        tiny_params.N,
        tiny_params.m,
        tiny_params.mu,
        tiny_params.d,
        params=tiny_params,
        clock=_clock,
    )
    assert isinstance(result, RunResult)
    assert result.manifest.engine_backend == "lineal"


def test_fim_auto_records_the_resolved_choice_not_the_literal_auto() -> None:
    """`"auto"`'s own manifest never contains the literal string `"auto"`.

    The whole reason this field exists: a runtime-data-dependent choice
    must still be recoverable from the persisted record. Checked at
    both ends of the threshold, on one config.
    """
    pytest.importorskip("numba")
    below = _finite_alleles_vector_params(d=30)
    below_result = fim(
        below.N,
        below.m,
        below.mu,
        below.d,
        params=below,
        clock=_clock,
        engine_backend="auto",
        auto_vector_min_d=35,
    )
    assert isinstance(below_result, RunResult)
    assert below_result.manifest.engine_backend == "generational"

    above = _finite_alleles_vector_params(d=40)
    above_result = fim(
        above.N,
        above.m,
        above.mu,
        above.d,
        params=above,
        clock=_clock,
        engine_backend="auto",
        auto_vector_min_d=35,
    )
    assert isinstance(above_result, RunResult)
    assert above_result.manifest.engine_backend == "generational-vector"


def test_fim_records_jit_in_the_manifest(tiny_params: SimulationParams) -> None:
    """`jit="numba"` is recorded exactly, not silently dropped."""
    pytest.importorskip("numba")
    result = fim(
        tiny_params.N,
        tiny_params.m,
        tiny_params.mu,
        tiny_params.d,
        params=tiny_params,
        clock=_clock,
        engine_backend="generational",
        jit="numba",
    )
    assert isinstance(result, RunResult)
    assert result.manifest.jit == "numba"


def test_fim_records_engine_backend_for_every_replicate_in_a_batch(
    tiny_params: SimulationParams,
) -> None:
    """A multi-replicate batch stamps every replicate's own manifest, not just one."""
    params = replace(tiny_params, n_replicates=3)
    results = fim(
        params.N,
        params.m,
        params.mu,
        params.d,
        params=params,
        clock=_clock,
        engine_backend="generational",
    )
    assert isinstance(results, tuple)
    assert len(results) == 3
    assert all(result.manifest.engine_backend == "generational" for result in results)


# `ThreadedAdvancer` (Stage F3): the Stage 0/1 parity tests above, re-run
# with real thread interleaving in the mix, proving determinism holds
# under real concurrency rather than only in principle — the design this
# implements calls this out explicitly as its own required test, not
# something the sequential-only tests above already cover.


def test_generational_backend_with_threaded_advancer_matches_lineal_for_scalar_run(
    tiny_params: SimulationParams,
) -> None:
    """`ThreadedAdvancer` reproduces `LinealBackend`'s scalar trajectory exactly."""
    lineal_store = InMemoryTrajectoryStore()
    lineal_result = LinealBackend().run(tiny_params, lineal_store, None, _clock)
    assert isinstance(lineal_result, RunResult)

    threaded_store = InMemoryTrajectoryStore()
    threaded_result = GenerationalBackend(ThreadedAdvancer()).run(
        tiny_params, threaded_store, None, _clock
    )
    assert isinstance(threaded_result, RunResult)

    assert threaded_result.report == lineal_result.report
    assert threaded_result.final_state == lineal_result.final_state
    assert list(threaded_store.read(threaded_result.run_id)) == list(
        lineal_store.read(lineal_result.run_id)
    )


def test_generational_backend_with_threaded_advancer_matches_lineal_for_batch(
    tiny_params: SimulationParams,
) -> None:
    """Real multi-block, multi-thread fan-out still matches `LinealBackend` exactly.

    Seven replicates against `max_workers=3` forces `_partition_into_blocks`
    to build blocks of uneven size (3, 2, 2) and actually exercises more
    than one block concurrently — not just the single-block, effectively-
    sequential case a smaller batch could pass by accident.
    """
    params = replace(tiny_params, n_replicates=7)

    lineal_store = InMemoryTrajectoryStore()
    lineal_results = LinealBackend().run(params, lineal_store, None, _clock)
    assert isinstance(lineal_results, tuple)

    threaded_store = InMemoryTrajectoryStore()
    threaded_results = GenerationalBackend(ThreadedAdvancer(max_workers=3)).run(
        params, threaded_store, None, _clock
    )
    assert isinstance(threaded_results, tuple)

    assert len(threaded_results) == len(lineal_results) == 7
    for lineal_result, threaded_result in zip(
        lineal_results, threaded_results, strict=True
    ):
        assert threaded_result.run_id == lineal_result.run_id
        assert threaded_result.report == lineal_result.report
        assert threaded_result.final_state == lineal_result.final_state
        assert list(threaded_store.read(threaded_result.run_id)) == list(
            lineal_store.read(lineal_result.run_id)
        )


def test_fim_engine_backend_generational_uses_threaded_advancer(
    tiny_params: SimulationParams,
) -> None:
    """`fim(..., engine_backend="generational")` now runs on `ThreadedAdvancer`
    by default (`build_engine_backend`) — end to end, through the public
    entry point, not just `GenerationalBackend` constructed directly.
    """
    result = fim(
        tiny_params.N,
        tiny_params.m,
        tiny_params.mu,
        tiny_params.d,
        params=tiny_params,
        clock=_clock,
        engine_backend="generational",
    )
    assert isinstance(result, RunResult)
    assert result.report["converged"] in (True, False)


def test_threaded_advancer_rejects_non_positive_max_workers() -> None:
    """`max_workers` below 1 is rejected at construction, not at first use."""
    with pytest.raises(ValueError, match="max_workers"):
        ThreadedAdvancer(max_workers=0)


# `jit="numba"` (Stage F5): `drift`'s own multinomial decomposition
# (`fim.model.operators._multinomial_via_binomial`) is bit-identical to
# `rng.multinomial` (`test/model/test_operators.py`), so a `Generational
# Backend` running with JIT enabled should be bit-identical to
# `LinealBackend` too, not merely statistically close — a materially
# stronger claim than the base `ThreadedAdvancer` parity tests above,
# and worth its own dedicated check.


def test_generational_backend_with_jit_matches_lineal_bit_for_bit(
    tiny_params: SimulationParams,
) -> None:
    """`ThreadedAdvancer(jit="numba")` reproduces `LinealBackend` exactly."""
    pytest.importorskip("numba")
    lineal_store = InMemoryTrajectoryStore()
    lineal_result = LinealBackend().run(tiny_params, lineal_store, None, _clock)
    assert isinstance(lineal_result, RunResult)

    jit_store = InMemoryTrajectoryStore()
    jit_result = GenerationalBackend(ThreadedAdvancer(jit="numba")).run(
        tiny_params, jit_store, None, _clock
    )
    assert isinstance(jit_result, RunResult)

    assert jit_result.report == lineal_result.report
    assert jit_result.final_state == lineal_result.final_state
    assert list(jit_store.read(jit_result.run_id)) == list(
        lineal_store.read(lineal_result.run_id)
    )


def test_fim_engine_backend_generational_with_jit_matches_default(
    tiny_params: SimulationParams,
) -> None:
    """`fim(..., engine_backend="generational", jit="numba")` end to end."""
    pytest.importorskip("numba")
    lineal_result = fim(
        tiny_params.N,
        tiny_params.m,
        tiny_params.mu,
        tiny_params.d,
        params=tiny_params,
        clock=_clock,
    )
    assert isinstance(lineal_result, RunResult)

    jit_result = fim(
        tiny_params.N,
        tiny_params.m,
        tiny_params.mu,
        tiny_params.d,
        params=tiny_params,
        clock=_clock,
        engine_backend="generational",
        jit="numba",
    )
    assert isinstance(jit_result, RunResult)

    assert jit_result.report == lineal_result.report
    assert jit_result.final_state == lineal_result.final_state


# `VectorizedAdvancer`/`"generational-vector"` (Stage F4): the array-native,
# fused `migrate`/`mutate`/`drift` backend (`fim.model.vectorized`).
# Statistical, not bit-identical, parity with `LinealBackend` is this
# backend's own correctness bar throughout (`fim.model.vectorized`'s own
# module docstring; vector design §6) — these tests check reproducibility,
# scope enforcement, and structural invariants (frequencies sum to one,
# capacity bound holds), not trajectory equality against `LinealBackend`.


def _finite_alleles_vector_params(**overrides: object) -> SimulationParams:
    """A `finite_alleles`/continuous-migration config `VectorizedAdvancer` accepts."""
    base = SimulationParams(
        N=40,
        m=0.2,
        mu=0.1,
        d=3,
        seed=20260901,
        loci=(LocusSpec(1, 2),),  # capacity 16
        mutation_model="finite_alleles",
        convergence_window=4,
        convergence_tolerance=1.0,
        max_generations=10,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def test_generational_vector_backend_matches_scope_of_lineal_reproducibility() -> None:
    """`GenerationalBackend(VectorizedAdvancer())` is reproducible for a fixed seed.

    Same seed, same everything else, run twice: exactly the same
    trajectory both times — determinism, not bit-identity to
    `LinealBackend`'s own dict-based path, is the property this checks.
    """
    pytest.importorskip("numba")
    params = _finite_alleles_vector_params()

    first_store = InMemoryTrajectoryStore()
    first_result = GenerationalBackend(VectorizedAdvancer()).run(
        params, first_store, None, _clock
    )
    assert isinstance(first_result, RunResult)

    second_store = InMemoryTrajectoryStore()
    second_result = GenerationalBackend(VectorizedAdvancer()).run(
        params, second_store, None, _clock
    )
    assert isinstance(second_result, RunResult)

    assert first_result.report == second_result.report
    assert first_result.final_state == second_result.final_state
    assert list(first_store.read(first_result.run_id)) == list(
        second_store.read(second_result.run_id)
    )


def test_generational_vector_backend_bounds_capacity_and_stays_valid() -> None:
    """A real `VectorizedAdvancer` run stays within its own bounded allele space.

    Directly proves the two structural invariants `fim.model.vectorized`
    exists to preserve end to end, not just within its own unit tests:
    every allele id observed stays inside `0..capacity-1`, and every
    deme's own final frequencies remain a valid distribution
    (`ModelState.validate_support` — the same check the finite-alleles
    lineal tests above already run).
    """
    pytest.importorskip("numba")
    params = _finite_alleles_vector_params()
    capacity = finite_allele_capacity(params.loci[0].length)

    result = GenerationalBackend(VectorizedAdvancer()).run(
        params, InMemoryTrajectoryStore(), None, _clock
    )
    assert isinstance(result, RunResult)

    rows = list(result.store.read(result.run_id))
    assert {int(row["allele_id"]) for row in rows} <= set(range(capacity))
    result.final_state.validate_support(tuple(_population_sizes(params.N, params.d)))


def test_generational_vector_backend_batch_is_independently_reproducible() -> None:
    """A real multi-replicate batch runs cleanly and independently reproduces.

    Mirrors `test_generational_backend_with_threaded_advancer_matches_
    lineal_for_batch`'s own shape (several replicates, checked
    independently) but checks each `VectorizedAdvancer` run against
    *itself* (same params, same seed, run twice), not against
    `LinealBackend` — this test's own name previously claimed the
    latter without actually checking it; see `test_generational_
    vector_backend_matches_lineal_statistically`, below, for the real
    cross-backend comparison, and `test_generational_vector_backend_
    matches_lineal_exactly_without_migration` for the case where a
    full multi-generation run *is* checked bit-for-bit.
    """
    pytest.importorskip("numba")
    params = replace(_finite_alleles_vector_params(), n_replicates=4)

    first_store = InMemoryTrajectoryStore()
    first_results = GenerationalBackend(VectorizedAdvancer()).run(
        params, first_store, None, _clock
    )
    assert isinstance(first_results, tuple)
    assert len(first_results) == 4

    second_store = InMemoryTrajectoryStore()
    second_results = GenerationalBackend(VectorizedAdvancer()).run(
        params, second_store, None, _clock
    )
    assert isinstance(second_results, tuple)

    for first_result, second_result in zip(first_results, second_results, strict=True):
        assert first_result.run_id == second_result.run_id
        assert first_result.report == second_result.report
        assert first_result.final_state == second_result.final_state


def test_generational_vector_backend_matches_lineal_exactly_without_migration() -> None:
    """A full multi-generation run matches `LinealBackend` bit-for-bit when `m=0`.

    The real, end-to-end proof this project's own operator-level exact-
    match tests (`test/model/test_vectorized.py`) never actually
    exercised: `fim.engine.VectorizedAdvancer` round-trips each lane's
    own state through `build_vectorized_state`/`vectorized_state_to_
    model_state` every generation (`ReplicaLane`'s own docstring), and
    a real correctness bug in the first of those — re-deriving finite-
    alleles minted bookkeeping from scratch every generation, silently
    forgetting any allele minted and then driven extinct within the
    same generation it was minted in — meant this never actually held,
    even though every individual operator had been proven exact in
    isolation. Fixed by carrying the bookkeeping forward
    (`build_vectorized_state`'s own `previous_locus_states` argument);
    confirmed directly, not assumed, with `m=0` here specifically to
    remove `migrate`'s own floating-point reduction-order divergence
    from the picture (`migrate_vectorized`'s dense matmul and `migrate`'s
    dict-based blend are two different, both-deterministic reduction
    orders for the same computation — a separate, accepted residual
    `test_generational_vector_backend_matches_lineal_statistically`,
    below, exists to characterize, not eliminate).
    """
    pytest.importorskip("numba")
    params = replace(_finite_alleles_vector_params(d=3), m=0.0, max_generations=10)

    lineal_store = InMemoryTrajectoryStore()
    lineal_result = LinealBackend().run(params, lineal_store, None, _clock)
    assert isinstance(lineal_result, RunResult)

    vector_store = InMemoryTrajectoryStore()
    vector_result = GenerationalBackend(VectorizedAdvancer()).run(
        params, vector_store, None, _clock
    )
    assert isinstance(vector_result, RunResult)

    assert vector_result.report == lineal_result.report
    assert vector_result.final_state == lineal_result.final_state
    assert list(vector_store.read(vector_result.run_id)) == list(
        lineal_store.read(lineal_result.run_id)
    )


def test_generational_vector_backend_matches_lineal_statistically() -> None:
    """Aggregate differentiation statistics agree with `LinealBackend`, at scale.

    With migration active, a full multi-generation run is *not*
    bit-for-bit identical to `LinealBackend` in general — `migrate`'s
    own floating-point reduction-order divergence (dense matmul vs.
    dict-based blend, `test_generational_vector_backend_matches_
    lineal_exactly_without_migration`'s own docstring) occasionally sits
    close enough to a discrete draw's own decision boundary to flip it,
    and that one flip changes which allele identities exist from that
    generation forward — measured directly across 30 seeds with
    migration active, 23 diverged from `LinealBackend` within the first
    three generations. That is expected, not a defect: the vector
    design's own original correctness bar for this backend was always
    "statistically, not bit-identically, equivalent" (`fim.model.
    vectorized`'s own module docstring), and Stage F8 only ever
    strengthened that to full bit-identity for the *individual
    operators* feeding a shared, identical starting state, not for a
    full run's own compounding sequence of independent decision points.

    What actually matters is whether that per-seed divergence is a
    genuine, unbiased alternate realization of the same underlying
    random process, or a *systematic* bias — the same distinction that
    made the minted-bookkeeping bug (fixed alongside this test) a real
    defect and this residual floating-point one not: checked directly,
    not assumed, via the same normal-approximation-band methodology
    this project's own `test_drift_vectorized_variance_matches_
    binomial_theory` already established, comparing each backend's own
    mean `D`/`G_ST` across 200 independently seeded replicates. A
    smaller sample (40, then 200, at a longer horizon) showed a
    borderline-significant gap that a larger one (600) resolved back to
    noise — recorded honestly rather than only reporting the
    comfortable number: real bias would have gotten *more* precisely
    measured as the sample grew, not smaller.
    """
    pytest.importorskip("numba")
    params = SimulationParams(
        N=40,
        m=0.1,
        mu=0.05,
        d=4,
        seed=13579,
        loci=(LocusSpec(1, 2),),
        mutation_model="finite_alleles",
        convergence_tolerance=0.0,
        convergence_window=21,
        max_generations=20,
        n_replicates=200,
    )

    lineal_results = fim(
        params.N, params.m, params.mu, params.d, params=params, engine_backend="lineal"
    )
    vector_results = fim(
        params.N,
        params.m,
        params.mu,
        params.d,
        params=params,
        engine_backend="generational-vector",
    )
    assert isinstance(lineal_results, tuple)
    assert isinstance(vector_results, tuple)

    def _as_float(value: float | None) -> float:
        # `G_ST` alone can be `None` (`DifferentiationReport`'s own
        # docstring) -- never for this config (real differentiation
        # signal, every replicate), so a real `None` here is a genuine
        # test-setup bug, not a case to special-case around.
        assert value is not None
        return value

    for stat in ("D", "G_ST"):
        lineal_values = [_as_float(result.report[stat]) for result in lineal_results]
        vector_values = [_as_float(result.report[stat]) for result in vector_results]
        lineal_mean = statistics.fmean(lineal_values)
        vector_mean = statistics.fmean(vector_values)
        standard_error = (
            statistics.variance(lineal_values) / len(lineal_values)
            + statistics.variance(vector_values) / len(vector_values)
        ) ** 0.5
        assert vector_mean == pytest.approx(lineal_mean, abs=5.0 * standard_error), stat


def test_fim_engine_backend_generational_vector_runs_end_to_end() -> None:
    """`fim(..., engine_backend="generational-vector")` works end to end."""
    pytest.importorskip("numba")
    params = _finite_alleles_vector_params()

    result = fim(
        params.N,
        params.m,
        params.mu,
        params.d,
        params=params,
        clock=_clock,
        engine_backend="generational-vector",
    )
    assert isinstance(result, RunResult)
    assert result.report["converged"] in (True, False)


def test_vectorized_advancer_caches_migration_weights_across_generations() -> None:
    """`ReplicaLane.migration_weights` is built once, then reused, not rebuilt.

    Found by the Stage 4/Stage V3 benchmark sweep: `symmetric_migration_
    weights`'s own O(d^2) matrix build was being redone every single
    generation, even though `params.m`/deme sizes never change mid-run.
    Checked directly, not just inferred from timing: the exact same
    array object (`is`, not just equal) survives two consecutive
    `advance()` calls.
    """
    pytest.importorskip("numba")
    params = _finite_alleles_vector_params(max_generations=3, convergence_window=3)
    store = InMemoryTrajectoryStore()
    lane = _build_replica_lane(params, 0, None, store, _clock)
    # `_build_replica_lane` never populates `migration_weights` itself —
    # only `VectorizedAdvancer.advance` does, on first use — but that
    # isn't asserted directly here: mypy narrows a field's type across
    # an `is None` check and does not invalidate that narrowing across
    # an opaque method call that mutates it, which would make the
    # second `advance()` call below a mypy-reported false "unreachable
    # statement." The two assertions that matter (cache populated;
    # cache reused, not rebuilt) do not need that initial check.
    advancer = VectorizedAdvancer()

    advancer.advance([lane], store)
    first_weights = lane.migration_weights
    assert first_weights is not None, "cache populated after the first generation"

    advancer.advance([lane], store)
    second_weights = lane.migration_weights
    assert second_weights is first_weights, (
        "same object, not rebuilt, second generation"
    )
