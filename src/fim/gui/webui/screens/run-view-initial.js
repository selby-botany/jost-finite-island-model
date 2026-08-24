"use strict";

/* The unified run view's `initial` state (unified-run-view design
 * §3.2.1, §3.2.2, §8 Phase E/F) -- renders the p_0 scatter, axis
 * labels, six statistics, and a generation-0 progress bar as soon as
 * the form has valid values (Phase F: `Api.get_initial_state_panels`),
 * so the canvas is never blank at startup or after a reset.
 */

// Declared here, not in `run-view-running.js`/`run-view-completed.js`
// (both of which need most of these too): every classic script on this
// page shares one global scope, so a `const` declared in one file is
// visible by bare name in every other -- declaring it twice would be a
// `SyntaxError`, not a shadow (`test_webui_global_scope.py`'s own
// module docstring). `run-view-initial.js` loads first among the three
// state files, so it is the natural, single owner.
const runCanvas = document.getElementById("run-canvas");
const runPlotTitle = document.getElementById("run-plot-title");
const runProgress = document.getElementById("run-progress");
const runCompleted = document.getElementById("run-completed");
const batchResultsTable = document.getElementById("batch-results-table");
const scrubberControls = document.getElementById("scrubber-controls");
const runDemePairSelector = document.getElementById("run-deme-pair-selector");
const runXDeme = document.getElementById("run-x-deme");
const runYDeme = document.getElementById("run-y-deme");
const runShowPairButton = document.getElementById("run-show-pair-button");
const runShowOverviewButton = document.getElementById("run-show-overview-button");

// The progress bar / label elements (declared in run-view-running.js
// but needed here too -- run-view-initial.js loads before run-view-
// running.js, so these declarations must live here). Run-view-running.js
// reads these by bare name without re-declaring them.
const progressBar = document.getElementById("progress-generation");
const progressLabel = document.getElementById("progress-generation-label");

// The p_0 statistics panel: a `<div>` inside `run-completed` whose
// six `[data-stat]` children are the same meter slots the completed
// state uses, reused here to show gen-0 statistics without duplicating
// the meter markup.
const initialStats = document.getElementById("initial-stats");

function clearRunCanvas() {
    _currentPanels = null;
    runCanvas.getContext("2d").clearRect(0, 0, runCanvas.width, runCanvas.height);
}

/**
 * Render the p_0 scatter and decorations (Phase F).
 *
 * Fetches `Api.get_initial_state_panels` with the current form values,
 * then draws panels, statistics, and the gen-0 progress bar. Silently
 * leaves everything blank if the form does not yet have valid values.
 */
async function renderInitialPreview() {
    const values = collectFormValues();
    const result = await window.pywebview.api.get_initial_state_panels(values);
    // State may have changed while the bridge call was in flight --
    // only draw if still in `initial`.
    if (window.fim.getRunViewState() !== "initial") {
        return;
    }
    if (!result.ok) {
        return;
    }
    // Progress bar at generation 0.
    runProgress.hidden = false;
    progressBar.max = result.maxGenerations;
    progressBar.value = 0;
    progressLabel.textContent = `0 / ${result.maxGenerations}`;

    // Six statistics for p_0.
    if (initialStats) {
        initialStats.hidden = false;
        for (const name of ["D", "G_ST", "E_ST", "K_ST", "H_S", "H_T"]) {
            const value = result.statistics[name];
            const slot = initialStats.querySelector(`[data-stat="${name}"]`);
            if (slot) {
                slot.replaceChildren(buildPointMeter(name, value));
            }
        }
    }

    // Scatter panels.
    const panels = result.panels;
    if (panels && panels.length > 0) {
        if (panels.length === 1) {
            drawScatter(runCanvas, panels[0]);
        } else {
            drawScatterGrid(runCanvas, panels);
        }
    }

    // Deme-pair selector -- static view only in `initial` (no live
    // bridge call, only a local redraw from already-fetched panels).
    runDemePairSelector.hidden = !panels || result.demeCount < 2;
    if (panels && panels.length > 0 && result.demeCount >= 2) {
        window.fim.wireDemePairSelector({
            xSelect: runXDeme,
            ySelect: runYDeme,
            showPairButton: runShowPairButton,
            showOverviewButton: runShowOverviewButton,
            container: runDemePairSelector,
            demeCount: result.demeCount,
            onShowPair: (_x, _y) => {
                // Static redraw only in `initial` -- no live pair
                // bridge call until a run is actually running.
            },
            onShowOverview: () => {
                if (panels.length === 1) {
                    drawScatter(runCanvas, panels[0]);
                } else {
                    drawScatterGrid(runCanvas, panels);
                }
            },
        });
    }
}

window.fim.renderInitialPreview = renderInitialPreview;

/**
 * Enter `initial`: hide every other state's own content, disable the
 * controls only `running`/`completed` make sense for, and render the
 * p_0 preview (Phase F: scatter, statistics, gen-0 progress bar).
 */
function enterInitialState() {
    window.fim.setRunViewState("initial");
    window.fim.setCompletedOutputDirectory(null);
    runCompleted.hidden = true;
    batchResultsTable.hidden = true;
    scrubberControls.hidden = true;
    runDemePairSelector.hidden = true;
    runProgress.hidden = true;
    cancelButton.disabled = true;
    openFolderButton.hidden = true;
    if (runPlotTitle) {
        runPlotTitle.textContent = "FIM simulation — initial conditions (p₀)";
    }
    // `resultsBackButton` is declared in run-view-completed.js (loads
    // after this file) but always present by the time any user event
    // or `whenApiReady` callback fires.
    if (typeof resultsBackButton !== "undefined") {
        resultsBackButton.hidden = true;
    }
    if (initialStats) {
        initialStats.hidden = true;
    }
    window.fim.resetScrubber();
    clearRunCanvas();
    // Render p_0 preview asynchronously -- do not await here since
    // `enterInitialState` is called synchronously from many sites.
    renderInitialPreview();
}

window.fim.enterInitialState = enterInitialState;

/**
 * `fim.menu.newConfiguration` (native File menu) -- a genuine reset,
 * unlike `configureTab`'s own "never resets a field" contract: fetches
 * fresh starter values and applies them, the same as a first app
 * launch. Cycles `window.__fimRunViewReady` false-then-true around the
 * reset so a test (or anything else) waiting for "the form is in a
 * fully settled state" has one reliable signal for both the initial
 * load and a later reset, instead of racing a DOM value change alone --
 * `resetInputForm` still has two more real bridge calls in flight
 * (`get_default_max_workers`, `revalidate`'s own `validate_form`) after
 * `field-N` itself already shows the new value.
 */
window.fim.menu.newConfiguration = async function newConfiguration() {
    window.fim.showScreen("screen-run");
    window.__fimRunViewReady = false;
    enterInitialState();
    await resetInputForm();
    // `enterInitialState`'s own `renderInitialPreview()` call already
    // fired above, but before `resetInputForm` had applied real
    // starter values -- `collectFormValues()` inside it reads
    // whatever the fields held at that instant, synchronously, before
    // any await, so that first call raced the reset and snapshotted
    // stale/empty fields. A confirmed-live bug, not hypothetical: the
    // bridge call it makes then fails validation and returns `!ok`,
    // which leaves the canvas, statistics, and progress bar all blank
    // with nothing ever re-triggering a redraw. Render again now that
    // the form genuinely holds starter values.
    await renderInitialPreview();
    window.__fimRunViewReady = true;
};

async function initializeRunView() {
    enterInitialState();
    await resetInputForm();
    // See the identical comment in `newConfiguration` above -- the
    // same race exists here, on cold start.
    await renderInitialPreview();
    window.__fimRunViewReady = true;
}

whenApiReady(initializeRunView);
