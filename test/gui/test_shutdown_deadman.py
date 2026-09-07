"""Tests for the GUI's own shutdown deadman timer (`fim.gui.app`).

Window-free and unmarked `gui`: none of this needs a real pywebview
window, since the deadman is armed *after* `webview.start()` has already
returned. The one test that needs a genuinely wedged process builds it in
a child interpreter instead, which is both faster and far more honest
than trying to provoke a real hang in-process.

Why this exists at all: a closed window that leaves the process running
is, to a user, an app that "won't quit" -- fixable only by Force Quit or
Task Manager. `fim.gui.app.create_window` already carries one fix for
that exact class (pywebview's own non-daemon HTTP handler threads) and
`test/gui/conftest.py` records several more, each an in-flight bridge
call or leftover thread wedging `Py_FinalizeEx`. The deadman is the
backstop for the next one, which is why its own correctness is worth
testing directly rather than trusting by inspection.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

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
