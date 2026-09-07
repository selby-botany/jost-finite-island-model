"""Headless functional tests for the Explore screen (design doc
`20260907-claude-sonnet-5-botanist-gui-redesign.md` §5).

Real DOM-driven proof that `webui/screens/explore.js` actually wires the
page correctly — `test/gui/test_app_api.py`'s own `test_get_equilibrium_
predictions_*`/`test_get_equilibrium_sweep_*` tests already prove the
bridge methods themselves are correct as plain Python calls; these tests
prove the page's own JavaScript calls them at the right moments and
updates the right elements, which no Python-only test can check.
"""

from __future__ import annotations

import queue
import time
from collections.abc import Callable
from typing import Any

import pytest
import webview

pytestmark = pytest.mark.gui

_POLL_ATTEMPTS = 200
_POLL_INTERVAL_SECONDS = 0.1
_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"


def test_menu_explore_shows_predictions_and_back_returns_to_the_prior_screen(
    window: webview.Window,
) -> None:
    """`fim.menu.explore` opens Explore with real predictions; Back returns.

    One `webview.start()` call driving several sequential trigger-then-
    poll round trips (`test_help_screen.py`'s own precedent for why: more
    than one round trip against a single window needs a manual driver,
    not the `drive` fixture, which destroys its window after one).

    The trigger wraps `fim.menu.explore()` in `setTimeout(..., 0)`,
    matching `fim.gui.app._build_menu`'s own real dispatcher exactly —
    calling an `async` `fim.menu.*` method directly as a bare
    `evaluate_js` expression deadlocks (`test_input_screen.py`'s own
    `test_menu_new_configuration_resets_an_edited_field` docstring has
    the full mechanism).
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
            window.evaluate_js("setTimeout(() => { window.fim.menu.explore(); }, 0);")
            settled = _poll_until(
                "({"
                "exploreVisible: !document.getElementById('screen-explore').hidden, "
                "exploreReady: window.__fimExploreReady === true, "
                "dValue: document.getElementById('explore-stat-D').textContent, "
                "canvasWidth: document.getElementById('explore-canvas').width"
                "})",
                lambda value: (
                    value["exploreVisible"] is True and value["exploreReady"] is True
                ),
            )
            window.evaluate_js(
                "document.getElementById('explore-back-button').click();"
            )
            back_visible = _poll_until(
                "!document.getElementById('screen-run').hidden",
                lambda value: value is True,
            )
            outcome.put(
                {
                    "exploreVisible": settled["exploreVisible"],
                    "dValue": settled["dValue"],
                    "canvasWidth": settled["canvasWidth"],
                    "backVisible": back_visible,
                }
            )
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=10)

    assert result["exploreVisible"] is True
    assert result["dValue"] != ""
    assert result["canvasWidth"] > 0
    assert result["backVisible"] is True


def test_changing_a_field_recomputes_predictions(
    window: webview.Window,
) -> None:
    """Committing (`change`) a new `m` value recomputes the predictions table.

    `equilibrium_d`'s own formula (`fim.statistics.differentiation`)
    means a much larger migration rate, everything else held fixed,
    strictly increases `D` — a large enough gap between the two `m`
    values below is not a hand-picked coincidence, it is guaranteed by
    the formula's own monotonicity in `m`, so a real recomputation is
    distinguishable from a stale, unchanged reading by simple inequality,
    with no dependency on either value's own exact digits.
    """
    outcome: queue.Queue[dict[str, str]] = queue.Queue(maxsize=1)

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
            window.evaluate_js("setTimeout(() => { window.fim.menu.explore(); }, 0);")
            _poll_until(
                "window.__fimExploreReady === true", lambda value: value is True
            )
            before = window.evaluate_js(
                "document.getElementById('explore-stat-D').textContent"
            )
            window.evaluate_js(
                "const field = document.getElementById('explore-m'); "
                "field.value = '0.4'; "
                "field.dispatchEvent(new Event('change'));"
            )
            after = _poll_until(
                "document.getElementById('explore-stat-D').textContent",
                lambda value: value != before,
            )
            outcome.put({"before": before, "after": after})
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=10)

    assert result["after"] != result["before"]
