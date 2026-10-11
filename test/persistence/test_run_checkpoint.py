"""Tests of atomic checkpoint publication (`run_checkpoint`).

Crashes are simulated by a fault hook that raises at each named step of the
protocol. After every crash the lifecycle pointer must still name a complete,
verifiable checkpoint (the previous one), and anything else on disk must be
unreferenced garbage that recovery removes.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest
import yaml

from fim.examples import classes
from fim.persistence.run_checkpoint import (
    ARRAYS_FILE_NAME,
    CHECKPOINT_FILE_NAME,
    CheckpointError,
    UnsupportedCheckpointError,
    checkpoints_directory,
    collect_checkpoint_garbage,
    commit_checkpoint,
    load_checkpoint,
    new_checkpoint_id,
    publish_checkpoint,
)
from fim.persistence.run_lifecycle import (
    AttemptMismatchError,
    CheckpointReference,
    RunPhase,
    RunState,
)
from fim.persistence.run_objects import RunObject, create_run_object, read_run_object
from fim.persistence.run_ownership import (
    ExecutionLease,
    reconcile_run_object,
    resume_run_object,
    start_run_object,
)

_CLASSES_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "classes.yaml"
AT = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
_VALID: dict[str, object] = {
    "N": 20,
    "ploidy": "haploid",
    "d": 2,
    "m": 0.1,
    "mu": 0.01,
    "seed": 20261005,
    "precision": 1.0,
    "max_generations": 10,
    "n_replicates": 1,
    "stop_batch_early": False,
}


class CrashError(Exception):
    """The simulated crash a fault hook raises."""


@pytest.fixture(autouse=True)
def _fixture_class_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check every `class` against the test fixture, never the shipped file."""
    monkeypatch.setattr(classes, "default_classes_path", lambda: _CLASSES_FIXTURE)


def _clock() -> datetime:
    """Return the fixed test time."""
    return AT


def _planned(tmp_path: Path) -> RunObject:
    """Create a planned object in `tmp_path`."""
    return create_run_object(
        yaml.safe_dump(_VALID, sort_keys=False),
        results=tmp_path,
        clock=_clock,
        id_factory=lambda: "obj-" + "1" * 32,
    )


def _start(tmp_path: Path) -> tuple[RunObject, ExecutionLease]:
    """Create and start an object; return it and its lease."""
    planned = _planned(tmp_path)
    counter = itertools.count(1)
    result = start_run_object(
        planned.object_id,
        expected_revision=planned.record.revision,
        results=tmp_path,
        clock=_clock,
        attempt_factory=lambda: f"att-{next(counter):032x}",
    )
    assert result.lease is not None
    return planned, result.lease


def _state(generation: int) -> tuple[dict[str, Any], dict[str, npt.NDArray[Any]]]:
    """A small payload and arrays that depend on `generation`."""
    payload = {"generation": generation, "rng": {"state": str(2**100 + generation)}}
    arrays = {
        "population.counts": np.arange(generation, generation + 5, dtype=np.int64),
        "monitor.values.0": np.linspace(0.0, 1.0, 4) + generation,
    }
    return payload, arrays


def _crash_at(point: str) -> Callable[[str], None]:
    """Return a fault hook that raises `CrashError` at `point`."""

    def hook(reached: str) -> None:
        if reached == point:
            raise CrashError(point)

    return hook


def _entries(directory: Path) -> list[str]:
    """Return the names under an object's checkpoints directory."""
    return sorted(entry.name for entry in checkpoints_directory(directory).iterdir())


def test_commit_publishes_and_points_at_a_verifiable_checkpoint(
    tmp_path: Path,
) -> None:
    """The pointer, digest, payload, and arrays all round-trip exactly."""
    planned, lease = _start(tmp_path)
    payload, arrays = _state(10)
    reference = commit_checkpoint(
        lease,
        payload=payload,
        arrays=arrays,
        committed_generation=10,
        phase=RunPhase.SIMULATING,
        checkpoint_id="ckpt-a",
    )
    record = read_run_object(planned.object_id, results=tmp_path).record
    assert record.latest_checkpoint == reference
    assert record.committed_generation == 10
    assert record.phase is RunPhase.SIMULATING
    loaded = load_checkpoint(planned.directory, reference)
    assert loaded.payload == payload
    assert sorted(loaded.arrays) == sorted(arrays)
    for name, values in arrays.items():
        assert loaded.arrays[name].dtype == values.dtype
        np.testing.assert_array_equal(loaded.arrays[name], values)
    lease.release()


def test_publication_bytes_are_a_pure_function_of_the_state(tmp_path: Path) -> None:
    """Two publications of the same state produce identical files and digests."""
    directory = tmp_path / "object"
    payload, arrays = _state(7)
    first = publish_checkpoint(
        directory,
        payload=payload,
        arrays=arrays,
        committed_generation=7,
        checkpoint_id="one",
    )
    second = publish_checkpoint(
        directory,
        payload=payload,
        arrays=arrays,
        committed_generation=7,
        checkpoint_id="two",
    )
    assert first.digest == second.digest
    base = checkpoints_directory(directory)
    for name in (CHECKPOINT_FILE_NAME, ARRAYS_FILE_NAME):
        assert (base / "one" / name).read_bytes() == (base / "two" / name).read_bytes()


def test_a_newer_commit_prunes_the_previous_checkpoint(tmp_path: Path) -> None:
    """Only the referenced checkpoint remains after a successful commit."""
    planned, lease = _start(tmp_path)
    for generation, name in ((10, "ckpt-a"), (20, "ckpt-b")):
        payload, arrays = _state(generation)
        commit_checkpoint(
            lease,
            payload=payload,
            arrays=arrays,
            committed_generation=generation,
            checkpoint_id=name,
        )
    assert _entries(planned.directory) == ["ckpt-b"]
    lease.release()


@pytest.mark.parametrize("point", ["staged", "published", "committed"])
def test_a_crash_at_any_step_leaves_a_verifiable_pointer(
    tmp_path: Path, point: str
) -> None:
    """Before the pointer moves, the old checkpoint stays; after, the new one."""
    planned, lease = _start(tmp_path)
    payload, arrays = _state(10)
    first = commit_checkpoint(
        lease,
        payload=payload,
        arrays=arrays,
        committed_generation=10,
        checkpoint_id="ckpt-a",
    )
    payload, arrays = _state(20)
    with pytest.raises(CrashError):
        commit_checkpoint(
            lease,
            payload=payload,
            arrays=arrays,
            committed_generation=20,
            checkpoint_id="ckpt-b",
            fault=_crash_at(point),
        )
    record = read_run_object(planned.object_id, results=tmp_path).record
    pointer = record.latest_checkpoint
    assert pointer is not None
    expected_name = "ckpt-b" if point == "committed" else "ckpt-a"
    assert pointer.checkpoint_id == expected_name
    if point != "committed":
        assert pointer == first
    load_checkpoint(planned.directory, pointer)
    # Recovery removes everything the pointer does not name.
    collect_checkpoint_garbage(planned.directory, pointer)
    assert _entries(planned.directory) == [expected_name]
    load_checkpoint(planned.directory, pointer)
    lease.release()


def test_a_crash_while_staging_leaves_no_staging_directory(tmp_path: Path) -> None:
    """An exception inside publication removes its hidden staging sibling."""
    directory = tmp_path / "object"
    payload, arrays = _state(3)
    with pytest.raises(CrashError):
        publish_checkpoint(
            directory,
            payload=payload,
            arrays=arrays,
            committed_generation=3,
            checkpoint_id="ckpt-x",
            fault=_crash_at("staged"),
        )
    assert _entries(directory) == []


def test_a_killed_staging_directory_is_garbage(tmp_path: Path) -> None:
    """A staging directory left by a killed process never becomes a pointer."""
    planned, lease = _start(tmp_path)
    payload, arrays = _state(10)
    reference = commit_checkpoint(
        lease,
        payload=payload,
        arrays=arrays,
        committed_generation=10,
        checkpoint_id="ckpt-a",
    )
    leftover = checkpoints_directory(planned.directory) / ".staging-ckpt-b"
    leftover.mkdir()
    (leftover / CHECKPOINT_FILE_NAME).write_text("{", encoding="utf-8")
    assert collect_checkpoint_garbage(planned.directory, reference) == (
        ".staging-ckpt-b",
    )
    assert _entries(planned.directory) == ["ckpt-a"]
    lease.release()


def test_a_stale_attempt_cannot_publish_a_pointer(tmp_path: Path) -> None:
    """A fenced worker's checkpoint is removed and the pointer is unchanged."""
    planned, stale = _start(tmp_path)
    payload, arrays = _state(10)
    first = commit_checkpoint(
        stale,
        payload=payload,
        arrays=arrays,
        committed_generation=10,
        checkpoint_id="ckpt-a",
    )
    stale_lock = stale._lock
    assert stale_lock is not None
    stale._lock = None
    stale_lock.close()
    interrupted = reconcile_run_object(planned.object_id, results=tmp_path)
    resumed = resume_run_object(
        planned.object_id,
        expected_revision=interrupted.record.revision,
        results=tmp_path,
        attempt_factory=lambda: "att-" + "f" * 32,
    )
    assert resumed.lease is not None
    stale._lock = stale_lock
    payload, arrays = _state(20)
    with pytest.raises(AttemptMismatchError):
        commit_checkpoint(
            stale,
            payload=payload,
            arrays=arrays,
            committed_generation=20,
            checkpoint_id="ckpt-stale",
        )
    record = read_run_object(planned.object_id, results=tmp_path).record
    assert record.state is RunState.RUNNING
    assert record.latest_checkpoint == first
    assert _entries(planned.directory) == ["ckpt-a"]
    resumed.lease.release()


def test_publishing_over_an_existing_checkpoint_is_refused(tmp_path: Path) -> None:
    """Published checkpoint directories are immutable."""
    directory = tmp_path / "object"
    payload, arrays = _state(1)
    publish_checkpoint(
        directory,
        payload=payload,
        arrays=arrays,
        committed_generation=1,
        checkpoint_id="ckpt-a",
    )
    with pytest.raises(FileExistsError):
        publish_checkpoint(
            directory,
            payload=payload,
            arrays=arrays,
            committed_generation=1,
            checkpoint_id="ckpt-a",
        )


@pytest.mark.parametrize(
    ("payload", "arrays", "generation", "checkpoint_id"),
    [
        ({"x": float("nan")}, {}, 1, "a"),
        ({"x": object()}, {}, 1, "a"),
        ({}, {"x": np.array([object()])}, 1, "a"),
        ({}, {}, -1, "a"),
        ({}, {}, 1, ".hidden"),
        ({}, {}, 1, "a/b"),
    ],
)
def test_unsafe_inputs_are_refused_before_anything_is_written(
    tmp_path: Path,
    payload: dict[str, Any],
    arrays: dict[str, npt.NDArray[Any]],
    generation: int,
    checkpoint_id: str,
) -> None:
    """Non-JSON payloads, object arrays, and bad names never reach disk."""
    directory = tmp_path / "object"
    with pytest.raises(ValueError):
        publish_checkpoint(
            directory,
            payload=payload,
            arrays=arrays,
            committed_generation=generation,
            checkpoint_id=checkpoint_id,
        )
    assert not checkpoints_directory(directory).exists() or (_entries(directory) == [])


def _published(tmp_path: Path) -> tuple[Path, CheckpointReference]:
    """Publish one checkpoint and return its object directory and reference."""
    directory = tmp_path / "object"
    payload, arrays = _state(5)
    return directory, publish_checkpoint(
        directory,
        payload=payload,
        arrays=arrays,
        committed_generation=5,
        checkpoint_id="ckpt-a",
    )


def test_a_tampered_document_fails_its_digest(tmp_path: Path) -> None:
    """Any change to `checkpoint.json` is detected."""
    directory, reference = _published(tmp_path)
    path = checkpoints_directory(directory) / "ckpt-a" / CHECKPOINT_FILE_NAME
    path.write_bytes(path.read_bytes().replace(b'"generation":5', b'"generation":6'))
    with pytest.raises(CheckpointError, match="digest"):
        load_checkpoint(directory, reference)


def test_tampered_arrays_fail_their_digest(tmp_path: Path) -> None:
    """Any change to `arrays.npz` is detected through the document digest."""
    directory, reference = _published(tmp_path)
    path = checkpoints_directory(directory) / "ckpt-a" / ARRAYS_FILE_NAME
    data = bytearray(path.read_bytes())
    data[-30] ^= 0xFF
    path.write_bytes(bytes(data))
    with pytest.raises(CheckpointError, match="arrays do not match"):
        load_checkpoint(directory, reference)


def test_a_missing_checkpoint_is_reported(tmp_path: Path) -> None:
    """A pointer to a directory that is gone fails explicitly."""
    directory, reference = _published(tmp_path)
    missing = CheckpointReference("ckpt-gone", reference.digest, 5)
    with pytest.raises(CheckpointError, match="unreadable"):
        load_checkpoint(directory, missing)


def test_a_generation_mismatch_is_reported(tmp_path: Path) -> None:
    """The reference and the document must agree on the generation."""
    directory, reference = _published(tmp_path)
    wrong = CheckpointReference(reference.checkpoint_id, reference.digest, 6)
    with pytest.raises(CheckpointError, match="generation"):
        load_checkpoint(directory, wrong)


def _rewritten(
    directory: Path, change: Callable[[dict[str, Any]], object]
) -> CheckpointReference:
    """Rewrite `ckpt-a`'s document and return a reference matching its digest."""
    import hashlib  # noqa: PLC0415 - only this helper recomputes digests

    path = checkpoints_directory(directory) / "ckpt-a" / CHECKPOINT_FILE_NAME
    document = json.loads(path.read_bytes())
    change(document)
    data = json.dumps(document).encode("utf-8")
    path.write_bytes(data)
    return CheckpointReference("ckpt-a", hashlib.sha256(data).hexdigest(), 5)


def test_a_newer_schema_is_not_executable(tmp_path: Path) -> None:
    """A future schema version is refused, not guessed at."""
    directory, _ = _published(tmp_path)
    reference = _rewritten(directory, lambda d: d.update(schema_version=2))
    with pytest.raises(UnsupportedCheckpointError, match="schema version 2"):
        load_checkpoint(directory, reference)


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(payload=[]),
        lambda d: d.update(array_names="x"),
        lambda d: d.update(array_names=["population.counts"]),
    ],
)
def test_a_malformed_document_is_refused(
    tmp_path: Path, change: Callable[[dict[str, Any]], object]
) -> None:
    """A document whose digest matches but whose structure is wrong fails."""
    directory, _ = _published(tmp_path)
    reference = _rewritten(directory, change)
    with pytest.raises(CheckpointError):
        load_checkpoint(directory, reference)


def test_checkpoint_ids_sort_by_generation() -> None:
    """Generated names order by generation and never collide."""
    names = [new_checkpoint_id(g) for g in (9, 10, 100)]
    assert names == sorted(names)
    assert new_checkpoint_id(5) != new_checkpoint_id(5)
