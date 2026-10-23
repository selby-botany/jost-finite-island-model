"""Execution ownership, fenced lifecycle commits, and idempotent commands.

Starting or resuming a Run object opens an *attempt*: a fresh `attempt_id`
recorded in `run.json` plus an exclusive attempt lock that the owning
process holds for as long as it executes. The attempt lock is the proof of
life; a `running` record whose attempt lock is free has lost its owner, and
`reconcile_run_object` records that as `interrupted`.

Every worker commit (progress, checkpoint, pause, completion, failure,
cancellation) goes through an `ExecutionLease` that names its attempt and
holds the object lock while it verifies ownership, so a stale worker cannot
publish after a newer attempt took over or after recovery revoked it.

Controller commands (start, resume, request pause or cancel, abandon) take
an optional idempotency key. The key is stored in the transition it causes,
atomically with the record, so a retried command returns the original
outcome instead of failing or applying twice.

The object lock is held only for a bookkeeping step, never for execution.
The attempt lock is only ever taken without blocking, so holding the object
lock while probing or taking it cannot deadlock.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import signal
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Final, Self

import psutil

from fim.paths import write_text_atomically
from fim.persistence.object_lock import (
    LockUnavailableError,
    exclusive_lock,
    is_lock_held,
)
from fim.persistence.run_lifecycle import (
    ACTIVE_STATES,
    AttemptMismatchError,
    CheckpointReference,
    CommandKeyReuseError,
    ErrorInfo,
    LifecycleError,
    RunEvent,
    RunLifecycleRecord,
    RunPhase,
    RunState,
    apply_event,
    find_command,
    record_progress,
)
from fim.persistence.run_objects import (
    RunObject,
    list_run_objects,
    object_directory,
    object_locked,
    read_run_object_unlocked,
    validate_configuration_text,
    write_run_record,
)
from fim.persistence.run_receipt import receipt_problem

logger = logging.getLogger(__name__)

_ATTEMPT_LOCKS_DIRECTORY_NAME: Final = ".attempts"
_OWNER_SUFFIX: Final = ".owner.json"
_KILL_POLL_SECONDS: Final = 0.05
# A recorded and an observed start time of the same process agree to far
# better than this; a reused PID started later differs by much more.
_START_TIME_TOLERANCE_SECONDS: Final = 1.0
KILLED_MESSAGE: Final = "stopped by Kill; work after the last checkpoint was discarded"
_WORKER_FINISH_EVENTS: Final = frozenset(
    {
        RunEvent.COMMIT_PAUSE,
        RunEvent.COMPLETE,
        RunEvent.FAIL,
        RunEvent.COMMIT_CANCEL,
    }
)

_STOPPED_IN_FINALIZATION: Final = frozenset({RunState.INTERRUPTED, RunState.FAILED})
_RECONCILED_STATES: Final = ACTIVE_STATES | _STOPPED_IN_FINALIZATION

Clock = Callable[[], datetime]
AttemptFactory = Callable[[], str]


class ConfigurationDriftError(LifecycleError):
    """The stored configuration no longer matches the recorded digest."""


class OwnershipConflictError(LifecycleError):
    """Another live attempt already owns the object's execution."""


class ResumeUnavailableError(LifecycleError):
    """The object has no committed checkpoint to resume from."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    """The outcome of a controller command.

    Args:
        run_object: The object after the command.
        replayed: Whether an earlier request with the same idempotency key
            already applied the command, so nothing changed now.
    """

    run_object: RunObject
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class StartResult:
    """The outcome of a start or resume command.

    Args:
        run_object: The object after the command.
        lease: The new attempt's lease, or `None` for a replayed request,
            whose attempt belongs to the original caller.
        replayed: Whether an earlier request with the same idempotency key
            already started this attempt.
    """

    run_object: RunObject
    lease: ExecutionLease | None
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class AttemptOwner:
    """The process that holds an attempt lock, so Kill can find it.

    The lock proves an owner is alive but not who it is; the owner writes
    this record beside the lock when it takes the attempt.

    Args:
        attempt_id: The attempt the process owns.
        pid: Its process ID.
        process_group: Its process group when it leads one (a GUI worker
            runs in its own session), else `None`.
        started_at: Its start time in seconds since the epoch, to tell it
            from a later process given the same PID; `None` if unknown.
    """

    attempt_id: str
    pid: int
    process_group: int | None
    started_at: float | None

    @classmethod
    def of_this_process(cls, attempt_id: str) -> AttemptOwner:
        """Describe the calling process as the owner of `attempt_id`."""
        pid = os.getpid()
        getpgid = getattr(os, "getpgid", None)
        group = pid if getpgid is not None and getpgid(0) == pid else None
        try:
            started_at: float | None = psutil.Process(pid).create_time()
        except psutil.Error:
            started_at = None
        return cls(attempt_id, pid, group, started_at)

    @classmethod
    def from_mapping(cls, mapping: Any) -> AttemptOwner | None:
        """Return the owner a JSON mapping describes, or `None` if malformed."""
        if not isinstance(mapping, dict):
            return None
        attempt_id = mapping.get("attemptId")
        pid = mapping.get("pid")
        group = mapping.get("processGroup")
        started_at = mapping.get("startedAt")
        if (
            not isinstance(attempt_id, str)
            or not isinstance(pid, int)
            or not (group is None or isinstance(group, int))
            or not (started_at is None or isinstance(started_at, int | float))
        ):
            return None
        return cls(attempt_id, pid, group, started_at)

    def to_mapping(self) -> dict[str, Any]:
        """Return the JSON-ready record."""
        return {
            "attemptId": self.attempt_id,
            "pid": self.pid,
            "processGroup": self.process_group,
            "startedAt": self.started_at,
        }


class ExecutionLease:
    """Ownership of one execution attempt, used for every worker commit.

    The lease holds the attempt lock from construction until `release`, a
    finishing commit, or exit from a `with` block. Leaving a `with` block by
    an exception while still owning execution records the run as `failed`.

    Attributes:
        object_id: The executing object.
        attempt_id: The attempt this lease represents.
    """

    def __init__(
        self,
        object_id: str,
        attempt_id: str,
        *,
        lock: contextlib.ExitStack,
        results: Path | None,
        clock: Clock | None,
    ) -> None:
        """Wrap an already-acquired attempt lock; use `start_run_object`."""
        self.object_id = object_id
        self.attempt_id = attempt_id
        self._lock: contextlib.ExitStack | None = lock
        self._results = results
        self._clock = clock

    def __enter__(self) -> Self:
        """Return the lease for use in a `with` block."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Record an escaping exception as a failure, then release."""
        if exc is not None and self.is_held and isinstance(exc, Exception):
            with contextlib.suppress(LifecycleError):
                self.finish(
                    RunEvent.FAIL,
                    error=ErrorInfo(
                        category="engine",
                        message=f"{type(exc).__name__}: {exc}",
                        attempt_id=self.attempt_id,
                    ),
                )
        self.release()

    @property
    def is_held(self) -> bool:
        """Return whether this lease still holds its attempt lock."""
        return self._lock is not None

    def commit_progress(
        self,
        *,
        phase: RunPhase | None = None,
        committed_generation: int | None = None,
        checkpoint: CheckpointReference | None = None,
    ) -> RunObject:
        """Durably record progress or a published checkpoint.

        Args:
            phase: Execution phase, if it changed.
            committed_generation: Last durably committed generation.
            checkpoint: A checkpoint already published by atomic rename.

        Returns:
            The object after the commit.

        Raises:
            AttemptMismatchError: This attempt no longer owns execution.
        """
        self._require_held()
        with object_locked(self.object_id, self._results):
            current = read_run_object_unlocked(self.object_id, self._results)
            record = record_progress(
                current.record,
                at=_now(self._clock),
                attempt_id=self.attempt_id,
                phase=phase,
                committed_generation=committed_generation,
                checkpoint=checkpoint,
            )
            write_run_record(current.directory, record)
        return _with_record(current, record)

    def current(self) -> RunObject:
        """Return the stored object, for example to observe a requested pause.

        Returns:
            The object as stored now. A `pausing` or `cancelling` state asks
            the worker to stop at its next safe point.
        """
        with object_locked(self.object_id, self._results):
            return read_run_object_unlocked(self.object_id, self._results)

    def finish(
        self,
        event: RunEvent,
        *,
        phase: RunPhase | None = None,
        error: ErrorInfo | None = None,
    ) -> RunObject:
        """Commit a finishing event and release the attempt.

        Args:
            event: `commit-pause`, `complete`, `fail`, or `commit-cancel`.
            phase: Execution phase to record, if it changed.
            error: For `fail`, the error to record.

        Returns:
            The object after the transition.

        Raises:
            ValueError: `event` is not a finishing worker event.
            AttemptMismatchError: This attempt no longer owns execution.
            IllegalTransitionError: The event is not legal now.
        """
        if event not in _WORKER_FINISH_EVENTS:
            raise ValueError(f"{event.value!r} is not a finishing worker event")
        self._require_held()
        with object_locked(self.object_id, self._results):
            current = read_run_object_unlocked(self.object_id, self._results)
            _require_owner(current, self.attempt_id)
            record = apply_event(
                current.record,
                event,
                at=_now(self._clock),
                attempt_id=self.attempt_id,
                phase=phase,
                error=error,
            )
            write_run_record(current.directory, record)
        self.release()
        logger.debug(
            "attempt %s finished %s as %s",
            self.attempt_id,
            self.object_id,
            record.state.value,
        )
        return _with_record(current, record)

    def request_pause(self) -> RunObject:
        """Ask this attempt itself to pause, as a SIGTERM does.

        A `running` object becomes `pausing`; the worker then pauses at its
        next safe point exactly as if a controller had asked. An object
        already pausing or cancelling is left as it is.

        Returns:
            The object after the request.

        Raises:
            AttemptMismatchError: This attempt no longer owns execution.
        """
        self._require_held()
        with object_locked(self.object_id, self._results):
            current = read_run_object_unlocked(self.object_id, self._results)
            _require_owner(current, self.attempt_id)
            if current.record.state is not RunState.RUNNING:
                return current
            record = apply_event(
                current.record,
                RunEvent.REQUEST_PAUSE,
                at=_now(self._clock),
                attempt_id=self.attempt_id,
            )
            write_run_record(current.directory, record)
        return _with_record(current, record)

    def release(self) -> None:
        """Release the attempt lock without a transition; idempotent.

        A run still recorded as active afterward has lost its owner, and
        `reconcile_run_object` will record it as `interrupted`.
        """
        if self._lock is not None:
            lock, self._lock = self._lock, None
            lock.close()

    def _require_held(self) -> None:
        """Refuse a commit from a lease that already released its lock."""
        if self._lock is None:
            raise AttemptMismatchError(
                f"attempt {self.attempt_id!r} no longer holds its lease"
            )


def abandon_run_object(
    object_id: str,
    *,
    expected_revision: int,
    results: Path | None = None,
    clock: Clock | None = None,
    command_key: str | None = None,
) -> CommandResult:
    """Abandon a planned or stopped run, making it `cancelled`.

    Args:
        object_id: Object to abandon.
        expected_revision: Lifecycle revision the caller read.
        results: Results directory (default: `results_directory()`).
        clock: Injectable current-time source.
        command_key: Optional idempotency key.

    Returns:
        The command outcome.
    """
    return _control(
        object_id,
        RunEvent.ABANDON,
        expected_revision=expected_revision,
        results=results,
        clock=clock,
        command_key=command_key,
    )


def attempt_lock_path(object_id: str, results: Path | None = None) -> Path:
    """Return the attempt lock an executing owner holds for the object.

    It lives outside the object directory so deletion and rename work on
    every platform.
    """
    directory = object_directory(object_id, results)
    return directory.parent / _ATTEMPT_LOCKS_DIRECTORY_NAME / f"{object_id}.lock"


def attempt_owner_path(object_id: str, results: Path | None = None) -> Path:
    """Return the record naming the process that holds the attempt lock."""
    lock = attempt_lock_path(object_id, results)
    return lock.with_name(lock.stem + _OWNER_SUFFIX)


def kill_run_object(
    object_id: str,
    *,
    results: Path | None = None,
    clock: Clock | None = None,
    timeout: float = 30.0,
) -> RunObject:
    """Stop an executing run's worker at once and record the run as `failed`.

    Unlike cancel, nothing is flushed or committed first: the worker is
    killed where it stands, so the run keeps only what its last checkpoint
    committed. A `failed` run can be deleted, or resumed from that
    checkpoint where the checkpoint is still intact.

    Args:
        object_id: A `running`, `pausing`, or `cancelling` object.
        results: Results directory (default: `results_directory()`).
        clock: Injectable current-time source.
        timeout: Seconds to wait for the killed worker to release its lock.

    Returns:
        The object afterward, normally `failed`. A worker that finished on
        its own first leaves the state it recorded.

    Raises:
        LifecycleError: The run is not executing.
        OwnershipConflictError: The live owner cannot be identified, or
            still holds its lock after `timeout` seconds.
    """
    lock_path = attempt_lock_path(object_id, results)
    with object_locked(object_id, results):
        current = read_run_object_unlocked(object_id, results)
        state = current.record.state
        if state not in ACTIVE_STATES:
            raise LifecycleError(
                f"run object {object_id!r} is {state.value}, not executing"
            )
        owner = None
        if is_lock_held(lock_path):
            owner = read_attempt_owner(object_id, results)
            if owner is None or owner.attempt_id != current.record.active_attempt_id:
                raise OwnershipConflictError(
                    f"the worker running {object_id!r} did not record its "
                    "process, so it cannot be killed here; stop it with "
                    "Cancel or end the process with the operating system"
                )
    if owner is not None:
        _kill_owner(owner)
        _wait_for_release(lock_path, timeout)
        # A killed worker never reaches the cleanup that removes its record.
        attempt_owner_path(object_id, results).unlink(missing_ok=True)
    killed = record_worker_failure(
        object_id, KILLED_MESSAGE, results=results, clock=clock, category="killed"
    )
    logger.info("killed run object %s; now %s", object_id, killed.record.state.value)
    return killed


def new_attempt_id() -> str:
    """Return a fresh random attempt ID."""
    return f"att-{uuid.uuid4().hex}"


def reconcile_run_object(
    object_id: str,
    *,
    results: Path | None = None,
    clock: Clock | None = None,
) -> RunObject:
    """Record what became of a run whose owner is gone.

    A live owner always holds the attempt lock, so a free lock proves the
    owner is gone; a timeout alone never does. An active run whose owner
    is gone is recorded as `interrupted`, unless it was finalizing and its
    completion receipt verifies (`fim.persistence.run_receipt`): then the
    owner published every artifact and only the commit was lost, so the
    run is recorded as `completed`. An `interrupted` or `failed` run that
    stopped while finalizing is completed the same way once its receipt
    verifies. Every other run is returned unchanged.

    Args:
        object_id: Object to reconcile.
        results: Results directory (default: `results_directory()`).
        clock: Injectable current-time source.

    Returns:
        The object after reconciliation.
    """
    with object_locked(object_id, results):
        current = read_run_object_unlocked(object_id, results)
        state = current.record.state
        if state not in _RECONCILED_STATES:
            return current
        if state in ACTIVE_STATES and is_lock_held(
            attempt_lock_path(object_id, results)
        ):
            return current
        event = (
            RunEvent.RECOVER_COMPLETION
            if _receipt_verifies(current)
            else RunEvent.OWNER_LOST
        )
        if event is RunEvent.OWNER_LOST and state not in ACTIVE_STATES:
            return current
        record = apply_event(current.record, event, at=_now(clock))
        write_run_record(current.directory, record)
    logger.info("run object %s lost its owner; marked %s", object_id, record.state)
    return _with_record(current, record)


def record_worker_failure(
    object_id: str,
    message: str,
    *,
    results: Path | None = None,
    clock: Clock | None = None,
    category: str = "worker",
) -> RunObject:
    """Record a child-process exit as a failed attempt when it owns no lock.

    A run no longer executing is returned unchanged, so a worker's own
    exit report and a Kill can both record the same exit.
    """
    with object_locked(object_id, results):
        current = read_run_object_unlocked(object_id, results)
        if current.record.state not in ACTIVE_STATES:
            return current
        if is_lock_held(attempt_lock_path(object_id, results)):
            raise OwnershipConflictError(
                f"Run object {object_id} still has an execution owner"
            )
        attempt_id = current.record.active_attempt_id
        if attempt_id is None:
            raise LifecycleError(
                f"Run object {object_id} has no active attempt to fail"
            )
        record = apply_event(
            current.record,
            RunEvent.FAIL,
            at=_now(clock),
            expected_revision=current.record.revision,
            error=ErrorInfo(
                category=category,
                message=message,
                attempt_id=attempt_id,
            ),
        )
        write_run_record(current.directory, record)
    logger.error("worker for Run object %s failed: %s", object_id, message)
    return _with_record(current, record)


def reconcile_run_objects(
    results: Path | None = None, *, clock: Clock | None = None
) -> list[RunObject]:
    """Reconcile every readable object; return those that changed state.

    Args:
        results: Results directory (default: `results_directory()`).
        clock: Injectable current-time source.

    Returns:
        The objects newly recorded as `interrupted` or recovered as
        `completed`.
    """
    changed: list[RunObject] = []
    for listed in list_run_objects(results).objects:
        if listed.record.state not in _RECONCILED_STATES:
            continue
        reconciled = reconcile_run_object(
            listed.object_id, results=results, clock=clock
        )
        if reconciled.record.revision != listed.record.revision:
            changed.append(reconciled)
    return changed


def read_attempt_owner(
    object_id: str, results: Path | None = None
) -> AttemptOwner | None:
    """Return the recorded owner of the object's attempt, if readable."""
    try:
        text = attempt_owner_path(object_id, results).read_text(encoding="utf-8")
        return AttemptOwner.from_mapping(json.loads(text))
    except (OSError, ValueError):
        return None


def request_cancel(
    object_id: str,
    *,
    expected_revision: int,
    results: Path | None = None,
    clock: Clock | None = None,
    command_key: str | None = None,
) -> CommandResult:
    """Ask the owning worker to cancel at its next safe point.

    Args:
        object_id: Object to cancel.
        expected_revision: Lifecycle revision the caller read.
        results: Results directory (default: `results_directory()`).
        clock: Injectable current-time source.
        command_key: Optional idempotency key.

    Returns:
        The command outcome; the run is `cancelling` until the worker
        acknowledges.
    """
    return _control(
        object_id,
        RunEvent.REQUEST_CANCEL,
        expected_revision=expected_revision,
        results=results,
        clock=clock,
        command_key=command_key,
    )


def request_pause(
    object_id: str,
    *,
    expected_revision: int,
    results: Path | None = None,
    clock: Clock | None = None,
    command_key: str | None = None,
) -> CommandResult:
    """Ask the owning worker to checkpoint and pause at its next safe point.

    Args:
        object_id: Object to pause.
        expected_revision: Lifecycle revision the caller read.
        results: Results directory (default: `results_directory()`).
        clock: Injectable current-time source.
        command_key: Optional idempotency key.

    Returns:
        The command outcome; the run is `pausing` until the worker
        acknowledges.
    """
    return _control(
        object_id,
        RunEvent.REQUEST_PAUSE,
        expected_revision=expected_revision,
        results=results,
        clock=clock,
        command_key=command_key,
    )


def resume_run_object(
    object_id: str,
    *,
    expected_revision: int,
    results: Path | None = None,
    clock: Clock | None = None,
    attempt_factory: AttemptFactory | None = None,
    command_key: str | None = None,
) -> StartResult:
    """Open a new attempt that continues from the latest checkpoint.

    Args:
        object_id: A `paused`, `interrupted`, `failed`, or `cancelled` object.
        expected_revision: Lifecycle revision the caller read.
        results: Results directory (default: `results_directory()`).
        clock: Injectable current-time source.
        attempt_factory: Injectable attempt ID source.
        command_key: Optional idempotency key.

    Returns:
        The outcome, with a lease the caller must use for every commit.

    Raises:
        ResumeUnavailableError: No checkpoint was ever committed.
        ConfigurationDriftError: `config.yaml` no longer matches the digest.
        OwnershipConflictError: Another live attempt holds the object.
    """
    return _open_attempt(
        object_id,
        RunEvent.RESUME,
        expected_revision=expected_revision,
        results=results,
        clock=clock,
        attempt_factory=attempt_factory,
        command_key=command_key,
    )


def start_run_object(
    object_id: str,
    *,
    expected_revision: int,
    results: Path | None = None,
    clock: Clock | None = None,
    attempt_factory: AttemptFactory | None = None,
    command_key: str | None = None,
) -> StartResult:
    """Open the first attempt of a planned object.

    A draft cannot start. The saved configuration is validated again and
    must match the digest recorded when it was saved.

    Args:
        object_id: A `planned` object.
        expected_revision: Lifecycle revision the caller read.
        results: Results directory (default: `results_directory()`).
        clock: Injectable current-time source.
        attempt_factory: Injectable attempt ID source.
        command_key: Optional idempotency key.

    Returns:
        The outcome, with a lease the caller must use for every commit.

    Raises:
        IllegalTransitionError: The object is not `planned`.
        ConfigurationDriftError: `config.yaml` no longer matches the digest.
        OwnershipConflictError: Another live attempt holds the object.
    """
    return _open_attempt(
        object_id,
        RunEvent.START,
        expected_revision=expected_revision,
        results=results,
        clock=clock,
        attempt_factory=attempt_factory,
        command_key=command_key,
    )


def _check_configuration(run_object: RunObject) -> None:
    """Refuse to execute a configuration that changed after it was saved."""
    configuration, _ = validate_configuration_text(run_object.config_text)
    if configuration.digest != run_object.record.configuration_digest:
        raise ConfigurationDriftError(
            f"config.yaml of {run_object.object_id!r} no longer matches its "
            "recorded configuration digest"
        )


def _control(
    object_id: str,
    event: RunEvent,
    *,
    expected_revision: int,
    results: Path | None,
    clock: Clock | None,
    command_key: str | None,
) -> CommandResult:
    """Apply one controller event under the object lock, idempotently."""
    with object_locked(object_id, results):
        current = read_run_object_unlocked(object_id, results)
        if _is_replay(current, event, command_key):
            return CommandResult(current, replayed=True)
        record = apply_event(
            current.record,
            event,
            at=_now(clock),
            expected_revision=expected_revision,
            command_key=command_key,
        )
        write_run_record(current.directory, record)
    return CommandResult(_with_record(current, record))


def _is_replay(run_object: RunObject, event: RunEvent, command_key: str | None) -> bool:
    """Return whether `command_key` already applied `event` to the object.

    Raises:
        CommandKeyReuseError: The key already applied a different event.
    """
    if command_key is None:
        return False
    original = find_command(run_object.record, command_key)
    if original is None:
        return False
    if original.event is not event:
        raise CommandKeyReuseError(command_key, original)
    return True


def _kill_owner(owner: AttemptOwner) -> None:
    """Kill the owner's process group, or the process alone if it leads none.

    A process whose start time differs from the record's is a later one
    given the same PID, and is left alone.
    """
    try:
        process = psutil.Process(owner.pid)
        if (
            owner.started_at is not None
            and abs(process.create_time() - owner.started_at)
            > _START_TIME_TOLERANCE_SECONDS
        ):
            logger.warning(
                "process %d is not the recorded owner of %s; not killed",
                owner.pid,
                owner.attempt_id,
            )
            return
        killpg = getattr(os, "killpg", None)
        if owner.process_group is not None and killpg is not None:
            logger.debug("killing process group %d", owner.process_group)
            killpg(owner.process_group, signal.SIGKILL)
        else:
            logger.debug("killing process %d", owner.pid)
            process.kill()
    except (psutil.NoSuchProcess, ProcessLookupError):
        logger.debug("owner process %d already exited", owner.pid)


def _now(clock: Clock | None) -> datetime:
    """Return the current time from `clock`, or the real UTC time."""
    return clock() if clock is not None else datetime.now(UTC)


def _open_attempt(
    object_id: str,
    event: RunEvent,
    *,
    expected_revision: int,
    results: Path | None,
    clock: Clock | None,
    attempt_factory: AttemptFactory | None,
    command_key: str | None,
) -> StartResult:
    """Commit `start` or `resume` and take the attempt lock, all-or-nothing.

    Replay, legality, and revision are decided first, so a retried or stale
    request reports that rather than an ownership conflict. The attempt lock
    is then taken without blocking while the object lock is held, and is
    released again if anything later fails, so a refused start never leaves
    a phantom owner behind.
    """
    lock = contextlib.ExitStack()
    try:
        with object_locked(object_id, results):
            current = read_run_object_unlocked(object_id, results)
            if _is_replay(current, event, command_key):
                return StartResult(current, lease=None, replayed=True)
            attempt_id = (attempt_factory or new_attempt_id)()
            record = apply_event(
                current.record,
                event,
                at=_now(clock),
                expected_revision=expected_revision,
                attempt_id=attempt_id,
                command_key=command_key,
            )
            if event is RunEvent.RESUME and current.record.latest_checkpoint is None:
                raise ResumeUnavailableError(
                    f"run object {object_id!r} has no committed checkpoint"
                )
            _check_configuration(current)
            try:
                lock.enter_context(
                    exclusive_lock(
                        attempt_lock_path(object_id, results), blocking=False
                    )
                )
            except LockUnavailableError as error:
                raise OwnershipConflictError(
                    f"another attempt is executing run object {object_id!r}"
                ) from error
            write_text_atomically(
                attempt_owner_path(object_id, results),
                json.dumps(AttemptOwner.of_this_process(attempt_id).to_mapping()),
                prefix=".owner.",
            )
            write_run_record(current.directory, record)
    except BaseException:
        lock.close()
        raise
    lease = ExecutionLease(
        object_id, attempt_id, lock=lock, results=results, clock=clock
    )
    logger.debug("attempt %s owns run object %s", attempt_id, object_id)
    return StartResult(_with_record(current, record), lease=lease)


def _receipt_verifies(run_object: RunObject) -> bool:
    """Return whether a finalizing run published a verified receipt."""
    record = run_object.record
    if record.phase is not RunPhase.FINALIZING or record.scientific_run_id is None:
        return False
    problem = receipt_problem(
        run_object.directory, kind=record.kind, run_id=record.scientific_run_id
    )
    if problem is not None:
        logger.info(
            "run object %s has no completion receipt: %s", record.object_id, problem
        )
    return problem is None


def _require_owner(run_object: RunObject, attempt_id: str) -> None:
    """Fence a worker commit from an attempt that no longer owns the run."""
    if run_object.record.active_attempt_id != attempt_id:
        raise AttemptMismatchError(
            f"attempt {attempt_id!r} does not own run {run_object.object_id!r}"
        )


def _wait_for_release(lock_path: Path, timeout: float) -> None:
    """Wait for a killed owner's lock to be released by the operating system.

    Raises:
        OwnershipConflictError: The lock is still held after `timeout`.
    """
    deadline = time.monotonic() + timeout
    while is_lock_held(lock_path):
        if time.monotonic() >= deadline:
            raise OwnershipConflictError(
                f"the killed worker still holds {lock_path.name} after "
                f"{timeout:g} seconds"
            )
        time.sleep(_KILL_POLL_SECONDS)


def _with_record(run_object: RunObject, record: RunLifecycleRecord) -> RunObject:
    """Return `run_object` with its lifecycle record replaced."""
    return replace(run_object, record=record)
