"use strict";

/* The unified run view's `completed` state (unified-run-view design
 * §3.2.1, §3.2.4, §3.7, §8 Phase E) -- unchanged content from the two
 * retired screens it replaces (`screens/results.js`, `screens/
 * batch-results.js`), plus the scrubber (`scrubber.js`) folded directly
 * in where the old, separate "Animate" button/screen used to be
 * (design §3.2.4: "there is no separate Animate button... the one time
 * slider simply keeps existing"). `enterCompletedState(payload, isBatch)`
 * is the one shared entry point every caller uses -- a live scalar run
 * finishing (`run-view-running.js`'s own `onRunDone`), a live batch
 * finishing (`onBatchDone`), and re-analyzing a persisted run
 * (`open-run.js`, always scalar) alike -- branching internally on
 * `isBatch` rather than being two unrelated code paths (design §3.2.5's
 * "one state model, not two" applied to this merge too).
 *
 * Every statistic arrives already formatted server-side
 * (`format_statistic`, matching `cli._format_optional`), so this file
 * does no numeric formatting of its own beyond the one field that is
 * never server-formatted (`Differentiation_q`, only ever present on
 * `Api.open_run`'s own payload). Each statistic renders through
 * `meters.js`'s shared `buildPointMeter`/`buildCiMeter`/
 * `buildOmittedMeter`.
 *
 * The scrubber is scalar-only (a batch's own `completed` view is a
 * pooled *final*-state scatter across replicates, design §2.4 -- there
 * is no one trajectory of its own to play back) and only when there is
 * more than one generation to scrub through; it always shows the
 * default projection, deliberately not pair-aware for this phase (the
 * "Compare demes directly" selector below only ever affects the
 * currently-drawn static frame, not future scrubbing) -- keeping these
 * two mechanisms independent, exactly as uncoupled as they were when
 * they lived on two separate screens, avoids a real interaction
 * question ("does choosing a pair apply to every frame or just this
 * one?") this phase's own "no new capabilities" scope does not need to
 * answer yet.
 */

const STATISTIC_NAMES = ["D", "G_ST", "E_ST", "K_ST", "H_S", "H_T"];

// See `wireCompletedScrubber`'s own comment: counts its own in-flight
// `get_animation_frames` calls. Zero means settled.
window.__fimScrubberPending = 0;

const resultsRunId = document.getElementById("results-run-id");
const resultsOutcome = document.getElementById("results-outcome");
const resultsStats = document.getElementById("results-stats");
const resultsDifferentiationQ = document.getElementById("results-differentiation-q");
const batchResultsSummary = document.getElementById("batch-results-summary");
const batchResultsTableBody = document.getElementById("batch-results-table-body");
const resultsBackButton = document.getElementById("results-back-button");

// Design §4.4's own "a statistic omitted from summary.json still
// renders as explicitly omitted, not blank" -- `_batch_done_payload`
// leaves `name` out of `summary` entirely rather than sending a
// null/undefined placeholder, matching `replicate_summary`'s own
// documented "omitted entirely rather than raising, since a single
// point has no interval."
const OMITTED_SUMMARY_TEXT = "omitted (fewer than two defined replicates)";

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

function renderBatchSummary(summary) {
    batchResultsSummary.replaceChildren();
    for (const name of STATISTIC_NAMES) {
        const interval = summary[name];
        const meter =
            interval === undefined
                ? buildOmittedMeter(name, OMITTED_SUMMARY_TEXT)
                : buildCiMeter(name, interval);
        batchResultsSummary.appendChild(meter);
    }
}

function renderBatchTable(replicates, p0Statistics) {
    batchResultsTableBody.replaceChildren();
    // p_0 baseline row — the initial conditions the entire batch shared.
    if (p0Statistics) {
        const baseRow = document.createElement("tr");
        baseRow.classList.add("p0-row");
        const baseCells = [
            0,
            "initial",
            ...STATISTIC_NAMES.map((name) => p0Statistics[name]),
        ];
        for (const value of baseCells) {
            const cell = document.createElement("td");
            cell.textContent = String(value);
            baseRow.appendChild(cell);
        }
        // Placeholder empty cell for the "Open" column.
        baseRow.appendChild(document.createElement("td"));
        batchResultsTableBody.appendChild(baseRow);
    }
    // Sort replicates by generation ascending (lowest generation first).
    const sorted = [...replicates].sort(
        (a, b) => a.generation - b.generation
    );
    for (const replicate of sorted) {
        const row = document.createElement("tr");
        // `replicate.reason` is always exactly `"statistic converged"`
        // when `converged` is true (`StopReason`'s own two-value enum) --
        // showing it alongside "Converged" said the same thing twice.
        // The `false` case keeps its own reason (`"hit the cap"`), which
        // adds real information "Not converged" alone does not carry.
        const outcome = replicate.converged
            ? "Converged"
            : `Not converged (${replicate.reason})`;
        const cells = [
            replicate.generation,
            outcome,
            ...STATISTIC_NAMES.map((name) => replicate.statistics[name]),
        ];
        for (const value of cells) {
            const cell = document.createElement("td");
            cell.textContent = String(value);
            row.appendChild(cell);
        }
        // "Open replicate" (design §4.4): the exact same operation as
        // "Open a run…" over one replicate's own trajectory --
        // `replicate.trajectoryPath` is already joined server-side
        // (`_batch_done_payload`'s own docstring), so this click never
        // does any path logic of its own.
        const openCell = document.createElement("td");
        const openButton = document.createElement("button");
        openButton.type = "button";
        openButton.textContent = "Open";
        openButton.addEventListener("click", async () => {
            const result = await window.pywebview.api.open_run({
                trajectoryPath: replicate.trajectoryPath,
            });
            if (result.ok) {
                window.fim.enterCompletedState(result, false);
            }
        });
        openCell.appendChild(openButton);
        row.appendChild(openCell);
        batchResultsTableBody.appendChild(row);
    }
}

function drawCompletedOverview(panels) {
    if (!panels || panels.length === 0) {
        return;
    }
    if (panels.length === 1) {
        drawScatter(runCanvas, panels[0]);
    } else {
        drawScatterGrid(runCanvas, panels);
    }
}

/**
 * Fetch and wire the scrubber over a just-completed scalar run's own
 * persisted trajectory -- the direct successor to the old, separate
 * "Animate" button's own `Api.get_animation_frames` call, now made
 * automatically rather than needing a second click (design §3.2.4).
 *
 * @param {string} outputDirectory
 * @param {number} generationCount
 */
async function wireCompletedScrubber(outputDirectory, generationCount) {
    if (generationCount <= 1) {
        scrubberControls.hidden = true;
        window.fim.resetScrubber();
        return;
    }
    // A counter, not a boolean: `completed` reached twice in one window
    // (e.g. re-running from `completed`) can leave the *first* run's own
    // fetch still in flight when the second's starts -- a plain
    // "settled = true" written by whichever of the two resolves first
    // would falsely read as fully settled while the other is still
    // pending. `window.__fimScrubberPending` counts calls actually in
    // flight; zero is the only state a caller should read as settled.
    window.__fimScrubberPending = (window.__fimScrubberPending || 0) + 1;
    try {
        const result = await window.pywebview.api.get_animation_frames(outputDirectory);
        // The state may already have moved on (a new run started, or a
        // different completed run opened) by the time this resolves --
        // never draw stale frames into whatever the canvas now shows.
        if (
            window.fim.getRunViewState() !== "completed" ||
            window.fim.getCompletedOutputDirectory() !== outputDirectory
        ) {
            return;
        }
        if (!result.ok || result.frames.length === 0) {
            scrubberControls.hidden = true;
            window.fim.resetScrubber();
            return;
        }
        scrubberControls.hidden = false;
        window.fim.setScrubberFrames(result.frames, (frame) => {
            drawCompletedOverview(frame.panels);
        });
    } finally {
        window.__fimScrubberPending -= 1;
    }
}

/**
 * Enter `completed`: render a just-finished (or re-opened) run's own
 * summary. The one shared entry point every caller uses.
 *
 * @param {object} payload - `_drain_run_messages`'s own `"done"` shape
 *     (scalar) or `_batch_done_payload`'s (batch) -- both carry
 *     `outputDirectory`/`runId`/`panels`/`demeCount`, diverging only in
 *     the statistics/table fields this function reads conditionally.
 * @param {boolean} isBatch
 */
window.fim.enterCompletedState = function enterCompletedState(payload, isBatch) {
    window.fim.setRunViewState("completed");
    window.fim.setCompletedOutputDirectory(payload.outputDirectory);
    runProgress.hidden = true;
    if (initialStats) {
        initialStats.hidden = true;
    }
    if (runPlotTitle) {
        runPlotTitle.textContent = payload.runId
            ? `FIM simulation — ${payload.runId}`
            : "FIM simulation — completed";
    }
    runCompleted.hidden = false;
    cancelButton.disabled = true;
    openFolderButton.hidden = false;
    resultsBackButton.hidden = false;
    resultsStats.hidden = isBatch;
    batchResultsSummary.hidden = !isBatch;
    batchResultsTable.hidden = !isBatch;
    resultsRunId.textContent = payload.runId;
    // `wireCompletedScrubber` (scalar branch, below) fetches animation
    // frames over a real, un-awaited-by-any-caller bridge call --
    // `window.__fimScrubberPending` (see that function) is how a test
    // knows the last thing this entry into `completed` does in the
    // background has actually finished, the same settled-flag shape
    // `conftest.py`'s own docstring already establishes for five other
    // bridge calls (`refreshRecentRuns`, `cancel_run`, "Open output
    // folder", the external-doc link, the `openExternal` menu dispatch)
    // -- a test destroying the window as soon as it sees `completed` is
    // otherwise racing this call exactly like those five once did. A
    // batch run never reaches `wireCompletedScrubber` at all, so there
    // is nothing to increment here for that branch.
    if (isBatch) {
        resultsOutcome.textContent = "";
        renderBatchSummary(payload.summary);
        renderBatchTable(payload.replicates, payload.p0Statistics);
        scrubberControls.hidden = true;
        window.fim.resetScrubber();
    } else {
        const report = payload.report;
        const reason = report.reason.charAt(0).toUpperCase() + report.reason.slice(1);
        resultsOutcome.textContent = `${reason}: generation ${report.generation}`;
        for (const name of STATISTIC_NAMES) {
            const value = payload.statistics[name];
            const element = document.getElementById(`stat-${name}`);
            element.replaceChildren(buildPointMeter(name, value));
        }
        renderDifferentiationQ(report);
        wireCompletedScrubber(payload.outputDirectory, payload.generationCount);
    }

    const panels = payload.panels;
    drawCompletedOverview(panels);
    runDemePairSelector.hidden = !panels || payload.demeCount < 2;
    if (panels && panels.length > 0) {
        window.fim.wireDemePairSelector({
            xSelect: runXDeme,
            ySelect: runYDeme,
            showPairButton: runShowPairButton,
            showOverviewButton: runShowOverviewButton,
            container: runDemePairSelector,
            demeCount: payload.demeCount,
            onShowPair: async (x, y) => {
                const outputDirectory = window.fim.getCompletedOutputDirectory();
                if (outputDirectory === null) {
                    return;
                }
                const getPanel = isBatch
                    ? window.pywebview.api.get_batch_deme_pair_panel
                    : window.pywebview.api.get_deme_pair_panel;
                const result = await getPanel(outputDirectory, x, y);
                if (result.ok) {
                    drawScatter(runCanvas, result.panel);
                }
            },
            onShowOverview: () => {
                drawCompletedOverview(panels);
            },
        });
    }

    window.fim.showScreen("screen-run");
};

window.fim.returnToInitialState = function returnToInitialState() {
    resultsBackButton.hidden = true;
    window.fim.showScreen("screen-run");
    window.fim.enterInitialState();
};

// Wire the Back button once on load -- navigates from `completed` back
// to `initial` (p_0 preview), the same as clicking "Run simulation"
// would do for a fresh start but without actually starting a run.
resultsBackButton.addEventListener("click", () => {
    window.fim.returnToInitialState();
});
