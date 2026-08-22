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
 */

// D/G_ST/E_ST/K_ST/H_S/H_T are every named differentiation/heterozygosity
// statistic this project reports, and each is naturally bounded to
// [0, 1] by construction (Jost's D, Nei's G_ST, and the heterozygosities
// alike) -- the one fixed scale every confidence-interval bar below is
// drawn against, with no per-statistic dynamic scaling needed.
const CI_BAR_MIN = 0.0;
const CI_BAR_MAX = 1.0;

const batchResultsCanvas = document.getElementById("batch-results-canvas");
const batchResultsRunId = document.getElementById("batch-results-run-id");
const batchResultsSummary = document.getElementById("batch-results-summary");
const batchResultsTableBody = document.getElementById("batch-results-table-body");
const batchNewRunButton = document.getElementById("batch-new-run-button");
const batchOpenFolderButton = document.getElementById("batch-open-folder-button");

let currentBatchOutputDirectory = null;

function percentageWithin(value, min, max) {
    const clamped = Math.min(Math.max(value, min), max);
    return ((clamped - min) / (max - min)) * 100;
}

function buildOmittedCiBar(name) {
    const row = document.createElement("div");
    row.className = "ci-bar";
    const label = document.createElement("span");
    label.className = "ci-bar-label";
    label.textContent = name;
    row.appendChild(label);
    const omitted = document.createElement("span");
    omitted.className = "ci-bar-omitted";
    // Design §4.4: "a statistic omitted from summary.json still renders
    // as explicitly omitted, not blank" -- `_batch_done_payload` leaves
    // `name` out of `summary` entirely rather than sending a null/undefined
    // placeholder, matching `replicate_summary`'s own documented "omitted
    // entirely rather than raising, since a single point has no interval."
    omitted.textContent = "omitted (fewer than two defined replicates)";
    row.appendChild(omitted);
    return row;
}

function buildCiBar(name, interval) {
    if (interval === undefined) {
        return buildOmittedCiBar(name);
    }
    // `interval.mean`/`.low`/`.high` arrive pre-formatted for display
    // (`format_statistic`, a `%.6g`-style string) -- parsed back into a
    // number here only to compute bar *geometry*, never to reformat the
    // label text itself, so this is not a second, client-side
    // implementation of the same display-formatting rule.
    const low = percentageWithin(Number(interval.low), CI_BAR_MIN, CI_BAR_MAX);
    const high = percentageWithin(Number(interval.high), CI_BAR_MIN, CI_BAR_MAX);
    const mean = percentageWithin(Number(interval.mean), CI_BAR_MIN, CI_BAR_MAX);

    const row = document.createElement("div");
    row.className = "ci-bar";

    const label = document.createElement("span");
    label.className = "ci-bar-label";
    label.textContent = name;
    row.appendChild(label);

    const track = document.createElement("div");
    track.className = "ci-bar-track";
    const fill = document.createElement("div");
    fill.className = "ci-bar-fill";
    fill.style.left = `${low}%`;
    fill.style.width = `${Math.max(high - low, 0)}%`;
    track.appendChild(fill);
    const meanMark = document.createElement("div");
    meanMark.className = "ci-bar-mean";
    meanMark.style.left = `${mean}%`;
    track.appendChild(meanMark);
    row.appendChild(track);

    const value = document.createElement("span");
    value.className = "ci-bar-value";
    value.textContent =
        `${interval.mean} [${interval.low}, ${interval.high}] (n=${interval.sampleCount})`;
    row.appendChild(value);

    return row;
}

function renderSummary(summary) {
    batchResultsSummary.replaceChildren();
    for (const name of STATISTIC_NAMES) {
        batchResultsSummary.appendChild(buildCiBar(name, summary[name]));
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
        // Same deliberate first-panel-only scope line `results.js`'s own
        // `showResults` documents for the `3 <= d <= 6` pairwise case.
        const panel = panels[0];
        drawScatter(batchResultsCanvas, panel.points, {
            xLabel: panel.x_label,
            yLabel: panel.y_label,
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
