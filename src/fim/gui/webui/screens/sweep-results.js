"use strict";

/* Sweep results (`20260923-claude-sonnet-5-sweep-as-study-implementation-
 * plan.md`, `selby/restricted`, §8): the statistic across the points of a
 * finished (or partly finished) sweep, read on open from the Study's own
 * stored plan and each point's own summary. Nothing is stored here.
 *
 * - One axis: the statistic at each value, with its confidence interval
 *   across replicates, and (optionally) the closed-form theory drawn
 *   behind it.
 * - Two axes: a heat map (`heatmap.js`) of the statistic's mean, or of the
 *   theory, or of simulation minus theory. A sweep with more axes holds
 *   the others at a chosen value; every axis is stored, any two are shown.
 * - Always a table of every planned point beside the chart, and a click on
 *   a point or a cell opens that run's own Results card.
 */

const sweepResultsSummary = document.getElementById("sweep-results-summary");
const sweepResultsStatistic = document.getElementById("sweep-results-statistic");
const sweepResultsX = document.getElementById("sweep-results-x");
const sweepResultsY = document.getElementById("sweep-results-y");
const sweepResultsMode = document.getElementById("sweep-results-mode");
const sweepResultsModeField = document.getElementById("sweep-results-mode-field");
const sweepResultsTheory = document.getElementById("sweep-results-theory");
const sweepResultsTheoryField = document.getElementById("sweep-results-theory-field");
const sweepResultsHeld = document.getElementById("sweep-results-held");
const sweepResultsCanvas = document.getElementById("sweep-results-canvas");
const sweepResultsReadout = document.getElementById("sweep-results-readout");
const sweepResultsHead = document.getElementById("sweep-results-head");
const sweepResultsBody = document.getElementById("sweep-results-body");
const sweepResultsSetupButton = document.getElementById("sweep-results-setup-button");
const sweepResultsView = document.getElementById("sweep-results");

// Statistics on a fixed 0 to 1 scale; every other statistic fits its data.
const SWEEP_PROPORTION_STATISTICS = new Set([
    "D",
    "G_ST",
    "E_ST",
    "K_ST",
    "H_S",
    "H_T",
    "H_ST",
]);
// Statistics the closed form can predict (`Api.get_sweep_theory`).
const SWEEP_THEORY_STATISTICS = new Set(["D", "G_ST", "E_ST", "H_S", "H_T"]);
const SWEEP_THEORY_SAMPLES = 60;
const SWEEP_LINE_MARGIN = { left: 64, right: 24, top: 16, bottom: 52 };

let sweepResultsData = null;
let sweepResultsStudyId = null;
let sweepResultsRenderSequence = 0;
// What the canvas currently shows, for hover and click: pixel points for a
// line chart, or a heat-map layout with the run each cell holds.
let sweepResultsHit = null;

window.__fimSweepResultsReady = false;

/**
 * Format an axis value or a statistic compactly.
 * @param {number|string} value
 * @returns {string}
 */
function sweepFormatValue(value) {
    return typeof value === "number" ? window.fim.heatmap.format(value) : String(value);
}

/**
 * The values of one axis.
 * @param {string} key
 * @returns {Array<number|string>}
 */
function sweepAxisValues(key) {
    return sweepResultsData.axes.find((axis) => axis.key === key).values;
}

/**
 * Whether an axis is numeric and spans enough to read on a log scale.
 * @param {Array<number|string>} values
 * @returns {boolean}
 */
function sweepAxisIsLog(values) {
    if (!values.every((value) => typeof value === "number" && value > 0)) {
        return false;
    }
    return Math.max(...values) / Math.min(...values) >= 20;
}

/**
 * The results joined to their planned points, keyed by grid index.
 * @returns {Map<number, object>}
 */
function sweepResultsByIndex() {
    return new Map(sweepResultsData.results.map((result) => [result.index, result]));
}

/**
 * The points that agree with every held axis value.
 * @returns {object[]} Planned points with their `result` (or `null`).
 */
function sweepFilteredPoints() {
    const held = Object.entries(sweepHeldValues());
    const results = sweepResultsByIndex();
    return sweepResultsData.points
        .filter((point) => held.every(([key, value]) => point.coordinates[key] === value))
        .map((point) => ({ ...point, result: results.get(point.index) || null }));
}

/**
 * The value each held axis is fixed at, from its select.
 * @returns {Record<string, number|string>}
 */
function sweepHeldValues() {
    const held = {};
    for (const select of sweepResultsHeld.querySelectorAll("select")) {
        const values = sweepAxisValues(select.dataset.key);
        held[select.dataset.key] = values[Number(select.value)];
    }
    return held;
}

/**
 * Show the results for a sweep Study.
 * @param {string} studyId
 */
window.fim.showSweepResults = async function showSweepResults(studyId) {
    window.__fimSweepResultsReady = false;
    const data = await window.pywebview.api.get_sweep_results(studyId);
    if (!data.ok) {
        window.fim.showScreen("screen-sweep");
        showSweepBanner(data.message);
        return;
    }
    showSweepBanner("");
    sweepResultsData = data;
    sweepResultsStudyId = studyId;
    sweepResultsSummary.textContent = sweepResultsSummaryText(data);
    populateSweepResultsControls(data);
    sweepTitle.textContent = data.name;
    sweepSetupView.hidden = true;
    sweepProgressView.hidden = true;
    sweepResultsView.hidden = false;
    window.fim.showScreen("screen-sweep");
    await renderSweepResults();
};

/**
 * One line: how many points are finished.
 * @param {object} data `Api.get_sweep_results` payload.
 * @returns {string}
 */
function sweepResultsSummaryText(data) {
    const total = data.points.length;
    const done = data.results.length;
    const axes = data.axes.map((axis) => axis.key).join(" and ");
    const pending = total - done;
    const remaining =
        pending > 0 ? ` ${pending} not run yet; resume the study to finish them.` : "";
    return `${done} of ${total} points finished, varying ${axes}.${remaining}`;
}

/**
 * Fill the statistic and axis selects from a results payload.
 * @param {object} data
 */
function populateSweepResultsControls(data) {
    const available = new Set(
        data.results.flatMap((result) => Object.keys(result.statistics))
    );
    const previous = sweepResultsStatistic.value;
    sweepResultsStatistic.replaceChildren();
    for (const name of STATISTIC_NAMES.filter((entry) => available.has(entry))) {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = name.replace("_", " ");
        sweepResultsStatistic.appendChild(option);
    }
    if (previous && available.has(previous)) {
        sweepResultsStatistic.value = previous;
    }
    const keys = data.axes.map((axis) => axis.key);
    sweepResultsX.replaceChildren();
    sweepResultsY.replaceChildren();
    const none = document.createElement("option");
    none.value = "";
    none.textContent = "(none: a line)";
    sweepResultsY.appendChild(none);
    for (const key of keys) {
        for (const select of [sweepResultsX, sweepResultsY]) {
            const option = document.createElement("option");
            option.value = key;
            option.textContent = key;
            select.appendChild(option);
        }
    }
    sweepResultsX.value = keys[0];
    sweepResultsY.value = keys.length > 1 ? keys[1] : "";
    sweepResultsMode.value = "simulation";
    sweepResultsTheory.checked = false;
    refreshSweepHeldControls();
}

/**
 * Rebuild the "held at" selects for the axes that are not displayed.
 */
function refreshSweepHeldControls() {
    const shown = [sweepResultsX.value, sweepResultsY.value].filter((key) => key !== "");
    sweepResultsHeld.replaceChildren();
    for (const axis of sweepResultsData.axes) {
        if (shown.includes(axis.key)) {
            continue;
        }
        const field = document.createElement("div");
        field.className = "field";
        const label = document.createElement("label");
        label.textContent = `${axis.key} held at`;
        const select = document.createElement("select");
        select.dataset.key = axis.key;
        axis.values.forEach((value, index) => {
            const option = document.createElement("option");
            option.value = String(index);
            option.textContent = sweepFormatValue(value);
            select.appendChild(option);
        });
        select.addEventListener("change", renderSweepResults);
        label.appendChild(select);
        field.appendChild(label);
        sweepResultsHeld.appendChild(field);
    }
}

/**
 * Draw the chart, the readout and the table for the current controls.
 */
async function renderSweepResults() {
    const sequence = (sweepResultsRenderSequence += 1);
    window.__fimSweepResultsReady = false;
    const statistic = sweepResultsStatistic.value;
    const xKey = sweepResultsX.value;
    const yKey = sweepResultsY.value === xKey ? "" : sweepResultsY.value;
    const points = sweepFilteredPoints();
    const hasTheory = SWEEP_THEORY_STATISTICS.has(statistic);
    sweepResultsTheoryField.hidden = yKey !== "";
    sweepResultsTheory.disabled = !hasTheory;
    sweepResultsModeField.hidden = yKey === "";
    for (const option of sweepResultsMode.options) {
        option.disabled = option.value !== "simulation" && !hasTheory;
    }
    if (!hasTheory) {
        sweepResultsMode.value = "simulation";
    }
    sweepResultsCanvas.width = Math.max(sweepResultsCanvas.clientWidth, 320);
    sweepResultsCanvas.height = yKey === "" ? 320 : 380;
    if (statistic === "") {
        sweepResultsReadout.textContent = "No finished points to show yet.";
        drawSweepResultsTable(statistic);
        window.__fimSweepResultsReady = true;
        return;
    }
    if (yKey === "") {
        await renderSweepLine(sequence, points, xKey, statistic);
    } else {
        await renderSweepHeatmap(sequence, points, xKey, yKey, statistic);
    }
    if (sequence !== sweepResultsRenderSequence) {
        return;
    }
    drawSweepResultsTable(statistic);
    window.__fimSweepResultsReady = true;
}

/**
 * The closed-form values at each coordinate set.
 * @param {Array<Record<string, number|string>>} coordinates
 * @returns {Promise<Array<Record<string, number|null>>>}
 */
async function sweepTheoryAt(coordinates) {
    const reply = await window.pywebview.api.get_sweep_theory(
        sweepResultsStudyId,
        coordinates
    );
    return reply.ok ? reply.values : coordinates.map(() => ({}));
}

/**
 * `count` values from `low` to `high`, log or linear.
 * @param {number} low
 * @param {number} high
 * @param {number} count
 * @param {boolean} log
 * @returns {number[]}
 */
function sweepSpaced(low, high, count, log) {
    return Array.from({ length: count }, (_, step) => {
        const fraction = step / (count - 1);
        return log
            ? low * (high / low) ** fraction
            : low + (high - low) * fraction;
    });
}

/**
 * The x values to evaluate the theory at along an axis: every integer for
 * an integer axis (there is no theory between 8 and 9 demes), otherwise
 * evenly spaced on the axis's scale.
 * @param {number[]} values The axis's values.
 * @param {boolean} log
 * @returns {number[]}
 */
function sweepTheorySamples(values, log) {
    const low = Math.min(...values);
    const high = Math.max(...values);
    if (values.every(Number.isInteger)) {
        const step = Math.max(1, Math.ceil((high - low) / (SWEEP_THEORY_SAMPLES - 1)));
        const integers = [];
        for (let value = low; value <= high; value += step) {
            integers.push(value);
        }
        if (integers[integers.length - 1] !== high) {
            integers.push(high);
        }
        return integers;
    }
    return sweepSpaced(low, high, SWEEP_THEORY_SAMPLES, log);
}

/**
 * Draw the one-axis chart: mean and interval per value, plus theory.
 * @param {number} sequence
 * @param {object[]} points
 * @param {string} xKey
 * @param {string} statistic
 */
async function renderSweepLine(sequence, points, xKey, statistic) {
    const values = sweepAxisValues(xKey);
    const numeric = values.every((value) => typeof value === "number");
    const log = numeric && sweepAxisIsLog(values);
    const held = sweepHeldValues();
    const marks = points
        .filter((point) => point.result && point.result.statistics[statistic])
        .map((point) => ({
            x: point.coordinates[xKey],
            index: point.index,
            directory: point.result.directory,
            nReplicates: point.result.nReplicates,
            coordinates: point.coordinates,
            ...point.result.statistics[statistic],
        }));
    let theory = [];
    if (sweepResultsTheory.checked && numeric) {
        const samples = sweepTheorySamples(values, log);
        const predicted = await sweepTheoryAt(
            samples.map((x) => ({ ...held, [xKey]: x }))
        );
        if (sequence !== sweepResultsRenderSequence) {
            return;
        }
        theory = samples.map((x, index) => ({ x, y: predicted[index][statistic] ?? null }));
    }
    sweepResultsHit = drawSweepLine(sweepResultsCanvas, {
        xKey,
        values,
        numeric,
        log,
        marks,
        theory,
        statistic,
    });
    sweepResultsReadout.textContent =
        marks.length === 0
            ? "No finished points at these settings."
            : "Hover a point for its value; click it to open that run.";
}

/**
 * Nice axis ticks between `low` and `high`.
 * @param {number} low
 * @param {number} high
 * @returns {number[]}
 */
function sweepTicks(low, high) {
    if (!(high > low)) {
        return [low];
    }
    const rough = (high - low) / 4;
    const magnitude = 10 ** Math.floor(Math.log10(rough));
    const step = [1, 2, 5, 10].map((factor) => factor * magnitude).find((s) => s >= rough);
    const ticks = [];
    for (let tick = Math.ceil(low / step) * step; tick <= high + step * 1e-9; tick += step) {
        ticks.push(Number(tick.toPrecision(12)));
    }
    return ticks;
}

/**
 * Draw the one-axis chart and return its pixel points for hit testing.
 * @param {HTMLCanvasElement} canvas
 * @param {object} spec
 * @returns {{kind: "line", pixels: Array<{x: number, y: number, mark: object}>}}
 */
function drawSweepLine(canvas, spec) {
    const context = canvas.getContext("2d");
    const style = getComputedStyle(document.documentElement);
    const muted = style.getPropertyValue("--fim-muted").trim() || "#5d6863";
    const border = style.getPropertyValue("--fim-border").trim() || "#cdd5d0";
    const foreground = style.getPropertyValue("--fim-foreground").trim() || "#242824";
    const color = STATISTIC_TRAJECTORY_COLORS[spec.statistic] || "#0072b2";
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.font = "12px sans-serif";
    const left = SWEEP_LINE_MARGIN.left;
    const top = SWEEP_LINE_MARGIN.top;
    const width = canvas.width - left - SWEEP_LINE_MARGIN.right;
    const height = canvas.height - top - SWEEP_LINE_MARGIN.bottom;

    const lows = spec.marks.map((mark) => mark.low ?? mark.mean);
    const highs = spec.marks.map((mark) => mark.high ?? mark.mean);
    const theoryValues = spec.theory.map((entry) => entry.y).filter((y) => y !== null);
    let yLow = Math.min(...lows, ...theoryValues, Infinity);
    let yHigh = Math.max(...highs, ...theoryValues, -Infinity);
    if (SWEEP_PROPORTION_STATISTICS.has(spec.statistic)) {
        yLow = 0;
        yHigh = 1;
    } else if (!(yHigh > yLow)) {
        yLow = Number.isFinite(yLow) ? yLow - 1 : 0;
        yHigh = Number.isFinite(yHigh) ? yHigh + 1 : 1;
    }
    const toY = (value) => top + height - ((value - yLow) / (yHigh - yLow)) * height;

    let toX;
    if (spec.numeric) {
        const low = Math.min(...spec.values);
        const high = Math.max(...spec.values);
        const scale = spec.log ? Math.log : (value) => value;
        const span = scale(high) - scale(low) || 1;
        toX = (value) => left + 12 + ((scale(value) - scale(low)) / span) * (width - 24);
    } else {
        toX = (value) =>
            left + ((spec.values.indexOf(value) + 0.5) / spec.values.length) * width;
    }

    context.strokeStyle = border;
    context.fillStyle = muted;
    context.textAlign = "right";
    for (const tick of sweepTicks(yLow, yHigh)) {
        const y = toY(tick);
        context.beginPath();
        context.moveTo(left, y);
        context.lineTo(left + width, y);
        context.stroke();
        context.fillText(window.fim.heatmap.format(tick), left - 6, y + 4);
    }
    context.textAlign = "center";
    const stride = Math.max(1, Math.ceil(spec.values.length / Math.max(width / 56, 1)));
    spec.values.forEach((value, index) => {
        if (index % stride === 0) {
            context.fillText(sweepFormatValue(value), toX(value), top + height + 16);
        }
    });
    context.fillStyle = foreground;
    context.fillText(spec.xKey, left + width / 2, canvas.height - 10);
    context.save();
    context.translate(14, top + height / 2);
    context.rotate(-Math.PI / 2);
    context.fillText(spec.statistic.replace("_", " "), 0, 0);
    context.restore();

    drawSweepTheory(context, spec, toX, toY, muted);

    const pixels = spec.marks.map((mark) => ({ x: toX(mark.x), y: toY(mark.mean), mark }));
    context.strokeStyle = color;
    context.fillStyle = color;
    context.lineWidth = 2;
    if (spec.numeric && pixels.length > 1) {
        context.beginPath();
        pixels.forEach((pixel, index) => {
            if (index === 0) {
                context.moveTo(pixel.x, pixel.y);
            } else {
                context.lineTo(pixel.x, pixel.y);
            }
        });
        context.stroke();
    }
    context.lineWidth = 1;
    for (const pixel of pixels) {
        if (pixel.mark.low !== null && pixel.mark.high !== null) {
            context.beginPath();
            context.moveTo(pixel.x, toY(pixel.mark.low));
            context.lineTo(pixel.x, toY(pixel.mark.high));
            context.stroke();
        }
        context.beginPath();
        context.arc(pixel.x, pixel.y, 4, 0, Math.PI * 2);
        context.fill();
    }
    return { kind: "line", pixels, statistic: spec.statistic, xKey: spec.xKey };
}

/**
 * Draw the dashed theory curve, breaking it wherever it is undefined.
 * @param {CanvasRenderingContext2D} context
 * @param {object} spec
 * @param {(value: number) => number} toX
 * @param {(value: number) => number} toY
 * @param {string} color
 */
function drawSweepTheory(context, spec, toX, toY, color) {
    if (spec.theory.length === 0) {
        return;
    }
    context.save();
    context.strokeStyle = color;
    context.lineWidth = 1.5;
    context.setLineDash([5, 4]);
    let drawing = false;
    context.beginPath();
    for (const entry of spec.theory) {
        if (entry.y === null) {
            drawing = false;
            continue;
        }
        if (drawing) {
            context.lineTo(toX(entry.x), toY(entry.y));
        } else {
            context.moveTo(toX(entry.x), toY(entry.y));
            drawing = true;
        }
    }
    context.stroke();
    context.restore();
}

/**
 * Draw the two-axis heat map.
 * @param {number} sequence
 * @param {object[]} points
 * @param {string} xKey
 * @param {string} yKey
 * @param {string} statistic
 */
async function renderSweepHeatmap(sequence, points, xKey, yKey, statistic) {
    const xValues = sweepAxisValues(xKey);
    const yValues = sweepAxisValues(yKey);
    const held = sweepHeldValues();
    const mode = sweepResultsMode.value;
    const cells = yValues.map((y) =>
        xValues.map((x) =>
            points.find(
                (point) => point.coordinates[xKey] === x && point.coordinates[yKey] === y
            ) || null
        )
    );
    let theory = null;
    if (mode !== "simulation") {
        const coordinates = yValues.flatMap((y) =>
            xValues.map((x) => ({ ...held, [xKey]: x, [yKey]: y }))
        );
        const predicted = await sweepTheoryAt(coordinates);
        if (sequence !== sweepResultsRenderSequence) {
            return;
        }
        theory = yValues.map((_, row) =>
            xValues.map((__, column) => predicted[row * xValues.length + column][statistic] ?? null)
        );
    }
    const simulated = cells.map((row) =>
        row.map((cell) =>
            cell && cell.result && cell.result.statistics[statistic]
                ? cell.result.statistics[statistic].mean
                : null
        )
    );
    const shownValues = simulated.map((row, r) =>
        row.map((value, c) => {
            if (mode === "theory") {
                return theory[r][c];
            }
            if (mode === "difference") {
                return value !== null && theory[r][c] !== null ? value - theory[r][c] : null;
            }
            return value;
        })
    );
    const flat = shownValues.flat().filter((value) => value !== null);
    const diverging = mode === "difference";
    let min = flat.length > 0 ? Math.min(...flat) : 0;
    let max = flat.length > 0 ? Math.max(...flat) : 1;
    if (diverging) {
        const reach = Math.max(Math.abs(min), Math.abs(max), 1e-9);
        min = -reach;
        max = reach;
    } else if (SWEEP_PROPORTION_STATISTICS.has(statistic)) {
        min = 0;
        max = 1;
    }
    const layout = window.fim.heatmap.draw(sweepResultsCanvas, {
        columns: xValues.map(sweepFormatValue),
        rows: yValues.map(sweepFormatValue),
        values: shownValues,
        min,
        max,
        diverging,
        xTitle: xKey,
        yTitle: yKey,
        valueTitle: `${diverging ? "Δ " : ""}${statistic.replace("_", " ")}`,
        selected: null,
    });
    sweepResultsHit = { kind: "heatmap", layout, cells, shownValues, xValues, yValues, mode };
    sweepResultsReadout.textContent =
        flat.length === 0
            ? "No finished points at these settings."
            : "Hover a cell for its value; click it to open that run.";
}

/**
 * The readout text for one mark or cell.
 * @param {object} point Planned point with its result.
 * @param {string} statistic
 * @returns {string}
 */
function sweepPointReadout(point, statistic) {
    const where = Object.entries(point.coordinates)
        .map(([key, value]) => `${key} ${sweepFormatValue(value)}`)
        .join(", ");
    const stat = point.result && point.result.statistics[statistic];
    if (!stat) {
        return `${where}: not run`;
    }
    const interval =
        stat.low !== null && stat.high !== null
            ? ` [${sweepFormatValue(stat.low)}, ${sweepFormatValue(stat.high)}]`
            : "";
    return (
        `${where}: ${statistic.replace("_", " ")} ${sweepFormatValue(stat.mean)}${interval}` +
        ` (${point.result.nReplicates} replicate${point.result.nReplicates === 1 ? "" : "s"})`
    );
}

/**
 * Open the run behind one point or cell on the ordinary Results card.
 * @param {object} result A results entry (`directory`, `nReplicates`).
 */
async function openSweepPoint(result) {
    if (result.nReplicates > 1) {
        await openBatch(result.directory);
    } else {
        await openTrajectory(`${result.directory}/trajectory.jsonl`);
    }
}

/**
 * Find what is under the pointer on the canvas.
 * @param {MouseEvent} event
 * @returns {{point: object}|null}
 */
function sweepResultsTarget(event) {
    if (sweepResultsHit === null) {
        return null;
    }
    if (sweepResultsHit.kind === "line") {
        const box = sweepResultsCanvas.getBoundingClientRect();
        const x = ((event.clientX - box.left) * sweepResultsCanvas.width) / Math.max(box.width, 1);
        const y =
            ((event.clientY - box.top) * sweepResultsCanvas.height) / Math.max(box.height, 1);
        let best = null;
        let bestDistance = 14;
        for (const pixel of sweepResultsHit.pixels) {
            const distance = Math.hypot(pixel.x - x, pixel.y - y);
            if (distance < bestDistance) {
                best = pixel;
                bestDistance = distance;
            }
        }
        return best === null ? null : { mark: best.mark };
    }
    const cell = window.fim.heatmap.cellAt(
        sweepResultsCanvas,
        sweepResultsHit.layout,
        event.clientX,
        event.clientY
    );
    if (cell === null) {
        return null;
    }
    const point = sweepResultsHit.cells[cell.row][cell.column];
    return { cell, point };
}

sweepResultsCanvas.addEventListener("mousemove", (event) => {
    const target = sweepResultsTarget(event);
    const statistic = sweepResultsStatistic.value;
    sweepResultsCanvas.style.cursor = target ? "pointer" : "default";
    if (target === null) {
        return;
    }
    if (target.mark) {
        const point = sweepResultsData.points.find((entry) => entry.index === target.mark.index);
        sweepResultsReadout.textContent = sweepPointReadout(
            { ...point, result: sweepResultsByIndex().get(point.index) },
            statistic
        );
    } else if (target.point) {
        sweepResultsReadout.textContent = sweepPointReadout(target.point, statistic);
        const shown = sweepResultsHit.shownValues[target.cell.row][target.cell.column];
        if (sweepResultsHit.mode !== "simulation" && shown !== null) {
            sweepResultsReadout.textContent += `; ${sweepResultsHit.mode} ${sweepFormatValue(shown)}`;
        }
    } else {
        const xValue = sweepResultsHit.xValues[target.cell.column];
        const yValue = sweepResultsHit.yValues[target.cell.row];
        sweepResultsReadout.textContent = `${sweepFormatValue(xValue)}, ${sweepFormatValue(yValue)}: not planned`;
    }
});

sweepResultsCanvas.addEventListener("click", async (event) => {
    const target = sweepResultsTarget(event);
    if (target === null) {
        return;
    }
    const result = target.mark
        ? sweepResultsByIndex().get(target.mark.index)
        : target.point && target.point.result;
    if (result) {
        await openSweepPoint(result);
    }
});

/**
 * Write the table of every planned point beside the chart. It always
 * lists every point, not only the slice the chart shows, so nothing is
 * hidden from it.
 * @param {string} statistic
 */
function drawSweepResultsTable(statistic) {
    const keys = sweepResultsData.axes.map((axis) => axis.key);
    const results = sweepResultsByIndex();
    sweepResultsHead.replaceChildren(
        sweepHeaderRow([
            ...keys,
            statistic ? `${statistic.replace("_", " ")} mean` : "mean",
            "low",
            "high",
            "replicates",
            "state",
            "",
        ])
    );
    sweepResultsBody.replaceChildren();
    const states = new Map(sweepResultsData.points.map((point) => [point.index, point]));
    for (const point of sweepResultsData.points) {
        const result = results.get(point.index);
        const stat = result && statistic ? result.statistics[statistic] : null;
        const row = document.createElement("tr");
        if (point.state === "failed") {
            row.classList.add("row-warning");
        }
        const cells = [
            ...keys.map((key) => sweepFormatValue(point.coordinates[key])),
            stat ? sweepFormatValue(stat.mean) : "",
            stat && stat.low !== null ? sweepFormatValue(stat.low) : "",
            stat && stat.high !== null ? sweepFormatValue(stat.high) : "",
            result ? String(result.nReplicates) : "",
            point.state === "failed" ? `failed (${point.reason})` : states.get(point.index).state,
        ];
        for (const text of cells) {
            const cell = document.createElement("td");
            cell.textContent = text;
            row.appendChild(cell);
        }
        const openCell = document.createElement("td");
        if (result) {
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = "Open";
            button.addEventListener("click", () => openSweepPoint(result));
            openCell.appendChild(button);
        }
        row.appendChild(openCell);
        sweepResultsBody.appendChild(row);
    }
}

for (const control of [sweepResultsStatistic, sweepResultsMode, sweepResultsTheory]) {
    control.addEventListener("change", renderSweepResults);
}
for (const control of [sweepResultsX, sweepResultsY]) {
    control.addEventListener("change", () => {
        refreshSweepHeldControls();
        renderSweepResults();
    });
}
sweepResultsSetupButton.addEventListener("click", () => window.fim.showSweepScreen());
window.addEventListener("resize", () => {
    if (!sweepResultsView.hidden && sweepResultsData !== null) {
        renderSweepResults();
    }
});
