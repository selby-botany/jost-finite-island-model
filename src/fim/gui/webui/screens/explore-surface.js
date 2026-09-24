"use strict";

/* Explore's surface mode (`20260923-claude-sonnet-5-sweep-as-study-
 * implementation-plan.md`, `selby/restricted`, §7.1): the closed-form
 * predictions over two parameters at once, as a heat map of one
 * statistic. Curve mode (one parameter, the original view) stays the
 * default and is untouched; this file only adds the second view.
 *
 * One `Api.get_equilibrium_grid` call returns every statistic at every
 * cell, so moving the probe (a click or drag on the map, an arrow key, or
 * either range input) re-reads the prediction table with no round trip.
 * Two small line charts show the statistic along the probe's row and
 * column, which is exactly the curve view, once per axis.
 *
 * Loaded after `explore.js` and `sweep-results.js`, whose helpers it
 * reuses (`collectExploreValues`, `renderExplorePredictionRows`,
 * `formatExploreTick`, `drawSweepLine`, `sweepAxisIsLog`).
 */

const exploreMode = document.getElementById("explore-mode");
const exploreSurfacePanel = document.getElementById("explore-surface");
const exploreAxisY = document.getElementById("explore-axis-y");
const exploreSurfaceStatistic = document.getElementById("explore-surface-statistic");
const exploreSurfaceCanvas = document.getElementById("explore-surface-canvas");
const exploreSurfaceX = document.getElementById("explore-surface-x");
const exploreSurfaceY = document.getElementById("explore-surface-y");
const exploreSurfaceXName = document.getElementById("explore-surface-x-name");
const exploreSurfaceYName = document.getElementById("explore-surface-y-name");
const exploreSurfaceReadout = document.getElementById("explore-surface-readout");
const exploreSliceX = document.getElementById("explore-slice-x");
const exploreSliceY = document.getElementById("explore-slice-y");

// Every prediction the grid carries as a number (the flag is not one).
const EXPLORE_SURFACE_STATISTICS = EXPLORE_PREDICTION_NAMES.filter(
    (name) => EXPLORE_STATISTIC_UNITS[name] !== "flag"
);

let exploreSurfaceGrid = null;
let exploreSurfaceProbe = null;
let exploreSurfaceLayout = null;
let exploreSurfaceSequence = 0;
let exploreSurfaceDragging = false;

window.__fimExploreSurfaceReady = false;

for (const name of EXPLORE_SURFACE_STATISTICS) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name.replace("_", " ");
    exploreSurfaceStatistic.appendChild(option);
}
exploreSurfaceStatistic.value = "D";
exploreAxisY.value = "d";

/**
 * Whether Explore is showing the surface.
 * @returns {boolean}
 */
function isExploreSurface() {
    return exploreMode.value === "surface";
}

/**
 * Keep the row axis different from the column axis.
 */
function syncExploreRowAxis() {
    for (const option of exploreAxisY.options) {
        option.disabled = option.value === exploreAxis.value;
    }
    if (exploreAxisY.value === exploreAxis.value) {
        exploreAxisY.value = Array.from(exploreAxisY.options).find(
            (option) => !option.disabled
        ).value;
    }
}

/**
 * Show the curve or the surface panel to match the mode select.
 */
async function applyExploreMode() {
    const surface = isExploreSurface();
    exploreCanvas.hidden = surface;
    document.querySelector(".explore-scrubber").hidden = surface;
    exploreSurfacePanel.hidden = !surface;
    if (surface) {
        syncExploreRowAxis();
        await refreshExploreSurface();
    } else {
        explorePredictions.classList.toggle("explore-scrubbed", false);
        if (_committedPredictions !== null) {
            renderExplorePredictionRows(
                _committedPredictions.predictions,
                _committedPredictions.qualifications
            );
        }
        window.__fimExploreSurfaceReady = true;
    }
}

/**
 * Fetch the grid for the current fields and axes and draw it.
 */
async function refreshExploreSurface() {
    if (!isExploreSurface()) {
        return;
    }
    const sequence = (exploreSurfaceSequence += 1);
    window.__fimExploreSurfaceReady = false;
    syncExploreRowAxis();
    const values = collectExploreValues();
    const grid = await window.pywebview.api.get_equilibrium_grid(
        exploreAxis.value,
        exploreAxisY.value,
        values.n,
        values.m,
        values.mu,
        values.d
    );
    if (sequence !== exploreSurfaceSequence) {
        return;
    }
    if (!grid.ok) {
        exploreBanner.textContent = grid.message;
        exploreBanner.hidden = false;
        exploreSurfaceGrid = null;
        window.__fimExploreSurfaceReady = true;
        return;
    }
    exploreBanner.hidden = true;
    exploreSurfaceGrid = grid;
    exploreSurfaceProbe = { row: grid.currentRow, column: grid.currentColumn };
    exploreSurfaceX.max = String(grid.xValues.length - 1);
    exploreSurfaceY.max = String(grid.yValues.length - 1);
    exploreSurfaceXName.textContent = exploreAxisLabel(grid.xAxis);
    exploreSurfaceYName.textContent = exploreAxisLabel(grid.yAxis);
    drawExploreSurface();
    window.__fimExploreSurfaceReady = true;
}

/**
 * Redraw the map, the slices, the readout and the table for the probe.
 */
function drawExploreSurface() {
    if (exploreSurfaceGrid === null) {
        return;
    }
    const grid = exploreSurfaceGrid;
    const statistic = exploreSurfaceStatistic.value;
    const rows = grid.values[statistic];
    const flat = rows.flat().filter((value) => value !== null);
    const proportion = EXPLORE_STATISTIC_UNITS[statistic] === "proportion";
    exploreSurfaceCanvas.width = Math.max(exploreSurfaceCanvas.clientWidth, 320);
    exploreSurfaceCanvas.height = 360;
    exploreSurfaceLayout = window.fim.heatmap.draw(exploreSurfaceCanvas, {
        columns: grid.xValues.map(formatExploreTick),
        rows: grid.yValues.map(formatExploreTick),
        values: rows,
        min: proportion || flat.length === 0 ? 0 : Math.min(...flat),
        max: proportion ? 1 : flat.length === 0 ? 1 : Math.max(...flat),
        xTitle: exploreAxisLabel(grid.xAxis),
        yTitle: exploreAxisLabel(grid.yAxis),
        valueTitle: statistic.replace("_", " "),
        selected: exploreSurfaceProbe,
    });
    exploreSurfaceX.value = String(exploreSurfaceProbe.column);
    exploreSurfaceY.value = String(exploreSurfaceProbe.row);
    drawExploreSlices(statistic);
    renderExploreSurfaceTable();
    describeExploreProbe(statistic);
}

/**
 * Say in words where the probe is and what the statistic is there.
 * @param {string} statistic
 */
function describeExploreProbe(statistic) {
    const grid = exploreSurfaceGrid;
    const probe = exploreSurfaceProbe;
    const atCurrent = probe.row === grid.currentRow && probe.column === grid.currentColumn;
    const value = grid.values[statistic][probe.row][probe.column];
    const where =
        `${exploreAxisLabel(grid.xAxis)} = ${formatExploreTick(grid.xValues[probe.column])}, ` +
        `${exploreAxisLabel(grid.yAxis)} = ${formatExploreTick(grid.yValues[probe.row])}`;
    const shown = formatExploreSweepValue(value, grid.digits ?? 4);
    const note = atCurrent ? "the configuration you entered" : "exploring";
    exploreSurfaceReadout.textContent = `${where}: ${statistic.replace("_", " ")} ${shown} (${note})`;
}

/**
 * Draw the statistic along the probe's row and column.
 * @param {string} statistic
 */
function drawExploreSlices(statistic) {
    const grid = exploreSurfaceGrid;
    const probe = exploreSurfaceProbe;
    const rows = grid.values[statistic];
    const draw = (canvas, axisKey, axisValues, series) => {
        canvas.width = Math.max(canvas.clientWidth, 240);
        canvas.height = 200;
        const marks = axisValues
            .map((x, index) => ({
                x,
                index,
                mean: series[index],
                low: null,
                high: null,
            }))
            .filter((mark) => mark.mean !== null);
        drawSweepLine(canvas, {
            xKey: exploreAxisLabel(axisKey),
            values: axisValues,
            numeric: true,
            log: sweepAxisIsLog(axisValues),
            marks,
            theory: [],
            statistic,
        });
    };
    draw(
        exploreSliceX,
        grid.xAxis,
        grid.xValues,
        rows[probe.row]
    );
    draw(
        exploreSliceY,
        grid.yAxis,
        grid.yValues,
        rows.map((row) => row[probe.column])
    );
}

/**
 * Fill the prediction table from the grid at the probe.
 */
function renderExploreSurfaceTable() {
    const grid = exploreSurfaceGrid;
    const probe = exploreSurfaceProbe;
    const atCurrent = probe.row === grid.currentRow && probe.column === grid.currentColumn;
    explorePredictions.classList.toggle("explore-scrubbed", !atCurrent);
    if (atCurrent && _committedPredictions !== null) {
        renderExplorePredictionRows(
            _committedPredictions.predictions,
            _committedPredictions.qualifications
        );
        return;
    }
    const predictions = {};
    for (const name of EXPLORE_PREDICTION_NAMES) {
        predictions[name] = grid.values[name]
            ? formatExploreSweepValue(grid.values[name][probe.row][probe.column], grid.digits ?? 4)
            : "undefined";
    }
    renderExplorePredictionRows(predictions, _committedPredictions?.qualifications);
}

/**
 * Move the probe to a cell, clamped to the grid.
 * @param {number} row
 * @param {number} column
 */
function setExploreProbe(row, column) {
    if (exploreSurfaceGrid === null) {
        return;
    }
    exploreSurfaceProbe = {
        row: Math.min(Math.max(row, 0), exploreSurfaceGrid.yValues.length - 1),
        column: Math.min(Math.max(column, 0), exploreSurfaceGrid.xValues.length - 1),
    };
    drawExploreSurface();
}

/**
 * Move the probe to the cell under a pointer, if there is one.
 * @param {MouseEvent} event
 */
function probeFromPointer(event) {
    if (exploreSurfaceLayout === null) {
        return;
    }
    const cell = window.fim.heatmap.cellAt(
        exploreSurfaceCanvas,
        exploreSurfaceLayout,
        event.clientX,
        event.clientY
    );
    if (cell !== null) {
        setExploreProbe(cell.row, cell.column);
    }
}

exploreMode.addEventListener("change", applyExploreMode);
exploreAxisY.addEventListener("change", refreshExploreSurface);
exploreSurfaceStatistic.addEventListener("change", drawExploreSurface);
for (const field of [exploreN, exploreD, exploreM, exploreMu, exploreAxis]) {
    field.addEventListener("change", () => {
        if (isExploreSurface()) {
            refreshExploreSurface();
        }
    });
}
exploreSurfaceX.addEventListener("input", () =>
    setExploreProbe(exploreSurfaceProbe.row, Number(exploreSurfaceX.value))
);
exploreSurfaceY.addEventListener("input", () =>
    setExploreProbe(Number(exploreSurfaceY.value), exploreSurfaceProbe.column)
);
exploreSurfaceCanvas.addEventListener("mousedown", (event) => {
    exploreSurfaceDragging = true;
    exploreSurfaceCanvas.focus();
    probeFromPointer(event);
});
exploreSurfaceCanvas.addEventListener("mousemove", (event) => {
    if (exploreSurfaceDragging) {
        probeFromPointer(event);
    }
});
window.addEventListener("mouseup", () => {
    exploreSurfaceDragging = false;
});
exploreSurfaceCanvas.addEventListener("keydown", (event) => {
    if (exploreSurfaceGrid === null) {
        return;
    }
    const step = { ArrowLeft: [0, -1], ArrowRight: [0, 1], ArrowUp: [-1, 0], ArrowDown: [1, 0] }[
        event.key
    ];
    if (step !== undefined) {
        event.preventDefault();
        setExploreProbe(
            exploreSurfaceProbe.row + step[0],
            exploreSurfaceProbe.column + step[1]
        );
    } else if (event.key === "Home") {
        event.preventDefault();
        setExploreProbe(exploreSurfaceGrid.currentRow, exploreSurfaceGrid.currentColumn);
    }
});

// In surface mode a click on a statistic row picks the statistic the map
// shows, the way it toggles a plotted series in curve mode.
for (const name of EXPLORE_SURFACE_STATISTICS) {
    const row = document.getElementById(`explore-stat-${name}`);
    if (row !== null) {
        row.addEventListener("click", () => {
            if (isExploreSurface()) {
                exploreSurfaceStatistic.value = name;
                drawExploreSurface();
            }
        });
    }
}

// Opening Explore while in surface mode draws the surface for whatever
// the four fields hold on arrival.
const showExploreCurve = window.fim.showExplore;
window.fim.showExplore = async function showExploreWithSurface(overrides) {
    await showExploreCurve(overrides);
    if (isExploreSurface()) {
        await refreshExploreSurface();
    }
};
