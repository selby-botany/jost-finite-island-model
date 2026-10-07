"use strict";

const settingsButton = document.getElementById("settings-button");
const settingsDialog = document.getElementById("modal-settings");
const settingsBanner = document.getElementById("settings-banner");
const startupBehaviorSelect = document.getElementById("settings-startup-behavior");
const defaultPloidySelect = document.getElementById("settings-default-ploidy");
const runGraphColumnsSelect = document.getElementById("settings-run-graph-columns");
const scatterStyleSelect = document.getElementById("settings-scatter-style");
const statisticsList = document.getElementById("settings-statistics-list");
const statisticsFilter = document.getElementById("settings-statistics-filter");
const pairwiseMaxDemesInput = document.getElementById("settings-pairwise-max-demes");

// Section titles for the catalog's `group` values, in display order.
const STATISTIC_GROUP_TITLES = {
    differentiation: "Differentiation",
    diversity: "Diversity",
    distance: "Distance",
    identity: "Gene identity",
    "nei-distance": "Nei distances",
    "nei-identity": "Nei identities",
};

// The preset buttons. Each returns the keys it shows; a preset replaces
// the current choice rather than adding to it.
const STATISTICS_PRESETS = {
    essentials: () => [...defaultShownStatistics],
    differentiation: () =>
        STATISTIC_CATALOG.filter((spec) =>
            ["differentiation", "diversity"].includes(spec.group)
        ).map((spec) => spec.key),
    // Nei's own multi-locus rule; the per-locus-mean forms are one click
    // away in the list below.
    distances: () =>
        STATISTIC_CATALOG.filter(
            (spec) =>
                spec.nei && spec.nei[0] === "distance" && spec.nei[2] === "pooled"
        ).map((spec) => spec.key),
    everything: () => STATISTIC_CATALOG.map((spec) => spec.key),
};

const settingsEngineBackendSelect = document.getElementById("settings-engine_backend");
const settingsNReplicatesInput = document.getElementById("settings-n_replicates");
const settingsMaxGenerationsInput = document.getElementById("settings-max_generations");
const settingsReplicateConfidenceSelect = document.getElementById(
    "settings-replicate_confidence"
);
const settingsMaxWorkersInput = document.getElementById("settings-max_workers");
const settingsMaxConcurrentReplicatesInput = document.getElementById(
    "settings-max_concurrent_replicates"
);
const settingsConvergenceWindowInput = document.getElementById(
    "settings-convergence_window"
);
const settingsConvergenceToleranceInput = document.getElementById(
    "settings-convergence_tolerance"
);
const settingsJitField = document.getElementById("settings-jit-field");
const settingsJitSelect = document.getElementById("settings-jit");
const settingsAutoVectorFields = document.getElementById("settings-auto-vector-fields");
const settingsAutoVectorMinDInput = document.getElementById("settings-auto_vector_min_d");
const settingsAutoVectorMaxCapacityInput = document.getElementById(
    "settings-auto_vector_max_capacity"
);
const settingsSaveButton = document.getElementById("settings-save-button");

const settingsResultsLocationInput = document.getElementById(
    "settings-results-location"
);
const settingsResultsLocationChangeButton = document.getElementById(
    "settings-results-location-change-button"
);
const settingsResultsLocationOverriddenHint = document.getElementById(
    "settings-results-location-overridden-hint"
);

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
 * Reveal `jit` only for the one execution engine it is a real, user-
 * facing choice for (`lineal` never accepts anything but off;
 * `generational-vector` always uses numba regardless), and the
 * `auto_vector_*` pair only for the `auto` engine, the only one either
 * threshold affects (`FIELD_HELP.jit`/`FIELD_HELP.auto_vector_min_d`'s
 * own wording) -- "where apropos," a real, reported request.
 */
function syncSettingsEngineBackendVisibility() {
    const backend = settingsEngineBackendSelect.value;
    settingsJitField.hidden = backend !== "generational";
    settingsAutoVectorFields.hidden = backend !== "auto";
}

settingsEngineBackendSelect.addEventListener(
    "change",
    syncSettingsEngineBackendVisibility
);

/**
 * Collect Settings' own execution-default fields into the same
 * `dict[str, str]` shape `Api.set_default_run_settings` expects
 * (`config_form.DEFAULT_RUN_SETTING_FIELD_NAMES`'s own key set, plus
 * `max_workers` -- not a `SimulationParams` field, handled as a special
 * case on both sides of the bridge, `Api.get_default_run_settings`'s own
 * docstring).
 * @returns {Record<string, string>}
 */
function collectDefaultRunSettingsValues() {
    return {
        engine_backend: settingsEngineBackendSelect.value,
        n_replicates: settingsNReplicatesInput.value,
        max_generations: settingsMaxGenerationsInput.value,
        convergence_window: settingsConvergenceWindowInput.value,
        convergence_tolerance: settingsConvergenceToleranceInput.value,
        replicate_confidence: settingsReplicateConfidenceSelect.value,
        jit: settingsJitSelect.value,
        auto_vector_min_d: settingsAutoVectorMinDInput.value,
        auto_vector_max_capacity: settingsAutoVectorMaxCapacityInput.value,
        max_workers: settingsMaxWorkersInput.value,
        max_concurrent_replicates: settingsMaxConcurrentReplicatesInput.value,
    };
}

/**
 * Seed Settings' own execution-default fields from `Api.get_default_
 * run_settings`'s own return shape -- the inverse of
 * `collectDefaultRunSettingsValues`, called once when the dialog opens.
 * @param {Record<string, string>} values
 */
function applyDefaultRunSettingsValues(values) {
    settingsEngineBackendSelect.value = values.engine_backend;
    settingsNReplicatesInput.value = values.n_replicates;
    settingsMaxGenerationsInput.value = values.max_generations;
    settingsConvergenceWindowInput.value = values.convergence_window;
    settingsConvergenceToleranceInput.value = values.convergence_tolerance;
    settingsReplicateConfidenceSelect.value = values.replicate_confidence;
    settingsJitSelect.value = values.jit;
    settingsAutoVectorMinDInput.value = values.auto_vector_min_d;
    settingsAutoVectorMaxCapacityInput.value = values.auto_vector_max_capacity;
    settingsMaxWorkersInput.value = values.max_workers;
    settingsMaxConcurrentReplicatesInput.value = values.max_concurrent_replicates;
    syncSettingsEngineBackendVisibility();
}

/**
 * Seed the "Storage location" field from `Api.get_results_location`
 * (design doc `20260918-claude-sonnet-5-configurable-storage-root-
 * design.md`, `selby/restricted`, §5 Option D1) -- when a `--root`/
 * `--results-directory` flag or `FIM_HOME`/`FIM_RESULTS_DIRECTORY`
 * already governs this process, `editable` is false: the field is
 * shown read-only with an explanatory hint instead of a working Change
 * button, since a Settings change here would have no visible effect
 * until that flag/variable is itself removed.
 */
async function loadSettingsResultsLocation() {
    const location = await window.pywebview.api.get_results_location();
    settingsResultsLocationInput.value = location.path;
    settingsResultsLocationChangeButton.hidden = !location.editable;
    settingsResultsLocationOverriddenHint.hidden = location.editable;
}

settingsResultsLocationChangeButton.addEventListener("click", async () => {
    const picked = await window.pywebview.api.browse_for_results_location();
    if (!picked.ok) {
        return;
    }
    const result = await window.pywebview.api.set_results_location(picked.path);
    if (!result.ok) {
        showSettingsBanner(result.message);
        return;
    }
    showSettingsBanner("");
    settingsResultsLocationInput.value = picked.path;
});

async function loadSettingsDialog() {
    showSettingsBanner("");
    startupBehaviorSelect.value = await window.pywebview.api.get_startup_behavior();
    defaultPloidySelect.value = await window.pywebview.api.get_default_ploidy();
    const runCardLayout = await window.pywebview.api.get_run_card_layout();
    runGraphColumnsSelect.value = String(runCardLayout.columns);
    scatterStyleSelect.value = runCardLayout.scatterStyle;
    applyDefaultRunSettingsValues(await window.pywebview.api.get_default_run_settings());
    renderStatisticsChooser();
    pairwiseMaxDemesInput.value = String(
        await window.pywebview.api.get_pairwise_max_demes()
    );
    await loadSettingsResultsLocation();
}

/**
 * Build the "Statistics shown" checklist from the catalog, grouped, with
 * each statistic's one-sentence description beneath its name.
 */
function renderStatisticsChooser() {
    statisticsList.replaceChildren();
    for (const [group, title] of Object.entries(STATISTIC_GROUP_TITLES)) {
        const members = STATISTIC_CATALOG.filter((spec) => spec.group === group);
        if (members.length === 0) {
            continue;
        }
        const section = document.createElement("div");
        section.className = "settings-statistics-group";
        const heading = document.createElement("h4");
        heading.textContent = title;
        section.appendChild(heading);
        for (const spec of members) {
            const item = document.createElement("label");
            item.className = "settings-statistics-item";
            item.dataset.filterText =
                `${spec.label_text} ${spec.description}`.toLowerCase();
            const box = document.createElement("input");
            box.type = "checkbox";
            box.value = spec.key;
            box.checked = isStatisticShown(spec.key);
            const name = document.createElement("span");
            name.innerHTML = spec.label_html;
            const description = document.createElement("span");
            description.className = "settings-statistics-description";
            description.textContent =
                spec.scope === "pair"
                    ? `Uses the deme pair chosen for the scatter plot. ${spec.description}`
                    : spec.description;
            item.append(box, " ", name, description);
            // The catalog's "opt_in" statistics cost time every
            // generation once shown (`Api._merge_default_run_settings`).
            if (spec.history === "opt_in") {
                item.title = EXPENSIVE_STATISTIC_NOTE;
                const cost = document.createElement("span");
                cost.className = "settings-statistics-cost";
                cost.textContent = EXPENSIVE_STATISTIC_NOTE;
                item.appendChild(cost);
            }
            section.appendChild(item);
        }
        statisticsList.appendChild(section);
    }
    applyStatisticsFilter();
}

/**
 * Hide checklist entries whose name and description do not contain the
 * filter text.
 */
function applyStatisticsFilter() {
    const needle = statisticsFilter.value.trim().toLowerCase();
    for (const item of statisticsList.querySelectorAll(".settings-statistics-item")) {
        item.hidden = needle !== "" && !item.dataset.filterText.includes(needle);
    }
}

/**
 * Save a new shown set and apply it everywhere at once.
 *
 * @param {string[]} keys
 */
async function saveShownStatistics(keys) {
    const result = await window.pywebview.api.set_shown_statistics(keys);
    window.fim.setShownStatistics(result.shown);
    for (const box of statisticsList.querySelectorAll("input[type=checkbox]")) {
        box.checked = isStatisticShown(box.value);
    }
}

statisticsList.addEventListener("change", async () => {
    const keys = Array.from(
        statisticsList.querySelectorAll("input[type=checkbox]:checked"),
        (box) => box.value
    );
    await saveShownStatistics(keys);
});

statisticsFilter.addEventListener("input", applyStatisticsFilter);

for (const button of document.querySelectorAll("[data-statistics-preset]")) {
    button.addEventListener("click", async () => {
        await saveShownStatistics(STATISTICS_PRESETS[button.dataset.statisticsPreset]());
    });
}

pairwiseMaxDemesInput.addEventListener("change", async () => {
    const result = await window.pywebview.api.set_pairwise_max_demes(
        Number(pairwiseMaxDemesInput.value)
    );
    showSettingsBanner(result.ok ? "" : result.message);
});

/**
 * Open Settings scrolled to "Statistics shown" (the statistics panel's
 * own "Choose statistics…" button).
 */
window.fim.openStatisticsSettings = async function openStatisticsSettings() {
    await loadSettingsDialog();
    window.fim.wireModal("modal-settings");
    settingsDialog.showModal();
    document.getElementById("settings-statistics").scrollIntoView({ block: "start" });
};

settingsButton.addEventListener("click", async () => {
    await loadSettingsDialog();
    window.fim.wireModal("modal-settings");
    settingsDialog.showModal();
});

// One "Choose…" button in each statistics panel's caption.
for (const button of document.querySelectorAll(".stats-choose-button")) {
    button.addEventListener("click", (event) => {
        event.stopPropagation();
        window.fim.openStatisticsSettings();
    });
}

// Settings saves still awaiting their bridge call (the
// `window.__fimGroupTogglesPending` shape). Each select below saves on
// its own `change`, with nothing on screen marking when the save has
// landed, so a caller reading a saved value back first waits for this
// to return to zero.
window.__fimSettingsSavesPending = 0;

/**
 * Count `save` in `window.__fimSettingsSavesPending` until it settles.
 * @param {Promise<*>} save - A settings save's bridge call.
 * @returns {Promise<*>} The bridge call's own result.
 */
async function trackSettingsSave(save) {
    window.__fimSettingsSavesPending += 1;
    try {
        return await save;
    } finally {
        window.__fimSettingsSavesPending -= 1;
    }
}

startupBehaviorSelect.addEventListener("change", async () => {
    const result = await trackSettingsSave(
        window.pywebview.api.set_startup_behavior(startupBehaviorSelect.value)
    );
    if (!result.ok) {
        showSettingsBanner(result.message);
    }
});

runGraphColumnsSelect.addEventListener("change", async () => {
    const result = await trackSettingsSave(
        window.pywebview.api.set_run_graph_columns(Number(runGraphColumnsSelect.value))
    );
    if (result.ok) {
        window.fim.setGraphColumns(result.columns);
    } else {
        showSettingsBanner(result.message);
    }
});

scatterStyleSelect.addEventListener("change", async () => {
    const result = await trackSettingsSave(
        window.pywebview.api.set_scatter_style(scatterStyleSelect.value)
    );
    if (result.ok) {
        window.fim.setScatterStyle(result.style);
    } else {
        showSettingsBanner(result.message);
    }
});

defaultPloidySelect.addEventListener("change", async () => {
    const result = await trackSettingsSave(
        window.pywebview.api.set_default_ploidy(defaultPloidySelect.value)
    );
    if (!result.ok) {
        showSettingsBanner(result.message);
    }
});

// Collected together into one bridge call on Save, unlike startup
// behavior/significant digits/appearance above and below (each a single
// independent scalar, applied immediately on its own `change`) -- this
// is ~11 fields, and a per-field round trip for each would be chatty
// for no real benefit, since none of them takes effect until a *new*
// configuration is started anyway (`index.html`'s own comment above
// `#modal-settings` has the full account). Closes the dialog on success
// -- a real, reported bug: Save used to leave it open, indistinguishable
// from a save that silently failed.
settingsSaveButton.addEventListener("click", async () => {
    const result = await trackSettingsSave(
        window.pywebview.api.set_default_run_settings(collectDefaultRunSettingsValues())
    );
    if (!result.ok) {
        showSettingsBanner(result.message);
        return;
    }
    showSettingsBanner("");
    settingsDialog.close();
    // A loaded configuration's run settings stay with its run; say again
    // how they differ from the Settings just saved.
    await window.fim.refreshRunSettingDifferences();
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
        trackSettingsSave(window.fim.menu.setSignificantDigits(Number(select.value)));
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
