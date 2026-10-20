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


def test_scrubber_and_graph_state_restore_for_each_document(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Frames, playback, graph choices, and scatter style stay document-local."""
    state = drive(
        window,
        trigger="""window.__fimTestResult = (() => {
            const workspace = window.fim.workspace;
            const first = workspace.selectedId();
            const drawFrame = () => {};
            window.fim.setScrubberFrames(
                [{generation: 10}, {generation: 20}, {generation: 30}],
                drawFrame,
                1
            );
            window.fim.setGraphAvailable("scatter", true);
            window.fim.showGraph("scatter");
            window.fim.setGraphColumns(2);
            window.fim.setScatterStyle("density");
            document.getElementById("scrubber-step-forward").click();
            const firstBefore = window.fim.getScrubberState();

            const second = workspace.createDocument();
            window.fim.setScrubberFrames(
                [{generation: 100}, {generation: 200}],
                drawFrame,
                0
            );
            window.fim.setGraphAvailable("trajectory", true);
            window.fim.showGraph("trajectory");
            window.fim.setGraphColumns(1);
            window.fim.setScatterStyle("circles");
            const secondState = window.fim.getScrubberState();
            const secondGraph = workspace.get(second).graphStage.graphColumns;
            const secondGraphs = window.fim.getVisibleGraphs();
            const secondStyle = window.fim.getScatterStyle();

            workspace.select(first);
            const restored = window.fim.getScrubberState();
            const result = {
                first,
                second,
                firstBefore,
                restored,
                secondState,
                firstGraph: workspace.get(first).graphStage.graphColumns,
                secondGraph,
                firstGraphs: window.fim.getVisibleGraphs(),
                secondGraphs,
                firstStyle: window.fim.getScatterStyle(),
                secondStyle,
                slider: document.getElementById("scrubber-range").value,
                label: document.getElementById("scrubber-label").textContent,
            };
            window.fim.resetScrubber();
            return result;
        })();""",
        read="window.__fimTestResult",
    )

    assert state["firstBefore"] == {
        "generations": [10, 20, 30],
        "currentIndex": 1,
        "playDirection": 1,
        "mode": "replay",
        "isLiveTracking": False,
    }
    assert state["restored"] == state["firstBefore"]
    assert state["secondState"]["generations"] == [100, 200]
    assert state["secondState"]["currentIndex"] == 0
    assert state["secondState"]["playDirection"] == 0
    assert state["firstGraph"] == 2
    assert state["secondGraph"] == 1
    assert state["firstGraphs"] == ["scatter"]
    assert state["secondGraphs"] == ["trajectory"]
    assert state["firstStyle"] == "density"
    assert state["secondStyle"] == "circles"
    assert state["slider"] == "1"
    assert state["label"] == "Generation 20"


def test_live_progress_is_owned_by_the_addressed_document(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Background scalar and batch progress changes only their owner model."""
    state = drive(
        window,
        trigger="""window.__fimTestResult = (() => {
            const workspace = window.fim.workspace;
            const scalar = workspace.selectedId();
            const batch = workspace.createDocument({}, { select: false });
            const selected = workspace.createDocument();
            workspace.attachJob(scalar, "job-scalar");
            workspace.attachJob(batch, "job-batch");
            const trajectoryName = TRAJECTORY_STATISTIC_NAMES[0];
            const progressBefore =
                document.getElementById("progress-generation-label").textContent;

            const scalarPayload = {
                generation: 10,
                maxGenerations: 100,
                demeCount: 2,
                statistics: {[trajectoryName]: "0.25"},
                panels: [],
            };
            window.fim.onRunProgress(scalarPayload);
            window.fim.onJobEvent({
                jobId: "job-scalar",
                sequence: 1,
                type: "progress",
                payload: scalarPayload,
            });
            const batchPayload = {
                replicateCount: 2,
                meanReportedGeneration: 7,
                maxGenerations: 100,
                demeCount: 2,
                initialStatistics: {
                    [trajectoryName]: {
                        mean: "0.5",
                        low: "0.4",
                        high: "0.6",
                        sampleCount: 2,
                    },
                },
                statistics: {
                    [trajectoryName]: {
                        mean: "0.3",
                        low: "0.2",
                        high: "0.4",
                        sampleCount: 2,
                    },
                },
                panels: [],
            };
            window.fim.onBatchProgress(batchPayload);
            window.fim.onJobEvent({
                jobId: "job-batch",
                sequence: 1,
                type: "progress",
                payload: batchPayload,
            });

            const scalarState = workspace.get(scalar).liveRun;
            const batchState = workspace.get(batch).liveRun;
            return {
                scalar,
                batch,
                selected,
                scalarGenerations: scalarState.liveTrajectoryGenerations,
                trajectoryName,
                scalarHistory:
                    scalarState.liveTrajectoryHistories[trajectoryName],
                scalarBatch: scalarState.liveBatchTrajectory,
                batchGenerations: batchState.liveTrajectoryGenerations,
                batchTrajectory:
                    batchState.liveBatchTrajectory[trajectoryName],
                batchScalar: batchState.liveTrajectoryHistories,
                selectedProgress:
                    document.getElementById("progress-generation-label").textContent,
                progressBefore,
            };
        })();""",
        read="window.__fimTestResult",
    )

    assert state["scalarGenerations"] == [10]
    assert state["trajectoryName"]
    assert state["scalarHistory"] == [0.25]
    assert state["scalarBatch"] == {}
    assert state["batchGenerations"] == []
    assert state["batchTrajectory"] == [
        {
            "generation": 0,
            "mean": "0.5",
            "low": "0.4",
            "high": "0.6",
            "sampleCount": 2,
        },
        {
            "generation": 7,
            "mean": "0.3",
            "low": "0.2",
            "high": "0.4",
            "sampleCount": 2,
        },
    ]
    assert state["batchScalar"] == {}
    assert state["selectedProgress"] == state["progressBefore"]
