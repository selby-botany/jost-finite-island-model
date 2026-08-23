"""Headless functional tests for `fim.gui.app` (design doc §6.4, §7.2).

The walking skeleton's own proof: the pywebview window builds, loads
`webui/index.html`, and the `Api` bridge — in-process (`ping`) and
cross-process (`ping_from_worker`) — round-trips correctly. Marked `gui`:
needs a real display (a real `WKWebView`/`WebView2`/WebKitGTK window),
exactly like the Tk-era suite needed a real Tk display.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
import webview

pytestmark = pytest.mark.gui


def test_create_window_loads_index_html(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """The window's own page is `webui/index.html`, not a blank default.

    `app.js`'s own automatic bootstrap (§7.2) should already have called
    `ping` and updated `#bridge-status` by the time this test's `read`
    first observes it settle away from "Connecting…" — no manually
    injected `trigger` needed, unlike the two tests below, which exercise
    the bridge directly rather than through the page's own script.
    """
    status = drive(
        window,
        trigger="null",
        read="document.getElementById('bridge-status').textContent",
        is_ready=lambda value: value != "Connecting…",
    )

    assert status == "Bridge connected (pong)."


def test_ping_round_trip(window: webview.Window, drive: Callable[..., Any]) -> None:
    """The basic JS-to-Python bridge call returns the real Python-side value."""
    result = drive(
        window,
        trigger=(
            "(async () => { "
            "window.__fimTestResult = await window.pywebview.api.ping(); "
            "})()"
        ),
        read="window.__fimTestResult",
    )

    assert result == "pong"


def test_set_significant_digits_round_trip(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """`set_significant_digits`/`get_significant_digits`, round-tripped
    through the bridge."""
    result = drive(
        window,
        trigger=(
            "(async () => { "
            "await window.pywebview.api.set_significant_digits(5); "
            "window.__fimTestResult = "
            "await window.pywebview.api.get_significant_digits(); "
            "})()"
        ),
        read="window.__fimTestResult",
    )

    assert result == 5


def test_menu_set_significant_digits_calls_the_bridge(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """`fim.menu.setSignificantDigits` (the View menu's dispatch target) reaches `Api`.

    The trigger wraps the whole thing in `setTimeout(..., 0)`, matching
    `fim.gui.app._build_menu`'s own real dispatcher exactly (not a test
    convenience) — see `test_input_screen.py`'s `test_menu_new_
    configuration_resets_an_edited_field` for why a bare `evaluate_js`
    call on an `async` `fim.menu.*` method deadlocks instead. The
    settled value is written to `window.__fimTestResult` rather than
    read back from a second `get_significant_digits()` call inside
    `read` itself — the same "trigger awaits and writes, read polls a
    plain value" split `test_ping_round_trip` above already
    establishes: `read` is re-evaluated synchronously on every poll,
    and a bare Promise-returning expression there would only ever see
    `{}` (this module's own docstring on `evaluate_js`).
    """
    result = drive(
        window,
        trigger=(
            "setTimeout(() => { (async () => { "
            "await window.fim.menu.setSignificantDigits(4); "
            "window.__fimTestResult = "
            "await window.pywebview.api.get_significant_digits(); "
            "})(); }, 0);"
        ),
        read="window.__fimTestResult",
    )

    assert result == 4


def test_ping_from_worker_round_trip(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """A trivial `ProcessPoolExecutor` call survives a real cross-process round trip.

    Direct regression test for the walking-skeleton's second proof
    (design §0.5, §7.2): `ProcessPoolExecutor` working at all from inside
    this exact pywebview-hosted process, checked before any real batch
    logic (`fim.gui.store.LiveProgressStore`, `fim.gui.batch_runner`,
    both already built and tested independently) is ever reached through
    it.
    """
    result = drive(
        window,
        trigger=(
            "(async () => { "
            "window.__fimTestResult = "
            "await window.pywebview.api.ping_from_worker(); "
            "})()"
        ),
        read="window.__fimTestResult",
    )

    assert result == "pong from worker"
