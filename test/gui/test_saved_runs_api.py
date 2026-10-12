"""Tests for the saved-run bridge calls on `fim.gui.app.Api` (no real window).

A saved run executes on a background thread. Tests wait for the drain
thread to release the run guard, which it does in its own `finally` after
its last push, with no timing budget; `conftest.poll_or_fail` bounds only
a hang.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import poll_or_fail

from fim import paths
from fim.examples import classes
from fim.gui import app as app_module
from fim.gui.app import Api
from fim.gui.config_form import DEFAULT_RUN_SETTING_FIELD_NAMES, starter_form_values
from fim.persistence.run_lifecycle import RunState
from fim.persistence.run_objects import read_run_object

_CLASSES_FIXTURE = (
    Path(__file__).resolve().parents[1] / "persistence" / "fixtures" / "classes.yaml"
)
_IDLE_POLL_SECONDS = 0.05


class _FakeWindow:
    """Records `evaluate_js` pushes."""

    def __init__(self) -> None:
        self.pushed: list[str] = []

    def evaluate_js(self, script: str) -> Any:
        self.pushed.append(script)
        return None

    def calls(self, name: str) -> list[str]:
        prefix = f"fim.{name}("
        return [script for script in self.pushed if script.startswith(prefix)]


@pytest.fixture(autouse=True)
def _fixture_class_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check every `class` against the test fixture, never the shipped file."""
    monkeypatch.setattr(classes, "default_classes_path", lambda: _CLASSES_FIXTURE)


@pytest.fixture
def results(tmp_path: Path) -> Iterator[Path]:
    """Isolate the results directory for the test."""
    root = tmp_path / "results"
    root.mkdir()
    paths.set_results_directory_override(root)
    try:
        yield root
    finally:
        paths.set_results_directory_override(None)


@pytest.fixture
def window(monkeypatch: pytest.MonkeyPatch) -> _FakeWindow:
    """Provide a fake active window."""
    fake = _FakeWindow()
    monkeypatch.setattr(app_module, "_active_window", lambda: fake)
    return fake


@pytest.fixture
def api(tmp_path: Path, results: Path) -> Api:
    """An `Api` whose defaults make every run tiny, lineal, and resumable."""
    del results
    api = Api(preferences_path=tmp_path / "preferences.json")
    defaults = {
        key: value
        for key, value in starter_form_values().items()
        if key in api.get_default_run_settings()
    }
    defaults.update(
        {"n_replicates": "1", "max_generations": "10", "engine_backend": "lineal"}
    )
    assert api.set_default_run_settings(defaults)["ok"] is True
    api.set_default_ploidy("1")
    return api


def _form(**changes: str) -> dict[str, str]:
    """A tiny valid Configure form, without its run settings."""
    values = {
        key: value
        for key, value in starter_form_values(overrides={"ploidy": "1"}).items()
        if key not in DEFAULT_RUN_SETTING_FIELD_NAMES
    }
    values.update({"N": "16", "d": "2", **changes})
    return values


def _wait_until_idle(api: Api) -> None:
    """Wait for the drain thread to release the run guard."""
    poll_or_fail(
        lambda: api._run_in_flight,
        lambda busy: not busy,
        "saved run drain thread (run guard release)",
        interval=_IDLE_POLL_SECONDS,
    )


def test_a_saved_draft_reopens_with_the_values_as_typed(api: Api) -> None:
    form = _form(N="many")

    saved = api.save_run_object(form, None, "Trial", "Not right yet")
    opened = api.open_run_object(saved["objectId"])

    assert saved["ok"] is True
    assert saved["state"] == "draft"
    assert saved["problem"]
    assert opened["ok"] is True
    assert opened["state"] == "draft"
    assert opened["editable"] is True
    assert opened["name"] == "Trial"
    assert {key: opened["values"][key] for key in form} == form


def test_a_draft_cannot_start(api: Api, window: _FakeWindow) -> None:
    saved = api.save_run_object(_form(N="many"))

    started = api.start_run_object(saved["objectId"])

    assert started["ok"] is False
    assert "cannot start" in started["message"]
    assert window.pushed == []


def test_a_planned_run_starts_completes_and_is_listed(
    api: Api, window: _FakeWindow, results: Path
) -> None:
    saved = api.save_run_object(_form(), None, "Tiny")
    assert saved["state"] == "planned"

    started = api.start_run_object(saved["objectId"])
    _wait_until_idle(api)

    assert started["ok"] is True
    assert started["isBatch"] is False
    assert started["name"] == "Tiny"
    done = window.calls("onRunDone")
    assert len(done) == 1
    finished = read_run_object(saved["objectId"], results=results)
    assert finished.record.state is RunState.COMPLETED
    payload = json.loads(done[0][len("fim.onRunDone(") : -1])
    assert str(finished.directory) in json.dumps(payload)
    listing = api.list_run_objects()
    assert listing["activeObjectId"] is None
    [row] = listing["runs"]
    assert row["state"] == "completed"
    assert row["actions"]["open"] is True


def test_cancel_and_delete_a_planned_run(api: Api) -> None:
    saved = api.save_run_object(_form())

    assert api.cancel_run_object(saved["objectId"]) == {
        "ok": True,
        "state": "cancelled",
    }
    assert api.delete_run_object(saved["objectId"]) == {"ok": True}
    assert api.list_run_objects()["runs"] == []


def test_pause_without_a_saved_run_in_flight_is_refused(api: Api) -> None:
    paused = api.pause_run()

    assert paused["ok"] is False


def test_pausing_on_close_with_nothing_running_does_nothing(api: Api) -> None:
    api.pause_active_object_run(0.0)

    assert api._active_object_id is None


def test_reopening_a_saved_run_carries_its_own_run_settings(api: Api) -> None:
    saved = api.save_run_object({**_form(), "max_generations": "25"})

    opened = api.open_run_object(saved["objectId"])

    assert opened["runSettings"]["max_generations"] == "25"
    assert {row["field"] for row in opened["runSettingDifferences"]} >= {
        "max_generations"
    }


def test_start_and_complete_a_saved_batch_run(
    api: Api, window: _FakeWindow, results: Path
) -> None:
    saved = api.save_run_object(
        {**_form(), "n_replicates": "2", "max_generations": "10"},
        None,
        "Tiny Batch",
    )
    assert saved["state"] == "planned"

    started = api.start_run_object(saved["objectId"])
    _wait_until_idle(api)

    assert started["ok"] is True
    assert started["isBatch"] is True
    assert started["name"] == "Tiny Batch"
    done = window.calls("onBatchDone")
    assert len(done) == 1
    finished = read_run_object(saved["objectId"], results=results)
    assert finished.record.state is RunState.COMPLETED
    assert (finished.directory / "manifest.json").is_file()
    assert (finished.directory / "replicate-001" / "manifest.json").is_file()
    assert (finished.directory / "replicate-002" / "manifest.json").is_file()
    listing = api.list_run_objects()
    assert listing["activeObjectId"] is None
    [row] = listing["runs"]
    assert row["state"] == "completed"
    assert row["actions"]["open"] is True


def test_clone_saved_run_via_api(api: Api) -> None:
    saved = api.save_run_object({**_form(), "name": "Source Run"})

    cloned = api.clone_run_object(saved["objectId"])

    assert cloned["ok"] is True
    assert cloned["objectId"] != saved["objectId"]
    assert cloned["state"] == "planned"

    listing = api.list_run_objects()
    ids = {row["objectId"] for row in listing["runs"]}
    assert {saved["objectId"], cloned["objectId"]} <= ids
