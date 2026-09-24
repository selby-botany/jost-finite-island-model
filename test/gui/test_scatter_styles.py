"""Every scatter point style draws, and each looks different.

The original encoding made a marker's radius grow with how many alleles
share its coordinates, so in a pooled batch the pile of alleles absent
from both demes at (0, 0) became the largest mark on the plot. Seven
styles are offered (Settings, "Scatter plot points") so a botanist can
choose by looking; this pins that each one draws something and that they
are genuinely different encodings, not seven names for one drawing.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
import webview

pytestmark = pytest.mark.gui

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"

_STYLES = ["circles", "color", "badge", "color-badge", "density", "dots", "trail"]

# A pooled-batch-shaped panel: a huge pile at the origin, a few piles of
# different sizes elsewhere, and singletons, with one "most frequent" ring.
_PANEL = (
    "{kind: 'frequency', x_label: 'Deme 1', y_label: 'Deme 2', points: ["
    "{x: 0, y: 0, count: 48, common: false}, "
    "{x: 1, y: 1, count: 9, common: false}, "
    "{x: 0.5, y: 0.45, count: 1, common: true}, "
    "{x: 0.2, y: 0.6, count: 3, common: false}, "
    "{x: 0.8, y: 0.1, count: 1, common: false}, "
    "{x: 0.05, y: 0, count: 2, common: false}]}"
)


def test_every_scatter_style_draws_and_they_are_all_distinct(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Seven styles, seven different non-blank drawings, no errors."""
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            f"const panel = {_PANEL}; "
            # With no run there is no scrubber history, so the trail style
            # would equal the original circles; stand in for the scrubber's
            # own provider with one earlier frame.
            "window.fim.getScrubberTrailPanels = () => [{points: ["
            "{x: 0.3, y: 0.3, count: 1, common: false}, "
            "{x: 0.6, y: 0.5, count: 1, common: false}]}]; "
            "const shots = {}; "
            f"for (const style of {_STYLES!r}) {{ "
            "window.fim.setScatterStyle(style); "
            "drawScatter(runCanvas, panel); "
            "shots[style] = {style: window.fim.getScatterStyle(), "
            "url: runCanvas.toDataURL(), "
            "blank: !runCanvas.getContext('2d').getImageData("
            "0, 0, runCanvas.width, runCanvas.height).data.some((v) => v !== 0)}; "
            "} "
            "window.__fimShots = shots;"
        ),
        read="window.__fimShots || null",
        is_ready=lambda value: value is not None,
    )

    assert set(settled) == set(_STYLES)
    for style, shot in settled.items():
        assert shot["style"] == style
        assert shot["blank"] is False, style
    assert len({shot["url"] for shot in settled.values()}) == len(_STYLES)


def test_an_unknown_scatter_style_is_ignored(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """A style the page does not know leaves the current one alone."""
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            "window.fim.setScatterStyle('density'); "
            "window.fim.setScatterStyle('pie'); "
            "window.__fimStyle = window.fim.getScatterStyle();"
        ),
        read="window.__fimStyle || null",
        is_ready=lambda value: value is not None,
    )

    assert settled == "density"


def test_the_badge_styles_replace_the_origin_circle_with_a_smaller_mark(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """The pile at (0, 0) stops being the biggest mark on the plot.

    Measured as ink: with the original circles the 48-allele pile covers
    a large disc at the corner; the badge styles draw a small dot and a
    label there instead, so a patch of the canvas just above and right of
    the origin holds far less ink.
    """
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(
            f"const panel = {_PANEL}; "
            "const ink = (style) => { "
            "window.fim.setScatterStyle(style); drawScatter(runCanvas, panel); "
            "const side = Math.min(runCanvas.width, runCanvas.height); "
            "const pad = side < 520 ? Math.max(28, Math.floor(side * 0.09)) : 44; "
            "const ox = pad, oy = runCanvas.height - pad; "
            "const data = runCanvas.getContext('2d').getImageData("
            "ox - 8, oy - 30, 22, 30).data; "
            "let n = 0; for (let i = 3; i < data.length; i += 4) "
            "{ if (data[i] !== 0) { n += 1; } } return n; }; "
            "window.__fimInk = {circles: ink('circles'), badge: ink('badge'), "
            "colorBadge: ink('color-badge')};"
        ),
        read="window.__fimInk || null",
        is_ready=lambda value: value is not None,
    )

    assert settled["circles"] > 0
    assert settled["badge"] < settled["circles"]
    assert settled["colorBadge"] < settled["circles"]
