"""Headless functional tests for the sweep screen (`webui/screens/sweep.js`).

Each test is one driven session (`webview.start` runs once per window),
so a test's `steps` function performs every action and returns what it
saw.
"""

from __future__ import annotations

import queue
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import webview

from fim.gui.app import await_bridge_threads
from fim.persistence import groups

pytestmark = pytest.mark.gui

_POLL_ATTEMPTS = 400
_POLL_INTERVAL_SECONDS = 0.1
_READY = "window.__fimRunViewReady === true"

_SET_TINY_FIELDS = """
function setField(name, value) {
    const field = document.getElementById(`field-${name}`);
    field.value = value;
    field.dispatchEvent(new Event('input', {bubbles: true}));
}
setField('N', '20');
setField('d', '2');
setField('m_rate', '0.1');
setField('mu_value', '0.01');
setField('locus_lengths', '200');
"""

# Turning the Sweep box on opens the setup dialog, as the botanist would.
_OPEN_SWEEP = "document.getElementById('configure-sweep-checkbox').click();"
# Done saves the dialog's axes; Run then starts the sweep, as it always does.
_START = (
    "document.getElementById('sweep-done-button').click();"
    "document.getElementById('run-button').click();"
)

_SCREEN_STATE = """({
    dialogOpen: document.getElementById('modal-sweep').open,
    planReady: window.__fimSweepPlanReady,
    rows: document.querySelectorAll('.sweep-axis').length,
    summary: document.getElementById('sweep-plan-summary').textContent,
    problem: document.getElementById('sweep-plan-summary')
        .classList.contains('sweep-plan-problem'),
    tableRows: document.querySelectorAll('#sweep-plan-body tr').length,
    doneDisabled: document.getElementById('sweep-done-button').disabled,
    summaryOnConfigure: document.getElementById('configure-sweep-summary').textContent,
    values: Array.from(document.querySelectorAll('.sweep-axis-values'))
        .map((span) => span.textContent)
})"""

# Point the first axis row at `d` with an explicit list.
_AXIS_D_LIST = """
function fire(element, type) {
    element.dispatchEvent(new Event(type, {bubbles: true}));
}
const row = document.querySelector('.sweep-axis');
const key = row.querySelector('.sweep-axis-key');
key.value = 'd'; fire(key, 'change');
const mode = row.querySelector('.sweep-axis-mode');
mode.value = 'list'; fire(mode, 'change');
const list = row.querySelector('.sweep-axis-list');
list.value = '2, 3'; fire(list, 'input');
"""

Poll = Callable[[str, Callable[[Any], bool]], Any]


def _drive(window: webview.Window, steps: Callable[[Poll], Any]) -> Any:
    """Run `steps` against a ready `window` and return its result."""
    outcome: queue.Queue[Any] = queue.Queue(maxsize=1)

    def poll_until(script: str, predicate: Callable[[Any], bool]) -> Any:
        value = None
        for _ in range(_POLL_ATTEMPTS):
            value = window.evaluate_js(script)
            if predicate(value):
                return value
            time.sleep(_POLL_INTERVAL_SECONDS)
        return value

    def run() -> None:
        try:
            poll_until(_READY, lambda value: value is True)
            outcome.put(steps(poll_until))
        finally:
            await_bridge_threads()
            window.destroy()

    webview.start(run)
    return outcome.get(timeout=10.0)


def _plan_ready(state: Any) -> bool:
    return (
        state is not None and state["planReady"] is True and state["dialogOpen"] is True
    )


def test_configure_opens_the_sweep_screen_with_one_default_axis(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(_SET_TINY_FIELDS + _OPEN_SWEEP)
        return poll_until(_SCREEN_STATE, _plan_ready)

    state = _drive(window, steps)

    assert state["rows"] == 1
    assert state["summary"].startswith("4 points, 4 new")
    assert state["tableRows"] == 4
    assert state["doneDisabled"] is False
    assert state["values"] == ["0.0001, 0.001, 0.01, 0.1"]


def test_adding_and_removing_an_axis_changes_the_grid(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(_SET_TINY_FIELDS + _OPEN_SWEEP)
        poll_until(_SCREEN_STATE, _plan_ready)
        window.evaluate_js("document.getElementById('sweep-add-axis-button').click();")
        two = poll_until(
            _SCREEN_STATE, lambda s: s["rows"] == 2 and s["planReady"] is True
        )
        window.evaluate_js("document.querySelector('.sweep-axis-remove').click();")
        one = poll_until(
            _SCREEN_STATE, lambda s: s["rows"] == 1 and s["planReady"] is True
        )
        return two, one

    two, one = _drive(window, steps)

    # The second axis opens on the first unused key, `N`, also 4 points.
    assert two["summary"].startswith("16 points")
    assert one["summary"].startswith("4 points")


def test_a_value_that_is_not_a_number_is_named_and_stops_the_run(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(_SET_TINY_FIELDS + _OPEN_SWEEP)
        poll_until(_SCREEN_STATE, _plan_ready)
        window.evaluate_js(
            "const start = document.querySelector('.sweep-axis-start');"
            "start.value = 'lots';"
            "start.dispatchEvent(new Event('input', {bubbles: true}));"
        )
        return poll_until(_SCREEN_STATE, lambda s: s["problem"] is True)

    state = _drive(window, steps)

    assert "must be a number" in state["summary"]
    assert state["doneDisabled"] is True


def test_running_a_sweep_shows_progress_and_creates_the_study(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    finished_state = """({
        finished: window.__fimSweepFinished,
        title: document.getElementById('sweep-title').textContent,
        states: Array.from(document.querySelectorAll('.sweep-point-state'))
            .map((cell) => cell.textContent),
        bar: [document.getElementById('sweep-progress-bar').value,
              document.getElementById('sweep-progress-bar').max],
        cancelHidden: document.getElementById('sweep-cancel-button').hidden,
        homeHidden: document.getElementById('sweep-home-button').hidden
    })"""

    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(_SET_TINY_FIELDS + _OPEN_SWEEP)
        poll_until(_SCREEN_STATE, _plan_ready)
        window.evaluate_js(_AXIS_D_LIST)
        poll_until(
            _SCREEN_STATE, lambda s: s["planReady"] is True and s["tableRows"] == 2
        )
        window.evaluate_js(_START)
        return poll_until(finished_state, lambda s: s["finished"] is True)

    settled = _drive(window, steps)

    assert settled["title"] == "Sweep finished"
    assert settled["states"] == ["done", "done"]
    assert settled["bar"] == [2, 2]
    assert settled["cancelHidden"] is True
    assert settled["homeHidden"] is False
    (study,) = [s for s in groups.list_studies() if s.sweep_spec is not None]
    assert study.name == "Sweep of d"
    assert study.run_count == 2


def test_a_large_sweep_needs_a_second_press_of_run(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    banner = """({
        text: document.getElementById('run-banner').textContent,
        screen: document.querySelector('.screen:not([hidden])').id
    })"""

    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(_SET_TINY_FIELDS + _OPEN_SWEEP)
        poll_until(_SCREEN_STATE, _plan_ready)
        window.evaluate_js(
            "const count = document.querySelector('.sweep-axis-count');"
            "count.value = '100';"
            "count.dispatchEvent(new Event('input', {bubbles: true}));"
        )
        poll_until(
            _SCREEN_STATE,
            lambda s: s["planReady"] is True and s["summary"].startswith("100 points"),
        )
        window.evaluate_js(_START)
        return poll_until(banner, lambda s: "Press Run again" in s["text"])

    armed = _drive(window, steps)

    assert "100 points" in armed["text"]
    assert armed["screen"] != "screen-sweep"
    assert [s for s in groups.list_studies() if s.sweep_spec is not None] == []


def test_the_sweep_is_part_of_configure_and_run_stays_run(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(_SET_TINY_FIELDS)
        before = window.evaluate_js(
            "({boxOn: document.getElementById('configure-sweep-checkbox').checked, "
            "setupHidden: document.getElementById('configure-sweep-button').hidden, "
            "runLabel: document.getElementById('configure-run-button')"
            ".textContent.trim()})"
        )
        window.evaluate_js(_OPEN_SWEEP)
        poll_until(_SCREEN_STATE, _plan_ready)
        window.evaluate_js("document.getElementById('sweep-done-button').click();")
        after = poll_until(
            "({boxOn: document.getElementById('configure-sweep-checkbox').checked, "
            "setupHidden: document.getElementById('configure-sweep-button').hidden, "
            "dialogOpen: document.getElementById('modal-sweep').open, "
            "summary: document.getElementById('configure-sweep-summary').textContent, "
            "runLabel: document.getElementById('configure-run-button')"
            ".textContent.trim()})",
            lambda s: s["dialogOpen"] is False,
        )
        return before, after

    before, after = _drive(window, steps)

    assert before["boxOn"] is False
    assert before["setupHidden"] is True
    assert after["boxOn"] is True
    assert after["setupHidden"] is False
    assert after["summary"] == "Sweep: m (4), 4 points."
    assert before["runLabel"] == after["runLabel"] == "Run"


def test_cancelling_the_dialog_with_nothing_saved_turns_the_box_off(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(_SET_TINY_FIELDS + _OPEN_SWEEP)
        poll_until(_SCREEN_STATE, _plan_ready)
        window.evaluate_js(
            "document.getElementById('sweep-dialog-cancel-button').click();"
        )
        return poll_until(
            "({boxOn: document.getElementById('configure-sweep-checkbox').checked, "
            "dialogOpen: document.getElementById('modal-sweep').open})",
            lambda s: s["dialogOpen"] is False,
        )

    state = _drive(window, steps)

    assert state["boxOn"] is False


def test_the_sweep_runs_into_the_study_chosen_on_configure(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    study = groups.create_study("Chosen")

    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(_SET_TINY_FIELDS + _OPEN_SWEEP)
        poll_until(_SCREEN_STATE, _plan_ready)
        window.evaluate_js(
            "window.fim.refreshRunStudySelectOptions().then(() => {"
            f"document.getElementById('run-study-select').value = '{study.study_id}';"
            "});"
        )
        poll_until(
            "document.getElementById('run-study-select').value",
            lambda value: value == study.study_id,
        )
        window.evaluate_js(_START)
        return poll_until(
            "window.__fimSweepFinished", lambda finished: finished is True
        )

    _drive(window, steps)

    chosen = groups.get_study(study.study_id)
    assert chosen.sweep_spec is not None
    assert chosen.run_count == 4
    assert [s.name for s in groups.list_studies()] == ["Chosen"]


def test_the_dialog_says_how_many_points_run_at_once_and_a_choice_sticks(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    note = """({
        planReady: window.__fimSweepPlanReady,
        dialogOpen: document.getElementById('modal-sweep').open,
        note: document.getElementById('sweep-concurrency-note').textContent,
        choice: document.getElementById('sweep-points-at-once').value
    })"""

    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(_SET_TINY_FIELDS + _OPEN_SWEEP)
        automatic = poll_until(
            note, lambda s: s["planReady"] is True and "at once" in s["note"]
        )
        window.evaluate_js(
            "const select = document.getElementById('sweep-points-at-once');"
            "select.value = '1';"
            "select.dispatchEvent(new Event('change', {bubbles: true}));"
        )
        one = poll_until(
            note, lambda s: s["planReady"] is True and s["note"].startswith("1 point")
        )
        window.evaluate_js("document.getElementById('sweep-done-button').click();")
        window.evaluate_js("document.getElementById('configure-sweep-button').click();")
        reopened = poll_until(
            note, lambda s: s["dialogOpen"] is True and s["planReady"] is True
        )
        return automatic, one, reopened

    automatic, one, reopened = _drive(window, steps)

    assert automatic["note"].startswith("Automatic: ")
    assert "cores" in automatic["note"]
    assert one["note"].startswith("1 point at once")
    assert reopened["choice"] == "1"
