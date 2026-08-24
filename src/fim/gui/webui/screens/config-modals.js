"use strict";

/* The six Configure modals' shared field logic (unified-run-view design
 * §3.1, §3.7, §8 Phase E). Every field maps one-to-one to a
 * src/fim/gui/config_form.py key, in that module's own tab grouping and
 * order -- see its module docstring for the authoritative field list
 * this markup mirrors. Validation, load, and save all route through the
 * bridge to that same module (never reimplemented here); the field-to-
 * section routing an invalid field needs (design §4.0 #2 of the
 * graphical-interface migration design) is computed in Python too
 * (`config_form.tab_for_error`/`field_for_error`, returned directly by
 * `Api.validate_form`) rather than duplicated as a second, hand-
 * maintained JS lookup table.
 *
 * Orthogonal to `runViewState` on purpose (design §3.7): a modal can be
 * opened from any of the three states, so nothing here depends on which
 * one is currently active, and nothing in `run-view-*.js` depends on
 * whether a modal happens to be open.
 */

const form = document.getElementById("input-form");

/* Composite/derived fields config_form.py's params_to_form_values
 * returns that have no directly-name-matched form input (read-only
 * summaries, shown as plain text instead). */
const SUMMARY_ONLY_KEYS = ["m_loaded_summary", "p0_summary"];

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

/**
 * Re-validate the form against the bridge and reflect the result in the
 * shared "Run simulation" button/reason text (`run-view-controls.js`
 * owns `runButton`/`runReason` themselves -- both classic scripts on
 * the same page share one global scope, so referencing them here by
 * bare name needs no import).
 */
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

/**
 * Fetch the starter form and apply it -- the fetch-and-apply half of
 * entering the `initial` state (`run-view-initial.js`), factored out
 * here alongside every other form-manipulation function so `fim.menu.
 * newConfiguration` and a fresh `initial` transition share the one
 * implementation.
 */
async function resetInputForm() {
    const values = await window.pywebview.api.get_starter_form();
    applyFormValues(values);
    const defaultWorkers = await window.pywebview.api.get_default_max_workers();
    form.elements.namedItem("max_workers").value = String(defaultWorkers);
    await revalidate();
}

/**
 * Delegated at `document`, not `form` -- confirmed live (design §3.1,
 * §8 Phase A): a field inside a Configure modal is `form="input-form"`
 * rather than a DOM descendant of `#input-form` (a `<dialog>` must live
 * outside every `.screen` section to render at all when shown while a
 * different screen is hidden, §6's own live-validation note), so its
 * `input`/`change` events bubble through the dialog and `#app`, never
 * through the form element itself -- a listener on `form` silently
 * stops firing for it. `event.target.form` is the browser's own
 * authoritative form-owner resolution (respects both DOM containment
 * and `form=""` identically), so filtering on it here catches every
 * field either way and ignores every field that is not this form's
 * own, without re-deriving that logic by hand.
 */
function wireConfigModalEvents() {
    const revalidateIfOwnField = (event) => {
        if (event.target && event.target.form === form) {
            syncConditionalVisibility();
            // Re-render the `initial` state's own p_0 preview after
            // validation settles, so it tracks the field the visitor
            // is actually editing instead of only ever reflecting
            // whatever values were on hand at the moment `initial` was
            // entered. `renderInitialPreview` already re-collects form
            // values itself and silently no-ops both when the form is
            // not currently valid and when a different state is
            // showing by the time it runs, so calling it unconditionally
            // here is safe.
            revalidate().then(() => {
                if (window.fim.getRunViewState() === "initial") {
                    window.fim.renderInitialPreview();
                }
            });
        }
    };
    document.addEventListener("input", revalidateIfOwnField);
    document.addEventListener("change", revalidateIfOwnField);
}

/**
 * Set one field directly to a literal value, without opening its own
 * modal -- the shared plumbing behind every Configure value-selector
 * leaf (design §3.1.3): the same re-sync/re-validate a real edit
 * already triggers (`wireConfigModalEvents`'s own delegated listener),
 * run once here since a menu click never fires a real DOM `input`/
 * `change` event for `document`-level delegation to catch.
 * @param {string} name
 * @param {string} value
 */
function setSingleFieldValue(name, value) {
    setFieldValue(name, value);
    syncConditionalVisibility();
    revalidate();
}

/**
 * `_build_menu`'s Configure menu -- opens the named section's own modal
 * over whatever the run view currently shows (design §3.1, §8 Phase
 * A/B), no navigation at all: the whole point is that opening a config
 * section no longer discards whatever the user was looking at (a live
 * run, a completed result). Never resets a field, the same distinction
 * `fim.menu.newConfiguration` (`run-view-initial.js`) draws against a
 * genuine reset.
 */
window.fim.menu.configureTab = async function configureTab(tabName) {
    window.fim.openConfigModal(tabName);
};

/**
 * Configure > Deme weighting (design §3.1.3) -- a genuinely categorical
 * field, one `MenuAction` per legal value.
 * @param {string} value
 */
window.fim.menu.setDemeWeighting = function setDemeWeighting(value) {
    setSingleFieldValue("deme_weighting", value);
};

/**
 * Configure > Mutation model (design §3.1.3) -- the same shape as
 * Deme weighting above.
 * @param {string} value
 */
window.fim.menu.setMutationModel = function setMutationModel(value) {
    setSingleFieldValue("mutation_model", value);
};

/**
 * Configure > Convergence statistic (design §3.1.3) -- *toggles* one
 * statistic's own checkbox rather than selecting it exclusively: the
 * field is a set (any combination of the six, `app.py`'s own
 * `_build_menu` docstring has the full reasoning), so an exclusive pick
 * here would silently discard whatever multi-statistic combination the
 * Convergence modal already has configured.
 * @param {string} checkboxName - e.g. `"cs_G_ST"`.
 */
window.fim.menu.toggleConvergenceStatistic = function toggleConvergenceStatistic(
    checkboxName
) {
    const field = form.elements.namedItem(checkboxName);
    if (field === null) {
        return;
    }
    field.checked = !field.checked;
    syncConditionalVisibility();
    revalidate();
};

whenApiReady(wireConfigModalEvents);
