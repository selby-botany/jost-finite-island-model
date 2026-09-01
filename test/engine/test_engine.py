"""End-to-end tests for the deterministic library engine."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from fim.engine import (
    FinalReport,
    GenerationalBackend,
    LinealBackend,
    RunResult,
    SequentialAdvancer,
    fim,
    replicate_summary,
    report_for_state,
    run_batch,
)
from fim.model.allele import MINTED_ID_START, AlleleId
from fim.model.locus import LocusSpec
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
    assert all_result.report["generation"] == 15
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
