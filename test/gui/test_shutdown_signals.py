"""Tests for `fim.gui.app`'s SIGTERM/SIGINT/SIGHUP shutdown handling.

Window-free and unmarked `gui`, like `test_shutdown_deadman.py`: none of
this needs a real pywebview window.

Before `_install_shutdown_signal_handlers` existed, none of these three
signals had any handler at all, so each one terminated the process
immediately -- no Python code ran, not even a `finally` block, and any
live `ProcessPoolExecutor` batch worker (`fim.engine._run_batch_parallel`)
was simply abandoned as an orphan (investigation recorded in
`20260928-claude-sonnet-5-shutdown-signal-handling-design.md`,
`selby/restricted`). The end-to-end test below sends a real `SIGTERM` to
a child interpreter and proves the process survives long enough to run
the handler, rather than dying on the spot the way it did before this
module existed -- the same "reproduce the real failure in a child
interpreter" discipline `test_shutdown_deadman.py`'s own wedged-process
test already uses, for the same reason: nothing here should risk the
signal reaching this test run's own process.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import textwrap
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from types import FrameType

import pytest
from conftest import COMPLETION_BACKSTOP_SECONDS, backstop_message

from fim.gui import app as app_module

_REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent


def _installed_handler(sig: signal.Signals) -> Callable[[int, FrameType | None], None]:
    """Return the real, callable handler `_install_shutdown_signal_handlers` set.

    `signal.getsignal`'s own return type also covers `SIG_DFL`/`SIG_IGN`/
    `None`, none of which this project's own handler ever is -- narrowing
    here once, rather than at every call site, is what makes calling the
    result directly type-check.
    """
    handler = signal.getsignal(sig)
    assert callable(handler)
    return handler


@pytest.fixture(autouse=True)
def _restore_signal_handlers() -> Iterator[None]:
    """Undo every `signal.signal()` call a test below makes.

    `signal.signal` is process-global state, not per-test: installing a
    real handler here and leaving it in place would leak into every other
    test sharing this `pytest-xdist` worker for the rest of the session,
    including ones that never import this module at all.
    """
    originals = {sig: signal.getsignal(sig) for sig in app_module._SHUTDOWN_SIGNALS}
    try:
        yield
    finally:
        for sig, handler in originals.items():
            signal.signal(sig, handler)


def test_shutdown_signals_excludes_sigkill() -> None:
    """`SIGKILL` cannot be caught, so it is never in the installed set."""
    assert signal.SIGKILL not in app_module._SHUTDOWN_SIGNALS


def test_shutdown_signals_includes_sigterm_and_sigint() -> None:
    """The two signals present on every platform this project ships to."""
    assert signal.SIGTERM in app_module._SHUTDOWN_SIGNALS
    assert signal.SIGINT in app_module._SHUTDOWN_SIGNALS


def test_install_shutdown_signal_handlers_replaces_every_default_handler(
    tmp_path: Path,
) -> None:
    """Every signal in `_SHUTDOWN_SIGNALS` gets a real, non-default handler."""
    api = app_module.Api(preferences_path=tmp_path / "preferences.json")

    app_module._install_shutdown_signal_handlers(api, timeout_seconds=3600)

    for sig in app_module._SHUTDOWN_SIGNALS:
        assert signal.getsignal(sig) not in (signal.SIG_DFL, signal.SIG_IGN)


def test_signal_handler_cancels_the_active_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Receiving the signal does what clicking Cancel already does.

    `Api._cancel_event` is the same `threading.Event` `start_run`/`_start_
    batch_run` hand to `fim.gui.runner`/`fim.gui.batch_runner`, and every
    live worker's `LiveProgressStore` already checks it (translated into
    `cancel_path`'s existence for a batch) before each generation's write
    -- setting it here is the one, already-trusted way to ask real,
    in-flight work to stop cooperatively.
    """
    monkeypatch.setattr(app_module, "_start_shutdown_deadman", lambda _timeout: None)
    monkeypatch.setattr(app_module, "_active_window", lambda: None)
    api = app_module.Api(preferences_path=tmp_path / "preferences.json")
    api._cancel_event = threading.Event()
    app_module._install_shutdown_signal_handlers(api, timeout_seconds=3600)

    _installed_handler(signal.SIGTERM)(signal.SIGTERM, None)

    assert api._cancel_event.is_set()


def test_signal_handler_arms_the_shutdown_deadman(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The deadman still guarantees a bounded exit even if cooperative
    cancellation never completes -- the signal path gets the identical
    backstop an ordinary window close already has.
    """
    armed: list[float] = []
    monkeypatch.setattr(app_module, "_start_shutdown_deadman", armed.append)
    monkeypatch.setattr(app_module, "_active_window", lambda: None)
    api = app_module.Api(preferences_path=tmp_path / "preferences.json")
    app_module._install_shutdown_signal_handlers(api, timeout_seconds=42.0)

    _installed_handler(signal.SIGINT)(signal.SIGINT, None)

    assert armed == [42.0]


def test_signal_handler_destroys_the_active_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The window is asked to close so `webview.start()` returns.

    Without this, `main`'s own blocking `webview.start()` call would
    never return on its own after a signal -- the deadman would still
    eventually force an exit, but only after its full timeout, rather
    than as soon as cancellation has had a real chance to finish.
    """
    monkeypatch.setattr(app_module, "_start_shutdown_deadman", lambda _timeout: None)
    destroyed = threading.Event()

    class FakeWindow:
        def destroy(self) -> None:
            destroyed.set()

    monkeypatch.setattr(app_module, "_active_window", FakeWindow)
    api = app_module.Api(preferences_path=tmp_path / "preferences.json")
    app_module._install_shutdown_signal_handlers(api, timeout_seconds=3600)

    _installed_handler(signal.SIGTERM)(signal.SIGTERM, None)

    assert destroyed.is_set()


def test_a_real_sigterm_is_handled_instead_of_killing_the_process(
    tmp_path: Path,
) -> None:
    """End to end, in a child interpreter: a real `SIGTERM` runs the handler.

    Before this handler existed, `os.kill(pid, SIGTERM)` against this
    exact program would have ended it on the spot -- the child would
    never reach its own later `print` calls at all. Reproduced in a
    child interpreter, not this test's own process, for the same reason
    `test_shutdown_deadman.py`'s wedged-process test uses one: nothing
    here should risk a real termination signal reaching the process
    running this suite.
    """
    preferences_path = tmp_path / "preferences.json"
    program = textwrap.dedent(
        f"""
        import os
        import signal
        import sys
        import threading
        from pathlib import Path

        sys.path.insert(0, "src")
        from fim.gui import app

        api = app.Api(preferences_path=Path({str(preferences_path)!r}))
        api._cancel_event = threading.Event()
        app._install_shutdown_signal_handlers(api, timeout_seconds=3600)
        print("ready", flush=True)
        os.kill(os.getpid(), signal.SIGTERM)
        print("survived SIGTERM", flush=True)
        print("cancel_event set:", api._cancel_event.is_set(), flush=True)
        """
    )
    # No timing budget: nothing here is about how long the child takes,
    # and most of that time is starting an interpreter and importing
    # `fim.gui.app` -- about 13 seconds on a loaded machine, against the
    # 30 this used to allow. Only the completion-signal backstop bounds
    # it, so a child that hangs fails instead of hanging this suite.
    try:
        completed = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(_REPOSITORY_ROOT),
            timeout=COMPLETION_BACKSTOP_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise AssertionError(backstop_message("SIGTERM child interpreter")) from error

    assert "ready" in completed.stdout
    assert "survived SIGTERM" in completed.stdout
    assert "cancel_event set: True" in completed.stdout
