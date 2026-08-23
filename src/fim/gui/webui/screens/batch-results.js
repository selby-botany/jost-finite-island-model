"use strict";

/* Screen 4: batch results (design doc §4.4) -- gains a new primary panel
 * over the original design (the pooled multi-replicate scatter,
 * `fim.viz.scatter.pooled_scatter_panels`, design §0.5); the replicate
 * table and confidence-interval bars are kept, not replaced.
 *
 * `fim.showBatchResults` is called once, by `progress.js`'s own
 * `onBatchDone` handler, with the exact payload `_batch_done_payload`
 * built in `app.py` -- every statistic already formatted server-side
 * (`format_statistic`), the same "the client never reimplements
 * Python's own display formatting" rule Screen 3 (`results.js`) follows.
 * This file's `STATISTIC_NAMES` reuses `results.js`'s own top-level
 * constant directly rather than redeclaring it -- both files are
 * classic, non-module scripts sharing one global scope (`index.html`
 * has no `type="module"` on either `<script>` tag), so a second
 * `const STATISTIC_NAMES` here would be a `SyntaxError`, not a shadow.
 * The confidence-interval meter itself (`buildCiMeter`/
 * `buildOmittedMeter`) now lives in the shared `meters.js`
 * (visualization-and-config-editors design §3.3) -- extracted from here
 * unchanged, so `results.js`'s own scalar statistics can use the
 * identical widget.
 */

const batchResultsCanvas = document.getElementById("batch-results-canvas");
const batchResultsRunId = document.getElementById("batch-results-run-id");
const batchResultsSummary = document.getElementById("batch-results-summary");
const batchResultsTableBody = document.getElementById("batch-results-table-body");
const batchNewRunButton = document.getElementById("batch-new-run-button");
const batchOpenFolderButton = document.getElementById("batch-open-folder-button");
const batchResultsDemePairSelector = document.getElementById(
    "batch-results-deme-pair-selector"
);
const batchResultsXDeme = document.getElementById("batch-results-x-deme");
const batchResultsYDeme = document.getElementById("batch-results-y-deme");
const batchResultsShowPairButton = document.getElementById(
    "batch-results-show-pair-button"
);
const batchResultsShowOverviewButton = document.getElementById(
    "batch-results-show-overview-button"
);

let currentBatchOutputDirectory = null;

// Design §4.4: "a statistic omitted from summary.json still renders as
// explicitly omitted, not blank" -- `_batch_done_payload` leaves `name`
// out of `summary` entirely rather than sending a null/undefined
// placeholder, matching `replicate_summary`'s own documented "omitted
// entirely rather than raising, since a single point has no interval."
const OMITTED_SUMMARY_TEXT = "omitted (fewer than two defined replicates)";

function renderSummary(summary) {
    batchResultsSummary.replaceChildren();
    for (const name of STATISTIC_NAMES) {
        const interval = summary[name];
        const meter =
            interval === undefined
                ? buildOmittedMeter(name, OMITTED_SUMMARY_TEXT)
                : buildCiMeter(name, interval);
        batchResultsSummary.appendChild(meter);
    }
}

function renderTable(replicates) {
    batchResultsTableBody.replaceChildren();
    for (const replicate of replicates) {
        const row = document.createElement("tr");
        const outcome = replicate.converged
            ? `Converged (${replicate.reason})`
            : `Not converged (${replicate.reason})`;
        const cells = [
            replicate.index,
            replicate.runId,
            replicate.generation,
            outcome,
            ...STATISTIC_NAMES.map((name) => replicate.statistics[name]),
        ];
        for (const value of cells) {
            const cell = document.createElement("td");
            cell.textContent = String(value);
            row.appendChild(cell);
        }
        // "Open replicate" (design §4.4): the exact same operation as
        // Screen 6's own "Open" over one replicate's own trajectory --
        // `replicate.trajectoryPath` is already joined server-side
        // (`_batch_done_payload`'s own docstring), so this click never
        // does any path logic of its own.
        const openCell = document.createElement("td");
        const openButton = document.createElement("button");
        openButton.type = "button";
        openButton.textContent = "Open";
        openButton.addEventListener("click", async () => {
            const result = await window.pywebview.api.open_run({
                trajectoryPath: replicate.trajectoryPath,
            });
            if (result.ok) {
                window.fim.showResults(result);
            }
        });
        openCell.appendChild(openButton);
        row.appendChild(openCell);
        batchResultsTableBody.appendChild(row);
    }
}

window.fim.showBatchResults = function showBatchResults(payload) {
    currentBatchOutputDirectory = payload.outputDirectory;
    batchResultsRunId.textContent = payload.runId;
    renderSummary(payload.summary);
    renderTable(payload.replicates);
    const panels = payload.panels;
    if (panels && panels.length > 0) {
        // Same "draw every panel" rule `results.js`'s own `showResults`
        // documents (visualization-and-config-editors design §3.1) --
        // supersedes the prior deliberate first-panel-only scope line.
        const drawOverview = () => {
            if (panels.length === 1) {
                drawScatter(batchResultsCanvas, panels[0].points, {
                    xLabel: panels[0].x_label,
                    yLabel: panels[0].y_label,
                });
            } else {
                drawScatterGrid(batchResultsCanvas, panels);
            }
        };
        drawOverview();
        window.fim.wireDemePairSelector({
            canvas: batchResultsCanvas,
            xSelect: batchResultsXDeme,
            ySelect: batchResultsYDeme,
            showPairButton: batchResultsShowPairButton,
            showOverviewButton: batchResultsShowOverviewButton,
            container: batchResultsDemePairSelector,
            demeCount: payload.demeCount,
            drawOverview,
            getOutputDirectory: () => currentBatchOutputDirectory,
            bridgeMethod: (outputDirectory, x, y) =>
                window.pywebview.api.get_batch_deme_pair_panel(outputDirectory, x, y),
        });
    }
    window.fim.showScreen("screen-batch-results");
};

batchNewRunButton.addEventListener("click", () => {
    window.fim.showScreen("screen-input");
});

batchOpenFolderButton.addEventListener("click", () => {
    if (currentBatchOutputDirectory !== null) {
        window.pywebview.api.open_output_folder(currentBatchOutputDirectory);
    }
});
