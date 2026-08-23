"""Headless functional tests for Screen 4, the batch results screen (design
doc §4.4, §7.6).

Real DOM-driven proof that a completed batch actually reaches
`fim.showBatchResults` (`webui/screens/batch-results.js`) and renders the
batch's own pooled scatter, confidence-interval bars, and per-replicate
table — `test/gui/test_app_api.py`'s own `_batch_done_payload` tests
already prove the payload's own content is correct as plain Python calls;
`test/gui/test_batch_running.py` already proves the bridge dispatches a
real batch and pushes its `"done"` message correctly. This file proves
the third link: that the page's own JavaScript, given that real payload,
renders it — which no Python-only test can check.

Drives a real, small (two-replicate) batch through the actual UI, the
same tiny-scale `_SET_TINY_BATCH_FIELDS` `test/gui/test_batch_running.py`
uses, and waits on `Api`'s own `on_message` hook rather than polling the
DOM for "Screen 4 is visible" — see `test/gui/test_running_screen.py`'s
own module docstring for why a DOM-polling test-driving loop, run
concurrently with a real background thread's own `evaluate_js` pushes,
is the wrong tool here. Once `done_event` fires, `_drain_batch_messages`'s
thread has already returned (the terminal message is the last thing it
processes), so the one verification `evaluate_js` call each test below
makes is never concurrent with anything.
"""

from __future__ import annotations

import queue
import threading
import time
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

_INPUT_SCREEN_READY = "window.__fimInputScreenReady === true"

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
    """Poll until Screen 1's own async initialization has finished.

    Safe to poll here: no background thread exists yet, so this loop is
    never a second concurrent `evaluate_js` caller.
    """
    for _ in range(_READY_POLL_ATTEMPTS):
        if window.evaluate_js(_INPUT_SCREEN_READY):
            return
        time.sleep(_READY_POLL_INTERVAL_SECONDS)
    raise AssertionError(
        f"input screen was not ready within "
        f"{_READY_POLL_ATTEMPTS * _READY_POLL_INTERVAL_SECONDS}s"
    )


def test_a_completed_batch_renders_the_batch_results_screen() -> None:
    """A finished two-replicate batch shows a run id, two table rows, and six CI bars.

    Every one of the six named statistics gets a confidence-interval
    bar (`buildCiBar`/`buildOmittedCiBar` — design §4.4's "a statistic
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
                    "screenVisible: "
                    "!document.getElementById('screen-batch-results').hidden, "
                    "runId: "
                    "document.getElementById('batch-results-run-id').textContent, "
                    "rowCount: "
                    "document.getElementById('batch-results-table-body')"
                    ".children.length, "
                    "ciBarCount: "
                    "document.getElementById('batch-results-summary')"
                    ".querySelectorAll('.ci-bar').length"
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
    assert settled["screenVisible"] is True
    assert settled["runId"].startswith("run-")
    assert settled["rowCount"] == 2
    assert settled["ciBarCount"] == 6


def test_batch_deme_pair_selector_switches_to_a_chosen_pair_and_back() -> None:
    """ "Show pair"/"Show overview" round-trip through the real batch bridge and back.

    The batch counterpart to `test_results_screen.py`'s own identically
    named scalar-run test — `d=3` past the overview's default "Deme 1
    vs Deme 2" pairwise panel, "Show pair" (Deme 1 vs Deme 3) redraws
    the canvas via a real `Api.get_batch_deme_pair_panel` call (pooled
    across both replicates, `deme_pair_panel`'s own docstring), and
    "Show overview" redraws it back to the exact original panel with
    no further bridge call.
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
                    "'batch-results-deme-pair-selector').hidden, "
                    "optionCount: document.getElementById("
                    "'batch-results-x-deme').options.length"
                    "})"
                )
                overview_snapshot = window.evaluate_js(
                    "document.getElementById('batch-results-canvas').toDataURL()"
                )
                window.evaluate_js(
                    "document.getElementById('batch-results-x-deme').value = '1';"
                    "document.getElementById('batch-results-y-deme').value = '3';"
                    "document.getElementById("
                    "'batch-results-show-pair-button').click();"
                )
                pair_snapshot = _poll_until(
                    "document.getElementById('batch-results-canvas').toDataURL()",
                    lambda value: value != overview_snapshot,
                )
                window.evaluate_js(
                    "document.getElementById("
                    "'batch-results-show-overview-button').click();"
                )
                reverted_snapshot = _poll_until(
                    "document.getElementById('batch-results-canvas').toDataURL()",
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


def test_batch_new_run_button_switches_back_to_the_input_screen() -> None:
    """ "New run" returns to Screen 1 without needing another bridge call.

    Drives the window directly (not via the `drive` fixture): this test
    needs two sequential trigger-then-poll stages against the *same*
    live window (finish a batch, only then click "New run") —
    `conftest.py`'s `drive_and_read` destroys the window in its own
    `finally` block after one such stage.
    """
    done_event = threading.Event()

    def on_message(message: RunMessage | BatchMessage) -> None:
        if message[0] in ("done", "cancelled", "error"):
            done_event.set()

    window = create_window(api=Api(on_message=on_message), hidden=True)
    outcome: queue.Queue[bool] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _wait_for_input_screen_ready(window)
            window.evaluate_js(
                _SET_TINY_BATCH_FIELDS
                + "document.getElementById('run-button').click();"
            )
            switched = False
            if done_event.wait(timeout=_EVENT_WAIT_TIMEOUT_SECONDS):
                window.evaluate_js(
                    "document.getElementById('batch-new-run-button').click();"
                )
                switched = window.evaluate_js(
                    "!document.getElementById('screen-input').hidden"
                )
            outcome.put(switched)
        finally:
            window.destroy()

    webview.start(_drive)
    switched = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert switched is True
