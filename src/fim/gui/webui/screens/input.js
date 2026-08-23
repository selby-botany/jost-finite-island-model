"use strict";

/* Screen 1: model input (design doc §4.1). Every field maps one-to-one
 * to a src/fim/gui/config_form.py key, in that module's own tab
 * grouping and order -- see its module docstring for the authoritative
 * field list this markup mirrors. Validation, load, and save all route
 * through the bridge to that same module (never reimplemented here);
 * the field-to-tab routing an invalid field needs (design §4.0 #2) is
 * computed in Python too (`config_form.tab_for_error`/`field_for_error`,
 * returned directly by `Api.validate_form`) rather than duplicated as a
 * second, hand-maintained JS lookup table -- one source of truth for a
 * mapping this project's own "do not duplicate" rule (developer guide)
 * already governs for logic, applied here to UI wiring too. */

const form = document.getElementById("input-form");
const banner = document.getElementById("input-banner");
const runButton = document.getElementById("run-button");
const runReason = document.getElementById("run-reason");
const loadButton = document.getElementById("load-yaml-button");
const saveButton = document.getElementById("save-yaml-button");
const openRunButton = document.getElementById("open-run-button");

/* Composite/derived fields config_form.py's params_to_form_values
 * returns that have no directly-name-matched form input (read-only
 * summaries, shown as plain text instead). */
const SUMMARY_ONLY_KEYS = ["m_loaded_summary", "p0_summary"];

/* The (now hidden, `app.css`'s `.tab-bar { display: none; }`) tab
 * bar's own six labels, keyed by the same `data-tab` name `_build_
 * menu`'s Configure menu items pass to `configureTab` -- the visible
 * navigation moved to that native menu (visualization-and-config-
 * editors design), but `current-tab-heading` still needs the same
 * human-readable text the old tab labels showed, so this map is kept
 * rather than reading text out of the now-hidden DOM it used to live
 * in. */
const TAB_LABELS = {
    population: "Population",
    migration: "Migration",
    mutation: "Mutation",
    initial_conditions: "Initial conditions",
    convergence: "Convergence",
    batch: "Batch",
};

const currentTabHeading = document.getElementById("current-tab-heading");

function setFieldValue(name, value) {
    const field = form.elements.namedItem(name);
    if (field === null) {
        return;
    }
    if (field instanceof RadioNodeList) {
        for (const option of field) {
            option.checked = option.value === value;
        }
        return;
    }
    if (field.type === "checkbox") {
        field.checked = value === "true";
        return;
    }
    field.value = value;
}

function applyFormValues(values) {
    for (const [key, value] of Object.entries(values)) {
        if (SUMMARY_ONLY_KEYS.includes(key)) {
            continue;
        }
        setFieldValue(key, value);
    }
    const loadedSummary = document.getElementById("m-loaded-summary");
    loadedSummary.textContent = values.m_loaded_summary || "";
    loadedSummary.hidden = !values.m_loaded_summary;
    const p0Summary = document.getElementById("p0-summary");
    p0Summary.textContent = values.p0_summary || "";
    p0Summary.hidden = !values.p0_summary;
    syncConditionalVisibility();
}

function collectFormValues() {
    const data = new FormData(form);
    const values = {};
    for (const [key, value] of data.entries()) {
        values[key] = value;
    }
    for (const name of ["cs_D", "cs_G_ST", "cs_E_ST", "cs_K_ST", "cs_H_S", "cs_H_T"]) {
        values[name] = data.has(name) ? "true" : "false";
    }
    return values;
}

function checkedStatisticCount() {
    return ["cs_D", "cs_G_ST", "cs_E_ST", "cs_K_ST", "cs_H_S", "cs_H_T"].filter(
        (name) => form.elements.namedItem(name).checked
    ).length;
}

function syncConditionalVisibility() {
    const mMode = form.elements.namedItem("m_mode").value;
    document.getElementById("m-scalar-fields").hidden = mMode !== "scalar";
    document.getElementById("m-topology-fields").hidden = mMode !== "topology";
    document.getElementById("m-loaded-summary").hidden = mMode !== "loaded";

    const muMode = form.elements.namedItem("mu_mode").value;
    document.getElementById("mu-mu-field").hidden = muMode !== "mu";
    document.getElementById("mu-mu_b-field").hidden = muMode !== "mu_b";

    document.getElementById("combinator-field").hidden = checkedStatisticCount() < 2;

    const nReplicatesField = form.elements.namedItem("n_replicates");
    const isBatch = parseInt(nReplicatesField.value, 10) > 1;
    document.getElementById("batch-only-fields").hidden = !isBatch;
}

function clearTabErrorDots() {
    for (const dot of document.querySelectorAll(".error-dot")) {
        dot.hidden = true;
    }
    for (const field of document.querySelectorAll(".field.invalid")) {
        field.classList.remove("invalid");
    }
}

function markTabError(tab, field) {
    if (tab !== null && tab !== undefined) {
        const dot = document.getElementById(`dot-${tab}`);
        if (dot !== null) {
            dot.hidden = false;
        }
    }
    if (field !== null && field !== undefined) {
        const input = form.elements.namedItem(field);
        const wrapper = input instanceof RadioNodeList ? input[0] : input;
        const fieldDiv = wrapper === null ? null : wrapper.closest(".field");
        if (fieldDiv !== null) {
            fieldDiv.classList.add("invalid");
        }
    }
}

async function revalidate() {
    clearTabErrorDots();
    const values = collectFormValues();
    const result = await window.pywebview.api.validate_form(values);
    if (result.ok) {
        runButton.disabled = false;
        runReason.textContent = "";
        return result;
    }
    runButton.disabled = true;
    const location = result.field ? ` (${result.field})` : "";
    runReason.textContent = `${result.message}${location}`;
    markTabError(result.tab, result.field);
    return result;
}

function showBanner(message) {
    if (!message) {
        banner.hidden = true;
        banner.textContent = "";
        return;
    }
    banner.hidden = false;
    banner.textContent = message;
}

async function switchToTab(tabName) {
    const radio = document.getElementById(`tab-${tabName}`);
    if (radio !== null) {
        radio.checked = true;
    }
    if (currentTabHeading !== null) {
        currentTabHeading.textContent = TAB_LABELS[tabName] || "";
    }
}

async function onRunClicked() {
    const result = await revalidate();
    if (!result.ok) {
        await switchToTab(result.tab);
        return;
    }
    const values = collectFormValues();
    const started = await window.pywebview.api.start_run(values);
    if (!started.ok) {
        showBanner(started.message);
        return;
    }
    showBanner("");
    document.getElementById("progress-generation").value = 0;
    document.getElementById("cancel-run-button").disabled = false;
    window.fim.resetBatchProgress();
    window.fim.showScreen("screen-progress");
}

async function onLoadYamlClicked() {
    const result = await window.pywebview.api.load_yaml();
    if (!result.ok) {
        if (result.message) {
            showBanner(result.message);
        }
        return;
    }
    showBanner("");
    applyFormValues(result.values);
    await revalidate();
}

async function onSaveYamlClicked() {
    const values = collectFormValues();
    const result = await window.pywebview.api.save_yaml(values);
    if (!result.ok) {
        if (result.message) {
            showBanner(result.message);
        }
        return;
    }
    showBanner("");
    runReason.textContent = `Saved to ${result.path}`;
}

function wireEvents() {
    form.addEventListener("input", () => {
        syncConditionalVisibility();
        revalidate();
    });
    form.addEventListener("change", () => {
        syncConditionalVisibility();
        revalidate();
    });
    runButton.addEventListener("click", onRunClicked);
    loadButton.addEventListener("click", onLoadYamlClicked);
    saveButton.addEventListener("click", onSaveYamlClicked);
    openRunButton.addEventListener("click", () => {
        window.fim.showOpenRunScreen();
    });
}

/**
 * Reset the form to fresh starter values -- the fetch-and-apply half of
 * `initializeInputScreen`, factored out so it can run a second time
 * (`fim.menu.newConfiguration`, visualization-and-config-editors design
 * §4.5) without also re-running `wireEvents()`, which would otherwise
 * double-bind every listener on the form a second call would add.
 */
async function resetInputForm() {
    const values = await window.pywebview.api.get_starter_form();
    applyFormValues(values);
    const defaultWorkers = await window.pywebview.api.get_default_max_workers();
    form.elements.namedItem("max_workers").value = String(defaultWorkers);
    await revalidate();
}

async function initializeInputScreen() {
    await resetInputForm();
    wireEvents();
    // Set only once every async step above has settled and event
    // listeners are attached -- tests poll this rather than racing a
    // synthetic DOM event against `wireEvents()` not having run yet.
    window.__fimInputScreenReady = true;
}

window.fim.menu.newConfiguration = async function newConfiguration() {
    window.fim.showScreen("screen-input");
    // Cycled false-then-true around the reset, the same flag
    // `initializeInputScreen` sets once at first load -- reused, not
    // duplicated, so a test (or anything else) waiting for "the input
    // screen's form is in a fully settled state" has one reliable
    // signal for both the initial load and a later reset, instead of
    // racing a DOM value change alone: `resetInputForm` still has two
    // more real bridge calls in flight (`get_default_max_workers`,
    // `revalidate`'s own `validate_form`) after `field-N` itself
    // already shows the new value.
    window.__fimInputScreenReady = false;
    await resetInputForm();
    window.__fimInputScreenReady = true;
};

/**
 * `_build_menu`'s Configure menu -- navigate to Screen 1, on the
 * requested tab, leaving whatever is already in the form alone. The
 * one behavioral difference from `newConfiguration` above: this never
 * resets a field, the same distinction that method's own docstring
 * draws against the "New run" buttons -- `configureTab` is pure
 * navigation, matching what clicking an old, now-hidden tab label
 * itself used to do.
 */
window.fim.menu.configureTab = async function configureTab(tabName) {
    window.fim.showScreen("screen-input");
    await switchToTab(tabName);
};

// `whenApiReady` itself is `app.js`'s own top-level function, not
// redeclared here -- a real, found-live duplicate definition (identical
// body, this file's own local copy) silently overwrote whichever one
// loaded second, a legal-but-fragile `SyntaxError`-free redeclaration
// classic scripts sharing one global scope allow for `function`
// (unlike `let`/`const`, which throw outright — the sharper version of
// this same hazard `test_no_top_level_identifier_is_declared_in_more_
// than_one_script` now guards against for every declaration kind).
// Worked only by execution-order luck (`app.js` loads first and calls
// its own copy before `input.js`'s own redeclaration ever ran), not by
// design -- removed here, this file now calls the one shared
// definition directly, matching how it already calls `drawScatter`/
// `drawScatterGrid` from `scatter.js` with no `window.fim.` prefix.
whenApiReady(initializeInputScreen);
