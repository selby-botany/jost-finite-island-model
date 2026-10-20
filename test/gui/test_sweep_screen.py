"""Headless functional tests for the sweep screen (`webui/screens/sweep.js`).

Each test is one driven session (`webview.start` runs once per window),
so a test's `steps` function performs every action and returns what it
saw.
"""

from __future__ import annotations

import json
import queue
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

import pytest
import webview
from conftest import poll_or_fail

from fim.gui.app import await_bridge_threads
from fim.persistence import groups

from .conftest import poll_page

pytestmark = pytest.mark.gui

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

    def run() -> None:
        try:
            poll_page(window, _READY, lambda value: value is True)
            outcome.put(steps(partial(poll_page, window)))
        except AssertionError as error:
            # A completion-signal backstop failure (any `poll_page` wait
            # or `_wait_for_sweep_finished`) is raised on the test's own
            # thread, not lost on pywebview's.
            outcome.put(error)
        finally:
            await_bridge_threads()
            window.destroy()

    webview.start(run)
    result = outcome.get(timeout=10.0)
    if isinstance(result, AssertionError):
        raise result
    return result


def _wait_for_sweep_finished(window: webview.Window) -> None:
    """Poll until the sweep screen says its sweep has ended, with no budget.

    `window.__fimSweepFinished` turns true on the sweep's `sweep_done`,
    `sweep_cancelled` or error push (`sweep.js`'s `finishSweep`), so this
    ends however the sweep ends. It used to share `_drive`'s old
    400-attempt poll, which a sweep of real runs outlasted on a loaded machine: the
    test then went on while points were still running ("0 of 2 points
    finished ... not run yet", a Study with no runs yet). How long a
    sweep takes depends on machine load, not on the commit; only the
    completion-signal backstop (`conftest.poll_or_fail`) bounds the wait.

    Args:
        window: The window whose sweep screen is running a sweep.

    Returns:
        None

    Raises:
        AssertionError: If the sweep has not ended at the backstop.
    """
    poll_or_fail(
        lambda: window.evaluate_js("window.__fimSweepFinished"),
        lambda finished: finished is True,
        "sweep (window.__fimSweepFinished)",
        interval=_POLL_INTERVAL_SECONDS,
    )


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


def test_sweep_dialog_refuses_to_apply_after_its_document_closes(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(_SET_TINY_FIELDS + _OPEN_SWEEP)
        poll_until(_SCREEN_STATE, _plan_ready)
        window.evaluate_js(
            "window.__fimSweepOwner = window.fim.workspace.selectedId();"
            "window.fim.workspace.createDocument();"
            "window.fim.workspace.closeDocument(window.__fimSweepOwner);"
        )
        window.evaluate_js("document.getElementById('sweep-done-button').click();")
        return window.evaluate_js("""({
            dialogOpen: document.getElementById('modal-sweep').open,
            banner: document.getElementById('sweep-banner-dialog').textContent,
            selectedSweepEnabled:
                document.getElementById('configure-sweep-checkbox').checked
        })""")

    state = _drive(window, steps)

    assert state["dialogOpen"] is True
    assert "document was closed" in state["banner"]
    assert state["selectedSweepEnabled"] is False


def test_sweep_dialog_applies_only_to_its_opening_document(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(_SET_TINY_FIELDS + _OPEN_SWEEP)
        poll_until(_SCREEN_STATE, _plan_ready)
        window.evaluate_js("""(() => {
            const workspace = window.fim.workspace;
            window.__fimSweepOwner = workspace.selectedId();
            window.__fimSweepOther = workspace.createDocument();
            document.getElementById('sweep-done-button').click();
        })()""")
        poll_until(
            "window.fim.getSweepDocumentSnapshot(window.__fimSweepOwner)"
            ".config !== null",
            lambda value: value is True,
        )
        return window.evaluate_js("""(() => {
            const workspace = window.fim.workspace;
            const owner = window.__fimSweepOwner;
            const other = window.__fimSweepOther;
            const ownerSetup = window.fim.getSweepDocumentSnapshot(owner);
            const otherSetup = window.fim.getSweepDocumentSnapshot(other);
            workspace.select(owner);
            const ownerChecked =
                document.getElementById('configure-sweep-checkbox').checked;
            workspace.select(other);
            return {
                ownerEnabled: ownerSetup.enabled,
                ownerHasConfig: ownerSetup.config !== null,
                otherEnabled: otherSetup.enabled,
                otherHasConfig: otherSetup.config !== null,
                ownerChecked,
                otherChecked:
                    document.getElementById('configure-sweep-checkbox').checked,
            };
        })()""")

    state = _drive(window, steps)

    assert state == {
        "ownerEnabled": True,
        "ownerHasConfig": True,
        "otherEnabled": False,
        "otherHasConfig": False,
        "ownerChecked": True,
        "otherChecked": False,
    }


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
        _wait_for_sweep_finished(window)
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
            # The close handler that saves runs after the dialog reports closed.
            lambda s: s["dialogOpen"] is False and s["summary"] != "",
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
            # The close event handler runs after the dialog reports closed.
            lambda s: s["dialogOpen"] is False and s["boxOn"] is False,
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
        _wait_for_sweep_finished(window)

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


def test_closing_the_dialog_any_way_but_cancel_keeps_what_it_showed(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    saved = """({
        axes: window.fim.getSweepDocumentSnapshot(
            window.fim.workspace.selectedId()
        ).config.request.axes.map((axis) => axis.key),
        summary: document.getElementById('configure-sweep-summary').textContent,
        open: document.getElementById('modal-sweep').open
    })"""

    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(_SET_TINY_FIELDS + _OPEN_SWEEP)
        poll_until(_SCREEN_STATE, _plan_ready)
        window.evaluate_js("document.getElementById('sweep-done-button').click();")
        first = poll_until(
            saved, lambda s: s["open"] is False and s["axes"] is not None
        )
        # Reopen, add an axis, then close as Escape does (no Done, no Cancel).
        window.evaluate_js("document.getElementById('configure-sweep-button').click();")
        poll_until(_SCREEN_STATE, lambda s: s["dialogOpen"] is True and s["planReady"])
        window.evaluate_js("document.getElementById('sweep-add-axis-button').click();")
        poll_until(_SCREEN_STATE, lambda s: s["rows"] == 2 and s["planReady"] is True)
        window.evaluate_js("document.getElementById('modal-sweep').close();")
        escaped = poll_until(
            saved, lambda s: s["open"] is False and len(s["axes"]) == 2
        )
        # Reopen, remove the axis, and press Cancel: the two axes stay.
        window.evaluate_js("document.getElementById('configure-sweep-button').click();")
        poll_until(_SCREEN_STATE, lambda s: s["dialogOpen"] is True and s["planReady"])
        window.evaluate_js("document.querySelector('.sweep-axis-remove').click();")
        poll_until(_SCREEN_STATE, lambda s: s["rows"] == 1 and s["planReady"] is True)
        window.evaluate_js(
            "document.getElementById('sweep-dialog-cancel-button').click();"
        )
        cancelled = poll_until(
            saved, lambda s: s["open"] is False and s["axes"] is not None
        )
        return first, escaped, cancelled

    first, escaped, cancelled = _drive(window, steps)

    assert first["axes"] == ["m"]
    assert escaped["axes"] == ["m", "N"]
    assert escaped["summary"] == "Sweep: m (4), N (4), 16 points."
    assert cancelled["axes"] == ["m", "N"]


def test_a_paused_point_is_shown_as_paused_when_the_sweep_reopens(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    import threading  # noqa: PLC0415

    from fim.sweep import SweepSpec, enumerate_points, expand_axis  # noqa: PLC0415
    from fim.sweep_run import (  # noqa: PLC0415
        LocalPointRunner,
        SweepEvent,
        create_sweep_study,
        run_sweep,
    )

    spec = SweepSpec(
        base={
            "N": 16,
            "ploidy": "diploid",
            "m": 0.1,
            "mu": 0.01,
            "seed": 7,
            "n_replicates": 1,
            "max_generations": 400,
        },
        axes=(expand_axis("d", [2, 3]),),
    )
    study_id = create_sweep_study(spec, enumerate_points(spec), "Paused").study_id
    cancel = threading.Event()

    def cancel_at_first_progress(event: SweepEvent) -> None:
        if event.kind == "point_progress":
            cancel.set()

    run_sweep(
        study_id,
        LocalPointRunner(),
        cancel_at_first_progress,
        cancel,
        points_at_once=1,
        # The test is about how a paused point is shown, so which point starts
        # first must not matter: pin grid order (the default starts the
        # costliest first).
        costliest_first=False,
    )
    states = """Array.from(document.querySelectorAll('.sweep-point-state'))
        .map((cell) => cell.textContent)"""

    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(f"window.fim.showSweepProgress('{study_id}')")
        return poll_until(states, lambda s: s is not None and len(s) == 2)

    assert _drive(window, steps) == ["paused", "waiting"]


_COST_STATE = """({
    planReady: window.__fimSweepPlanReady,
    costReady: window.__fimSweepCostReady,
    shown: !document.getElementById('sweep-cost').hidden,
    time: document.getElementById('sweep-cost-time').textContent,
    disk: document.getElementById('sweep-cost-disk').textContent,
    probe: document.getElementById('sweep-cost-probe').textContent,
})"""


def test_the_dialog_shows_the_three_cost_lines_and_updates_them_on_an_edit(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(_SET_TINY_FIELDS + _OPEN_SWEEP)
        poll_until(_SCREEN_STATE, _plan_ready)
        first = poll_until(
            _COST_STATE, lambda s: s["costReady"] is True and s["shown"] is True
        )
        window.evaluate_js(_AXIS_D_LIST)
        second = poll_until(
            _COST_STATE,
            lambda s: (
                s["costReady"] is True and s["probe"].startswith("2 shape class(es)")
            ),
        )
        return first, second

    first, second = _drive(window, steps)

    assert first["time"].startswith("about ")
    assert "with the full trajectory log" in first["disk"]
    assert first["probe"].startswith("1 shape class(es) measured")
    assert second["probe"].startswith("2 shape class(es) measured")  # d = 2 and 3


def test_a_plan_with_a_problem_hides_the_cost(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(_SET_TINY_FIELDS + _OPEN_SWEEP)
        poll_until(_SCREEN_STATE, _plan_ready)
        poll_until(_COST_STATE, lambda s: s["costReady"] is True and s["shown"] is True)
        window.evaluate_js(_AXIS_D_LIST)
        poll_until(_COST_STATE, lambda s: s["costReady"] is True)
        window.evaluate_js(
            "const list = document.querySelector('.sweep-axis-list');"
            "list.value = 'two';"
            "list.dispatchEvent(new Event('input', {bubbles: true}));"
        )
        return poll_until(_COST_STATE, lambda s: s["shown"] is False)

    state = _drive(window, steps)

    assert state["shown"] is False


_PROGRESS_STATE = """({
    eta: document.getElementById('sweep-progress-eta').textContent,
    took: Array.from(document.querySelectorAll('.sweep-point-took'))
        .map((cell) => cell.textContent),
    header: Array.from(document.querySelectorAll('#sweep-progress-head th'))
        .map((cell) => cell.textContent),
    etaReady: window.__fimSweepEtaReady,
})"""


def test_the_progress_view_shows_the_time_left_and_how_long_each_point_took(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(_SET_TINY_FIELDS + _OPEN_SWEEP)
        poll_until(_SCREEN_STATE, _plan_ready)
        window.evaluate_js(_AXIS_D_LIST)
        poll_until(
            _SCREEN_STATE, lambda s: s["planReady"] is True and s["tableRows"] == 2
        )
        window.evaluate_js(_START)
        _wait_for_sweep_finished(window)
        return poll_until(
            _PROGRESS_STATE,
            lambda s: (
                s["etaReady"] is True
                and s["eta"] == "Time left: none: every point is done"
            ),
        )

    state = _drive(window, steps)

    assert state["header"][-2:] == ["State", "Took"]
    assert len(state["took"]) == 2
    assert all(text.endswith(("s", "min", "h")) for text in state["took"])


# Point axis row `index` at `key` with an explicit list of values.
_SET_ROW = """
function fire(element, type) {
    element.dispatchEvent(new Event(type, {bubbles: true}));
}
function setRow(index, key, text) {
    const row = document.querySelectorAll('.sweep-axis')[index];
    const select = row.querySelector('.sweep-axis-key');
    select.value = key; fire(select, 'change');
    const mode = row.querySelector('.sweep-axis-mode');
    mode.value = 'list'; fire(mode, 'change');
    const list = row.querySelector('.sweep-axis-list');
    list.value = text; fire(list, 'input');
}
"""

_KEY_MENU = """(() => {
    const select = document.querySelector('.sweep-axis-key');
    const group = select.querySelector('optgroup');
    return {
        curated: Array.from(select.children)
            .filter((child) => child.tagName === 'OPTION')
            .map((option) => option.value),
        groupLabel: group === null ? null : group.label,
        more: group === null
            ? []
            : Array.from(group.querySelectorAll('option')).map((o) => o.value),
    };
})()"""


def test_the_key_menu_lists_the_curated_parameters_then_more_parameters(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(_SET_TINY_FIELDS + _OPEN_SWEEP)
        poll_until(_SCREEN_STATE, _plan_ready)
        return window.evaluate_js(_KEY_MENU)

    menu = _drive(window, steps)

    assert menu["curated"][:5] == ["N", "d", "m", "mu", "Nm"]
    assert menu["groupLabel"] == "More parameters"
    assert {"n_loci", "mutation_model", "track_expensive_statistics"} <= set(
        menu["more"]
    )
    assert not set(menu["curated"]) & set(menu["more"])


def test_a_generic_parameter_opens_on_a_list_and_a_third_axis_is_allowed(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(_SET_TINY_FIELDS + _OPEN_SWEEP)
        poll_until(_SCREEN_STATE, _plan_ready)
        window.evaluate_js(_SET_ROW + "setRow(0, 'd', '2, 3');")
        poll_until(_SCREEN_STATE, lambda s: s["planReady"] and s["tableRows"] == 2)
        for _ in range(2):
            window.evaluate_js(
                "document.getElementById('sweep-add-axis-button').click();"
            )
        # Choosing a setting with no span of its own opens its row on a list.
        opened_on = window.evaluate_js(
            _SET_ROW + "const pick = document.querySelectorAll('.sweep-axis')[1]"
            ".querySelector('.sweep-axis-key');"
            "pick.value = 'n_loci'; fire(pick, 'change');"
            "document.querySelectorAll('.sweep-axis')[1]"
            ".querySelector('.sweep-axis-mode').value;"
        )
        window.evaluate_js(
            _SET_ROW + "setRow(1, 'n_loci', '1, 2'); setRow(2, 'N', '20, 40');"
        )
        three = poll_until(
            _SCREEN_STATE,
            lambda s: s["rows"] == 3 and s["planReady"] and s["tableRows"] == 8,
        )
        return three, opened_on

    three, opened_on = _drive(window, steps)

    assert opened_on == "list"
    assert three["summary"].startswith("8 points")
    assert three["doneDisabled"] is False


def test_axes_can_be_combined_together_instead_of_as_a_grid(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    set_strategy = (
        "const strategy = document.getElementById('sweep-strategy');"
        "strategy.value = '{value}';"
        "strategy.dispatchEvent(new Event('change', {{bubbles: true}}));"
    )

    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(_SET_TINY_FIELDS + _OPEN_SWEEP)
        poll_until(_SCREEN_STATE, _plan_ready)
        window.evaluate_js("document.getElementById('sweep-add-axis-button').click();")
        window.evaluate_js(
            _SET_ROW + "setRow(0, 'd', '2, 3'); setRow(1, 'N', '20, 40');"
        )
        grid = poll_until(
            _SCREEN_STATE, lambda s: s["planReady"] and s["tableRows"] == 4
        )
        window.evaluate_js(set_strategy.format(value="zip"))
        zipped = poll_until(
            _SCREEN_STATE, lambda s: s["planReady"] and s["tableRows"] == 2
        )
        window.evaluate_js(_SET_ROW + "setRow(1, 'N', '20, 40, 80');")
        unequal = poll_until(_SCREEN_STATE, lambda s: s["problem"] is True)
        return grid, zipped, unequal

    grid, zipped, unequal = _drive(window, steps)

    assert grid["summary"].startswith("4 points")
    assert zipped["summary"].startswith("2 points")
    assert "equal lengths" in unequal["summary"]
    assert unequal["doneDisabled"] is True


_KEEP_STATE = """({
    value: document.getElementById('sweep-keep').value,
    detailHidden: document.getElementById('sweep-keep-detail').hidden,
    labels: Array.from(document.querySelectorAll('#sweep-keep option'))
        .map((option) => option.textContent),
    note: document.getElementById('sweep-keep-note').textContent,
    history: document.getElementById('sweep-history').value,
    costReady: window.__fimSweepCostReady,
    request: JSON.stringify(buildSweepRequest().recording)
})"""

_CHOOSE_KEEP = (
    "const keep = document.getElementById('sweep-keep');"
    "keep.value = '{value}';"
    "keep.dispatchEvent(new Event('change', {{bubbles: true}}));"
)


def test_the_keep_selector_shows_a_disk_figure_for_each_choice(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(_SET_TINY_FIELDS + _OPEN_SWEEP)
        poll_until(_SCREEN_STATE, _plan_ready)
        measured = poll_until(
            _KEEP_STATE,
            lambda s: s["costReady"] is True and "up to" in s["labels"][0],
        )
        window.evaluate_js(_CHOOSE_KEEP.format(value="tapered"))
        tapered = poll_until(_KEEP_STATE, lambda s: s["detailHidden"] is False)
        window.evaluate_js(_CHOOSE_KEEP.format(value="full"))
        back = poll_until(_KEEP_STATE, lambda s: s["detailHidden"] is True)
        return measured, tapered, back

    measured, tapered, back = _drive(window, steps)

    assert measured["value"] == "full" and measured["history"] == "compact"
    assert all("up to" in label for label in measured["labels"])
    assert json.loads(measured["request"]) == {
        "retention": "full",
        "keepLast": 1000,
        "keepBurnIn": False,
        "history": "compact",
    }
    assert json.loads(tapered["request"])["retention"] == "tapered"
    assert back["value"] == "full"
