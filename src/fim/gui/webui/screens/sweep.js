"use strict";

/* Sweep screen (`20260923-claude-sonnet-5-sweep-as-study-implementation-
 * plan.md`, `selby/restricted`, §6): run the Configure form over a range
 * of one or more parameters as a single Study.
 *
 * Two views on one screen. The setup view is a set of axis rows and a
 * live plan (`Api.plan_sweep`, debounced, so no run happens): every
 * point, whether it is new, already computed, or invalid and why. The
 * progress view follows a running sweep through `fim.onSweepEvent`
 * pushes from `Api.start_sweep`'s own background thread.
 *
 * A sweep of `confirmationThreshold` points or more needs a second click:
 * `window.confirm` hangs pywebview, so the Run button re-labels itself
 * ("Confirm: run 250 points") and only the second click starts it.
 */

const sweepBanner = document.getElementById("sweep-banner");
const sweepBaseSummary = document.getElementById("sweep-base-summary");
const sweepAxesContainer = document.getElementById("sweep-axes");
const sweepAddAxisButton = document.getElementById("sweep-add-axis-button");
const sweepNameInput = document.getElementById("sweep-name");
const sweepExperimentSelect = document.getElementById("sweep-experiment");
const sweepSeedPolicySelect = document.getElementById("sweep-seed-policy");
const sweepPlanSummary = document.getElementById("sweep-plan-summary");
const sweepPlanTable = document.getElementById("sweep-plan-table");
const sweepPlanHead = document.getElementById("sweep-plan-head");
const sweepPlanBody = document.getElementById("sweep-plan-body");
const sweepRunButton = document.getElementById("sweep-run-button");
const sweepSetupView = document.getElementById("sweep-setup");
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
let sweepLastPlan = null;
let sweepRunningStudyId = null;
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
    return { axes, seedPolicy: sweepSeedPolicySelect.value };
}

/** Schedule a fresh plan after the user stops editing. */
function onSweepAxesChanged() {
    sweepConfirmArmed = false;
    sweepRunButton.textContent = "Run sweep";
    window.__fimSweepPlanReady = false;
    sweepAddAxisButton.disabled =
        sweepAxisKeys().length >= (sweepKeys ? sweepKeys.length : 0);
    window.clearTimeout(sweepPlanTimer);
    sweepPlanTimer = window.setTimeout(refreshSweepPlan, SWEEP_PLAN_DEBOUNCE_MS);
}

/**
 * Suggest a Study name from the chosen axes, unless the user typed one.
 */
function suggestSweepName() {
    if (sweepNameInput.dataset.edited === "true") {
        return;
    }
    const keys = sweepAxisKeys();
    sweepNameInput.value = keys.length > 0 ? `Sweep of ${keys.join(" and ")}` : "Sweep";
}

/**
 * Ask the bridge for the plan and draw it.
 */
async function refreshSweepPlan() {
    suggestSweepName();
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
    sweepRunButton.disabled = true;
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
    sweepRunButton.disabled = plan.points.length === 0;
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
 * Click on "Run sweep": confirm a large plan on a second click, then start.
 */
async function onSweepRunClicked() {
    showSweepBanner("");
    if (sweepLastPlan === null || sweepLastPlan.points.length === 0) {
        return;
    }
    if (sweepLastPlan.needsConfirmation && !sweepConfirmArmed) {
        sweepConfirmArmed = true;
        sweepRunButton.textContent = `Confirm: run ${sweepLastPlan.points.length} points`;
        return;
    }
    sweepRunButton.disabled = true;
    const experiment = sweepExperimentSelect.value || null;
    const result = await window.pywebview.api.start_sweep(
        sweepBaseValues,
        buildSweepRequest(),
        sweepNameInput.value.trim() || "Sweep",
        experiment,
        sweepConfirmArmed
    );
    if (!result.ok) {
        showSweepBanner(result.message);
        sweepRunButton.disabled = false;
        return;
    }
    await enterSweepProgress(result.studyId);
}

/**
 * Switch to the progress view for a running (or resumed) sweep.
 * @param {string} studyId
 */
async function enterSweepProgress(studyId) {
    sweepRunningStudyId = studyId;
    sweepLastStudyId = studyId;
    window.__fimSweepFinished = false;
    sweepTitle.textContent = "Sweep running";
    sweepSetupView.hidden = true;
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
    const failed = text.startsWith("failed");
    row.classList.toggle("row-warning", failed);
}

const SWEEP_EVENT_STATE_TEXT = {
    point_started: "running",
    point_reused: "reused an existing run",
    point_done: "done",
};

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
    if (event.kind === "point_started") {
        sweepProgressText.textContent = `Running point ${event.position} of ${event.total}…`;
    }
    if (["point_done", "point_reused", "point_failed"].includes(event.kind)) {
        sweepProgressBar.value = event.position;
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
    sweepRunningStudyId = null;
    window.__fimSweepFinished = true;
}

/**
 * Show the setup view, seeded from Configure's current form.
 * @param {{axes?: Array<{key: string, initial?: object}>}} [options]
 */
window.fim.showSweepScreen = async function showSweepScreen(options = {}) {
    showSweepBanner("");
    window.__fimSweepPlanReady = false;
    if (sweepKeys === null) {
        sweepKeys = await window.pywebview.api.get_sweepable_keys();
    }
    sweepBaseValues = collectFormValues();
    sweepBaseSummary.textContent = sweepBaseSummaryText(sweepBaseValues);
    const experiments = await window.pywebview.api.list_experiments();
    sweepExperimentSelect.replaceChildren();
    const none = document.createElement("option");
    none.value = "";
    none.textContent = "No experiment";
    sweepExperimentSelect.appendChild(none);
    for (const experiment of experiments) {
        const option = document.createElement("option");
        option.value = experiment.experimentId;
        option.textContent = experiment.name;
        sweepExperimentSelect.appendChild(option);
    }
    if (sweepRunningStudyId === null) {
        sweepTitle.textContent = "Run as a sweep";
        sweepSetupView.hidden = false;
        sweepProgressView.hidden = true;
        document.getElementById("sweep-results").hidden = true;
        sweepAxesContainer.replaceChildren();
        sweepNameInput.dataset.edited = "false";
        const axes = options.axes && options.axes.length > 0 ? options.axes : [{ key: "m" }];
        for (const axis of axes) {
            addSweepAxisRow(axis.key, axis.initial || {});
        }
    }
    window.fim.showScreen("screen-sweep");
};

sweepAddAxisButton.addEventListener("click", () => addSweepAxisRow());
sweepNameInput.addEventListener("input", () => {
    sweepNameInput.dataset.edited = "true";
});
sweepSeedPolicySelect.addEventListener("change", onSweepAxesChanged);
sweepRunButton.addEventListener("click", onSweepRunClicked);
sweepCancelButton.addEventListener("click", async () => {
    sweepCancelButton.disabled = true;
    await window.pywebview.api.cancel_sweep();
});
sweepSetupButton.addEventListener("click", () => window.fim.showSweepScreen());
sweepHomeButton.addEventListener("click", () => window.fim.menu.openRun());
sweepViewResultsButton.addEventListener("click", () => {
    if (sweepLastStudyId !== null) {
        window.fim.showSweepResults(sweepLastStudyId);
    }
});
