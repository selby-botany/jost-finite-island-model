"""Tests for closing Studies and Experiments and their derived summaries.

Run lifecycle design §6.3: a closed group freezes its organization, not
its Runs, and a group's execution summary is derived from the distinct
Run objects it reaches rather than stored as a second state machine.
"""

from __future__ import annotations

import itertools
import threading
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from fim import paths
from fim.group_lifecycle import (
    GroupBusyError,
    GroupSummary,
    close_experiment,
    close_study,
    effective_state,
    experiment_summary,
    reopen_experiment,
    reopen_study,
    study_summary,
)
from fim.persistence import groups
from fim.persistence.groups import ClosedGroupError, ReadOnlyError
from fim.persistence.run_lifecycle import RunEvent, RunPhase, RunState
from fim.persistence.run_objects import (
    RunObject,
    create_run_object,
    read_run_object,
    write_run_record,
)
from fim.persistence.run_ownership import ExecutionLease, start_run_object
from fim.sweep import SweepSpec, enumerate_points, expand_axis
from fim.sweep_run import (
    LocalPointRunner,
    attach_sweep_to_study,
    create_sweep_study,
    run_sweep,
)

_AT = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)

_VALID: dict[str, object] = {
    "N": 20,
    "ploidy": "haploid",
    "d": 2,
    "m": 0.1,
    "mu": 0.01,
    "seed": 20261011,
    "precision": 1.0,
    "max_generations": 10,
}

_SWEEP_BASE: dict[str, Any] = {
    "N": 16,
    "ploidy": "diploid",
    "d": 2,
    "m": 0.1,
    "mu": 0.01,
    "seed": 7,
    "n_replicates": 1,
    "max_generations": 10,
}


@pytest.fixture
def results(tmp_path: Path) -> Iterator[Path]:
    """Point the results directory at a temporary one for the test."""
    root = tmp_path / "results"
    root.mkdir()
    paths.set_results_directory_override(root)
    try:
        yield root
    finally:
        paths.set_results_directory_override(None)


def _clock() -> datetime:
    """Return the fixed test time."""
    return _AT


def _object(results: Path, number: int) -> RunObject:
    """Create a planned Run object with a deterministic ID."""
    text = yaml.safe_dump({**_VALID, "seed": number}, sort_keys=False)
    return create_run_object(
        text,
        results=results,
        clock=_clock,
        id_factory=lambda: f"obj-{number:032x}",
    )


def _start(results: Path, run_object: RunObject) -> ExecutionLease:
    """Start `run_object`; the lease holds its attempt lock until released."""
    counter = itertools.count(1)
    lease = start_run_object(
        run_object.object_id,
        expected_revision=run_object.record.revision,
        results=results,
        clock=_clock,
        attempt_factory=lambda: f"att-{next(counter):032x}",
    ).lease
    assert lease is not None
    return lease


def _legacy_run(results: Path, name: str) -> Path:
    """Create a minimal legacy run directory with a manifest."""
    directory = results / name
    directory.mkdir()
    (directory / "manifest.json").write_text("{}", encoding="utf-8")
    return directory


def _study_with(results: Path, name: str, *members: Path) -> str:
    """Create a Study holding `members` and return its id."""
    study_id = groups.create_study(name).study_id
    for member in members:
        groups.add_run_to_study(study_id, member)
    return study_id


def _sweep(*values: int) -> tuple[SweepSpec, Any]:
    """Return a one-axis sweep over `d` and its plan."""
    spec = SweepSpec(base=dict(_SWEEP_BASE), axes=(expand_axis("d", list(values)),))
    return spec, enumerate_points(spec)


# -- open/closed flag -------------------------------------------------------


def test_a_legacy_group_manifest_reads_open(results: Path) -> None:
    study_id = groups.create_study("Legacy").study_id
    experiment_id = groups.create_experiment("Legacy").experiment_id

    assert "closed" not in groups.get_study(study_id).to_dict()
    assert "closed" not in groups.get_experiment(experiment_id).to_dict()
    assert not groups.get_study(study_id).closed
    assert not groups.get_experiment(experiment_id).closed


def test_closing_is_recorded_and_reopening_clears_it(results: Path) -> None:
    study_id = groups.create_study("Ring").study_id

    closed = close_study(study_id)

    assert closed.closed
    assert groups.get_study(study_id).to_dict()["closed"] is True
    assert close_study(study_id) == groups.get_study(study_id)
    reopened = reopen_study(study_id)
    assert not reopened.closed
    assert "closed" not in groups.get_study(study_id).to_dict()


def test_a_closed_study_refuses_every_membership_change(results: Path) -> None:
    run_a = _legacy_run(results, "run-a")
    run_b = _legacy_run(results, "run-b")
    study_id = _study_with(results, "Ring", run_a)
    close_study(study_id)

    actions: tuple[Callable[[], object], ...] = (
        lambda: groups.add_run_to_study(study_id, run_b),
        lambda: groups.remove_run_from_study(study_id, run_a),
        lambda: groups.clear_study_runs(study_id),
        lambda: groups.delete_study(study_id),
    )
    for action in actions:
        with pytest.raises(ClosedGroupError, match="reopen it first"):
            action()

    assert groups.get_study(study_id).run_directories == ("run-a",)
    assert run_a.exists()


def test_a_closed_study_still_accepts_notes_and_an_existing_member(
    results: Path,
) -> None:
    run_a = _legacy_run(results, "run-a")
    study_id = _study_with(results, "Ring", run_a)
    close_study(study_id)

    groups.add_run_to_study(study_id, run_a)
    renamed = groups.update_study_details(
        study_id, name="Ring, final", description="Done.", documentation=None
    )

    assert renamed.name == "Ring, final"
    assert renamed.closed


def test_closing_a_study_does_not_freeze_a_run_it_shares(results: Path) -> None:
    run_a = _legacy_run(results, "run-a")
    closed_id = _study_with(results, "Closed", run_a)
    open_id = _study_with(results, "Open", run_a)
    close_study(closed_id)

    groups.remove_run_references([run_a])

    assert groups.get_study(closed_id).run_directories == ()
    assert groups.get_study(open_id).run_directories == ()


def test_a_closed_experiment_refuses_new_studies_and_deletion(results: Path) -> None:
    experiment_id = groups.create_experiment("Topology").experiment_id
    member = groups.create_study("Ring").study_id
    groups.add_study_to_experiment(experiment_id, member)
    outsider = groups.create_study("Star").study_id
    close_experiment(experiment_id)

    groups.add_study_to_experiment(experiment_id, member)
    with pytest.raises(ClosedGroupError):
        groups.add_study_to_experiment(experiment_id, outsider)
    with pytest.raises(ClosedGroupError):
        groups.delete_experiment(experiment_id)

    reopen_experiment(experiment_id)
    groups.delete_experiment(experiment_id)
    assert groups.get_study(outsider).study_id == outsider


def test_deleting_an_experiment_keeps_a_closed_study(results: Path) -> None:
    run_a = _legacy_run(results, "run-a")
    experiment_id = groups.create_experiment("Topology").experiment_id
    kept = _study_with(results, "Closed", run_a)
    groups.add_study_to_experiment(experiment_id, kept)
    close_study(kept)

    groups.delete_experiment(experiment_id)

    assert groups.get_study(kept).closed
    assert run_a.exists()


def test_the_default_groups_always_stay_open(results: Path) -> None:
    groups.ensure_default_study()

    with pytest.raises(ClosedGroupError, match="always stays open"):
        close_study(groups.DEFAULT_STUDY_ID)
    with pytest.raises(ClosedGroupError, match="always stays open"):
        close_experiment(groups.DEFAULT_EXPERIMENT_ID)
    assert not reopen_study(groups.DEFAULT_STUDY_ID).closed


def test_a_read_only_group_cannot_be_closed(results: Path) -> None:
    study_id = groups.write_read_only_study(
        "study-example", name="Example", run_directories=[]
    ).study_id

    with pytest.raises(ReadOnlyError):
        close_study(study_id)


# -- active-work validation -------------------------------------------------


def test_closing_is_refused_while_a_member_run_is_running(results: Path) -> None:
    planned = _object(results, 1)
    study_id = _study_with(results, "Ring", planned.directory)
    experiment_id = groups.create_experiment("Topology").experiment_id
    groups.add_study_to_experiment(experiment_id, study_id)
    lease = _start(results, planned)
    try:
        with pytest.raises(GroupBusyError, match="1 run"):
            close_study(study_id)
        with pytest.raises(GroupBusyError):
            close_experiment(experiment_id)
        assert not groups.get_study(study_id).closed
        running = read_run_object(planned.object_id, results=results)
        assert running.record.state is RunState.RUNNING
    finally:
        lease.finish(RunEvent.COMPLETE, phase=RunPhase.FINALIZING)

    assert close_study(study_id).closed
    assert close_experiment(experiment_id).closed


def test_a_running_record_whose_owner_is_gone_reads_interrupted(
    results: Path,
) -> None:
    planned = _object(results, 1)
    write_run_record(
        planned.directory,
        replace(
            planned.record, state=RunState.RUNNING, active_attempt_id=f"att-{'0' * 32}"
        ),
    )
    study_id = _study_with(results, "Ring", planned.directory)
    stored = read_run_object(planned.object_id, results=results)

    assert effective_state(stored, results) is RunState.INTERRUPTED
    summary = study_summary(groups.get_study(study_id))
    assert summary.states == {"interrupted": 1}
    assert summary.active == 0
    assert close_study(study_id).closed


# -- derived summaries ------------------------------------------------------


def test_a_study_summary_counts_objects_by_state_and_legacy_runs_as_completed(
    results: Path,
) -> None:
    planned = _object(results, 1)
    draft = create_run_object(
        "N: [\n", results=results, clock=_clock, id_factory=lambda: f"obj-{9:032x}"
    )
    legacy = _legacy_run(results, "run-a")
    study_id = _study_with(results, "Ring", planned.directory, draft.directory, legacy)

    summary = study_summary(groups.get_study(study_id))

    assert summary == GroupSummary(
        states={"planned": 1, "draft": 1, "completed": 1},
        runs=3,
        unmaterialized=0,
        active=0,
    )
    assert list(summary.to_dict()["states"]) == ["draft", "planned", "completed"]


def test_an_unreadable_member_object_is_left_out_of_the_summary(
    results: Path,
) -> None:
    planned = _object(results, 1)
    study_id = _study_with(results, "Ring", planned.directory)
    (planned.directory / "run.json").write_text("{", encoding="utf-8")

    assert study_summary(groups.get_study(study_id)).runs == 0


def test_an_experiment_counts_a_run_shared_by_two_studies_once(
    results: Path,
) -> None:
    shared = _object(results, 1)
    own = _object(results, 2)
    first = _study_with(results, "Ring", shared.directory)
    second = _study_with(results, "Star", shared.directory, own.directory)
    experiment_id = groups.create_experiment("Topology").experiment_id
    groups.add_study_to_experiment(experiment_id, first)
    groups.add_study_to_experiment(experiment_id, second)
    groups.add_study_to_experiment(experiment_id, groups.create_study("Gone").study_id)
    groups.study_manifest_path(groups.list_studies()[-1].study_id).unlink()

    summary = experiment_summary(groups.get_experiment(experiment_id))

    assert summary.runs == 2
    assert summary.states == {"planned": 2}
    assert study_summary(groups.get_study(second)).runs == 2


def test_a_sweep_summary_counts_points_with_no_run_yet(results: Path) -> None:
    spec, plan = _sweep(2, 3, 4)
    study_id = create_sweep_study(spec, plan, "Sweep").study_id

    before = study_summary(groups.get_study(study_id))
    assert (before.runs, before.unmaterialized) == (0, 3)

    run_sweep(study_id, LocalPointRunner(), lambda _: None, threading.Event())

    after = study_summary(groups.get_study(study_id))
    assert after.states == {"completed": 3}
    assert (after.runs, after.unmaterialized) == (3, 0)


def test_a_paused_sweep_point_counts_as_its_linked_object(results: Path) -> None:
    spec = SweepSpec(
        base={**_SWEEP_BASE, "max_generations": 400},
        axes=(expand_axis("d", [2, 3]),),
    )
    study_id = create_sweep_study(spec, enumerate_points(spec), "Sweep").study_id
    cancel = threading.Event()

    def cancel_at_first_progress(event: Any) -> None:
        if event.kind == "point_progress":
            cancel.set()

    run_sweep(
        study_id,
        LocalPointRunner(),
        cancel_at_first_progress,
        cancel,
        points_at_once=1,
    )

    summary = study_summary(groups.get_study(study_id))
    assert summary.states == {"paused": 1}
    assert (summary.runs, summary.unmaterialized) == (1, 1)


def test_a_malformed_sweep_is_summarized_by_its_members_alone(
    results: Path,
) -> None:
    legacy = _legacy_run(results, "run-a")
    study_id = _study_with(results, "Broken", legacy)
    groups.write_study_manifest(
        groups.study_manifest_path(study_id),
        replace(groups.get_study(study_id), sweep_spec={"points": "nonsense"}),
    )

    summary = study_summary(groups.get_study(study_id))

    assert (summary.runs, summary.unmaterialized) == (1, 0)


# -- sweeps and closed Studies ----------------------------------------------


def test_a_closed_sweep_study_cannot_be_continued(results: Path) -> None:
    spec, plan = _sweep(2)
    study_id = create_sweep_study(spec, plan, "Sweep").study_id
    close_study(study_id)

    with pytest.raises(ClosedGroupError):
        run_sweep(study_id, LocalPointRunner(), lambda _: None, threading.Event())

    assert groups.get_study(study_id).run_directories == ()


def test_a_closed_study_cannot_be_given_a_sweep(results: Path) -> None:
    spec, plan = _sweep(2)
    study_id = groups.create_study("Ring").study_id
    close_study(study_id)

    with pytest.raises(ClosedGroupError):
        attach_sweep_to_study(study_id, spec, plan)

    assert groups.get_study(study_id).sweep_spec is None


def test_a_sweep_into_a_closed_experiment_leaves_no_study_behind(
    results: Path,
) -> None:
    spec, plan = _sweep(2)
    experiment_id = groups.create_experiment("Topology").experiment_id
    close_experiment(experiment_id)

    with pytest.raises(ClosedGroupError):
        create_sweep_study(spec, plan, "Sweep", experiment_id=experiment_id)

    assert groups.list_studies() == []
