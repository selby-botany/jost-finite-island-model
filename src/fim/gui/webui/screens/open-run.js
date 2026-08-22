"use strict";

/* Screen 6: open an existing run (design doc §4.6) -- pick a persisted
 * trajectory and generation, then re-analyze it, matching `fim stats`.
 *
 * Reached from Screen 1's own "Open a run…" button (`input.js`). Opening
 * succeeds by handing `Api.open_run`'s own Screen-3-shaped payload
 * straight to `window.fim.showResults` -- design §4.6's "opening a run
 * re-renders Screen 3... unchanged" realized as literal reuse, not a
 * second rendering path.
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
    const runs = await window.pywebview.api.list_recent_runs();
    for (const run of runs) {
        const row = document.createElement("tr");
        for (const value of [run.runId, run.endedAt, run.label]) {
            const cell = document.createElement("td");
            cell.textContent = value;
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
    window.fim.showResults(result);
});

openRunBackButton.addEventListener("click", () => {
    window.fim.showScreen("screen-input");
});

window.fim.showOpenRunScreen = function showOpenRunScreen() {
    showOpenRunBanner("");
    setSelectedTrajectory(null);
    generationValueInput.value = "";
    differentiationOrdersInput.value = "";
    window.fim.showScreen("screen-open-run");
    refreshRecentRuns();
};
