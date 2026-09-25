"""Headless functional tests for the sweep results view (`sweep-results.js`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import webview

from fim.persistence import groups
from fim.sweep import SweepSpec, enumerate_points, expand_axis
from fim.sweep_run import create_sweep_study

from .test_sweep_screen import (
    _OPEN_SWEEP,
    _SCREEN_STATE,
    _SET_TINY_FIELDS,
    _START,
    Poll,
    _drive,
    _plan_ready,
)

pytestmark = pytest.mark.gui

# Axis rows: d as a list, and (for two axes) m as a list on a second row.
_AXES = """
function fire(element, type) {
    element.dispatchEvent(new Event(type, {bubbles: true}));
}
function setAxis(row, key, list) {
    const keySelect = row.querySelector('.sweep-axis-key');
    keySelect.value = key; fire(keySelect, 'change');
    const mode = row.querySelector('.sweep-axis-mode');
    mode.value = 'list'; fire(mode, 'change');
    const input = row.querySelector('.sweep-axis-list');
    input.value = list; fire(input, 'input');
}
"""
_ONE_AXIS = _AXES + "setAxis(document.querySelector('.sweep-axis'), 'd', '2, 3');"
_RESULTS_STATE = """({
    ready: window.__fimSweepResultsReady,
    resultsHidden: document.getElementById('sweep-results').hidden,
    summary: document.getElementById('sweep-results-summary').textContent,
    statistics: Array.from(document.querySelectorAll('#sweep-results-statistic option'))
        .map((option) => option.value),
    kind: sweepResultsHit === null ? null : sweepResultsHit.kind,
    inked: (() => {
        const canvas = document.getElementById('sweep-results-canvas');
        const data = canvas.getContext('2d')
            .getImageData(0, 0, canvas.width, canvas.height).data;
        for (let i = 3; i < data.length; i += 4) { if (data[i] !== 0) { return true; } }
        return false;
    })(),
    readout: document.getElementById('sweep-results-readout').textContent,
    tableRows: document.querySelectorAll('#sweep-results-body tr').length,
    openButtons: document.querySelectorAll('#sweep-results-body button').length,
    modeHidden: document.getElementById('sweep-results-mode-field').hidden,
    theoryHidden: document.getElementById('sweep-results-theory-field').hidden,
    theoryDisabled: document.getElementById('sweep-results-theory').disabled,
    held: document.querySelectorAll('#sweep-results-held select').length
})"""


def _run_and_view_results(window: webview.Window, poll_until: Poll, axes: str) -> Any:
    """Run a sweep from the screen, then open its results view."""
    window.evaluate_js(_SET_TINY_FIELDS + _OPEN_SWEEP)
    poll_until(_SCREEN_STATE, _plan_ready)
    window.evaluate_js(axes)
    poll_until(_SCREEN_STATE, lambda s: s["planReady"] is True and s["tableRows"] >= 2)
    window.evaluate_js(_START)
    poll_until("window.__fimSweepFinished", lambda finished: finished is True)
    window.evaluate_js("document.getElementById('sweep-view-results-button').click();")
    return poll_until(
        _RESULTS_STATE,
        lambda s: s["ready"] is True and s["resultsHidden"] is False,
    )


def test_a_one_axis_sweep_draws_a_line_and_a_table_and_can_toggle_theory(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    def steps(poll_until: Poll) -> Any:
        line = _run_and_view_results(window, poll_until, _ONE_AXIS)
        window.evaluate_js(
            "const box = document.getElementById('sweep-results-theory');"
            "box.checked = true;"
            "box.dispatchEvent(new Event('change', {bubbles: true}));"
        )
        theory = poll_until(_RESULTS_STATE, lambda s: s["ready"] is True)
        return line, theory

    line, theory = _drive(window, steps)

    assert line["summary"].startswith("2 of 2 points finished, varying d")
    assert line["statistics"][0] == "D"
    assert line["kind"] == "line"
    assert line["inked"] is True
    assert line["tableRows"] == 2
    assert line["openButtons"] == 2
    assert line["modeHidden"] is True
    assert line["theoryHidden"] is False
    assert line["theoryDisabled"] is False
    assert theory["kind"] == "line"
    assert theory["inked"] is True


def test_a_two_axis_sweep_draws_a_heat_map_in_all_three_modes(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(_SET_TINY_FIELDS + _OPEN_SWEEP)
        poll_until(_SCREEN_STATE, _plan_ready)
        window.evaluate_js(
            _AXES + "setAxis(document.querySelector('.sweep-axis'), 'd', '2, 3');"
        )
        poll_until(
            _SCREEN_STATE, lambda s: s["planReady"] is True and s["tableRows"] == 2
        )
        window.evaluate_js("document.getElementById('sweep-add-axis-button').click();")
        poll_until(_SCREEN_STATE, lambda s: s["rows"] == 2 and s["planReady"] is True)
        window.evaluate_js(
            _AXES
            + "setAxis(document.querySelectorAll('.sweep-axis')[1], 'm', '0.05, 0.1');"
        )
        poll_until(
            _SCREEN_STATE, lambda s: s["planReady"] is True and s["tableRows"] == 4
        )
        window.evaluate_js(_START)
        poll_until("window.__fimSweepFinished", lambda finished: finished is True)
        window.evaluate_js(
            "document.getElementById('sweep-view-results-button').click();"
        )
        states = {
            "simulation": poll_until(
                _RESULTS_STATE,
                lambda s: s["ready"] is True and s["resultsHidden"] is False,
            )
        }
        for mode in ("theory", "difference"):
            window.evaluate_js(
                "const mode = document.getElementById('sweep-results-mode');"
                f"mode.value = '{mode}';"
                "mode.dispatchEvent(new Event('change', {bubbles: true}));"
            )
            states[mode] = poll_until(_RESULTS_STATE, lambda s: s["ready"] is True)
        return states

    states = _drive(window, steps)

    simulation = states["simulation"]
    assert simulation["summary"].startswith("4 of 4 points finished, varying d and m")
    assert simulation["kind"] == "heatmap"
    assert simulation["modeHidden"] is False
    assert simulation["theoryHidden"] is True
    assert simulation["tableRows"] == 4
    for mode in ("simulation", "theory", "difference"):
        assert states[mode]["kind"] == "heatmap"
        assert states[mode]["inked"] is True


def test_clicking_a_cell_opens_that_runs_results_card(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    def steps(poll_until: Poll) -> Any:
        window.evaluate_js(_SET_TINY_FIELDS + _OPEN_SWEEP)
        poll_until(_SCREEN_STATE, _plan_ready)
        window.evaluate_js(_ONE_AXIS)
        poll_until(
            _SCREEN_STATE, lambda s: s["planReady"] is True and s["tableRows"] == 2
        )
        window.evaluate_js(_START)
        poll_until("window.__fimSweepFinished", lambda finished: finished is True)
        window.evaluate_js(
            "document.getElementById('sweep-view-results-button').click();"
        )
        poll_until(
            _RESULTS_STATE, lambda s: s["ready"] is True and s["resultsHidden"] is False
        )
        # Click the first mark, at its pixel from the chart's own hit list.
        window.evaluate_js(
            """(() => {
                const canvas = document.getElementById('sweep-results-canvas');
                const pixel = sweepResultsHit.pixels[0];
                const box = canvas.getBoundingClientRect();
                canvas.dispatchEvent(new MouseEvent('click', {
                    bubbles: true,
                    clientX: box.left + pixel.x * box.width / canvas.width,
                    clientY: box.top + pixel.y * box.height / canvas.height,
                }));
            })();"""
        )
        # Wait out the scrubber-frame fetch the opened run starts: destroying
        # the window under a bridge call still in flight crashes the process.
        return poll_until(
            "({screen: document.querySelector('.screen:not([hidden])').id, "
            "state: window.fim.getRunViewState(), "
            "pending: window.__fimScrubberPending})",
            lambda s: (
                s["screen"] == "screen-run"
                and s["state"] == "completed"
                and s["pending"] == 0
            ),
        )

    opened = _drive(window, steps)

    assert (opened["screen"], opened["state"]) == ("screen-run", "completed")


def test_home_offers_sweep_results_and_continue_only_for_a_sweep_study(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    spec = SweepSpec(
        base={
            "N": 16,
            "ploidy": 1,
            "d": 2,
            "m": 0.1,
            "mu": 0.01,
            "seed": 7,
            "n_replicates": 1,
            "max_generations": 10,
            "convergence_window": 3,
        },
        axes=(expand_axis("d", [2, 3]),),
    )
    create_sweep_study(spec, enumerate_points(spec), "Planned sweep")
    groups.create_study("By hand")

    def steps(poll_until: Poll) -> Any:
        return poll_until(
            "Array.from(document.querySelectorAll('.open-run-group-actions'))"
            ".map((actions) => Array.from(actions.querySelectorAll('button'))"
            ".map((button) => button.textContent.trim()))",
            lambda groups_seen: any("Sweep results…" in names for names in groups_seen),
        )

    seen = _drive(window, steps)

    sweep = next(names for names in seen if "Sweep results…" in names)
    by_hand = next(
        names
        for names in seen
        if "Sweep results…" not in names and "Delete runs…" in names
    )
    assert "Continue sweep" in sweep
    assert "Re-run all…" not in sweep and "Re-run all…" not in by_hand
    assert "Sweep results…" not in by_hand
    assert "Continue sweep" not in by_hand


def test_home_shows_only_studies_that_exist_and_offers_delete_runs(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    study = groups.create_study("Ring")
    experiment = groups.create_experiment("Migration")
    groups.add_study_to_experiment(experiment.experiment_id, study.study_id)
    gone = groups.create_study("Gone")
    groups.add_study_to_experiment(experiment.experiment_id, gone.study_id)
    # The state an older delete left behind: the Study is gone, the
    # Experiment still lists it.
    groups.study_manifest_path(gone.study_id).unlink()
    groups.create_study("Loose")

    def steps(poll_until: Poll) -> Any:
        return poll_until(
            "({headers: Array.from(document.querySelectorAll("
            "'.open-run-group-header')).map((row) => row.textContent), "
            "buttons: Array.from(document.querySelectorAll("
            "'.open-run-group-actions button')).map((b) => b.textContent.trim())})",
            lambda seen: any("Migration" in header for header in seen["headers"]),
        )

    seen = _drive(window, steps)

    migration = next(h for h in seen["headers"] if "Migration" in h)
    assert "1 study" in migration
    assert "2 studies" not in migration
    assert "Delete runs…" in seen["buttons"]
