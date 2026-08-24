"""Headless functional tests for Screen 2, the running screen (design doc
§0.5, §4.2, §6.4).

Real DOM-driven proof that clicking "Run simulation" actually starts a
real background run (`fim.gui.runner.start_run`, unchanged from the
Tk-era build) and that the page's own `fim.onRunProgress`/`onRunDone`/
`onRunCancelled` handlers (`webui/screens/run-view-running.js`) react
correctly as messages arrive — `test/gui/test_app_api.py` and `test/gui/
test_runner.py` already prove the bridge and business logic
independently; these tests prove the two are actually wired together
correctly end to end, which no Python-only test can check.

Three real regressions were found and fixed while writing this file, all
instances of "same commit, different result" — the exact defect this
project's own testing discipline forbids re-running into a pass rather
than fixing. The first two were assertion bugs (see each test's own
docstring). The third took real, methodical root-causing to pin down
correctly, and is worth recording in full so it is not mis-diagnosed
again:

The visible symptom — a test hanging until its own poll budget expired,
`resultsScreenVisible`/`bannerText` never becoming what was expected —
first looked exactly like a `window.evaluate_js` reliability limit:
production's only caller of it is `fim.gui.app._drain_run_messages`, one
background thread, one call at a time (`grep -rn evaluate_js src/fim/`
confirms), while an earlier version of this file's own test-driving code
had *two* — its own poll loop, running concurrently with that
background thread. Raising the poll timeout (tried first, to 120 real
seconds) and slowing the poll cadence (tried second) each reduced how
often the symptom surfaced without eliminating it, and `fim.gui.app.Api`
grew two event-driven test hooks (`on_run_started`, `on_message` — see
`Api.__init__`'s own docstring) to remove the test's own poll loop from
the equation entirely, on the theory that removing the second concurrent
caller would remove the collision.

It did not fully explain the failures that remained. The actual cause,
found only once `on_message` gave a test a way to record whether
`Api.start_run` even ran at all: `fim.paths.default_output_directory()`
names its directory by the current wall-clock *second*
(`run-YYYYMMDD-HHMMSS`, unchanged from the CLI's own pre-existing
behavior — see `test/test_paths.py`'s own regression proof for that), so
two calls landing in the same second collide. Several of this project's
own `gui`-marked tests each start a real run in quick succession, and —
running in the same pytest process, sometimes only a fraction of a
second apart — occasionally did exactly that: `Api.start_run` correctly
and immediately returned `{"ok": False, "message": "output directory
already exists: ..."}`, `_drain_run_messages` was never even reached,
and every DOM-polling assertion this file had was simply waiting for a
push that could never come — indistinguishable, from a pure DOM-polling
vantage point, from a genuinely stuck background thread. Fixed at the
source: `Api.start_run` now resolves its output directory through
`fim.gui.app._resolve_available_output_directory`, which retries past a
same-second collision by waiting for the wall clock to cross into a new
second (`test/gui/test_app_api.py` covers that function directly and
fast, with `time.sleep` mocked out) — a real, if narrow, production
reliability gap this fixed for a live user too, not only for this test
suite's own rapid succession of runs.

The `on_run_started`/`on_message` hooks stayed even once the real cause
was found: waiting on a plain `threading.Event` a real background thread
sets, rather than polling `window.evaluate_js` for the same fact, is a
strictly better test-driving shape on its own merits (design §3.4's own
"push, not poll," extended to how a test observes a run) — it just was
not, on its own, the fix for this specific failure.
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

# Only used before any run starts (waiting for the input screen's own
# async initialization) -- no background thread exists yet at that
# point, so this loop is never a second concurrent `evaluate_js` caller
# the way a poll loop waiting on a *run's* progress would be.
_READY_POLL_INTERVAL_SECONDS = 0.05
_READY_POLL_ATTEMPTS = 200

_EVENT_WAIT_TIMEOUT_SECONDS = 30.0
_OUTCOME_TIMEOUT_SECONDS = 40.0

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"

# Mirrors `test/gui/test_results_screen.py`'s own `_SET_TINY_FIELDS`
# (itself mirroring `test/conftest.py`'s `tiny_params` fixture).
_SET_TINY_FIELDS = """
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
"""

# A real, previously-reproduced defect this constant exists to close:
# the starter form's own defaults (`d=20`) were assumed, in an earlier
# version of both tests below, to have "no realistic chance of
# finishing on its own" within a test's own timeframe -- false on a
# fast-enough or lightly-loaded machine, confirmed live by watching the
# starter-default run reach `"done"` in under two seconds. An
# impossibly tight `convergence_tolerance` was tried first and was
# *still* probabilistic, not a real fix: `fim.convergence.criteria.
# trailing_window_stable` compares two *half-window means*, not an
# absolute statistic value, so an extremely small but nonzero tolerance
# can still legitimately be satisfied if the two halves happen to
# average out close enough — confirmed live, failing 2 of 3 repeat
# runs even with `tolerance = 1e-12`. `trailing_window_stable` itself
# has one real, *structural* guarantee instead: `len(history) < window`
# always returns `False`, unconditionally, before that comparison is
# ever made. Setting `convergence_window` to the same value as `max_
# generations` (never rejected -- validation only rejects a window
# *greater* than `max_generations + 1`) means convergence can never
# even be evaluated, let alone satisfied, until the run reaches the
# generation cap itself -- forcing the full ~16-second-plus run
# `max_generations=10000` at this project's own measured per-generation
# cost implies, by construction, not by hoping a delta stays above
# whatever tolerance was chosen.
_SET_UNREACHABLE_CONVERGENCE = """
document.getElementById('field-convergence_window').value = '10000';
document.getElementById('field-convergence_window')
    .dispatchEvent(new Event('input', {bubbles: true}));
"""


def _wait_for_input_screen_ready(window: webview.Window) -> None:
    """Poll until Screen 1's own async initialization has finished.

    Safe to poll here (unlike waiting on a run's own progress): no
    background thread exists yet, so this loop is never a second
    concurrent `evaluate_js` caller.

    Raises:
        AssertionError: If the screen never becomes ready — surfaced
            loudly rather than silently falling through to fire a
            trigger against a form that never finished populating.
    """
    for _ in range(_READY_POLL_ATTEMPTS):
        if window.evaluate_js(_INPUT_SCREEN_READY):
            return
        time.sleep(_READY_POLL_INTERVAL_SECONDS)
    raise AssertionError(
        f"input screen was not ready within "
        f"{_READY_POLL_ATTEMPTS * _READY_POLL_INTERVAL_SECONDS}s"
    )


def _wait_for_cancel_run_settled(window: webview.Window) -> None:
    """Poll until `cancel_run()`'s own fire-and-forget bridge call has resolved.

    Real, previously-reproduced hazard, not a hypothetical one:
    `screens/run-view-controls.js`'s own `onCancelClicked` handler calls
    `window.pywebview.api.cancel_run()`, and `cancelButton.disabled`
    flips synchronously well before that call's own return value is
    delivered back to pywebview's own JS bridge — `_drain_run_messages`'s
    own, entirely separate `onRunCancelled` push (what `cancelled_event`
    above actually watches) settles first far more often than not, but
    nothing before this fix ordered the two, so a window destroyed the
    instant `cancelled_event` fired could still race `cancel_run()`'s own
    in-flight delivery, throwing on pywebview's own delivery thread and
    hanging the whole interpreter at shutdown (`test/gui/conftest.py`'s
    own module docstring records the identical shape from a different
    call site, `open-run.js`'s `refreshRecentRuns`).

    Safe to poll here for the same reason `_wait_for_input_screen_ready`
    is: `_drain_run_messages`'s background thread has already returned
    by the time `cancelled_event` is set (its own `"cancelled"` branch
    pushes `onRunCancelled` and returns immediately after), so this loop
    is never a second concurrent `evaluate_js` caller.

    Raises:
        AssertionError: If the flag never settles — surfaced loudly
            rather than silently reading stale DOM state.
    """
    for _ in range(_READY_POLL_ATTEMPTS):
        if window.evaluate_js("window.__fimCancelRunSettled === true"):
            return
        time.sleep(_READY_POLL_INTERVAL_SECONDS)
    raise AssertionError(
        f"cancel_run() did not settle within "
        f"{_READY_POLL_ATTEMPTS * _READY_POLL_INTERVAL_SECONDS}s"
    )


def test_run_button_starts_a_real_run_that_pushes_live_progress() -> None:
    """Clicking "Run simulation" with a valid form starts a real background run.

    Waits on `done_event` (set from `Api`'s `on_message` hook, the
    moment `_drain_run_messages` dispatches a terminal message) rather
    than polling the DOM for "Screen 3 is visible" — this module's own
    docstring records why a test-side poll loop is the wrong tool while
    a real background run is in flight. Once `done_event` fires,
    `_drain_run_messages`'s thread has already returned (the terminal
    message is the last thing it processes), so the one verification
    `evaluate_js` call below is never concurrent with anything.

    Checks `progress-generation-label`'s *text*, not `progress-
    generation`'s own numeric `value`: a second real regression, found
    writing this test. The very first reported generation is
    legitimately `0` (the persisted initial state, before any step),
    and every later generation can legitimately get throttled away
    inside the same ~50ms window (`fim.gui.runner.ProgressThrottle`),
    leaving `0` as the one real value a correct push ever wrote —
    `assert generationValue > 0` failed every time against a
    fast-converging run for exactly that reason. `progress-generation-
    label` starts empty in the markup and is set only by `onRunProgress`,
    so it stays a direct, generation-number-independent proof a push
    landed.
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
                _SET_TINY_FIELDS + "document.getElementById('run-button').click();"
            )
            settled = None
            if done_event.wait(timeout=_EVENT_WAIT_TIMEOUT_SECONDS):
                settled = window.evaluate_js(
                    "({"
                    "runViewState: window.fim.getRunViewState(), "
                    "generationLabel: "
                    "document.getElementById('progress-generation-label')"
                    ".textContent"
                    "})"
                )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert settled is not None, (
        f"done_event was never set within {_EVENT_WAIT_TIMEOUT_SECONDS}s "
        f"(start_run called: {started_event.is_set()}, "
        f"messages received: {messages!r})"
    )
    assert settled["runViewState"] == "completed"
    assert settled["generationLabel"] != ""


def test_cancel_button_stops_the_run_and_shows_the_cancelled_banner() -> None:
    """Clicking Cancel reaches the same real background run `Api.start_run` started.

    Proves the other half of the wiring `_drain_run_messages` handles —
    `Api.cancel_run` setting the real `threading.Event` `fim.gui.runner`'s
    worker thread checks, and `fim.onRunCancelled` (not `onRunDone`)
    landing on the page in response — the one path the "does a push
    arrive" test above never exercises, since that test never cancels.

    Waits on `started_event` (`Api`'s `on_run_started` hook — fired
    synchronously inside `start_run` itself, with no `evaluate_js` call
    involved at all) before clicking Cancel, rather than polling the DOM
    for "screen-progress is visible": `started_event` firing is the
    exact moment `_cancel_event` is guaranteed already assigned (`Api.
    start_run`'s own code sets it, then calls this hook, then starts the
    background thread — in that order), so it is both the earliest
    correct moment to click Cancel and one with no polling loop involved
    to collide with `_drain_run_messages`'s own `evaluate_js` calls.

    Deliberately uses the starter form's own (large) `d`/`N`, not
    `_SET_TINY_FIELDS`, plus `_SET_UNREACHABLE_CONVERGENCE` on top — not
    the starter defaults' own `convergence_tolerance` alone, which a
    real, once-reproduced regression showed can legitimately converge
    in well under two seconds on a fast-enough or lightly-loaded
    machine, racing past Cancel and never firing `cancelled_event` at
    all (that constant's own comment has the full story, including why
    an unreachable *tolerance* alone was tried and rejected too).
    `started_event` alone still decides when it is safe to cancel,
    regardless of population size — the widened `convergence_window` is
    what structurally guarantees there is still a run left to cancel by
    the time it does.

    Also waits on `_wait_for_cancel_run_settled` before reading final
    DOM state — `cancelled_event` alone proved this test's own two
    outcomes correctly, but this file's own click on `cancel-run-button`
    fires an un-awaited `cancel_run()` bridge call with no DOM effect of
    its own tying it to either signal; a real, once-reproduced hang (a
    `git push`'s own pre-push test run left "HUNG" well after printing
    "813 passed") traced via `sample <pid>` to this exact call still
    being in flight — on pywebview's own JS-delivery thread — when this
    test's own `window.destroy()` ran. See `_wait_for_cancel_run_
    settled`'s own docstring for the full mechanism.
    """
    started_event = threading.Event()
    cancelled_event = threading.Event()

    def on_run_started() -> None:
        started_event.set()

    def on_message(message: RunMessage | BatchMessage) -> None:
        if message[0] == "cancelled":
            cancelled_event.set()

    window = create_window(
        api=Api(on_run_started=on_run_started, on_message=on_message), hidden=True
    )
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _wait_for_input_screen_ready(window)
            window.evaluate_js(
                _SET_UNREACHABLE_CONVERGENCE
                + "document.getElementById('run-button').click();"
            )
            settled = None
            if started_event.wait(timeout=_EVENT_WAIT_TIMEOUT_SECONDS):
                # `started_event` fires from *inside* `Api.start_run`,
                # synchronously, before that call even returns to JS --
                # a real, confirmed-live race, not a hypothetical one:
                # `cancel-run-button` stays disabled (`onRunClicked`'s
                # own continuation, which enables it, only runs once
                # the *full* round trip resolves) at the exact instant
                # this event fires, every time, measured directly.
                # Clicking it that early is silently swallowed (a
                # disabled button fires no click listener), so `Api.
                # cancel_run()` is never called and `cancelled_event`
                # never fires either -- indistinguishable, from this
                # test's own vantage point, from a genuinely stuck run.
                # A short poll for the actual DOM state the click
                # depends on closes it: safe this early specifically
                # because `_drain_run_messages`'s own background thread
                # (the one whose concurrent `evaluate_js` calls this
                # file's own module docstring warns against racing) has
                # not even started yet at this point -- it is spawned
                # after `Api.start_run` already returns, later than
                # `on_run_started` fires.
                for _ in range(_READY_POLL_ATTEMPTS):
                    if window.evaluate_js(
                        "document.getElementById('cancel-run-button').disabled === "
                        "false"
                    ):
                        break
                    time.sleep(_READY_POLL_INTERVAL_SECONDS)
                window.evaluate_js(
                    "document.getElementById('cancel-run-button').click();"
                )
                if cancelled_event.wait(timeout=_EVENT_WAIT_TIMEOUT_SECONDS):
                    _wait_for_cancel_run_settled(window)
                    settled = window.evaluate_js(
                        "({"
                        "bannerText: "
                        "document.getElementById('run-banner').textContent, "
                        "cancelDisabled: "
                        "document.getElementById('cancel-run-button').disabled"
                        "})"
                    )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert settled is not None, (
        "started_event or cancelled_event was never set within "
        f"{_EVENT_WAIT_TIMEOUT_SECONDS}s each"
    )
    assert "cancelled" in settled["bannerText"]
    assert settled["cancelDisabled"] is True


def test_live_deme_pair_selector_shows_a_chosen_pair_during_a_real_run() -> None:
    """ "Show pair" swaps the live progress canvas mid-run to a different view.

    Same starter-`d`-plus-`_SET_UNREACHABLE_CONVERGENCE` setup as the
    Cancel test above (`d=20`, past `scatter.PAIRWISE_MAX_DEMES`, so the
    default live view is one Deme-1-vs-Deme-2 panel, unified-run-view
    design §3.6 — "Show pair" (Deme 1 vs Deme 3) should still look
    different from it) and the same reasoning for using it: that
    constant's own comment records a real, confirmed defect an earlier
    version of this test could have hit too (the starter form's own
    `convergence_tolerance` alone can converge in well under two
    seconds on a fast-enough machine) even though it was not the one
    that actually surfaced it. `started_event` alone still decides when
    it is safe to interact, Cancel ending the test rather than waiting
    the run out.

    Waits for real progress via `on_message`'s own `progress_count`,
    never by polling the DOM with `evaluate_js` while the background
    thread is still pushing — this file's own module docstring records
    at length why a concurrent DOM-polling loop during a live run is
    the wrong tool here; each `evaluate_js` call below is a single,
    one-off read or click, the same shape the Cancel test's own
    mid-run `cancel-run-button` click already establishes as safe.

    An earlier version of this test waited for a fixed count of
    progress messages after the click ("surely three ticks is enough
    margin for `Api.set_live_deme_pair`'s own bridge round trip to
    land") — a wall-clock guess, and it failed exactly the way this
    project's own house rule on non-deterministic tests warns a guess
    like that will: passing most runs, timing out once in three under
    real, if unremarkable, system load. Waiting on `api.get_live_deme_
    pair()` instead — the Python-side state a tick's own `live_
    deme_pair()` call reads before deciding whether to compute
    `pairPanel` — closes that gap for good: once it returns the
    selected pair, the very next progress message is *guaranteed*, by
    construction, to carry `pairPanel`.

    A second, subtler version of the same mistake surfaced later,
    diagnosed and fixed at its actual source rather than papered over
    here: `run-view-running.js`'s own `showingLiveDemePair` flag — the
    *client-side* switch `drawProgressPanels` reads to decide which of
    `panels`/`pairPanel` to draw — used to flip only after `onShowPair`
    finished *awaiting* the bridge call, a full JS-await-Python-JS round
    trip later than `self._live_deme_pair` itself (which becomes
    non-`None` the instant the Python side executes). A tick processed
    in that narrower gap already carried `pairPanel` but was still drawn
    as the overview, because the flag telling the client which one to
    draw had not caught up yet. Fixed at that same source: the flag
    now flips synchronously, before the bridge call is even awaited, so
    it is never later than the click. With that fixed, waiting for one
    more `progress_count` tick after the round trip lands and reading
    the canvas once is correct again — no retry loop of `evaluate_js`
    calls competing with the background thread's own concurrent pushes,
    exactly the shape this file's own module docstring already spent a
    real investigation establishing as the right one; an intermediate
    version of this test introduced such a loop instead, diagnosed the
    symptom (needs more margin) without finding this cause, and made
    the test slower without actually closing the gap.
    """
    started_event = threading.Event()
    cancelled_event = threading.Event()
    progress_count = 0
    progress_count_when_pair_landed: int | None = None

    def on_run_started() -> None:
        started_event.set()

    def on_message(message: RunMessage | BatchMessage) -> None:
        nonlocal progress_count
        if message[0] == "progress":
            progress_count += 1
        elif message[0] == "cancelled":
            cancelled_event.set()

    api = Api(on_run_started=on_run_started, on_message=on_message)
    window = create_window(api=api, hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        nonlocal progress_count_when_pair_landed
        try:
            _wait_for_input_screen_ready(window)
            window.evaluate_js(
                _SET_UNREACHABLE_CONVERGENCE
                + "document.getElementById('run-button').click();"
            )
            settled = None
            if started_event.wait(timeout=_EVENT_WAIT_TIMEOUT_SECONDS):
                for _ in range(_READY_POLL_ATTEMPTS):
                    if progress_count >= 1:
                        break
                    time.sleep(_READY_POLL_INTERVAL_SECONDS)
                overview_snapshot = window.evaluate_js(
                    "document.getElementById('run-canvas').toDataURL()"
                )
                selector_hidden = window.evaluate_js(
                    "document.getElementById('run-deme-pair-selector').hidden"
                )
                window.evaluate_js(
                    "document.getElementById('run-x-deme').value = '1';"
                    "document.getElementById('run-y-deme').value = '3';"
                    "document.getElementById("
                    "'run-show-pair-button').click();"
                )
                # Wait for the bridge round trip itself to land, not
                # for an arbitrary number of ticks — see this test's
                # own docstring for why.
                for _ in range(_READY_POLL_ATTEMPTS):
                    if api.get_live_deme_pair() is not None:
                        progress_count_when_pair_landed = progress_count
                        break
                    time.sleep(_READY_POLL_INTERVAL_SECONDS)
                if progress_count_when_pair_landed is not None:
                    for _ in range(_READY_POLL_ATTEMPTS):
                        if progress_count > progress_count_when_pair_landed:
                            break
                        time.sleep(_READY_POLL_INTERVAL_SECONDS)
                    pair_snapshot = window.evaluate_js(
                        "document.getElementById('run-canvas').toDataURL()"
                    )
                    settled = {
                        "selectorHidden": selector_hidden,
                        "pairDiffersFromOverview": (pair_snapshot != overview_snapshot),
                    }
                window.evaluate_js(
                    "document.getElementById('cancel-run-button').click();"
                )
                cancelled_event.wait(timeout=_EVENT_WAIT_TIMEOUT_SECONDS)
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert settled is not None, (
        "started_event, the first progress push, or set_live_deme_pair's own "
        "bridge round trip was never observed in time "
        f"(progress messages seen: {progress_count}, pair landed at: "
        f"{progress_count_when_pair_landed})"
    )
    assert settled["selectorHidden"] is False
    assert settled["pairDiffersFromOverview"] is True
