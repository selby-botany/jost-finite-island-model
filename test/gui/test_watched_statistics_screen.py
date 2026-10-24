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
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import webview
from conftest import wait_or_fail

from fim.gui.app import Api, create_window
from fim.gui.batch_runner import BatchMessage
from fim.gui.runner import RunMessage

from .conftest import poll_page, wait_for_run_view_ready
from .test_batch_results_screen import _SET_TINY_BATCH_FIELDS
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
        label = "G subscript ST" if name == "G_ST" else name
        prefix = f"{label}. The run watches {label}"
        assert marks[name]["label"].startswith(prefix), name
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


def test_a_batch_marks_its_own_watched_statistics_not_the_previous_runs(
    fast_batch_run_settings: Path,
) -> None:
    """A batch underlines the statistics its stop rule watches, only those.

    The marks come from the run on screen. The batch's completed payload is
    captured and shown again after the marks of an earlier run (here `H_S` and
    `K_ST`, which this batch does not watch) are put back, as opening a saved
    batch after another run would do. The default batch watches `D` and
    `G_ST`.
    """
    done_event = threading.Event()

    def on_message(message: RunMessage | BatchMessage) -> None:
        if message[0] in ("done", "cancelled", "error"):
            done_event.set()

    window = create_window(api=Api(on_message=on_message), hidden=True)
    outcome: queue.Queue[dict[str, list[str]]] = queue.Queue(maxsize=1)
    capture = (
        "window.__original = window.fim.enterCompletedState;"
        "window.fim.enterCompletedState = function (payload, isBatch) {"
        "window.__payload = payload; return window.__original(payload, isBatch);"
        "};"
    )
    reopen = (
        "window.fim.setWatchedStatistics(['H_S', 'K_ST']);"
        "window.__original(window.__payload, true);"
    )
    read = (
        "({marked: Array.from(document.querySelectorAll("
        "'#batch-results-summary-body tr.stat-watched'))"
        ".map((row) => row.dataset.trajectoryStatistic), "
        "watched: window.__payload.batch.statistics})"
    )

    def _drive() -> None:
        try:
            wait_for_run_view_ready(window)
            window.evaluate_js(capture)
            window.evaluate_js(
                _SET_TINY_BATCH_FIELDS
                + "document.getElementById('run-button').click();"
            )
            wait_or_fail(done_event, "batch (done, cancelled or error message)")
            window.evaluate_js(reopen)
            outcome.put(window.evaluate_js(read))
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=120)

    assert settled["watched"] == ["D", "G_ST"]
    assert sorted(settled["marked"]) == ["D", "G_ST"]
