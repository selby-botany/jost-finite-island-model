"use strict";

/* The Configure workspace's shared field logic (botanist GUI redesign
 * doc `20260907-claude-sonnet-5-botanist-gui-redesign.md` §4; this
 * file's own name predates that redesign -- every field it once opened
 * inside its own per-section `<dialog>` now lives directly on the
 * always-visible `screen-configure` two-panel layout instead, but the
 * collection/validation logic below is unchanged either way). Every
 * field maps one-to-one to a src/fim/gui/config_form.py key -- see that
 * module's own docstring for the authoritative field list this markup
 * mirrors. Validation, load, and save all route through the bridge to
 * that same module (never reimplemented here); an invalid field's own
 * routing (`config_form.field_for_error`, returned directly by
 * `Api.validate_form`) is computed in Python too, rather than
 * duplicated as a second, hand-maintained JS lookup table.
 *
 * Orthogonal to `runViewState` on purpose (design §3.1: every rail
 * destination stays reachable at every point in a run's lifecycle):
 * Configure can be reached from any of the three run-view states, so
 * nothing here depends on which one is currently active, and nothing in
 * `run-view-*.js` depends on whether Configure happens to be showing.
 */

const form = document.getElementById("input-form");

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
        setFieldValue(key, value);
    }
    // `field-m_matrix_json`/`field-loci_json`/`field-p0_json`'s own
    // values are now set (by the loop above, like any other field), but
    // the *visible* grids they drive are separate DOM rows
    // `migration-matrix.js`/`loci-grid.js`/`p0-grid.js` each own --
    // rebuilding them from those values is each file's own concern, not
    // this function's. `rebuildP0Grid` runs last since its own grid
    // shape depends on the loci grid the previous call just rebuilt;
    // `updateFixedPerDemePreview` is pure display (no field of its own
    // to set) but depends on the same `d`/loci state, so it runs last
    // too.
    window.fim.rebuildMigrationMatrixGrid();
    window.fim.rebuildLociGrid();
    window.fim.rebuildP0Grid();
    window.fim.updateFixedPerDemePreview();
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
    document.getElementById("m-matrix-fields").hidden = mMode !== "matrix";

    const muMode = form.elements.namedItem("mu_mode").value;
    document.getElementById("mu-mu-field").hidden = muMode !== "mu";
    document.getElementById("mu-mu_b-field").hidden = muMode !== "mu_b";

    const initialConditionsMode = form.elements.namedItem(
        "initial_conditions_mode"
    ).value;
    document.getElementById("initial-conditions-dirichlet-fields").hidden =
        initialConditionsMode !== "dirichlet";
    document.getElementById("initial-conditions-equilibrium-fields").hidden =
        initialConditionsMode !== "equilibrium_split";
    document.getElementById("initial-conditions-explicit-fields").hidden =
        initialConditionsMode !== "explicit_p0";
    document.getElementById("initial-conditions-fixed-fields").hidden =
        initialConditionsMode !== "fixed_per_deme";

    const lociMode = form.elements.namedItem("loci_mode").value;
    document.getElementById("loci-lengths-fields").hidden = lociMode !== "lengths";
    document.getElementById("loci-custom-fields").hidden = lociMode !== "custom";

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
    // The parameter strip (design §3.2) reflects the form's own current
    // values regardless of whether they validate -- a strip that only
    // updated on a *valid* form would freeze on the last good value
    // while a user is mid-edit, exactly the "lost track of what I set"
    // complaint the strip exists to fix.
    window.fim.updateParameterStrip(values);
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
 * Fetch the true starter form and apply it -- `fim.menu.
 * newConfiguration`'s own unconditional reset (design doc `20260907-
 * claude-sonnet-5-gui-preferences-persistence-design.md`: an explicit
 * "New configuration" always means `STARTER_CONFIG`, never whatever
 * happens to be saved). `get_starter_form` never includes `max_workers`
 * (`config_form.starter_form_values`'s own docstring: it is not a
 * `SimulationParams` field at all), so this fetches and applies
 * `get_default_max_workers` itself, same as it always has.
 *
 * Distinct from `loadInitialForm`, just below, which a fresh app launch
 * uses instead -- the two used to be the same call (`get_starter_form`)
 * before saved form values existed to restore, which is exactly the
 * process-local-only gap P1 item 4 fixes.
 */
async function resetInputForm() {
    const values = await window.pywebview.api.get_starter_form();
    applyFormValues(values);
    const defaultWorkers = await window.pywebview.api.get_default_max_workers();
    form.elements.namedItem("max_workers").value = String(defaultWorkers);
    await revalidate();
}

/**
 * Fetch this launch's own initial form and apply it -- prefers the last
 * successfully submitted form (`Api.get_initial_form`, re-validated
 * server-side) over the true starter values `resetInputForm` above
 * always uses, so a botanist's own values survive a relaunch. Already
 * includes a usable `max_workers` (`collectFormValues`'s own `FormData`
 * scan covers every form field, not only `SimulationParams` ones), so —
 * unlike `resetInputForm` -- there is no second bridge call to make here.
 */
async function loadInitialForm() {
    const values = await window.pywebview.api.get_initial_form();
    applyFormValues(values);
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
// Equilibrium split's own three fields (botanist GUI design doc §4.3)
// drive a real, possibly-slow ancestral simulation
// (`EquilibriumSplitInitialCondition`) inside `renderInitialPreview`'s
// own `get_initial_state_panels` call — unlike every other field here,
// whose own preview cost is negligible. Re-running that simulation on
// every "input" event (each keystroke, while a value like "10000" is
// still being typed one digit at a time) would be wasteful and could
// make the dialog feel unresponsive, so these three wait for "change"
// (blur or Enter) instead — the same commit discipline Explore's own
// fields already use, for the identical reason.
const EQUILIBRIUM_SPLIT_FIELD_NAMES = [
    "equilibrium_convergence_window",
    "equilibrium_convergence_tolerance",
    "equilibrium_max_generations",
];

function wireConfigModalEvents() {
    const revalidateIfOwnField = (event) => {
        if (event.target && event.target.form === form) {
            syncConditionalVisibility();
            const skipPreview =
                event.type === "input" &&
                EQUILIBRIUM_SPLIT_FIELD_NAMES.includes(event.target.name);
            // Re-render the `initial` state's own p_0 preview after
            // validation settles, so it tracks the field the visitor
            // is actually editing instead of only ever reflecting
            // whatever values were on hand at the moment `initial` was
            // entered. `renderInitialPreview` already re-collects form
            // values itself and silently no-ops both when the form is
            // not currently valid and when a different state is
            // showing by the time it runs, so calling it unconditionally
            // here is safe -- except for the three equilibrium fields on
            // a plain keystroke (`skipPreview`, above), where revalidate
            // (cheap: parses and range-checks, never simulates) still
            // runs for live inline error feedback, but the expensive
            // preview itself waits for "change".
            revalidate().then(() => {
                if (!skipPreview && window.fim.getRunViewState() === "initial") {
                    window.fim.renderInitialPreview();
                }
            });
        }
    };
    document.addEventListener("input", revalidateIfOwnField);
    document.addEventListener("change", revalidateIfOwnField);
}

/**
 * Navigate to Configure and scroll the field an invalid "Run
 * simulation" click named into view (`run-view-controls.js`'s own
 * `onRunClicked`, `Api.validate_form`'s own `result.field`). Replaces
 * the six-modal era's own `openConfigModal(result.tab)` -- every field
 * now lives directly on the always-visible Configure screen (design §4,
 * §16 phase 1), so there is no modal left to open, and naming the exact
 * field is more precise than naming its old section ever was. `field`
 * can be `null` (an unknown-key error `field_for_error` could not
 * place) -- Configure still opens, just with nothing further to focus.
 * @param {string|null} field
 */
function focusInvalidField(field) {
    window.fim.showScreen("screen-configure");
    if (!field) {
        return;
    }
    const target = form.elements.namedItem(field);
    const element = target instanceof RadioNodeList ? target[0] : target;
    if (element === null || element === undefined) {
        return;
    }
    const container = element.closest(".field") || element.closest("fieldset.composite");
    (container || element).scrollIntoView({ block: "center", behavior: "smooth" });
    if (typeof element.focus === "function") {
        element.focus({ preventScroll: true });
    }
}

window.fim.focusInvalidField = focusInvalidField;

/**
 * Significant digits (design §4.2 -- moved out of the native View
 * menu's own quick-toggle submenu into an ordinary Configure field).
 * Not a `SimulationParams` field: no `name`/`form="input-form"`, no
 * `collectFormValues()`/`revalidate()` involvement, wired directly to
 * the same `Api.set_significant_digits` bridge call the old menu items
 * made. `Api.get_significant_digits` seeds the select's own initial
 * value once, on launch -- this setting is process-local (`Api.__init__`
 * 's own docstring), never part of a saved/loaded configuration, so
 * there is nothing to re-sync on `applyFormValues`/`resetInputForm`.
 */
async function wireSignificantDigitsField() {
    const select = document.getElementById("field-significant_digits");
    select.value = String(await window.pywebview.api.get_significant_digits());
    // `fim.menu.setSignificantDigits` (`app.js`) already has the
    // bridge-call-plus-alert-on-failure logic this needs -- the same
    // method the native View menu's own items used to call, reused
    // rather than duplicated now that this field is that menu's
    // replacement.
    select.addEventListener("change", () => {
        window.fim.menu.setSignificantDigits(Number(select.value));
    });
}

whenApiReady(wireConfigModalEvents);
whenApiReady(wireSignificantDigitsField);
