"""Tests for the GUI's own shutdown deadman timer (`fim.gui.app`).

Window-free and unmarked `gui`: none of this needs a real pywebview
window, since the deadman is armed *after* `webview.start()` has already
returned. The one test that needs a genuinely wedged process builds it in
a child interpreter instead, which is both faster and far more honest
than trying to provoke a real hang in-process.

Why this exists at all: a closed window that leaves the process running
is, to a user, an app that "won't quit" -- fixable only by Force Quit or
Task Manager. `fim.gui.app.create_window` already carries one fix for
that exact class (pywebview's own non-daemon HTTP handler threads);
`in_flight_bridge_threads`/`await_bridge_threads`, tested below, are the
structural one for in-flight bridge calls specifically -- moved here in
full from `test/gui/conftest.py`, where they lived before design doc
`20260912-claude-sonnet-5-shutdown-bridge-thread-settle-design.md`
(`selby/restricted`), so production code and the test suite can share
one implementation of the wait. The deadman is the backstop for the
next leftover thread that fix does not catch, which is why its own
correctness is worth testing directly rather than trusting by
inspection.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from fim import logging_setup
from fim.gui import app as app_module

_REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent


def test_shutdown_timeout_defaults_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no environment override, the built-in default applies."""
    monkeypatch.delenv("FIM_GUI_SHUTDOWN_TIMEOUT", raising=False)
    assert app_module.shutdown_timeout() == app_module._SHUTDOWN_DEADMAN_SECONDS


def test_shutdown_timeout_honors_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`FIM_GUI_SHUTDOWN_TIMEOUT` overrides the default.

    The documented escape hatch for a developer chasing a real shutdown
    hang, who needs the process to stay alive and inspectable rather than
    be terminated out from under them.
    """
    monkeypatch.setenv("FIM_GUI_SHUTDOWN_TIMEOUT", "45.5")
    assert app_module.shutdown_timeout() == 45.5


def test_shutdown_timeout_falls_back_on_a_malformed_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mistyped timeout falls back instead of raising.

    This is a safety net; crashing on exit because the safety net's own
    configuration was mistyped would be a worse failure than the hang it
    guards against, and would hit the user at the least recoverable
    possible moment.
    """
    monkeypatch.setenv("FIM_GUI_SHUTDOWN_TIMEOUT", "soon-ish")
    assert app_module.shutdown_timeout() == app_module._SHUTDOWN_DEADMAN_SECONDS


def test_a_zero_timeout_disables_the_deadman() -> None:
    """Zero starts no timer at all, leaving shutdown completely unbounded.

    Verified by observing that no deadman thread exists afterward, rather
    than by trusting the early return: the thread is the entire
    mechanism, so its absence is the only thing that actually proves the
    disable worked.
    """
    app_module._start_shutdown_deadman(0)
    assert not _deadman_threads()


def test_the_deadman_thread_is_a_daemon_so_it_cannot_itself_block_exit() -> None:
    """The guard must never become the thing it guards against.

    A non-daemon timer thread would be joined by
    `wait_for_thread_shutdown` and would delay every ordinary exit by the
    full timeout -- turning a safety net into a guaranteed, universal
    version of the exact bug it exists to prevent. A generous timeout is
    used so the timer is certain to still be waiting when it is
    inspected, and never fires during this test.
    """
    app_module._start_shutdown_deadman(3600)
    threads = _deadman_threads()
    assert len(threads) == 1
    assert threads[0].daemon is True


def _deadman_threads() -> list[threading.Thread]:
    """Return every live deadman timer thread.

    Args:
        None

    Returns:
        The matching threads, identified by the name
        `_start_shutdown_deadman` gives them.
    """
    return [
        thread
        for thread in threading.enumerate()
        if thread.name == "fim-shutdown-deadman" and thread.is_alive()
    ]


def test_a_wedged_shutdown_is_forced_to_exit() -> None:
    """End to end: a process that cannot finalize is terminated anyway.

    Reproduces the real user-visible failure exactly -- a non-daemon
    thread outliving the closed window, wedging `Py_FinalizeEx ->
    wait_for_thread_shutdown` -- in a child interpreter, and proves the
    deadman converts an app that "won't quit" into a bounded exit with a
    real explanation.

    Without the deadman this child runs forever, which is precisely what
    a user would experience. `subprocess`'s own generous `timeout` means
    a regression here fails as a timeout rather than hanging this suite
    in turn.
    """
    program = textwrap.dedent(
        """
        import sys, threading
        sys.path.insert(0, "src")
        from fim.gui import app

        # Exactly the real bug: a non-daemon thread that outlives the
        # closed window and never finishes.
        threading.Thread(target=threading.Event().wait).start()
        print("window closed", flush=True)
        app._start_shutdown_deadman(3)
        """
    )
    started = time.monotonic()
    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=str(_REPOSITORY_ROOT),
    )
    elapsed = time.monotonic() - started

    assert "window closed" in completed.stdout
    # The user (and the log) gets a real explanation, not a mute kill.
    assert "shutdown did not complete" in completed.stderr
    # The stacks needed to fix the underlying cause are captured at the
    # one moment the offending thread is still identifiable -- a forced
    # exit must not trade a diagnosable hang for a silent one.
    assert "Thread" in completed.stderr
    # A distinct status, so a forced exit is never mistaken for a clean
    # close in a log, a shell's `$?`, or a bug report.
    assert completed.returncode == app_module._SHUTDOWN_DEADMAN_EXIT_CODE
    # Bounded by the timeout, not merely "eventually" -- proving the
    # deadman ended it rather than the wedge resolving on its own.
    assert elapsed < 60


def _make_bridge_thread(release: threading.Event) -> threading.Thread:
    """Build a thread matching pywebview's own bridge-delivery shape.

    Reproduces the leak's exact shape rather than a stand-in: a
    *non-daemon* thread whose target's qualified name is
    `js_bridge_call.<locals>._call`, which is precisely what pywebview
    starts for every `window.pywebview.api.*` call and precisely what was
    named in the 2026-09-07 CI diagnostic dump.

    Args:
        release: Event the thread waits on, so the caller controls
            exactly when it finishes.

    Returns:
        An unstarted thread.
    """

    def js_bridge_call() -> Callable[[], None]:
        # Nested exactly as in `webview.util` so the qualified name the
        # matcher keys on is identical -- a flat function would be named
        # differently and would silently not be matched.
        def _call() -> None:
            release.wait()

        return _call

    return threading.Thread(target=js_bridge_call())


def test_in_flight_bridge_threads_identifies_a_bridge_thread() -> None:
    """A live bridge-delivery thread is recognized.

    Asserts against an explicit candidate list rather than live
    interpreter state: a GUI test scheduled earlier in the same `-n auto`
    worker can legitimately still hold a bridge thread, so testing against
    `threading.enumerate()` would make this depend on execution order
    instead of on the code under test.
    """
    release = threading.Event()
    blocker = _make_bridge_thread(release)
    blocker.start()
    try:
        assert app_module.in_flight_bridge_threads([blocker]) == [blocker]
    finally:
        release.set()
        blocker.join(timeout=5)


def test_in_flight_bridge_threads_ignores_unrelated_threads() -> None:
    """Only pywebview's own bridge threads are matched.

    A non-daemon thread belonging to something else must not make every
    GUI teardown pay the full timeout.
    """
    release = threading.Event()
    unrelated = threading.Thread(target=release.wait, name="fim-test-unrelated")
    unrelated.start()
    try:
        assert app_module.in_flight_bridge_threads([unrelated]) == []
    finally:
        release.set()
        unrelated.join(timeout=5)


def test_in_flight_bridge_threads_ignores_a_finished_bridge_thread() -> None:
    """A bridge thread that has already finished does not block shutdown.

    The distinction that makes the whole wait terminate: `Py_FinalizeEx`
    joins live non-daemon threads, so a completed one is irrelevant.
    """
    release = threading.Event()
    finished = _make_bridge_thread(release)
    finished.start()
    release.set()
    finished.join(timeout=5)

    assert app_module.in_flight_bridge_threads([finished]) == []


def test_await_bridge_threads_waits_while_a_bridge_call_is_in_flight() -> None:
    """The wait actually blocks while a bridge thread is alive.

    Proves the loop consumes its budget rather than returning at once, so
    a real in-flight delivery is given time to finish before a window is
    destroyed out from under it.
    """
    release = threading.Event()
    blocker = _make_bridge_thread(release)
    blocker.start()
    try:
        started = time.monotonic()
        app_module.await_bridge_threads(timeout=0.5)
        waited = time.monotonic() - started

        assert waited >= 0.4
        assert blocker.is_alive()
    finally:
        release.set()
        blocker.join(timeout=5)


def test_shutdown_dump_streams_always_includes_stderr() -> None:
    """stderr is a destination even when no log file handler exists.

    A terminal-launched `fim` must still get the dump, and the collector
    must never return an empty list -- a dump with nowhere to go is the
    silent forced exit this whole mechanism exists to avoid.
    """
    streams = app_module._shutdown_dump_streams()

    assert sys.stderr in streams


def test_shutdown_dump_streams_includes_open_log_file(tmp_path: Path) -> None:
    """An active log file is offered as a dump destination.

    This is the destination that matters: a GUI launched from Finder or a
    Start-menu shortcut has no console, so stderr goes nowhere and the log
    file is the only place a traceback can survive to reach a maintainer.
    """
    log_file = tmp_path / "fim.log"
    logging_setup.configure("debug", {"file": str(log_file)})
    try:
        streams = app_module._shutdown_dump_streams()

        assert any(getattr(stream, "name", None) == str(log_file) for stream in streams)
    finally:
        # Restore the suite's own logging rather than leaving every later
        # test writing into a tmp_path that is about to be deleted.
        logging_setup.configure("warning", {"file": "none"})


def test_shutdown_dump_streams_survives_unresolvable_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure to enumerate log handlers degrades to stderr, not a raise.

    This runs when shutdown has already failed. A diagnostic helper that
    raised here would leave the process wedged in exactly the hang the
    caller's next line exists to end, so failing soft is the required
    behavior rather than a nicety.
    """

    def explode() -> list[object]:
        raise RuntimeError("handlers unavailable")

    monkeypatch.setattr(logging_setup, "log_file_streams", explode)

    streams = app_module._shutdown_dump_streams()

    assert streams == [sys.stderr]


def test_forced_exit_writes_traceback_to_log_file(tmp_path: Path) -> None:
    """A real wedged child leaves its thread dump in the log file.

    The end-to-end proof of the windowed-launch case: stderr is
    deliberately discarded here, exactly as it is for a GUI started from a
    dock icon or shortcut, so anything asserted below reached the log file
    on its own merits. Without this, a recurrence would be recorded as
    "it hung" with no way to identify the thread responsible.
    """
    log_file = tmp_path / "fim.log"
    program = textwrap.dedent(
        f"""
        import sys, threading
        sys.path.insert(0, "src")
        from fim import logging_setup
        from fim.gui import app

        logging_setup.configure("debug", {{"file": {str(log_file)!r}}})
        threading.Thread(target=threading.Event().wait).start()
        app._start_shutdown_deadman(3)
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        stdout=subprocess.DEVNULL,
        # Discarded on purpose: proves the log file stands alone.
        stderr=subprocess.DEVNULL,
        timeout=120,
        check=False,
        cwd=str(_REPOSITORY_ROOT),
    )

    assert completed.returncode == app_module._SHUTDOWN_DEADMAN_EXIT_CODE
    contents = log_file.read_text(encoding="utf-8")
    # The log records that it fired ...
    assert "shutdown deadman fired" in contents
    # ... and, crucially, why -- the stacks that name the offending thread.
    assert "Thread" in contents
