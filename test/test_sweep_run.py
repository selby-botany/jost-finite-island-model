"""Tests for `fim.sweep_run`: creating, running, resuming and reusing sweeps.

Points are real but tiny runs (small populations, a handful of
generations), so the run ids, manifests and attachments are the real ones.
"""

from __future__ import annotations

import json
import shutil
import threading
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from fim import paths
from fim.model.params import SimulationParams
from fim.persistence import groups
from fim.sweep import SweepSpec, enumerate_points, expand_axis
from fim.sweep_run import (
    LocalPointRunner,
    PointFailure,
    SweepEvent,
    create_sweep_study,
    read_failures,
    run_sweep,
    stored_points,
    sweep_point_results,
    sweep_point_statuses,
)

_BASE: dict[str, Any] = {
    "N": 16,
    "ploidy": 2,
    "d": 2,
    "m": 0.1,
    "mu": 0.01,
    "seed": 7,
    "n_replicates": 1,
    "max_generations": 10,
    "convergence_window": 3,
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


def _study(results: Path, *values: int, replicates: int = 1) -> str:
    """Create a sweep Study over `d` (`values`) and return its id."""
    spec = SweepSpec(
        base={**_BASE, "n_replicates": replicates},
        axes=(expand_axis("d", list(values)),),
    )
    return create_sweep_study(spec, enumerate_points(spec), "Test sweep").study_id


def _run(
    study_id: str,
    runner: Any = None,
    cancel: threading.Event | None = None,
    **options: Any,
) -> tuple[Any, list[SweepEvent]]:
    """Run a sweep, returning the outcome and every event."""
    events: list[SweepEvent] = []
    outcome = run_sweep(
        study_id,
        runner or LocalPointRunner(),
        events.append,
        cancel or threading.Event(),
        **options,
    )
    return outcome, events


def test_creating_a_sweep_stores_the_spec_and_plan_before_anything_runs(
    results: Path,
) -> None:
    study_id = _study(results, 2, 3)

    study = groups.get_study(study_id)

    assert study.run_directories == ()
    assert [p["coordinates"] for p in stored_points(study)] == [
        {"d": 2},
        {"d": 3},
    ]
    assert [s.state for s in sweep_point_statuses(study)] == ["waiting", "waiting"]


def test_a_sweep_with_no_valid_points_is_refused(results: Path) -> None:
    spec = SweepSpec(
        base={
            **_BASE,
            "m": {"topology": "torus", "rate": 0.1, "rows": 3, "columns": 4},
            "d": 12,
        },
        axes=(expand_axis("d", [5]),),
    )

    with pytest.raises(ValueError, match="no valid points"):
        create_sweep_study(spec, enumerate_points(spec), "Nothing")


def test_a_sweep_can_be_created_inside_an_experiment(results: Path) -> None:
    experiment = groups.create_experiment("Differentiation")
    spec = SweepSpec(base=_BASE, axes=(expand_axis("d", [2]),))

    study = create_sweep_study(
        spec,
        enumerate_points(spec),
        "In an experiment",
        experiment_id=experiment.experiment_id,
    )

    assert groups.get_experiment(experiment.experiment_id).study_ids == (
        study.study_id,
    )


def test_an_unknown_experiment_leaves_no_study_behind(results: Path) -> None:
    spec = SweepSpec(base=_BASE, axes=(expand_axis("d", [2]),))

    with pytest.raises(ValueError, match="experiment"):
        create_sweep_study(
            spec, enumerate_points(spec), "Orphan", experiment_id="experiment-nope"
        )

    assert groups.list_studies() == []


def test_running_a_sweep_attaches_one_ordinary_run_per_point(results: Path) -> None:
    study_id = _study(results, 2, 3)

    outcome, events = _run(study_id)

    assert (outcome.done, outcome.failed, outcome.cancelled) == (2, 0, False)
    study = groups.get_study(study_id)
    assert study.run_count == 2
    assert [s.state for s in sweep_point_statuses(study)] == ["done", "done"]
    assert [e.kind for e in events if e.kind != "point_progress"] == [
        "point_started",
        "point_done",
        "point_started",
        "point_done",
        "sweep_done",
    ]
    ids = {
        groups.run_id_of(directory) for directory in groups.study_run_directories(study)
    }
    assert ids == {p["run_id"] for p in stored_points(study)}


def test_a_batch_point_is_a_whole_batch(results: Path) -> None:
    study_id = _study(results, 2, replicates=2)

    outcome, _ = _run(study_id)

    assert outcome.done == 1
    directory = groups.study_run_directories(groups.get_study(study_id))[0]
    assert (directory / "manifest.json").is_file()
    assert any(child.is_dir() for child in directory.iterdir())


def test_running_again_recomputes_nothing(results: Path) -> None:
    study_id = _study(results, 2, 3)
    _run(study_id)

    outcome, events = _run(study_id)

    assert (outcome.done, outcome.already_present) == (0, 2)
    assert [e.kind for e in events] == ["sweep_done"]
    assert len(list(results.glob("*/manifest.json"))) == 2


def test_resuming_after_a_cancel_finishes_only_what_is_missing(results: Path) -> None:
    study_id = _study(results, 2, 3, 4)
    cancel = threading.Event()
    seen: list[str] = []

    def stop_after_first(event: SweepEvent) -> None:
        seen.append(event.kind)
        if event.kind == "point_done":
            cancel.set()

    first = run_sweep(study_id, LocalPointRunner(), stop_after_first, cancel)

    assert (first.done, first.cancelled) == (1, True)
    assert seen[-1] == "sweep_cancelled"
    assert [s.state for s in sweep_point_statuses(groups.get_study(study_id))] == [
        "done",
        "waiting",
        "waiting",
    ]

    second, _ = _run(study_id)

    assert (second.done, second.already_present, second.cancelled) == (2, 1, False)
    assert len(list(results.glob("*/manifest.json"))) == 3


def test_a_second_sweep_reuses_the_points_the_first_computed(results: Path) -> None:
    first = _study(results, 2, 3)
    _run(first)
    second = _study(results, 2, 3)

    outcome, events = _run(second)

    assert (outcome.done, outcome.reused) == (0, 2)
    assert [e.kind for e in events if e.kind == "point_reused"] == ["point_reused"] * 2
    assert len(list(results.glob("*/manifest.json"))) == 2
    assert groups.get_study(second).run_count == 2


def test_a_deleted_run_makes_its_point_waiting_again(results: Path) -> None:
    study_id = _study(results, 2)
    _run(study_id)
    shutil.rmtree(groups.study_run_directories(groups.get_study(study_id))[0])

    assert [s.state for s in sweep_point_statuses(groups.get_study(study_id))] == [
        "waiting"
    ]


class _Failing:
    """A runner whose points fail (or fail for chosen indices)."""

    def __init__(self, fail_when: Callable[[SimulationParams], bool]) -> None:
        self.fail_when = fail_when
        self.local = LocalPointRunner()
        self.calls = 0

    def run_point(
        self,
        params: SimulationParams,
        cancel_event: threading.Event,
        on_message: Callable[[object], None] | None = None,
    ) -> Path | PointFailure:
        self.calls += 1
        if self.fail_when(params):
            return PointFailure("engine exploded")
        return self.local.run_point(params, cancel_event, on_message)


def test_a_failed_point_is_recorded_and_the_sweep_continues(results: Path) -> None:
    study_id = _study(results, 2, 3)
    runner = _Failing(lambda params: params.d == 2)

    outcome, events = _run(study_id, runner)

    assert (outcome.done, outcome.failed) == (1, 1)
    statuses = sweep_point_statuses(groups.get_study(study_id))
    assert [(s.state, s.reason) for s in statuses] == [
        ("failed", "engine exploded"),
        ("done", None),
    ]
    assert any(
        e.kind == "point_failed" and e.detail == "engine exploded" for e in events
    )
    assert len(read_failures(study_id)) == 1


def test_a_recorded_failure_survives_and_is_skipped_unless_retried(
    results: Path,
) -> None:
    study_id = _study(results, 2, 3)
    _run(study_id, _Failing(lambda params: params.d == 2))

    skipped = _Failing(lambda _params: False)
    outcome, _ = _run(study_id, skipped)

    assert (outcome.skipped_failed, outcome.done, skipped.calls) == (1, 0, 0)

    retried, _ = _run(study_id, _Failing(lambda _params: False), retry_failed=True)

    assert (retried.done, retried.failed) == (1, 0)
    assert read_failures(study_id) == {}
    assert [s.state for s in sweep_point_statuses(groups.get_study(study_id))] == [
        "done",
        "done",
    ]


def test_the_failure_file_is_not_mistaken_for_a_study(results: Path) -> None:
    study_id = _study(results, 2)
    _run(study_id, _Failing(lambda _params: True))

    assert [study.study_id for study in groups.list_studies()] == [study_id]


def test_a_stored_plan_that_no_longer_matches_is_refused(results: Path) -> None:
    study_id = _study(results, 2)
    manifest_path = groups.study_manifest_path(study_id)
    study = groups.read_study_manifest(manifest_path)
    tampered = dict(study.sweep_spec or {})
    tampered["points"] = [{**stored_points(study)[0], "run_id": "run-0000000000000000"}]
    groups.write_study_manifest(manifest_path, replace(study, sweep_spec=tampered))

    with pytest.raises(ValueError, match="no longer matches"):
        _run(study_id)


def test_running_a_study_that_is_not_a_sweep_is_refused(results: Path) -> None:
    study = groups.create_study("By hand")

    with pytest.raises(ValueError, match="not a sweep"):
        _run(study.study_id)


def test_point_results_read_a_batch_summary_and_a_scalar_report(results: Path) -> None:
    batch = _study(results, 2, replicates=2)
    scalar = _study(results, 3)
    _run(batch)
    _run(scalar)

    (batch_point,) = sweep_point_results(groups.get_study(batch))
    (scalar_point,) = sweep_point_results(groups.get_study(scalar))

    assert batch_point.coordinates == {"d": 2}
    assert batch_point.n_replicates == 2
    assert set(batch_point.statistics["D"]) == {"mean", "low", "high"}
    assert batch_point.statistics["D"]["low"] is not None
    assert scalar_point.n_replicates == 1
    assert scalar_point.statistics["D"]["low"] is None
    assert scalar_point.statistics["D"]["mean"] is not None


def test_point_results_skip_points_that_have_not_run(results: Path) -> None:
    study_id = _study(results, 2, 3)

    assert sweep_point_results(groups.get_study(study_id)) == []


_OTHER_VERSION = "9.9.9"


def test_a_run_from_another_software_version_is_stale_not_done(results: Path) -> None:
    study_id = _study(results, 2)
    _run(study_id)
    study = groups.get_study(study_id)

    assert [s.state for s in sweep_point_statuses(study)] == ["done"]
    assert [
        s.state for s in sweep_point_statuses(study, software_version=_OTHER_VERSION)
    ] == ["stale"]


def test_a_new_version_recomputes_and_a_matching_result_replaces_the_old_run(
    results: Path,
) -> None:
    study_id = _study(results, 2, 3)
    _run(study_id)
    old = set(groups.study_run_directories(groups.get_study(study_id)))

    outcome, events = _run(study_id, software_version=_OTHER_VERSION)

    assert (outcome.recomputed, outcome.differing, outcome.done) == (2, 0, 0)
    assert [e.kind for e in events if e.kind.startswith("point_")].count(
        "point_recomputed"
    ) == 2
    now = set(groups.study_run_directories(groups.get_study(study_id)))
    assert len(now) == 2
    assert not (old & now)
    assert not any(directory.exists() for directory in old)
    assert len(list(results.glob("*/manifest.json"))) == 2
    recomputed = next(e for e in events if e.kind == "point_recomputed")
    assert recomputed.detail["identical"] is True  # type: ignore[index]


class _Tampering:
    """Recomputes a point, then breaks the new run's recorded trajectory digest."""

    def __init__(self) -> None:
        self.local = LocalPointRunner()

    def run_point(
        self,
        params: SimulationParams,
        cancel_event: threading.Event,
        on_message: Callable[[object], None] | None = None,
    ) -> Path | PointFailure:
        result = self.local.run_point(params, cancel_event, on_message)
        if isinstance(result, Path):
            manifest_path = result / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifacts"]["trajectory"]["sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return result


def test_a_recomputed_result_that_differs_is_reported_and_both_runs_are_kept(
    results: Path,
) -> None:
    study_id = _study(results, 2)
    _run(study_id)
    (old,) = groups.study_run_directories(groups.get_study(study_id))

    outcome, events = _run(study_id, _Tampering(), software_version=_OTHER_VERSION)

    assert (outcome.differing, outcome.recomputed) == (1, 0)
    differs = next(e for e in events if e.kind == "point_differs")
    assert differs.detail["identical"] is False  # type: ignore[index]
    assert differs.detail["differences"][0]["label"] == "trajectory"  # type: ignore[index]
    members = groups.study_run_directories(groups.get_study(study_id))
    assert old in members
    assert len(members) == 2
    (new,) = [m for m in members if m != old]
    assert (
        json.loads((new / "reproducibility.json").read_text("utf-8"))["identical"]
        is False
    )
