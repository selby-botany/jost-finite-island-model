"use strict";

/* The Examples dialog (design doc `20261005-claude-opus-5-5-read-only-
 * examples-and-classes-design.md` §5, `selby/restricted`): every bundled
 * example, grouped by class. The class tree is on the left; the selected
 * class's examples are on the right, each with its name and description;
 * the selected example's README excerpt is below them.
 *
 * "Load into Configure" calls `Api.load_example`, which strips the
 * configuration's labels and internal `_` keys, then applies the values
 * the same way a loaded YAML file is applied (`config-modals.js`'s own
 * `applyFormValues`) and puts the example's name and description into
 * Configure's "Run name" and "Run description" boxes.
 *
 * "Open saved result" (design §5) opens the example's seeded, read-only
 * run (`Api.open_saved_example`, then the same open a Home row uses,
 * which shows the saved statistics: design §4.3). It is disabled, with
 * the reason as its tooltip, for an example with no saved result yet.
 *
 * Keyboard: Up/Down (and Home/End) move within the tree or the list,
 * Right moves from the tree to the list, Left moves back (or from a
 * child class to its parent), Return loads the selected example, and
 * Escape closes the dialog (the native `<dialog>` behavior).
 *
 * Every page script shares one global scope (`index.html` loads them as
 * classic scripts), so this file's top-level names all start with
 * `examples`.
 */

const examplesDialog = document.getElementById("modal-examples");
const examplesClassTree = document.getElementById("examples-class-tree");
const examplesList = document.getElementById("examples-list");
const examplesEmptyNote = document.getElementById("examples-empty-note");
const examplesClassDescription = document.getElementById(
    "examples-class-description"
);
const examplesDetailName = document.getElementById("examples-detail-name");
const examplesDetailExcerpt = document.getElementById("examples-detail-excerpt");
const examplesLoadError = document.getElementById("examples-load-error");
const examplesLoadButton = document.getElementById("examples-load-button");
const examplesViewYamlButton = document.getElementById("examples-view-yaml-button");
const examplesOpenSavedButton = document.getElementById("examples-open-saved-button");
const examplesCancelButton = document.getElementById("examples-cancel-button");
const examplesRunNameInput = document.getElementById("run-name-input");
const examplesRunDescriptionInput = document.getElementById("run-description-input");

// `Api.list_examples`'s last result, and what is selected in it.
let examplesCatalog = { classes: [], examples: [] };
let examplesSelectedClassId = null;
let examplesSelectedExampleId = null;

// Set once the dialog is open and showing a freshly fetched catalog,
// and once a "Load into Configure" has finished (applied or refused) --
// the `window.__fimXReady` flag precedent (`__fimPresetsListReady`,
// `screens/presets.js`): a test polling only "is the dialog open" could
// otherwise read it before the async bridge call has settled.
window.__fimExamplesDialogReady = false;
window.__fimExampleLoadSettled = false;
window.__fimExampleOpenSettled = false;

// Why "Open saved result" is disabled for an example without one.
const EXAMPLES_NO_SAVED_RESULT_TEXT =
    "This example has no saved result yet. Load it into Configure and run it.";

/**
 * Every class in display order, each parent followed by its children.
 * @returns {Array<{id: string, title: string, description: string,
 *     level: number, parentId: string|null, childIds: string[]}>}
 */
function examplesFlatClasses() {
    const flat = [];
    for (const parent of examplesCatalog.classes) {
        const children = parent.children || [];
        flat.push({
            id: parent.id,
            title: parent.title,
            description: parent.description,
            level: 1,
            parentId: null,
            childIds: children.map((child) => child.id),
        });
        for (const child of children) {
            flat.push({
                id: child.id,
                title: child.title,
                description: child.description,
                level: 2,
                parentId: parent.id,
                childIds: [],
            });
        }
    }
    return flat;
}

/**
 * Focus `element` and make it the one tab stop in its widget (the
 * roving-tabindex pattern the tree and the list both use).
 * @param {HTMLElement} container
 * @param {HTMLElement} element
 */
function examplesFocusItem(container, element) {
    for (const item of container.children) {
        item.tabIndex = item === element ? 0 : -1;
    }
    element.focus();
}

/**
 * The examples shown for one class: its own, then its children's.
 * @param {string|null} classId
 * @returns {Array<object>}
 */
function examplesInClass(classId) {
    const entry = examplesFlatClasses().find((item) => item.id === classId);
    if (!entry) {
        return [];
    }
    const members = new Set([entry.id, ...entry.childIds]);
    return examplesCatalog.examples.filter((example) => members.has(example.class));
}

/**
 * Handle the keys the tree and the list share: Up/Down/Home/End move the
 * selection, Return loads. Returns whether the key was handled.
 * @param {KeyboardEvent} event
 * @param {HTMLElement} container
 * @param {(item: HTMLElement) => void} select
 * @returns {boolean}
 */
function examplesHandleListKey(event, container, select) {
    const items = Array.from(container.children);
    // The current item is the widget's one tab stop (roving tabindex),
    // which is the focused item whenever the widget has focus.
    const index = items.findIndex((item) => item.tabIndex === 0);
    let target = null;
    if (event.key === "ArrowDown") {
        target = items[Math.min(index + 1, items.length - 1)];
    } else if (event.key === "ArrowUp") {
        target = items[Math.max(index - 1, 0)];
    } else if (event.key === "Home") {
        target = items[0];
    } else if (event.key === "End") {
        target = items[items.length - 1];
    } else if (event.key === "Enter") {
        examplesLoadSelected();
        return true;
    } else {
        return false;
    }
    if (target) {
        select(target);
        examplesFocusItem(container, target);
    }
    return true;
}

/**
 * Put a loaded example into Configure as an editable copy: its values in
 * the form, its name and description in the "Run name" and "Run
 * description" boxes, and Configure showing. Shared by "Load into
 * Configure" here and "Run it" on a run opened from its saved results
 * (`run-view-completed.js`), which both receive `Api.load_example`'s
 * result shape.
 * @param {{values: object, name: string|null, description: string|null}} result
 * @param {string} fallbackName What to call the loaded configuration when
 *     it has no name of its own.
 */
window.fim.applyLoadedExample = async function applyLoadedExample(result, fallbackName) {
    applyFormValues(result.values);
    examplesRunNameInput.value = result.name || "";
    examplesRunDescriptionInput.value = result.description || "";
    window.fim.rememberLoadedPreset(result.name || fallbackName);
    // "Into Configure": show it when the load started elsewhere (the
    // Welcome panel, the Run card). Showing it revalidates the form; when
    // it is already showing, revalidate here instead.
    if (document.getElementById("screen-configure").hidden) {
        await window.fim.showConfigureScreen();
    } else {
        await revalidate();
    }
    if (window.fim.getRunViewState() === "initial") {
        window.fim.renderInitialPreview();
    }
};

/**
 * Load the selected example into Configure, then close the dialog. A
 * refusal (no configuration, or one the form cannot represent) is shown
 * inside the dialog, which stays open.
 */
async function examplesLoadSelected() {
    const example = examplesSelectedExample();
    if (!example || !example.loadable) {
        return;
    }
    window.__fimExampleLoadSettled = false;
    examplesLoadButton.disabled = true;
    try {
        const result = await window.pywebview.api.load_example(example.id);
        if (!result.ok) {
            examplesShowError(`This example could not be loaded: ${result.message}`);
            return;
        }
        examplesDialog.close();
        await window.fim.applyLoadedExample(result, example.name);
    } finally {
        examplesLoadButton.disabled = !example.loadable;
        window.__fimExampleLoadSettled = true;
    }
}

/**
 * Open the selected example's saved result on the Results card, closing
 * the dialog. A refusal is shown inside the dialog, which stays open.
 */
async function examplesOpenSaved() {
    const example = examplesSelectedExample();
    if (!example || !example.has_saved_result) {
        return;
    }
    window.__fimExampleOpenSettled = false;
    examplesOpenSavedButton.disabled = true;
    try {
        const result = await window.pywebview.api.open_saved_example(example.id);
        if (!result.ok) {
            examplesShowError(`This example's saved result could not be opened: ${result.message}`);
            return;
        }
        examplesDialog.close();
        await window.fim.openComputedRun(result.directory, result.isBatch);
    } finally {
        examplesOpenSavedButton.disabled = !example.has_saved_result;
        window.__fimExampleOpenSettled = true;
    }
}

/**
 * Show the selected example's README excerpt, and say up front why it
 * cannot be loaded if it cannot.
 */
function examplesRenderDetail() {
    const example = examplesSelectedExample();
    examplesDetailName.textContent = example ? example.name : "";
    examplesDetailExcerpt.textContent = example ? example.excerpt : "";
    examplesLoadButton.disabled = !example || !example.loadable;
    examplesViewYamlButton.disabled = !example || !example.has_configuration;
    const hasSaved = Boolean(example && example.has_saved_result);
    examplesOpenSavedButton.disabled = !hasSaved;
    examplesOpenSavedButton.title = example && !hasSaved ? EXAMPLES_NO_SAVED_RESULT_TEXT : "";
    if (example && !example.loadable) {
        examplesShowError(`This example cannot be loaded into the form: ${example.message}`);
    } else {
        examplesShowError("");
    }
}

/**
 * Rebuild the examples list for the selected class.
 */
function examplesRenderList() {
    const entry = examplesFlatClasses().find(
        (item) => item.id === examplesSelectedClassId
    );
    examplesClassDescription.textContent = entry ? entry.description : "";
    const shown = examplesInClass(examplesSelectedClassId);
    if (!shown.some((example) => example.id === examplesSelectedExampleId)) {
        examplesSelectedExampleId = shown.length ? shown[0].id : null;
    }
    examplesList.replaceChildren();
    for (const example of shown) {
        const item = document.createElement("li");
        item.setAttribute("role", "option");
        item.dataset.exampleId = example.id;
        const selected = example.id === examplesSelectedExampleId;
        item.setAttribute("aria-selected", String(selected));
        item.tabIndex = selected ? 0 : -1;
        item.setAttribute(
            "aria-label",
            example.loadable
                ? `${example.name}. ${example.description}`
                : `${example.name} (view YAML only). ${example.description}`
        );
        const name = document.createElement("span");
        name.className = "examples-item-name";
        name.textContent = example.loadable
            ? example.name
            : `${example.name} (view YAML only)`;
        const description = document.createElement("span");
        description.className = "examples-item-description";
        description.textContent = example.description;
        item.append(name, description);
        item.addEventListener("click", () => {
            examplesSelectExample(item);
            examplesFocusItem(examplesList, item);
        });
        item.addEventListener("dblclick", () => examplesLoadSelected());
        examplesList.appendChild(item);
    }
    examplesRenderDetail();
}

/**
 * Rebuild the class tree: a flat list of tree items whose `aria-level`
 * carries the (at most two-level) hierarchy. Every parent is always
 * expanded; the tree is shallow enough that collapsing it would only
 * hide examples.
 */
function examplesRenderTree() {
    const flat = examplesFlatClasses();
    examplesClassTree.replaceChildren();
    for (const entry of flat) {
        const siblings = flat.filter((item) => item.parentId === entry.parentId);
        const item = document.createElement("li");
        item.setAttribute("role", "treeitem");
        item.className = entry.level === 1
            ? "examples-class"
            : "examples-class examples-class-child";
        item.dataset.classId = entry.id;
        item.setAttribute("aria-level", String(entry.level));
        item.setAttribute("aria-setsize", String(siblings.length));
        item.setAttribute("aria-posinset", String(siblings.indexOf(entry) + 1));
        if (entry.childIds.length) {
            item.setAttribute("aria-expanded", "true");
        }
        const selected = entry.id === examplesSelectedClassId;
        item.setAttribute("aria-selected", String(selected));
        item.tabIndex = selected ? 0 : -1;
        const count = examplesInClass(entry.id).length;
        item.textContent = `${entry.title} (${count})`;
        item.title = entry.description || entry.title;
        item.addEventListener("click", () => {
            examplesSelectClass(item);
            examplesFocusItem(examplesClassTree, item);
        });
        examplesClassTree.appendChild(item);
    }
}

/**
 * Select the class `item` names and show its examples.
 * @param {HTMLElement} item
 */
function examplesSelectClass(item) {
    examplesSelectedClassId = item.dataset.classId;
    for (const other of examplesClassTree.children) {
        other.setAttribute("aria-selected", String(other === item));
    }
    examplesRenderList();
}

/**
 * Select the example `item` names and show its excerpt.
 * @param {HTMLElement} item
 */
function examplesSelectExample(item) {
    examplesSelectedExampleId = item.dataset.exampleId;
    for (const other of examplesList.children) {
        other.setAttribute("aria-selected", String(other === item));
    }
    examplesRenderDetail();
}

/**
 * The selected example's catalog entry, or `undefined`.
 * @returns {object|undefined}
 */
function examplesSelectedExample() {
    return examplesCatalog.examples.find(
        (example) => example.id === examplesSelectedExampleId
    );
}

/**
 * Show (or, given an empty `message`, hide) the dialog's error line.
 * @param {string} message
 */
function examplesShowError(message) {
    examplesLoadError.textContent = message;
    examplesLoadError.hidden = !message;
}

examplesClassTree.addEventListener("keydown", (event) => {
    if (event.key === "ArrowRight") {
        const target = examplesList.querySelector("[aria-selected='true']");
        if (target) {
            examplesFocusItem(examplesList, target);
        }
        event.preventDefault();
        return;
    }
    if (event.key === "ArrowLeft") {
        const entry = examplesFlatClasses().find(
            (item) => item.id === examplesSelectedClassId
        );
        const parent = entry?.parentId
            ? examplesClassTree.querySelector(`[data-class-id='${entry.parentId}']`)
            : null;
        if (parent) {
            examplesSelectClass(parent);
            examplesFocusItem(examplesClassTree, parent);
        }
        event.preventDefault();
        return;
    }
    if (examplesHandleListKey(event, examplesClassTree, examplesSelectClass)) {
        event.preventDefault();
    }
});

examplesList.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft") {
        const target = examplesClassTree.querySelector("[aria-selected='true']");
        if (target) {
            examplesFocusItem(examplesClassTree, target);
        }
        event.preventDefault();
        return;
    }
    if (examplesHandleListKey(event, examplesList, examplesSelectExample)) {
        event.preventDefault();
    }
});

// A click on the backdrop (the dialog element itself, outside its
// content) closes the dialog, as `wireModal` does for other dialogs.
examplesDialog.addEventListener("click", (event) => {
    if (event.target === examplesDialog) {
        examplesDialog.close();
    }
});

examplesCancelButton.addEventListener("click", () => examplesDialog.close());
examplesLoadButton.addEventListener("click", () => examplesLoadSelected());
examplesOpenSavedButton.addEventListener("click", () => examplesOpenSaved());
examplesViewYamlButton.addEventListener("click", () => {
    const example = examplesSelectedExample();
    if (example) {
        window.fim.showPresetYaml(example.id);
    }
});

document
    .getElementById("configure-examples-button")
    .addEventListener("click", () => window.fim.showExamplesDialog());

/**
 * Fetch the catalog and open the dialog, keeping the previous selection
 * when it still exists, otherwise selecting the first class with
 * examples and its first example.
 */
window.fim.showExamplesDialog = async function showExamplesDialog() {
    window.__fimExamplesDialogReady = false;
    const result = await window.pywebview.api.list_examples();
    examplesCatalog = result.ok ? result : { classes: [], examples: [] };
    const flat = examplesFlatClasses();
    if (!flat.some((entry) => entry.id === examplesSelectedClassId)) {
        const first = flat.find((entry) => examplesInClass(entry.id).length > 0);
        examplesSelectedClassId = first ? first.id : null;
    }
    examplesEmptyNote.hidden = examplesCatalog.examples.length > 0;
    examplesRenderTree();
    examplesRenderList();
    examplesDialog.showModal();
    const selected = examplesClassTree.querySelector("[aria-selected='true']");
    if (selected) {
        examplesFocusItem(examplesClassTree, selected);
    }
    window.__fimExamplesDialogReady = true;
};
