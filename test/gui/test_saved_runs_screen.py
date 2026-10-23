"""Headless functional test for Saved runs (`screens/saved-runs.js`).

Proves the page wires the saved-run bridge end to end in a real window:
"Save for later" keeps an invalid form as a Draft and a fixed one as
Planned (the same saved run both times), Home lists it with a Start
button, and Start runs it to completion in the ordinary running view.

`test_saved_runs_api.py`, `test_object_bridge.py` and
`test_object_runner.py` prove the Python half. Every wait here is on a
page value or a Python-side event, polled with `poll_page`/`wait_or_fail`,
so the outcome does not depend on how fast the machine is.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Any

import pytest
import webview
from conftest import wait_or_fail

from fim.gui.app import Api, create_window
from fim.gui.batch_runner import BatchMessage
from fim.gui.preferences import GuiPreferences, save_preferences
from fim.gui.runner import RunMessage

from .conftest import poll_page, wait_for_run_view_ready
from .test_saved_runs_api import _form

pytestmark = pytest.mark.gui

_OUTCOME_TIMEOUT_SECONDS = 60.0

_SET_FIELDS = """
function setField(name, value) {
    const field = document.getElementById(`field-${name}`);
    field.value = value;
    field.dispatchEvent(new Event('input', {bubbles: true}));
}
setField('N', '%(N)s');
setField('d', '2');
setField('seed', '20260814');
setField('m_rate', '0.1');
setField('mu_value', '0.01');
setField('locus_lengths', '200');
document.getElementById('save-run-object-button').click();
"""

_RUN_BANNER = "document.getElementById('run-banner').textContent"

_SAVED_ROWS = (
    "Array.from(document.querySelectorAll('#saved-runs-body tr')).map("
    "function(row){return {id: row.dataset.objectId, "
    "actions: Array.from(row.querySelectorAll('button')).map("
    "function(button){return button.dataset.action;})};})"
)


@pytest.fixture
def saved_run_settings(_isolate_gui_preferences: Path) -> Path:
    """Settings for one short, resumable (lineal), scalar run."""
    save_preferences(
        _isolate_gui_preferences,
        GuiPreferences(
            welcome_dismissed=True,
            default_ploidy="1",
            default_run_settings={
                "n_replicates": "1",
                "max_generations": "40",
                "engine_backend": "lineal",
            },
        ),
    )
    return _isolate_gui_preferences


def test_a_draft_is_fixed_saved_and_run_from_home(
    saved_run_settings: Path,
) -> None:
    done_event = threading.Event()
    messages: list[RunMessage | BatchMessage] = []

    def on_message(message: RunMessage | BatchMessage) -> None:
        messages.append(message)
        if message[0] in ("done", "cancelled", "paused", "error"):
            done_event.set()

    window = create_window(api=Api(on_message=on_message), hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            wait_for_run_view_ready(window)
            window.evaluate_js(_SET_FIELDS % {"N": "lots"})
            draft_banner = poll_page(
                window,
                _RUN_BANNER,
                lambda text: "Draft" in str(text),
                what="Draft save banner",
            )
            window.evaluate_js(_SET_FIELDS % {"N": "20"})
            planned_banner = poll_page(
                window,
                _RUN_BANNER,
                lambda text: str(text).startswith("Saved."),
                what="Planned save banner",
            )
            window.evaluate_js("window.fim.showOpenRunScreen();")
            rows = poll_page(window, _SAVED_ROWS, bool, what="Saved runs row")
            window.evaluate_js(
                "document.querySelector("
                "'#saved-runs-body button[data-action=\"start\"]').click();"
            )
            wait_or_fail(done_event, "saved run end")
            settled = poll_page(
                window,
                "({state: window.fim.getRunViewState(), "
                "pauseHidden: document.getElementById('pause-run-button').hidden, "
                "label: window.fim.workspace.selected().label})",
                lambda value: value["state"] == "completed",
                what="completed view",
            )
            outcome.put(
                {
                    "draftBanner": draft_banner,
                    "plannedBanner": planned_banner,
                    "rows": rows,
                    **settled,
                }
            )
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert settled is not None
    assert "cannot run until this is fixed" in settled["draftBanner"]
    [row] = settled["rows"]
    assert "start" in row["actions"]
    assert "edit" in row["actions"]
    assert messages[-1][0] == "done"
    assert settled["pauseHidden"] is True
    assert settled["label"].startswith("20 haploid, d=2, m=0.1, mu=0.01")


def test_editing_saved_run_stays_with_its_document(
    saved_run_settings: Path,
) -> None:
    window = create_window(api=Api(), hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            wait_for_run_view_ready(window)
            first = window.evaluate_js("window.fim.workspace.selectedId()")
            window.evaluate_js(_SET_FIELDS % {"N": "20"})
            poll_page(
                window,
                _RUN_BANNER,
                lambda text: str(text).startswith("Saved."),
                what="saved-run confirmation",
            )
            window.evaluate_js("window.fim.workspace.createDocument();")
            window.evaluate_js("window.fim.showOpenRunScreen();")
            poll_page(window, _SAVED_ROWS, bool, what="Saved runs row")
            window.evaluate_js(
                "document.querySelector("
                "'#saved-runs-body button[data-action=\"edit\"]').click();"
            )
            editing = poll_page(
                window,
                "({tabId: window.fim.workspace.selectedId(), "
                "objectId: window.fim.getEditingObjectId()})",
                lambda value: value["tabId"] == first and bool(value["objectId"]),
                what="saved-run document activation",
            )
            editing = editing["objectId"]
            second = window.evaluate_js("window.fim.workspace.createDocument()")
            second_editing = window.evaluate_js("window.fim.getEditingObjectId()")
            window.evaluate_js(f"window.fim.workspace.select({first!r});")
            first_editing = window.evaluate_js("window.fim.getEditingObjectId()")
            outcome.put(
                {
                    "editing": editing,
                    "first": first,
                    "second": second,
                    "secondEditing": second_editing,
                    "firstEditing": first_editing,
                }
            )
        finally:
            window.destroy()

    webview.start(_drive)
    settled = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert settled is not None
    assert settled["editing"]
    assert settled["second"] != settled["first"]
    assert settled["secondEditing"] is None
    assert settled["firstEditing"] == settled["editing"]


def _stub_row(
    state: str, *, kill: bool, delete: bool, name: str = "Long run"
) -> dict[str, Any]:
    """One `list_run_objects` row for a saved run in `state`."""
    return {
        "objectId": "obj-" + "a" * 32,
        "name": name,
        "description": None,
        "configSummary": {
            "N": "225 diploid",
            "d": "10",
            "m": "0.01",
            "mu": "1e-05",
            "mutation_model": "kam",
            "replicates": "200",
            "seed": "7",
        },
        "scientificRunId": "run-1",
        "state": state,
        "generation": 7,
        "updatedAt": "2026-10-23T12:00:00Z",
        "issues": [],
        "error": None,
        "directory": "/nonexistent",
        "actions": {"clone": True, "kill": kill, "delete": delete},
        "reasons": {},
    }


class _StubbedSavedRunsApi(Api):
    """Serves one saved run whose state the test changes from Python."""

    def __init__(self) -> None:
        super().__init__()
        self.row = _stub_row("cancelling", kill=True, delete=False)
        self.killed: list[str] = []
        self.deleted: list[str] = []
        self.selected_items: list[dict[str, str]] = []

    owned_here: bool = False

    def list_run_objects(self) -> dict[str, Any]:
        """Return the current stub row, executed here when `owned_here`."""
        owned = [self.row["objectId"]] if self.owned_here else []
        return {
            "ok": True,
            "runs": [self.row],
            "unreadable": [],
            "activeObjectId": None,
            "activeObjectIds": owned,
        }

    def kill_run_object(self, object_id: str) -> dict[str, Any]:
        """Record the kill and report the run failed."""
        self.killed.append(object_id)
        self.row = _stub_row("failed", kill=False, delete=True)
        return {"ok": True, "state": "failed"}

    def delete_run_object(self, object_id: str) -> dict[str, Any]:
        """Record the deletion."""
        self.deleted.append(object_id)
        return {"ok": True}

    def delete_selected(self, items: list[dict[str, str]]) -> dict[str, Any]:
        """Record the saved runs in a Home selection, and delete nothing else."""
        objects = [item["objectId"] for item in items if item["kind"] == "object"]
        self.deleted.extend(objects)
        self.selected_items = items
        return {
            "ok": True,
            "deletedRunCount": 0,
            "deletedStudyCount": 0,
            "deletedExperimentCount": 0,
            "deletedObjectCount": len(objects),
        }


_SAVED_ROW_STATE = (
    "(document.querySelector('#saved-runs-body tr td:nth-child(2)') || {})"
    ".textContent || null"
)


def test_home_rereads_an_unsettled_saved_run_until_it_settles(
    saved_run_settings: Path,
) -> None:
    """A Cancelling row becomes Cancelled with no action from the user."""
    api = _StubbedSavedRunsApi()
    window = create_window(api=api, hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            wait_for_run_view_ready(window)
            window.evaluate_js(
                "window.fim.savedRunsPollMilliseconds = 20;"
                "window.fim.showOpenRunScreen();"
            )
            first = poll_page(
                window, _SAVED_ROW_STATE, bool, what="Cancelling saved run"
            )
            api.row = _stub_row("cancelled", kill=False, delete=True)
            settled = poll_page(
                window,
                _SAVED_ROW_STATE,
                lambda text: text == "Cancelled",
                what="Cancelled saved run",
            )
            outcome.put({"first": first, "settled": settled})
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert result == {"first": "Cancelling", "settled": "Cancelled"}


_SAVED_ROW_NAME = (
    "(function(){const cell = document.querySelector("
    "'#saved-runs-body tr td:first-child');"
    "return cell ? {text: cell.textContent, title: cell.title} : null;})()"
)


def test_an_unnamed_saved_run_is_shown_by_its_settings(
    saved_run_settings: Path,
) -> None:
    """The object id is kept for the tooltip, not shown as the name."""
    api = _StubbedSavedRunsApi()
    api.row = _stub_row("cancelled", kill=False, delete=True, name="")
    window = create_window(api=api, hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            wait_for_run_view_ready(window)
            window.evaluate_js("window.fim.showOpenRunScreen();")
            outcome.put(poll_page(window, _SAVED_ROW_NAME, bool, what="saved run name"))
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert result is not None
    assert result["text"] == ("225 diploid, d=10, m=0.01, mu=1e-05, 200 replicates")
    assert "obj-" + "a" * 32 in result["title"]
    assert "seed=7" in result["title"]
    assert "Run ID: run-1" in result["title"]


def test_watch_returns_to_the_document_running_the_saved_run(
    saved_run_settings: Path,
) -> None:
    """Home links a run this window executes back to its own document."""
    api = _StubbedSavedRunsApi()
    api.row = _stub_row("running", kill=True, delete=False, name="")
    api.owned_here = True
    window = create_window(api=api, hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)
    object_id = "obj-" + "a" * 32

    def _drive() -> None:
        try:
            wait_for_run_view_ready(window)
            running_tab = window.evaluate_js(
                "(function(){const workspace = window.fim.workspace;"
                "const tabId = workspace.createDocument("
                f"{{label: 'Long run', runObjectId: '{object_id}'}},"
                "{select: false});"
                "window.fim.showOpenRunScreen();"
                "return tabId;})()"
            )
            rows = poll_page(
                window,
                _SAVED_ROWS,
                lambda value: bool(value) and "watch" in value[0]["actions"],
                what="watchable saved run",
            )
            home_tab = window.evaluate_js("window.fim.workspace.selectedId()")
            window.evaluate_js("document.querySelector('#saved-runs-body a').click();")
            shown = poll_page(
                window,
                "(function(){const state = window.fim.workspace.selected();"
                "return {tab: state.tabId,"
                " screen: state.history[state.historyIndex], label: state.label};})()",
                lambda value: value["tab"] != home_tab,
                what="running document",
            )
            outcome.put({"running": running_tab, "rows": rows, "shown": shown})
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert result is not None
    assert result["rows"][0]["actions"] == ["watch", "clone", "kill"]
    assert result["shown"] == {
        "tab": result["running"],
        "screen": "screen-run",
        "label": "225 diploid, d=10, m=0.01, mu=1e-05, 200 replicates",
    }


def test_a_run_executing_elsewhere_offers_no_watch(
    saved_run_settings: Path,
) -> None:
    """Without its document here, the row says where to watch it instead."""
    api = _StubbedSavedRunsApi()
    api.row = _stub_row("running", kill=True, delete=False)
    window = create_window(api=api, hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            wait_for_run_view_ready(window)
            window.evaluate_js("window.fim.showOpenRunScreen();")
            rows = poll_page(window, _SAVED_ROWS, bool, what="running saved run")
            title = window.evaluate_js(
                "document.querySelector('#saved-runs-body tr td:nth-child(2)').title"
            )
            outcome.put({"rows": rows, "title": title})
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert result is not None
    assert result["rows"][0]["actions"] == ["clone", "kill"]
    assert "another window or command" in result["title"]


_SAVED_CHECKBOXES = (
    "Array.from(document.querySelectorAll("
    "'#saved-runs-body .saved-run-select-checkbox')).map("
    "function(box){return {checked: box.checked,"
    " shown: getComputedStyle(box).display !== 'none'};})"
)


def test_select_all_and_delete_selected_include_saved_runs(
    saved_run_settings: Path,
) -> None:
    """A deletable saved run joins Home's selection; Delete removes it."""
    api = _StubbedSavedRunsApi()
    api.row = _stub_row("cancelled", kill=False, delete=True)
    window = create_window(api=api, hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            wait_for_run_view_ready(window)
            window.evaluate_js("window.fim.showOpenRunScreen();")
            hidden = poll_page(
                window, _SAVED_CHECKBOXES, bool, what="saved-run checkbox"
            )
            window.evaluate_js(
                "document.getElementById('open-run-select-all-button').click();"
            )
            selected = poll_page(
                window,
                _SAVED_CHECKBOXES,
                lambda boxes: boxes[0]["checked"] and boxes[0]["shown"],
                what="selected saved run",
            )
            label = window.evaluate_js(
                "document.getElementById('open-run-delete-selected-button').textContent"
            )
            window.evaluate_js(
                "document.getElementById('open-run-delete-selected-button')"
                ".click();"
                "document.querySelector('.open-run-inline-confirm button')"
                ".click();"
            )
            after = poll_page(
                window,
                "document.getElementById('open-run-delete-selected-button')"
                ".textContent",
                lambda text: text == "Delete selected",
                what="selection cleared by the deletion",
            )
            outcome.put(
                {
                    "hidden": hidden,
                    "selected": selected,
                    "label": label,
                    "after": after,
                }
            )
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert result is not None
    assert result["hidden"] == [{"checked": False, "shown": False}]
    assert result["selected"] == [{"checked": True, "shown": True}]
    assert result["label"].startswith("Delete selected (")
    assert {"kind": "object", "objectId": "obj-" + "a" * 32} in api.selected_items
    assert api.deleted == ["obj-" + "a" * 32]


def test_kill_then_delete_from_home(saved_run_settings: Path) -> None:
    """Kill asks first, kills, then offers to delete what is left."""
    api = _StubbedSavedRunsApi()
    api.row = _stub_row("running", kill=True, delete=False)
    window = create_window(api=api, hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            wait_for_run_view_ready(window)
            window.evaluate_js("window.fim.showOpenRunScreen();")
            rows = poll_page(window, _SAVED_ROWS, bool, what="running saved run")
            window.evaluate_js(
                "window.__fimConfirms = [];"
                "window.confirm = function(text) {"
                "window.__fimConfirms.push(text); return true;};"
                "document.querySelector("
                "'#saved-runs-body button[data-action=\"kill\"]').click();"
            )
            prompts = poll_page(
                window,
                "window.__fimConfirms",
                lambda texts: len(texts) == 2,
                what="kill and delete confirmations",
            )
            settled = poll_page(
                window, _SAVED_ROWS, lambda value: value != rows, what="reread"
            )
            outcome.put({"rows": rows, "prompts": prompts, "settled": settled})
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert result is not None
    assert result["rows"][0]["actions"] == ["clone", "kill"]
    assert result["prompts"][0].startswith('Kill "Long run" now?')
    assert result["prompts"][1].startswith('"Long run" was killed. Delete it')
    assert api.killed == api.deleted == ["obj-" + "a" * 32]
    assert result["settled"][0]["actions"] == ["clone", "delete"]


@pytest.mark.parametrize("generation, expected", [(None, "—"), (0, "0"), (7, "7")])
def test_saved_run_generation_has_a_placeholder_only_when_absent(
    saved_run_settings: Path, generation: int | None, expected: str
) -> None:
    """Planned rows have no committed generation; zero is still a generation."""
    api = _StubbedSavedRunsApi()
    api.row = _stub_row("planned", kill=False, delete=True)
    api.row["generation"] = generation
    window = create_window(api=api, hidden=True)
    outcome: queue.Queue[str] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            wait_for_run_view_ready(window)
            window.evaluate_js("window.fim.showOpenRunScreen();")
            outcome.put(
                poll_page(
                    window,
                    "document.querySelector('#saved-runs-body tr td:nth-child(3)')"
                    "?.textContent",
                    bool,
                    what="saved generation",
                )
            )
        finally:
            window.destroy()

    webview.start(_drive)
    assert outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS) == expected


@pytest.mark.parametrize("named", [False, True])
def test_opening_a_saved_run_labels_its_new_tab(
    saved_run_settings: Path, named: bool
) -> None:
    """Open supplies the same name/settings fallback as the Home row."""
    api = Api()
    saved = api.save_run_object(_form(), run_name="Named trial" if named else "")
    window = create_window(api=api, hidden=True)
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            wait_for_run_view_ready(window)
            window.evaluate_js(f"editSavedRun({saved['objectId']!r});")
            state = poll_page(
                window,
                "({label: window.fim.workspace.selected().label, "
                "editing: window.fim.getEditingObjectId()})",
                lambda value: value["editing"] == saved["objectId"],
                what="opened saved-run tab",
            )
            window.evaluate_js(
                "showStartRefusal(window.fim.workspace.selectedId(), 'Busy');"
            )
            state["refusal"] = window.evaluate_js(_RUN_BANNER)
            window.evaluate_js(
                "const documentState = window.fim.workspace.selected();"
                f"documentState.runObjectId = {saved['objectId']!r};"
                "documentState.jobState = 'running';"
                "window.fim.workspace.attachJob(documentState.tabId, 'refusal-job');"
                "window.fim.resumeRunObjectFromTab(documentState.tabId);"
            )
            state["resumeRefusal"] = poll_page(
                window,
                _RUN_BANNER,
                lambda value: "a run is in progress" in str(value),
                what="resume admission backstop",
            )
            outcome.put(state)
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)
    summary = saved["configSummary"]
    expected = (
        "Named trial"
        if named
        else (f"{summary['N']}, d={summary['d']}, m={summary['m']}, mu={summary['mu']}")
    )
    if not named and summary["replicates"] != "1":
        expected += f", {summary['replicates']} replicates"
    assert result["label"] == expected
    assert result["refusal"] == f"{expected}: Busy"
    assert result["resumeRefusal"] == (
        f"{expected}: a run is in progress; wait for it to finish or cancel it first"
    )


@pytest.mark.parametrize(
    "ending", ["completed", "paused", "cancelled", "failed", "killed"]
)
def test_run_button_tracks_its_own_document_job(
    saved_run_settings: Path, ending: str
) -> None:
    """Job notifications and validation cannot enable a running tab's button."""
    window = create_window(api=Api(), hidden=True)
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            wait_for_run_view_ready(window)
            result = window.evaluate_js("""
                (function() {
                    const workspace = window.fim.workspace;
                    const state = workspace.selected();
                    state.validationResult = {ok: true};
                    workspace.attachJob(state.tabId, 'button-test-job');
                    state.jobState = 'starting';
                    workspace.notifyChange();
                    const button = document.getElementById('run-button');
                    const starting = button.disabled;
                    window.fim.onJobEvent({jobId: 'button-test-job',
                        sequence: 1, type: 'status', payload: {}});
                    showValidationResult(state.validationResult);
                    const running = button.disabled;
                    const other = workspace.createDocument();
                    const otherEnabled = !button.disabled;
                    workspace.select(state.tabId);
                    return {starting, running, otherEnabled,
                        restoredRunning: button.disabled};
                })()
            """)
            window.evaluate_js(
                "window.fim.workspace.selected().runObjectState = "
                f"{ending!r};"
                "window.fim.onJobEvent({jobId: 'button-test-job', sequence: 2, "
                f"type: 'terminal', payload: {{state: {ending!r}}}}});"
            )
            result["endedEnabled"] = window.evaluate_js(
                "!document.getElementById('run-button').disabled"
            )
            result["text"] = window.evaluate_js(
                "document.getElementById('run-button').textContent"
            )
            window.evaluate_js(
                "window.fim.onJobEvent({jobId: 'button-test-job', sequence: 3, "
                "type: 'finished', payload: {}});"
            )
            result["finishedEnabled"] = window.evaluate_js(
                "!document.getElementById('run-button').disabled"
            )
            window.evaluate_js("""
                window.fim.workspace.selected().validationResult = {ok: false};
                window.fim.workspace.notifyChange();
            """)
            result["invalidDisabled"] = window.evaluate_js(
                "document.getElementById('run-button').disabled"
            )
            outcome.put(result)
        finally:
            window.destroy()

    webview.start(_drive)
    result = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)
    assert all(
        result[key]
        for key in (
            "starting",
            "running",
            "otherEnabled",
            "restoredRunning",
            "endedEnabled",
            "finishedEnabled",
            "invalidDisabled",
        )
    )
    assert result["text"] == ("Resume run" if ending == "paused" else "Run simulation")
