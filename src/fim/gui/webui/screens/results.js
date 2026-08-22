"use strict";

/* Screen 3: results (design doc §4.3) -- the run summary (every named
 * statistic, convergence outcome) beside a `<canvas>` rendering of the
 * completed run's scatter, no longer an `<img>` (design §0.5).
 *
 * `fim.showResults` is called once, by `progress.js`'s own `onRunDone`
 * handler, with the exact payload `_drain_run_messages` built in
 * `app.py` -- every statistic already formatted server-side
 * (`format_statistic`, matching `cli._format_optional`), so this file
 * does no numeric formatting of its own (design §3.5's "the client
 * never does linear algebra" extended here to "the client never
 * reimplements Python's own display formatting either").
 */

const STATISTIC_NAMES = ["D", "G_ST", "E_ST", "K_ST", "H_S", "H_T"];

const resultsCanvas = document.getElementById("results-canvas");
const resultsRunId = document.getElementById("results-run-id");
const resultsOutcome = document.getElementById("results-outcome");
const resultsDifferentiationQ = document.getElementById("results-differentiation-q");
const newRunButton = document.getElementById("new-run-button");
const animateButton = document.getElementById("animate-button");
const openFolderButton = document.getElementById("open-folder-button");

let currentOutputDirectory = null;

function renderDifferentiationQ(report) {
    // Only `Api.open_run`'s own payload can carry this (design §4.6's
    // q-sweep field) -- a live run's own `"done"` push never does, so
    // this element stays hidden for the common case.
    const sweep = report.Differentiation_q;
    if (!sweep) {
        resultsDifferentiationQ.hidden = true;
        resultsDifferentiationQ.replaceChildren();
        return;
    }
    resultsDifferentiationQ.hidden = false;
    resultsDifferentiationQ.replaceChildren();
    for (const [order, value] of Object.entries(sweep)) {
        const line = document.createElement("p");
        line.className = "field-stat";
        // `value` is a raw float here (`fim.reanalyze.differentiation_q_
        // for_state`'s own return type, sent unformatted since only the
        // six named statistics go through `format_statistic` server-
        // side) -- `toPrecision` mirrors that same `%.6g`-style rounding
        // client-side for this one, not-yet-server-formatted field.
        line.textContent = `q=${order}: ${Number(value).toPrecision(6)}`;
        resultsDifferentiationQ.appendChild(line);
    }
}

window.fim.showResults = function showResults(payload) {
    currentOutputDirectory = payload.outputDirectory;
    resultsRunId.textContent = payload.runId;
    const report = payload.report;
    const reason = report.reason.charAt(0).toUpperCase() + report.reason.slice(1);
    resultsOutcome.textContent = `${reason}: generation ${report.generation}`;
    for (const name of STATISTIC_NAMES) {
        document.getElementById(`stat-${name}`).textContent =
            `${name} = ${payload.statistics[name]}`;
    }
    renderDifferentiationQ(report);
    const panels = payload.panels;
    if (panels && panels.length > 0) {
        // Milestone W4 draws only the first panel, the same deliberate
        // scope line `progress.js`'s `drawFirstPanel` already documents
        // for the `3 <= d <= 6` pairwise case -- every panel is still
        // present in `panels`, just not all rendered yet.
        const panel = panels[0];
        drawScatter(resultsCanvas, panel.points, {
            xLabel: panel.x_label,
            yLabel: panel.y_label,
        });
    }
    animateButton.disabled = payload.generationCount <= 1;
    window.fim.showScreen("screen-results");
};

newRunButton.addEventListener("click", () => {
    window.fim.showScreen("screen-input");
});

openFolderButton.addEventListener("click", () => {
    if (currentOutputDirectory !== null) {
        window.pywebview.api.open_output_folder(currentOutputDirectory);
    }
});

animateButton.addEventListener("click", () => {
    if (currentOutputDirectory !== null) {
        window.fim.showAnimation(currentOutputDirectory);
    }
});
