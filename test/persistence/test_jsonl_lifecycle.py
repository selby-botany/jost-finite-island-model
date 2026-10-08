"""Lifecycle tests for the kept-open `JSONLTrajectoryStore` append handle.

`JSONLTrajectoryStore.write_generation` keeps one append handle open
between generations (re-opening a just-written file costs milliseconds)
and flushes once per generation. These tests pin the contract that makes
that safe: a reader sees every flushed generation while the handle is
open, `close` is idempotent and never ends a store's life, a pickled copy
re-opens by itself, `discard` and an external delete stay consistent, a
directory renamed into place by `fim.paths.atomic_directory` loses
nothing, and no real entry point leaves a file open.

Everything is deterministic: no test waits on a timing budget, and the
only waits (threads) are bounded by `conftest.join_or_fail`.
"""

from __future__ import annotations

import json
import pickle
import sys
from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import assert_none_open

from fim import paths
from fim.engine import SequentialAdvancer, fim, run_batch
from fim.model.allele import AlleleId
from fim.model.locus import LocusSpec
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.persistence.jsonl_store import (
    EQUILIBRIUM_TRAJECTORY_FILENAME,
    JSONLTrajectoryStore,
)
from fim.persistence.store import (
    InMemoryTrajectoryStore,
    ReplicateFanoutStore,
    close_run_store,
    close_store,
)


def _state(generation: int) -> ModelState:
    """Return one sparse state: three nonzero rows per generation."""
    return ModelState(
        loci=(LocusSpec(1, 200),),
        frequencies=(
            ({AlleleId(0): 0.25, AlleleId(1): 0.75},),
            ({AlleleId(0): 1.0},),
        ),
        generation=generation,
    )


def _write(store: JSONLTrajectoryStore, run_id: str, generation: int) -> None:
    """Write generation `generation` of `run_id` (three rows) to `store`."""
    store.write_generation(run_id, generation, _state(generation).to_rows(run_id))


def _generations(store: JSONLTrajectoryStore, run_id: str) -> list[int]:
    """Return `run_id`'s stored generations, each once, in stored order."""
    return list(dict.fromkeys(row["generation"] for row in store.read(run_id)))


def test_a_reader_sees_every_flushed_generation_while_the_handle_is_open(
    tmp_path: Path,
) -> None:
    """Reading mid-run needs no close: each generation is flushed on return."""
    store = JSONLTrajectoryStore(tmp_path / "trajectory.jsonl")
    assert not store.is_open()

    for generation in range(3):
        _write(store, "run-a", generation)

        assert store.is_open()
        assert _generations(store, "run-a") == list(range(generation + 1))
        # A reader that never touched the store sees the same bytes.
        assert len((tmp_path / "trajectory.jsonl").read_text().splitlines()) == (
            3 * (generation + 1)
        )
    store.close()


def test_the_handle_is_opened_once_and_reused_across_generations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ten generations cost one `open`, the point of keeping the handle."""
    opened: list[Path] = []
    original_open = Path.open

    def counting_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        opened.append(self)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)
    store = JSONLTrajectoryStore(tmp_path / "trajectory.jsonl")

    for generation in range(10):
        _write(store, "run-a", generation)

    assert opened == [tmp_path / "trajectory.jsonl"]
    store.close()


def test_close_is_idempotent_and_the_next_write_appends(tmp_path: Path) -> None:
    """A closed store is not finished: it re-opens in append mode, losing nothing."""
    store = JSONLTrajectoryStore(tmp_path / "trajectory.jsonl")
    _write(store, "run-a", 0)

    store.close()
    store.close()

    assert not store.is_open()
    # Read after close: nothing was lost and nothing needs the handle.
    assert _generations(store, "run-a") == [0]
    _write(store, "run-a", 1)
    assert store.is_open()
    store.close()
    assert _generations(store, "run-a") == [0, 1]


def test_close_before_any_write_is_a_no_op(tmp_path: Path) -> None:
    """Closing a store that never wrote creates no file and raises nothing."""
    store = JSONLTrajectoryStore(tmp_path / "trajectory.jsonl")

    store.close()

    assert not (tmp_path / "trajectory.jsonl").exists()


def test_context_manager_closes_on_exit_and_on_error(tmp_path: Path) -> None:
    """Leaving a `with` block releases the handle, even through an exception."""
    with JSONLTrajectoryStore(tmp_path / "a.jsonl") as store:
        _write(store, "run-a", 0)
        assert store.is_open()
    assert not store.is_open()

    failing = JSONLTrajectoryStore(tmp_path / "b.jsonl")
    with pytest.raises(RuntimeError, match="boom"), failing:
        _write(failing, "run-a", 0)
        raise RuntimeError("boom")
    assert not failing.is_open()
    assert _generations(failing, "run-a") == [0]


def test_close_also_closes_the_ancestral_phase_companion(tmp_path: Path) -> None:
    """`close` reaches the sibling `equilibrium_trajectory.jsonl` handle too."""
    store = JSONLTrajectoryStore(tmp_path / "trajectory.jsonl")
    companion = store.equilibrium_store("run-a")
    assert isinstance(companion, JSONLTrajectoryStore)
    _write(store, "run-a", 0)
    _write(companion, "run-a", 0)
    assert store.is_open()
    assert companion.is_open()

    store.close()

    assert not store.is_open()
    assert not companion.is_open()
    assert companion.path == tmp_path / EQUILIBRIUM_TRAJECTORY_FILENAME


def test_pickle_drops_the_handle_and_the_copy_appends_on_its_own(
    tmp_path: Path,
) -> None:
    """A pickled store crosses a process boundary without its open handle.

    `RunResult.store` is pickled back from a worker process while a store
    may still be open. The original keeps its handle; the copy re-opens
    the file lazily, in append mode, on its first write.
    """
    store = JSONLTrajectoryStore(tmp_path / "trajectory.jsonl")
    companion = store.equilibrium_store("run-a")
    _write(store, "run-a", 0)
    assert isinstance(companion, JSONLTrajectoryStore)
    _write(companion, "run-a", 0)

    clone = pickle.loads(pickle.dumps(store))

    assert store.is_open()
    assert not clone.is_open()
    clone_companion = clone.equilibrium_store("run-a")
    assert isinstance(clone_companion, JSONLTrajectoryStore)
    assert not clone_companion.is_open()
    # Readable at once, before any write.
    assert _generations(clone, "run-a") == [0]
    _write(clone, "run-a", 1)
    _write(store, "run-a", 2)
    clone.close()
    store.close()
    assert _generations(store, "run-a") == [0, 1, 2]


def test_discard_then_write_re_creates_the_file_consistently(tmp_path: Path) -> None:
    """`discard` closes the handle; the next write starts a fresh, correct file."""
    store = JSONLTrajectoryStore(tmp_path / "trajectory.jsonl")
    _write(store, "run-a", 0)
    _write(store, "run-b", 0)
    assert store.is_open()

    store.discard("run-a")

    assert not store.is_open()
    assert _generations(store, "run-a") == []
    assert _generations(store, "run-b") == [0]
    _write(store, "run-a", 5)
    store.close()
    assert _generations(store, "run-a") == [5]
    assert _generations(store, "run-b") == [0]

    # Discarding the last rows removes the file; a later write re-creates it.
    store.discard("run-a")
    store.discard("run-b")
    assert not store.path.exists()
    _write(store, "run-c", 0)
    store.close()
    assert _generations(store, "run-c") == [0]


def test_discard_of_an_unknown_run_still_releases_the_handle(tmp_path: Path) -> None:
    """A no-op discard leaves the rows alone; the store stays usable."""
    store = JSONLTrajectoryStore(tmp_path / "trajectory.jsonl")
    _write(store, "run-a", 0)

    store.discard("run-never-written")

    assert _generations(store, "run-a") == [0]
    _write(store, "run-a", 1)
    store.close()
    assert _generations(store, "run-a") == [0, 1]


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows refuses to delete a file that is held open",
)
def test_a_file_deleted_under_an_open_handle_is_re_created_by_the_next_write(
    tmp_path: Path,
) -> None:
    """Writing on to an unlinked file would lose data; the file is re-created.

    The open-per-generation writer this replaced re-created a deleted
    file; the kept handle notices its link count dropped to zero and does
    the same.
    """
    path = tmp_path / "trajectory.jsonl"
    store = JSONLTrajectoryStore(path)
    _write(store, "run-a", 0)
    path.unlink()

    _write(store, "run-a", 1)
    store.close()

    assert _generations(store, "run-a") == [1]


def test_a_failed_write_drops_the_handle_so_no_partial_bytes_follow(
    tmp_path: Path,
) -> None:
    """An I/O error mid-write must not leave buffered bytes for a retry to append."""
    store = JSONLTrajectoryStore(tmp_path / "trajectory.jsonl")
    _write(store, "run-a", 0)
    real_handle = store._handle
    assert real_handle is not None
    kept = real_handle

    class FailingHandle:
        """Stand-in whose `write` fails after reporting itself open."""

        closed = False

        def fileno(self) -> int:
            """Delegate so the link-count check passes."""
            return kept.fileno()

        def write(self, _text: str) -> int:
            """Fail like a full disk."""
            raise OSError("disk full")

        def close(self) -> None:
            """Close the real handle behind this stand-in."""
            kept.close()

    store._handle = FailingHandle()  # type: ignore[assignment]

    with pytest.raises(OSError, match="disk full"):
        _write(store, "run-a", 1)

    assert not store.is_open()
    _write(store, "run-a", 2)
    store.close()
    assert _generations(store, "run-a") == [0, 2]


def test_an_unencodable_row_leaves_the_file_untouched(tmp_path: Path) -> None:
    """Every line is encoded before the first byte is written.

    `validate=False` skips `normalize_row`, so a non-finite frequency is
    caught only by the encoder; the generation must not be half-written.
    """
    store = JSONLTrajectoryStore(tmp_path / "trajectory.jsonl")
    _write(store, "run-a", 0)
    rows = _state(1).to_rows("run-a")
    rows[-1]["frequency"] = float("nan")

    with pytest.raises(ValueError, match="Out of range float"):
        store.write_generation("run-a", 1, rows, validate=False)

    store.close()
    assert _generations(store, "run-a") == [0]


def test_an_empty_generation_is_still_rejected_before_any_file_is_opened(
    tmp_path: Path,
) -> None:
    """Behavior kept from the open-per-generation writer."""
    store = JSONLTrajectoryStore(tmp_path / "trajectory.jsonl")

    with pytest.raises(ValueError, match="at least one row"):
        store.write_generation("run-a", 0, [])

    assert not store.is_open()
    assert not store.path.exists()


def test_atomic_directory_publishes_a_closed_store_intact(tmp_path: Path) -> None:
    """Closing before the rename (the owners' rule) publishes every byte."""
    target = tmp_path / "published"
    with paths.atomic_directory(target) as working_directory:
        with JSONLTrajectoryStore(working_directory / "trajectory.jsonl") as store:
            for generation in range(3):
                _write(store, "run-a", generation)
        assert not store.is_open()

    published = JSONLTrajectoryStore(target / "trajectory.jsonl")
    assert _generations(published, "run-a") == [0, 1, 2]


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows refuses to rename a directory holding an open file",
)
def test_a_rename_under_an_open_handle_loses_nothing_on_posix(tmp_path: Path) -> None:
    """The handle follows the file through a rename (POSIX); no data is lost.

    Documents why the owners still close first: this only holds where the
    platform allows the rename at all, and it leaves `store.path` stale.
    """
    source = tmp_path / "work"
    source.mkdir()
    store = JSONLTrajectoryStore(source / "trajectory.jsonl")
    _write(store, "run-a", 0)

    source.rename(tmp_path / "published")
    _write(store, "run-a", 1)
    store.close()

    reader = JSONLTrajectoryStore(tmp_path / "published" / "trajectory.jsonl")
    assert _generations(reader, "run-a") == [0, 1]


def test_fanout_close_run_closes_only_that_child(tmp_path: Path) -> None:
    """`close_run` releases one replicate's file; `close` releases the rest."""
    fanout = ReplicateFanoutStore(
        lambda run_id: JSONLTrajectoryStore(tmp_path / f"{run_id}.jsonl")
    )
    for run_id in ("run-1", "run-2"):
        fanout.write_generation(run_id, 0, _state(0).to_rows(run_id))
    first = fanout._stores["run-1"]
    second = fanout._stores["run-2"]
    assert isinstance(first, JSONLTrajectoryStore)
    assert isinstance(second, JSONLTrajectoryStore)

    fanout.close_run("run-1")
    assert not first.is_open()
    assert second.is_open()
    fanout.close_run("run-never-seen")  # a no-op, not an error

    fanout.close()
    assert not second.is_open()
    # Still usable: the next write re-opens.
    fanout.write_generation("run-1", 1, _state(1).to_rows("run-1"))
    assert first.is_open()
    fanout.close()
    assert list(dict.fromkeys(r["generation"] for r in fanout.read("run-1"))) == [0, 1]


def test_close_helpers_ignore_stores_without_a_close_method() -> None:
    """`close_store`/`close_run_store` accept any `TrajectoryStore`."""

    class Bare:
        """A store implementing only the three required methods."""

        def write_generation(
            self,
            run_id: str,
            generation: int,
            rows: Iterable[Mapping[str, Any]],
            *,
            validate: bool = True,
        ) -> None:
            """Accept and ignore a generation."""

        def read(self, _run_id: str) -> Any:
            """Yield nothing."""
            return iter(())

        def discard(self, run_id: str) -> None:
            """Discard nothing."""

    close_store(Bare())
    close_run_store(Bare(), "run-a")
    close_store(InMemoryTrajectoryStore())
    close_run_store(InMemoryTrajectoryStore(), "run-a")


def _params(**updates: Any) -> SimulationParams:
    """Return a tiny, fast batch configuration."""
    return replace(
        SimulationParams(
            gene_copies=20,
            m=0.1,
            mu=0.01,
            d=2,
            seed=20260814,
            loci=(LocusSpec(1, 200),),
            convergence_window=4,
            convergence_tolerance=1.0,
            max_generations=10,
            n_replicates=1,
            replicate_tolerance=None,
        ),
        **updates,
    )


def test_engine_closes_the_store_it_was_given_and_the_result_still_reads(
    tmp_path: Path, tracked_jsonl_stores: list[JSONLTrajectoryStore]
) -> None:
    """`fim(store=...)` closes the store when the run ends, even on `fim` alone."""
    store = JSONLTrajectoryStore(tmp_path / "trajectory.jsonl")
    params = _params()

    result = fim(
        params.gene_copies,
        params.m,
        params.mu,
        params.d,
        params=params,
        store=store,
    )

    assert not isinstance(result, tuple)
    assert_none_open(tracked_jsonl_stores)
    assert len(list(result.store.read(result.run_id))) > 0


def test_engine_closes_the_store_when_the_run_raises(tmp_path: Path) -> None:
    """A run that fails partway still releases its file (a cancelled GUI run)."""

    class FailingAfterTwoGenerations(JSONLTrajectoryStore):
        """Real store that raises on its third generation."""

        def write_generation(
            self,
            run_id: str,
            generation: int,
            rows: Iterable[Mapping[str, Any]],
            *,
            validate: bool = True,
        ) -> None:
            """Write, then fail once generation 2 is reached."""
            if generation >= 2:
                raise RuntimeError("cancelled")
            super().write_generation(run_id, generation, rows, validate=validate)

    store = FailingAfterTwoGenerations(tmp_path / "trajectory.jsonl")
    params = _params(max_generations=20)

    with pytest.raises(RuntimeError, match="cancelled"):
        fim(
            params.gene_copies,
            params.m,
            params.mu,
            params.d,
            params=params,
            store=store,
        )

    assert not store.is_open()
    assert _generations(store, store_run_id(store)) == [0, 1]


def _run_ids(store: JSONLTrajectoryStore) -> set[str]:
    """Return every run id found in `store`'s file."""
    return {
        json.loads(line)["run_id"]
        for line in store.path.read_text().splitlines()
        if line
    }


def store_run_id(store: JSONLTrajectoryStore) -> str:
    """Return the single run id found in `store`'s file."""
    (run_id,) = _run_ids(store)
    return run_id


def _fixed_clock() -> datetime:
    """Return a constant time: manifests need a clock, these tests do not."""
    return datetime(2026, 1, 1, tzinfo=UTC)


def test_generational_batch_holds_open_only_the_running_lanes(
    tmp_path: Path,
) -> None:
    """With a window of one, a finished replicate's file is closed before the next.

    Without per-lane closing a batch of N replicates would hold N files
    open for its whole run, which exhausts the descriptor limit on a large
    batch.
    """
    params = _params(
        n_replicates=4, max_concurrent_replicates=1, engine_backend="generational"
    )
    built: list[JSONLTrajectoryStore] = []
    open_when_built: list[int] = []

    def factory(run_id: str) -> JSONLTrajectoryStore:
        """Build a replicate's store, noting how many earlier ones are open."""
        open_when_built.append(sum(store.is_open() for store in built))
        store = JSONLTrajectoryStore(tmp_path / f"{run_id}.jsonl")
        built.append(store)
        return store

    fanout = ReplicateFanoutStore(factory)
    results = run_batch(params, fanout, "batch", _fixed_clock, SequentialAdvancer())

    assert len(results) == 4
    assert len(built) == 4
    assert open_when_built == [0, 0, 0, 0]
    fanout.close()
    assert not any(store.is_open() for store in built)
