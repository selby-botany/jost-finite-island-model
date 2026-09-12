"""Headless functional proof that a real batch run reaches the bridge end to end
(`doc/fim-gui-design.md` §7.2).

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
pipeline through `Api`'s own `on_message` hook -- push,
not poll, extended to how a test observes a run — see `test/gui/
test_running_screen.py`'s own module docstring for why a DOM-polling
test-driving loop is the wrong tool here) rather than by reading DOM
state a screen that does not exist yet would have rendered.
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

# A small, fast, two-replicate batch — the same tiny-scale values `test/
# gui/test_running_screen.py`'s own `_SET_TINY_FIELDS` uses for a scalar
# run, plus `n_replicates` set to 2 -- there is no separate
# "batch mode" toggle; `n_replicates` *is* the toggle -- and a matching
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

# Same shape as `test_running_screen.py`'s own `_SET_UNREACHABLE_
# CONVERGENCE`, plus `max_generations` raised to match (that file's own
# comment on its identical pairing explains why both fields, not
# convergence_window alone, are needed: `trailing_window_stable`'s own
# structural `len(history) < window` guard means convergence cannot
# even be *evaluated* before the generation cap, so this run cannot
# finish before the cap either -- `_SET_TINY_BATCH_FIELDS`'s own
# `max_generations: '10'` would otherwise let a batch this small reach
# the cap, and its own `atomic_directory` staging directory get cleaned
# up, before this test's own poll for reporting replicates ever runs).
# A batch that never settles within test time keeps reporting real,
# growing progress until explicitly cancelled -- needed for the live-
# trajectory test below, which must observe at least one real tick with
# two or more replicates simultaneously reporting, not just the batch's
# own terminal "done"/"cancelled".
_SET_UNREACHABLE_BATCH_CONVERGENCE = """
document.getElementById('field-convergence_window').value = '10000';
document.getElementById('field-convergence_window')
    .dispatchEvent(new Event('input', {bubbles: true}));
document.getElementById('field-max_generations').value = '10000';
document.getElementById('field-max_generations')
    .dispatchEvent(new Event('input', {bubbles: true}));
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


def _count_replicate_progress_sidecars(working_directory: Path) -> int:
    """Count how many replicates have written their own `.progress` sidecar.

    Reads the filesystem directly, Python-side — never `evaluate_js` --
    so this can be polled in a loop concurrently with `_drain_batch_
    messages`'s own background poll thread without racing it the way a
    DOM-polling loop would (this file's own module docstring, and
    `test/gui/test_running_screen.py`'s identical reasoning for its own
    `on_message`/`api.get_live_deme_pair()` waits, both record why).
    Glob-based rather than reconstructing each replicate's own exact
    directory name (`batch_runner.replicate_output_directory` needs the
    batch's own deterministic `run_id`, not available to a test driving
    only the DOM) — `replicate-*` is that helper's own fixed naming
    scheme either way.
    """
    return len(list(working_directory.glob("replicate-*/.progress")))


def test_a_live_batch_shows_a_trajectory_panel_once_two_replicates_report() -> None:
    """The live trajectory panel (batch trajectory panel design `20260912-
    claude-sonnet-5-batch-trajectory-panel-design.md`, `selby/restricted`,
    commit 1) appears mid-batch, not only once it finishes.

    `_push_batch_progress` needs at least two currently-reporting
    replicates before `reports_summary` defines any interval at all
    (its own docstring) — waits on real `.progress` sidecar files
    reaching that count, Python-side, rather than guessing a wall-clock
    delay is enough (this project's own house rule against a
    non-deterministic wait, `feedback_tests_are_functions_of_their_
    commit.md`) or polling the DOM concurrently with the background
    poll thread's own pushes (`_count_replicate_progress_sidecars`'s
    own docstring). Once that count is reached, the *next* poll tick
    (at most `_BATCH_POLL_INTERVAL_SECONDS` later) is guaranteed, by
    construction, to push a non-empty `statistics` dict -- the same
    "wait on the real precondition, not a fixed message count" fix
    `test_live_deme_pair_selector_shows_a_chosen_pair_during_a_real_run`
    already applied for the analogous scalar-run race.

    Cancels the batch to end the test rather than waiting for it to
    converge (`convergence_window` is set unreachably high specifically
    so it does not, `_SET_UNREACHABLE_BATCH_CONVERGENCE`) -- the same
    "Cancel ends the test" precedent `test_running_screen.py`'s own
    Cancel-button test already established.
    """
    started_event = threading.Event()
    cancelled_event = threading.Event()
    working_directory_holder: list[Path] = []

    def on_message(message: RunMessage | BatchMessage) -> None:
        if message[0] == "started":
            started_event.set()
            working_directory_holder.append(message[1])
        if message[0] in ("done", "cancelled", "error"):
            cancelled_event.set()

    window = create_window(api=Api(on_message=on_message), hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _wait_for_input_screen_ready(window)
            window.evaluate_js(
                _SET_TINY_BATCH_FIELDS
                + _SET_UNREACHABLE_BATCH_CONVERGENCE
                + "document.getElementById('run-button').click();"
            )
            if not started_event.wait(timeout=_EVENT_WAIT_TIMEOUT_SECONDS):
                outcome.put(None)
                return
            working_directory = working_directory_holder[0]
            reported_enough = False
            for _ in range(_READY_POLL_ATTEMPTS):
                if _count_replicate_progress_sidecars(working_directory) >= 2:
                    reported_enough = True
                    break
                time.sleep(_READY_POLL_INTERVAL_SECONDS)
            settled = None
            if reported_enough:
                # One real `_BATCH_POLL_INTERVAL_SECONDS` tick (0.5s),
                # plus margin, guarantees the push this test is waiting
                # for has already reached the page.
                time.sleep(1.0)
                settled = window.evaluate_js(
                    "({"
                    "frameHidden: "
                    "document.getElementById('run-trajectory-frame').hidden, "
                    "legendChildCount: "
                    "document.getElementById('run-trajectory-legend').children"
                    ".length"
                    "})"
                )
            window.evaluate_js("document.getElementById('cancel-run-button').click();")
            cancelled_event.wait(timeout=_EVENT_WAIT_TIMEOUT_SECONDS)
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert settled is not None, "never saw two replicates report progress in time"
    assert settled["frameHidden"] is False
    # All six report statistics, matching the scalar live trajectory's
    # own default (design §6.2: "all six report statistics... each
    # watched or not"), not only whichever is being watched for
    # convergence.
    assert settled["legendChildCount"] == 6
