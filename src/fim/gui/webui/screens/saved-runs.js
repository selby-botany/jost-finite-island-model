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
 * - Home's "Saved runs" table keeps unfinished saved runs. Completed runs
 *   are listed with results rather than duplicated here.
 * - Home's Select, Select all, and Delete selected include saved runs
 *   that may be deleted now, alongside runs, studies, and experiments.
 * - "Watch" (or the run's name) returns to the running view of a saved
 *   run this window executes, after its document has gone back to Home.
 * - "Kill" stops an executing saved run at once, without the flush that
 *   Cancel waits for, and records it as Failed; Delete then removes it.
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
    ["watch", "Watch"],
    ["start", "Start"],
    ["resume", "Resume"],
    ["resurrect", "Resurrect"],
    ["clone", "Clone"],
    ["edit", "Edit"],
    ["open", "Open"],
    ["cancel", "Cancel"],
    ["kill", "Kill"],
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
            if (state.activeSavedRunId !== null) {
                const documentState = window.fim.workspace.get(tabId);
                if (documentState) {
                    documentState.runObjectId = state.activeSavedRunId;
                    documentState.runObjectState = "finished";
                }
            }
            state.activeSavedRunId = null;
            state.pausePending = false;
        }
    }
    renderPauseRunButton();
    window.fim.refreshRunButton?.();
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
 * Describe a saved run's settings in a few words, such as
 * "225 diploid, d=10, m=0.01, mu=1e-05, 200 replicates".
 * @param {Record<string, string> | null | undefined} summary The row's
 *     `configSummary`.
 * @returns {string} The description, or "" for a Draft.
 */
function savedRunSettingsText(summary) {
    if (!summary) {
        return "";
    }
    const parts = [summary.N, `d=${summary.d}`, `m=${summary.m}`, `mu=${summary.mu}`];
    if (summary.replicates && summary.replicates !== "1") {
        parts.push(`${summary.replicates} replicates`);
    }
    return parts.join(", ");
}

/**
 * List every summarized setting, as Home's completed runs show them.
 * @param {Record<string, string>} summary The row's `configSummary`.
 * @returns {string} "N=… d=… … seed=…".
 */
function formatSavedRunSummary(summary) {
    return Object.entries(summary)
        .map(([key, value]) => `${key}=${value}`)
        .join(" ");
}

/**
 * Name a saved run for people: its name, else its settings, and its
 * object id only for a Draft that has neither.
 * @param {object} run A row from `Api.list_run_objects`.
 * @returns {string} The name to show.
 */
function savedRunDisplayName(run) {
    return run.name || savedRunSettingsText(run.configSummary) || run.objectId;
}

/**
 * Show a timestamp in this computer's local time, to the second.
 * @param {string} timestamp An ISO 8601 UTC timestamp.
 * @returns {string} The local time, or `timestamp` unchanged if unreadable.
 */
function formatSavedRunTime(timestamp) {
    const date = new Date(timestamp);
    if (Number.isNaN(date.getTime())) {
        return timestamp;
    }
    return date.toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "medium",
    });
}

// Saved runs chosen with Home's Select, by object id. Only a run that may
// be deleted now gets a checkbox; a reread drops any that no longer may.
const selectedSavedRunIds = new Set();
let deletableSavedRunIds = [];

/**
 * Build a row's checkbox for Home's Select, Select all, and Delete selected.
 * @param {object} run A row from `Api.list_run_objects`.
 * @returns {HTMLInputElement} The checkbox.
 */
function buildSavedRunCheckbox(run) {
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "open-run-select-checkbox saved-run-select-checkbox";
    checkbox.dataset.objectId = run.objectId;
    checkbox.checked = selectedSavedRunIds.has(run.objectId);
    checkbox.setAttribute(
        "aria-label",
        `Select ${savedRunDisplayName(run)} for deletion`
    );
    checkbox.addEventListener("change", () => {
        if (checkbox.checked) {
            selectedSavedRunIds.add(run.objectId);
        } else {
            selectedSavedRunIds.delete(run.objectId);
        }
        window.fim.updateSelectionToolbar?.();
    });
    return checkbox;
}

/** Make every saved-run checkbox show the current selection. */
function renderSavedRunSelection() {
    for (const checkbox of savedRunsBody.querySelectorAll(".saved-run-select-checkbox")) {
        checkbox.checked = selectedSavedRunIds.has(checkbox.dataset.objectId);
    }
}

// Home's selection toolbar (`open-run.js`) reads and changes this one.
window.fim.savedRunSelection = {
    size: () => selectedSavedRunIds.size,
    ids: () => [...selectedSavedRunIds],
    selectAll() {
        for (const objectId of deletableSavedRunIds) {
            selectedSavedRunIds.add(objectId);
        }
        renderSavedRunSelection();
    },
    clear() {
        selectedSavedRunIds.clear();
        renderSavedRunSelection();
    },
    setSelecting(selecting) {
        savedRunsTable.classList.toggle("open-run-selecting", selecting);
    },
};

/**
 * Build one table row.
 * @param {object} run A row from `Api.list_run_objects`.
 * @returns {HTMLTableRowElement} The row.
 */
function buildSavedRunRow(run) {
    const row = document.createElement("tr");
    row.dataset.objectId = run.objectId;
    const name = document.createElement("td");
    if (run.actions.delete) {
        name.append(buildSavedRunCheckbox(run));
    }
    if (run.actions.watch) {
        const link = document.createElement("a");
        link.href = "#";
        link.textContent = savedRunDisplayName(run);
        link.addEventListener("click", (event) => {
            event.preventDefault();
            onSavedRunAction("watch", run);
        });
        name.append(link);
    } else {
        name.append(savedRunDisplayName(run));
    }
    // The identifiers and full settings are for telling runs apart or
    // finding their files, not for reading at a glance.
    name.title = [
        run.description,
        run.configSummary ? formatSavedRunSummary(run.configSummary) : "",
        `Saved run: ${run.objectId}`,
        run.scientificRunId ? `Run ID: ${run.scientificRunId}` : "",
    ]
        .filter((line) => line)
        .join("\n");
    const state = document.createElement("td");
    state.textContent = SAVED_RUN_STATE_TEXT[run.state] || run.state;
    const problems = run.error ? [run.error] : run.issues;
    if (problems.length > 0) {
        state.title = problems.join("\n");
    } else if (run.executingElsewhere) {
        state.title =
            "Running in another window or command; watch it there";
    }
    const generation = document.createElement("td");
    generation.textContent = run.generation == null ? "—" : String(run.generation);
    const updated = document.createElement("td");
    updated.textContent = formatSavedRunTime(run.updatedAt);
    updated.title = run.updatedAt;
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

// States a worker is still changing. While any row shows one, Home
// rereads the table so "Cancelling" becomes "Cancelled" without a visit
// elsewhere and back.
const SAVED_RUN_UNSETTLED_STATES = new Set([
    "starting",
    "running",
    "pausing",
    "cancelling",
]);

// Milliseconds between rereads. A property, not a constant, so a test
// can shorten it.
window.fim.savedRunsPollMilliseconds = 3000;

let savedRunsPollTimer = null;

function homeIsShowing() {
    const home = document.getElementById("screen-open-run");
    return home !== null && !home.hidden;
}

/**
 * Reread the table later while a row is unsettled and Home is showing.
 * @param {object[]} runs The rows just shown.
 */
function scheduleSavedRunsPoll(runs) {
    clearTimeout(savedRunsPollTimer);
    savedRunsPollTimer = null;
    const unsettled = runs.some((run) => SAVED_RUN_UNSETTLED_STATES.has(run.state));
    if (!unsettled || !homeIsShowing()) {
        return;
    }
    savedRunsPollTimer = setTimeout(() => {
        savedRunsPollTimer = null;
        if (homeIsShowing()) {
            refreshSavedRuns();
        }
    }, window.fim.savedRunsPollMilliseconds);
}

/** Reload the Saved runs table from disk. */
/**
 * Find the document that started a saved run, while it is still open.
 * @param {string} objectId The saved run.
 * @returns {string | null} Its tab id, or `null`.
 */
function savedRunTabFor(objectId) {
    const workspace = window.fim.workspace;
    return (
        workspace.ids().find((tabId) => workspace.get(tabId)?.runObjectId === objectId) ??
        null
    );
}

/**
 * Return to the running view of a saved run this window is executing:
 * select its document and show its Run screen, which leaving for Home
 * kept running.
 * @param {object} run The row's saved run.
 */
function watchSavedRun(run) {
    const tabId = savedRunTabFor(run.objectId);
    if (tabId === null) {
        showSavedRunsBanner(
            `${savedRunDisplayName(run)} is no longer open in this window.`
        );
        return;
    }
    const documentState = window.fim.workspace.get(tabId);
    documentState.label = savedRunDisplayName(run);
    documentState.savedRunConfigSummary = run.configSummary;
    window.fim.workspace.select(tabId);
    window.fim.workspace.notifyChange();
    window.fim.showScreen("screen-run");
}

async function refreshSavedRuns() {
    const listing = await window.pywebview.api.list_run_objects();
    if (!listing.ok) {
        showSavedRunsBanner(listing.message);
        return;
    }
    const ownedHere = new Set(listing.activeObjectIds || []);
    const unfinished = listing.runs
        .filter((run) => run.state !== "completed")
        .map((run) => {
            const executing = SAVED_RUN_UNSETTLED_STATES.has(run.state);
            const watchable =
                executing &&
                ownedHere.has(run.objectId) &&
                savedRunTabFor(run.objectId) !== null;
            return {
                ...run,
                actions: { ...run.actions, watch: watchable },
                executingElsewhere: executing && !ownedHere.has(run.objectId),
            };
        });
    deletableSavedRunIds = unfinished
        .filter((run) => run.actions.delete)
        .map((run) => run.objectId);
    const deletable = new Set(deletableSavedRunIds);
    for (const objectId of [...selectedSavedRunIds]) {
        if (!deletable.has(objectId)) {
            selectedSavedRunIds.delete(objectId);
        }
    }
    savedRunsBody.replaceChildren(...unfinished.map(buildSavedRunRow));
    window.fim.updateSelectionToolbar?.();
    savedRunsTable.hidden = unfinished.length === 0;
    savedRunsEmpty.hidden = unfinished.length > 0;
    if (listing.unreadable.length > 0) {
        showSavedRunsBanner(`Some saved runs cannot be read: ${listing.unreadable.join("; ")}`);
    }
    scheduleSavedRunsPoll(unfinished);
}

window.fim.refreshSavedRuns = refreshSavedRuns;

/**
 * Carry out one row action.
 * @param {string} action One of `SAVED_RUN_ACTIONS`' keys.
 * @param {object} run The row's saved run.
 */
async function onSavedRunAction(action, run) {
    showSavedRunsBanner("");
    if (action === "watch") {
        watchSavedRun(run);
        return;
    }
    if (action === "edit") {
        await editSavedRun(run.objectId);
        return;
    }
    if (["start", "resume", "resurrect"].includes(action)) {
        await startSavedRun(run);
        return;
    }
    if (action === "open") {
        await window.fim.openComputedRun(
            run.directory,
            false,
            null,
            run.resourceKey
        );
        return;
    }
    const label = savedRunDisplayName(run);
    let result;
    if (action === "clone") {
        result = await window.pywebview.api.clone_run_object(run.objectId);
        if (result.ok) {
            await refreshSavedRuns();
            await editSavedRun(result.objectId);
            return;
        }
    } else if (action === "cancel") {
        result = await window.pywebview.api.cancel_run_object(run.objectId);
    } else if (action === "kill") {
        if (!window.confirm(
            `Kill "${label}" now? It stops at once, without saving anything ` +
            "since its last checkpoint."
        )) {
            return;
        }
        result = await window.pywebview.api.kill_run_object(run.objectId);
        if (
            result.ok &&
            window.confirm(`"${label}" was killed. Delete it and all of its data too?`)
        ) {
            result = await window.pywebview.api.delete_run_object(run.objectId);
            if (result.ok) {
                clearEditingObject(run.objectId);
            }
        }
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
    let tabId = window.fim.workspace.selectedId();
    const tabsEnabled = window.fim.workspace.documentTabsEnabled();
    const opened = await window.pywebview.api.open_run_object(objectId);
    if (!opened.ok) {
        showSavedRunsBanner(opened.message);
        return;
    }
    if (
        !tabsEnabled &&
        window.fim.workspace.selectedId() !== tabId
    ) {
        return;
    }
    if (tabsEnabled) {
        if (opened.editable) {
            const resource = window.fim.workspace.openResource(
                opened.resourceKey || `saved-run:${objectId}`,
                {
                    kind: "saved-run",
                    label: savedRunDisplayName(opened),
                    formValues: opened.values,
                    readOnly: false,
                }
            );
            tabId = resource.tabId;
            if (!resource.created) {
                activateWorkspaceDocument(tabId);
                return;
            }
        } else {
            tabId = window.fim.workspace.createDocument({
                kind: "draft",
                label: `${savedRunDisplayName(opened)} copy`,
            });
        }
    }
    applyFormValues(opened.values);
    window.fim.applyLoadedRunSettings(opened);
    const state = savedRunStateFor(tabId);
    if (state) {
        state.editingObjectId = opened.editable ? objectId : null;
    }
    const documentState = window.fim.workspace.get(tabId);
    if (documentState && opened.resourceKey && opened.editable) {
        documentState.resourceKey = opened.resourceKey;
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
 * Confirm recomputing a copy whose current settings still match its source.
 * @param {object|null} source Current source display and checkpoint information.
 * @returns {boolean} Whether starting may proceed.
 */
function confirmIdenticalSource(source) {
    if (!source) {
        return true;
    }
    let message = `This run is identical to "${savedRunDisplayName(source)}", ` +
        "so it will recompute exactly the same results from generation 0.";
    if (
        source.hasCheckpoint &&
        ["cancelled", "failed", "paused", "interrupted"].includes(source.state)
    ) {
        message += " Continuing the original keeps its progress; " +
            "use Resume on the original when available instead of starting a copy.";
    }
    return window.confirm(`${message}\n\nStart anyway?`);
}

/**
 * Offer to continue a planned run from a stopped run with its settings.
 *
 * Declining asks whether to start from generation 0 instead; when the
 * stopped run is also this copy's source, that question is the
 * identical-source warning, which the caller then shows.
 * @param {object} run The row's saved run, still planned.
 * @param {object} checked `get_run_object_start_source`'s result.
 * @returns {Promise<boolean|null>} True once the checkpoint is copied,
 *     false to start from generation 0, or null to start nothing.
 */
async function continueFromStoppedRun(run, checked) {
    const candidate = checked.continueFrom;
    if (!candidate) {
        return false;
    }
    const generation = candidate.committedGeneration;
    const accepted = window.confirm(
        `"${savedRunDisplayName(candidate)}" has the same settings and ` +
        `stopped at generation ${generation}.\n\n` +
        `Continue from generation ${generation}? ` +
        "Cancel to choose whether to start from generation 0 instead."
    );
    if (!accepted) {
        const sameSource = checked.identicalToSource?.objectId === candidate.objectId;
        return sameSource || window.confirm("Start from generation 0?") ? false : null;
    }
    const adopted = await window.pywebview.api.adopt_run_object_checkpoint(
        run.objectId,
        candidate.objectId
    );
    if (!adopted.ok) {
        showSavedRunsBanner(adopted.message);
        return null;
    }
    return true;
}

/**
 * Check current form settings, not the last saved digest, before Run.
 * @param {string} tabId The edited document.
 * @param {object} values The current configuration form.
 * @returns {Promise<boolean>} Whether starting may proceed.
 */
window.fim.confirmConfiguredCloneStart = async function confirmConfiguredCloneStart(
    tabId,
    values
) {
    const objectId = savedRunStateFor(tabId)?.editingObjectId;
    if (!objectId) {
        return true;
    }
    const checked = await window.pywebview.api.get_run_object_start_source(objectId, values);
    if (!checked.ok) {
        window.fim.showRunBanner(checked.message);
        return false;
    }
    return confirmIdenticalSource(checked.identicalToSource);
};

/**
 * Start a planned saved run, or resume a paused or stopped one, in the
 * same running view "Run simulation" uses. While another run or a sweep
 * is in flight, only says so here on Home, and leaves the running view
 * alone (`startRunExclusively`, `run-view-controls.js`).
 * @param {object} run The row's saved run.
 */
async function startSavedRun(run) {
    // Home may still show an older row while its refresh is in flight.
    const checked = await window.pywebview.api.get_run_object_start_source(run.objectId);
    if (!checked.ok) {
        showSavedRunsBanner(checked.message);
        return;
    }
    const continued = await continueFromStoppedRun(run, checked);
    if (continued === null) {
        return;
    }
    if (!continued && !confirmIdenticalSource(checked.identicalToSource)) {
        return;
    }
    let startTabId = window.fim.workspace.selectedId();
    let ownerTabId = null;
    if (window.fim.workspace.documentTabsEnabled()) {
        const opened = window.fim.workspace.openResource(
            run.resourceKey || `saved-run:${run.objectId}`,
            {
                kind: "saved-run",
                label: savedRunDisplayName(run),
                savedRunConfigSummary: run.configSummary,
                readOnly: false,
            }
        );
        ownerTabId = opened.tabId;
        startTabId = opened.tabId;
        window.fim.showScreen("screen-run");
    }
    await window.fim.startRunExclusively(
        () => beginSavedRun(run, startTabId),
        (message) => {
            if (ownerTabId !== null) {
                const state = window.fim.workspace.get(ownerTabId);
                if (state) {
                    state.startRefusal =
                        `${savedRunDisplayName(run)}: ${message}`;
                    window.fim.workspace.notifyChange();
                    if (window.fim.workspace.selectedId() === ownerTabId) {
                        window.fim.showScreen("screen-run");
                        showRunBanner(state.startRefusal);
                    }
                    if (state.closeAfterStart) {
                        state.closeAfterStart = false;
                        window.fim.requestCloseDocument(ownerTabId);
                    }
                }
            } else {
                showSavedRunsBanner(message);
            }
        },
        { tabId: startTabId, jobKind: "saved-run" }
    );
}

/**
 * Associate a running or finished Run object with its workspace document.
 * @param {string} tabId The owning workspace document.
 * @param {string} objectId The Run object identifier.
 */
window.fim.activateRunObjectForDocument = function activateRunObjectForDocument(
    tabId,
    objectId
) {
    const state = savedRunStateFor(tabId);
    const documentState = window.fim.workspace.get(tabId);
    if (!state || !documentState) {
        return;
    }
    state.activeSavedRunId = objectId;
    documentState.runObjectId = objectId;
    if (
        documentState.jobState !== "finished" &&
        documentState.jobState !== "terminal"
    ) {
        documentState.runObjectState = "running";
    } else {
        documentState.runObjectState = "finished";
    }
    renderPauseRunButton();
    window.fim.refreshRunButton?.();
};

window.fim.resumeRunObjectFromTab = async function resumeRunObjectFromTab(tabId) {
    const documentState = window.fim.workspace.get(tabId);
    const objectId = documentState?.runObjectId;
    if (!documentState || !objectId) {
        return;
    }
    const run = {
        objectId,
        name: documentState.runTitle?.state?.runName || documentState.label,
        configSummary: documentState.savedRunConfigSummary,
    };
    await window.fim.startRunExclusively(
        () => beginSavedRun(run, tabId),
        (message) => {
            documentState.startRefusal = `${savedRunDisplayName(run)}: ${message}`;
            window.fim.workspace.notifyChange();
            if (window.fim.workspace.selectedId() === tabId) {
                window.fim.showRunBanner(documentState.startRefusal);
            }
        },
        { tabId, jobKind: "saved-run" }
    );
};

/**
 * Show the running view and start `run`, once `startSavedRun` knows that
 * no other run is starting or in flight.
 * @param {object} run The row's saved run.
 */
async function beginSavedRun(run, tabId = window.fim.workspace.selectedId()) {
    const selected = window.fim.workspace.selectedId() === tabId;
    if (selected) {
        window.fim.showScreen("screen-run");
        window.fim.showRunBanner("");
    }
    window.fim.enterRunningState(false, tabId);
    window.fim.setRunCardTitle(
        { phase: "running", runName: run.name || null },
        tabId
    );
    const state = savedRunStateFor(tabId);
    const documentState = window.fim.workspace.get(tabId);
    if (state) {
        state.activeSavedRunId = run.objectId;
    }
    if (documentState) {
        documentState.jobKind = "saved-run";
        documentState.jobState = "starting";
        documentState.runObjectId = run.objectId;
        documentState.runObjectState = "running";
        documentState.label = savedRunDisplayName(run);
        documentState.savedRunConfigSummary = run.configSummary;
        window.fim.workspace.notifyChange();
    }
    if (selected) {
        renderPauseRunButton();
    }
    const started = await window.fim.jobs.startWithMemoryConfirmation(
        "start_run_object",
        [run.objectId]
    );
    if (!started.ok) {
        if (state) {
            state.activeSavedRunId = null;
        }
        if (window.fim.workspace.selectedId() === tabId) {
            renderPauseRunButton();
            window.fim.enterInitialState();
        }
        if (documentState) {
            documentState.jobKind = null;
            documentState.jobState = null;
            documentState.startRefusal =
                `${savedRunDisplayName(run)}: ${started.message}`;
            window.fim.workspace.notifyChange();
        }
        if (window.fim.workspace.selectedId() === tabId) {
            window.fim.showRunBanner(
                documentState?.startRefusal || started.message
            );
        }
        if (documentState?.closeAfterStart) {
            documentState.closeAfterStart = false;
            window.fim.requestCloseDocument(tabId);
        }
        return;
    }
    if (documentState && started.jobId) {
        documentState.jobState = "running";
        window.fim.workspace.attachJob(
            tabId,
            started.jobId,
            started.priority
        );
    }
    if (documentState && started.resourceKey) {
        window.fim.workspace.setResourceKey(tabId, started.resourceKey);
    }
    if (documentState?.closeAfterStart) {
        documentState.closeAfterStart = false;
        window.fim.requestCloseDocument(tabId);
    }
    if (window.fim.workspace.selectedId() !== tabId) {
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
        if (saved.resourceKey) {
            window.fim.workspace.setResourceKey(tabId, saved.resourceKey);
            const documentState = window.fim.workspace.get(tabId);
            if (documentState) {
                documentState.kind = "saved-run";
                documentState.label = savedRunDisplayName({ ...saved, name });
                documentState.savedRunConfigSummary = saved.configSummary;
            }
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
    const paused = await window.pywebview.api.pause_run(state.attachedJobId);
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
    for (const tabId of window.fim.workspace.ids()) {
        const state = savedRunStateFor(tabId);
        const documentState = window.fim.workspace.get(tabId);
        if (state?.activeSavedRunId && documentState) {
            documentState.runObjectId = state.activeSavedRunId;
            documentState.runObjectState = "paused";
            state.activeSavedRunId = null;
            state.pausePending = false;
            break;
        }
    }
    endActiveSavedRun();
    cancelButton.disabled = true;
    window.fim.showRunBanner(
        `Paused at generation ${generation}. Resume it with the Run button.`
    );
    window.fim.refreshRunButton?.();
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
