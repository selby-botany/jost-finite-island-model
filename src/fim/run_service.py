"""The lifecycle service: execute and control persisted Run objects.

Every front end (the CLI now, the GUI next) drives Run objects through this
module, so the state machine in `fim.persistence.run_lifecycle` and the
ownership protocol in `fim.persistence.run_ownership` are applied in one
place only.

`execute_run_object` is the worker. It opens an attempt (start for a
`planned` object, resume for a `paused`, `interrupted`, or `failed` one),
runs a `fim.execution.ScalarSession` (one replicate) or a
`fim.batch_session.BatchSession` (several) in bounded steps, and between
steps sits at a safe point where it:

- completes the run when the convergence monitor stops it, even if a pause
  or cancel was requested meanwhile (completion is never delayed by an
  extra generation);
- acknowledges a requested pause only after a checkpoint is published and
  recorded, then releases ownership;
- acknowledges a requested cancel after committing the trajectory written
  so far, which is kept as partial data;
- publishes a checkpoint when the cadence (generations or seconds) is due.

A scalar run's trajectory log lives in the object directory as
`trajectory.tlog`; the completed run's report, convergence history, and
manifest are written beside it by the caller-supplied artifact writer
before completion is committed, with the manifest last.

A batch lays its object directory out as `fim run` lays out a batch: one
`replicate-NNN/` directory per replicate, each with its own log and, once
complete, the same artifacts a scalar run has; then `summary.json`; then,
last, the batch `manifest.json`. A batch's committed progress is its tick
count (`BatchSession.generation`). Replicate directories the batch did not
keep (past an adaptive stop, or built by an interrupted attempt that ran
past its checkpoint) are removed before the batch is published.
"""

from __future__ import annotations

import logging
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml

from fim import __version__
from fim.batch_session import BatchSession, check_batch_session_support
from fim.config.storage import CHECKPOINT_GENERATIONS, CHECKPOINT_SECONDS
from fim.engine import RunResult, replicate_summary
from fim.execution import (
    ScalarSession,
    UnsupportedSessionError,
    check_session_support,
)
from fim.model.params import SimulationParams
from fim.persistence.binary_store import (
    TRAJECTORY_LOG_FILENAME,
    BinaryLogStore,
    log_options,
)
from fim.persistence.manifest import (
    CURRENT_BATCH_SCHEMA_VERSION,
    ArtifactDigest,
    BatchManifest,
    hash_file,
)
from fim.persistence.manifest import (
    write_batch_manifest as write_batch_manifest,  # noqa: PLC0414
)
from fim.persistence.report import write_report
from fim.persistence.run_checkpoint import (
    CheckpointError,
    UnsupportedCheckpointError,
    load_checkpoint,
)
from fim.persistence.run_lifecycle import (
    ACTIVE_STATES,
    RESUMABLE_STATES,
    LifecycleError,
    RunEvent,
    RunLifecycleRecord,
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
from fim.persistence.run_receipt import receipt_problem

logger = logging.getLogger(__name__)

DEFAULT_CHECKPOINT_GENERATIONS: Final = CHECKPOINT_GENERATIONS
"""Committed generations between periodic checkpoints."""

DEFAULT_CHECKPOINT_SECONDS: Final = CHECKPOINT_SECONDS
"""Seconds between periodic checkpoints, checked at safe points."""

DEFAULT_CONTROL_GENERATIONS: Final = 50
"""Generations between checks for a pause or cancel request."""

REPLICATE_DIRECTORY_PREFIX: Final = "replicate-"
"""Prefix of each replicate's directory inside a batch Run object."""

BATCH_SUMMARY_FILENAME: Final = "summary.json"
"""A completed batch's cross-replicate summary, beside its manifest."""

MANIFEST_FILENAME: Final = "manifest.json"
"""A completed run's manifest, written last."""

ArtifactWriter = Callable[[RunResult, Path], object]
"""Writes one completed run's artifacts into a directory, manifest last.

For a batch it is called once per kept replicate, with that replicate's
directory."""

StoreFactory = Callable[[Path, SimulationParams], BinaryLogStore]
"""Opens the trajectory log at a path for a configuration."""

Session = ScalarSession | BatchSession
"""A resumable session of either shape."""

ProgressObserver = Callable[[Session], None]
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


def cadence_for_params(
    params: SimulationParams,
    *,
    generations: int | None = None,
    seconds: float | None = None,
    control_generations: int = DEFAULT_CONTROL_GENERATIONS,
) -> CheckpointCadence:
    """Return the checkpoint cadence chosen by the run's Expert Settings.

    Args:
        params: The run's simulation parameters.
        generations: Optional generation interval override.
        seconds: Optional time interval override in seconds.
        control_generations: Generations between control polling checks.

    Returns:
        The configured CheckpointCadence.
    """
    expert = params.expert
    resolved_generations = (
        generations if generations is not None else expert.checkpoint_generations
    )
    resolved_seconds = seconds if seconds is not None else expert.checkpoint_seconds
    return CheckpointCadence(
        generations=resolved_generations,
        seconds=resolved_seconds,
        control_generations=control_generations,
    )


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


def check_run_support(params: SimulationParams) -> None:
    """Refuse a configuration no resumable session can run.

    Args:
        params: The configuration to run.

    Raises:
        UnsupportedSessionError: If the resolved backend cannot be built.
    """
    if params.n_replicates == 1:
        check_session_support(params)
    else:
        check_batch_session_support(params)


def completion_was_recovered(record: RunLifecycleRecord) -> bool:
    """Return whether reconciliation, not a worker, committed completion.

    Such a run's worker published a verified completion receipt and then
    stopped before committing `completed` (`fim.persistence.run_receipt`).

    Args:
        record: A lifecycle record.

    Returns:
        Whether the record is `completed` by `recover-completion`.
    """
    history = record.transition_history
    return (
        record.state is RunState.COMPLETED
        and bool(history)
        and history[-1].event is RunEvent.RECOVER_COMPLETION
    )


def default_store_factory(path: Path, params: SimulationParams) -> BinaryLogStore:
    """Open the trajectory log with the options the run's Expert Settings choose."""
    return BinaryLogStore(path, **log_options(params.expert))


def diagnose_run_object(run_object: RunObject) -> tuple[str, ...]:
    """Explain what would stop a Run object from resuming or being trusted.

    Read-only: nothing is repaired or recorded. Each problem is one
    sentence a person can act on: a corrupt checkpoint or one written by
    an incompatible release, a stopped run with no checkpoint, a
    configuration this release cannot run, an unfinished finalization, or
    a completed run whose published results no longer match their receipt.

    Args:
        run_object: The object to examine.

    Returns:
        The problems found, in a fixed order; empty if there are none.
    """
    record = run_object.record
    problems: list[str] = []
    if record.state in RESUMABLE_STATES:
        problems.extend(_resume_problems(run_object))
    run_id = record.scientific_run_id
    if run_id is not None and record.state is RunState.COMPLETED:
        problem = receipt_problem(run_object.directory, kind=record.kind, run_id=run_id)
        if problem is not None:
            problems.append(f"the published results are damaged: {problem}")
    elif (
        run_id is not None
        and record.phase is RunPhase.FINALIZING
        and record.state is not RunState.CANCELLED
    ):
        problem = receipt_problem(run_object.directory, kind=record.kind, run_id=run_id)
        if problem is not None:
            problems.append(f"finalization did not finish: {problem}")
    return tuple(problems)


def execute_run_object(
    object_id: str,
    *,
    results: Path | None = None,
    write_artifacts: ArtifactWriter | None = None,
    clock: Clock | None = None,
    cadence: CheckpointCadence | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    store_factory: StoreFactory = default_store_factory,
    attempt_factory: AttemptFactory | None = None,
    command_key: str | None = None,
    observer: ProgressObserver | None = None,
    check_support: Callable[[SimulationParams], object] | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> RunObject:
    """Start or resume a Run object and execute it until it stops.

    Args:
        object_id: A `planned` object to start, or a `paused`,
            `interrupted`, or `failed` object to resume.
        results: Results directory (default: `results_directory()`).
        write_artifacts: Writes the completed run's artifacts (default:
            `fim.persistence.artifacts.write_run_artifacts`; a test seam).
        clock: Current-time source for lifecycle and manifest timestamps.
        cadence: Checkpoint and control intervals (default: 1,000
            generations or 60 seconds, checking requests every 50).
        monotonic: Elapsed-time source for the checkpoint interval.
        store_factory: Opens the trajectory log.
        attempt_factory: Injectable attempt ID source.
        command_key: Optional idempotency key for the start or resume.
        observer: Called at each safe point, for progress display.
        check_support: Refuses a configuration this caller cannot run, by
            raising `UnsupportedSessionError` (default:
            `check_run_support`, which accepts scalars and batches). A
            caller that handles only one shape narrows it.
        stop_requested: Polled at each safe point; once it returns true
            the run pauses there, as `fim.termination.sigterm_requests_stop`
            arranges for SIGTERM. It must not block or take locks.

    Returns:
        The object after this attempt stopped: `completed`, `paused`, or
        `cancelled`. A replayed request returns the object unchanged, and
        so does an object whose completion reconciliation just recovered
        (`completion_was_recovered`).

    Raises:
        DraftRunError: The object is a draft.
        UnsupportedSessionError: The configuration cannot run in a
            resumable session; nothing is recorded.
        IllegalTransitionError: The object cannot start or resume now.
        Exception: Whatever stopped execution; the run is recorded as
            `failed` first.
    """
    if write_artifacts is None:
        # Imported here: the writer pulls in matplotlib, which a plain
        # `import fim.run_service` should not.
        from fim.persistence.artifacts import write_run_artifacts  # noqa: PLC0415

        write_artifacts = write_run_artifacts
    current = reconcile_run_object(object_id, results=results, clock=clock)
    record = current.record
    if completion_was_recovered(record):
        return current
    if record.state is RunState.DRAFT:
        raise DraftRunError(object_id, record.validation_issues)
    params = _load_params(current)
    (check_support or check_run_support)(params)
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
                cadence or cadence_for_params(params),
                monotonic,
                observer,
                stop_requested,
            )


def _drive(
    session: Session,
    lease: ExecutionLease,
    directory: Path,
    write_artifacts: ArtifactWriter,
    cadence: CheckpointCadence,
    monotonic: Callable[[], float],
    observer: ProgressObserver | None,
    stop_requested: Callable[[], bool] | None,
) -> RunObject:
    """Advance `session` between safe points until it stops or is told to."""
    checkpointed_generation = session.generation
    checkpointed_at = monotonic()
    while not session.stopped:
        # A pending request is honored only while the run is still going:
        # a run that has converged completes instead.
        state = lease.current().record.state
        if (
            state is RunState.RUNNING
            and stop_requested is not None
            and stop_requested()
        ):
            logger.info("run object %s asked to stop; pausing", lease.object_id)
            state = lease.request_pause().record.state
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
    # without stepping again. A receipt left by an earlier attempt goes
    # before any artifact is rewritten, so a stale receipt can never vouch
    # for a partial rewrite (`fim.persistence.run_receipt`).
    session.checkpoint(lease)
    (directory / MANIFEST_FILENAME).unlink(missing_ok=True)
    if isinstance(session, BatchSession):
        results = session.finalize()
        _publish_batch(
            results,
            session.params,
            directory,
            session.run_id or "",
            write_artifacts,
        )
        logger.info(
            "run object %s completed with %d replicate(s)",
            lease.object_id,
            len(results),
        )
    else:
        result = session.finalize()
        write_artifacts(result, directory)
        logger.info(
            "run object %s completed at generation %s",
            lease.object_id,
            result.report["generation"],
        )
    return lease.finish(RunEvent.COMPLETE, phase=RunPhase.FINALIZING)


def _cancel(session: Session, lease: ExecutionLease) -> RunObject:
    """Commit the trajectory written so far and acknowledge cancellation."""
    if isinstance(session, BatchSession):
        lease.commit_progress(committed_generation=session.commit_logs())
    elif isinstance(session.store, BinaryLogStore):
        position = session.store.checkpoint()
        lease.commit_progress(committed_generation=position.generation)
    session.close()
    logger.info("run object %s cancelled", lease.object_id)
    return lease.finish(RunEvent.COMMIT_CANCEL)


def _load_params(run_object: RunObject) -> SimulationParams:
    """Parse the object's saved configuration, which validated when saved."""
    document = yaml.safe_load(run_object.config_text)
    return SimulationParams.from_mapping(document)


load_params = _load_params
"""Public alias for loading simulation parameters from a RunObject."""


def _open_session(
    run_object: RunObject,
    params: SimulationParams,
    lease: ExecutionLease,
    store_factory: StoreFactory,
    clock: Clock | None,
) -> Session:
    """Create the first safe point and checkpoint it, or restore the latest one."""
    directory = run_object.directory
    run_id = run_object.record.scientific_run_id
    reference = run_object.record.latest_checkpoint
    if params.n_replicates > 1:
        # A planned configuration is valid, so it always has a run ID.
        batch_run_id = run_id or ""
        replicate_factory = _replicate_store_factory(
            directory, batch_run_id, params, store_factory
        )
        if reference is not None:
            return BatchSession.restore(
                params,
                load_checkpoint(directory, reference),
                replicate_factory,
                clock=clock,
            )
        lease.commit_progress(phase=RunPhase.INITIALIZING)
        session: Session = BatchSession.create(
            params, replicate_factory, run_id=batch_run_id, clock=clock
        )
    else:
        path = directory / TRAJECTORY_LOG_FILENAME
        if reference is not None:
            return ScalarSession.restore(
                params,
                load_checkpoint(directory, reference),
                store_factory(path, params),
                clock=clock,
            )
        lease.commit_progress(phase=RunPhase.INITIALIZING)
        session = ScalarSession.create(
            params, store_factory(path, params), run_id=run_id, clock=clock
        )
    try:
        session.checkpoint(lease)
    except BaseException:
        session.close()
        raise
    return session


def _publish_batch(
    results: tuple[RunResult, ...],
    params: SimulationParams,
    directory: Path,
    run_id: str,
    write_artifacts: ArtifactWriter,
) -> None:
    """Write a completed batch's artifacts, the batch manifest last.

    Replicate directories the batch did not keep are removed first. The
    batch's start and end times are the earliest replicate start and the
    latest replicate end, so a resumed batch reports when its work ran.
    """
    kept = {
        replicate_directory(directory, run_id, result.run_id).name for result in results
    }
    for entry in sorted(directory.glob(f"{REPLICATE_DIRECTORY_PREFIX}*")):
        if entry.is_dir() and entry.name not in kept:
            shutil.rmtree(entry)
    digests: dict[str, ArtifactDigest] = {}
    for result in results:
        replicate = replicate_directory(directory, run_id, result.run_id)
        write_artifacts(result, replicate)
        digests[replicate.name] = hash_file(replicate / MANIFEST_FILENAME)
    summary = directory / BATCH_SUMMARY_FILENAME
    write_report(summary, replicate_summary(results))
    digests["summary"] = hash_file(summary)
    write_batch_manifest(
        directory / MANIFEST_FILENAME,
        BatchManifest(
            schema_version=CURRENT_BATCH_SCHEMA_VERSION,
            run_id=run_id,
            replicate_run_ids=tuple(result.run_id for result in results),
            parameters=params.to_dict(),
            started_at=min(result.manifest.started_at for result in results),
            ended_at=max(result.manifest.ended_at for result in results),
            software_version=__version__,
            artifacts=digests,
        ),
    )


def _replicate_store_factory(
    directory: Path,
    run_id: str,
    params: SimulationParams,
    store_factory: StoreFactory,
) -> Callable[[str], BinaryLogStore]:
    """Return a factory opening each replicate's log in its own directory."""

    def build(replicate_run_id: str) -> BinaryLogStore:
        """Open one replicate's log, creating its directory."""
        replicate = replicate_directory(directory, run_id, replicate_run_id)
        replicate.mkdir(exist_ok=True)
        return store_factory(replicate / TRAJECTORY_LOG_FILENAME, params)

    return build


def _resume_problems(run_object: RunObject) -> list[str]:
    """Return why a paused, interrupted, or failed run may not resume."""
    record = run_object.record
    problems: list[str] = []
    try:
        check_run_support(_load_params(run_object))
    except UnsupportedSessionError as error:
        problems.append(f"this release cannot run the configuration: {error}")
    reference = record.latest_checkpoint
    if reference is None:
        if record.phase is not None:
            problems.append(
                "it stopped before its first checkpoint, so it cannot resume; "
                "clone it to run the same configuration from generation 0"
            )
        return problems
    try:
        load_checkpoint(run_object.directory, reference)
    except UnsupportedCheckpointError as error:
        problems.append(f"the checkpoint needs a different release: {error}")
    except CheckpointError as error:
        problems.append(f"the checkpoint is corrupt: {error}")
    return problems


def replicate_directory(directory: Path, run_id: str, replicate_run_id: str) -> Path:
    """Return one replicate's directory inside a batch Run object.

    Args:
        directory: The batch object's directory.
        run_id: The batch's scientific run ID.
        replicate_run_id: The replicate's run ID, `<run_id>-rNNN`.

    Returns:
        `<directory>/replicate-NNN`, as `fim run` names it.
    """
    index = int(replicate_run_id.removeprefix(f"{run_id}-r"))
    return directory / f"{REPLICATE_DIRECTORY_PREFIX}{index:03}"
