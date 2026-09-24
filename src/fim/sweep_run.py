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
import queue
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from fim import paths
from fim.model.params import SimulationParams
from fim.persistence import groups
from fim.persistence.groups import StudyManifest
from fim.sweep import SweepPlan, SweepPoint, SweepSpec, enumerate_points

logger = logging.getLogger(__name__)

PointState = Literal["done", "failed", "waiting"]
"""A point is `done` (a member run has its id), `failed`, or `waiting`."""

EventKind = Literal[
    "point_started",
    "point_progress",
    "point_reused",
    "point_done",
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
    ) -> Path | PointFailure:
        """Run `params` and return the published run directory or a failure."""
        ...


class LocalPointRunner:
    """Runs a point in this process's own scalar or batch machinery.

    The same engine invocation and atomic artifact publication the desktop
    app uses, so a sweep point is an ordinary Run the app can open.
    """

    def run_point(
        self,
        params: SimulationParams,
        cancel_event: threading.Event,
        on_message: Callable[[object], None] | None = None,
    ) -> Path | PointFailure:
        """Run `params` synchronously; see `PointRunner.run_point`."""
        # Imported here: the runners pull in matplotlib and the GUI
        # package, which a plain `import fim.sweep_run` should not.
        from fim.gui import batch_runner, runner  # noqa: PLC0415

        output_directory = paths.default_output_directory()
        message_queue: queue.Queue[Any] = queue.Queue()
        try:
            if params.n_replicates > 1:
                batch_runner.start_batch_run(
                    params, output_directory, message_queue, cancel_event
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
    study: StudyManifest, *, results: Path | None = None
) -> list[PointStatus]:
    """Return every planned point with its derived state.

    `done` if a member run of the Study has the point's `run_id`,
    `failed` if a failure was recorded and no such run exists, otherwise
    `waiting`.
    """
    present = {
        run_id
        for directory in groups.study_run_directories(study, results=results)
        if (run_id := groups.run_id_of(directory)) is not None
    }
    failures = read_failures(study.study_id, results=results)
    statuses = []
    for entry in stored_points(study):
        run_id = str(entry["run_id"])
        failure = failures.get(run_id)
        if run_id in present:
            state: PointState = "done"
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


def run_sweep(
    study_id: str,
    runner: PointRunner,
    on_event: Callable[[SweepEvent], None],
    cancel_event: threading.Event,
    *,
    retry_failed: bool = False,
    results: Path | None = None,
) -> SweepOutcome:
    """Run every point of a sweep Study that is not already present.

    Points are handled in grid order, one at a time. A failed point is
    recorded and the sweep continues. Cancelling stops the current point
    and skips the rest; completed points stay attached.

    Args:
        study_id: A sweep Study created by `create_sweep_study`.
        runner: Runs one point.
        on_event: Called with every `SweepEvent`.
        cancel_event: Set to stop the sweep.
        retry_failed: Also retry points recorded as failed; by default
            they are skipped.
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
    present = {
        run_id
        for directory in groups.study_run_directories(study, results=results)
        if (run_id := groups.run_id_of(directory)) is not None
    }
    total = len(planned)
    tally = {"done": 0, "reused": 0, "already_present": 0, "failed": 0, "skipped": 0}
    cancelled = False
    position = 0
    for position, point in enumerate(planned, start=1):
        if cancel_event.is_set():
            cancelled = True
            break
        if point.run_id in present:
            tally["already_present"] += 1
            continue
        if point.run_id in failures and not retry_failed:
            tally["skipped"] += 1
            continue
        outcome = _run_one_point(
            study_id, point, runner, on_event, cancel_event, position, total, results
        )
        if outcome == "cancelled":
            cancelled = True
            break
        tally[outcome] += 1
    on_event(
        SweepEvent(
            "sweep_cancelled" if cancelled else "sweep_done",
            -1,
            "",
            position,
            total,
        )
    )
    return SweepOutcome(
        done=tally["done"],
        reused=tally["reused"],
        already_present=tally["already_present"],
        failed=tally["failed"],
        skipped_failed=tally["skipped"],
        cancelled=cancelled,
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
    study_id: str,
    point: SweepPoint,
    runner: PointRunner,
    on_event: Callable[[SweepEvent], None],
    cancel_event: threading.Event,
    position: int,
    total: int,
    results: Path | None,
) -> Literal["done", "reused", "failed", "cancelled"]:
    """Attach, reuse or compute one point; return which."""
    reusable = groups.find_run_directories(point.run_id, results=results)
    if reusable:
        groups.add_run_to_study(study_id, reusable[0], results=results)
        _clear_failure(study_id, point.run_id, results)
        on_event(SweepEvent("point_reused", point.index, point.run_id, position, total))
        return "reused"
    on_event(SweepEvent("point_started", point.index, point.run_id, position, total))

    def progress(message: object) -> None:
        on_event(
            SweepEvent(
                "point_progress", point.index, point.run_id, position, total, message
            )
        )

    result = runner.run_point(
        SimulationParams.from_mapping(point.params), cancel_event, progress
    )
    if isinstance(result, PointFailure):
        if cancel_event.is_set():
            return "cancelled"
        _record_failure(study_id, point, result.reason, results)
        on_event(
            SweepEvent(
                "point_failed",
                point.index,
                point.run_id,
                position,
                total,
                result.reason,
            )
        )
        return "failed"
    groups.add_run_to_study(study_id, result, results=results)
    _clear_failure(study_id, point.run_id, results)
    on_event(SweepEvent("point_done", point.index, point.run_id, position, total))
    return "done"
