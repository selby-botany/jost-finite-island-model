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
