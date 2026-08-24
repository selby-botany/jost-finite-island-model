"""Headless functional tests for the Help screen (in-app help design §4.4)."""

from __future__ import annotations

import queue
import time
import webbrowser
from collections.abc import Callable
from typing import Any

import pytest
import webview

pytestmark = pytest.mark.gui

_POLL_ATTEMPTS = 100
_POLL_INTERVAL_SECONDS = 0.1
_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"


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
                "!document.getElementById('screen-run').hidden",
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


def test_external_doc_link_reaches_the_browser_and_settles(
    window: webview.Window, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rendered `data-fim-external` link opens the OS browser and settles.

    Clicks whichever real external link `usage.html`'s own rendered
    fragment happens to contain first (`dev/bin/generate-help-html`'s
    own link rewriting) rather than a synthetic one, the same
    "exercise the real generated content" precedent the rest of this
    file already follows. Monkeypatches `webbrowser.open` — the same
    hook `test_app_api.py`'s own `test_open_external_link_opens_the_
    os_default_browser` uses as a plain Python call — so this never
    opens a real browser.

    Also proves a real, once-reproduced hang is closed:
    `helpContent`'s own click handler called `window.pywebview.api.
    open_external_link(...)` without anything downstream awaiting it,
    so nothing tied a test's own teardown to that call having actually
    finished before `screens/help.js`'s own `window.__fimHelpExternal
    LinkSettled` flag. See `test_running_screen.py`'s own `_wait_for_
    cancel_run_settled` for the full mechanism, traced there via
    `sample <pid>` on a `git push`'s own hung pre-push `pytest` run.
    """
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", opened.append)
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
            window.evaluate_js("window.fim.showHelp('usage');")
            _poll_until(
                "document.querySelectorAll('[data-fim-external]').length > 0",
                lambda value: value is True,
            )
            window.evaluate_js("document.querySelector('[data-fim-external]').click();")
            settled = _poll_until(
                "window.__fimHelpExternalLinkSettled === true",
                lambda value: value is True,
            )
            outcome.put(bool(settled))
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=10)

    assert settled is True
    assert len(opened) == 1
    assert opened[0].startswith("https://")


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
            # Waits for `window.__fimOpenRunRecentRunsLoaded`, not only
            # screen visibility -- `showOpenRunScreen` shows the screen
            # synchronously and fires its own `refreshRecentRuns()` (an
            # async `list_recent_runs()` bridge call) without awaiting it
            # (`open-run.js`'s own comment on that flag explains why;
            # `test_open_run_screen.py::test_open_a_run_button_reaches_
            # screen_six` already established this exact pattern). On a
            # `results/` directory with hundreds of real runs, that call
            # takes real time -- proceeding to destroy the window before
            # it settles is a real, reproducible `JavascriptException`
            # (`_returnValuesCallbacks[...] is not a function`) once the
            # background thread tries to deliver its answer to a window
            # already gone, not merely a theoretical race.
            _poll_until(
                "!document.getElementById('screen-open-run').hidden "
                "&& window.__fimOpenRunRecentRunsLoaded === true",
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
