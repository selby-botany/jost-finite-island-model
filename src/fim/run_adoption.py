"""Continue a planned Run object from another object's matching checkpoint.

A run is a pure function of its effective configuration: two objects with
the same configuration digest compute the same trajectory, generation for
generation. So when a planned object's configuration matches one that was
stopped part way -- a clone of a cancelled run, for example -- the planned
object can take a copy of that run's latest checkpoint and trajectory logs
and resume from there instead of recomputing from generation 0.

Adoption copies files; it never moves or changes the source. The copy is
staged in a hidden directory inside the target, verified, and published
under the target's lock, and the target's lifecycle record then moves from
`planned` to `paused` (`RunEvent.ADOPT_CHECKPOINT`). Resuming it is an
ordinary resume: restoring the checkpoint truncates each log back to the
checkpoint's position, so log records written after the checkpoint are
harmless. On APFS the copy is a copy-on-write clone, so even a multi-gigabyte
batch checkpoint is adopted in moments and uses no extra disk until either
object writes.
"""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from fim.paths import default_directory_mode
from fim.persistence.binary_store import TRAJECTORY_LOG_FILENAME
from fim.persistence.run_checkpoint import (
    checkpoints_directory,
    verify_checkpoint_files,
)
from fim.persistence.run_lifecycle import (
    ACTIVE_STATES,
    RESUMABLE_STATES,
    CheckpointReference,
    IllegalTransitionError,
    LifecycleError,
    RevisionConflictError,
    RunEvent,
    RunKind,
    RunState,
    apply_event,
)
from fim.persistence.run_objects import (
    RunObject,
    list_run_objects,
    object_locked,
    read_run_object,
    read_run_object_unlocked,
    write_run_record,
)
from fim.run_service import REPLICATE_DIRECTORY_PREFIX

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]

_STAGING_PREFIX: Final = ".adopting-"
_CLONE_NOFOLLOW: Final = 0x0001


class AdoptionError(LifecycleError):
    """A checkpoint cannot be adopted from the requested source."""


@dataclass(frozen=True, slots=True)
class ContinuationSource:
    """Another object whose checkpoint a planned object could continue from.

    Args:
        object_id: The source object.
        name: The source's run name, if it has one.
        state: The source's lifecycle state.
        checkpoint: The source's latest checkpoint.
    """

    object_id: str
    name: str | None
    state: RunState
    checkpoint: CheckpointReference

    @property
    def committed_generation(self) -> int:
        """Return the generation the checkpoint fully contains."""
        return self.checkpoint.committed_generation


def adopt_checkpoint(
    target_id: str,
    source_id: str,
    *,
    expected_revision: int,
    results: Path | None = None,
    clock: Clock | None = None,
) -> RunObject:
    """Copy a matching checkpoint and its logs into a planned object.

    Args:
        target_id: The planned object that continues.
        source_id: The stopped object whose checkpoint it continues from.
        expected_revision: The target's lifecycle revision the caller read.
        results: Results directory (default: `results_directory()`).
        clock: Injectable current-time source.

    Returns:
        The target, now `paused` at the adopted checkpoint.

    Raises:
        AdoptionError: The source is not a stopped run with a checkpoint for
            the same configuration, or the target is the source.
        RevisionConflictError: The target changed since it was read.
        IllegalTransitionError: The target is no longer planned.
        CheckpointError: The copied checkpoint does not verify.
    """
    if target_id == source_id:
        raise AdoptionError("a run cannot continue from its own checkpoint")
    target = read_run_object(target_id, results=results)
    _check_target(target, expected_revision)
    staging = target.directory / f"{_STAGING_PREFIX}{uuid.uuid4().hex}"
    staging.mkdir(mode=default_directory_mode())
    try:
        # The source lock keeps the source from resuming, which would
        # truncate its logs and replace its checkpoint mid-copy.
        with object_locked(source_id, results):
            source = read_run_object_unlocked(source_id, results)
            reference = _check_source(source, target)
            copied = _copy_execution_files(source, reference, staging)
        verify_checkpoint_files(staging, reference)
        with object_locked(target_id, results):
            current = read_run_object_unlocked(target_id, results)
            _check_target(current, expected_revision)
            _publish(staging, copied, current.directory)
            record = apply_event(
                current.record,
                RunEvent.ADOPT_CHECKPOINT,
                at=(clock or _utc_now)(),
                expected_revision=expected_revision,
                checkpoint=reference,
            )
            write_run_record(current.directory, record)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    logger.info(
        "run object %s adopted checkpoint %s at generation %d from %s",
        target_id,
        reference.checkpoint_id,
        reference.committed_generation,
        source_id,
    )
    return read_run_object(target_id, results=results)


def find_continuation_sources(
    target_id: str, *, results: Path | None = None
) -> tuple[ContinuationSource, ...]:
    """Return stopped objects a planned object could continue from.

    A source has the same configuration digest and kind, holds a
    checkpoint, and is neither executing nor completed (a completed run is
    reused as a whole instead). The furthest checkpoint comes first.

    Args:
        target_id: The planned object about to start.
        results: Results directory (default: `results_directory()`).

    Returns:
        Candidates, furthest first; empty when the target is not planned.
    """
    target = read_run_object(target_id, results=results)
    if target.record.state is not RunState.PLANNED:
        return ()
    found = [
        ContinuationSource(
            object_id=candidate.object_id,
            name=candidate.labels.name,
            state=candidate.record.state,
            checkpoint=candidate.record.latest_checkpoint,
        )
        for candidate in list_run_objects(results).objects
        if candidate.object_id != target_id
        and candidate.record.latest_checkpoint is not None
        and _source_problem(candidate, target) is None
    ]
    return tuple(
        sorted(found, key=lambda item: (-item.committed_generation, item.object_id))
    )


def _check_source(source: RunObject, target: RunObject) -> CheckpointReference:
    """Return the source's checkpoint, or refuse a source that cannot lend it.

    Raises:
        AdoptionError: The source cannot be continued from.
    """
    problem = _source_problem(source, target)
    reference = source.record.latest_checkpoint
    if problem is None and reference is None:
        problem = "it has no checkpoint"
    if problem is not None or reference is None:
        raise AdoptionError(f"cannot continue from {source.object_id}: {problem}")
    return reference


def _check_target(target: RunObject, expected_revision: int) -> None:
    """Refuse a target that changed or is no longer planned.

    Raises:
        RevisionConflictError: The target changed since it was read.
        IllegalTransitionError: The target is not planned.
    """
    record = target.record
    if record.revision != expected_revision:
        raise RevisionConflictError(expected_revision, record.revision)
    if record.state is not RunState.PLANNED:
        raise IllegalTransitionError(
            record.state, RunEvent.ADOPT_CHECKPOINT, record.revision
        )


def _clone_file(source: Path, target: Path) -> None:
    """Copy one file, as a copy-on-write clone where the filesystem allows."""
    clone = _clonefile_function()
    if clone is not None and clone(bytes(source), bytes(target), _CLONE_NOFOLLOW) == 0:
        return
    shutil.copy2(source, target)


def _clonefile_function() -> Callable[[bytes, bytes, int], int] | None:
    """Return macOS `clonefile(2)`, or None where libc has no such call."""
    try:
        function = ctypes.CDLL(None, use_errno=True).clonefile
    except (AttributeError, OSError):
        return None
    function.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32)
    function.restype = ctypes.c_int
    return function


def _copy_execution_files(
    source: RunObject, reference: CheckpointReference, staging: Path
) -> tuple[str, ...]:
    """Copy the checkpoint and trajectory logs resume needs into `staging`.

    Returns:
        The top-level names copied, for `_publish`.
    """
    checkpoint = checkpoints_directory(source.directory) / reference.checkpoint_id
    copied_checkpoint = checkpoints_directory(staging) / reference.checkpoint_id
    copied_checkpoint.mkdir(parents=True, mode=default_directory_mode())
    for entry in sorted(checkpoint.iterdir()):
        _clone_file(entry, copied_checkpoint / entry.name)
    names = [checkpoints_directory(staging).name]
    if source.record.kind is RunKind.BATCH:
        for replicate in sorted(
            source.directory.glob(f"{REPLICATE_DIRECTORY_PREFIX}*")
        ):
            log = replicate / TRAJECTORY_LOG_FILENAME
            if not log.is_file():
                continue
            (staging / replicate.name).mkdir(mode=default_directory_mode())
            _clone_file(log, staging / replicate.name / TRAJECTORY_LOG_FILENAME)
            names.append(replicate.name)
    else:
        log = source.directory / TRAJECTORY_LOG_FILENAME
        if not log.is_file():
            raise AdoptionError(
                f"cannot continue from {source.object_id}: its trajectory log "
                "is missing"
            )
        _clone_file(log, staging / TRAJECTORY_LOG_FILENAME)
        names.append(TRAJECTORY_LOG_FILENAME)
    _fsync_tree(staging)
    return tuple(names)


def _fsync_path(path: Path) -> None:
    """Flush one file or directory entry to disk where the platform allows."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _fsync_tree(root: Path) -> None:
    """Flush every file and directory under `root`, children first."""
    for directory, _subdirectories, files in os.walk(root, topdown=False):
        for name in files:
            _fsync_path(Path(directory) / name)
        _fsync_path(Path(directory))


def _publish(staging: Path, names: tuple[str, ...], directory: Path) -> None:
    """Move the staged files into a planned object's directory.

    A planned object has no execution files, so anything already there is
    left over from an adoption that stopped before its record was written.
    Other staging directories belong to such adoptions too.
    """
    for leftover in (
        checkpoints_directory(directory),
        directory / TRAJECTORY_LOG_FILENAME,
        *directory.glob(f"{REPLICATE_DIRECTORY_PREFIX}*"),
    ):
        if leftover.is_dir():
            shutil.rmtree(leftover)
        else:
            leftover.unlink(missing_ok=True)
    for name in names:
        (staging / name).rename(directory / name)
    _fsync_path(directory)
    for other in directory.glob(f"{_STAGING_PREFIX}*"):
        if other != staging:
            shutil.rmtree(other, ignore_errors=True)


def _source_problem(source: RunObject, target: RunObject) -> str | None:
    """Return why `source` cannot lend its checkpoint to `target`, if it cannot."""
    record = source.record
    if record.configuration_digest != target.record.configuration_digest:
        return "its configuration differs"
    if record.kind is not target.record.kind:
        return "it is a different kind of run"
    if record.state in ACTIVE_STATES:
        return f"it is {record.state.value}; wait for it to stop"
    if record.state is RunState.COMPLETED:
        return "it is completed; open its results instead"
    if record.state not in RESUMABLE_STATES:
        return f"a {record.state.value} run has no progress to continue"
    return None


def _utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)
