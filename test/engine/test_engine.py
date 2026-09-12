"""End-to-end tests for the deterministic library engine."""

import functools
import itertools
import statistics
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from fim import engine
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
    pooled_convergence_histories,
    replicate_summary,
    report_for_state,
    run_batch,
)
from fim.model.allele import MINTED_ID_START, AlleleId
from fim.model.locus import LocusSpec, finite_allele_capacity
from fim.model.operators import _population_sizes
from fim.model.params import ConvergenceCombinator, SimulationParams
from fim.model.state import ModelState
from fim.model.vectorized import (
    build_vectorized_state,
    step_vectorized,
    vectorized_state_to_model_state,
)
from fim.persistence.jsonl_store import JSONLTrajectoryStore
from fim.persistence.store import InMemoryTrajectoryStore, TrajectoryStore
from fim.statistics import differentiation


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
        "n_replicates": 1,
        "replicate_tolerance": None,
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


def _sigma_band_params(
    tiny_params: SimulationParams, **changes: object
) -> SimulationParams:
    """`tiny_params`, with a sigma band requested (design doc §"1")."""
    return SimulationParams.from_mapping(
        {
            **tiny_params.to_dict(),
            "sigma_band_multiplier": 2.0,
            "sigma_band_window": 5,
            **changes,
        }
    )


def test_sigma_band_summary_matches_a_hand_computed_mean_and_sigma() -> None:
    """`_sigma_band_summary` computes a population mean/sigma, not a sample one.

    Hand-computed against `[0.1, 0.2, 0.3]`: mean `0.2`, population
    variance `((0.1)**2 + 0**2 + (0.1)**2) / 3`, sigma the square root
    of that — dividing by the window size itself (`3`), not `3 - 1`
    (design doc decision 3's own "describes the observed spread of the
    window that ran, not an estimate extrapolated from a sample").
    """
    summary = engine._sigma_band_summary({"D": [0.1, 0.2, 0.3]}, 2.0)

    expected_sigma = statistics.pstdev([0.1, 0.2, 0.3])
    assert summary["D"]["mean"] == pytest.approx(0.2)
    assert summary["D"]["sigma"] == pytest.approx(expected_sigma)
    assert summary["D"]["lower"] == pytest.approx(0.2 - 2.0 * expected_sigma)
    assert summary["D"]["upper"] == pytest.approx(0.2 + 2.0 * expected_sigma)


def test_sigma_band_summary_omits_a_statistic_with_no_defined_values() -> None:
    """A statistic undefined for the whole window is dropped, not fabricated."""
    summary = engine._sigma_band_summary({"D": [0.5], "G_ST": []}, 3.0)

    assert set(summary) == {"D"}


def test_sigma_band_extension_leaves_the_primary_report_and_final_state_unchanged(
    tiny_params: SimulationParams,
) -> None:
    """The extension is strictly additive — decision 4's own core invariant.

    An otherwise-identical run with the sigma band enabled reports the
    identical `report`/`final_state`/`generation` a plain run without it
    would — the extension's own further generations never surface there
    at all, only in `manifest.sigma_band`.
    """
    plain = _run(tiny_params)
    extended = _run(_sigma_band_params(tiny_params))

    # `run_id` itself deliberately differs: `deterministic_run_id`
    # derives it from the full configuration
    # (`sigma_band_multiplier`/`sigma_band_window` included), so a
    # sigma-band-enabled run is correctly a distinct configuration, not
    # a bug in this comparison.
    assert {k: v for k, v in extended.report.items() if k != "run_id"} == {
        k: v for k, v in plain.report.items() if k != "run_id"
    }
    assert extended.final_state == plain.final_state
    assert extended.manifest.generation == plain.manifest.generation
    assert extended.manifest.generation_count == plain.manifest.generation_count
    assert plain.manifest.sigma_band is None
    assert extended.manifest.sigma_band is not None
    assert extended.manifest.sigma_band_multiplier == 2.0
    assert extended.manifest.sigma_band_window == 5


def test_sigma_band_is_none_when_the_run_only_hits_the_cap() -> None:
    """An unconverged (capped) run is never extended, even with the band configured.

    Mirrors `test_cap_is_a_valid_nonconverged_result`'s own capped
    configuration, with a sigma band also requested — decision 3's own
    "extending an unconverged run would misrepresent stability that was
    never reached."
    """
    params = SimulationParams.from_mapping(
        {
            **_tiny_config(),
            "convergence_window": 2,
            "convergence_tolerance": 0.0,
            "max_generations": 2,
            "sigma_band_multiplier": 2.0,
            "sigma_band_window": 5,
        }
    )

    result = _run(params)

    assert not result.report["converged"]
    assert result.manifest.sigma_band is None
    assert result.manifest.sigma_band_multiplier is None
    assert result.manifest.sigma_band_window is None


def test_sigma_band_window_length_changes_the_computed_band(
    tiny_params: SimulationParams,
) -> None:
    """A longer extension window genuinely runs further generations.

    Externally observable proof the extension loop actually iterates
    `sigma_band_window` times, not a fixed or ignored count: two window
    lengths, same seed otherwise, produce different bands (a different
    number of real, seeded-random generations were stepped through).
    """
    short = _run(_sigma_band_params(tiny_params, sigma_band_window=2))
    long = _run(_sigma_band_params(tiny_params, sigma_band_window=50))

    assert short.manifest.sigma_band != long.manifest.sigma_band


def test_sigma_band_is_reproducible_for_the_same_seed(
    tiny_params: SimulationParams,
) -> None:
    """The same seed and configuration reproduce a byte-identical band."""
    params = _sigma_band_params(tiny_params)

    first = _run(params)
    second = _run(params)

    assert first.manifest.sigma_band == second.manifest.sigma_band


@pytest.mark.parametrize(
    "backend_changes",
    [
        {"engine_backend": "lineal"},
        {"engine_backend": "generational"},
        # `"auto"`, with the default `mutation_model="infinite_alleles"`,
        # always resolves to `"generational"`
        # (`_resolve_auto_engine_backend`) — never `"lineal"`, which is
        # exactly why v1's own `"lineal"`-only restriction left this
        # feature unreachable for this project's recommended default.
        {"engine_backend": "auto"},
    ],
)
def test_sigma_band_is_supported_under_every_resolved_backend(
    tiny_params: SimulationParams, backend_changes: dict[str, object]
) -> None:
    """v2 lifted v1's `"lineal"`-only restriction — design doc decision 7.

    The direct replacement for this test's own v1 predecessor
    (`test_sigma_band_rejects_every_non_lineal_backend`), which asserted
    a `ValueError` here. v2's own step 7 removes that guard deliberately,
    so the assertion inverts: every one of these resolved backends now
    computes a real band rather than refusing the request.
    `"generational-vector"` needs a bounded finite-alleles capacity and
    so is covered separately, below.
    """
    params = _sigma_band_params(tiny_params, **backend_changes)

    result = _run(params)

    assert result.report["converged"]
    assert result.manifest.sigma_band is not None
    assert set(result.manifest.sigma_band) == set(params.convergence_statistics)
    assert result.manifest.sigma_band_multiplier == 2.0
    assert result.manifest.sigma_band_window == 5
    assert result.sigma_band_trajectory is not None
    assert len(result.sigma_band_trajectory) == 5


def test_sigma_band_under_generational_matches_lineal_exactly(
    tiny_params: SimulationParams,
) -> None:
    """`"generational"` reuses v1's extension helper, so its band is identical.

    Decision 7's own reasoning for sharing one dict-based implementation
    between the two backends, turned into an assertion: `Sequential
    Advancer` steps a lane with exactly the `step(...)` call `_run_one`
    itself uses and is bit-identical to `LinealBackend` for the same seed,
    so sharing `_run_dict_based_sigma_band_extension` must leave the two
    backends' bands bit-identical too — not merely statistically close.
    A future change that accidentally gave `"generational"` its own
    divergent extension path would fail here.
    """
    lineal = _run(_sigma_band_params(tiny_params, engine_backend="lineal"))
    generational = _run(_sigma_band_params(tiny_params, engine_backend="generational"))

    assert generational.manifest.sigma_band == lineal.manifest.sigma_band
    assert generational.sigma_band_trajectory == lineal.sigma_band_trajectory


def test_sigma_band_is_none_under_generational_when_the_run_only_hits_the_cap() -> None:
    """A capped `"generational"` lane is never extended either — decision 3, batch-side.

    The batch-path counterpart to `test_sigma_band_is_none_when_the_run_
    only_hits_the_cap`: `_lane_is_sigma_band_eligible` gates on the
    lane's own `outcome.converged`, so an unconverged lane gets no band
    no matter which backend drove it.
    """
    params = SimulationParams.from_mapping(
        {
            **_tiny_config(),
            "convergence_window": 2,
            "convergence_tolerance": 0.0,
            "max_generations": 2,
            "sigma_band_multiplier": 2.0,
            "sigma_band_window": 5,
            "engine_backend": "generational",
        }
    )

    result = _run(params)

    assert not result.report["converged"]
    assert result.manifest.sigma_band is None
    assert result.manifest.sigma_band_multiplier is None
    assert result.manifest.sigma_band_window is None
    assert result.sigma_band_trajectory is None


def test_sigma_band_extends_every_replicate_of_a_generational_batch() -> None:
    """A real batch gets one band per replicate, not just the first.

    `_apply_sigma_band_extensions` walks every finalized lane, so a
    multi-replicate batch's own manifests come out the same shape v1's
    `"lineal"` scalar case already produced — the v2 enforcement
    inventory's own "identical in shape to v1's `"lineal"` case".
    """
    params = SimulationParams.from_mapping(
        {
            **_tiny_config(),
            "n_replicates": 3,
            "sigma_band_multiplier": 2.0,
            "sigma_band_window": 5,
            "engine_backend": "generational",
        }
    )

    output = fim(params.N, params.m, params.mu, params.d, params=params, clock=_clock)
    assert isinstance(output, tuple)
    assert len(output) == 3

    for result in output:
        assert result.manifest.sigma_band is not None
        assert result.manifest.sigma_band_multiplier == 2.0
        assert result.manifest.sigma_band_window == 5
        assert result.sigma_band_trajectory is not None
        assert len(result.sigma_band_trajectory) == 5
    # Each replicate has its own seeded stream, so the bands genuinely
    # differ — proof every lane ran its own extension rather than one
    # lane's result being copied across all three.
    assert len({repr(result.manifest.sigma_band) for result in output}) == 3


def test_sigma_band_is_never_computed_for_an_adaptively_abandoned_lane() -> None:
    """An adaptive stop's abandoned lanes get no band — decision 8's closing note.

    `_apply_sigma_band_extensions` skips any lane with no `result` at
    all, which is exactly the set an adaptive `replicate_tolerance` stop
    discarded from the store just above `run_batch`'s own early return.
    A band is therefore never computed from, or persisted for, a
    replicate the adaptive stop chose not to keep.
    """
    params = SimulationParams.from_mapping(
        {
            **_tiny_config(),
            "n_replicates": 10,
            "replicate_minimum": 3,
            "replicate_tolerance": 1000.0,
            "sigma_band_multiplier": 2.0,
            "sigma_band_window": 5,
            "engine_backend": "generational",
        }
    )

    output = fim(params.N, params.m, params.mu, params.d, params=params, clock=_clock)
    assert isinstance(output, tuple)
    # The adaptive stop keeps strictly fewer than the requested ten.
    assert len(output) == 3
    # Every *kept* replicate still gets its own band.
    for result in output:
        assert result.manifest.sigma_band is not None
        assert result.sigma_band_trajectory is not None


def _sigma_band_vector_params(**overrides: object) -> SimulationParams:
    """A sigma-band-enabled config `VectorizedAdvancer` accepts.

    `loci=(LocusSpec(1, 2),)` gives a finite-alleles capacity of just 16
    (`finite_allele_capacity`), deliberately: a tiny identity space makes
    alleles go extinct and later reappear routinely within a short
    extension window, which is exactly the scenario decision 7's
    forgotten-minted-identity bug would silently mishandle. A
    `length=200` locus (this file's own `_tiny_config`) would instead
    exceed `auto_vector_max_capacity` outright.
    """
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
        n_replicates=1,
        replicate_tolerance=None,
        sigma_band_multiplier=2.0,
        sigma_band_window=12,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def _drive_vector_lane_to_stop(params: SimulationParams) -> ReplicaLane:
    """Advance one `VectorizedAdvancer` lane until it stops, without finalizing it.

    Deliberately skips `_finalize_replica_lane`, so the lane is observed
    in exactly the state `_apply_sigma_band_extensions` sees it in: its
    `vectorized_state` still live and correctly up to date.
    """
    store = InMemoryTrajectoryStore()
    lane = _build_replica_lane(params, 0, None, store, _clock)
    advancer = VectorizedAdvancer()
    while not lane.monitor.should_stop():
        advancer.advance([lane], store)
    return lane


def _present_allele_ids(locus_state: object) -> set[int]:
    """Return which allele ids carry any frequency at all in a locus's dense array."""
    frequencies = locus_state.frequencies  # type: ignore[attr-defined]
    return {int(i) for i in np.flatnonzero(frequencies.sum(axis=0) > 0.0)}


def _lanes_holding_caches(lanes: Sequence[ReplicaLane]) -> int:
    """Count how many lanes still hold a `VectorizedAdvancer` cache.

    `vectorized_state` is the field `_finalize_replica_lane` releases
    immediately for an ineligible lane and defers for an eligible one, so
    counting it is how decision 9's own peak-then-release claim is
    observed directly rather than asserted.
    """
    return sum(1 for lane in lanes if lane.vectorized_state is not None)


def test_vectorized_sigma_band_matches_its_own_trajectory_rows() -> None:
    """`_run_vectorized_sigma_band_extension`'s band reduces exactly its own rows.

    The array-native mirror of `test_sigma_band_summary_matches_a_hand_
    computed_mean_and_sigma`, checked against a real
    `"generational-vector"` run rather than an injected series: the
    reported band must be the population mean/sigma of precisely the
    per-generation values the same extension recorded, so a helper that
    buffered one set of numbers and summarized another would fail here.
    """
    pytest.importorskip("numba")
    params = _sigma_band_vector_params()

    result = GenerationalBackend(VectorizedAdvancer()).run(
        params, InMemoryTrajectoryStore(), None, _clock
    )
    assert isinstance(result, RunResult)
    assert result.manifest.sigma_band is not None
    assert result.sigma_band_trajectory is not None
    assert len(result.sigma_band_trajectory) == 12

    for name, band in result.manifest.sigma_band.items():
        series: list[float] = []
        for row in result.sigma_band_trajectory:
            if name not in row:
                continue
            value = row[name]
            assert isinstance(value, float)
            series.append(value)
        expected_sigma = statistics.pstdev(series)
        assert band["mean"] == pytest.approx(statistics.fmean(series))
        assert band["sigma"] == pytest.approx(expected_sigma)
        assert band["lower"] == pytest.approx(band["mean"] - 2.0 * expected_sigma)
        assert band["upper"] == pytest.approx(band["mean"] + 2.0 * expected_sigma)


def test_vectorized_extension_keeps_minted_identities_through_extinction() -> None:
    """The extension never forgets an allele minted and since driven extinct.

    Decision 7's own named bug, guarded directly. A V-lane's minted
    bookkeeping lives inside `VectorizedState`, never in
    `lane.finite_alleles`, so continuing such a lane by rebuilding a
    state from `lane.state` alone (or by switching to the dict-based
    `step`) would treat only the currently-*present* alleles as the
    whole minted set — re-minting identities the run had permanently
    retired and undercounting `minted_count`.

    Asserted in three parts: that rebuilding really would lose
    information (otherwise this test would pass for the wrong reason, on
    a run where nothing had gone extinct yet); that the extension window
    genuinely spans an extinction *and* a later reappearance (so the
    scenario is actually exercised); and that the real extension's own
    bookkeeping only ever advances.
    """
    pytest.importorskip("numba")
    params = _sigma_band_vector_params()

    lane = _drive_vector_lane_to_stop(params)
    assert lane.vectorized_state is not None
    before = lane.vectorized_state.locus_states[0]
    minted_before = int(before.minted_mask.sum())

    # Part 1: the rejected approach demonstrably loses minted identities.
    rebuilt = build_vectorized_state(lane.state).locus_states[0]
    assert int(rebuilt.minted_mask.sum()) < minted_before
    assert rebuilt.minted_count < before.minted_count

    # Part 2: replay the same window on an identically-seeded second lane
    # to confirm an extinction and a later reappearance really occur in
    # it. A replay is needed because the extension itself persists no
    # per-generation state, only each watched statistic's own value.
    replay = _drive_vector_lane_to_stop(params)
    assert replay.vectorized_state is not None
    sizes = np.asarray(
        _population_sizes(replay.params.N, replay.state.deme_count), dtype=np.int64
    )
    presence = [_present_allele_ids(replay.vectorized_state.locus_states[0])]
    vectorized_state = replay.vectorized_state
    assert isinstance(replay.params.m, float)
    for _ in range(12):
        vectorized_state = step_vectorized(
            vectorized_state,
            replay.migration_weights,
            replay.params.mutation_rates,
            sizes,
            replay.rng,
            symmetric_rate=replay.params.m,
        )
        presence.append(_present_allele_ids(vectorized_state.locus_states[0]))
    went_extinct: set[int] = set()
    for earlier, later in itertools.pairwise(presence):
        went_extinct |= earlier - later
    reappeared = {
        allele
        for index, present in enumerate(presence)
        for allele in present
        if any(allele not in prior for prior in presence[:index])
    }
    assert went_extinct, "window exercised no extinction — scenario not covered"
    assert went_extinct & reappeared, "no allele reappeared after going extinct"

    # Part 3: the real extension's bookkeeping only ever advances.
    band, trajectory = engine._run_vectorized_sigma_band_extension(
        lane, multiplier=2.0, window=12
    )
    assert band and len(trajectory) == 12
    assert lane.vectorized_state is not None
    after = lane.vectorized_state.locus_states[0]
    assert int(after.minted_mask.sum()) >= minted_before
    assert after.minted_count >= before.minted_count
    # Every identity minted before the extension is still marked minted.
    assert bool(np.all(after.minted_mask[before.minted_mask]))


def test_sigma_band_extensions_never_interleave_with_batch_ticks() -> None:
    """No lane's own advancement is delayed by another lane's extension.

    Decision 8's rejected inline alternative, turned into a regression
    test. Running each extension the instant its lane was found in
    `newly_stopped` would step `sigma_band_window` further generations
    for that lane *before* the batch's next tick advanced any other
    still-active lane — stalling a live batch's visible progress. The
    deferred post-pass cannot: every extension must happen after the
    final tick.

    The configuration is deliberately staggered (one replicate runs far
    longer than the other three), so inline and deferred would genuinely
    differ here — with every lane converging on the same generation the
    two orderings would be indistinguishable and this test would prove
    nothing.
    """
    events: list[str] = []
    real_extension = engine._run_dict_based_sigma_band_extension

    def recording_extension(*args: object, **kwargs: object) -> object:
        events.append("extension")
        return real_extension(*args, **kwargs)  # type: ignore[arg-type]

    class RecordingAdvancer:
        """A real `SequentialAdvancer`, logging one event per tick."""

        def __init__(self) -> None:
            self._inner = SequentialAdvancer()

        def advance(
            self, active_lanes: Sequence[ReplicaLane], store: TrajectoryStore
        ) -> list[ReplicaLane]:
            events.append("tick")
            return self._inner.advance(active_lanes, store)

    params = SimulationParams.from_mapping(
        {
            **_tiny_config(),
            "n_replicates": 4,
            "convergence_tolerance": 0.02,
            "max_generations": 40,
            "sigma_band_multiplier": 2.0,
            "sigma_band_window": 5,
        }
    )

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            engine, "_run_dict_based_sigma_band_extension", recording_extension
        )
        results = run_batch(
            params, InMemoryTrajectoryStore(), None, _clock, RecordingAdvancer()
        )

    # The batch really is staggered: lanes stopped on different generations.
    assert len({result.manifest.generation for result in results}) > 1
    # Every lane got an extension...
    assert events.count("extension") == len(results) == 4
    # ...and not one of them ran before the batch's final tick.
    first_extension = events.index("extension")
    assert "tick" not in events[first_extension:]


def test_vectorized_sigma_band_caches_peak_in_the_post_pass_then_release() -> None:
    """Deferred caches peak during the post-pass and are all released by its end.

    Decision 9's accepted worst case, measured rather than merely
    asserted: with the band enabled, every eligible lane defers its
    `VectorizedState` release (reopening the growth `FIM-48` closed), so
    all of them are alive when the post-pass begins. The cost stays a
    *temporary* peak because the pass releases each lane's own cache the
    instant that lane's extension finishes — so the live count falls
    monotonically through the pass and reaches zero by its end, rather
    than persisting after the batch returns.
    """
    pytest.importorskip("numba")
    params = _sigma_band_vector_params(
        n_replicates=4, convergence_tolerance=0.02, max_generations=40
    )
    live_counts: list[int] = []
    observed: dict[str, int] = {}
    real_apply = engine._apply_sigma_band_extensions
    real_extension = engine._run_vectorized_sigma_band_extension

    def capturing_apply(lanes: Sequence[ReplicaLane], advancer: object) -> None:
        observed["before"] = _lanes_holding_caches(lanes)

        def recording_extension(lane: ReplicaLane, **kwargs: object) -> object:
            # Counted *before* this lane's own release, so the first
            # observation is the genuine peak.
            live_counts.append(_lanes_holding_caches(lanes))
            return real_extension(lane, **kwargs)  # type: ignore[arg-type]

        with pytest.MonkeyPatch.context() as inner:
            inner.setattr(
                engine, "_run_vectorized_sigma_band_extension", recording_extension
            )
            real_apply(lanes, advancer)  # type: ignore[arg-type]
        observed["after"] = _lanes_holding_caches(lanes)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(engine, "_apply_sigma_band_extensions", capturing_apply)
        results = GenerationalBackend(VectorizedAdvancer()).run(
            params, InMemoryTrajectoryStore(), None, _clock
        )
    assert isinstance(results, tuple)
    assert len(results) == 4

    # The peak is real: every eligible lane deferred its release, so all
    # four were alive at once — the cost decision 9 names explicitly.
    assert observed["before"] == 4
    assert live_counts == [4, 3, 2, 1]
    # ...and it is only a peak: nothing is still held afterward.
    assert observed["after"] == 0
    for result in results:
        assert result.manifest.sigma_band is not None


def test_a_batch_without_a_sigma_band_still_releases_caches_at_finalization() -> None:
    """Decision 9's own "opt-in" half: no band requested, `FIM-48` unchanged.

    The control for the test above. `_finalize_replica_lane` only skips
    its release for a sigma-band-eligible lane, so a batch that never
    asked for a band must still release every `VectorizedState` the
    instant its lane stops — exactly `FIM-48`'s own guarantee, not
    weakened by v2 having made a conditional out of it.
    """
    pytest.importorskip("numba")
    params = _sigma_band_vector_params(
        n_replicates=4,
        convergence_tolerance=0.02,
        max_generations=40,
        sigma_band_multiplier=None,
        sigma_band_window=None,
    )
    observed: dict[str, int] = {}
    real_apply = engine._apply_sigma_band_extensions

    def capturing_apply(lanes: Sequence[ReplicaLane], advancer: object) -> None:
        observed["before"] = _lanes_holding_caches(lanes)
        real_apply(lanes, advancer)  # type: ignore[arg-type]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(engine, "_apply_sigma_band_extensions", capturing_apply)
        results = GenerationalBackend(VectorizedAdvancer()).run(
            params, InMemoryTrajectoryStore(), None, _clock
        )
    assert isinstance(results, tuple)

    # Nothing was ever deferred: every cache was already released by
    # `_finalize_replica_lane`, before the post-pass was even reached.
    assert observed["before"] == 0
    for result in results:
        assert result.manifest.sigma_band is None
        assert result.sigma_band_trajectory is None


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
            "replicate_tolerance": 1000.0,
            "engine_backend": "generational",
        }
    )
    store = InMemoryTrajectoryStore()

    output = fim(
        params.N,
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
    ) -> ReplicaLane:
        nonlocal concurrently_active, peak_concurrently_active, build_call_count
        lane = real_build(params, replica_index, run_id, store, clock)
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

    output = fim(params.N, params.m, params.mu, params.d, params=params, clock=_clock)

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
        base_params.N,
        base_params.m,
        base_params.mu,
        base_params.d,
        params=base_params,
        clock=_clock,
    )
    windowed_params = replace(base_params, max_concurrent_replicates=2)
    windowed = fim(
        windowed_params.N,
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
    impossible). Changed once `replicate_tolerance` stopped defaulting
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
            "replicate_tolerance": 1000.0,
        }
    )
    assert params.replicate_minimum == 3

    output = fim(params.N, params.m, params.mu, params.d, params=params, clock=_clock)
    assert isinstance(output, tuple)
    assert len(output) == 3


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

    assert set(summary) == {
        "D",
        "G_ST",
        "E_ST",
        "K_ST",
        "H_S",
        "H_T",
        "H_ST",
        "Gs",
        "Gd",
    }
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

    Not built from `tiny_params`: its own tight, fast-converging
    defaults have every replicate stop at the identical generation
    (confirmed live -- the whole reason this test needs staggered
    stops), so this test picks its own `seed`/`convergence_tolerance`/
    `max_generations` specifically to produce real spread (`[3, 5, 6,
    12, 15]`, confirmed live for this exact configuration) instead.
    """
    params = SimulationParams(
        N=20,
        m=0.1,
        mu=0.01,
        d=2,
        seed=42,
        loci=(LocusSpec(1, 200),),
        convergence_window=4,
        convergence_tolerance=0.02,
        max_generations=30,
        n_replicates=5,
        replicate_tolerance=None,
    )
    output = fim(params.N, params.m, params.mu, params.d, params=params, clock=_clock)
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

    # Only the always-tracked four (`fim.engine._ALWAYS_TRACKED_
    # STATISTICS`), not all six `STATISTIC_NAMES` the completed
    # scalar/live-batch trajectory panels can show -- `E_ST`/`K_ST`
    # only ever get a *per-generation* history at all with
    # `track_expensive_statistics=True` (unset here), unlike a live
    # tick's own `report_for_state`, which always computes a full
    # report regardless of that setting.
    assert set(pooled) == {"D", "G_ST", "H_S", "H_T"}
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
        # the pre-fix behavior this test's own docstring describes.
        assert all(count == 5 for count in sample_counts), (
            f"{name}: sample_count dropped below the full replicate "
            f"count somewhere -- carry-forward is not working"
        )
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
        tiny_params.N, tiny_params.m, tiny_params.mu, tiny_params.d, params=tiny_params
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
    output = fim(params.N, params.m, params.mu, params.d, params=params)
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
    assert set(pooled) == {"D", "H_S", "H_T"}
    for name in ("D", "H_S", "H_T"):
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
            params.N,
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
        params.N,
        params.m,
        params.mu,
        params.d,
        params=params,
        store_factory=functools.partial(_jsonl_store_factory, tmp_path),
    )

    assert isinstance(output, RunResult)
    assert (tmp_path / f"{output.run_id}.jsonl").exists()


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


def _jsonl_store_factory(directory: Path, run_id: str) -> JSONLTrajectoryStore:
    """Module-level, `functools.partial`-bindable `store_factory` for FIM-50.

    A worker process must be able to pickle a reference to `store_
    factory` itself, ruling out a closure or lambda; a test binds
    `directory` to its own `tmp_path` via `functools.partial` before
    passing this through, giving every replicate a real, file-backed
    store on disk (unlike `_in_memory_store_factory` below, which
    ignores `run_id` entirely and cannot show whether a specific run's
    own artifacts survived).
    """
    return JSONLTrajectoryStore(directory / f"{run_id}.jsonl")


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
            "replicate_tolerance": 1000.0,
        }
    )

    output = fim(
        params.N,
        params.m,
        params.mu,
        params.d,
        params=params,
        max_workers=2,
        store_factory=functools.partial(_jsonl_store_factory, tmp_path),
    )

    assert isinstance(output, tuple)
    assert 3 <= len(output) <= 4
    returned_run_ids = {result.run_id for result in output}
    surviving_run_ids = {path.stem for path in tmp_path.glob("*.jsonl")}
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
        {**tiny_params.to_dict(), "n_replicates": 8, "replicate_tolerance": None}
    )
    output = fim(params.N, params.m, params.mu, params.d, params=params, clock=_clock)
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
        {**tiny_params.to_dict(), "n_replicates": 8, "replicate_tolerance": None}
    )
    output = fim(params.N, params.m, params.mu, params.d, params=params, clock=_clock)
    assert isinstance(output, tuple)

    summary = bootstrap_replicate_summary(output, rng=np.random.default_rng(2))

    for statistic, interval in summary.items():
        assert interval["low"] <= interval["mean"] <= interval["high"], statistic
        assert interval["sample_count"] == len(output)
        assert interval["confidence"] == 0.95


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
        {**tiny_params.to_dict(), "n_replicates": 6, "replicate_tolerance": None}
    )
    output = fim(params.N, params.m, params.mu, params.d, params=params, clock=_clock)
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
        {**tiny_params.to_dict(), "n_replicates": 4, "replicate_tolerance": None}
    )
    output = fim(params.N, params.m, params.mu, params.d, params=params, clock=_clock)
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

    output = fim(params.N, params.m, params.mu, params.d, params=params, clock=_clock)

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
        n_replicates=1,
        replicate_tolerance=None,
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
    # `D`/`G_ST`/`H_S`/`H_T` are always present now, regardless of what
    # is actually watched (`fim.engine._ALWAYS_TRACKED_STATISTICS`) — a
    # display-only superset of the single watched statistic (`D` here),
    # which never itself changes what `converged_on` reports.
    assert set(result.convergence_histories) == {"D", "G_ST", "H_S", "H_T"}
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
        n_replicates=1,
        replicate_tolerance=None,
    )

    first = _run(params)
    second = _run(params)

    assert list(first.store.read(first.run_id)) == list(
        second.store.read(second.run_id)
    )
    assert first.report == second.report
    assert first.report["converged_on"] == ["D", "G_ST"]
    # `H_S`/`H_T` ride along too, always (`_ALWAYS_TRACKED_STATISTICS`) —
    # `D`/`G_ST` here are both watched *and* always-tracked, so this run's
    # own `converged_on` is unaffected by the two extra names.
    assert set(first.convergence_histories) == {"D", "G_ST", "H_S", "H_T"}
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
            n_replicates=1,
            replicate_tolerance=None,
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
        n_replicates=1,
        replicate_tolerance=None,
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
        n_replicates=1,
        replicate_tolerance=None,
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
        n_replicates=1,
        replicate_tolerance=None,
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
    below feeds this same state through `build_vectorized_state`, which
    allocates a real `(deme_count, 4**length)` array.
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
            N=10,
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
    `VectorizedState`-specific plumbing (deriving `deme_count` from a
    locus's own dense array shape, not a `ModelState.deme_count`
    attribute that doesn't exist here) feeds it correctly, not
    re-litigate the aggregation math itself.
    """
    state = _two_locus_state_with_divergent_per_locus_estimates()
    params = SimulationParams(
        N=10,
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
    vectorized_state = build_vectorized_state(state)
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
        N=10,
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
    — proves `VectorizedState`'s own dense-array path skips the same
    work, not just the dict-based path.
    """
    state = _two_locus_state_with_divergent_per_locus_estimates()
    params = SimulationParams(
        N=10,
        m=0.1,
        mu=0.0,
        d=2,
        seed=7,
        loci=state.loci,
        convergence_statistic="D",
    )
    vectorized_state = build_vectorized_state(state)
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
    """`D`/`G_ST`/`H_S`/`H_T` are present regardless of what is watched.

    The display-only counterpart to the two "skips E_ST/K_ST" tests
    above: those four cost nothing extra to compute (`statistics_
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
        N=10, m=0.1, mu=0.0, d=2, seed=7, loci=state.loci, convergence_statistic="D"
    )

    values = _convergence_values(state, params)

    assert set(values) == {"D", "G_ST", "H_S", "H_T"}


def test_convergence_values_vectorized_always_includes_d_g_st_h_s_h_t() -> None:
    """The array-native path returns the identical always-tracked superset."""
    state = _two_locus_state_with_divergent_per_locus_estimates()
    params = SimulationParams(
        N=10, m=0.1, mu=0.0, d=2, seed=7, loci=state.loci, convergence_statistic="D"
    )
    vectorized_state = build_vectorized_state(state)

    values = _convergence_values_vectorized(vectorized_state, params)

    assert set(values) == {"D", "G_ST", "H_S", "H_T"}


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
        N=10,
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
    assert set(values) == {"D", "G_ST", "H_S", "H_T", "E_ST", "K_ST"}


def test_track_expensive_statistics_vectorized_computes_e_st_and_k_st(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The array-native path gets the identical opt-in fix."""
    state = _two_locus_state_with_divergent_per_locus_estimates()
    params = SimulationParams(
        N=10,
        m=0.1,
        mu=0.0,
        d=2,
        seed=7,
        loci=state.loci,
        convergence_statistic="D",
        track_expensive_statistics=True,
    )
    vectorized_state = build_vectorized_state(state)
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
    assert set(values) == {"D", "G_ST", "H_S", "H_T", "E_ST", "K_ST"}


def test_run_result_convergence_histories_include_always_tracked_statistics() -> None:
    """A real, full scalar run's own `convergence_histories` include the free four.

    End-to-end proof through `_run_one` itself (not just the per-
    generation helper functions above): a run watching only `D` still
    comes back with real `G_ST`/`H_S`/`H_T` history too, of the same
    length as the watched one — the exact GUI-visible symptom the bug
    report described (a trajectory panel/completed view that narrowed
    down to only the watched statistic once a run finished).
    """
    params = SimulationParams(
        N=15,
        m=0.1,
        mu=0.01,
        d=2,
        seed=99,
        loci=(LocusSpec(1, 50),),
        convergence_statistic="D",
        convergence_window=4,
        convergence_tolerance=0.02,
        max_generations=20,
        n_replicates=1,
        replicate_tolerance=None,
    )

    result = _run(params)

    assert set(result.convergence_histories) == {"D", "G_ST", "H_S", "H_T"}
    for name in ("G_ST", "H_S", "H_T"):
        assert len(result.convergence_histories[name]) == len(
            result.convergence_generations
        )


def test_run_result_convergence_histories_include_e_st_k_st_when_opted_in() -> None:
    """`track_expensive_statistics=True` extends a real run's own recorded history.

    Same run as above, only with the opt-in set — `E_ST`/`K_ST` now
    join the always-tracked four in `RunResult.convergence_histories`,
    each with a real per-generation history the same length as every
    other tracked statistic's own.
    """
    params = SimulationParams(
        N=15,
        m=0.1,
        mu=0.01,
        d=2,
        seed=99,
        loci=(LocusSpec(1, 50),),
        convergence_statistic="D",
        convergence_window=4,
        convergence_tolerance=0.02,
        max_generations=20,
        n_replicates=1,
        replicate_tolerance=None,
        track_expensive_statistics=True,
    )

    result = _run(params)

    assert set(result.convergence_histories) == {
        "D",
        "G_ST",
        "H_S",
        "H_T",
        "E_ST",
        "K_ST",
    }
    for name in ("G_ST", "H_S", "H_T", "E_ST", "K_ST"):
        assert len(result.convergence_histories[name]) == len(
            result.convergence_generations
        )


def test_sigma_band_stays_scoped_to_watched_statistics_only() -> None:
    """The within-run sigma band never picks up the new always-tracked extras.

    `_run_one`'s own sigma-band extension buffers `_convergence_values`'
    now-richer per-generation output — this confirms it still only ever
    keeps what `doc/configuration.md`'s own `sigma_band_multiplier` entry
    documents ("reports each watched statistic"), not every name
    `_convergence_values` now happens to also return.
    """
    params = SimulationParams(
        N=15,
        m=0.1,
        mu=0.01,
        d=2,
        seed=99,
        loci=(LocusSpec(1, 50),),
        convergence_statistic="D",
        convergence_window=4,
        convergence_tolerance=0.02,
        max_generations=20,
        n_replicates=1,
        replicate_tolerance=None,
        sigma_band_multiplier=2.0,
        sigma_band_window=5,
        track_expensive_statistics=True,
    )

    result = _run(params)

    if result.report["converged"]:
        assert result.manifest.sigma_band is not None
        assert set(result.manifest.sigma_band) == {"D"}


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
        n_replicates=1,
        replicate_tolerance=None,
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
            "n_replicates": 1,
            "replicate_tolerance": None,
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
            "n_replicates": 1,
            "replicate_tolerance": None,
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
        "n_replicates": 1,
        "replicate_tolerance": None,
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
            "n_replicates": 1,
            "replicate_tolerance": None,
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
        "n_replicates": 1,
        "replicate_tolerance": None,
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
        "n_replicates": 1,
        "replicate_tolerance": None,
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
            "n_replicates": 1,
            "replicate_tolerance": None,
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
    # The always-tracked-statistics fix (`fim.engine._ALWAYS_TRACKED_
    # STATISTICS`) reaches `"generational"`'s own per-lane monitor
    # (`_build_replica_lane`) through the identical `_convergence_values`
    # call `"lineal"`'s `_run_one` already makes — no separate wiring was
    # needed for the batch/generational-backend path, confirmed here
    # rather than merely assumed.
    assert set(lineal_result.convergence_histories) == {"D", "G_ST", "H_S", "H_T"}
    assert set(generational_result.convergence_histories) == {
        "D",
        "G_ST",
        "H_S",
        "H_T",
    }


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


def test_build_engine_backend_auto_requires_numba_when_resolved_to_vector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`"auto"` resolving to Backend V hits the identical numba guard.

    `_resolve_auto_engine_backend` never checks numba itself (its own
    docstring names only `d`/capacity/mutation-model/migrant-sampling as
    what it decides on) — the guard lives once, in `build_engine_backend`
    itself, downstream of resolution, so `"auto"` gets it for free rather
    than needing its own duplicate check.
    """
    monkeypatch.setattr(engine, "_numba_is_available", lambda: False)
    params = _finite_alleles_vector_params(d=40)
    with pytest.raises(ValueError, match="numba"):
        build_engine_backend("auto", params=params, auto_vector_min_d=35)


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
    """Explicit selection now gets the same capacity ceiling `"auto"` already had."""
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
        params.N,
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
        params.N,
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


def test_fim_dirichlet_run_leaves_equilibrium_manifest_fields_none(
    tiny_params: SimulationParams,
) -> None:
    """An ordinary Dirichlet-prior run never populates the equilibrium-only fields."""
    result = fim(
        tiny_params.N,
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
    """A `finite_alleles`/continuous-migration config `VectorizedAdvancer` accepts.

    `n_replicates=1`/`replicate_tolerance=None` explicitly, not
    `SimulationParams`'s own current defaults (`200`/`0.01`) — every
    caller of this helper except one explicit override
    (`test_generational_vector_backend_batch_is_independently_
    reproducible`) wants a single scalar run.
    """
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
        n_replicates=1,
        replicate_tolerance=None,
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


def test_finalize_replica_lane_releases_vectorized_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_finalize_replica_lane` clears a lane's own dense cache once it stops.

    Regression test for `FIM-48`: without this, a finished lane's own
    `VectorizedState` cache stayed referenced by `run_batch`'s own
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
        assert lane.migration_weights is None
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
    """A full multi-generation run matches `LinealBackend` bit-for-bit when `m=0`.

    The real, end-to-end proof this project's own operator-level exact-
    match tests (`test/model/test_vectorized.py`) never actually
    exercised: `fim.engine.VectorizedAdvancer` calls `build_vectorized_
    state` once, on a lane's own first tick, then steps that cached
    `VectorizedState` (`ReplicaLane.vectorized_state`) directly for
    every generation after — and a real correctness bug in the earlier,
    per-generation-rebuild version of that path — re-deriving finite-
    alleles minted bookkeeping from scratch every generation, silently
    forgetting any allele minted and then driven extinct within the
    same generation it was minted in — meant this never actually held,
    even though every individual operator had been proven exact in
    isolation. Fixed by carrying the bookkeeping forward
    (`build_vectorized_state`'s own `previous_locus_states` argument,
    still used for that one first-tick call); confirmed directly, not
    assumed, with `m=0` here specifically to
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


def test_vectorized_advancer_builds_a_lanes_state_only_once_each_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`VectorizedAdvancer` converts a lane's state once each way, not every tick.

    Regression test: `advance()` used to call `build_vectorized_state`
    (rebuilding `VectorizedState` from `ModelState`) and `vectorized_
    state_to_model_state` (the reverse) on *every* generation, for
    every lane — a real, measured cost, larger than the biology it sat
    next to at a large capacity (design doc `20260901-claude-sonnet-5-
    fim-engine-backend-factory-design.md` §11's own reopened "across-
    generation fusion" question). `ReplicaLane.vectorized_state` now
    caches the live array state across generations, so each direction
    should be paid exactly once per lane for a whole run: forward, on
    that lane's own first tick; backward, once it stops. Counts real
    calls by wrapping (not replacing) both functions, so this also
    exercises a real multi-generation, multi-replicate batch end to
    end, not a mocked-out one.

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

    def _counting_build_vectorized_state(*args: object, **kwargs: object) -> object:
        nonlocal build_call_count
        build_call_count += 1
        return build_vectorized_state(*args, **kwargs)  # type: ignore[arg-type]

    reconstruct_call_count = 0

    def _counting_vectorized_state_to_model_state(
        *args: object, **kwargs: object
    ) -> object:
        nonlocal reconstruct_call_count
        reconstruct_call_count += 1
        return vectorized_state_to_model_state(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        engine, "build_vectorized_state", _counting_build_vectorized_state
    )
    monkeypatch.setattr(
        engine,
        "vectorized_state_to_model_state",
        _counting_vectorized_state_to_model_state,
    )

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
        # Explicit, not `SimulationParams`'s own current default
        # (`0.01`): this test's own paired-mean comparison needs the
        # exact same fixed replicate count run by both backends,
        # deliberately not an adaptive stop that could legitimately fire
        # at a different replicate count for each (their per-replicate
        # values differ slightly under migration by design — that
        # divergence is exactly what this test measures).
        replicate_tolerance=None,
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


def test_generational_vector_matches_lineal_statistically_multi_locus() -> None:
    """Aggregate differentiation statistics agree with `LinealBackend` for 2+ loci.

    A genuinely different mechanism from `test_generational_vector_
    backend_matches_lineal_statistically`, above, not a duplicate of it:
    that test isolates *migration's* own floating-point reduction-order
    divergence by using one locus (where `step_vectorized`'s per-locus
    fusion cannot diverge from `operators.step`'s own stage ordering at
    all — there is only one locus to loop over either way). This test
    instead sets `m=0.0` (no floating-point migration divergence
    possible) and uses two loci, isolating the *other*, structural
    mechanism this project's own multi-model engine review, 2026-09-04,
    found (`FIM-09`/finding C-01/finding P1-1): `step_vectorized` fuses
    `migrate` → `mutate` → `drift` per locus, one whole locus at a time,
    while `operators.step` runs each stage across every locus first, in
    a deme-major order — the two draw from the shared RNG stream in a
    different sequence the instant more than one locus is tracked, with
    or without migration. `fim.model.vectorized`'s and `fim.engine.fim`'s
    own docstrings were previously unqualified by locus count on this
    exact claim; both now name this test as the multi-locus statistical
    parity proof superseding the old, narrower claim.

    Same normal-approximation-band methodology as the migration-active
    test above, applied to the same two watched statistics.
    """
    pytest.importorskip("numba")
    params = SimulationParams(
        N=40,
        m=0.0,
        mu=0.05,
        d=4,
        seed=13579,
        loci=(LocusSpec(1, 2), LocusSpec(2, 2)),
        mutation_model="finite_alleles",
        convergence_tolerance=0.0,
        convergence_window=21,
        max_generations=20,
        n_replicates=200,
        # Explicit, not `SimulationParams`'s own current default
        # (`0.01`): this test's own paired-mean comparison needs the
        # exact same fixed replicate count run by both backends,
        # deliberately not an adaptive stop that could legitimately fire
        # at a different replicate count for each.
        replicate_tolerance=None,
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

    Found by the Stage 4/Stage V3 benchmark sweep: a genuine `(d, d)`
    weight-matrix conversion was being redone every single generation,
    even though `params.m`/deme sizes never change mid-run. A genuine
    matrix `m` (`d=3`, symmetric-equivalent by construction, not a
    scalar) — `20260903-claude-sonnet-5-fim-vg-performance-campaign-
    design.md` §6.1 item 1's own `O(d)` fix took the scalar-rate case
    out of this cache entirely (see `test_vectorized_advancer_skips_
    migration_weights_cache_for_a_scalar_rate`, below); this test still
    covers the case that fix deliberately left the caching behavior
    unchanged for. Checked directly, not just inferred from timing: the
    exact same array object (`is`, not just equal) survives two
    consecutive `advance()` calls.
    """
    pytest.importorskip("numba")
    matrix = ((0.8, 0.1, 0.1), (0.1, 0.8, 0.1), (0.1, 0.1, 0.8))
    params = _finite_alleles_vector_params(
        m=matrix, max_generations=3, convergence_window=3
    )
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


def test_vectorized_advancer_skips_migration_weights_cache_for_a_scalar_rate() -> None:
    """A plain scalar `m` never populates `migration_weights` at all, ever.

    `20260903-claude-sonnet-5-fim-vg-performance-campaign-design.md`
    §6.1 item 1: `migrate_vectorized_symmetric` computes a scalar-rate
    migration blend directly, in `O(d*K)`, with no `(d, d)` matrix ever
    built — so there is nothing to cache for this, this project's own
    most common configuration, not merely a cache that happens to stay
    unused. Checked across several generations, not just the first
    tick, since a regression that rebuilds-and-discards a matrix every
    generation would leave this field `None` too, indistinguishable
    from the fix at a single tick.
    """
    pytest.importorskip("numba")
    params = _finite_alleles_vector_params(max_generations=3, convergence_window=3)
    store = InMemoryTrajectoryStore()
    lane = _build_replica_lane(params, 0, None, store, _clock)
    advancer = VectorizedAdvancer()

    for _ in range(3):
        advancer.advance([lane], store)
        assert lane.migration_weights is None
