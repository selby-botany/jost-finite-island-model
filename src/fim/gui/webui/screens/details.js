"use strict";

/* What an Experiment, Study, or Run is, and why: the description tooltip
 * every displayed name carries, and the details dialog that shows and
 * edits it.
 *
 * Experiments and Studies have a name, a one-line description, and
 * longer free-form documentation (their question, rationale, findings);
 * a Run has a name and a description (`fim.persistence.groups`,
 * `fim.persistence.run_metadata`). Wherever one of these names is shown
 * (Home's tree, the Run card's title, Configure's study choice, a sweep's
 * title), hovering it shows the description, and clicking it -- or its
 * "ⓘ" button -- opens the details dialog.
 *
 * Every page script shares one global scope (`index.html` loads them as
 * classic scripts), so this file's top-level names all start with
 * `details` to stay clear of every other file's own.
 *
 * Saving dispatches a `fim:details-saved` event on `window`, carrying the
 * saved details, so each screen showing a name refreshes itself without
 * this file knowing which screens exist.
 */

const detailsDialog = document.getElementById("modal-details");
const detailsForm = document.getElementById("details-form");
const detailsTitle = document.getElementById("details-title");
const detailsIdentity = document.getElementById("details-identity");
const detailsNameInput = document.getElementById("details-name");
const detailsDescriptionInput = document.getElementById("details-description");
const detailsDocumentationField = document.getElementById(
    "details-documentation-field"
);
const detailsDocumentationInput = document.getElementById("details-documentation");
const detailsError = document.getElementById("details-error");
const detailsCancelButton = document.getElementById("details-cancel-button");

// The kind's display word, for the dialog title and the tooltip.
const DETAILS_KIND_LABELS = {
    experiment: "Experiment",
    study: "Study",
    run: "Run",
};

// What the dialog is currently editing: `{kind, id, directoryName?}`, or
// `null` while it is closed.
let detailsEditing = null;

/**
 * The tooltip text for one named thing: its kind and name, then its
 * description, or a nudge to add one.
 * @param {{kind: string, name: string|null, description: string|null,
 *     documentation?: string|null, directoryName?: string}} details
 * @returns {string}
 */
function detailsTooltipText(details) {
    const kind = DETAILS_KIND_LABELS[details.kind] ?? "Item";
    const name = details.name || details.directoryName || "(unnamed)";
    const description = details.description
        ? details.description
        : "No description yet. Click for details to add one.";
    const documented = details.documentation ? "\n(Has documentation; click to read.)" : "";
    return `${kind}: ${name}\n${description}${documented}`;
}

window.fim.detailsTooltipText = detailsTooltipText;

/**
 * Give `element` a description tooltip, and make clicking it open the
 * details dialog. The click does not propagate, so a name inside a
 * clickable row (Home's tree) opens the dialog rather than the row.
 * Safe to call again on the same element (a sweep's title, reused for
 * each Study shown): the latest `details` wins and only one click
 * listener is ever added. `null` details detaches it.
 * @param {HTMLElement} element
 * @param {object|null} details See `detailsTooltipText`; also `id`.
 */
window.fim.attachDetails = function attachDetails(element, details) {
    element._fimDetails = details;
    if (details === null) {
        element.removeAttribute("title");
        element.classList.remove("details-link");
        return;
    }
    element.title = detailsTooltipText(details);
    element.classList.add("details-link");
    if (element._fimDetailsWired) {
        return;
    }
    element._fimDetailsWired = true;
    element.addEventListener("click", (event) => {
        const current = element._fimDetails;
        if (!current) {
            return;
        }
        event.stopPropagation();
        window.fim.openDetailsDialog(current.kind, current.id);
    });
};

/**
 * A small "ⓘ" button opening the details dialog, carrying the same
 * description tooltip -- for a name whose own click already means
 * something else (a tree row's expand toggle).
 * @param {object} details See `window.fim.attachDetails`.
 * @returns {HTMLButtonElement}
 */
window.fim.buildDetailsButton = function buildDetailsButton(details) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "details-button";
    button.textContent = "ⓘ";
    const name = details.name || details.directoryName || "";
    button.setAttribute("aria-label", `Details for ${name}`);
    button.title = detailsTooltipText(details);
    button.addEventListener("click", (event) => {
        event.stopPropagation();
        window.fim.openDetailsDialog(details.kind, details.id);
    });
    return button;
};

/**
 * Open the details dialog for one Experiment, Study, or Run, reading its
 * current details fresh from disk rather than trusting the caller's copy
 * (another window, or a sweep, may have changed it since).
 * @param {"experiment"|"study"|"run"} kind
 * @param {string} id The Experiment/Study id, or the run's directory.
 */
window.fim.openDetailsDialog = async function openDetailsDialog(kind, id) {
    window.__fimDetailsDialogReady = false;
    const result = await window.pywebview.api.get_details(kind, id);
    if (!result.ok) {
        detailsEditing = null;
        detailsShowFields({ kind, name: "", description: "", documentation: "" });
        detailsTitle.textContent = DETAILS_KIND_LABELS[kind] ?? "Details";
        detailsShowError(result.message);
    } else {
        detailsEditing = result.details;
        detailsShowFields(result.details);
        detailsError.hidden = true;
    }
    if (!detailsDialog.open) {
        detailsDialog.showModal();
    }
    detailsNameInput.focus();
    window.__fimDetailsDialogReady = true;
};

/**
 * Fill the dialog's fields from `details`. A Run has no documentation
 * field, and its name may be blank (its directory name stands in).
 * @param {object} details
 */
function detailsShowFields(details) {
    const isRun = details.kind === "run";
    const kindLabel = DETAILS_KIND_LABELS[details.kind] ?? "Details";
    detailsTitle.textContent = details.name ? `${kindLabel}: ${details.name}` : kindLabel;
    detailsIdentity.textContent = isRun
        ? `Folder: ${details.directoryName ?? ""}`
        : `${kindLabel} id: ${details.id ?? ""}`;
    detailsNameInput.value = details.name ?? "";
    detailsNameInput.required = !isRun;
    detailsNameInput.placeholder = isRun ? "Optional; the folder name is used if blank" : "";
    detailsDescriptionInput.value = details.description ?? "";
    detailsDocumentationField.hidden = isRun;
    detailsDocumentationInput.value = details.documentation ?? "";
}

/**
 * Show an error line in the dialog.
 * @param {string} message
 */
function detailsShowError(message) {
    detailsError.textContent = message;
    detailsError.hidden = false;
}

/**
 * Save the dialog's fields through the bridge method for its kind.
 * @returns {Promise<object>} The bridge result.
 */
async function detailsSave() {
    const name = detailsNameInput.value;
    const description = detailsDescriptionInput.value;
    const documentation = detailsDocumentationInput.value;
    const api = window.pywebview.api;
    if (detailsEditing.kind === "experiment") {
        return api.update_experiment_details(
            detailsEditing.id,
            name,
            description,
            documentation
        );
    }
    if (detailsEditing.kind === "study") {
        return api.update_study_details(detailsEditing.id, name, description, documentation);
    }
    return api.update_run_details(detailsEditing.id, name, description);
}

// Not `wireModal` (app.js): its `Return` handler closes the dialog,
// which would discard an edit and break typing a new line in the
// documentation field. Backdrop click and Cancel both discard; Escape is
// the browser's own.
detailsDialog.addEventListener("click", (event) => {
    if (event.target === detailsDialog) {
        detailsDialog.close("cancel");
    }
});

detailsCancelButton.addEventListener("click", () => {
    detailsDialog.close("cancel");
});

detailsDialog.addEventListener("close", () => {
    detailsEditing = null;
});

// `method="dialog"` would close on submit; `preventDefault` keeps the
// dialog open until the save actually succeeds, so an error can show.
detailsForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (detailsEditing === null) {
        detailsDialog.close("cancel");
        return;
    }
    window.__fimDetailsSaved = false;
    const result = await detailsSave();
    if (!result.ok) {
        detailsShowError(result.message);
        return;
    }
    detailsDialog.close("save");
    window.dispatchEvent(new CustomEvent("fim:details-saved", { detail: result.details }));
    window.__fimDetailsSaved = true;
});
