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


def test_sweep_curve_has_a_legend_matching_the_shared_statistic_color_palette(
    window: webview.Window,
) -> None:
    """`#explore-legend` names all three plotted lines in the shared statistic colors.

    Botanist GUI design doc `20260907-claude-sonnet-5-botanist-gui-
    redesign.md` §11.3: "the axis label on Explore" is named directly as
    part of the one statistic-color contract every screen shares, so this
    reads `STATISTIC_TRAJECTORY_COLORS` (`run-view-completed.js`, a
    module-scope `const` in the same classic-script global scope every
    `webui/screens/*.js` file shares, per `index.html`'s own `<script>`
    order) back from the live page rather than hardcoding a second copy
    of those hex values here — a real color drift between the two would
    fail this test, not silently pass with a stale expectation.
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
            _poll_until(
                "window.__fimExploreReady === true", lambda value: value is True
            )
            result = window.evaluate_js(
                "({"
                "items: Array.from("
                "document.getElementById('explore-legend').children"
                ").map((span) => ({"
                "text: span.textContent, "
                "color: span.querySelector('.swatch').style.backgroundColor"
                "})), "
                "expectedColors: {"
                "D: STATISTIC_TRAJECTORY_COLORS.D, "
                "G_ST: STATISTIC_TRAJECTORY_COLORS.G_ST, "
                "E_ST: STATISTIC_TRAJECTORY_COLORS.E_ST"
                "}"
                "})"
            )
            outcome.put(result)
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=10)

    names = [item["text"] for item in result["items"]]
    assert names == ["D", "G_ST", "E_ST"]

    def _to_rgb(hex_color: str) -> str:
        red, green, blue = (
            int(hex_color[1:3], 16),
            int(hex_color[3:5], 16),
            int(hex_color[5:7], 16),
        )
        return f"rgb({red}, {green}, {blue})"

    for item in result["items"]:
        expected_hex = result["expectedColors"][item["text"]]
        assert item["color"] == _to_rgb(expected_hex)


def test_sweep_curve_draws_axis_titles(window: webview.Window) -> None:
    """The canvas draws a real x-axis title (the swept field) and y-axis title.

    Checked the same way `test_results_screen.py`'s own sigma-band test
    checks a canvas fill actually happened: the alpha channel of a small
    rectangle in each title's own drawn region, non-zero only if
    something was actually painted there (a canvas starts fully
    transparent) -- not by trying to read the text back out of raster
    pixels, which no test in this package attempts.
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
            _poll_until(
                "window.__fimExploreReady === true", lambda value: value is True
            )
            result = window.evaluate_js(
                "(() => {"
                "var c = document.getElementById('explore-canvas');"
                "var ctx = c.getContext('2d');"
                "var data = ctx.getImageData(0, 0, c.width, c.height).data;"
                "function nonBlankInRect(x0, y0, x1, y1) {"
                "  var count = 0;"
                "  for (var y = y0; y < y1; y++) {"
                "    for (var x = x0; x < x1; x++) {"
                "      var i = (y * c.width + x) * 4 + 3;"
                "      if (data[i] !== 0) { count += 1; }"
                "    }"
                "  }"
                "  return count;"
                "}"
                # Bottom strip: the x-axis title (`exploreAxisLabel`); left
                # strip: the rotated "Differentiation" y-axis title.
                "return {"
                "  xTitleNonBlankPixels: nonBlankInRect("
                "0, c.height - 14, c.width, c.height - 2), "
                "  yTitleNonBlankPixels: nonBlankInRect(2, 0, 24, c.height)"
                "};"
                "})()"
            )
            outcome.put(result)
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=10)

    assert result["xTitleNonBlankPixels"] > 0
    assert result["yTitleNonBlankPixels"] > 0
