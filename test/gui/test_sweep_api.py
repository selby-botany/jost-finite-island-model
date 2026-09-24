"""Tests for the sweep bridge calls on `fim.gui.app.Api` (no real window)."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from fim import paths
from fim.gui import app as app_module
from fim.gui.app import Api
from fim.gui.config_form import DEFAULT_RUN_SETTING_FIELD_NAMES, starter_form_values
from fim.persistence import groups
from fim.sweep_run import LocalPointRunner

_WAIT_SECONDS = 60


class _FakeWindow:
    """Records `evaluate_js` pushes and signals when a sweep ends."""

    def __init__(self) -> None:
        self.pushed: list[str] = []
        self.finished = threading.Event()

    def evaluate_js(self, script: str) -> Any:
        self.pushed.append(script)
        if '"kind": "sweep_done"' in script or '"kind": "sweep_cancelled"' in script:
            self.finished.set()
        return None

    def events(self) -> list[dict[str, Any]]:
        prefix = "fim.onSweepEvent("
        return [
            json.loads(script[len(prefix) : -1])
            for script in self.pushed
            if script.startswith(prefix)
        ]


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
    """An `Api` whose defaults make every point a tiny single run."""
    del results
    api = Api(preferences_path=tmp_path / "preferences.json")
    defaults = {
        key: value
        for key, value in starter_form_values().items()
        if key in api.get_default_run_settings()
    }
    defaults.update(
        {"n_replicates": "1", "convergence_window": "3", "max_generations": "10"}
    )
    assert api.set_default_run_settings(defaults)["ok"] is True
    api.set_default_ploidy("1")
    return api


def _form() -> dict[str, str]:
    """A tiny valid Configure form."""
    values = {
        key: value
        for key, value in starter_form_values(overrides={"ploidy": "1"}).items()
        if key not in DEFAULT_RUN_SETTING_FIELD_NAMES
    }
    values.update({"N": "16", "d": "2"})
    return values


def _request(*axes: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"axes": list(axes), **extra}


_D_AXIS = {"key": "d", "values": [2, 3]}


def test_the_sweepable_keys_are_described_for_the_page(api: Api) -> None:
    keys = {entry["key"]: entry for entry in api.get_sweepable_keys()}

    assert set(keys) >= {"N", "d", "m", "mu", "topology", "deme_weighting"}
    assert keys["N"]["unit"] == "individuals"
    assert keys["m"]["scale"] == "log"
    assert keys["topology"]["choices"] == ["island", "ring", "linear", "torus"]


def test_plan_sweep_counts_points_and_reports_invalid_ones(api: Api) -> None:
    plan = api.plan_sweep(_form(), _request({"key": "d", "values": [2, 3, 1]}))

    assert plan["ok"] is False
    assert "at least" in plan["message"]

    plan = api.plan_sweep(_form(), _request(_D_AXIS))

    assert plan["ok"] is True
    assert plan["gridSize"] == 2
    assert [point["coordinates"] for point in plan["points"]] == [{"d": 2}, {"d": 3}]
    assert plan["newCount"] == 2
    assert plan["reusedCount"] == 0
    assert plan["needsConfirmation"] is False
    assert plan["estimate"]["points"] == 2


def test_plan_sweep_reads_a_range_definition(api: Api) -> None:
    plan = api.plan_sweep(
        _form(),
        _request({"key": "m", "range": {"start": 0.001, "stop": 0.1, "count": 3}}),
    )

    assert [point["coordinates"]["m"] for point in plan["points"]] == [0.001, 0.01, 0.1]


def test_plan_sweep_needs_at_least_one_axis(api: Api) -> None:
    plan = api.plan_sweep(_form(), {"axes": []})

    assert plan["ok"] is False
    assert "at least one axis" in plan["message"]


def test_start_sweep_runs_every_point_and_pushes_events(
    api: Api, window: _FakeWindow
) -> None:
    started = api.start_sweep(_form(), _request(_D_AXIS), "Demes")

    assert started["ok"] is True
    assert started["total"] == 2
    assert window.finished.wait(_WAIT_SECONDS)
    events = window.events()
    assert [e["kind"] for e in events if e["kind"] != "point_progress"] == [
        "point_started",
        "point_done",
        "point_started",
        "point_done",
        "sweep_done",
    ]
    study = groups.get_study(started["studyId"])
    assert study.name == "Demes"
    assert study.run_count == 2
    status = api.get_sweep_status(started["studyId"])
    assert [point["state"] for point in status["points"]] == ["done", "done"]


def test_a_finished_sweep_reports_its_results(api: Api, window: _FakeWindow) -> None:
    started = api.start_sweep(_form(), _request(_D_AXIS), "Demes")
    assert window.finished.wait(_WAIT_SECONDS)

    payload = api.get_sweep_results(started["studyId"])

    assert payload["ok"] is True
    assert [entry["coordinates"] for entry in payload["results"]] == [
        {"d": 2},
        {"d": 3},
    ]
    assert "D" in payload["results"][0]["statistics"]
    assert payload["axes"] == [{"key": "d", "values": [2, 3]}]


def test_a_large_sweep_needs_confirmation_before_anything_is_created(
    api: Api, window: _FakeWindow
) -> None:
    request = _request(
        {"key": "m", "range": {"start": 0.001, "stop": 0.1, "count": 100}}
    )

    result = api.start_sweep(_form(), request, "Big")

    assert result["ok"] is False
    assert result["needsConfirmation"] is True
    assert groups.list_studies() == []
    assert window.pushed == []


def test_a_sweep_and_a_run_cannot_overlap(api: Api, window: _FakeWindow) -> None:
    del window
    api._run_in_flight = True

    refused = api.start_sweep(_form(), _request(_D_AXIS), "Blocked")

    assert refused["ok"] is False
    assert "run is in progress" in refused["message"]
    assert groups.list_studies() == []

    api._run_in_flight = False
    api._sweep_cancel_event = threading.Event()

    also_refused = api.start_sweep(_form(), _request(_D_AXIS), "Blocked")
    run_refused = api.start_run(_form())

    assert "already running" in also_refused["message"]
    assert run_refused["ok"] is False
    assert "sweep is running" in run_refused["message"]


def test_cancelling_stops_the_sweep_and_resume_finishes_it(
    api: Api, window: _FakeWindow
) -> None:
    original = LocalPointRunner.run_point

    def cancel_after_first(
        self: Any, params: Any, cancel_event: threading.Event, on_message: Any = None
    ) -> Any:
        result = original(self, params, cancel_event, on_message)
        api.cancel_sweep()
        return result

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(LocalPointRunner, "run_point", cancel_after_first)
    try:
        started = api.start_sweep(_form(), _request(_D_AXIS), "Interrupted")
        assert window.finished.wait(_WAIT_SECONDS)
    finally:
        monkeypatch.undo()
    assert window.events()[-1]["kind"] == "sweep_cancelled"
    study_id = started["studyId"]
    assert _wait_until_idle(api)
    assert groups.get_study(study_id).run_count == 1

    window.finished.clear()
    window.pushed.clear()
    resumed = api.resume_sweep(study_id)

    assert resumed["ok"] is True
    assert window.finished.wait(_WAIT_SECONDS)
    assert _wait_until_idle(api)
    assert groups.get_study(study_id).run_count == 2


def _wait_until_idle(api: Api) -> bool:
    """Wait for the sweep thread to release the busy guard."""
    for _ in range(_WAIT_SECONDS * 20):
        if api._sweep_cancel_event is None:
            return True
        threading.Event().wait(0.05)
    return False


def test_resuming_something_that_is_not_a_sweep_is_refused(
    api: Api, window: _FakeWindow
) -> None:
    del window
    study = groups.create_study("By hand")

    result = api.resume_sweep(study.study_id)

    assert result["ok"] is False
    assert "not a sweep" in result["message"]


def test_a_sweep_needs_an_active_window(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_module, "_active_window", lambda: None)

    result = api.start_sweep(_form(), _request(_D_AXIS), "No window")

    assert result == {"ok": False, "message": "no active window"}
    assert groups.list_studies() == []


def test_sweep_theory_evaluates_the_closed_form_at_sweep_coordinates(
    api: Api, window: _FakeWindow
) -> None:
    started = api.start_sweep(
        _form(), _request({"key": "m", "values": [0.01, 0.1]}), "Theory"
    )
    assert window.finished.wait(_WAIT_SECONDS)

    theory = api.get_sweep_theory(
        started["studyId"], [{"m": 0.01}, {"m": 0.1}, {"m": 0.1, "topology": "ring"}]
    )

    assert theory["ok"] is True
    low, high, ring = theory["values"]
    assert set(low) == {"D", "G_ST", "E_ST", "H_S", "H_T"}
    assert low["D"] is not None and high["D"] is not None
    assert low["D"] < high["D"] or low["D"] > high["D"]
    # An N axis counts individuals; the closed form sees gene copies.
    by_n = api.get_sweep_theory(started["studyId"], [{"N": 8}, {"N": 16}])["values"]
    assert by_n[0]["H_S"] != by_n[1]["H_S"]
    # A topology the island-model theory does not cover is undefined, not an error.
    assert all(value is None for value in ring.values())


def test_sweep_theory_for_an_unknown_study_is_a_clean_failure(api: Api) -> None:
    result = api.get_sweep_theory("study-ffffffff", [{"m": 0.1}])

    assert result["ok"] is False


def test_list_studies_reports_the_planned_point_count_of_a_sweep_only(
    api: Api, window: _FakeWindow
) -> None:
    started = api.start_sweep(_form(), _request(_D_AXIS), "Counted")
    assert window.finished.wait(_WAIT_SECONDS)
    api.create_study("By hand")

    counts = {study["name"]: study["sweepPointCount"] for study in api.list_studies()}

    assert started["ok"] is True
    assert counts == {"Counted": 2, "By hand": None}
