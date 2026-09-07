"""Headless functional tests for the migration-matrix grid editor
(botanist GUI design doc `20260907-claude-sonnet-5-botanist-gui-redesign.md`
§4.4).

Real DOM-driven proof that `webui/screens/migration-matrix.js` actually
builds, resizes, and reads back a real grid of cells —
`test/gui/test_config_form.py`'s own `test_m_to_payload_matrix_mode_*`/
`test_m_from_params_matrix_*` tests already prove `m_to_payload`/
`m_from_params` correct as plain Python calls; these tests prove the
page's own JavaScript builds the grid those functions actually read
from and write to, which no Python-only test can check.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
import webview

from fim.gui.app import Api, create_window
from fim.gui.batch_runner import BatchMessage
from fim.gui.runner import RunMessage

pytestmark = pytest.mark.gui

_POLL_ATTEMPTS = 200
_POLL_INTERVAL_SECONDS = 0.1
_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"


def _set_field(name: str, value: str) -> str:
    """One `setField`-shaped JS statement (`test_running_screen.py`'s own helper)."""
    return (
        f"(function() {{ const field = document.getElementById('field-{name}'); "
        f"field.value = '{value}'; "
        "field.dispatchEvent(new Event('input', {bubbles: true})); })();"
    )


def test_selecting_matrix_mode_builds_an_identity_grid_matching_d(
    window: webview.Window,
) -> None:
    """Switching to matrix mode with no prior matrix builds a d-by-d identity grid."""
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def _poll_until(script: str, predicate: Callable[[Any], bool]) -> Any:
        value = None
        for _ in range(_POLL_ATTEMPTS):
            value = window.evaluate_js(script)
            if predicate(value):
                return value
            time.sleep(_POLL_INTERVAL_SECONDS)
        return value

    def _drive() -> None:
        try:
            _poll_until(_INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(_set_field("d", "3"))
            window.evaluate_js(
                "document.querySelector("
                '\'input[name="m_mode"][value="matrix"]\').click();'
            )
            settled = _poll_until(
                "({"
                "rowCount: document.querySelectorAll("
                "'#m-matrix-grid tbody tr').length, "
                "cellValues: Array.from("
                "document.querySelectorAll('#m-matrix-grid .matrix-cell')"
                ").map((cell) => cell.value), "
                "matrixJson: document.getElementById('field-m_matrix_json').value"
                "})",
                lambda value: value is not None and value["rowCount"] > 0,
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=10)

    assert settled["rowCount"] == 3
    assert settled["cellValues"] == ["1", "0", "0", "0", "1", "0", "0", "0", "1"]
    assert settled["matrixJson"] == "[[1,0,0],[0,1,0],[0,0,1]]"


def test_editing_a_cell_updates_the_row_sum_and_flags_an_invalid_row(
    window: webview.Window,
) -> None:
    """Typing into a cell recomputes that row's own sum and warns when it isn't 1."""
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def _poll_until(script: str, predicate: Callable[[Any], bool]) -> Any:
        value = None
        for _ in range(_POLL_ATTEMPTS):
            value = window.evaluate_js(script)
            if predicate(value):
                return value
            time.sleep(_POLL_INTERVAL_SECONDS)
        return value

    def _drive() -> None:
        try:
            _poll_until(_INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(_set_field("d", "2"))
            window.evaluate_js(
                "document.querySelector("
                '\'input[name="m_mode"][value="matrix"]\').click();'
            )
            _poll_until(
                "document.querySelectorAll('#m-matrix-grid tbody tr').length",
                lambda value: value == 2,
            )
            window.evaluate_js(
                "const cells = document.querySelectorAll("
                "'#m-matrix-grid .matrix-cell'); "
                "cells[0].value = '0.9'; "
                "cells[1].value = '0.2'; "
                "cells[1].dispatchEvent(new Event('input', {bubbles: true}));"
            )
            settled = _poll_until(
                "({"
                "rowSumText: document.querySelector("
                "'#m-matrix-grid .matrix-row-sum').textContent, "
                "rowSumInvalid: document.querySelector("
                "'#m-matrix-grid .matrix-row-sum').classList.contains("
                "'matrix-row-sum-invalid'), "
                "matrixJson: document.getElementById('field-m_matrix_json').value"
                "})",
                lambda value: value is not None and value["rowSumText"] != "1.000",
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=10)

    assert settled["rowSumText"] == "1.100"
    assert settled["rowSumInvalid"] is True
    assert settled["matrixJson"].startswith("[[0.9,0.2]")


def test_changing_d_resizes_the_grid_preserving_existing_values(
    window: webview.Window,
) -> None:
    """Growing `d` while matrix mode is active adds rows/columns without losing data."""
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def _poll_until(script: str, predicate: Callable[[Any], bool]) -> Any:
        value = None
        for _ in range(_POLL_ATTEMPTS):
            value = window.evaluate_js(script)
            if predicate(value):
                return value
            time.sleep(_POLL_INTERVAL_SECONDS)
        return value

    def _drive() -> None:
        try:
            _poll_until(_INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(_set_field("d", "2"))
            window.evaluate_js(
                "document.querySelector("
                '\'input[name="m_mode"][value="matrix"]\').click();'
            )
            _poll_until(
                "document.querySelectorAll('#m-matrix-grid tbody tr').length",
                lambda value: value == 2,
            )
            window.evaluate_js(
                "const cells = document.querySelectorAll("
                "'#m-matrix-grid .matrix-cell'); "
                "cells[0].value = '0.7'; "
                "cells[0].dispatchEvent(new Event('input', {bubbles: true}));"
            )
            window.evaluate_js(_set_field("d", "3"))
            settled = _poll_until(
                "({"
                "rowCount: document.querySelectorAll("
                "'#m-matrix-grid tbody tr').length, "
                "firstCellValue: document.querySelector("
                "'#m-matrix-grid .matrix-cell').value"
                "})",
                lambda value: value is not None and value["rowCount"] == 3,
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=10)

    assert settled["rowCount"] == 3
    assert settled["firstCellValue"] == "0.7"


def test_a_real_run_with_a_hand_edited_matrix_completes() -> None:
    """A run submitted with a hand-edited full matrix actually completes.

    Same event-driven "wait on a real `threading.Event`, never poll a
    live background run" shape `test_running_screen.py`'s own real-run
    tests already use, for the identical reason those tests' own
    docstrings record.
    """
    started_event = threading.Event()
    done_event = threading.Event()
    messages: list[RunMessage | BatchMessage] = []

    def on_run_started() -> None:
        started_event.set()

    def on_message(message: RunMessage | BatchMessage) -> None:
        messages.append(message)
        if message[0] in ("done", "cancelled", "error"):
            done_event.set()

    window = create_window(
        api=Api(on_run_started=on_run_started, on_message=on_message), hidden=True
    )
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _poll_until(script: str, predicate: Callable[[Any], bool]) -> Any:
        value = None
        for _ in range(_POLL_ATTEMPTS):
            value = window.evaluate_js(script)
            if predicate(value):
                return value
            time.sleep(_POLL_INTERVAL_SECONDS)
        return value

    def _drive() -> None:
        try:
            _poll_until(_INPUT_SCREEN_READY, lambda value: value is True)
            window.evaluate_js(_set_field("N", "20"))
            window.evaluate_js(_set_field("d", "2"))
            window.evaluate_js(_set_field("seed", "20260814"))
            window.evaluate_js(_set_field("mu_value", "0.01"))
            window.evaluate_js(_set_field("locus_lengths", "200"))
            window.evaluate_js(_set_field("convergence_window", "4"))
            window.evaluate_js(_set_field("convergence_tolerance", "1.0"))
            window.evaluate_js(_set_field("max_generations", "10"))
            window.evaluate_js(_set_field("n_replicates", "1"))
            window.evaluate_js(
                "document.querySelector("
                '\'input[name="m_mode"][value="matrix"]\').click();'
            )
            _poll_until(
                "document.querySelectorAll('#m-matrix-grid tbody tr').length",
                lambda value: value == 2,
            )
            window.evaluate_js(
                "const cells = document.querySelectorAll("
                "'#m-matrix-grid .matrix-cell'); "
                "cells[0].value = '0.9'; cells[1].value = '0.1'; "
                "cells[2].value = '0.1'; cells[3].value = '0.9'; "
                "cells[3].dispatchEvent(new Event('input', {bubbles: true}));"
            )
            window.evaluate_js("document.getElementById('run-button').click();")
            settled = None
            if done_event.wait(timeout=30.0):
                settled = window.evaluate_js(
                    "({"
                    "runViewState: window.fim.getRunViewState(), "
                    "outcomeText: document.getElementById('results-outcome')"
                    ".textContent"
                    "})"
                )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=40.0)

    assert settled is not None, (
        f"done_event was never set (start_run called: "
        f"{started_event.is_set()}, messages: {messages!r})"
    )
    assert settled["runViewState"] == "completed"
    assert settled["outcomeText"] != ""
    assert messages[-1][0] == "done"
