"use strict";

/* Presets (botanist GUI design doc `20260907-claude-sonnet-5-botanist-
 * gui-redesign.md` §4.5): every `doc/usage.md` worked example, listed
 * live from `Api.list_presets` and applied to the real Configure form
 * the same way a loaded YAML file already is (`config-modals.js`'s own
 * `applyFormValues`) -- picking one is a shortcut for hand-typing that
 * same worked example's own values, never a second, independent
 * configuration mechanism.
 *
 * Reachable from any screen via the File menu (`fim.menu.loadExample`),
 * matching `fim.menu.openRun`/`fim.menu.explore`'s own "always
 * reachable" precedent. `applyFormValues`/`revalidate` (called below)
 * are plain top-level functions declared in `config-modals.js` -- every
 * script on this page shares one global scope (`index.html` has no
 * `type="module"` on any `<script>` tag), so no import is needed; both
 * are only ever called from inside `applyPreset`, well after every
 * script has finished its own top-level execution, so load order
 * between this file and `config-modals.js` does not matter.
 */

const presetsDialog = document.getElementById("modal-presets");
const presetsList = document.getElementById("presets-list");
const presetsEmptyNote = document.getElementById("presets-empty-note");

/**
 * Apply one preset's own form values, then close the picker -- the
 * identical apply path `loadInitialForm`/"Load YAML…" already use, so a
 * preset is genuinely indistinguishable from having hand-loaded the
 * same YAML file.
 * @param {string} presetId
 */
async function applyPreset(presetId) {
    const result = await window.pywebview.api.get_preset_form_values(presetId);
    if (!result.ok) {
        presetsDialog.close();
        window.alert(`Could not load this example: ${result.message}`);
        return;
    }
    applyFormValues(result.values);
    await revalidate();
    presetsDialog.close();
    if (window.fim.getRunViewState() === "initial") {
        window.fim.renderInitialPreview();
    }
}

window.fim.menu.loadExample = async function loadExample() {
    window.fim.wireModal("modal-presets");
    const result = await window.pywebview.api.list_presets();
    presetsList.replaceChildren();
    const found = result.ok ? result.presets : [];
    presetsEmptyNote.hidden = found.length > 0;
    for (const preset of found) {
        const item = document.createElement("li");
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = preset.title;
        button.addEventListener("click", () => applyPreset(preset.id));
        item.appendChild(button);
        presetsList.appendChild(item);
    }
    presetsDialog.showModal();
};
