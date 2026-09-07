"""Headless functional tests for the Presets picker (botanist GUI design
doc `20260907-claude-sonnet-5-botanist-gui-redesign.md` §4.5).

Real DOM-driven proof that `webui/screens/presets.js` actually wires the
page correctly — `test/gui/test_app_api.py`'s own `test_list_presets_*`/
`test_get_preset_form_values_*` tests already prove the bridge methods
themselves are correct as plain Python calls; these tests prove the
page's own JavaScript calls them at the right moments and updates the
right fields, which no Python-only test can check.
"""

from __future__ import annotations

import queue
import time
from collections.abc import Callable
from typing import Any

import pytest
import webview

pytestmark = pytest.mark.gui

_POLL_ATTEMPTS = 200
_POLL_INTERVAL_SECONDS = 0.1
_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"


def test_load_example_populates_the_list_and_applies_the_chosen_preset(
    window: webview.Window,
) -> None:
    """`fim.menu.loadExample` lists every preset; clicking one loads its own values.

    The trigger wraps `fim.menu.loadExample()` in `setTimeout(..., 0)`,
    matching `fim.gui.app._build_menu`'s own real dispatcher exactly —
    calling an `async` `fim.menu.*` method directly as a bare
    `evaluate_js` expression deadlocks (`test_input_screen.py`'s own
    `test_menu_new_configuration_resets_an_edited_field` docstring has
    the full mechanism).
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
            window.evaluate_js(
                "setTimeout(() => { window.fim.menu.loadExample(); }, 0);"
            )
            item_count = _poll_until(
                "document.getElementById('presets-list').children.length",
                lambda value: value is not None and value > 0,
            )
            # Click the second preset ("Stepping-stone (spatial)
            # migration", per `fim.gui.presets`' own document order) --
            # distinct from the starter form's own default N/d/m, so a
            # changed field afterward is real proof the click did
            # something, not a coincidental match with what was already
            # there.
            window.evaluate_js(
                "document.querySelectorAll('#presets-list button')[1].click();"
            )
            settled = _poll_until(
                "({"
                "dialogOpen: document.getElementById('modal-presets').open, "
                "mMode: document.querySelector("
                "'input[name=\"m_mode\"]:checked').value, "
                "nValue: document.getElementById('field-N').value, "
                "matrixFieldsHidden: "
                "document.getElementById('m-matrix-fields').hidden, "
                "matrixRowCount: "
                "document.querySelectorAll('#m-matrix-grid tbody tr').length, "
                "firstCellValue: "
                "document.querySelector('#m-matrix-grid .matrix-cell').value"
                "})",
                lambda value: value is not None and value["dialogOpen"] is False,
            )
            outcome.put({"itemCount": item_count, "settled": settled})
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=10)

    assert result["itemCount"] > 0
    assert result["settled"]["dialogOpen"] is False
    assert result["settled"]["mMode"] == "matrix"
    assert result["settled"]["nValue"] == "150"
    # The stepping-stone preset's own d=6 ring matrix, real values in a
    # real, rendered, editable grid — not a read-only "loaded" badge.
    assert result["settled"]["matrixFieldsHidden"] is False
    assert result["settled"]["matrixRowCount"] == 6
    assert result["settled"]["firstCellValue"] != ""


def test_save_current_as_preset_then_delete_it(window: webview.Window) -> None:
    """ "Save current as…" adds a real, listed, loadable, deletable preset.

    One `webview.start()` call driving several sequential trigger-then-
    poll stages against the same window (`test_help_screen.py`'s own
    precedent for why: more than one round trip against a single window
    needs a manual driver, not the `drive` fixture, which destroys its
    window after one).
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
            window.evaluate_js(
                "setTimeout(() => { window.fim.menu.loadExample(); }, 0);"
            )
            _poll_until(
                "document.getElementById('presets-list').children.length",
                lambda value: value is not None and value > 0,
            )
            window.evaluate_js(
                "document.getElementById('save-current-as-preset-button').click();"
            )
            _poll_until(
                "document.getElementById('modal-save-preset').open",
                lambda value: value is True,
            )
            window.evaluate_js(
                "document.getElementById('save-preset-name').value = "
                "'My saved scenario';"
                "document.getElementById('save-preset-accept-button').click();"
            )
            after_save = _poll_until(
                "({"
                "saveDialogOpen: "
                "document.getElementById('modal-save-preset').open, "
                "titles: Array.from("
                "document.querySelectorAll('#presets-list li > button:first-child')"
                ").map((button) => button.textContent), "
                "deleteButtonCount: "
                "document.querySelectorAll('.presets-delete-button').length"
                "})",
                lambda value: value is not None and value["saveDialogOpen"] is False,
            )
            window.evaluate_js(
                "document.querySelector('.presets-delete-button').click();"
            )
            after_delete = _poll_until(
                "document.querySelectorAll('.presets-delete-button').length",
                lambda value: value == 0,
            )
            outcome.put({"afterSave": after_save, "afterDeleteCount": after_delete})
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=10)

    assert "My saved scenario" in result["afterSave"]["titles"]
    assert result["afterSave"]["deleteButtonCount"] == 1
    assert result["afterDeleteCount"] == 0


def test_save_current_as_preset_shows_a_validation_error_without_closing(
    window: webview.Window,
) -> None:
    """An invalid current form's own error shows in the dialog, which stays open."""
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
                "const field = document.getElementById('field-N'); "
                "field.value = 'not-a-number'; "
                "field.dispatchEvent(new Event('input', {bubbles: true}));"
            )
            window.evaluate_js(
                "setTimeout(() => { window.fim.menu.loadExample(); }, 0);"
            )
            _poll_until(
                "document.getElementById('presets-list').children.length",
                lambda value: value is not None and value > 0,
            )
            window.evaluate_js(
                "document.getElementById('save-current-as-preset-button').click();"
            )
            _poll_until(
                "document.getElementById('modal-save-preset').open",
                lambda value: value is True,
            )
            window.evaluate_js(
                "document.getElementById('save-preset-name').value = 'Broken';"
                "document.getElementById('save-preset-accept-button').click();"
            )
            settled = _poll_until(
                "({"
                "errorHidden: "
                "document.getElementById('save-preset-error').hidden, "
                "dialogOpen: "
                "document.getElementById('modal-save-preset').open"
                "})",
                lambda value: value is not None and value["errorHidden"] is False,
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=10)

    assert settled["errorHidden"] is False
    assert settled["dialogOpen"] is True
