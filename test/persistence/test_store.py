"""Tests for incremental trajectory and manifest persistence."""

import threading
from collections.abc import Iterator
from pathlib import Path

from conftest import COMPLETION_BACKSTOP_SECONDS, join_or_fail

from fim.model.allele import AlleleId
from fim.model.locus import LocusSpec
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.persistence.binary_store import (
    EQUILIBRIUM_LOG_FILENAME,
    BinaryLogStore,
)
from fim.persistence.manifest import RunManifest, read_manifest, write_manifest
from fim.persistence.store import (
    EquilibriumStoreProvider,
    InMemoryTrajectoryStore,
    ReplicateFanoutStore,
    TrajectoryRow,
    equilibrium_store_for,
)


def _state(generation: int) -> ModelState:
    """Return one sparse state for persistence tests."""
    return ModelState(
        loci=(LocusSpec(1, 200),),
        frequencies=(
            ({AlleleId(0): 0.25, AlleleId(1): 0.75},),
            ({AlleleId(0): 1.0},),
        ),
        generation=generation,
    )


def test_in_memory_store_round_trips_rows() -> None:
    """The protocol contract preserves every public-schema field."""
    store = InMemoryTrajectoryStore()
    rows = _state(0).to_rows("run-a")

    store.write_generation("run-a", 0, rows)

    assert list(store.read("run-a")) == rows


def test_in_memory_store_discard_removes_only_the_named_run() -> None:
    """`discard` drops one run's rows and leaves every other run's alone.

    Phase 5, item 3 of `dev/doc/apps/selby/jost-finite-island-model/
    20260904-claude-sonnet-5-fim-engine-review-remediations.md`
    (`FIM-49`/`FIM-50`) — the capability an orphaned or overshoot
    replicate's own already-written rows are cleaned up through.
    """
    store = InMemoryTrajectoryStore()
    store.write_generation("run-a", 0, _state(0).to_rows("run-a"))
    store.write_generation("run-b", 0, _state(0).to_rows("run-b"))

    store.discard("run-a")

    assert list(store.read("run-a")) == []
    assert len(list(store.read("run-b"))) == 3


def test_in_memory_store_discard_is_a_no_op_for_an_unknown_run() -> None:
    """Discarding a run that was never written raises nothing and changes nothing."""
    store = InMemoryTrajectoryStore()
    store.write_generation("run-a", 0, _state(0).to_rows("run-a"))

    store.discard("run-never-written")

    assert len(list(store.read("run-a"))) == 3


def _write_concurrently(store: InMemoryTrajectoryStore, generation_count: int) -> None:
    """Write `generation_count` distinct generations to `store`, all at once.

    A `threading.Barrier` holds every thread at the starting line until
    all of them have reached `write_generation`, maximizing genuine
    concurrent overlap rather than leaving it to scheduling luck —
    `fim.engine.ThreadedAdvancer`'s own real usage pattern (several
    threads writing different generations of different replicates to one
    shared store at once), stress-tested directly here rather than only
    exercised incidentally through a full engine run.
    """
    # Bounded like every completion wait (`COMPLETION_BACKSTOP_SECONDS`): a
    # writer that never reaches the barrier breaks it instead of hanging.
    barrier = threading.Barrier(generation_count, timeout=COMPLETION_BACKSTOP_SECONDS)

    def _write(generation: int) -> None:
        barrier.wait()
        store.write_generation("run-a", generation, _state(generation).to_rows("run-a"))

    threads = [
        threading.Thread(target=_write, args=(generation,))
        for generation in range(generation_count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        join_or_fail(thread, "concurrent trajectory writer")


def test_in_memory_store_write_generation_is_thread_safe() -> None:
    """Concurrent `write_generation` calls never corrupt or lose rows.

    `InMemoryTrajectoryStore._lock` is what this test is actually
    proving exists and works — without it, `list.extend` calls from
    several threads racing on `_rows` risk a lost update, not merely a
    theoretical concern once a free-threaded (no-GIL) CPython build is
    in the picture.
    """
    store = InMemoryTrajectoryStore()
    generation_count = 20

    _write_concurrently(store, generation_count)

    rows = list(store.read("run-a"))
    assert len(rows) == generation_count * 3  # 3 nonzero-frequency rows/generation
    assert {row["generation"] for row in rows} == set(range(generation_count))


def test_replicate_fanout_store_routes_each_run_id_to_its_own_store() -> None:
    """Two run_ids' own rows land in two separate, independent stores.

    The whole point of `ReplicateFanoutStore`
    (`20260914-claude-sonnet-5-non-lineal-batch-execution-design.md`,
    `selby/restricted`, §5.1): a `generational`/`generational-vector`
    batch's own `run_batch` writes every replicate through this one
    object, but each replicate's own rows must end up in that
    replicate's own real store, not interleaved into one shared store
    the way a bare `InMemoryTrajectoryStore` would.
    """
    built: dict[str, InMemoryTrajectoryStore] = {}

    def factory(run_id: str) -> InMemoryTrajectoryStore:
        store = InMemoryTrajectoryStore()
        built[run_id] = store
        return store

    fanout = ReplicateFanoutStore(factory)
    fanout.write_generation("run-a", 0, _state(0).to_rows("run-a"))
    fanout.write_generation("run-b", 0, _state(0).to_rows("run-b"))

    assert list(fanout.read("run-a")) == list(built["run-a"].read("run-a"))
    assert list(fanout.read("run-b")) == list(built["run-b"].read("run-b"))
    assert list(built["run-a"].read("run-b")) == []
    assert list(built["run-b"].read("run-a")) == []


def test_replicate_fanout_store_builds_each_child_store_only_once() -> None:
    """`store_factory` is called exactly once per distinct run_id.

    Several generations of the same replicate must not each rebuild a
    fresh, empty child store — that would silently drop every
    generation but the last one written.
    """
    call_count = 0

    def factory(run_id: str) -> InMemoryTrajectoryStore:
        nonlocal call_count
        call_count += 1
        return InMemoryTrajectoryStore()

    fanout = ReplicateFanoutStore(factory)
    for generation in range(3):
        fanout.write_generation(
            "run-a", generation, _state(generation).to_rows("run-a")
        )
    fanout.write_generation("run-b", 0, _state(0).to_rows("run-b"))

    assert call_count == 2
    assert {row["generation"] for row in fanout.read("run-a")} == {0, 1, 2}


def test_replicate_fanout_store_discard_delegates_to_the_correct_child() -> None:
    """Discarding one run_id never touches another run_id's own child store."""
    fanout = ReplicateFanoutStore(lambda _run_id: InMemoryTrajectoryStore())
    fanout.write_generation("run-a", 0, _state(0).to_rows("run-a"))
    fanout.write_generation("run-b", 0, _state(0).to_rows("run-b"))

    fanout.discard("run-a")

    assert list(fanout.read("run-a")) == []
    assert len(list(fanout.read("run-b"))) == 3  # 3 nonzero-frequency rows


def test_replicate_fanout_store_discard_is_a_no_op_for_an_unseen_run() -> None:
    """Discarding a run_id this store never wrote builds no child store at all.

    Matches `TrajectoryStore.discard`'s own documented "no rows, no
    error" contract — an adaptive stop's own abandoned lane
    (`fim.engine.run_batch`'s own docstring) may never have written a
    single row before its own discard call arrives.
    """
    built = 0

    def factory(run_id: str) -> InMemoryTrajectoryStore:
        nonlocal built
        built += 1
        return InMemoryTrajectoryStore()

    fanout = ReplicateFanoutStore(factory)

    fanout.discard("run-never-written")

    assert built == 0


def test_replicate_fanout_store_is_thread_safe_across_concurrent_run_ids() -> None:
    """Concurrent first writes to distinct run_ids never race on child creation.

    The worst case for `ReplicateFanoutStore._store_for`'s own lazy
    get-or-create: several threads each writing a *different*
    replicate's own generation zero at once, the way
    `fim.engine.ThreadedAdvancer` fans a batch's own lanes out across
    threads. Without `_lock`, two threads racing on the same run_id
    could each build and register their own child store, silently
    losing whichever write lost the race.
    """
    run_count = 20
    built: dict[str, InMemoryTrajectoryStore] = {}
    build_lock = threading.Lock()

    def factory(run_id: str) -> InMemoryTrajectoryStore:
        store = InMemoryTrajectoryStore()
        with build_lock:
            built[run_id] = store
        return store

    fanout = ReplicateFanoutStore(factory)
    barrier = threading.Barrier(run_count, timeout=COMPLETION_BACKSTOP_SECONDS)

    def _write(index: int) -> None:
        run_id = f"run-{index}"
        barrier.wait()
        fanout.write_generation(run_id, 0, _state(0).to_rows(run_id))

    threads = [
        threading.Thread(target=_write, args=(index,)) for index in range(run_count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        join_or_fail(thread, "fan-out store writer")

    assert len(built) == run_count
    for index in range(run_count):
        run_id = f"run-{index}"
        assert len(list(fanout.read(run_id))) == 3  # 3 nonzero-frequency rows


def test_in_memory_store_equilibrium_store_is_a_separate_store() -> None:
    """In memory, the companion is a second store, shared across runs."""
    store = InMemoryTrajectoryStore()

    companion = store.equilibrium_store("run-a")
    companion.write_generation("run-a", 0, _state(0).to_rows("run-a"))

    assert store.equilibrium_store("run-b") is companion
    assert list(store.read("run-a")) == []
    assert len(list(companion.read("run-a"))) == 3


def test_replicate_fanout_store_equilibrium_store_follows_each_run(
    tmp_path: Path,
) -> None:
    """Each replicate's ancestral rows land beside that replicate's own file."""
    fanout = ReplicateFanoutStore(
        lambda run_id: BinaryLogStore(tmp_path / run_id / "trajectory.tlog")
    )

    companion = fanout.equilibrium_store("run-b")

    assert isinstance(companion, BinaryLogStore)
    assert companion.path == tmp_path / "run-b" / EQUILIBRIUM_LOG_FILENAME


def test_equilibrium_store_for_falls_back_to_memory_for_other_stores() -> None:
    """A store without a companion of its own still gets a readable one."""

    class _BareStore:
        """A `TrajectoryStore` with no `equilibrium_store` method."""

        def write_generation(self, *args: object, **kwargs: object) -> None:
            """Accept and drop one generation."""

        def read(self, run_id: str) -> Iterator[TrajectoryRow]:
            """Yield nothing."""
            del run_id
            return iter(())

        def discard(self, run_id: str) -> None:
            """Discard nothing."""
            del run_id

    bare = _BareStore()
    assert not isinstance(bare, EquilibriumStoreProvider)
    assert isinstance(equilibrium_store_for(bare, "run-a"), InMemoryTrajectoryStore)


def test_manifest_round_trip_reconstructs_parameters(tmp_path: Path) -> None:
    """A saved manifest contains a lossless replay configuration."""
    params = SimulationParams(
        gene_copies=20,
        m=0.1,
        mu=0.001,
        d=2,
        seed=7,
        loci=(LocusSpec(1, 200),),
    )
    manifest = RunManifest(
        schema_version=1,
        run_id="run-a",
        parameters=params.to_dict(),
        started_at="2026-08-14T20:00:00Z",
        ended_at="2026-08-14T20:00:01Z",
        converged=True,
        convergence_statistic="D",
        stop_reason="statistic converged",
        generation=4,
        generation_count=5,
        software_version="1.0.0",
    )
    path = tmp_path / "manifest.json"

    write_manifest(path, manifest)
    restored = read_manifest(path)

    assert restored == manifest
    assert restored.params() == params


def test_manifest_round_trip_reconstructs_several_convergence_statistics(
    tmp_path: Path,
) -> None:
    """A manifest watching several statistics is a lossless replay too."""
    params = SimulationParams(
        gene_copies=20,
        m=0.1,
        mu=0.001,
        d=2,
        seed=7,
        loci=(LocusSpec(1, 200),),
        convergence_statistic=("D", "G_ST"),
        convergence_combinator="any",
    )
    manifest = RunManifest(
        schema_version=1,
        run_id="run-a",
        parameters=params.to_dict(),
        started_at="2026-08-14T20:00:00Z",
        ended_at="2026-08-14T20:00:01Z",
        converged=True,
        convergence_statistic=("D", "G_ST"),
        stop_reason="statistic converged",
        generation=4,
        generation_count=5,
        software_version="1.0.0",
    )
    path = tmp_path / "manifest.json"

    write_manifest(path, manifest)
    restored = read_manifest(path)

    assert restored == manifest
    assert restored.convergence_statistic == ("D", "G_ST")
    assert restored.params() == params
