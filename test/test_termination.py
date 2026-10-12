"""Tests for `fim.termination`: SIGTERM policies for command-line runs.

Each test sends SIGTERM to its own process from the main thread, so the
handler runs at a known point; nothing depends on timing.
"""

from __future__ import annotations

import os
import signal
import sys
import threading

import pytest

from fim.termination import (
    TERMINATED_EXIT_STATUS,
    sigterm_raises_exit,
    sigterm_requests_stop,
)

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="SIGTERM cannot be delivered to self here"
)


def test_the_exit_status_is_what_a_shell_reports() -> None:
    """128 plus the signal number, as for a process SIGTERM ended."""
    assert TERMINATED_EXIT_STATUS == 143


def test_sigterm_requests_a_stop_once() -> None:
    """The first SIGTERM only sets the flag; the default action returns."""
    previous = signal.getsignal(signal.SIGTERM)
    with sigterm_requests_stop() as requested:
        assert not requested()
        os.kill(os.getpid(), signal.SIGTERM)
        assert requested()
        assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
    assert signal.getsignal(signal.SIGTERM) is previous


def test_sigterm_raises_exit_with_the_terminated_status() -> None:
    """SIGTERM unwinds as `SystemExit(143)`, so cleanup runs."""
    previous = signal.getsignal(signal.SIGTERM)
    with pytest.raises(SystemExit) as raised, sigterm_raises_exit():
        os.kill(os.getpid(), signal.SIGTERM)
    assert raised.value.code == TERMINATED_EXIT_STATUS
    assert signal.getsignal(signal.SIGTERM) is previous


def test_nothing_is_installed_outside_the_main_thread() -> None:
    """A worker thread cannot install handlers; the policies do nothing."""
    previous = signal.getsignal(signal.SIGTERM)
    seen: list[object] = []

    def work() -> None:
        with sigterm_requests_stop() as requested:
            seen.append(requested())
        with sigterm_raises_exit():
            seen.append(signal.getsignal(signal.SIGTERM))

    thread = threading.Thread(target=work)
    thread.start()
    thread.join()
    assert seen == [False, previous]
