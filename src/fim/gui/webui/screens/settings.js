"use strict";

const settingsButton = document.getElementById("settings-button");
const settingsDialog = document.getElementById("modal-settings");
const settingsBanner = document.getElementById("settings-banner");
const startupBehaviorSelect = document.getElementById("settings-startup-behavior");

// The Structure panel's own `#cs-selector` order -- kept identical so a
// value collected here and one collected there mean the same thing to
// `config_form.CONVERGENCE_STATISTIC_NAMES` on the Python side.
const SETTINGS_CONVERGENCE_STATISTIC_NAMES = [
    "D",
    "G_ST",
    "E_ST",
    "K_ST",
    "H_S",
    "H_T",
    "A_CGD",
    "Delta",
    "MI",
];

const settingsEngineBackendSelect = document.getElementById("settings-engine_backend");
const settingsNReplicatesInput = document.getElementById("settings-n_replicates");
const settingsCombinatorField = document.getElementById("settings-combinator-field");
const settingsConvergenceCombinatorSelect = document.getElementById(
    "settings-convergence_combinator"
);
const settingsConvergenceWindowInput = document.getElementById(
    "settings-convergence_window"
);
const settingsConvergenceToleranceInput = document.getElementById(
    "settings-convergence_tolerance"
);
const settingsSaveButton = document.getElementById("settings-save-button");

function showSettingsBanner(message) {
    if (!message) {
        settingsBanner.hidden = true;
        settingsBanner.textContent = "";
        return;
    }
    settingsBanner.hidden = false;
    settingsBanner.textContent = message;
}

/**
 * How many of Settings' own convergence-statistic checkboxes are
 * checked -- `config-modals.js`'s own `checkedStatisticCount`, mirrored
 * here rather than shared, since Settings' checkboxes have no shared
 * `<form>` to query via `form.elements.namedItem` the way Configure's
 * own copy does.
 */
function checkedSettingsStatisticCount() {
    return SETTINGS_CONVERGENCE_STATISTIC_NAMES.filter(
        (name) => document.getElementById(`settings-cs_${name}`).checked
    ).length;
}

/**
 * Reveal the combinator field only once two or more statistics are
 * checked -- the identical rule `config-modals.js`'s own
 * `syncConditionalVisibility` already applies to Configure's own copy.
 */
function syncSettingsConditionalVisibility() {
    settingsCombinatorField.hidden = checkedSettingsStatisticCount() < 2;
}

for (const name of SETTINGS_CONVERGENCE_STATISTIC_NAMES) {
    document
        .getElementById(`settings-cs_${name}`)
        .addEventListener("change", syncSettingsConditionalVisibility);
}

/**
 * Collect Settings' own execution/convergence-selection fields into the
 * same `dict[str, str]` shape `Api.set_default_run_settings` expects
 * (`config_form.DEFAULT_RUN_SETTING_FIELD_NAMES`'s own key set) -- an
 * explicit-checkbox-presence read for the `cs_*` keys, the same
 * "checked -> \"true\", unchecked -> \"false\"" convention `collect
 * FormValues` already uses for Configure's own copy, reimplemented here
 * since there is no shared `<form>`/`FormData` to scan.
 * @returns {Record<string, string>}
 */
function collectDefaultRunSettingsValues() {
    const values = {
        engine_backend: settingsEngineBackendSelect.value,
        n_replicates: settingsNReplicatesInput.value,
        convergence_combinator: settingsConvergenceCombinatorSelect.value,
        convergence_window: settingsConvergenceWindowInput.value,
        convergence_tolerance: settingsConvergenceToleranceInput.value,
    };
    for (const name of SETTINGS_CONVERGENCE_STATISTIC_NAMES) {
        values[`cs_${name}`] = document.getElementById(`settings-cs_${name}`).checked
            ? "true"
            : "false";
    }
    return values;
}

/**
 * Seed Settings' own execution/convergence-selection fields from
 * `Api.get_default_run_settings`'s own return shape -- the inverse of
 * `collectDefaultRunSettingsValues`, called once when the dialog opens.
 * @param {Record<string, string>} values
 */
function applyDefaultRunSettingsValues(values) {
    settingsEngineBackendSelect.value = values.engine_backend;
    settingsNReplicatesInput.value = values.n_replicates;
    settingsConvergenceCombinatorSelect.value = values.convergence_combinator;
    settingsConvergenceWindowInput.value = values.convergence_window;
    settingsConvergenceToleranceInput.value = values.convergence_tolerance;
    for (const name of SETTINGS_CONVERGENCE_STATISTIC_NAMES) {
        document.getElementById(`settings-cs_${name}`).checked =
            values[`cs_${name}`] === "true";
    }
    syncSettingsConditionalVisibility();
}

async function loadSettingsDialog() {
    showSettingsBanner("");
    startupBehaviorSelect.value = await window.pywebview.api.get_startup_behavior();
    applyDefaultRunSettingsValues(await window.pywebview.api.get_default_run_settings());
}

settingsButton.addEventListener("click", async () => {
    await loadSettingsDialog();
    window.fim.wireModal("modal-settings");
    settingsDialog.showModal();
});

startupBehaviorSelect.addEventListener("change", async () => {
    const result = await window.pywebview.api.set_startup_behavior(
        startupBehaviorSelect.value
    );
    if (!result.ok) {
        showSettingsBanner(result.message);
    }
});

// Collected together into one bridge call on Save, unlike startup
// behavior/significant digits/appearance above and below (each a single
// independent scalar, applied immediately on its own `change`) -- this
// is ~14 fields, and a per-field round trip for each would be chatty
// for no real benefit, since none of them takes effect until a *new*
// configuration is started anyway (`index.html`'s own comment above
// `#modal-settings` has the full account).
settingsSaveButton.addEventListener("click", async () => {
    const result = await window.pywebview.api.set_default_run_settings(
        collectDefaultRunSettingsValues()
    );
    showSettingsBanner(result.ok ? "" : result.message);
});

/**
 * Significant digits (design §4.2 -- moved out of the native View
 * menu's own quick-toggle submenu into an ordinary Configure field,
 * then relocated again into Settings on a real, reported request --
 * `index.html`'s own comment above `#modal-settings` has the full
 * account). Not a `SimulationParams` field: no `name`/`form="input-
 * form"`, no `collectFormValues()`/`revalidate()` involvement, wired
 * directly to the same `Api.set_significant_digits` bridge call the old
 * menu items made. `Api.get_significant_digits` seeds the select's own
 * initial value once, on launch -- this setting is process-local
 * (`Api.__init__`'s own docstring), never part of a saved/loaded
 * configuration, so there is nothing to re-sync on `applyFormValues`/
 * `resetInputForm`. Wired at module load, not gated behind the dialog
 * opening -- nothing here depends on Settings actually being visible.
 */
async function wireSignificantDigitsField() {
    const select = document.getElementById("settings-significant_digits");
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

/**
 * Apply a dark-mode override to the page itself, immediately -- design
 * §11.2/§12: an explicit choice takes effect right away, not only on
 * the next launch. `""`/`null` clears the attribute entirely, letting
 * `app.css`'s own `@media (prefers-color-scheme: dark)` block (guarded
 * `:not([data-theme="light"])`) take back over, following the OS again.
 * @param {string|null} value - `"light"`, `"dark"`, or `""`/`null` for
 *     "follow the OS."
 */
function applyDarkModeOverride(value) {
    if (value) {
        document.documentElement.dataset.theme = value;
    } else {
        delete document.documentElement.dataset.theme;
    }
}

window.fim.applyDarkModeOverride = applyDarkModeOverride;

/**
 * Dark mode override (design §11.2, §12) -- the same shape as
 * `wireSignificantDigitsField` just above, applied here to `Api.get_
 * dark_mode_override`/`set_dark_mode_override` instead. The select's
 * own empty-string "Follow system" option is sent to the bridge as
 * `null`, matching `GuiPreferences.dark_mode_override`'s own "`None`
 * means follow the OS" contract -- an HTML `<select>` has no native
 * `null` value of its own, only strings. Wired at module load, not
 * gated behind the dialog opening: dark mode must apply before Settings
 * is ever opened, exactly as it did living in Configure.
 */
async function wireDarkModeOverrideField() {
    const select = document.getElementById("settings-dark_mode_override");
    const saved = await window.pywebview.api.get_dark_mode_override();
    select.value = saved || "";
    applyDarkModeOverride(saved);
    select.addEventListener("change", async () => {
        const value = select.value || null;
        const result = await window.pywebview.api.set_dark_mode_override(value);
        if (!result.ok) {
            window.alert(`Could not change appearance: ${result.message}`);
            return;
        }
        applyDarkModeOverride(value);
    });
}

whenApiReady(wireSignificantDigitsField);
whenApiReady(wireDarkModeOverrideField);
