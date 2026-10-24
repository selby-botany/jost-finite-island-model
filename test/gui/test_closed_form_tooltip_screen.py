"""Headless functional tests for the prediction comparison in the run messages.

A statistic with a prediction gets a sentence in the run's messages that
compares its average over the evidence window with the prediction averaged
over the same generations, and gives the mean squared difference over the
whole run. The prediction is the closed-form expected trajectory when the
model has one ($D$, $G_{ST}$, $H_S$, $H_T$, $H_{ST}$), otherwise the predicted
equilibrium ($D$, $G_{ST}$, $E_{ST}$): a run with stochastic migrants has no
closed-form trajectory but is still compared.
"""

from __future__ import annotations

import queue
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import webview

from .conftest import poll_page

pytestmark = pytest.mark.gui

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"

# A sampled closed form (the unequal-sizes shape) on the same grid as the
# history, so the expected values need no interpolation: the prediction
# averages 0.275 over the four generations and the history 0.25, so the
# averages differ by 0.025 (5.0 standard errors of 0.005). Squared
# differences 0, 0.0025, 0.0025, 0.01 average 0.00375. Against a predicted
# equilibrium of 0.25 instead: squared differences 0.0225, 0.0025, 0.0025,
# 0.0225 average 0.0125, and the averages agree.
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
    const estimate = {
        mean: 0.25, standardError: 0.005, known: true, start: 0, end: 3, count: 4,
    };
    return {
        names: Object.keys(comparisons),
        sentence: predictionSentence("D", comparisons.D, estimate),
        none: predictionSentence("K_ST", comparisons.K_ST, estimate),
        noEstimate: predictionSentence("D", comparisons.D, null),
        empty: Object.keys(closedFormComparisons(generations, histories, null)),
        fallbackNames: Object.keys(fallback),
        fallback: predictionSentence("D", fallback.D, estimate),
    };
})()
"""


def test_the_comparison_arithmetic_on_a_known_trajectory(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Hand-computed averages and squared difference; a short history is left out."""
    settled = drive(window, ready=_INPUT_SCREEN_READY, trigger="null", read=_COMPARISON)

    assert settled["names"] == ["D"]
    assert settled["sentence"] == (
        "The prediction for D (closed-form trajectory), averaged over the same "
        "generations, is 0.275. This run's average differs from it by 0.025, "
        "which is 5.0 standard errors. Over the whole run the mean squared "
        "difference from the prediction is 0.00375."
    )
    assert settled["none"] == ""
    assert settled["noEstimate"] == ""
    assert settled["empty"] == []
    assert settled["fallbackNames"] == ["D"]
    assert settled["fallback"] == (
        "The prediction for D (predicted equilibrium 0.250), averaged over the "
        "same generations, is 0.250. This run's average differs from it by "
        "0.000, which is 0.0 standard errors. Over the whole run the mean "
        "squared difference from the prediction is 0.0125."
    )


@pytest.mark.parametrize(
    ("migrant_sampling", "basis"),
    [
        ("continuous", "(closed-form trajectory)"),
        ("stochastic", "(predicted equilibrium "),
    ],
)
def test_a_completed_runs_predicted_rows_carry_the_comparison(
    fast_scalar_run_settings: Path,
    window: webview.Window,
    migrant_sampling: str,
    basis: str,
) -> None:
    """After a real run, D's messages compare it with its prediction; K_ST's do not.

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
        "messages: Array.from(document.querySelectorAll('#run-messages li'))"
        ".map((item) => item.textContent).join(' ')"
        "})"
    )
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            poll_page(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(
                set_fields + "document.getElementById('run-button').click();"
            )
            outcome.put(
                poll_page(
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

    assert "The prediction for D " in settled["messages"]
    assert basis in settled["messages"]
    assert "the mean squared difference from the prediction is " in settled["messages"]
    assert "The prediction for K_ST" not in settled["messages"]
    # The tooltip is the headline only; the comparison lives in the messages.
    assert "prediction" not in settled["D"]
