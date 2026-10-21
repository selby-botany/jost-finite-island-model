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
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
import webview
from conftest import poll_or_fail

from fim.gui import object_bridge
from fim.gui.app import Api, create_window
from fim.persistence import run_objects

from .conftest import poll_page

pytestmark = pytest.mark.gui

_POLL_INTERVAL_SECONDS = 0.1
_DIALOG_OPEN = "document.getElementById('modal-confirm-close').open === true"
_DIALOG_CLOSED = "document.getElementById('modal-confirm-close').open === false"
_DOCUMENT_DIALOG_OPEN = (
    "document.getElementById('modal-confirm-close-document').open === true"
)
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


def test_closing_a_hidden_dirty_document_updates_the_aggregate() -> None:
    """Removing the last dirty document cannot leave a stale window-close veto."""

    def steps(window: webview.Window, api: Api) -> None:
        first = window.evaluate_js("window.fim.workspace.selectedId()")
        window.evaluate_js(_EDIT_DEMES)
        _wait_for_unsaved(api, True)
        window.evaluate_js(
            "window.fim.workspace.createDocument();window.fim.markFormClean(); true;"
        )
        _wait_for_unsaved(api, True)
        window.evaluate_js(f"window.fim.workspace.closeDocument('{first}'); true;")
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


def test_quitting_from_home_lists_hidden_dirty_documents_and_the_running_job() -> None:
    def steps(window: webview.Window, api: Api) -> bool:
        dirty_tab = window.evaluate_js("window.fim.workspace.selectedId()")
        window.evaluate_js(_EDIT_DEMES)
        _wait_for_unsaved(api, True)
        clean_tab = window.evaluate_js(
            "window.fim.workspace.createDocument();"
            "window.fim.markFormClean();"
            "window.fim.workspace.selectedId();"
        )
        window.evaluate_js(f"window.fim.workspace.select('{dirty_tab}');")
        window.evaluate_js(f"window.fim.workspace.select('{clean_tab}');")
        window.evaluate_js("window.fim.showOpenRunScreen(); true;")
        api._run_in_flight = True
        api._cancel_event = threading.Event()
        api._job_id = "job-close-test"

        assert _start_close(window) is True
        poll_page(window, _DIALOG_OPEN, what="the close dialog to open from Home")
        state = window.evaluate_js(
            "(function(){"
            "return {"
            "screen: document.querySelector('.screen:not([hidden])').id,"
            "dirty: [...document.querySelectorAll("
            "'#confirm-close-dirty-documents li')].map(item => item.textContent),"
            "job: document.getElementById('confirm-close-active-job').textContent"
            "};"
            "})()"
        )
        assert state["screen"] == "screen-open-run"
        assert state["dirty"] == [f"New configuration (draft) — {dirty_tab}"]
        assert state["job"] == "Active jobs: run — job-close-test"
        _click(window, "confirm-close-discard-button")
        poll_or_fail(
            lambda: api._close_gate.closing,
            lambda closing: closing is True,
            "the window to close after the close dialog is answered",
            interval=_POLL_INTERVAL_SECONDS,
        )
        return True

    _drive(steps)


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


def test_failed_save_keeps_the_close_dialog_open() -> None:
    def steps(window: webview.Window, api: Api) -> None:
        window.evaluate_js(_EDIT_DEMES)
        _wait_for_unsaved(api, True)
        window.evaluate_js(
            "window.pywebview.api.save_run_object = async () => ({"
            "ok: false, message: 'The disk is not writable.'"
            "}); true;"
        )
        assert _start_close(window) is True
        poll_page(window, _DIALOG_OPEN, what="the close dialog to open")
        _click(window, "confirm-close-primary-button")
        poll_page(
            window,
            "document.getElementById('confirm-close-error').textContent === "
            "'The disk is not writable.'",
            what="the save failure to be shown",
        )
        assert api._close_gate.closing is False
        assert api._close_gate.unsaved_edits is True
        assert (
            window.evaluate_js(
                "document.getElementById('confirm-close-primary-button').disabled"
            )
            is False
        )
        assert window.evaluate_js(_DIALOG_OPEN) is True

    _drive(steps)


def test_failed_document_save_keeps_that_tab_open() -> None:
    def steps(window: webview.Window, api: Api) -> None:
        window.evaluate_js(_EDIT_DEMES)
        _wait_for_unsaved(api, True)
        tab_id = window.evaluate_js(
            "window.fim.workspace.setDocumentTabsEnabled(true);"
            "window.fim.workspace.selectedId();"
        )
        window.evaluate_js(
            "window.fim.saveFormForLater = async () => ({"
            "ok: false, message: 'The saved-run directory is read-only.'"
            "});"
            f"window.fim.requestCloseDocument({tab_id!r}); true;"
        )
        poll_page(
            window,
            _DOCUMENT_DIALOG_OPEN,
            what="the document close dialog to open",
        )
        window.evaluate_js("window.fim.resolveCloseDocument('save'); true;")
        poll_page(
            window,
            "document.getElementById('confirm-close-document-error').textContent "
            "=== 'The saved-run directory is read-only.'",
            what="the document save failure to be shown",
        )
        assert (
            window.evaluate_js(f"window.fim.workspace.get({tab_id!r}).formDirty")
            is True
        )
        assert (
            window.evaluate_js(f"window.fim.workspace.get({tab_id!r}) !== null") is True
        )
        assert window.evaluate_js(_DOCUMENT_DIALOG_OPEN) is True
        assert api._close_gate.closing is False

    _drive(steps)


def test_closing_a_document_during_saved_run_admission_waits_for_acceptance() -> None:
    def steps(window: webview.Window, api: Api) -> None:
        window.evaluate_js(
            "window.fim.workspace.setDocumentTabsEnabled(true);"
            "window.pywebview.api.can_start_run = () => new Promise((resolve) => {"
            "window.__resolveStartAdmission = resolve;"
            "});"
            "window.pywebview.api.get_run_context = async () => ({ok: true});"
            "window.pywebview.api.start_run_object = async () => ({"
            "ok: true, jobId: 'job-admission-accepted', isBatch: false"
            "});"
            "void startSavedRun({"
            "objectId: 'saved-admission-accepted',"
            "name: 'Field trial',"
            "resourceKey: 'saved-run:saved-admission-accepted'"
            "}); true;"
        )
        poll_page(
            window,
            "window.fim.workspace.selected().jobState === 'starting'",
            what="saved-run admission to start",
        )
        tab_id = window.evaluate_js("window.fim.workspace.selectedId()")
        window.evaluate_js(f"window.fim.requestCloseDocument({tab_id!r}); true;")
        poll_page(
            window,
            _DOCUMENT_DIALOG_OPEN,
            what="the starting-document close dialog",
        )
        assert (
            window.evaluate_js("window.fim.workspace.selected().closeAfterStart")
            is True
        )
        assert (
            window.evaluate_js(
                "document.getElementById('confirm-close-document-job-button').hidden"
            )
            is True
        )

        window.evaluate_js("window.__resolveStartAdmission({ok: true}); true;")
        poll_page(
            window,
            "window.fim.workspace.selected().jobState === 'running' && "
            "document.getElementById('confirm-close-document-job-button').textContent "
            "=== 'Pause and close'",
            what="accepted admission to show the saved-run close action",
        )
        assert (
            window.evaluate_js(f"window.fim.workspace.get({tab_id!r}).attachedJobId")
            == "job-admission-accepted"
        )
        _click(window, "confirm-close-document-keep-button")
        poll_page(
            window,
            "document.getElementById('modal-confirm-close-document').open === false",
            what="the accepted run to remain open",
        )
        assert (
            window.evaluate_js(f"window.fim.workspace.get({tab_id!r}).jobState")
            == "running"
        )
        assert api._close_gate.closing is False

    _drive(steps)


def test_saved_run_admission_refusal_is_shown_on_its_own_document() -> None:
    def steps(window: webview.Window, api: Api) -> None:
        window.evaluate_js(
            "window.fim.workspace.setDocumentTabsEnabled(true);"
            "window.pywebview.api.can_start_run = async () => ({"
            "ok: false, message: 'Another run is active.'"
            "});"
            "window.pywebview.api.start_run_object = async () => {"
            "window.__fimUnexpectedStart = true;"
            "return {ok: true, jobId: 'unexpected'};"
            "};"
            "void startSavedRun({"
            "objectId: 'saved-admission-refused',"
            "name: 'Field trial',"
            "resourceKey: 'saved-run:saved-admission-refused'"
            "}); true;"
        )
        poll_page(
            window,
            "window.fim.workspace.selected().startRefusal === "
            "'Field trial: Another run is active.'",
            what="the saved-run refusal to reach its owner",
        )
        result = window.evaluate_js(
            "(function(){"
            "const state = window.fim.workspace.selected();"
            "return {"
            "kind: state.kind,"
            "jobState: state.jobState,"
            "banner: document.getElementById('run-banner').textContent,"
            "unexpectedStart: window.__fimUnexpectedStart === true"
            "};"
            "})()"
        )
        assert result == {
            "kind": "saved-run",
            "jobState": None,
            "banner": "Field trial: Another run is active.",
            "unexpectedStart": False,
        }
        assert api._close_gate.closing is False

    _drive(steps)


@pytest.mark.parametrize(
    ("action", "closed"),
    [("discard", True), ("keep", False)],
)
def test_dirty_document_can_be_discarded_or_kept_open(
    action: str,
    closed: bool,
) -> None:
    def steps(window: webview.Window, api: Api) -> None:
        window.evaluate_js(_EDIT_DEMES)
        _wait_for_unsaved(api, True)
        tab_id = window.evaluate_js(
            "window.fim.workspace.setDocumentTabsEnabled(true);"
            "window.fim.workspace.selectedId();"
        )
        window.evaluate_js(
            "window.__fimDocumentSaveCalls = 0;"
            "window.pywebview.api.save_run_object = async () => {"
            "window.__fimDocumentSaveCalls += 1;"
            "return {ok: true};"
            "};"
            f"window.fim.requestCloseDocument({tab_id!r}); true;"
        )
        poll_page(
            window,
            _DOCUMENT_DIALOG_OPEN,
            what="the dirty-document close dialog",
        )
        button_id = (
            "confirm-close-document-discard-button"
            if action == "discard"
            else "confirm-close-document-keep-button"
        )
        _click(window, button_id)
        if closed:
            poll_page(
                window,
                f"window.fim.workspace.get({tab_id!r}) === null",
                what="the discarded document to close",
            )
            _wait_for_unsaved(api, False)
        else:
            poll_page(
                window,
                "document.getElementById('modal-confirm-close-document').open "
                "=== false",
                what="the kept document dialog to close",
            )
            assert (
                window.evaluate_js(f"window.fim.workspace.get({tab_id!r}).formDirty")
                is True
            )
            assert api._close_gate.unsaved_edits is True
        assert window.evaluate_js("window.__fimDocumentSaveCalls") == 0
        assert api._close_gate.closing is False

    _drive(steps)


def test_saving_a_dirty_document_saves_and_closes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(run_objects, "results_directory", lambda: results)

    def steps(window: webview.Window, api: Api) -> None:
        window.evaluate_js(_EDIT_DEMES)
        _wait_for_unsaved(api, True)
        tab_id = window.evaluate_js(
            "window.fim.workspace.setDocumentTabsEnabled(true);"
            "window.fim.workspace.selectedId();"
        )
        window.evaluate_js(f"window.fim.requestCloseDocument({tab_id!r}); true;")
        poll_page(
            window,
            _DOCUMENT_DIALOG_OPEN,
            what="the dirty-document close dialog",
        )
        _click(window, "confirm-close-document-save-button")
        poll_page(
            window,
            f"window.fim.workspace.get({tab_id!r}) === null",
            what="the saved document to close",
        )
        _wait_for_unsaved(api, False)

    _drive(steps)
    [row] = object_bridge.list_rows(results)["runs"]
    assert row["state"] in {"draft", "planned"}


def test_closing_a_finished_job_releases_it_without_stopping_again() -> None:
    def steps(window: webview.Window, api: Api) -> None:
        state = window.evaluate_js("""(function(){
            window.fim.workspace.setDocumentTabsEnabled(true);
            const tabId = window.fim.workspace.selectedId();
            const state = window.fim.workspace.get(tabId);
            state.jobKind = "run";
            state.jobState = "finished";
            window.fim.workspace.attachJob(tabId, "job-already-finished");
            const calls = [];
            window.__fimCloseTabCalls = calls;
            window.pywebview.api.cancel_run = async () => {
                calls.push("cancel");
                return {ok: true};
            };
            window.pywebview.api.close_job_document = async () => {
                calls.push("release");
                return {ok: true};
            };
            window.fim.requestCloseDocument(tabId);
            return {tabId};
        })()""")
        poll_page(
            window,
            f"!window.fim.workspace.ids().includes({state['tabId']!r})",
            what="the finished document to close",
        )
        assert window.evaluate_js("window.__fimCloseTabCalls") == ["release"]
        assert api._close_gate.closing is False

    _drive(steps)


@pytest.mark.parametrize(
    ("job_kind", "expected_call", "expected_button"),
    [
        ("saved-run", "pause_run", "Pause and close"),
        ("run", "cancel_run", "Stop and close"),
        ("sweep", "cancel_sweep", "Stop and close"),
    ],
)
def test_closing_a_running_document_stops_its_job_and_drops_late_events(
    job_kind: str,
    expected_call: str,
    expected_button: str,
) -> None:
    def steps(window: webview.Window, api: Api) -> None:
        state = window.evaluate_js(
            "(function(){"
            "window.fim.workspace.setDocumentTabsEnabled(true);"
            "const tabId = window.fim.workspace.selectedId();"
            "const state = window.fim.workspace.get(tabId);"
            "state.jobKind = " + repr(job_kind) + ";"
            "state.jobState = 'running';"
            "window.fim.workspace.attachJob(tabId, 'job-close-tab');"
            "const calls = [];"
            "window.__fimCloseTabCalls = calls;"
            "window.pywebview.api.pause_run = async () => {"
            "calls.push('pause_run'); return {ok: true};"
            "};"
            "window.pywebview.api.cancel_run = async () => {"
            "calls.push('cancel_run'); return {ok: true};"
            "};"
            "window.pywebview.api.cancel_sweep = async () => {"
            "calls.push('cancel_sweep'); return {ok: true};"
            "};"
            "window.pywebview.api.get_job_snapshot = async (jobId) => ({"
            "ok: true, jobId, sequence: 2, snapshot: {type: 'finished'}"
            "});"
            "window.pywebview.api.close_job_document = async () => ({ok: true});"
            "window.fim.requestCloseDocument(tabId);"
            "return {tabId, calls};"
            "})()"
        )
        poll_page(
            window,
            _DOCUMENT_DIALOG_OPEN,
            what="the running-document close dialog",
        )
        assert (
            window.evaluate_js(
                "document.getElementById('confirm-close-document-job-button').textContent"
            )
            == expected_button
        )
        window.evaluate_js(
            "document.getElementById("
            "'confirm-close-document-job-button').click(); true;"
        )
        poll_page(
            window,
            f"!window.fim.workspace.ids().includes({state['tabId']!r})",
            what="the stopped document to close",
        )
        outcome = window.evaluate_js(
            "(function(){"
            "return {"
            "calls: window.fim.jobs.isDocumentClosed('job-close-tab'),"
            "owner: window.fim.workspace.documentForJob('job-close-tab'),"
            "late: window.fim.onJobEvent({"
            "jobId: 'job-close-tab', sequence: 3, type: 'progress',"
            "payload: {generation: 1}"
            "})"
            "};"
            "})()"
        )
        assert outcome == {"calls": True, "owner": None, "late": False}
        assert window.evaluate_js("window.__fimCloseTabCalls") == [expected_call]
        assert api._close_gate.closing is False

    _drive(steps)


def test_finished_job_release_failure_can_be_retried_without_stopping_again() -> None:
    def steps(window: webview.Window, api: Api) -> None:
        state = window.evaluate_js("""(function(){
            window.fim.workspace.setDocumentTabsEnabled(true);
            const tabId = window.fim.workspace.selectedId();
            const state = window.fim.workspace.get(tabId);
            state.jobKind = "run";
            state.jobState = "running";
            window.fim.workspace.attachJob(tabId, "job-release-retry");
            const calls = [];
            window.__fimCloseTabCalls = calls;
            window.pywebview.api.cancel_run = async () => {
                calls.push("cancel");
                return {ok: true};
            };
            window.pywebview.api.get_job_snapshot = async (jobId) => ({
                ok: true, jobId, sequence: 2, snapshot: {type: "finished"}
            });
            let releaseCalls = 0;
            window.pywebview.api.close_job_document = async () => {
                releaseCalls += 1;
                calls.push("release");
                return releaseCalls === 1
                    ? {ok: false, message: "job still active"}
                    : {ok: true};
            };
            window.fim.requestCloseDocument(tabId);
            return {tabId, calls};
        })()""")
        poll_page(
            window,
            _DOCUMENT_DIALOG_OPEN,
            what="the running-document close dialog",
        )
        window.evaluate_js(
            "document.getElementById("
            "'confirm-close-document-job-button').click(); true;"
        )
        poll_page(
            window,
            "document.getElementById('confirm-close-document-error').textContent "
            "=== 'job still active'",
            what="the first release failure",
        )
        assert (
            window.evaluate_js(
                "document.getElementById('confirm-close-document-job-button').textContent"
            )
            == "Retry close"
        )
        window.evaluate_js(
            "document.getElementById("
            "'confirm-close-document-job-button').click(); true;"
        )
        poll_page(
            window,
            f"!window.fim.workspace.ids().includes({state['tabId']!r})",
            what="the document to close after retry",
        )
        assert window.evaluate_js("window.__fimCloseTabCalls") == [
            "cancel",
            "release",
            "release",
        ]
        assert api._close_gate.closing is False

    _drive(steps)


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
