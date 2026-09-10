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
            # there. Each `<li>`'s own first button specifically (not a
            # flat index into every button on the page): a "View YAML"
            # button now sits beside each title button too, so a flat
            # `querySelectorAll('#presets-list button')[1]` no longer
            # names the second preset's own title at all -- it names the
            # *first* preset's own "View YAML" button instead (a real
            # regression this exact fix closes, caught live by this
            # test itself failing the moment that button was added).
            window.evaluate_js(
                "document.querySelector("
                "'#presets-list li:nth-child(2) button:first-child')"
                ".click();"
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
            # A real, previously-reproduced regression this reset closes:
            # `window.__fimPresetsListReady` was already `true` from the
            # initial `loadExample()` call above, well before this
            # click -- polling for it to become `true` again *without*
            # first setting it back to `false` here risks reading that
            # stale, already-`true` value on the very first poll
            # attempt, before `savePresetForm`'s own submit handler has
            # even started its own async `refreshPresetsList()` call
            # (`presets.js`'s own docstring on the flag has the full
            # mechanism: the save dialog closes *before* the list
            # refresh completes, so polling "is the dialog closed"
            # alone — this test's own original shape — is exactly this
            # same race, one layer up).
            window.evaluate_js(
                "window.__fimPresetsListReady = false;"
                "document.getElementById('save-preset-name').value = "
                "'My saved scenario';"
                "document.getElementById('save-preset-accept-button').click();"
            )
            after_save = _poll_until(
                "({"
                "saveDialogOpen: "
                "document.getElementById('modal-save-preset').open, "
                "listReady: window.__fimPresetsListReady === true, "
                "titles: Array.from("
                "document.querySelectorAll('#presets-list li > button:first-child')"
                ").map((button) => button.textContent), "
                "deleteButtonCount: "
                "document.querySelectorAll('.presets-delete-button').length"
                "})",
                lambda value: (
                    value is not None
                    and value["saveDialogOpen"] is False
                    and value["listReady"] is True
                ),
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


def test_view_yaml_shows_the_chosen_presets_own_text(window: webview.Window) -> None:
    """ "View YAML" opens `modal-preset-yaml` with that preset's own title and text.

    Botanist GUI design doc §10: the examples library's plain-text half.
    `test_app_api.py`'s own `test_get_preset_yaml_*` tests already prove
    `Api.get_preset_yaml` itself is correct; this proves the page's own
    JavaScript calls it at the right moment and shows what it returns.
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
            first_title = window.evaluate_js(
                "document.querySelector("
                "'#presets-list li:first-child button:first-child')"
                ".textContent"
            )
            window.evaluate_js(
                "document.querySelector("
                "'#presets-list li:first-child .presets-view-yaml-button')"
                ".click();"
            )
            settled = _poll_until(
                "({"
                "presetsDialogOpen: document.getElementById('modal-presets').open, "
                "yamlDialogOpen: "
                "document.getElementById('modal-preset-yaml').open, "
                "yamlTitle: "
                "document.getElementById('preset-yaml-title').textContent, "
                "yamlTextLength: "
                "document.getElementById('preset-yaml-text').value.length"
                "})",
                lambda value: value is not None and value["yamlDialogOpen"] is True,
            )
            outcome.put({"firstTitle": first_title, "settled": settled})
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=10)

    # Both dialogs open at once (a stacked native `<dialog>`, not a
    # replacement) -- "View YAML" is a detail view reachable *from* the
    # picker, not a navigation away from it.
    assert result["settled"]["presetsDialogOpen"] is True
    assert result["settled"]["yamlDialogOpen"] is True
    assert result["settled"]["yamlTitle"] == result["firstTitle"]
    assert result["settled"]["yamlTextLength"] > 0


def test_copy_to_clipboard_writes_the_shown_yaml_text(window: webview.Window) -> None:
    """ "Copy to clipboard" writes exactly the text currently shown, once.

    Stubs `navigator.clipboard.writeText` with a spy before clicking,
    rather than letting the real button reach the real OS clipboard
    (confirmed live, before this test was written, that a real
    `pywebview` window's own `navigator.clipboard.writeText` genuinely
    writes to and is readable back from the real system pasteboard —
    exactly the side effect on a developer's own machine a test must
    never cause).
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
                "window.__fimClipboardCalls = []; "
                "navigator.clipboard.writeText = (text) => { "
                "window.__fimClipboardCalls.push(text); "
                "return Promise.resolve(); "
                "};"
            )
            window.evaluate_js(
                "setTimeout(() => { window.fim.menu.loadExample(); }, 0);"
            )
            _poll_until(
                "document.getElementById('presets-list').children.length",
                lambda value: value is not None and value > 0,
            )
            window.evaluate_js(
                "document.querySelector("
                "'#presets-list li:first-child .presets-view-yaml-button')"
                ".click();"
            )
            _poll_until(
                "window.__fimPresetYamlReady === true", lambda value: value is True
            )
            shown_text = window.evaluate_js(
                "document.getElementById('preset-yaml-text').value"
            )
            window.evaluate_js(
                "document.getElementById('preset-yaml-copy-button').click();"
            )
            settled = _poll_until(
                "({"
                "copyReady: window.__fimPresetYamlCopyReady === true, "
                "copiedNoteHidden: "
                "document.getElementById('preset-yaml-copied-note').hidden, "
                "calls: window.__fimClipboardCalls"
                "})",
                lambda value: value is not None and value["copyReady"] is True,
            )
            outcome.put({"shownText": shown_text, "settled": settled})
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=10)

    assert result["settled"]["calls"] == [result["shownText"]]
    assert result["settled"]["copiedNoteHidden"] is False


def test_duplicate_current_configuration_button_starts_disabled(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """ "Duplicate current configuration" has nothing to fork before a preset loads."""
    settled = drive(
        window,
        ready=_INPUT_SCREEN_READY,
        trigger="null",
        read="document.getElementById('configure-duplicate-preset-button').disabled",
        is_ready=lambda value: value is not None,
    )

    assert settled is True


def test_loading_a_preset_enables_duplicate_and_saving_it_creates_a_new_preset(
    window: webview.Window,
) -> None:
    """Loading a preset enables "Duplicate…"; using it saves a prefilled-name copy.

    Botanist GUI design doc §4.5: "Duplicate current configuration...
    so sweeping one parameter across several runs starts from
    'everything held fixed' rather than from scratch each time." Driven
    manually (`window.fim.menu.loadExample`/`newConfiguration` are both
    `async` -- `test_input_screen.py`'s own docstring on why a bare
    `evaluate_js` call to an `async` `fim.menu.*` method deadlocks).
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
            first_title = window.evaluate_js(
                "document.querySelector("
                "'#presets-list li:first-child button:first-child')"
                ".textContent"
            )
            window.evaluate_js(
                "document.querySelector("
                "'#presets-list li:first-child button:first-child')"
                ".click();"
            )
            _poll_until(
                "document.getElementById('configure-duplicate-preset-button')"
                ".disabled === false",
                lambda value: value is True,
            )
            window.evaluate_js(
                "document.getElementById('configure-duplicate-preset-button').click();"
            )
            _poll_until(
                "document.getElementById('modal-save-preset').open",
                lambda value: value is True,
            )
            prefilled_name = window.evaluate_js(
                "document.getElementById('save-preset-name').value"
            )
            window.evaluate_js(
                "window.__fimPresetsListReady = false;"
                "document.getElementById('save-preset-accept-button').click();"
            )
            _poll_until(
                "window.__fimPresetsListReady === true"
                " || !document.getElementById('modal-save-preset').open",
                lambda value: value is True,
            )
            window.evaluate_js(
                "window.__fimDuplicateSaveResult = null;"
                "(async () => { window.__fimDuplicateSaveResult = "
                "await window.pywebview.api.list_presets(); })();"
            )
            list_result = _poll_until(
                "window.__fimDuplicateSaveResult",
                lambda value: value is not None,
            )
            outcome.put(
                {
                    "firstTitle": first_title,
                    "prefilledName": prefilled_name,
                    "userPresetTitles": [
                        preset["title"]
                        for preset in list_result["presets"]
                        if not preset["builtin"]
                    ],
                }
            )
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=10)

    assert result["prefilledName"] == f"{result['firstTitle']} copy"
    assert result["userPresetTitles"] == [f"{result['firstTitle']} copy"]


def test_new_configuration_disables_duplicate_button_again(
    window: webview.Window,
) -> None:
    """An explicit reset to starter values has nothing left to fork.

    `screens/presets.js`'s own `lastLoadedPresetTitle` docstring: an
    explicit "New configuration" is not "the loaded preset, plus edits"
    any more.
    """
    outcome: queue.Queue[bool | None] = queue.Queue(maxsize=1)

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
                "document.querySelector("
                "'#presets-list li:first-child button:first-child')"
                ".click();"
            )
            _poll_until(
                "document.getElementById('configure-duplicate-preset-button')"
                ".disabled === false",
                lambda value: value is True,
            )
            window.evaluate_js(
                "window.__fimRunViewReady = false;"
                "setTimeout(() => { window.fim.menu.newConfiguration(); }, 0);"
            )
            settled = _poll_until(
                "window.__fimRunViewReady === true ? "
                "document.getElementById('configure-duplicate-preset-button')"
                ".disabled : null",
                lambda value: value is not None,
            )
            outcome.put(settled)
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=10)

    assert settled is True
