"""Tests for the browser document store and its compatibility signals."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import webview

pytestmark = pytest.mark.gui


def test_workspace_registers_every_existing_global_flag() -> None:
    """All legacy browser flags route through the document-local store."""
    webui = Path(__file__).parents[2] / "src/fim/gui/webui"
    workspace_source = (webui / "workspace.js").read_text()
    registered = set(re.findall(r'"(__fim[A-Za-z0-9_]+)"', workspace_source))
    existing = set()
    for path in webui.rglob("*.js"):
        existing.update(re.findall(r"window\.(__fim[A-Za-z0-9_]+)", path.read_text()))

    assert existing <= registered


def test_workspace_order_selection_and_ids_are_stable(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Closing a document preserves order and never makes its ID reusable."""
    state = drive(
        window,
        trigger="""window.__fimTestResult = (() => {
            const workspace = window.fim.workspace;
            const first = workspace.ids()[0];
            const initialCount = workspace.ids().length;
            const second = workspace.createDocument({ label: "second" });
            const third = workspace.createDocument({ label: "third" });
            workspace.select(first);
            workspace.closeDocument(second);
            const fourth = workspace.createDocument({ label: "fourth" });
            workspace.select(third);
            return {
                first,
                second,
                third,
                fourth,
                ids: workspace.ids(),
                selected: workspace.selectedId(),
                initialCount,
            };
        })();""",
        read="window.__fimTestResult",
    )

    assert state["first"] == "document-1"
    assert state["initialCount"] == 1
    assert state["second"] == "document-2"
    assert state["third"] == "document-3"
    assert state["fourth"] == "document-4"
    assert state["ids"] == ["document-1", "document-3", "document-4"]
    assert state["selected"] == "document-3"


def test_workspace_history_is_owned_by_each_document(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Moving one document's history cursor leaves another history intact."""
    state = drive(
        window,
        trigger="""window.__fimTestResult = (() => {
            const workspace = window.fim.workspace;
            const first = workspace.selectedId();
            workspace.recordHistory(first, "screen-help");
            workspace.recordHistory(first, "screen-run");
            const second = workspace.createDocument({}, { select: false });
            workspace.recordHistory(second, "screen-home");

            const firstBack = workspace.moveHistory(first, -1);
            const firstState = workspace.get(first);
            const secondStateBefore = workspace.get(second);
            const secondIndexBefore = secondStateBefore.historyIndex;
            const secondBack = workspace.moveHistory(second, -1);
            const secondStateAfter = workspace.get(second);
            return {
                firstHistory: firstState.history,
                firstIndex: firstState.historyIndex,
                firstBack,
                secondHistory: secondStateBefore.history,
                secondIndexBefore,
                secondBack,
                secondIndexAfter: secondStateAfter.historyIndex,
            };
        })();""",
        read="window.__fimTestResult",
    )

    assert state["firstHistory"] == [
        "screen-open-run",
        "screen-help",
        "screen-run",
    ]
    assert state["firstIndex"] == 1
    assert state["firstBack"] == "screen-help"
    assert state["secondHistory"] == ["screen-open-run", "screen-home"]
    assert state["secondIndexBefore"] == 1
    assert state["secondBack"] == "screen-open-run"
    assert state["secondIndexAfter"] == 0


def test_legacy_settle_aliases_route_to_the_selected_document(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Existing global flags read and write the selected document's signal."""
    state = drive(
        window,
        trigger="""window.__fimTestResult = (() => {
            const workspace = window.fim.workspace;
            const first = workspace.selectedId();
            const second = workspace.createDocument();
            window.__fimRunStartSettled = true;
            const secondSignal = workspace.settled(second, "runStart");

            workspace.select(first);
            const firstBefore = window.__fimRunStartSettled;
            window.__fimRunStartSettled = false;
            const firstSignal = workspace.settled(first, "runStart");

            workspace.select(second);
            return {
                firstBeforeMissing: typeof firstBefore === "undefined",
                firstSignal,
                secondSignal,
                selectedAlias: window.__fimRunStartSettled,
            };
        })();""",
        read="window.__fimTestResult",
    )

    assert state == {
        "firstBeforeMissing": True,
        "firstSignal": False,
        "secondSignal": True,
        "selectedAlias": True,
    }


def test_job_and_resource_indexes_follow_document_lifecycle(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Indexes resolve to their owner and are cleared when it closes."""
    state = drive(
        window,
        trigger="""window.__fimTestResult = (() => {
            const workspace = window.fim.workspace;
            const first = workspace.selectedId();
            const second = workspace.createDocument();
            workspace.setResourceKey(first, "run:one");
            workspace.attachJob(first, "job-one");
            const resourceOwner = workspace.documentForResource("run:one");
            const jobOwner = workspace.documentForJob("job-one");
            workspace.closeDocument(first);
            return {
                resourceOwner,
                jobOwner,
                resourceAfterClose:
                    workspace.documentForResource("run:one"),
                jobAfterClose: workspace.documentForJob("job-one"),
                selected: workspace.selectedId(),
                remaining: workspace.ids(),
                second,
            };
        })();""",
        read="window.__fimTestResult",
    )

    assert state["resourceOwner"] == "document-1"
    assert state["jobOwner"] == "document-1"
    assert state["resourceAfterClose"] is None
    assert state["jobAfterClose"] is None
    assert state["selected"] == state["second"] == "document-2"
    assert state["remaining"] == ["document-2"]
