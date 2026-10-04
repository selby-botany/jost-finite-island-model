"""Headless functional tests for the prediction comparison in statistic tooltips.

A statistic with a prediction shows, first in its statistics-row tooltip,
`ΔXₚ` (observed minus predicted at the displayed generation) and the
running mean squared error of the trajectory so far. The prediction is
the closed-form expected trajectory when the model has one (`D`, `G_ST`,
`H_S`, `H_T`, `H_ST`), otherwise the predicted equilibrium (`D`, `G_ST`,
`E_ST`): a run with stochastic migrants has no closed-form trajectory but
is still compared.
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
# history, so the expected values need no interpolation: differences 0,
# -0.05, +0.05, -0.1. Running MSE: 0.00375 over all four, 0.001667 over
# the first three. Against a predicted equilibrium of 0.25 instead:
# differences -0.15, -0.05, +0.05, +0.15, MSE 0.0125.
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
    const fallback = closedFormComparisons(generations, histories, null, {D: "0.25"});
    return {
        names: Object.keys(comparisons),
        last: closedFormNote(comparisons, "D"),
        second: closedFormNote(comparisons, "D", 2),
        none: closedFormNote(comparisons, "K_ST"),
        empty: Object.keys(closedFormComparisons(generations, histories, null)),
        fallbackNames: Object.keys(fallback),
        fallback: closedFormNote(fallback, "D"),
        placed: withPredictionNote(closedFormNote(comparisons, "D"), "description"),
    };
})()
"""


def test_the_comparison_arithmetic_on_a_known_trajectory(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Hand-computed difference and MSE; a short history is left out."""
    settled = drive(window, ready=_INPUT_SCREEN_READY, trigger="null", read=_COMPARISON)

    assert settled["names"] == ["D"]
    assert settled["last"] == (
        f"ΔDₚ = {_MINUS}0.100, MSE = 0.00375 (vs closed-form trajectory; "
        "MSE over 4 recorded generations to generation 3)"
    )
    assert settled["second"].startswith("ΔDₚ = +0.0500, MSE = 0.00167 ")
    assert "over 3 recorded generations to generation 2" in settled["second"]
    assert settled["none"] == ""
    assert settled["empty"] == []
    assert settled["fallbackNames"] == ["D"]
    assert settled["fallback"].startswith("ΔDₚ = +0.150, MSE = 0.0125 ")
    assert "vs predicted equilibrium 0.250" in settled["fallback"]
    assert settled["placed"].endswith(" — description")


def _poll(window: webview.Window, script: str, predicate: Callable[[Any], bool]) -> Any:
    """Evaluate `script` until `predicate` holds (bounded), returning the value."""
    value = None
    for _ in range(600):
        value = window.evaluate_js(script)
        if predicate(value):
            return value
        time.sleep(0.1)
    return value


@pytest.mark.parametrize(
    ("migrant_sampling", "basis"),
    [
        ("continuous", "vs closed-form trajectory"),
        ("stochastic", "vs predicted equilibrium"),
    ],
)
def test_a_completed_runs_predicted_rows_carry_the_comparison(
    fast_scalar_run_settings: Path,
    window: webview.Window,
    migrant_sampling: str,
    basis: str,
) -> None:
    """After a real run, D's tooltip leads with ΔDₚ and the MSE; K_ST's has none.

    Stochastic migrants put the run outside the closed-form trajectory's
    model, so D is compared with its predicted equilibrium instead.
    """
    set_fields = (
        "function setField(name, value) {"
        "const field = document.getElementById(`field-${name}`);"
        "field.value = value;"
        "field.dispatchEvent(new Event('input', {bubbles: true}));"
        "}"
        "setField('N', '20'); setField('d', '2'); setField('seed', '20260814');"
        "setField('m_rate', '0.1'); setField('mu_value', '0.01');"
        "setField('locus_lengths', '200');"
        "const sampling = document.getElementById('field-migrant_sampling');"
        f"sampling.value = '{migrant_sampling}';"
        "sampling.dispatchEvent(new Event('change', {bubbles: true}));"
    )
    titles = (
        "({"
        "state: window.fim.getRunViewState(), "
        "pending: window.__fimScrubberPending, "
        "D: document.getElementById('stat-D').title, "
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

    assert " — ΔDₚ = " in settled["D"]
    assert "MSE = " in settled["D"]
    assert basis in settled["D"]
    # The comparison comes right after the value, before the description.
    assert settled["D"].index("ΔDₚ") < settled["D"].index("allelic differentiation")
    assert "ₚ" not in settled["K_ST"]
