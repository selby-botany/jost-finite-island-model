"use strict";

/* Close guard: offer to save before the window closes.
 *
 * Two jobs:
 *
 * 1. Know whether the Configure form or sweep setup has changes that are
 *    not saved. The form counts as saved after it is loaded (the initial
 *    form, a preset, an example, a saved run, a file), after "Save for
 *    later", after a run starts from it, and after "Save configuration…".
 *    Sweep setup is tracked separately because saved runs do not keep it.
 *    The page tells Python (`Api.report_unsaved_edits`) whenever the answer
 *    changes. Python, not the page, decides whether a close needs the
 *    dialog, so a page that fails to load can never trap the window.
 * 2. Show the dialog when Python vetoes a close (`Api.on_window_closing`)
 *    and carry out the answer: save the form as a saved run (a Draft when
 *    it is not valid yet, Planned when it is ready to run), then
 *    `Api.finish_close`, which pauses a saved run or stops other work and
 *    closes the window.
 */

const closeDialog = document.getElementById("modal-confirm-close");
const closeReasons = document.getElementById("confirm-close-reasons");
const closeError = document.getElementById("confirm-close-error");
const closeKeepButton = document.getElementById("confirm-close-keep-button");
const closeDiscardButton = document.getElementById("confirm-close-discard-button");
const closePrimaryButton = document.getElementById("confirm-close-primary-button");
const closeRunNameInput = document.getElementById("run-name-input");
const closeRunDescriptionInput = document.getElementById("run-description-input");

// What Python was last told, so a bridge call is made only on a change.
let reportedUnsavedEdits = false;

// The words for each thing a close would affect, in the order shown.
const CLOSE_REASONS = [
    [
        "unsavedEdits",
        "Your configuration has changes that are not saved. Saving keeps it " +
            "under Saved runs on Home: as a Draft if it is not complete yet, " +
            "or as Planned if it is ready to run.",
    ],
    [
        "savedRun",
        "A saved run is in progress. It pauses at a checkpoint, and you can " +
            "resume it from Saved runs on Home.",
    ],
    ["run", "A run is in progress. It cannot be resumed, so closing stops it."],
    [
        "sweep",
        "A sweep is in progress. Closing stops it. You can continue it later " +
            "from its Study.",
    ],
];

/**
 * One document's form and controls as a comparable string.
 * @param {string} tabId
 * @returns {string} A snapshot of what "Save for later" keeps.
 */
function formSnapshot(tabId) {
    const state = window.fim.workspace.get(tabId);
    if (!state) {
        return "";
    }
    const controls = runControlsState(tabId) || {};
    const values =
        state.formValues ||
        (window.fim.workspace.selectedId() === tabId
            ? collectFormValues(tabId)
            : {});
    return JSON.stringify([
        values,
        controls.runName || "",
        controls.runDescription || "",
        controls.studySelection || "",
        controls.newStudyName || "",
        controls.newStudyDescription || "",
        controls.newStudyExperimentId || "",
    ]);
}

/** Snapshot sweep setup, which is not stored with a saved run. */
function sweepSnapshot(tabId) {
    return JSON.stringify(
        window.fim.getSweepDocumentSnapshot
            ? window.fim.getSweepDocumentSnapshot(tabId)
            : null
    );
}

/** Recompute document dirty state and tell Python when the aggregate flips. */
function reportUnsavedEdits() {
    let unsaved = false;
    let changed = false;
    for (const tabId of window.fim.workspace.ids()) {
        const state = window.fim.workspace.get(tabId);
        const wasDirty = state.formDirty === true;
        if (state.cleanFormSnapshot === null || state.cleanFormSnapshot === undefined) {
            state.formDirty = false;
            changed ||= wasDirty;
            continue;
        }
        state.formDirty =
            formSnapshot(tabId) !== state.cleanFormSnapshot ||
            sweepSnapshot(tabId) !== state.cleanSweepSnapshot;
        changed ||= wasDirty !== state.formDirty;
        unsaved ||= state.formDirty;
    }
    if (changed) {
        window.fim.workspace.notifyChange();
    }
    if (unsaved === reportedUnsavedEdits) {
        return;
    }
    reportedUnsavedEdits = unsaved;
    window.pywebview.api.report_unsaved_edits(unsaved).catch(() => {
        // Closing, or the bridge is gone: nothing left to protect.
    });
}

/** Treat one document as saved, as it is right now. */
window.fim.markFormClean = function markFormClean(
    tabId = window.fim.workspace.selectedId()
) {
    if (tabId === null || !window.fim.workspace.get(tabId)) {
        return;
    }
    if (window.fim.workspace.selectedId() === tabId) {
        collectFormValues(tabId);
    }
    const state = window.fim.workspace.get(tabId);
    state.cleanFormSnapshot = formSnapshot(tabId);
    if (state.cleanSweepSnapshot === undefined) {
        state.cleanSweepSnapshot = sweepSnapshot(tabId);
    }
    state.formDirty = false;
    reportUnsavedEdits();
    window.fim.workspace.notifyChange();
    // For the tests (and nothing else): the first snapshot exists.
    if (window.fim.workspace.selectedId() === tabId) {
        window.__fimCloseGuardReady = true;
    }
};

/**
 * `applyFormValues` loaded a form. Its run name and description are set
 * by the caller a moment later, so the snapshot waits for the revalidation
 * every load ends with, then for the tick after it.
 */
window.fim.expectLoadedForm = function expectLoadedForm(
    tabId = window.fim.workspace.selectedId()
) {
    const state = tabId === null ? null : window.fim.workspace.get(tabId);
    if (state) {
        state.loadedFormPending = true;
    }
};

/** `revalidate` finished: take a pending snapshot, or look for edits. */
window.fim.formRevalidated = function formRevalidated(
    tabId = window.fim.workspace.selectedId()
) {
    const state = tabId === null ? null : window.fim.workspace.get(tabId);
    if (!state) {
        return;
    }
    if (state.loadedFormPending) {
        state.loadedFormPending = false;
        setTimeout(() => window.fim.markFormClean(tabId), 0);
        return;
    }
    reportUnsavedEdits();
};

window.fim.refreshUnsavedEdits = reportUnsavedEdits;

/**
 * Show why `needs` makes closing worth a question, and the buttons for it.
 * @param {object} needs `Api.close_requirements`' answer.
 */
function describeClose(needs) {
    closeReasons.replaceChildren(
        ...CLOSE_REASONS.filter(([key]) => needs[key]).map(([, text]) => {
            const item = document.createElement("li");
            item.textContent = text;
            return item;
        })
    );
    closeDiscardButton.hidden = !needs.unsavedEdits;
    if (needs.unsavedEdits) {
        closePrimaryButton.textContent = "Save and close";
    } else {
        closePrimaryButton.textContent = needs.savedRun ? "Pause and close" : "Stop and close";
    }
    closeError.hidden = true;
    closeError.textContent = "";
}

/**
 * Show the close dialog. Python calls this after it vetoes a close.
 * Does nothing when the dialog is already open.
 */
window.fim.confirmClose = async function confirmClose() {
    if (closeDialog.open) {
        return;
    }
    const needs = await window.pywebview.api.close_requirements();
    if (!needs || !needs.ok) {
        // Cannot say what is at stake; do not trap the window.
        await window.pywebview.api.finish_close();
        return;
    }
    describeClose(needs);
    setCloseButtonsDisabled(false);
    closeDialog.showModal();
    closePrimaryButton.focus();
};

/**
 * @param {boolean} disabled Whether the three buttons ignore clicks.
 */
function setCloseButtonsDisabled(disabled) {
    closeKeepButton.disabled = disabled;
    closeDiscardButton.disabled = disabled;
    closePrimaryButton.disabled = disabled;
}

/**
 * Close the window now that the question is answered.
 */
async function finishClose() {
    const finished = await window.pywebview.api.finish_close();
    if (finished && finished.ok) {
        return;
    }
    closeError.textContent = (finished && finished.message) || "fim could not close.";
    closeError.hidden = false;
    setCloseButtonsDisabled(false);
}

/** "Save and close", "Pause and close", or "Stop and close". */
async function onClosePrimaryClicked() {
    setCloseButtonsDisabled(true);
    if (!closeDiscardButton.hidden) {
        const saved = await window.fim.saveFormForLater();
        if (!saved.ok) {
            // Stay open: closing now would lose the edits the user asked to keep.
            closeError.textContent = saved.message;
            closeError.hidden = false;
            setCloseButtonsDisabled(false);
            return;
        }
        const tabId = window.fim.workspace.selectedId();
        const state = tabId === null ? null : window.fim.workspace.get(tabId);
        if (state && state.formDirty) {
            closeError.textContent =
                "The configuration was saved, but the sweep setup is not " +
                "stored with saved runs. Close without saving to discard " +
                "the sweep setup, or keep working.";
            closeError.hidden = false;
            setCloseButtonsDisabled(false);
            closePrimaryButton.textContent = "Configuration saved";
            closePrimaryButton.disabled = true;
            return;
        }
    }
    await finishClose();
}

/** "Close without saving". */
async function onCloseDiscardClicked() {
    setCloseButtonsDisabled(true);
    await finishClose();
}

function wireCloseGuard() {
    closePrimaryButton.addEventListener("click", onClosePrimaryClicked);
    closeDiscardButton.addEventListener("click", onCloseDiscardClicked);
    closeKeepButton.addEventListener("click", () => closeDialog.close());
    const reportRelevantEdit = (event) => {
        const target = event.target;
        if (!(target instanceof Element)) {
            return;
        }
        if (
            target.form === document.getElementById("input-form") ||
            target === closeRunNameInput ||
            target === closeRunDescriptionInput ||
            target.closest("#run-details-row") ||
            target.closest("#modal-sweep")
        ) {
            reportUnsavedEdits();
        }
    };
    document.addEventListener("input", reportRelevantEdit);
    document.addEventListener("change", reportRelevantEdit);
    window.fim.workspace.onChange(reportUnsavedEdits);
}

whenApiReady(wireCloseGuard);
