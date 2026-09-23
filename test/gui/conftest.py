r"""Shared fixtures for `fim.gui` headless functional tests.

(`doc/fim-gui-design.md` §4.)

Replaces the Tk-era `conftest.py` (session-scoped `tk.Tk()` root, a
disabled cyclic garbage collector to work around a Tkinter/threading
crash) entirely — none of that applies to pywebview. The driving pattern
here is `webview.start(callback)`: pywebview's own event loop starts,
`callback` then runs on a background thread once the window is shown,
drives the page via `window.evaluate_js(...)`, and calls
`window.destroy()` when done, letting `webview.start()` return — the same
"construct real widgets, drive them synchronously, never call the real
blocking entry point without a controlled exit" discipline the Tk-era
tests followed, adapted to pywebview's own API.

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

import os
import queue
import signal
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import webview

from fim import paths as paths_module
from fim.gui import app as app_module
from fim.gui import preferences as preferences_module
from fim.gui.app import await_bridge_threads, create_window
from fim.gui.preferences import GuiPreferences, save_preferences

_POLL_INTERVAL_SECONDS = 0.1
_IS_LINUX = sys.platform.startswith("linux")

# `window.destroy()` never terminates the `WebKitWebProcess`/
# `WebKitNetworkProcess` helper subprocesses WebKitGTK spawned for that
# window -- confirmed directly, not inferred: an Ubuntu-24.04 + Xvfb
# container built from this project's own CI dependency list (`.github/
# workflows/ci.yml`'s "Install pywebview's Linux GTK/WebKit runtime
# dependencies" step) and driven through this exact create-window/
# drive/destroy cycle in a tight loop leaked exactly two of these
# processes -- never fewer, never reclaimed -- on every single
# iteration, regardless of how long the GLib main loop was pumped
# afterwards (tried up to 1.5s of `Gtk.events_pending()` draining per
# iteration: no effect) or whether `private_mode` was left at
# pywebview's own default (`True`, a fresh ephemeral `WebContext` -- and
# so a fresh, never-reused process pool -- per window). This is what
# was actually killing CI: this package's `-m gui` step runs ~190 of
# these real-window tests sequentially in one long-lived process (no
# xdist, unlike every other step), so the leak accumulates without
# bound across the whole run. Reproduced the exact failure directly:
# system memory climbed from ~1.1GB to the container's entire ~11.9GB
# budget in lockstep with the leaked-process count (9 to 245), and the
# run was hard-killed once memory was exhausted -- the same shape as
# the CI job's own `exit code 143` (a signal-based kill, not any of
# this codebase's own `os._exit` calls, all of which were separately
# ruled out).
#
# The fix is *not* calling `WebKitWebView.terminate_web_process()`
# (crashed the process outright when tried, mid-destroy) or anything
# WebKitGTK-API-level. It is simpler and entirely outside WebKitGTK's
# own object model: snapshot this process's own `WebKit*`-named child
# PIDs before creating a window, diff against the same snapshot after
# it is destroyed, and `SIGTERM` plus `waitpid` any PIDs that appeared
# and are still this process's direct children -- confirmed, in the
# same container, to hold the leaked-process count perfectly flat
# (zero growth) across 60 consecutive create/destroy cycles, with zero
# lingering zombies. Linux-only (`sys.platform`-gated): this is a
# WebKitGTK-specific leak, and macOS/Windows pywebview backends (Cocoa/
# WKWebView, .NET/EdgeWebView2) do not spawn these processes at all, so
# `/proc` (Linux-only already) is never consulted there.
_LEAKED_WEBKIT_PROCESS_NAMES = frozenset(
    # Linux limits the comm field to 15 characters.
    {"WebKitWebProces", "WebKitNetworkPr", "WebKitStoragePro"}
)


def _webkit_process_pids() -> set[int]:
    """Return every live PID whose process name is a leaked WebKit helper.

    This intentionally does *not* filter by parent PID. An earlier
    version of this function did (matching only PIDs whose `ppid` was
    this test process's own), on the theory that only this process's
    own windows' helpers should ever be touched. That version held a
    60-iteration isolated create/destroy probe script's leaked-process
    count perfectly flat -- but made *no* difference at all when wired
    into this real test suite: every single reap call still measured
    zero leaked PIDs, and the full `-m gui` run still climbed to
    memory exhaustion exactly as before.

    Direct inspection of `ps -eo pid,ppid,comm` *during* a real (not
    probed-in-isolation) test explains why: WebKitGTK launches each
    helper process through `bubblewrap` (`bwrap`, present and used here
    -- `dpkg -l bubblewrap` confirms the package, and WebKitGTK's own
    process launcher uses it whenever it is available for sandboxing).
    `bwrap` double-forks, so the helper is reparented away from this
    test process to init (PID 1) within milliseconds of being spawned --
    long before this fixture's teardown-time snapshot runs. By the time
    the diff below executes, the leaked PID's `ppid` is already `1`, not
    this process's PID, so a `ppid`-filtered scan never sees it. (The
    isolated probe script that validated the parent-filtered version
    ran in an otherwise-idle container with no other WebKit-spawning
    process, and happened to poll fast enough, immediately after each
    `destroy()`, that this window did not matter there the same way.)

    Since this only ever runs in a single-tenant CI job or a dedicated
    local reproduction container -- nothing else on the machine spawns
    processes named `WebKitWebProcess`/`WebKitNetworkProcess`/
    `WebKitStorageProcess` -- matching purely on process name, with no
    `ppid` filter at all, is safe and is what actually catches the
    leaked (now-orphaned-to-init) helpers.

    Reads `/proc/<pid>/stat` directly rather than shelling out to `ps`/
    `pgrep` (this runs around every one of ~190 tests, so it needs to be
    cheap) -- `stat`'s second field is `comm`, truncated to Linux's
    15-character task-name limit and parenthesized. `rfind(")")` finds
    the true closing paren the way `man proc` documents doing.
    """
    pids: set[int] = set()
    proc = Path("/proc")
    try:
        entries = proc.iterdir()
    except OSError:
        return pids
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8")
        except OSError:
            continue
        close_paren = stat.rfind(")")
        if close_paren == -1:
            continue
        comm = stat[stat.index("(") + 1 : close_paren]
        if comm in _LEAKED_WEBKIT_PROCESS_NAMES:
            pids.add(int(entry.name))
    return pids


def _reap_leaked_webkit_processes(pids_before: set[int]) -> None:
    """`SIGTERM` any `WebKit*` helper processes new since `pids_before`.

    Args:
        pids_before: Every live `WebKit*` helper PID this function (or
            `_reap_leaked_webkit_helpers`) last snapshotted via
            `_webkit_process_pids`. Only PIDs that appear *after* that
            snapshot are touched, so a still-live window from an
            enclosing scope (not a pattern this suite actually uses,
            but not assumed away either) is never killed out from under
            it.

    Does not `waitpid` the reaped PIDs: by the time a leaked helper's
    PID is visible here it has already been reparented to init (see
    `_webkit_process_pids`'s own docstring), so it is no longer this
    process's child to wait on -- init reaps it once `SIGTERM` takes
    effect, same as it reaps any other orphan.
    """
    if not _IS_LINUX:
        return
    for pid in _webkit_process_pids() - pids_before:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue


# This suite's own bound for `await_bridge_threads` (`fim.gui.app`,
# moved there in full -- design doc `20260912-claude-sonnet-5-shutdown-
# bridge-thread-settle-design.md`, `selby/restricted`), passed
# explicitly below rather than left to that function's own default:
# production wants a short, UI-thread-safe bound (that module's own
# constant), while a test process is already willing to spend real
# wall-clock time on setup/teardown and benefits from a more generous
# one. Kept at this suite's own long-standing value so this move changes
# no test's own timing.
_BRIDGE_SETTLE_TIMEOUT_SECONDS = 10.0


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
    # Before destroying the window: let any bridge call still in flight
    # finish delivering. Destroying first is what strands pywebview's own
    # non-daemon `_call` thread and wedges interpreter shutdown. `built.
    # destroy()` itself now also settles first (`create_window`'s own
    # `events.closing` registration), but this explicit call stays: a
    # test's own `built.destroy()` call below reaches it either way, and
    # this suite's own, more generous timeout is worth keeping rather
    # than the shorter production default `create_window` uses.
    await_bridge_threads(timeout=_BRIDGE_SETTLE_TIMEOUT_SECONDS)
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
            # Same ordering rule as the `window` fixture's own teardown:
            # settle in-flight bridge calls before destroying the window,
            # or their non-daemon delivery threads outlive it.
            await_bridge_threads()
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


# Module-level, not per-test: `_reap_leaked_webkit_helpers`'s own
# teardown-time reap attempt cannot catch every leaked helper, because
# the underlying `bwrap`-launched process does not always finish
# spawning before that teardown runs. Confirmed directly: instrumenting
# the reap call with a print of its own before/after/leaked counts
# showed `leaked=0` on *every single test* of a real (not probed-in-
# isolation) run, yet the same run left dozens of new, permanently
# orphaned (`ppid=1`) `WebKit*` PIDs behind once it finished -- the
# helper process for a given test's window was still mid-launch at that
# test's own teardown, and only became visible sometime during the
# *next* test. Carrying this set across tests, and re-checking it once
# more at the following test's setup, gives each leaked helper a whole
# extra test's worth of time to finish appearing before it is reaped,
# which is enough in practice (a single test's own runtime dwarfs the
# helper-process launch delay that caused the miss).
_last_known_webkit_pids: set[int] = set()


@pytest.fixture(autouse=True)
def _reap_leaked_webkit_helpers() -> Iterator[None]:
    """Reap leaked `WebKit*` helper processes, package-wide, across two passes.

    Autouse and package-scoped (not folded into the `window` fixture's
    own teardown) because most tests here call `create_window()`
    directly rather than through that fixture -- confirmed by grepping
    every `create_window(` call site in this package: `test_running_
    screen.py`, `test_shutdown_deadman.py`, and others all build (and
    destroy) their own window straight in the test body, several with an
    `api=` this shared fixture does not support. Wrapping every test
    unconditionally, regardless of how many windows it builds or how it
    tears them down, is what actually closes the leak this fixture
    exists for; `_reap_leaked_webkit_processes`'s own docstring has the
    full root-cause story.

    Reaps both before and after the test body: the "before" pass is
    what actually catches most leaks (see `_last_known_webkit_pids`'s
    own comment on why the "after" pass alone is not enough), while the
    "after" pass still runs so a test that is itself slow leaves less
    time for its own leaked helper to sit around before the *next*
    test's "before" pass catches it.
    """
    if not _IS_LINUX:
        yield
        return
    _reap_leaked_webkit_processes(_last_known_webkit_pids)
    _last_known_webkit_pids.clear()
    _last_known_webkit_pids.update(_webkit_process_pids())
    yield
    _reap_leaked_webkit_processes(_last_known_webkit_pids)
    _last_known_webkit_pids.clear()
    _last_known_webkit_pids.update(_webkit_process_pids())


@pytest.fixture(autouse=True)
def _isolate_logging(log_isolation: None) -> None:
    """Opt every test in this package into `test/conftest.py`'s own `log_isolation`.

    `fim.gui.app.main` calls `fim.logging_setup.configure()` for real;
    nothing under `test/gui/` calls `main()` itself except a dedicated
    error-path test in `test_app_api.py` (every other test here drives
    `create_window()`/`webview.start()` directly, never `main()`), but
    this stays package-wide so that stays true for any future test too.
    """


@pytest.fixture(autouse=True)
def _isolate_path_overrides() -> Iterator[None]:
    """Restore every `fim.paths`/`fim.gui.preferences` override after each test.

    `fim.gui.app.main`'s own `--root`/`--results-directory`/`--log-
    directory`/`--preferences-file` handling (`20260918-claude-sonnet-5-
    configurable-storage-root-design.md`, `selby/restricted`) calls the
    matching `set_*_override` function directly, with no matching
    `set_*_override(None)` call of its own -- by design, meant to
    outlive that one call for the rest of a real process's own lifetime.
    A test that exercises one of these flags via a real `app_module.
    main([...])` call would otherwise leak it into every later test in
    the same worker process, in this package or (since `-n auto`
    schedules whole test *files*, not just this package, onto a shared
    worker) any other -- the identical hazard `test/cli/conftest.py`'s
    own sibling fixture already guards against, one entry point over.
    Deliberately does not also touch `_isolate_gui_results`'s own
    `project_root` patch, above -- that patch replaces `project_root`
    outright rather than setting `_root_override`, so it needs no
    matching restore here.
    """
    try:
        yield
    finally:
        paths_module.set_root_override(None)
        paths_module.set_results_directory_override(None)
        paths_module.set_log_directory_override(None)
        preferences_module.set_preferences_file_override(None)


@pytest.fixture(autouse=True)
def _isolate_gui_preferences(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    """Never let a bare `Api()`/`create_window()` touch a real preferences file.

    `Api.__init__`'s own `preferences_path` parameter — and `create_
    window`'s own default `Api()` — fall back to `preferences_file_
    path()`, the real, platform-specific location a packaged desktop
    build actually uses. The overwhelming majority of this package's
    tests construct `Api()`/`create_window()` with no `preferences_path`
    of their own; without this fixture, every one of them would read,
    and — since `set_significant_digits`/`start_run` persist changes —
    write, a real developer's own `~/Library/Application Support/fim/
    preferences.json` (or platform equivalent) on every test run. This
    redirects `fim.gui.app.preferences_file_path`'s default to a fresh,
    per-test `tmp_path` location instead, matching `test/gui/
    test_preferences.py`'s own injectable `home` parameter — the same
    "never touch a real home directory" discipline, applied here at the
    `Api`/`create_window` boundary rather than inside `preferences.py`
    itself.

    Returns the resolved path itself (autouse fixtures may still be
    requested by name for their return value): a test that wants a
    starting `GuiPreferences` other than this fixture's own default
    seed -- `test_welcome_screen.py`'s own `_build_window_with_welcome_
    not_dismissed`, for instance -- requests this fixture directly and
    overwrites that same path with `save_preferences`, rather than
    reaching into `fim.gui.app`'s own `preferences_file_path` attribute
    by hand (which `mypy --strict`'s `no_implicit_reexport` flags: `app.
    py` imports that name from `fim.gui.preferences` without explicitly
    re-exporting it, so accessing it as `app_module.preferences_file_
    path` from outside `fim.gui.app` is not a type-checked contract).
    """
    # Deliberately its own `tmp_path_factory`-minted directory, not a
    # child of this test's own `tmp_path` fixture: a large fraction of
    # this package's own tests construct `Api(preferences_path=tmp_path
    # / "preferences.json")` directly, reusing the very same per-test
    # `tmp_path` this fixture could also receive, to assert something
    # about a file that has *never* been written yet (`test_app_api.
    # py`'s own `test_set_significant_digits_rejecting_a_value_does_not_
    # persist`) or to assert `tmp_path`'s own directory listing holds
    # exactly one file (`test_preferences.py`'s own `test_save_is_
    # atomic_no_temp_file_left_behind`) -- both confirmed live to break
    # the moment an earlier version of this fixture pre-seeded anything
    # at all inside `tmp_path`, whether directly or in a subdirectory of
    # it, since the bare directory (or its listing) a test asserted was
    # untouched already held this fixture's own seed first. A directory
    # from a wholly separate `tmp_path_factory` mint means the pre-seed
    # below can never appear inside any test's own `tmp_path`-rooted
    # path or listing — only the *default* `preferences_file_path()`
    # (used by every `Api()`/`create_window()` call with no `preferences
    # _path` of its own) ever resolves here.
    preferences_path = tmp_path_factory.mktemp("gui-test-defaults") / "preferences.json"
    monkeypatch.setattr(app_module, "preferences_file_path", lambda: preferences_path)
    # Also pre-seed `welcome_dismissed=True`: `GuiPreferences`'s own
    # dataclass default is `False` (a genuine first launch has never
    # dismissed anything), which is exactly right for production but
    # would otherwise pop `screens/welcome.js`'s `<dialog id="modal-
    # welcome">` open, as a native `showModal()`, over every single
    # test in this package the moment `initializeRunView` settles --
    # confirmed live before this fixture was changed: a bare `create_
    # window()` with no preferences file left `document.activeElement`
    # unable to move onto an underlying field at all (`el.focus()`
    # silently declining to move focus, the same "inert background"
    # shape `test_field_help_screen.py`'s own history already
    # documents for a `hidden` ancestor -- a modal's own light-dismiss
    # inert-ing of the rest of the page is the identical hazard, not a
    # new one). The one test file that actually exercises the welcome
    # panel (`test_welcome_screen.py`) overrides this by writing its
    # own `GuiPreferences(welcome_dismissed=False)` before building its
    # window, the same explicit-override pattern `test_app_api.py`
    # already uses for `dark_mode_override`/`significant_digits` --
    # every other test in this package never has to know this dialog
    # exists.
    # `default_ploidy="1"`: the app makes the botanist choose a ploidy, so
    # without a default no test could submit a run. Haploid (individuals
    # equal gene copies) keeps every explicit `N` a test types meaning
    # exactly what it always did.
    save_preferences(
        preferences_path,
        GuiPreferences(welcome_dismissed=True, default_ploidy="1"),
    )
    return preferences_path


@pytest.fixture(autouse=True)
def _isolate_gui_results(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    """Never let a real run/Study/Experiment write into this checkout's own `results/`.

    `fim.paths.results_directory` — and everything derived from it:
    `studies_directory`/`experiments_directory`/`fim_index_directory`
    (`fim.persistence.groups`), `fim.gui.recent_runs.list_recent_runs`'s
    own no-argument default, and `Api.start_run`'s own `paths.default_
    output_directory()` — resolves through `fim.paths.project_root`,
    anchored on the real, installed `fim` package's own checkout root
    when nothing overrides it. Left unguarded, any test in this package
    that starts a real run (`Api.start_run`, or `fim.cli.main(["run",
    ...])` with no `-o`/`--output` of its own) writes a real, genuinely
    timestamped `run-YYYYMMDD-.../` directory straight into this
    repository's own real `results/` — confirmed directly: a full `test/
    gui/` suite run left dozens of stray run directories behind, every
    time, silently, since `results/` is gitignored and nothing else ever
    notices. `20260917-claude-sonnet-5-run-study-experiment-workflow-
    ergonomics.md` (`selby/restricted`) records the discovery.

    Patches `project_root` itself, not `results_directory` directly —
    the same choice `test/conftest.py`'s own `log_isolation` already
    made for `default_log_file`'s identical hazard, one derived function
    over, for the identical reason: patching the *root* rather than a
    function derived from it means a test that specifically exercises
    *derivation from `project_root`* keeps working completely unchanged.
    `test_recent_runs.py`'s own `test_recent_runs_defaults_to_paths_
    results_directory` patches `project_root` itself, later, inside its
    own test body, specifically to prove `list_recent_runs()`'s own
    no-argument default resolves all the way through the real chain —
    that call simply takes precedence over this fixture's own patch for
    its own duration, the same "a test's own explicit override always
    wins" precedent every `results_directory`-patching test elsewhere in
    this package (`test_open_run_screen.py`, `test_app_api.py`,
    `test_compare_screen.py`) already relies on for exactly this reason.
    Patching `results_directory` directly here instead would have
    silently disabled that one test's own real derivation-through-
    `project_root` logic, the identical failure `log_isolation`'s own
    docstring already records for a first, rejected attempt at that
    fixture.

    `fim.gui.app._webui_directory` (the actual GUI asset-serving path —
    `index.html`/`app.css`/every `webui/screens/*.js` a real window
    renders) resolves from `__file__` directly, never through `project_
    root` at all, so this never touches what a real window actually
    shows.
    """
    root = tmp_path_factory.mktemp("gui-test-results-root")
    monkeypatch.setattr(paths_module, "project_root", lambda: root)
    return root


@pytest.fixture
def fast_scalar_run_settings(_isolate_gui_preferences: Path) -> Path:
    """Pre-seed Settings' own defaults for one small, fast, scalar run.

    `n_replicates`/`max_generations`/the convergence-loop timing pair
    moved out of Configure's own `<form>` entirely and into the Settings
    dialog (`2026-09-16` revision, `config_form.DEFAULT_RUN_SETTING_
    FIELD_NAMES`) -- a test that wants one small, fast, scalar run (the
    overwhelming majority of this package's own "click Run and wait for
    completion" tests, previously driven by setting `field-n_replicates`
    etc. directly, the same DOM elements that no longer exist) can no
    longer force that by writing a Configure field. This is the
    replacement: the same "request `_isolate_gui_preferences` directly
    and overwrite that same path with `save_preferences`" override
    pattern that fixture's own docstring already documents for
    `test_welcome_screen.py`, applied here instead.

    Only the four fields a fast test actually needs differ from the
    starter defaults; `Api.get_default_run_settings`'s own overlay
    (`starter_form_values(overrides=...)`) fills every other `DEFAULT_
    RUN_SETTING_FIELD_NAMES` key in from the true starter values, the
    same partial-save tolerance a real Settings dialog save never
    actually exercises (it always submits the full set) but the
    underlying store has always been able to hold.

    Request this fixture *before* `window` (or any fixture that builds
    one) in a test's own parameter list -- pytest sets up same-scope
    fixtures in request order, and the override must land on disk
    before `Api()`/`create_window()` ever reads it.
    """
    save_preferences(
        _isolate_gui_preferences,
        GuiPreferences(
            welcome_dismissed=True,
            default_ploidy="1",
            default_run_settings={
                "n_replicates": "1",
                "max_generations": "10",
                "convergence_window": "4",
                "convergence_tolerance": "1.0",
            },
        ),
    )
    return _isolate_gui_preferences


@pytest.fixture
def unreachable_convergence_run_settings(_isolate_gui_preferences: Path) -> Path:
    """Pre-seed Settings so a fresh scalar run cannot converge before its cap.

    `test/gui/test_running_screen.py`'s own module docstring records the
    real, previously-reproduced defect this exists to close: a test that
    wants to observe a run *while it is still going* (not only once it
    finishes) needs a run slow enough to actually catch mid-flight, and
    "the starter form's own defaults happen to take a while" is not a
    real guarantee -- confirmed live, finishing in under two seconds on
    a fast enough machine. The actual fix has one real, *structural*
    guarantee instead: `fim.convergence.criteria.trailing_window_stable`
    always returns `False` while `len(history) < window`, unconditionally,
    before any statistic comparison is even made -- so setting
    `convergence_window` to the same value as `max_generations` (never
    rejected; validation only rejects a window *greater* than `max_
    generations + 1`) forces the full run out to the generation cap
    itself, by construction, not by hoping a delta stays above whatever
    tolerance was chosen. `max_generations` is deliberately left
    unoverridden here (stays at the true starter default, `10000`) so
    `convergence_window` matches it without repeating the value --
    `n_replicates` is pinned to `1` so this stays the one, real, scalar
    run every call site wants, not a batch.

    Both fields moved out of Configure's own `<form>` entirely and into
    the Settings dialog (`2026-09-16` revision) -- `field-convergence_
    window`/`field-n_replicates` no longer exist to set via a DOM
    trigger script, so this is `fast_scalar_run_settings`'s own sibling,
    with different values for a deliberately different purpose (a real,
    several-second run to observe mid-flight, not a fast one to finish
    quickly) -- request it the same way, before `window` (or any
    fixture that builds one) in a test's own parameter list.
    """
    save_preferences(
        _isolate_gui_preferences,
        GuiPreferences(
            welcome_dismissed=True,
            default_ploidy="1",
            default_run_settings={
                "n_replicates": "1",
                "convergence_window": "10000",
            },
        ),
    )
    return _isolate_gui_preferences


@pytest.fixture
def fast_batch_run_settings(_isolate_gui_preferences: Path) -> Path:
    """Pre-seed Settings for one small, fast, two-replicate batch run.

    `test/gui/test_batch_running.py`'s own sibling to `fast_scalar_run_
    settings`: the same tiny-scale values that fixture's own docstring
    explains, plus `n_replicates`/`max_workers` set for a small real
    batch (`n_replicates > 1` is the GUI's own scalar-vs-batch toggle,
    `fim.gui.app.Api.start_run`) instead of one scalar run.
    """
    save_preferences(
        _isolate_gui_preferences,
        GuiPreferences(
            welcome_dismissed=True,
            default_ploidy="1",
            default_run_settings={
                "n_replicates": "2",
                "max_generations": "10",
                "convergence_window": "4",
                "convergence_tolerance": "1.0",
                "max_workers": "2",
            },
        ),
    )
    return _isolate_gui_preferences


@pytest.fixture
def unreachable_batch_run_settings(_isolate_gui_preferences: Path) -> Path:
    """Pre-seed Settings for a small batch that cannot converge before its cap.

    `test/gui/test_batch_running.py`'s own sibling to `unreachable_
    convergence_run_settings`, for a small two-replicate batch instead
    of one scalar run -- a batch that never settles within test time
    keeps reporting real, growing progress until explicitly cancelled,
    needed by that file's own live-trajectory tests, which must observe
    at least one real tick with two or more replicates simultaneously
    reporting, not just the batch's own terminal "done"/"cancelled".
    Sets `max_generations` and `convergence_window` to the same large
    value, the identical "structural, not probabilistic" guarantee
    `unreachable_convergence_run_settings`'s own docstring explains.
    """
    save_preferences(
        _isolate_gui_preferences,
        GuiPreferences(
            welcome_dismissed=True,
            default_ploidy="1",
            default_run_settings={
                "n_replicates": "2",
                "max_generations": "10000",
                "convergence_window": "10000",
                "max_workers": "2",
            },
        ),
    )
    return _isolate_gui_preferences


@pytest.fixture
def staggered_batch_run_settings(_isolate_gui_preferences: Path) -> Path:
    """Pre-seed Settings for a 5-replicate batch with staggered stopping generations.

    `test/gui/test_batch_results_screen.py`'s own sibling to `fast_
    batch_run_settings`, for the specific `[3, 5, 6, 12, 15]`-generation
    staggered-stopping shape that file's own `_SET_STAGGERED_BATCH_
    FIELDS` comment explains -- needed so the completed batch trajectory
    panel's own pooled band actually exercises real, uneven replicate
    coverage across generations, not just the every-replicate-stops-
    together case `fast_batch_run_settings` happens to produce.
    """
    save_preferences(
        _isolate_gui_preferences,
        GuiPreferences(
            welcome_dismissed=True,
            default_ploidy="1",
            default_run_settings={
                "n_replicates": "5",
                "max_generations": "30",
                "convergence_window": "4",
                "convergence_tolerance": "0.02",
                "max_workers": "3",
            },
        ),
    )
    return _isolate_gui_preferences
