"""Close and reopen Studies and Experiments, and summarize their Runs.

Run lifecycle design §6.3 (`20261006-gpt-6.1-sol-run-lifecycle-and-
checkpoint-design.md`, `selby/restricted`). A group never has an
execution state machine of its own: its summary is derived on every
read from the lifecycle records of the distinct Run objects it reaches,
plus the sweep points that have no Run object yet. A Run shared by two
Studies of one Experiment counts once in the Experiment's summary.

Closing a group (`fim.persistence.groups.ClosedGroupError`) freezes its
organization only. It is refused while any Run the group reaches is
active, so a group never reads closed while work still lands in it, and
it never stops, pauses, or freezes a Run.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from fim.persistence import groups
from fim.persistence.groups import ExperimentManifest, StudyManifest
from fim.persistence.object_lock import is_lock_held
from fim.persistence.run_lifecycle import (
    ACTIVE_STATES,
    LifecycleError,
    RunState,
    is_object_id,
)
from fim.persistence.run_objects import (
    OBJECTS_DIRECTORY_NAME,
    RunObject,
    read_run_object,
)
from fim.persistence.run_ownership import attempt_lock_path
from fim.sweep_run import read_point_objects, sweep_point_statuses

logger = logging.getLogger(__name__)

LEGACY_STATE: Final = RunState.COMPLETED.value
"""The state a legacy run directory (one with no lifecycle record) reads."""

_UNMATERIALIZED_POINT_STATES: Final = frozenset({"waiting", "failed"})
"""Sweep point states that, with no linked object, mean no Run exists yet."""


class GroupBusyError(ValueError):
    """Refuse to close a group while a Run it reaches is active.

    A `ValueError` subclass, so the CLI and GUI report it like every other
    refused group edit.
    """


@dataclass(frozen=True, slots=True)
class GroupSummary:
    """A group's execution summary, derived from its distinct Runs.

    Args:
        states: How many distinct Runs are in each effective lifecycle
            state, keyed by `RunState` value. A legacy run directory counts
            as `completed`; an active Run whose owner is gone counts as
            `interrupted`.
        runs: How many distinct Runs the group reaches.
        unmaterialized: Planned sweep points with no Run object and no
            finished run yet.
        active: How many distinct Runs are executing right now.
    """

    states: Mapping[str, int] = field(default_factory=dict)
    runs: int = 0
    unmaterialized: int = 0
    active: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-ready summary, states in lifecycle order."""
        order = [state.value for state in RunState]
        return {
            "states": {
                name: self.states[name] for name in sorted(self.states, key=order.index)
            },
            "runs": self.runs,
            "unmaterialized": self.unmaterialized,
            "active": self.active,
        }


def close_experiment(
    experiment_id: str, *, results: Path | None = None
) -> ExperimentManifest:
    """Close an Experiment once no Run of any of its Studies is active.

    Its Studies stay open or closed exactly as they were.

    Raises:
        ValueError: No Experiment with this id exists.
        ReadOnlyError: The Experiment is read-only.
        GroupBusyError: A Run of one of its Studies is executing.
    """
    experiment = groups.get_experiment(experiment_id, results=results)
    _refuse_busy(
        experiment.name, experiment_id, experiment_summary(experiment, results=results)
    )
    return groups.set_experiment_closed(experiment_id, True, results=results)


def close_study(study_id: str, *, results: Path | None = None) -> StudyManifest:
    """Close a Study once none of its Runs is active.

    Raises:
        ValueError: No Study with this id exists.
        ReadOnlyError: The Study is read-only.
        GroupBusyError: One of its Runs is executing.
    """
    study = groups.get_study(study_id, results=results)
    _refuse_busy(study.name, study_id, study_summary(study, results=results))
    return groups.set_study_closed(study_id, True, results=results)


def effective_state(run_object: RunObject, results: Path | None = None) -> RunState:
    """Return a Run's lifecycle state as a reader should report it.

    Read-only: an active Run whose attempt lock is free has lost its owner
    and reads `interrupted` without being reconciled here.
    """
    state = run_object.record.state
    if state in ACTIVE_STATES and not is_lock_held(
        attempt_lock_path(run_object.object_id, results)
    ):
        return RunState.INTERRUPTED
    return state


def experiment_summary(
    experiment: ExperimentManifest, *, results: Path | None = None
) -> GroupSummary:
    """Summarize every distinct Run the Experiment's Studies reach.

    A Study listed by the Experiment that no longer exists is skipped.
    """
    studies = []
    for study_id in experiment.study_ids:
        try:
            studies.append(groups.get_study(study_id, results=results))
        except ValueError as error:
            logger.debug("skipping missing study %s: %s", study_id, error)
    return _summarize(studies, results)


def reopen_experiment(
    experiment_id: str, *, results: Path | None = None
) -> ExperimentManifest:
    """Reopen an Experiment so its organization can change again.

    Raises:
        ValueError: No Experiment with this id exists.
        ReadOnlyError: The Experiment is read-only.
    """
    return groups.set_experiment_closed(experiment_id, False, results=results)


def reopen_study(study_id: str, *, results: Path | None = None) -> StudyManifest:
    """Reopen a Study so its membership can change again.

    Raises:
        ValueError: No Study with this id exists.
        ReadOnlyError: The Study is read-only.
    """
    return groups.set_study_closed(study_id, False, results=results)


def study_summary(study: StudyManifest, *, results: Path | None = None) -> GroupSummary:
    """Summarize every distinct Run the Study reaches.

    Members and the sweep's linked Run objects count once each, by object
    ID; a legacy member counts once by its directory. A Run object that
    cannot be read is left out rather than guessed at.
    """
    return _summarize([study], results)


def _member_key(directory: Path) -> str:
    """Return the identity a member directory is counted under."""
    if directory.parent.name == OBJECTS_DIRECTORY_NAME and is_object_id(directory.name):
        return directory.name
    return str(directory.resolve())


def _object_state(object_id: str, results: Path | None) -> RunState | None:
    """Read one Run object's effective state; `None` if it cannot be read."""
    try:
        return effective_state(read_run_object(object_id, results=results), results)
    except (LifecycleError, OSError, ValueError) as error:
        logger.debug("ignoring unreadable run object %s: %s", object_id, error)
        return None


def _refuse_busy(name: str, identifier: str, summary: GroupSummary) -> None:
    """Raise `GroupBusyError` when `summary` reports an active Run."""
    if summary.active:
        raise GroupBusyError(
            f"{name!r} ({identifier}) has {summary.active} run(s) in progress; "
            "pause or cancel them, or wait for them to finish, before closing it"
        )


def _summarize(studies: Iterable[StudyManifest], results: Path | None) -> GroupSummary:
    """Count the distinct Runs and unmaterialized points of `studies`."""
    seen: dict[str, str] = {}
    unmaterialized = 0
    for study in studies:
        for directory in groups.study_run_directories(study, results=results):
            key = _member_key(directory)
            if key in seen:
                continue
            if not is_object_id(key):
                seen[key] = LEGACY_STATE
            elif (state := _object_state(key, results)) is not None:
                seen[key] = state.value
        if study.sweep_spec is None:
            continue
        for object_id in read_point_objects(study.study_id, results=results).values():
            if object_id in seen:
                continue
            linked = _object_state(object_id, results)
            if linked is not None:
                seen[object_id] = linked.value
        try:
            statuses = sweep_point_statuses(study, results=results)
        except ValueError as error:
            logger.debug("skipping malformed sweep %s: %s", study.study_id, error)
            continue
        unmaterialized += sum(
            1
            for status in statuses
            if status.object_id is None and status.state in _UNMATERIALIZED_POINT_STATES
        )
    active = {state.value for state in ACTIVE_STATES}
    return GroupSummary(
        states=dict(Counter(seen.values())),
        runs=len(seen),
        unmaterialized=unmaterialized,
        active=sum(1 for state in seen.values() if state in active),
    )
