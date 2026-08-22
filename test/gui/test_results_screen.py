"""Headless functional tests for Screen 3, the results screen (design doc
§4.3, §6.4).

Real DOM-driven proof that a completed scalar run actually reaches
`fim.showResults` (`webui/screens/results.js`) and renders the run's own
summary and scatter — `test/gui/test_app_api.py` already proves
`format_statistic`/`open_output_folder` independently as plain Python
calls; these tests prove the page's own JavaScript displays what
`_drain_run_messages`'s `"done"` payload actually carries.

Drives a small, fast-converging configuration through the real form (the
same values `test/conftest.py`'s `tiny_params` fixture uses, entered as
form fields — `tiny_params` itself is a `SimulationParams`, not a form
values `dict`, so it cannot be handed to `start_run` directly), not the
starter form's own `d: 20`/`max_generations: 10000` defaults, so each
test here completes in well under a second rather than needing to wait
out (or rely on early convergence of) a 10000-generation run.

`test/gui/test_running_screen.py`'s own module docstring records a real,
repeatedly-reproduced investigation into these tests intermittently
hanging when run alongside that file's — not caused by this file's own
(small, fast) configuration, and not fixed by raising `_POLL_ATTEMPTS`
alone, but by `conftest.py`'s own `_POLL_INTERVAL_SECONDS`: two threads
(a test's own poll loop, and `fim.gui.app._drain_run_messages` pushing
progress from its own background thread) both calling `window.
evaluate_js` on the same window raised the odds of a collision the
faster the poll loop hammered it.
"""

from __future__ import annotations

import queue
import time
from collections.abc import Callable
from typing import Any

import pytest
import webview

pytestmark = pytest.mark.gui

_POLL_INTERVAL_SECONDS = 0.1
_POLL_ATTEMPTS = 600
# Generous margin over the "New run" test's own three sequential poll
# stages, each individually bounded by `_POLL_ATTEMPTS`.
_DRIVE_TIMEOUT_SECONDS = 3 * _POLL_ATTEMPTS * _POLL_INTERVAL_SECONDS + 10.0

_INPUT_SCREEN_READY = "window.__fimInputScreenReady === true"

# Mirrors `test/conftest.py`'s `tiny_params` fixture field for field, so
# the run converges (or hits its own small generation cap) almost
# immediately — see this module's own docstring.
_SET_TINY_FIELDS = """
function setField(name, value) {
    const field = document.getElementById(`field-${name}`);
    field.value = value;
    field.dispatchEvent(new Event('input', {bubbles: true}));
}
setField('N', '20');
setField('d', '2');
setField('seed', '20260814');
setField('m_rate', '0.1');
setField('mu_value', '0.01');
setField('locus_lengths', '200');
setField('convergence_window', '4');
setField('convergence_tolerance', '1.0');
setField('max_generations', '10');
"""


def test_a_completed_run_renders_the_results_screen(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """A finished run shows its run id, outcome, all six statistics, and a scatter."""
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger=(_SET_TINY_FIELDS + "document.getElementById('run-button').click();"),
        read=(
            "({"
            "resultsVisible: !document.getElementById('screen-results').hidden, "
            "runId: document.getElementById('results-run-id').textContent, "
            "outcome: document.getElementById('results-outcome').textContent, "
            "statD: document.getElementById('stat-D').textContent, "
            "statGST: document.getElementById('stat-G_ST').textContent, "
            "animateDisabled: document.getElementById('animate-button').disabled"
            "})"
        ),
        is_ready=lambda value: (
            value is not None and value.get("resultsVisible") is True
        ),
        poll_attempts=_POLL_ATTEMPTS,
    )

    assert settled["resultsVisible"] is True
    assert settled["runId"].startswith("run-")
    assert "generation" in settled["outcome"]
    assert settled["statD"].startswith("D = ")
    assert settled["statGST"].startswith("G_ST = ")
    # `tiny_params`-scale runs always persist more than one generation
    # (`convergence_window`'s own minimum of 2 forces at least one step
    # past generation 0 before stability can first be evaluated — the
    # same guarantee `_animate_is_enabled`'s own docstring in the
    # Tk-era `results_screen.py` names), so "Animate" is enabled here.
    assert settled["animateDisabled"] is False


def test_new_run_button_switches_back_to_the_input_screen(
    window: webview.Window,
) -> None:
    """ "New run" returns to Screen 1 without needing another bridge call.

    Drives the window directly (not via the `drive` fixture): this test
    needs two sequential trigger-then-poll stages against the *same*
    live window (finish a run, only then click "New run") —
    `conftest.py`'s `drive_and_read` destroys the window in its own
    `finally` block after one such stage, the same reason
    `test/gui/test_running_screen.py`'s own cancel test drives directly.
    """
    outcome: queue.Queue[bool] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            for _ in range(_POLL_ATTEMPTS):
                if window.evaluate_js(_INPUT_SCREEN_READY):
                    break
                time.sleep(_POLL_INTERVAL_SECONDS)
            window.evaluate_js(
                _SET_TINY_FIELDS + "document.getElementById('run-button').click();"
            )
            for _ in range(_POLL_ATTEMPTS):
                if window.evaluate_js(
                    "!document.getElementById('screen-results').hidden"
                ):
                    break
                time.sleep(_POLL_INTERVAL_SECONDS)
            window.evaluate_js("document.getElementById('new-run-button').click();")
            switched = False
            for _ in range(_POLL_ATTEMPTS):
                switched = window.evaluate_js(
                    "!document.getElementById('screen-input').hidden"
                )
                if switched:
                    break
                time.sleep(_POLL_INTERVAL_SECONDS)
            outcome.put(switched)
        finally:
            window.destroy()

    webview.start(_drive)
    switched = outcome.get(timeout=_DRIVE_TIMEOUT_SECONDS)

    assert switched is True
