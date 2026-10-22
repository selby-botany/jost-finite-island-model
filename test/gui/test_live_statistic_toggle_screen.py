"""Headless functional tests: hiding a statistic while a run is still going.

A researcher hides statistics during a run to declutter the trajectory
graph and follow one of them. The statistics-table row toggle
(`run-view-completed.js`'s `toggleTrajectoryStatistic`) must therefore
work on a live run, not only on a finished one, and a hidden statistic
must stay hidden as later progress ticks redraw the graph and rebuild the
table rows.

The run is real (a background thread pushes `fim.onRunProgress`), and the
tests wait on Python-side progress counts, never on a page-polling loop
racing that thread (see `test_running_screen.py`'s module docstring).
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import webview
from conftest import poll_or_fail, wait_or_fail

from fim.gui.app import Api, create_window
from fim.gui.batch_runner import BatchMessage
from fim.gui.runner import RunMessage

from .conftest import wait_for_run_view_ready
from .test_running_screen import _wait_for_cancel_run_settled

pytestmark = pytest.mark.gui

_OUTCOME_TIMEOUT_SECONDS = 60.0
_PROGRESS_POLL_SECONDS = 0.05

# Pixels drawn in exactly the `D` curve's stroke color. Counted on the
# trajectory canvas: nonzero while `D` is plotted, zero once it is hidden.
_D_PIXEL_COUNT = """
(() => {
    const canvas = document.getElementById('run-trajectory-canvas');
    const probe = document.createElement('canvas').getContext('2d');
    probe.fillStyle = STATISTIC_TRAJECTORY_COLORS.D;
    probe.fillRect(0, 0, 1, 1);
    const [red, green, blue] = probe.getImageData(0, 0, 1, 1).data;
    const data = canvas.getContext('2d')
        .getImageData(0, 0, canvas.width, canvas.height).data;
    let count = 0;
    for (let i = 0; i < data.length; i += 4) {
        if (data[i] === red && data[i + 1] === green && data[i + 2] === blue
            && data[i + 3] === 255) {
            count += 1;
        }
    }
    return count;
})()
"""

_D_ROW_STATE = """
(() => {
    const row = document.querySelector(
        '#results-stats tr[data-trajectory-statistic="D"]');
    return {
        hiddenClass: row.classList.contains('stat-plot-hidden'),
        ariaPressed: row.getAttribute('aria-pressed'),
        inHiddenSet: window.fim.workspace.selected().completedView
            .hiddenTrajectoryStatistics.has('D'),
        trajectoryHidden: isTrajectoryHidden('D'),
    };
})()
"""

_CLICK_D_ROW = (
    "document.querySelector("
    "'#results-stats tr[data-trajectory-statistic=\"D\"]').click();"
)

# Tags the D row's cells so a later read can tell whether they are the very
# same elements. A pointer press lands on one of these cells; if a progress
# tick replaced them before the release, the browser would drop the click.
_TAG_D_ROW_CELLS = """
(() => {
    const row = document.querySelector(
        '#results-stats tr[data-trajectory-statistic="D"]');
    for (const cell of row.children) {
        cell.dataset.pressTarget = 'kept';
    }
    return row.children.length;
})()
"""

_D_ROW_CELL_TAGS = """
(() => {
    const row = document.querySelector(
        '#results-stats tr[data-trajectory-statistic="D"]');
    return Array.from(row.children).map(
        (cell) => ({kept: cell.dataset.pressTarget === 'kept',
                    connected: cell.isConnected}));
})()
"""


def _during_a_live_run(
    body: Callable[
        [webview.Window, Callable[[int, str], None], Callable[[], int]], Any
    ],
) -> Any:
    """Start a real run, hand `body` the window mid-run, cancel, return its result.

    Args:
        body: Called once the run is going, with the window, a function that
            waits until at least N progress messages have arrived (given the
            count and what is being waited for), and a function returning
            the progress messages so far.

    Returns:
        Whatever `body` returned.
    """
    started_event = threading.Event()
    cancelled_event = threading.Event()
    progress_count = 0

    def on_message(message: RunMessage | BatchMessage) -> None:
        nonlocal progress_count
        if message[0] == "progress":
            progress_count += 1
        elif message[0] == "cancelled":
            cancelled_event.set()

    def wait_for_progress(at_least: int, what: str) -> None:
        poll_or_fail(
            lambda: progress_count,
            lambda count: count >= at_least,
            what,
            interval=_PROGRESS_POLL_SECONDS,
        )

    api = Api(on_run_started=started_event.set, on_message=on_message)
    window = create_window(api=api, hidden=True)
    outcome: queue.Queue[Any] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            wait_for_run_view_ready(window)
            window.evaluate_js("document.getElementById('run-button').click();")
            wait_or_fail(started_event, "run start (on_run_started)")
            poll_or_fail(
                lambda: window.evaluate_js(
                    "window.fim.workspace.settled("
                    "window.fim.workspace.selectedId(), 'runStart')"
                ),
                lambda settled: settled is True,
                "Run start response",
                interval=_PROGRESS_POLL_SECONDS,
            )
            result = body(window, wait_for_progress, lambda: progress_count)
            window.evaluate_js("document.getElementById('cancel-run-button').click();")
            wait_or_fail(cancelled_event, "run cancellation (cancelled message)")
            _wait_for_cancel_run_settled(window)
            outcome.put(result)
        finally:
            window.destroy()

    webview.start(_drive)
    return outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)


def _read_d(window: webview.Window) -> dict[str, Any]:
    """Read the D row's state and the pixels its curve currently draws."""
    return {
        "row": window.evaluate_js(_D_ROW_STATE),
        "pixels": window.evaluate_js(_D_PIXEL_COUNT),
    }


def test_a_statistic_can_be_hidden_and_shown_during_a_live_run(
    unreachable_convergence_run_settings: Path,
) -> None:
    """Hide `D` mid-run: its curve goes, stays gone across ticks, and returns."""

    def body(
        window: webview.Window,
        wait_for_progress: Callable[[int, str], None],
        progress_so_far: Callable[[], int],
    ) -> dict[str, dict[str, Any]]:
        wait_for_progress(3, "progress message 3")
        plotted = _read_d(window)
        # Hide D, then let several later ticks redraw the graph and rebuild
        # the table rows before reading the state back.
        window.evaluate_js(_CLICK_D_ROW)
        hidden_now = _read_d(window)
        wait_for_progress(progress_so_far() + 3, "three more progress messages")
        hidden_later = _read_d(window)
        # The same row, clicked again after those ticks, shows D again.
        window.evaluate_js(_CLICK_D_ROW)
        return {
            "plotted": plotted,
            "hidden_now": hidden_now,
            "hidden_later": hidden_later,
            "shown_again": _read_d(window),
        }

    settled = _during_a_live_run(body)

    assert settled is not None
    assert settled["plotted"]["pixels"] > 0
    assert settled["plotted"]["row"]["hiddenClass"] is False
    assert settled["plotted"]["row"]["ariaPressed"] == "true"
    for phase in ("hidden_now", "hidden_later"):
        state = settled[phase]
        assert state["row"]["inHiddenSet"] is True, phase
        assert state["row"]["trajectoryHidden"] is True, phase
        assert state["row"]["hiddenClass"] is True, phase
        assert state["row"]["ariaPressed"] == "false", phase
        assert state["pixels"] == 0, phase
    assert settled["shown_again"]["row"]["inHiddenSet"] is False
    assert settled["shown_again"]["row"]["ariaPressed"] == "true"
    assert settled["shown_again"]["pixels"] > 0


def test_statistics_rows_keep_their_cells_across_live_ticks(
    unreachable_convergence_run_settings: Path,
) -> None:
    """A live tick updates a row's cells in place, never replaces them.

    A mouse click needs its press and release on the same element. Rebuilding
    the cells on every progress tick removes the element under the pointer
    between the two, and WebKit then delivers no click at all, which is how
    a fast run made the row toggle unusable while a synthetic `click()`
    (which this file's first test uses) still worked.
    """

    def body(
        window: webview.Window,
        wait_for_progress: Callable[[int, str], None],
        progress_so_far: Callable[[], int],
    ) -> list[dict[str, bool]]:
        wait_for_progress(3, "progress message 3")
        window.evaluate_js(_TAG_D_ROW_CELLS)
        wait_for_progress(progress_so_far() + 3, "three more progress messages")
        tags: list[dict[str, bool]] = window.evaluate_js(_D_ROW_CELL_TAGS)
        return tags

    tags = _during_a_live_run(body)

    assert tags is not None
    assert len(tags) == 3
    assert all(tag == {"kept": True, "connected": True} for tag in tags)
