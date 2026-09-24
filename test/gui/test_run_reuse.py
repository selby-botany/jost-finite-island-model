"""A configuration already computed is reused, not computed again."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
import webview

from fim import paths
from fim.gui import app as app_module
from fim.gui.app import Api
from fim.persistence import groups

from .test_sweep_api import _WAIT_SECONDS, _form, api, results  # noqa: F401


class _RunWindow:
    """Records pushes and signals when a run reports done."""

    def __init__(self) -> None:
        self.done = threading.Event()

    def evaluate_js(self, script: str) -> Any:
        if script.startswith(("fim.onRunDone(", "fim.onBatchDone(")):
            self.done.set()
        return None


def test_running_the_same_configuration_twice_computes_it_once(
    api: Api,  # noqa: F811
    results: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _RunWindow()
    monkeypatch.setattr(app_module, "_active_window", lambda: window)
    study = groups.create_study("Runs")

    first = api.start_run(_form(), study.study_id)
    assert first["ok"] is True
    assert "reused" not in first
    assert window.done.wait(_WAIT_SECONDS)
    directories = list(results.glob("*/manifest.json"))
    assert len(directories) == 1

    second = api.start_run(_form(), study.study_id)

    assert second["ok"] is True
    assert second["reused"] is True
    assert Path(second["directory"]) == directories[0].parent
    assert list(results.glob("*/manifest.json")) == directories
    assert groups.get_study(study.study_id).run_count == 1


def test_a_reused_run_is_attached_to_the_study_chosen_the_second_time(
    api: Api,  # noqa: F811
    results: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _RunWindow()
    monkeypatch.setattr(app_module, "_active_window", lambda: window)
    first_study = groups.create_study("First")
    second_study = groups.create_study("Second")
    api.start_run(_form(), first_study.study_id)
    assert window.done.wait(_WAIT_SECONDS)

    reused = api.start_run(_form(), second_study.study_id)

    assert reused["reused"] is True
    assert len(list(results.glob("*/manifest.json"))) == 1
    assert groups.get_study(second_study.study_id).run_count == 1
    assert groups.get_study(first_study.study_id).run_count == 1


def test_clicking_run_on_a_computed_configuration_shows_it_and_says_so(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    from .test_sweep_screen import _SET_TINY_FIELDS, Poll, _drive  # noqa: PLC0415

    state = """({
        view: window.fim.getRunViewState(),
        banner: document.getElementById('run-banner').textContent,
        bannerHidden: document.getElementById('run-banner').hidden
    })"""

    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(
            _SET_TINY_FIELDS + "document.getElementById('run-button').click();"
        )
        poll_until(state, lambda s: s["view"] == "completed")
        before = len(groups.list_studies())
        window.evaluate_js(
            "window.fim.showScreen('screen-configure');"
            "document.getElementById('configure-run-button').click();"
        )
        second = poll_until(state, lambda s: "already computed" in s["banner"])
        return before, second

    before, second = _drive(window, steps)

    assert second["view"] == "completed"
    assert second["bannerHidden"] is False
    runs = list(paths.results_directory().glob("*/manifest.json"))
    assert len(runs) == 1
    assert before == len(groups.list_studies())
