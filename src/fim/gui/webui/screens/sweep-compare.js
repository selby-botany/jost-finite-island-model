"use strict";

/* Compare sweeps (`doc/20261014-claude-sonnet-5-5-sweep-features-design.md`,
 * `selby/restricted`, section 8): one statistic from two or more sweeps on
 * the axes they share, drawn as one curve per sweep, with a difference panel
 * when exactly two are compared. Nothing is stored: every view asks
 * `Api.compare_sweeps` (`fim.sweep_compare`), which defines what "comparable"
 * means, and the chart is the sweep results card's own (`drawSweepLine`).
 *
 * A sweep's theory curve uses that sweep's own base configuration, so two
 * sweeps that differ in a base setting each get their own dashed curve.
 */

const sweepCompareBanner = document.getElementById("sweep-compare-banner");
const sweepCompareTitle = document.getElementById("sweep-compare-title");
const sweepCompareSummary = document.getElementById("sweep-compare-summary");
const sweepCompareNotes = document.getElementById("sweep-compare-notes");
const sweepCompareStatistic = document.getElementById("sweep-compare-statistic");
const sweepCompareX = document.getElementById("sweep-compare-x");
const sweepCompareInterval = document.getElementById("sweep-compare-interval");
const sweepCompareXScale = document.getElementById("sweep-compare-xscale");
const sweepCompareYScale = document.getElementById("sweep-compare-yscale");
const sweepCompareTheory = document.getElementById("sweep-compare-theory");
const sweepCompareHeld = document.getElementById("sweep-compare-held");
const sweepCompareCanvas = document.getElementById("sweep-compare-canvas");
const sweepCompareReadout = document.getElementById("sweep-compare-readout");
const sweepCompareDifferencePanel = document.getElementById("sweep-compare-difference-panel");
const sweepCompareDifferenceTitle = document.getElementById("sweep-compare-difference-title");
const sweepCompareDifferenceCanvas = document.getElementById(
    "sweep-compare-difference-canvas"
);
const sweepCompareHead = document.getElementById("sweep-compare-head");
const sweepCompareBody = document.getElementById("sweep-compare-body");

// What the seeds of the compared runs mean for the interval of a difference.
const SWEEP_COMPARE_PAIRING_NOTES = {
    independent: "The compared runs used different seeds, so they are independent.",
    "shared-seeds":
        "The compared runs used the same seeds, so they are paired; the interval of a " +
        "difference assumes independence and is only approximate.",
    "partly-shared-seeds": "Some of the compared runs used the same seeds.",
    unknown: "No coordinate has a finished point in two of the sweeps.",
};

let sweepCompareStudyIds = [];
let sweepCompareData = null;
let sweepCompareSequence = 0;
// What the canvases currently show, for hover.
let sweepCompareHit = null;
let sweepCompareLastSpec = null;

window.__fimSweepCompareReady = false;

/**
 * Show (or, given a falsy message, hide) the compare screen's banner.
 * @param {string} [message]
 */
function showSweepCompareBanner(message) {
    sweepCompareBanner.hidden = !message;
    sweepCompareBanner.textContent = message || "";
}

/**
 * Ask for a comparison.
 * @param {string} statistic A statistic key, or "" for the default.
 * @returns {Promise<object>} The `Api.compare_sweeps` reply.
 */
async function requestSweepComparison(statistic) {
    return await window.pywebview.api.compare_sweeps(
        sweepCompareStudyIds,
        statistic,
        null,
        null
    );
}

/**
 * Open the compare screen for two or more sweep Studies.
 * @param {string[]} studyIds
 * @returns {Promise<{ok: boolean, message?: string}>} `ok: false` with the
 *     reason when the sweeps cannot be compared; the screen is not shown.
 */
window.fim.showSweepCompare = async function showSweepCompare(studyIds) {
    window.__fimSweepCompareReady = false;
    if (studyIds.length > SWEEP_MAX_SERIES) {
        return {
            ok: false,
            message: `Compare at most ${SWEEP_MAX_SERIES} sweeps at a time.`,
        };
    }
    sweepCompareStudyIds = studyIds;
    const reply = await requestSweepComparison("");
    if (!reply.ok) {
        return reply;
    }
    sweepCompareData = reply;
    showSweepCompareBanner("");
    populateSweepCompareControls(reply);
    window.fim.showScreen("screen-sweep-compare");
    await renderSweepCompare();
    return { ok: true };
};

/**
 * Fill the controls from a comparison.
 * @param {object} data
 */
function populateSweepCompareControls(data) {
    sweepCompareTitle.textContent = `Compare ${data.labels.join(" and ")}`;
    sweepCompareSummary.textContent =
        `${data.points.length} shared point${data.points.length === 1 ? "" : "s"}, ` +
        `joined on ${data.on.join(" and ")}.`;
    const shown = STATISTIC_NAMES.filter(
        (name) => data.available.includes(name) && isStatisticShown(name)
    );
    const offered = shown.length > 0 ? shown : data.available;
    sweepCompareStatistic.replaceChildren();
    for (const name of offered) {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = statisticDisplayName(name);
        sweepCompareStatistic.appendChild(option);
    }
    sweepCompareStatistic.value = data.statistic;
    sweepCompareX.replaceChildren();
    for (const key of data.on) {
        const option = document.createElement("option");
        option.value = key;
        option.textContent = key;
        sweepCompareX.appendChild(option);
    }
    sweepCompareX.value = data.on[0];
    sweepCompareInterval.value = "ci";
    sweepCompareXScale.value = "auto";
    sweepCompareYScale.value = "auto";
    sweepCompareTheory.checked = false;
    refreshSweepCompareHeld();
    writeSweepCompareNotes(data);
}

/**
 * List what the sweeps differ in and how their seeds relate.
 * @param {object} data
 */
function writeSweepCompareNotes(data) {
    const lines = [];
    for (const [key, values] of Object.entries(data.baseDifferences)) {
        const parts = Object.entries(values).map(([label, value]) => `${label}: ${value}`);
        lines.push(`Differs in ${key}: ${parts.join("; ")}`);
    }
    const policies = Object.entries(data.seedPolicies)
        .map(([label, policy]) => `${label}: ${policy}`)
        .join("; ");
    lines.push(`Seed policy: ${policies}`);
    lines.push(SWEEP_COMPARE_PAIRING_NOTES[data.pairing]);
    sweepCompareNotes.replaceChildren(
        ...lines.map((line) => {
            const item = document.createElement("li");
            item.textContent = line;
            return item;
        })
    );
}

/**
 * One "held at" select for each joined axis that is not across.
 */
function refreshSweepCompareHeld() {
    sweepCompareHeld.replaceChildren();
    for (const key of sweepCompareData.on) {
        if (key === sweepCompareX.value) {
            continue;
        }
        const field = document.createElement("div");
        field.className = "field";
        const label = document.createElement("label");
        label.textContent = `${key} held at`;
        const select = document.createElement("select");
        select.dataset.key = key;
        sweepCompareData.axes[key].forEach((value, index) => {
            const option = document.createElement("option");
            option.value = String(index);
            option.textContent = sweepFormatValue(value);
            select.appendChild(option);
        });
        select.addEventListener("change", renderSweepCompare);
        label.appendChild(select);
        field.appendChild(label);
        sweepCompareHeld.appendChild(field);
    }
}

/**
 * The value each held axis is fixed at.
 * @returns {Record<string, number|string>}
 */
function sweepCompareHeldValues() {
    const held = {};
    for (const select of sweepCompareHeld.querySelectorAll("select")) {
        held[select.dataset.key] = sweepCompareData.axes[select.dataset.key][Number(select.value)];
    }
    return held;
}

/**
 * A chart mark for one sweep's cell at a point.
 * @param {object} point
 * @param {string} label
 * @param {string} xKey
 * @returns {object}
 */
function sweepCompareMark(point, label, xKey) {
    const cell = point.cells[label];
    return {
        x: point.coordinates[xKey],
        index: -1,
        label,
        mean: cell.mean,
        ciLow: cell.low,
        ciHigh: cell.high,
        sdLow: cell.sdLow,
        sdHigh: cell.sdHigh,
        health: cell.health,
    };
}

/**
 * The closed-form values for one sweep at each x, held values included.
 * @param {string} studyId
 * @param {Array<Record<string, number|string>>} coordinates
 * @returns {Promise<Array<Record<string, number|null>>>}
 */
async function sweepCompareTheoryAt(studyId, coordinates) {
    const reply = await window.pywebview.api.get_sweep_theory(studyId, coordinates);
    return reply.ok ? reply.values : coordinates.map(() => ({}));
}

/**
 * Draw the comparison, its difference panel and its table.
 */
async function renderSweepCompare() {
    const sequence = (sweepCompareSequence += 1);
    window.__fimSweepCompareReady = false;
    const statistic = sweepCompareStatistic.value;
    if (statistic !== sweepCompareData.statistic) {
        const reply = await requestSweepComparison(statistic);
        if (sequence !== sweepCompareSequence) {
            return;
        }
        if (!reply.ok) {
            showSweepCompareBanner(reply.message);
            window.__fimSweepCompareReady = true;
            return;
        }
        sweepCompareData = reply;
        showSweepCompareBanner("");
    }
    const data = sweepCompareData;
    const xKey = sweepCompareX.value;
    const held = sweepCompareHeldValues();
    const points = data.points.filter((point) =>
        Object.entries(held).every(([key, value]) => point.coordinates[key] === value)
    );
    const values = data.axes[xKey];
    const numeric = values.every((value) => typeof value === "number");
    const xScale = sweepCompareXScale.value;
    const log = numeric && (xScale === "log" || (xScale === "auto" && sweepAxisIsLog(values)));
    const curves = data.labels.map((label, position) => ({
        label,
        color: SWEEP_SERIES_COLORS[position],
        shape: SWEEP_SERIES_SHAPES[position],
        byValue: label,
        marks: points.filter((point) => point.cells[label]).map((p) => sweepCompareMark(p, label, xKey)),
        theory: [],
    }));
    if (sweepCompareTheory.checked && numeric && SWEEP_THEORY_STATISTICS.has(statistic)) {
        const samples = sweepTheorySamples(values, log);
        const coordinates = samples.map((x) => ({ ...held, [xKey]: x }));
        const answers = await Promise.all(
            sweepCompareStudyIds.map((studyId) => sweepCompareTheoryAt(studyId, coordinates))
        );
        if (sequence !== sweepCompareSequence) {
            return;
        }
        curves.forEach((curve, index) => {
            curve.theory = samples.map((x, step) => ({
                x,
                y: answers[index][step][statistic] ?? null,
            }));
        });
    }
    sweepCompareCanvas.width = Math.max(sweepCompareCanvas.clientWidth, SWEEP_CANVAS_MIN_WIDTH);
    sweepCompareCanvas.height = SWEEP_LINE_CANVAS_HEIGHT;
    sweepCompareLastSpec = {
        xKey,
        values,
        numeric,
        log,
        curves,
        statistic,
        interval: sweepCompareInterval.value,
        yScale: sweepCompareYScale.value,
        focus: null,
    };
    sweepCompareHit = drawSweepLine(sweepCompareCanvas, sweepCompareLastSpec);
    drawSweepCompareDifference(data, points, xKey, values, numeric, log);
    drawSweepCompareTable(data);
    const drawn = curves.reduce((count, curve) => count + curve.marks.length, 0);
    sweepCompareReadout.textContent =
        drawn === 0
            ? "No finished points at these settings."
            : "Hover a point for its value.";
    window.__fimSweepCompareReady = true;
}

/**
 * Draw the panel for the difference of two sweeps (first minus second).
 * It is shown only when exactly two sweeps are compared.
 * @param {object} data
 * @param {object[]} points
 * @param {string} xKey
 * @param {Array<number|string>} values
 * @param {boolean} numeric
 * @param {boolean} log
 */
function drawSweepCompareDifference(data, points, xKey, values, numeric, log) {
    const two = data.labels.length === 2;
    sweepCompareDifferencePanel.hidden = !two;
    if (!two) {
        return;
    }
    const [first, second] = data.labels;
    sweepCompareDifferenceTitle.textContent =
        `Difference: ${first} minus ${second}` +
        " (interval by Welch's method; it assumes the runs are independent)";
    sweepCompareDifferenceCanvas.width = Math.max(
        sweepCompareDifferenceCanvas.clientWidth,
        SWEEP_CANVAS_MIN_WIDTH
    );
    sweepCompareDifferenceCanvas.height = SWEEP_LINE_CANVAS_HEIGHT;
    const marks = points
        .filter((point) => point.difference !== null)
        .map((point) => ({
            x: point.coordinates[xKey],
            index: -1,
            label: `${first} minus ${second}`,
            mean: point.difference.difference,
            ciLow: point.difference.low,
            ciHigh: point.difference.high,
            sdLow: null,
            sdHigh: null,
            health: "healthy",
        }));
    drawSweepLine(sweepCompareDifferenceCanvas, {
        xKey,
        values,
        numeric,
        log,
        curves: [
            {
                label: null,
                color: OKABE_ITO_BLACK,
                shape: "circle",
                byValue: null,
                marks,
                theory: [],
            },
        ],
        statistic: "",
        yLabel: `${first} minus ${second}`,
        zeroLine: true,
        interval: "ci",
        yScale: "linear",
        focus: null,
    });
}

/**
 * The table of every shared point: each sweep's mean and interval, the
 * difference for two sweeps, and a note where a point is flagged.
 * @param {object} data
 */
function drawSweepCompareTable(data) {
    const two = data.labels.length === 2;
    const headings = [...data.on];
    for (const label of data.labels) {
        headings.push(`${label} mean`, "low", "high");
    }
    if (two) {
        headings.push("difference", "low", "high");
    }
    sweepCompareHead.replaceChildren(sweepHeaderRow(headings));
    sweepCompareBody.replaceChildren();
    for (const point of data.points) {
        const row = document.createElement("tr");
        const texts = data.on.map((key) => sweepFormatValue(point.coordinates[key]));
        for (const label of data.labels) {
            const cell = point.cells[label];
            const flag = cell && cell.health !== "healthy" ? ` (${cell.health})` : "";
            texts.push(
                cell ? `${sweepFormatValue(cell.mean)}${flag}` : "",
                cell && cell.low !== null ? sweepFormatValue(cell.low) : "",
                cell && cell.high !== null ? sweepFormatValue(cell.high) : ""
            );
        }
        if (two) {
            const diff = point.difference;
            texts.push(
                diff ? sweepFormatValue(diff.difference) : "",
                diff && diff.low !== null ? sweepFormatValue(diff.low) : "",
                diff && diff.high !== null ? sweepFormatValue(diff.high) : ""
            );
        }
        for (const text of texts) {
            const cell = document.createElement("td");
            cell.textContent = text;
            row.appendChild(cell);
        }
        sweepCompareBody.appendChild(row);
    }
}

sweepCompareCanvas.addEventListener("mousemove", (event) => {
    if (sweepCompareHit === null) {
        return;
    }
    const box = sweepCompareCanvas.getBoundingClientRect();
    const x = ((event.clientX - box.left) * sweepCompareCanvas.width) / Math.max(box.width, 1);
    const y = ((event.clientY - box.top) * sweepCompareCanvas.height) / Math.max(box.height, 1);
    let best = null;
    let bestDistance = SWEEP_HIT_RADIUS_PX;
    for (const pixel of sweepCompareHit.pixels) {
        const distance = Math.hypot(pixel.x - x, pixel.y - y);
        if (distance < bestDistance) {
            best = pixel;
            bestDistance = distance;
        }
    }
    if (best === null) {
        return;
    }
    const mark = best.mark;
    const interval =
        mark.ciLow !== null && mark.ciHigh !== null
            ? ` [${sweepFormatValue(mark.ciLow)}, ${sweepFormatValue(mark.ciHigh)}]`
            : "";
    const flag = mark.health === "healthy" ? "" : `; ${mark.health}`;
    sweepCompareReadout.textContent =
        `${mark.label}: ${sweepCompareLastSpec.xKey} ${sweepFormatValue(mark.x)}, ` +
        `${statisticDisplayName(sweepCompareLastSpec.statistic)} ` +
        `${sweepFormatValue(mark.mean)}${interval}${flag}`;
});

sweepCompareX.addEventListener("change", () => {
    refreshSweepCompareHeld();
    renderSweepCompare();
});
for (const control of [
    sweepCompareStatistic,
    sweepCompareInterval,
    sweepCompareXScale,
    sweepCompareYScale,
    sweepCompareTheory,
]) {
    control.addEventListener("change", renderSweepCompare);
}
window.addEventListener("resize", () => {
    if (!document.getElementById("screen-sweep-compare").hidden && sweepCompareData !== null) {
        renderSweepCompare();
    }
});
