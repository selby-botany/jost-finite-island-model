"""Tests for continuing a planned Run object from another's checkpoint."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml
from conftest import FAST_EXPERT_SETTINGS

from fim import run_adoption
from fim.engine import RunResult
from fim.examples import classes
from fim.model.params import SimulationParams
from fim.persistence.artifacts import write_run_artifacts
from fim.persistence.binary_store import BinaryLogStore
from fim.persistence.run_checkpoint import CheckpointError, checkpoints_directory
from fim.persistence.run_lifecycle import (
    IllegalTransitionError,
    RunEvent,
    RunState,
)
from fim.persistence.run_objects import (
    RunObject,
    create_run_object,
    read_run_object,
)
from fim.persistence.run_ownership import request_cancel, start_run_object
from fim.persistence.tlog import iter_frames
from fim.run_adoption import (
    AdoptionError,
    adopt_checkpoint,
    find_continuation_sources,
)
from fim.run_service import CheckpointCadence, Session, execute_run_object

_CLASSES_FIXTURE = (
    Path(__file__).resolve().parent / "persistence" / "fixtures" / "classes.yaml"
)
AT = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
SOURCE_ID = "obj-" + "a" * 32
TARGET_ID = "obj-" + "b" * 32
WHOLE_ID = "obj-" + "c" * 32
OTHER_ID = "obj-" + "d" * 32

_SCALAR: dict[str, Any] = {
    "N": 20,
    "ploidy": "haploid",
    "d": 2,
    "m": 0.1,
    "mu": 0.05,
    "seed": 20261023,
    "loci": [{"locus_id": 1, "length": 200}],
    "precision": 0.0,
    "max_generations": 30,
    "n_replicates": 1,
    "stop_batch_early": False,
    "engine_backend": "lineal",
}

_BATCH: dict[str, Any] = {
    "N": 20,
    "ploidy": "haploid",
    "d": 2,
    "m": 0.1,
    "mu": 0.01,
    "seed": 20261024,
    "loci": [{"locus_id": 1, "length": 200}],
    "precision": 1.0,
    "convergence_burn_in": 1,
    "expert": dict(FAST_EXPERT_SETTINGS),
    "max_generations": 30,
    "n_replicates": 3,
    "stop_batch_early": False,
    "engine_backend": "generational",
}

_CADENCE = CheckpointCadence(generations=10, seconds=1e9, control_generations=5)


@pytest.fixture(autouse=True)
def _fixture_class_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check every `class` against the test fixture, never the shipped file."""
    monkeypatch.setattr(classes, "default_classes_path", lambda: _CLASSES_FIXTURE)


class _Writer:
    """An artifact writer that remembers what it was asked to write."""

    def __init__(self) -> None:
        """Start with nothing written."""
        self.results: list[RunResult] = []

    def __call__(self, result: RunResult, directory: Path) -> None:
        """Remember `result` and write the standard artifacts."""
        self.results.append(result)
        write_run_artifacts(result, directory)


def _clock() -> datetime:
    """Return the fixed test time."""
    return AT


def _create(
    results: Path, object_id: str, config: dict[str, Any], **changes: Any
) -> RunObject:
    """Save a Run object with a fixed identity."""
    text = yaml.safe_dump({**config, **changes}, sort_keys=False)
    return create_run_object(
        text, results=results, clock=_clock, id_factory=lambda: object_id
    )


def _store(path: Path, _params: SimulationParams) -> BinaryLogStore:
    """Return a log whose block boundaries never depend on time."""
    return BinaryLogStore(
        path,
        background=False,
        block_seconds=None,
        block_generations=4,
        key_every=4,
    )


def _execute(
    results: Path,
    object_id: str,
    writer: _Writer,
    observer: Callable[[Session], None] | None = None,
) -> RunObject:
    """Execute one object with deterministic stores and identities."""
    counter = itertools.count(1)
    return execute_run_object(
        object_id,
        write_artifacts=writer,
        results=results,
        clock=_clock,
        cadence=_CADENCE,
        monotonic=lambda: 0.0,
        store_factory=_store,
        attempt_factory=lambda: f"att-{next(counter):032x}",
        observer=observer,
    )


def _cancel_at(
    results: Path, object_id: str, generation: int
) -> Callable[[Session], None]:
    """Return an observer that cancels `object_id` once, at `generation`."""
    done: list[bool] = []

    def observe(session: Session) -> None:
        if session.generation >= generation and not done:
            done.append(True)
            revision = read_run_object(object_id, results=results).record.revision
            request_cancel(object_id, expected_revision=revision, results=results)

    return observe


def _cancelled_source(results: Path, config: dict[str, Any]) -> RunObject:
    """Run a source to generation 15 and cancel it; its checkpoint is at 10."""
    _create(results, SOURCE_ID, config)
    cancelled = _execute(
        results, SOURCE_ID, _Writer(), _cancel_at(results, SOURCE_ID, 15)
    )
    assert cancelled.record.state is RunState.CANCELLED
    checkpoint = cancelled.record.latest_checkpoint
    assert checkpoint is not None
    assert checkpoint.committed_generation == 10
    return cancelled


def _adopt(results: Path, source_id: str = SOURCE_ID) -> RunObject:
    """Adopt `source_id`'s checkpoint into the target at its current revision."""
    revision = read_run_object(TARGET_ID, results=results).record.revision
    return adopt_checkpoint(
        TARGET_ID, source_id, expected_revision=revision, results=results, clock=_clock
    )


def _assert_same_logs(left: Path, right: Path) -> None:
    """Assert two objects hold frame-for-frame identical trajectory logs."""
    left_logs = sorted(path.relative_to(left) for path in left.rglob("*.tlog"))
    right_logs = sorted(path.relative_to(right) for path in right.rglob("*.tlog"))
    assert left_logs == right_logs
    assert left_logs
    for name in left_logs:
        left_frames = list(iter_frames(left / name))
        right_frames = list(iter_frames(right / name))
        assert [frame.generation for frame in left_frames] == [
            frame.generation for frame in right_frames
        ]
        for one, other in zip(left_frames, right_frames, strict=True):
            np.testing.assert_array_equal(one.counts, other.counts)
            np.testing.assert_array_equal(one.allele_ids, other.allele_ids)


def _assert_same_results(left: list[RunResult], right: list[RunResult]) -> None:
    """Assert two executions produced the same scientific results."""
    assert [result.run_id for result in left] == [result.run_id for result in right]
    for one, other in zip(left, right, strict=True):
        assert one.report == other.report
        assert one.final_state == other.final_state
        assert one.convergence_histories == other.convergence_histories


@pytest.mark.parametrize("config", [_SCALAR, _BATCH], ids=["scalar", "batch"])
def test_a_continued_copy_finishes_as_the_uninterrupted_run(
    tmp_path: Path, config: dict[str, Any]
) -> None:
    """Continuing from a cancelled run's checkpoint changes no result."""
    whole_writer = _Writer()
    _create(tmp_path / "whole", WHOLE_ID, config)
    whole = _execute(tmp_path / "whole", WHOLE_ID, whole_writer)
    _cancelled_source(tmp_path, config)
    _create(tmp_path, TARGET_ID, config)

    adopted = _adopt(tmp_path)

    record = adopted.record
    assert record.state is RunState.PAUSED
    assert record.committed_generation == 10
    assert record.transition_history[-1].event is RunEvent.ADOPT_CHECKPOINT
    assert not list(adopted.directory.glob(".adopting-*"))
    writer = _Writer()
    finished = _execute(tmp_path, TARGET_ID, writer)
    assert finished.record.state is RunState.COMPLETED
    _assert_same_results(writer.results, whole_writer.results)
    _assert_same_logs(finished.directory, whole.directory)
    source = read_run_object(SOURCE_ID, results=tmp_path)
    assert source.record.state is RunState.CANCELLED
    assert source.record.committed_generation == 15


def test_adoption_copies_without_clonefile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Filesystems without copy-on-write clones get an ordinary copy."""
    monkeypatch.setattr(run_adoption, "_clonefile_function", lambda: None)
    source = _cancelled_source(tmp_path, _SCALAR)
    _create(tmp_path, TARGET_ID, _SCALAR)

    adopted = _adopt(tmp_path)

    assert source.record.latest_checkpoint is not None
    checkpoint_id = source.record.latest_checkpoint.checkpoint_id
    for name in ("checkpoint.json", "arrays.npz"):
        copied = checkpoints_directory(adopted.directory) / checkpoint_id / name
        original = checkpoints_directory(source.directory) / checkpoint_id / name
        assert copied.read_bytes() == original.read_bytes()
    assert (adopted.directory / "trajectory.tlog").read_bytes() == (
        source.directory / "trajectory.tlog"
    ).read_bytes()


def test_sources_are_matching_stopped_runs_furthest_first(tmp_path: Path) -> None:
    """Only same-configuration runs with progress are offered."""
    _cancelled_source(tmp_path, _SCALAR)
    _create(tmp_path, OTHER_ID, _SCALAR, seed=1)
    _execute(tmp_path, OTHER_ID, _Writer(), _cancel_at(tmp_path, OTHER_ID, 15))
    _create(tmp_path, WHOLE_ID, _SCALAR)
    _create(tmp_path, TARGET_ID, _SCALAR)

    (source,) = find_continuation_sources(TARGET_ID, results=tmp_path)

    assert source.object_id == SOURCE_ID
    assert source.state is RunState.CANCELLED
    assert source.committed_generation == 10
    _adopt(tmp_path)
    assert find_continuation_sources(TARGET_ID, results=tmp_path) == ()


def test_a_different_configuration_is_refused(tmp_path: Path) -> None:
    """A checkpoint continues only the configuration that wrote it."""
    _cancelled_source(tmp_path, _SCALAR)
    _create(tmp_path, TARGET_ID, _SCALAR, seed=1)

    with pytest.raises(AdoptionError, match="configuration differs"):
        _adopt(tmp_path)

    assert read_run_object(TARGET_ID, results=tmp_path).record.state is (
        RunState.PLANNED
    )


def test_an_executing_source_is_refused(tmp_path: Path) -> None:
    """A source that is running could rewrite its files mid-copy."""
    _create(tmp_path, SOURCE_ID, _SCALAR)
    _create(tmp_path, TARGET_ID, _SCALAR)
    opened = start_run_object(
        SOURCE_ID, expected_revision=1, results=tmp_path, clock=_clock
    )
    assert opened.lease is not None
    with opened.lease, pytest.raises(AdoptionError, match="running"):
        _adopt(tmp_path)


def test_a_source_without_progress_is_refused(tmp_path: Path) -> None:
    """A planned source has nothing to continue from."""
    _create(tmp_path, SOURCE_ID, _SCALAR)
    _create(tmp_path, TARGET_ID, _SCALAR)

    with pytest.raises(AdoptionError, match="no progress"):
        _adopt(tmp_path)


def test_a_corrupt_checkpoint_is_refused_and_leaves_the_target_planned(
    tmp_path: Path,
) -> None:
    """A copy that does not verify is discarded before anything is published."""
    source = _cancelled_source(tmp_path, _SCALAR)
    reference = source.record.latest_checkpoint
    assert reference is not None
    arrays = (
        checkpoints_directory(source.directory) / reference.checkpoint_id / "arrays.npz"
    )
    arrays.write_bytes(arrays.read_bytes() + b"tampered")
    _create(tmp_path, TARGET_ID, _SCALAR)

    with pytest.raises(CheckpointError, match="arrays do not match"):
        _adopt(tmp_path)

    target = read_run_object(TARGET_ID, results=tmp_path)
    assert target.record.state is RunState.PLANNED
    assert sorted(path.name for path in target.directory.iterdir()) == [
        "config.yaml",
        "run.json",
    ]


def test_only_a_planned_target_adopts(tmp_path: Path) -> None:
    """An object that already has progress keeps it."""
    _cancelled_source(tmp_path, _SCALAR)
    _create(tmp_path, TARGET_ID, _SCALAR)
    _adopt(tmp_path)

    with pytest.raises(IllegalTransitionError, match="'paused'"):
        _adopt(tmp_path)
    with pytest.raises(AdoptionError, match="its own checkpoint"):
        adopt_checkpoint(SOURCE_ID, SOURCE_ID, expected_revision=1, results=tmp_path)


def test_leftovers_of_an_interrupted_adoption_are_replaced(tmp_path: Path) -> None:
    """Files from an adoption that never recorded itself do not survive."""
    _cancelled_source(tmp_path, _BATCH)
    target = _create(tmp_path, TARGET_ID, _BATCH)
    stale = target.directory / ".adopting-stale"
    stale.mkdir()
    (stale / "junk").write_text("stale")
    (target.directory / "replicate-999").mkdir()
    (checkpoints_directory(target.directory) / "ckpt-stale").mkdir(parents=True)

    adopted = _adopt(tmp_path)

    names = sorted(path.name for path in adopted.directory.iterdir())
    assert "replicate-999" not in names
    assert not stale.exists()
    checkpoint = adopted.record.latest_checkpoint
    assert checkpoint is not None
    kept = checkpoints_directory(adopted.directory).iterdir()
    assert [path.name for path in kept] == [checkpoint.checkpoint_id]
