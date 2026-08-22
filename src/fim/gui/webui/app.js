"use strict";

/* App-wide bootstrap and the small shared namespace every screen script
 * attaches its own bridge-push handlers to (design doc §4, §7.2-§7.4).
 *
 * `window.fim` exists for exactly one reason: `Api.start_run` (design
 * §3.4's "push, not poll") calls `window.evaluate_js("fim.onRunProgress(
 * ...)")` from a background thread whenever a run reports progress --
 * that call needs a stable, always-present global to land on regardless
 * of which screen happens to be showing, so `showScreen`/`onRun*` live
 * here rather than inside `screens/progress.js` itself (which only
 * *implements* what `onRunProgress` etc. actually do once Screen 2
 * exists -- see that file). Screens not yet built register no-op
 * handlers here implicitly, by simply not overriding them.
 */

const fim = {
    /**
     * Show exactly one top-level `.screen` section, hiding the rest.
     * @param {string} screenId
     */
    showScreen(screenId) {
        for (const section of document.querySelectorAll(".screen")) {
            section.hidden = section.id !== screenId;
        }
    },

    onRunProgress() {
        // Overridden once Screen 2 (screens/progress.js) exists.
    },
    onRunDone() {
        // Overridden once Screen 2 (screens/progress.js) exists.
    },
    onRunCancelled() {
        // Overridden once Screen 2 (screens/progress.js) exists.
    },
    onRunError() {
        // Overridden once Screen 2 (screens/progress.js) exists.
    },

    // The batch (`n_replicates > 1`) counterparts `Api._start_batch_run`
    // pushes instead (design §4.1's "n_replicates *is* the toggle" —
    // the same one `start_run` call, a different message shape). No-op
    // stubs for now, matching the scalar handlers' own walking-skeleton
    // precedent above: `fim.gui.app._drain_batch_messages` already
    // calls these for real (Milestone W5's backend half), so a real
    // batch run does not throw a `JavascriptException` calling an
    // undefined function — Screen 2/4's own batch-aware rendering is
    // Milestone W5's remaining, frontend half.
    onBatchProgress() {
        // Overridden once the batch progress screen extension exists.
    },
    onBatchDone() {
        // Overridden once Screen 4 (screens/batch-results.js) exists.
    },
    onBatchCancelled() {
        // Overridden once the batch progress screen extension exists.
    },
    onBatchError() {
        // Overridden once the batch progress screen extension exists.
    },
};

window.fim = fim;

async function connectBridge() {
    const status = document.getElementById("bridge-status");
    try {
        const pong = await window.pywebview.api.ping();
        status.textContent = `Bridge connected (${pong}).`;
    } catch (error) {
        status.textContent = `Bridge error: ${error}`;
    }
}

function whenApiReady(callback) {
    if (window.pywebview && window.pywebview.api) {
        callback();
        return;
    }
    window.addEventListener("pywebviewready", callback, { once: true });
}

whenApiReady(connectBridge);
