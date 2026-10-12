"""Tests for `fim.gui.object_runner`: a saved run on a background thread.

Pause and Cancel are requested from the test observer at a chosen
generation, so every scenario stops at the same safe point on every run.
Each test joins the worker thread; none waits on a clock.
"""

from __future__ import annotations

import queue
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from fim.engine import RunResult
from fim.examples import classes
from fim.execution import ScalarSession, UnsupportedSessionError
from fim.gui.object_runner import start_object_run
from fim.gui.runner import run_artifact_targets, write_run_artifacts
from fim.persistence.run_lifecycle import RunState
from fim.persistence.run_objects import create_run_object, read_run_object
from fim.persistence.run_ownership import request_cancel, request_pause
from fim.run_service import CheckpointCadence, execute_run_object

_CLASSES_FIXTURE = (
    Path(__file__).resolve().parents[1] / "persistence" / "fixtures" / "classes.yaml"
)

_CONFIG: dict[str, Any] = {
    "N": 20,
    "ploidy": "haploid",
    "d": 2,
    "m": 0.1,
    "mu": 0.05,
    "seed": 20261011,
    "loci": [{"locus_id": 1, "length": 200}],
    "precision": 0.0,
    "max_generations": 30,
    "n_replicates": 1,
    "stop_batch_early": False,
    "engine_backend": "lineal",
}

# Requests are checked every 5 generations; time never triggers a checkpoint.
_CADENCE = CheckpointCadence(generations=10, seconds=1e9, control_generations=5)


@pytest.fixture(autouse=True)
def _fixture_class_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check every `class` against the test fixture, never the shipped file."""
    monkeypatch.setattr(classes, "default_classes_path", lambda: _CLASSES_FIXTURE)


def _create(results: Path, **changes: Any) -> str:
    """Save a configuration as a new object and return its ID."""
    text = yaml.safe_dump({**_CONFIG, **changes}, sort_keys=False)
    return create_run_object(text, results=results).object_id


def _execute(
    object_id: str,
    results: Path,
    observer: Callable[..., None] | None = None,
) -> list[Any]:
    """Run the object to its next stop and return every message it posted."""
    messages: queue.Queue[Any] = queue.Queue()
    worker = start_object_run(
        object_id,
        messages,
        results=results,
        cadence=_CADENCE,
        observer=observer,
    )
    worker.join()
    posted: list[Any] = []
    while not messages.empty():
        posted.append(messages.get_nowait())
    return posted


def _request_at(
    generation: int, object_id: str, results: Path, request: Callable[..., Any]
) -> Callable[[ScalarSession], None]:
    """Return an observer that sends `request` once `generation` is reached."""
    sent: list[bool] = []

    def observe(session: ScalarSession) -> None:
        if session.generation >= generation and not sent:
            sent.append(True)
            current = read_run_object(object_id, results=results)
            request(
                object_id,
                expected_revision=current.record.revision,
                results=results,
            )

    return observe


def test_a_planned_run_posts_progress_then_its_result(tmp_path: Path) -> None:
    object_id = _create(tmp_path)

    posted = _execute(object_id, tmp_path)

    assert posted[0][0] == "progress"
    assert posted[-1][0] == "done"
    assert isinstance(posted[-1][1], RunResult)
    finished = read_run_object(object_id, results=tmp_path)
    assert finished.record.state is RunState.COMPLETED
    assert (finished.directory / "manifest.json").is_file()


def test_a_paused_run_resumes_to_the_uninterrupted_result(tmp_path: Path) -> None:
    straight = _execute(_create(tmp_path), tmp_path)[-1]
    object_id = _create(tmp_path)

    paused = _execute(
        object_id, tmp_path, _request_at(10, object_id, tmp_path, request_pause)
    )
    resumed = _execute(object_id, tmp_path)

    assert paused[-1] == ("paused", 10)
    assert read_run_object(object_id, results=tmp_path).record.state is (
        RunState.COMPLETED
    )
    assert resumed[-1][0] == "done"
    assert isinstance(straight[1], RunResult)
    assert isinstance(resumed[-1][1], RunResult)
    assert resumed[-1][1].report == straight[1].report


def test_a_cancelled_run_posts_the_generation_it_stopped_at(tmp_path: Path) -> None:
    object_id = _create(tmp_path)

    posted = _execute(
        object_id, tmp_path, _request_at(10, object_id, tmp_path, request_cancel)
    )

    assert posted[-1] == ("cancelled", 10)
    assert read_run_object(object_id, results=tmp_path).record.state is (
        RunState.CANCELLED
    )


def test_a_draft_posts_an_error_naming_its_issues(tmp_path: Path) -> None:
    object_id = _create(tmp_path, N="many")

    posted = _execute(object_id, tmp_path)

    assert len(posted) == 1
    assert posted[0][0] == "error"
    assert "draft" in str(posted[0][1])


def test_an_unsupported_configuration_posts_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    object_id = _create(tmp_path)

    def _fail(_p: object) -> None:
        raise UnsupportedSessionError("unsupported backend")

    monkeypatch.setattr("fim.gui.object_runner.check_session_support", _fail)

    posted = _execute(object_id, tmp_path)

    assert [message[0] for message in posted] == ["error"]
    assert "unsupported backend" in str(posted[0][1])
    assert read_run_object(object_id, results=tmp_path).record.state is (
        RunState.PLANNED
    )


def test_a_planned_batch_run_posts_started_then_done(tmp_path: Path) -> None:
    object_id = _create(tmp_path, n_replicates=2, max_generations=15)

    posted = _execute(object_id, tmp_path)

    assert posted[0][0] == "started"
    assert posted[-1][0] == "done"
    results = posted[-1][1]
    assert isinstance(results, tuple)
    assert len(results) == 2
    assert all(isinstance(r, RunResult) for r in results)
    finished = read_run_object(object_id, results=tmp_path)
    assert finished.record.state is RunState.COMPLETED
    assert (finished.directory / "manifest.json").is_file()
    assert (finished.directory / "replicate-001" / "manifest.json").is_file()
    assert (finished.directory / "replicate-002" / "manifest.json").is_file()


def test_a_run_killed_after_publishing_reports_it_already_finished(
    tmp_path: Path,
) -> None:
    """A verified receipt from a killed attempt completes without a result."""
    object_id = _create(tmp_path)

    def write_then_die(result: RunResult, directory: Path) -> None:
        write_run_artifacts(result, run_artifact_targets(directory))
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        execute_run_object(object_id, write_artifacts=write_then_die, results=tmp_path)

    posted = _execute(object_id, tmp_path)

    assert posted == [
        ("error", "this saved run had already finished; its results are saved")
    ]
    finished = read_run_object(object_id, results=tmp_path)
    assert finished.record.state is RunState.COMPLETED
