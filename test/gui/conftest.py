"""Shared fixtures for `fim.gui` headless functional tests (design doc
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
"""

from __future__ import annotations

import queue
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
import webview

from fim.gui.app import create_window

_POLL_INTERVAL_SECONDS = 0.02


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
    built = create_window()
    yield built
    if built in webview.windows:
        built.destroy()


def drive_and_read(
    target_window: webview.Window,
    trigger: str,
    read: str,
    *,
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

    Args:
        target_window: The window to drive — normally the `window`
            fixture's own window, already built but not yet shown.
        trigger: A JS statement evaluated once, to start the real work
            (e.g. `"runValidate()"`, where `runValidate` is defined in
            the page's own script or injected by the caller first).
        read: A JS expression polled after `trigger`, until `is_ready`
            accepts its value.
        is_ready: Decides whether a polled `read` value is a real result
            worth returning, versus still-unset placeholder state.
            Defaults to "not `None`, not an empty string, not an empty
            object" — right for most DOM-text or plain-value reads;
            override for a call whose real result can legitimately be
            one of those (e.g. an empty string is itself meaningful).
        poll_attempts: How many times to re-evaluate `read`, each
            `_POLL_INTERVAL_SECONDS` apart, before giving up.
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
