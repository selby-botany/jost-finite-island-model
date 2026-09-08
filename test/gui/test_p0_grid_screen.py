"""Headless functional tests for the explicit p_0 grid editor
(botanist GUI design doc `20260907-claude-sonnet-5-botanist-gui-redesign.md`
§4.4).

Real DOM-driven proof that `webui/screens/p0-grid.js` actually builds,
resizes, and reads back a real grid of per-cell allele-frequency
mappings — `test/gui/test_config_form.py`'s own
`test_initial_conditions_to_payload_explicit_p0_*`/
`test_initial_conditions_from_params_explicit_p0_*` tests already prove
`initial_conditions_to_payload`/`initial_conditions_from_params`
correct as plain Python calls; these tests prove the page's own
JavaScript builds the grid those functions actually read from and
write to, which no Python-only test can check. Mirrors
`test_migration_matrix_screen.py`'s own shape exactly.
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


def test_selecting_explicit_p0_mode_builds_a_default_grid_matching_d_and_loci(
    window: webview.Window,
) -> None:
    """Switching to explicit-p0 mode with no prior `p_0` builds a d-by-locus grid."""
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
                '\'input[name="initial_conditions_mode"]'
                '[value="explicit_p0"]\').click();'
            )
            settled = _poll_until(
                "({"
                "rowCount: document.querySelectorAll("
                "'#p0-grid tbody tr').length, "
                "colCount: document.querySelectorAll("
                "'#p0-grid thead th').length - 1, "
                "cellValues: Array.from("
                "document.querySelectorAll('#p0-grid .p0-cell')"
                ").map((cell) => cell.value), "
                "p0Json: document.getElementById('field-p0_json').value"
                "})",
                lambda value: value is not None and value["rowCount"] > 0,
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=10)

    assert settled["rowCount"] == 3
    assert settled["colCount"] == 1
    assert settled["cellValues"] == ["0:1", "0:1", "0:1"]
    assert settled["p0Json"] == '[[{"0":1}],[{"0":1}],[{"0":1}]]'


def test_editing_a_cell_updates_its_own_sum_and_flags_an_invalid_cell(
    window: webview.Window,
) -> None:
    """Typing into a cell recomputes that cell's own sum and warns when it isn't 1."""
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
                '\'input[name="initial_conditions_mode"]'
                '[value="explicit_p0"]\').click();'
            )
            _poll_until(
                "document.querySelectorAll('#p0-grid tbody tr').length",
                lambda value: value == 2,
            )
            window.evaluate_js(
                "const cell = document.querySelector('#p0-grid .p0-cell'); "
                "cell.value = '0:0.5,1:0.3'; "
                "cell.dispatchEvent(new Event('input', {bubbles: true}));"
            )
            settled = _poll_until(
                "({"
                "cellSumText: document.querySelector("
                "'#p0-grid .p0-cell-sum').textContent, "
                "cellSumInvalid: document.querySelector("
                "'#p0-grid .p0-cell-sum').classList.contains("
                "'p0-cell-sum-invalid'), "
                "p0Json: document.getElementById('field-p0_json').value"
                "})",
                lambda value: value is not None and value["cellSumText"] != "",
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=10)

    assert settled["cellSumText"] == "0.800"
    assert settled["cellSumInvalid"] is True
    assert settled["p0Json"].startswith('[[{"0":0.5,"1":0.3}]')


def test_changing_d_resizes_the_grid_preserving_existing_values(
    window: webview.Window,
) -> None:
    """Growing `d` while explicit mode is active adds rows without losing data."""
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
                '\'input[name="initial_conditions_mode"]'
                '[value="explicit_p0"]\').click();'
            )
            _poll_until(
                "document.querySelectorAll('#p0-grid tbody tr').length",
                lambda value: value == 2,
            )
            window.evaluate_js(
                "const cell = document.querySelector('#p0-grid .p0-cell'); "
                "cell.value = '0:0.4,1:0.6'; "
                "cell.dispatchEvent(new Event('input', {bubbles: true}));"
            )
            window.evaluate_js(_set_field("d", "3"))
            settled = _poll_until(
                "({"
                "rowCount: document.querySelectorAll("
                "'#p0-grid tbody tr').length, "
                "firstCellValue: document.querySelector("
                "'#p0-grid .p0-cell').value"
                "})",
                lambda value: value is not None and value["rowCount"] == 3,
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=10)

    assert settled["rowCount"] == 3
    assert settled["firstCellValue"] == "0:0.4,1:0.6"


def test_adding_a_custom_locus_grows_the_p0_grids_own_columns(
    window: webview.Window,
) -> None:
    """Adding a row to the custom loci grid grows p_0's own column count to match."""
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
            window.evaluate_js(_set_field("d", "1"))
            window.evaluate_js(
                "document.querySelector("
                '\'input[name="initial_conditions_mode"]'
                '[value="explicit_p0"]\').click();'
            )
            _poll_until(
                "document.querySelectorAll('#p0-grid thead th').length - 1",
                lambda value: value == 1,
            )
            window.evaluate_js(
                "document.querySelector("
                '\'input[name="loci_mode"][value="custom"]\').click();'
            )
            _poll_until(
                "document.querySelectorAll('#loci-grid tbody tr').length",
                lambda value: value == 1,
            )
            window.evaluate_js(
                "document.getElementById('loci-add-row-button').click();"
            )
            settled = _poll_until(
                "({"
                "lociRowCount: document.querySelectorAll("
                "'#loci-grid tbody tr').length, "
                "p0ColCount: document.querySelectorAll("
                "'#p0-grid thead th').length - 1"
                "})",
                lambda value: value is not None and value["lociRowCount"] == 2,
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=10)

    assert settled["lociRowCount"] == 2
    assert settled["p0ColCount"] == 2


def test_a_real_run_with_a_hand_edited_p0_completes() -> None:
    """A run submitted with a hand-edited explicit p_0 actually completes.

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
                '\'input[name="initial_conditions_mode"]'
                '[value="explicit_p0"]\').click();'
            )
            _poll_until(
                "document.querySelectorAll('#p0-grid tbody tr').length",
                lambda value: value == 2,
            )
            window.evaluate_js(
                "const cells = document.querySelectorAll('#p0-grid .p0-cell'); "
                "cells[0].value = '0:1'; "
                "cells[1].value = '0:0.5,1:0.5'; "
                "cells[1].dispatchEvent(new Event('input', {bubbles: true}));"
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
