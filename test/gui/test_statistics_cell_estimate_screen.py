"""A finished run's statistics cells show the average, not the last value.

The value in the last generation is one noisy draw (the Wright-Takahata
example's `D` ends at 0.65 while its average over the evidence window is
0.29). The statistics panel therefore shows each statistic's window mean and
standard error, and keeps the last generation's value in the tooltip,
labeled as such. A statistic without a per-generation history is unchanged.
"""

from __future__ import annotations

import queue
import re
from pathlib import Path
from typing import Any

import pytest
import webview

from .conftest import poll_page

pytestmark = pytest.mark.gui

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"

_SET_FIELDS = """
function setField(name, value) {
    const field = document.getElementById(`field-${name}`);
    field.value = value;
    field.dispatchEvent(new Event('input', {bubbles: true}));
}
setField('N', '20'); setField('d', '2'); setField('seed', '20260814');
setField('m_rate', '0.1'); setField('mu_value', '0.01');
setField('locus_lengths', '200');
"""

_CELLS = """
({
    state: window.fim.getRunViewState(),
    pending: window.__fimScrubberPending,
    D: {
        text: document.querySelector('#stat-D .stat-value')?.textContent,
        title: document.getElementById('stat-D').title,
    },
    K_ST: {
        text: document.querySelector('#stat-K_ST .stat-value')?.textContent,
        title: document.getElementById('stat-K_ST').title,
    },
})
"""

# "0.29 ± 0.06" or "0.193 ± 0.007".
_ESTIMATE = re.compile(r"^\d\.\d{2,3} ± \d\.\d{2,3}$")


def test_a_completed_runs_cells_show_the_average_and_its_standard_error(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    """D's cell reads "mean ± SE" and its tooltip names the last value."""
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            poll_page(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(
                _SET_FIELDS + "document.getElementById('run-button').click();"
            )
            outcome.put(
                poll_page(
                    window,
                    _CELLS,
                    lambda value: (
                        value["state"] == "completed" and value["pending"] == 0
                    ),
                )
            )
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=120)

    assert _ESTIMATE.match(settled["D"]["text"]), settled["D"]["text"]
    assert "mean ± 1 standard error" in settled["D"]["title"]
    assert "last generation " in settled["D"]["title"]
    # A statistic with no history keeps its single value.
    assert "±" not in settled["K_ST"]["text"]
    assert "last generation" not in settled["K_ST"]["title"]
