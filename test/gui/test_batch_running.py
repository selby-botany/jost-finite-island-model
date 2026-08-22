"""Headless functional proof that a real batch run reaches the bridge end to end
(design doc §0.5, §3.4, §7.6).

Milestone W5's backend half: `Api.start_run` dispatching to `fim.gui.
batch_runner.start_batch_run` and `fim.gui.app._drain_batch_messages`
draining its messages, against a *real* multi-process batch — not the
pure-function proofs `test/gui/test_app_api.py`'s own `_batch_done_
payload`/`_push_batch_progress` tests already give (those construct
`RunResult`s and sidecar files directly; this test proves the two are
actually wired together, which no Python-only test can check). The
frontend screens these pushes will eventually drive (the outer
replicate-count bar, Screen 4's own table/CI bars/pooled scatter) are
Milestone W5's remaining, separate piece — `webui/app.js`'s own
`onBatchProgress`/`onBatchDone`/`onBatchCancelled`/`onBatchError` are
still no-op stubs at this point (the same walking-skeleton precedent
Milestone W1 set for the scalar handlers), so this test observes the
pipeline through `Api`'s own `on_message` hook (design §3.4's "push,
not poll," extended to how a test observes a run — see `test/gui/
test_running_screen.py`'s own module docstring for why a DOM-polling
test-driving loop is the wrong tool here) rather than by reading DOM
state a screen that does not exist yet would have rendered.
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

# A small, fast, two-replicate batch — the same tiny-scale values `test/
# gui/test_running_screen.py`'s own `_SET_TINY_FIELDS` uses for a scalar
# run, plus `n_replicates` set to 2 (design §4.1's "there is no separate
# 'batch mode' toggle; `n_replicates` *is* the toggle") and a matching
# small `max_workers`.
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
    never a second concurrent `evaluate_js` caller — see `test/gui/
    test_running_screen.py`'s own module docstring for why that
    distinction matters.
    """
    for _ in range(_READY_POLL_ATTEMPTS):
        if window.evaluate_js(_INPUT_SCREEN_READY):
            return
        time.sleep(_READY_POLL_INTERVAL_SECONDS)
    raise AssertionError(
        f"input screen was not ready within "
        f"{_READY_POLL_ATTEMPTS * _READY_POLL_INTERVAL_SECONDS}s"
    )


def test_start_run_dispatches_a_real_batch_and_pushes_its_done_message() -> None:
    """`n_replicates: 2` reaches a real `ProcessPoolExecutor` batch, not a scalar run.

    Waits on `done_event` (`Api`'s `on_message` hook) rather than
    polling the DOM: `_drain_batch_messages` calls `window.evaluate_js`
    for `fim.onBatchDone` before ever calling `on_message` for that same
    message, so `done_event` firing is itself proof that call
    *succeeded* — a `JavascriptException` from a missing page-side
    handler would raise inside `_drain_batch_messages` first, and
    `on_message` (hence `done_event`) would never fire at all.
    """
    started_event = threading.Event()
    done_event = threading.Event()
    messages: list[RunMessage | BatchMessage] = []

    def on_run_started() -> None:
        started_event.set()

    def on_message(message: RunMessage | BatchMessage) -> None:
        messages.append(message)
        if message[0] in ("done", "cancelled", "error"):
            done_event.set()

    window = create_window(
        api=Api(on_run_started=on_run_started, on_message=on_message), hidden=True
    )
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
                settled = {"started": started_event.is_set()}
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert settled is not None, (
        f"done_event was never set within {_EVENT_WAIT_TIMEOUT_SECONDS}s "
        f"(messages received: {messages!r})"
    )
    assert settled["started"] is True
    assert messages[0][0] == "started"
    assert messages[-1][0] == "done"
    results = messages[-1][1]
    # A scalar "done" carries one `RunResult`; a batch's own carries a
    # tuple of them — `isinstance` (not just the `[0] == "done"` check
    # above) is what actually distinguishes the two for mypy here, the
    # same disambiguation `batch_runner._batch_worker`'s own `isinstance
    # (results, tuple)` guard uses for the identical union.
    assert isinstance(results, tuple)
    assert len(results) == 2
