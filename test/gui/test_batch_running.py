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
from collections.abc import Callable
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


def _wait_for_progress_with_statistics(
    progress_queue: queue.Queue[dict[str, Any]], timeout: float
) -> dict[str, Any] | None:
    """Drain `progress_queue` until a tick with a non-empty `statistics`
    dict arrives, or `timeout` elapses overall.

    Fed by `Api(on_batch_progress=...)` (`app.py`'s own docstring on that
    hook), which is called *after* that tick's own `window.evaluate_js
    (fim.onBatchProgress(...))` push already completed — so by the time
    this function returns, the DOM has already been updated by that
    exact tick, and a caller's own single, immediately-following
    `evaluate_js` read needs no additional wait or margin of its own.
    This is the reason this hook exists at all rather than a Python-side
    poll of `.progress` sidecar files (an earlier version of this file
    did that, then slept a fixed margin and hoped the page had caught up
    — confirmed live, twice, not enough on a slower CI runner) or a
    DOM-polling loop of the test's own (`test/gui/test_running_screen.
    py`'s own module docstring: racing `_drain_batch_messages`'s own
    background `evaluate_js` calls is a real, previously diagnosed
    defect, not a theoretical one) — push, not poll, all the way through.

    The first few ticks legitimately have empty `statistics`
    (`_push_batch_progress`'s own docstring: needs two or more
    currently-reporting replicates before `reports_summary` defines any
    interval at all), so this drains past those rather than returning
    the first item unconditionally.
    """
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            payload = progress_queue.get(timeout=remaining)
        except queue.Empty:
            return None
        if payload.get("statistics"):
            return payload


def test_a_live_batch_shows_a_trajectory_panel_once_two_replicates_report() -> None:
    """The live trajectory panel (batch trajectory panel design `20260912-
    claude-sonnet-5-batch-trajectory-panel-design.md`, `selby/restricted`,
    commit 1) appears mid-batch, not only once it finishes.

    `_push_batch_progress` needs at least two currently-reporting
    replicates before `reports_summary` defines any interval at all
    (its own docstring) — waits on `Api`'s own `on_batch_progress` hook
    (`_wait_for_progress_with_statistics`) for the first tick whose own
    `statistics` dict is non-empty, rather than guessing a wall-clock
    delay is enough (this project's own house rule against a
    non-deterministic wait, `feedback_tests_are_functions_of_their_
    commit.md`) or polling the DOM concurrently with the background
    poll thread's own pushes (`_wait_for_progress_with_statistics`'s own
    docstring). An earlier version of this test polled `.progress`
    sidecar files for the same fact, then slept a fixed margin before
    reading the DOM once — confirmed live to be not always enough on a
    slower CI runner (two real CI failures, `test_a_live_batch_trajectory
    _legend_toggle_works_mid_run`'s own identical race hit the same run).
    `on_batch_progress` fires only after its own tick's `evaluate_js`
    push has already completed, so no such margin is needed at all here.

    Cancels the batch to end the test rather than waiting for it to
    converge (`convergence_window` is set unreachably high specifically
    so it does not, `_SET_UNREACHABLE_BATCH_CONVERGENCE`) -- the same
    "Cancel ends the test" precedent `test_running_screen.py`'s own
    Cancel-button test already established.
    """
    cancelled_event = threading.Event()
    progress_queue: queue.Queue[dict[str, Any]] = queue.Queue()

    def on_message(message: RunMessage | BatchMessage) -> None:
        if message[0] in ("done", "cancelled", "error"):
            cancelled_event.set()

    window = create_window(
        api=Api(on_message=on_message, on_batch_progress=progress_queue.put),
        hidden=True,
    )
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _wait_for_input_screen_ready(window)
            window.evaluate_js(
                _SET_TINY_BATCH_FIELDS
                + _SET_UNREACHABLE_BATCH_CONVERGENCE
                + "document.getElementById('run-button').click();"
            )
            settled = None
            if _wait_for_progress_with_statistics(
                progress_queue, _EVENT_WAIT_TIMEOUT_SECONDS
            ):
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

    assert settled is not None, "never saw a progress push with statistics in time"
    assert settled["frameHidden"] is False
    # All six report statistics, matching the scalar live trajectory's
    # own default (design §6.2: "all six report statistics... each
    # watched or not"), not only whichever is being watched for
    # convergence.
    assert settled["legendChildCount"] == 6


def test_a_live_batch_trajectory_legend_toggle_works_mid_run() -> None:
    """A legend click during a still-running batch re-renders, not crashes.

    Batch trajectory panel design `20260912-claude-sonnet-5-batch-
    trajectory-panel-design.md` (`selby/restricted`), commit 3: the
    live view's own accumulator (`run-view-running.js`'s own
    `liveBatchTrajectory`) now feeds the same `renderBatchTrajectory`/
    `buildBatchTrajectoryLegendItem` a completed batch's own trajectory
    already uses (`test/gui/test_batch_results_screen.py`'s own
    `test_a_completed_batchs_own_pooled_trajectory_renders` proves that
    machinery draws correctly in the completed case) -- this test's own
    job is narrower: prove the *live* wiring reaches it too, by
    actually clicking a legend item while the batch is still `running`
    and confirming its own `aria-pressed`/class flip without an
    unhandled exception breaking the next real progress push.

    Waits on `Api`'s own `on_batch_progress` hook throughout, the same
    "push, not poll" shape `test_a_live_batch_shows_a_trajectory_panel_
    once_two_replicates_report`'s own docstring explains in full — an
    earlier, `.progress`-sidecar-polling-then-fixed-sleep version of
    this test raised a real `JavascriptException` on CI (`querySelector`
    returning `null` — the legend had not rendered yet when the sleep
    ended), confirmed live rather than assumed to be a slower-CI-runner
    instance of the identical race the sibling test above hit in the
    same run.
    """
    cancelled_event = threading.Event()
    progress_queue: queue.Queue[dict[str, Any]] = queue.Queue()

    def on_message(message: RunMessage | BatchMessage) -> None:
        if message[0] in ("done", "cancelled", "error"):
            cancelled_event.set()

    window = create_window(
        api=Api(on_message=on_message, on_batch_progress=progress_queue.put),
        hidden=True,
    )
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _wait_for_input_screen_ready(window)
            window.evaluate_js(
                _SET_TINY_BATCH_FIELDS
                + _SET_UNREACHABLE_BATCH_CONVERGENCE
                + "document.getElementById('run-button').click();"
            )
            settled = None
            if _wait_for_progress_with_statistics(
                progress_queue, _EVENT_WAIT_TIMEOUT_SECONDS
            ):
                before = window.evaluate_js(
                    "document.querySelector('#run-trajectory-legend .legend-item')"
                    ".getAttribute('aria-pressed')"
                )
                window.evaluate_js(
                    "document.querySelector("
                    "'#run-trajectory-legend .legend-item').click();"
                )
                after_click = window.evaluate_js(
                    "({"
                    "ariaPressed: document.querySelector("
                    "'#run-trajectory-legend .legend-item')"
                    ".getAttribute('aria-pressed'), "
                    "className: document.querySelector("
                    "'#run-trajectory-legend .legend-item').className"
                    "})"
                )
                # A real, later progress tick must still land cleanly --
                # proof the toggle did not leave the accumulator/renderer
                # pair in a broken state a later `onBatchProgress` call
                # would throw inside. Any further tick at all proves
                # this, not only one with a non-empty `statistics` dict
                # (a replicate finishing early can legitimately make a
                # later tick's own `statistics` empty again) -- a plain
                # queue drain, not `_wait_for_progress_with_statistics`.
                still_running = None
                try:
                    progress_queue.get(timeout=_EVENT_WAIT_TIMEOUT_SECONDS)
                except queue.Empty:
                    pass
                else:
                    still_running = window.evaluate_js("window.fim.getRunViewState()")
                settled = {
                    "before": before,
                    "afterClick": after_click,
                    "stillRunning": still_running,
                }
            window.evaluate_js("document.getElementById('cancel-run-button').click();")
            cancelled_event.wait(timeout=_EVENT_WAIT_TIMEOUT_SECONDS)
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert settled is not None, "never saw a progress push with statistics in time"
    assert settled["before"] == "true"
    assert settled["afterClick"]["ariaPressed"] == "false"
    assert "legend-item-hidden" in settled["afterClick"]["className"]
    assert settled["stillRunning"] == "running"


def test_set_live_batch_trajectory_initial_point_updates_in_place(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Generation 0 is inserted once, then updated, never duplicated.

    Batch trajectory panel design `20260912-claude-sonnet-5-batch-
    trajectory-panel-design.md` (`selby/restricted`), follow-up: unlike
    `accumulateLiveBatchTrajectory`'s own always-append ticks,
    generation 0's own `sampleCount` only ever grows as more replicates
    start (a replicate's own generation-0 state never changes, so it
    stays counted even after that replicate moves on), so calling this
    again with a larger `sampleCount` must overwrite the existing
    generation-0 point in place, not append a second one alongside it.
    Called directly (`liveBatchTrajectory`, `setLiveBatchTrajectory
    InitialPoint`, and `STATISTIC_NAMES` are plain globals shared
    across every script this page loads, this file's own module
    docstring's "one shared canvas... sharing one global scope" already
    established) rather than through a real batch, since this is pure
    client-side accumulator logic with no bridge call of its own to
    exercise.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "setLiveBatchTrajectoryInitialPoint("
            "{D: {mean: '0', low: '-0.1', high: '0.1', sampleCount: 2}});"
            "setLiveBatchTrajectoryInitialPoint("
            "{D: {mean: '0.01', low: '-0.05', high: '0.06', sampleCount: 5}});"
        ),
        read=(
            "({length: liveBatchTrajectory.D.length, point: liveBatchTrajectory.D[0]})"
        ),
        is_ready=lambda value: value is not None,
    )

    assert settled["length"] == 1
    assert settled["point"]["generation"] == 0
    assert settled["point"]["mean"] == "0.01"
    assert settled["point"]["sampleCount"] == 5
