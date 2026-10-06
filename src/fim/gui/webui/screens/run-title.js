"use strict";

/* The Run card's title: "<Experiment> — <what is showing>".
 *
 * The Experiment's name stands where a generic "FIM simulation" used to,
 * so the card always says which line of work it belongs to. The second
 * part names what is showing: the initial conditions, a run in progress,
 * a finished run (its name, or its folder when it has none), or a whole
 * Study reopened from Home. Each name carries its description as a
 * tooltip and opens its details on click (`screens/details.js`).
 *
 * The run-view state files (`run-view-initial.js`, `-running.js`,
 * `-completed.js`) say which phase is showing through `window.fim.
 * setRunCardTitle`; which Experiment, Study and Run that phase belongs to
 * comes from `Api.get_run_context`, fetched after the title has already
 * been drawn from what is known, so the title never waits on the bridge.
 */

const runTitleElement = document.getElementById("run-plot-title");

// What the run view shows: `{phase, directory, directoryName, studyId,
// runName}`; see `window.fim.setRunCardTitle`.
let runTitleState = { phase: "initial" };

// The last `Api.get_run_context` result and the key (`runTitleKey`) of
// the state it was fetched for: a context is drawn only for that same
// state, never a previous run's.
let runTitleContext = null;
let runTitleContextKey = null;

// Bumped by every fetch, so a slow reply for an older state is dropped.
let runTitleToken = 0;

/**
 * The key a context belongs to: a run's directory, else the Study it is
 * fetched for ("" meaning the Study Configure has selected).
 * @param {object} state
 * @returns {string}
 */
function runTitleKey(state) {
    if (state.directory) {
        return `run:${state.directory}`;
    }
    if (state.studyId) {
        return `study:${state.studyId}`;
    }
    const selected = window.fim.getSelectedRunStudyId
        ? window.fim.getSelectedRunStudyId()
        : null;
    return `selected:${selected ?? ""}`;
}

/**
 * Say what the run view is showing, redraw the title at once, then fetch
 * which Experiment/Study/Run that is and redraw again.
 * @param {{phase: "initial"|"running"|"completed"|"study",
 *     directory?: string|null, directoryName?: string|null,
 *     studyId?: string|null, runName?: string|null}} state `directory`
 *     and `directoryName` describe a finished run; `studyId` a reopened
 *     Study; `runName` the name typed for a run in progress.
 */
window.fim.setRunCardTitle = function setRunCardTitle(state) {
    runTitleState = { ...state };
    renderRunTitle();
    refreshRunTitleContext();
};

/** Fetch the current state's context and redraw; a no-op before the bridge is up. */
async function refreshRunTitleContext() {
    const token = ++runTitleToken;
    window.__fimRunTitleReady = false;
    const api = window.pywebview && window.pywebview.api;
    if (!api || typeof api.get_run_context !== "function") {
        return;
    }
    const key = runTitleKey(runTitleState);
    const selected = window.fim.getSelectedRunStudyId
        ? window.fim.getSelectedRunStudyId()
        : null;
    const context = await api.get_run_context(
        runTitleState.directory ?? null,
        runTitleState.studyId ?? selected
    );
    if (token !== runTitleToken) {
        return;
    }
    runTitleContext = context && context.ok ? context : null;
    runTitleContextKey = key;
    renderRunTitle();
    window.__fimRunTitleReady = true;
}

window.fim.refreshRunCardTitle = refreshRunTitleContext;

/**
 * The second half of the title: what is showing.
 * @param {object|null} context The matching context, or `null`.
 * @returns {{text: string, details: object|null}}
 */
function runTitleSubject(context) {
    const state = runTitleState;
    if (state.phase === "running") {
        return {
            text: state.runName ? `${state.runName} — in progress` : "in progress",
            details: null,
        };
    }
    if (state.phase === "study") {
        const study = context ? context.study : null;
        return {
            text: study ? `${study.name} (all runs)` : "study (all runs)",
            details: study,
        };
    }
    if (state.phase === "completed") {
        const run = context ? context.run : null;
        const name = run ? run.name : null;
        const folder = state.directoryName || (run ? run.directoryName : null);
        if (name) {
            return { text: folder ? `${name} (${folder})` : name, details: run };
        }
        return { text: folder || "completed", details: run };
    }
    return { text: "initial conditions (p₀)", details: null };
}

/** Draw the title from `runTitleState` and, when it matches, `runTitleContext`. */
function renderRunTitle() {
    if (!runTitleElement) {
        return;
    }
    const context =
        runTitleContextKey === runTitleKey(runTitleState) ? runTitleContext : null;
    const experiment = context ? context.experiment : null;
    const experimentPart = document.createElement("span");
    experimentPart.textContent = experiment ? experiment.name : "FIM simulation";
    if (experiment) {
        window.fim.attachDetails(experimentPart, experiment);
    }
    const subject = runTitleSubject(context);
    const subjectPart = document.createElement("span");
    subjectPart.textContent = subject.text;
    if (subject.details) {
        window.fim.attachDetails(subjectPart, subject.details);
    }
    const parts = [experimentPart];
    // A read-only example Experiment, and a read-only Run or Study shown
    // after it, each carry the lock (read-only examples design §3).
    if (experiment && experiment.readOnly) {
        parts.push(window.fim.buildReadOnlyBadge("experiment"));
    }
    parts.push(" — ", subjectPart);
    if (subject.details && subject.details.readOnly) {
        parts.push(window.fim.buildReadOnlyBadge(subject.details.kind));
    }
    runTitleElement.replaceChildren(...parts);
}

// A rename anywhere (Home, this title, a sweep) shows here at once.
window.addEventListener("fim:details-saved", () => {
    refreshRunTitleContext();
});

// The title is first drawn before the bridge exists (`enterInitialState`
// at launch), so fetch the context as soon as it does.
whenApiReady(refreshRunTitleContext);
