"use strict";

/* Presets (botanist GUI design doc `20260907-claude-sonnet-5-botanist-
 * gui-redesign.md` §4.5/§12): every `doc/usage.md` worked example, plus
 * any user-saved configuration, listed live from `Api.list_presets` and
 * applied to the real Configure form the same way a loaded YAML file
 * already is (`config-modals.js`'s own `applyFormValues`) -- picking
 * one is a shortcut for hand-typing that same configuration's own
 * values, never a second, independent configuration mechanism.
 *
 * Reachable from any screen via the File menu (`fim.menu.loadExample`),
 * matching `fim.menu.openRun`/`fim.menu.explore`'s own "always
 * reachable" precedent. `applyFormValues`/`revalidate`/`collectFormValues`
 * (called below) are plain top-level functions declared in `config-
 * modals.js` -- every script on this page shares one global scope
 * (`index.html` has no `type="module"` on any `<script>` tag), so no
 * import is needed; all three are only ever called from inside an
 * event handler, well after every script has finished its own
 * top-level execution, so load order between this file and
 * `config-modals.js` does not matter.
 */

const presetsDialog = document.getElementById("modal-presets");
const presetsList = document.getElementById("presets-list");
const presetsEmptyNote = document.getElementById("presets-empty-note");
const saveCurrentAsPresetButton = document.getElementById(
    "save-current-as-preset-button"
);
const savePresetDialog = document.getElementById("modal-save-preset");
const savePresetForm = document.getElementById("save-preset-form");
const savePresetNameInput = document.getElementById("save-preset-name");
const savePresetError = document.getElementById("save-preset-error");
const savePresetCancelButton = document.getElementById("save-preset-cancel-button");

const USER_PRESET_ID_PREFIX = "user:";

// A real, previously-reproduced regression this flag exists to close:
// `savePresetForm`'s own submit handler closes `modal-save-preset`
// (synchronously) *before* the `refreshPresetsList()` call that follows
// it has actually finished its own async round trip to the bridge --
// on a slower or more loaded machine, a test (or a fast, real user)
// polling only "is the save dialog closed" can observe the picker's own
// list still showing its pre-save contents, in the narrow window
// between the dialog closing and the list actually being rebuilt. Set
// `false` at the start of every `refreshPresetsList()` call and `true`
// only once the list is fully rebuilt, matching this project's own
// established `window.__fimXReady`-flag precedent
// (`__fimRunViewReady`, `__fimExploreReady`,
// `__fimOpenRunRecentRunsLoaded`) for exactly this class of hazard.
window.__fimPresetsListReady = false;

/**
 * Re-fetch every preset (built-in and user-saved alike) and rebuild the
 * picker's own list. Called on open, and again after a save or delete
 * so the list a user is looking at never goes stale mid-session.
 */
async function refreshPresetsList() {
    window.__fimPresetsListReady = false;
    const result = await window.pywebview.api.list_presets();
    presetsList.replaceChildren();
    const found = result.ok ? result.presets : [];
    presetsEmptyNote.hidden = found.length > 0;
    for (const preset of found) {
        const item = document.createElement("li");
        const openButton = document.createElement("button");
        openButton.type = "button";
        openButton.tabIndex = 0;
        openButton.textContent = preset.title;
        openButton.addEventListener("click", () => applyPreset(preset.id));
        item.appendChild(openButton);
        // Only a user-saved preset can be deleted -- a built-in worked
        // example ships with the app and has no delete affordance at
        // all, the same "read-only" distinction `fim.gui.presets`'s own
        // module docstring draws.
        if (!preset.builtin) {
            const deleteButton = document.createElement("button");
            deleteButton.type = "button";
            deleteButton.tabIndex = 0;
            deleteButton.className = "presets-delete-button";
            deleteButton.textContent = "Delete";
            deleteButton.setAttribute("aria-label", `Delete "${preset.title}"`);
            deleteButton.addEventListener("click", async (event) => {
                event.stopPropagation();
                const name = preset.id.slice(USER_PRESET_ID_PREFIX.length);
                await window.pywebview.api.delete_user_preset(name);
                await refreshPresetsList();
            });
            item.appendChild(deleteButton);
        }
        presetsList.appendChild(item);
    }
    window.__fimPresetsListReady = true;
}

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
    await refreshPresetsList();
    presetsDialog.showModal();
};

// `wireModal` (app.js) is deliberately not used for this dialog: its
// own `keydown` handler calls `dialog.close()` directly on `Return`,
// which would close without saving, bypassing `savePresetForm`'s own
// submit handler below entirely. Backdrop-click-to-close (not automatic
// for a native `<dialog>`, `wireModal`'s own doc comment already
// established why) is wired here instead, narrowly, as a Cancel.
savePresetDialog.addEventListener("click", (event) => {
    if (event.target === savePresetDialog) {
        savePresetDialog.close("cancel");
    }
});

saveCurrentAsPresetButton.addEventListener("click", () => {
    savePresetNameInput.value = "";
    savePresetError.hidden = true;
    savePresetDialog.showModal();
    savePresetNameInput.focus();
});

// A plain `type="button"`, not `type="submit"` -- clicking it must
// never trigger the name field's own `required` validation the way any
// submit button in the same form would (botanist GUI design doc §4.7:
// Cancel always means "discard, unconditionally," never "validate
// first").
savePresetCancelButton.addEventListener("click", () => {
    savePresetDialog.close("cancel");
});

// `method="dialog"`'s own default submission action is exactly
// `dialog.close(clickedButton.value)` -- `preventDefault()` here cancels
// that default the same way it would cancel a plain form's navigation,
// so the dialog stays open (and open to showing an error) until the
// real bridge call actually succeeds.
savePresetForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = savePresetNameInput.value;
    const values = collectFormValues();
    const result = await window.pywebview.api.save_current_as_preset(name, values);
    if (!result.ok) {
        savePresetError.textContent = result.message;
        savePresetError.hidden = false;
        return;
    }
    savePresetDialog.close("save");
    if (presetsDialog.open) {
        await refreshPresetsList();
    }
});
