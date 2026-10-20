"""Headless functional tests for the close dialog and unsaved-edit tracking.

`test_close_guard.py` proves the Python half with plain calls. These prove the
page wires it: the form's unsaved edits reach Python, a close that would lose
them is vetoed and shows the dialog, and each answer does what it says (save
the form as a saved run, close without saving, keep working).

The close is started with `window.events.closing.set()`, the call every
platform's native close handler makes (`test_shutdown_deadman.py` uses the
same one), so the test runs the same way on every CI platform. Every wait is
on a page flag or a Python-side state the code under test sets, never a delay.
"""

from __future__ import annotations

import queue
from collections.abc import Callable

import pytest
import webview
from conftest import poll_or_fail

from fim.gui import object_bridge
from fim.gui.app import Api, create_window

from .conftest import poll_page

pytestmark = pytest.mark.gui

_POLL_INTERVAL_SECONDS = 0.1
_DIALOG_OPEN = "document.getElementById('modal-confirm-close').open === true"
_DIALOG_CLOSED = "document.getElementById('modal-confirm-close').open === false"
_EDIT_DEMES = (
    "(function(){"
    "var field = document.getElementById('field-d');"
    "field.value = '3';"
    "field.dispatchEvent(new Event('input', {bubbles: true}));"
    "return true;})()"
)
_EDIT_DEMES_TO_FOUR = (
    "(function(){"
    "var field = document.getElementById('field-d');"
    "field.value = '4';"
    "field.dispatchEvent(new Event('input', {bubbles: true}));"
    "return true;})()"
)


def _click(window: webview.Window, element_id: str) -> None:
    """Click a button in the page."""
    window.evaluate_js(f"document.getElementById('{element_id}').click(); true;")


def _drive(
    steps: Callable[[webview.Window, Api], bool | None],
) -> Api:
    """Open a hidden window over a fresh `Api`, run `steps`, and return the `Api`.

    `steps` runs on the callback thread once the first form has loaded and
    been taken as the saved baseline. It returns `True` when the close dialog
    already closed the window; otherwise the window is destroyed afterwards
    (`events.closing.set()` only asks, it does not close).
    """
    api = Api()
    window = create_window(api=api, hidden=True)
    outcome: queue.Queue[BaseException | None] = queue.Queue(maxsize=1)

    def run() -> None:
        closed = False
        try:
            poll_page(
                window,
                "window.__fimCloseGuardReady === true",
                what="the first form to be taken as saved",
                interval=_POLL_INTERVAL_SECONDS,
            )
            closed = bool(steps(window, api))
            outcome.put(None)
        except BaseException as error:
            outcome.put(error)
        finally:
            if not closed:
                window.destroy()

    webview.start(run)
    error = outcome.get(timeout=10.0)
    if error is not None:
        raise error
    return api


def _wait_for_unsaved(api: Api, expected: bool) -> None:
    """Wait for Python to hear that the form has (or has no) unsaved edits."""
    poll_or_fail(
        lambda: api._close_gate.unsaved_edits,
        lambda unsaved: unsaved is expected,
        f"unsaved edits to be reported as {expected}",
        interval=_POLL_INTERVAL_SECONDS,
    )


def _start_close(window: webview.Window) -> bool:
    """Start a close the way a native close handler does; return the veto."""
    return bool(window.events.closing.set())


def test_editing_the_form_reports_unsaved_edits_and_loading_one_clears_them() -> None:
    def steps(window: webview.Window, api: Api) -> None:
        assert api._close_gate.unsaved_edits is False
        window.evaluate_js(_EDIT_DEMES)
        _wait_for_unsaved(api, True)
        # A new configuration is a loaded form, so it is the new baseline.
        window.evaluate_js("resetInputForm(); true;")
        _wait_for_unsaved(api, False)

    _drive(steps)


def test_unsaved_edits_are_aggregated_across_documents() -> None:
    def steps(window: webview.Window, api: Api) -> None:
        first = window.evaluate_js("window.fim.workspace.selectedId()")
        window.evaluate_js(_EDIT_DEMES)
        _wait_for_unsaved(api, True)

        second = window.evaluate_js("window.fim.workspace.createDocument()")
        window.evaluate_js("window.fim.markFormClean(); true;")
        window.evaluate_js(_EDIT_DEMES_TO_FOUR)
        _wait_for_unsaved(api, True)

        window.evaluate_js(
            f"window.fim.workspace.select('{first}');"
            f"window.fim.markFormClean('{first}'); true;"
        )
        _wait_for_unsaved(api, True)
        window.evaluate_js(
            f"window.fim.workspace.select('{second}');"
            f"window.fim.markFormClean('{second}'); true;"
        )
        _wait_for_unsaved(api, False)

    _drive(steps)


def test_a_close_with_no_edits_goes_straight_through() -> None:
    def steps(window: webview.Window, api: Api) -> None:
        assert _start_close(window) is False
        assert api._close_gate.closing is True

    _drive(steps)


def test_discarding_closes_without_saving() -> None:
    def steps(window: webview.Window, api: Api) -> bool:
        window.evaluate_js(_EDIT_DEMES)
        _wait_for_unsaved(api, True)

        assert _start_close(window) is True

        poll_page(window, _DIALOG_OPEN, what="the close dialog to open")
        assert (
            window.evaluate_js(
                "document.getElementById('confirm-close-primary-button').textContent"
            )
            == "Save and close"
        )
        _click(window, "confirm-close-discard-button")
        poll_or_fail(
            lambda: api._close_gate.closing,
            lambda closing: closing is True,
            "the window to close",
            interval=_POLL_INTERVAL_SECONDS,
        )
        return True

    api = _drive(steps)
    assert object_bridge.list_rows()["runs"] == []
    assert api._close_gate.closing is True


def test_saving_keeps_the_form_as_a_saved_run_and_closes() -> None:
    def steps(window: webview.Window, api: Api) -> bool:
        window.evaluate_js(_EDIT_DEMES)
        _wait_for_unsaved(api, True)

        assert _start_close(window) is True

        poll_page(window, _DIALOG_OPEN, what="the close dialog to open")
        _click(window, "confirm-close-primary-button")
        poll_or_fail(
            lambda: api._close_gate.closing,
            lambda closing: closing is True,
            "the window to close",
            interval=_POLL_INTERVAL_SECONDS,
        )
        return True

    _drive(steps)
    [row] = object_bridge.list_rows()["runs"]
    assert row["state"] in {"draft", "planned"}


def test_keeping_working_leaves_the_window_open_and_the_edits_unsaved() -> None:
    def steps(window: webview.Window, api: Api) -> None:
        window.evaluate_js(_EDIT_DEMES)
        _wait_for_unsaved(api, True)

        assert _start_close(window) is True

        poll_page(window, _DIALOG_OPEN, what="the close dialog to open")
        _click(window, "confirm-close-keep-button")
        poll_page(window, _DIALOG_CLOSED, what="the close dialog to close")
        assert api._close_gate.closing is False
        assert api._close_gate.unsaved_edits is True

    _drive(steps)
    assert object_bridge.list_rows()["runs"] == []


def test_saving_before_a_close_clears_the_unsaved_edits() -> None:
    def steps(window: webview.Window, api: Api) -> None:
        window.evaluate_js(_EDIT_DEMES)
        _wait_for_unsaved(api, True)
        _click(window, "save-run-object-button")
        _wait_for_unsaved(api, False)
        assert _start_close(window) is False

    _drive(steps)


def test_saving_before_a_close_does_not_discard_unsaved_sweep_setup() -> None:
    def steps(window: webview.Window, api: Api) -> bool:
        window.evaluate_js(
            "(function(){"
            "var state = window.fim.workspace.selected();"
            "state.sweepSetup = {"
            "enabled: true, config: {request: {axes: []}},"
            "draft: {axes: [{key: 'm'}]}"
            "};"
            "window.fim.refreshUnsavedEdits();"
            "return true;})()"
        )
        _wait_for_unsaved(api, True)

        assert _start_close(window) is True
        poll_page(window, _DIALOG_OPEN, what="the close dialog to open")
        _click(window, "confirm-close-primary-button")
        poll_page(
            window,
            "document.getElementById('confirm-close-error').hidden === false",
            what="the close dialog to explain that sweep setup was not saved",
        )
        assert api._close_gate.closing is False
        assert api._close_gate.unsaved_edits is True
        assert (
            window.evaluate_js(
                "document.getElementById('confirm-close-primary-button').disabled"
            )
            is True
        )
        _click(window, "confirm-close-discard-button")
        poll_or_fail(
            lambda: api._close_gate.closing,
            lambda closing: closing is True,
            "the window to close after discarding sweep setup",
            interval=_POLL_INTERVAL_SECONDS,
        )
        return True

    _drive(steps)
