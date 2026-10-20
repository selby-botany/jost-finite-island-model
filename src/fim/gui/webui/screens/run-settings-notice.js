"use strict";

/* A loaded configuration's own run settings, and the notice that says
 * how they differ from the user's Settings.
 *
 * The run settings (`config_form.DEFAULT_RUN_SETTING_FIELD_NAMES`:
 * execution engine, replicates, generation cap, convergence timing and
 * so on) live in the Settings dialog, not on Configure's form. Loading
 * a configuration -- an example, a preset, a YAML file, a saved run --
 * used to copy its run settings into Settings permanently, so one
 * example quietly changed every later New configuration. Now they apply
 * to the run being configured only:
 *
 * - `applyLoadedRunSettings` keeps the loaded values here, and
 *   `perRunSettingValues` adds them to every form submission
 *   (`collectFormValues`, `config-modals.js`). `Api._merge_default_run_
 *   settings` keeps a value a submission already has, so validation,
 *   saving and the run itself all use the loaded values.
 * - `clearLoadedRunSettings` drops them when the form starts fresh (New
 *   configuration, Explore's handoff), so Settings apply again.
 * - The notice (one copy on Configure, one on the Run card) lists each
 *   field that differs, with "Edit this run", "Make these my Settings"
 *   and "Dismiss". Configure also offers the editor when no values differ. A
 *   "Run settings" item in the parameter strip stays while they differ,
 *   and brings a dismissed notice back.
 *
 * `window.__fimRunSettingsNoticeSettled` is false while an edit is applying
 * or "Make these my Settings" is saving, so a test can wait for its result.
 */

function runSettingsStateFor(tabId = window.fim.workspace.selectedId()) {
    const documentState = window.fim.workspace.get(tabId);
    if (!documentState) {
        return null;
    }
    if (!documentState.runSettingsNotice) {
        documentState.runSettingsNotice = {
            tabId,
            loaded: null,
            original: null,
            differences: [],
            dismissed: false,
            adopted: false,
            adoptionError: null,
            requestToken: 0,
        };
    }
    return documentState.runSettingsNotice;
}

const runSettingsNotices = Array.from(
    document.querySelectorAll("[data-run-settings-notice]")
);
const runSettingsStripItem = document.getElementById("parameter-strip-run-settings");
const runSettingsStripValue = document.getElementById(
    "parameter-strip-run-settings-value"
);

/**
 * The run's own run settings, to add to a form submission.
 * @returns {Record<string, string>} empty when Settings apply.
 */
function perRunSettingValues() {
    const state = runSettingsStateFor();
    return state === null || state.loaded === null ? {} : { ...state.loaded };
}

/**
 * Fill one notice's table and say what it shows.
 * @param {HTMLElement} notice
 */
function fillRunSettingsNotice(notice, state) {
    const summary = notice.querySelector(".run-settings-notice-summary");
    const table = notice.querySelector(".run-settings-notice-table");
    const body = table.querySelector("tbody");
    const adoptButton = notice.querySelector("[data-run-settings-adopt]");
    body.replaceChildren();
    if (state.adoptionError) {
        summary.textContent = state.adoptionError;
        return;
    }
    if (state.adopted) {
        summary.textContent =
            "Done. Your Settings now match this run, and new configurations will use them.";
        table.hidden = true;
        adoptButton.hidden = true;
        return;
    }
    summary.textContent =
        "This configuration comes with its own run settings. They are used for " +
        "this run only, and your Settings have not changed. These differ from " +
        "your Settings:";
    table.hidden = false;
    adoptButton.hidden = false;
    for (const difference of state.differences) {
        const row = document.createElement("tr");
        row.dataset.field = difference.field;
        const label = document.createElement("th");
        label.scope = "row";
        label.textContent = difference.label;
        const runValue = document.createElement("td");
        runValue.textContent = difference.runText;
        const settingsValue = document.createElement("td");
        settingsValue.textContent = difference.settingsText;
        row.append(label, runValue, settingsValue);
        body.appendChild(row);
    }
}

/**
 * Show or hide every notice copy and the parameter strip item. The Run
 * card's copy shows only before a run starts (`initial`); once a run is
 * going or finished, the card is about that run's results.
 */
function renderRunSettingsNotice(state = runSettingsStateFor()) {
    if (!state || state !== runSettingsStateFor()) {
        return;
    }
    const differs = state.loaded !== null && state.differences.length > 0;
    const showNotice = (differs || state.adopted) && !state.dismissed;
    for (const notice of runSettingsNotices) {
        const onRunCard = notice.closest("#screen-run") !== null;
        const runCardReady =
            !onRunCard || window.fim.getRunViewState() === "initial";
        notice.hidden = !(showNotice && runCardReady);
        if (!notice.hidden) {
            fillRunSettingsNotice(notice, state);
        }
    }
    runSettingsStripItem.hidden = !differs;
    const count = state.differences.length;
    runSettingsStripValue.textContent = differs
        ? `this run's own (${count} differ${count === 1 ? "s" : ""})`
        : "";
}

window.fim.renderRunSettingsNotice = renderRunSettingsNotice;

/**
 * Use a just-loaded configuration's run settings for this run, and
 * tell the user how they differ from Settings.
 * @param {{runSettings?: Record<string, string>,
 *     runSettingDifferences?: Array<object>}} result A load's result
 *     (`Api.load_example`, `load_preset`, `load_yaml`,
 *     `load_run_configuration`).
 */
function applyLoadedRunSettings(result) {
    const state = runSettingsStateFor();
    if (!state) {
        return;
    }
    state.requestToken += 1;
    state.loaded = result.runSettings ? { ...result.runSettings } : null;
    state.original = state.loaded === null ? null : { ...state.loaded };
    state.differences = result.runSettingDifferences || [];
    state.dismissed = false;
    state.adopted = false;
    state.adoptionError = null;
    renderRunSettingsNotice();
}

window.fim.applyLoadedRunSettings = applyLoadedRunSettings;

/** Go back to Settings for the run settings (a fresh configuration). */
function clearLoadedRunSettings() {
    const state = runSettingsStateFor();
    if (!state) {
        return;
    }
    state.requestToken += 1;
    state.loaded = null;
    state.original = null;
    state.differences = [];
    state.dismissed = false;
    state.adopted = false;
    state.adoptionError = null;
    renderRunSettingsNotice();
}

window.fim.clearLoadedRunSettings = clearLoadedRunSettings;

/**
 * Compare the run's own run settings with Settings again -- after
 * Settings change, so the notice never shows a stale "your Settings".
 */
async function refreshRunSettingDifferences(state = runSettingsStateFor()) {
    if (!state || state.loaded === null) {
        return;
    }
    const tabId = state.tabId;
    const token = ++state.requestToken;
    const differences =
        await window.pywebview.api.get_run_setting_differences(
            { ...state.loaded }
        );
    if (
        !window.fim.workspace.get(tabId) ||
        state !== runSettingsStateFor(tabId) ||
        token !== state.requestToken
    ) {
        return;
    }
    state.differences = differences;
    renderRunSettingsNotice();
}

window.fim.refreshRunSettingDifferences = refreshRunSettingDifferences;

/**
 * Apply validated editor values to the pending configuration, not Settings.
 * @param {Record<string, string>} values
 */
async function updateRunSettings(values) {
    const tabId = window.fim.workspace.selectedId();
    const state = runSettingsStateFor(tabId);
    if (!state) {
        return;
    }
    state.requestToken += 1;
    state.loaded = { ...values };
    state.dismissed = false;
    state.adopted = false;
    state.adoptionError = null;
    await refreshRunSettingDifferences(state);
    if (window.fim.workspace.selectedId() !== tabId) {
        return;
    }
    await revalidate();
    if (window.fim.getRunViewState() === "initial") {
        await window.fim.renderInitialPreview();
    }
}

window.fim.updateRunSettings = updateRunSettings;
window.fim.originalRunSettings = function originalRunSettings() {
    const original = runSettingsStateFor()?.original;
    return original === null || original === undefined ? null : { ...original };
};

/**
 * "Make these my Settings": save the run's own run settings as Settings,
 * through the same `set_default_run_settings` call the Settings dialog
 * makes. `max_workers` is a machine setting no configuration names, so
 * the saved one is kept.
 */
async function adoptLoadedRunSettings() {
    const tabId = window.fim.workspace.selectedId();
    const state = runSettingsStateFor(tabId);
    if (!state || state.loaded === null) {
        return;
    }
    const loaded = { ...state.loaded };
    window.fim.workspace.settle(tabId, "runSettingsNotice", false);
    try {
        const current = await window.pywebview.api.get_default_run_settings();
        const result = await window.pywebview.api.set_default_run_settings({
            ...current,
            ...loaded,
        });
        if (!result.ok) {
            state.adoptionError =
                `Your Settings could not be changed: ${result.message}`;
            renderRunSettingsNotice(state);
            return;
        }
        state.adopted = true;
        state.dismissed = false;
        state.adoptionError = null;
        await refreshRunSettingDifferences(state);
        renderRunSettingsNotice(state);
    } finally {
        if (window.fim.workspace.get(tabId)) {
            window.fim.workspace.settle(tabId, "runSettingsNotice", true);
        }
    }
}

for (const notice of runSettingsNotices) {
    notice
        .querySelector("[data-run-settings-edit]")
        .addEventListener("click", () => window.fim.openRunSettings());
    notice
        .querySelector("[data-run-settings-adopt]")
        .addEventListener("click", adoptLoadedRunSettings);
    notice.querySelector("[data-run-settings-dismiss]").addEventListener("click", () => {
        const state = runSettingsStateFor();
        if (!state) {
            return;
        }
        state.dismissed = true;
        state.adopted = false;
        renderRunSettingsNotice();
    });
}

// The strip item also opens Configure (`nav-rail.js` wires every
// `.parameter-strip-item`); here it brings a dismissed notice back.
runSettingsStripItem.addEventListener("click", () => {
    const state = runSettingsStateFor();
    if (!state) {
        return;
    }
    state.dismissed = false;
    renderRunSettingsNotice();
});

window.fim.workspace.onSelectionChange(() => {
    renderRunSettingsNotice();
    refreshRunSettingDifferences();
});
