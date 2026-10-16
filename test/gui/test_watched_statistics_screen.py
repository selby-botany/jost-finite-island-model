"""The statistics table underlines the statistics the run watches.

Bold, dimming and a color swatch already say whether a statistic is plotted.
A fourth cue says which statistics the convergence rule watches: an
underline on the row's name, with an explanation on the name cell for
hover and for assistive technology. It shows during the run and after it,
for every statistic in `convergence_statistic` (by default `D` and `G_ST`)
and for no other.
"""

from __future__ import annotations

import queue
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import webview

from .conftest import poll_page
from .test_live_statistic_toggle_screen import _during_a_live_run

pytestmark = pytest.mark.gui

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"

_MARKS = """
(() => {
    const rows = Array.from(document.querySelectorAll(
        '#results-stats tr[data-trajectory-statistic]'));
    const marks = {};
    for (const row of rows) {
        const cell = row.querySelector('.stat-name');
        marks[row.dataset.trajectoryStatistic] = {
            watched: row.classList.contains('stat-watched'),
            underlined: getComputedStyle(cell).textDecorationLine.includes('underline'),
            title: cell.title,
            label: cell.getAttribute('aria-label'),
        };
    }
    return marks;
})()
"""

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


def _assert_default_watch(marks: dict[str, dict[str, Any]]) -> None:
    """D and G_ST are marked, with an explanation; nothing else is."""
    for name in ("D", "G_ST"):
        assert marks[name]["watched"] is True, name
        assert marks[name]["underlined"] is True, name
        assert marks[name]["title"].endswith("known to the requested precision."), name
        assert marks[name]["label"].startswith(f"{name}. The run watches {name}"), name
    others = [name for name in marks if name not in ("D", "G_ST")]
    assert others
    for name in others:
        assert marks[name]["watched"] is False, name
        assert marks[name]["underlined"] is False, name
        assert marks[name]["title"] == "", name
        assert marks[name]["label"] is None, name


def test_the_watched_statistics_are_underlined_during_a_run(
    unreachable_convergence_run_settings: Path,
) -> None:
    """A live run marks the statistics its convergence rule watches."""

    def body(
        window: webview.Window,
        wait_for_progress: Callable[[int, str], None],
        progress_so_far: Callable[[], int],
    ) -> dict[str, dict[str, Any]]:
        wait_for_progress(3, "progress message 3")
        marks: dict[str, dict[str, Any]] = window.evaluate_js(_MARKS)
        return marks

    _assert_default_watch(_during_a_live_run(body))


def test_the_watched_statistics_are_underlined_after_a_run(
    fast_scalar_run_settings: Path, window: webview.Window
) -> None:
    """A finished run keeps the marks."""
    outcome: queue.Queue[dict[str, dict[str, Any]]] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            poll_page(window, _INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(
                _SET_FIELDS + "document.getElementById('run-button').click();"
            )
            poll_page(
                window,
                "window.fim.getRunViewState() === 'completed'"
                " && window.__fimScrubberPending === 0",
                lambda value: value is True,
            )
            outcome.put(window.evaluate_js(_MARKS))
        finally:
            window.destroy()

    webview.start(_drive)
    _assert_default_watch(outcome.get(timeout=120))
