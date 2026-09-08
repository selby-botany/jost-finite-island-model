"""Headless functional tests for the custom-locus-ID grid editor
(botanist GUI design doc `20260907-claude-sonnet-5-botanist-gui-redesign.md`
§4.4).

Real DOM-driven proof that `webui/screens/loci-grid.js` actually builds,
grows, shrinks, and reads back a real grid of `(locus ID, length)` rows —
`test/gui/test_config_form.py`'s own `test_loci_to_payload_*`/
`test_loci_from_params_*` tests already prove `loci_to_payload`/
`loci_from_params` correct as plain Python calls; these tests prove the
page's own JavaScript builds the grid those functions actually read from
and write to, which no Python-only test can check. Mirrors
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


def test_selecting_custom_mode_builds_a_single_default_row(
    window: webview.Window,
) -> None:
    """Switching to custom mode with no prior `loci` builds one default row."""
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
            window.evaluate_js(
                "document.querySelector("
                '\'input[name="loci_mode"][value="custom"]\').click();'
            )
            settled = _poll_until(
                "({"
                "rowCount: document.querySelectorAll("
                "'#loci-grid tbody tr').length, "
                "idValue: document.querySelector('.loci-id-cell') "
                "&& document.querySelector('.loci-id-cell').value, "
                "lengthValue: document.querySelector('.loci-length-cell') "
                "&& document.querySelector('.loci-length-cell').value, "
                "lociJson: document.getElementById('field-loci_json').value"
                "})",
                lambda value: value is not None and value["rowCount"] > 0,
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=10)

    assert settled["rowCount"] == 1
    assert settled["idValue"] == "1"
    assert settled["lengthValue"] == "200"
    assert settled["lociJson"] == '[{"locus_id":1,"length":200}]'


def test_add_row_button_appends_the_next_sequential_locus_id(
    window: webview.Window,
) -> None:
    """ "Add locus" appends a row one past the highest already present."""
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
            window.evaluate_js(
                "document.querySelector("
                '\'input[name="loci_mode"][value="custom"]\').click();'
            )
            _poll_until(
                "document.querySelectorAll('#loci-grid tbody tr').length",
                lambda value: value == 1,
            )
            window.evaluate_js(
                "document.querySelector('.loci-id-cell').value = '5'; "
                "document.querySelector('.loci-id-cell')"
                ".dispatchEvent(new Event('input', {bubbles: true}));"
            )
            window.evaluate_js(
                "document.getElementById('loci-add-row-button').click();"
            )
            settled = _poll_until(
                "({"
                "rowCount: document.querySelectorAll("
                "'#loci-grid tbody tr').length, "
                "idValues: Array.from("
                "document.querySelectorAll('.loci-id-cell')"
                ").map((cell) => cell.value), "
                "lociJson: document.getElementById('field-loci_json').value"
                "})",
                lambda value: value is not None and value["rowCount"] == 2,
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=10)

    assert settled["rowCount"] == 2
    assert settled["idValues"] == ["5", "6"]
    assert settled["lociJson"] == (
        '[{"locus_id":5,"length":200},{"locus_id":6,"length":200}]'
    )


def test_remove_row_leaves_at_least_one_row(window: webview.Window) -> None:
    """Removing rows stops at one -- a `loci` list can never submit empty."""
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
            window.evaluate_js(
                "document.querySelector("
                '\'input[name="loci_mode"][value="custom"]\').click();'
            )
            _poll_until(
                "document.querySelectorAll('#loci-grid tbody tr').length",
                lambda value: value == 1,
            )
            window.evaluate_js(
                "document.querySelector('#loci-grid tbody tr button').click();"
            )
            # Give any (incorrect) removal a moment to happen before
            # asserting it did not.
            time.sleep(0.2)
            settled = window.evaluate_js(
                "({rowCount: document.querySelectorAll('#loci-grid tbody tr').length})"
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=10)

    assert settled["rowCount"] == 1


def test_a_real_run_with_custom_nonsequential_locus_ids_completes() -> None:
    """A run submitted with custom, non-sequential locus IDs actually completes.

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
            window.evaluate_js(_set_field("convergence_window", "4"))
            window.evaluate_js(_set_field("convergence_tolerance", "1.0"))
            window.evaluate_js(_set_field("max_generations", "10"))
            window.evaluate_js(_set_field("n_replicates", "1"))
            window.evaluate_js(
                "document.querySelector("
                '\'input[name="loci_mode"][value="custom"]\').click();'
            )
            _poll_until(
                "document.querySelectorAll('#loci-grid tbody tr').length",
                lambda value: value == 1,
            )
            window.evaluate_js(
                "document.querySelector('.loci-id-cell').value = '10'; "
                "document.querySelector('.loci-length-cell').value = '50'; "
                "document.querySelector('.loci-length-cell')"
                ".dispatchEvent(new Event('input', {bubbles: true}));"
            )
            window.evaluate_js(
                "document.getElementById('loci-add-row-button').click();"
            )
            _poll_until(
                "document.querySelectorAll('#loci-grid tbody tr').length",
                lambda value: value == 2,
            )
            window.evaluate_js(
                "const lengthCells = document.querySelectorAll("
                "'.loci-length-cell'); "
                "lengthCells[1].value = '75'; "
                "lengthCells[1].dispatchEvent(new Event('input', {bubbles: true}));"
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
