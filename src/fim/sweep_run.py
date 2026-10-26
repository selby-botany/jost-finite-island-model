"""Create, execute, resume, and inspect parameter-sweep Studies.

Sweep specifications and plans are stored with a Study before execution.
Completed points are identified from their run IDs; pending points can be
resumed, and point failures and Run object links are stored separately.
Execution is sequential across sweep points; batch replicates may use their
own worker processes.
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import os
import queue
import shutil
import threading
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from concurrent.futures import Executor, Future, ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal, Protocol, runtime_checkable

import yaml

from fim import __version__, paths
from fim.config.defaults import DEFAULT_PAIRWISE_MAX_DEMES
from fim.errors import StoreError
from fim.execution import UnsupportedSessionError
from fim.execution_priority import Priority, initialize_pool_worker
from fim.model.params import SimulationParams
from fim.persistence import groups
from fim.persistence.groups import StudyManifest
from fim.persistence.object_lock import (
    LockUnavailableError,
    exclusive_lock,
    is_lock_held,
)
from fim.persistence.recording import run_satisfies
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
from fim.sweep_cost import format_bytes, format_duration, order_costliest_first
from fim.sweep_table import (
    HealthCache,
    PointRow,
    StatisticCell,
    read_point_health,
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
    "sweep_budget_reached",
]


class SweepLockedError(StoreError, ValueError):
    """Another process is already running this sweep.

    A `ValueError` subclass so the command line and the app report it as
    a plain error, like any other reason a sweep cannot start.
    """


FailureClass = Literal["transient", "deterministic"]
"""Whether trying a failed point again can help: a `transient` failure (a
worker process that died, a file system error) can pass; a `deterministic`
one (the engine rejected the configuration) happens every time."""

_FAILURE_CLASSES: Final[tuple[tuple[type[BaseException], FailureClass], ...]] = (
    (BrokenProcessPool, "transient"),
    (OSError, "transient"),
    (Exception, "deterministic"),
)
"""How `classify_failure` sorts an exception: the first row it is an instance
of decides, so a more specific type goes above a broader one."""


def classify_failure(error: BaseException) -> FailureClass:
    """Say whether a point that failed with `error` is worth trying again.

    Args:
        error: What the point raised.

    Returns:
        The class of the first row of the classification table that
        `error` matches; `"deterministic"` if none does.
    """
    for kind, failure_class in _FAILURE_CLASSES:
        if isinstance(error, kind):
            return failure_class
    return "deterministic"


@dataclass(frozen=True, slots=True)
class PointFailure:
    """Why one point produced no run.

    Attributes:
        reason: What went wrong, in words for the person running the sweep.
        failure_class: Whether trying the point again can help.
        attempts: How many times the point was tried before this failure
            was returned.
    """

    reason: str
    failure_class: FailureClass = "deterministic"
    attempts: int = 1


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
    """The tally `run_sweep` returns.

    `budget_reached` says which budget stopped the sweep (in words), or is
    `None`; a sweep stopped by its budget is not `cancelled`, and what it did
    not start stays waiting, to be resumed.
    """

    done: int
    reused: int
    already_present: int
    failed: int
    skipped_failed: int
    cancelled: bool
    recomputed: int = 0
    differing: int = 0
    budget_reached: str | None = None


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
            output_directory, reservation = paths.allocate_output_directory()
        except FileExistsError as error:
            return PointFailure(str(error), classify_failure(error))
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
        return PointFailure(str(error), classify_failure(error))
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


def _failure_kind(failure: PointFailure) -> str:
    """Name a failure's kind in a worker's `(kind, value)` answer."""
    return "transient" if failure.failure_class == "transient" else "failure"


def _point_failure(kind: str, value: str) -> PointFailure:
    """Rebuild a `PointFailure` from a worker's `(kind, value)` answer."""
    return PointFailure(value, "transient" if kind == "transient" else "deterministic")


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
        return _failure_kind(result), result.reason
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
        return _failure_kind(result), result.reason
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

    A cancel reaches the worker through a shared event. A worker that dies
    breaks the whole pool: every point in flight is reported as failed, and
    the pool is replaced so the points that follow still run, instead of
    the sweep ending on the first submit to a dead pool.

    Args:
        processes: How many points can run at once.
        max_workers: Default worker processes for a lineal batch point.
        pairwise_max_demes: Largest deme count for which each point saves
            every deme pair's Nei identities (`pairwise.json`).
        executor_factory: Builds the pool from a process count. The default
            starts spawned worker processes; a test passes a fake that
            breaks on demand.
        lower_priority: Lower spawned workers' operating-system priority.
        results: Results directory shared with the sweep caller.
    """

    def __init__(
        self,
        processes: int,
        max_workers: int | None = None,
        *,
        pairwise_max_demes: int = DEFAULT_PAIRWISE_MAX_DEMES,
        executor_factory: Callable[[int], Executor] | None = None,
        lower_priority: bool = False,
        priority: Priority | None = None,
        results: Path | None = None,
    ) -> None:
        context = multiprocessing.get_context("spawn")
        self._manager = context.Manager()
        self._processes = processes
        self._results_directory = (
            results if results is not None else paths.results_directory()
        )
        worker_priority: Priority = priority or (
            "lower" if lower_priority else "normal"
        )
        self._factory: Callable[[int], Executor] = executor_factory or (
            lambda count: ProcessPoolExecutor(
                count,
                mp_context=context,
                initializer=initialize_pool_worker,
                initargs=(worker_priority,),
            )
        )
        self._lock = threading.Lock()
        self._executor = self._factory(processes)
        self.max_workers = max_workers
        self.pairwise_max_demes = pairwise_max_demes

    def _replace(self, broken: Executor) -> None:
        """Swap in a fresh pool for `broken`, once however many threads ask.

        Several point threads see the same dead pool at the same time; only
        the first finds `broken` still current and replaces it.

        Args:
            broken: The pool that failed.
        """
        with self._lock:
            if self._executor is not broken:
                return
            self._executor = self._factory(self._processes)
        broken.shutdown(wait=False, cancel_futures=True)

    def _submit(
        self, function: Callable[..., tuple[str, str]], *args: object
    ) -> tuple[Future[tuple[str, str]], Executor]:
        """Submit `function`, replacing a pool that is already broken.

        A pool whose worker died refuses every later submit. One replacement
        is tried; a second refusal is raised to the caller.

        Args:
            function: The worker entry point.
            *args: Its arguments.

        Returns:
            The future and the pool it was submitted to.

        Raises:
            BrokenProcessPool: A fresh pool refused the submit as well.
            RuntimeError: The pool was shut down.
        """
        with self._lock:
            executor = self._executor
        try:
            return executor.submit(function, *args), executor
        except (BrokenProcessPool, RuntimeError):
            self._replace(executor)
        with self._lock:
            executor = self._executor
        return executor.submit(function, *args), executor

    def _await(
        self,
        function: Callable[..., tuple[str, str]],
        args: tuple[object, ...],
        cancel_event: threading.Event,
        shared_cancel: Any,
    ) -> tuple[str, str]:
        """Run `function` in the pool and wait, forwarding a cancel.

        Args:
            function: The worker entry point.
            args: Its arguments.
            cancel_event: The sweep's cancel event.
            shared_cancel: The event the worker process watches.

        Returns:
            The worker's `(kind, value)`, or `("transient", reason)` if the
            pool broke before or while the point ran.
        """
        try:
            future, executor = self._submit(function, *args)
        except (BrokenProcessPool, RuntimeError, OSError) as error:
            logger.warning("sweep worker pool refused a point: %s", error)
            return "transient", f"the worker pool could not start the point: {error}"
        while True:
            try:
                return future.result(timeout=0.25)
            except FutureTimeoutError:
                if cancel_event.is_set():
                    shared_cancel.set()
            except BrokenProcessPool:
                self._replace(executor)
                return "transient", "a worker process ended unexpectedly"
            except Exception as error:
                logger.warning("sweep worker raised: %s", error)
                return "failure", str(error) or type(error).__name__

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
        kind, value = self._await(
            _run_point_in_process,
            (
                params,
                str(self._results_directory),
                max_workers if max_workers is not None else self.max_workers,
                shared_cancel,
            ),
            cancel_event,
            shared_cancel,
        )
        return Path(value) if kind == "ok" else _point_failure(kind, value)

    def run_object_point(
        self,
        object_id: str,
        cancel_event: threading.Event,
        on_message: Callable[[object], None] | None = None,
    ) -> Path | PointFailure | PointPaused:
        """Execute a point's Run object in a worker process."""
        del on_message  # progress inside a point does not cross processes
        shared_cancel = self._manager.Event()
        kind, value = self._await(
            _run_object_in_process,
            (
                object_id,
                str(self._results_directory),
                self.pairwise_max_demes,
                shared_cancel,
            ),
            cancel_event,
            shared_cancel,
        )
        if kind == "ok":
            return Path(value)
        if kind == "paused":
            return PointPaused(value)
        return _point_failure(kind, value)

    def close(self) -> None:
        """Stop the worker processes and the shared-event server."""
        with self._lock:
            executor = self._executor
        executor.shutdown(wait=True, cancel_futures=True)
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


@dataclass(frozen=True, slots=True)
class _SweepWorkPlan:
    """Existing and pending point state shared by planning and execution."""

    study: StudyManifest
    spec: SweepSpec
    planned: tuple[SweepPoint, ...]
    todo: list[SweepPoint]
    already_present: int
    skipped: int
    rerun_ids: frozenset[str]
    older_runs: Mapping[str, Path]


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


STORED_PLAN_LIMIT: Final = 5000
"""Points up to which a Study stores the whole plan.

A larger plan stores its digest and point count only: the points are a pure
function of the specification, so they are enumerated again when needed, and
the digest keeps the check that the plan still matches this version."""


def _stored_plan(spec: SweepSpec, plan: SweepPlan) -> dict[str, object]:
    """Return what a Study stores of a plan: the spec, the digest, the points.

    A plan above `STORED_PLAN_LIMIT` points keeps its count in place of the
    list.
    """
    stored: dict[str, object] = {**spec.to_dict(), "digest": spec.digest(plan)}
    if len(plan.points) > STORED_PLAN_LIMIT:
        stored["point_count"] = len(plan.points)
    else:
        stored["points"] = [point.to_dict() for point in plan.points]
    return stored


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
    stored = _stored_plan(spec, plan)
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
    stored = _stored_plan(spec, plan)
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
    if points is None and "point_count" in study.sweep_spec:
        plan = enumerate_points(sweep_spec_of(study))
        return [point.to_dict() for point in plan.points]
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


def completed_run_ids(
    *, software_version: str = __version__, results: Path | None = None
) -> set[str]:
    """Return the id of every run a sweep would reuse instead of computing.

    See `completed_runs`; this is only the ids.
    """
    return set(completed_runs(software_version=software_version, results=results))


def completed_runs(
    *, software_version: str = __version__, results: Path | None = None
) -> dict[str, Path]:
    """Return each run a sweep would reuse instead of computing, by run id.

    A run counts if it is a finished run directory, or a completed Run
    object, made by `software_version`: the same rule `run_sweep` applies,
    so a plan can say beforehand which points are already computed.

    Args:
        software_version: The version whose runs count as done.
        results: Optional results-directory override.
    """
    root = results if results is not None else paths.results_directory()
    found: dict[str, Path] = dict(_completed_objects(software_version, results))
    if root.is_dir():
        for manifest in sorted(root.glob("*/manifest.json")):
            identity = groups.run_identity_of(manifest.parent)
            if identity is not None and identity[1] == software_version:
                found[identity[0]] = manifest.parent
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


@dataclass(frozen=True, slots=True)
class SweepBudget:
    """How much a call to `run_sweep` may spend before it stops.

    Every limit is optional. A sweep that reaches one starts no more points,
    pauses the ones in flight where they can pause (a Run object keeps its
    checkpoint), and ends with a `sweep_budget_reached` event; the points it
    did not start stay waiting for `fim sweep resume`.

    Attributes:
        max_wall_seconds: Wall-clock seconds, from the start of the call.
        max_new_points: Points this call may compute (reused points are free).
        max_disk_bytes: Bytes this call may write, counted as the size of the
            runs it has finished.
    """

    max_wall_seconds: float | None = None
    max_new_points: int | None = None
    max_disk_bytes: int | None = None

    def __post_init__(self) -> None:
        """Refuse a limit that is not positive."""
        for name in ("max_wall_seconds", "max_new_points", "max_disk_bytes"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive")


class _LinkedEvent(threading.Event):
    """A cancel event that is also set by this call's own budget.

    The caller's event is only read, never set, so a budget stop is not
    mistaken for the user cancelling.
    """

    def __init__(self, user: threading.Event) -> None:
        super().__init__()
        self._user = user

    def is_set(self) -> bool:
        """Return whether the caller cancelled or the budget stopped the sweep."""
        return self._user.is_set() or super().is_set()


class _BudgetTracker:
    """Counts what a call has spent and decides when a budget is reached."""

    def __init__(
        self, budget: SweepBudget, clock: Callable[[], float], stop: threading.Event
    ) -> None:
        self._budget = budget
        self._clock = clock
        self._stop = stop
        self._started = clock()
        self._lock = threading.Lock()
        self._reserved = 0
        self._bytes = 0
        self.reason: str | None = None

    def reserve(self) -> bool:
        """Claim one new point to compute; `False` once a budget is reached."""
        with self._lock:
            if self.reason is not None or self._tripped_locked():
                return False
            limit = self._budget.max_new_points
            if limit is not None and self._reserved >= limit:
                # Points already running were within the budget and finish.
                self.reason = f"the limit of {limit} new point(s) was reached"
                return False
            self._reserved += 1
            return True

    def add_bytes(self, size: int) -> None:
        """Count the bytes of a finished point, then check the budget."""
        with self._lock:
            self._bytes += size
        self.check()

    def check(self) -> None:
        """Stop the sweep if the time or disk limit has been reached."""
        with self._lock:
            if self.reason is None:
                self._tripped_locked()

    def _tripped_locked(self) -> bool:
        """Record and act on a reached time or disk limit; the lock is held."""
        budget = self._budget
        if (
            budget.max_wall_seconds is not None
            and self._clock() - self._started >= budget.max_wall_seconds
        ):
            limit = format_duration(budget.max_wall_seconds)
            self.reason = f"the time limit of {limit} was reached"
        elif budget.max_disk_bytes is not None and self._bytes >= budget.max_disk_bytes:
            self.reason = (
                f"the disk limit of {format_bytes(budget.max_disk_bytes)} was reached"
            )
        else:
            return False
        self._stop.set()
        return True


def _tree_bytes(path: Path) -> int:
    """Return the total size of the files under `path` (0 if it is gone)."""
    if path.is_file():
        return path.stat().st_size
    total = 0
    for directory, _names, files in os.walk(path):
        for name in files:
            try:
                total += (Path(directory) / name).stat().st_size
            except OSError:
                continue
    return total


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
    # Times a point that failed for a transient reason is tried in all.
    max_attempts: int = 1
    # Seconds from a clock that never goes backwards (a point's duration).
    clock: Callable[[], float] = time.monotonic
    # Counts spending against `run_sweep(budget=...)`; `None` without one.
    budget: _BudgetTracker | None = None

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
    max_attempts: int = 1,
    costliest_first: bool = True,
    only: Collection[str] | None = None,
    budget: SweepBudget | None = None,
    cores: int | None = None,
    process_points: bool = False,
    lower_priority: bool = False,
    priority: Priority | None = None,
    clock: Callable[[], float] = time.monotonic,
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
        max_attempts: Times to try a point that fails for a transient reason
            (`classify_failure`) before recording the failure. A
            deterministic failure is never tried again. The attempts are
            back to back, with no delay, so the result never depends on a
            clock.
        costliest_first: Start the points that may take longest first, which
            shortens the whole sweep when several run at once (the standard
            longest-job-first rule). Points are stored by id, so the order
            changes no result. `False` keeps grid order.
        only: Run just the points with these ids (a pilot of the costliest
            few, say); the others are left as they were, neither run nor
            counted as skipped. `None` runs every point that needs it.
        budget: Limits on this call (time, new points, disk). When one is
            reached the sweep starts no more points, pauses those in flight
            where it can, emits `sweep_budget_reached`, and returns with
            `SweepOutcome.budget_reached` set; it is not a cancel, and what
            was not started stays waiting.
        cores: Core budget available for this sweep, or the machine count
            when `None`.
        process_points: Force each local point to execute in a child process,
            even when only one point runs at a time.
        lower_priority: Lower child processes' priority, intended for GUI
            sweeps while command-line behavior remains unchanged.
        clock: Seconds, from a clock that never goes backwards; each finished
            point's duration is reported in its event, and the time budget is
            measured on it. A test seam.
        results: Optional results-directory override.

    Returns:
        The tally.

    Raises:
        ValueError: `max_attempts` is below 1, the Study is not a sweep, or
            the stored plan no longer matches what this version of the
            program enumerates.
        ClosedGroupError: The Study is closed; reopen it to continue.
        SweepLockedError: Another process is running this sweep. A sweep
            holds a lock on its Study for as long as it runs, released by
            the operating system if the process dies, so two sweeps never
            work on one Study at the same time.
    """
    _require_attempts(max_attempts)
    lock = sweep_lock_path(study_id, results=results)
    try:
        with exclusive_lock(lock, blocking=False):
            return _run_sweep_locked(
                study_id,
                runner,
                on_event,
                cancel_event,
                retry_failed=retry_failed,
                rerun=rerun,
                software_version=software_version,
                points_at_once=points_at_once,
                max_workers=max_workers,
                max_attempts=max_attempts,
                costliest_first=costliest_first,
                only=only,
                budget=budget,
                cores=cores,
                process_points=process_points,
                lower_priority=lower_priority,
                priority=priority,
                clock=clock,
                results=results,
            )
    except LockUnavailableError:
        raise SweepLockedError(
            f"another process is running sweep {study_id}; wait for it to "
            "finish, or stop it, and try again"
        ) from None


def sweep_lock_held(study_id: str, *, results: Path | None = None) -> bool:
    """Return whether a process is running the sweep `study_id` right now.

    Args:
        study_id: A sweep Study.
        results: Optional results-directory override.

    Returns:
        True while any process, this one included, is inside `run_sweep` for
        the Study.
    """
    return is_lock_held(sweep_lock_path(study_id, results=results))


def sweep_lock_path(study_id: str, *, results: Path | None = None) -> Path:
    """Return the lock file a running sweep holds for its Study.

    Kept beside the failure file, so the Study index's `*.json` scan never
    sees it.
    """
    return failures_path(study_id, results=results).with_suffix(".lock")


def _run_sweep_locked(
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
    max_attempts: int = 1,
    costliest_first: bool = True,
    only: Collection[str] | None = None,
    budget: SweepBudget | None = None,
    cores: int | None = None,
    process_points: bool = False,
    lower_priority: bool = False,
    priority: Priority | None = None,
    clock: Callable[[], float] = time.monotonic,
    results: Path | None = None,
) -> SweepOutcome:
    """Run the sweep; the caller holds the Study's sweep lock."""
    work_plan = _prepare_sweep_work(
        study_id,
        retry_failed=retry_failed,
        rerun=rerun,
        software_version=software_version,
        only=only,
        costliest_first=costliest_first,
        results=results,
    )
    study = work_plan.study
    spec = work_plan.spec
    planned = work_plan.planned
    rerun_ids = work_plan.rerun_ids
    older_runs = work_plan.older_runs
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
    todo = work_plan.todo
    tally["already_present"] = work_plan.already_present
    tally["skipped"] = work_plan.skipped
    first_params = SimulationParams.from_mapping(planned[0].params)
    uses_objects = isinstance(runner, ObjectPointRunner) and _object_supported(
        first_params
    )
    concurrency = resolve_concurrency(
        points=len(todo),
        params=first_params,
        requested=points_at_once,
        max_workers=max_workers,
        cores=cores,
        in_process_batches=uses_objects,
    )
    process_runner: ProcessPointRunner | None = None
    effective_runner: PointRunner = runner
    if (process_points or concurrency.points_at_once > 1) and isinstance(
        runner, LocalPointRunner
    ):
        process_runner = ProcessPointRunner(
            concurrency.points_at_once,
            runner.max_workers,
            pairwise_max_demes=runner.pairwise_max_demes,
            lower_priority=lower_priority,
            priority=priority,
            results=results,
        )
        effective_runner = process_runner
    stop: threading.Event = _LinkedEvent(cancel_event)
    tracker = _BudgetTracker(budget, clock, stop) if budget is not None else None
    context = _Context(
        study_id,
        effective_runner,
        on_event,
        stop,
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
        max_attempts=max_attempts,
        clock=clock,
        budget=tracker,
    )
    cancelled = False

    def work(point: SweepPoint) -> str:
        if stop.is_set():
            return "cancelled"
        return _run_one_point(context, point, older_runs.get(point.run_id))

    try:
        if todo:
            with ThreadPoolExecutor(max_workers=concurrency.points_at_once) as pool:
                for outcome in pool.map(work, todo):
                    if outcome == "cancelled":
                        cancelled = True
                    elif outcome != "budget":
                        tally[outcome] += 1
    finally:
        if process_runner is not None:
            process_runner.close()
    budget_reason = tracker.reason if tracker is not None else None
    if budget_reason is not None and not cancel_event.is_set():
        # Stopped by its own budget, not by the caller: not a cancel.
        cancelled = False
    else:
        budget_reason = None
        cancelled = cancelled or (cancel_event.is_set() and bool(todo))
    kind: EventKind = (
        "sweep_budget_reached"
        if budget_reason is not None
        else "sweep_cancelled"
        if cancelled
        else "sweep_done"
    )
    on_event(SweepEvent(kind, -1, "", context.finished, len(planned), budget_reason))
    summary = SweepOutcome(
        done=tally["done"],
        reused=tally["reused"],
        already_present=tally["already_present"],
        failed=tally["failed"],
        skipped_failed=tally["skipped"],
        cancelled=cancelled,
        recomputed=tally["recomputed"],
        differing=tally["differs"],
        budget_reached=budget_reason,
    )
    if rerun and not cancelled and budget_reason is None:
        _write_verification(study, spec, summary, results)
    return summary


def _points_to_run(
    planned: Sequence[SweepPoint],
    *,
    rerun_ids: frozenset[str],
    present: Mapping[str, Path],
    failures: Mapping[str, Any],
    objects: Mapping[str, str],
    results: Path | None,
    retry_failed: bool,
    only: Collection[str] | None,
    costliest_first: bool,
) -> tuple[list[SweepPoint], int, int]:
    """Choose the points a sweep has to run, and in what order.

    Returns:
        The points to run (restricted to `only`, and costliest first if
        asked), how many were already present, and how many were skipped
        because they failed before and `retry_failed` is off.
    """
    todo: list[SweepPoint] = []
    already_present = skipped = 0
    for point in planned:
        if point.run_id in rerun_ids:
            todo.append(point)
        elif point.run_id in present:
            already_present += 1
        elif _is_skipped_failure(
            point.run_id, failures, objects, results, retry_failed=retry_failed
        ):
            skipped += 1
        else:
            todo.append(point)
    if only is not None:
        todo = [point for point in todo if point.run_id in only]
    if costliest_first:
        todo = order_costliest_first(todo)
    return todo, already_present, skipped


def _prepare_sweep_work(
    study_id: str,
    *,
    retry_failed: bool,
    rerun: bool,
    software_version: str,
    only: Collection[str] | None,
    costliest_first: bool,
    results: Path | None,
) -> _SweepWorkPlan:
    """Resolve the exact pending work a resume request would execute."""
    study = groups.get_study(study_id, results=results)
    groups.refuse_closed(study, "given more sweep runs")
    spec = sweep_spec_of(study)
    planned = _matching_plan(study, enumerate_points(spec))
    failures = read_failures(study_id, results=results)
    objects = read_point_objects(study_id, results=results)
    present, stale = _member_runs(study, software_version, results)
    rerun_ids = (
        frozenset(point.run_id for point in planned if point.run_id in present)
        if rerun
        else frozenset()
    )
    short = frozenset(
        point.run_id
        for point in planned
        if point.run_id in present
        and not run_satisfies(present[point.run_id], point.params)
    )
    rerun_ids = rerun_ids | short
    todo, already_present, skipped = _points_to_run(
        planned,
        rerun_ids=rerun_ids,
        present=present,
        failures=failures,
        objects=objects,
        results=results,
        retry_failed=retry_failed,
        only=only,
        costliest_first=costliest_first,
    )
    older_runs = {**stale, **{run_id: present[run_id] for run_id in rerun_ids}}
    return _SweepWorkPlan(
        study,
        spec,
        planned,
        todo,
        already_present,
        skipped,
        rerun_ids,
        older_runs,
    )


def pending_sweep_point_count(
    study_id: str,
    *,
    retry_failed: bool = False,
    rerun: bool = False,
    software_version: str = __version__,
    results: Path | None = None,
) -> int:
    """Return the exact number of points a sweep resume would execute."""
    return len(
        _prepare_sweep_work(
            study_id,
            retry_failed=retry_failed,
            rerun=rerun,
            software_version=software_version,
            only=None,
            costliest_first=False,
            results=results,
        ).todo
    )


def verification_path(study_id: str, *, results: Path | None = None) -> Path:
    """Return the file recording a sweep's last full re-run (`verified.json`).

    Kept beside the status file, so the Study index's `*.json` scan does not
    see it.
    """
    return failures_path(study_id, results=results).with_name(
        f"{study_id}.verified.json"
    )


def read_verification(
    study_id: str, *, results: Path | None = None
) -> dict[str, Any] | None:
    """Return what the last full re-run of a sweep found, or `None`.

    Keys: `verified_at` (UTC text), `software_version`, `plan_digest`,
    `points_checked`, `identical` and `differing` (points), and `result`
    (`"identical"` if every recomputed point matched bit for bit, else
    `"differs"`).
    """
    try:
        loaded = json.loads(
            verification_path(study_id, results=results).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _write_verification(
    study: StudyManifest,
    spec: SweepSpec,
    outcome: SweepOutcome,
    results: Path | None,
) -> None:
    """Record a completed re-run that actually recomputed points.

    A re-run that recomputed nothing (no point had finished) verifies
    nothing and writes nothing.
    """
    checked = outcome.recomputed + outcome.differing
    if checked == 0:
        return
    document = {
        "verified_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "software_version": __version__,
        "plan_digest": (study.sweep_spec or {}).get("digest") or spec.digest(),
        "points_checked": checked,
        "identical": outcome.recomputed,
        "differing": outcome.differing,
        "result": "identical" if outcome.differing == 0 else "differs",
    }
    path = verification_path(study.study_id, results=results)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _require_attempts(max_attempts: int) -> None:
    """Refuse a limit that would never try a point.

    Raises:
        ValueError: `max_attempts` is below 1.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")


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
    failure: PointFailure,
    results: Path | None,
) -> None:
    """Persist one failed point so 'failed' survives a restart.

    The record keeps the failure's class and the attempts made, added to
    those of an earlier failure of the same point, so a point retried
    across several resumes shows its total.
    """
    failures = read_failures(study_id, results=results)
    earlier = failures.get(point.run_id, {}).get("attempts", 0)
    failures[point.run_id] = {
        "index": point.index,
        "reason": failure.reason,
        "failure_class": failure.failure_class,
        "attempts": (earlier if isinstance(earlier, int) else 0) + failure.attempts,
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


@dataclass(frozen=True, slots=True)
class Replan:
    """What `replan_sweep` changed, or would change.

    Attributes:
        renamed: `(old run id, new run id)` for each point whose coordinates
            are unchanged but whose id differs.
        unchanged: Points whose id already matches.
        added: Run ids of points the stored plan did not have.
        removed: Run ids of stored points that are no longer valid.
        orphaned_runs: Runs still in the Study whose id matches no point of
            the new plan. They stay attached as ordinary runs, and the
            points they stood for run again.
        failures_moved: Recorded failures carried over to a new id.
        unlinked_objects: Run objects that were linked to a renamed point.
            They are left as they are, but no longer continued by the sweep.
        written: Whether the Study was changed (false for a dry run).
    """

    renamed: tuple[tuple[str, str], ...]
    unchanged: int
    added: tuple[str, ...]
    removed: tuple[str, ...]
    orphaned_runs: tuple[Path, ...]
    failures_moved: int
    unlinked_objects: tuple[str, ...]
    written: bool


def _coordinate_key(coordinates: Mapping[str, Any]) -> str:
    """Return a text key for a point's coordinates, equal across a JSON round trip."""
    return json.dumps(dict(coordinates), sort_keys=True, default=str)


def replan_sweep(
    study_id: str, *, dry_run: bool = False, results: Path | None = None
) -> Replan:
    """Re-identify a sweep Study's points under this version's run ids.

    A run id is a hash of the point's configuration, so a release that
    changes what the hash covers changes every id, and the stored plan then
    no longer matches (`run_sweep` refuses it). The plan is enumerated again
    from the stored specification and each new point is matched to its
    stored one by coordinates, never by id. The stored plan is rewritten, and
    the recorded failures follow their points. (A large plan stores only its
    digest, so there are no old ids to pair: the digest is rewritten, every
    point reads as unchanged, and a failure recorded under an old id no
    longer applies.) Runs already in the Study are
    never touched: one whose id no longer matches any point stays attached as
    an ordinary run, listed in the result, and its point runs again.

    Args:
        study_id: A sweep Study.
        dry_run: Report what would change and write nothing.
        results: Optional results-directory override.

    Returns:
        What changed, or would change.

    Raises:
        ValueError: The Study is not a sweep, has no valid points, or has
            points that coordinates cannot tell apart.
        ClosedGroupError: The Study is closed.
        SweepLockedError: A process is running the sweep.
    """
    study = groups.get_study(study_id, results=results)
    groups.refuse_closed(study, "re-planned")
    plan = enumerate_points(sweep_spec_of(study))
    if not plan.points:
        raise ValueError("the sweep has no valid points to run")
    old_entries = stored_points(study)
    old_by_key = {
        _coordinate_key(entry.get("coordinates", {})): entry for entry in old_entries
    }
    new_keys = [_coordinate_key(point.coordinates) for point in plan.points]
    if len(old_by_key) != len(old_entries) or len(set(new_keys)) != len(new_keys):
        raise ValueError("the sweep's points cannot be told apart by coordinates")

    # Pair each new point with its stored one.
    renamed: list[tuple[str, str]] = []
    added: list[str] = []
    unchanged = 0
    for key, point in zip(new_keys, plan.points, strict=True):
        entry = old_by_key.pop(key, None)
        if entry is None:
            added.append(point.run_id)
        elif str(entry["run_id"]) == point.run_id:
            unchanged += 1
        else:
            renamed.append((str(entry["run_id"]), point.run_id))
    removed = tuple(str(entry["run_id"]) for entry in old_by_key.values())

    # Runs that no longer stand for a point, and the status that follows ids.
    new_ids = {point.run_id for point in plan.points}
    orphaned = tuple(
        directory
        for directory in groups.study_run_directories(study, results=results)
        if (identity := groups.run_identity_of(directory)) is not None
        and identity[0] not in new_ids
    )
    failures = read_failures(study_id, results=results)
    moved = {old: new for old, new in renamed if old in failures}
    objects = read_point_objects(study_id, results=results)
    unlinked = tuple(objects[old] for old, _new in renamed if old in objects)
    outcome = Replan(
        renamed=tuple(renamed),
        unchanged=unchanged,
        added=tuple(added),
        removed=removed,
        orphaned_runs=orphaned,
        failures_moved=len(moved),
        unlinked_objects=unlinked,
        written=not dry_run,
    )
    if dry_run:
        return outcome

    # Write under the sweep lock, so no sweep reads a half-rewritten plan.
    try:
        with exclusive_lock(sweep_lock_path(study_id, results=results), blocking=False):
            _write_replan(study, plan, renamed, results)
    except LockUnavailableError:
        raise SweepLockedError(
            f"another process is running sweep {study_id}; stop it before re-planning"
        ) from None
    return outcome


def _write_replan(
    study: StudyManifest,
    plan: SweepPlan,
    renamed: list[tuple[str, str]],
    results: Path | None,
) -> None:
    """Store the new plan and move the failure and object records with it."""
    assert study.sweep_spec is not None  # checked by `stored_points`
    stored = _stored_plan(sweep_spec_of(study), plan)
    kept = {
        key: value
        for key, value in study.sweep_spec.items()
        if key not in {"points", "point_count"}
    }
    updated = replace(study, sweep_spec={**kept, **stored})
    groups.write_study_manifest(
        groups.study_manifest_path(study.study_id, results=results), updated
    )
    failures = read_failures(study.study_id, results=results)
    objects = read_point_objects(study.study_id, results=results)
    for old, new in renamed:
        if old in failures:
            failures[new] = failures.pop(old)
        objects.pop(old, None)
    _write_status(study.study_id, results, "failures", failures)
    _write_status(study.study_id, results, "objects", objects)


def _matching_plan(study: StudyManifest, plan: SweepPlan) -> tuple[SweepPoint, ...]:
    """Return the enumerated points, after checking they match the stored plan."""
    spec = study.sweep_spec or {}
    if "points" not in spec and "point_count" in spec:
        # A large plan keeps only its digest, so that is what must still match.
        matches = spec.get("digest") == sweep_spec_of(study).digest(plan)
    else:
        stored = [str(entry["run_id"]) for entry in stored_points(study)]
        matches = stored == [point.run_id for point in plan.points]
    if not matches:
        raise ValueError(
            "the stored sweep plan no longer matches this version of the "
            "program (run ids differ); re-identify its points with "
            f"`fim sweep replan {study.study_id}` (add --dry-run to see "
            "what would change first)"
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
    """Run one point, trying again while it fails for a transient reason.

    A failure the engine reports is returned at once: the same
    configuration fails the same way. A transient one (a worker that died,
    a file system error) is tried again up to `context.max_attempts` in
    all, unless the sweep was cancelled. The attempts are back to back,
    so the outcome never depends on a clock.

    Returns:
        The outcome and the point's Run object ID, if any. A failure
        carries how many attempts it took.
    """
    attempts = 0
    while True:
        attempts += 1
        outcome, object_id = _dispatch_once(context, point, progress)
        if not isinstance(outcome, PointFailure):
            return outcome, object_id
        retry = (
            outcome.failure_class == "transient"
            and attempts < context.max_attempts
            and not context.cancel_event.is_set()
        )
        if not retry:
            return replace(outcome, attempts=attempts), object_id
        logger.warning(
            "sweep point %s failed (attempt %d of %d), trying again: %s",
            point.run_id,
            attempts,
            context.max_attempts,
            outcome.reason,
        )


def _dispatch_once(
    context: _Context, point: SweepPoint, progress: Callable[[object], None]
) -> tuple[Path | PointFailure | PointPaused, str | None]:
    """Run one point once and return its outcome with its Run object ID."""
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
            return PointFailure(str(error), classify_failure(error)), None
        return runner.run_object_point(object_id, context.cancel_event, progress), (
            object_id
        )
    outcome = runner.run_point(
        params, context.cancel_event, progress, context.workers_per_point
    )
    return outcome, None


def _run_one_point(
    context: _Context, point: SweepPoint, stale_member: Path | None
) -> Literal[
    "done", "reused", "recomputed", "differs", "failed", "cancelled", "budget"
]:
    """Attach, reuse or compute one point; compare it with an older version's run."""
    reusable = groups.find_run_directories(
        point.run_id,
        software_version=context.software_version,
        results=context.results,
    )
    completed = (context.completed_objects or {}).get(point.run_id)
    if completed is not None:
        reusable.append(completed)
    # A run that kept less than this point asks for (a tapered run where full
    # frames are wanted) is not reused; the point is computed again.
    reusable = [d for d in reusable if run_satisfies(d, point.params)]
    if point.run_id in context.rerun_ids:
        # Recomputing on purpose: the run it already has is the thing to
        # compare with, not a result to reuse.
        reusable = []
    reused = bool(reusable)
    object_id: str | None = None
    if reused:
        result: Path | PointFailure | PointPaused = reusable[0]
    else:
        if context.budget is not None and not context.budget.reserve():
            return "budget"
        context.emit("point_started", point, finish=False)

        def progress(message: object) -> None:
            context.emit("point_progress", point, message, finish=False)
            if context.budget is not None:
                context.budget.check()

        dispatched = context.clock()
        result, object_id = _dispatch_point(context, point, progress)
        elapsed = context.clock() - dispatched
    if isinstance(result, PointPaused):
        return "cancelled"
    if isinstance(result, PointFailure):
        if context.cancel_event.is_set():
            return "cancelled"
        if object_id is None:
            # A Run object records its own failure; only a failure before
            # one existed needs the sweep's own record.
            with context.lock:
                _record_failure(context.study_id, point, result, context.results)
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
    context.emit(
        "point_reused" if reused else "point_done",
        point,
        None if reused else {"seconds": elapsed},
        finish=True,
    )
    if not reused and context.budget is not None:
        context.budget.add_bytes(_tree_bytes(result))
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
            except groups.UnsafeRunReferenceError as error:
                # Should never happen -- `older` always names a point's
                # own existing run under `context.results` -- but every
                # Study's own link was already replaced before this
                # could raise, so the sweep continues; only the stale
                # directory survives.
                logger.warning("could not remove superseded run %s: %s", older, error)
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


def sweep_point_results(
    study: StudyManifest, *, results: Path | None = None
) -> list[PointRow]:
    """Read the final statistics of every finished point, in grid order.

    A batch point reads its `summary.json` (mean and confidence interval
    across replicates); a scalar point reads its `report.json`. Each row also
    carries how the point's replicates ended (`PointHealth`), derived from the
    run's manifests and cached beside the sweep's status file. A point whose
    files cannot be read is left out, like a missing run directory elsewhere
    in the Study code. The statistics are computed each time from small JSON
    files; only the health counts are cached, and deleting the cache loses
    nothing.
    """
    current, stale = _member_runs(study, __version__, results)
    cache = HealthCache(health_cache_path(study.study_id, results=results))
    found: list[PointRow] = []
    for entry in stored_points(study):
        run_id = str(entry["run_id"])
        directory = current.get(run_id) or stale.get(run_id)
        if directory is None:
            continue
        row = _read_point_row(
            entry, directory, "done" if run_id in current else "stale", cache
        )
        if row is not None:
            found.append(row)
    cache.save()
    return found


def health_cache_path(study_id: str, *, results: Path | None = None) -> Path:
    """Return the disposable cache of a sweep's point health counts."""
    return failures_path(study_id, results=results).with_name(f"{study_id}.health.json")


def trajectory_cache_path(study_id: str, *, results: Path | None = None) -> Path:
    """Return the disposable folder of a sweep's cached point trajectories."""
    return failures_path(study_id, results=results).with_name(
        f"{study_id}.trajectories"
    )


def _read_point_row(
    entry: Mapping[str, Any], directory: Path, state: PointState, cache: HealthCache
) -> PointRow | None:
    """Build a point's row from its run directory, or `None` if unreadable."""
    statistics = _read_point_statistics(directory)
    if statistics is None:
        return None
    manifest = _read_json_object(directory / "manifest.json")
    parameters = manifest.get("parameters")
    seed = parameters.get("seed") if isinstance(parameters, dict) else None
    backend = manifest.get("engine_backend")
    if backend is None:
        replicate = _read_json_object(directory / "replicate-001" / "manifest.json")
        backend = replicate.get("engine_backend")
    return PointRow(
        index=int(entry["index"]),
        coordinates=dict(entry.get("coordinates", {})),
        run_id=str(entry["run_id"]),
        state=state,
        directory=directory,
        n_replicates=statistics[0],
        seed=seed if isinstance(seed, int) else None,
        software_version=_text_or_none(manifest.get("software_version")),
        engine_backend=_text_or_none(backend),
        started_at=_text_or_none(manifest.get("started_at")),
        ended_at=_text_or_none(manifest.get("ended_at")),
        statistics=statistics[1],
        health=read_point_health(directory, cache=cache),
        parameters=dict(parameters) if isinstance(parameters, dict) else {},
    )


def _read_json_object(path: Path) -> dict[str, Any]:
    """Return the JSON object in `path`, or an empty one if it cannot be read."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _text_or_none(value: object) -> str | None:
    """Return `value` if it is text, else `None`."""
    return value if isinstance(value, str) else None


def _read_point_statistics(
    directory: Path,
) -> tuple[int, dict[str, StatisticCell]] | None:
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
                name: StatisticCell(mean=float(value))
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
) -> tuple[int, dict[str, StatisticCell]] | None:
    """Reduce a batch `summary.json` to a cell per statistic, keeping every field."""
    if not isinstance(payload, dict):
        return None
    statistics: dict[str, StatisticCell] = {}
    replicates = 0
    for name, entry in payload.items():
        if isinstance(entry, dict) and "mean" in entry:
            cell = StatisticCell.from_summary_entry(entry)
            statistics[name] = cell
            replicates = max(replicates, cell.sample_count)
    return (replicates, statistics) if statistics else None
