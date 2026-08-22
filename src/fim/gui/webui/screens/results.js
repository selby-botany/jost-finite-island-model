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
const newRunButton = document.getElementById("new-run-button");
const animateButton = document.getElementById("animate-button");
const openFolderButton = document.getElementById("open-folder-button");

let currentOutputDirectory = null;

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

// "Animate" is enabled/disabled correctly (`_animate_is_enabled`'s own
// rule, ported server-side into `generationCount <= 1`), but has no
// screen to open yet -- Milestone W6 (design §7.7) builds Screen 5 and
// wires this click to it, the same order the original Tk build's own
// milestone plan used (G6 after G3).
