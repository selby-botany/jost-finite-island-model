"use strict";

/* Screen 2: running, as a live scatter (design doc §0.5, §4.2) -- the
 * direct answer to "the botanists want to see scatterplots... live".
 *
 * `Api.start_run` pushes `fim.onRunProgress`/`onRunDone`/`onRunCancelled`/
 * `onRunError` (scalar) or `onBatchProgress`/`onBatchDone`/
 * `onBatchCancelled`/`onBatchError` (batch -- design §4.1's "n_replicates
 * *is* the toggle") from a background thread as a run proceeds (design
 * §3.4); this file's only job is reacting to those pushes -- it never
 * calls into the bridge itself except to fire "Cancel". One screen, one
 * set of DOM elements, either way (design §4.4's "the same screen, not
 * two different ones" principle, applied here too): the batch handlers
 * repurpose the same progress bar/label/canvas the scalar ones use,
 * showing replicate-reporting progress and a *pooled* scatter
 * (`fim.viz.scatter.pooled_scatter_panels`) instead of one run's own
 * generation and single-state scatter.
 *
 * `panels` (from `fim.viz.scatter.scatter_panels`/`pooled_scatter_
 * panels`, design §3.5) can hold more than one 2-D panel for
 * `3 <= d <= 6` (one per deme pair) -- every panel is drawn, as a
 * small-multiples grid when there is more than one
 * (visualization-and-config-editors design §3.1; supersedes Milestone
 * W3's own "draws only the first panel" scope line).
 *
 * Screens 3/4/5's own "Compare demes directly" choice extends here too
 * (`app.js`'s shared `wireDemePairSelector`), the one case where the
 * data is still live rather than a fixed completed/sampled set:
 * `Api.set_live_deme_pair` tells the running simulation's own
 * background thread which pair to start including in every subsequent
 * push (`pairPanel`, alongside the existing `panels`), and this file
 * decides which of the two to draw (`showingLiveDemePair`) -- no
 * per-tick or per-frame bridge call, only one bridge call per
 * selection change, the same shape every other screen's own selector
 * already uses.
 */

const progressCanvas = document.getElementById("progress-canvas");
const progressBar = document.getElementById("progress-generation");
const progressLabel = document.getElementById("progress-generation-label");
const progressBanner = document.getElementById("progress-banner");
const cancelButton = document.getElementById("cancel-run-button");
const progressDemePairSelector = document.getElementById(
    "progress-deme-pair-selector"
);
const progressXDeme = document.getElementById("progress-x-deme");
const progressYDeme = document.getElementById("progress-y-deme");
const progressShowPairButton = document.getElementById(
    "progress-show-pair-button"
);
const progressShowOverviewButton = document.getElementById(
    "progress-show-overview-button"
);

// The displayed batch-progress count, tracked separately from whatever
// `onBatchProgress` last reported -- see that handler's own comment for
// why the raw reported count can legitimately regress mid-batch, and
// `screens/input.js`'s `onRunClicked` (which resets this to 0 for every
// new run, the same place it already resets the shared progress bar's
// own DOM value) for why this is safe to track as simple module state
// rather than something scoped per run.
let batchProgressHighWaterMark = 0;
// Whether the selector below has been wired for *this* run yet --
// re-wiring on every single push (they can arrive many times a second)
// would rebuild the dropdowns and reset whatever pair the user already
// picked; wiring once, on the first push a fresh run makes, is enough
// (`demeCount` is the same for every push within one run).
let liveDemeSelectorWired = false;
// Whether the canvas is currently showing `pairPanel` (one explicit,
// user-chosen deme pair) rather than `panels` (the default pairwise-
// grid-or-first-pair view, unified-run-view design §3.6) --
// `onShowPair`/`onShowOverview` below flip this; every push's own
// handler reads it to decide which of the two to draw.
let showingLiveDemePair = false;
// The most recent push this run has made, kept only so "Show overview"
// can redraw instantly from data already on hand -- the same "no
// second bridge call needed for that direction" guarantee Screens 3/4/5's
// own identical selector already gives, extended here to a live stream.
let lastProgressPayload = null;

window.fim.resetBatchProgress = function resetBatchProgress() {
    batchProgressHighWaterMark = 0;
    liveDemeSelectorWired = false;
    showingLiveDemePair = false;
    lastProgressPayload = null;
};

function showProgressBanner(message) {
    if (!message) {
        progressBanner.hidden = true;
        progressBanner.textContent = "";
        return;
    }
    progressBanner.hidden = false;
    progressBanner.textContent = message;
}

function drawProgressPanels(payload) {
    // `pairPanel` is only ever present once a live pair has been
    // selected (`Api._drain_run_messages`/`_push_batch_progress` only
    // compute it then) -- `showingLiveDemePair` alone is not enough to
    // draw from it, since the very first push after "Show pair" is
    // clicked can still land before the *next* one carries a fresh
    // `pairPanel` for it.
    if (showingLiveDemePair && payload.pairPanel) {
        drawScatter(progressCanvas, payload.pairPanel);
        return;
    }
    const panels = payload.panels;
    if (!panels || panels.length === 0) {
        return;
    }
    if (panels.length === 1) {
        drawScatter(progressCanvas, panels[0]);
    } else {
        drawScatterGrid(progressCanvas, panels);
    }
}

/**
 * Wire the live "Compare demes directly" selector once, the first time
 * a fresh run's own progress push carries a `demeCount` -- see
 * `liveDemeSelectorWired`'s own comment for why only once per run.
 *
 * @param {number} demeCount
 */
function wireLiveDemePairSelector(demeCount) {
    window.fim.wireDemePairSelector({
        xSelect: progressXDeme,
        ySelect: progressYDeme,
        showPairButton: progressShowPairButton,
        showOverviewButton: progressShowOverviewButton,
        container: progressDemePairSelector,
        demeCount,
        onShowPair: async (x, y) => {
            // Tells the *running* simulation's own background thread
            // which pair to start including in every subsequent push
            // (`Api.set_live_deme_pair`) -- unlike Screens 3/4/5's own
            // `onShowPair`, there is no already-computed panel to draw
            // immediately: the canvas updates on the next push, the
            // same "near-instant, not literally instant" cadence
            // `progressBar`/`progressLabel` above already have.
            await window.pywebview.api.set_live_deme_pair(x, y);
            showingLiveDemePair = true;
        },
        onShowOverview: async () => {
            await window.pywebview.api.set_live_deme_pair(null, null);
            showingLiveDemePair = false;
            // Unlike "Show pair", the default view's own `panels` is
            // always present in every push already received -- redraw
            // immediately from the last one, no need to wait for the
            // next tick.
            if (lastProgressPayload) {
                drawProgressPanels(lastProgressPayload);
            }
        },
    });
}

window.fim.onRunProgress = function onRunProgress(payload) {
    progressBar.max = payload.maxGenerations;
    progressBar.value = payload.generation;
    progressLabel.textContent = `${payload.generation} / ${payload.maxGenerations}`;
    if (!liveDemeSelectorWired) {
        wireLiveDemePairSelector(payload.demeCount);
        liveDemeSelectorWired = true;
    }
    lastProgressPayload = payload;
    drawProgressPanels(payload);
};

window.fim.onRunDone = function onRunDone(payload) {
    cancelButton.disabled = true;
    window.fim.showResults(payload);
};

window.fim.onRunCancelled = function onRunCancelled(generation) {
    showProgressBanner(`Run cancelled at generation ${generation}; no artifacts were written.`);
    cancelButton.disabled = true;
};

window.fim.onRunError = function onRunError(message) {
    showProgressBanner(message);
    cancelButton.disabled = true;
};

window.fim.onBatchProgress = function onBatchProgress(payload) {
    // `payload.reportedReplicateCount` can legitimately regress between
    // two ticks, late in a batch with an adaptive `replicate_tolerance`
    // stop set: that stop is only decided after a whole concurrent
    // worker wave completes (`fim.engine._run_batch_parallel`), so a
    // worker beyond the replicate that triggered it can still be
    // mid-run -- and counted by this same poll -- when the decision
    // lands. Once the batch prunes that now-orphaned replicate's
    // directory (`cli._prune_orphan_replicate_directories`'s own
    // docstring), the very next poll sees one fewer valid replicate
    // than a moment before. Real, reported behavior ("the generation
    // tracking bar jumps around during the last ~20%"), not a
    // hypothetical -- the displayed count only ever moves forward.
    batchProgressHighWaterMark = Math.max(
        batchProgressHighWaterMark,
        payload.reportedReplicateCount
    );
    progressBar.max = payload.replicateCount;
    progressBar.value = batchProgressHighWaterMark;
    progressLabel.textContent =
        `${batchProgressHighWaterMark} / ${payload.replicateCount} replicates reporting`;
    if (!liveDemeSelectorWired) {
        wireLiveDemePairSelector(payload.demeCount);
        liveDemeSelectorWired = true;
    }
    lastProgressPayload = payload;
    drawProgressPanels(payload);
};

window.fim.onBatchDone = function onBatchDone(payload) {
    cancelButton.disabled = true;
    window.fim.showBatchResults(payload);
};

window.fim.onBatchCancelled = function onBatchCancelled(payload) {
    showProgressBanner(
        `Batch cancelled (replicate ${payload.replicateIndex}, ` +
            `generation ${payload.generation}); no artifacts were written.`
    );
    cancelButton.disabled = true;
};

window.fim.onBatchError = function onBatchError(message) {
    showProgressBanner(message);
    cancelButton.disabled = true;
};

cancelButton.addEventListener("click", async () => {
    cancelButton.disabled = true;
    // `window.__fimCancelRunSettled`, not only `cancelButton.disabled`
    // (which flips synchronously, before this bridge call's own promise
    // ever resolves): the exact hazard `open-run.js`'s own `refreshRecent
    // Runs` docstring records -- a test that tears its window down the
    // instant a DOM-visible effect appears can destroy the window while
    // `cancel_run()`'s own return value is still in flight back to
    // pywebview's own JS bridge (`webview/util.py`'s `js_bridge_call`),
    // throwing on that now-orphaned delivery thread and hanging the whole
    // interpreter at shutdown (confirmed live: this exact click handler,
    // racing `test_running_screen.py`'s own `cancelled_event` wait, which
    // watches a *different* signal -- the real run thread's own
    // `onRunCancelled` push -- and was not itself waiting on this call).
    window.__fimCancelRunSettled = false;
    await window.pywebview.api.cancel_run();
    window.__fimCancelRunSettled = true;
});
