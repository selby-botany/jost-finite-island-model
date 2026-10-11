"""Tests for `fim object` (`fim.cli_objects`).

Each test drives the real `cli.main` against its own results directory.
Pause is requested from the progress observer at a fixed generation, so no
test depends on timing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from fim import cli, run_service
from fim.examples import classes
from fim.execution import ScalarSession
from fim.persistence.run_objects import read_run_object
from fim.persistence.run_ownership import request_pause

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


@pytest.fixture(autouse=True)
def _fixture_class_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check every `class` against the test fixture, never the shipped file."""
    monkeypatch.setattr(classes, "default_classes_path", lambda: _CLASSES_FIXTURE)


def _config(tmp_path: Path, name: str = "config.yaml", **changes: Any) -> Path:
    """Write `_CONFIG` with `changes` applied and return its path."""
    path = tmp_path / name
    path.write_text(yaml.safe_dump({**_CONFIG, **changes}, sort_keys=False))
    return path


def _create(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], config: Path
) -> tuple[str, str]:
    """Create an object from `config`; return its ID and the command output."""
    assert _main(tmp_path, "object", "create", str(config)) == 0
    output = capsys.readouterr().out
    match = re.search(r"Created (obj-[0-9a-f]+)", output)
    assert match is not None, output
    return match.group(1), output


def _main(tmp_path: Path, *arguments: str) -> int:
    """Run `fim` with a temporary results directory."""
    return cli.main(["--results-directory", str(_results(tmp_path)), *arguments])


def _results(tmp_path: Path) -> Path:
    """Return the test's results directory."""
    return tmp_path / "results"


def test_create_saves_a_valid_configuration_as_planned(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    object_id, output = _create(tmp_path, capsys, _config(tmp_path))

    assert f"Created {object_id} (planned)" in output
    assert "cannot start" not in output


def test_create_saves_an_invalid_configuration_as_a_draft_with_its_issues(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    object_id, output = _create(tmp_path, capsys, _config(tmp_path, N=-1))

    assert f"Created {object_id} (draft)" in output
    assert "It cannot start until these are fixed:" in output
    assert "  - " in output


def test_starting_a_draft_fails_and_lists_its_issues(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    object_id, _ = _create(tmp_path, capsys, _config(tmp_path, N=-1))

    status = _main(tmp_path, "object", "start", object_id)

    error = capsys.readouterr().err
    assert status == 2
    assert f"fim: error: {object_id} is a draft and cannot start:" in error
    assert "  - " in error


def test_editing_a_draft_into_a_valid_configuration_makes_it_planned(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    object_id, _ = _create(tmp_path, capsys, _config(tmp_path, N=-1))

    status = _main(tmp_path, "object", "edit", object_id, str(_config(tmp_path)))

    assert status == 0
    assert f"Saved {object_id} (planned)" in capsys.readouterr().out


def test_start_runs_a_planned_object_to_completion_with_its_artifacts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    object_id, _ = _create(tmp_path, capsys, _config(tmp_path))

    status = _main(tmp_path, "object", "start", object_id)

    output = capsys.readouterr().out
    finished = read_run_object(object_id, results=_results(tmp_path))
    assert status == 0
    assert f"{object_id} is completed at generation 30" in output
    assert finished.record.state.value == "completed"
    assert (finished.directory / "trajectory.tlog").is_file()
    assert (finished.directory / "manifest.json").is_file()


def test_status_and_list_show_the_lifecycle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    object_id, _ = _create(tmp_path, capsys, _config(tmp_path))

    assert _main(tmp_path, "object", "status", object_id) == 0
    status_output = capsys.readouterr().out
    assert _main(tmp_path, "object", "status", object_id, "--json") == 0
    record = json.loads(capsys.readouterr().out)
    assert _main(tmp_path, "object", "list") == 0
    list_output = capsys.readouterr().out

    assert re.search(r"State:\s+planned", status_output)
    assert record["state"] == "planned"
    assert record["object_id"] == object_id
    assert object_id in list_output
    assert "planned" in list_output


def test_list_with_no_objects_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _main(tmp_path, "object", "list") == 0
    assert "No run objects." in capsys.readouterr().out


def test_pausing_a_planned_object_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    object_id, _ = _create(tmp_path, capsys, _config(tmp_path))

    status = _main(tmp_path, "object", "pause", object_id)

    assert status == 2
    assert "fim: error:" in capsys.readouterr().err


def test_resume_refuses_a_planned_object_and_start_refuses_a_paused_one(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    object_id, _ = _create(tmp_path, capsys, _config(tmp_path))
    assert _main(tmp_path, "object", "resume", object_id) == 2
    assert "only a paused, interrupted, or failed" in capsys.readouterr().err

    # Pause the run from the observer at generation 15, as another
    # terminal's `fim object pause` would.
    original = run_service.execute_run_object

    def pause_at_15(object_id: str, **kwargs: Any) -> Any:
        def observer(session: ScalarSession) -> None:
            if session.generation == 15:
                current = read_run_object(object_id, results=_results(tmp_path))
                request_pause(
                    object_id,
                    expected_revision=current.record.revision,
                    results=_results(tmp_path),
                )

        return original(
            object_id,
            cadence=run_service.CheckpointCadence(10, 1e9, 5),
            observer=observer,
            **kwargs,
        )

    monkeypatch.setattr("fim.cli_objects.execute_run_object", pause_at_15)
    assert _main(tmp_path, "object", "start", object_id) == 0
    assert f"{object_id} is paused at generation 15" in capsys.readouterr().out

    assert _main(tmp_path, "object", "start", object_id) == 2
    assert f"use `fim object resume {object_id}`" in capsys.readouterr().err

    monkeypatch.setattr("fim.cli_objects.execute_run_object", original)
    assert _main(tmp_path, "object", "resume", object_id) == 0
    assert f"{object_id} is completed at generation 30" in capsys.readouterr().out


def test_cancel_abandons_a_planned_object_and_delete_removes_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    object_id, _ = _create(tmp_path, capsys, _config(tmp_path))

    assert _main(tmp_path, "object", "cancel", object_id) == 0
    assert f"Cancelled {object_id}" in capsys.readouterr().out
    assert _main(tmp_path, "object", "delete", object_id) == 0
    assert f"Deleted {object_id}" in capsys.readouterr().out
    assert _main(tmp_path, "object", "list") == 0
    assert "No run objects." in capsys.readouterr().out


def test_an_interrupted_start_points_to_resume(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    object_id, _ = _create(tmp_path, capsys, _config(tmp_path))

    def interrupted(*_: Any, **__: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr("fim.cli_objects.execute_run_object", interrupted)

    assert _main(tmp_path, "object", "start", object_id) == 130
    assert f"fim object resume {object_id}" in capsys.readouterr().err
