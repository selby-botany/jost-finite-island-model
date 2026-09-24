"use strict";

/* Sweep (`20260923-claude-sonnet-5-sweep-as-study-implementation-
 * plan.md`, `selby/restricted`, §6): run the Configure form over a range
 * of one or more parameters as a single Study.
 *
 * A sweep is part of Configure, not a separate place to run things. The
 * Sweep box beside Run turns it on; "Set up sweep…" opens `#modal-sweep`,
 * where the axes (what varies, over which values) and the seed policy are
 * chosen, with a live plan (`Api.plan_sweep`, so nothing runs) that counts
 * the points, flags invalid ones and lists them on request. The study is
 * Configure's own study choice. Run always means Run: with the box on it
 * starts the sweep (`runConfiguredSweep`), otherwise a single run.
 *
 * Progress follows a running sweep through `fim.onSweepEvent` pushes from
 * `Api.start_sweep`'s background thread, on the sweep screen.
 *
 * A sweep of `confirmationThreshold` points or more needs a second press
 * of Run: `window.confirm` hangs pywebview, so the first press says so and
 * only the second starts it.
 */

const sweepBanner = document.getElementById("sweep-banner");
const sweepDialog = document.getElementById("modal-sweep");
const sweepDialogBanner = document.getElementById("sweep-banner-dialog");
const sweepDoneButton = document.getElementById("sweep-done-button");
const sweepDialogCancelButton = document.getElementById("sweep-dialog-cancel-button");
const sweepCheckbox = document.getElementById("configure-sweep-checkbox");
const sweepConfigureButton = document.getElementById("configure-sweep-button");
const sweepConfigureSummary = document.getElementById("configure-sweep-summary");
const sweepBaseSummary = document.getElementById("sweep-base-summary");
const sweepAxesContainer = document.getElementById("sweep-axes");
const sweepAddAxisButton = document.getElementById("sweep-add-axis-button");
const sweepSeedPolicySelect = document.getElementById("sweep-seed-policy");
const sweepPointsAtOnceSelect = document.getElementById("sweep-points-at-once");
const sweepConcurrencyNote = document.getElementById("sweep-concurrency-note");
const sweepPlanSummary = document.getElementById("sweep-plan-summary");
const sweepPlanTable = document.getElementById("sweep-plan-table");
const sweepPlanHead = document.getElementById("sweep-plan-head");
const sweepPlanBody = document.getElementById("sweep-plan-body");
const sweepProgressView = document.getElementById("sweep-progress");
const sweepProgressText = document.getElementById("sweep-progress-text");
const sweepProgressBar = document.getElementById("sweep-progress-bar");
const sweepProgressHead = document.getElementById("sweep-progress-head");
const sweepProgressBody = document.getElementById("sweep-progress-body");
const sweepCancelButton = document.getElementById("sweep-cancel-button");
const sweepSetupButton = document.getElementById("sweep-setup-button");
const sweepHomeButton = document.getElementById("sweep-home-button");
const sweepTitle = document.getElementById("sweep-title");
const sweepViewResultsButton = document.getElementById("sweep-view-results-button");

// How many plan rows to draw; a larger plan says how many it omitted.
const SWEEP_PLAN_ROW_LIMIT = 200;
// Wait this long after the last edit before asking for a new plan.
const SWEEP_PLAN_DEBOUNCE_MS = 300;

// Sensible first range per key, so a new axis opens on something useful
// rather than on the full display domain (which spans decades).
const SWEEP_DEFAULT_RANGES = {
    m: { start: 0.0001, stop: 0.1, count: 4 },
    mu: { start: 0.000001, stop: 0.001, count: 4 },
    N: { start: 10, stop: 1000, count: 4 },
    d: { start: 2, stop: 16, count: 4 },
};

let sweepKeys = null;
let sweepBaseValues = {};
let sweepPlanTimer = null;
let sweepPlanSequence = 0;
let sweepConfirmArmed = false;
// The saved configuration: `{request, summary, pointCount}` once Done was
// pressed in the dialog, else `null`.
let sweepConfig = null;
let sweepLastPlan = null;
let sweepLastStudyId = null;

window.__fimSweepPlanReady = false;
window.__fimSweepFinished = false;

/**
 * Show (or, given a falsy `message`, hide) this screen's banner.
 * @param {string} [message]
 */
function showSweepBanner(message) {
    sweepBanner.hidden = !message;
    sweepBanner.textContent = message || "";
}

/**
 * Show (or hide) the setup dialog's own banner.
 * @param {string} [message]
 */
function showSweepDialogBanner(message) {
    sweepDialogBanner.hidden = !message;
    sweepDialogBanner.textContent = message || "";
}

/**
 * The sweepable-key metadata for `key`.
 * @param {string} key
 */
function sweepKeyInfo(key) {
    return sweepKeys.find((entry) => entry.key === key);
}

/**
 * One line naming what is held fixed, from the Configure form.
 * @param {Record<string, string>} values
 * @returns {string}
 */
function sweepBaseSummaryText(values) {
    const ploidyNames = { 1: "haploid", 2: "diploid", 3: "triploid", 4: "tetraploid" };
    const ploidy = ploidyNames[values.ploidy] || `ploidy ${values.ploidy || "?"}`;
    const migration = values.m_rate ? `m ${values.m_rate}` : "m as configured";
    const mutation = values.mu_value ? `μ ${values.mu_value}` : "μ as configured";
    return (
        `Held fixed, from Configure: N ${values.N} ${ploidy} individuals per ` +
        `deme, d ${values.d}, ${migration}, ${mutation}. ` +
        "The axes below replace those they name."
    );
}

/**
 * Build one axis row and append it to the axes container.
 * @param {string} [preferredKey] The key to open on; the first unused one if absent.
 * @param {object} [initial] `{mode, start, stop, count, scale, list, choices}` overrides.
 */
function addSweepAxisRow(preferredKey, initial = {}) {
    const used = new Set(sweepAxisKeys());
    const key =
        preferredKey && !used.has(preferredKey)
            ? preferredKey
            : sweepKeys.map((entry) => entry.key).find((name) => !used.has(name));
    if (key === undefined) {
        return;
    }
    const row = document.createElement("div");
    row.className = "sweep-axis";

    const keySelect = document.createElement("select");
    keySelect.className = "sweep-axis-key";
    keySelect.setAttribute("aria-label", "Parameter to vary");
    for (const entry of sweepKeys) {
        const option = document.createElement("option");
        option.value = entry.key;
        option.textContent = entry.label;
        keySelect.appendChild(option);
    }
    keySelect.value = key;

    const numeric = document.createElement("span");
    numeric.className = "sweep-axis-numeric";
    const modeSelect = document.createElement("select");
    modeSelect.className = "sweep-axis-mode";
    modeSelect.setAttribute("aria-label", "Range or list of values");
    for (const [value, label] of [
        ["range", "range"],
        ["list", "list"],
    ]) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        modeSelect.appendChild(option);
    }
    const rangeGroup = document.createElement("span");
    rangeGroup.className = "sweep-axis-range";
    const startInput = sweepNumberInput("sweep-axis-start", "from");
    const stopInput = sweepNumberInput("sweep-axis-stop", "to");
    const countInput = sweepNumberInput("sweep-axis-count", "points");
    countInput.min = "1";
    const scaleSelect = document.createElement("select");
    scaleSelect.className = "sweep-axis-scale";
    scaleSelect.setAttribute("aria-label", "Spacing");
    for (const [value, label] of [
        ["linear", "linear"],
        ["log", "log"],
    ]) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        scaleSelect.appendChild(option);
    }
    rangeGroup.append(
        sweepLabeled("from", startInput),
        sweepLabeled("to", stopInput),
        sweepLabeled("points", countInput),
        scaleSelect
    );
    const listInput = document.createElement("input");
    listInput.type = "text";
    listInput.className = "sweep-axis-list";
    listInput.placeholder = "for example 4, 8, 16";
    listInput.setAttribute("aria-label", "Values, separated by commas");
    listInput.hidden = true;
    numeric.append(modeSelect, rangeGroup, listInput);

    const choices = document.createElement("span");
    choices.className = "sweep-axis-choices";

    const values = document.createElement("span");
    values.className = "sweep-axis-values";

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "sweep-axis-remove";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => {
        row.remove();
        onSweepAxesChanged();
    });

    row.append(keySelect, numeric, choices, values, remove);
    sweepAxesContainer.appendChild(row);

    keySelect.addEventListener("change", () => {
        configureSweepAxisRow(row, keySelect.value, {});
        onSweepAxesChanged();
    });
    modeSelect.addEventListener("change", () => {
        rangeGroup.hidden = modeSelect.value !== "range";
        listInput.hidden = modeSelect.value !== "list";
        onSweepAxesChanged();
    });
    for (const control of [startInput, stopInput, countInput, scaleSelect, listInput]) {
        control.addEventListener("input", onSweepAxesChanged);
        control.addEventListener("change", onSweepAxesChanged);
    }
    configureSweepAxisRow(row, key, initial);
    onSweepAxesChanged();
}

/**
 * A small numeric text input for an axis range.
 * @param {string} className
 * @param {string} label Accessible name.
 * @returns {HTMLInputElement}
 */
function sweepNumberInput(className, label) {
    const input = document.createElement("input");
    input.type = "text";
    input.inputMode = "decimal";
    input.className = className;
    input.setAttribute("aria-label", label);
    return input;
}

/**
 * Wrap an input in a visible label.
 * @param {string} text
 * @param {HTMLElement} input
 * @returns {HTMLLabelElement}
 */
function sweepLabeled(text, input) {
    const label = document.createElement("label");
    label.className = "sweep-axis-field";
    label.append(`${text} `, input);
    return label;
}

/**
 * Reset an axis row's controls to suit `key` (numeric range or choices).
 * @param {HTMLElement} row
 * @param {string} key
 * @param {object} initial
 */
function configureSweepAxisRow(row, key, initial) {
    const info = sweepKeyInfo(key);
    const numeric = row.querySelector(".sweep-axis-numeric");
    const choices = row.querySelector(".sweep-axis-choices");
    const isChoice = info.kind === "choice";
    numeric.hidden = isChoice;
    choices.hidden = !isChoice;
    choices.replaceChildren();
    row.querySelector(".sweep-axis-key").value = key;
    if (isChoice) {
        const chosen = new Set(initial.choices || info.choices);
        for (const choice of info.choices) {
            const label = document.createElement("label");
            label.className = "sweep-axis-choice";
            const box = document.createElement("input");
            box.type = "checkbox";
            box.value = choice;
            box.checked = chosen.has(choice);
            box.addEventListener("change", onSweepAxesChanged);
            label.append(box, ` ${choice}`);
            choices.appendChild(label);
        }
        return;
    }
    const fallback = info.displayDomain
        ? { start: info.displayDomain[0], stop: info.displayDomain[1], count: 4 }
        : { start: 1, stop: 10, count: 4 };
    const range = { ...(SWEEP_DEFAULT_RANGES[key] || fallback), ...initial };
    row.querySelector(".sweep-axis-mode").value = initial.mode || "range";
    row.querySelector(".sweep-axis-range").hidden = (initial.mode || "range") !== "range";
    const listInput = row.querySelector(".sweep-axis-list");
    listInput.hidden = (initial.mode || "range") !== "list";
    listInput.value = initial.list || "";
    row.querySelector(".sweep-axis-start").value = String(range.start);
    row.querySelector(".sweep-axis-stop").value = String(range.stop);
    row.querySelector(".sweep-axis-count").value = String(range.count);
    row.querySelector(".sweep-axis-scale").value = initial.scale || info.scale;
}

/**
 * The keys currently chosen across the axis rows, in order.
 * @returns {string[]}
 */
function sweepAxisKeys() {
    return Array.from(sweepAxesContainer.querySelectorAll(".sweep-axis-key")).map(
        (select) => select.value
    );
}

/**
 * Read one axis row into the bridge request's axis entry.
 * @param {HTMLElement} row
 * @returns {{key: string, values?: Array, range?: object}}
 * @throws {Error} With a message naming the field, if it is not a number.
 */
function readSweepAxisRow(row) {
    const key = row.querySelector(".sweep-axis-key").value;
    const info = sweepKeyInfo(key);
    if (info.kind === "choice") {
        const values = Array.from(row.querySelectorAll(".sweep-axis-choices input"))
            .filter((box) => box.checked)
            .map((box) => box.value);
        return { key, values };
    }
    const toNumber = (text, what) => {
        const number = Number(String(text).trim());
        if (String(text).trim() === "" || !Number.isFinite(number)) {
            throw new Error(`${key}: ${what} must be a number`);
        }
        return number;
    };
    if (row.querySelector(".sweep-axis-mode").value === "list") {
        const items = row
            .querySelector(".sweep-axis-list")
            .value.split(",")
            .map((item) => item.trim())
            .filter((item) => item !== "");
        return { key, values: items.map((item) => toNumber(item, "each value")) };
    }
    return {
        key,
        range: {
            start: toNumber(row.querySelector(".sweep-axis-start").value, "from"),
            stop: toNumber(row.querySelector(".sweep-axis-stop").value, "to"),
            count: toNumber(row.querySelector(".sweep-axis-count").value, "points"),
            scale: row.querySelector(".sweep-axis-scale").value,
        },
    };
}

/**
 * Read every axis row and the seed policy into a bridge request.
 * @returns {{axes: object[], seedPolicy: string}}
 */
function buildSweepRequest() {
    const axes = Array.from(sweepAxesContainer.querySelectorAll(".sweep-axis")).map(
        readSweepAxisRow
    );
    return {
        axes,
        seedPolicy: sweepSeedPolicySelect.value,
        pointsAtOnce: sweepPointsAtOnceSelect.value,
    };
}

/** Schedule a fresh plan after the user stops editing. */
function onSweepAxesChanged() {
    sweepDoneButton.disabled = true;
    window.__fimSweepPlanReady = false;
    sweepAddAxisButton.disabled =
        sweepAxisKeys().length >= (sweepKeys ? sweepKeys.length : 0);
    window.clearTimeout(sweepPlanTimer);
    sweepPlanTimer = window.setTimeout(refreshSweepPlan, SWEEP_PLAN_DEBOUNCE_MS);
}

/**
 * Ask the bridge for the plan and draw it.
 */
async function refreshSweepPlan() {
    const sequence = (sweepPlanSequence += 1);
    let request;
    try {
        request = buildSweepRequest();
    } catch (error) {
        drawSweepPlanProblem(error.message);
        return;
    }
    const plan = await window.pywebview.api.plan_sweep(sweepBaseValues, request);
    if (sequence !== sweepPlanSequence) {
        return;
    }
    if (!plan.ok) {
        drawSweepPlanProblem(plan.message);
        return;
    }
    sweepLastPlan = plan;
    drawSweepPlan(plan);
    window.__fimSweepPlanReady = true;
}

/**
 * Show a problem with the current axes instead of a plan.
 * @param {string} message
 */
function drawSweepPlanProblem(message) {
    sweepLastPlan = null;
    sweepPlanSummary.textContent = message;
    sweepPlanSummary.classList.add("sweep-plan-problem");
    sweepPlanTable.hidden = true;
    sweepDoneButton.disabled = true;
    for (const span of sweepAxesContainer.querySelectorAll(".sweep-axis-values")) {
        span.textContent = "";
    }
    window.__fimSweepPlanReady = true;
}

/**
 * Draw a valid plan: the values each axis produced, the counts, and a
 * table of the points.
 * @param {object} plan `Api.plan_sweep`'s payload.
 */
function drawSweepPlan(plan) {
    sweepPlanSummary.classList.remove("sweep-plan-problem");
    const rows = Array.from(sweepAxesContainer.querySelectorAll(".sweep-axis"));
    plan.axes.forEach((axis, index) => {
        rows[index].querySelector(".sweep-axis-values").textContent = axis.values.join(", ");
    });
    const pieces = [
        `${plan.points.length} point${plan.points.length === 1 ? "" : "s"}`,
        `${plan.newCount} new`,
    ];
    if (plan.reusedCount > 0) {
        pieces.push(`${plan.reusedCount} already computed`);
    }
    if (plan.invalid.length > 0) {
        pieces.push(`${plan.invalid.length} invalid`);
    }
    if (plan.collapsed > 0) {
        pieces.push(`${plan.collapsed} duplicate`);
    }
    const estimate = plan.estimate;
    sweepPlanSummary.textContent =
        `${pieces.join(", ")}. ${estimate.replicates} replicate` +
        `${estimate.replicates === 1 ? "" : "s"} each, up to ` +
        `${estimate.max_generations} generations.`;

    const keys = plan.axes.map((axis) => axis.key);
    sweepPlanHead.replaceChildren(sweepHeaderRow(["#", ...keys, "Result"]));
    sweepPlanBody.replaceChildren();
    const entries = [
        ...plan.points.map((point) => ({
            index: point.index,
            coordinates: point.coordinates,
            result: point.exists ? "already computed" : "new",
            invalid: false,
        })),
        ...plan.invalid.map((point) => ({
            index: point.index,
            coordinates: point.coordinates,
            result: `invalid: ${point.reason}`,
            invalid: true,
        })),
    ].sort((a, b) => a.index - b.index);
    for (const entry of entries.slice(0, SWEEP_PLAN_ROW_LIMIT)) {
        const row = document.createElement("tr");
        if (entry.invalid) {
            row.classList.add("row-warning");
        }
        for (const text of [
            String(entry.index),
            ...keys.map((key) => String(entry.coordinates[key])),
            entry.result,
        ]) {
            const cell = document.createElement("td");
            cell.textContent = text;
            row.appendChild(cell);
        }
        sweepPlanBody.appendChild(row);
    }
    if (entries.length > SWEEP_PLAN_ROW_LIMIT) {
        sweepPlanSummary.textContent += ` Showing the first ${SWEEP_PLAN_ROW_LIMIT} of ${entries.length} grid positions.`;
    }
    sweepPlanTable.hidden = false;
    sweepDoneButton.disabled = plan.points.length === 0;
    drawSweepConcurrencyNote(plan.concurrency);
}

/**
 * Say how the sweep will use the machine: how many points at once and how
 * many worker processes each batch point gets.
 * @param {{pointsAtOnce: number, workersPerPoint: number|null, cores: number,
 *     automatic: boolean}|undefined} concurrency
 */
function drawSweepConcurrencyNote(concurrency) {
    if (!concurrency) {
        sweepConcurrencyNote.textContent = "";
        return;
    }
    const points = concurrency.pointsAtOnce;
    const each =
        concurrency.workersPerPoint === null
            ? ""
            : `, each with ${concurrency.workersPerPoint} worker${
                  concurrency.workersPerPoint === 1 ? "" : "s"
              }`;
    const lead = concurrency.automatic ? "Automatic: " : "";
    sweepConcurrencyNote.textContent =
        `${lead}${points} point${points === 1 ? "" : "s"} at once${each} ` +
        `on ${concurrency.cores} cores.`;
}

/**
 * A `<tr>` of `<th>` cells.
 * @param {string[]} labels
 * @returns {HTMLTableRowElement}
 */
function sweepHeaderRow(labels) {
    const row = document.createElement("tr");
    for (const label of labels) {
        const cell = document.createElement("th");
        cell.textContent = label;
        row.appendChild(cell);
    }
    return row;
}

/**
 * Switch to the progress view for a running (or resumed) sweep.
 * @param {string} studyId
 */
async function enterSweepProgress(studyId) {
    sweepLastStudyId = studyId;
    window.__fimSweepFinished = false;
    sweepTitle.textContent = "Sweep running";
    sweepProgressView.hidden = false;
    sweepCancelButton.hidden = false;
    sweepCancelButton.disabled = false;
    sweepSetupButton.hidden = true;
    sweepHomeButton.hidden = true;
    sweepViewResultsButton.hidden = true;
    document.getElementById("sweep-results").hidden = true;
    const status = await window.pywebview.api.get_sweep_status(studyId);
    if (!status.ok) {
        showSweepBanner(status.message);
        return;
    }
    const keys = status.axes.map((axis) => axis.key);
    sweepProgressHead.replaceChildren(sweepHeaderRow(["#", ...keys, "State"]));
    sweepProgressBody.replaceChildren();
    for (const point of status.points) {
        const row = document.createElement("tr");
        row.dataset.index = String(point.index);
        for (const text of [
            String(point.index),
            ...keys.map((key) => String(point.coordinates[key])),
        ]) {
            const cell = document.createElement("td");
            cell.textContent = text;
            row.appendChild(cell);
        }
        const state = document.createElement("td");
        state.className = "sweep-point-state";
        row.appendChild(state);
        sweepProgressBody.appendChild(row);
        setSweepPointState(point.index, point.state === "done" ? "done" : "waiting");
    }
    const done = status.points.filter((point) => point.state === "done").length;
    sweepProgressBar.max = Math.max(status.points.length, 1);
    sweepProgressBar.value = done;
    sweepProgressText.textContent = `${done} of ${status.points.length} points done`;
}

window.fim.showSweepProgress = enterSweepProgress;

/**
 * Write one point's state into the progress table.
 * @param {number} index
 * @param {string} text
 */
function setSweepPointState(index, text) {
    const row = sweepProgressBody.querySelector(`tr[data-index="${index}"]`);
    if (row === null) {
        return;
    }
    row.querySelector(".sweep-point-state").textContent = text;
    const failed = text.startsWith("failed") || text.includes("DIFFERS");
    row.classList.toggle("row-warning", failed);
}

const SWEEP_EVENT_STATE_TEXT = {
    point_started: "running",
    point_reused: "reused an existing run",
    point_done: "done",
    point_recomputed: "recomputed; matches the earlier version's result",
    point_differs: "recomputed; DIFFERS from the earlier version's result",
};

/**
 * A short list of what changed in a reproducibility comparison.
 * @param {{differences: Array<{label: string, old: *, new: *}>}} comparison
 * @returns {string}
 */
function sweepDifferenceText(comparison) {
    return comparison.differences
        .map((item) => `${item.label}: ${item.old} → ${item.new}`)
        .slice(0, 6)
        .join("; ");
}

/**
 * Handle one pushed sweep event (`Api._push_sweep_event`).
 * @param {{kind: string, index: number, position: number, total: number, detail: string|null}} event
 */
window.fim.onSweepEvent = function onSweepEvent(event) {
    if (event.kind in SWEEP_EVENT_STATE_TEXT) {
        setSweepPointState(event.index, SWEEP_EVENT_STATE_TEXT[event.kind]);
    } else if (event.kind === "point_failed") {
        setSweepPointState(event.index, `failed (${event.detail})`);
    }
    if (event.kind === "point_differs") {
        showSweepBanner(
            "Reproducibility warning: a point recomputed under this software " +
                "version gives different values from the earlier version's " +
                `(${sweepDifferenceText(event.detail)}). Both runs are kept.`
        );
    }
    if (event.kind === "point_started") {
        sweepProgressText.textContent = `Running… ${event.position} of ${event.total} points finished`;
    }
    if (
        [
            "point_done",
            "point_reused",
            "point_failed",
            "point_recomputed",
            "point_differs",
        ].includes(event.kind)
    ) {
        sweepProgressBar.value = event.position;
        sweepProgressText.textContent = `${event.position} of ${event.total} points finished`;
    }
    if (event.kind === "sweep_done" || event.kind === "sweep_cancelled") {
        finishSweep(event.kind === "sweep_cancelled");
    }
};

/**
 * Handle a sweep that could not start or continue.
 * @param {string} message
 */
window.fim.onSweepError = function onSweepError(message) {
    showSweepBanner(message);
    finishSweep(false);
};

/**
 * Leave the progress view in its finished state.
 * @param {boolean} cancelled
 */
function finishSweep(cancelled) {
    sweepTitle.textContent = cancelled ? "Sweep cancelled" : "Sweep finished";
    sweepProgressText.textContent = cancelled
        ? "Cancelled. Finished points are kept; resume the study from Home."
        : "Every point has been run. The study is in Home.";
    sweepCancelButton.hidden = true;
    sweepSetupButton.hidden = false;
    sweepHomeButton.hidden = false;
    sweepViewResultsButton.hidden = sweepLastStudyId === null;
    window.__fimSweepFinished = true;
}

/**
 * The initial state of an axis row for a saved request entry.
 * @param {{key: string, values?: Array, range?: object}} entry
 * @returns {{key: string, initial: object}}
 */
function sweepAxisInitial(entry) {
    if (entry.range) {
        return {
            key: entry.key,
            initial: {
                mode: "range",
                start: entry.range.start,
                stop: entry.range.stop,
                count: entry.range.count,
                scale: entry.range.scale,
            },
        };
    }
    const info = sweepKeyInfo(entry.key);
    if (info && info.kind === "choice") {
        return { key: entry.key, initial: { choices: entry.values } };
    }
    return { key: entry.key, initial: { mode: "list", list: entry.values.join(", ") } };
}

/**
 * Open the setup dialog, on the saved configuration or, failing that, on
 * `options.axes` or a first axis.
 * @param {{axes?: Array<{key: string, initial?: object}>}} [options]
 */
window.fim.openSweepDialog = async function openSweepDialog(options = {}) {
    showSweepDialogBanner("");
    window.__fimSweepPlanReady = false;
    if (sweepKeys === null) {
        sweepKeys = await window.pywebview.api.get_sweepable_keys();
    }
    sweepBaseValues = collectFormValues();
    sweepBaseSummary.textContent = sweepBaseSummaryText(sweepBaseValues);
    sweepAxesContainer.replaceChildren();
    let axes = options.axes;
    if (!axes || axes.length === 0) {
        axes =
            sweepConfig === null
                ? [{ key: "m" }]
                : sweepConfig.request.axes.map(sweepAxisInitial);
    }
    if (sweepConfig !== null) {
        sweepSeedPolicySelect.value = sweepConfig.request.seedPolicy;
        sweepPointsAtOnceSelect.value = sweepConfig.request.pointsAtOnce ?? "auto";
    }
    for (const axis of axes) {
        addSweepAxisRow(axis.key, axis.initial || {});
    }
    if (!sweepDialog.open) {
        sweepDialog.showModal();
    }
};

/** Reflect the saved configuration on Configure's Sweep box and summary. */
function syncSweepControls() {
    const enabled = sweepCheckbox.checked;
    sweepConfigureButton.hidden = !enabled;
    sweepConfigureSummary.hidden = !enabled || sweepConfig === null;
    sweepConfigureSummary.textContent = sweepConfig === null ? "" : sweepConfig.summary;
}

/**
 * Summarize a plan for Configure: what varies and how many points.
 * @param {{axes: Array<{key: string, values: Array}>, points: Array}} plan
 * @returns {string}
 */
function sweepSummaryText(plan) {
    const axes = plan.axes.map((axis) => `${axis.key} (${axis.values.length})`).join(", ");
    const count = plan.points.length;
    return `Sweep: ${axes}, ${count} point${count === 1 ? "" : "s"}.`;
}

/**
 * Save a request as the sweep configuration and turn the box on.
 * @param {{axes: object[], seedPolicy: string}} request
 * @param {object} plan Its plan, for the summary.
 */
function saveSweepConfiguration(request, plan) {
    sweepConfig = { request, summary: sweepSummaryText(plan), pointCount: plan.points.length };
    sweepConfirmArmed = false;
    sweepCheckbox.checked = true;
    syncSweepControls();
}

/**
 * Set the sweep from outside (Explore's "Sweep this for real"): plan the
 * request against Configure's current values, save it, and turn it on.
 * @param {{axes: object[], seedPolicy?: string}} request
 * @returns {Promise<{ok: boolean, message?: string}>}
 */
window.fim.setSweepConfiguration = async function setSweepConfiguration(request) {
    const full = { seedPolicy: "spaced", ...request };
    const plan = await window.pywebview.api.plan_sweep(collectFormValues(), full);
    if (!plan.ok) {
        return plan;
    }
    saveSweepConfiguration(full, plan);
    return { ok: true };
};

/**
 * Whether Configure's Sweep box is on and holds a configuration.
 * @returns {boolean}
 */
window.fim.isSweepEnabled = function isSweepEnabled() {
    return sweepCheckbox.checked && sweepConfig !== null;
};

/**
 * What Run does with the Sweep box on: run Configure's values over the saved
 * axes, into Configure's chosen study. A large plan needs a second press.
 * @param {string|null} studyId Configure's own study choice.
 * @returns {Promise<void>}
 */
window.fim.runConfiguredSweep = async function runConfiguredSweep(studyId) {
    showRunBanner("");
    const result = await window.pywebview.api.start_sweep(
        collectFormValues(),
        sweepConfig.request,
        studyId,
        sweepConfirmArmed
    );
    if (result.needsConfirmation && !sweepConfirmArmed) {
        sweepConfirmArmed = true;
        showRunBanner(
            `This sweep has ${result.total} points. Press Run again to confirm.`
        );
        return;
    }
    if (!result.ok) {
        showRunBanner(result.message);
        return;
    }
    sweepConfirmArmed = false;
    window.fim.showScreen("screen-sweep");
    document.getElementById("sweep-results").hidden = true;
    sweepProgressView.hidden = false;
    await enterSweepProgress(result.studyId);
};

sweepCheckbox.addEventListener("change", async () => {
    syncSweepControls();
    if (sweepCheckbox.checked && sweepConfig === null) {
        await window.fim.openSweepDialog();
    }
});
sweepConfigureButton.addEventListener("click", () => window.fim.openSweepDialog());
sweepAddAxisButton.addEventListener("click", () => addSweepAxisRow());
sweepSeedPolicySelect.addEventListener("change", onSweepAxesChanged);
sweepPointsAtOnceSelect.addEventListener("change", onSweepAxesChanged);
sweepDoneButton.addEventListener("click", () => {
    if (sweepLastPlan === null) {
        return;
    }
    saveSweepConfiguration(buildSweepRequest(), sweepLastPlan);
    sweepDialog.close();
});
sweepDialogCancelButton.addEventListener("click", () => sweepDialog.close());
sweepDialog.addEventListener("close", () => {
    // Cancelled with nothing saved: the box is not on with nothing to run.
    if (sweepConfig === null) {
        sweepCheckbox.checked = false;
        syncSweepControls();
    }
});
sweepCancelButton.addEventListener("click", async () => {
    sweepCancelButton.disabled = true;
    await window.pywebview.api.cancel_sweep();
});
sweepSetupButton.addEventListener("click", () => window.fim.showConfigureScreen());
sweepHomeButton.addEventListener("click", () => window.fim.menu.openRun());
sweepViewResultsButton.addEventListener("click", () => {
    if (sweepLastStudyId !== null) {
        window.fim.showSweepResults(sweepLastStudyId);
    }
});
