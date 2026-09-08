"use strict";

/* The Compare workspace (botanist GUI design doc `20260907-claude-
 * sonnet-5-botanist-gui-redesign.md` §8): pick two or more previously
 * completed runs and overlay them -- "how does the conclusion change
 * as I vary this one knob" for real simulated runs, without
 * re-running anything.
 *
 * This first slice covers the small-multiples scatter half only: one
 * final-state deme-1-vs-2 panel per selected run, plus a legend naming
 * whichever configuration field(s) actually differ across the
 * selection (`Api.compare_runs`, reusing `reanalyze_trajectory`/
 * `scatter_panels` exactly as "Open a run…" already does -- "no new
 * engine computation," the design's own resolution-ledger entry for
 * this workspace). The trajectory-over-generations overlay the design
 * doc also describes needs its own new curve-plotting infrastructure
 * (no statistic-over-generations plot exists anywhere in this GUI
 * today, only a final-state scatter and point-in-time meters) and is
 * deferred to a later slice rather than folded in here.
 *
 * Each panel is drawn with `drawScatterCell` directly, not the shared
 * `drawScatter` helper (`scatter.js`) -- `drawScatter` sets a single,
 * page-wide `_currentPanel` for the one main run-view canvas' own
 * `ResizeObserver` callback to redraw from; calling it once per
 * compare panel would silently repoint that shared state at whichever
 * panel was drawn last, corrupting the main run view's own redraw the
 * next time its canvas resizes. `drawScatterCell` is the stateless
 * "draw this one panel into this one rect of this one context"
 * primitive `drawScatter` itself is already built on, safe to call
 * once per canvas with no shared state at all.
 */

const compareBanner = document.getElementById("compare-banner");
const compareRecentRunsBody = document.getElementById("compare-recent-runs-body");
const compareRunButton = document.getElementById("compare-run-button");
const compareBackButton = document.getElementById("compare-back-button");
const compareResults = document.getElementById("compare-results");
const compareLegend = document.getElementById("compare-legend");
const comparePanels = document.getElementById("compare-panels");

const _COMPARE_STATISTIC_NAMES = ["D", "G_ST", "E_ST", "K_ST", "H_S", "H_T"];
const _COMPARE_MINIMUM_RUNS = 2;

// The screen shown before Compare was opened, so "Back" returns there
// -- the same contract `explore.js`'s own `exploreReturnScreen`
// already established, mirrored here rather than shared, since the two
// screens' own return targets are otherwise independent.
let compareReturnScreen = "screen-run";

function showCompareBanner(message) {
    if (!message) {
        compareBanner.hidden = true;
        compareBanner.textContent = "";
        return;
    }
    compareBanner.hidden = false;
    compareBanner.textContent = message;
}

/**
 * Every checked, non-batch row's own trajectory path, in row order.
 * @returns {string[]}
 */
function checkedTrajectoryPaths() {
    return Array.from(
        compareRecentRunsBody.querySelectorAll('input[type="checkbox"]:checked')
    ).map((checkbox) => checkbox.dataset.trajectoryPath);
}

function updateCompareRunButton() {
    compareRunButton.disabled = checkedTrajectoryPaths().length < _COMPARE_MINIMUM_RUNS;
}

async function refreshCompareRecentRuns() {
    // Mirrors `open-run.js`'s own `refreshRecentRuns`/`window.__fimOpen
    // RunRecentRunsLoaded` precedent exactly, for the identical reason:
    // this call is fired without being awaited by `showCompareScreen`,
    // so a real filesystem scan never blocks the screen transition, and
    // a test needs its own observable "this async call actually
    // settled" signal rather than treating "screen visible" as proof.
    window.__fimCompareRecentRunsLoaded = false;
    compareRecentRunsBody.replaceChildren();
    const runs = await window.pywebview.api.list_recent_runs();
    for (const run of runs) {
        const row = document.createElement("tr");
        const checkboxCell = document.createElement("td");
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        if (run.isBatch) {
            // Design §0, §4.0 #9's own "batch has no single trajectory"
            // rule, applied here exactly as `open-run.js` already
            // applies it: no checkbox to check at all, rather than one
            // that silently could never be compared.
            checkbox.disabled = true;
        } else {
            checkbox.dataset.trajectoryPath = run.trajectoryPath;
            checkbox.addEventListener("change", updateCompareRunButton);
        }
        checkboxCell.appendChild(checkbox);
        row.appendChild(checkboxCell);
        for (const value of [run.runId, run.endedAt, run.label]) {
            const cell = document.createElement("td");
            cell.textContent = value;
            row.appendChild(cell);
        }
        compareRecentRunsBody.appendChild(row);
    }
    updateCompareRunButton();
    window.__fimCompareRecentRunsLoaded = true;
}

/**
 * Draw one run's own panel into a canvas already attached to the live
 * DOM -- `canvas.clientWidth`/`clientHeight` (sized via `app.css`'s own
 * `.compare-panel canvas` rule) read back as `0` from a still-detached
 * element or one inside a still-`hidden` ancestor, so this must run
 * only after both the canvas itself and `#compare-results` are already
 * visible (unlike the main run canvas, this first slice does not track
 * window resizes for these).
 * @param {HTMLCanvasElement} canvas
 * @param {{x_label?: string, y_label?: string, kind?: string, points: object[]}} panel
 */
function drawComparePanel(canvas, panel) {
    canvas.width = canvas.clientWidth;
    canvas.height = canvas.clientHeight;
    const context = canvas.getContext("2d");
    context.clearRect(0, 0, canvas.width, canvas.height);
    drawScatterCell(
        context,
        { x: 0, y: 0, width: canvas.width, height: canvas.height },
        panel,
        { padding: SINGLE_PANEL_PADDING, tickFontSize: SINGLE_PANEL_TICK_FONT, markerScale: 1 }
    );
}

/**
 * Render the legend table naming every field that actually differs
 * across the compared runs -- one column per run, one row per
 * differing field, plus a header row of run IDs. An empty
 * `differingFields` (every compared run shares the identical
 * configuration) renders a single explanatory note instead of an
 * otherwise-empty table.
 * @param {object[]} runs
 * @param {string[]} differingFields
 */
function renderCompareLegend(runs, differingFields) {
    compareLegend.replaceChildren();
    if (differingFields.length === 0) {
        const row = document.createElement("tr");
        const cell = document.createElement("td");
        cell.textContent = "No configuration differences among the selected runs.";
        row.appendChild(cell);
        compareLegend.appendChild(row);
        return;
    }
    const headerRow = document.createElement("tr");
    headerRow.appendChild(document.createElement("th"));
    for (const run of runs) {
        const th = document.createElement("th");
        th.textContent = run.runId;
        headerRow.appendChild(th);
    }
    compareLegend.appendChild(headerRow);
    for (const field of differingFields) {
        const row = document.createElement("tr");
        const label = document.createElement("th");
        label.textContent = field;
        row.appendChild(label);
        for (const run of runs) {
            const cell = document.createElement("td");
            cell.textContent = run.configSummary[field];
            row.appendChild(cell);
        }
        compareLegend.appendChild(row);
    }
}

/**
 * Render one small-multiples panel per compared run: its own canvas,
 * a config-summary caption, and its own final statistics. Every
 * canvas is built and appended to the live DOM first, then sized and
 * drawn in a second pass (`drawComparePanel`'s own docstring has why)
 * -- the caller is responsible for `#compare-results` itself already
 * being visible before this runs.
 * @param {object[]} runs
 */
function renderComparePanels(runs) {
    comparePanels.replaceChildren();
    const pending = [];
    for (const run of runs) {
        const cell = document.createElement("div");
        cell.className = "compare-panel";
        const heading = document.createElement("h3");
        heading.textContent = run.runId;
        cell.appendChild(heading);
        const canvas = document.createElement("canvas");
        cell.appendChild(canvas);
        pending.push({ canvas, panel: run.panel });
        const stats = document.createElement("p");
        stats.className = "hint";
        stats.textContent = _COMPARE_STATISTIC_NAMES.map(
            (name) => `${name}=${run.statistics[name]}`
        ).join(" ");
        cell.appendChild(stats);
        comparePanels.appendChild(cell);
    }
    for (const { canvas, panel } of pending) {
        drawComparePanel(canvas, panel);
    }
}

compareRunButton.addEventListener("click", async () => {
    window.__fimCompareResultsReady = false;
    const paths = checkedTrajectoryPaths();
    const result = await window.pywebview.api.compare_runs(paths);
    if (!result.ok) {
        showCompareBanner(result.message);
        compareResults.hidden = true;
        window.__fimCompareResultsReady = true;
        return;
    }
    showCompareBanner("");
    // `#compare-results` must already be visible before the panels
    // below are built: `drawComparePanel` reads each canvas's own
    // `clientWidth`/`clientHeight`, which read `0` from an element
    // inside a still-`hidden` ancestor.
    compareResults.hidden = false;
    renderCompareLegend(result.runs, result.differingFields);
    renderComparePanels(result.runs);
    window.__fimCompareResultsReady = true;
});

compareBackButton.addEventListener("click", () => {
    window.fim.showScreen(compareReturnScreen);
});

window.fim.menu.compareRuns = function compareRuns() {
    const currentlyVisible = document.querySelector(".screen:not([hidden])");
    if (currentlyVisible !== null && currentlyVisible.id !== "screen-compare") {
        compareReturnScreen = currentlyVisible.id;
    }
    showCompareBanner("");
    compareResults.hidden = true;
    window.fim.showScreen("screen-compare");
    refreshCompareRecentRuns();
};
