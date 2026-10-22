"""Tests for `fim.sweep_run`: creating, running, resuming and reusing sweeps.

Points are real but tiny runs (small populations, a handful of
generations), so the run ids, manifests and attachments are the real ones.
"""

from __future__ import annotations

import itertools
import json
import multiprocessing
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import Executor, Future
from concurrent.futures.process import BrokenProcessPool
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from conftest import COMPLETION_BACKSTOP_SECONDS, backstop_message, join_or_fail

from fim import __version__, paths
from fim import sweep_run as sweep_run_module
from fim.execution_priority import Priority, initialize_pool_worker
from fim.gui.sweep_bridge import status_payload
from fim.model.params import SimulationParams
from fim.persistence import groups
from fim.persistence.object_lock import exclusive_lock
from fim.persistence.run_lifecycle import RunState
from fim.persistence.run_metadata import read_run_metadata, run_metadata_path
from fim.persistence.run_objects import (
    create_run_object,
    list_run_objects,
    read_run_object,
    write_run_record,
)
from fim.reproducibility import compare_runs
from fim.sweep import SweepSpec, enumerate_points, expand_axis
from fim.sweep_run import (
    Concurrency,
    FailureClass,
    LocalPointRunner,
    PointFailure,
    ProcessPointRunner,
    SweepBudget,
    SweepEvent,
    SweepLockedError,
    classify_failure,
    completed_run_ids,
    create_sweep_study,
    failures_path,
    read_failures,
    read_point_objects,
    read_verification,
    replan_sweep,
    resolve_concurrency,
    run_sweep,
    stored_points,
    sweep_lock_held,
    sweep_lock_path,
    sweep_point_results,
    sweep_point_statuses,
    sweep_spec_of,
    verification_path,
)

_BASE: dict[str, Any] = {
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


def _manifest_count(results: Path) -> int:
    """Count the finished runs under `results`, legacy and Run object alike."""
    return len(list(results.glob("*/manifest.json"))) + len(
        list(results.glob("objects/*/manifest.json"))
    )


def _study(results: Path, *values: int, replicates: int = 1, **base: Any) -> str:
    """Create a sweep Study over `d` (`values`) and return its id."""
    spec = SweepSpec(
        base={**_BASE, "n_replicates": replicates, **base},
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

    # One at a time, so the events arrive in a fixed order.
    outcome, events = _run(study_id, points_at_once=1)

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
    assert _manifest_count(results) == 2


def test_resuming_after_a_cancel_finishes_only_what_is_missing(results: Path) -> None:
    study_id = _study(results, 2, 3, 4)
    cancel = threading.Event()
    seen: list[str] = []

    def stop_after_first(event: SweepEvent) -> None:
        seen.append(event.kind)
        if event.kind == "point_done":
            cancel.set()

    first = run_sweep(
        study_id, LocalPointRunner(), stop_after_first, cancel, points_at_once=1
    )

    assert (first.done, first.cancelled) == (1, True)
    assert seen[-1] == "sweep_cancelled"
    assert [s.state for s in sweep_point_statuses(groups.get_study(study_id))] == [
        "done",
        "waiting",
        "waiting",
    ]

    second, _ = _run(study_id)

    assert (second.done, second.already_present, second.cancelled) == (2, 1, False)
    assert _manifest_count(results) == 3


def test_a_second_sweep_reuses_the_points_the_first_computed(results: Path) -> None:
    first = _study(results, 2, 3)
    _run(first)
    second = _study(results, 2, 3)

    outcome, events = _run(second)

    assert (outcome.done, outcome.reused) == (0, 2)
    assert [e.kind for e in events if e.kind == "point_reused"] == ["point_reused"] * 2
    assert _manifest_count(results) == 2
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
        max_workers: int | None = None,
    ) -> Path | PointFailure:
        self.calls += 1
        if self.fail_when(params):
            return PointFailure("engine exploded")
        return self.local.run_point(params, cancel_event, on_message, max_workers)


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
    assert batch_point.statistics["D"].low is not None
    assert batch_point.statistics["D"].sample_count == 2
    assert scalar_point.n_replicates == 1
    assert scalar_point.statistics["D"].low is None
    assert scalar_point.statistics["D"].mean is not None


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
    assert _manifest_count(results) == 2
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
        max_workers: int | None = None,
    ) -> Path | PointFailure:
        result = self.local.run_point(params, cancel_event, on_message, max_workers)
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


def test_rerun_recomputes_completed_points_and_a_match_replaces_the_old_run(
    results: Path,
) -> None:
    study_id = _study(results, 2, 3)
    _run(study_id)
    old = set(groups.study_run_directories(groups.get_study(study_id)))

    outcome, events = _run(study_id, rerun=True)

    assert (outcome.recomputed, outcome.differing, outcome.already_present) == (2, 0, 0)
    assert outcome.done == 0
    assert [e.kind for e in events].count("point_recomputed") == 2
    now = set(groups.study_run_directories(groups.get_study(study_id)))
    assert len(now) == 2
    assert not (old & now)
    assert not any(directory.exists() for directory in old)
    assert _manifest_count(results) == 2


def test_rerun_that_differs_keeps_both_runs_and_reports_it(results: Path) -> None:
    study_id = _study(results, 2)
    _run(study_id)
    (old,) = groups.study_run_directories(groups.get_study(study_id))

    outcome, events = _run(study_id, _Tampering(), rerun=True)

    assert (outcome.differing, outcome.recomputed) == (1, 0)
    assert any(e.kind == "point_differs" for e in events)
    members = groups.study_run_directories(groups.get_study(study_id))
    assert old in members
    assert len(members) == 2


def test_rerun_still_runs_the_points_that_are_missing(results: Path) -> None:
    study_id = _study(results, 2, 3, 4)
    cancel = threading.Event()

    def stop_after_first(event: SweepEvent) -> None:
        if event.kind == "point_done":
            cancel.set()

    run_sweep(study_id, LocalPointRunner(), stop_after_first, cancel, points_at_once=1)
    (finished,) = groups.study_run_directories(groups.get_study(study_id))

    outcome, _ = _run(study_id, rerun=True)

    # The one completed point is recomputed; the two missing ones are run.
    assert (outcome.recomputed, outcome.done) == (1, 2)
    members = groups.study_run_directories(groups.get_study(study_id))
    assert len(members) == 3
    assert finished not in members


def test_without_rerun_a_completed_point_is_left_alone(results: Path) -> None:
    study_id = _study(results, 2, 3)
    _run(study_id)
    before = set(groups.study_run_directories(groups.get_study(study_id)))

    outcome, _ = _run(study_id)

    assert (outcome.recomputed, outcome.already_present) == (0, 2)
    assert set(groups.study_run_directories(groups.get_study(study_id))) == before


def _params(**overrides: Any) -> SimulationParams:
    return SimulationParams.from_mapping({**_BASE, **overrides})


def test_auto_concurrency_fills_the_cores_by_what_each_point_needs() -> None:
    # A single run needs one core: as many points as cores (capped by points).
    assert resolve_concurrency(points=30, params=_params(), cores=10) == Concurrency(
        10, None, 10
    )
    assert resolve_concurrency(points=4, params=_params(), cores=10).points_at_once == 4
    # A batch of 4 replicates needs 4 workers: 2 points fit on 10 cores.
    batch = resolve_concurrency(points=30, params=_params(n_replicates=4), cores=10)
    assert (batch.points_at_once, batch.workers_per_point) == (2, 4)
    # A batch that already fills the machine runs one point at a time.
    wide = resolve_concurrency(points=30, params=_params(n_replicates=200), cores=10)
    assert (wide.points_at_once, wide.workers_per_point) == (1, 10)


def test_concurrency_respects_a_worker_limit_and_a_request() -> None:
    limited = resolve_concurrency(
        points=30, params=_params(n_replicates=4), max_workers=2, cores=10
    )
    assert (limited.points_at_once, limited.workers_per_point) == (5, 2)
    asked = resolve_concurrency(
        points=30, params=_params(n_replicates=4), requested=3, cores=10
    )
    assert (asked.points_at_once, asked.workers_per_point) == (3, 3)
    assert resolve_concurrency(points=2, params=_params(), requested=8, cores=10) == (
        Concurrency(2, None, 10)
    )
    one = resolve_concurrency(points=9, params=_params(n_replicates=4), cores=1)
    assert (one.points_at_once, one.workers_per_point) == (1, 1)


class _Barrier:
    """A runner that only finishes once `parties` points are in flight together.

    The barrier has no timing budget: a 30-second one made "not
    concurrent" a statement about how quickly a loaded machine scheduled
    the point threads. A sweep that runs points one at a time never fills
    the barrier; only `conftest.COMPLETION_BACKSTOP_SECONDS`, a hang
    backstop far above any scheduling delay, ends that wait, and the
    point fails naming the barrier (`run_sweep` re-raises it).
    """

    def __init__(self, parties: int) -> None:
        self.barrier = threading.Barrier(parties, timeout=COMPLETION_BACKSTOP_SECONDS)
        self.local = LocalPointRunner()
        self.workers: list[int | None] = []

    def run_point(
        self,
        params: SimulationParams,
        cancel_event: threading.Event,
        on_message: Callable[[object], None] | None = None,
        max_workers: int | None = None,
    ) -> Path | PointFailure:
        self.workers.append(max_workers)
        try:
            self.barrier.wait()
        except threading.BrokenBarrierError as error:
            raise AssertionError(
                backstop_message("concurrency barrier (points in flight together)")
            ) from error
        return self.local.run_point(params, cancel_event, on_message, max_workers)


def test_points_really_run_at_the_same_time(results: Path) -> None:
    study_id = _study(results, 2, 3, 4)
    runner = _Barrier(3)

    outcome, events = _run(study_id, runner, points_at_once=3)

    # All three reached the barrier together, or `wait` would have failed.
    assert (outcome.done, outcome.failed) == (3, 0)
    finished = [e.position for e in events if e.kind == "point_done"]
    assert sorted(finished) == [1, 2, 3]
    assert events[-1].kind == "sweep_done"
    assert events[-1].position == 3
    assert len(groups.get_study(study_id).run_directories) == 3


def test_a_concurrent_sweep_matches_a_serial_one_byte_for_byte(
    results: Path, tmp_path: Path
) -> None:
    serial_id = _study(results, 2, 3, 4)
    _run(serial_id, points_at_once=1)
    serial = {
        groups.run_id_of(d): d
        for d in groups.study_run_directories(groups.get_study(serial_id))
    }
    second_root = tmp_path / "second"
    second_root.mkdir()
    paths.set_results_directory_override(second_root)
    concurrent_id = _study(second_root, 2, 3, 4)

    outcome, _ = _run(concurrent_id, points_at_once=3)

    assert outcome.done == 3
    concurrent = {
        groups.run_id_of(d): d
        for d in groups.study_run_directories(groups.get_study(concurrent_id))
    }
    assert serial.keys() == concurrent.keys()
    for run_id, directory in serial.items():
        assert compare_runs(directory, concurrent[run_id]).identical is True


def test_cancelling_a_concurrent_sweep_stops_it_and_keeps_finished_points(
    results: Path,
) -> None:
    study_id = _study(results, 2, 3, 4, 5, 6, 7)
    cancel = threading.Event()

    def stop_after_first(event: SweepEvent) -> None:
        if event.kind == "point_done":
            cancel.set()

    outcome = run_sweep(
        study_id,
        LocalPointRunner(),
        stop_after_first,
        cancel,
        points_at_once=2,
    )

    assert outcome.cancelled is True
    assert 1 <= outcome.done < 6
    assert groups.get_study(study_id).run_count == outcome.done


def test_the_worker_limit_reaches_each_batch_point(results: Path) -> None:
    study_id = _study(results, 2, 3, replicates=2)
    runner = _Barrier(1)

    _run(study_id, runner, points_at_once=1, max_workers=2)

    assert runner.workers == [2, 2]


def test_a_batch_on_a_non_lineal_engine_needs_one_core_so_points_fill_the_machine() -> (
    None
):
    for backend in ("auto", "generational"):
        chosen = resolve_concurrency(
            points=30,
            params=_params(n_replicates=200, engine_backend=backend),
            cores=10,
        )
        assert (chosen.points_at_once, chosen.workers_per_point) == (10, None)


def test_processes_run_the_points_of_a_sweep_and_the_runs_match_the_serial_ones(
    results: Path, tmp_path: Path
) -> None:
    serial_id = _study(results, 2, 3)
    _run(serial_id, points_at_once=1)
    serial = {
        groups.run_id_of(d): d
        for d in groups.study_run_directories(groups.get_study(serial_id))
    }
    second_root = tmp_path / "second"
    second_root.mkdir()
    paths.set_results_directory_override(second_root)
    process_id = _study(second_root, 2, 3)

    outcome, events = _run(process_id, points_at_once=2)

    assert (outcome.done, outcome.failed) == (2, 0)
    assert sorted(e.position for e in events if e.kind == "point_done") == [1, 2]
    assert not list(second_root.glob(".reserve-*"))
    for directory in groups.study_run_directories(groups.get_study(process_id)):
        assert directory.parent == (second_root / "objects").resolve()
        assert compare_runs(serial[groups.run_id_of(directory)], directory).identical


def test_run_directories_are_reserved_so_two_points_never_share_a_name(
    results: Path,
) -> None:
    first, first_marker = paths.allocate_output_directory()
    second, second_marker = paths.allocate_output_directory()

    assert first != second
    assert first_marker.is_dir() and second_marker.is_dir()
    assert first.parent == results
    first_marker.rmdir()
    second_marker.rmdir()


@pytest.mark.parametrize("fails", [False, True], ids=["published", "failed"])
def test_local_point_runner_releases_its_reservation(
    results: Path,
    monkeypatch: pytest.MonkeyPatch,
    fails: bool,
) -> None:
    """A point releases its marker after publishing or when its run fails."""
    del results
    runner = LocalPointRunner()
    output_directories: list[Path] = []

    def run(
        _self: LocalPointRunner,
        _params: SimulationParams,
        output_directory: Path,
        _cancel_event: threading.Event,
        _on_message: Callable[[object], None] | None,
        _max_workers: int | None,
    ) -> Path:
        """Publish a directory or raise after observing its reservation."""
        reservation = output_directory.parent / f".reserve-{output_directory.name}"
        assert reservation.is_dir()
        output_directories.append(output_directory)
        if fails:
            raise RuntimeError("point failed")
        output_directory.mkdir()
        return output_directory

    monkeypatch.setattr(LocalPointRunner, "_run", run)
    if fails:
        with pytest.raises(RuntimeError, match="point failed"):
            runner.run_point(_params(), threading.Event())
    else:
        assert runner.run_point(_params(), threading.Event()) == output_directories[0]

    assert len(output_directories) == 1
    output_directory = output_directories[0]
    assert not (output_directory.parent / f".reserve-{output_directory.name}").exists()


def _names_by_coordinate(study_id: str) -> dict[int, tuple[str | None, str | None]]:
    """Return each member run's metadata name and description, by its `d`."""
    study = groups.get_study(study_id)
    d_by_run_id = {
        point["run_id"]: point["coordinates"]["d"] for point in stored_points(study)
    }
    found: dict[int, tuple[str | None, str | None]] = {}
    for directory in groups.study_run_directories(study):
        metadata = read_run_metadata(run_metadata_path(directory))
        found[d_by_run_id[groups.run_id_of(directory)]] = (
            metadata.name,
            metadata.description,
        )
    return found


def test_each_point_run_is_named_after_its_study_and_coordinates(
    results: Path,
) -> None:
    """A sweep's runs say which sweep and which point they are."""
    study_id = _study(results, 2, 3)

    _run(study_id, points_at_once=1)

    assert _names_by_coordinate(study_id) == {
        2: (
            "Test sweep sweep d=2",
            'Point 1 of 2 of the sweep "Test sweep": d=2 of d=[2, 3].',
        ),
        3: (
            "Test sweep sweep d=3",
            'Point 2 of 2 of the sweep "Test sweep": d=3 of d=[2, 3].',
        ),
    }


def test_a_reused_run_keeps_the_name_it_already_has(results: Path) -> None:
    """A second sweep reusing a point does not rename the first sweep's run."""
    first = _study(results, 2)
    _run(first)
    second = create_sweep_study(
        SweepSpec(base=_BASE, axes=(expand_axis("d", [2]),)),
        enumerate_points(SweepSpec(base=_BASE, axes=(expand_axis("d", [2]),))),
        "Another sweep",
    ).study_id

    outcome, _ = _run(second)

    assert outcome.reused == 1
    assert _names_by_coordinate(second)[2][0] == "Test sweep sweep d=2"


def test_a_reused_run_without_a_name_is_named_by_the_sweep(results: Path) -> None:
    """A reused run nobody named (an ordinary single run) gets the sweep's name."""
    first = _study(results, 2)
    _run(first)
    for directory in groups.study_run_directories(groups.get_study(first)):
        run_metadata_path(directory).unlink()
    second = _study(results, 2)

    _run(second)

    assert _names_by_coordinate(second)[2][0] == "Test sweep sweep d=2"


def _read_only_study(results: Path, *values: int) -> str:
    """Create a sweep Study whose every point is a read-only run."""
    spec = SweepSpec(
        base={**_BASE, "_read_only": True}, axes=(expand_axis("d", list(values)),)
    )
    return create_sweep_study(spec, enumerate_points(spec), "Read-only sweep").study_id


def test_a_read_only_point_run_keeps_its_own_labels_and_the_sweep_succeeds(
    results: Path,
) -> None:
    """Naming a read-only run is refused; the sweep logs that and carries on."""
    study_id = _read_only_study(results, 2)

    outcome, _ = _run(study_id)

    assert (outcome.done, outcome.failed) == (1, 0)
    (directory,) = groups.study_run_directories(groups.get_study(study_id))
    assert groups.is_run_read_only(directory)
    assert not run_metadata_path(directory).exists()


def test_a_matching_recompute_of_a_read_only_run_keeps_the_old_run(
    results: Path,
) -> None:
    """A read-only run is never superseded: both runs stay, the sweep succeeds."""
    study_id = _read_only_study(results, 2)
    _run(study_id)
    (old,) = groups.study_run_directories(groups.get_study(study_id))

    outcome, events = _run(study_id, software_version=_OTHER_VERSION)

    assert (outcome.recomputed, outcome.failed) == (1, 0)
    assert any(e.kind == "point_recomputed" for e in events)
    assert old.is_dir()
    assert old in groups.study_run_directories(groups.get_study(study_id))


# --- Sweep points as Run objects --------------------------------------------


def _point_object_ids(study_id: str, results: Path) -> dict[str, str]:
    """Return the study's point Run objects, by run id."""
    return read_point_objects(study_id, results=results)


def _cancel_on_first_progress(cancel: threading.Event) -> Callable[[SweepEvent], None]:
    """Return an event handler that cancels at a point's first safe point."""

    def handle(event: SweepEvent) -> None:
        if event.kind == "point_progress":
            cancel.set()

    return handle


def test_each_dispatched_point_becomes_a_linked_completed_run_object(
    results: Path,
) -> None:
    study_id = _study(results, 2, 3)

    _run(study_id, points_at_once=1)

    linked = _point_object_ids(study_id, results)
    statuses = sweep_point_statuses(groups.get_study(study_id))
    assert sorted(linked) == sorted(s.run_id for s in statuses)
    assert {s.object_id for s in statuses} == set(linked.values())
    for run_id, object_id in linked.items():
        record = read_run_object(object_id, results=results).record
        assert record.state is RunState.COMPLETED
        assert record.scientific_run_id == run_id
    payload = status_payload(groups.get_study(study_id), results=results)
    assert {p["runId"]: p["objectId"] for p in payload["points"]} == linked


def test_a_cancelled_sweep_pauses_its_point_and_the_next_run_resumes_it(
    results: Path,
) -> None:
    study_id = _study(results, 2, max_generations=400)
    cancel = threading.Event()

    first = run_sweep(
        study_id,
        LocalPointRunner(),
        _cancel_on_first_progress(cancel),
        cancel,
        points_at_once=1,
    )

    assert (first.done, first.cancelled) == (0, True)
    (object_id,) = _point_object_ids(study_id, results).values()
    assert read_run_object(object_id, results=results).record.state is RunState.PAUSED
    (status,) = sweep_point_statuses(groups.get_study(study_id))
    assert (status.state, status.object_id) == ("paused", object_id)

    second, _ = _run(study_id)

    assert second.done == 1
    assert _point_object_ids(study_id, results) == {status.run_id: object_id}
    final = read_run_object(object_id, results=results).record
    assert final.state is RunState.COMPLETED
    (done,) = sweep_point_statuses(groups.get_study(study_id))
    assert done.state == "done"


def test_an_active_point_object_without_an_owner_reads_interrupted(
    results: Path,
) -> None:
    study_id = _study(results, 2, max_generations=400)
    cancel = threading.Event()
    run_sweep(
        study_id,
        LocalPointRunner(),
        _cancel_on_first_progress(cancel),
        cancel,
        points_at_once=1,
    )
    (object_id,) = _point_object_ids(study_id, results).values()
    paused = read_run_object(object_id, results=results)
    # Rewriting the record as `running` with no attempt lock held is what
    # a killed sweep process leaves behind.
    write_run_record(
        paused.directory,
        replace(
            paused.record,
            state=RunState.RUNNING,
            active_attempt_id=f"att-{'0' * 32}",
        ),
    )

    (status,) = sweep_point_statuses(groups.get_study(study_id))

    assert status.state == "interrupted"


def test_a_failed_point_object_is_skipped_unless_retried(
    results: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fim.persistence import artifacts  # noqa: PLC0415

    study_id = _study(results, 2)
    real_writer = artifacts.write_run_artifacts

    def broken_writer(*args: Any, **kwargs: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(artifacts, "write_run_artifacts", broken_writer)
    first, _ = _run(study_id)

    assert first.failed == 1
    (object_id,) = _point_object_ids(study_id, results).values()
    assert read_run_object(object_id, results=results).record.state is RunState.FAILED
    (status,) = sweep_point_statuses(groups.get_study(study_id))
    assert status.state == "failed"
    assert "disk full" in (status.reason or "")
    # The object records its own failure; the sweep keeps none of its own.
    assert read_failures(study_id, results=results) == {}

    monkeypatch.setattr(artifacts, "write_run_artifacts", real_writer)
    skipped, _ = _run(study_id)
    assert (skipped.done, skipped.skipped_failed) == (0, 1)

    retried, _ = _run(study_id, retry_failed=True)

    assert retried.done == 1
    assert _point_object_ids(study_id, results) == {status.run_id: object_id}
    assert (
        read_run_object(object_id, results=results).record.state is RunState.COMPLETED
    )


def test_a_completed_point_object_is_reused_by_another_sweep(results: Path) -> None:
    first = _study(results, 2)
    _run(first)
    second = _study(results, 2)

    outcome, _ = _run(second)

    assert (outcome.done, outcome.reused) == (0, 1)
    assert len(list_run_objects(results).objects) == 1
    assert (
        groups.get_study(second).run_directories
        == groups.get_study(first).run_directories
    )


def test_a_paused_point_object_is_never_counted_as_done(results: Path) -> None:
    first = _study(results, 2, max_generations=400)
    cancel = threading.Event()
    run_sweep(
        first,
        LocalPointRunner(),
        _cancel_on_first_progress(cancel),
        cancel,
        points_at_once=1,
    )
    second = _study(results, 2, max_generations=400)

    assert [s.state for s in sweep_point_statuses(groups.get_study(second))] == [
        "waiting"
    ]
    assert groups.get_study(first).run_directories == ()


def test_the_status_file_keeps_failures_and_objects_apart(results: Path) -> None:
    study_id = _study(results, 2, 3)
    cancel = threading.Event()

    def stop_after_first(event: SweepEvent) -> None:
        if event.kind == "point_done":
            cancel.set()

    # The first point completes as an object; the second then fails in a
    # runner that has no objects, so the sweep records that failure.
    run_sweep(study_id, LocalPointRunner(), stop_after_first, cancel, points_at_once=1)
    _run(study_id, _Failing(lambda _params: True))

    status = json.loads(failures_path(study_id, results=results).read_text())

    assert set(status) == {"failures", "objects"}
    assert len(status["failures"]) == 1
    assert len(status["objects"]) == 1
    assert set(status["failures"]) != set(status["objects"])


def test_object_points_run_batches_in_one_process() -> None:
    params = SimulationParams.from_mapping({**_BASE, "n_replicates": 4})

    params = replace(params, engine_backend="lineal")

    lineal = resolve_concurrency(points=8, params=params, cores=8)
    in_process = resolve_concurrency(
        points=8, params=params, cores=8, in_process_batches=True
    )

    assert (lineal.points_at_once, lineal.workers_per_point) == (2, 4)
    assert (in_process.points_at_once, in_process.workers_per_point) == (8, None)


def test_a_worker_reports_a_cancelled_point_object_as_paused(results: Path) -> None:
    from fim.sweep_run import _run_object_in_process  # noqa: PLC0415

    created = create_run_object(
        yaml.safe_dump({**_BASE, "max_generations": 400}), results=results
    )
    cancel = threading.Event()
    cancel.set()

    outcome = _run_object_in_process(created.object_id, str(results), 0, cancel)

    assert outcome == ("paused", "paused")
    assert (
        read_run_object(created.object_id, results=results).record.state
        is RunState.PAUSED
    )


class _FakePool(Executor):
    """A pool that answers each submit from a script, with no processes.

    Args:
        refuse: Raise `BrokenProcessPool` from `submit`, as a pool does once
            a worker has died.
        outcome: What the returned future holds: a `(kind, value)` result, or
            an exception to raise from `result()`.
    """

    def __init__(
        self,
        *,
        refuse: bool = False,
        outcome: tuple[str, str] | BaseException = ("ok", "/point"),
    ) -> None:
        self.refuse = refuse
        self.outcome = outcome
        self.submitted = 0
        self.shut_down = False

    def submit(self, fn: Any, /, *args: Any, **kwargs: Any) -> Future[Any]:
        del fn, args, kwargs  # the fake answers from its script
        self.submitted += 1
        if self.refuse:
            raise BrokenProcessPool("a worker died")
        future: Future[Any] = Future()
        if isinstance(self.outcome, BaseException):
            future.set_exception(self.outcome)
        else:
            future.set_result(self.outcome)
        return future

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        del wait, cancel_futures
        self.shut_down = True


def _scripted_runner(*pools: _FakePool) -> ProcessPointRunner:
    """A `ProcessPointRunner` that takes its pools, in order, from `pools`."""
    supply = iter(pools)
    return ProcessPointRunner(2, executor_factory=lambda _count: next(supply))


def _run_fake_point(runner: ProcessPointRunner) -> Any:
    """Run one point through `runner`; the fake pool never reads `params`."""
    return runner.run_point(_params(), threading.Event())


def test_a_pool_that_refuses_a_submit_is_replaced_and_the_point_still_runs() -> None:
    dead, fresh = _FakePool(refuse=True), _FakePool()
    runner = _scripted_runner(dead, fresh)
    try:
        outcome = _run_fake_point(runner)
    finally:
        runner.close()

    assert outcome == Path("/point")
    assert dead.shut_down
    assert fresh.submitted == 1


def test_a_pool_that_stays_broken_fails_the_point_instead_of_raising() -> None:
    runner = _scripted_runner(_FakePool(refuse=True), _FakePool(refuse=True))
    try:
        outcome = _run_fake_point(runner)
    finally:
        runner.close()

    assert isinstance(outcome, PointFailure)
    assert "could not start the point" in outcome.reason


def test_a_worker_that_dies_mid_point_fails_it_and_later_points_use_a_new_pool() -> (
    None
):
    dying = _FakePool(outcome=BrokenProcessPool("terminated abruptly"))
    fresh = _FakePool()
    runner = _scripted_runner(dying, fresh)
    try:
        first = _run_fake_point(runner)
        second = _run_fake_point(runner)
    finally:
        runner.close()

    assert first == PointFailure("a worker process ended unexpectedly", "transient")
    assert second == Path("/point")
    assert dying.shut_down
    assert (dying.submitted, fresh.submitted) == (1, 1)


def test_an_exception_raised_by_a_worker_fails_that_point_only() -> None:
    raising = _FakePool(outcome=TypeError("cannot pickle a lock"))
    runner = _scripted_runner(raising)
    try:
        first = _run_fake_point(runner)
        second = _run_fake_point(runner)
    finally:
        runner.close()

    assert first == PointFailure("cannot pickle a lock")
    assert isinstance(second, PointFailure)
    assert raising.submitted == 2


def test_several_threads_seeing_one_dead_pool_replace_it_once() -> None:
    dead = _FakePool(outcome=BrokenProcessPool("terminated abruptly"))
    created: list[_FakePool] = []

    def factory(_count: int) -> _FakePool:
        pool = dead if not created else _FakePool()
        created.append(pool)
        return pool

    runner = ProcessPointRunner(4, executor_factory=factory)
    outcomes: list[Any] = []
    barrier = threading.Barrier(3)

    def point() -> None:
        barrier.wait()
        outcomes.append(_run_fake_point(runner))

    threads = [threading.Thread(target=point) for _ in range(3)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            join_or_fail(thread, "a point thread")
    finally:
        runner.close()

    assert len(outcomes) == 3
    # every submit reached the dead pool or its single replacement: at most
    # one replacement was built, never one per thread
    assert len(created) == 2


def test_pool_priority_initializer_is_gui_only_and_survives_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only GUI sweep pools lower priority, including replacement pools."""
    created: list[dict[str, Any]] = []

    class FakeManager:
        def shutdown(self) -> None:
            return None

    class FakeExecutor(Executor):
        def __init__(self) -> None:
            self.shut_down = False

        def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
            del wait, cancel_futures
            self.shut_down = True

    context = SimpleNamespace(Manager=FakeManager)
    instances: list[FakeExecutor] = []

    def create_executor(*args: Any, **kwargs: Any) -> FakeExecutor:
        created.append({"args": args, **kwargs})
        executor = FakeExecutor()
        instances.append(executor)
        return executor

    monkeypatch.setattr(multiprocessing, "get_context", lambda _method: context)
    monkeypatch.setattr(sweep_run_module, "ProcessPoolExecutor", create_executor)
    results = Path("/tmp/test-sweep-results")
    priorities: tuple[tuple[Priority, bool], ...] = (
        ("normal", False),
        ("lower", True),
        ("lowest", False),
    )
    for priority, lower_priority in priorities:
        start = len(created)
        runner = ProcessPointRunner(
            2,
            lower_priority=lower_priority,
            priority=priority,
            results=results,
        )
        assert runner._results_directory == results
        first_pool = instances[-1]
        runner._replace(first_pool)
        replacement_pool = instances[-1]
        runner.close()
        pools = created[start:]
        assert len(pools) == 2
        assert all(pool["initializer"] is initialize_pool_worker for pool in pools)
        assert all(pool["initargs"] == (priority,) for pool in pools)
        assert all(pool["mp_context"] is context for pool in pools)
        assert first_pool.shut_down
        assert replacement_pool.shut_down


class _Flaky:
    """A runner that fails its first `failures` calls, then runs for real."""

    def __init__(
        self,
        failures: int,
        failure_class: FailureClass,
        cancel: threading.Event | None = None,
    ) -> None:
        self.failures = failures
        self.failure_class: FailureClass = failure_class
        self.cancel = cancel
        self.local = LocalPointRunner()
        self.calls = 0

    def run_point(
        self,
        params: SimulationParams,
        cancel_event: threading.Event,
        on_message: Callable[[object], None] | None = None,
        max_workers: int | None = None,
    ) -> Path | PointFailure:
        self.calls += 1
        if self.calls <= self.failures:
            if self.cancel is not None:
                self.cancel.set()
            return PointFailure("scripted failure", self.failure_class)
        return self.local.run_point(params, cancel_event, on_message, max_workers)


def test_a_transient_failure_is_tried_again_and_the_point_succeeds(
    results: Path,
) -> None:
    study_id = _study(results, 2)
    runner = _Flaky(1, "transient")

    outcome, _ = _run(study_id, runner, max_attempts=2, points_at_once=1)

    assert (outcome.done, outcome.failed) == (1, 0)
    assert runner.calls == 2
    assert read_failures(study_id) == {}


def test_a_deterministic_failure_is_never_tried_again(results: Path) -> None:
    study_id = _study(results, 2)
    runner = _Flaky(5, "deterministic")

    outcome, _ = _run(study_id, runner, max_attempts=3, points_at_once=1)

    assert (outcome.done, outcome.failed) == (0, 1)
    assert runner.calls == 1
    record = next(iter(read_failures(study_id).values()))
    assert (record["failure_class"], record["attempts"]) == ("deterministic", 1)


def test_attempts_stop_at_the_limit_and_the_record_keeps_the_count(
    results: Path,
) -> None:
    study_id = _study(results, 2)
    runner = _Flaky(5, "transient")

    outcome, _ = _run(study_id, runner, max_attempts=3, points_at_once=1)

    assert (outcome.done, outcome.failed) == (0, 1)
    assert runner.calls == 3
    record = next(iter(read_failures(study_id).values()))
    assert (record["failure_class"], record["attempts"]) == ("transient", 3)


def test_the_default_is_one_attempt(results: Path) -> None:
    study_id = _study(results, 2)
    runner = _Flaky(1, "transient")

    outcome, _ = _run(study_id, runner, points_at_once=1)

    assert (outcome.done, outcome.failed) == (0, 1)
    assert runner.calls == 1


def test_attempts_add_up_across_resumes(results: Path) -> None:
    study_id = _study(results, 2)
    _run(study_id, _Flaky(9, "transient"), max_attempts=2, points_at_once=1)

    _run(
        study_id,
        _Flaky(9, "transient"),
        max_attempts=2,
        retry_failed=True,
        points_at_once=1,
    )

    record = next(iter(read_failures(study_id).values()))
    assert record["attempts"] == 4


def test_a_cancel_stops_the_retries(results: Path) -> None:
    study_id = _study(results, 2)
    cancel = threading.Event()
    runner = _Flaky(5, "transient", cancel)

    outcome, _ = _run(study_id, runner, cancel, max_attempts=4, points_at_once=1)

    assert runner.calls == 1
    assert outcome.cancelled


def test_fewer_than_one_attempt_is_refused(results: Path) -> None:
    study_id = _study(results, 2)

    with pytest.raises(ValueError, match="max_attempts"):
        _run(study_id, max_attempts=0)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (BrokenProcessPool("died"), "transient"),
        (FileNotFoundError("gone"), "transient"),
        (ValueError("bad configuration"), "deterministic"),
        (ZeroDivisionError("division by zero"), "deterministic"),
    ],
)
def test_failures_are_classified_by_exception_type(
    error: BaseException, expected: str
) -> None:
    assert classify_failure(error) == expected


def test_a_second_sweep_on_a_study_that_is_running_is_refused(results: Path) -> None:
    study_id = _study(results, 2)
    runner = _Flaky(0, "transient")

    with (
        exclusive_lock(sweep_lock_path(study_id), blocking=False),
        pytest.raises(SweepLockedError, match="another process is running"),
    ):
        _run(study_id, runner)

    assert runner.calls == 0
    assert _manifest_count(results) == 0


def test_the_lock_is_held_while_the_sweep_runs_and_free_after(results: Path) -> None:
    study_id = _study(results, 2)
    held_during: list[bool] = []
    assert not sweep_lock_held(study_id)

    run_sweep(
        study_id,
        LocalPointRunner(),
        lambda _event: held_during.append(sweep_lock_held(study_id)),
        threading.Event(),
        points_at_once=1,
    )

    assert held_during
    assert all(held_during)
    assert not sweep_lock_held(study_id)


class _Exploding:
    """A runner that raises an exception no handler expects."""

    def run_point(
        self,
        params: SimulationParams,
        cancel_event: threading.Event,
        on_message: Callable[[object], None] | None = None,
        max_workers: int | None = None,
    ) -> Path | PointFailure:
        del params, cancel_event, on_message, max_workers
        raise KeyError("not a failure the sweep knows")


def test_the_lock_is_released_after_an_exception_and_after_a_cancel(
    results: Path,
) -> None:
    study_id = _study(results, 2, 3)
    with pytest.raises(KeyError):
        _run(study_id, _Exploding(), points_at_once=1)
    assert not sweep_lock_held(study_id)

    cancel = threading.Event()
    cancel.set()
    outcome, _ = _run(study_id, cancel=cancel)
    assert outcome.cancelled
    assert not sweep_lock_held(study_id)

    outcome, _ = _run(study_id, points_at_once=1)
    assert outcome.done == 2


def test_another_process_holding_the_lock_blocks_the_sweep_until_it_dies(
    results: Path,
) -> None:
    study_id = _study(results, 2, 3)
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, pathlib, time\n"
            "from fim.persistence.object_lock import exclusive_lock\n"
            "with exclusive_lock(pathlib.Path(sys.argv[1]), blocking=False):\n"
            "    print('held', flush=True)\n"
            "    time.sleep(3600)\n",
            str(sweep_lock_path(study_id)),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        assert sweep_lock_held(study_id)
        with pytest.raises(SweepLockedError):
            _run(study_id)
    finally:
        # SIGKILL: the operating system must free the lock with the process.
        holder.kill()
        holder.wait()

    assert not sweep_lock_held(study_id)
    outcome, _ = _run(study_id, points_at_once=1)
    assert outcome.done == 2


def _identity_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `enumerate_points` hash configurations differently, as a release
    that changes what the run id covers would."""
    from fim.engine import deterministic_run_id  # noqa: PLC0415

    monkeypatch.setattr(
        "fim.sweep.deterministic_run_id", lambda p: "v2-" + deterministic_run_id(p)
    )


def _stored_ids(study_id: str) -> list[str]:
    """Return the run ids in a Study's stored plan, in grid order."""
    return [str(e["run_id"]) for e in stored_points(groups.get_study(study_id))]


def test_a_stale_plan_names_the_command_that_repairs_it(
    results: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    study_id = _study(results, 2, 3)
    _identity_changes(monkeypatch)

    with pytest.raises(ValueError, match=f"fim sweep replan {study_id}"):
        _run(study_id)


def test_replan_matches_points_by_coordinates_and_rewrites_the_plan(
    results: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    study_id = _study(results, 2, 3)
    old_ids = _stored_ids(study_id)
    _identity_changes(monkeypatch)

    outcome = replan_sweep(study_id)

    assert outcome.written
    assert outcome.renamed == tuple((old, "v2-" + old) for old in old_ids)
    assert (outcome.unchanged, outcome.added, outcome.removed) == (0, (), ())
    assert _stored_ids(study_id) == ["v2-" + old for old in old_ids]
    again = replan_sweep(study_id)
    assert (again.renamed, again.unchanged) == ((), 2)


def test_runs_whose_ids_no_longer_match_stay_attached_and_are_listed(
    results: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    study_id = _study(results, 2, 3)
    _run(study_id, points_at_once=1)
    members = sorted(groups.study_run_directories(groups.get_study(study_id)))
    _identity_changes(monkeypatch)

    outcome = replan_sweep(study_id)

    assert sorted(outcome.orphaned_runs) == members
    assert sorted(groups.study_run_directories(groups.get_study(study_id))) == members
    assert [s.state for s in sweep_point_statuses(groups.get_study(study_id))] == [
        "waiting",
        "waiting",
    ]


def test_a_dry_run_reports_the_change_and_writes_nothing(
    results: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    study_id = _study(results, 2, 3)
    before = _stored_ids(study_id)
    _identity_changes(monkeypatch)

    outcome = replan_sweep(study_id, dry_run=True)

    assert not outcome.written
    assert len(outcome.renamed) == 2
    assert _stored_ids(study_id) == before
    assert not sweep_lock_path(study_id).exists()


def test_a_recorded_failure_follows_its_point_to_the_new_id(
    results: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    study_id = _study(results, 2, 3)
    _run(study_id, _Failing(lambda params: params.d == 2), points_at_once=1)
    (failed_id,) = read_failures(study_id)
    _identity_changes(monkeypatch)

    outcome = replan_sweep(study_id)

    assert outcome.failures_moved == 1
    assert list(read_failures(study_id)) == ["v2-" + failed_id]


def test_a_sweep_stored_under_old_ids_is_repaired_and_then_runs(
    results: Path,
) -> None:
    """The fixture is a Study whose stored plan and failure file use ids from
    before the identity change (2026-10-15), written directly."""
    study_id = _study(results, 2, 3)
    real = _stored_ids(study_id)
    study = groups.get_study(study_id)
    assert study.sweep_spec is not None
    old = ["a0ld0000000" + str(i) for i in range(len(real))]
    points = [
        {**e, "run_id": o} for e, o in zip(stored_points(study), old, strict=True)
    ]
    groups.write_study_manifest(
        groups.study_manifest_path(study_id),
        replace(study, sweep_spec={**study.sweep_spec, "points": points}),
    )
    with pytest.raises(ValueError, match="replan"):
        _run(study_id)

    outcome = replan_sweep(study_id)

    assert outcome.renamed == tuple(zip(old, real, strict=True))
    assert _stored_ids(study_id) == real
    run, _ = _run(study_id, points_at_once=1)
    assert run.done == 2


def test_replan_waits_for_no_one_and_refuses_while_a_sweep_runs(
    results: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    study_id = _study(results, 2, 3)
    _identity_changes(monkeypatch)

    with exclusive_lock(sweep_lock_path(study_id), blocking=False):
        preview = replan_sweep(study_id, dry_run=True)
        with pytest.raises(SweepLockedError):
            replan_sweep(study_id)

    assert len(preview.renamed) == 2
    assert all(not i.startswith("v2-") for i in _stored_ids(study_id))


def test_replan_refuses_a_study_that_is_not_a_sweep(results: Path) -> None:
    study = groups.create_study("Plain", None)

    with pytest.raises(ValueError, match="not a sweep"):
        replan_sweep(study.study_id)


def _burn_in_sweep(results: Path) -> str:
    """A tiny real sweep whose points differ in burn-in and so in expected cost."""
    spec = SweepSpec(
        base={
            "N": 8,
            "ploidy": "haploid",
            "d": 2,
            "m": 0.1,
            "mu": 0.2,
            "seed": 7,
            "n_replicates": 1,
            "precision": 0.1,
        },
        axes=(expand_axis("N", [8, 32]),),
    )
    return create_sweep_study(spec, enumerate_points(spec), "Order").study_id


def _started(events: list[SweepEvent]) -> list[int]:
    """The grid indices of the points, in the order they started."""
    return [e.index for e in events if e.kind == "point_started"]


def test_the_costliest_point_starts_first_and_the_stored_study_is_the_same(
    results: Path, tmp_path: Path
) -> None:
    ordered_id = _burn_in_sweep(results)
    ordered_outcome, ordered_events = _run(ordered_id, points_at_once=1)
    ordered_runs = {
        groups.run_id_of(d): d
        for d in groups.study_run_directories(groups.get_study(ordered_id))
    }
    other_root = tmp_path / "grid-order"
    other_root.mkdir()
    paths.set_results_directory_override(other_root)
    plain_id = _burn_in_sweep(other_root)

    plain_outcome, plain_events = _run(
        plain_id, points_at_once=1, costliest_first=False
    )

    assert _started(ordered_events) == [1, 0]  # the larger N has the longer burn-in
    assert _started(plain_events) == [0, 1]
    assert (ordered_outcome.done, plain_outcome.done) == (2, 2)
    plain_runs = {
        groups.run_id_of(d): d
        for d in groups.study_run_directories(groups.get_study(plain_id))
    }
    assert set(ordered_runs) == set(plain_runs) and len(plain_runs) == 2
    for run_id, directory in ordered_runs.items():
        assert compare_runs(plain_runs[run_id], directory).identical


def test_only_runs_the_chosen_points_and_leaves_the_others_waiting(
    results: Path,
) -> None:
    study_id = _burn_in_sweep(results)
    first, second = (e["run_id"] for e in stored_points(groups.get_study(study_id)))

    outcome, _ = _run(study_id, points_at_once=1, only={second})

    assert (outcome.done, outcome.skipped_failed, outcome.failed) == (1, 0, 0)
    states = [s.state for s in sweep_point_statuses(groups.get_study(study_id))]
    assert states == ["waiting", "done"]
    finish, _ = _run(study_id, points_at_once=1)
    assert finish.done == 1 and finish.already_present == 1
    assert first != second


def test_a_finished_points_event_carries_how_long_it_took(results: Path) -> None:
    study_id = _burn_in_sweep(results)
    ticks = itertools.count(0, 5)
    events: list[SweepEvent] = []

    run_sweep(
        study_id,
        LocalPointRunner(),
        events.append,
        threading.Event(),
        points_at_once=1,
        clock=lambda: float(next(ticks)),
    )

    done = [e for e in events if e.kind == "point_done"]
    assert [e.detail for e in done] == [{"seconds": 5.0}, {"seconds": 5.0}]
    again: list[SweepEvent] = []
    run_sweep(
        study_id, LocalPointRunner(), again.append, threading.Event(), points_at_once=1
    )
    assert [e.kind for e in again if e.kind.startswith("point_")] == []


def test_completed_run_ids_are_the_points_a_sweep_would_reuse(results: Path) -> None:
    study_id = _burn_in_sweep(results)
    point_ids = {str(e["run_id"]) for e in stored_points(groups.get_study(study_id))}
    assert completed_run_ids() == set()

    _run(study_id, points_at_once=1)

    assert completed_run_ids() == point_ids
    assert completed_run_ids(software_version=_OTHER_VERSION) == set()
    assert completed_run_ids(results=results / "nowhere") == set()


def test_a_run_that_did_not_complete_is_not_counted(results: Path) -> None:
    study_id = _burn_in_sweep(results)
    first = str(stored_points(groups.get_study(study_id))[0]["run_id"])

    _run(study_id, _Failing(lambda _params: True), points_at_once=1)

    assert first not in completed_run_ids()


# --- budgets -----------------------------------------------------------------


def _budget_study(count: int) -> str:
    """A tiny real sweep over `count` values of `N`; every point is cheap."""
    spec = SweepSpec(
        base={
            "N": 8,
            "ploidy": "haploid",
            "d": 2,
            "m": 0.1,
            "mu": 0.2,
            "seed": 7,
            "n_replicates": 1,
            "precision": 0.1,
        },
        axes=(expand_axis("N", [8, 16, 24, 32][:count]),),
    )
    return create_sweep_study(spec, enumerate_points(spec), "Budget").study_id


def _states(study_id: str) -> list[str]:
    """The state of every planned point."""
    return [s.state for s in sweep_point_statuses(groups.get_study(study_id))]


class _Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _Spending:
    """A runner that moves the clock as each point runs, then runs it for real."""

    def __init__(self, clock: _Clock, seconds: float, *, report: bool = False) -> None:
        self.clock, self.seconds, self.report = clock, seconds, report
        self.local = LocalPointRunner()
        self.saw_cancel: list[bool] = []

    def run_point(
        self,
        params: SimulationParams,
        cancel_event: threading.Event,
        on_message: Callable[[object], None] | None = None,
        max_workers: int | None = None,
    ) -> Path | PointFailure:
        self.clock.now += self.seconds
        if self.report and on_message is not None:
            on_message(("progress", 1))
        self.saw_cancel.append(cancel_event.is_set())
        if cancel_event.is_set():
            return PointFailure("cancelled")
        # Progress reaches the sweep (and so the budget) only if asked to.
        forward = on_message if self.report else None
        return self.local.run_point(params, cancel_event, forward, max_workers)


def test_a_new_point_limit_stops_after_that_many_and_the_rest_wait(
    results: Path,
) -> None:
    study_id = _budget_study(4)
    user_cancel = threading.Event()

    outcome, events = _run(
        study_id,
        cancel=user_cancel,
        points_at_once=1,
        budget=SweepBudget(max_new_points=2),
    )

    assert outcome.done == 2
    assert outcome.budget_reached == "the limit of 2 new point(s) was reached"
    assert outcome.cancelled is False and outcome.failed == 0
    assert user_cancel.is_set() is False  # a budget stop is not the caller's cancel
    assert [e.kind for e in events if e.kind.startswith("sweep_")] == [
        "sweep_budget_reached"
    ]
    assert events[-1].detail == outcome.budget_reached
    assert sorted(_states(study_id)) == ["done", "done", "waiting", "waiting"]
    assert read_failures(study_id) == {}


def test_the_points_a_budget_left_waiting_finish_on_the_next_call(
    results: Path,
) -> None:
    study_id = _budget_study(4)
    _run(study_id, points_at_once=1, budget=SweepBudget(max_new_points=3))

    outcome, events = _run(study_id, points_at_once=1)

    assert (outcome.done, outcome.already_present, outcome.budget_reached) == (
        1,
        3,
        None,
    )
    assert events[-1].kind == "sweep_done"
    assert _states(study_id) == ["done"] * 4


def test_a_limit_equal_to_the_work_is_not_reached(results: Path) -> None:
    study_id = _budget_study(2)

    outcome, events = _run(
        study_id, points_at_once=1, budget=SweepBudget(max_new_points=2)
    )

    assert (outcome.done, outcome.budget_reached) == (2, None)
    assert events[-1].kind == "sweep_done"


def test_reused_points_do_not_count_against_the_new_point_limit(
    results: Path,
) -> None:
    first = _budget_study(2)
    _run(first, points_at_once=1)
    second = _budget_study(4)  # the same first two points, two new ones

    outcome, _ = _run(second, points_at_once=1, budget=SweepBudget(max_new_points=2))

    assert outcome.budget_reached is None
    assert _states(second) == ["done"] * 4


def test_a_time_limit_is_measured_on_the_injected_clock(results: Path) -> None:
    study_id = _budget_study(4)
    clock = _Clock()

    outcome, _ = _run(
        study_id,
        _Spending(clock, 50.0),
        points_at_once=1,
        clock=clock,
        budget=SweepBudget(max_wall_seconds=100.0),
    )

    # Two points take 100 s; the third is not started.
    assert outcome.done == 2
    assert outcome.budget_reached == "the time limit of 2 min was reached"
    assert sorted(_states(study_id)) == ["done", "done", "waiting", "waiting"]


def test_a_time_limit_reached_inside_a_point_pauses_it_instead_of_failing_it(
    results: Path,
) -> None:
    study_id = _budget_study(3)
    clock = _Clock()
    runner = _Spending(clock, 500.0, report=True)

    outcome, _ = _run(
        study_id,
        runner,
        points_at_once=1,
        clock=clock,
        budget=SweepBudget(max_wall_seconds=100.0),
    )

    assert runner.saw_cancel == [True]  # the point was told to stop
    assert (outcome.done, outcome.failed) == (0, 0)
    assert outcome.budget_reached is not None and outcome.cancelled is False
    assert _states(study_id) == ["waiting"] * 3  # not failed: resumable
    assert read_failures(study_id) == {}


def test_a_disk_limit_stops_once_the_finished_points_take_that_much(
    results: Path,
) -> None:
    study_id = _budget_study(3)

    outcome, _ = _run(study_id, points_at_once=1, budget=SweepBudget(max_disk_bytes=1))

    assert outcome.done == 1
    assert outcome.budget_reached == "the disk limit of 1 B was reached"
    assert sorted(_states(study_id)) == ["done", "waiting", "waiting"]
    (kept,) = groups.study_run_directories(groups.get_study(study_id))
    assert (kept / "manifest.json").is_file()  # the finished point is readable


def test_a_generous_budget_changes_nothing(results: Path) -> None:
    study_id = _budget_study(2)

    outcome, events = _run(
        study_id,
        points_at_once=1,
        budget=SweepBudget(
            max_wall_seconds=1e9, max_new_points=100, max_disk_bytes=10**12
        ),
    )

    assert (outcome.done, outcome.budget_reached) == (2, None)
    assert events[-1].kind == "sweep_done"


@pytest.mark.parametrize(
    "limits",
    [
        {"max_wall_seconds": 0.0},
        {"max_new_points": 0},
        {"max_disk_bytes": -1},
    ],
)
def test_a_limit_that_is_not_positive_is_refused(limits: dict[str, float]) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        SweepBudget(**limits)  # type: ignore[arg-type]


# --- the digest stored with a plan, and verified.json -------------------------


def test_the_plan_is_stored_with_its_digest(results: Path) -> None:
    study_id = _burn_in_sweep(results)
    study = groups.get_study(study_id)

    stored = (study.sweep_spec or {})["digest"]

    assert stored == sweep_spec_of(study).digest()


def test_replan_updates_the_stored_digest(
    results: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fim.engine import deterministic_run_id  # noqa: PLC0415

    study_id = _burn_in_sweep(results)
    before = (groups.get_study(study_id).sweep_spec or {})["digest"]
    monkeypatch.setattr(
        "fim.sweep.deterministic_run_id", lambda p: "v2-" + deterministic_run_id(p)
    )

    replan_sweep(study_id)

    after_study = groups.get_study(study_id)
    assert (after_study.sweep_spec or {})["digest"] == sweep_spec_of(
        after_study
    ).digest()
    assert (after_study.sweep_spec or {})["digest"] != before


_ISO_UTC = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"


def test_a_full_rerun_that_matches_records_a_verification(results: Path) -> None:
    study_id = _burn_in_sweep(results)
    _run(study_id, points_at_once=1)
    assert read_verification(study_id) is None  # a plain run verifies nothing

    outcome, _ = _run(study_id, points_at_once=1, rerun=True)

    record = read_verification(study_id)
    assert record is not None
    assert (outcome.recomputed, outcome.differing) == (2, 0)
    assert record["result"] == "identical"
    assert (record["points_checked"], record["identical"], record["differing"]) == (
        2,
        2,
        0,
    )
    assert record["software_version"] == __version__
    assert (
        record["plan_digest"] == (groups.get_study(study_id).sweep_spec or {})["digest"]
    )
    assert re.fullmatch(_ISO_UTC, record["verified_at"])
    assert verification_path(study_id).is_file()


def test_a_rerun_that_differs_records_it_and_keeps_both_runs(results: Path) -> None:
    study_id = _burn_in_sweep(results)
    _run(study_id, points_at_once=1)

    _run(study_id, _Tampering(), points_at_once=1, rerun=True)

    record = read_verification(study_id)
    assert record is not None
    assert record["result"] == "differs"
    assert (record["points_checked"], record["identical"], record["differing"]) == (
        2,
        0,
        2,
    )


def test_a_rerun_with_nothing_finished_verifies_nothing(results: Path) -> None:
    study_id = _burn_in_sweep(results)

    _run(study_id, points_at_once=1, rerun=True)

    assert read_verification(study_id) is None  # the points were computed, not checked


def test_a_stopped_rerun_does_not_claim_to_have_verified(results: Path) -> None:
    study_id = _burn_in_sweep(results)
    _run(study_id, points_at_once=1)

    outcome, _ = _run(
        study_id, points_at_once=1, rerun=True, budget=SweepBudget(max_new_points=1)
    )

    assert outcome.budget_reached is not None
    assert read_verification(study_id) is None


@pytest.mark.parametrize("content", [None, "not json", "[1]", ""])
def test_a_missing_or_damaged_verification_reads_as_none(
    results: Path, content: str | None
) -> None:
    study_id = _burn_in_sweep(results)
    path = verification_path(study_id)
    if content is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    assert read_verification(study_id) is None


def test_a_large_plan_stores_its_digest_and_count_not_its_points(
    results: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("fim.sweep_run.STORED_PLAN_LIMIT", 1)

    study_id = _study(results, 2, 3, 4)

    stored = groups.get_study(study_id).sweep_spec or {}
    assert "points" not in stored
    assert stored["point_count"] == 3
    assert stored["digest"] == sweep_spec_of(groups.get_study(study_id)).digest()


def test_a_digest_only_plan_still_lists_runs_and_resumes(
    results: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("fim.sweep_run.STORED_PLAN_LIMIT", 1)
    study_id = _study(results, 2, 3)
    expected = [
        p.run_id
        for p in enumerate_points(sweep_spec_of(groups.get_study(study_id))).points
    ]

    assert _stored_ids(study_id) == expected
    outcome, _ = _run(study_id, points_at_once=1)
    assert outcome.done == 2
    again, _ = _run(study_id, points_at_once=1)
    assert again.done == 0
    assert [s.state for s in sweep_point_statuses(groups.get_study(study_id))] == [
        "done",
        "done",
    ]


def test_a_digest_only_plan_notices_changed_run_ids_and_replan_repairs_it(
    results: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("fim.sweep_run.STORED_PLAN_LIMIT", 1)
    study_id = _study(results, 2, 3)
    before = (groups.get_study(study_id).sweep_spec or {})["digest"]
    _identity_changes(monkeypatch)

    with pytest.raises(ValueError, match=f"fim sweep replan {study_id}"):
        _run(study_id)
    outcome = replan_sweep(study_id)

    stored = groups.get_study(study_id).sweep_spec or {}
    assert outcome.written
    assert stored["digest"] != before
    assert stored["point_count"] == 2 and "points" not in stored
    assert _stored_ids(study_id) == [
        p.run_id
        for p in enumerate_points(sweep_spec_of(groups.get_study(study_id))).points
    ]


def test_the_zip_and_list_strategies_run_as_sweeps(results: Path) -> None:
    spec = SweepSpec.from_rows(_BASE, [{"d": 3}, {"d": 2}])
    study_id = create_sweep_study(spec, enumerate_points(spec), "List").study_id

    outcome, _ = _run(study_id, points_at_once=1)

    assert outcome.done == 2
    rebuilt = sweep_spec_of(groups.get_study(study_id))
    assert rebuilt == spec


# --- recording levels and reuse ----------------------------------------------

_TAPERED: dict[str, Any] = {
    "trajectory_retention": "tapered",
    "trajectory_keep_last": 3,
}


def _point_directory(study_id: str) -> Path:
    """Return the run directory of the first finished point of a sweep."""
    rows = sweep_point_results(groups.get_study(study_id))
    assert rows
    return rows[0].directory


def test_a_tapered_run_is_not_reused_where_full_frames_are_asked(
    results: Path,
) -> None:
    tapered = _study(results, 2, 3, **_TAPERED)
    first, _ = _run(tapered, points_at_once=1)
    full = _study(results, 2, 3)

    second, _ = _run(full, points_at_once=1)

    assert first.done == 2
    assert (second.done, second.reused) == (2, 0)
    assert _point_directory(full) != _point_directory(tapered)


def test_a_full_run_is_reused_where_tapered_frames_are_asked(results: Path) -> None:
    full = _study(results, 2, 3)
    _run(full, points_at_once=1)
    tapered = _study(results, 2, 3, **_TAPERED)

    outcome, _ = _run(tapered, points_at_once=1)

    assert (outcome.done, outcome.reused) == (0, 2)


def test_a_plan_marks_only_the_runs_that_hold_what_it_asks(results: Path) -> None:
    from fim.gui.sweep_bridge import plan_payload  # noqa: PLC0415

    _run(_study(results, 2, 3, **_TAPERED), points_at_once=1)
    for changes, reused in (({}, 0), (_TAPERED, 2)):
        spec = SweepSpec(base={**_BASE, **changes}, axes=(expand_axis("d", [2, 3]),))

        plan = plan_payload(spec, enumerate_points(spec))

        assert plan["reusedCount"] == reused


@pytest.mark.parametrize(
    ("kept", "compared", "not_compared"),
    [
        (_TAPERED, {"report", "pairwise"}, {"trajectory", "convergence"}),
        (
            {"trajectory_retention": "statistics"},
            {"report", "pairwise"},
            {"trajectory", "convergence", "history"},
        ),
        (
            {"history_format": "compact"},
            {"report", "pairwise", "trajectory"},
            {"convergence"},
        ),
    ],
)
def test_runs_that_kept_different_things_agree_on_what_both_kept(
    results: Path,
    kept: dict[str, Any],
    compared: set[str],
    not_compared: set[str],
) -> None:
    from fim.reproducibility import compare_runs  # noqa: PLC0415

    # The run that kept less comes first; a request for everything cannot
    # reuse it, so the second sweep computes the point again.
    short = _study(results, 2, **kept)
    _run(short)
    # (A sweep writes a compact history by default, so "everything" says exact.)
    full = _study(results, 2, history_format="exact")
    outcome, _ = _run(full)
    assert outcome.done == 1

    comparison = compare_runs(_point_directory(short), _point_directory(full))

    assert comparison.identical
    assert compared <= set(comparison.compared_on)
    assert not_compared == set(comparison.not_compared)


def test_a_difference_in_what_both_kept_is_still_found(results: Path) -> None:
    from fim.reproducibility import compare_runs  # noqa: PLC0415

    statistics = _study(results, 2, trajectory_retention="statistics")
    _run(statistics)
    full = _study(results, 2)
    _run(full)
    left, right = _point_directory(statistics), _point_directory(full)
    report = json.loads((right / "report.json").read_text())
    report["D"] = report["D"] + 0.5
    (right / "report.json").write_text(json.dumps(report))
    manifest = json.loads((right / "manifest.json").read_text())
    from fim.persistence.manifest import hash_file  # noqa: PLC0415

    manifest["artifacts"]["report"] = hash_file(right / "report.json")
    (right / "manifest.json").write_text(json.dumps(manifest))

    comparison = compare_runs(left, right)

    assert not comparison.identical
    assert next(item.label for item in comparison.differences) == "report"
    assert "trajectory" not in comparison.compared_on
