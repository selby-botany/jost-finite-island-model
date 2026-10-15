"""Running a sweep: create its Study, run the points, resume, and report status.

The execution half of `fim.sweep`
(`20260923-claude-sonnet-5-sweep-as-study-implementation-plan.md`,
`selby/restricted`, sections 3 and 4).

- `create_sweep_study` stores the specification and the plan in a new
  Study's `sweep_spec` *before* any point runs, so an interrupted sweep
  resumes from what is stored.
- `run_sweep` walks the stored points. Each point is skipped if the Study
  already holds a run with its `run_id`, attached if such a run exists
  anywhere under `results/`, and computed otherwise. Resuming is the same
  operation as running.
- Point status is *derived* (a point is done if a member run has its id),
  never stored, so a manifest cannot disagree with the run directories.
  Only failures and each point's Run object link are recorded, in a small
  file beside the Study index.
- A point dispatched by `LocalPointRunner` or `ProcessPointRunner` is a
  resumable Run object (`fim.run_service`), created lazily when the point
  first runs. Cancelling a sweep pauses its points in flight, and the next
  run resumes them from their checkpoints instead of starting over. Only a
  completed object is ever counted as a finished point.
- Points run one after another. A batch already spreads its replicates
  over the cores; running several batches at once would oversubscribe
  them. `PointRunner` is the seam a cluster would implement instead.
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import os
import queue
import shutil
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal, Protocol, runtime_checkable

import yaml

from fim import __version__, paths
from fim.config.defaults import DEFAULT_PAIRWISE_MAX_DEMES
from fim.execution import UnsupportedSessionError
from fim.model.params import SimulationParams
from fim.persistence import groups
from fim.persistence.groups import StudyManifest
from fim.persistence.object_lock import is_lock_held
from fim.persistence.run_lifecycle import (
    ACTIVE_STATES,
    LifecycleError,
    RunState,
)
from fim.persistence.run_metadata import (
    read_run_metadata,
    replace_run_metadata,
    run_metadata_path,
)
from fim.persistence.run_objects import (
    RunObject,
    create_run_object,
    list_run_objects,
    read_run_object,
)
from fim.persistence.run_ownership import attempt_lock_path, request_pause
from fim.reproducibility import compare_runs
from fim.run_service import check_run_support
from fim.sweep import (
    SweepPlan,
    SweepPoint,
    SweepSpec,
    enumerate_points,
    point_run_description,
    point_run_name,
)

logger = logging.getLogger(__name__)

PointState = Literal[
    "done",
    "stale",
    "failed",
    "waiting",
    "planned",
    "running",
    "paused",
    "interrupted",
]
"""Where a point stands.

`done` (a member run has its id, from this software version); the linked
Run object's lifecycle state when it is `planned`, `running`, `paused`,
`interrupted`, or `failed`; `stale` (only a run from another software
version); `failed` (recorded before any Run object existed); or `waiting`.
"""

_LINKED_STATES: Final[Mapping[RunState, PointState]] = {
    RunState.PLANNED: "planned",
    RunState.RUNNING: "running",
    RunState.PAUSING: "running",
    RunState.CANCELLING: "running",
    RunState.PAUSED: "paused",
    RunState.INTERRUPTED: "interrupted",
    RunState.FAILED: "failed",
}
"""Linked object states a point reports; others fall through to the rest."""

_CONTINUABLE_STATES: Final = frozenset(
    {
        RunState.PLANNED,
        RunState.RUNNING,
        RunState.PAUSING,
        RunState.CANCELLING,
        RunState.PAUSED,
        RunState.INTERRUPTED,
        RunState.FAILED,
    }
)
"""Linked object states a sweep continues rather than replacing."""

EventKind = Literal[
    "point_started",
    "point_progress",
    "point_reused",
    "point_done",
    "point_recomputed",
    "point_differs",
    "point_failed",
    "sweep_done",
    "sweep_cancelled",
]


@dataclass(frozen=True, slots=True)
class PointFailure:
    """Why one point produced no run."""

    reason: str


@dataclass(frozen=True, slots=True)
class PointPaused:
    """A point's Run object stopped, resumable, before it completed.

    Attributes:
        state: The object's lifecycle state, `paused` or `cancelled`.
    """

    state: str


@dataclass(frozen=True, slots=True)
class SweepEvent:
    """One progress event from `run_sweep`.

    Attributes:
        kind: What happened.
        index: The point's grid index, or `-1` for a whole-sweep event.
        run_id: The point's id, or an empty string for a whole-sweep event.
        position: How many points have been dealt with so far.
        total: How many points the sweep has.
        detail: A reason for a failure, or the raw run message for progress.
    """

    kind: EventKind
    index: int
    run_id: str
    position: int
    total: int
    detail: object = None


@dataclass(frozen=True, slots=True)
class SweepOutcome:
    """The tally `run_sweep` returns."""

    done: int
    reused: int
    already_present: int
    failed: int
    skipped_failed: int
    cancelled: bool
    recomputed: int = 0
    differing: int = 0


@dataclass(frozen=True, slots=True)
class PointStatus:
    """One planned point and where it stands."""

    index: int
    coordinates: Mapping[str, Any]
    run_id: str
    state: PointState
    reason: str | None = None
    object_id: str | None = None


class PointRunner(Protocol):
    """Runs one point's configuration to completion.

    The local implementation is `LocalPointRunner`. A cluster would supply
    another that computes a point elsewhere and returns the result
    directory; points are independent and self-describing, and a point's
    identity is its content hash, so no shared state is needed.
    """

    def run_point(
        self,
        params: SimulationParams,
        cancel_event: threading.Event,
        on_message: Callable[[object], None] | None = None,
        max_workers: int | None = None,
    ) -> Path | PointFailure:
        """Run `params` and return the published run directory or a failure.

        `max_workers` sizes the worker processes of a batch point (a lineal
        batch's replicates); `None` leaves the runner's own default.
        """
        ...


@runtime_checkable
class ObjectPointRunner(Protocol):
    """A runner that can also execute a point as a resumable Run object."""

    def run_object_point(
        self,
        object_id: str,
        cancel_event: threading.Event,
        on_message: Callable[[object], None] | None = None,
    ) -> Path | PointFailure | PointPaused:
        """Start or resume the object; return its directory once completed.

        A set `cancel_event` pauses the object at its next safe point.
        """
        ...


def _allocate_output_directory() -> tuple[Path, Path]:
    """Return `(directory, reservation)` for a new run no other point holds.

    `paths.default_output_directory` names a directory by the microsecond
    and checks it does not exist yet, but the directory is only created when
    a run publishes, so two points allocating at once could be handed one
    name. A reservation (a hidden marker directory, made atomically) closes
    that window between threads and between processes. The caller removes
    the reservation when the run has published or failed.
    """
    for _ in range(1000):
        candidate = paths.default_output_directory()
        reservation = candidate.parent / f".reserve-{candidate.name}"
        try:
            reservation.mkdir(parents=True)
        except FileExistsError:
            continue
        return candidate, reservation
    raise FileExistsError("could not allocate a unique run directory")


class LocalPointRunner:
    """Runs a point in this process's own scalar or batch machinery.

    The same engine invocation and atomic artifact publication the desktop
    app uses, so a sweep point is an ordinary Run the app can open. Safe to
    call from several threads at once, one call per point in flight.

    Args:
        max_workers: Worker processes for a lineal batch point, or `None`
            for the runner's default (one per core). `run_point`'s own
            `max_workers` argument overrides it for one call.
        pairwise_max_demes: Largest deme count for which each point saves
            every deme pair's Nei identities (`pairwise.json`).
    """

    def __init__(
        self,
        max_workers: int | None = None,
        *,
        pairwise_max_demes: int = DEFAULT_PAIRWISE_MAX_DEMES,
    ) -> None:
        self.max_workers = max_workers
        self.pairwise_max_demes = pairwise_max_demes

    def run_point(
        self,
        params: SimulationParams,
        cancel_event: threading.Event,
        on_message: Callable[[object], None] | None = None,
        max_workers: int | None = None,
    ) -> Path | PointFailure:
        """Run `params` synchronously; see `PointRunner.run_point`."""
        # Imported here: the runners pull in matplotlib and the GUI
        # package, which a plain `import fim.sweep_run` should not.

        try:
            output_directory, reservation = _allocate_output_directory()
        except FileExistsError as error:
            return PointFailure(str(error))
        try:
            return self._run(
                params, output_directory, cancel_event, on_message, max_workers
            )
        finally:
            shutil.rmtree(reservation, ignore_errors=True)

    def run_object_point(
        self,
        object_id: str,
        cancel_event: threading.Event,
        on_message: Callable[[object], None] | None = None,
    ) -> Path | PointFailure | PointPaused:
        """Execute a point's Run object here; see `ObjectPointRunner`."""
        return execute_point_object(
            object_id,
            cancel_event,
            on_message,
            pairwise_max_demes=self.pairwise_max_demes,
        )

    def _run(
        self,
        params: SimulationParams,
        output_directory: Path,
        cancel_event: threading.Event,
        on_message: Callable[[object], None] | None,
        max_workers: int | None,
    ) -> Path | PointFailure:
        """Start the run into `output_directory` and drain its messages."""
        from fim.gui import batch_runner, runner  # noqa: PLC0415

        workers = max_workers if max_workers is not None else self.max_workers
        message_queue: queue.Queue[Any] = queue.Queue()
        try:
            if params.n_replicates > 1:
                batch_runner.start_batch_run(
                    params,
                    output_directory,
                    message_queue,
                    cancel_event,
                    max_workers=workers,
                    pairwise_max_demes=self.pairwise_max_demes,
                )
            else:
                runner.start_run(
                    params,
                    output_directory,
                    message_queue,
                    cancel_event,
                    pairwise_max_demes=self.pairwise_max_demes,
                )
        except FileExistsError:
            return PointFailure("the output directory already exists")
        while True:
            message = message_queue.get()
            kind = message[0]
            if kind == "done":
                return output_directory
            if kind == "cancelled":
                return PointFailure("cancelled")
            if kind == "error":
                return PointFailure(str(message[1]))
            if on_message is not None:
                on_message(message)


def execute_point_object(
    object_id: str,
    cancel_event: Any,
    on_message: Callable[[object], None] | None = None,
    *,
    pairwise_max_demes: int = DEFAULT_PAIRWISE_MAX_DEMES,
    results: Path | None = None,
) -> Path | PointFailure | PointPaused:
    """Start or resume one point's Run object until it stops.

    The object is executed by `fim.run_service.execute_run_object` with the
    desktop app's artifact writer, so a completed point is an ordinary run.
    A set `cancel_event` becomes a durable pause request at the next safe
    point: the checkpoint it leaves lets the next sweep resume the point.

    Args:
        object_id: The point's Run object.
        cancel_event: A `threading.Event` or a shared manager event.
        on_message: Receives `("progress", generation)` at each safe point.
        pairwise_max_demes: Largest deme count whose `pairwise.json` the
            completed point writes.
        results: Results directory (default: `results_directory()`).

    Returns:
        The object directory once completed, `PointPaused` if it stopped
        resumable, or `PointFailure` if it could not run or failed.
    """
    # Imported here: the artifact writer pulls in matplotlib, which a plain
    # `import fim.sweep_run` should not.
    from fim.persistence.artifacts import write_run_artifacts  # noqa: PLC0415
    from fim.run_service import Session, execute_run_object  # noqa: PLC0415

    pause_requested = False

    def observe(session: Session) -> None:
        nonlocal pause_requested
        if cancel_event.is_set() and not pause_requested:
            pause_requested = True
            current = read_run_object(object_id, results=results)
            if current.record.state is RunState.RUNNING:
                request_pause(
                    object_id,
                    expected_revision=current.record.revision,
                    results=results,
                )
        if on_message is not None:
            on_message(("progress", session.generation))

    def write(result: Any, directory: Path) -> None:
        write_run_artifacts(
            result,
            directory,
            pairwise_max_demes=pairwise_max_demes,
        )

    try:
        finished = execute_run_object(
            object_id, write_artifacts=write, results=results, observer=observe
        )
    except _POINT_ERRORS as error:
        logger.warning("sweep point object %s failed: %s", object_id, error)
        return PointFailure(str(error))
    state = finished.record.state
    if state is RunState.COMPLETED:
        return finished.directory
    if state in (RunState.PAUSED, RunState.CANCELLED):
        return PointPaused(state.value)
    return PointFailure(f"the point's run object is {state.value}")


# What a point's execution can raise once the object has been opened; the
# lifecycle service has already recorded the object as failed by then.
_POINT_ERRORS: Final = (
    LifecycleError,
    ArithmeticError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


def _run_object_in_process(
    object_id: str,
    results_directory: str,
    pairwise_max_demes: int,
    cancel_event: Any,
) -> tuple[str, str]:
    """Execute one point's Run object in a worker process.

    Returns `("ok", directory)`, `("paused", state)`, or `("failure",
    reason)`. The results directory is set explicitly because a spawned
    process starts without the parent's location overrides.
    """
    paths.set_results_directory_override(Path(results_directory))
    result = execute_point_object(
        object_id, cancel_event, None, pairwise_max_demes=pairwise_max_demes
    )
    if isinstance(result, PointFailure):
        return "failure", result.reason
    if isinstance(result, PointPaused):
        return "paused", result.state
    return "ok", str(result)


def _run_point_in_process(
    params: SimulationParams,
    results_directory: str,
    max_workers: int | None,
    cancel_event: Any,
) -> tuple[str, str]:
    """Run one point in a worker process; the entry point `ProcessPointRunner` submits.

    Returns `("ok", directory)` or `("failure", reason)`. The results
    directory is set explicitly because a spawned process starts without
    the parent's location overrides.
    """
    paths.set_results_directory_override(Path(results_directory))
    result = LocalPointRunner().run_point(params, cancel_event, None, max_workers)
    if isinstance(result, PointFailure):
        return "failure", result.reason
    return "ok", str(result)


class ProcessPointRunner:
    """Runs each point in a worker process, so points really run in parallel.

    A run inside one process is limited by the interpreter lock for the
    engines that step demes and replicates in Python (the generational
    engines), which a batch of threads cannot get past: measured, four such
    points took 45 s one at a time, 18 s on four threads and 10 s in four
    processes. A lineal batch already uses processes for its replicates and
    runs the same in either. Each worker process runs `LocalPointRunner` on
    one point at a time, so a lineal batch inside it starts its own workers.

    A cancel reaches the worker through a shared event, and a worker that
    dies is reported as a failed point, not a hung sweep.

    Args:
        processes: How many points can run at once.
        max_workers: Default worker processes for a lineal batch point.
        pairwise_max_demes: Largest deme count for which each point saves
            every deme pair's Nei identities (`pairwise.json`).
    """

    def __init__(
        self,
        processes: int,
        max_workers: int | None = None,
        *,
        pairwise_max_demes: int = DEFAULT_PAIRWISE_MAX_DEMES,
    ) -> None:
        context = multiprocessing.get_context("spawn")
        self._manager = context.Manager()
        self._executor = ProcessPoolExecutor(processes, mp_context=context)
        self.max_workers = max_workers
        self.pairwise_max_demes = pairwise_max_demes

    def run_point(
        self,
        params: SimulationParams,
        cancel_event: threading.Event,
        on_message: Callable[[object], None] | None = None,
        max_workers: int | None = None,
    ) -> Path | PointFailure:
        """Run `params` in a worker process; see `PointRunner.run_point`."""
        del on_message  # progress inside a point does not cross processes
        shared_cancel = self._manager.Event()
        future = self._executor.submit(
            _run_point_in_process,
            params,
            str(paths.results_directory()),
            max_workers if max_workers is not None else self.max_workers,
            shared_cancel,
        )
        while True:
            try:
                kind, value = future.result(timeout=0.25)
            except FutureTimeoutError:
                if cancel_event.is_set():
                    shared_cancel.set()
                continue
            except BrokenProcessPool:
                return PointFailure("a worker process ended unexpectedly")
            return Path(value) if kind == "ok" else PointFailure(value)

    def run_object_point(
        self,
        object_id: str,
        cancel_event: threading.Event,
        on_message: Callable[[object], None] | None = None,
    ) -> Path | PointFailure | PointPaused:
        """Execute a point's Run object in a worker process."""
        del on_message  # progress inside a point does not cross processes
        shared_cancel = self._manager.Event()
        future = self._executor.submit(
            _run_object_in_process,
            object_id,
            str(paths.results_directory()),
            self.pairwise_max_demes,
            shared_cancel,
        )
        while True:
            try:
                kind, value = future.result(timeout=0.25)
            except FutureTimeoutError:
                if cancel_event.is_set():
                    shared_cancel.set()
                continue
            except BrokenProcessPool:
                return PointFailure("a worker process ended unexpectedly")
            if kind == "ok":
                return Path(value)
            return PointPaused(value) if kind == "paused" else PointFailure(value)

    def close(self) -> None:
        """Stop the worker processes and the shared-event server."""
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._manager.shutdown()


@dataclass(frozen=True, slots=True)
class Concurrency:
    """How a sweep spreads its points over the machine.

    Attributes:
        points_at_once: Points running at the same time.
        workers_per_point: Worker processes each lineal batch point uses, or
            `None` where a point's own default applies (a single run, or a
            non-lineal engine).
        cores: The core count the split was made from.
    """

    points_at_once: int
    workers_per_point: int | None
    cores: int


def resolve_concurrency(
    *,
    points: int,
    params: SimulationParams,
    requested: int | None = None,
    max_workers: int | None = None,
    cores: int | None = None,
    in_process_batches: bool = False,
) -> Concurrency:
    """Choose how many points run at once, and each point's workers.

    A batch already spreads its replicates over cores, so points at once
    times workers per point should not exceed the core count. "Auto"
    (`requested=None`) fills the machine: a single run, or a batch on any
    engine but lineal, needs one core, so up to one point per core; a batch
    of `r` replicates on the lineal engine needs about `min(r, cores)`, so
    `cores // that` points. `max_workers`, when the user set it, is each
    point's worker count and shrinks how many fit.

    Args:
        points: How many points there are to run (caps the answer).
        params: One point's configuration (replicates and engine are the
            same across a sweep's points).
        requested: Points at once the user asked for, or `None` for auto.
        max_workers: The user's per-batch worker limit, if any.
        cores: Core count; the machine's own when `None`.
        in_process_batches: Points run as Run objects, whose batch session
            steps every replicate inside one process on any engine, so each
            point needs one core.
    """
    available = cores if cores is not None else (os.cpu_count() or 1)
    lineal = params.engine_backend == "lineal" and not in_process_batches
    # Only a lineal batch uses more than one core per point: it spreads its
    # replicates over worker processes. Every other engine (the generational
    # ones, and "auto", which never picks lineal) steps a whole batch inside
    # one process, and measured at 0.7 to 0.9 cores each.
    if params.n_replicates > 1 and lineal:
        demand = min(params.n_replicates, max_workers or available)
    else:
        demand = 1
    demand = max(1, demand)
    at_once = requested if requested is not None else max(1, available // demand)
    at_once = max(1, min(at_once, max(points, 1)))
    workers: int | None = None
    if params.n_replicates > 1 and lineal:
        workers = max_workers or max(1, min(params.n_replicates, available // at_once))
    return Concurrency(at_once, workers, available)


def create_sweep_study(
    spec: SweepSpec,
    plan: SweepPlan,
    name: str,
    *,
    description: str | None = None,
    experiment_id: str | None = None,
    results: Path | None = None,
) -> StudyManifest:
    """Create the Study for a sweep, its spec and plan stored before any run.

    Args:
        spec: The sweep specification.
        plan: Its enumerated plan; only the valid points are stored.
        name: The Study name.
        description: Optional longer description.
        experiment_id: Add the Study to this Experiment, if given.
        results: Optional results-directory override.

    Returns:
        The new Study, with `sweep_spec` holding the specification and the
        planned points.

    Raises:
        ValueError: The plan has no valid points, or the Experiment does
            not exist.
    """
    if not plan.points:
        raise ValueError("the sweep has no valid points to run")
    stored = {**spec.to_dict(), "points": [point.to_dict() for point in plan.points]}
    study = groups.create_study(name, description, results=results, sweep_spec=stored)
    if experiment_id is not None:
        try:
            groups.add_study_to_experiment(
                experiment_id, study.study_id, results=results
            )
        except ValueError:
            groups.delete_study(study.study_id, results=results)
            raise
    return study


def attach_sweep_to_study(
    study_id: str,
    spec: SweepSpec,
    plan: SweepPlan,
    *,
    results: Path | None = None,
) -> StudyManifest:
    """Make an existing Study the home of a sweep by storing its spec and plan.

    The Study keeps any Runs it already has; a planned point whose run is
    already a member counts as done. A Study holds at most one sweep.

    Raises:
        ValueError: The plan has no valid points, the Study does not exist,
            or it already holds a sweep.
        ClosedGroupError: The Study is closed.
    """
    if not plan.points:
        raise ValueError("the sweep has no valid points to run")
    study = groups.get_study(study_id, results=results)
    groups.refuse_closed(study, "given a sweep")
    if study.sweep_spec is not None:
        raise ValueError(
            f"study {study.name!r} already holds a sweep; choose another study"
        )
    stored = {**spec.to_dict(), "points": [point.to_dict() for point in plan.points]}
    updated = replace(study, sweep_spec=stored)
    groups.write_study_manifest(
        groups.study_manifest_path(study_id, results=results), updated
    )
    return updated


def sweep_spec_of(study: StudyManifest) -> SweepSpec:
    """Rebuild the `SweepSpec` a sweep Study stored.

    Raises:
        ValueError: The Study is not a sweep, or its spec is malformed.
    """
    if study.sweep_spec is None:
        raise ValueError(f"study {study.study_id} is not a sweep")
    return SweepSpec.from_dict(study.sweep_spec)


def stored_points(study: StudyManifest) -> list[Mapping[str, Any]]:
    """Return the planned points stored in a sweep Study.

    Raises:
        ValueError: The Study is not a sweep, or its points are malformed.
    """
    if study.sweep_spec is None:
        raise ValueError(f"study {study.study_id} is not a sweep")
    points = study.sweep_spec.get("points")
    if not isinstance(points, list) or not all(
        isinstance(entry, Mapping) and "run_id" in entry and "index" in entry
        for entry in points
    ):
        raise ValueError("the sweep Study's stored points are malformed")
    return points


def sweep_point_statuses(
    study: StudyManifest,
    *,
    software_version: str = __version__,
    results: Path | None = None,
) -> list[PointStatus]:
    """Return every planned point with its derived state.

    `done` if a member run of the Study has the point's `run_id` and was
    made by this `software_version`. Otherwise the linked Run object's
    lifecycle state when it is `planned`, `running`, `paused`,
    `interrupted`, or `failed` (an active object whose owner is gone reads
    `interrupted`); then `stale` if the only member was made by another
    version (it is recomputed, and compared, on the next run); `failed` if
    a failure was recorded before any object existed; otherwise `waiting`.
    A completed or cancelled object that is not a member yet is never
    reported as done.
    """
    current, stale = _member_runs(study, software_version, results)
    failures = read_failures(study.study_id, results=results)
    objects = read_point_objects(study.study_id, results=results)
    statuses = []
    for entry in stored_points(study):
        run_id = str(entry["run_id"])
        failure = failures.get(run_id)
        linked = _linked_object(objects.get(run_id), results)
        linked_state = _linked_point_state(linked, results)
        reason: str | None = None
        if run_id in current:
            state: PointState = "done"
        elif linked is not None and linked_state is not None:
            state = linked_state
            error = linked.record.last_error
            if state == "failed" and error is not None:
                reason = error.message
        elif run_id in stale:
            state = "stale"
        elif failure is not None:
            state = "failed"
            reason = str(failure["reason"])
        else:
            state = "waiting"
        statuses.append(
            PointStatus(
                index=int(entry["index"]),
                coordinates=dict(entry.get("coordinates", {})),
                run_id=run_id,
                state=state,
                reason=reason,
                object_id=linked.object_id if linked is not None else None,
            )
        )
    return statuses


def _linked_object(object_id: str | None, results: Path | None) -> RunObject | None:
    """Read a point's linked Run object; `None` if unlinked or unreadable."""
    if object_id is None:
        return None
    try:
        return read_run_object(object_id, results=results)
    except (LifecycleError, OSError, ValueError) as error:
        logger.debug("ignoring unreadable point object %s: %s", object_id, error)
        return None


def _linked_point_state(
    linked: RunObject | None, results: Path | None
) -> PointState | None:
    """Map a linked object's lifecycle state to a point state, if it has one.

    Read-only: an active object whose attempt lock is free has lost its
    owner, so it reads `interrupted` without being reconciled here.
    """
    if linked is None:
        return None
    state = linked.record.state
    if state in ACTIVE_STATES and not is_lock_held(
        attempt_lock_path(linked.object_id, results)
    ):
        return "interrupted"
    return _LINKED_STATES.get(state)


def _completed_objects(software_version: str, results: Path | None) -> dict[str, Path]:
    """Return each completed Run object's directory by `run_id`.

    Only a `completed` lifecycle record whose manifest names this
    `software_version` counts: a directory or a manifest alone is never
    proof that a run finished.
    """
    found: dict[str, Path] = {}
    for run_object in list_run_objects(results).objects:
        record = run_object.record
        if record.state is not RunState.COMPLETED or record.scientific_run_id is None:
            continue
        identity = groups.run_identity_of(run_object.directory)
        if identity == (record.scientific_run_id, software_version):
            found.setdefault(record.scientific_run_id, run_object.directory)
    return found


def _member_runs(
    study: StudyManifest, software_version: str, results: Path | None
) -> tuple[dict[str, Path], dict[str, Path]]:
    """Split a Study's member runs by id: made by this version, or by another."""
    current: dict[str, Path] = {}
    stale: dict[str, Path] = {}
    for directory in groups.study_run_directories(study, results=results):
        identity = groups.run_identity_of(directory)
        if identity is None:
            continue
        run_id, version = identity
        if version == software_version:
            current[run_id] = directory
        else:
            stale.setdefault(run_id, directory)
    return current, stale


@dataclass(slots=True)
class _Context:
    """What every point of one `run_sweep` call shares."""

    study_id: str
    runner: PointRunner
    on_event: Callable[[SweepEvent], None]
    cancel_event: threading.Event
    software_version: str
    results: Path | None
    total: int
    workers_per_point: int | None
    # Guards the Study manifest, the failure file, and the finished count,
    # each a read-modify-write that several points in flight would race on.
    lock: threading.Lock
    finished: int = 0
    # Name each point's run after the Study (`_name_point_run`).
    study_name: str = ""
    spec: SweepSpec | None = None
    # Each planned point's 1-based place in the plan, by `run_id`.
    positions: Mapping[str, int] | None = None
    # Points run as resumable Run objects (`ObjectPointRunner`).
    uses_objects: bool = False
    # Completed Run objects of this software version, by `run_id`.
    completed_objects: Mapping[str, Path] | None = None
    # Points recomputed on purpose (`run_sweep(rerun=True)`): their existing
    # runs are not reused, only compared with the result.
    rerun_ids: frozenset[str] = frozenset()

    def emit(
        self, kind: EventKind, point: SweepPoint, detail: object = None, *, finish: bool
    ) -> None:
        """Send one event; a finishing event advances the finished count."""
        with self.lock:
            if finish:
                self.finished += 1
            event = SweepEvent(
                kind, point.index, point.run_id, self.finished, self.total, detail
            )
            self.on_event(event)


def run_sweep(
    study_id: str,
    runner: PointRunner,
    on_event: Callable[[SweepEvent], None],
    cancel_event: threading.Event,
    *,
    retry_failed: bool = False,
    rerun: bool = False,
    software_version: str = __version__,
    points_at_once: int | None = None,
    max_workers: int | None = None,
    results: Path | None = None,
) -> SweepOutcome:
    """Run every point of a sweep Study that is not already present.

    Points that need running are started in grid order, several at once
    (`resolve_concurrency`: by default as many as fill the machine, since a
    single run uses one core and a batch only as many as its replicates).
    A failed point is recorded and the sweep continues. Cancelling stops
    every point in flight and starts no more; completed points stay
    attached. Results do not depend on how many run at once: each point is
    a pure function of its configuration.

    With an `ObjectPointRunner` (both runners here), each point runs as a
    Run object created when the point is first dispatched and linked to it
    in the status file. Cancelling pauses the objects in flight; the next
    call resumes a paused or interrupted object, and a failed one only
    with `retry_failed`. A configuration a resumable session cannot run
    falls back to the runner's `run_point`.

    Args:
        study_id: A sweep Study created by `create_sweep_study`.
        runner: Runs one point. Must be safe to call from several threads.
        on_event: Called with every `SweepEvent`, one at a time. A point's
            `position` is how many points have finished, not its index.
        cancel_event: Set to stop the sweep.
        retry_failed: Also retry points recorded as failed; by default
            they are skipped.
        rerun: Also recompute every point already completed under
            `software_version`, from the stored specification, and compare
            each with its existing run. A result that matches bit for bit
            replaces the existing run (every Study that held it now holds the
            new one); a difference keeps both and is reported, never silent,
            like a recompute after a version change. Points that are missing
            or failed follow the usual rules.
        software_version: The version whose runs count as done. A point whose
            only run was made by another version is recomputed and compared
            with it; a difference is reported, never silent.
        points_at_once: How many points to run at the same time, or `None`
            for automatic.
        max_workers: Worker processes for each batch point, or `None`.
        results: Optional results-directory override.

    Returns:
        The tally.

    Raises:
        ValueError: The Study is not a sweep, or the stored plan no longer
            matches what this version of the program enumerates.
        ClosedGroupError: The Study is closed; reopen it to continue.
    """
    study = groups.get_study(study_id, results=results)
    groups.refuse_closed(study, "given more sweep runs")
    spec = sweep_spec_of(study)
    planned = _matching_plan(study, enumerate_points(spec))
    failures = read_failures(study_id, results=results)
    objects = read_point_objects(study_id, results=results)
    present, stale = _member_runs(study, software_version, results)
    # Completed points to recompute: each is compared with the run it has now.
    rerun_ids = (
        frozenset(point.run_id for point in planned if point.run_id in present)
        if rerun
        else frozenset()
    )
    older_runs = {**stale, **{run_id: present[run_id] for run_id in rerun_ids}}
    tally = dict.fromkeys(
        (
            "done",
            "reused",
            "already_present",
            "failed",
            "skipped",
            "recomputed",
            "differs",
        ),
        0,
    )
    todo: list[SweepPoint] = []
    for point in planned:
        if point.run_id in rerun_ids:
            todo.append(point)
        elif point.run_id in present:
            tally["already_present"] += 1
        elif _is_skipped_failure(
            point.run_id, failures, objects, results, retry_failed=retry_failed
        ):
            tally["skipped"] += 1
        else:
            todo.append(point)
    first_params = SimulationParams.from_mapping(planned[0].params)
    uses_objects = isinstance(runner, ObjectPointRunner) and _object_supported(
        first_params
    )
    concurrency = resolve_concurrency(
        points=len(todo),
        params=first_params,
        requested=points_at_once,
        max_workers=max_workers,
        in_process_batches=uses_objects,
    )
    process_runner: ProcessPointRunner | None = None
    effective_runner: PointRunner = runner
    if concurrency.points_at_once > 1 and isinstance(runner, LocalPointRunner):
        process_runner = ProcessPointRunner(
            concurrency.points_at_once,
            runner.max_workers,
            pairwise_max_demes=runner.pairwise_max_demes,
        )
        effective_runner = process_runner
    context = _Context(
        study_id,
        effective_runner,
        on_event,
        cancel_event,
        software_version,
        results,
        len(planned),
        concurrency.workers_per_point,
        threading.Lock(),
        finished=tally["already_present"] + tally["skipped"],
        study_name=study.name,
        spec=spec,
        positions={point.run_id: place for place, point in enumerate(planned, 1)},
        uses_objects=uses_objects,
        completed_objects=(
            _completed_objects(software_version, results) if uses_objects else {}
        ),
        rerun_ids=rerun_ids,
    )
    cancelled = False

    def work(point: SweepPoint) -> str:
        if cancel_event.is_set():
            return "cancelled"
        return _run_one_point(context, point, older_runs.get(point.run_id))

    try:
        if todo:
            with ThreadPoolExecutor(max_workers=concurrency.points_at_once) as pool:
                for outcome in pool.map(work, todo):
                    if outcome == "cancelled":
                        cancelled = True
                    else:
                        tally[outcome] += 1
    finally:
        if process_runner is not None:
            process_runner.close()
    cancelled = cancelled or (cancel_event.is_set() and bool(todo))
    on_event(
        SweepEvent(
            "sweep_cancelled" if cancelled else "sweep_done",
            -1,
            "",
            context.finished,
            len(planned),
        )
    )
    return SweepOutcome(
        done=tally["done"],
        reused=tally["reused"],
        already_present=tally["already_present"],
        failed=tally["failed"],
        skipped_failed=tally["skipped"],
        cancelled=cancelled,
        recomputed=tally["recomputed"],
        differing=tally["differs"],
    )


def failures_path(study_id: str, *, results: Path | None = None) -> Path:
    """Return the file recording a sweep Study's failed points.

    Kept in a subdirectory of the Study index so the index's own
    `*.json` scan never mistakes it for a Study manifest.
    """
    return paths.studies_directory(results) / "sweep-status" / f"{study_id}.json"


def read_failures(
    study_id: str, *, results: Path | None = None
) -> dict[str, dict[str, Any]]:
    """Return recorded failures by point `run_id`; empty if none or unreadable."""
    failures = _read_status(study_id, results).get("failures")
    return dict(failures) if isinstance(failures, dict) else {}


def read_point_objects(study_id: str, *, results: Path | None = None) -> dict[str, str]:
    """Return each point's linked Run object ID by point `run_id`."""
    objects = _read_status(study_id, results).get("objects")
    if not isinstance(objects, dict):
        return {}
    return {
        str(run_id): object_id
        for run_id, object_id in objects.items()
        if isinstance(object_id, str)
    }


def _read_status(study_id: str, results: Path | None) -> dict[str, Any]:
    """Return the sweep status file's contents; empty if none or unreadable."""
    path = failures_path(study_id, results=results)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _record_failure(
    study_id: str,
    point: SweepPoint,
    reason: str,
    results: Path | None,
) -> None:
    """Persist one failed point so 'failed' survives a restart."""
    failures = read_failures(study_id, results=results)
    failures[point.run_id] = {
        "index": point.index,
        "reason": reason,
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _write_failures(study_id, failures, results)


def _clear_failure(study_id: str, run_id: str, results: Path | None) -> None:
    """Forget a recorded failure once the point has a run."""
    failures = read_failures(study_id, results=results)
    if failures.pop(run_id, None) is not None:
        _write_failures(study_id, failures, results)


def _write_failures(
    study_id: str, failures: Mapping[str, Any], results: Path | None
) -> None:
    """Replace the failure record, keeping the object links."""
    _write_status(study_id, results, "failures", failures)


def _link_object(
    study_id: str, run_id: str, object_id: str, results: Path | None
) -> None:
    """Record that `object_id` is the Run object executing point `run_id`."""
    objects = read_point_objects(study_id, results=results)
    objects[run_id] = object_id
    _write_status(study_id, results, "objects", objects)


def _write_status(
    study_id: str, results: Path | None, key: str, value: Mapping[str, Any]
) -> None:
    """Replace one section of the status file atomically, keeping the others."""
    payload = _read_status(study_id, results)
    payload[key] = dict(value)
    path = failures_path(study_id, results=results)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    paths.replace_with_retry(temporary, path)


def _matching_plan(study: StudyManifest, plan: SweepPlan) -> tuple[SweepPoint, ...]:
    """Return the enumerated points, after checking they match the stored plan."""
    stored = [str(entry["run_id"]) for entry in stored_points(study)]
    current = [point.run_id for point in plan.points]
    if stored != current:
        raise ValueError(
            "the stored sweep plan no longer matches this version of the "
            "program (run ids differ); create the sweep again"
        )
    return plan.points


def _is_skipped_failure(
    run_id: str,
    failures: Mapping[str, Any],
    objects: Mapping[str, str],
    results: Path | None,
    *,
    retry_failed: bool,
) -> bool:
    """Return whether a failed point is left alone because of no retry.

    A linked object that failed is skipped like a recorded failure. One
    that is planned, paused, or interrupted is continued even when an
    earlier failure was recorded before it existed.
    """
    if retry_failed:
        return False
    linked = _linked_object(objects.get(run_id), results)
    if linked is not None and linked.record.state in _CONTINUABLE_STATES:
        return linked.record.state is RunState.FAILED
    return run_id in failures


def _object_supported(params: SimulationParams) -> bool:
    """Return whether a resumable session can run `params`."""
    try:
        check_run_support(params)
    except UnsupportedSessionError as error:
        logger.debug("sweep point runs without a run object: %s", error)
        return False
    return True


def _point_object(context: _Context, point: SweepPoint) -> str:
    """Return the Run object for a point, creating and linking it if needed.

    A linked object that can still continue (planned, active, paused,
    interrupted, or failed) is reused, so its checkpoints are kept. One
    that completed under another version, was cancelled, or is gone is
    replaced by a new object.

    Raises:
        ValueError: The new object's run ID differs from the point's.
    """
    with context.lock:
        linked = _linked_object(
            read_point_objects(context.study_id, results=context.results).get(
                point.run_id
            ),
            context.results,
        )
        if linked is not None and linked.record.state in _CONTINUABLE_STATES:
            return linked.object_id
        # A JSON round trip turns the point's mapping into plain values
        # that the YAML dumper accepts.
        document = json.loads(json.dumps(dict(point.params)))
        created = create_run_object(
            yaml.safe_dump(document, sort_keys=True), results=context.results
        )
        if created.record.scientific_run_id != point.run_id:
            raise ValueError(
                f"the run object for point {point.run_id} has run ID "
                f"{created.record.scientific_run_id}"
            )
        _link_object(context.study_id, point.run_id, created.object_id, context.results)
        return created.object_id


def _dispatch_point(
    context: _Context, point: SweepPoint, progress: Callable[[object], None]
) -> tuple[Path | PointFailure | PointPaused, str | None]:
    """Run one point and return its outcome with its Run object ID, if any."""
    params = SimulationParams.from_mapping(point.params)
    runner = context.runner
    if (
        context.uses_objects
        and isinstance(runner, ObjectPointRunner)
        and _object_supported(params)
    ):
        try:
            object_id = _point_object(context, point)
        except (LifecycleError, OSError, ValueError) as error:
            return PointFailure(str(error)), None
        return runner.run_object_point(object_id, context.cancel_event, progress), (
            object_id
        )
    outcome = runner.run_point(
        params, context.cancel_event, progress, context.workers_per_point
    )
    return outcome, None


def _run_one_point(
    context: _Context, point: SweepPoint, stale_member: Path | None
) -> Literal["done", "reused", "recomputed", "differs", "failed", "cancelled"]:
    """Attach, reuse or compute one point; compare it with an older version's run."""
    reusable = groups.find_run_directories(
        point.run_id,
        software_version=context.software_version,
        results=context.results,
    )
    completed = (context.completed_objects or {}).get(point.run_id)
    if completed is not None:
        reusable.append(completed)
    if point.run_id in context.rerun_ids:
        # Recomputing on purpose: the run it already has is the thing to
        # compare with, not a result to reuse.
        reusable = []
    reused = bool(reusable)
    object_id: str | None = None
    if reused:
        result: Path | PointFailure | PointPaused = reusable[0]
    else:
        context.emit("point_started", point, finish=False)

        def progress(message: object) -> None:
            context.emit("point_progress", point, message, finish=False)

        result, object_id = _dispatch_point(context, point, progress)
    if isinstance(result, PointPaused):
        return "cancelled"
    if isinstance(result, PointFailure):
        if context.cancel_event.is_set():
            return "cancelled"
        if object_id is None:
            # A Run object records its own failure; only a failure before
            # one existed needs the sweep's own record.
            with context.lock:
                _record_failure(context.study_id, point, result.reason, context.results)
        context.emit("point_failed", point, result.reason, finish=True)
        return "failed"
    with context.lock:
        groups.add_run_to_study(context.study_id, result, results=context.results)
        _clear_failure(context.study_id, point.run_id, context.results)
    _name_point_run(context, point, result)
    older = stale_member or next(
        iter(
            groups.find_stale_run_directories(
                point.run_id, context.software_version, results=context.results
            )
        ),
        None,
    )
    if older is not None and older.resolve() != result.resolve():
        return _compare_with_older(context, point, older, result)
    context.emit("point_reused" if reused else "point_done", point, finish=True)
    return "reused" if reused else "done"


def _name_point_run(context: _Context, point: SweepPoint, directory: Path) -> None:
    """Give a sweep point's run a name and description, unless it has a name.

    A run without a name is unrecognizable among hundreds of siblings,
    so each point's run is named after its Study and its coordinates
    (`fim.sweep.point_run_name`). A reused run that someone already
    named keeps that name. Naming is best effort: a failure is logged,
    never fatal to the sweep, since the run itself succeeded.

    A read-only example run (one linked into the sweep Study by hand)
    keeps its shipped name: `replace_run_metadata` refuses it with
    `ReadOnlyError`, which is expected, so it is logged at debug level
    rather than as a warning.
    """
    if context.spec is None:
        return
    metadata_path = run_metadata_path(directory)
    if metadata_path.is_file():
        try:
            if read_run_metadata(metadata_path).name is not None:
                return
        except (OSError, ValueError) as error:
            logger.debug("replacing unreadable run metadata %s: %s", directory, error)
    positions = context.positions or {}
    try:
        replace_run_metadata(
            directory,
            name=point_run_name(context.study_name, point),
            description=point_run_description(
                context.study_name,
                context.spec,
                point,
                positions.get(point.run_id, point.index + 1),
                context.total,
            ),
        )
    except groups.ReadOnlyError as error:
        logger.debug("keeping the read-only run's own name %s: %s", directory, error)
    except OSError as error:
        logger.warning("could not name sweep run %s: %s", directory, error)


def _compare_with_older(
    context: _Context, point: SweepPoint, older: Path, recomputed: Path
) -> Literal["recomputed", "differs"]:
    """Compare a recomputed point with the run another version made.

    A match means the old run is an exact duplicate: every Study that held it
    now holds the recomputed one and the old directory goes. A mismatch keeps
    both linked, records the difference beside the new run, and reports it.
    """
    comparison = compare_runs(older, recomputed)
    if comparison.identical:
        with context.lock:
            try:
                groups.supersede_run(older, recomputed, results=context.results)
            except groups.ReadOnlyError as error:
                # A read-only example is never deleted or relinked; the
                # matching recomputed run simply lives beside it.
                logger.info("kept read-only run %s: %s", older, error)
        context.emit("point_recomputed", point, comparison.to_dict(), finish=True)
        return "recomputed"
    write_reproducibility_note(recomputed, comparison.to_dict())
    context.emit("point_differs", point, comparison.to_dict(), finish=True)
    return "differs"


def write_reproducibility_note(directory: Path, comparison: Mapping[str, Any]) -> None:
    """Record, beside a recomputed run, that it differs from an older version's.

    A sidecar file, deliberately outside the manifest's artifact digests, so
    the run's own record of itself is untouched.
    """
    (directory / "reproducibility.json").write_text(
        json.dumps(comparison, indent=2), encoding="utf-8"
    )


@dataclass(frozen=True, slots=True)
class PointResult:
    """One finished point's statistics, for the across-points views.

    Attributes:
        index: The point's grid index.
        coordinates: The varied values, by axis key.
        run_id: The point's id.
        directory: The point's run directory.
        n_replicates: Replicates the run has (1 for a scalar run).
        statistics: Each statistic as `{"mean": ..., "low": ..., "high": ...}`;
            `low` and `high` are `None` for a single run, which has no
            across-replicate interval.
    """

    index: int
    coordinates: Mapping[str, Any]
    run_id: str
    directory: Path
    n_replicates: int
    statistics: Mapping[str, Mapping[str, float | None]]


def sweep_point_results(
    study: StudyManifest, *, results: Path | None = None
) -> list[PointResult]:
    """Read the final statistics of every finished point, in grid order.

    A batch point reads its `summary.json` (mean and confidence interval
    across replicates); a scalar point reads its `report.json`. A point
    whose files cannot be read is left out, like a missing run directory
    elsewhere in the Study code. Nothing is stored: the aggregate is
    computed each time from small JSON files.
    """
    current, stale = _member_runs(study, __version__, results)
    by_id = {**stale, **current}
    found: list[PointResult] = []
    for entry in stored_points(study):
        directory = by_id.get(str(entry["run_id"]))
        if directory is None:
            continue
        statistics = _read_point_statistics(directory)
        if statistics is None:
            continue
        found.append(
            PointResult(
                index=int(entry["index"]),
                coordinates=dict(entry.get("coordinates", {})),
                run_id=str(entry["run_id"]),
                directory=directory,
                n_replicates=statistics[0],
                statistics=statistics[1],
            )
        )
    return found


def _read_point_statistics(
    directory: Path,
) -> tuple[int, dict[str, dict[str, float | None]]] | None:
    """Return `(replicates, statistics)` from a run directory, or `None`."""
    summary = directory / "summary.json"
    report = directory / "report.json"
    try:
        if summary.is_file():
            payload = json.loads(summary.read_text(encoding="utf-8"))
            return _batch_statistics(payload)
        if report.is_file():
            payload = json.loads(report.read_text(encoding="utf-8"))
            return 1, {
                name: {"mean": float(value), "low": None, "high": None}
                for name, value in payload.items()
                if isinstance(value, int | float)
                and not isinstance(value, bool)
                and name != "generation"
            }
    except (OSError, ValueError, TypeError):
        return None
    return None


def _batch_statistics(
    payload: object,
) -> tuple[int, dict[str, dict[str, float | None]]] | None:
    """Reduce a batch `summary.json` to mean and interval per statistic."""
    if not isinstance(payload, dict):
        return None
    statistics: dict[str, dict[str, float | None]] = {}
    replicates = 0
    for name, entry in payload.items():
        if isinstance(entry, dict) and "mean" in entry:
            statistics[name] = {
                "mean": float(entry["mean"]),
                "low": float(entry["low"]) if "low" in entry else None,
                "high": float(entry["high"]) if "high" in entry else None,
            }
            replicates = max(replicates, int(entry.get("sample_count", 0)))
    return (replicates, statistics) if statistics else None
