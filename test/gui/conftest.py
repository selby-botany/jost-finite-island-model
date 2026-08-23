r"""Shared fixtures for `fim.gui` headless functional tests (design doc
`20260821-claude-sonnet-5-graphical-interface.md` §6.1, §6.4).

Replaces the Tk-era `conftest.py` (session-scoped `tk.Tk()` root, a
disabled cyclic garbage collector to work around a Tkinter/threading
crash) entirely — none of that applies to pywebview. The driving pattern
here is `webview.start(callback)`: pywebview's own event loop starts,
`callback` then runs on a background thread once the window is shown,
drives the page via `window.evaluate_js(...)`, and calls
`window.destroy()` when done, letting `webview.start()` return — the same
"construct real widgets, drive them synchronously, never call the real
blocking entry point without a controlled exit" discipline the Tk-era
tests followed (§6.1), adapted to pywebview's own API.

`window.evaluate_js(...)` returns the raw value of whatever JS expression
it evaluates, never the resolved value of a Promise that expression
happens to produce (confirmed directly against a real window before
`fim.gui.app` was written — see that module's own docstring). Every test
in this package therefore drives the bridge through a small `async` JS
wrapper that `await`s the real `js_api` call and writes its result
somewhere read back with a second `evaluate_js` call — `drive_and_read`
below is the one place that pattern lives, so no individual test file
has to re-derive it.

`_POLL_INTERVAL_SECONDS` is deliberately not aggressive. `webview.
Window.evaluate_js`'s macOS implementation (`platforms/cocoa.py`)
schedules the real JS call onto the main run loop via `AppHelper.
callAfter` and blocks the calling thread on an un-timed `Semaphore.
acquire()` until a completion handler fires — safe for one caller, but
`test/gui/test_running_screen.py`'s own docstring records a real,
repeatedly-reproduced investigation into a hang traced to *this* poll
loop and a real background thread (`fim.gui.app._drain_run_messages`,
pushing `fim.onRunProgress`/`onRunDone` from its own thread as a run
proceeds) both calling `evaluate_js` on the same window at once — a
20ms poll cadence, hammering `AppHelper.callAfter` continuously for the
lifetime of a whole test, measurably raised the odds of landing on
whichever thread the collision hit. `poll_attempts` in each test
controls the real wall-clock ceiling this trades against; a slower
cadence here only costs time in a genuine failure path, not in the
common (fast, already-converged) case.

A second, distinct hazard, found investigating an intermittent
multi-minute stall in the *whole* `pytest test/gui/ -m gui` process
(not any one test): `sample`-ing the stalled process showed the main
thread parked in `Py_FinalizeEx -> wait_for_thread_shutdown`, all 17
`gui` tests already finished and passed (confirmed once by letting the
run finish naturally — `17 passed` did eventually print, ~29 real
minutes later) — CPython's own interpreter finalizer waiting on a
leftover OS thread that never signals it is done, not a stuck test.
Re-running with output written straight to a file (unbuffered, `python
-u`) rather than through the harness's own pipe-and-tail capture — the
earlier symptom's whole visible shape, "no output for a very long
time," was itself partly an artifact of libc's default block-buffering
of a non-tty stdout, sitting unflushed behind the same stuck
finalizer — surfaced the real trigger as a `PytestUnhandledThread
ExceptionWarning`: `webview.errors.JavascriptException` on
`pywebview`'s own JS-bridge delivery thread (`webview/util.py`'s
`js_bridge_call._call`), which calls back into a window's JS context
once a `js_api` method's Python-side call returns. `open-run.js`'s
`showOpenRunScreen` shows Screen 6 synchronously, then fires its own
`refreshRecentRuns()` (an async `list_recent_runs()` bridge call)
*without* awaiting it — deliberately, so a real filesystem scan never
blocks the screen transition. `test_open_run_screen.py`'s own
`test_open_a_run_button_reaches_screen_six` used to poll only for
screen visibility, which the `drive` fixture's own `window.destroy()`
then acted on immediately — tearing the window down while that
fire-and-forget call could still be in flight back to pywebview's own
bridge, on a thread with no way to know the window it was about to call
into was already gone. Fixed at the source, the same `window.__fim*
Ready`-flag pattern `test_input_screen.py` already established:
`open-run.js` now sets `window.__fimOpenRunRecentRunsLoaded` once
`refreshRecentRuns()` actually settles, and the test polls that
alongside screen visibility before returning control to `drive`'s own
teardown. `pyproject.toml`'s `filterwarnings` now also promotes
`PytestUnhandledThreadExceptionWarning` to a hard test failure
generally, so any future instance of this shape — a background thread
throwing after the window/page it depended on is gone — fails loudly
the moment it happens rather than only ever showing up as an
easy-to-miss warning line (or, worse, only as an unexplained stall).

That fix measurably reduced how often the stall reproduces (roughly
1-in-13 attempts afterward, versus a much shorter run of attempts
before it, in the same investigation), but a single post-fix stall was
observed once more and was not re-diagnosed to full certainty — it may
be a lower-frequency instance of the same class from a different
`gui` test's own fire-and-forget bridge call, or may have been
coincidental host contention from other tooling running at the same
time. Recorded here, honestly incomplete, rather than either claimed
fully fixed or left completely undocumented: if this resurfaces,
`sample <pid>` a stalled `pytest -m gui` process first (confirms
whether it is this same finalizer-wait shape at all) before assuming a
new cause, and prefer a real, unbuffered `python -u ... -v` run over
the harness's own piped-and-tailed capture when chasing it, since a
piped run's own buffering can look identical to a genuine hang.

2026-08-23: this exact shape (`Py_FinalizeEx -> wait_for_thread_
shutdown`) hung a real `git push`'s own pre-push `pytest -m 'not
statistical and not slow and not packaging'` run indefinitely — not
self-resolving, unlike every case above — traced via `sample <pid>` to
`screens/progress.js`'s own `cancelButton` handler: `window.pywebview.
api.cancel_run()`, fired without being awaited, with `cancelButton.
disabled = true` as its only DOM-visible effect, flipping synchronously
well before that call's own return value is delivered back to
pywebview's own JS bridge. `test_running_screen.py`'s own `test_cancel_
button_stops_the_run_and_shows_the_cancelled_banner` was not itself
waiting on this settling — it watches a *different* signal, the real
run thread's own separate `onRunCancelled` push — so it could (and,
that one time, did) destroy the window while `cancel_run()`'s own
delivery was still in flight. Fixed the same way as `refreshRecentRuns`
above: `progress.js` now sets `window.__fimCancelRunSettled` around an
`await`ed `cancel_run()` call, and the test polls it before reading
final state. The same audit found four more `window.pywebview.api.*`
calls sharing the identical un-awaited shape with no live-reproduced
test race (`results.js`/`batch-results.js`'s "Open output folder",
`help.js`'s external-doc link, `app.js`'s `openExternal` menu dispatch)
— all five now closed and each has its own regression test proving the
settle flag actually works, in `test_running_screen.py`,
`test_results_screen.py`, `test_batch_results_screen.py`,
`test_help_screen.py`, and `test_app.py` respectively. `grep -n
"window\.pywebview\.api\." src/fim/gui/webui/ --include='*.js'` is the
audit command that found all five call sites (filtering out the two
`bridgeMethod:` callback *definitions* in `results.js`/`batch-
results.js`, which are already awaited at their own real call site in
`app.js`'s `wireDemePairSelector`) — worth re-running against any new
screen before assuming this class is closed for good.

Re-running the full `gui` suite five times in a row immediately after
that fix (chasing confidence, not a fixed regression count) reproduced
a *related but distinct* delay once: the same `Py_FinalizeEx` shape,
but this time a live `sample` showed a different thread blocked in
`sock_recv_into -> readline` — reading from a socket, not from a JS
bridge delivery — and, unlike every case above, the process eventually
exited on its own within roughly a minute rather than hanging
indefinitely. `ProcessPoolExecutor` is the leading suspect (`engine.py`
and `app.py`'s own `ping_from_worker` both construct one, always via a
`with` block, so cleanup should be automatic) rather than a diagnosed
cause — not chased further, since a self-resolving delay is a
materially different, lower-severity problem than a true hang, and this
session's actual, reported failure (the hung `git push`) was already
traced to the `cancel_run()` race above, confirmed via the same live
`sample` technique before it was fixed. Recorded here for the same
reason the note above is: so the next person chasing a `gui`-suite
stall starts from what is already known instead of re-deriving it, and
so "read the docstring" does not have to depend on someone remembering
to during an actual incident — `sample <pid>` first, check whether the
blocked thread is a JS bridge delivery or a socket read, and only then
decide which of the two investigations above it continues.
"""

from __future__ import annotations

import queue
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
import webview

from fim.gui.app import create_window

_POLL_INTERVAL_SECONDS = 0.1


@pytest.fixture
def window() -> Iterator[webview.Window]:
    """Build one real pywebview window per test and guarantee it closes.

    Function-scoped, unlike the Tk-era `conftest.py`'s session-scoped
    root: pywebview does not share Tkinter's "repeated create/destroy
    within one process corrupts global interpreter state" problem (no
    crash of that shape was ever observed while building this fixture,
    across the same repeated-full-suite-run check the Tk-era comment
    describes doing), so each test gets a genuinely fresh window rather
    than reusing one across the whole session.
    """
    # `hidden=True`: a real window is still built and driven identically
    # (evaluate_js behaves the same either way), it just never becomes
    # visible on screen -- the same reason every `gui`-marked test in
    # this package passes it, not only under CI's own Xvfb: a real
    # window flashing open on a developer's own screen on every local
    # `git push` (the pre-push hook's own `pytest` run includes `gui`)
    # is a genuine nuisance this removes entirely, on every platform,
    # not only headless Linux.
    built = create_window(hidden=True)
    yield built
    if built in webview.windows:
        built.destroy()


def drive_and_read(
    target_window: webview.Window,
    trigger: str,
    read: str,
    *,
    ready: str | None = None,
    is_ready: Callable[[Any], bool] = lambda value: value not in (None, "", {}),
    poll_attempts: int = 250,
    timeout: float = 10.0,
) -> Any:
    """Fire `trigger`, poll `read` until it settles, and return its final value.

    `evaluate_js` does not block for an `async` JS expression's eventual
    result — confirmed directly against a real window before
    `fim.gui.app` was written (that module's own docstring): it returns
    almost immediately after `trigger` is handed to the page, whether or
    not anything `trigger` started has finished yet. The correct,
    confirmed-working shape is therefore two separate `evaluate_js`
    calls: one to fire `trigger` (typically calling an `async` JS
    function that awaits a real `js_api` call and writes its result into
    the DOM or a `window`-scoped variable), and a second, polled with a
    short sleep between attempts, to `read` that same location back once
    it has actually been written.

    Every `evaluate_js` call this helper makes evaluates a plain,
    synchronous expression — never an `async` one containing its own
    internal `await`/`setTimeout` wait loop. An early version of `ready`
    support tried exactly that (an async trigger IIFE polling a flag
    with `await new Promise((r) => setTimeout(r, ...))` internally) and
    the whole driver thread hung indefinitely: whatever pywebview's
    `evaluate_js` does internally to hand a synchronous return value back
    to Python appears to block the page's own JS event loop for the
    duration of that one call, so a `setTimeout` callback *inside* an
    in-flight `evaluate_js` call never gets a chance to fire — a
    deadlock, not a timeout, confirmed by a hang that outlasted every
    generous bound tried. `ready` is polled the same way `read` is
    instead: a bare boolean expression, evaluated once per iteration by a
    fresh `evaluate_js` call that returns immediately either way, with
    the actual waiting done in this Python loop, never inside the page's
    own JS.

    Args:
        target_window: The window to drive — normally the `window`
            fixture's own window, already built but not yet shown.
        trigger: A JS statement evaluated once, to start the real work
            (e.g. `"runValidate()"`, where `runValidate` is defined in
            the page's own script or injected by the caller first).
        read: A JS expression polled after `trigger`, until `is_ready`
            accepts its value.
        ready: An optional plain (non-`async`) JS boolean expression,
            polled *before* `trigger` fires, until truthy — for a
            `trigger` that must not run until the page's own async
            initialization has finished attaching its event listeners
            (see `test/gui/test_input_screen.py`'s `window.__fimInput
            ScreenReady` flag). `None` skips this wait entirely and
            fires `trigger` immediately, matching every test that does
            not need it.
        is_ready: Decides whether a polled `read` value is a real result
            worth returning, versus still-unset placeholder state.
            Defaults to "not `None`, not an empty string, not an empty
            object" — right for most DOM-text or plain-value reads;
            override for a call whose real result can legitimately be
            one of those (e.g. an empty string is itself meaningful).
        poll_attempts: How many times to re-evaluate `read` (and, if
            given, `ready`), each `_POLL_INTERVAL_SECONDS` apart, before
            giving up.
        timeout: Seconds to wait for `webview.start` itself to return
            after the drive callback finishes, before failing loudly
            rather than hanging the test session.

    Returns:
        `read`'s value once `is_ready` accepts it, or its last observed
        value if `poll_attempts` is exhausted first — the caller's own
        assertion is expected to fail clearly on a value that never
        became ready, rather than this helper raising an opaque timeout
        for what might be a legitimately slow but still-succeeding call.

    Raises:
        AssertionError: If `webview.start` did not return within
            `timeout` after the drive callback finished.
    """
    outcome: queue.Queue[Any] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            if ready is not None:
                for _ in range(poll_attempts):
                    if target_window.evaluate_js(ready):
                        break
                    time.sleep(_POLL_INTERVAL_SECONDS)
            target_window.evaluate_js(trigger)
            value: Any = None
            for _ in range(poll_attempts):
                value = target_window.evaluate_js(read)
                if is_ready(value):
                    break
                time.sleep(_POLL_INTERVAL_SECONDS)
            outcome.put(value)
        finally:
            target_window.destroy()

    webview.start(_drive)
    try:
        return outcome.get(timeout=timeout)
    except queue.Empty as error:
        raise AssertionError(
            f"webview.start's driver thread never returned a result within "
            f"{timeout}s for trigger={trigger!r}"
        ) from error


@pytest.fixture
def drive() -> Callable[..., Any]:
    """Bind `drive_and_read` as a fixture, for tests that prefer the fixture style."""
    return drive_and_read
