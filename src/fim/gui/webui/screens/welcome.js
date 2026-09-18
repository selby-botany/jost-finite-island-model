"use strict";

/* The first-launch welcome panel (botanist GUI design doc `20260907-
 * claude-sonnet-5-botanist-gui-redesign.md` §10): shown once, the very
 * first time `Api.get_welcome_dismissed()` says it has not been shown
 * yet, offering "Try a worked example…" (opens the same `modal-presets`
 * gallery the File menu's own "Load example…" already does,
 * `screens/presets.js`'s `fim.menu.loadExample`) or "Start from
 * scratch" (the current, already-showing starter form -- there is
 * nothing else to do).
 *
 * Dismissal is tracked by the dialog's own native `close` event, not by
 * either button individually, so every way of leaving it (either
 * button, Escape, backdrop-click) calls `Api.dismiss_welcome()` exactly
 * once -- the simplest way to guarantee "seen once" really means once,
 * regardless of how a user actually leaves it, without duplicating that
 * call across every dismissal path by hand.
 *
 * Storage-location confirmation (Run/Study/Experiment workflow-
 * ergonomics follow-up, `20260918-claude-sonnet-5-configurable-
 * storage-root-design.md`, `selby/restricted`, §5 Option D4): folded
 * into this same, already-once-only panel rather than a new dialog or
 * an on-every-launch interruption. `welcomeStorageLocationChanged`
 * tracks whether the botanist actually picked a different folder (the
 * `close` handler only calls `Api.set_results_location` when it did,
 * so the common "left the default alone" case costs nothing beyond the
 * one already-existing `dismiss_welcome` call).
 */

const welcomeDialog = document.getElementById("modal-welcome");
const welcomeStorageLocationPath = document.getElementById(
    "welcome-storage-location-path"
);
const welcomeStorageLocationChangeButton = document.getElementById(
    "welcome-storage-location-change-button"
);

let welcomeStorageLocationChanged = false;

welcomeDialog.addEventListener("close", () => {
    // Fire-and-forget: nothing on screen depends on either call's own
    // return value, and the panel is already gone by the time either
    // resolves.
    window.pywebview.api.dismiss_welcome();
    if (welcomeStorageLocationChanged) {
        window.pywebview.api.set_results_location(
            welcomeStorageLocationPath.textContent
        );
    }
});

document
    .getElementById("welcome-try-example-button")
    .addEventListener("click", () => {
        welcomeDialog.close();
        window.fim.menu.loadExample();
    });

document
    .getElementById("welcome-start-scratch-button")
    .addEventListener("click", () => {
        welcomeDialog.close();
    });

welcomeStorageLocationChangeButton.addEventListener("click", async () => {
    const result = await window.pywebview.api.browse_for_results_location();
    if (!result.ok) {
        return;
    }
    welcomeStorageLocationPath.textContent = result.path;
    welcomeStorageLocationChanged = true;
});

/**
 * Show the welcome panel once, on a fresh launch that has not dismissed
 * it before -- called from `run-view-initial.js`'s own
 * `initializeRunView`, after the real launch sequence has already
 * settled (this panel floats over an already-correct initial view
 * rather than blocking it from ever rendering).
 */
window.fim.maybeShowWelcome = async function maybeShowWelcome() {
    const dismissed = await window.pywebview.api.get_welcome_dismissed();
    if (!dismissed) {
        const location = await window.pywebview.api.get_results_location();
        welcomeStorageLocationPath.textContent = location.path;
        // A `--root`/`--results-directory` flag or `FIM_HOME`/`FIM_
        // RESULTS_DIRECTORY` already governs this process -- "Change…"
        // would save a Settings value with no effect until that flag/
        // variable is itself removed (`Api.get_results_location`'s own
        // docstring), so it is hidden rather than offered.
        welcomeStorageLocationChangeButton.hidden = !location.editable;
        window.fim.wireModal("modal-welcome");
        welcomeDialog.showModal();
    }
};
