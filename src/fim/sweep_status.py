"""Where a sweep stands: its points by state, who is running it, and the ETA.

Everything here is read from what a sweep already stores (its plan, its
status file, its runs' manifests and its lock), so any process can ask while
another is running the sweep, and nothing is written.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from fim.model.params import SimulationParams
from fim.persistence import groups
from fim.sweep import enumerate_points
from fim.sweep_cost import (
    Actual,
    Remaining,
    format_duration,
    read_actual,
    remaining_cost,
    static_cost,
)
from fim.sweep_run import (
    PointStatus,
    read_failures,
    resolve_concurrency,
    sweep_lock_held,
    sweep_point_statuses,
    sweep_spec_of,
)


@dataclass(frozen=True, slots=True)
class FailedPoint:
    """A point that failed, and what is known about why.

    Attributes:
        index: The point's grid index.
        coordinates: The varied values, by axis key.
        reason: Why it failed.
        attempts: Times it was tried, or `None` if not recorded.
    """

    index: int
    coordinates: Mapping[str, object]
    reason: str
    attempts: int | None


@dataclass(frozen=True, slots=True)
class SweepStatus:
    """A snapshot of a sweep.

    Attributes:
        study_id: The sweep Study's id.
        name: The Study's name.
        total: Planned points.
        counts: Points in each state (`done`, `waiting`, `running`, ...).
        running: The points running now (or interrupted mid-run).
        failures: The points that failed.
        locked: Whether a process is running the sweep right now.
        last_finished: When the most recently finished point ended (UTC
            text), or `None` if none has.
        remaining: What is left, with the calibrated time to finish, or
            `None` if the stored plan no longer matches this version.
    """

    study_id: str
    name: str
    total: int
    counts: Mapping[str, int]
    running: tuple[PointStatus, ...]
    failures: tuple[FailedPoint, ...]
    locked: bool
    last_finished: str | None
    remaining: Remaining | None

    @property
    def finished(self) -> bool:
        """Whether every point is done and no process is running the sweep."""
        return self.counts.get("done", 0) == self.total and not self.locked


def read_sweep_status(study_id: str, *, results: Path | None = None) -> SweepStatus:
    """Read a sweep Study's status.

    Args:
        study_id: A sweep Study.
        results: Optional results-directory override.

    Returns:
        The snapshot.

    Raises:
        ValueError: The Study does not exist or is not a sweep.
    """
    study = groups.get_study(study_id, results=results)
    spec = sweep_spec_of(study)
    statuses = sweep_point_statuses(study, results=results)
    recorded = read_failures(study_id, results=results)
    failures = tuple(
        FailedPoint(
            status.index,
            status.coordinates,
            status.reason or "",
            _attempts(recorded.get(status.run_id)),
        )
        for status in statuses
        if status.state == "failed"
    )
    actual = _finished_points(study, statuses, results)
    plan = enumerate_points(spec)
    current = [p.run_id for p in plan.points] == [s.run_id for s in statuses]
    remaining = None
    if current and plan.points:
        todo = len(plan.points) - len(actual)
        chosen = resolve_concurrency(
            points=max(todo, 1),
            params=SimulationParams.from_mapping(plan.points[0].params),
        )
        cost = static_cost(spec, plan, points_at_once=chosen.points_at_once)
        remaining = remaining_cost(cost, actual)
    return SweepStatus(
        study_id=study_id,
        name=study.name,
        total=len(statuses),
        counts=dict(Counter(status.state for status in statuses)),
        running=tuple(s for s in statuses if s.state == "running"),
        failures=failures,
        locked=sweep_lock_held(study_id, results=results),
        last_finished=max((a.ended_at for a in actual.values()), default=None),
        remaining=remaining,
    )


def _attempts(record: object) -> int | None:
    """Return the attempts a failure record holds, if it holds any."""
    value = record.get("attempts") if isinstance(record, dict) else None
    return value if isinstance(value, int) else None


def _finished_points(
    study: groups.StudyManifest,
    statuses: list[PointStatus],
    results: Path | None,
) -> dict[str, Actual]:
    """Read the actual cost of every point that has finished."""
    done = {status.run_id for status in statuses if status.state == "done"}
    found: dict[str, Actual] = {}
    for directory in groups.study_run_directories(study, results=results):
        run_id = groups.run_id_of(directory)
        if run_id in done and run_id not in found:
            actual = read_actual(run_id, directory)
            if actual is not None:
                found[run_id] = actual
    return found


def describe_remaining(status: SweepStatus) -> str:
    """Say in a sentence how much of the sweep is left and how long it may take.

    Args:
        status: A snapshot from `read_sweep_status`.

    Returns:
        For example `"about 2 h 10 min for 14 point(s), from the speed of the
        6 finished point(s) (an upper figure: ...)"`, or why there is no
        estimate yet.
    """
    remaining = status.remaining
    if remaining is None:
        return (
            "unknown: the stored plan no longer matches this version "
            f"(fim sweep replan {status.study_id})"
        )
    if remaining.points == 0:
        return "none: every point is done"
    if remaining.calibrated_seconds is None:
        return (
            f"not yet known for {remaining.points} point(s): the first finished "
            "point calibrates it"
        )
    return (
        f"about {format_duration(remaining.calibrated_seconds)} for "
        f"{remaining.points} point(s), from the speed of the "
        f"{remaining.finished_points} finished point(s) (an upper figure: points "
        "that settle early finish sooner)"
    )
