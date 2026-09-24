"""Headless functional tests for Explore's surface mode (`explore-surface.js`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import webview

from .test_sweep_screen import Poll, _drive

pytestmark = pytest.mark.gui

_STATE = """({
    curveReady: window.__fimExploreReady === true,
    surfaceReady: window.__fimExploreSurfaceReady,
    mode: document.getElementById('explore-mode').value,
    surfaceHidden: document.getElementById('explore-surface').hidden,
    curveHidden: document.getElementById('explore-canvas').hidden,
    rowAxis: document.getElementById('explore-axis-y').value,
    disabledRowAxes: Array.from(document.querySelectorAll('#explore-axis-y option'))
        .filter((option) => option.disabled).map((option) => option.value),
    readout: document.getElementById('explore-surface-readout').textContent,
    probe: exploreSurfaceProbe,
    scrubbed: document.getElementById('explore-predictions')
        .classList.contains('explore-scrubbed'),
    dValue: document.querySelector('#explore-stat-D')?.textContent,
    inked: (() => {
        const canvas = document.getElementById('explore-surface-canvas');
        const data = canvas.getContext('2d')
            .getImageData(0, 0, canvas.width, canvas.height).data;
        for (let i = 3; i < data.length; i += 4) { if (data[i] !== 0) { return true; } }
        return false;
    })()
})"""

_OPEN_EXPLORE = "window.fim.showExplore();"
_SURFACE = """
const mode = document.getElementById('explore-mode');
mode.value = 'surface';
mode.dispatchEvent(new Event('change', {bubbles: true}));
"""


def test_curve_stays_the_default_and_the_surface_is_hidden(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(_OPEN_EXPLORE)
        return poll_until(_STATE, lambda s: s["curveReady"] is True)

    state = _drive(window, steps)

    assert state["mode"] == "curve"
    assert state["surfaceHidden"] is True
    assert state["curveHidden"] is False


def test_the_surface_draws_a_heat_map_and_the_probe_reads_the_table(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(_OPEN_EXPLORE)
        poll_until(_STATE, lambda s: s["curveReady"] is True)
        window.evaluate_js(_SURFACE)
        surface = poll_until(_STATE, lambda s: s["surfaceReady"] is True and s["inked"])
        # Move the probe one column and one row with the keyboard.
        window.evaluate_js(
            """(() => {
                const canvas = document.getElementById('explore-surface-canvas');
                canvas.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowRight'}));
                canvas.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowDown'}));
            })();"""
        )
        moved = poll_until(_STATE, lambda s: s["scrubbed"] is True)
        window.evaluate_js(
            "document.getElementById('explore-surface-canvas')"
            ".dispatchEvent(new KeyboardEvent('keydown', {key: 'Home'}));"
        )
        home = poll_until(_STATE, lambda s: s["scrubbed"] is False)
        return surface, moved, home

    surface, moved, home = _drive(window, steps)

    assert surface["mode"] == "surface"
    assert surface["surfaceHidden"] is False
    assert surface["curveHidden"] is True
    assert surface["rowAxis"] != "m"
    assert "m" in surface["disabledRowAxes"]
    assert "the configuration you entered" in surface["readout"]
    assert "exploring" in moved["readout"]
    assert moved["probe"] != surface["probe"]
    assert home["probe"] == surface["probe"]
    assert "the configuration you entered" in home["readout"]


def test_switching_the_column_axis_keeps_the_two_axes_different(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(_OPEN_EXPLORE)
        poll_until(_STATE, lambda s: s["curveReady"] is True)
        window.evaluate_js(_SURFACE)
        poll_until(_STATE, lambda s: s["surfaceReady"] is True)
        # The column axis defaults to m and the row axis to d; make the
        # column axis d, which must move the row axis off d.
        window.evaluate_js(
            "const axis = document.getElementById('explore-axis');"
            "axis.value = 'd';"
            "axis.dispatchEvent(new Event('change', {bubbles: true}));"
        )
        return poll_until(
            _STATE,
            lambda s: s["surfaceReady"] is True and "d" in s["disabledRowAxes"],
        )

    state = _drive(window, steps)

    assert state["rowAxis"] != "d"
    assert state["inked"] is True
