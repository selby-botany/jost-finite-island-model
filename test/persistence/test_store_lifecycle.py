"""Lifecycle tests for the trajectory store contract, on the binary log.

These pin what the engine and its owners rely on whichever store they are
given: a directory renamed into place by `fim.paths.atomic_directory` loses
nothing once its store is closed, a fan-out closes one replicate's log
without touching the others, the close helpers accept any store, and no real
entry point leaves a writer open, even when a run fails partway.

Everything is deterministic: no test waits on a timing budget.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import assert_none_open

from fim import paths
from fim.config.expert import ExpertSettings
from fim.engine import SequentialAdvancer, fim, run_batch
from fim.model.allele import AlleleId
from fim.model.locus import LocusSpec
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.persistence.binary_store import BinaryLogStore, log_options
from fim.persistence.frame import TrajectoryFrame
from fim.persistence.store import (
    InMemoryTrajectoryStore,
    ReplicateFanoutStore,
    close_run_store,
    close_store,
)
from fim.persistence.tlog_reader import LogReader


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


def _write(store: BinaryLogStore, run_id: str, generation: int) -> None:
    """Write generation `generation` of `run_id` (three rows) to `store`."""
    store.write_generation(run_id, generation, _state(generation).to_rows(run_id))


def _generations(store: BinaryLogStore, run_id: str) -> list[int]:
    """Return `run_id`'s stored generations, each once, in stored order."""
    return list(dict.fromkeys(row["generation"] for row in store.read(run_id)))


def test_atomic_directory_publishes_a_closed_store_intact(tmp_path: Path) -> None:
    """Closing before the rename (the owners' rule) publishes every byte."""
    target = tmp_path / "published"
    with paths.atomic_directory(target) as working_directory:
        with BinaryLogStore(working_directory / "trajectory.tlog") as store:
            for generation in range(3):
                _write(store, "run-a", generation)
        assert not store.is_open()

    published = BinaryLogStore(target / "trajectory.tlog")
    assert _generations(published, "run-a") == [0, 1, 2]


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows refuses to rename a directory holding an open file",
)
def test_a_rename_under_an_open_log_loses_nothing_on_posix(tmp_path: Path) -> None:
    """The open descriptor follows the file through a rename (POSIX).

    Documents why the owners still close first: this only holds where the
    platform allows the rename at all, and it leaves `store.path` stale.
    """
    source = tmp_path / "work"
    source.mkdir()
    store = BinaryLogStore(source / "trajectory.tlog")
    _write(store, "run-a", 0)
    store.flush()

    source.rename(tmp_path / "published")
    _write(store, "run-a", 1)
    store.close()

    reader = BinaryLogStore(tmp_path / "published" / "trajectory.tlog")
    assert _generations(reader, "run-a") == [0, 1]


def test_fanout_close_run_closes_only_that_child(tmp_path: Path) -> None:
    """`close_run` releases one replicate's log; `close` releases the rest."""
    fanout = ReplicateFanoutStore(
        lambda run_id: BinaryLogStore(tmp_path / f"{run_id}.tlog")
    )
    for run_id in ("run-1", "run-2"):
        fanout.write_generation(run_id, 0, _state(0).to_rows(run_id))
    first = fanout._stores["run-1"]
    second = fanout._stores["run-2"]
    assert isinstance(first, BinaryLogStore)
    assert isinstance(second, BinaryLogStore)

    fanout.close_run("run-1")
    assert not first.is_open()
    assert second.is_open()
    fanout.close_run("run-never-seen")  # a no-op, not an error

    fanout.close()
    assert not second.is_open()
    assert _generations(first, "run-1") == [0]
    assert _generations(second, "run-2") == [0]


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
            precision=1.0,
            max_generations=10,
            n_replicates=1,
            stop_batch_early=False,
        ),
        **updates,
    )


def test_engine_closes_the_store_it_was_given_and_the_result_still_reads(
    tmp_path: Path, tracked_log_stores: list[BinaryLogStore]
) -> None:
    """`fim(store=...)` closes the store when the run ends, even on `fim` alone."""
    store = BinaryLogStore(tmp_path / "trajectory.tlog")
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
    assert_none_open(tracked_log_stores)
    assert len(list(result.store.read(result.run_id))) > 0


@pytest.mark.parametrize("backend", ["lineal", "generational", "generational-vector"])
def test_engine_closes_the_store_when_the_run_raises(
    tmp_path: Path, backend: str
) -> None:
    """A run that fails partway still releases its log (a cancelled GUI run).

    Every generation before the failure stays committed.
    """

    class FailingAfterTwoGenerations(BinaryLogStore):
        """Real store that raises on its third generation."""

        def write_frame(self, run_id: str, frame: TrajectoryFrame) -> None:
            """Write, then fail once generation 2 is reached."""
            if frame.generation >= 2:
                raise RuntimeError("cancelled")
            super().write_frame(run_id, frame)

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

    store = FailingAfterTwoGenerations(tmp_path / "trajectory.tlog")
    params = _params(max_generations=20)

    with pytest.raises(RuntimeError, match="cancelled"):
        fim(
            params.gene_copies,
            params.m,
            params.mu,
            params.d,
            params=params,
            store=store,
            engine_backend=backend,  # type: ignore[arg-type]
        )

    assert not store.is_open()
    with LogReader(store.path) as reader:
        assert reader.generation_numbers() == [0, 1]


def _fixed_clock() -> datetime:
    """Return a constant time: manifests need a clock, these tests do not."""
    return datetime(2026, 1, 1, tzinfo=UTC)


def test_generational_batch_holds_open_only_the_running_lanes(
    tmp_path: Path,
) -> None:
    """With a window of one, a finished replicate's log is closed before the next.

    Without per-lane closing a batch of N replicates would hold N writers
    (each with a thread and a descriptor) open for its whole run.
    """
    params = _params(
        n_replicates=4, max_concurrent_replicates=1, engine_backend="generational"
    )
    built: list[BinaryLogStore] = []
    open_when_built: list[int] = []

    def factory(run_id: str) -> BinaryLogStore:
        """Build a replicate's store, noting how many earlier ones are open."""
        open_when_built.append(sum(store.is_open() for store in built))
        store = BinaryLogStore(tmp_path / f"{run_id}.tlog")
        built.append(store)
        return store

    fanout = ReplicateFanoutStore(factory)
    results = run_batch(params, fanout, "batch", _fixed_clock, SequentialAdvancer())

    assert len(results) == 4
    assert len(built) == 4
    assert open_when_built == [0, 0, 0, 0]
    fanout.close()
    assert not any(store.is_open() for store in built)


def test_log_options_carry_the_expert_log_settings_to_the_store() -> None:
    """`log_options` maps the Expert Settings onto `BinaryLogStore`'s options."""
    expert = ExpertSettings(
        log_key_every=64, log_sync_seconds=0.5, log_block_generations=32
    )

    assert log_options(expert) == {
        "key_every": 64,
        "sync_seconds": 0.5,
        "block_generations": 32,
    }
