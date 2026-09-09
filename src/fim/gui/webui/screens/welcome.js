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
 */

const welcomeDialog = document.getElementById("modal-welcome");

welcomeDialog.addEventListener("close", () => {
    // Fire-and-forget: nothing on screen depends on this call's own
    // return value, and the panel is already gone by the time it
    // resolves either way.
    window.pywebview.api.dismiss_welcome();
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
        window.fim.wireModal("modal-welcome");
        welcomeDialog.showModal();
    }
};
