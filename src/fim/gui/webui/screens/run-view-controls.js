"use strict";

/* The unified run view's always-present controls (unified-run-view
 * design §3.1.4, §3.7, §8 Phase E) -- Run simulation/Cancel/Open
 * output folder, plus File-menu-driven load/save/open-run actions, and
 * the shared
 * status banner/reason line, none of which depend on which of
 * `initial`/`running`/`completed` is currently showing. `run-view-
 * initial.js`/`run-view-running.js`/`run-view-completed.js` each only
 * toggle `cancelButton.disabled`/`openFolderButton.hidden` to match
 * their own state, rather than owning a second copy of either button.
 */

const runBanner = document.getElementById("run-banner");
const runReason = document.getElementById("run-reason");
const runButton = document.getElementById("run-button");
const cancelButton = document.getElementById("cancel-run-button");
const openFolderButton = document.getElementById("open-folder-button");

function showRunBanner(message) {
    if (!message) {
        runBanner.hidden = true;
        runBanner.textContent = "";
        return;
    }
    runBanner.hidden = false;
    runBanner.textContent = message;
}

/**
 * "Run simulation" -- works identically from any state (design §3.2.1:
 * a fresh click while `completed` is showing is itself one of the two
 * ways `completed → initial → running` happens, reusing the current
 * form values, no separate "New run" step needed).
 */
async function onRunClicked() {
    const result = await revalidate();
    if (!result.ok) {
        window.fim.focusInvalidField(result.field);
        return;
    }
    // Design §3.1: "clicking 'Run simulation' anywhere in Configure
    // jumps to the Run destination" -- `fim.menu.runSimulation` (the
    // Configure footer's own button, and the native Run menu) already
    // gets this by navigating *before* clicking this same button, but a
    // valid submission through this handler is the one genuine
    // "starting a run" event regardless of which caller reached it, so
    // it belongs here too, not only in one caller's own wrapper. Real
    // usage never needed this explicitly before Home became this app's
    // own default landing screen (`run-button` physically lives inside
    // `screen-run`, unreachable by a real click while hidden), but a
    // scripted `.click()` can still reach it from elsewhere (`test/
    // gui/*.py`'s own trigger scripts), and this handler's own
    // trajectory/scatter canvases both size themselves from `client
    // Width`/`clientHeight` at draw time -- a canvas drawn while its
    // section is `hidden` (`display: none`, `clientWidth === 0`) sticks
    // at a stale, wrong size until something else happens to redraw it
    // later, `scatter.js`'s own `ResizeObserver` safety net notwith-
    // standing (`run-trajectory-canvas` has no such observer at all).
    // Showing the real destination unconditionally, before any of that
    // drawing starts, is simpler and more robust than teaching every
    // canvas its own resize-recovery path.
    window.fim.showScreen("screen-run");
    const values = collectFormValues();
    // Enter `running` now, synchronously, before awaiting the bridge
    // call -- not after it resolves. `Api.start_run` starts the run
    // and returns as soon as the worker thread is launched, so for a
    // fast run the whole thing (including its own `onRunDone` push)
    // can land before this `await` ever continues; leaving whatever
    // `completed` was still showing on screen for that entire window
    // means a fresh run started from `completed` briefly (or, for a
    // fast enough run, never visibly) looks like nothing happened --
    // the prior run's own results just sit there. Clearing to
    // `running` first removes that stale view immediately, the same
    // "the state a click causes has to be visible before its own
    // bridge round-trip, not after" fix already needed for Cancel's
    // button-enable timing.
    showRunBanner("");
    // `n_replicates` is a Settings-only default now (Configure's own
    // `<form>` never submits it), so `values` alone cannot say whether
    // this is a batch -- entering a provisional scalar-shaped `running`
    // state first (clearing any stale `completed` view immediately, the
    // same "visible before its own bridge round trip" reasoning as
    // Cancel's button-enable timing) and correcting it below from
    // `start_run`'s own real, validated answer (`started.isBatch`)
    // once it resolves -- the "batch or scalar" toggle design §4.1
    // already established server-side (`Api._start_batch_run`'s own
    // dispatch), reported back explicitly since this caller can no
    // longer compute it locally.
    window.fim.enterRunningState(false);
    const started = await window.pywebview.api.start_run(values);
    if (!started.ok) {
        // Rare (an output-directory collision retry timing out, or a
        // validation edge case `revalidate` above did not catch) --
        // revert to `initial` so the form stays editable and the
        // error is visible, matching the pre-Phase-E behavior of
        // never leaving the input screen on a failed start.
        window.fim.enterInitialState();
        showRunBanner(started.message);
        return;
    }
    window.fim.enterRunningState(Boolean(started.isBatch));
    // The live trajectory panel's own predicted-equilibrium reference
    // line (design §6.2) -- `started.equilibrium` is `undefined` for a
    // batch (`_start_batch_run` sends no such field at all) and `null`
    // for a scalar run whose `N`/`m`/`mu` are not all plain scalars;
    // `setLiveEquilibriumReference` treats both the same as "nothing to
    // overlay," matching `renderTrajectory`'s own "nothing to show,
    // don't draw anything" discipline.
    window.fim.setLiveEquilibriumReference(started.equilibrium);
    // The live trajectory panel's own identity-recovery curve overlay
    // (design §6.2) -- same reasoning and the same `undefined`-for-a-
    // batch/`null`-for-a-non-scalar-configuration shape as `started.
    // equilibrium` immediately above.
    window.fim.setLiveIdentityRecoveryReference(started.identityRecovery);
}

async function onLoadYamlClicked() {
    const result = await window.pywebview.api.load_yaml();
    if (!result.ok) {
        if (result.message) {
            showRunBanner(result.message);
        }
        return;
    }
    showRunBanner("");
    applyFormValues(result.values);
    await revalidate();
}

async function onSaveYamlClicked() {
    const values = collectFormValues();
    const result = await window.pywebview.api.save_yaml(values);
    if (!result.ok) {
        if (result.message) {
            showRunBanner(result.message);
        }
        return;
    }
    showRunBanner("");
    runReason.textContent = `Saved to ${result.path}`;
}

window.fim.openConfiguration = async function openConfiguration() {
    await onLoadYamlClicked();
};

window.fim.saveConfiguration = async function saveConfiguration() {
    await onSaveYamlClicked();
};

async function onCancelClicked() {
    cancelButton.disabled = true;
    // `window.__fimCancelRunSettled`, not only `cancelButton.disabled`
    // (which flips synchronously, before this bridge call's own promise
    // ever resolves): a test that tears its window down the instant a
    // DOM-visible effect appears can destroy the window while `cancel_
    // run()`'s own return value is still in flight back to pywebview's
    // own JS bridge, throwing on that now-orphaned delivery thread and
    // hanging the whole interpreter at shutdown -- confirmed live,
    // `screens/run-view-running.js`'s own module docstring has the
    // full history.
    window.__fimCancelRunSettled = false;
    await window.pywebview.api.cancel_run();
    window.__fimCancelRunSettled = true;
}

async function onOpenFolderClicked() {
    const outputDirectory = window.fim.getCompletedOutputDirectory();
    if (outputDirectory === null) {
        return;
    }
    // `window.__fimOpenFolderSettled` -- the same settle-flag fix, and
    // the same reason, as `onCancelClicked` above: a fire-and-forget
    // bridge call left in flight when a test's window is destroyed can
    // hang interpreter shutdown, and this button has no DOM-visible
    // effect at all to (mis-)use as a settle signal.
    window.__fimOpenFolderSettled = false;
    await window.pywebview.api.open_output_folder(outputDirectory);
    window.__fimOpenFolderSettled = true;
}

function wireRunViewControls() {
    runButton.addEventListener("click", onRunClicked);
    cancelButton.addEventListener("click", onCancelClicked);
    openFolderButton.addEventListener("click", onOpenFolderClicked);
}

window.fim.showRunBanner = showRunBanner;

whenApiReady(wireRunViewControls);
