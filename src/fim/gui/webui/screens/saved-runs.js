"use strict";

/* Saved runs: durable run objects the GUI can keep, edit, start, pause,
 * resume, cancel, and delete (`fim.gui.object_bridge`, phase-2 run
 * lifecycle plan, commit 10).
 *
 * - "Save for later" (Run toolbar) keeps the form as a saved run. An
 *   invalid or incomplete form is saved as a Draft and can be reopened
 *   and fixed later; a valid one is saved as Planned. Saving again while
 *   a saved run is being edited updates that run, so a run moves between
 *   Draft and Planned as its form is broken or fixed.
 * - Home's "Saved runs" table lists every saved run with only the actions
 *   its state allows (`Api.list_run_objects`).
 * - "Pause" (Run toolbar, shown only while a saved run executes) asks the
 *   run to write a checkpoint and stop; Resume continues it exactly.
 *
 * The running view itself is the same one "Run simulation" uses
 * (`run-view-running.js`); this file only adds what differs for a saved
 * run, by wrapping the `fim.onRun*` push handlers once every screen
 * script has loaded.
 */

const savedRunsBanner = document.getElementById("saved-runs-banner");
const savedRunsEmpty = document.getElementById("saved-runs-empty");
const savedRunsTable = document.getElementById("saved-runs-table");
const savedRunsBody = document.getElementById("saved-runs-body");
const saveRunObjectButton = document.getElementById("save-run-object-button");
const pauseRunButton = document.getElementById("pause-run-button");

function savedRunStateFor(tabId = window.fim.workspace.selectedId()) {
    const documentState = window.fim.workspace.get(tabId);
    if (!documentState) {
        return null;
    }
    if (!documentState.savedRunState) {
        documentState.savedRunState = {
            editingObjectId: null,
            activeSavedRunId: null,
            pausePending: false,
        };
    }
    return documentState.savedRunState;
}

function renderPauseRunButton() {
    const state = savedRunStateFor();
    pauseRunButton.hidden = !state || state.activeSavedRunId === null;
    pauseRunButton.disabled = !state || state.pausePending;
}

window.fim.workspace.onSelectionChange(renderPauseRunButton);

// Actions in the order their buttons appear in a row.
const SAVED_RUN_ACTIONS = [
    ["start", "Start"],
    ["resume", "Resume"],
    ["clone", "Clone"],
    ["edit", "Edit"],
    ["open", "Open"],
    ["cancel", "Cancel"],
    ["delete", "Delete"],
];

// What each state means to someone reading the table.
const SAVED_RUN_STATE_TEXT = {
    draft: "Draft (not valid yet)",
    planned: "Planned",
    starting: "Starting",
    running: "Running",
    pausing: "Pausing",
    paused: "Paused",
    cancelling: "Cancelling",
    interrupted: "Interrupted",
    failed: "Failed",
    completed: "Completed",
    cancelled: "Cancelled",
};

function showSavedRunsBanner(message) {
    savedRunsBanner.hidden = !message;
    savedRunsBanner.textContent = message || "";
}

/** Hide Pause and forget the active saved run once it stops for any reason. */
function endActiveSavedRun() {
    for (const tabId of window.fim.workspace.ids()) {
        const state = savedRunStateFor(tabId);
        if (state) {
            state.activeSavedRunId = null;
            state.pausePending = false;
        }
    }
    renderPauseRunButton();
}

function clearEditingObject(objectId) {
    for (const tabId of window.fim.workspace.ids()) {
        const state = savedRunStateFor(tabId);
        if (state && state.editingObjectId === objectId) {
            state.editingObjectId = null;
        }
    }
}

/**
 * Build one table row.
 * @param {object} run A row from `Api.list_run_objects`.
 * @returns {HTMLTableRowElement} The row.
 */
function buildSavedRunRow(run) {
    const row = document.createElement("tr");
    row.dataset.objectId = run.objectId;
    const name = document.createElement("td");
    name.textContent = run.name || run.objectId;
    if (run.description) {
        name.title = run.description;
    }
    const state = document.createElement("td");
    state.textContent = SAVED_RUN_STATE_TEXT[run.state] || run.state;
    const problems = run.error ? [run.error] : run.issues;
    if (problems.length > 0) {
        state.title = problems.join("\n");
    }
    const generation = document.createElement("td");
    generation.textContent = String(run.generation);
    const updated = document.createElement("td");
    updated.textContent = run.updatedAt;
    const actions = document.createElement("td");
    actions.className = "saved-runs-actions";
    for (const [action, label] of SAVED_RUN_ACTIONS) {
        if (!run.actions[action]) {
            continue;
        }
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = label;
        button.dataset.action = action;
        button.addEventListener("click", () => onSavedRunAction(action, run));
        actions.append(button);
    }
    row.append(name, state, generation, updated, actions);
    return row;
}

/** Reload the Saved runs table from disk. */
async function refreshSavedRuns() {
    const listing = await window.pywebview.api.list_run_objects();
    if (!listing.ok) {
        showSavedRunsBanner(listing.message);
        return;
    }
    savedRunsBody.replaceChildren(...listing.runs.map(buildSavedRunRow));
    savedRunsTable.hidden = listing.runs.length === 0;
    savedRunsEmpty.hidden = listing.runs.length > 0;
    if (listing.unreadable.length > 0) {
        showSavedRunsBanner(`Some saved runs cannot be read: ${listing.unreadable.join("; ")}`);
    }
}

window.fim.refreshSavedRuns = refreshSavedRuns;

/**
 * Carry out one row action.
 * @param {string} action One of `SAVED_RUN_ACTIONS`' keys.
 * @param {object} run The row's saved run.
 */
async function onSavedRunAction(action, run) {
    showSavedRunsBanner("");
    if (action === "edit") {
        await editSavedRun(run.objectId);
        return;
    }
    if (action === "start" || action === "resume") {
        await startSavedRun(run);
        return;
    }
    if (action === "open") {
        await window.fim.openComputedRun(run.directory, false);
        return;
    }
    const label = run.name || run.objectId;
    let result;
    if (action === "clone") {
        result = await window.pywebview.api.clone_run_object(run.objectId);
        if (result.ok) {
            showSavedRunsBanner(`Cloned "${label}" as a new saved run.`);
        }
    } else if (action === "cancel") {
        result = await window.pywebview.api.cancel_run_object(run.objectId);
    } else if (action === "delete") {
        if (!window.confirm(`Delete the saved run "${label}" and all of its data?`)) {
            return;
        }
        result = await window.pywebview.api.delete_run_object(run.objectId);
        if (result.ok) {
            clearEditingObject(run.objectId);
        }
    }
    if (!result.ok) {
        showSavedRunsBanner(result.message);
    }
    await refreshSavedRuns();
}

/**
 * Show a saved run's configuration in Configure, to change and save it.
 * @param {string} objectId The saved run.
 */
async function editSavedRun(objectId) {
    const tabId = window.fim.workspace.selectedId();
    const opened = await window.pywebview.api.open_run_object(objectId);
    if (!opened.ok) {
        showSavedRunsBanner(opened.message);
        return;
    }
    if (window.fim.workspace.selectedId() !== tabId) {
        return;
    }
    applyFormValues(opened.values);
    window.fim.applyLoadedRunSettings(opened);
    const state = savedRunStateFor(tabId);
    if (state) {
        state.editingObjectId = opened.editable ? objectId : null;
    }
    window.fim.setRunDetails(opened.name || "", opened.description || "");
    window.fim.showScreen("screen-run");
    await revalidate();
    window.fim.showRunBanner(
        opened.editable
            ? `Editing the saved run "${opened.name || objectId}" ` +
                  `(${SAVED_RUN_STATE_TEXT[opened.state]}). ` +
                  '"Save for later" updates it.'
            : "This saved run has started, so its configuration cannot change. " +
                  '"Save for later" keeps a copy as a new saved run.'
    );
}

/**
 * Start a planned saved run, or resume a paused or stopped one, in the
 * same running view "Run simulation" uses. While another run or a sweep
 * is in flight, only says so here on Home, and leaves the running view
 * alone (`startRunExclusively`, `run-view-controls.js`).
 * @param {object} run The row's saved run.
 */
async function startSavedRun(run) {
    await window.fim.startRunExclusively(() => beginSavedRun(run), showSavedRunsBanner);
}

/**
 * Show the running view and start `run`, once `startSavedRun` knows that
 * no other run is starting or in flight.
 * @param {object} run The row's saved run.
 */
async function beginSavedRun(run) {
    const tabId = window.fim.workspace.selectedId();
    window.fim.showScreen("screen-run");
    window.fim.showRunBanner("");
    window.fim.enterRunningState(false);
    window.fim.setRunCardTitle({ phase: "running", runName: run.name || null });
    const state = savedRunStateFor(tabId);
    if (state) {
        state.activeSavedRunId = run.objectId;
    }
    renderPauseRunButton();
    const started = await window.pywebview.api.start_run_object(run.objectId);
    if (!started.ok) {
        if (state) {
            state.activeSavedRunId = null;
        }
        renderPauseRunButton();
        window.fim.enterInitialState();
        window.fim.showRunBanner(started.message);
        return;
    }
    window.fim.applyRunKind(Boolean(started.isBatch));
    if (!started.isBatch) {
        window.fim.setLiveEquilibriumReference(started.equilibrium);
        window.fim.setLiveIdentityRecoveryReference(started.identityRecovery);
        window.fim.setLiveClosedForm(started.closedForm);
        window.fim.setLiveConvergence(started.convergence);
    }
}

/**
 * Keep the form as a saved run: a Draft when it is not valid yet, Planned
 * when it is ready to run. Shared by "Save for later" and the close dialog
 * (`screens/close-guard.js`).
 * @returns {Promise<object>} `Api.save_run_object`'s result.
 */
async function saveFormForLater() {
    const tabId = window.fim.workspace.selectedId();
    const state = savedRunStateFor(tabId);
    const name = document.getElementById("run-name-input").value.trim();
    const description = document
        .getElementById("run-description-input")
        .value.trim();
    window.fim.setRunDetails(name, description);
    const values = collectFormValues(tabId);
    const saved = await window.pywebview.api.save_run_object(
        values,
        state ? state.editingObjectId : null,
        name,
        description
    );
    if (saved.ok) {
        if (state) {
            state.editingObjectId = saved.objectId;
        }
        window.fim.markFormClean(tabId);
    }
    return saved;
}

window.fim.saveFormForLater = saveFormForLater;

async function onSaveRunObjectClicked() {
    const tabId = window.fim.workspace.selectedId();
    const saved = await saveFormForLater();
    if (window.fim.workspace.selectedId() !== tabId) {
        return;
    }
    if (!saved.ok) {
        window.fim.showRunBanner(saved.message);
        return;
    }
    window.fim.showRunBanner(
        saved.state === "draft"
            ? `Saved as a Draft. It cannot run until this is fixed: ${saved.problem}`
            : "Saved. Start it from Saved runs on Home."
    );
}

async function onPauseClicked() {
    const tabId = window.fim.workspace.selectedId();
    const state = savedRunStateFor(tabId);
    if (!state || state.activeSavedRunId === null || state.pausePending) {
        return;
    }
    state.pausePending = true;
    renderPauseRunButton();
    const paused = await window.pywebview.api.pause_run();
    state.pausePending = false;
    renderPauseRunButton();
    if (!paused.ok) {
        if (window.fim.workspace.selectedId() === tabId) {
            window.fim.showRunBanner(paused.message);
        }
    }
}

/**
 * Wrap a `window.fim` function so `after` runs once it returns.
 * @param {string} name The function's name on `window.fim`.
 * @param {Function} after Called with the same arguments.
 */
function wrapFimFunction(name, after) {
    const original = window.fim[name];
    window.fim[name] = function wrapped(...args) {
        const result = original.apply(this, args);
        after(...args);
        return result;
    };
}

window.fim.onRunPaused = function onRunPaused(generation) {
    endActiveSavedRun();
    cancelButton.disabled = true;
    window.fim.showRunBanner(
        `Paused at generation ${generation}. Resume it from Saved runs on Home.`
    );
};

window.fim.getEditingObjectId = function getEditingObjectId() {
    return savedRunStateFor()?.editingObjectId ?? null;
};

// After every screen script has loaded, so the wrapped functions exist.
document.addEventListener("DOMContentLoaded", () => {
    wrapFimFunction("onRunDone", endActiveSavedRun);
    wrapFimFunction("onRunError", endActiveSavedRun);
    wrapFimFunction("onBatchDone", endActiveSavedRun);
    wrapFimFunction("onBatchError", endActiveSavedRun);
    const plainCancelled = window.fim.onRunCancelled;
    window.fim.onRunCancelled = function onRunCancelled(generation) {
        const activeSavedRunId = window.fim.workspace
            .ids()
            .some((tabId) => {
                const state = savedRunStateFor(tabId);
                return state !== null && state.activeSavedRunId !== null;
            });
        if (!activeSavedRunId) {
            plainCancelled(generation);
            return;
        }
        endActiveSavedRun();
        cancelButton.disabled = true;
        window.fim.showRunBanner(
            `Run cancelled at generation ${generation}. ` +
                "Its saved run keeps the data written so far."
        );
    };
    const plainBatchCancelled = window.fim.onBatchCancelled;
    window.fim.onBatchCancelled = function onBatchCancelled(payload) {
        const activeSavedRunId = window.fim.workspace
            .ids()
            .some((tabId) => {
                const state = savedRunStateFor(tabId);
                return state !== null && state.activeSavedRunId !== null;
            });
        if (!activeSavedRunId) {
            if (plainBatchCancelled) {
                plainBatchCancelled(payload);
            }
            return;
        }
        endActiveSavedRun();
        cancelButton.disabled = true;
        const gen =
            payload && payload.generation != null ? payload.generation : 0;
        window.fim.showRunBanner(
            `Run cancelled at generation ${gen}. ` +
                "Its saved run keeps the data written so far."
        );
    };
    // Loading or resetting a configuration is no longer editing the saved run.
    const clearEditing = () => {
        const state = savedRunStateFor();
        if (state) {
            state.editingObjectId = null;
        }
    };
    wrapFimFunction("applyLoadedRunSettings", clearEditing);
    wrapFimFunction("clearLoadedRunSettings", clearEditing);
});

function wireSavedRuns() {
    saveRunObjectButton.addEventListener("click", onSaveRunObjectClicked);
    pauseRunButton.addEventListener("click", onPauseClicked);
}

whenApiReady(wireSavedRuns);
