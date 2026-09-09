"""Headless functional tests for the Configure workspace's dark-mode
override field (botanist GUI design doc `20260907-claude-sonnet-5-
botanist-gui-redesign.md` §11.2, §12).

Real DOM-driven proof that `webui/screens/config-modals.js`'s
`wireDarkModeOverrideField`/`applyDarkModeOverride` actually apply and
persist a choice -- `test/gui/test_app_api.py` already proves the bridge
methods themselves are correct as plain Python calls; these tests prove
the page's own JavaScript calls them at the right moments and updates
`document.documentElement`'s own `data-theme` attribute, which no
Python-only test can check.
"""

from __future__ import annotations

import queue
import time
from collections.abc import Callable
from typing import Any

import pytest
import webview

pytestmark = pytest.mark.gui

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"
_POLL_ATTEMPTS = 200
_POLL_INTERVAL_SECONDS = 0.1


def test_starts_with_no_theme_override_and_the_select_showing_follow_system(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """A fresh launch (no saved preference) follows the OS -- no override applied."""
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger="null",
        read=(
            "({"
            "themeAttr: document.documentElement.dataset.theme || null, "
            "selectValue: document.getElementById('field-dark_mode_override').value"
            "})"
        ),
        is_ready=lambda value: value is not None,
    )

    assert settled["themeAttr"] is None
    assert settled["selectValue"] == ""


def test_choosing_dark_applies_the_theme_attribute_immediately(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Selecting "Dark" sets `data-theme="dark"` on the page right away."""
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "(function(){"
            "var select = document.getElementById('field-dark_mode_override');"
            "select.value = 'dark';"
            "select.dispatchEvent(new Event('change'));"
            "})();"
        ),
        read="document.documentElement.dataset.theme || null",
        is_ready=lambda value: value == "dark",
    )

    assert settled == "dark"


def test_choosing_dark_then_follow_system_clears_the_theme_attribute(
    window: webview.Window,
) -> None:
    """Returning to "Follow system" removes the override entirely, not just visually.

    Driven manually (not the `drive` fixture, which destroys its window
    after one round trip): the second `change` must wait for the first
    one's own async `set_dark_mode_override` bridge call to settle
    first, or the two could resolve out of order and leave a stale
    theme applied -- `wireDarkModeOverrideField`'s own handler is
    `async`, so firing both events in one synchronous script (as an
    earlier version of this test did) races exactly that.
    """
    outcome: queue.Queue[bool | None] = queue.Queue(maxsize=1)

    def _poll_until(script: str, predicate: Callable[[Any], bool]) -> Any:
        value = None
        for _ in range(_POLL_ATTEMPTS):
            value = window.evaluate_js(script)
            if predicate(value):
                return value
            time.sleep(_POLL_INTERVAL_SECONDS)
        return value

    def _drive() -> None:
        try:
            _poll_until(_INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(
                "(function(){"
                "var select = document.getElementById('field-dark_mode_override');"
                "select.value = 'dark';"
                "select.dispatchEvent(new Event('change'));"
                "})();"
            )
            _poll_until(
                "document.documentElement.dataset.theme",
                lambda value: value == "dark",
            )
            window.evaluate_js(
                "(function(){"
                "var select = document.getElementById('field-dark_mode_override');"
                "select.value = '';"
                "select.dispatchEvent(new Event('change'));"
                "})();"
            )
            settled = _poll_until(
                "document.documentElement.hasAttribute('data-theme')",
                lambda value: value is False,
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=10.0)

    assert settled is False
