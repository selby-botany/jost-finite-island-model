"""End-to-end tests for the deterministic library engine."""

import functools
import json
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

import numpy as np
import pytest
from conftest import FAST_CONVERGENCE, FAST_EXPERT_SETTINGS

from fim import engine
from fim.config.expert import ExpertSettings
from fim.convergence import StopReason
from fim.engine import (
    Clock,
    FinalReport,
    GenerationalBackend,
    LinealBackend,
    ReplicaLane,
    RunResult,
    SequentialAdvancer,
    ThreadedAdvancer,
    VectorizedAdvancer,
    _build_replica_lane,
    _convergence_values,
    _convergence_values_vectorized,
    bootstrap_replicate_summary,
    build_engine_backend,
    deterministic_run_id,
    fim,
    history_free_statistic_values,
    pair_statistic_values,
    pooled_convergence_histories,
    replicate_summary,
    report_for_state,
    reports_summary,
    run_batch,
)
from fim.model.allele import MINTED_ID_START, AlleleId
from fim.model.initial import _mean_h_s, generate_initial_state
from fim.model.locus import LocusSpec, finite_allele_capacity
from fim.model.operators import _population_sizes
from fim.model.params import ConvergenceCombinator, EngineBackend, SimulationParams
from fim.model.state import ModelState
from fim.model.vector_block import (
    MIGRATION_MATRIX,
    MIGRATION_SCALAR,
    VectorBlock,
    VectorMigration,
)
from fim.persistence.binary_store import BinaryLogStore
from fim.persistence.store import (
    InMemoryTrajectoryStore,
    TrajectoryRow,
    TrajectoryStore,
)
from fim.statistics import differentiation
from fim.statistics.catalog import report_keys
from fim.statistics.differentiation import (
    _jost_d_from_within_and_total,
    derived_differentiation,
)
from fim.statistics.genetic_distance import (
    NEI_DENOMINATORS,
    NEI_LOCUS_RULES,
    nei_all_demes_identity,
    nei_pair_identity,
)


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
        "ploidy": "haploid",
        "m": 0.1,
        "mu": 0.01,
        "d": 2,
        "seed": 20260814,
        "loci": [{"locus_id": 1, "length": 200}],
        "precision": 1.0,
        "convergence_burn_in": 1,
        "expert": dict(FAST_EXPERT_SETTINGS),
        "max_generations": 40,
        "n_replicates": 1,
        "stop_batch_early": False,
    }


def _run(params: SimulationParams) -> RunResult:
    """Run one scalar configuration and narrow the output type."""
    result = fim(
        params.gene_copies,
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
        # The one part of a report only the run's own monitor can supply.
        window_statistics=result.report["window_statistics"],
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
    `precision=0.0` requires the two half-window means to
    match exactly, which a real drifting `D` trajectory essentially never
    does in two generations.
    """
    params = SimulationParams.from_mapping(
        {
            **tiny_params.to_dict(),
            "precision": 0.0,
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
    """Each replicate is the single run its own parameters describe.

    A replicate of a batch is an independent run with seed `seed + index`
    that averages for the window the batch assigned it; its recorded
    parameters (`n_replicates` one, the window explicit) reproduce it exactly.
    """
    batched_params = SimulationParams.from_mapping(
        {**tiny_params.to_dict(), "n_replicates": 2}
    )
    store = InMemoryTrajectoryStore()

    output = fim(
        batched_params.gene_copies,
        batched_params.m,
        batched_params.mu,
        batched_params.d,
        params=batched_params,
        store=store,
        clock=_clock,
    )

    assert isinstance(output, tuple)
    assert len(output) == 2
    assert output[0].params.replicate_averaging_window > 0
    assert _run(output[0].params).final_state == output[0].final_state
    assert _run(output[1].params).final_state == output[1].final_state
    assert output[0].params.seed == tiny_params.seed
    assert output[1].params.seed == tiny_params.seed + 1


def test_public_signature_mismatches_are_reported(
    tiny_params: SimulationParams,
) -> None:
    """The legacy positional arguments must agree with the parameter bag."""
    cases = (
        (21, tiny_params.m, tiny_params.mu, tiny_params.d, "gene_copies"),
        (tiny_params.gene_copies, 0.2, tiny_params.mu, tiny_params.d, "m"),
        (tiny_params.gene_copies, tiny_params.m, 0.2, tiny_params.d, "mu"),
        (tiny_params.gene_copies, tiny_params.m, tiny_params.mu, 3, "d"),
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
        params.gene_copies,
        params.m,
        params.mu,
        params.d,
        params=params,
        run_id="batch",
        clock=_clock,
    )
    assert isinstance(output, tuple)
    assert [result.run_id for result in output] == ["batch-r001", "batch-r002"]


def test_stopping_the_batch_early_off_is_unaffected_by_the_adaptive_machinery(
    tiny_params: SimulationParams,
) -> None:
    """`stop_batch_early` off keeps the fixed-count batch loop exact."""
    params = SimulationParams.from_mapping({**tiny_params.to_dict(), "n_replicates": 4})
    assert params.batch_precision is None
    output = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, clock=_clock
    )
    assert isinstance(output, tuple)
    assert len(output) == 4


def test_a_generous_precision_can_stop_the_batch_before_the_cap() -> None:
    """A generous tolerance stops as soon as `replicate_minimum` is reached."""
    params = SimulationParams.from_mapping(
        {
            **_tiny_config(),
            "n_replicates": 10,
            "replicate_minimum": 3,
            # Any statistic this project reports is bounded in [0, 1], so a
            # tolerance this large is always satisfied once the minimum
            # sample is available — the stop is deterministic, not lucky.
            "precision": 1000.0,
            "stop_batch_early": True,
        }
    )
    output = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, clock=_clock
    )
    assert isinstance(output, tuple)
    assert len(output) == 3


def test_generational_adaptive_stop_discards_abandoned_lanes_own_rows() -> None:
    """No abandoned lane's own rows survive an adaptive stop in a shared store.

    Regression test for `FIM-49`: `_build_replica_lane` writes each
    lane's own generation zero eagerly, before `run_batch`'s own
    generation-first loop ever runs — so even a lane the loop never
    gets to advance at all (never finalized, no `RunResult`) still has
    rows in `store` that need discarding, not just lanes that got
    partway through. Checked against `store.read` (the public
    contract), never `store._rows` directly.
    """
    params = SimulationParams.from_mapping(
        {
            **_tiny_config(),
            "n_replicates": 10,
            "replicate_minimum": 3,
            "precision": 1000.0,
            "stop_batch_early": True,
            "engine_backend": "generational",
        }
    )
    store = InMemoryTrajectoryStore()

    output = fim(
        params.gene_copies,
        params.m,
        params.mu,
        params.d,
        params=params,
        store=store,
        clock=_clock,
    )

    assert isinstance(output, tuple)
    assert len(output) == 3
    returned_run_ids = {result.run_id for result in output}
    every_possible_run_id = {
        deterministic_run_id(replace(params, seed=params.seed + index, n_replicates=1))
        for index in range(params.n_replicates)
    }
    still_present_run_ids = {
        run_id for run_id in every_possible_run_id if list(store.read(run_id))
    }
    assert still_present_run_ids == returned_run_ids


def test_run_batch_bounds_concurrently_active_lanes_to_the_configured_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`max_concurrent_replicates` caps how many lanes are ever alive at once.

    Regression test for `FIM-45`/`FIM-48`: `run_batch` now builds lanes
    lazily, only as an earlier lane's own slot frees up, rather than all
    `n_replicates` of them up front. Instruments `_build_replica_lane`/
    `_finalize_replica_lane` (both still calling through to the real
    implementation) to track the running "built but not yet finalized"
    count directly, rather than trusting only the final result count —
    a bug that built every lane immediately but still returned the
    right *count* of results would pass a result-count-only check.
    """
    params = SimulationParams.from_mapping(
        {
            **_tiny_config(),
            "n_replicates": 6,
            "engine_backend": "generational",
            "max_concurrent_replicates": 2,
        }
    )
    concurrently_active = 0
    peak_concurrently_active = 0
    build_call_count = 0
    real_build = engine._build_replica_lane
    real_finalize = engine._finalize_replica_lane

    def _counting_build(
        params: SimulationParams,
        replica_index: int,
        run_id: str | None,
        store: TrajectoryStore,
        clock: Clock,
        planner: object = None,
    ) -> ReplicaLane:
        nonlocal concurrently_active, peak_concurrently_active, build_call_count
        lane = real_build(params, replica_index, run_id, store, clock, planner)  # type: ignore[arg-type]
        build_call_count += 1
        concurrently_active += 1
        peak_concurrently_active = max(peak_concurrently_active, concurrently_active)
        return lane

    def _counting_finalize(
        lane: ReplicaLane, clock: Clock, store: TrajectoryStore
    ) -> RunResult:
        nonlocal concurrently_active
        result = real_finalize(lane, clock, store)
        concurrently_active -= 1
        return result

    monkeypatch.setattr(engine, "_build_replica_lane", _counting_build)
    monkeypatch.setattr(engine, "_finalize_replica_lane", _counting_finalize)

    output = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, clock=_clock
    )

    assert isinstance(output, tuple)
    assert len(output) == 6
    assert build_call_count == 6
    assert peak_concurrently_active == 2


def _report_without_run_id(report: FinalReport) -> dict[str, object]:
    """Strip `run_id` for a report comparison that ignores it deliberately.

    Shared by every "windowing changes nothing about what a batch
    computes" test below — `FinalReport.run_id` is expected to differ
    whenever `max_concurrent_replicates` does (`deterministic_run_id`
    hashes a run's own entire configuration), so it is excluded here
    rather than making every call site remember to do so.
    """
    return {key: value for key, value in report.items() if key != "run_id"}


def test_max_concurrent_replicates_does_not_change_what_a_batch_computes() -> None:
    """A window changes only *when* lanes are built, never a run's own trajectory.

    `n_replicates=6` against a window of `2` versus no window (`None`,
    every prior release's own behavior) must agree exactly, replicate
    for replicate: `run_batch`'s own generation-first ordering already
    made every lane's own result depend only on that lane's own prior
    state (see its docstring) — windowing changes only when a lane is
    constructed and advanced relative to another, which that same
    argument already covers. `run_id` itself is deliberately excluded
    from the comparison: `deterministic_run_id` hashes a run's own
    *entire* configuration (`engine_backend`/`jit`/`auto_vector_min_d`
    already do the same), so a differing `max_concurrent_replicates`
    changing `run_id` is expected, not a defect — what must not differ
    is what the run actually computed.
    """
    base_params = SimulationParams.from_mapping(
        {
            **_tiny_config(),
            "n_replicates": 6,
            "engine_backend": "generational",
        }
    )
    unbounded = fim(
        base_params.gene_copies,
        base_params.m,
        base_params.mu,
        base_params.d,
        params=base_params,
        clock=_clock,
    )
    windowed_params = replace(base_params, max_concurrent_replicates=2)
    windowed = fim(
        windowed_params.gene_copies,
        windowed_params.m,
        windowed_params.mu,
        windowed_params.d,
        params=windowed_params,
        clock=_clock,
    )

    assert isinstance(unbounded, tuple)
    assert isinstance(windowed, tuple)
    for unbounded_result, windowed_result in zip(unbounded, windowed, strict=True):
        assert unbounded_result.final_state == windowed_result.final_state
        assert _report_without_run_id(
            unbounded_result.report
        ) == _report_without_run_id(windowed_result.report)


def test_replicate_minimum_above_n_replicates_runs_to_completion() -> None:
    """`replicate_minimum` above `n_replicates` is clamped, not rejected.

    Regression test, superseding an earlier version of this same test
    that asserted the *opposite*: `replicate_minimum=100` with
    `n_replicates=3` used to raise `ValueError` at construction
    (adaptive stopping could never even be evaluated, let alone fire,
    so the config was rejected as describing something structurally
    impossible). Changed once `stop_batch_early` became the default
    to `None` (`fim.model.params.SimulationParams.__post_init__`'s own
    comment has the full reasoning): the identical combination now
    arises from nothing more deliberate than setting a small `n_
    replicates` without separately thinking about `replicate_minimum`
    at all — found live, not assumed, when every GUI batch test that
    only ever sets `n_replicates` failed exactly this way in CI
    (`jost-finite-island-model` run 33656031751). `replicate_minimum`
    is now silently clamped down to `n_replicates` instead, so the
    adaptive check becomes reachable at the last possible replicate
    rather than never — this test confirms the clamp (`params.
    replicate_minimum == 3`, not `100`) and that the batch actually
    completes with the full `n_replicates` count, not fewer.
    """
    params = SimulationParams.from_mapping(
        {
            **_tiny_config(),
            "n_replicates": 3,
            "replicate_minimum": 100,
            "precision": 1000.0,
            "stop_batch_early": True,
        }
    )
    assert params.replicate_minimum == 3

    output = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, clock=_clock
    )
    assert isinstance(output, tuple)
    assert len(output) == 3


def test_replicate_tolerance_never_stops_on_a_permanently_undefined_statistic() -> None:
    """A batch watching only an always-undefined `G_ST` runs to the full cap.

    Regression test: every replicate here is fully monomorphic at
    its one locus, so `G_ST` is undefined for every one of them and its
    stopping-criterion window never fills. The batch correctly falls back
    to the `n_replicates` cap rather than the prior behavior, where
    substituting `0.0` for every undefined replicate produced a constant
    zero history that satisfied an exact `precision=0.0`
    immediately at `replicate_minimum` — a fabricated "convergence" the
    run's actual (complete lack of) data never supported.
    """
    params = SimulationParams(
        gene_copies=10,
        m=0.0,
        mu=0.0,
        d=2,
        seed=7,
        loci=(LocusSpec(1, 100),),
        convergence_statistic="G_ST",
        max_generations=2,
        n_replicates=5,
        replicate_minimum=2,
        precision=0.0,
        stop_batch_early=True,
        initial_frequencies=(
            ({AlleleId(0): 1.0},),
            ({AlleleId(0): 1.0},),
        ),
    )
    output = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, clock=_clock
    )
    assert isinstance(output, tuple)
    assert len(output) == 5
    assert all(result.report["G_ST"] is None for result in output)
    assert "G_ST" not in replicate_summary(output)


def test_replicate_summary_reports_a_confidence_interval_per_statistic(
    tiny_params: SimulationParams,
) -> None:
    """The batch summary covers every statistic with at least two samples."""
    params = SimulationParams.from_mapping({**tiny_params.to_dict(), "n_replicates": 5})
    output = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, clock=_clock
    )
    assert isinstance(output, tuple)

    summary = replicate_summary(output)

    assert set(summary) == set(report_keys())
    assert summary["D"]["sample_count"] == 5
    assert summary["D"]["low"] <= summary["D"]["mean"] <= summary["D"]["high"]
    assert summary["D"]["confidence"] == 0.95


def test_replicate_summary_reports_a_real_sample_standard_deviation(
    tiny_params: SimulationParams,
) -> None:
    """Every Student's-t interval carries the spread of its own replicates.

    The sample-standard-deviation half of botanist GUI design doc
    `20260907-claude-sonnet-5-botanist-gui-redesign.md` §7.2 (see
    `20260912-claude-sonnet-5-sample-std-dev-tooltip-design.md`,
    `selby/restricted`): `confidence_interval` is the symmetric
    constructor, so `sample_std` is a real number here for every
    statistic — never `None`, which this project reserves for the
    percentile-bootstrap constructor that has no single honest value to
    report (see the bootstrap counterpart test below).
    """
    params = SimulationParams.from_mapping({**tiny_params.to_dict(), "n_replicates": 5})
    output = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, clock=_clock
    )
    assert isinstance(output, tuple)

    summary = replicate_summary(output)

    for name, interval in summary.items():
        sample_std = interval["sample_std"]
        assert sample_std is not None, name
        assert sample_std >= 0.0, name
    # Recomputed straight from the same replicates' own window means of `D`
    # (what each replicate measured), so this pins the reported number against
    # the sample it claims to describe rather than against the
    # implementation's own expression.
    draws = [result.report["window_statistics"]["D"]["mean"] for result in output]
    mean = statistics.fmean(draws)
    variance = sum((value - mean) ** 2 for value in draws) / (len(draws) - 1)
    assert summary["D"]["sample_std"] == pytest.approx(math.sqrt(variance))


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
        # A per-run nested payload (`fim.convergence.window_statistics`), not
        # a single number to average across replicates the way every other
        # field here is -- `reports_summary`'s own across-replicate interval
        # already exists for that; this field answers a different question
        # (how precise was *this one run's* own trailing-window mean).
        "window_statistics",
    }
    numeric_fields = set(FinalReport.__annotations__) - non_statistic_fields
    params = SimulationParams.from_mapping({**tiny_params.to_dict(), "n_replicates": 5})
    output = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, clock=_clock
    )
    assert isinstance(output, tuple)

    summary = replicate_summary(output)

    assert set(summary) == numeric_fields


def test_pooled_convergence_histories_carries_a_stopped_replicates_value_forward() -> (
    None
):
    """A replicate's own `sample_count` contribution never disappears once it stops.

    Batch trajectory panel design `20260912-claude-sonnet-5-batch-
    trajectory-panel-design.md` (`selby/restricted`): a real 5-replicate
    batch, each replicate stopping at its own (stochastic, but fully
    deterministic for this fixed seed) generation -- the exact
    "replicates stop at different generations" case commit 2's own
    first draft got wrong, reported live: counting only the replicates
    *still running* at a later generation is systematically biased
    (a replicate stops because it converged, not at random, so the
    ones still running later are the stragglers, not a representative
    subset) and produced a real, confirmed case where the very next
    generation's own interval, computed from only 6 of 20 remaining
    stragglers, was several times wider than the generation before it
    — for no reason related to the population's actual behavior. This
    function now holds each replicate's own final value constant for
    every later generation too, so `sample_count` stays at `len(results)`
    for the entire plotted range instead of shrinking as replicates
    finish -- structural invariants below hold regardless of exactly
    *which* generation each replicate happens to stop at, so this test
    does not depend on that stochastic detail beyond the fixed seed
    already making it reproducible.

    Not built from `tiny_params`: replicates of a batch average for an
    assigned window, so they stop together unless the windows differ. This
    test uses a first wave of two replicates (the Expert Setting
    `batch_width`), which average for the first-wave guess, while the other
    three average for the window matched to them, so the stops are staggered
    (`[120, 120, 120, 300, 300]`, confirmed live for this exact
    configuration).
    """
    params = SimulationParams(
        gene_copies=20,
        m=0.1,
        mu=0.01,
        d=2,
        seed=42,
        loci=(LocusSpec(1, 200),),
        precision=0.1,
        convergence_burn_in=1,
        expert=ExpertSettings(**{**FAST_EXPERT_SETTINGS, "batch_width": 2}),
        max_generations=300,
        n_replicates=5,
        stop_batch_early=False,
    )
    output = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, clock=_clock
    )
    assert isinstance(output, tuple)
    final_generations = sorted(result.report["generation"] for result in output)
    # A real precondition for the rest of this test to mean anything:
    # if every replicate happened to stop at the identical generation,
    # there would be nothing for "carries a value forward" to exercise.
    assert len(set(final_generations)) > 1, (
        "fixture no longer produces staggered stopping generations -- "
        "pick a different seed/tolerance so this test still exercises "
        "the carry-forward case it is named for"
    )
    earliest_stop = final_generations[0]

    pooled = pooled_convergence_histories(output)

    # Only the always-tracked five (`fim.engine._ALWAYS_TRACKED_
    # STATISTICS`), not all ten `STATISTIC_NAMES` the completed
    # scalar/live-batch trajectory panels can show -- `E_ST`/`K_ST`
    # only ever get a *per-generation* history at all with
    # `track_expensive_statistics=True` (unset here), unlike a live
    # tick's own `report_for_state`, which always computes a full
    # report regardless of that setting.
    assert set(pooled) == {"D", "G_ST", "H_S", "H_T", "H_ST"}
    for name, points in pooled.items():
        generations = [point["generation"] for point in points]
        # Dense, not sparse: every integer generation from 0 through
        # the slowest replicate's own final one, not only generations
        # some replicate happened to stop at.
        assert generations == list(range(final_generations[-1] + 1)), (
            f"{name}: not a dense, ascending 0..max range"
        )
        sample_counts = [point["sample_count"] for point in points]
        # The whole point of carrying a value forward: every replicate
        # still counts everywhere, so this never drops below 5, unlike
        # the pre-fix behavior this test's own docstring describes. A
        # statistic a replicate has no value for in some generation (`G_ST`
        # is undefined while a replicate has lost all its variation) has
        # fewer, never more, and only that statistic.
        always_defined = all(
            len(result.convergence_histories[name])
            == len(result.convergence_generations)
            for result in output
        )
        if always_defined:
            assert all(count == 5 for count in sample_counts), (
                f"{name}: sample_count dropped below the full replicate "
                f"count somewhere -- carry-forward is not working"
            )
        else:
            assert all(count <= 5 for count in sample_counts)
        assert all(point["low"] <= point["mean"] <= point["high"] for point in points)
    assert pooled["D"][0]["generation"] == 0
    # The generation right after the earliest replicate stops is
    # exactly the point the pre-fix code got wrong (that replicate
    # would have vanished from the pool there instead of contributing
    # its own carried-forward value) -- still a full sample count now.
    assert pooled["D"][earliest_stop + 1]["sample_count"] == 5


def test_pooled_convergence_histories_requires_at_least_two_results(
    tiny_params: SimulationParams,
) -> None:
    """The same "single replicate has no interval" guard `replicate_summary` applies."""
    output = fim(
        tiny_params.gene_copies,
        tiny_params.m,
        tiny_params.mu,
        tiny_params.d,
        params=tiny_params,
    )
    assert isinstance(output, RunResult)

    with pytest.raises(ValueError, match="at least two results"):
        pooled_convergence_histories(())
    with pytest.raises(ValueError, match="at least two results"):
        pooled_convergence_histories((output,))


def test_pooled_convergence_histories_drops_a_replicate_with_an_interior_gap(
    tiny_params: SimulationParams,
) -> None:
    """A statistic shorter than `convergence_generations` is dropped, not guessed at.

    `ConvergenceMonitor.record` can genuinely omit one tracked statistic
    on some round without omitting the others (`G_ST`, whenever every
    tracked locus is currently monomorphic -- `_convergence_values`'s
    own docstring), leaving that one statistic's own history shorter
    than `convergence_generations` for that one replicate. Without a
    per-statistic generation list (`ConvergenceMonitor` does not track
    one), this function cannot know *which* generation was skipped, so
    it drops that replicate's own contribution to that one statistic
    entirely (`pooled_convergence_histories`'s own docstring) rather
    than guessing an alignment that could silently pair a real value
    with the wrong generation. Simulated here by shortening one real
    replicate's own `G_ST` history by one entry after the fact
    (`dataclasses.replace`, `RunResult` is frozen) -- deliberately not
    a hand-built `RunResult` from scratch, so every other field stays
    exactly what a real run actually produced.
    """
    params = replace(tiny_params, n_replicates=2)
    output = fim(params.gene_copies, params.m, params.mu, params.d, params=params)
    assert isinstance(output, tuple)
    assert len(output) == 2
    intact, corrupted = output
    shortened_histories = dict(corrupted.convergence_histories)
    shortened_histories["G_ST"] = shortened_histories["G_ST"][:-1]
    corrupted = replace(corrupted, convergence_histories=shortened_histories)

    pooled = pooled_convergence_histories((intact, corrupted))

    # `G_ST` drops out of the pool entirely: with only 2 replicates
    # total and one dropped for this one statistic, no generation ever
    # has the required minimum of 2 defined values left.
    assert "G_ST" not in pooled
    # Every other statistic is untouched -- the corruption above only
    # ever touched `G_ST`'s own history, on one replicate.
    assert set(pooled) == {"D", "H_S", "H_T", "H_ST"}
    for name in ("D", "H_S", "H_T", "H_ST"):
        assert all(point["sample_count"] == 2 for point in pooled[name])


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
    output = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, clock=_clock
    )
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
    output = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, max_workers=2
    )
    assert isinstance(output, tuple)
    assert sorted(result.params.seed for result in output) == [0, 1, 2, 3]


def test_max_workers_produces_the_same_replicates_as_sequential_execution() -> None:
    """Parallel batching changes nothing about the computed results."""
    params = SimulationParams.from_mapping({**_tiny_config(), "n_replicates": 4})

    sequential = fim(params.gene_copies, params.m, params.mu, params.d, params=params)
    parallel = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, max_workers=2
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
        params.gene_copies,
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
            tiny_params.gene_copies,
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
            params.gene_copies,
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
        fim(
            params.gene_copies,
            params.m,
            params.mu,
            params.d,
            params=params,
            max_workers=0,
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_workers": 0}, "max_workers must be at least 1"),
        (
            {"store": InMemoryTrajectoryStore(), "max_workers": 2},
            "max_workers requires store=None",
        ),
    ],
)
def test_single_replicate_run_still_validates_max_workers(
    kwargs: dict[str, object], message: str
) -> None:
    """A single-replicate run no longer bypasses `max_workers` validation.

    Regression test for FIM-05: `LinealBackend.run`'s own early return
    for `n_replicates == 1` used to skip straight past every `max_
    workers` check below it in the function — a `max_workers=0`, or a
    `store` given alongside `max_workers`, both silently succeeded
    there instead of raising, the exact validation a multi-replicate
    batch (`n_replicates > 1`) already enforced.
    """
    params = SimulationParams.from_mapping(_tiny_config())
    with pytest.raises(ValueError, match=message):
        fim(
            params.gene_copies,
            params.m,
            params.mu,
            params.d,
            params=params,
            **kwargs,  # type: ignore[arg-type]
        )


def test_single_replicate_run_uses_store_factory(tmp_path: Path) -> None:
    """A single-replicate run honors `store_factory`, not just a shared `store`.

    Regression test for FIM-05: `LinealBackend.run`'s own `n_replicates
    == 1` early return used to ignore a given `store_factory` entirely,
    silently building a fresh `InMemoryTrajectoryStore()` instead — the
    one place `store_factory` had no effect at all. A real, file-backed
    factory (rather than another `InMemoryTrajectoryStore`, which the
    silent fallback also produces, and so could not distinguish the two)
    proves it was actually called: its own file only exists on disk if
    the factory itself ran.
    """
    params = SimulationParams.from_mapping(_tiny_config())

    output = fim(
        params.gene_copies,
        params.m,
        params.mu,
        params.d,
        params=params,
        store_factory=functools.partial(_log_store_factory, tmp_path),
    )

    assert isinstance(output, RunResult)
    assert (tmp_path / f"{output.run_id}.tlog").exists()


def test_max_workers_rejects_an_unpicklable_clock() -> None:
    """A closure `clock` fails at the call site, not deep in worker spawn.

    Regression test: the prior behavior let an unpicklable
    `clock` reach `ProcessPoolExecutor`, where it failed as raw pickling
    noise from inside worker-process spawn machinery.
    """
    params = SimulationParams.from_mapping({**_tiny_config(), "n_replicates": 2})
    with pytest.raises(ValueError, match="clock must be picklable"):
        fim(
            params.gene_copies,
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
            params.gene_copies,
            params.m,
            params.mu,
            params.d,
            params=params,
            max_workers=2,
            store_factory=lambda _run_id: InMemoryTrajectoryStore(),
        )


def test_max_workers_respects_adaptive_stopping_in_batches() -> None:
    """Batched parallel replicates still honor the early stop.

    A batch can overshoot the exact minimal replicate count by at most
    ``max_workers - 1``, since the stopping decision is only applied once
    a whole concurrent batch has completed.
    """
    params = SimulationParams.from_mapping(
        {
            **_tiny_config(),
            "n_replicates": 10,
            "replicate_minimum": 3,
            "precision": 1000.0,
            "stop_batch_early": True,
        }
    )
    output = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, max_workers=2
    )
    assert isinstance(output, tuple)
    assert 3 <= len(output) <= 4


def _log_store_factory(directory: Path, run_id: str) -> BinaryLogStore:
    """Module-level, `functools.partial`-bindable `store_factory` for FIM-50.

    A worker process must be able to pickle a reference to `store_
    factory` itself, ruling out a closure or lambda; a test binds
    `directory` to its own `tmp_path` via `functools.partial` before
    passing this through, giving every replicate a real, file-backed
    store on disk (unlike `_in_memory_store_factory` below, which
    ignores `run_id` entirely and cannot show whether a specific run's
    own artifacts survived).
    """
    return BinaryLogStore(directory / f"{run_id}.tlog")


def test_parallel_batch_adaptive_stop_discards_overshoot_replicates_artifacts(
    tmp_path: Path,
) -> None:
    """No overshoot replicate's own persisted file survives an adaptive stop.

    Regression test for `FIM-50`: with `max_workers=2` and a real,
    file-backed `store_factory`, an adaptive stop can leave up to
    `max_workers - 1` already-running "overshoot" replicates finishing
    after the stop decision — their own store artifacts must be
    discarded from disk, not left behind with no `RunResult` in the
    return value to account for them.
    """
    params = SimulationParams.from_mapping(
        {
            **_tiny_config(),
            "n_replicates": 10,
            "replicate_minimum": 3,
            "precision": 1000.0,
            "stop_batch_early": True,
        }
    )

    output = fim(
        params.gene_copies,
        params.m,
        params.mu,
        params.d,
        params=params,
        max_workers=2,
        store_factory=functools.partial(_log_store_factory, tmp_path),
    )

    assert isinstance(output, tuple)
    assert 3 <= len(output) <= 4
    returned_run_ids = {result.run_id for result in output}
    surviving_run_ids = {path.stem for path in tmp_path.glob("*.tlog")}
    assert surviving_run_ids == returned_run_ids


def _in_memory_store_factory(run_id: str) -> InMemoryTrajectoryStore:
    """Module-level `store_factory` — a worker process must be able to
    pickle a reference to it, which a closure or lambda cannot survive."""
    del run_id  # unused; signature-compatible with `fim`'s `store_factory`
    return InMemoryTrajectoryStore()


def test_max_workers_uses_store_factory_per_replicate() -> None:
    """Each worker gets its own store, built by `store_factory` in-process."""
    params = SimulationParams.from_mapping({**_tiny_config(), "n_replicates": 2})
    output = fim(
        params.gene_copies,
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


@pytest.mark.parametrize("engine_backend", ["generational", "generational-vector"])
def test_non_lineal_batch_uses_store_factory_per_replicate(
    engine_backend: str, tmp_path: Path
) -> None:
    """A batch under a non-`lineal` backend now honors `store_factory` too.

    `20260914-claude-sonnet-5-non-lineal-batch-execution-design.md`
    (`selby/restricted`), §5.2: `GenerationalBackend` wraps a given
    `store_factory` in a `ReplicateFanoutStore`, so every replicate ends
    up with its own real, independent store — the same outcome
    `LinealBackend`'s own `store_factory` path already produces
    (`test_store_factory_gives_every_sequential_replicate_its_own_store`,
    above), closing the one real gap that made a CLI/GUI batch under any
    backend but `lineal` unreachable.
    """
    if engine_backend == "generational-vector":
        pytest.importorskip("numba")
        base_params = _finite_alleles_vector_params(n_replicates=2)
    else:
        base_params = SimulationParams.from_mapping(
            {**_tiny_config(), "n_replicates": 2}
        )
    params = replace(base_params, engine_backend=engine_backend)  # type: ignore[arg-type]

    output = fim(
        params.gene_copies,
        params.m,
        params.mu,
        params.d,
        params=params,
        store_factory=functools.partial(_log_store_factory, tmp_path),
    )

    assert isinstance(output, tuple)
    assert len(output) == 2
    assert output[0].run_id != output[1].run_id
    for result in output:
        assert (tmp_path / f"{result.run_id}.tlog").exists()
        assert list(result.store.read(result.run_id))


def test_non_lineal_single_replicate_run_uses_store_factory(tmp_path: Path) -> None:
    """A scalar run under a non-`lineal` backend also honors `store_factory`.

    `GenerationalBackend.run` always calls `run_batch`, even for
    `n_replicates == 1` — no separate scalar fast path the way
    `LinealBackend` has — so this is really the same code path as the
    batch case above, checked directly for `n_replicates == 1` too
    since `LinealBackend`'s own analogous scalar/store_factory
    interaction was a real, previously-shipped bug (FIM-05,
    `test_single_replicate_run_uses_store_factory`, above).
    """
    params = replace(
        SimulationParams.from_mapping(_tiny_config()), engine_backend="generational"
    )

    output = fim(
        params.gene_copies,
        params.m,
        params.mu,
        params.d,
        params=params,
        store_factory=functools.partial(_log_store_factory, tmp_path),
    )

    assert isinstance(output, RunResult)
    assert (tmp_path / f"{output.run_id}.tlog").exists()


def test_replicate_summary_requires_at_least_two_results(
    tiny_params: SimulationParams,
) -> None:
    """A single result has no interval to compute."""
    with pytest.raises(ValueError, match="at least two"):
        replicate_summary(())
    with pytest.raises(ValueError, match="at least two"):
        replicate_summary((_run(tiny_params),))


def test_bootstrap_replicate_summary_point_estimate_matches_the_pooled_ratio(
    tiny_params: SimulationParams,
) -> None:
    """The point estimate is the grand ratio-of-means, not a mean of ratios.

    Exit-criterion test for `R3` part 3 of `dev/doc/apps/selby/jost-
    finite-island-model/20260903-claude-opus-5-gene-identity-recursion-
    fim-implications.md`: `bootstrap_replicate_summary`'s own `"mean"`
    field must equal `1 - mean(Gd_i) / mean(Gs_i)` computed directly
    from the same batch's own reports — the "ratio of means across
    everything" the identity recursion predicts — not `mean(D_i)`
    (`replicate_summary`'s own estimator, a mean of ratios, subject to
    a real Jensen-gap bias `D`/`G_ST` are the only statistics with).
    """
    params = SimulationParams.from_mapping(
        {**tiny_params.to_dict(), "n_replicates": 8, "stop_batch_early": False}
    )
    output = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, clock=_clock
    )
    assert isinstance(output, tuple)

    summary = bootstrap_replicate_summary(output, rng=np.random.default_rng(1))

    mean_gs = statistics.fmean(result.report["Gs"] for result in output)
    mean_gd = statistics.fmean(result.report["Gd"] for result in output)
    within = 1.0 - mean_gs
    total = 1.0 - ((params.d - 1) * mean_gd + mean_gs) / params.d
    expected_d = ((total - within) / (1.0 - within)) * params.d / (params.d - 1)
    mean_of_per_replicate_d = statistics.fmean(result.report["D"] for result in output)

    assert summary["D"]["mean"] == pytest.approx(expected_d)
    # Not a vacuous check: the two estimators must actually differ here,
    # or this test could pass even if the implementation silently fell
    # back to `replicate_summary`'s own mean-of-ratios estimator.
    assert summary["D"]["mean"] != pytest.approx(mean_of_per_replicate_d)


def test_bootstrap_replicate_summary_interval_contains_its_own_point_estimate(
    tiny_params: SimulationParams,
) -> None:
    """The reported interval actually brackets the reported point estimate."""
    params = SimulationParams.from_mapping(
        {**tiny_params.to_dict(), "n_replicates": 8, "stop_batch_early": False}
    )
    output = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, clock=_clock
    )
    assert isinstance(output, tuple)

    summary = bootstrap_replicate_summary(output, rng=np.random.default_rng(2))

    for statistic, interval in summary.items():
        assert interval["low"] <= interval["mean"] <= interval["high"], statistic
        assert interval["sample_count"] == len(output)
        assert interval["confidence"] == 0.95


def test_bootstrap_replicate_summary_reports_no_sample_standard_deviation(
    tiny_params: SimulationParams,
) -> None:
    """A percentile-bootstrap interval reports `sample_std` as `None`.

    The deliberate asymmetric case of the sample-standard-deviation
    design (`20260912-claude-sonnet-5-sample-std-dev-tooltip-design.md`,
    `selby/restricted`, approach A1): `_bootstrap_interval` never sees a
    per-replicate sample of the statistic at all — only a point estimate
    and a distribution of resampled *grand ratios* — so there is no
    single number that honestly describes how much the replicates
    differ from each other. `None` states that affirmatively, and is
    what the GUI's own tooltip reads as "this interval has no honest
    symmetric summary; show `low`/`high` alone."

    Paired with an explicit check that the interval really is asymmetric
    here, so this test cannot pass for the uninteresting reason that the
    bootstrap happened to reproduce a symmetric interval on this batch.
    """
    params = SimulationParams.from_mapping(
        {**tiny_params.to_dict(), "n_replicates": 8, "stop_batch_early": False}
    )
    output = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, clock=_clock
    )
    assert isinstance(output, tuple)

    summary = bootstrap_replicate_summary(output, rng=np.random.default_rng(3))

    assert summary, "the bootstrap summary defines at least `D`"
    for statistic, interval in summary.items():
        assert interval["sample_std"] is None, statistic
    left = summary["D"]["mean"] - summary["D"]["low"]
    right = summary["D"]["high"] - summary["D"]["mean"]
    assert left != pytest.approx(right, abs=1e-12)


def test_bootstrap_replicate_summary_is_deterministic_for_a_given_rng_state(
    tiny_params: SimulationParams,
) -> None:
    """The same `rng` state reproduces the identical interval, bit for bit.

    Bootstrap resampling is still real randomness — this project's own
    zero-tolerance-for-nondeterminism discipline requires it to be
    exactly reproducible for a given, explicitly threaded `rng` seed,
    the same as every other source of randomness in this project.
    """
    params = SimulationParams.from_mapping(
        {**tiny_params.to_dict(), "n_replicates": 6, "stop_batch_early": False}
    )
    output = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, clock=_clock
    )
    assert isinstance(output, tuple)

    first = bootstrap_replicate_summary(output, rng=np.random.default_rng(7))
    second = bootstrap_replicate_summary(output, rng=np.random.default_rng(7))

    assert first == second


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"confidence": 0.0}, "confidence"),
        ({"confidence": 1.0}, "confidence"),
        ({"bootstrap_samples": 0}, "bootstrap_samples"),
    ],
)
def test_bootstrap_replicate_summary_rejects_invalid_inputs(
    tiny_params: SimulationParams,
    kwargs: dict[str, object],
    message: str,
) -> None:
    """Every keyword argument is validated, not passed straight to numpy."""
    params = SimulationParams.from_mapping(
        {**tiny_params.to_dict(), "n_replicates": 4, "stop_batch_early": False}
    )
    output = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, clock=_clock
    )
    assert isinstance(output, tuple)

    with pytest.raises(ValueError, match=message):
        bootstrap_replicate_summary(
            output,
            rng=np.random.default_rng(1),
            **kwargs,  # type: ignore[arg-type]
        )


def test_bootstrap_replicate_summary_requires_at_least_two_results(
    tiny_params: SimulationParams,
) -> None:
    """A single result has no batch to resample."""
    with pytest.raises(ValueError, match="at least two"):
        bootstrap_replicate_summary((), rng=np.random.default_rng(1))
    with pytest.raises(ValueError, match="at least two"):
        bootstrap_replicate_summary((_run(tiny_params),), rng=np.random.default_rng(1))


def test_convergence_can_watch_h_st(tiny_params: SimulationParams) -> None:
    """A run can actually watch `H_ST` for convergence, not just report it.

    Regression test for `FIM-51`: `_report_statistic` used to raise
    `unsupported convergence statistic: H_ST` the instant a per-
    generation convergence check tried to read it, even though `H_ST` is
    a real, always-defined `DifferentiationReport` field (`fim.model.
    params._CONVERGENCE_STATISTICS` now allows selecting it in the first
    place — this is that config's own engine-level counterpart).
    """
    params = replace(tiny_params, convergence_statistic="H_ST")

    output = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, clock=_clock
    )

    assert isinstance(output, RunResult)
    assert output.report["H_ST"] is not None
    assert output.report["converged_on"] == "H_ST"


def test_final_report_gs_and_gd_match_the_pooled_h_s_and_h_t(
    tiny_params: SimulationParams,
) -> None:
    """`FinalReport`'s `Gs`/`Gd` are the linear closed forms of its own `H_S`/`H_T`.

    Regression/exit-criterion test for `R4` of `dev/doc/apps/selby/
    jost-finite-island-model/20260903-claude-opus-5-gene-identity-
    recursion-fim-implications.md`: run across several loci so `H_S`/
    `H_T` are themselves already averages (`report_for_state`'s own
    `mean_h_s`/`mean_h_t`) — `Gs`/`Gd` being linear in `H_S`/`H_T` means
    computing them from those already-pooled means must agree exactly
    with the formula in `fim.statistics.differentiation.gs`/`gd`'s own
    docstrings, not merely approximately.
    """
    params = replace(
        tiny_params, loci=(LocusSpec(1, 200), LocusSpec(2, 150), LocusSpec(3, 100))
    )

    result = _run(params)

    within = result.report["H_S"]
    total = result.report["H_T"]
    deme_count = params.d
    assert result.report["Gs"] == pytest.approx(1.0 - within)
    assert result.report["Gd"] == pytest.approx(
        (deme_count * (1.0 - total) - (1.0 - within)) / (deme_count - 1)
    )


def test_naive_manifest_clock_is_rejected(tiny_params: SimulationParams) -> None:
    """Manifest timestamps require an explicit timezone."""
    with pytest.raises(ValueError, match="timezone-aware"):
        fim(
            tiny_params.gene_copies,
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
        gene_copies=10,
        m=0.0,
        mu=0.0,
        d=2,
        seed=7,
        loci=(LocusSpec(1, 100),),
        convergence_statistic="G_ST",
        precision=0.0,
        max_generations=2,
        n_replicates=1,
        stop_batch_early=False,
        initial_frequencies=(
            ({AlleleId(0): 1.0},),
            ({AlleleId(0): 1.0},),
        ),
    )
    result = fim(
        params.gene_copies,
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
        gene_copies=10,
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
        max_generations=1,
        n_replicates=3,
        replicate_minimum=2,
        precision=1000.0,
        stop_batch_early=True,
        initial_frequencies=(
            ({AlleleId(0): 1.0}, {AlleleId(0): 0.5, AlleleId(1): 0.5}),
            ({AlleleId(0): 1.0}, {AlleleId(0): 0.5, AlleleId(1): 0.5}),
        ),
    )
    output = fim(params.gene_copies, params.m, params.mu, params.d, params=params)
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
        gene_copies=10,
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
        max_generations=1,
        n_replicates=3,
        replicate_minimum=2,
        precision=1000.0,
        stop_batch_early=True,
        initial_frequencies=(
            ({AlleleId(0): 1.0},),
            ({AlleleId(0): 1.0},),
        ),
    )
    output = fim(params.gene_copies, params.m, params.mu, params.d, params=params)
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
    result = _run(replace(tiny_params, convergence_statistic="D"))

    assert result.report["converged_on"] == "D"
    assert isinstance(result.report["converged_on"], str)
    # `D`/`G_ST`/`H_S`/`H_T`/`H_ST` are always present now, regardless of
    # what is actually watched (`fim.engine._ALWAYS_TRACKED_STATISTICS`)
    # — a display-only superset of the single watched statistic (`D`
    # here), which never itself changes what `converged_on` reports.
    assert set(result.convergence_histories) == {"D", "G_ST", "H_S", "H_T", "H_ST"}
    assert result.convergence_histories["D"] == result.convergence_history


def test_multi_statistic_run_watches_and_reports_every_statistic() -> None:
    """Watching several statistics is reproducible and reports every history."""
    params = SimulationParams(
        gene_copies=25,
        m=0.15,
        mu=0.03,
        d=3,
        seed=20260800,
        loci=(LocusSpec(1, 100),),
        convergence_statistic=("D", "G_ST"),
        convergence_combinator="all",
        precision=0.1,
        **FAST_CONVERGENCE,
        max_generations=300,
        n_replicates=1,
        stop_batch_early=False,
    )

    first = _run(params)
    second = _run(params)

    assert list(first.store.read(first.run_id)) == list(
        second.store.read(second.run_id)
    )
    assert first.report == second.report
    assert first.report["converged_on"] == ["D", "G_ST"]
    # `H_S`/`H_T`/`H_ST` ride along too, always (`_ALWAYS_TRACKED_
    # STATISTICS`) — `D`/`G_ST` here are both watched *and* always-
    # tracked, so this run's own `converged_on` is unaffected by the
    # three extra names.
    assert set(first.convergence_histories) == {"D", "G_ST", "H_S", "H_T", "H_ST"}
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
            gene_copies=25,
            m=0.15,
            mu=0.03,
            d=3,
            seed=20260800,
            loci=(LocusSpec(1, 100),),
            convergence_statistic=("D", "G_ST"),
            convergence_combinator=combinator,
            precision=0.1,
            **FAST_CONVERGENCE,
            max_generations=300,
            n_replicates=1,
            stop_batch_early=False,
        )

    any_params = _params("any")
    all_params = _params("all")

    any_result = _run(any_params)
    all_result = _run(all_params)

    assert any_result.report["converged"]
    assert all_result.report["converged"]
    # The checks fall at generations 4, 9, 19, 39, 79, 159 (burn-in 1, first
    # check 3 after it, window doubling): `D` is known well enough at the check
    # at 19, `G_ST` only at the one at 159. Deterministic: re-run in isolation
    # before updating the values.
    assert any_result.report["generation"] == 19
    assert all_result.report["generation"] == 159
    assert any_result.report["generation"] < all_result.report["generation"]


def _monomorphic_any_params(engine_backend: EngineBackend) -> SimulationParams:
    """Return a run where, under `any`, D settles and G_ST never can.

    One founding allele and no mutation keep every deme fixed for it: D is
    exactly 0 every generation, while G_ST is undefined (0/0) every
    generation and so never accumulates a history to be stable on.
    """
    return SimulationParams(
        gene_copies=20,
        m=0.1,
        mu=0.0,
        d=3,
        seed=7,
        loci=(LocusSpec(1, 5),),
        initial_allele_count=1,
        mutation_model="finite_alleles",
        convergence_statistic=("D", "G_ST"),
        convergence_combinator="any",
        precision=0.0,
        **FAST_CONVERGENCE,
        max_generations=50,
        n_replicates=2,
        stop_batch_early=False,
        engine_backend=engine_backend,
    )


@pytest.mark.parametrize("engine_backend", ["lineal", "generational"])
def test_converged_on_names_only_the_statistics_that_passed(
    engine_backend: EngineBackend,
) -> None:
    """Under `any`, `converged_on` is what passed, not every watched statistic.

    Both report paths: the lineal backend's own run loop (`_run_one`) and
    a generational lane (`_finish_lane`).
    """
    output = fim(
        20,
        0.1,
        0.0,
        3,
        params=_monomorphic_any_params(engine_backend),
        clock=_clock,
    )

    assert isinstance(output, tuple)
    for result in output:
        assert result.report["converged"] is True
        assert result.report["converged_on"] == ["D"]


def test_converged_on_is_none_for_a_run_that_hit_the_cap() -> None:
    """A capped run converged on nothing; `converged_on` says so."""
    params = SimulationParams(
        gene_copies=20,
        m=0.1,
        mu=0.01,
        d=3,
        seed=7,
        loci=(LocusSpec(1, 100),),
        precision=0.0,
        max_generations=60,
        n_replicates=1,
        stop_batch_early=False,
    )

    result = _run(params)

    assert result.report["converged"] is False
    assert result.report["reason"] == "hit the cap"
    assert result.report["converged_on"] is None


def test_report_for_state_without_a_monitor_counts_every_watched_statistic(
    tiny_params: SimulationParams,
) -> None:
    """A caller with no monitor (a re-analysis) reports every watched statistic.

    Exact for one statistic or `all`; a state that did not converge
    (a preview, a progress tick) reports `None`.
    """
    single = replace(tiny_params, convergence_statistic="D")
    state = generate_initial_state(single)
    multi = replace(tiny_params, convergence_statistic=("D", "G_ST"))

    def converged_on(params: SimulationParams, *, converged: bool) -> object:
        return report_for_state(
            state, params, run_id="r", converged=converged, reason="x"
        )["converged_on"]

    assert converged_on(single, converged=True) == "D"
    assert converged_on(multi, converged=True) == ["D", "G_ST"]
    assert converged_on(single, converged=False) is None
    assert converged_on(multi, converged=False) is None


def test_mutation_ids_follow_high_explicit_initial_id() -> None:
    """Mutations cannot collide with labels supplied through explicit p_0."""
    params = SimulationParams(
        gene_copies=1,
        m=0.0,
        mu=1.0,
        d=2,
        seed=7,
        loci=(LocusSpec(1, 100),),
        initial_allele_count=1,
        max_generations=1,
        n_replicates=1,
        stop_batch_early=False,
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
        gene_copies=sizes,
        m=0.2,
        mu=0.05,
        d=2,
        seed=20260817,
        loci=(LocusSpec(1, 100),),
        precision=1.0,
        max_generations=8,
        n_replicates=1,
        stop_batch_early=False,
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
        gene_copies=(10, 10_000),
        m=0.1,
        mu=0.0,
        d=2,
        seed=7,
        loci=loci,
        deme_weighting="size",
    )
    equal_params = SimulationParams(
        gene_copies=(10, 10_000),
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
        gene_copies=20,
        m=matrix,
        mu=0.05,
        d=3,
        seed=20260817,
        loci=(LocusSpec(1, 100),),
        precision=1.0,
        max_generations=8,
        n_replicates=1,
        stop_batch_early=False,
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
        gene_copies=10,
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


def test_report_for_state_captures_the_all_demes_nei_family() -> None:
    """All eight all-demes Nei fields, matching the library functions.

    The arithmetic pooled identity is `Gd / Gs` (Jost's `1 - D` for the
    pooled locus rule), a check that shares no code with the Nei family.
    """
    loci = (LocusSpec(1, 100), LocusSpec(2, 100))
    frequencies = (
        ({AlleleId(0): 1.0}, {AlleleId(0): 0.5, AlleleId(1): 0.5}),
        ({AlleleId(0): 0.2, AlleleId(2): 0.8}, {AlleleId(1): 1.0}),
        ({AlleleId(0): 0.5, AlleleId(2): 0.5}, {AlleleId(0): 0.3, AlleleId(1): 0.7}),
    )
    state = ModelState(loci=loci, frequencies=frequencies)
    params = SimulationParams(gene_copies=10, m=0.1, mu=0.0, d=3, seed=7, loci=loci)
    report = report_for_state(
        state, params, run_id="run-a", converged=False, reason="test"
    )
    tables = [[frequencies[deme][locus] for deme in range(3)] for locus in range(2)]
    for denominator in NEI_DENOMINATORS:
        suffix = "GEO" if denominator == "geometric" else "ARITH"
        for locus_rule in NEI_LOCUS_RULES:
            rule = "_LOCUS_MEAN" if locus_rule == "locus_mean" else ""
            fields = cast("Mapping[str, float]", report)
            identity_value = fields[f"NEI_I_ALL_{suffix}{rule}"]
            assert identity_value == pytest.approx(
                nei_all_demes_identity(
                    tables, denominator=denominator, locus_rule=locus_rule
                )
            )
            assert fields[f"NEI_D_ALL_{suffix}{rule}"] == pytest.approx(
                -math.log(identity_value)
            )
    assert report["NEI_I_ALL_ARITH"] == pytest.approx(report["Gd"] / report["Gs"])


def test_pair_statistic_values_match_the_library_pair_functions() -> None:
    """All eight pair values for demes 1 and 3 equal `nei_pair_identity`."""
    loci = (LocusSpec(1, 100), LocusSpec(2, 100))
    frequencies = (
        ({AlleleId(0): 1.0}, {AlleleId(0): 0.5, AlleleId(1): 0.5}),
        ({AlleleId(0): 0.2, AlleleId(2): 0.8}, {AlleleId(1): 1.0}),
        ({AlleleId(0): 0.5, AlleleId(2): 0.5}, {AlleleId(0): 0.3, AlleleId(1): 0.7}),
    )
    state = ModelState(loci=loci, frequencies=frequencies)

    values = pair_statistic_values(state, 0, 2)

    for denominator in NEI_DENOMINATORS:
        suffix = "GEO" if denominator == "geometric" else "ARITH"
        for locus_rule in NEI_LOCUS_RULES:
            rule = "_LOCUS_MEAN" if locus_rule == "locus_mean" else ""
            expected = nei_pair_identity(
                [frequencies[0][0], frequencies[0][1]],
                [frequencies[2][0], frequencies[2][1]],
                denominator=denominator,
                locus_rule=locus_rule,
            )
            assert values[f"NEI_I_PAIR_{suffix}{rule}"] == pytest.approx(expected)
            assert values[f"NEI_D_PAIR_{suffix}{rule}"] == pytest.approx(
                -math.log(expected)
            )
    with pytest.raises(ValueError, match="outside"):
        pair_statistic_values(state, 0, 3)


def test_history_free_values_equal_the_reports_gs_gd_and_nei() -> None:
    """The scrubber's cheap per-frame values equal `report_for_state`'s."""
    loci = (LocusSpec(1, 100), LocusSpec(2, 100))
    frequencies = (
        ({AlleleId(0): 1.0}, {AlleleId(0): 0.5, AlleleId(1): 0.5}),
        ({AlleleId(0): 0.2, AlleleId(2): 0.8}, {AlleleId(1): 1.0}),
    )
    state = ModelState(loci=loci, frequencies=frequencies)
    params = SimulationParams(gene_copies=10, m=0.1, mu=0.0, d=2, seed=7, loci=loci)
    report = cast(
        "Mapping[str, float | None]",
        report_for_state(state, params, run_id="r", converged=False, reason="test"),
    )

    values = history_free_statistic_values(state)

    for key, value in values.items():
        assert value == pytest.approx(report[key])


def test_report_for_state_captures_the_pooled_heterozygosity_measures() -> None:
    """D_m, R_ST, both G'_ST and coancestry F_ST from the pooled H_S/H_T."""
    loci = (LocusSpec(1, 100), LocusSpec(2, 100))
    frequencies = (
        ({AlleleId(0): 1.0}, {AlleleId(0): 0.5, AlleleId(1): 0.5}),
        ({AlleleId(0): 0.2, AlleleId(2): 0.8}, {AlleleId(1): 1.0}),
        ({AlleleId(0): 0.5, AlleleId(2): 0.5}, {AlleleId(0): 0.3, AlleleId(1): 0.7}),
    )
    state = ModelState(loci=loci, frequencies=frequencies)
    params = SimulationParams(gene_copies=10, m=0.1, mu=0.0, d=3, seed=7, loci=loci)
    report = cast(
        "Mapping[str, float | None]",
        report_for_state(state, params, run_id="r", converged=False, reason="test"),
    )
    h_s_value, h_t_value = report["H_S"], report["H_T"]
    assert h_s_value is not None and h_t_value is not None
    expected = derived_differentiation(h_s_value, h_t_value, 3)
    for key, value in expected.items():
        assert report[key] == pytest.approx(value)
    assert report["F_ST"] == pytest.approx(
        (report["Gs"] - report["Gd"]) / (1.0 - report["Gd"])  # type: ignore[operator]
    )


def test_reports_summary_omits_a_nei_distance_infinite_in_any_report() -> None:
    """`None` for a Nei distance is infinite: no finite mean, so omitted.

    `G_ST`'s `None` (undefined) still just drops that replicate, as before.
    """
    loci = (LocusSpec(1, 100),)
    params = SimulationParams(gene_copies=10, m=0.1, mu=0.0, d=2, seed=7, loci=loci)
    shared = ModelState(
        loci=loci,
        frequencies=(
            ({AlleleId(0): 0.5, AlleleId(1): 0.5},),
            ({AlleleId(0): 0.2, AlleleId(1): 0.8},),
        ),
    )
    disjoint = ModelState(
        loci=loci, frequencies=(({AlleleId(0): 1.0},), ({AlleleId(1): 1.0},))
    )
    reports = [
        report_for_state(state, params, run_id="r", converged=False, reason="t")
        for state in (shared, shared, disjoint)
    ]

    summary = reports_summary(reports)

    assert "NEI_D_ALL_GEO" not in summary
    assert summary["NEI_I_ALL_GEO"]["sample_count"] == 3
    finite = reports_summary(reports[:2])
    assert finite["NEI_D_ALL_GEO"]["sample_count"] == 2


def test_report_for_state_reports_an_infinite_nei_distance_as_none() -> None:
    """No allele shared between any pair: distance `None`, identity 0."""
    loci = (LocusSpec(1, 100),)
    state = ModelState(
        loci=loci,
        frequencies=(({AlleleId(0): 1.0},), ({AlleleId(1): 1.0},)),
    )
    params = SimulationParams(gene_copies=10, m=0.1, mu=0.0, d=2, seed=7, loci=loci)
    report = report_for_state(
        state, params, run_id="run-a", converged=False, reason="test"
    )
    assert report["NEI_D_ALL_GEO"] is None
    assert report["NEI_D_ALL_ARITH_LOCUS_MEAN"] is None
    assert report["NEI_I_ALL_GEO"] == 0.0


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
        gene_copies=10,
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
        SimulationParams(gene_copies=10, m=0.1, mu=0.0, d=2, seed=7, loci=(loci[1],)),
        run_id="run-b",
        converged=False,
        reason="test",
    )
    assert report["G_ST"] == pytest.approx(polymorphic_locus_only["G_ST"])


def _two_locus_state_with_divergent_per_locus_estimates() -> ModelState:
    """Two loci whose own `H_S`/`H_T` genuinely differ, one deme pair, two alleles each.

    Deliberately not the same per-locus differentiation everywhere:
    `"ratio_of_means"`/`"mean_of_ratios"` agree exactly whenever every
    locus has identical `H_S`/`H_T` (both estimators reduce to the same
    single value averaged with itself), so a regression test built from
    such a state could pass by coincidence even without `_convergence_
    values` respecting `locus_aggregation` at all. These two loci's own
    allele frequencies are deliberately different enough (one lightly,
    one heavily differentiated) that the two estimators give genuinely
    different `D`/`G_ST` — see `test_convergence_watches_the_same_d_
    and_g_st_report_for_state_uses`. Locus length `1` (capacity `4`), not
    this file's own usual `100` — inert for the dict-based statistics
    either way (`test_locus_length_does_not_affect_the_report`), but
    `test_convergence_values_vectorized_watches_the_same_d_and_g_st`
    below feeds this same state through a `VectorBlock`.
    """
    loci = (LocusSpec(1, 1), LocusSpec(2, 1))
    return ModelState(
        loci=loci,
        frequencies=(
            (
                {AlleleId(0): 0.55, AlleleId(1): 0.45},
                {AlleleId(0): 0.95, AlleleId(1): 0.05},
            ),
            (
                {AlleleId(0): 0.45, AlleleId(1): 0.55},
                {AlleleId(0): 0.05, AlleleId(1): 0.95},
            ),
        ),
    )


def _vector_block_for(state: ModelState, params: SimulationParams) -> VectorBlock:
    """Return a `VectorBlock` holding `state`, for the statistics-only tests.

    The state need not be on the `1 / N` grid: these tests only read the
    block's statistics, they never step it.
    """
    return VectorBlock.from_model_state(
        state,
        sizes=params.population_sizes,
        mutation_rates=params.mutation_rates,
        migration=VectorMigration.from_parameter(params.m, params.d),
        mutation_model=params.mutation_model,
        next_id=MINTED_ID_START,
    )


def test_convergence_watches_the_same_d_and_g_st_report_for_state_uses() -> None:
    """The convergence monitor's own `D`/`G_ST` match `report_for_state`'s, always.

    Before this fix, `_convergence_values` (and its vectorized
    counterpart) always aggregated `D`/`G_ST` across loci via a plain
    per-locus mean (`"mean_of_ratios"`), regardless of `params.
    locus_aggregation` — `report_for_state`'s own `D`/`G_ST` fields, by
    contrast, already respected it. Under the default `locus_
    aggregation="ratio_of_means"`, the two estimators are materially
    different (this project's own `params.py` docstring: 0.25% vs 1.88%
    from the exact gene-identity recursion at reference scale), so a
    multi-locus run could stop its trailing window on a `D` its own
    final report never actually showed (this project's own multi-model
    engine review, 2026-09-04, `FIM-10`/finding C-02/finding P1-2 — one
    of three reviewers naming it the only finding among four full
    reviews that changes a scientific result). Checked under both
    aggregation choices, not only the default, so a future regression in
    either branch of `_watched_statistic_values` is caught.
    """
    state = _two_locus_state_with_divergent_per_locus_estimates()
    for locus_aggregation in ("ratio_of_means", "mean_of_ratios"):
        params = SimulationParams(
            gene_copies=10,
            m=0.1,
            mu=0.0,
            d=2,
            seed=7,
            loci=state.loci,
            convergence_statistic=("D", "G_ST"),
            locus_aggregation=locus_aggregation,
        )
        report = report_for_state(
            state, params, run_id="run-a", converged=False, reason="test"
        )
        watched = _convergence_values(state, params)
        assert report["G_ST"] is not None
        assert watched["D"] == pytest.approx(report["D"])
        assert watched["G_ST"] == pytest.approx(report["G_ST"])


def test_convergence_values_vectorized_watches_the_same_d_and_g_st() -> None:
    """The array-native convergence path gets the identical `FIM-10` fix.

    `_convergence_values_vectorized` shares `_watched_statistic_values`
    with the dict-based path above — this only needs to confirm the
    `VectorBlock`-specific plumbing (the kernel's per-locus table and its
    own aggregation) agrees with it, not re-litigate the aggregation math
    itself.
    """
    state = _two_locus_state_with_divergent_per_locus_estimates()
    params = SimulationParams(
        gene_copies=10,
        m=0.1,
        mu=0.0,
        d=2,
        seed=7,
        loci=state.loci,
        convergence_statistic=("D", "G_ST"),
    )
    report = report_for_state(
        state, params, run_id="run-a", converged=False, reason="test"
    )
    vectorized_state = _vector_block_for(state, params)
    watched = _convergence_values_vectorized(vectorized_state, params)
    assert report["G_ST"] is not None
    assert watched["D"] == pytest.approx(report["D"])
    assert watched["G_ST"] == pytest.approx(report["G_ST"])


def test_convergence_values_skips_e_st_and_k_st_when_only_d_is_watched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_convergence_values` never computes `E_ST`/`K_ST` when nothing watches them.

    `FIM-24`/`FIM-32` (Phase 7 item 4,
    `20260904-claude-sonnet-5-fim-engine-review-remediations.md`): an
    allocation/call-count regression test at the full convergence-check
    entry point, not just at `statistics_report` directly — proves the
    `params.convergence_statistics` filter this fix adds actually
    reaches `differentiation._e_st_from_demes`/`_k_st_from_demes`,
    end to end, for the common case (only `D` watched, this project's
    own stated default) matching this project's own established
    `FIM-53`/`FIM-27`/`FIM-28`/`FIM-36` precedent for this kind of claim.
    """
    state = _two_locus_state_with_divergent_per_locus_estimates()
    params = SimulationParams(
        gene_copies=10,
        m=0.1,
        mu=0.0,
        d=2,
        seed=7,
        loci=state.loci,
        convergence_statistic="D",
    )
    e_st_calls = 0
    k_st_calls = 0
    original_e_st = differentiation._e_st_from_demes
    original_k_st = differentiation._k_st_from_demes

    def counting_e_st(*args: object, **kwargs: object) -> float:
        nonlocal e_st_calls
        e_st_calls += 1
        return original_e_st(*args, **kwargs)  # type: ignore[arg-type]

    def counting_k_st(*args: object, **kwargs: object) -> float:
        nonlocal k_st_calls
        k_st_calls += 1
        return original_k_st(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(differentiation, "_e_st_from_demes", counting_e_st)
    monkeypatch.setattr(differentiation, "_k_st_from_demes", counting_k_st)

    watched = _convergence_values(state, params)

    assert e_st_calls == 0
    assert k_st_calls == 0
    assert "D" in watched


def test_convergence_values_vectorized_skips_e_st_and_k_st_when_only_d_is_watched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The array-native convergence path gets the identical `FIM-24`/`FIM-32` fix.

    Mirrors `test_convergence_values_skips_e_st_and_k_st_when_only_d_
    is_watched` above, through `_convergence_values_vectorized` instead
    — proves the array-native path skips the same
    work, not just the dict-based path.
    """
    state = _two_locus_state_with_divergent_per_locus_estimates()
    params = SimulationParams(
        gene_copies=10,
        m=0.1,
        mu=0.0,
        d=2,
        seed=7,
        loci=state.loci,
        convergence_statistic="D",
    )
    vectorized_state = _vector_block_for(state, params)
    e_st_calls = 0
    k_st_calls = 0
    original_e_st = differentiation._e_st_from_demes
    original_k_st = differentiation._k_st_from_demes

    def counting_e_st(*args: object, **kwargs: object) -> float:
        nonlocal e_st_calls
        e_st_calls += 1
        return original_e_st(*args, **kwargs)  # type: ignore[arg-type]

    def counting_k_st(*args: object, **kwargs: object) -> float:
        nonlocal k_st_calls
        k_st_calls += 1
        return original_k_st(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(differentiation, "_e_st_from_demes", counting_e_st)
    monkeypatch.setattr(differentiation, "_k_st_from_demes", counting_k_st)

    watched = _convergence_values_vectorized(vectorized_state, params)

    assert e_st_calls == 0
    assert k_st_calls == 0
    assert "D" in watched


def test_convergence_values_always_includes_the_always_tracked_four() -> None:
    """`D`/`G_ST`/`H_S`/`H_T`/`H_ST` are present regardless of what is watched.

    The display-only counterpart to the two "skips E_ST/K_ST" tests
    above: those five cost nothing extra to compute (`statistics_
    report` already computes them unconditionally — `b12679b`'s own
    docstring), so `_watched_statistic_values` no longer discards them
    from the returned mapping just because they were not named in
    `convergence_statistic`. Confirms `fim.engine._ALWAYS_TRACKED_
    STATISTICS` end to end, through the same full convergence-check
    entry point the "skips" tests exercise, not just at `_watched_
    statistic_values` directly.
    """
    state = _two_locus_state_with_divergent_per_locus_estimates()
    params = SimulationParams(
        gene_copies=10,
        m=0.1,
        mu=0.0,
        d=2,
        seed=7,
        loci=state.loci,
        convergence_statistic="D",
    )

    values = _convergence_values(state, params)

    assert set(values) == {"D", "G_ST", "H_S", "H_T", "H_ST"}


def test_convergence_values_vectorized_always_includes_d_g_st_h_s_h_t() -> None:
    """The array-native path returns the identical always-tracked superset."""
    state = _two_locus_state_with_divergent_per_locus_estimates()
    params = SimulationParams(
        gene_copies=10,
        m=0.1,
        mu=0.0,
        d=2,
        seed=7,
        loci=state.loci,
        convergence_statistic="D",
    )
    vectorized_state = _vector_block_for(state, params)

    values = _convergence_values_vectorized(vectorized_state, params)

    assert set(values) == {"D", "G_ST", "H_S", "H_T", "H_ST"}


def test_track_expensive_statistics_computes_e_st_and_k_st_even_when_unwatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`track_expensive_statistics=True` reaches `_e_st_from_demes`/`_k_st_from_demes`.

    The opt-in's own call-count regression test, mirroring `test_
    convergence_values_skips_e_st_and_k_st_when_only_d_is_watched`
    exactly, but with the new field turned on and neither statistic
    watched: proves the opt-in alone (no watching required) is enough to
    reach both real implementations, end to end, through `_convergence_
    values`.
    """
    state = _two_locus_state_with_divergent_per_locus_estimates()
    params = SimulationParams(
        gene_copies=10,
        m=0.1,
        mu=0.0,
        d=2,
        seed=7,
        loci=state.loci,
        convergence_statistic="D",
        track_expensive_statistics=True,
    )
    e_st_calls = 0
    k_st_calls = 0
    original_e_st = differentiation._e_st_from_demes
    original_k_st = differentiation._k_st_from_demes

    def counting_e_st(*args: object, **kwargs: object) -> float:
        nonlocal e_st_calls
        e_st_calls += 1
        return original_e_st(*args, **kwargs)  # type: ignore[arg-type]

    def counting_k_st(*args: object, **kwargs: object) -> float:
        nonlocal k_st_calls
        k_st_calls += 1
        return original_k_st(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(differentiation, "_e_st_from_demes", counting_e_st)
    monkeypatch.setattr(differentiation, "_k_st_from_demes", counting_k_st)

    values = _convergence_values(state, params)

    assert e_st_calls > 0
    assert k_st_calls > 0
    assert set(values) == {
        "D",
        "G_ST",
        "H_S",
        "H_T",
        "H_ST",
        "E_ST",
        "K_ST",
        "A_CGD",
        "Delta",
        "MI",
    }


def test_track_expensive_statistics_vectorized_computes_e_st_and_k_st(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The array-native path gets the identical opt-in fix."""
    state = _two_locus_state_with_divergent_per_locus_estimates()
    params = SimulationParams(
        gene_copies=10,
        m=0.1,
        mu=0.0,
        d=2,
        seed=7,
        loci=state.loci,
        convergence_statistic="D",
        track_expensive_statistics=True,
    )
    vectorized_state = _vector_block_for(state, params)
    e_st_calls = 0
    k_st_calls = 0
    original_e_st = differentiation._e_st_from_demes
    original_k_st = differentiation._k_st_from_demes

    def counting_e_st(*args: object, **kwargs: object) -> float:
        nonlocal e_st_calls
        e_st_calls += 1
        return original_e_st(*args, **kwargs)  # type: ignore[arg-type]

    def counting_k_st(*args: object, **kwargs: object) -> float:
        nonlocal k_st_calls
        k_st_calls += 1
        return original_k_st(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(differentiation, "_e_st_from_demes", counting_e_st)
    monkeypatch.setattr(differentiation, "_k_st_from_demes", counting_k_st)

    values = _convergence_values_vectorized(vectorized_state, params)

    assert e_st_calls > 0
    assert k_st_calls > 0
    assert set(values) == {
        "D",
        "G_ST",
        "H_S",
        "H_T",
        "H_ST",
        "E_ST",
        "K_ST",
        "A_CGD",
        "Delta",
        "MI",
    }


def test_track_expensive_statistics_computes_a_cgd_delta_mi_even_when_unwatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`track_expensive_statistics=True` reaches the three literature statistics too.

    The `A_CGD`/`Delta`/`MI` counterpart to `test_track_expensive_
    statistics_computes_e_st_and_k_st_even_when_unwatched`, above --
    proves the opt-in-but-unwatched path reaches all three, exactly
    like it already does for `E_ST`/`K_ST`. They are also watchable
    (`test_convergence_statistic_accepts_the_expensive_bonus_
    measurements`, `test_params.py`) since a real, reported request
    reversed this session's own earlier "display-only bonus
    measurement" choice — this test's own `convergence_statistic`
    stays unwatched (`D`, below) specifically to isolate the opt-in
    path from the watched one.
    """
    state = _two_locus_state_with_divergent_per_locus_estimates()
    params = SimulationParams(
        gene_copies=10,
        m=0.1,
        mu=0.0,
        d=2,
        seed=7,
        loci=state.loci,
        convergence_statistic="D",
        track_expensive_statistics=True,
    )
    a_cgd_calls = 0
    delta_calls = 0
    mi_calls = 0
    original_a_cgd = differentiation._allelic_distance_from_demes
    original_delta = differentiation._gregorius_delta_from_demes
    original_mi = differentiation._mutual_information_from_demes

    def counting_a_cgd(*args: object, **kwargs: object) -> float:
        nonlocal a_cgd_calls
        a_cgd_calls += 1
        return original_a_cgd(*args, **kwargs)  # type: ignore[arg-type]

    def counting_delta(*args: object, **kwargs: object) -> float:
        nonlocal delta_calls
        delta_calls += 1
        return original_delta(*args, **kwargs)  # type: ignore[arg-type]

    def counting_mi(*args: object, **kwargs: object) -> float:
        nonlocal mi_calls
        mi_calls += 1
        return original_mi(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(differentiation, "_allelic_distance_from_demes", counting_a_cgd)
    monkeypatch.setattr(differentiation, "_gregorius_delta_from_demes", counting_delta)
    monkeypatch.setattr(differentiation, "_mutual_information_from_demes", counting_mi)

    values = _convergence_values(state, params)

    assert a_cgd_calls > 0
    assert delta_calls > 0
    assert mi_calls > 0
    assert set(values) == {
        "D",
        "G_ST",
        "H_S",
        "H_T",
        "H_ST",
        "E_ST",
        "K_ST",
        "A_CGD",
        "Delta",
        "MI",
    }


def test_run_result_convergence_histories_include_always_tracked_statistics() -> None:
    """A real, full scalar run's own `convergence_histories` include the free five.

    End-to-end proof through `_run_one` itself (not just the per-
    generation helper functions above): a run watching only `D` still
    comes back with real `G_ST`/`H_S`/`H_T`/`H_ST` history too, of the
    same length as the watched one — the exact GUI-visible symptom the
    bug report described (a trajectory panel/completed view that
    narrowed down to only the watched statistic once a run finished).
    `H_ST` joined the other three later (`fim.engine.
    _ALWAYS_TRACKED_STATISTICS`'s own docstring has the "found live"
    account of a real, reported second instance of this same symptom).
    """
    params = SimulationParams(
        gene_copies=15,
        m=0.1,
        mu=0.01,
        d=2,
        seed=99,
        loci=(LocusSpec(1, 50),),
        convergence_statistic="D",
        precision=0.02,
        max_generations=20,
        n_replicates=1,
        stop_batch_early=False,
    )

    result = _run(params)

    assert set(result.convergence_histories) == {"D", "G_ST", "H_S", "H_T", "H_ST"}
    for name in ("G_ST", "H_S", "H_T", "H_ST"):
        assert len(result.convergence_histories[name]) == len(
            result.convergence_generations
        )


def test_run_result_convergence_histories_include_e_st_k_st_when_opted_in() -> None:
    """`track_expensive_statistics=True` extends a real run's own recorded history.

    Same run as above, only with the opt-in set — `E_ST`/`K_ST`/
    `A_CGD`/`Delta`/`MI` now join the always-tracked five in `RunResult.
    convergence_histories`, each with a real per-generation history the
    same length as every other tracked statistic's own.
    """
    params = SimulationParams(
        gene_copies=15,
        m=0.1,
        mu=0.01,
        d=2,
        seed=99,
        loci=(LocusSpec(1, 50),),
        convergence_statistic="D",
        precision=0.02,
        max_generations=20,
        n_replicates=1,
        stop_batch_early=False,
        track_expensive_statistics=True,
    )

    result = _run(params)

    assert set(result.convergence_histories) == {
        "D",
        "G_ST",
        "H_S",
        "H_T",
        "H_ST",
        "E_ST",
        "K_ST",
        "A_CGD",
        "Delta",
        "MI",
    }
    for name in ("G_ST", "H_S", "H_T", "H_ST", "E_ST", "K_ST", "A_CGD", "Delta", "MI"):
        assert len(result.convergence_histories[name]) == len(
            result.convergence_generations
        )


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
        params = SimulationParams(gene_copies=20, m=0.1, mu=0.0, d=2, seed=7, loci=loci)
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
        gene_copies=20,
        m=0.1,
        mu=0.02,
        d=2,
        seed=20260818,
        loci=(LocusSpec(1, 50), LocusSpec(2, 8_000)),
        precision=1.0,
        max_generations=8,
        n_replicates=1,
        stop_batch_early=False,
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
            "ploidy": "haploid",
            "d": 8,
            "m": {"topology": "ring", "rate": 0.2},
            "mu": 0.02,
            "seed": 20260821,
            "precision": 1.0,
            "max_generations": 8,
            "n_replicates": 1,
            "stop_batch_early": False,
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
            "ploidy": "haploid",
            "d": 3,
            "m": 0.2,
            "mu": 0.02,
            "seed": 20260818,
            "migrant_sampling": "stochastic",
            "precision": 1.0,
            "max_generations": 8,
            "n_replicates": 1,
            "stop_batch_early": False,
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
        "ploidy": "haploid",
        "d": 3,
        "m": 0.2,
        "mu": 0.02,
        "seed": 20260818,
        "precision": 1.0,
        "max_generations": 8,
        "n_replicates": 1,
        "stop_batch_early": False,
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
            "ploidy": "haploid",
            "d": 3,
            "m": 0.2,
            "mu": 0.1,
            "seed": 20260821,
            "loci": [{"locus_id": 1, "length": 1}],
            "mutation_model": "finite_alleles",
            "precision": 1.0,
            "max_generations": 10,
            "n_replicates": 1,
            "stop_batch_early": False,
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
        "ploidy": "haploid",
        "d": 3,
        "m": 0.2,
        "mu": 0.02,
        "seed": 20260821,
        "precision": 1.0,
        "max_generations": 8,
        "n_replicates": 1,
        "stop_batch_early": False,
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
        "ploidy": "haploid",
        "d": 3,
        "m": 0.2,
        "seed": 20260822,
        "loci": loci,
        "precision": 1.0,
        "max_generations": 8,
        "n_replicates": 1,
        "stop_batch_early": False,
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
            "ploidy": "haploid",
            "d": 3,
            "m": 0.2,
            "mu_b": 0.05,
            "seed": 20260822,
            "loci": [{"locus_id": 1, "length": 1}],
            "mutation_model": "finite_alleles",
            "precision": 1.0,
            "max_generations": 10,
            "n_replicates": 1,
            "stop_batch_early": False,
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
# computed never changes *what* it computes. `stop_batch_early` is
# deliberately off in both of these: with it on, the two backends' own
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
    """The adaptive replicate stop fires once enough of the accepted
    prefix has finished, admitting simultaneous stops in ascending
    `replica_index`, deterministically across repeated runs.

    `precision` is set astronomically large so every criterion is
    satisfied the instant a window has enough effective observations,
    regardless of their actual values: the lanes then stop within a few
    ticks of each other, so what is under test is the order the batch
    accepts them in, not real convergence timing. With `replicate_minimum
    == 2`, the batch-wide stop fires on the *second* replicate in
    ascending order, exactly replicates 0 and 1, leaving replicates 2-4
    never even reached.
    """
    params = SimulationParams(
        gene_copies=20,
        m=0.1,
        mu=0.01,
        d=2,
        seed=20260901,
        loci=(LocusSpec(1, 200),),
        max_generations=50,
        n_replicates=5,
        precision=1e12,
        **FAST_CONVERGENCE,
        stop_batch_early=True,
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
    assert [result.report["generation"] for result in first] == [
        result.report["generation"] for result in second
    ]


class _ReversedFinishAdvancer:
    """Wrap an `Advancer` so higher-numbered lanes report stopping first.

    Each lane `inner` reports stopped is held back from `run_batch` for
    `2 * (lane_count - replica_index)` further ticks, so replicate 1 is
    always the last of any group to be reported. A held lane's monitor
    has already stopped, so `inner` hands it back unstepped on every
    later tick (the generation-zero contract of `Advancer.advance`): its
    own result is untouched, and only *when* `run_batch` learns of it
    changes. This is the case where finishing order and replicate order
    disagree as much as they can, built deterministically rather than
    found by searching seeds.
    """

    def __init__(self, inner: engine.Advancer, lane_count: int) -> None:
        """Wrap `inner` for a batch of `lane_count` replicates.

        Args:
            inner: The advancer that actually steps the lanes.
            lane_count: The batch's `n_replicates`.
        """
        self._inner = inner
        self._lane_count = lane_count
        self._tick = 0
        self._release_tick: dict[int, int] = {}
        self.reported_order: list[int] = []

    def advance(
        self, active_lanes: Sequence[ReplicaLane], store: TrajectoryStore
    ) -> list[ReplicaLane]:
        """Step every lane through `inner`; report only released stops."""
        self._tick += 1
        for lane in self._inner.advance(active_lanes, store):
            delay = 2 * (self._lane_count - lane.replica_index)
            self._release_tick.setdefault(lane.replica_index, self._tick + delay)
        released = [
            lane
            for lane in active_lanes
            if self._release_tick.get(lane.replica_index, math.inf) <= self._tick
        ]
        self.reported_order += [lane.replica_index for lane in released]
        return released


def _adaptive_dict_params(**overrides: object) -> SimulationParams:
    """An adaptive batch whose replicates converge at different generations.

    With this seed, replicate 2 converges at generation 10 and most
    others at generation 3, and the adaptive stop keeps six of twelve
    replicates in replicate order — so a finishing-order stop would keep
    a different set. Tests using it assert that precondition rather than
    assume it.
    """
    base = SimulationParams.from_mapping(
        {
            **_tiny_config(),
            "convergence_statistic": "D",
            "max_generations": 60,
            "n_replicates": 12,
            "replicate_minimum": 3,
            "precision": 0.2,
            "stop_batch_early": True,
        }
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def _assert_same_batch(
    actual: Sequence[RunResult], expected: Sequence[RunResult]
) -> None:
    """Require the same kept replicates, reports, final states, and summary."""
    assert [result.run_id for result in actual] == [
        result.run_id for result in expected
    ]
    for actual_result, expected_result in zip(actual, expected, strict=True):
        assert actual_result.report == expected_result.report
        assert actual_result.final_state == expected_result.final_state
    assert replicate_summary(actual) == replicate_summary(expected)


@pytest.mark.parametrize("window", [None, 8])
def test_generational_adaptive_batch_keeps_the_replicate_order_prefix(
    window: int | None,
) -> None:
    """Replicates that finish first are not the ones an adaptive batch keeps.

    Regression test for the finishing-order defect: `run_batch` used to
    feed the adaptive stopping rule in the order lanes converged, so a
    generation-first batch kept whichever replicates converged soonest —
    a biased sample, since convergence time correlates with the
    statistics themselves. Here the higher-numbered replicates are made
    to report first (`_ReversedFinishAdvancer`), and the batch must
    still keep exactly the replicates `LinealBackend` keeps, replicates
    1 to k, with an identical summary, with or without a window.
    """
    params = _adaptive_dict_params(max_concurrent_replicates=window)
    lineal = LinealBackend().run(params, InMemoryTrajectoryStore(), "batch", _clock)
    assert isinstance(lineal, tuple)
    kept = len(lineal)
    assert 3 <= kept < params.n_replicates

    advancer = _ReversedFinishAdvancer(
        ThreadedAdvancer(max_workers=2), params.n_replicates
    )
    store = InMemoryTrajectoryStore()
    generational = run_batch(params, store, "batch", _clock, advancer)

    # Precondition: the first `kept` lanes reported are not replicates
    # 1 to `kept`, so a finishing-order stop would have kept others.
    assert sorted(advancer.reported_order[:kept]) != list(range(kept))
    assert [result.run_id for result in generational] == [
        f"batch-r{index:03}" for index in range(1, kept + 1)
    ]
    _assert_same_batch(generational, lineal)

    # Lanes finished or running past the stopping replicate leave no rows.
    for index in range(kept + 1, params.n_replicates + 1):
        assert list(store.read(f"batch-r{index:03}")) == []


def test_generational_adaptive_batch_matches_lineal_under_real_timing() -> None:
    """With real convergence times, `generational` keeps lineal's replicates.

    No artificial delay: the first wave of two replicates really does run
    longer than the replicates after it, which average for the shorter
    matched window (asserted below from a fixed-count batch of the same
    seeds), so this is the worked example's own situation in miniature,
    through the public `fim()` entry point.
    """
    params = _adaptive_dict_params(
        max_generations=300,
        expert=ExpertSettings(**{**FAST_EXPERT_SETTINGS, "batch_width": 2}),
    )
    fixed = fim(
        params.gene_copies,
        params.m,
        params.mu,
        params.d,
        params=replace(params, stop_batch_early=False),
        clock=_clock,
    )
    lineal = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, clock=_clock
    )
    generational = fim(
        params.gene_copies,
        params.m,
        params.mu,
        params.d,
        params=params,
        clock=_clock,
        engine_backend="generational",
    )
    assert isinstance(fixed, tuple)
    assert isinstance(lineal, tuple)
    assert isinstance(generational, tuple)

    # Precondition: some replicate past the kept prefix converges before
    # the prefix's slowest replicate does.
    kept = len(lineal)
    generations = [result.report["generation"] for result in fixed]
    assert min(generations[kept:]) < max(generations[:kept])

    assert len(generational) == kept
    for actual, expected in zip(generational, lineal, strict=True):
        assert actual.final_state == expected.final_state
        assert {k: v for k, v in actual.report.items() if k != "run_id"} == {
            k: v for k, v in expected.report.items() if k != "run_id"
        }
    assert replicate_summary(generational) == replicate_summary(lineal)


def test_vector_adaptive_batch_keeps_the_replicate_order_prefix() -> None:
    """`generational-vector` keeps the replicate-order prefix too.

    The vector backend draws a different stream from `lineal`, so its
    reference is itself run with a window of one, which advances one
    replicate at a time and so admits them strictly in replicate order.
    """
    params = SimulationParams(
        gene_copies=40,
        m=0.1,
        mu=0.05,
        d=3,
        seed=20260901,
        loci=(LocusSpec(1, 2),),  # capacity 16
        mutation_model="finite_alleles",
        convergence_statistic="D",
        max_generations=60,
        n_replicates=12,
        replicate_minimum=3,
        precision=0.2,
        stop_batch_early=True,
    )
    reference = run_batch(
        replace(params, max_concurrent_replicates=1),
        InMemoryTrajectoryStore(),
        "batch",
        _clock,
        VectorizedAdvancer(),
    )
    kept = len(reference)
    assert 3 <= kept < params.n_replicates

    advancer = _ReversedFinishAdvancer(VectorizedAdvancer(), params.n_replicates)
    vector = run_batch(params, InMemoryTrajectoryStore(), "batch", _clock, advancer)

    assert sorted(advancer.reported_order[:kept]) != list(range(kept))
    _assert_same_batch(vector, reference)


# `fim()`'s own `engine_backend`/`jit` keywords (Stage F2): the factory
# actually reachable by a real caller, not just `build_engine_backend`/
# `GenerationalBackend` constructed directly the way the tests above do.


def test_fim_engine_backend_generational_matches_default(
    tiny_params: SimulationParams,
) -> None:
    """`fim(..., engine_backend="generational")` matches the untouched default."""
    lineal_result = fim(
        tiny_params.gene_copies,
        tiny_params.m,
        tiny_params.mu,
        tiny_params.d,
        params=tiny_params,
        clock=_clock,
    )
    assert isinstance(lineal_result, RunResult)

    generational_result = fim(
        tiny_params.gene_copies,
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
    # The always-tracked-statistics fix (`fim.engine._ALWAYS_TRACKED_
    # STATISTICS`) reaches `"generational"`'s own per-lane monitor
    # (`_build_replica_lane`) through the identical `_convergence_values`
    # call `"lineal"`'s `_run_one` already makes — no separate wiring was
    # needed for the batch/generational-backend path, confirmed here
    # rather than merely assumed.
    assert set(lineal_result.convergence_histories) == {
        "D",
        "G_ST",
        "H_S",
        "H_T",
        "H_ST",
    }
    assert set(generational_result.convergence_histories) == {
        "D",
        "G_ST",
        "H_S",
        "H_T",
        "H_ST",
    }


def test_fim_rejects_jit_on_lineal(tiny_params: SimulationParams) -> None:
    """`jit` is never offered on the lineal backend — a permanent restriction."""
    with pytest.raises(ValueError, match="lineal backend"):
        fim(
            tiny_params.gene_copies,
            tiny_params.m,
            tiny_params.mu,
            tiny_params.d,
            params=tiny_params,
            jit="numba",
        )


def test_fim_rejects_max_workers_on_other_backends(
    tiny_params: SimulationParams,
) -> None:
    """`max_workers` is lineal-only — never a silent no-op.

    `store_factory` used to be rejected here too; it no longer is
    (`test_non_lineal_batch_uses_store_factory_per_replicate`, above) —
    only `max_workers` still means "size a `ProcessPoolExecutor`," which
    only `LinealBackend` ever builds
    (`20260914-claude-sonnet-5-non-lineal-batch-execution-design.md`,
    `selby/restricted`, §5.3).
    """
    with pytest.raises(ValueError, match="lineal-backend-only"):
        fim(
            tiny_params.gene_copies,
            tiny_params.m,
            tiny_params.mu,
            tiny_params.d,
            params=tiny_params,
            engine_backend="generational",
            max_workers=2,
        )


def test_fim_generational_vector_runs_infinite_alleles(
    tiny_params: SimulationParams,
) -> None:
    """`"generational-vector"` runs the default infinite-alleles model.

    `tiny_params`'s own default `mutation_model` is `"infinite_alleles"`,
    the common case a caller reaches without choosing anything. The
    result is `LinealBackend`'s (the exactness contract; the full matrix
    is `test/engine/test_vector_parity.py`), so a report comparison is
    enough here.
    """
    pytest.importorskip("numba")
    vector = fim(
        tiny_params.gene_copies,
        tiny_params.m,
        tiny_params.mu,
        tiny_params.d,
        params=tiny_params,
        run_id="run",
        clock=_clock,
        engine_backend="generational-vector",
    )
    lineal = fim(
        tiny_params.gene_copies,
        tiny_params.m,
        tiny_params.mu,
        tiny_params.d,
        params=tiny_params,
        run_id="run",
        clock=_clock,
        engine_backend="lineal",
    )
    assert isinstance(vector, RunResult) and isinstance(lineal, RunResult)
    assert vector.report == lineal.report
    assert vector.final_state == lineal.final_state
    assert vector.manifest.engine_backend == "generational-vector"


def test_params_and_engine_accept_stochastic_migrants_on_the_vector_backend(
    tiny_params: SimulationParams,
) -> None:
    """`generational-vector` runs stochastic migrant counts, and says so everywhere.

    `SimulationParams` constructs with it, and `VectorizedAdvancer.advance`
    steps a stochastic lane instead of refusing it. (Parity with Backend L
    is `test_vector_parity`'s job.)
    """
    pytest.importorskip("numba")
    for model in ("infinite_alleles", "finite_alleles"):
        replace(
            tiny_params,
            mutation_model=model,
            loci=(LocusSpec(1, 2),),
            migrant_sampling="stochastic",
            engine_backend="generational-vector",
        )
    stochastic = replace(tiny_params, migrant_sampling="stochastic")
    store = InMemoryTrajectoryStore()
    lane = _build_replica_lane(stochastic, 0, None, store, _clock)
    VectorizedAdvancer().advance([lane], store)
    assert lane.vectorized_state is not None
    assert lane.vectorized_state.generation == 1


def test_params_and_engine_reject_the_same_vector_jit_configuration(
    tiny_params: SimulationParams,
) -> None:
    """Config-time validation and `build_engine_backend` agree on `jit`.

    `generational-vector` has no `jit` toggle, so both refuse `jit="numba"`.
    """
    pytest.importorskip("numba")
    with pytest.raises(ValueError, match="jit='off'"):
        replace(tiny_params, engine_backend="generational-vector", jit="numba")
    with pytest.raises(ValueError, match="jit"):
        build_engine_backend("generational-vector", jit="numba")


def test_fim_generational_vector_runs_stochastic_migrant_sampling(
    tiny_params: SimulationParams,
) -> None:
    """`"generational-vector"` runs stochastic migrant counts, equal to lineal."""
    pytest.importorskip("numba")
    params = replace(
        tiny_params,
        mutation_model="finite_alleles",
        loci=(LocusSpec(1, 2),),
        migrant_sampling="stochastic",
    )
    reports = {}
    for backend in ("lineal", "generational-vector"):
        result = fim(
            params.gene_copies,
            params.m,
            params.mu,
            params.d,
            params=params,
            engine_backend=backend,
            store=InMemoryTrajectoryStore(),
        )
        assert not isinstance(result, tuple)
        reports[backend] = (result.report, result.final_state)
    assert reports["generational-vector"] == reports["lineal"]


def test_fim_generational_vector_rejects_jit(tiny_params: SimulationParams) -> None:
    """`jit` has no separate toggle under `"generational-vector"` — a `ValueError`.

    Numba is required internally, unconditionally, for its own mutate
    step; only `jit="off"` (the default) is accepted, so a caller who
    asks for `jit="numba"` explicitly gets an error, not a silent no-op.
    """
    params = replace(tiny_params, mutation_model="finite_alleles")
    with pytest.raises(ValueError, match="generational-vector"):
        fim(
            params.gene_copies,
            params.m,
            params.mu,
            params.d,
            params=params,
            engine_backend="generational-vector",
            jit="numba",
        )


def test_build_engine_backend_generational_vector_requires_numba(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A numba-less install fails at config time, not on the first `advance()`.

    Monkeypatches `fim.engine._numba_is_available` directly rather than
    actually uninstalling numba from the test environment — see that
    function's own docstring for why it exists as a separate, mockable
    unit. Before this guard existed, `build_engine_backend` happily
    returned a `GenerationalBackend(VectorizedAdvancer())` regardless,
    and the failure only surfaced as a raw `ImportError` from inside
    `fim.model.vectorized` on the first generation — after generation
    zero had already been persisted to the trajectory store (this
    project's own multi-model engine review, 2026-09-04, `FIM-01`).
    """
    monkeypatch.setattr(engine, "_numba_is_available", lambda: False)
    with pytest.raises(ValueError, match="numba"):
        build_engine_backend("generational-vector")


@pytest.mark.parametrize("model", ["infinite_alleles", "finite_alleles"])
def test_build_engine_backend_auto_falls_back_to_generational_without_numba(
    monkeypatch: pytest.MonkeyPatch,
    model: Literal["infinite_alleles", "finite_alleles"],
) -> None:
    """`"auto"` never raises for a missing numba: it picks Backend G instead.

    Backend V's output is identical to Backend G's, so the only cost of a
    machine without numba is speed. An *explicit* `"generational-vector"`
    still raises (the test above), because the caller named a backend that
    cannot run.
    """
    monkeypatch.setattr(engine, "_numba_is_available", lambda: False)
    params = replace(_finite_alleles_vector_params(d=40), mutation_model=model)
    params = replace(params, loci=(LocusSpec(1, 2),))
    backend = build_engine_backend("auto", params=params, auto_vector_min_d=35)
    assert isinstance(backend, GenerationalBackend)
    assert isinstance(backend._advancer, ThreadedAdvancer)


def test_build_engine_backend_generational_vector_accepts_numba_present() -> None:
    """The positive case, alongside the two negative ones above.

    Confirms the new `_numba_is_available` guard is not itself firing a
    false positive in this dev environment's own normal state (numba
    installed, per `pip install fim[jit]`) — without this, the two
    monkeypatched tests above could both pass for the wrong reason if the
    guard raised unconditionally. Also the "no `params`" case for the
    capacity ceiling below: with no `params` to read a capacity from,
    that check is skipped entirely rather than erroring — `fim()` itself
    always passes `params`, so this only matters for this function's own
    lower-level, params-less construction form.
    """
    backend = build_engine_backend("generational-vector")
    assert isinstance(backend, GenerationalBackend)


# `auto_vector_max_capacity` used to gate only `"auto"`'s own choice
# between Backend G and Backend V (`_resolve_auto_engine_backend`) —
# explicit `"generational-vector"` selection accepted any capacity at
# all, including the default locus length's own `4**200`, which `build_
# vectorized_state` cannot allocate (this project's own multi-model
# engine review, 2026-09-04, `FIM-47`/finding C-04/finding P1.2/finding
# P1-4). These mirror the "auto" capacity tests above exactly, with the
# same params, to make the before/after contrast direct: the same config
# that makes `"auto"` fall back to Backend G now makes explicit
# `"generational-vector"` raise, rather than silently attempting the
# allocation anyway.


def test_build_engine_backend_vector_rejects_oversized_capacity() -> None:
    """Explicit finite-alleles selection gets the same capacity ceiling `"auto"` has."""
    params = _finite_alleles_vector_params(
        d=40, loci=(LocusSpec(1, 6),)
    )  # capacity 4096
    with pytest.raises(ValueError, match="auto_vector_max_capacity"):
        build_engine_backend(
            "generational-vector", params=params, auto_vector_max_capacity=1024
        )


def test_build_engine_backend_vector_respects_custom_capacity_ceiling() -> None:
    """The explicit-path ceiling is a real, configurable parameter, not a hidden one."""
    params = _finite_alleles_vector_params(
        d=40, loci=(LocusSpec(1, 6),)
    )  # capacity 4096
    backend = build_engine_backend(
        "generational-vector", params=params, auto_vector_max_capacity=4096
    )
    assert isinstance(backend, GenerationalBackend)
    assert isinstance(backend._advancer, VectorizedAdvancer)


def test_build_engine_backend_vector_needs_every_locus_within_capacity() -> None:
    """One oversized locus disqualifies explicit selection too, not just `"auto"`."""
    params = _finite_alleles_vector_params(
        d=40, loci=(LocusSpec(1, 2), LocusSpec(2, 6))
    )  # capacities 16, 4096
    with pytest.raises(ValueError, match="auto_vector_max_capacity"):
        build_engine_backend(
            "generational-vector", params=params, auto_vector_max_capacity=1024
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


def test_build_engine_backend_auto_picks_vector_for_stochastic_migration() -> None:
    """Stochastic migrant counts no longer disqualify `"auto"` from Backend V.

    `d=40` clears the default threshold, and `VectorizedAdvancer` draws the
    migrant counts itself, so `"auto"` picks it just as for continuous
    migration.
    """
    pytest.importorskip("numba")
    params = replace(_finite_alleles_vector_params(d=40), migrant_sampling="stochastic")
    backend = build_engine_backend("auto", params=params, auto_vector_min_d=35)
    assert isinstance(backend, GenerationalBackend)
    assert isinstance(backend._advancer, VectorizedAdvancer)


def test_build_engine_backend_auto_picks_generational_when_vector_ineligible() -> None:
    """A large `d` alone is not enough: `jit="numba"` keeps `"auto"` on G.

    `d=40` clears the default threshold, but only Backend G offers `jit`,
    so a caller who asked for it gets it rather than a `ValueError`.
    """
    params = replace(
        _finite_alleles_vector_params(d=40), engine_backend="auto", jit="numba"
    )
    backend = build_engine_backend(
        "auto", params=params, auto_vector_min_d=35, jit="numba"
    )
    assert isinstance(backend, GenerationalBackend)
    assert isinstance(backend._advancer, ThreadedAdvancer)


def test_build_engine_backend_auto_picks_vector_for_infinite_alleles() -> None:
    """Infinite alleles with continuous migration is V's home ground now.

    Locus length 200 (capacity `4 ** 200`) is irrelevant: an
    infinite-alleles table is as wide as the alleles alive at once, so the
    capacity ceiling does not apply, however small it is set.
    """
    params = replace(
        _finite_alleles_vector_params(d=40, loci=(LocusSpec(1, 200),)),
        mutation_model="infinite_alleles",
    )
    backend = build_engine_backend(
        "auto", params=params, auto_vector_min_d=2, auto_vector_max_capacity=1
    )
    assert isinstance(backend, GenerationalBackend)
    assert isinstance(backend._advancer, VectorizedAdvancer)


def test_build_engine_backend_auto_respects_custom_threshold() -> None:
    """The cutover is a real, configurable parameter, not a hidden constant."""
    params = _finite_alleles_vector_params(d=10)
    backend = build_engine_backend("auto", params=params, auto_vector_min_d=5)
    assert isinstance(backend, GenerationalBackend)
    assert isinstance(backend._advancer, VectorizedAdvancer)


def test_build_engine_backend_auto_honors_a_jit_request_with_generational() -> None:
    """`"auto"` with `jit="numba"` picks Backend G, the one backend with a toggle.

    Backend V has no `jit` switch; failing a configuration that names
    `engine_backend: auto` and `jit: numba` (which ran Backend G before V
    could run infinite alleles) would be a regression, and the output is
    the same either way, so the request wins.
    """
    params = _finite_alleles_vector_params(d=40)
    backend = build_engine_backend(
        "auto", params=params, auto_vector_min_d=35, jit="numba"
    )
    assert isinstance(backend, GenerationalBackend)
    assert isinstance(backend._advancer, ThreadedAdvancer)
    with pytest.raises(ValueError, match="generational-vector"):
        build_engine_backend("generational-vector", params=params, jit="numba")


# `auto_vector_max_capacity` (`20260903-claude-sonnet-5-fim-vg-
# performance-campaign-design.md` §6.1 item 2): "auto" used to read
# `params.d` alone, so a large-`d`, large-capacity config could resolve
# to `"generational-vector"` even inside the loci-length-sweep region
# already found to lose there (backend-factory design §10 item 10b).


def test_build_engine_backend_auto_picks_generational_above_capacity_ceiling() -> None:
    """A large `d` alone is not enough — capacity above the ceiling still falls back.

    `d=40` clears `auto_vector_min_d` and `mutation_model`/
    `migrant_sampling` are eligible, but `length=6` (capacity `4096`)
    exceeds `DEFAULT_AUTO_VECTOR_MAX_CAPACITY` (`1024`) — the exact
    loci-length-sweep region already found `"generational-vector"`
    losing at.
    """
    params = _finite_alleles_vector_params(d=40, loci=(LocusSpec(1, 6),))
    backend = build_engine_backend(
        "auto", params=params, auto_vector_min_d=35, auto_vector_max_capacity=1024
    )
    assert isinstance(backend, GenerationalBackend)
    assert isinstance(backend._advancer, ThreadedAdvancer)


def test_build_engine_backend_auto_respects_custom_capacity_ceiling() -> None:
    """The capacity ceiling is a real, configurable parameter, not a hidden constant."""
    # capacity 4096
    params = _finite_alleles_vector_params(d=40, loci=(LocusSpec(1, 6),))
    backend = build_engine_backend(
        "auto", params=params, auto_vector_min_d=35, auto_vector_max_capacity=4096
    )
    assert isinstance(backend, GenerationalBackend)
    assert isinstance(backend._advancer, VectorizedAdvancer)


def test_build_engine_backend_auto_needs_every_locus_within_capacity() -> None:
    """One oversized locus disqualifies the whole run, not just its own locus.

    Mirrors how `mutation_model`/`migrant_sampling` eligibility already
    disqualifies the whole run from a single violated property — this
    checks the same "one disqualifying property anywhere" logic applies
    across `params.loci`, not just within one field: a small, eligible
    first locus does not rescue a run whose *second* locus exceeds the
    ceiling.
    """
    params = _finite_alleles_vector_params(
        d=40, loci=(LocusSpec(1, 2), LocusSpec(2, 6))
    )  # capacities 16, 4096
    backend = build_engine_backend(
        "auto", params=params, auto_vector_min_d=35, auto_vector_max_capacity=1024
    )
    assert isinstance(backend, GenerationalBackend)
    assert isinstance(backend._advancer, ThreadedAdvancer)


def test_fim_engine_backend_auto_runs_end_to_end(
    tiny_params: SimulationParams,
) -> None:
    """`fim(..., engine_backend="auto")` works through the public entry point.

    `tiny_params` is an infinite-alleles run with `d=2`, which is exactly
    the default `auto_vector_min_d`, so `"auto"` lands on Backend V, and
    the manifest says so.
    """
    pytest.importorskip("numba")
    result = fim(
        tiny_params.gene_copies,
        tiny_params.m,
        tiny_params.mu,
        tiny_params.d,
        params=tiny_params,
        clock=_clock,
        engine_backend="auto",
    )
    assert isinstance(result, RunResult)
    assert result.report["converged"] in (True, False)
    assert result.manifest.engine_backend == "generational-vector"


def test_fim_engine_backend_auto_records_generational_without_numba(
    tiny_params: SimulationParams, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without numba, `"auto"` runs Backend G and the manifest records G.

    The same run, same seed: only the provenance differs from the run
    above, because Backend V and Backend G produce identical output.
    """
    monkeypatch.setattr(engine, "_numba_is_available", lambda: False)
    result = fim(
        tiny_params.gene_copies,
        tiny_params.m,
        tiny_params.mu,
        tiny_params.d,
        params=tiny_params,
        clock=_clock,
        engine_backend="auto",
    )
    assert isinstance(result, RunResult)
    assert result.manifest.engine_backend == "generational"


def test_fim_engine_backend_auto_reaches_vector_end_to_end() -> None:
    """`fim(..., engine_backend="auto")` reaches Backend V when the config qualifies."""
    pytest.importorskip("numba")
    params = _finite_alleles_vector_params(d=40)

    result = fim(
        params.gene_copies,
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
        tiny_params.gene_copies,
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
        tiny_params.gene_copies,
        tiny_params.m,
        tiny_params.mu,
        tiny_params.d,
        params=tiny_params,
        clock=_clock,
    )
    assert isinstance(result, RunResult)
    assert result.manifest.engine_backend == "lineal"


def test_fim_records_equilibrium_split_provenance_in_the_manifest(
    tiny_params: SimulationParams,
) -> None:
    """A real, small equilibrium-split run populates all three new manifest fields.

    `20260907-claude-sonnet-5-equilibrium-split-design.md`, decision 5.
    `equilibrium_convergence_window=2`/`equilibrium_convergence_tolerance=
    1.0` guarantee convergence as soon as the trailing window fills (`H_S`
    is bounded in `[0, 1)`, so any two values are within a tolerance of
    `1.0`) -- exercising at least one real mutate/drift step of the
    ancestral phase without a slow test.
    """
    params = replace(
        tiny_params,
        equilibrium_convergence_window=2,
        equilibrium_convergence_tolerance=1.0,
        equilibrium_max_generations=50,
    )

    result = fim(
        params.gene_copies,
        params.m,
        params.mu,
        params.d,
        params=params,
        clock=_clock,
    )

    assert isinstance(result, RunResult)
    manifest = result.manifest
    assert manifest.initial_condition_mode == "equilibrium_split"
    assert manifest.equilibrium_generation_count is not None
    assert manifest.equilibrium_generation_count >= 1
    assert manifest.equilibrium_final_heterozygosity is not None
    assert 0.0 <= manifest.equilibrium_final_heterozygosity < 1.0


def test_fim_records_equilibrium_split_provenance_under_the_generational_backend(
    tiny_params: SimulationParams,
) -> None:
    """The Generational backend's own separate manifest-construction path
    (`_finalize_replica_lane`, not `_run_one`) records the identical
    provenance -- both code paths call `_generate_initial_state_with_
    outcome` independently, so both need their own coverage.
    """
    params = replace(
        tiny_params,
        equilibrium_convergence_window=2,
        equilibrium_convergence_tolerance=1.0,
        equilibrium_max_generations=50,
    )

    result = fim(
        params.gene_copies,
        params.m,
        params.mu,
        params.d,
        params=params,
        clock=_clock,
        engine_backend="generational",
    )

    assert isinstance(result, RunResult)
    manifest = result.manifest
    assert manifest.initial_condition_mode == "equilibrium_split"
    assert manifest.equilibrium_generation_count is not None
    assert manifest.equilibrium_final_heterozygosity is not None


def _equilibrium_split(params: SimulationParams) -> SimulationParams:
    """Return `params` founded by a short, real equilibrium-split ancestral phase."""
    return replace(
        params,
        equilibrium_convergence_window=2,
        equilibrium_convergence_tolerance=0.5,
        equilibrium_max_generations=200,
    )


@pytest.mark.parametrize("backend", ["lineal", "generational"])
def test_fim_streams_the_ancestral_phase_beside_the_trajectory(
    tiny_params: SimulationParams, tmp_path: Path, backend: EngineBackend
) -> None:
    """An equilibrium-split run writes `equilibrium_trajectory.tlog`.

    Both engine paths that support equilibrium-split (`_run_one` and
    `_build_replica_lane`) write it, beside `trajectory.tlog`, in the
    same row schema: the one ancestral deme at every generation of its
    own counter, `0` through `equilibrium_generation_count`, ending on
    exactly the heterozygosity the manifest records for the split.
    """
    params = _equilibrium_split(tiny_params)
    store = BinaryLogStore(tmp_path / "trajectory.tlog")

    result = fim(
        params.gene_copies,
        params.m,
        params.mu,
        params.d,
        params=params,
        store=store,
        clock=_clock,
        engine_backend=backend,
    )

    assert isinstance(result, RunResult)
    path = tmp_path / "equilibrium_trajectory.tlog"
    assert isinstance(result.equilibrium_store, BinaryLogStore)
    assert result.equilibrium_store.path == path
    rows = list(BinaryLogStore(path).read(result.run_id))
    count = result.manifest.equilibrium_generation_count
    assert count is not None
    assert count > 2
    by_generation: dict[int, list[TrajectoryRow]] = {}
    for row in rows:
        by_generation.setdefault(row["generation"], []).append(row)
    assert list(by_generation) == list(range(count + 1))
    assert {row["deme"] for row in rows} == {1}
    final = ModelState.from_rows(by_generation[count], loci=params.loci)
    assert final.deme_count == 1
    assert _mean_h_s(final) == pytest.approx(
        result.manifest.equilibrium_final_heterozygosity
    )


def test_fim_equilibrium_trajectory_is_identical_across_backends(
    tiny_params: SimulationParams, tmp_path: Path
) -> None:
    """The ancestral phase draws only from its own stream: same bytes either way."""
    params = _equilibrium_split(tiny_params)
    paths = {}
    for backend in ("lineal", "generational"):
        directory = tmp_path / backend
        fim(
            params.gene_copies,
            params.m,
            params.mu,
            params.d,
            params=params,
            store=BinaryLogStore(directory / "trajectory.tlog"),
            run_id="run-same",
            clock=_clock,
            engine_backend=backend,
        )
        paths[backend] = directory / "equilibrium_trajectory.tlog"
    assert paths["lineal"].read_bytes() == paths["generational"].read_bytes()


def test_fim_keeps_an_in_memory_ancestral_trajectory_for_a_library_call(
    tiny_params: SimulationParams,
) -> None:
    """With no store given, the ancestral rows stay readable on the result."""
    params = _equilibrium_split(tiny_params)

    result = fim(
        params.gene_copies,
        params.m,
        params.mu,
        params.d,
        params=params,
        clock=_clock,
    )

    assert isinstance(result, RunResult)
    assert result.equilibrium_store is not None
    ancestral_rows = result.equilibrium_store.read(result.run_id)
    generations = {row["generation"] for row in ancestral_rows}
    count = result.manifest.equilibrium_generation_count
    assert count is not None
    assert generations == set(range(count + 1))
    main_generations = {row["generation"] for row in result.store.read(result.run_id)}
    assert main_generations == set(range(result.manifest.generation_count))


def test_fim_dirichlet_run_writes_no_ancestral_trajectory(
    tiny_params: SimulationParams, tmp_path: Path
) -> None:
    """Every other initial condition has no ancestral phase and no file."""
    result = fim(
        tiny_params.gene_copies,
        tiny_params.m,
        tiny_params.mu,
        tiny_params.d,
        params=tiny_params,
        store=BinaryLogStore(tmp_path / "trajectory.tlog"),
        clock=_clock,
    )

    assert isinstance(result, RunResult)
    assert result.equilibrium_store is None
    assert not (tmp_path / "equilibrium_trajectory.tlog").exists()


def test_fim_dirichlet_run_leaves_equilibrium_manifest_fields_none(
    tiny_params: SimulationParams,
) -> None:
    """An ordinary Dirichlet-prior run never populates the equilibrium-only fields."""
    result = fim(
        tiny_params.gene_copies,
        tiny_params.m,
        tiny_params.mu,
        tiny_params.d,
        params=tiny_params,
        clock=_clock,
    )

    assert isinstance(result, RunResult)
    assert result.manifest.initial_condition_mode == "dirichlet"
    assert result.manifest.equilibrium_generation_count is None
    assert result.manifest.equilibrium_final_heterozygosity is None


def test_fim_auto_records_the_resolved_choice_not_the_literal_auto() -> None:
    """`"auto"`'s own manifest never contains the literal string `"auto"`.

    The whole reason this field exists: a runtime-data-dependent choice
    must still be recoverable from the persisted record. Checked at
    both ends of the threshold, on one config.
    """
    pytest.importorskip("numba")
    below = _finite_alleles_vector_params(d=30)
    below_result = fim(
        below.gene_copies,
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
        above.gene_copies,
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
        tiny_params.gene_copies,
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
        params.gene_copies,
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
        tiny_params.gene_copies,
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


def test_threaded_advancer_reuses_its_own_executor_across_ticks(
    tiny_params: SimulationParams,
) -> None:
    """`ThreadedAdvancer` builds one `ThreadPoolExecutor`, not one per generation.

    Regression test: `advance()` used to build a fresh
    `ThreadPoolExecutor` (and a fresh `SequentialAdvancer`) on every
    call — a real, measured cost across a multi-generation batch
    (design doc `20260901-claude-sonnet-5-fim-engine-backend-factory-
    design.md` S10 item 10a). Runs a real multi-generation batch (not a
    single `advance()` call in isolation, the same "a full run is not
    the same thing as its own parts" discipline this project's own
    Stage F8 minted-state bug already taught) and confirms the exact
    same `ThreadPoolExecutor`/`SequentialAdvancer` objects served every
    tick, by identity.
    """
    params = replace(tiny_params, n_replicates=3, max_generations=5)
    advancer = ThreadedAdvancer(max_workers=2)
    store = InMemoryTrajectoryStore()
    lanes = [
        _build_replica_lane(params, index, None, store, _clock)
        for index in range(params.n_replicates)
    ]

    # Not asserted directly (`advancer._executor is None` here would
    # narrow mypy's own static type for the rest of this function to
    # the literal `None`, since it cannot see `advance()`'s own
    # internal mutation of that attribute) -- the loop below already
    # confirms the executor exists, and stays the same object, from
    # the first tick onward.
    seen_executors: set[int] = set()
    seen_sequentials: set[int] = set()
    while any(lane.active for lane in lanes):
        active_lanes = [lane for lane in lanes if lane.active]
        newly_stopped = advancer.advance(active_lanes, store)
        assert advancer._executor is not None
        seen_executors.add(id(advancer._executor))
        seen_sequentials.add(id(advancer._sequential))
        for lane in newly_stopped:
            lane.active = False

    assert len(seen_executors) == 1
    assert len(seen_sequentials) == 1


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


# Stage 4 of `20260901-claude-sonnet-5-fim-engine-backend-factory-
# design.md` §10 item 10e's own phased plan: a full multi-generation
# round-trip parity test across the whole `step` pipeline (migrate,
# mutate, drift, in order), driven through the real `GenerationalBackend`/
# `SequentialAdvancer`/`run_batch` integration — not `step()` called
# directly in a hand-rolled test loop the way every stage 1-3b test
# above already does. Stage F8's own minted-bookkeeping bug (§10 Stage
# F8, "a real, serious, previously-unknown bug found by actually running
# a full multi-generation batch end to end, not caught by any per-
# operator exact-match test") is the reason this step exists as its own
# stage, not folded into stage 3b's own already-large test list — a
# per-operator test proves an operator is correct in isolation, not that
# the real batch-driving loop wires lanes/rng/`finite_alleles` together
# correctly.


def test_generational_backend_with_sequential_advancer_jit_matches_lineal_for_batch(
    tiny_params: SimulationParams,
) -> None:
    """`SequentialAdvancer(jit=True)` reproduces `LinealBackend` exactly, as a batch.

    Default infinite-alleles model, default continuous migration —
    stages 1-3 in full: `migrate`'s own batched blend, `mutate`'s own
    batched event-count draw and whole-generation minting reservation.
    A real multi-replicate batch (`n_replicates=3`), not a single lane,
    so `run_batch`'s own lane-dispatch loop is genuinely exercised, not
    just `SequentialAdvancer.advance`'s own single-lane path.
    """
    pytest.importorskip("numba")
    params = replace(tiny_params, n_replicates=3)

    lineal_store = InMemoryTrajectoryStore()
    lineal_results = LinealBackend().run(params, lineal_store, None, _clock)
    assert isinstance(lineal_results, tuple)

    jit_store = InMemoryTrajectoryStore()
    jit_results = GenerationalBackend(SequentialAdvancer(jit=True)).run(
        params, jit_store, None, _clock
    )
    assert isinstance(jit_results, tuple)

    assert len(jit_results) == len(lineal_results) == 3
    for lineal_result, jit_result in zip(lineal_results, jit_results, strict=True):
        assert jit_result.run_id == lineal_result.run_id
        assert jit_result.report == lineal_result.report
        assert jit_result.final_state == lineal_result.final_state
        assert jit_result.manifest == lineal_result.manifest
        assert list(jit_store.read(jit_result.run_id)) == list(
            lineal_store.read(lineal_result.run_id)
        )


def test_sequential_advancer_jit_matches_lineal_under_finite_alleles(
    tiny_params: SimulationParams,
) -> None:
    """The same round-trip parity holds under the finite-alleles model too.

    A short locus (`length=2`, capacity 16) deliberately, not `tiny_
    params`'s own `length=200` (capacity `4**200`) — a capacity that
    large would silently stay under `_MAX_JIT_FINITE_ALLELE_CAPACITY`'s
    own bound every single time, meaning this test would never actually
    exercise the batched target-selection kernel (stage 3b) at all, only
    the already-covered event-count/source-attribution paths. `mutate`'s
    own event-count batching stays off here regardless (`finite_alleles`
    given — see `mutate`'s own `jit` docstring), so this specifically
    proves the source-attribution and target-selection halves survive
    the real batch-driving loop, across several demes and several real
    generations, not just `mutate`'s own already-covered single-call
    tests.
    """
    pytest.importorskip("numba")
    params = replace(
        tiny_params,
        d=4,
        mutation_model="finite_alleles",
        loci=(LocusSpec(1, 2),),  # capacity 16
        n_replicates=3,
    )

    lineal_store = InMemoryTrajectoryStore()
    lineal_results = LinealBackend().run(params, lineal_store, None, _clock)
    assert isinstance(lineal_results, tuple)

    jit_store = InMemoryTrajectoryStore()
    jit_results = GenerationalBackend(SequentialAdvancer(jit=True)).run(
        params, jit_store, None, _clock
    )
    assert isinstance(jit_results, tuple)

    assert len(jit_results) == len(lineal_results) == 3
    for lineal_result, jit_result in zip(lineal_results, jit_results, strict=True):
        assert jit_result.run_id == lineal_result.run_id
        assert jit_result.report == lineal_result.report
        assert jit_result.final_state == lineal_result.final_state
        assert jit_result.manifest == lineal_result.manifest
        assert list(jit_store.read(jit_result.run_id)) == list(
            lineal_store.read(lineal_result.run_id)
        )


def test_sequential_advancer_jit_matches_lineal_under_stochastic_migration(
    tiny_params: SimulationParams,
) -> None:
    """Round-trip parity holds when `migrate`'s own `jit` path is ineligible too.

    `migrant_sampling="stochastic"` takes `migrate`'s own batched path
    out of scope entirely (`migrate`'s own `jit` docstring) — combined
    with `mutation_model="finite_alleles"`, this is the "more than one
    operator's own `jit` support is simultaneously out of scope, in the
    same real run" case none of stages 1-3b's own tests exercised
    together, only ever one operator's own ineligibility at a time.
    """
    pytest.importorskip("numba")
    params = replace(
        tiny_params,
        d=4,
        mutation_model="finite_alleles",
        migrant_sampling="stochastic",
        loci=(LocusSpec(1, 2),),  # capacity 16
        n_replicates=3,
    )

    lineal_store = InMemoryTrajectoryStore()
    lineal_results = LinealBackend().run(params, lineal_store, None, _clock)
    assert isinstance(lineal_results, tuple)

    jit_store = InMemoryTrajectoryStore()
    jit_results = GenerationalBackend(SequentialAdvancer(jit=True)).run(
        params, jit_store, None, _clock
    )
    assert isinstance(jit_results, tuple)

    assert len(jit_results) == len(lineal_results) == 3
    for lineal_result, jit_result in zip(lineal_results, jit_results, strict=True):
        assert jit_result.run_id == lineal_result.run_id
        assert jit_result.report == lineal_result.report
        assert jit_result.final_state == lineal_result.final_state
        assert jit_result.manifest == lineal_result.manifest
        assert list(jit_store.read(jit_result.run_id)) == list(
            lineal_store.read(lineal_result.run_id)
        )


def test_fim_engine_backend_generational_with_jit_matches_default(
    tiny_params: SimulationParams,
) -> None:
    """`fim(..., engine_backend="generational", jit="numba")` end to end."""
    pytest.importorskip("numba")
    lineal_result = fim(
        tiny_params.gene_copies,
        tiny_params.m,
        tiny_params.mu,
        tiny_params.d,
        params=tiny_params,
        clock=_clock,
    )
    assert isinstance(lineal_result, RunResult)

    jit_result = fim(
        tiny_params.gene_copies,
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
    """A `finite_alleles`/continuous-migration config `VectorizedAdvancer` accepts.

    `n_replicates=1`/`stop_batch_early=False` explicitly, not
    `SimulationParams`'s own current defaults (`200`/`0.01`) — every
    caller of this helper except one explicit override
    (`test_generational_vector_backend_batch_is_independently_
    reproducible`) wants a single scalar run.
    """
    base = SimulationParams(
        gene_copies=40,
        m=0.2,
        mu=0.1,
        d=3,
        seed=20260901,
        loci=(LocusSpec(1, 2),),  # capacity 16
        mutation_model="finite_alleles",
        precision=1.0,
        max_generations=10,
        n_replicates=1,
        stop_batch_early=False,
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
    result.final_state.validate_support(
        tuple(_population_sizes(params.gene_copies, params.d))
    )


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


def test_finalize_replica_lane_releases_vectorized_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_finalize_replica_lane` clears a lane's own dense cache once it stops.

    Regression test for `FIM-48`: without this, a finished lane's own
    `VectorBlock` cache stayed referenced by `run_batch`'s own
    `lanes` list for the rest of the batch's own run, for nothing —
    `RunResult` only ever reads `lane.state` (already rebuilt by
    `VectorizedAdvancer.advance` before this function is ever called),
    never `lane.vectorized_state` itself. Also confirms the cache
    genuinely existed right before release, so this is not a vacuous
    pass on a lane that never populated it in the first place.
    """
    pytest.importorskip("numba")
    params = _finite_alleles_vector_params()
    had_cache_before_release = False
    real_finalize = engine._finalize_replica_lane

    def _spying_finalize(
        lane: ReplicaLane, clock: Clock, store: TrajectoryStore
    ) -> RunResult:
        nonlocal had_cache_before_release
        had_cache_before_release = lane.vectorized_state is not None
        result = real_finalize(lane, clock, store)
        assert lane.vectorized_state is None
        return result

    monkeypatch.setattr(engine, "_finalize_replica_lane", _spying_finalize)

    GenerationalBackend(VectorizedAdvancer()).run(
        params, InMemoryTrajectoryStore(), None, _clock
    )

    assert had_cache_before_release


def test_generational_vector_backend_windowed_batch_matches_unbounded() -> None:
    """A `max_concurrent_replicates` window changes nothing about Backend V's output.

    The same invariant `test_max_concurrent_replicates_does_not_change_
    what_a_batch_computes` checks for the dict-based `SequentialAdvancer`
    path, here for `VectorizedAdvancer` specifically — the one `Advancer`
    `FIM-48`'s own memory finding is actually about, so this is the
    backend a windowing bug most plausibly could have corrupted (a stale
    `vectorized_state` reused across lanes, say) without the dict-based
    test above ever noticing. `run_id` is deliberately excluded from the
    comparison — see the dict-based test's own docstring for why a
    differing `max_concurrent_replicates` changing it is expected.
    """
    pytest.importorskip("numba")
    params = replace(_finite_alleles_vector_params(), n_replicates=6)

    unbounded = GenerationalBackend(VectorizedAdvancer()).run(
        params, InMemoryTrajectoryStore(), None, _clock
    )
    windowed = GenerationalBackend(VectorizedAdvancer()).run(
        replace(params, max_concurrent_replicates=2),
        InMemoryTrajectoryStore(),
        None,
        _clock,
    )

    assert isinstance(unbounded, tuple)
    assert isinstance(windowed, tuple)
    for unbounded_result, windowed_result in zip(unbounded, windowed, strict=True):
        assert _report_without_run_id(
            unbounded_result.report
        ) == _report_without_run_id(windowed_result.report)
        assert unbounded_result.final_state == windowed_result.final_state


def test_generational_vector_backend_matches_lineal_exactly_without_migration() -> None:
    """A full multi-generation finite-alleles run matches `LinealBackend` when `m=0`.

    The original end-to-end exactness proof, kept as a small, readable
    case now that exactness holds in general
    (`test/engine/test_vector_parity.py` is the full matrix, with
    migration, several loci and batches). It guards the bug that made the
    first version of this path diverge: a locus's minted-state bookkeeping
    re-derived from the present alleles every generation forgets any allele
    minted and then driven extinct, even within the generation it was
    minted in — the normal fate of a fresh low-frequency mutant. The block
    carries the bookkeeping forward instead.
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


def test_vectorized_advancer_builds_a_lanes_state_only_once_each_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`VectorizedAdvancer` converts a lane's state once each way, not every tick.

    Regression test: `advance()` used to rebuild its array state from the
    `ModelState` and convert back on *every* generation, for every lane —
    a real, measured cost, larger than the biology it sat next to at a
    large capacity (design doc `20260901-claude-sonnet-5-fim-engine-
    backend-factory-design.md` §11's own reopened "across-generation
    fusion" question). `ReplicaLane.vectorized_state` caches the live
    `VectorBlock` across generations, so each direction is paid exactly
    once per lane for a whole run: forward (`VectorBlock.from_model_state`)
    on that lane's own first tick; backward (`VectorBlock.to_model_state`)
    once it stops. Counts real calls by wrapping (not replacing) both, so
    this also exercises a real multi-generation, multi-replicate batch end
    to end, not a mocked-out one.

    `lane.state` (`ModelState`) itself is checked too: it must stay
    exactly the generation-zero object `_build_replica_lane` built,
    completely untouched, for every tick before a lane stops — proof
    that nothing mid-run reassigns it via some other path than the one
    counted call above.
    """
    pytest.importorskip("numba")
    params = replace(
        _finite_alleles_vector_params(d=3), n_replicates=3, max_generations=6
    )

    build_call_count = 0
    real_build = VectorBlock.from_model_state

    def _counting_build(*args: object, **kwargs: object) -> VectorBlock:
        nonlocal build_call_count
        build_call_count += 1
        return real_build(*args, **kwargs)  # type: ignore[arg-type]

    reconstruct_call_count = 0
    real_reconstruct = VectorBlock.to_model_state

    def _counting_reconstruct(block: VectorBlock) -> ModelState:
        nonlocal reconstruct_call_count
        reconstruct_call_count += 1
        return real_reconstruct(block)

    monkeypatch.setattr(VectorBlock, "from_model_state", _counting_build)
    monkeypatch.setattr(VectorBlock, "to_model_state", _counting_reconstruct)

    advancer = VectorizedAdvancer()
    store = InMemoryTrajectoryStore()
    lanes = [
        _build_replica_lane(params, index, None, store, _clock)
        for index in range(params.n_replicates)
    ]
    initial_model_states = {lane.replica_index: lane.state for lane in lanes}

    while any(lane.active for lane in lanes):
        active_lanes = [lane for lane in lanes if lane.active]
        newly_stopped = advancer.advance(active_lanes, store)
        for lane in active_lanes:
            if lane not in newly_stopped:
                assert lane.state is initial_model_states[lane.replica_index]
        for lane in newly_stopped:
            lane.active = False

    assert build_call_count == params.n_replicates
    assert reconstruct_call_count == params.n_replicates
    for lane in lanes:
        assert lane.state is not initial_model_states[lane.replica_index]


def test_fim_engine_backend_generational_vector_runs_end_to_end() -> None:
    """`fim(..., engine_backend="generational-vector")` works end to end."""
    pytest.importorskip("numba")
    params = _finite_alleles_vector_params()

    result = fim(
        params.gene_copies,
        params.m,
        params.mu,
        params.d,
        params=params,
        clock=_clock,
        engine_backend="generational-vector",
    )
    assert isinstance(result, RunResult)
    assert result.report["converged"] in (True, False)


def test_vectorized_advancer_builds_a_migration_plan_once_per_lane() -> None:
    """A genuine `(d, d)` matrix `m` is converted once, then reused every tick.

    Found by the Stage 4/Stage V3 benchmark sweep: a weight-matrix
    conversion was being redone every generation even though `params.m`
    and the deme sizes never change mid-run. The lane's block now holds
    one `VectorMigration` for the whole run; checked directly rather than
    inferred from timing: the exact same plan and the exact same array
    object (`is`, not just equal) survive two consecutive `advance()`
    calls.
    """
    pytest.importorskip("numba")
    matrix = ((0.8, 0.1, 0.1), (0.1, 0.8, 0.1), (0.1, 0.1, 0.8))
    params = _finite_alleles_vector_params(m=matrix, max_generations=3)
    store = InMemoryTrajectoryStore()
    lane = _build_replica_lane(params, 0, None, store, _clock)
    advancer = VectorizedAdvancer()

    advancer.advance([lane], store)
    assert isinstance(lane.vectorized_state, VectorBlock)
    first_plan = lane.vectorized_state.migration
    assert first_plan.kind == MIGRATION_MATRIX
    assert first_plan.weights.tolist() == [list(row) for row in matrix]

    advancer.advance([lane], store)
    assert isinstance(lane.vectorized_state, VectorBlock)
    assert lane.vectorized_state.migration is first_plan
    assert lane.vectorized_state.migration.weights is first_plan.weights


def test_vectorized_advancer_builds_no_matrix_for_a_scalar_rate() -> None:
    """A plain scalar `m` never builds a `(d, d)` matrix, on any tick.

    The scalar rate is applied by a size-weighted pool in `O(d * K)` with
    no matrix at all, so there is nothing to build for this, the most
    common configuration. Checked across several generations, not just
    the first tick.
    """
    pytest.importorskip("numba")
    params = _finite_alleles_vector_params(max_generations=3)
    store = InMemoryTrajectoryStore()
    lane = _build_replica_lane(params, 0, None, store, _clock)
    advancer = VectorizedAdvancer()

    for _ in range(3):
        advancer.advance([lane], store)
        assert isinstance(lane.vectorized_state, VectorBlock)
        plan = lane.vectorized_state.migration
        assert plan.kind == MIGRATION_SCALAR
        assert plan.weights.size == 0


# Cross-backend *structural* parity (performance-baseline remediation
# item 10, step 3: "add deterministic structural/per-operation
# regression tests in CI where possible; leave timing/RSS comparison as
# a controlled maintainer benchmark"). Everything below asserts the
# shape of what a backend produced, never how long it took -- a
# wall-clock comparison belongs in `dev/bin/benchmark-engines` and its
# recorded tables (`doc/fim-engine-backend-benchmarks.md`), never in a
# gate whose result would then depend on runner load rather than on the
# commit (CLAUDE.md: "a test is a pure function of its commit").


def _structural_backends() -> list[tuple[str, GenerationalBackend | LinealBackend]]:
    """Return every engine backend/advancer combination `fim` can reach, named.

    One list, so a newly added `Advancer` joins the cross-backend
    structural invariant below by being added here once rather than by
    someone remembering to write a parallel test for it. Built eagerly,
    including the two numba-dependent entries, so the caller can skip
    the whole comparison at once when `numba` is absent rather than
    silently comparing a narrowed subset that still passes.
    """
    return [
        ("lineal", LinealBackend()),
        ("generational/sequential", GenerationalBackend(SequentialAdvancer())),
        (
            "generational/sequential+jit",
            # `SequentialAdvancer`'s own `jit` is a plain `bool`, unlike
            # `ThreadedAdvancer`'s `JitChoice` string, below.
            GenerationalBackend(SequentialAdvancer(jit=True)),
        ),
        ("generational/threaded", GenerationalBackend(ThreadedAdvancer())),
        (
            "generational/threaded+jit",
            GenerationalBackend(ThreadedAdvancer(jit="numba")),
        ),
        ("generational-vector", GenerationalBackend(VectorizedAdvancer())),
    ]


def test_every_engine_backend_visits_the_same_generations_and_output_shape() -> None:
    """Every backend writes one full distribution per generation, deme, and locus.

    The structural counterpart to this file's own value-level parity
    tests, which are necessarily pairwise and necessarily narrow:
    `test_generational_vector_backend_matches_lineal_exactly_without_
    migration` can only compare `LinealBackend` to Backend V with
    `m=0.0`, because with migration active the two diverge bit-for-bit
    by design (`migrate_vectorized`'s dense matmul versus `migrate`'s
    dict-based blend -- see that test's own docstring), and the
    statistical tests that *do* run with migration active compare
    distributions across hundreds of replicates rather than one run's
    own structure.

    That leaves a real gap this closes: with migration active -- the
    ordinary, default case -- nothing asserted that all six
    backend/advancer combinations even agree on *how much* they
    produce. A backend that silently stopped one generation early, or
    wrote generation zero twice, or dropped a locus, or emitted an
    unnormalized distribution, would diverge in values anyway, so no
    value comparison could distinguish that defect from the accepted
    floating-point divergence. These invariants are independent of
    every value:

    - the generations visited are exactly `0 .. max_generations`,
    - each `(generation, deme, locus)` appears once and its
      frequencies sum to one,
    - the stop reason, stopping generation, and `converged` flag agree
      across every backend, and
    - the persisted row keys and report keys are the same set
      everywhere.

    `precision=0.0` with a real window is what makes the
    third invariant meaningful rather than coincidental: an exactly-zero
    half-window mean difference effectively cannot occur here, so every
    backend is expected to stop at the generation cap, and the
    assertion says so directly instead of comparing whatever each one
    happened to do. A backend that converged early would fail loudly
    here rather than quietly being compared against a different-length
    run.
    """
    pytest.importorskip("numba")
    params = SimulationParams(
        gene_copies=40,
        m=0.2,
        mu=0.1,
        d=3,
        seed=20260901,
        loci=(LocusSpec(1, 2), LocusSpec(2, 2)),
        mutation_model="finite_alleles",
        precision=0.0,
        max_generations=6,
        n_replicates=1,
        stop_batch_early=False,
    )
    expected_generations = tuple(range(params.max_generations + 1))
    expected_groups = {
        (generation, deme, locus.locus_id)
        for generation in expected_generations
        # Deme ids are 1-based in a persisted row, matching the
        # model's own `ModelState` deme numbering.
        for deme in range(1, params.d + 1)
        for locus in params.loci
    }

    row_key_sets: dict[str, frozenset[str]] = {}
    report_key_sets: dict[str, frozenset[str]] = {}
    stopping_summaries: dict[str, tuple[object, object, object]] = {}

    for name, backend in _structural_backends():
        store = InMemoryTrajectoryStore()
        result = backend.run(params, store, None, _clock)
        assert isinstance(result, RunResult), name
        rows = list(store.read(result.run_id))
        assert rows, name

        # Every row belongs to this run, and the generations present are
        # exactly the expected contiguous range -- not merely the right
        # count of them, which a duplicated generation zero alongside a
        # missing final generation would also satisfy.
        assert {row["run_id"] for row in rows} == {result.run_id}, name
        assert tuple(sorted({row["generation"] for row in rows})) == (
            expected_generations
        ), name

        # One complete, normalized frequency distribution per
        # (generation, deme, locus): the "same shape of output" claim,
        # asserted against a computed expectation rather than against
        # another backend's output, so each backend stands on its own.
        group_totals: dict[tuple[int, int, int], float] = {}
        for row in rows:
            key = (row["generation"], row["deme"], row["locus_id"])
            group_totals[key] = group_totals.get(key, 0.0) + float(row["frequency"])
        assert set(group_totals) == expected_groups, name
        for key, total in group_totals.items():
            assert total == pytest.approx(1.0, abs=1e-9), (name, key)

        row_key_sets[name] = frozenset(rows[0].keys())
        report_key_sets[name] = frozenset(result.report)
        stopping_summaries[name] = (
            result.report["reason"],
            result.report["generation"],
            result.report["converged"],
        )

    # `run_id` deliberately excluded from the cross-backend comparison
    # below by comparing only key *sets* and the stopping summary:
    # `deterministic_run_id` hashes a run's own whole configuration,
    # `engine_backend` included, so every backend here has a different
    # one by design.
    assert len(set(row_key_sets.values())) == 1, row_key_sets
    assert len(set(report_key_sets.values())) == 1, report_key_sets
    assert len(set(stopping_summaries.values())) == 1, stopping_summaries
    assert next(iter(stopping_summaries.values())) == (
        StopReason.MAX_GENERATIONS,
        params.max_generations,
        False,
    )


def test_the_report_carries_both_expected_value_forms_for_d_and_g_st(
    tiny_params: SimulationParams,
) -> None:
    """`D` and `G_ST` report both forms; a non-identity statistic reports one."""
    result = _run(tiny_params)

    window = result.report["window_statistics"]
    for name in ("D", "G_ST"):
        entry = window[name]
        assert entry["selected_form"] == "mean_of_values"
        assert entry["mean"] == entry["mean_of_values"]["mean"]
        assert set(entry["value_of_means"]) == {"mean", "standard_error"}
        assert entry["undefined_generations"] >= 0
    assert "value_of_means" not in window["H_ST"]
    assert window["H_ST"]["selected_form"] == "mean_of_values"


def test_value_of_means_selection_changes_the_headline_not_the_other_form(
    tiny_params: SimulationParams,
) -> None:
    """The setting picks the headline; both forms stay in the report."""
    chosen = replace(tiny_params, convergence_estimate="value_of_means")

    default_entry = _run(tiny_params).report["window_statistics"]["D"]
    chosen_entry = _run(chosen).report["window_statistics"]["D"]

    assert chosen_entry["selected_form"] == "value_of_means"
    assert chosen_entry["mean"] == chosen_entry["value_of_means"]["mean"]
    assert default_entry["mean"] == default_entry["mean_of_values"]["mean"]


def test_every_statistic_reports_the_one_shared_evidence_window(
    tiny_params: SimulationParams,
) -> None:
    """All entries share a window, the burn-in, the targets, and a finite `z`."""
    params = replace(tiny_params, precision=0.0, max_generations=400)
    result = _run(params)

    window = result.report["window_statistics"]
    assert {"D", "G_ST", "H_S", "H_T", "H_ST"} <= set(window)
    first = window["D"]
    assert first["burn_in"] == 1
    assert first["window_start"] == 1
    assert first["window_end"] == result.report["generation"]
    assert first["minimum_ess"] == params.expert.minimum_effective_sample_size
    for name, entry in window.items():
        for key in (
            "window_start",
            "window_end",
            "burn_in",
            "minimum_ess",
            "target_standard_error",
        ):
            assert entry[key] == first[key], (name, key)
        assert math.isfinite(entry["geweke_z"]), name
    # The report must be strict JSON: no infinity, no NaN.
    json.dumps(result.report, allow_nan=False)


def test_a_short_run_reports_no_geweke_z(tiny_params: SimulationParams) -> None:
    """A window too short for two segments has no start-against-end diagnostic."""
    result = _run(tiny_params)

    assert all("geweke_z" not in e for e in result.report["window_statistics"].values())


def _matched_batch_params(**changes: object) -> SimulationParams:
    """A six-replicate batch whose first wave is two, so windows are matched."""
    config: dict[str, object] = {
        **_tiny_config(),
        "precision": 0.05,
        "max_generations": 400,
        "n_replicates": 6,
        "expert": {**FAST_EXPERT_SETTINGS, "batch_width": 2},
    }
    config.update(changes)
    return SimulationParams.from_mapping(config)


def test_a_batch_matches_each_replicates_window_to_the_first_wave() -> None:
    """The first wave averages for the guess; the rest for the matched window."""
    params = _matched_batch_params()
    output = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, clock=_clock
    )
    assert isinstance(output, tuple)

    windows = [result.params.replicate_averaging_window for result in output]
    assert params.relaxation_time is not None
    guess = math.ceil(
        params.expert.first_wave_averaging_multiple * params.relaxation_time
    )
    assert windows[:2] == [min(guess, 399)] * 2
    assert len(set(windows[2:])) == 1
    assert windows[2] != windows[0]
    for result, window in zip(output, windows, strict=True):
        assert result.report["reason"] == "averaging window complete"
        assert result.report["generation"] == result.params.convergence_burn_in + window
        entry = result.report["window_statistics"]["D"]
        assert entry["window_start"] == result.params.convergence_burn_in
        assert entry["window_end"] == result.report["generation"]


def test_every_backend_gives_the_same_matched_batch() -> None:
    """Windows come from the configuration, so no backend can change them."""
    params = _matched_batch_params()
    lineal = LinealBackend().run(params, InMemoryTrajectoryStore(), None, _clock)
    sequential = GenerationalBackend(SequentialAdvancer()).run(
        params, InMemoryTrajectoryStore(), None, _clock
    )
    threaded = GenerationalBackend(ThreadedAdvancer(max_workers=3)).run(
        params, InMemoryTrajectoryStore(), None, _clock
    )
    assert isinstance(lineal, tuple)
    assert isinstance(sequential, tuple)
    assert isinstance(threaded, tuple)

    for other in (sequential, threaded):
        assert len(other) == len(lineal) == 6
        for actual, expected in zip(other, lineal, strict=True):
            assert actual.run_id == expected.run_id
            assert actual.params == expected.params
            assert actual.final_state == expected.final_state
            assert actual.report == expected.report
    assert replicate_summary(sequential) == replicate_summary(lineal)


def test_a_limit_on_concurrent_replicates_is_the_first_wave_too() -> None:
    """`max_concurrent_replicates` sets the wave; results match the lineal batch."""
    params = _matched_batch_params(max_concurrent_replicates=2)
    lineal = LinealBackend().run(params, InMemoryTrajectoryStore(), None, _clock)
    generational = GenerationalBackend(SequentialAdvancer()).run(
        params, InMemoryTrajectoryStore(), None, _clock
    )
    assert isinstance(lineal, tuple)
    assert isinstance(generational, tuple)

    assert [r.final_state for r in generational] == [r.final_state for r in lineal]
    assert [r.report for r in generational] == [r.report for r in lineal]


def test_the_parallel_worker_path_matches_the_sequential_batch() -> None:
    """Worker processes get the same windows, in waves, as the sequential loop."""
    params = _matched_batch_params()
    sequential = LinealBackend().run(params, InMemoryTrajectoryStore(), None, _clock)
    parallel = LinealBackend(max_workers=3).run(params, None, None, _clock)
    assert isinstance(sequential, tuple)
    assert isinstance(parallel, tuple)

    assert [r.final_state for r in parallel] == [r.final_state for r in sequential]
    assert [r.report for r in parallel] == [r.report for r in sequential]


def test_a_fixed_window_is_the_same_for_every_replicate_and_for_a_single_run(
    tiny_params: SimulationParams,
) -> None:
    """An explicit window is a fixed-window run, alone or in a batch."""
    batch = replace(
        tiny_params, n_replicates=3, replicate_averaging_window=17, max_generations=100
    )
    output = fim(
        batch.gene_copies, batch.m, batch.mu, batch.d, params=batch, clock=_clock
    )
    assert isinstance(output, tuple)

    for result in output:
        assert result.params.replicate_averaging_window == 17
        assert result.report["generation"] == 1 + 17
    alone = _run(output[1].params)
    assert alone.final_state == output[1].final_state


def test_a_batch_summary_is_the_mean_of_the_replicates_window_means() -> None:
    """Each replicate contributes what it measured over its window."""
    params = _matched_batch_params()
    output = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, clock=_clock
    )
    assert isinstance(output, tuple)

    summary = replicate_summary(output)

    means = [r.report["window_statistics"]["D"]["mean"] for r in output]
    assert summary["D"]["mean"] == pytest.approx(statistics.fmean(means))
    final_values = [r.report["D"] for r in output]
    assert statistics.fmean(final_values) != pytest.approx(summary["D"]["mean"])


def test_a_value_of_means_batch_pools_the_identities_before_the_statistic() -> None:
    """Form two for a batch: `f` of the pooled `H_S`/`H_T`, delta-method interval."""
    params = _matched_batch_params(convergence_estimate="value_of_means")
    output = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, clock=_clock
    )
    assert isinstance(output, tuple)

    summary = replicate_summary(output)

    windows = [r.report["window_statistics"] for r in output]
    mean_s = statistics.fmean(w["H_S"]["mean"] for w in windows)
    mean_t = statistics.fmean(w["H_T"]["mean"] for w in windows)
    expected = _jost_d_from_within_and_total(2, mean_s, mean_t)
    assert summary["D"]["mean"] == pytest.approx(expected, abs=1e-12)
    per_replicate = statistics.fmean(w["D"]["value_of_means"]["mean"] for w in windows)
    assert summary["D"]["mean"] != pytest.approx(per_replicate, abs=1e-15)
    assert summary["D"]["half_width"] > 0.0
    assert summary["D"]["low"] < summary["D"]["mean"] < summary["D"]["high"]


def test_planned_replicates_runs_every_replicate_without_an_early_stop() -> None:
    """`planned_replicates` ignores `stop_batch_early` and keeps all replicates."""
    params = _matched_batch_params(
        precision_method="planned_replicates", stop_batch_early=True, precision=0.5
    )
    output = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, clock=_clock
    )
    assert isinstance(output, tuple)

    assert len(output) == 6
    assert all(r.params.precision_method == "interval" for r in output)


def test_a_replicate_with_no_burn_in_keeps_the_within_run_rule() -> None:
    """With no relaxation time the fractional burn-in leaves no window to match."""
    params = SimulationParams.from_mapping(
        {
            **_tiny_config(),
            "mu": 0.0,
            "m": 0.0,
            "convergence_burn_in": "auto",
            "max_generations": 60,
            "n_replicates": 3,
            "precision": 1.0,
            "initial_allele_count": 2,
        }
    )
    assert params.convergence_burn_in == 0

    output = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, clock=_clock
    )

    assert isinstance(output, tuple)
    assert all(r.report["reason"] != "averaging window complete" for r in output)


def test_a_capped_run_reports_how_long_it_would_have_needed(
    tiny_params: SimulationParams,
) -> None:
    """A run that hits the cap records `projected_generations` per statistic."""
    params = replace(
        tiny_params, precision=0.001, max_generations=300, stop_batch_early=False
    )
    result = _run(params)

    assert result.report["converged"] is False
    entry = result.report["window_statistics"]["D"]
    assert entry["projected_generations"] > params.max_generations
    json.dumps(result.report, allow_nan=False)


def test_a_run_that_converged_reports_no_projection(
    tiny_params: SimulationParams,
) -> None:
    """Only a capped run has a projection."""
    result = _run(tiny_params)

    assert result.report["converged"] is True
    assert all(
        "projected_generations" not in e
        for e in result.report["window_statistics"].values()
    )


_POOLED_WATCHED = ("Gs", "Gd", "D_m", "R_ST", "G_ST_NEI_LOG", "G_ST_HEDRICK", "F_ST")
_NEI_WATCHED = (
    "NEI_I_ALL_GEO",
    "NEI_I_ALL_ARITH",
    "NEI_I_ALL_GEO_LOCUS_MEAN",
    "NEI_I_ALL_ARITH_LOCUS_MEAN",
    "NEI_D_ALL_GEO",
    "NEI_D_ALL_ARITH",
)


@pytest.mark.parametrize("statistic", [*_POOLED_WATCHED, *_NEI_WATCHED])
def test_a_run_can_watch_any_global_statistic_and_records_its_history(
    tiny_params: SimulationParams, statistic: str
) -> None:
    """The watched statistic's per-generation history ends at its report value."""
    params = replace(
        tiny_params,
        convergence_statistic=statistic,
        precision=0.0,
        max_generations=30,
        loci=(LocusSpec(1, 200), LocusSpec(2, 200)),
        mu=(0.01, 0.01),
    )
    result = _run(params)

    history = result.convergence_histories[statistic]
    assert len(history) > 5
    final = result.report[statistic]  # type: ignore[literal-required]
    if final is not None:
        assert history[-1] == pytest.approx(final, rel=1e-12, abs=1e-12)
    assert statistic in result.report["window_statistics"] or len(history) < 3


@pytest.mark.parametrize("backend", ["generational", "generational-vector"])
def test_watched_extra_statistics_agree_across_backends(
    tiny_params: SimulationParams, backend: EngineBackend
) -> None:
    """`Gs`, `D_m` and a Nei identity have the same history under every kernel."""
    if backend == "generational-vector":
        pytest.importorskip("numba")
    watched = ("Gs", "D_m", "NEI_I_ALL_GEO")
    base = replace(
        tiny_params,
        convergence_statistic=watched,
        convergence_combinator="all",
        precision=0.0,
        max_generations=25,
        mutation_model="finite_alleles",
        loci=(LocusSpec(1, 4),),
    )
    reference = _run(base)
    other = _run(replace(base, engine_backend=backend))

    for name in watched:
        assert other.convergence_histories[name] == pytest.approx(
            reference.convergence_histories[name], rel=1e-12, abs=1e-12
        )


def test_a_statistic_precision_override_reaches_the_monitor(
    tiny_params: SimulationParams,
) -> None:
    """A loose override lets a run stop where the run precision never would."""
    strict = replace(
        tiny_params,
        convergence_statistic="D",
        precision=0.0005,
        max_generations=200,
        stop_batch_early=False,
    )
    loose = replace(strict, statistic_precision=(("D", 0.9),))

    assert _run(strict).report["converged"] is False
    result = _run(loose)
    assert result.report["converged"] is True
    assert result.report["window_statistics"]["D"]["target_standard_error"] > 0.4


def test_the_unbounded_statistic_uses_a_relative_target(
    tiny_params: SimulationParams,
) -> None:
    """`A_CGD` counts alleles: its target scales with its mean, not the precision."""
    params = replace(
        tiny_params,
        convergence_statistic="A_CGD",
        precision=0.05,
        max_generations=40,
    )
    result = _run(params)

    entry = result.report["window_statistics"]["A_CGD"]
    z = 1.959963984540054
    expected = 0.05 * max(1.0, abs(entry["mean"])) / z
    assert entry["target_standard_error"] == pytest.approx(expected)


def _thinned_params(**changes: object) -> SimulationParams:
    """A single run that thins every 5th generation from generation 8 on."""
    config: dict[str, object] = {
        **_tiny_config(),
        "precision": 0.0,
        "max_generations": 40,
        "trajectory_retention": "thinned",
        "trajectory_stride": 5,
        "trajectory_thinning_start": 8,
    }
    config.update(changes)
    return SimulationParams.from_mapping(config)


def _written_generations(result: RunResult) -> list[int]:
    """Distinct generation numbers a run wrote to its in-memory store, ascending."""
    return sorted({row["generation"] for row in result.store.read(result.run_id)})


def test_a_thinned_run_writes_the_kept_generations_only() -> None:
    """Generation 0, the head, every 5th from 8, the burn-in, and the final one."""
    result = _run(_thinned_params())

    expected = sorted({*range(8), *range(8, 41, 5), 1, 40})
    assert _written_generations(result) == expected
    assert result.report["generation"] == 40


def test_thinning_changes_no_statistic_and_no_final_state() -> None:
    """Same seed, same report and final state, whether or not frames are skipped."""
    thinned = _run(_thinned_params())
    full = _run(_thinned_params(trajectory_retention="full"))

    assert thinned.final_state == full.final_state
    assert thinned.convergence_histories == full.convergence_histories
    assert {k: v for k, v in thinned.report.items() if k != "run_id"} == {
        k: v for k, v in full.report.items() if k != "run_id"
    }


@pytest.mark.parametrize("backend", ["generational", "generational-vector"])
def test_every_backend_thins_to_the_same_generations(backend: EngineBackend) -> None:
    """Lanes of the generation-first drivers keep the same frames as a lineal run."""
    if backend == "generational-vector":
        pytest.importorskip("numba")
    base = _thinned_params(
        mutation_model="finite_alleles", loci=[{"locus_id": 1, "length": 4}]
    )
    reference = _run(base)
    other = _run(replace(base, engine_backend=backend))

    assert _written_generations(other) == _written_generations(reference)
    assert other.final_state == reference.final_state


def test_a_batch_replicate_stops_on_a_generation_thinning_would_skip() -> None:
    """The stop generation is always written, whatever the stride says."""
    params = _thinned_params(
        n_replicates=3,
        replicate_averaging_window=23,
        trajectory_stride=50,
        trajectory_thinning_start=4,
    )
    output = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, clock=_clock
    )
    assert isinstance(output, tuple)

    for result in output:
        generations = _written_generations(result)
        assert generations[-1] == result.report["generation"] == 1 + 23
        assert generations[:4] == [0, 1, 2, 3]
