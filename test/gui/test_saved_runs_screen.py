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
                "pauseHidden: document.getElementById('pause-run-button').hidden})",
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
            window.evaluate_js("window.fim.showOpenRunScreen();")
            poll_page(window, _SAVED_ROWS, bool, what="Saved runs row")
            window.evaluate_js(
                "document.querySelector("
                "'#saved-runs-body button[data-action=\"edit\"]').click();"
            )
            editing = poll_page(
                window,
                "window.fim.getEditingObjectId()",
                bool,
                what="editing saved-run ID",
            )
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
