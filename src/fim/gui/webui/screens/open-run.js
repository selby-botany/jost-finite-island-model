"use strict";

/* Screen 6: open an existing run (design doc §4.6) -- pick a persisted
 * trajectory and generation, then re-analyze it, matching `fim stats`.
 * This is also the Home rail destination (`screens/nav-rail.js`'s own
 * `resolveDestination` map: `"home": "screen-open-run"`) -- one shared
 * screen, not two, so home enrichment design doc `20260909-claude-
 * sonnet-5-home-enrichment-design.md`'s (`selby/restricted`) per-row
 * config summary and final statistics/outcome columns (`Api.list_home_
 * runs`, approach A1: reads each row's own already-computed `report.
 * json`/`summary.json`, never `trajectory.jsonl`, never re-derived)
 * show up identically whether this screen was reached via the rail or
 * via the File menu's own "Open run…" action.
 *
 * Reached from the File menu's own "Open run…" action (`app.js`'s
 * `fim.menu.openRun`). Opening succeeds by handing `Api.open_run`'s
 * own `completed`-shaped payload straight to `window.fim.
 * enterCompletedState`, landing directly in that state (unified-run-view
 * design §3.2.1) -- literal reuse of the same rendering path a live
 * run's own `onRunDone` already uses, not a second one.
 */

const openRunBanner = document.getElementById("open-run-banner");
const recentRunsBody = document.getElementById("open-run-recent-runs-body");
const browseButton = document.getElementById("browse-trajectory-button");
const generationValueInput = document.getElementById("open-run-generation-value");
const differentiationOrdersInput = document.getElementById(
    "open-run-differentiation-orders"
);
const openButton = document.getElementById("open-run-open-button");
const openRunBackButton = document.getElementById("open-run-back-button");

let selectedTrajectoryPath = null;

function showOpenRunBanner(message) {
    if (!message) {
        openRunBanner.hidden = true;
        openRunBanner.textContent = "";
        return;
    }
    openRunBanner.hidden = false;
    openRunBanner.textContent = message;
}

function setSelectedTrajectory(path) {
    selectedTrajectoryPath = path;
    openButton.disabled = path === null;
}

function generationMode() {
    const checked = document.querySelector(
        'input[name="open_run_generation_mode"]:checked'
    );
    return checked ? checked.value : "final";
}

/**
 * Render one row's own config summary as a compact, single-line string
 * (home enrichment design doc `20260909-claude-sonnet-5-home-
 * enrichment-design.md`, `selby/restricted`). `Object.entries` walks
 * `configSummary` in `Api.list_home_runs`'s own fixed key order (`N`,
 * `d`, `seed`, `m`, `mu`, `mutation_model`) — JSON preserves object key
 * order, so nothing here needs to know that order itself.
 * @param {Record<string, string> | null} configSummary
 * @returns {string}
 */
function formatRowConfigSummary(configSummary) {
    if (!configSummary) {
        return "";
    }
    return Object.entries(configSummary)
        .map(([key, value]) => `${key}=${value}`)
        .join(" ");
}

/**
 * Render one row's own final statistics as a compact, single-line
 * string -- a point value for a scalar run, or `"mean [low, high]"`
 * for a batch's own confidence interval, the identical text `webui/
 * meters.js`'s own `buildCiMeter` already shows in its tooltip (not a
 * second, independently worded CI format).
 * @param {Record<string, string | {mean: string, low: string, high: string}> | null} statistics
 * @returns {string}
 */
function formatRowStatistics(statistics) {
    if (!statistics) {
        return "";
    }
    return Object.entries(statistics)
        .map(([name, value]) =>
            typeof value === "string"
                ? `${name}=${value}`
                : `${name}=${value.mean} [${value.low}, ${value.high}]`
        )
        .join(" ");
}

async function refreshRecentRuns() {
    // `showOpenRunScreen` fires this without awaiting it (a real
    // filesystem scan should not block the screen transition), so
    // `window.__fimOpenRunRecentRunsLoaded` is the only observable
    // signal that this async call has actually settled -- tests poll
    // it before tearing down their window, rather than treating
    // "screen visible" (true the instant `showOpenRunScreen` returns,
    // well before this promise resolves) as proof this call is done.
    // Skipping that wait let a real window get destroyed while
    // `Api.list_recent_runs()`'s result was still in flight back to
    // pywebview's own JS bridge, surfacing as a `JavascriptException`
    // on pywebview's own delivery thread (`webview/util.py`'s
    // `js_bridge_call`) -- harmless to this page, but a real source of
    // an occasional, very slow interpreter-shutdown stall while that
    // thread outlived the window it was about to call back into.
    window.__fimOpenRunRecentRunsLoaded = false;
    recentRunsBody.replaceChildren();
    const runs = await window.pywebview.api.list_home_runs();
    for (const run of runs) {
        const row = document.createElement("tr");
        const configText = formatRowConfigSummary(run.configSummary);
        const statisticsText = formatRowStatistics(run.statistics);
        for (const [value, className] of [
            [run.runId, null],
            [run.endedAt, null],
            [run.label, null],
            [configText, "open-run-summary-cell"],
            [statisticsText, "open-run-summary-cell"],
        ]) {
            const cell = document.createElement("td");
            cell.textContent = value;
            // `configText`/`statisticsText` can run long (six fields,
            // six statistics) -- capped and ellipsized in CSS, with the
            // full text still reachable on hover via `title` rather
            // than silently truncated with no way to see the rest.
            if (className !== null) {
                cell.className = className;
                cell.title = value;
            }
            row.appendChild(cell);
        }
        row.addEventListener("click", () => {
            for (const sibling of recentRunsBody.querySelectorAll("tr")) {
                sibling.classList.remove("selected");
            }
            row.classList.add("selected");
            if (run.isBatch) {
                // Design §0, §4.0 #9: a batch manifest has no single
                // trajectory of its own to verify or re-analyze here --
                // named explicitly rather than silently doing nothing
                // or attempting (and failing) to re-analyze it anyway.
                setSelectedTrajectory(null);
                showOpenRunBanner(
                    "batch runs have no single trajectory — open a replicate " +
                        "from its own batch results screen instead"
                );
                return;
            }
            showOpenRunBanner("");
            setSelectedTrajectory(run.trajectoryPath);
        });
        recentRunsBody.appendChild(row);
    }
    window.__fimOpenRunRecentRunsLoaded = true;
}

browseButton.addEventListener("click", async () => {
    const result = await window.pywebview.api.browse_for_trajectory();
    if (!result.ok) {
        return;
    }
    showOpenRunBanner("");
    setSelectedTrajectory(result.path);
});

openButton.addEventListener("click", async () => {
    if (selectedTrajectoryPath === null) {
        showOpenRunBanner("no trajectory selected");
        return;
    }
    const values = {
        trajectoryPath: selectedTrajectoryPath,
        generationMode: generationMode(),
        generation: generationValueInput.value,
        differentiationOrders: differentiationOrdersInput.value,
    };
    const result = await window.pywebview.api.open_run(values);
    if (!result.ok) {
        showOpenRunBanner(result.message);
        return;
    }
    showOpenRunBanner("");
    window.fim.enterCompletedState(result, false);
});

openRunBackButton.addEventListener("click", () => {
    window.fim.showScreen("screen-run");
});

window.fim.showOpenRunScreen = function showOpenRunScreen() {
    showOpenRunBanner("");
    setSelectedTrajectory(null);
    generationValueInput.value = "";
    differentiationOrdersInput.value = "";
    window.fim.showScreen("screen-open-run");
    refreshRecentRuns();
};
