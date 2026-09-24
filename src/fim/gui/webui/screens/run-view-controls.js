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
const runStudySelect = document.getElementById("run-study-select");
const runStudyNewRow = document.getElementById("run-study-new-row");
const runStudyNewNameInput = document.getElementById("run-study-new-name");
const runStudyNewCreateButton = document.getElementById("run-study-new-create-button");
const runStudyNewCancelButton = document.getElementById("run-study-new-cancel-button");
const runStudyNewExperimentSelect = document.getElementById("run-study-new-experiment");

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
    // "New study…" selected but never actually created (or cancelled)
    // -- `Api.start_run` would otherwise reject this as an unknown
    // study id, a confusing failure mode for a botanist who simply
    // has not finished the inline row yet. Checked before `revalidate`
    // below so this reads as its own, more specific message rather
    // than piggybacking on the form's own field-level errors.
    if (runStudySelect.value === "__new__") {
        showRunBanner("create or cancel the new study first");
        runStudyNewNameInput.focus();
        return;
    }
    const result = await revalidate();
    if (!result.ok) {
        window.fim.focusInvalidField(result.field);
        return;
    }
    // Configure's Sweep box: Run means Run, so with it on this same button
    // starts the sweep (`sweep.js`) into the same study choice.
    if (window.fim.isSweepEnabled()) {
        await window.fim.runConfiguredSweep(runStudySelect.value || null);
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
    // later. Showing the real destination unconditionally, before any
    // of that drawing starts, is simpler and more robust than teaching
    // every canvas its own resize-recovery path; the graph stage's own
    // per-pane `ResizeObserver` wiring (`run-graph-stage.js`) is the
    // uniform backstop for any draw path that still lands on a hidden
    // section (an opened run's own `enterCompletedState` draws before
    // its own `showScreen`).
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
    // `run-study-select`'s own current value (Run/Study/Experiment
    // workflow-ergonomics design `20260917-claude-sonnet-5-run-study-
    // experiment-workflow-ergonomics.md`, `selby/restricted`, item 3)
    // -- read here, at the moment "Run" is actually clicked, not
    // wherever the click originated from; see that `<select>`'s own
    // comment in `index.html` for why this works regardless of which
    // screen was showing a moment ago. Empty string (its own "No
    // study" default option) becomes `null`, `Api.start_run`'s own
    // "attach nothing" case -- identical to a plain `fim run` with no
    // `--study` flag at the CLI layer.
    const studyId = runStudySelect.value || null;
    const started = await window.pywebview.api.start_run(values, studyId);
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
    if (started.reused) {
        // Already computed: nothing was started. Show the existing run the
        // way Home opens it, and say so.
        window.fim.enterInitialState();
        await window.fim.openComputedRun(started.directory, Boolean(started.isBatch));
        showRunBanner(
            "This configuration was already computed, so nothing was run. " +
                "Showing the existing result."
        );
        return;
    }
    // Only the run-kind-dependent panels, not the whole running state:
    // the run has been going since `start_run` executed server-side, so
    // its first progress pushes can already have landed while this same
    // call was still being awaited. Re-entering the full state here
    // would throw those away -- see `applyRunKind`'s own comment
    // (`run-view-running.js`) for the list, and for the intermittent CI
    // failure that surfaced it.
    window.fim.applyRunKind(Boolean(started.isBatch));
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

// Bumped by every `refreshRunStudySelectOptions` call, at its own
// start -- lets a call whose own fetch resolves *after* a later call
// already started (both in flight at once: launch's own `wireRunView
// Controls` call, still pending, and a near-immediate `showConfigure
// Screen` visit) recognize it is now stale and skip mutating the
// select at all, rather than clobbering whatever the newer call (or a
// selection made in between) already wrote. A real, reproduced race,
// not a hypothetical one -- confirmed live, driving Configure this
// same way a fraction of a second after launch.
let runStudyRefreshToken = 0;

/**
 * Repopulate `run-study-select` from `Api.list_studies()` -- called once
 * at launch (`wireRunViewControls`, below) and again every time
 * Configure is shown (`screens/nav-rail.js`'s own `showConfigureScreen`)
 * so a Study created since app launch appears with no restart needed,
 * the same "never trust a stale fetch across visits" precedent `screens/
 * open-run.js`'s own `refreshHomeExampleOptions` already established.
 * Preserves the currently selected study, if it still exists after the
 * refetch, rather than silently resetting to "No study" underneath a
 * choice the botanist already made this session.
 */
async function refreshRunStudySelectOptions() {
    const token = ++runStudyRefreshToken;
    const previousValue = runStudySelect.value;
    const studies = await window.pywebview.api.list_studies();
    if (token !== runStudyRefreshToken) {
        return;
    }
    runStudySelect.replaceChildren();
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "No study";
    runStudySelect.appendChild(placeholder);
    // Re-added on every rebuild -- `replaceChildren()` just above wipes
    // `index.html`'s own static copy along with every real Study option.
    const newStudyOption = document.createElement("option");
    newStudyOption.value = "__new__";
    newStudyOption.textContent = "New study…";
    runStudySelect.appendChild(newStudyOption);
    for (const study of studies) {
        const option = document.createElement("option");
        option.value = study.studyId;
        option.textContent = study.name;
        runStudySelect.appendChild(option);
    }
    if (studies.some((study) => study.studyId === previousValue)) {
        runStudySelect.value = previousValue;
    }
    // A refresh mid-creation (rare -- Configure is only re-shown by
    // navigating to it, which the inline row itself never triggers) is
    // still handled correctly rather than left showing a stale row: the
    // preservation check above never matches `"__new__"` (no real Study
    // ever has that id), so the select falls back to its own first
    // option ("No study") on any refresh that does not restore it.
    syncRunStudyNewRowVisibility();
}

window.fim.refreshRunStudySelectOptions = refreshRunStudySelectOptions;

/** Show/hide `run-study-new-row` to match `run-study-select`'s own current value. */
function syncRunStudyNewRowVisibility() {
    runStudyNewRow.hidden = runStudySelect.value !== "__new__";
    if (!runStudyNewRow.hidden) {
        populateRunStudyNewExperiments();
    }
}

/**
 * Fill the new-study row's experiment choice: no experiment, or one that
 * exists. A study is placed in an experiment when it is created.
 */
async function populateRunStudyNewExperiments() {
    const experiments = await window.pywebview.api.list_experiments();
    const previous = runStudyNewExperimentSelect.value;
    runStudyNewExperimentSelect.replaceChildren();
    const none = document.createElement("option");
    none.value = "";
    none.textContent = "No experiment";
    runStudyNewExperimentSelect.appendChild(none);
    for (const experiment of experiments) {
        const option = document.createElement("option");
        option.value = experiment.experimentId;
        option.textContent = experiment.name;
        runStudyNewExperimentSelect.appendChild(option);
    }
    if (experiments.some((experiment) => experiment.experimentId === previous)) {
        runStudyNewExperimentSelect.value = previous;
    }
}

runStudySelect.addEventListener("change", () => {
    syncRunStudyNewRowVisibility();
    if (!runStudyNewRow.hidden) {
        runStudyNewNameInput.focus();
    }
});

/**
 * Create the Study named in `run-study-new-row`'s own input, then select
 * it -- the inline counterpart to `open-run.js`'s own `createGroup`,
 * scoped to Configure's own footer rather than a Home card (§1 Option B).
 */
async function onRunStudyNewCreateClicked() {
    const name = runStudyNewNameInput.value.trim();
    if (!name) {
        showRunBanner("a study needs a name");
        return;
    }
    const result = await window.pywebview.api.create_study(
        name,
        "",
        runStudyNewExperimentSelect.value || null
    );
    if (!result.ok) {
        showRunBanner(result.message);
        return;
    }
    showRunBanner("");
    runStudyNewNameInput.value = "";
    await refreshRunStudySelectOptions();
    // Not covered by `refreshRunStudySelectOptions`'s own previous-value
    // preservation (that logic matches `"__new__"` against nothing,
    // §"refreshRunStudySelectOptions" above) -- the freshly created
    // study's own id is only known here, after this call resolves.
    runStudySelect.value = result.studyId;
    syncRunStudyNewRowVisibility();
}

function onRunStudyNewCancelClicked() {
    runStudyNewNameInput.value = "";
    runStudySelect.value = "";
    syncRunStudyNewRowVisibility();
}

runStudyNewCreateButton.addEventListener("click", onRunStudyNewCreateClicked);
runStudyNewCancelButton.addEventListener("click", onRunStudyNewCancelClicked);

/**
 * Pre-select "New study…" and reveal its own inline row -- the Explore
 * handoff's own soft nudge (§2's "New study…" default rather than "No
 * study," `20260918-claude-sonnet-5-explore-to-study-run-handoff-
 * design.md`, `selby/restricted`, §5/§8), exposed so `explore.js` can
 * reach it after navigating to Configure without reaching into this
 * file's own private `<select>` reference directly.
 */
window.fim.preselectNewStudy = function preselectNewStudy() {
    runStudySelect.value = "__new__";
    syncRunStudyNewRowVisibility();
    runStudyNewNameInput.focus();
};

/**
 * Hard-select an existing Study on `run-study-select` -- the Home
 * tree's own "Create run…" button (`open-run.js`, `20260918-claude-
 * sonnet-5-home-tree-reorg-design.md`, `selby/restricted`, §4), reached
 * after a botanist has already explicitly chosen this Study by
 * clicking its own row, unlike `preselectNewStudy`'s own soft nudge.
 * Still fully changeable afterward on Configure itself -- this only
 * sets the initial value, exactly like any other selection.
 * @param {string} studyId
 */
window.fim.selectStudyForNewRun = function selectStudyForNewRun(studyId) {
    runStudySelect.value = studyId;
    syncRunStudyNewRowVisibility();
};

function wireRunViewControls() {
    runButton.addEventListener("click", onRunClicked);
    cancelButton.addEventListener("click", onCancelClicked);
    openFolderButton.addEventListener("click", onOpenFolderClicked);
    refreshRunStudySelectOptions();
}

window.fim.showRunBanner = showRunBanner;

whenApiReady(wireRunViewControls);
