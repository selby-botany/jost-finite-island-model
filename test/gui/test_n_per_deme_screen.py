"""Headless functional tests for the per-deme population-size grid editor
(botanist GUI design doc `20260907-claude-sonnet-5-botanist-gui-redesign.md`
§4.1, §4.4).

Real DOM-driven proof that `webui/screens/n-per-deme.js` actually builds,
seeds, resizes, and reads back a real grid of per-deme `N` values —
`test/gui/test_config_form.py`'s own `test_form_values_to_payload_accepts_
a_per_deme_n_list` already proves the server side accepts the comma-
separated shape this grid writes into `field-N`; these tests prove the
page's own JavaScript builds that shape correctly, which no Python-only
test can check. Mirrors `test_migration_matrix_screen.py`'s/`test_loci_
grid_screen.py`'s own shape exactly.
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


def test_switching_to_per_deme_mode_seeds_the_grid_from_the_scalar(
    window: webview.Window,
) -> None:
    """Switching to per-deme mode replicates the current scalar N across every row."""
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
            starter_n = window.evaluate_js("document.getElementById('field-N').value")
            starter_d = int(
                window.evaluate_js("document.getElementById('field-d').value")
            )
            window.evaluate_js(
                "document.querySelector("
                '\'input[name="n_mode"][value="per_deme"]\').click();'
            )
            settled = _poll_until(
                "({"
                "rowCount: document.querySelectorAll("
                "'#n-per-deme-grid tbody tr').length, "
                "fieldN: document.getElementById('field-N').value, "
                "sameHidden: document.getElementById('n-same-fields').hidden, "
                "perDemeHidden: document.getElementById('n-per-deme-fields').hidden"
                "})",
                lambda value: value is not None and value["rowCount"] > 0,
            )
            outcome.put({**settled, "starterN": starter_n, "starterD": starter_d})
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=10)

    assert settled["rowCount"] == settled["starterD"]
    assert settled["fieldN"] == ", ".join([settled["starterN"]] * settled["starterD"])
    assert settled["sameHidden"] is True
    assert settled["perDemeHidden"] is False


def test_editing_a_per_deme_row_updates_field_n(window: webview.Window) -> None:
    """Editing one row's own value updates `field-N`'s comma-separated list."""
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
                '\'input[name="n_mode"][value="per_deme"]\').click();'
            )
            _poll_until(
                "document.querySelectorAll('#n-per-deme-grid tbody tr').length",
                lambda value: value == 3,
            )
            window.evaluate_js(
                "const cells = document.querySelectorAll('.n-per-deme-cell'); "
                "cells[1].value = '777'; "
                "cells[1].dispatchEvent(new Event('input', {bubbles: true}));"
            )
            settled = _poll_until(
                "document.getElementById('field-N').value",
                lambda value: value is not None and "777" in value,
            )
            outcome.put({"fieldN": settled})
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=10)

    assert settled["fieldN"].split(", ")[1] == "777"


def test_changing_d_resizes_the_grid_preserving_existing_values_by_position(
    window: webview.Window,
) -> None:
    """Growing/shrinking `d` resizes the grid, keeping already-entered values in place.

    The same by-position grow/shrink behavior `buildP0Grid`'s own resize
    already uses -- proven directly here since `n-per-deme.js` implements
    it independently (no shared helper between the two grids).
    """
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
            window.evaluate_js(_set_field("d", "4"))
            window.evaluate_js(
                "document.querySelector("
                '\'input[name="n_mode"][value="per_deme"]\').click();'
            )
            _poll_until(
                "document.querySelectorAll('#n-per-deme-grid tbody tr').length",
                lambda value: value == 4,
            )
            window.evaluate_js(
                "const cells = document.querySelectorAll('.n-per-deme-cell'); "
                "cells[0].value = '111'; "
                "cells[0].dispatchEvent(new Event('input', {bubbles: true}));"
            )
            _poll_until(
                "document.getElementById('field-N').value",
                lambda value: value is not None and value.startswith("111"),
            )
            window.evaluate_js(_set_field("d", "2"))
            settled = _poll_until(
                "({"
                "rowCount: document.querySelectorAll("
                "'#n-per-deme-grid tbody tr').length, "
                "fieldN: document.getElementById('field-N').value"
                "})",
                lambda value: value is not None and value["rowCount"] == 2,
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=10)

    assert settled["rowCount"] == 2
    assert settled["fieldN"].split(", ")[0] == "111"


def test_a_real_run_with_distinct_per_deme_n_values_completes() -> None:
    """A run submitted with genuinely different per-deme N values actually completes.

    Same event-driven "wait on a real `threading.Event`, never poll a
    live background run" shape `test_loci_grid_screen.py`'s own real-run
    test uses, for the identical reason its own docstring records.
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
            window.evaluate_js(_set_field("d", "2"))
            window.evaluate_js(_set_field("seed", "20260814"))
            window.evaluate_js(_set_field("mu_value", "0.01"))
            window.evaluate_js(_set_field("convergence_window", "4"))
            window.evaluate_js(_set_field("convergence_tolerance", "1.0"))
            window.evaluate_js(_set_field("max_generations", "10"))
            window.evaluate_js(_set_field("n_replicates", "1"))
            window.evaluate_js(
                "document.querySelector("
                '\'input[name="n_mode"][value="per_deme"]\').click();'
            )
            _poll_until(
                "document.querySelectorAll('#n-per-deme-grid tbody tr').length",
                lambda value: value == 2,
            )
            window.evaluate_js(
                "const cells = document.querySelectorAll('.n-per-deme-cell'); "
                "cells[0].value = '15'; "
                "cells[0].dispatchEvent(new Event('input', {bubbles: true})); "
                "cells[1].value = '25'; "
                "cells[1].dispatchEvent(new Event('input', {bubbles: true}));"
            )
            _poll_until(
                "document.getElementById('field-N').value",
                lambda value: value == "15, 25",
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
