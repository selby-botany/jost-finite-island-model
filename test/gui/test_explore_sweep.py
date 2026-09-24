"""Headless functional tests for Explore's "Choose a sweep" panel."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import webview

from fim.gui import app as app_module

from .test_explore_surface import _OPEN_EXPLORE, _STATE, _SURFACE
from .test_sweep_screen import Poll, _drive

pytestmark = pytest.mark.gui

_PANEL = """({
    ready: window.__fimExploreSweepReady,
    panelHidden: document.getElementById('explore-sweep-panel').hidden,
    expanded: document.getElementById('explore-sweep-toggle')
        .getAttribute('aria-expanded'),
    controls: Array.from(document.querySelectorAll('.axis-range'))
        .map((el) => el.dataset.key),
    chips: Array.from(document.querySelectorAll('.axis-range')).map(
        (el) => Array.from(el.querySelectorAll('.axis-range-chip')).map(
            (chip) => chip.textContent)),
    ticks: Array.from(document.querySelectorAll('.axis-range')).map(
        (el) => el.querySelectorAll('.axis-range-tick').length),
    plan: document.getElementById('explore-sweep-plan').textContent,
    runDisabled: document.getElementById('explore-sweep-button').disabled,
    countDisabled: Array.from(document.querySelectorAll('.axis-range-count'))
        .map((input) => input.disabled),
    restoreHidden: Array.from(document.querySelectorAll('.axis-range-restore'))
        .map((button) => button.hidden),
    markers: exploreSurfaceMarkers.length
})"""

_TOGGLE = "document.getElementById('explore-sweep-toggle').click();"


def _open_panel(window: webview.Window, poll_until: Poll, surface: bool) -> Any:
    window.evaluate_js(_OPEN_EXPLORE)
    poll_until(_STATE, lambda s: s["curveReady"] is True)
    if surface:
        window.evaluate_js(_SURFACE)
        poll_until(_STATE, lambda s: s["surfaceReady"] is True)
    window.evaluate_js(_TOGGLE)
    return poll_until(
        _PANEL,
        lambda s: (
            s["ready"] is True and s["panelHidden"] is False and "point" in s["plan"]
        ),
    )


def test_the_javascript_axis_domains_match_the_bridges(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(_OPEN_EXPLORE)
        poll_until(_STATE, lambda s: s["curveReady"] is True)
        return window.evaluate_js(
            "Object.fromEntries(Object.entries(EXPLORE_SWEEP_AXES)"
            ".map(([key, info]) => [key, info.domain]))"
        )

    domains = _drive(window, steps)

    assert {
        key: list(bounds)
        for key, bounds in app_module._EQUILIBRIUM_SWEEP_DOMAINS.items()
    } == domains


def test_the_panel_shows_the_interval_ticks_chips_and_a_plan_count(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    def steps(poll_until: Poll) -> Any:
        return _open_panel(window, poll_until, surface=False)

    panel = _drive(window, steps)

    assert panel["expanded"] == "true"
    assert panel["controls"] == ["m"]
    assert panel["ticks"] == [5]
    assert len(panel["chips"][0]) == 5
    assert panel["plan"] == "5 points."
    assert panel["runDisabled"] is False


def test_removing_a_chip_makes_the_axis_a_custom_list(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    def steps(poll_until: Poll) -> Any:
        _open_panel(window, poll_until, surface=False)
        window.evaluate_js("document.querySelector('.axis-range-chip').click();")
        return poll_until(
            _PANEL, lambda s: s["ready"] is True and s["plan"].startswith("4 point")
        )

    panel = _drive(window, steps)

    assert panel["countDisabled"] == [True]
    assert panel["restoreHidden"] == [False]
    assert len(panel["chips"][0]) == 4


def test_a_surface_plans_the_grid_and_draws_the_points_on_the_map(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(_OPEN_EXPLORE)
        poll_until(_STATE, lambda s: s["curveReady"] is True)
        window.evaluate_js(_SURFACE)
        poll_until(_STATE, lambda s: s["surfaceReady"] is True)
        window.evaluate_js(_TOGGLE)
        return poll_until(
            _PANEL,
            lambda s: s["ready"] is True and s["plan"].startswith("20 points"),
        )

    panel = _drive(window, steps)

    # m (5 points) by d (4 points, integers 2 to 16).
    assert panel["controls"] == ["m", "d"]
    assert panel["markers"] == 20


def test_even_in_the_predicted_response_places_points_differently(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    read_values = (
        "Array.from(document.querySelectorAll('.axis-range-chip'))"
        ".map((chip) => chip.textContent)"
    )

    def steps(poll_until: Poll) -> Any:
        _open_panel(window, poll_until, surface=False)
        even = window.evaluate_js(read_values)
        window.evaluate_js(
            "const spacing = document.querySelector('.axis-range-spacing');"
            "spacing.value = 'response';"
            "spacing.dispatchEvent(new Event('change', {bubbles: true}));"
        )
        poll_until(_PANEL, lambda s: s["ready"] is True)
        response = poll_until(read_values, lambda values: values != even)
        return even, response

    even, response = _drive(window, steps)

    assert response != even
    assert 2 <= len(response) <= 5


def test_sweep_this_for_real_sets_the_sweep_on_configure(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    def steps(poll_until: Poll) -> Any:
        _open_panel(window, poll_until, surface=False)
        window.evaluate_js("document.getElementById('explore-sweep-button').click();")
        return poll_until(
            "({screen: document.querySelector('.screen:not([hidden])').id, "
            "boxOn: document.getElementById('configure-sweep-checkbox').checked, "
            "summary: document.getElementById('configure-sweep-summary').textContent, "
            "dialogOpen: document.getElementById('modal-sweep').open})",
            lambda s: s["screen"] == "screen-configure" and s["boxOn"] is True,
        )

    configure = _drive(window, steps)

    assert configure["summary"] == "Sweep: m (5), 5 points."
    assert configure["dialogOpen"] is False


def test_an_n_axis_is_converted_from_gene_copies_to_individuals(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    def steps(poll_until: Poll) -> Any:
        _open_panel(window, poll_until, surface=False)
        return window.evaluate_js(
            "[exploreSweepDefinition({key: 'N', range: {start: 200, stop: 2000, "
            "count: 3, scale: 'log'}}, 2), "
            "exploreSweepDefinition({key: 'N', values: [10, 11, 200]}, 2)]"
        )

    ranged, listed = _drive(window, steps)

    assert ranged["range"]["start"] == 100
    assert ranged["range"]["stop"] == 1000
    # 10 and 11 gene copies are 5 and 6 individuals; nothing collapses here,
    # but a repeat after rounding would.
    assert listed["values"] == [5, 6, 100]
