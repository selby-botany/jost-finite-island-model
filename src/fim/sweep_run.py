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
  Only failures are recorded, in a small file beside the Study index.
- Points run one after another. A batch already spreads its replicates
  over the cores; running several batches at once would oversubscribe
  them. `PointRunner` is the seam a cluster would implement instead.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from fim import __version__, paths
from fim.model.params import SimulationParams
from fim.persistence import groups
from fim.persistence.groups import StudyManifest
from fim.reproducibility import compare_runs
from fim.sweep import SweepPlan, SweepPoint, SweepSpec, enumerate_points

logger = logging.getLogger(__name__)

PointState = Literal["done", "stale", "failed", "waiting"]
"""A point is `done` (a member run has its id, from this software version), `stale`
(only a run from another software version), `failed`, or `waiting`."""

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


_ALLOCATION_LOCK = threading.Lock()
_ALLOCATED_DIRECTORIES: set[Path] = set()


def _allocate_output_directory() -> Path:
    """Return a new run directory name no other point in this process holds.

    `paths.default_output_directory` names a directory by the microsecond
    and checks it does not exist yet, but the directory is only created when
    a run publishes, so two points allocating at once could be handed one
    name. The lock and the set close that window.
    """
    with _ALLOCATION_LOCK:
        for _ in range(1000):
            candidate = paths.default_output_directory()
            if candidate not in _ALLOCATED_DIRECTORIES:
                _ALLOCATED_DIRECTORIES.add(candidate)
                return candidate
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
    """

    def __init__(self, max_workers: int | None = None) -> None:
        self.max_workers = max_workers

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
        from fim.gui import batch_runner, runner  # noqa: PLC0415

        try:
            output_directory = _allocate_output_directory()
        except FileExistsError as error:
            return PointFailure(str(error))
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
                )
            else:
                runner.start_run(params, output_directory, message_queue, cancel_event)
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
) -> Concurrency:
    """Choose how many points run at once, and each point's workers.

    A batch already spreads its replicates over cores, so points at once
    times workers per point should not exceed the core count. "Auto"
    (`requested=None`) fills the machine: a single run needs one core, so
    up to one point per core; a batch of `r` replicates on a lineal engine
    needs about `min(r, cores)`, so `cores // that` points. A non-lineal
    engine's demand is its concurrent replicates. `max_workers`, when the
    user set it, is each point's worker count and shrinks how many fit.

    Args:
        points: How many points there are to run (caps the answer).
        params: One point's configuration (replicates and engine are the
            same across a sweep's points).
        requested: Points at once the user asked for, or `None` for auto.
        max_workers: The user's per-batch worker limit, if any.
        cores: Core count; the machine's own when `None`.
    """
    available = cores if cores is not None else (os.cpu_count() or 1)
    lineal = params.engine_backend in {"lineal", "auto"}
    if params.n_replicates <= 1:
        demand = 1
    elif lineal:
        demand = min(params.n_replicates, max_workers or available)
    else:
        concurrent = params.max_concurrent_replicates or params.n_replicates
        demand = min(concurrent, available)
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
    """
    if not plan.points:
        raise ValueError("the sweep has no valid points to run")
    study = groups.get_study(study_id, results=results)
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
    made by this `software_version`; `stale` if the only such member was
    made by another version (it is recomputed, and compared, on the next
    run); `failed` if a failure was recorded and there is no such run;
    otherwise `waiting`.
    """
    current, stale = _member_runs(study, software_version, results)
    failures = read_failures(study.study_id, results=results)
    statuses = []
    for entry in stored_points(study):
        run_id = str(entry["run_id"])
        failure = failures.get(run_id)
        if run_id in current:
            state: PointState = "done"
        elif run_id in stale:
            state = "stale"
        elif failure is not None:
            state = "failed"
        else:
            state = "waiting"
        statuses.append(
            PointStatus(
                index=int(entry["index"]),
                coordinates=dict(entry.get("coordinates", {})),
                run_id=run_id,
                state=state,
                reason=(
                    str(failure["reason"])
                    if failure is not None and state == "failed"
                    else None
                ),
            )
        )
    return statuses


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

    Args:
        study_id: A sweep Study created by `create_sweep_study`.
        runner: Runs one point. Must be safe to call from several threads.
        on_event: Called with every `SweepEvent`, one at a time. A point's
            `position` is how many points have finished, not its index.
        cancel_event: Set to stop the sweep.
        retry_failed: Also retry points recorded as failed; by default
            they are skipped.
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
    """
    study = groups.get_study(study_id, results=results)
    spec = sweep_spec_of(study)
    planned = _matching_plan(study, enumerate_points(spec))
    failures = read_failures(study_id, results=results)
    present, stale = _member_runs(study, software_version, results)
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
        if point.run_id in present:
            tally["already_present"] += 1
        elif point.run_id in failures and not retry_failed:
            tally["skipped"] += 1
        else:
            todo.append(point)
    concurrency = resolve_concurrency(
        points=len(todo),
        params=SimulationParams.from_mapping(planned[0].params),
        requested=points_at_once,
        max_workers=max_workers,
    )
    context = _Context(
        study_id,
        runner,
        on_event,
        cancel_event,
        software_version,
        results,
        len(planned),
        concurrency.workers_per_point,
        threading.Lock(),
        finished=tally["already_present"] + tally["skipped"],
    )
    cancelled = False

    def work(point: SweepPoint) -> str:
        if cancel_event.is_set():
            return "cancelled"
        return _run_one_point(context, point, stale.get(point.run_id))

    if todo:
        with ThreadPoolExecutor(max_workers=concurrency.points_at_once) as pool:
            for outcome in pool.map(work, todo):
                if outcome == "cancelled":
                    cancelled = True
                else:
                    tally[outcome] += 1
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
    path = failures_path(study_id, results=results)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    failures = payload.get("failures") if isinstance(payload, dict) else None
    return dict(failures) if isinstance(failures, dict) else {}


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
    """Write the failure record atomically."""
    path = failures_path(study_id, results=results)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"failures": dict(failures)}, indent=2), encoding="utf-8"
    )
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


def _run_one_point(
    context: _Context, point: SweepPoint, stale_member: Path | None
) -> Literal["done", "reused", "recomputed", "differs", "failed", "cancelled"]:
    """Attach, reuse or compute one point; compare it with an older version's run."""
    reusable = groups.find_run_directories(
        point.run_id,
        software_version=context.software_version,
        results=context.results,
    )
    reused = bool(reusable)
    if reused:
        result: Path | PointFailure = reusable[0]
    else:
        context.emit("point_started", point, finish=False)

        def progress(message: object) -> None:
            context.emit("point_progress", point, message, finish=False)

        result = context.runner.run_point(
            SimulationParams.from_mapping(point.params),
            context.cancel_event,
            progress,
            context.workers_per_point,
        )
    if isinstance(result, PointFailure):
        if context.cancel_event.is_set():
            return "cancelled"
        with context.lock:
            _record_failure(context.study_id, point, result.reason, context.results)
        context.emit("point_failed", point, result.reason, finish=True)
        return "failed"
    with context.lock:
        groups.add_run_to_study(context.study_id, result, results=context.results)
        _clear_failure(context.study_id, point.run_id, context.results)
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
            groups.supersede_run(older, recomputed, results=context.results)
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
