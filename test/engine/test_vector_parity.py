"""Backend V's exactness contract, checked through the public `fim()` call.

For the same configuration and seed, Backend V (`generational-vector`)
must produce exactly what Backends L (`lineal`) and G (`generational`)
produce: every trajectory row (allele id and frequency bit, in order),
the final report, the final state, the convergence histories, the batch
summary, and the manifest except the field that records which backend
ran. These tests compare complete row streams and complete outputs, never
summaries alone, over the shared configuration matrix in
`test/vector_support.py`, and also through batches, adaptive batches, a
sigma-band extension, a convergence-stopped run, the expensive opt-in
statistics and a JSONL trajectory file (byte for byte).

Same-platform identity is the contract: the compiled kernels use Numba's
`lgamma`, `log`, `log1p` and `exp`, which are verified to match CPython's
only on the development platform (see `fim.model.vector_block`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from vector_support import FINITE_CASES, INFINITE_CASES, loci, make_params

pytest.importorskip("numba")

from fim import engine
from fim.engine import (
    RunResult,
    VectorizedAdvancer,
    _build_replica_lane,
    fim,
    replicate_summary,
)
from fim.model.params import SimulationParams
from fim.model.vector_block import VectorBlock, VectorMemoryCeilingError
from fim.persistence.jsonl_store import JSONLTrajectoryStore
from fim.persistence.store import InMemoryTrajectoryStore

RUN_ID = "run"


def _clock() -> datetime:
    """Return a fixed manifest timestamp so manifests compare exactly."""
    return datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def _run(
    params: SimulationParams, backend: str, store: InMemoryTrajectoryStore
) -> tuple[RunResult, ...]:
    """Run `params` on `backend`, writing rows into `store`; always a tuple."""
    output = fim(
        params.gene_copies,
        params.m,
        params.mu,
        params.d,
        params=params,
        store=store,
        run_id=RUN_ID,
        clock=_clock,
        engine_backend=backend,  # type: ignore[arg-type]
    )
    return output if isinstance(output, tuple) else (output,)


def _comparable_manifest(result: RunResult) -> dict[str, object]:
    """Return a manifest as a dict without the backend-provenance fields."""
    manifest = result.manifest.to_dict()
    manifest.pop("engine_backend")
    parameters = dict(manifest["parameters"])  # type: ignore[call-overload]
    parameters.pop("engine_backend")
    manifest["parameters"] = parameters
    return manifest


def _assert_identical(
    actual: tuple[RunResult, ...],
    actual_store: InMemoryTrajectoryStore,
    expected: tuple[RunResult, ...],
    expected_store: InMemoryTrajectoryStore,
) -> None:
    """Require identical rows, reports, final states, histories and manifests."""
    assert [result.run_id for result in actual] == [
        result.run_id for result in expected
    ]
    for got, want in zip(actual, expected, strict=True):
        # The complete, ordered row stream of the replicate.
        assert list(actual_store.read(got.run_id)) == list(
            expected_store.read(want.run_id)
        )
        assert got.report == want.report
        assert got.final_state == want.final_state
        assert got.convergence_generations == want.convergence_generations
        assert got.convergence_history == want.convergence_history
        assert got.convergence_histories == want.convergence_histories
        assert _comparable_manifest(got) == _comparable_manifest(want)
        assert got.manifest.sigma_band == want.manifest.sigma_band
        assert got.sigma_band_trajectory == want.sigma_band_trajectory
    if len(actual) >= 2:
        assert replicate_summary(actual) == replicate_summary(expected)


def _assert_vector_matches(
    params: SimulationParams, *, against: tuple[str, ...] = ("lineal",)
) -> int:
    """Run V and each reference backend on `params`; require identity.

    Returns:
        The number of generations the vector run recorded, so a caller
        can confirm the run was not trivially short.
    """
    vector_store = InMemoryTrajectoryStore()
    vector = _run(params, "generational-vector", vector_store)
    for backend in against:
        reference_store = InMemoryTrajectoryStore()
        reference = _run(params, backend, reference_store)
        _assert_identical(vector, vector_store, reference, reference_store)
    return len(vector[0].convergence_generations)


@pytest.mark.parametrize("name", list(INFINITE_CASES))
def test_infinite_alleles_vector_matches_lineal_through_fim(name: str) -> None:
    """Every infinite-alleles case: rows, report, final state, manifest."""
    assert _assert_vector_matches(INFINITE_CASES[name](40)) > 2


@pytest.mark.parametrize(
    "name",
    [
        "multi-locus with migration",
        "per-locus mutation rates",
        "migration matrix",
        "unequal deme sizes",
    ],
)
def test_infinite_alleles_vector_matches_generational_through_fim(name: str) -> None:
    """V also equals G (`generational`, threaded advancer), not just L."""
    _assert_vector_matches(INFINITE_CASES[name](40), against=("generational",))


def test_dear_nolan_low_shape_matches_lineal_for_thousands_of_generations() -> None:
    """The dear-nolan-low shape at three loci, 3,000 generations, every row."""
    assert _assert_vector_matches(make_params(3000, loci=loci(3))) == 3001


def test_replicate_batch_matches_lineal() -> None:
    """Three replicates: every replicate's rows, reports and the batch summary."""
    params = make_params(
        30, loci=loci(2), mu=0.01, m=0.05, n_replicates=3, replicate_tolerance=None
    )
    assert _assert_vector_matches(params, against=("lineal", "generational")) > 2


def test_adaptive_replicate_batch_keeps_the_same_replicates() -> None:
    """An adaptive batch (`replicate_tolerance`) keeps the same replicate prefix."""
    params = make_params(
        60,
        loci=loci(2),
        mu=0.01,
        m=0.05,
        gene_copies=40,
        d=3,
        n_replicates=8,
        replicate_minimum=3,
        replicate_tolerance=0.5,
        convergence_window=10,
        convergence_tolerance=0.2,
    )
    vector_store = InMemoryTrajectoryStore()
    vector = _run(params, "generational-vector", vector_store)
    lineal_store = InMemoryTrajectoryStore()
    lineal = _run(params, "lineal", lineal_store)
    assert 3 <= len(lineal) < params.n_replicates, "the batch should stop early"
    _assert_identical(vector, vector_store, lineal, lineal_store)


def test_convergence_stopped_run_matches_lineal() -> None:
    """A run that converges (not one cut off by the cap) stops identically."""
    params = make_params(
        4000,
        loci=loci(2),
        gene_copies=30,
        d=3,
        mu=0.05,
        m=0.2,
        convergence_window=20,
        convergence_tolerance=0.1,
    )
    vector_store = InMemoryTrajectoryStore()
    vector = _run(params, "generational-vector", vector_store)
    lineal_store = InMemoryTrajectoryStore()
    lineal = _run(params, "lineal", lineal_store)
    assert lineal[0].manifest.converged
    assert lineal[0].manifest.generation < 4000
    _assert_identical(vector, vector_store, lineal, lineal_store)


def test_sigma_band_extension_matches_lineal() -> None:
    """The extension after convergence (rows aside) gives the same band."""
    params = make_params(
        4000,
        loci=loci(2),
        gene_copies=30,
        d=3,
        mu=0.05,
        m=0.2,
        convergence_window=20,
        convergence_tolerance=0.1,
        sigma_band_multiplier=2.0,
        sigma_band_window=15,
    )
    vector_store = InMemoryTrajectoryStore()
    vector = _run(params, "generational-vector", vector_store)
    lineal_store = InMemoryTrajectoryStore()
    lineal = _run(params, "lineal", lineal_store)
    assert lineal[0].manifest.sigma_band is not None
    assert lineal[0].sigma_band_trajectory is not None
    assert len(lineal[0].sigma_band_trajectory) == 15
    _assert_identical(vector, vector_store, lineal, lineal_store)


@pytest.mark.parametrize("aggregation", ["ratio_of_means", "mean_of_ratios"])
def test_expensive_statistics_and_aggregation_choices_match_lineal(
    aggregation: str,
) -> None:
    """Opt-in statistics (computed in Python) and both locus aggregations."""
    params = make_params(
        30,
        loci=loci(3),
        mu=0.02,
        m=0.05,
        track_expensive_statistics=True,
        convergence_statistic=("D", "E_ST"),
        locus_aggregation=aggregation,
        deme_weighting="size",
    )
    _assert_vector_matches(params)


def test_window_of_concurrent_replicates_matches_lineal() -> None:
    """`max_concurrent_replicates` changes scheduling, never results."""
    params = make_params(
        30,
        loci=loci(2),
        mu=0.01,
        m=0.05,
        n_replicates=4,
        max_concurrent_replicates=2,
    )
    _assert_vector_matches(params)


def test_jsonl_trajectory_file_is_byte_identical(tmp_path: Path) -> None:
    """The JSONL file V writes equals Backend L's, byte for byte."""
    params = make_params(25, loci=loci(2), mu=0.02, m=0.05)
    paths = {}
    for backend in ("lineal", "generational-vector"):
        path = tmp_path / f"{backend}.jsonl"
        paths[backend] = path
        output = fim(
            params.gene_copies,
            params.m,
            params.mu,
            params.d,
            params=params,
            store=JSONLTrajectoryStore(path),
            run_id=RUN_ID,
            clock=_clock,
            engine_backend=backend,
        )
        assert isinstance(output, RunResult)
    assert paths["lineal"].read_bytes() == paths["generational-vector"].read_bytes()
    assert paths["lineal"].stat().st_size > 0


def test_fim_records_the_resolved_backend_in_the_manifest() -> None:
    """The manifest names the backend that actually ran."""
    params = make_params(5, loci=loci(1), mu=0.02, m=0.05)
    results = {
        backend: _run(params, backend, InMemoryTrajectoryStore())[0]
        for backend in ("lineal", "generational-vector")
    }
    assert results["lineal"].manifest.engine_backend == "lineal"
    assert results["generational-vector"].manifest.engine_backend == (
        "generational-vector"
    )


def test_a_stopped_lane_hands_its_identity_counter_back_to_the_registry() -> None:
    """After the lane stops, `lane.registry` has not fallen behind the block.

    The block mints identities inside its arrays; `lane.state` and the
    registry are brought up to date only at the stop, the one moment
    something outside the array loop reads them.
    """
    params = make_params(40, loci=loci(2), mu=0.02, m=0.05)
    store = InMemoryTrajectoryStore()
    lane = _build_replica_lane(params, 0, None, store, _clock)
    start = lane.registry.next_value
    advancer = VectorizedAdvancer()
    while not lane.monitor.should_stop():
        advancer.advance([lane], store)
    block = lane.vectorized_state
    assert isinstance(block, VectorBlock)
    assert block.next_id > start, "the run should have minted identities"
    assert lane.registry.next_value == block.next_id
    assert lane.state == block.to_model_state()
    assert lane.state.generation == params.max_generations


def test_the_advancer_enforces_its_memory_ceiling_with_an_actionable_message() -> None:
    """A table that cannot fit fails at the first tick, naming the remedies."""
    params = make_params(10, loci=loci(2), mu=0.02, m=0.05)
    store = InMemoryTrajectoryStore()
    lane = _build_replica_lane(params, 0, None, store, _clock)
    with pytest.raises(VectorMemoryCeilingError, match="FIM_VECTOR_MEMORY_CEILING"):
        VectorizedAdvancer(memory_ceiling_bytes=64).advance([lane], store)


def test_equilibrium_split_start_matches_lineal() -> None:
    """A run founded by an equilibrium split continues identically on V.

    The ancestral phase runs on the dictionary-based operators (and
    mints identities there); V then starts from that founding state and
    from the registry counter the phase left, and must neither collide
    with an ancestral identity nor draw differently.
    """
    params = make_params(
        30,
        loci=loci(2),
        gene_copies=40,
        d=3,
        mu=0.02,
        m=0.05,
        equilibrium_convergence_window=10,
        equilibrium_convergence_tolerance=0.2,
        equilibrium_max_generations=60,
    )
    vector_store = InMemoryTrajectoryStore()
    vector = _run(params, "generational-vector", vector_store)
    lineal_store = InMemoryTrajectoryStore()
    lineal = _run(params, "lineal", lineal_store)
    assert lineal[0].manifest.initial_condition_mode == "equilibrium_split"
    _assert_identical(vector, vector_store, lineal, lineal_store)


@pytest.mark.parametrize("name", list(FINITE_CASES))
def test_finite_alleles_vector_matches_lineal_through_fim(name: str) -> None:
    """Every finite-alleles case: rows, report, final state, manifest.

    Finite alleles used to match Backend L only statistically (and only
    bitwise for one locus without migration); the kernel now reproduces
    the stage order, the migration arithmetic and the K-allele target
    draws, so it is exact here too.
    """
    assert _assert_vector_matches(FINITE_CASES[name](40)) > 2


@pytest.mark.parametrize(
    "name", ["finite, 64 states", "finite, migration matrix", "finite, unequal sizes"]
)
def test_finite_alleles_vector_matches_generational_through_fim(name: str) -> None:
    """V also equals G for finite alleles, not just L."""
    _assert_vector_matches(FINITE_CASES[name](40), against=("generational",))


def test_finite_alleles_replicate_batch_and_adaptive_stop_match_lineal() -> None:
    """Finite alleles through a 3-replicate batch and an adaptive batch."""
    batch = make_params(
        30,
        loci=loci(2, 3),
        mu=0.03,
        m=0.05,
        n_replicates=3,
        mutation_model="finite_alleles",
    )
    assert _assert_vector_matches(batch, against=("lineal", "generational")) > 2
    adaptive = make_params(
        60,
        loci=loci(2, 3),
        gene_copies=40,
        d=3,
        mu=0.03,
        m=0.05,
        n_replicates=8,
        replicate_minimum=3,
        replicate_tolerance=0.5,
        convergence_window=10,
        convergence_tolerance=0.2,
        mutation_model="finite_alleles",
    )
    vector_store = InMemoryTrajectoryStore()
    vector = _run(adaptive, "generational-vector", vector_store)
    lineal_store = InMemoryTrajectoryStore()
    lineal = _run(adaptive, "lineal", lineal_store)
    assert 3 <= len(lineal) < adaptive.n_replicates, "the batch should stop early"
    _assert_identical(vector, vector_store, lineal, lineal_store)


def test_finite_alleles_sigma_band_extension_matches_lineal() -> None:
    """The extension continues the minted bookkeeping exactly as L does.

    A 16-state locus makes alleles go extinct and reappear within the
    window, which is what a forgotten-minted-identity bug would mishandle.
    """
    params = make_params(
        4000,
        loci=loci(1, 2),
        gene_copies=40,
        d=3,
        mu=0.1,
        m=0.2,
        convergence_window=20,
        convergence_tolerance=0.15,
        sigma_band_multiplier=2.0,
        sigma_band_window=15,
        mutation_model="finite_alleles",
    )
    vector_store = InMemoryTrajectoryStore()
    vector = _run(params, "generational-vector", vector_store)
    lineal_store = InMemoryTrajectoryStore()
    lineal = _run(params, "lineal", lineal_store)
    assert lineal[0].manifest.converged
    assert lineal[0].manifest.sigma_band is not None
    _assert_identical(vector, vector_store, lineal, lineal_store)


def test_finite_alleles_jsonl_trajectory_file_is_byte_identical(
    tmp_path: Path,
) -> None:
    """The finite-alleles JSONL file V writes equals Backend L's, byte for byte."""
    params = make_params(
        25, loci=loci(2, 3), mu=0.03, m=0.05, mutation_model="finite_alleles"
    )
    paths = {}
    for backend in ("lineal", "generational-vector"):
        path = tmp_path / f"{backend}.jsonl"
        paths[backend] = path
        output = fim(
            params.gene_copies,
            params.m,
            params.mu,
            params.d,
            params=params,
            store=JSONLTrajectoryStore(path),
            run_id=RUN_ID,
            clock=_clock,
            engine_backend=backend,
        )
        assert isinstance(output, RunResult)
    assert paths["lineal"].read_bytes() == paths["generational-vector"].read_bytes()


@pytest.mark.parametrize("name", ["multi-locus with migration", "migration matrix"])
def test_auto_resolves_to_vector_and_matches_lineal(name: str) -> None:
    """`engine_backend: auto` picks V for infinite alleles, and nothing changes.

    The manifest records the resolved backend; every other part of the
    output equals Backend L's, which is why `auto` may choose V whenever it
    is eligible.
    """
    params = INFINITE_CASES[name](30)
    auto_store = InMemoryTrajectoryStore()
    auto = _run(params, "auto", auto_store)
    lineal_store = InMemoryTrajectoryStore()
    lineal = _run(params, "lineal", lineal_store)
    assert auto[0].manifest.engine_backend == "generational-vector"
    _assert_identical(auto, auto_store, lineal, lineal_store)


def test_auto_without_numba_falls_back_to_generational_with_identical_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With numba missing `auto` runs G, and the output is the same as V's."""
    params = INFINITE_CASES["multi-locus with migration"](30)
    vector_store = InMemoryTrajectoryStore()
    vector = _run(params, "auto", vector_store)
    assert vector[0].manifest.engine_backend == "generational-vector"
    monkeypatch.setattr(engine, "_numba_is_available", lambda: False)
    fallback_store = InMemoryTrajectoryStore()
    fallback = _run(params, "auto", fallback_store)
    assert fallback[0].manifest.engine_backend == "generational"
    _assert_identical(vector, vector_store, fallback, fallback_store)
