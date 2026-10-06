"""A configuration already computed is reused, not computed again."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
import webview
from conftest import poll_or_fail

from fim import paths
from fim.gui import app as app_module
from fim.gui.app import Api
from fim.persistence import groups
from fim.reproducibility import Comparison, Difference

from .test_sweep_api import _IDLE_POLL_SECONDS, _form, api, results  # noqa: F401


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
    _wait_idle(api)
    assert window.done.is_set()
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
    _wait_idle(api)
    assert window.done.is_set()

    reused = api.start_run(_form(), second_study.study_id)

    assert reused["reused"] is True
    assert len(list(results.glob("*/manifest.json"))) == 1
    assert groups.get_study(second_study.study_id).run_count == 1
    assert groups.get_study(first_study.study_id).run_count == 1


@pytest.mark.gui
def test_clicking_run_on_a_computed_configuration_shows_it_and_says_so(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    from .test_sweep_screen import _SET_TINY_FIELDS, Poll, _drive  # noqa: PLC0415

    state = """({
        view: window.fim.getRunViewState(),
        pending: window.__fimScrubberPending,
        banner: document.getElementById('run-banner').textContent,
        bannerHidden: document.getElementById('run-banner').hidden
    })"""

    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(
            _SET_TINY_FIELDS + "document.getElementById('run-button').click();"
        )
        # The completed view fetches its scrubber frames in the background;
        # destroying the window under a bridge call still in flight crashes
        # the process, so every stage waits for that counter to reach zero.
        poll_until(state, lambda s: s["view"] == "completed" and s["pending"] == 0)
        before = len(groups.list_studies())
        window.evaluate_js(
            "window.fim.showScreen('screen-configure');"
            "document.getElementById('configure-run-button').click();"
        )
        second = poll_until(
            state,
            lambda s: "already computed" in s["banner"] and s["pending"] == 0,
        )
        return before, second

    before, second = _drive(window, steps)

    assert second["view"] == "completed"
    assert second["bannerHidden"] is False
    runs = list(paths.results_directory().glob("*/manifest.json"))
    assert len(runs) == 1
    assert before == len(groups.list_studies())


class _RecordingWindow(_RunWindow):
    """A run window that also keeps every script it was sent."""

    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[str] = []

    def evaluate_js(self, script: str) -> Any:
        self.scripts.append(script)
        return super().evaluate_js(script)

    def reproducibility_script(self) -> str | None:
        """Return the reproducibility push, if the run sent one.

        Read only after `_wait_idle`: the comparison runs synchronously
        on the run's own drain thread before it clears the in-flight
        guard, so once idle, a push that was ever going to be sent has
        been.
        """
        for script in self.scripts:
            if script.startswith("fim.onReproducibilityChecked("):
                return script
        return None


def _wait_idle(instance: Api) -> None:
    """Wait for the run's drain thread to clear the in-flight guard.

    `start_run` sets `_run_in_flight` before returning and the drain
    thread clears it in its own `finally`, after its terminal push and
    any reproducibility check, so this returns once the run has ended
    however it ended. This used to give up silently after 60 seconds and
    let the test read a run still in progress; it now has no timing
    budget, since how long a run takes depends on machine load, not on
    the commit. The poll interval affects only how soon this notices;
    the completion-signal backstop (`conftest.poll_or_fail`) fails a run
    that never ends, rather than hanging.
    """
    poll_or_fail(
        lambda: instance._run_in_flight,
        lambda in_flight: not in_flight,
        "run drain thread (in-flight guard release)",
        interval=_IDLE_POLL_SECONDS,
    )


def test_a_new_software_version_recomputes_and_a_matching_result_replaces_the_old(
    api: Api,  # noqa: F811
    results: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _RecordingWindow()
    monkeypatch.setattr(app_module, "_active_window", lambda: window)
    study = groups.create_study("Runs")
    api.start_run(_form(), study.study_id)
    _wait_idle(api)
    assert window.done.is_set()
    (old,) = [path.parent for path in results.glob("*/manifest.json")]
    window.done.clear()
    window.scripts.clear()

    monkeypatch.setattr(app_module, "fim_version", "9.9.9")
    second = api.start_run(_form(), study.study_id)

    assert second["ok"] is True
    assert "reused" not in second
    _wait_idle(api)
    assert window.done.is_set()
    script = window.reproducibility_script()
    assert script is not None
    assert '"identical": true' in script
    remaining = [path.parent for path in results.glob("*/manifest.json")]
    assert len(remaining) == 1
    assert remaining[0] != old
    assert groups.study_run_directories(groups.get_study(study.study_id)) == [
        remaining[0].resolve()
    ]


def test_a_recomputed_result_that_differs_is_reported_and_both_runs_are_kept(
    api: Api,  # noqa: F811
    results: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _RecordingWindow()
    monkeypatch.setattr(app_module, "_active_window", lambda: window)
    study = groups.create_study("Runs")
    api.start_run(_form(), study.study_id)
    _wait_idle(api)
    assert window.done.is_set()
    window.done.clear()
    window.scripts.clear()

    def differing(old: Path, new: Path) -> Comparison:
        return Comparison(
            identical=False,
            differences=(Difference("D", 0.25, 0.26),),
            old_version="1.2.0",
            new_version="9.9.9",
        )

    monkeypatch.setattr(app_module, "fim_version", "9.9.9")
    monkeypatch.setattr(app_module, "compare_runs", differing)
    api.start_run(_form(), study.study_id)
    _wait_idle(api)
    assert window.done.is_set()
    script = window.reproducibility_script()

    assert script is not None
    assert '"identical": false' in script
    directories = [path.parent for path in results.glob("*/manifest.json")]
    assert len(directories) == 2
    notes = [d for d in directories if (d / "reproducibility.json").is_file()]
    assert len(notes) == 1
