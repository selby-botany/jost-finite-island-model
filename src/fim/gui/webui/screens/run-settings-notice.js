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
 *   field that differs, with "Make these my Settings" and "Dismiss". A
 *   "Run settings" item in the parameter strip stays while they differ,
 *   and brings a dismissed notice back.
 *
 * `window.__fimRunSettingsNoticeSettled` is false while "Make these my
 * Settings" is saving and comparing, so a test can wait for its result.
 */

/** The loaded configuration's run settings, or `null` to use Settings. */
let loadedRunSettings = null;

/** How `loadedRunSettings` differs from Settings (`Api` entries). */
let runSettingDifferenceRows = [];

/** Whether the user closed the notice for the current load. */
let runSettingsNoticeDismissed = false;

/** Whether "Make these my Settings" just saved them. */
let runSettingsAdopted = false;

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
    return loadedRunSettings === null ? {} : { ...loadedRunSettings };
}

/**
 * Fill one notice's table and say what it shows.
 * @param {HTMLElement} notice
 */
function fillRunSettingsNotice(notice) {
    const summary = notice.querySelector(".run-settings-notice-summary");
    const table = notice.querySelector(".run-settings-notice-table");
    const body = table.querySelector("tbody");
    const adoptButton = notice.querySelector("[data-run-settings-adopt]");
    body.replaceChildren();
    if (runSettingsAdopted) {
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
    for (const difference of runSettingDifferenceRows) {
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
function renderRunSettingsNotice() {
    const differs = loadedRunSettings !== null && runSettingDifferenceRows.length > 0;
    const showNotice = (differs || runSettingsAdopted) && !runSettingsNoticeDismissed;
    for (const notice of runSettingsNotices) {
        const onRunCard = notice.closest("#screen-run") !== null;
        const runCardReady =
            !onRunCard || window.fim.getRunViewState() === "initial";
        notice.hidden = !(showNotice && runCardReady);
        if (!notice.hidden) {
            fillRunSettingsNotice(notice);
        }
    }
    runSettingsStripItem.hidden = !differs;
    const count = runSettingDifferenceRows.length;
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
    loadedRunSettings = result.runSettings ? { ...result.runSettings } : null;
    runSettingDifferenceRows = result.runSettingDifferences || [];
    runSettingsNoticeDismissed = false;
    runSettingsAdopted = false;
    renderRunSettingsNotice();
}

window.fim.applyLoadedRunSettings = applyLoadedRunSettings;

/** Go back to Settings for the run settings (a fresh configuration). */
function clearLoadedRunSettings() {
    loadedRunSettings = null;
    runSettingDifferenceRows = [];
    runSettingsNoticeDismissed = false;
    runSettingsAdopted = false;
    renderRunSettingsNotice();
}

window.fim.clearLoadedRunSettings = clearLoadedRunSettings;

/**
 * Compare the run's own run settings with Settings again -- after
 * Settings change, so the notice never shows a stale "your Settings".
 */
async function refreshRunSettingDifferences() {
    if (loadedRunSettings === null) {
        return;
    }
    runSettingDifferenceRows = await window.pywebview.api.get_run_setting_differences(
        loadedRunSettings
    );
    renderRunSettingsNotice();
}

window.fim.refreshRunSettingDifferences = refreshRunSettingDifferences;

/**
 * "Make these my Settings": save the run's own run settings as Settings,
 * through the same `set_default_run_settings` call the Settings dialog
 * makes. `max_workers` is a machine setting no configuration names, so
 * the saved one is kept.
 * @param {HTMLElement} notice The notice whose button was pressed, for
 *     reporting a failure.
 */
async function adoptLoadedRunSettings(notice) {
    if (loadedRunSettings === null) {
        return;
    }
    window.__fimRunSettingsNoticeSettled = false;
    try {
        const current = await window.pywebview.api.get_default_run_settings();
        const result = await window.pywebview.api.set_default_run_settings({
            ...current,
            ...loadedRunSettings,
        });
        if (!result.ok) {
            notice.querySelector(".run-settings-notice-summary").textContent =
                `Your Settings could not be changed: ${result.message}`;
            return;
        }
        runSettingsAdopted = true;
        runSettingsNoticeDismissed = false;
        await refreshRunSettingDifferences();
        renderRunSettingsNotice();
    } finally {
        window.__fimRunSettingsNoticeSettled = true;
    }
}

for (const notice of runSettingsNotices) {
    notice
        .querySelector("[data-run-settings-adopt]")
        .addEventListener("click", () => adoptLoadedRunSettings(notice));
    notice.querySelector("[data-run-settings-dismiss]").addEventListener("click", () => {
        runSettingsNoticeDismissed = true;
        runSettingsAdopted = false;
        renderRunSettingsNotice();
    });
}

// The strip item also opens Configure (`nav-rail.js` wires every
// `.parameter-strip-item`); here it brings a dismissed notice back.
runSettingsStripItem.addEventListener("click", () => {
    runSettingsNoticeDismissed = false;
    renderRunSettingsNotice();
});
