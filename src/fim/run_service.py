"""The lifecycle service: execute and control persisted Run objects.

Every front end (the CLI now, the GUI next) drives Run objects through this
module, so the state machine in `fim.persistence.run_lifecycle` and the
ownership protocol in `fim.persistence.run_ownership` are applied in one
place only.

`execute_run_object` is the worker. It opens an attempt (start for a
`planned` object, resume for a `paused`, `interrupted`, or `failed` one),
runs a `fim.execution.ScalarSession` in bounded steps, and between steps
sits at a safe point where it:

- completes the run when the convergence monitor stops it, even if a pause
  or cancel was requested meanwhile (completion is never delayed by an
  extra generation);
- acknowledges a requested pause only after a checkpoint is published and
  recorded, then releases ownership;
- acknowledges a requested cancel after committing the trajectory written
  so far, which is kept as partial data;
- publishes a checkpoint when the cadence (generations or seconds) is due.

The trajectory log lives in the object directory as `trajectory.tlog`; the
completed run's report, convergence history, and manifest are written
beside it by the caller-supplied artifact writer before completion is
committed, with the manifest last.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml

from fim.engine import RunResult
from fim.execution import ScalarSession, check_session_support
from fim.model.params import SimulationParams
from fim.persistence.binary_store import (
    TRAJECTORY_LOG_FILENAME,
    BinaryLogStore,
    log_options,
)
from fim.persistence.run_checkpoint import load_checkpoint
from fim.persistence.run_lifecycle import (
    ACTIVE_STATES,
    RESUMABLE_STATES,
    LifecycleError,
    RunEvent,
    RunPhase,
    RunState,
    ValidationIssue,
)
from fim.persistence.run_objects import RunObject, read_run_object
from fim.persistence.run_ownership import (
    AttemptFactory,
    Clock,
    CommandResult,
    ExecutionLease,
    abandon_run_object,
    reconcile_run_object,
    request_cancel,
    resume_run_object,
    start_run_object,
)

logger = logging.getLogger(__name__)

DEFAULT_CHECKPOINT_GENERATIONS: Final = 1000
"""Committed generations between periodic checkpoints."""

DEFAULT_CHECKPOINT_SECONDS: Final = 60.0
"""Seconds between periodic checkpoints, checked at safe points."""

DEFAULT_CONTROL_GENERATIONS: Final = 50
"""Generations between checks for a pause or cancel request."""

ArtifactWriter = Callable[[RunResult, Path], object]
"""Writes a completed run's artifacts into a directory, manifest last."""

StoreFactory = Callable[[Path, SimulationParams], BinaryLogStore]
"""Opens the trajectory log at a path for a configuration."""

ProgressObserver = Callable[[ScalarSession], None]
"""Called at each safe point with the session, for progress display."""


class DraftRunError(LifecycleError):
    """A draft cannot execute until its configuration is valid.

    Attributes:
        object_id: The draft object.
        issues: The stored validation issues.
    """

    def __init__(self, object_id: str, issues: tuple[ValidationIssue, ...]) -> None:
        """Record the draft's issues and describe them."""
        self.object_id = object_id
        self.issues = issues
        details = "; ".join(
            f"{issue.location}: {issue.message}" if issue.location else issue.message
            for issue in issues
        )
        super().__init__(
            f"run object {object_id!r} is a draft and cannot start: {details}"
        )


@dataclass(frozen=True, slots=True)
class CheckpointCadence:
    """When periodic checkpoints are published.

    Args:
        generations: Committed generations between checkpoints.
        seconds: Seconds between checkpoints, measured by the service's
            monotonic source. Time never affects scientific output.
        control_generations: Generations between checks for a pause or
            cancel request.
    """

    generations: int = DEFAULT_CHECKPOINT_GENERATIONS
    seconds: float = DEFAULT_CHECKPOINT_SECONDS
    control_generations: int = DEFAULT_CONTROL_GENERATIONS

    def __post_init__(self) -> None:
        """Reject a cadence that would never make progress."""
        if self.generations < 1 or self.control_generations < 1:
            raise ValueError("checkpoint and control intervals must be positive")
        if self.seconds <= 0:
            raise ValueError("the checkpoint interval in seconds must be positive")


def cancel_run_object(
    object_id: str,
    *,
    expected_revision: int,
    results: Path | None = None,
    clock: Clock | None = None,
    command_key: str | None = None,
) -> CommandResult:
    """Cancel a run, asking its worker or abandoning it as the state requires.

    An executing run is asked to stop at its next safe point and stays
    `cancelling` until its worker acknowledges. A planned or stopped run has
    no worker, so it is abandoned at once. Partial data is kept either way.

    Args:
        object_id: Object to cancel.
        expected_revision: Lifecycle revision the caller read.
        results: Results directory (default: `results_directory()`).
        clock: Injectable current-time source.
        command_key: Optional idempotency key.

    Returns:
        The command outcome.
    """
    current = read_run_object(object_id, results=results)
    command = (
        request_cancel if current.record.state in ACTIVE_STATES else abandon_run_object
    )
    return command(
        object_id,
        expected_revision=expected_revision,
        results=results,
        clock=clock,
        command_key=command_key,
    )


def default_store_factory(path: Path, params: SimulationParams) -> BinaryLogStore:
    """Open the trajectory log with the options the run's Expert Settings choose."""
    return BinaryLogStore(path, **log_options(params.expert))


def execute_run_object(
    object_id: str,
    *,
    write_artifacts: ArtifactWriter,
    results: Path | None = None,
    clock: Clock | None = None,
    cadence: CheckpointCadence | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    store_factory: StoreFactory = default_store_factory,
    attempt_factory: AttemptFactory | None = None,
    command_key: str | None = None,
    observer: ProgressObserver | None = None,
) -> RunObject:
    """Start or resume a Run object and execute it until it stops.

    Args:
        object_id: A `planned` object to start, or a `paused`,
            `interrupted`, or `failed` object to resume.
        write_artifacts: Writes the completed run's artifacts.
        results: Results directory (default: `results_directory()`).
        clock: Current-time source for lifecycle and manifest timestamps.
        cadence: Checkpoint and control intervals (default: 1,000
            generations or 60 seconds, checking requests every 50).
        monotonic: Elapsed-time source for the checkpoint interval.
        store_factory: Opens the trajectory log.
        attempt_factory: Injectable attempt ID source.
        command_key: Optional idempotency key for the start or resume.
        observer: Called at each safe point, for progress display.

    Returns:
        The object after this attempt stopped: `completed`, `paused`, or
        `cancelled`. A replayed request returns the object unchanged.

    Raises:
        DraftRunError: The object is a draft.
        UnsupportedSessionError: The configuration cannot run in a
            resumable session; nothing is recorded.
        IllegalTransitionError: The object cannot start or resume now.
        Exception: Whatever stopped execution; the run is recorded as
            `failed` first.
    """
    current = reconcile_run_object(object_id, results=results, clock=clock)
    record = current.record
    if record.state is RunState.DRAFT:
        raise DraftRunError(object_id, record.validation_issues)
    params = _load_params(current)
    check_session_support(params)
    opener = resume_run_object if record.state in RESUMABLE_STATES else start_run_object
    opened = opener(
        object_id,
        expected_revision=record.revision,
        results=results,
        clock=clock,
        attempt_factory=attempt_factory,
        command_key=command_key,
    )
    if opened.lease is None:
        return opened.run_object
    with opened.lease as lease:
        session = _open_session(opened.run_object, params, lease, store_factory, clock)
        with session:
            return _drive(
                session,
                lease,
                opened.run_object.directory,
                write_artifacts,
                cadence or CheckpointCadence(),
                monotonic,
                observer,
            )


def _drive(
    session: ScalarSession,
    lease: ExecutionLease,
    directory: Path,
    write_artifacts: ArtifactWriter,
    cadence: CheckpointCadence,
    monotonic: Callable[[], float],
    observer: ProgressObserver | None,
) -> RunObject:
    """Advance `session` between safe points until it stops or is told to."""
    checkpointed_generation = session.generation
    checkpointed_at = monotonic()
    while not session.stopped:
        # A pending request is honored only while the run is still going:
        # a run that has converged completes instead.
        state = lease.current().record.state
        if state is RunState.PAUSING:
            session.checkpoint(lease)
            logger.info("run object %s paused", lease.object_id)
            return lease.finish(RunEvent.COMMIT_PAUSE)
        if state is RunState.CANCELLING:
            return _cancel(session, lease)
        due = cadence.generations - (session.generation - checkpointed_generation)
        stopped = session.advance(min(cadence.control_generations, due))
        if observer is not None:
            observer(session)
        elapsed = monotonic() - checkpointed_at
        if stopped:
            break
        if (
            session.generation - checkpointed_generation >= cadence.generations
            or elapsed >= cadence.seconds
        ):
            session.checkpoint(lease)
            checkpointed_generation = session.generation
            checkpointed_at = monotonic()
    # The finalizing checkpoint lets an interrupted finalization resume
    # without stepping again.
    session.checkpoint(lease)
    result = session.finalize()
    write_artifacts(result, directory)
    logger.info(
        "run object %s completed at generation %s",
        lease.object_id,
        result.report["generation"],
    )
    return lease.finish(RunEvent.COMPLETE, phase=RunPhase.FINALIZING)


def _cancel(session: ScalarSession, lease: ExecutionLease) -> RunObject:
    """Commit the trajectory written so far and acknowledge cancellation."""
    store = session.store
    if isinstance(store, BinaryLogStore):
        position = store.checkpoint()
        lease.commit_progress(committed_generation=position.generation)
    session.close()
    logger.info("run object %s cancelled", lease.object_id)
    return lease.finish(RunEvent.COMMIT_CANCEL)


def _load_params(run_object: RunObject) -> SimulationParams:
    """Parse the object's saved configuration, which validated when saved."""
    document = yaml.safe_load(run_object.config_text)
    return SimulationParams.from_mapping(document)


def _open_session(
    run_object: RunObject,
    params: SimulationParams,
    lease: ExecutionLease,
    store_factory: StoreFactory,
    clock: Clock | None,
) -> ScalarSession:
    """Create generation zero and checkpoint it, or restore the latest checkpoint."""
    path = run_object.directory / TRAJECTORY_LOG_FILENAME
    reference = run_object.record.latest_checkpoint
    if reference is None:
        lease.commit_progress(phase=RunPhase.INITIALIZING)
        session = ScalarSession.create(
            params,
            store_factory(path, params),
            run_id=run_object.record.scientific_run_id,
            clock=clock,
        )
        try:
            session.checkpoint(lease)
        except BaseException:
            session.close()
            raise
        return session
    checkpoint = load_checkpoint(run_object.directory, reference)
    return ScalarSession.restore(
        params, checkpoint, store_factory(path, params), clock=clock
    )
