"use strict";

/* Sweep results (`20260923-claude-sonnet-5-sweep-as-study-implementation-
 * plan.md`, `selby/restricted`, §8): the statistic across the points of a
 * finished (or partly finished) sweep, read on open from the Study's own
 * stored plan and each point's own summary. Nothing is stored here.
 *
 * - One axis: the statistic at each value, with its confidence interval
 *   across replicates, and (optionally) the closed-form theory drawn
 *   behind it.
 * - A family of curves: "Curves by" splits the line into one curve per value
 *   of a second axis, each with its own color and marker shape, error bars
 *   (interval of the mean, spread, or both), and optional theory; the x and
 *   y axes can be linear or log.
 * - Two axes: a heat map (`heatmap.js`) of the statistic's mean, or of the
 *   theory, or of simulation minus theory. A sweep with more axes holds
 *   the others at a chosen value; every axis is stored, any two are shown.
 * - Always a table of every planned point beside the chart, and a click on
 *   a point or a cell opens that run's own Results card.
 * - A point whose replicates did not all settle before the generation limit
 *   is flagged, never removed: a hollow ring on the chart or heat map (an X
 *   through it when none settled), its reason in the table and the readout.
 */

const sweepResultsSummary = document.getElementById("sweep-results-summary");
const sweepResultsViewSelect = document.getElementById("sweep-results-view");
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
const sweepResultsBy = document.getElementById("sweep-results-by");
const sweepResultsByField = document.getElementById("sweep-results-by-field");
const sweepResultsInterval = document.getElementById("sweep-results-interval");
const sweepResultsIntervalField = document.getElementById("sweep-results-interval-field");
const sweepResultsXScale = document.getElementById("sweep-results-xscale");
const sweepResultsXScaleField = document.getElementById("sweep-results-xscale-field");
const sweepResultsYScale = document.getElementById("sweep-results-yscale");
const sweepResultsYScaleField = document.getElementById("sweep-results-yscale-field");
const sweepResultsSaveTable = document.getElementById("sweep-results-save-table");
const sweepResultsSaveChart = document.getElementById("sweep-results-save-chart");
const sweepResultsView = document.getElementById("sweep-results");

// Whether a statistic is on a fixed 0-to-1 scale comes from the catalog
// (`isProportionStatistic`); every other statistic fits its data.
// Statistics the closed form can predict (`Api.get_sweep_theory`).
const SWEEP_THEORY_STATISTICS = new Set(["D", "G_ST", "E_ST", "H_S", "H_T"]);
// A point whose only run came from another software version is recomputed
// (and compared) the next time the sweep runs.
const SWEEP_STATE_LABELS = { stale: "older version (rerun to update)" };

let sweepResultsData = null;
let sweepResultsStudyId = null;
let sweepResultsRenderSequence = 0;
let sweepResultsPending = 0;
// What the canvas currently shows, for hover and click: pixel points for a
// line chart, or a heat-map layout with the run each cell holds.
let sweepResultsHit = null;
// The line chart's last spec (so the keyboard focus can redraw it without a
// new request) and the point the keyboard is on: `{curve, position}`.
let sweepResultsLastSpec = null;
let sweepFocus = null;
function sweepResultsControlState() {
    return {
        view: sweepResultsViewSelect.value,
        statistic: sweepResultsStatistic.value,
        x: sweepResultsX.value,
        y: sweepResultsY.value,
        mode: sweepResultsMode.value,
        theory: sweepResultsTheory.checked,
        by: sweepResultsBy.value,
        interval: sweepResultsInterval.value,
        xScale: sweepResultsXScale.value,
        yScale: sweepResultsYScale.value,
    };
}

function saveSweepResultsDocumentState(tabId) {
    const state = window.fim.workspace.get(tabId);
    if (!state || !sweepResultsData || !sweepResultsStudyId) {
        return;
    }
    state.sweepResultsData = sweepResultsData;
    state.sweepResultsStudyId = sweepResultsStudyId;
    state.sweepResultsControls = sweepResultsControlState();
}

function restoreSweepResultsControls(controls) {
    if (!controls) {
        return;
    }
    for (const [control, value] of [
        [sweepResultsViewSelect, controls.view],
        [sweepResultsStatistic, controls.statistic],
        [sweepResultsX, controls.x],
        [sweepResultsY, controls.y],
        [sweepResultsMode, controls.mode],
        [sweepResultsBy, controls.by],
        [sweepResultsInterval, controls.interval],
        [sweepResultsXScale, controls.xScale],
        [sweepResultsYScale, controls.yScale],
    ]) {
        if (value !== undefined && [...control.options].some((item) => item.value === value)) {
            control.value = value;
        }
    }
    if (controls.theory !== undefined) {
        sweepResultsTheory.checked = controls.theory;
    }
}

async function showSweepResultsDocument(state) {
    sweepResultsData = state.sweepResultsData;
    sweepResultsStudyId = state.sweepResultsStudyId;
    sweepResultsSummary.textContent = sweepResultsSummaryText(sweepResultsData);
    populateSweepResultsControls(sweepResultsData);
    restoreSweepResultsControls(state.sweepResultsControls);
    sweepTitle.textContent = sweepResultsData.name;
    window.fim.attachDetails(sweepTitle, sweepStudyDetails(sweepResultsData));
    sweepProgressView.hidden = true;
    sweepResultsView.hidden = false;
    window.fim.showScreen("screen-sweep");
    await renderSweepResults();
}

window.fim.workspace.onSelectionChange((tabId, previousId) => {
    if (previousId !== null && sweepResultsData) {
        saveSweepResultsDocumentState(previousId);
    }
    sweepResultsRenderSequence += 1;
    const state = tabId === null ? null : window.fim.workspace.get(tabId);
    if (!state?.sweepResultsData) {
        sweepResultsData = null;
        sweepResultsStudyId = null;
        sweepResultsView.hidden = true;
        if (sweepResultsPending === 0) {
            window.__fimSweepResultsReady = true;
        }
        return;
    }
    void showSweepResultsDocument(state);
});

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
 * Whether an axis's values are geometrically spaced across a wide range,
 * so it reads best on a log scale: consecutive ratios agree to within 5%
 * and the whole span is a factor of 5 or more (two values: 20 or more).
 * @param {Array<number|string>} values
 * @returns {boolean}
 */
function sweepAxisIsLog(values) {
    if (!values.every((value) => typeof value === "number" && value > 0)) {
        return false;
    }
    const span = Math.max(...values) / Math.min(...values);
    if (values.length < 3) {
        return span >= SWEEP_LOG_TWO_VALUE_SPAN;
    }
    const ratios = values.slice(1).map((value, index) => value / values[index]);
    const consistent = ratios.every(
        (ratio) => Math.abs(ratio / ratios[0] - 1) < SWEEP_LOG_RATIO_TOLERANCE
    );
    return consistent && span >= SWEEP_LOG_MIN_SPAN;
}

/**
 * The results joined to their planned points, keyed by grid index.
 * @returns {Map<number, object>}
 */
function sweepResultsByIndex() {
    return new Map(sweepResultsData.results.map((result) => [result.index, result]));
}

/**
 * How far to trust one point's result.
 * @param {object|null} result A results entry.
 * @returns {"healthy"|"degraded"|"unreliable"}
 */
function sweepHealthClass(result) {
    return result && result.health ? result.health.class : "healthy";
}

/**
 * Why a point is flagged, in words; empty when it is healthy.
 * @param {object|null} result A results entry.
 * @returns {string}
 */
function sweepHealthReason(result) {
    return result && result.health ? result.health.reason : "";
}

/**
 * The marker a heat map or chart gives a point of this health.
 * @param {"healthy"|"degraded"|"unreliable"} health
 * @returns {"dot"|"ring"|"crossed"}
 */
function sweepHealthMarker(health) {
    return { healthy: "dot", degraded: "ring", unreliable: "crossed" }[health];
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
    sweepResultsPending += 1;
    window.__fimSweepResultsReady = false;
    try {
        const workspace = window.fim.workspace;
        let tabId = workspace.selectedId();
        let created = false;
        if (workspace.documentTabsEnabled()) {
            const opened = workspace.openResource(`sweep-results:${studyId}`, {
                kind: "sweep-results",
                label: "Sweep results",
                readOnly: true,
                history: ["screen-sweep"],
            });
            tabId = opened.tabId;
            created = opened.created;
            if (!created) {
                activateWorkspaceDocument(tabId);
                return;
            }
        }
        const documentState = workspace.get(tabId);
        const data = await window.pywebview.api.get_sweep_results(studyId);
        if (workspace.get(tabId) !== documentState) {
            return;
        }
        if (!data.ok) {
            if (created) {
                workspace.closeDocument(tabId);
            }
            if (
                !workspace.documentTabsEnabled() ||
                workspace.selectedId() === tabId
            ) {
                window.fim.showScreen("screen-sweep");
                showSweepBanner(data.message);
            }
            return;
        }
        documentState.sweepResultsData = data;
        documentState.sweepResultsStudyId = studyId;
        documentState.readOnly = true;
        documentState.kind = "sweep-results";
        documentState.label = data.name || "Sweep results";
        documentState.sweepResultsControls = null;
        if (workspace.selectedId() === tabId) {
            showSweepBanner("");
            await showSweepResultsDocument(documentState);
        }
    } finally {
        sweepResultsPending -= 1;
        if (sweepResultsPending === 0) {
            window.__fimSweepResultsReady = true;
        }
    }
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
    const flagged = data.results.filter((result) => sweepHealthClass(result) !== "healthy");
    const warning =
        flagged.length > 0
            ? ` ${flagged.length} flagged (drawn hollow): not every replicate settled.`
            : "";
    return `${done} of ${total} points finished, varying ${axes}.${remaining}${warning}`;
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
    // Only statistics the researcher has chosen to show are offered.
    const offered = STATISTIC_NAMES.filter(
        (entry) => available.has(entry) && isStatisticShown(entry)
    );
    for (const name of offered) {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = statisticDisplayName(name);
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
    sweepResultsViewSelect.value = "values";
    sweepResultsMode.value = "simulation";
    sweepResultsTheory.checked = false;
    sweepResultsBy.value = "";
    sweepResultsInterval.value = "ci";
    sweepResultsXScale.value = "auto";
    sweepResultsYScale.value = "auto";
    refreshSweepHeldControls();
}

/**
 * Offer every axis but the ones already across and "and" as the "Curves by"
 * axis, keeping the choice if it is still offered.
 */
function refreshSweepByControl() {
    const previous = sweepResultsBy.value;
    const taken = sweepShowsTrajectories() ? [] : [sweepResultsX.value, sweepResultsY.value];
    sweepResultsBy.replaceChildren();
    const none = document.createElement("option");
    none.value = "";
    none.textContent = "(none: one curve)";
    sweepResultsBy.appendChild(none);
    for (const axis of sweepResultsData.axes) {
        if (taken.includes(axis.key)) {
            continue;
        }
        const option = document.createElement("option");
        option.value = axis.key;
        option.textContent = axis.key;
        sweepResultsBy.appendChild(option);
    }
    sweepResultsBy.value = Array.from(sweepResultsBy.options).some(
        (option) => option.value === previous
    )
        ? previous
        : "";
}

/**
 * Rebuild the "held at" selects for the axes that are not displayed.
 */
function refreshSweepHeldControls() {
    refreshSweepByControl();
    const shown = (
        sweepShowsTrajectories()
            ? [sweepResultsBy.value]
            : [sweepResultsX.value, sweepResultsY.value, sweepResultsBy.value]
    ).filter((key) => key !== "");
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
    const tabId = window.fim.workspace.selectedId();
    if (tabId !== null && sweepResultsData) {
        saveSweepResultsDocumentState(tabId);
    }
    if (!sweepResultsData) {
        window.__fimSweepResultsReady = true;
        return;
    }
    const statistic = sweepResultsStatistic.value;
    const trajectory = sweepShowsTrajectories();
    const xKey = sweepResultsX.value;
    const yKey = !trajectory && sweepResultsY.value !== xKey ? sweepResultsY.value : "";
    const byKey = sweepResultsBy.value;
    const points = sweepFilteredPoints();
    const hasTheory = SWEEP_THEORY_STATISTICS.has(statistic);
    for (const field of [
        sweepResultsTheoryField,
        sweepResultsByField,
        sweepResultsIntervalField,
        sweepResultsXScaleField,
        sweepResultsYScaleField,
    ]) {
        field.hidden = yKey !== "";
    }
    sweepResultsX.closest(".field").hidden = trajectory;
    sweepResultsY.closest(".field").hidden = trajectory;
    sweepResultsTheory.disabled = !hasTheory;
    sweepResultsModeField.hidden = yKey === "";
    for (const option of sweepResultsMode.options) {
        option.disabled = option.value !== "simulation" && !hasTheory;
    }
    if (!hasTheory) {
        sweepResultsMode.value = "simulation";
    }
    sweepResultsCanvas.width = Math.max(sweepResultsCanvas.clientWidth, SWEEP_CANVAS_MIN_WIDTH);
    sweepResultsCanvas.height =
        yKey === "" ? SWEEP_LINE_CANVAS_HEIGHT : SWEEP_HEATMAP_CANVAS_HEIGHT;
    if (statistic === "") {
        sweepResultsReadout.textContent = "No finished points to show yet.";
        drawSweepResultsTable(statistic);
        window.__fimSweepResultsReady = true;
        return;
    }
    if (trajectory) {
        await renderSweepTrajectories(sequence, byKey, statistic);
    } else if (yKey === "") {
        await renderSweepLine(sequence, xKey, byKey, statistic);
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
 * Draw the one-axis chart: one curve, or a family of curves (one per value
 * of the "Curves by" axis), each with its error bars, markers and theory.
 * @param {number} sequence
 * @param {string} xKey
 * @param {string} byKey The axis that splits the curves, or "".
 * @param {string} statistic
 */
async function renderSweepLine(sequence, xKey, byKey, statistic) {
    const values = sweepAxisValues(xKey);
    const numeric = values.every((value) => typeof value === "number");
    const xScale = sweepResultsXScale.value;
    const log = numeric && (xScale === "log" || (xScale === "auto" && sweepAxisIsLog(values)));
    const held = sweepHeldValues();
    const reply = await window.pywebview.api.get_sweep_series(
        sweepResultsStudyId,
        xKey,
        statistic,
        byKey || null,
        held
    );
    if (sequence !== sweepResultsRenderSequence) {
        return;
    }
    if (!reply.ok) {
        sweepResultsHit = null;
        sweepResultsLastSpec = null;
        sweepFocus = null;
        const context = sweepResultsCanvas.getContext("2d");
        context.clearRect(0, 0, sweepResultsCanvas.width, sweepResultsCanvas.height);
        sweepResultsReadout.textContent = reply.message;
        return;
    }
    const single = STATISTIC_TRAJECTORY_COLORS[statistic] || SWEEP_FALLBACK_COLOR;
    const curves = reply.series.slice(0, SWEEP_MAX_SERIES).map((curve, position) => ({
        label: byKey ? `${byKey} = ${sweepFormatValue(curve.byValue)}` : null,
        color: byKey ? SWEEP_SERIES_COLORS[position] : single,
        shape: byKey ? SWEEP_SERIES_SHAPES[position] : "circle",
        byValue: curve.byValue,
        marks: curve.points,
        theory: [],
    }));
    if (sweepResultsTheory.checked && numeric) {
        const samples = sweepTheorySamples(values, log);
        const coordinates = curves.flatMap((curve) =>
            samples.map((x) => ({
                ...held,
                ...(byKey ? { [byKey]: curve.byValue } : {}),
                [xKey]: x,
            }))
        );
        const predicted = await sweepTheoryAt(coordinates);
        if (sequence !== sweepResultsRenderSequence) {
            return;
        }
        curves.forEach((curve, index) => {
            curve.theory = samples.map((x, step) => ({
                x,
                y: predicted[index * samples.length + step][statistic] ?? null,
            }));
        });
    }
    sweepFocus = null;
    redrawSweepLine({
        xKey,
        values,
        numeric,
        log,
        curves,
        statistic,
        interval: sweepResultsInterval.value,
        yScale: sweepResultsYScale.value,
    });
    const drawn = curves.reduce((count, curve) => count + curve.marks.length, 0);
    const cut =
        reply.series.length > SWEEP_MAX_SERIES
            ? ` Showing the first ${SWEEP_MAX_SERIES} of ${reply.series.length} curves; hold an axis or use a heat map for the rest.`
            : "";
    sweepResultsReadout.textContent =
        (drawn === 0
            ? "No finished points at these settings."
            : "Hover a point for its value; click it to open that run, or use the arrow keys and Enter.") +
        cut;
}

/**
 * Draw the line chart again from its last spec, with the keyboard focus.
 * @param {object} spec
 */
function redrawSweepLine(spec) {
    sweepResultsLastSpec = spec;
    sweepResultsHit = drawSweepLine(sweepResultsCanvas, { ...spec, focus: sweepFocus });
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
    const rough = (high - low) / SWEEP_TICK_INTERVALS;
    const magnitude = 10 ** Math.floor(Math.log10(rough));
    const step = TICK_STEP_MULTIPLES.map((factor) => factor * magnitude).find((s) => s >= rough);
    const ticks = [];
    const end = high + step * TICK_EDGE_EPSILON;
    for (let tick = Math.ceil(low / step) * step; tick <= end; tick += step) {
        ticks.push(Number(tick.toPrecision(SWEEP_TICK_PRECISION)));
    }
    return ticks;
}

/**
 * The values an error bar of this point reaches, for fitting the y axis.
 * @param {object} mark
 * @param {string} interval "ci", "sd" or "both".
 * @returns {number[]}
 */
function sweepBarEnds(mark, interval) {
    const ends = [mark.mean];
    if (interval !== "sd") {
        ends.push(mark.ciLow ?? mark.mean, mark.ciHigh ?? mark.mean);
    }
    if (interval !== "ci") {
        ends.push(mark.sdLow ?? mark.mean, mark.sdHigh ?? mark.mean);
    }
    return ends;
}

/**
 * Powers of ten between two powers of ten.
 * @param {number} lowExponent
 * @param {number} highExponent
 * @returns {number[]}
 */
function sweepLogTicks(lowExponent, highExponent) {
    const ticks = [];
    for (let exponent = lowExponent; exponent <= highExponent; exponent += 1) {
        ticks.push(10 ** exponent);
    }
    return ticks;
}

/**
 * Draw the one-axis chart and return its pixel points for hit testing.
 * @param {HTMLCanvasElement} canvas
 * @param {object} spec
 * @returns {{kind: "line", pixels: Array<{x: number, y: number, mark: object,
 *   curve: number, position: number}>}}
 */
function drawSweepLine(canvas, spec) {
    const context = canvas.getContext("2d");
    const style = getComputedStyle(document.documentElement);
    const muted = style.getPropertyValue("--fim-muted").trim() || THEME_FALLBACK_MUTED;
    const border = style.getPropertyValue("--fim-border").trim() || THEME_FALLBACK_BORDER;
    const foreground =
        style.getPropertyValue("--fim-foreground").trim() || THEME_FALLBACK_FOREGROUND;
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.font = FONT_AXIS_LARGE;
    const left = SWEEP_LINE_MARGIN.left;
    const top = SWEEP_LINE_MARGIN.top;
    const width = canvas.width - left - SWEEP_LINE_MARGIN.right;
    const height = canvas.height - top - SWEEP_LINE_MARGIN.bottom;

    const marks = spec.curves.flatMap((curve) => curve.marks);
    const reached = marks.flatMap((mark) => sweepBarEnds(mark, spec.interval));
    const theoryValues = spec.curves
        .flatMap((curve) => curve.theory.map((entry) => entry.y))
        .filter((y) => y !== null);
    // A difference chart always shows zero, the line that means "no difference".
    const everything = [...reached, ...theoryValues, ...(spec.zeroLine ? [0] : [])];
    const positive = everything.filter((value) => value > 0);
    const yLog = spec.yScale === "log" && positive.length > 0;
    let yLow = Math.min(...everything, Infinity);
    let yHigh = Math.max(...everything, -Infinity);
    let lowExponent = 0;
    let highExponent = 0;
    if (yLog) {
        lowExponent = Math.floor(Math.log10(Math.min(...positive)));
        highExponent = Math.max(
            Math.ceil(Math.log10(Math.max(...positive))),
            lowExponent + SWEEP_LOG_MIN_DECADES
        );
        yLow = 10 ** lowExponent;
        yHigh = 10 ** highExponent;
    } else if (spec.statistic !== "" && isProportionStatistic(spec.statistic)) {
        yLow = 0;
        yHigh = 1;
    } else if (!(yHigh > yLow)) {
        yLow = Number.isFinite(yLow) ? yLow - 1 : 0;
        yHigh = Number.isFinite(yHigh) ? yHigh + 1 : 1;
    }
    // A value off a log axis (zero or negative) sits on the bottom edge.
    const toY = yLog
        ? (value) =>
              value > 0
                  ? top +
                    height -
                    Math.min(
                        1,
                        Math.max(
                            0,
                            (Math.log10(value) - lowExponent) / (highExponent - lowExponent)
                        )
                    ) *
                        height
                  : top + height
        : (value) => top + height - ((value - yLow) / (yHigh - yLow)) * height;

    let toX;
    if (spec.numeric) {
        const low = Math.min(...spec.values);
        const high = Math.max(...spec.values);
        const scale = spec.log ? Math.log : (value) => value;
        const span = scale(high) - scale(low) || 1;
        toX = (value) =>
            left +
            SWEEP_X_INSET +
            ((scale(value) - scale(low)) / span) * (width - 2 * SWEEP_X_INSET);
    } else {
        toX = (value) =>
            left + ((spec.values.indexOf(value) + 0.5) / spec.values.length) * width;
    }

    context.strokeStyle = border;
    context.fillStyle = muted;
    context.textAlign = "right";
    const yTicks = yLog ? sweepLogTicks(lowExponent, highExponent) : sweepTicks(yLow, yHigh);
    for (const tick of yTicks) {
        const y = toY(tick);
        context.beginPath();
        context.moveTo(left, y);
        context.lineTo(left + width, y);
        context.stroke();
        context.fillText(
            window.fim.heatmap.format(tick),
            left - SWEEP_Y_LABEL_GAP,
            y + SWEEP_Y_LABEL_BASELINE
        );
    }
    context.textAlign = "center";
    const labelSlots = Math.max(width / SWEEP_MIN_X_LABEL_SPACING_PX, 1);
    const stride = Math.max(1, Math.ceil(spec.values.length / labelSlots));
    spec.values.forEach((value, index) => {
        if (index % stride === 0) {
            context.fillText(
                sweepFormatValue(value),
                toX(value),
                top + height + SWEEP_X_LABEL_DROP
            );
        }
    });
    context.fillStyle = foreground;
    const xTitle = `${spec.xKey}${spec.log ? " (log scale)" : ""}`;
    context.fillText(xTitle, left + width / 2, canvas.height - SWEEP_X_TITLE_RISE);
    context.save();
    context.translate(SWEEP_Y_TITLE_INSET, top + height / 2);
    context.rotate(-QUARTER_TURN);
    context.fillText(
        `${spec.yLabel ?? statisticDisplayName(spec.statistic)}${yLog ? " (log scale)" : ""}`,
        0,
        0
    );
    context.restore();
    if (spec.zeroLine && !yLog) {
        context.save();
        context.strokeStyle = foreground;
        context.lineWidth = AXIS_LINE_WIDTH;
        context.setLineDash(DASH_THEORY_CURVE);
        context.beginPath();
        context.moveTo(left, toY(0));
        context.lineTo(left + width, toY(0));
        context.stroke();
        context.restore();
    }

    for (const curve of spec.curves) {
        drawSweepTheory(context, curve.theory, toX, toY, curve.color);
    }

    const pixels = [];
    const single = spec.curves.length <= 1;
    spec.curves.forEach((curve, curveIndex) => {
        const own = curve.marks.map((mark, position) => ({
            x: toX(mark.x),
            y: toY(mark.mean),
            mark,
            curve: curveIndex,
            position,
        }));
        context.strokeStyle = curve.color;
        context.lineWidth = CURVE_LINE_WIDTH;
        if (spec.numeric && own.length > 1) {
            context.beginPath();
            own.forEach((pixel, index) => {
                if (index === 0) {
                    context.moveTo(pixel.x, pixel.y);
                } else {
                    context.lineTo(pixel.x, pixel.y);
                }
            });
            context.stroke();
        }
        context.lineWidth = AXIS_LINE_WIDTH;
        for (const pixel of own) {
            drawSweepErrorBars(context, pixel, toY, curve.color, spec.interval);
            drawSweepMarker(
                context,
                pixel.x,
                pixel.y,
                sweepHealthMarker(pixel.mark.health),
                curve.color,
                curve.shape,
                single ? SWEEP_POINT_RADIUS : SWEEP_SERIES_MARKER_RADIUS
            );
        }
        pixels.push(...own);
    });
    drawSweepLegend(context, canvas, spec.curves, marks, foreground);
    const focused = spec.focus
        ? pixels.find(
              (pixel) =>
                  pixel.curve === spec.focus.curve && pixel.position === spec.focus.position
          )
        : null;
    if (focused) {
        context.strokeStyle = foreground;
        context.lineWidth = SWEEP_FOCUS_LINE_WIDTH;
        context.beginPath();
        context.arc(focused.x, focused.y, SWEEP_FOCUS_RADIUS, 0, TAU);
        context.stroke();
        context.lineWidth = AXIS_LINE_WIDTH;
    }
    return { kind: "line", pixels, statistic: spec.statistic, xKey: spec.xKey };
}

/**
 * Draw one point's error bars: the interval of the mean as a solid bar with
 * caps, the spread across replicates as a thinner dashed one.
 * @param {CanvasRenderingContext2D} context
 * @param {{x: number, y: number, mark: object}} pixel
 * @param {(value: number) => number} toY
 * @param {string} color
 * @param {string} interval "ci", "sd" or "both".
 */
function drawSweepErrorBars(context, pixel, toY, color, interval) {
    const bars = [];
    if (interval !== "sd" && pixel.mark.ciLow !== null && pixel.mark.ciHigh !== null) {
        bars.push({ low: pixel.mark.ciLow, high: pixel.mark.ciHigh, solid: true });
    }
    if (interval !== "ci" && pixel.mark.sdLow !== null && pixel.mark.sdHigh !== null) {
        bars.push({ low: pixel.mark.sdLow, high: pixel.mark.sdHigh, solid: false });
    }
    context.save();
    context.strokeStyle = color;
    for (const bar of bars) {
        context.lineWidth = bar.solid ? AXIS_LINE_WIDTH : SWEEP_SD_LINE_WIDTH;
        const cap = bar.solid ? SWEEP_ERROR_CAP_HALF_WIDTH : SWEEP_ERROR_CAP_HALF_WIDTH + 1;
        context.setLineDash(bar.solid ? [] : SWEEP_SD_DASH);
        context.beginPath();
        context.moveTo(pixel.x, toY(bar.low));
        context.lineTo(pixel.x, toY(bar.high));
        context.stroke();
        context.setLineDash([]);
        context.beginPath();
        for (const end of [bar.low, bar.high]) {
            context.moveTo(pixel.x - cap, toY(end));
            context.lineTo(pixel.x + cap, toY(end));
        }
        context.stroke();
    }
    context.restore();
}

/**
 * Trace the outline of a marker shape around a point.
 * @param {CanvasRenderingContext2D} context
 * @param {string} shape A name from `SWEEP_SERIES_SHAPES`.
 * @param {number} x
 * @param {number} y
 * @param {number} radius
 */
function traceSweepShape(context, shape, x, y, radius) {
    context.beginPath();
    const sides = SWEEP_POLYGON_SIDES[shape];
    if (sides === undefined) {
        context.arc(x, y, radius, 0, TAU);
        return;
    }
    // A square is a diamond turned an eighth of a turn; a downward triangle
    // is an upward one turned half a turn.
    const turn = shape === "square" ? Math.PI / 4 : shape === "triangle-down" ? Math.PI : 0;
    for (let corner = 0; corner < sides; corner += 1) {
        const angle = -Math.PI / 2 + turn + (corner * TAU) / sides;
        const px = x + radius * Math.cos(angle);
        const py = y + radius * Math.sin(angle);
        if (corner === 0) {
            context.moveTo(px, py);
        } else {
            context.lineTo(px, py);
        }
    }
    context.closePath();
}

/**
 * Draw one point's marker: filled when its replicates settled, a hollow
 * outline when some did not, with an X through it when none did. The shape
 * (circle, square, ...) tells curves apart without color.
 * @param {CanvasRenderingContext2D} context
 * @param {number} x
 * @param {number} y
 * @param {"dot"|"ring"|"crossed"} kind
 * @param {string} color
 * @param {string} [shape] A name from `SWEEP_SERIES_SHAPES`.
 * @param {number} [radius] The filled marker's radius.
 */
function drawSweepMarker(context, x, y, kind, color, shape = "circle", radius = SWEEP_POINT_RADIUS) {
    context.strokeStyle = color;
    context.fillStyle = color;
    if (kind === "dot") {
        traceSweepShape(context, shape, x, y, radius);
        context.fill();
        return;
    }
    context.lineWidth = SWEEP_FLAG_LINE_WIDTH;
    traceSweepShape(context, shape, x, y, Math.max(radius, SWEEP_FLAG_RADIUS));
    context.stroke();
    if (kind === "crossed") {
        const reach = SWEEP_FLAG_CROSS_REACH;
        context.beginPath();
        context.moveTo(x - reach, y - reach);
        context.lineTo(x + reach, y + reach);
        context.moveTo(x - reach, y + reach);
        context.lineTo(x + reach, y - reach);
        context.stroke();
    }
    context.lineWidth = AXIS_LINE_WIDTH;
}

/**
 * The key to the chart, in its top right: one line per curve of a family,
 * then one per kind of hollow marker the chart actually shows.
 * @param {CanvasRenderingContext2D} context
 * @param {HTMLCanvasElement} canvas
 * @param {object[]} curves
 * @param {object[]} marks Every mark drawn.
 * @param {string} textColor
 */
function drawSweepLegend(context, canvas, curves, marks, textColor) {
    const entries = [];
    if (curves.length > 1) {
        for (const curve of curves) {
            entries.push({ label: curve.label, kind: "dot", color: curve.color, shape: curve.shape });
        }
    }
    const neutral = curves.length > 1 ? textColor : (curves[0] && curves[0].color) || textColor;
    for (const entry of [
        { health: "degraded", label: "degraded: some replicates did not settle" },
        { health: "unreliable", label: "unreliable: none settled" },
    ]) {
        if (marks.some((mark) => mark.health === entry.health)) {
            entries.push({
                label: entry.label,
                kind: sweepHealthMarker(entry.health),
                color: neutral,
                shape: "circle",
            });
        }
    }
    context.save();
    context.font = FONT_AXIS_LARGE;
    context.textAlign = "right";
    entries.forEach((entry, line) => {
        const y = SWEEP_LEGEND_INSET + line * SWEEP_LEGEND_LINE_HEIGHT;
        const textX = canvas.width - SWEEP_LEGEND_INSET;
        context.fillStyle = textColor;
        context.fillText(entry.label, textX, y + SWEEP_Y_LABEL_BASELINE);
        const width = context.measureText(entry.label).width;
        drawSweepMarker(
            context,
            textX - width - SWEEP_LEGEND_TEXT_GAP,
            y,
            entry.kind,
            entry.color,
            entry.shape,
            SWEEP_SERIES_MARKER_RADIUS
        );
    });
    context.restore();
}

/**
 * Draw one curve's dashed theory line, breaking it wherever it is undefined.
 * @param {CanvasRenderingContext2D} context
 * @param {Array<{x: number, y: number|null}>} theory
 * @param {(value: number) => number} toX
 * @param {(value: number) => number} toY
 * @param {string} color
 */
function drawSweepTheory(context, theory, toX, toY, color) {
    if (theory.length === 0) {
        return;
    }
    context.save();
    context.strokeStyle = color;
    context.lineWidth = OVERLAY_LINE_WIDTH;
    context.setLineDash(DASH_THEORY_CURVE);
    let drawing = false;
    context.beginPath();
    for (const entry of theory) {
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
            if (mode === "relative") {
                return value !== null && theory[r][c] !== null && theory[r][c] !== 0
                    ? (value - theory[r][c]) / theory[r][c]
                    : null;
            }
            return value;
        })
    );
    const flat = shownValues.flat().filter((value) => value !== null);
    const diverging = mode === "difference" || mode === "relative";
    let min = flat.length > 0 ? Math.min(...flat) : 0;
    let max = flat.length > 0 ? Math.max(...flat) : 1;
    if (diverging) {
        const reach = Math.max(Math.abs(min), Math.abs(max), 1e-9);
        min = -reach;
        max = reach;
    } else if (isProportionStatistic(statistic)) {
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
        valueTitle:
            mode === "relative"
                ? `Δ ${statisticDisplayName(statistic)} / theory`
                : `${diverging ? "Δ " : ""}${statisticDisplayName(statistic)}`,
        selected: null,
        markers: sweepFlaggedMarkers(cells),
    });
    sweepResultsHit = { kind: "heatmap", layout, cells, shownValues, xValues, yValues, mode };
    sweepResultsReadout.textContent =
        flat.length === 0
            ? "No finished points at these settings."
            : "Hover a cell for its value; click it to open that run.";
}

/**
 * Heat map markers for the cells whose point is flagged, at cell centres.
 * @param {Array<Array<object|null>>} cells Rows of planned points with results.
 * @returns {Array<{row: number, column: number, kind: string}>}
 */
function sweepFlaggedMarkers(cells) {
    const markers = [];
    cells.forEach((row, rowIndex) =>
        row.forEach((cell, columnIndex) => {
            const health = sweepHealthClass(cell && cell.result);
            if (health !== "healthy") {
                markers.push({
                    row: rowIndex + 0.5,
                    column: columnIndex + 0.5,
                    kind: sweepHealthMarker(health),
                });
            }
        })
    );
    return markers;
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
    const reason = sweepHealthReason(point.result);
    const count = point.result.nReplicates;
    const replicates = `${count} replicate${count === 1 ? "" : "s"}`;
    const health = reason ? `; ${sweepHealthClass(point.result)}: ${reason}` : "";
    const value = `${statisticDisplayName(statistic)} ${sweepFormatValue(stat.mean)}`;
    return `${where}: ${value}${interval} (${replicates})${health}`;
}

/**
 * Open the run behind one point or cell on the ordinary Results card.
 * @param {object} result A results entry (`directory`, `nReplicates`).
 */
async function openSweepPoint(result) {
    if (result.nReplicates > 1) {
        await openBatch(result.directory);
    } else {
        await openTrajectory(`${result.directory}/trajectory.tlog`);
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
    if (sweepResultsHit.kind === "trajectory") {
        const box = sweepResultsCanvas.getBoundingClientRect();
        const x = ((event.clientX - box.left) * sweepResultsCanvas.width) / Math.max(box.width, 1);
        const y =
            ((event.clientY - box.top) * sweepResultsCanvas.height) / Math.max(box.height, 1);
        const pixel = sweepTrajectorySampleAt(x, y);
        return pixel === null ? null : { trajectory: pixel };
    }
    if (sweepResultsHit.kind === "line") {
        const box = sweepResultsCanvas.getBoundingClientRect();
        const x = ((event.clientX - box.left) * sweepResultsCanvas.width) / Math.max(box.width, 1);
        const y =
            ((event.clientY - box.top) * sweepResultsCanvas.height) / Math.max(box.height, 1);
        let best = null;
        let bestDistance = SWEEP_HIT_RADIUS_PX;
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
    if (target.trajectory) {
        showSweepTrajectorySample(target.trajectory);
    } else if (target.mark) {
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

/**
 * Move the keyboard focus around the line chart: left and right along a
 * curve, up and down between curves; Enter opens the point's run.
 * @param {KeyboardEvent} event
 */
function moveSweepFocus(event) {
    if (sweepResultsHit !== null && sweepResultsHit.kind === "trajectory") {
        moveSweepTrajectoryFocus(event);
        return;
    }
    if (sweepResultsHit === null || sweepResultsHit.kind !== "line") {
        return;
    }
    const curves = sweepResultsLastSpec.curves;
    const filled = curves.map((curve, index) => (curve.marks.length > 0 ? index : -1)).filter((i) => i >= 0);
    if (filled.length === 0) {
        return;
    }
    const step = { ArrowRight: [0, 1], ArrowLeft: [0, -1], ArrowDown: [1, 0], ArrowUp: [-1, 0] }[
        event.key
    ];
    if (event.key === "Enter" && sweepFocus !== null) {
        const mark = curves[sweepFocus.curve].marks[sweepFocus.position];
        const result = sweepResultsByIndex().get(mark.index);
        if (result) {
            openSweepPoint(result);
        }
        event.preventDefault();
        return;
    }
    if (step === undefined) {
        return;
    }
    event.preventDefault();
    if (sweepFocus === null) {
        sweepFocus = { curve: filled[0], position: 0 };
    } else {
        const place = filled.indexOf(sweepFocus.curve);
        const curve = filled[Math.min(filled.length - 1, Math.max(0, place + step[0]))];
        const length = curves[curve].marks.length;
        sweepFocus = {
            curve,
            position: Math.min(length - 1, Math.max(0, sweepFocus.position + step[1])),
        };
    }
    const mark = curves[sweepFocus.curve].marks[sweepFocus.position];
    const point = sweepResultsData.points.find((entry) => entry.index === mark.index);
    sweepResultsReadout.textContent = sweepPointReadout(
        { ...point, result: sweepResultsByIndex().get(point.index) },
        sweepResultsStatistic.value
    );
    redrawSweepLine(sweepResultsLastSpec);
}

sweepResultsCanvas.addEventListener("keydown", moveSweepFocus);

sweepResultsCanvas.addEventListener("click", async (event) => {
    const target = sweepResultsTarget(event);
    if (target === null) {
        return;
    }
    let result;
    if (target.trajectory) {
        const curve = sweepTrajectoryLastSpec.curves[target.trajectory.curve];
        result = sweepResultsByIndex().get(curve.points[target.trajectory.point].index);
    } else {
        result = target.mark
            ? sweepResultsByIndex().get(target.mark.index)
            : target.point && target.point.result;
    }
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
            statistic ? `${statisticDisplayName(statistic)} mean` : "mean",
            "low",
            "high",
            "replicates",
            "health",
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
        const health = sweepHealthClass(result);
        if (point.state === "failed" || health === "unreliable") {
            row.classList.add("row-warning");
        }
        const cells = [
            ...keys.map((key) => sweepFormatValue(point.coordinates[key])),
            stat ? sweepFormatValue(stat.mean) : "",
            stat && stat.low !== null ? sweepFormatValue(stat.low) : "",
            stat && stat.high !== null ? sweepFormatValue(stat.high) : "",
            result ? String(result.nReplicates) : "",
            health === "healthy" ? "" : `${health}: ${sweepHealthReason(result)}`,
            point.state === "failed"
                ? `failed (${point.reason})`
                : SWEEP_STATE_LABELS[states.get(point.index).state] ??
                  states.get(point.index).state,
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

/**
 * A file-name-safe version of a title: lowercase, other characters as hyphens.
 * @param {string} text
 * @returns {string}
 */
function sweepSlug(text) {
    return text
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "");
}

/**
 * Say what a save did in the readout: where it went, why it failed, or
 * nothing for a cancelled dialog (the researcher's own choice).
 * @param {{ok: boolean, path?: string, message?: string}} reply
 */
function reportSweepSave(reply) {
    if (reply.ok) {
        sweepResultsReadout.textContent = `Saved ${reply.path}`;
    } else if (reply.message) {
        sweepResultsReadout.textContent = `Could not save: ${reply.message}`;
    }
}

/**
 * Save the results table (CSV and its .meta.json) through a native Save
 * dialog. It writes every statistic the researcher has chosen to show, with
 * the closed-form columns for those that have one, so the file does not
 * depend on which statistic the chart is showing.
 * @returns {Promise<{ok: boolean, path?: string, message?: string}>}
 */
window.fim.saveSweepTable = async function saveSweepTable() {
    const statistics = Array.from(sweepResultsStatistic.options).map(
        (option) => option.value
    );
    const reply = await window.pywebview.api.export_sweep_table(sweepResultsStudyId, {
        statistics,
        withTheory: true,
    });
    reportSweepSave(reply);
    return reply;
};

/**
 * Save the chart as it is drawn now, as a PNG, through the same bridge call
 * every other graph uses (`Api.export_graph_image`).
 * @returns {Promise<{ok: boolean, path?: string, message?: string}>}
 */
window.fim.saveSweepChart = async function saveSweepChart() {
    const name = sweepSlug(sweepResultsData.name) || "sweep";
    const statistic = sweepSlug(sweepResultsStatistic.value);
    const reply = await window.pywebview.api.export_graph_image({
        dataUrl: sweepResultsCanvas.toDataURL("image/png"),
        suggestedFilename: `${name}${statistic ? `-${statistic}` : ""}.png`,
    });
    reportSweepSave(reply);
    return reply;
};

sweepResultsSaveTable.addEventListener("click", () => window.fim.saveSweepTable());
sweepResultsSaveChart.addEventListener("click", () => window.fim.saveSweepChart());

for (const control of [
    sweepResultsStatistic,
    sweepResultsMode,
    sweepResultsTheory,
    sweepResultsBy,
    sweepResultsInterval,
    sweepResultsXScale,
    sweepResultsYScale,
]) {
    control.addEventListener("change", renderSweepResults);
}
sweepResultsViewSelect.addEventListener("change", () => {
    // A trajectory view is one curve per value of an axis, so open on the
    // first axis rather than on a single point.
    if (sweepShowsTrajectories() && sweepResultsBy.value === "") {
        refreshSweepByControl();
        sweepResultsBy.value = sweepResultsData.axes[0].key;
    }
    refreshSweepHeldControls();
    renderSweepResults();
});
for (const control of [sweepResultsX, sweepResultsY]) {
    control.addEventListener("change", () => {
        refreshSweepHeldControls();
        renderSweepResults();
    });
}
sweepResultsSetupButton.addEventListener("click", () => window.fim.showConfigureScreen());
window.addEventListener("resize", () => {
    if (!sweepResultsView.hidden && sweepResultsData !== null) {
        renderSweepResults();
    }
});
