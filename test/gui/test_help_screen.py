"""Headless functional tests for the Help screen (in-app help design §4.4)."""

from __future__ import annotations

import queue
import time
from collections.abc import Callable
from typing import Any

import pytest
import webview

pytestmark = pytest.mark.gui

_POLL_ATTEMPTS = 100
_POLL_INTERVAL_SECONDS = 0.1
_INPUT_SCREEN_READY = "window.__fimInputScreenReady === true"


def test_help_screen_shows_usage_and_back_returns_to_the_prior_screen(
    window: webview.Window,
) -> None:
    """`fim.showHelp` renders `usage.html`; Back returns to the prior screen.

    Two sequential stages against one live window (the same manual-driver
    shape `test_results_screen.py`'s own deme-pair-selector test uses,
    for the same reason: more than one trigger-then-poll round trip on
    a single window needs one `webview.start()` call, not the `drive`
    fixture called twice — its own driver destroys the window in a
    `finally` block after its first, single round trip).
    """
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

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
            window.evaluate_js("window.fim.showHelp('usage');")
            settled = _poll_until(
                "({"
                "helpVisible: !document.getElementById('screen-help').hidden, "
                "hasContent: "
                "document.getElementById('help-content').textContent.length > 0"
                "})",
                lambda value: value["helpVisible"] is True,
            )
            window.evaluate_js("document.getElementById('help-back-button').click();")
            back_visible = _poll_until(
                "!document.getElementById('screen-input').hidden",
                lambda value: value is True,
            )
            outcome.put(
                {
                    "helpVisible": settled["helpVisible"],
                    "hasContent": settled["hasContent"],
                    "backVisible": back_visible,
                }
            )
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=10)

    assert result["helpVisible"] is True
    assert result["hasContent"] is True
    assert result["backVisible"] is True


def test_help_screen_returns_to_results_when_opened_from_there(
    window: webview.Window,
) -> None:
    """Back returns to Results, not a fixed default, when Help was opened from there.

    The one genuinely new interaction Help adds relative to every other
    screen's own fixed-target "Back" button (design §4.4): recording
    *whichever* screen was showing, not always the same one.
    """
    outcome: queue.Queue[bool] = queue.Queue(maxsize=1)

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
            # Show any other screen first -- Screen 6 (open a run) needs
            # no completed run to reach, unlike Results/Batch results.
            window.evaluate_js("window.fim.showOpenRunScreen();")
            _poll_until(
                "!document.getElementById('screen-open-run').hidden",
                lambda value: value is True,
            )
            window.evaluate_js("window.fim.showHelp('configuration');")
            _poll_until(
                "!document.getElementById('screen-help').hidden",
                lambda value: value is True,
            )
            window.evaluate_js("document.getElementById('help-back-button').click();")
            back_visible = _poll_until(
                "!document.getElementById('screen-open-run').hidden",
                lambda value: value is True,
            )
            outcome.put(back_visible)
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=10)

    assert result is True
