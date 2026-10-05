"use strict";

/* Presets (botanist GUI design doc `20260907-claude-sonnet-5-botanist-
 * gui-redesign.md` §4.5/§12): every bundled example with a configuration,
 * plus any user-saved configuration, listed live from `Api.list_presets` and
 * applied to the real Configure form the same way a loaded YAML file
 * already is (`config-modals.js`'s own `applyFormValues`) -- picking
 * one is a shortcut for hand-typing that same configuration's own
 * values, never a second, independent configuration mechanism.
 *
 * Reachable from any screen via the File menu (`fim.menu.loadExample`).
 * The Examples dialog (`screens/examples.js`) is the class-organized
 * view of the same bundled examples, opened from Configure and the
 * Welcome panel; this picker stays for user-saved presets until it is
 * folded into that dialog (design doc `20261005-claude-opus-5-5-read-
 * only-examples-and-classes-design.md` §5, `selby/restricted`). This
 * menu entry point matches
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
const presetYamlDialog = document.getElementById("modal-preset-yaml");
const presetYamlTitle = document.getElementById("preset-yaml-title");
const presetYamlText = document.getElementById("preset-yaml-text");
const presetYamlCopiedNote = document.getElementById("preset-yaml-copied-note");
const presetYamlCopyButton = document.getElementById("preset-yaml-copy-button");
const configureDuplicatePresetButton = document.getElementById(
    "configure-duplicate-preset-button"
);

// Design §4.5's own "Duplicate current configuration": the title of
// whichever preset or example was most recently loaded into the live
// form via the picker or the Examples dialog, or `null` before the
// first one ever is -- `rememberLoadedPreset`, below, is the one place
// this is set;
// `configureDuplicatePresetButton`'s own click handler and `window.fim.
// clearLastLoadedPreset` (called from `fim.menu.newConfiguration`) are
// the only other places that read or clear it.
let lastLoadedPresetTitle = null;

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
        // A preset this form has no way to apply is labeled before it
        // is picked rather than only discovered by picking it.
        openButton.textContent = preset.loadable
            ? preset.title
            : `${preset.title} (view YAML only)`;
        openButton.addEventListener("click", () => applyPreset(preset.id, preset.title));
        item.appendChild(openButton);
        // Every preset, built-in or user-saved alike, gets a "View
        // YAML" affordance (design §10's own "examples library" --
        // built-in and user-saved presets share the identical `Api.
        // get_preset_yaml` mechanism, so there is no reason to offer
        // this for one kind and not the other).
        const viewYamlButton = document.createElement("button");
        viewYamlButton.type = "button";
        viewYamlButton.tabIndex = 0;
        viewYamlButton.className = "presets-view-yaml-button";
        viewYamlButton.textContent = "View YAML";
        viewYamlButton.setAttribute("aria-label", `View "${preset.title}" as YAML`);
        viewYamlButton.addEventListener("click", (event) => {
            event.stopPropagation();
            showPresetYaml(preset.id);
        });
        item.appendChild(viewYamlButton);
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
 * Show a small, dismissible, non-modal note that a worked example could
 * not be applied — `window.alert`'s blocking OS chrome is jarring
 * mid-exploration for the one built-in example this affects today, an
 * already-labeled (`refreshPresetsList`, above), expected limitation
 * rather than a real error (design doc `20260913-claude-sonnet-5-gui-
 * worked-example-loadability-design.md`, `selby/restricted`, Option C).
 * Shown on whichever screen is actually visible when the failure
 * happens — the `modal-presets` picker `applyPreset` closes first is
 * reachable from any screen (`fim.menu.loadExample`'s own docstring) —
 * rather than a
 * new, fourth-or-fifth banner element of its own. Every screen
 * `applyPreset` can actually be reached from already has its own
 * dedicated `showXBanner` function (`run-view-controls.js`,
 * `screens/open-run.js`, `screens/nav-rail.js`, `screens/compare.js`)
 * except Explore (its own banner is managed inline, not through a
 * reusable function of its own) and Help (no banner element at all) —
 * this dispatch table is built inside the function body, evaluated only
 * once actually called, rather than at this script's own top-level
 * execution: `nav-rail.js`'s own `showConfigureBanner` does not exist
 * yet at that point, since it loads after this file
 * (`index.html`'s own `<script>` order).
 * @param {string} title
 * @param {string} message
 */
function showExampleLoadNotice(title, message) {
    const banners = {
        "screen-run": showRunBanner,
        "screen-open-run": showOpenRunBanner,
        "screen-configure": showConfigureBanner,
        "screen-compare": showCompareBanner,
    };
    const currentScreenId = document.querySelector(".screen:not([hidden])")?.id;
    const showBanner = banners[currentScreenId];
    if (showBanner) {
        showBanner(`Could not load "${title}": ${message}`);
    } else {
        window.alert(`Could not load "${title}": ${message}`);
    }
}

/**
 * Apply one preset's own form values, then close the picker -- the
 * identical apply path `loadInitialForm`/"Load YAML…" already use, so a
 * preset is genuinely indistinguishable from having hand-loaded the
 * same YAML file.
 * @param {string} presetId
 * @param {string} presetTitle
 * @returns {Promise<boolean>} whether the preset actually applied.
 */
async function applyPreset(presetId, presetTitle) {
    const result = await window.pywebview.api.load_preset(presetId);
    if (!result.ok) {
        presetsDialog.close();
        showExampleLoadNotice(presetTitle, result.message);
        return false;
    }
    applyFormValues(result.values);
    await revalidate();
    presetsDialog.close();
    if (window.fim.getRunViewState() === "initial") {
        window.fim.renderInitialPreview();
    }
    rememberLoadedPreset(presetTitle);
    return true;
}

/**
 * Remember the title of a configuration just loaded from a preset or an
 * example, enabling "Duplicate current configuration" (design §4.5).
 * Called only after a *successful* load -- there is nothing to fork from
 * a preset that was never actually applied. Never cleared by a later
 * form edit: forking "the loaded preset, plus whatever I have tweaked
 * since" is exactly the point, not only forking it verbatim. Shared with
 * the Examples dialog (`screens/examples.js`).
 * @param {string} title
 */
function rememberLoadedPreset(title) {
    lastLoadedPresetTitle = title;
    configureDuplicatePresetButton.disabled = false;
}

window.fim.rememberLoadedPreset = rememberLoadedPreset;


// Set once `showPresetYaml`'s own bridge call has settled and the
// dialog is genuinely showing the requested preset's own text --
// `window.__fimXReady`-flag precedent (`__fimPresetsListReady`, above)
// for the same reason: a test polling only "is the dialog open" could
// otherwise observe it open with the *previous* preset's text still in
// the textarea, in the narrow window before this async call resolves.
window.__fimPresetYamlReady = false;

/**
 * Fetch one preset's own YAML text and show it in `modal-preset-yaml`.
 * @param {string} presetId
 */
async function showPresetYaml(presetId) {
    window.__fimPresetYamlReady = false;
    const result = await window.pywebview.api.get_preset_yaml(presetId);
    if (!result.ok) {
        window.alert(`Could not load this example: ${result.message}`);
        return;
    }
    presetYamlTitle.textContent = result.title;
    presetYamlText.value = result.yaml;
    presetYamlCopiedNote.hidden = true;
    window.fim.wireModal("modal-preset-yaml");
    presetYamlDialog.showModal();
    window.__fimPresetYamlReady = true;
}

// The Examples dialog's "View YAML" (`screens/examples.js`) opens this
// same dialog on top of itself.
window.fim.showPresetYaml = showPresetYaml;

// Set once a "Copy to clipboard" click has actually finished writing --
// same flag idiom as `__fimPresetYamlReady` above, for the identical
// reason: `navigator.clipboard.writeText` is itself async, and a test
// clicking this button needs a real signal to wait on rather than
// guessing how long a clipboard write takes.
window.__fimPresetYamlCopyReady = false;

presetYamlCopyButton.addEventListener("click", async () => {
    window.__fimPresetYamlCopyReady = false;
    await navigator.clipboard.writeText(presetYamlText.value);
    presetYamlCopiedNote.hidden = false;
    window.__fimPresetYamlCopyReady = true;
});

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

// Design §4.5's own "Duplicate current configuration" -- reuses the
// identical `modal-save-preset` dialog and `savePresetForm` submit
// handler "Save current as…" already does (below), pre-filled with a
// suggested name rather than blank, and saving whatever the live form
// currently holds (not the original preset's own stored values) -- a
// field already tweaked since loading is duplicated right along with
// everything else, exactly the "start from everything held fixed"
// workflow design §4.5 names. The suggested name is only ever a
// starting point: `savePresetNameInput` stays a plain, editable text
// field, and `save_current_as_preset` already silently overwrites an
// existing name (`Api.save_current_as_preset`'s own docstring), the
// identical risk "Save current as…" already carries for any name.
configureDuplicatePresetButton.addEventListener("click", () => {
    savePresetNameInput.value = `${lastLoadedPresetTitle} copy`;
    savePresetError.hidden = true;
    savePresetDialog.showModal();
    savePresetNameInput.focus();
    savePresetNameInput.select();
});

window.fim.clearLastLoadedPreset = function clearLastLoadedPreset() {
    lastLoadedPresetTitle = null;
    configureDuplicatePresetButton.disabled = true;
};

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
