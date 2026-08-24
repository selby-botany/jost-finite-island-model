"use strict";

/* The unified run view's `initial` state (unified-run-view design
 * §3.2.1, §3.2.2, §8 Phase E) -- deliberately thin this phase: no `p_0`
 * preview yet (design §8 Phase F adds `Api.get_initial_state_panels`
 * and live field-change re-rendering), only the state transition itself
 * and the one-time/`newConfiguration` form reset that used to live in
 * the now-retired `screens/input.js`.
 */

// Declared here, not in `run-view-running.js`/`run-view-completed.js`
// (both of which need most of these too): every classic script on this
// page shares one global scope, so a `const` declared in one file is
// visible by bare name in every other -- declaring it twice would be a
// `SyntaxError`, not a shadow (`test_webui_global_scope.py`'s own
// module docstring). `run-view-initial.js` loads first among the three
// state files, so it is the natural, single owner.
const runCanvas = document.getElementById("run-canvas");
const runProgress = document.getElementById("run-progress");
const runCompleted = document.getElementById("run-completed");
const batchResultsTable = document.getElementById("batch-results-table");
const scrubberControls = document.getElementById("scrubber-controls");
const runDemePairSelector = document.getElementById("run-deme-pair-selector");
const runXDeme = document.getElementById("run-x-deme");
const runYDeme = document.getElementById("run-y-deme");
const runShowPairButton = document.getElementById("run-show-pair-button");
const runShowOverviewButton = document.getElementById("run-show-overview-button");

function clearRunCanvas() {
    runCanvas.getContext("2d").clearRect(0, 0, runCanvas.width, runCanvas.height);
}

/**
 * Enter `initial`: hide every other state's own content, disable the
 * controls only `running`/`completed` make sense for, and leave the
 * shared canvas blank -- this phase's own scope stops there (design §8
 * Phase F is what actually renders something here).
 */
function enterInitialState() {
    window.fim.setRunViewState("initial");
    window.fim.setCompletedOutputDirectory(null);
    runProgress.hidden = true;
    runCompleted.hidden = true;
    batchResultsTable.hidden = true;
    scrubberControls.hidden = true;
    runDemePairSelector.hidden = true;
    cancelButton.disabled = true;
    openFolderButton.hidden = true;
    window.fim.resetScrubber();
    clearRunCanvas();
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
    window.__fimRunViewReady = true;
};

async function initializeRunView() {
    enterInitialState();
    await resetInputForm();
    window.__fimRunViewReady = true;
}

whenApiReady(initializeRunView);
