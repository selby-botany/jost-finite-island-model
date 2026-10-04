"""Headless functional tests for the closed-form comparison in statistic tooltips.

A statistic with a closed-form expected trajectory (`D`, `G_ST`, `H_S`,
`H_T`, `H_ST`) shows, in its statistics-row tooltip, the observed value
minus the closed-form prediction at the displayed generation and the
recorded trajectory's mean squared error from the closed-form trajectory.
"""

from __future__ import annotations

import queue
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import webview

pytestmark = pytest.mark.gui

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"

# The tooltip uses a true minus sign (U+2212), not a hyphen.
_MINUS = "\u2212"

# A sampled closed form (the unequal-sizes shape) on the same grid as the
# history, so the expected values need no interpolation: differences
# 0, -0.05, +0.05, -0.1, mean square 0.00375.
_COMPARISON = """
(() => {
    const generations = [0, 1, 2, 3];
    const histories = {
        D: [0.1, 0.2, 0.3, 0.4],
        G_ST: [0.1, 0.2],
        H_S: [0.5, 0.5, 0.5, 0.5],
        H_T: [0.6, 0.6, 0.6, 0.6],
    };
    const closedForm = {
        generations: [0, 1, 2, 3],
        statistics: {D: [0.1, 0.25, 0.25, 0.5], G_ST: [0, 0, 0, 0]},
    };
    const comparisons = closedFormComparisons(generations, histories, closedForm);
    return {
        names: Object.keys(comparisons),
        mse: comparisons.D.mse,
        count: comparisons.D.count,
        last: closedFormNote(comparisons, "D"),
        second: closedFormNote(comparisons, "D", 2),
        none: closedFormNote(comparisons, "K_ST"),
        empty: Object.keys(closedFormComparisons(generations, histories, null)),
    };
})()
"""


def test_the_comparison_arithmetic_on_a_known_trajectory(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Hand-computed difference and MSE; a short history is left out."""
    settled = drive(window, ready=_INPUT_SCREEN_READY, trigger="null", read=_COMPARISON)

    assert settled["names"] == ["D"]
    assert settled["mse"] == pytest.approx(0.00375)
    assert settled["count"] == 4
    assert f"D {_MINUS} D_predicted = {_MINUS}0.100 at generation 3" in settled["last"]
    assert "trajectory MSE from predicted = 0.00375 over 4" in settled["last"]
    assert f"D {_MINUS} D_predicted = +0.0500 at generation 2" in settled["second"]
    assert settled["none"] == ""
    assert settled["empty"] == []


def _poll(window: webview.Window, script: str, predicate: Callable[[Any], bool]) -> Any:
    """Evaluate `script` until `predicate` holds (bounded), returning the value."""
    value = None
    for _ in range(600):
        value = window.evaluate_js(script)
        if predicate(value):
            return value
        time.sleep(0.1)
    return value


def test_a_completed_runs_closed_form_rows_carry_the_comparison(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    """After a real run: closed-form rows have it, others do not."""
    set_fields = (
        "function setField(name, value) {"
        "const field = document.getElementById(`field-${name}`);"
        "field.value = value;"
        "field.dispatchEvent(new Event('input', {bubbles: true}));"
        "}"
        "setField('N', '20'); setField('d', '2'); setField('seed', '20260814');"
        "setField('m_rate', '0.1'); setField('mu_value', '0.01');"
        "setField('locus_lengths', '200');"
    )
    titles = (
        "({"
        "state: window.fim.getRunViewState(), "
        "pending: window.__fimScrubberPending, "
        "D: document.getElementById('stat-D').title, "
        "H_S: document.getElementById('stat-H_S').title, "
        "K_ST: document.getElementById('stat-K_ST').title"
        "})"
    )
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            _poll(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(
                set_fields + "document.getElementById('run-button').click();"
            )
            outcome.put(
                _poll(
                    window,
                    titles,
                    lambda value: (
                        value["state"] == "completed" and value["pending"] == 0
                    ),
                )
            )
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=120)

    for name in ("D", "H_S"):
        assert f"{name} {_MINUS} {name}_predicted = " in settled[name]
        assert "trajectory MSE from predicted = " in settled[name]
    assert "_predicted" not in settled["K_ST"]
