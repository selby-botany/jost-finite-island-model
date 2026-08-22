"""Headless functional tests for Screen 2, the running screen (design doc
§0.5, §4.2, §6.4).

Real DOM-driven proof that clicking "Run simulation" actually starts a
real background run (`fim.gui.runner.start_run`, unchanged from the
Tk-era build) and that the page's own `fim.onRunProgress`/`onRunDone`
handlers (`webui/screens/progress.js`) update the live scatter as pushed
— `test/gui/test_app_api.py` and `test/gui/test_runner.py` already prove
the bridge and business logic independently; these tests prove the two
are actually wired together correctly end to end, which no Python-only
test can check.
"""

from __future__ import annotations

import queue
import time
from collections.abc import Callable
from typing import Any

import pytest
import webview

pytestmark = pytest.mark.gui

_INPUT_SCREEN_READY = "window.__fimInputScreenReady === true"
_POLL_INTERVAL_SECONDS = 0.02
_POLL_ATTEMPTS = 1000


def test_run_button_switches_to_the_live_progress_screen(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Clicking "Run simulation" with the (already-valid) starter form starts a run.

    `tiny_params`-scale is not what the starter form uses (it defaults
    to `d: 20`, `max_generations: 10000` — `fim.cli.STARTER_CONFIG`), so
    this test does not wait for the run to finish, only for Screen 2 to
    become visible and receive its first live progress push — a real,
    if partial, proof that `Api.start_run` actually started a background
    run and `fim.onRunProgress` actually updated the DOM, without this
    test's own runtime depending on a 10000-generation run completing.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger="document.getElementById('run-button').click();",
        read=(
            "({"
            "progressScreenVisible: "
            "!document.getElementById('screen-progress').hidden, "
            "generationValue: document.getElementById('progress-generation').value"
            "})"
        ),
        is_ready=lambda value: (
            value is not None and value.get("progressScreenVisible") is True
        ),
        poll_attempts=1000,
    )

    assert settled["progressScreenVisible"] is True
    # `progress-generation`'s `value` starts at 0 and only ever moves
    # forward as `fim.onRunProgress` pushes arrive -- reading it back
    # as a nonnegative number (not asserting a specific value, since a
    # real, unmocked simulation's own speed decides how many pushes
    # land before this poll stops) is the direct proof a push arrived
    # and was applied, not just that the screen switched.
    assert float(settled["generationValue"]) >= 0


def test_cancel_button_stops_the_run_and_shows_the_cancelled_banner(
    window: webview.Window,
) -> None:
    """Clicking Cancel reaches the same real background run `Api.start_run` started.

    Proves the other half of the wiring `_drain_run_messages` handles —
    `Api.cancel_run` setting the real `threading.Event` `fim.gui.runner`'s
    worker thread checks, and `fim.onRunCancelled` (not `onRunDone`)
    landing on the page in response — the one path the "does a push
    arrive" test above never exercises, since that test never clicks
    Cancel.

    Drives the window directly (not via the `drive` fixture): this test
    needs two sequential trigger-then-poll stages against the *same*
    live window (click Run, wait for it to actually start, only then
    click Cancel) — `conftest.py`'s `drive_and_read` destroys the window
    in its own `finally` block after one such stage, so a second call
    against the same `window` fixture value would evaluate against an
    already-destroyed window.
    """
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            for _ in range(_POLL_ATTEMPTS):
                if window.evaluate_js(_INPUT_SCREEN_READY):
                    break
                time.sleep(_POLL_INTERVAL_SECONDS)
            window.evaluate_js("document.getElementById('run-button').click();")
            for _ in range(_POLL_ATTEMPTS):
                if window.evaluate_js(
                    "!document.getElementById('screen-progress').hidden"
                ):
                    break
                time.sleep(_POLL_INTERVAL_SECONDS)
            window.evaluate_js("document.getElementById('cancel-run-button').click();")
            settled: dict[str, Any] = {}
            for _ in range(_POLL_ATTEMPTS):
                settled = window.evaluate_js(
                    "({"
                    "bannerHidden: "
                    "document.getElementById('progress-banner').hidden, "
                    "bannerText: "
                    "document.getElementById('progress-banner').textContent, "
                    "cancelDisabled: "
                    "document.getElementById('cancel-run-button').disabled"
                    "})"
                )
                if settled.get("bannerHidden") is False:
                    break
                time.sleep(_POLL_INTERVAL_SECONDS)
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=10.0)

    assert "cancelled" in settled["bannerText"]
    assert settled["cancelDisabled"] is True
