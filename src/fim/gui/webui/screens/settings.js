"use strict";

const settingsButton = document.getElementById("settings-button");
const settingsDialog = document.getElementById("modal-settings");
const settingsBanner = document.getElementById("settings-banner");
const startupBehaviorSelect = document.getElementById("settings-startup-behavior");

function showSettingsBanner(message) {
    if (!message) {
        settingsBanner.hidden = true;
        settingsBanner.textContent = "";
        return;
    }
    settingsBanner.hidden = false;
    settingsBanner.textContent = message;
}

async function loadSettingsDialog() {
    showSettingsBanner("");
    startupBehaviorSelect.value = await window.pywebview.api.get_startup_behavior();
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
