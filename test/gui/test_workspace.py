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


def test_completed_view_state_restores_for_each_document(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Completed statistics and trajectory choices survive document switches."""
    state = drive(
        window,
        ready="STATISTIC_NAMES.length > 0",
        trigger="""window.__fimTestResult = (() => {
            const workspace = window.fim.workspace;
            const first = workspace.selectedId();
            const firstState = workspace.get(first).completedView;
            const statistic = TRAJECTORY_STATISTIC_NAMES.find(
                (name) => !DEFAULT_HIDDEN_TRAJECTORY_STATISTICS.includes(name)
            );
            const finalStatistics = (value) =>
                Object.fromEntries(STATISTIC_NAMES.map((name) => [name, value]));
            Object.assign(firstState, {
                hasCompletedResult: true,
                isBatch: false,
                completedFinalStatistics: finalStatistics("0.42"),
                completedTrajectoryGenerations: [0, 10],
                completedTrajectoryHistories: {
                    [statistic]: [0.24, 0.42],
                },
                completedGenerationCount: 10,
                completedEffectiveAlleles: null,
                completedReport: {},
                currentScrubGeneration: 0,
                currentScrubIsFinal: false,
            });
            firstState.hiddenTrajectoryStatistics.add(statistic);
            window.fim.setRunViewState("completed");
            window.fim.setTrajectoryDisplay("trailing_mean");
            window.fim.setTrajectoryBandWidth(2);
            window.fim.setWatchedStatistics([statistic], true);

            const second = workspace.createDocument();
            const secondState = workspace.get(second).completedView;
            Object.assign(secondState, {
                hasCompletedResult: true,
                isBatch: false,
                completedFinalStatistics: finalStatistics("0.17"),
                completedEffectiveAlleles: null,
                completedReport: {},
                currentScrubGeneration: null,
                currentScrubIsFinal: true,
            });
            secondState.hiddenTrajectoryStatistics.delete(statistic);
            window.fim.setTrajectoryDisplay("every_generation");
            window.fim.setTrajectoryBandWidth(1);

            workspace.select(first);
            const firstView = {
                display: document.getElementById("run-trajectory-display").value,
                bandWidth:
                    document.getElementById("run-trajectory-band-width").value,
                statistic: document
                    .getElementById(`stat-${statistic}`)
                    .querySelector(".stat-value").textContent,
                hidden: workspace.get(first).completedView
                    .hiddenTrajectoryStatistics.has(statistic),
                watched: document
                    .getElementById(`stat-${statistic}`)
                    .classList.contains("stat-watched"),
            };
            workspace.select(second);
            const secondView = {
                display: document.getElementById("run-trajectory-display").value,
                bandWidth:
                    document.getElementById("run-trajectory-band-width").value,
                statistic: document
                    .getElementById(`stat-${statistic}`)
                    .querySelector(".stat-value").textContent,
                hidden: workspace.get(second).completedView
                    .hiddenTrajectoryStatistics.has(statistic),
            };
            return {firstView, secondView};
        })();""",
        read="window.__fimTestResult",
    )

    assert state["firstView"] == {
        "display": "trailing_mean",
        "bandWidth": "2",
        "statistic": "0.24",
        "hidden": True,
        "watched": True,
    }
    assert state["secondView"] == {
        "display": "every_generation",
        "bandWidth": "1",
        "statistic": "0.17",
        "hidden": False,
    }


def test_completed_pair_batch_and_saved_note_are_document_local(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Switching documents restores each batch's pair and saved-result note."""
    state = drive(
        window,
        trigger="""window.__fimTestResult = (() => {
            const workspace = window.fim.workspace;
            const first = workspace.selectedId();
            window.fim.setWatchedStatistics([], true);
            const firstState = workspace.get(first).completedView;
            Object.assign(firstState, {
                hasCompletedResult: true,
                isBatch: true,
                completedPair: [1, 3],
                completedBatchSummary: {},
                completedBatchEffectiveAlleles: null,
                completedBatchReplicates: [],
                completedBatchP0Statistics: null,
                pooledConvergenceHistories: null,
                completedDemeCount: null,
                completedPanels: null,
                savedResultNote: {
                    outputDirectory: "/runs/first",
                    readOnly: true,
                },
            });
            window.fim.setCompletedOutputDirectory("/runs/first");
            window.fim.setRunViewState("completed");

            const second = workspace.createDocument();
            window.fim.setWatchedStatistics([], true);
            const secondState = workspace.get(second).completedView;
            Object.assign(secondState, {
                hasCompletedResult: true,
                isBatch: true,
                completedPair: [2, 4],
                completedBatchSummary: {},
                completedBatchEffectiveAlleles: null,
                completedBatchReplicates: [],
                completedBatchP0Statistics: null,
                pooledConvergenceHistories: null,
                completedDemeCount: null,
                completedPanels: null,
                savedResultNote: {
                    outputDirectory: "/runs/second",
                    readOnly: false,
                },
            });
            window.fim.setCompletedOutputDirectory("/runs/second");

            workspace.select(first);
            const firstView = {
                pair: [...firstState.completedPair],
                outputDirectory: window.fim.getCompletedOutputDirectory(),
                savedNote: document.getElementById("run-saved-result-text")
                    .textContent,
                batchTableHidden:
                    document.getElementById("batch-results-table").hidden,
            };
            workspace.select(second);
            const secondView = {
                pair: [...secondState.completedPair],
                outputDirectory: window.fim.getCompletedOutputDirectory(),
                savedNote: document.getElementById("run-saved-result-text")
                    .textContent,
                batchTableHidden:
                    document.getElementById("batch-results-table").hidden,
            };
            return {firstView, secondView};
        })();""",
        read="window.__fimTestResult",
    )

    assert state["firstView"] == {
        "pair": [1, 3],
        "outputDirectory": "/runs/first",
        "savedNote": "Run this example to see its trajectories",
        "batchTableHidden": False,
    }
    assert state["secondView"] == {
        "pair": [2, 4],
        "outputDirectory": "/runs/second",
        "savedNote": (
            "This run's trajectory is not saved. Run it again to see its trajectories"
        ),
        "batchTableHidden": False,
    }


def test_live_progress_is_owned_by_the_addressed_document(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Background scalar and batch progress changes only their owner model."""
    state = drive(
        window,
        ready="STATISTIC_NAMES.length > 0",
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


def test_run_controls_title_settings_and_saved_run_are_document_local(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Run metadata, overrides, title, and saved-run editing follow tabs."""
    state = drive(
        window,
        trigger="""window.__fimTestResult = (() => {
            const workspace = window.fim.workspace;
            const first = workspace.selectedId();
            window.fim.setRunDetails("First run", "First description");
            window.fim.selectStudyForNewRun("study-default");
            window.fim.setRunCardTitle({
                phase: "running",
                runName: "First run",
            });
            window.fim.applyLoadedRunSettings({
                runSettings: {n_replicates: "11"},
                runSettingDifferences: [],
            });
            workspace.get(first).savedRunState = {
                editingObjectId: "saved-first",
                activeSavedRunId: "active-first",
                pausePending: false,
            };

            const second = workspace.createDocument();
            window.fim.setRunDetails("Second run", "Second description");
            window.fim.preselectNewStudy();
            window.fim.setRunCardTitle({
                phase: "running",
                runName: "Second run",
            });
            window.fim.applyLoadedRunSettings({
                runSettings: {n_replicates: "22"},
                runSettingDifferences: [],
            });

            const secondView = {
                name: document.getElementById("run-name-input").value,
                description:
                    document.getElementById("run-description-input").value,
                study: document.getElementById("run-study-select").value,
                title: document.getElementById("run-plot-title").textContent,
                settings: window.fim.originalRunSettings(),
                replicates: collectFormValues().n_replicates,
                editing: window.fim.getEditingObjectId(),
                pauseHidden:
                    document.getElementById("pause-run-button").hidden,
            };
            workspace.select(first);
            const firstView = {
                name: document.getElementById("run-name-input").value,
                description:
                    document.getElementById("run-description-input").value,
                study: document.getElementById("run-study-select").value,
                title: document.getElementById("run-plot-title").textContent,
                settings: window.fim.originalRunSettings(),
                replicates: collectFormValues().n_replicates,
                editing: window.fim.getEditingObjectId(),
                pauseHidden:
                    document.getElementById("pause-run-button").hidden,
            };
            return {firstView, secondView};
        })();""",
        read="window.__fimTestResult",
    )

    assert state["firstView"] == {
        "name": "First run",
        "description": "First description",
        "study": "",
        "title": "FIM simulation — First run — in progress",
        "settings": {"n_replicates": "11"},
        "replicates": "11",
        "editing": "saved-first",
        "pauseHidden": False,
    }
    assert state["secondView"] == {
        "name": "Second run",
        "description": "Second description",
        "study": "__new__",
        "title": "FIM simulation — Second run — in progress",
        "settings": {"n_replicates": "22"},
        "replicates": "22",
        "editing": None,
        "pauseHidden": True,
    }


def test_configure_form_and_composite_editors_restore_per_document(
    window: webview.Window, drive: Callable[..., Any]
) -> None:
    """Form values and composite-editor modes follow the selected document."""
    state = drive(
        window,
        trigger="""window.__fimTestPromise = (async () => {
            const workspace = window.fim.workspace;
            const first = workspace.selectedId();
            function setField(name, value) {
                const field = document.getElementById(`field-${name}`);
                field.value = value;
                field.dispatchEvent(new Event("input", {bubbles: true}));
            }
            function setMode(name, value) {
                const radio = document.querySelector(
                    `input[name="${name}"][value="${value}"]`
                );
                radio.checked = true;
                radio.dispatchEvent(new Event("change", {bubbles: true}));
            }

            setField("d", "3");
            setField("N", "11, 22, 33");
            setField(
                "m_matrix_json",
                "[[0.9,0.1,0],[0.2,0.7,0.1],[0,0.3,0.7]]"
            );
            setField("loci_json", '[{"locus_id":7,"length":50}]');
            setField("p0_json", '[[{"0":0.8,"1":0.2}]]');
            setMode("m_mode", "matrix");
            setMode("loci_mode", "custom");
            setMode("initial_conditions_mode", "explicit_p0");
            setMode("n_mode", "per_deme");
            await window.fim.revalidateForm();

            const second = workspace.createDocument();
            setField("d", "5");
            setField("N", "99");
            setField("m_matrix_json", "[[0.6,0.4]]");
            setField("loci_json", '[{"locus_id":4,"length":60}]');
            setField("p0_json", '[[{"1":1}]]');
            setMode("n_mode", "same");
            await window.fim.revalidateForm();
            workspace.select(first);
            const firstView = {
                d: document.getElementById("field-d").value,
                N: document.getElementById("field-N").value,
                nMode: document.querySelector(
                    'input[name="n_mode"]:checked'
                ).value,
                migrationMode: document.querySelector(
                    'input[name="m_mode"]:checked'
                ).value,
                lociMode: document.querySelector(
                    'input[name="loci_mode"]:checked'
                ).value,
                initialMode: document.querySelector(
                    'input[name="initial_conditions_mode"]:checked'
                ).value,
                matrix: document.getElementById("field-m_matrix_json").value,
                matrixCell: document.querySelector(".matrix-cell").value,
                loci: document.getElementById("field-loci_json").value,
                locusId: document.querySelector(".loci-id-cell").value,
                p0: document.getElementById("field-p0_json").value,
                p0Cell: document.querySelector(".p0-cell").value,
            };
            workspace.select(second);
            const secondView = {
                d: document.getElementById("field-d").value,
                N: document.getElementById("field-N").value,
                nMode: document.querySelector(
                    'input[name="n_mode"]:checked'
                ).value,
                migrationMode: document.querySelector(
                    'input[name="m_mode"]:checked'
                ).value,
                lociMode: document.querySelector(
                    'input[name="loci_mode"]:checked'
                ).value,
                initialMode: document.querySelector(
                    'input[name="initial_conditions_mode"]:checked'
                ).value,
                matrixCell: document.querySelector(".matrix-cell").value,
                locusId: document.querySelector(".loci-id-cell").value,
                p0Cell: document.querySelector(".p0-cell").value,
            };
            return {firstView, secondView};
        })().then((result) => window.__fimTestResult = result);""",
        read="window.__fimTestResult",
        ready="window.__fimRunViewReady === true",
    )

    assert state["firstView"]["d"] == "3"
    assert state["firstView"]["N"] == "11, 22, 33"
    assert state["firstView"]["nMode"] == "per_deme"
    assert state["firstView"]["migrationMode"] == "matrix"
    assert state["firstView"]["lociMode"] == "custom"
    assert state["firstView"]["initialMode"] == "explicit_p0"
    assert state["firstView"]["matrixCell"] == "0.9"
    assert state["firstView"]["locusId"] == "7"
    assert state["firstView"]["p0Cell"] == "0:0.8,1:0.2"
    assert state["secondView"]["d"] == "5"
    assert state["secondView"]["N"] == "99"
    assert state["secondView"]["nMode"] == "same"
    assert state["secondView"]["migrationMode"] == "matrix"
    assert state["secondView"]["lociMode"] == "custom"
    assert state["secondView"]["initialMode"] == "explicit_p0"
    assert state["secondView"]["matrixCell"] == "0.6"
    assert state["secondView"]["locusId"] == "4"
    assert state["secondView"]["p0Cell"] == "1:1"
