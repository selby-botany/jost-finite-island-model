"""Tests for `fim.sweep_status`: a sweep's snapshot, read while it runs."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from fim import paths
from fim.persistence import groups
from fim.persistence.object_lock import exclusive_lock
from fim.sweep import SweepSpec, enumerate_points, expand_axis
from fim.sweep_run import (
    LocalPointRunner,
    PointFailure,
    create_sweep_study,
    run_sweep,
    stored_points,
    sweep_lock_path,
)
from fim.sweep_status import read_sweep_status

_BASE: dict[str, Any] = {
    "N": 8,
    "ploidy": "haploid",
    "d": 2,
    "m": 0.1,
    "mu": 0.2,
    "seed": 7,
    "n_replicates": 1,
    "precision": 0.1,
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


def _study(*values: int) -> str:
    """Create a sweep Study over `N` and return its id."""
    spec = SweepSpec(base=_BASE, axes=(expand_axis("N", list(values)),))
    return create_sweep_study(spec, enumerate_points(spec), "Status").study_id


def _run(study_id: str, runner: Any = None, **options: Any) -> None:
    run_sweep(
        study_id,
        runner or LocalPointRunner(),
        lambda _event: None,
        threading.Event(),
        points_at_once=1,
        **options,
    )


def test_an_unstarted_sweep_has_every_point_waiting_and_no_estimate_yet(
    results: Path,
) -> None:
    study_id = _study(8, 16, 32)

    status = read_sweep_status(study_id)

    assert (status.total, dict(status.counts)) == (3, {"waiting": 3})
    assert status.locked is False and status.finished is False
    assert status.last_finished is None
    assert status.remaining is not None
    assert (status.remaining.points, status.remaining.finished_points) == (3, 0)
    assert status.remaining.calibrated_seconds is None


def test_a_partly_run_sweep_calibrates_the_time_left_from_what_finished(
    results: Path,
) -> None:
    study_id = _study(8, 16, 32)
    first = stored_points(groups.get_study(study_id))[2]["run_id"]

    _run(study_id, only={str(first)})
    status = read_sweep_status(study_id)

    assert dict(status.counts) == {"done": 1, "waiting": 2}
    assert status.last_finished is not None
    assert status.remaining is not None
    assert (status.remaining.points, status.remaining.finished_points) == (2, 1)
    assert status.remaining.calibrated_seconds is not None
    assert status.remaining.calibrated_seconds >= 0.0


def test_a_finished_sweep_with_no_runner_is_finished(results: Path) -> None:
    study_id = _study(8, 16)
    _run(study_id)

    status = read_sweep_status(study_id)

    assert dict(status.counts) == {"done": 2}
    assert status.finished is True
    assert status.remaining is not None and status.remaining.points == 0


def test_the_lock_says_a_process_is_running_the_sweep(results: Path) -> None:
    study_id = _study(8, 16)

    with exclusive_lock(sweep_lock_path(study_id), blocking=False):
        locked = read_sweep_status(study_id)
    unlocked = read_sweep_status(study_id)

    assert (locked.locked, unlocked.locked) == (True, False)


class _Failing:
    """A runner whose points all fail, for a recorded failure to read."""

    def run_point(self, *args: Any, **kwargs: Any) -> PointFailure:
        del args, kwargs
        return PointFailure("the engine exploded")


def test_failed_points_are_listed_with_their_reason_and_attempts(
    results: Path,
) -> None:
    study_id = _study(8, 16)
    _run(study_id, _Failing())

    status = read_sweep_status(study_id)

    assert dict(status.counts) == {"failed": 2}
    assert [(f.index, f.reason, f.attempts) for f in status.failures] == [
        (0, "the engine exploded", 1),
        (1, "the engine exploded", 1),
    ]
    assert status.finished is False


def test_a_stale_plan_has_no_estimate_instead_of_a_wrong_one(
    results: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fim.engine import deterministic_run_id  # noqa: PLC0415

    study_id = _study(8, 16)
    monkeypatch.setattr(
        "fim.sweep.deterministic_run_id", lambda p: "v2-" + deterministic_run_id(p)
    )

    status = read_sweep_status(study_id)

    assert status.remaining is None
    assert status.total == 2


def test_a_study_that_is_not_a_sweep_is_refused(results: Path) -> None:
    study = groups.create_study("Plain")

    with pytest.raises(ValueError, match="not a sweep"):
        read_sweep_status(study.study_id)
