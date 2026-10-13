"use strict";

/* Close guard: offer to save before the window closes.
 *
 * Two jobs:
 *
 * 1. Know whether the Configure form has changes that are not saved. The
 *    form counts as saved after it is loaded (the initial form, a preset,
 *    an example, a saved run, a file), after "Save for later", after a run
 *    starts from it, and after "Save configuration…". Anything else that
 *    makes the form differ from that snapshot is an unsaved edit, and the
 *    page tells Python (`Api.report_unsaved_edits`) whenever the answer
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

// The form as it was when it last counted as saved, or `null` until the
// first form has loaded (nothing can be unsaved before then).
let savedFormSnapshot = null;
// Set by `applyFormValues`: the next settled revalidation takes the snapshot.
let loadedFormPending = false;
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
 * The form and run labels as one comparable string.
 * @returns {string} A snapshot of everything "Save for later" would keep.
 */
function formSnapshot() {
    return JSON.stringify([
        collectFormValues(),
        closeRunNameInput.value.trim(),
        closeRunDescriptionInput.value.trim(),
    ]);
}

/** Tell Python when "has unsaved edits" flips. */
function reportUnsavedEdits() {
    if (savedFormSnapshot === null) {
        return;
    }
    const unsaved = formSnapshot() !== savedFormSnapshot;
    if (unsaved === reportedUnsavedEdits) {
        return;
    }
    reportedUnsavedEdits = unsaved;
    window.pywebview.api.report_unsaved_edits(unsaved).catch(() => {
        // Closing, or the bridge is gone: nothing left to protect.
    });
}

/** Treat the form as saved, as it is right now. */
window.fim.markFormClean = function markFormClean() {
    savedFormSnapshot = formSnapshot();
    reportUnsavedEdits();
    // For the tests (and nothing else): the first snapshot exists.
    window.__fimCloseGuardReady = true;
};

/**
 * `applyFormValues` loaded a form. Its run name and description are set
 * by the caller a moment later, so the snapshot waits for the revalidation
 * every load ends with, then for the tick after it.
 */
window.fim.expectLoadedForm = function expectLoadedForm() {
    loadedFormPending = true;
};

/** `revalidate` finished: take a pending snapshot, or look for edits. */
window.fim.formRevalidated = function formRevalidated() {
    if (loadedFormPending) {
        loadedFormPending = false;
        setTimeout(window.fim.markFormClean, 0);
        return;
    }
    reportUnsavedEdits();
};

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
    // The run name and description are part of what "Save for later" keeps.
    for (const input of [closeRunNameInput, closeRunDescriptionInput]) {
        input.addEventListener("input", reportUnsavedEdits);
    }
}

whenApiReady(wireCloseGuard);
