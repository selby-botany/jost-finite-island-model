"use strict";

/* The Compare workspace (botanist GUI design doc `20260907-claude-
 * sonnet-5-botanist-gui-redesign.md` §8): pick two or more previously
 * completed runs and overlay them -- "how does the conclusion change
 * as I vary this one knob" for real simulated runs, without
 * re-running anything.
 *
 * Two overlays, both from one `Api.compare_runs` call (no new engine
 * computation, design's own resolution-ledger entry for this
 * workspace): a small-multiples scatter, one final-state deme-1-vs-2
 * panel per selected run, and a trajectory-over-generations overlay,
 * one statistic at a time (a selector, `compareTrajectoryStatistic`,
 * picks which of the six), colored by run rather than by statistic --
 * every run's own full six-statistic sampled history already arrived
 * in the one `compare_runs` response (`reanalyze_trajectory`/
 * `sampled_statistic_history`, both reused exactly as "Open a run…"/
 * the animation screen's own frame sampler already do), so switching
 * the selector redraws instantly, client-side, with no further bridge
 * call. `compareRuns` (below) holds that response between the initial
 * fetch and any later redraw.
 *
 * Each small-multiples panel is drawn with `drawScatterCell` directly,
 * not the shared `drawScatter` helper (`scatter.js`) -- `drawScatter`
 * sets a single, page-wide `_currentPanel` for the one main run-view
 * canvas' own `ResizeObserver` callback to redraw from; calling it once
 * per compare panel would silently repoint that shared state at
 * whichever panel was drawn last, corrupting the main run view's own
 * redraw the next time its canvas resizes. `drawScatterCell` is the
 * stateless "draw this one panel into this one rect of this one
 * context" primitive `drawScatter` itself is already built on, safe to
 * call once per canvas with no shared state at all.
 *
 * The trajectory overlay is deliberately a new, separate drawing
 * function (`drawCompareTrajectory`, below) rather than a generalized
 * `run-view-completed.js`'s own `drawTrajectoryCurve` -- that function
 * plots several statistics at once, colored *by statistic*, sharing one
 * run's own generation range; this one plots one statistic at a time,
 * colored *by run*, across two or more runs whose own generation ranges
 * need not match at all. Sharing one function between those two shapes
 * would need more parameters than it would save code (the same
 * reasoning `run-view-completed.js`'s own module docstring already
 * gives for not generalizing `drawDifferentiationQCurve` either).
 */

const compareBanner = document.getElementById("compare-banner");
const compareRecentRunsBody = document.getElementById("compare-recent-runs-body");
const compareRunButton = document.getElementById("compare-run-button");
const compareBackButton = document.getElementById("compare-back-button");
const compareResults = document.getElementById("compare-results");
const compareLegend = document.getElementById("compare-legend");
const comparePanels = document.getElementById("compare-panels");
const compareTrajectoryStatistic = document.getElementById(
    "compare-trajectory-statistic"
);
const compareTrajectoryCanvas = document.getElementById("compare-trajectory-canvas");
const compareTrajectoryLegend = document.getElementById("compare-trajectory-legend");

const _COMPARE_STATISTIC_NAMES = ["D", "G_ST", "E_ST", "K_ST", "H_S", "H_T"];
const _COMPARE_MINIMUM_RUNS = 2;

// A fixed, colorblind-safe qualitative palette (Okabe-Ito, the same
// family `run-view-completed.js`'s own `STATISTIC_TRAJECTORY_COLORS`
// draws from), one color per compared run rather than per statistic --
// cycles if more runs are selected than colors, which only degrades to
// two runs sharing a color rather than ever running out. Kept distinct
// from that other array (not reused verbatim) since the two encode
// unrelated things -- run identity here, statistic identity there --
// design principle §11.1's own "no other part of the interface borrows
// [a data-encoding palette] for anything unrelated to the data it
// encodes," applied by construction rather than by sharing one array
// for two different meanings.
const _COMPARE_RUN_COLORS = [
    "#0072b2",
    "#d55e00",
    "#009e73",
    "#cc79a7",
    "#e69f00",
    "#56b4e9",
    "#000000",
];

// Set once `compareRunButton`'s own click handler has a real result to
// redraw from -- `null` before the first successful compare, or after
// a failed one, so `compareTrajectoryStatistic`'s own `change` handler
// has nothing stale to redraw against.
let currentCompareRuns = null;

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

/**
 * Extract one statistic's own numeric history for one run, aligned with
 * its own `generations` -- `histories[name]` arrives as `format_
 * statistic`'s own display strings (`Api.compare_runs`'s own docstring:
 * "matching the live run view's identical `onRunProgress` convention"),
 * so an undefined value at a given generation (`"undefined"`, `G_ST` at
 * a currently-monomorphic locus) is dropped here via `Number.isFinite`,
 * the identical filter `accumulateLiveTrajectory` already applies for
 * the same reason -- never plotted as `NaN`, and never left in the
 * array misaligned against the wrong generation number.
 * @param {object} run
 * @param {string} statisticName
 * @returns {{generations: number[], values: number[]}}
 */
function compareRunSeries(run, statisticName) {
    const generations = [];
    const values = [];
    run.histories[statisticName].forEach((text, index) => {
        const value = Number(text);
        if (Number.isFinite(value)) {
            generations.push(run.generations[index]);
            values.push(value);
        }
    });
    return { generations, values };
}

/**
 * Draw one statistic's own trajectory for every compared run, one color
 * per run, on one shared set of axes -- design doc §8's own "overlay
 * their trajectory plots on one set of axes, one color per run."
 * Compared runs need not share a generation range at all (unlike
 * `run-view-completed.js`'s own single-run `drawTrajectoryCurve`, which
 * only ever plots several statistics from the same one run): the x-axis
 * domain spans the lowest and highest generation number across every
 * compared run's own series, so a run that stopped early still draws
 * its own curve only as far as it actually goes, rather than being
 * stretched or clipped to match a longer-running compared run.
 * @param {object[]} runs
 * @param {string} statisticName
 */
function drawCompareTrajectory(runs, statisticName) {
    const canvas = compareTrajectoryCanvas;
    canvas.width = canvas.clientWidth;
    canvas.height = canvas.clientHeight;
    const context = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    context.clearRect(0, 0, width, height);

    const series = runs.map((run) => compareRunSeries(run, statisticName));
    const allGenerations = series.flatMap((one) => one.generations);
    const allValues = series.flatMap((one) => one.values);
    compareTrajectoryLegend.replaceChildren();
    if (allGenerations.length === 0) {
        return;
    }

    const plotLeft = 42;
    const plotRight = width - 12;
    const plotTop = 12;
    const plotBottom = height - 22;
    const minGeneration = Math.min(...allGenerations);
    const maxGeneration = Math.max(...allGenerations);
    // Same fixed-floor-and-ceiling domain choice `drawTrajectoryCurve`
    // already makes, for the identical reason: every named statistic's
    // own natural range starts at `[0, 1]`, so a comparison that never
    // leaves a narrow band still reads against the same axes any other
    // statistic plot on this page already uses.
    const minValue = Math.min(0, ...allValues);
    const maxValue = Math.max(1, ...allValues);

    function xToPixel(generation) {
        const fraction =
            maxGeneration === minGeneration
                ? 0
                : (generation - minGeneration) / (maxGeneration - minGeneration);
        return plotLeft + fraction * (plotRight - plotLeft);
    }
    function yToPixel(value) {
        const fraction =
            maxValue === minValue ? 0 : (value - minValue) / (maxValue - minValue);
        return plotBottom - fraction * (plotBottom - plotTop);
    }

    const style = getComputedStyle(document.documentElement);
    const borderColor = style.getPropertyValue("--fim-border").trim();
    const mutedColor = style.getPropertyValue("--fim-muted").trim();

    context.strokeStyle = borderColor;
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(plotLeft, plotTop);
    context.lineTo(plotLeft, plotBottom);
    context.lineTo(plotRight, plotBottom);
    context.stroke();

    context.fillStyle = mutedColor;
    context.font = "10px sans-serif";
    context.textAlign = "right";
    context.textBaseline = "middle";
    context.fillText(maxValue.toFixed(2), plotLeft - 6, plotTop);
    context.fillText(minValue.toFixed(2), plotLeft - 6, plotBottom);
    context.textBaseline = "top";
    context.fillText(`gen ${maxGeneration}`, plotRight, plotBottom + 4);
    context.textAlign = "left";
    context.fillText(`gen ${minGeneration}`, plotLeft, plotBottom + 4);

    runs.forEach((run, index) => {
        const { generations, values } = series[index];
        if (generations.length === 0) {
            return;
        }
        const color = _COMPARE_RUN_COLORS[index % _COMPARE_RUN_COLORS.length];
        context.strokeStyle = color;
        context.lineWidth = 2;
        context.beginPath();
        generations.forEach((generation, pointIndex) => {
            const x = xToPixel(generation);
            const y = yToPixel(values[pointIndex]);
            if (pointIndex === 0) {
                context.moveTo(x, y);
            } else {
                context.lineTo(x, y);
            }
        });
        context.stroke();

        const item = document.createElement("span");
        const swatch = document.createElement("span");
        swatch.className = "swatch";
        swatch.style.backgroundColor = color;
        item.appendChild(swatch);
        const label = document.createElement("span");
        label.textContent = run.runId;
        item.appendChild(label);
        compareTrajectoryLegend.appendChild(item);
    });
}

compareTrajectoryStatistic.addEventListener("change", () => {
    if (currentCompareRuns !== null) {
        drawCompareTrajectory(currentCompareRuns, compareTrajectoryStatistic.value);
    }
});

compareRunButton.addEventListener("click", async () => {
    window.__fimCompareResultsReady = false;
    const paths = checkedTrajectoryPaths();
    const result = await window.pywebview.api.compare_runs(paths);
    if (!result.ok) {
        currentCompareRuns = null;
        showCompareBanner(result.message);
        compareResults.hidden = true;
        window.__fimCompareResultsReady = true;
        return;
    }
    showCompareBanner("");
    // `#compare-results` must already be visible before the panels
    // below are built: `drawComparePanel`/`drawCompareTrajectory` both
    // read a canvas's own `clientWidth`/`clientHeight`, which read `0`
    // from an element inside a still-`hidden` ancestor.
    compareResults.hidden = false;
    currentCompareRuns = result.runs;
    renderCompareLegend(result.runs, result.differingFields);
    renderComparePanels(result.runs);
    drawCompareTrajectory(result.runs, compareTrajectoryStatistic.value);
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
    // A fresh visit starts with no stale comparison to redraw against
    // if the statistic selector is touched before "Compare ▶" is ever
    // clicked again -- `compareResults` is already hidden either way,
    // so this is about not holding onto a previous visit's own run
    // data at all, not about anything currently visible.
    currentCompareRuns = null;
    window.fim.showScreen("screen-compare");
    refreshCompareRecentRuns();
};
