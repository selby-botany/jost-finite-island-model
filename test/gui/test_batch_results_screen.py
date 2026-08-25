"""Headless functional tests for the unified run view's own `completed`
state, batch case (design doc §4.4, §7.6; unified-run-view design §3.2.5,
§8 Phase E).

Real DOM-driven proof that a completed batch actually reaches
`fim.enterCompletedState(payload, true)` (`webui/screens/run-view-
completed.js`) and renders the batch's own pooled scatter,
confidence-interval bars, and per-replicate table — `test/gui/
test_app_api.py`'s own `_batch_done_payload` tests already prove the
payload's own content is correct as plain Python calls;
`test/gui/test_batch_running.py` already proves the bridge dispatches a
real batch and pushes its `"done"` message correctly. This file proves
the third link: that the page's own JavaScript, given that real payload,
renders it — which no Python-only test can check.

The scalar counterpart is `test/gui/test_results_screen.py`; the two
files share the same element ids (`results-run-id`, `run-canvas`,
`run-deme-pair-selector`, `open-folder-button`, ...) below `completed`,
since `enterCompletedState` is the one shared entry point for both kinds
of run (design §3.2.5's "one state model, not two"), branching internally
on `isBatch` only for the statistics/table fields that actually differ.

Drives a real, small (two-replicate) batch through the actual UI, the
same tiny-scale `_SET_TINY_BATCH_FIELDS` `test/gui/test_batch_running.py`
uses, and waits on `Api`'s own `on_message` hook rather than polling the
DOM for "the run view is showing `completed`" — see `test/gui/
test_running_screen.py`'s own module docstring for why a DOM-polling
test-driving loop, run concurrently with a real background thread's own
`evaluate_js` pushes, is the wrong tool here. Once `done_event` fires,
`_drain_batch_messages`'s thread has already returned (the terminal
message is the last thing it processes), so the one verification
`evaluate_js` call each test below makes is never concurrent with
anything.
"""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import webview

from fim.gui.app import Api, create_window
from fim.gui.batch_runner import BatchMessage
from fim.gui.runner import RunMessage

pytestmark = pytest.mark.gui

_READY_POLL_INTERVAL_SECONDS = 0.05
_READY_POLL_ATTEMPTS = 200
_EVENT_WAIT_TIMEOUT_SECONDS = 30.0
_OUTCOME_TIMEOUT_SECONDS = 40.0

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"

# Mirrors `test/gui/test_batch_running.py`'s own `_SET_TINY_BATCH_FIELDS`.
_SET_TINY_BATCH_FIELDS = """
function setField(name, value) {
    const field = document.getElementById(`field-${name}`);
    field.value = value;
    field.dispatchEvent(new Event('input', {bubbles: true}));
}
setField('N', '20');
setField('d', '2');
setField('seed', '20260814');
setField('m_rate', '0.1');
setField('mu_value', '0.01');
setField('locus_lengths', '200');
setField('convergence_window', '4');
setField('convergence_tolerance', '1.0');
setField('max_generations', '10');
setField('n_replicates', '2');
setField('max_workers', '2');
"""


def _wait_for_input_screen_ready(window: webview.Window) -> None:
    """Poll until the run view's own async initialization has finished.

    Safe to poll here: no background thread exists yet, so this loop is
    never a second concurrent `evaluate_js` caller.
    """
    for _ in range(_READY_POLL_ATTEMPTS):
        if window.evaluate_js(_INPUT_SCREEN_READY):
            return
        time.sleep(_READY_POLL_INTERVAL_SECONDS)
    raise AssertionError(
        f"the run view was not ready within "
        f"{_READY_POLL_ATTEMPTS * _READY_POLL_INTERVAL_SECONDS}s"
    )


def test_a_completed_batch_renders_the_run_view() -> None:
    """A finished two-replicate batch shows a run id, two table rows, and six CI bars.

    Every one of the six named statistics gets a confidence-interval
    bar (`buildCiMeter`/`buildOmittedMeter` — design §4.4's "a statistic
    omitted from summary.json still renders as explicitly omitted, not
    blank"), so `#batch-results-summary` always has exactly six
    `.ci-bar` children regardless of which, if any, statistics
    `replicate_summary` actually defined for this particular run.
    """
    done_event = threading.Event()
    messages: list[RunMessage | BatchMessage] = []

    def on_message(message: RunMessage | BatchMessage) -> None:
        messages.append(message)
        if message[0] in ("done", "cancelled", "error"):
            done_event.set()

    window = create_window(api=Api(on_message=on_message), hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _wait_for_input_screen_ready(window)
            window.evaluate_js(
                _SET_TINY_BATCH_FIELDS
                + "document.getElementById('run-button').click();"
            )
            settled = None
            if done_event.wait(timeout=_EVENT_WAIT_TIMEOUT_SECONDS):
                settled = window.evaluate_js(
                    "({"
                    "runViewState: window.fim.getRunViewState(), "
                    "runId: "
                    "document.getElementById('results-run-id').textContent, "
                    "rowCount: "
                    "document.getElementById('batch-results-table-body')"
                    ".children.length, "
                    "ciBarCount: "
                    "document.getElementById('batch-results-summary')"
                    ".querySelectorAll('.ci-bar').length, "
                    "firstRowCells: Array.from("
                    "document.getElementById('batch-results-table-body')"
                    ".children[0].children"
                    ").map((cell) => cell.textContent), "
                    "secondRowCells: Array.from("
                    "document.getElementById('batch-results-table-body')"
                    ".children[1].children"
                    ").map((cell) => cell.textContent)"
                    "})"
                )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert settled is not None, (
        f"done_event was never set within {_EVENT_WAIT_TIMEOUT_SECONDS}s "
        f"(messages received: {messages!r})"
    )
    assert settled["runViewState"] == "completed"
    assert settled["runId"].startswith("run-")
    # Row 0 is the p_0 baseline; rows 1 and 2 are the two replicates.
    assert settled["rowCount"] == 3
    assert settled["ciBarCount"] == 6
    # p_0 row: generation=0, outcome="initial".
    first_row = settled["firstRowCells"]
    assert first_row[0] == "0"
    # Column order: Generation | Replicate | Outcome | ...
    assert first_row[2] == "initial"
    # Second row is the first replicate. Columns: Generation | Replicate | Outcome | ...
    # "Converged", not "Converged (statistic converged)" -- `replicate.
    # reason` is redundant with `converged` in the true case (`StopReason`
    # only ever pairs them one way), so the parenthetical said nothing a
    # reader did not already know.
    second_row = settled["secondRowCells"]
    assert second_row[2] == "Converged"


def test_batch_deme_pair_selector_switches_to_a_chosen_pair_and_back() -> None:
    """ "Show pair"/"Show overview" round-trip through the real batch bridge and back.

    The batch counterpart to `test_results_screen.py`'s own identically
    named scalar-run test — `d=3` past the overview's default "Deme 1
    vs Deme 2" pairwise panel, "Show pair" (Deme 1 vs Deme 3) redraws
    the canvas via a real `Api.get_batch_deme_pair_panel` call (pooled
    across both replicates, `deme_pair_panel`'s own docstring), and
    "Show overview" redraws it back to the exact original panel with
    no further bridge call. The selector and canvas are the same shared
    elements the scalar test drives (`run-deme-pair-selector`, `run-x-
    deme`, `run-canvas`, ...) — `run-view-completed.js` dispatches to
    the batch- or scalar-flavored bridge call by `window.fim.
    getRunViewState`'s own `isBatch` bookkeeping, not by a different
    element id.
    """
    done_event = threading.Event()

    def on_message(message: RunMessage | BatchMessage) -> None:
        if message[0] in ("done", "cancelled", "error"):
            done_event.set()

    window = create_window(api=Api(on_message=on_message), hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)
    set_fields = _SET_TINY_BATCH_FIELDS.replace(
        "setField('d', '2');", "setField('d', '3');"
    )

    def _poll_until(script: str, predicate: Any) -> Any:
        value = None
        for _ in range(_READY_POLL_ATTEMPTS):
            value = window.evaluate_js(script)
            if predicate(value):
                return value
            time.sleep(_READY_POLL_INTERVAL_SECONDS)
        return value

    def _drive() -> None:
        try:
            _wait_for_input_screen_ready(window)
            window.evaluate_js(
                set_fields + "document.getElementById('run-button').click();"
            )
            settled = None
            if done_event.wait(timeout=_EVENT_WAIT_TIMEOUT_SECONDS):
                selector_state = window.evaluate_js(
                    "({"
                    "hidden: document.getElementById("
                    "'run-deme-pair-selector').hidden, "
                    "optionCount: document.getElementById("
                    "'run-x-deme').options.length"
                    "})"
                )
                overview_snapshot = window.evaluate_js(
                    "document.getElementById('run-canvas').toDataURL()"
                )
                window.evaluate_js(
                    "document.getElementById('run-x-deme').value = '1';"
                    "document.getElementById('run-y-deme').value = '3';"
                    "document.getElementById('run-show-pair-button').click();"
                )
                pair_snapshot = _poll_until(
                    "document.getElementById('run-canvas').toDataURL()",
                    lambda value: value != overview_snapshot,
                )
                window.evaluate_js(
                    "document.getElementById('run-show-overview-button').click();"
                )
                reverted_snapshot = _poll_until(
                    "document.getElementById('run-canvas').toDataURL()",
                    lambda value: value == overview_snapshot,
                )
                settled = {
                    "selectorHidden": selector_state["hidden"],
                    "optionCount": selector_state["optionCount"],
                    "pairDiffersFromOverview": pair_snapshot != overview_snapshot,
                    "revertedMatchesOverview": reverted_snapshot == overview_snapshot,
                }
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert settled is not None, (
        f"done_event was never set within {_EVENT_WAIT_TIMEOUT_SECONDS}s"
    )
    assert settled["selectorHidden"] is False
    assert settled["optionCount"] == 3
    assert settled["pairDiffersFromOverview"] is True
    assert settled["revertedMatchesOverview"] is True


def test_running_a_batch_again_from_completed_starts_a_new_batch() -> None:
    """ "Run simulation," clicked again from a completed batch, starts a new one.

    The batch counterpart to `test_results_screen.py`'s own `test_
    running_simulation_again_from_completed_starts_a_new_run` — no
    separate "New run" button exists any more (retired this phase,
    design §8 Phase E: the shared controls are always present, so the
    same button that started the first batch is already right there).
    Proven by the *output directory* changing between the two completed
    views, the same reason the scalar test gives: `deterministic_run_id
    (params)` is deliberately the same string for two batches of
    identical form values, so it cannot serve as this test's proof.

    Drives the window directly, waiting on two separate `on_message`
    hooks (one per batch) rather than `conftest.py`'s `drive` fixture,
    which destroys the window after one such stage.
    """
    first_done = threading.Event()
    second_done = threading.Event()
    done_count = 0

    def on_message(message: RunMessage | BatchMessage) -> None:
        nonlocal done_count
        if message[0] in ("done", "cancelled", "error"):
            done_count += 1
            if done_count == 1:
                first_done.set()
            elif done_count == 2:
                second_done.set()

    window = create_window(api=Api(on_message=on_message), hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _wait_for_input_screen_ready(window)
            window.evaluate_js(
                _SET_TINY_BATCH_FIELDS
                + "document.getElementById('run-button').click();"
            )
            settled = None
            if first_done.wait(timeout=_EVENT_WAIT_TIMEOUT_SECONDS):
                first_output_directory = window.evaluate_js(
                    "window.fim.getCompletedOutputDirectory()"
                )
                # A fresh click reuses whatever the form already has --
                # no field needs re-setting, and no "New run"/reset step
                # comes first.
                window.evaluate_js("document.getElementById('run-button').click();")
                if second_done.wait(timeout=_EVENT_WAIT_TIMEOUT_SECONDS):
                    second_output_directory = window.evaluate_js(
                        "window.fim.getCompletedOutputDirectory()"
                    )
                    settled = {
                        "firstOutputDirectory": first_output_directory,
                        "secondOutputDirectory": second_output_directory,
                    }
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert settled is not None, (
        f"both batches did not complete within "
        f"{2 * _EVENT_WAIT_TIMEOUT_SECONDS}s combined"
    )
    assert settled["firstOutputDirectory"]
    assert settled["secondOutputDirectory"]
    assert settled["secondOutputDirectory"] != settled["firstOutputDirectory"]


def test_open_folder_button_reaches_the_injected_opener_and_settles() -> None:
    """ "Open output folder" reaches the injected opener and settles before teardown.

    The batch-results counterpart to `test_results_screen.py`'s own
    identically-named test — same shared `open-folder-button`/`window.
    __fimOpenFolderSettled` flag (one button now, regardless of scalar
    or batch, design §8 Phase E), same injected-`open_folder` hook (so a
    real Finder/Explorer window never opens here either), same real,
    once-reproduced hang this closes: a click handler calling `window.
    pywebview.api.open_output_folder(...)` with nothing downstream
    awaiting it. See `test_running_screen.py`'s own `_wait_for_cancel_
    run_settled` for the full mechanism, traced there via `sample <pid>`
    on a `git push`'s own hung pre-push `pytest` run.
    """
    opened: list[Path] = []
    done_event = threading.Event()

    def on_message(message: RunMessage | BatchMessage) -> None:
        if message[0] in ("done", "cancelled", "error"):
            done_event.set()

    window = create_window(
        api=Api(on_message=on_message, open_folder=opened.append), hidden=True
    )
    outcome: queue.Queue[Any] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _wait_for_input_screen_ready(window)
            window.evaluate_js(
                _SET_TINY_BATCH_FIELDS
                + "document.getElementById('run-button').click();"
            )
            settled = False
            if done_event.wait(timeout=_EVENT_WAIT_TIMEOUT_SECONDS):
                window.evaluate_js(
                    "document.getElementById('open-folder-button').click();"
                )
                for _ in range(_READY_POLL_ATTEMPTS):
                    settled = window.evaluate_js(
                        "window.__fimOpenFolderSettled === true"
                    )
                    if settled:
                        break
                    time.sleep(_READY_POLL_INTERVAL_SECONDS)
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert settled is True
    assert len(opened) == 1
