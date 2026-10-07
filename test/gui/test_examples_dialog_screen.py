"""Headless functional tests for the Examples dialog (`screens/examples.js`).

Design doc `20261005-claude-opus-5-5-read-only-examples-and-classes-
design.md` §5, `selby/restricted`: Configure's "Examples…" button opens a
modal with the class tree on the left and the selected class's examples
on the right; "Load into Configure" (or Return) applies the example to
the form and fills the Run name/description boxes.

`test_app_api.py` already proves `Api.list_examples`/`Api.load_example`
as plain Python calls; these tests prove the page wires them: keyboard
navigation through the real catalog's tree and list, the load itself,
and the dialog's fit inside the 900x700 test window. Expected positions
are computed from the bundled catalog, not hard-coded, so assigning
classes to the examples changes the route the keys take, not the test.
"""

from __future__ import annotations

import queue
from collections.abc import Callable
from functools import partial
from typing import Any

import pytest
import webview

from fim.gui import app as app_module
from fim.gui import presets as presets_module
from fim.gui.app import await_bridge_threads

from .conftest import poll_page

pytestmark = pytest.mark.gui

_INPUT_SCREEN_READY = "window.__fimRunViewReady === true"
_DIALOG_READY = "window.__fimExamplesDialogReady === true"
_TARGET_EXAMPLE_ID = "stepping-stone-spatial-migration"
# Every bundled example with a configuration now loads, so the unloadable
# case is made by refusing this one (`_refuse_example`), with the form's
# own wording for a construct it cannot show.
_UNLOADABLE_EXAMPLE_ID = "per-base-mutation-rate-across-unequal-locus-lengths"
_REFUSAL = (
    "this configuration uses a different mu for each locus that no single "
    "per-base rate (mu_b) produces; edit the YAML file directly — the form "
    "only edits a single shared mu or mu_b"
)

_OPEN_DIALOG = (
    "window.fim.showConfigureScreen().then(() => "
    "document.getElementById('configure-examples-button').click());"
)


def _drive(
    window: webview.Window,
    steps: Callable[[Callable[[str, Callable[[Any], bool]], Any]], Any],
) -> Any:
    """Run `steps` against a ready `window` (`test_nav_rail.py`'s pattern).

    `steps` receives a `poll_until(script, predicate)` helper; its return
    value comes back through a queue, since the caller's thread cannot
    touch `window` directly. Bridge calls are settled before the window
    is destroyed, as in the `window` fixture's own teardown.
    """
    outcome: queue.Queue[Any] = queue.Queue(maxsize=1)

    def _run() -> None:
        try:
            poll_page(window, _INPUT_SCREEN_READY, lambda value: value is True)
            outcome.put(steps(partial(poll_page, window)))
        finally:
            await_bridge_threads()
            window.destroy()

    webview.start(_run)
    return outcome.get(timeout=10.0)


def _route_to(example_id: str) -> tuple[int, int, presets_module.Example]:
    """Return the tree position of the example's class, its list position, and it.

    Mirrors `screens/examples.js`: the tree lists each parent class, then
    its children; a class shows its own examples, then its children's,
    in catalog order.
    """
    catalog = presets_module.load_catalog(app_module._webui_directory())
    flat = [
        (entry, member)
        for parent in catalog.classes
        for entry, member in [
            (parent, {parent.class_id, *(child.class_id for child in parent.children)}),
            *((child, {child.class_id}) for child in parent.children),
        ]
    ]
    example = next(e for e in catalog.examples if e.example_id == example_id)
    tree_index = next(
        index
        for index, (entry, _members) in enumerate(flat)
        if entry.class_id == example.class_id
    )
    members = flat[tree_index][1]
    shown = [e.example_id for e in catalog.examples if e.class_id in members]
    return tree_index, shown.index(example_id), example


def _press(element_id: str, key: str, times: int = 1) -> str:
    """A script dispatching `key` to an element's keydown listener `times` times."""
    return (
        "(function(){"
        f"var target = document.getElementById({element_id!r});"
        f"for (var i = 0; i < {times}; i++) {{"
        f"target.dispatchEvent(new KeyboardEvent('keydown', "
        f"{{key: {key!r}, bubbles: true}}));"
        "}"
        "})();"
    )


def _selected(element_id: str, attribute: str) -> str:
    """A script reading the selected item's data attribute in a tree or list."""
    return (
        f"document.getElementById({element_id!r})"
        f".querySelector('[aria-selected=\"true\"]')?.dataset.{attribute} ?? null"
    )


def test_keyboard_navigation_loads_an_example_into_configure(
    window: webview.Window,
) -> None:
    """Arrow keys reach the example, Return loads it, and the Run boxes are filled."""
    tree_index, list_index, example = _route_to(_TARGET_EXAMPLE_ID)

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js(_OPEN_DIALOG)
        poll_until(_DIALOG_READY, lambda value: value is True)
        # The tree opens on its first class; walk down to the target's.
        window.evaluate_js(_press("examples-class-tree", "Home"))
        window.evaluate_js(_press("examples-class-tree", "ArrowDown", tree_index))
        selected_class = window.evaluate_js(_selected("examples-class-tree", "classId"))
        window.evaluate_js(_press("examples-class-tree", "ArrowRight"))
        window.evaluate_js(_press("examples-list", "Home"))
        window.evaluate_js(_press("examples-list", "ArrowDown", list_index))
        selected_example = window.evaluate_js(_selected("examples-list", "exampleId"))
        detail_name = window.evaluate_js(
            "document.getElementById('examples-detail-name').textContent"
        )
        window.evaluate_js("window.__fimExampleLoadSettled = false;")
        window.evaluate_js(_press("examples-list", "Enter"))
        poll_until(
            "window.__fimExampleLoadSettled === true "
            "&& (window.__fimValidationPending || 0) === 0",
            lambda value: value is True,
        )
        return {
            "selectedClass": selected_class,
            "selectedExample": selected_example,
            "detailName": detail_name,
            **window.evaluate_js(
                "({"
                "dialogOpen: document.getElementById('modal-examples').open, "
                "configureVisible: "
                "!document.getElementById('screen-configure').hidden, "
                "mMode: document.querySelector("
                "'input[name=\"m_mode\"]:checked')?.value, "
                "nValue: document.getElementById('field-N').value, "
                "runName: document.getElementById('run-name-input').value, "
                "runDescription: "
                "document.getElementById('run-description-input').value, "
                "duplicateDisabled: document.getElementById("
                "'configure-duplicate-preset-button').disabled, "
                "runDisabled: document.getElementById('run-button').disabled"
                "})"
            ),
        }

    result = _drive(window, steps)

    assert result["selectedClass"] == example.class_id
    assert result["selectedExample"] == _TARGET_EXAMPLE_ID
    assert result["detailName"] == example.name
    assert result["dialogOpen"] is False
    assert result["configureVisible"] is True
    assert result["mMode"] == "matrix"
    # The example's `N: 150` with `ploidy: haploid`: 150 individuals.
    assert result["nValue"] == "150"
    assert result["runName"] == example.name
    assert result["runDescription"] == example.description
    assert result["duplicateDisabled"] is False
    assert result["runDisabled"] is False


def _refuse_example(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `_UNLOADABLE_EXAMPLE_ID` unloadable, as a form limitation would.

    Patches the one function every example takes into the form
    (`app_module._example_form_values`), so `list_examples` marks it
    unloadable and `load_example` refuses it, with `_REFUSAL` as the
    reason; every other example is unaffected.
    """
    original = app_module._example_form_values

    def refuse(example: presets_module.Example) -> dict[str, Any]:
        """Refuse the chosen example; pass every other one through."""
        if example.example_id == _UNLOADABLE_EXAMPLE_ID:
            return {"ok": False, "message": _REFUSAL}
        return original(example)

    monkeypatch.setattr(app_module, "_example_form_values", refuse)


def test_unloadable_example_explains_itself_and_offers_its_yaml(
    window: webview.Window, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An example the form cannot show cannot load; the dialog says why up front.

    Selected by clicking, the other way into the list. "View YAML" still
    opens its configuration, on top of the Examples dialog.
    """
    _refuse_example(monkeypatch)
    tree_index, list_index, example = _route_to(_UNLOADABLE_EXAMPLE_ID)

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js(_OPEN_DIALOG)
        poll_until(_DIALOG_READY, lambda value: value is True)
        window.evaluate_js(
            "document.getElementById('examples-class-tree')"
            f".children[{tree_index}].click();"
        )
        window.evaluate_js(
            f"document.getElementById('examples-list').children[{list_index}].click();"
        )
        state = window.evaluate_js(
            "({"
            "loadDisabled: document.getElementById('examples-load-button').disabled, "
            "errorHidden: document.getElementById('examples-load-error').hidden, "
            "errorText: document.getElementById('examples-load-error').textContent, "
            "itemText: document.getElementById('examples-list')"
            ".querySelector('[aria-selected=\"true\"]').textContent"
            "})"
        )
        window.evaluate_js("window.__fimPresetYamlReady = false;")
        window.evaluate_js(
            "document.getElementById('examples-view-yaml-button').click();"
        )
        poll_until("window.__fimPresetYamlReady === true", lambda value: value is True)
        state["yaml"] = window.evaluate_js(
            "document.getElementById('preset-yaml-text').value"
        )
        state["bothOpen"] = window.evaluate_js(
            "document.getElementById('modal-preset-yaml').open "
            "&& document.getElementById('modal-examples').open"
        )
        return state

    result = _drive(window, steps)

    assert result["loadDisabled"] is True
    assert result["errorHidden"] is False
    assert _REFUSAL in result["errorText"]
    assert result["itemText"].startswith(f"{example.name} (view YAML only)")
    assert "mu_b" in result["yaml"]
    assert result["bothOpen"] is True


def test_dialog_fits_the_default_window_and_labels_every_control(
    window: webview.Window,
) -> None:
    """At 900x700 the dialog fits with no horizontal overflow; controls are labelled."""

    def steps(poll_until: Callable[[str, Callable[[Any], bool]], Any]) -> Any:
        window.evaluate_js(_OPEN_DIALOG)
        poll_until(_DIALOG_READY, lambda value: value is True)
        return window.evaluate_js(
            "(function(){"
            "var dialog = document.getElementById('modal-examples');"
            "var box = dialog.getBoundingClientRect();"
            "var unlabelled = Array.from(dialog.querySelectorAll("
            '\'button, [role="tree"], [role="listbox"], [role="option"], '
            '[role="treeitem"]\')).filter(function (element) {'
            "return !element.getAttribute('aria-label') "
            "&& !element.textContent.trim();"
            "}).length;"
            "return {"
            "viewportWidth: window.innerWidth, viewportHeight: window.innerHeight, "
            "left: box.left, top: box.top, right: box.right, bottom: box.bottom, "
            "overflowX: dialog.scrollWidth > dialog.clientWidth, "
            "treeItems: document.getElementById('examples-class-tree')"
            ".children.length, "
            "unlabelled: unlabelled"
            "};"
            "})()"
        )

    result = _drive(window, steps)

    assert result["left"] >= 0
    assert result["top"] >= 0
    assert result["right"] <= result["viewportWidth"]
    assert result["bottom"] <= result["viewportHeight"]
    assert result["overflowX"] is False
    assert result["treeItems"] >= 1
    assert result["unlabelled"] == 0
