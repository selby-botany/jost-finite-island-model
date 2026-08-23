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
 * reimplements Python's own display formatting either"). Each statistic
 * renders through `meters.js`'s shared `buildPointMeter` (visualization-
 * and-config-editors design §3.3) -- the same meter widget Screen 4's
 * own confidence-interval bars use, in a "point only" mode with no
 * shaded interval, since a single run has none to show.
 */

const STATISTIC_NAMES = ["D", "G_ST", "E_ST", "K_ST", "H_S", "H_T"];

const resultsCanvas = document.getElementById("results-canvas");
const resultsRunId = document.getElementById("results-run-id");
const resultsOutcome = document.getElementById("results-outcome");
const resultsDifferentiationQ = document.getElementById("results-differentiation-q");
const resultsGenerationCount = document.getElementById("results-generation-count");
const newRunButton = document.getElementById("new-run-button");
const animateButton = document.getElementById("animate-button");
const openFolderButton = document.getElementById("open-folder-button");
const resultsDemePairSelector = document.getElementById("results-deme-pair-selector");
const resultsXDeme = document.getElementById("results-x-deme");
const resultsYDeme = document.getElementById("results-y-deme");
const resultsShowPairButton = document.getElementById("results-show-pair-button");
const resultsShowOverviewButton = document.getElementById(
    "results-show-overview-button"
);

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
        const value = payload.statistics[name];
        const element = document.getElementById(`stat-${name}`);
        element.replaceChildren(buildPointMeter(name, value));
    }
    renderDifferentiationQ(report);
    const panels = payload.panels;
    if (panels && panels.length > 0) {
        // Every panel `scatter_panels` computed is drawn -- one panel
        // fills the canvas (`d <= 2` or the `d > 6` PCA fallback), more
        // than one draws as a small-multiples grid (the `3 <= d <= 6`
        // pairwise case: visualization-and-config-editors design §3.1;
        // superseded Milestone W4's own "draws only the first panel"
        // scope line).
        const drawOverview = () => {
            if (panels.length === 1) {
                drawScatter(resultsCanvas, panels[0]);
            } else {
                drawScatterGrid(resultsCanvas, panels);
            }
        };
        drawOverview();
        window.fim.wireDemePairSelector({
            xSelect: resultsXDeme,
            ySelect: resultsYDeme,
            showPairButton: resultsShowPairButton,
            showOverviewButton: resultsShowOverviewButton,
            container: resultsDemePairSelector,
            demeCount: payload.demeCount,
            onShowPair: async (x, y) => {
                if (currentOutputDirectory === null) {
                    return;
                }
                const result = await window.pywebview.api.get_deme_pair_panel(
                    currentOutputDirectory,
                    x,
                    y
                );
                if (result.ok) {
                    drawScatter(resultsCanvas, result.panel);
                }
            },
            onShowOverview: drawOverview,
        });
    }
    animateButton.disabled = payload.generationCount <= 1;
    // Visualization-and-config-editors design §3.4: enough to answer "is
    // there a trajectory here worth navigating" without a screen switch
    // -- the real scrubber stays on Screen 5, reached via "Animate".
    if (payload.generationCount > 1) {
        resultsGenerationCount.hidden = false;
        resultsGenerationCount.textContent =
            `Trajectory: ${payload.generationCount} generations recorded`;
    } else {
        resultsGenerationCount.hidden = true;
    }
    window.fim.showScreen("screen-results");
};

newRunButton.addEventListener("click", () => {
    window.fim.showScreen("screen-input");
});

openFolderButton.addEventListener("click", async () => {
    if (currentOutputDirectory === null) {
        return;
    }
    // `window.__fimResultsOpenFolderSettled` -- the same settle-flag
    // fix, and the same reason, as `progress.js`'s own `cancelButton`
    // handler: a fire-and-forget bridge call left in flight when a
    // test's window is destroyed can throw on pywebview's own delivery
    // thread and hang interpreter shutdown, and this button had no
    // DOM-visible effect at all to (mis-)use as a settle signal.
    window.__fimResultsOpenFolderSettled = false;
    await window.pywebview.api.open_output_folder(currentOutputDirectory);
    window.__fimResultsOpenFolderSettled = true;
});

animateButton.addEventListener("click", () => {
    if (currentOutputDirectory !== null) {
        window.fim.showAnimation(currentOutputDirectory);
    }
});
