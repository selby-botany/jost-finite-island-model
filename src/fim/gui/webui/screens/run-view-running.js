"use strict";

/* The unified run view's `running` state (unified-run-view design
 * §3.2.1, §3.2.3, §3.7, §8 Phase E) -- a live scatter (design doc §0.5,
 * §4.2's original "the botanists want to see scatterplots... live"),
 * folded out of the retired `screens/progress.js` into this state-
 * scoped file with no behavioral change beyond the merge itself
 * (design §8 Phase G adds the live table/statistics-with-CIs this
 * state does not have yet).
 *
 * `Api.start_run` pushes `fim.onRunProgress`/`onRunDone`/`onRunCancelled`/
 * `onRunError` (scalar) or `onBatchProgress`/`onBatchDone`/
 * `onBatchCancelled`/`onBatchError` (batch -- design §4.1's "n_replicates
 * *is* the toggle") from a background thread as a run proceeds (design
 * §3.4); this file's only job is reacting to those pushes -- it never
 * calls into the bridge itself except to set a live deme pair. One
 * shared canvas either way (design §4.4's "the same screen, not two
 * different ones" principle, applied here too): the batch handlers
 * repurpose the same progress bar/label/canvas the scalar ones use,
 * showing replicate-reporting progress and a *pooled* scatter
 * (`fim.viz.scatter.pooled_scatter_panels`) instead of one run's own
 * generation and single-state scatter.
 *
 * Three real regressions were found and fixed while writing this
 * screen's own predecessor file, all instances of "same commit,
 * different result" -- the exact defect this project's own testing
 * discipline forbids re-running into a pass rather than fixing. Two
 * were assertion bugs; the third (a background thread left alive past
 * a test's own return, hanging the whole interpreter at shutdown) took
 * real, methodical root-causing to pin down, traced to `fim.paths.
 * default_output_directory()` naming its directory by the current
 * wall-clock *second* and two runs landing in the same one -- fixed at
 * the source, `fim.gui.app._resolve_available_output_directory`, not
 * here. `Api.__init__`'s own `on_run_started`/`on_message` test hooks
 * exist because of that investigation.
 *
 * A second, later regression of the identical *shape* -- a genuine
 * background thread left alive, this time from `Api.cancel_run()`'s own
 * fire-and-forget bridge call racing a test's window teardown -- is
 * what `run-view-controls.js`'s own `onCancelClicked` and its
 * `window.__fimCancelRunSettled` flag guard against.
 *
 * `panels` (from `fim.viz.scatter.scatter_panels`/`pooled_scatter_
 * panels`, design §3.5) can hold more than one 2-D panel for
 * `3 <= d <= 6` (one per deme pair) -- every panel is drawn, as a
 * small-multiples grid when there is more than one.
 *
 * The "Compare demes directly" choice (`app.js`'s shared `wireDemePair
 * Selector`) is the one case where the data is still live rather than a
 * fixed completed/sampled set: `Api.set_live_deme_pair` tells the
 * running simulation's own background thread which pair to start
 * including in every subsequent push (`pairPanel`, alongside the
 * existing `panels`), and this file decides which of the two to draw
 * (`showingLiveDemePair`) -- no per-tick or per-frame bridge call, only
 * one bridge call per selection change, the same shape every other
 * state's own selector already uses.
 */

// `progressBar` and `progressLabel` are declared in run-view-initial.js
// (loads first) and shared via the page's one global scope.

// Whether the selector below has been wired for *this* run yet --
// `onBatchProgress` last reported -- see that handler's own comment for
// why the raw reported count can legitimately regress mid-batch.
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
// second bridge call needed for that direction" guarantee every other
// state's own identical selector already gives, extended here to a
// live stream.
let lastProgressPayload = null;

/**
 * Enter `running`: reset every per-run tracking variable above, show
 * the progress indicator, hide `completed`'s own content, and enable
 * Cancel -- the shared entry point `run-view-controls.js`'s own
 * `onRunClicked` calls once `Api.start_run` confirms a run has
 * genuinely started.
 */
function enterRunningState() {
    window.fim.setRunViewState("running");
    batchProgressHighWaterMark = 0;
    liveDemeSelectorWired = false;
    showingLiveDemePair = false;
    lastProgressPayload = null;
    progressBar.value = 0;
    runProgress.hidden = false;
    if (initialStats) {
        initialStats.hidden = true;
    }
    runCompleted.hidden = true;
    batchResultsTable.hidden = true;
    scrubberControls.hidden = true;
    runDemePairSelector.hidden = true;
    cancelButton.disabled = false;
    openFolderButton.hidden = true;
    resultsBackButton.hidden = true;
    window.fim.resetScrubber();
    clearRunCanvas();
}

window.fim.enterRunningState = enterRunningState;

function drawProgressPanels(payload) {
    // `pairPanel` is only ever present once a live pair has been
    // selected (`Api._drain_run_messages`/`_push_batch_progress` only
    // compute it then) -- `showingLiveDemePair` alone is not enough to
    // draw from it, since the very first push after "Show pair" is
    // clicked can still land before the *next* one carries a fresh
    // `pairPanel` for it.
    if (showingLiveDemePair && payload.pairPanel) {
        drawScatter(runCanvas, payload.pairPanel);
        return;
    }
    const panels = payload.panels;
    if (!panels || panels.length === 0) {
        return;
    }
    if (panels.length === 1) {
        drawScatter(runCanvas, panels[0]);
    } else {
        drawScatterGrid(runCanvas, panels);
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
    runDemePairSelector.hidden = demeCount < 2;
    window.fim.wireDemePairSelector({
        xSelect: runXDeme,
        ySelect: runYDeme,
        showPairButton: runShowPairButton,
        showOverviewButton: runShowOverviewButton,
        container: runDemePairSelector,
        demeCount,
        onShowPair: async (x, y) => {
            // Set *before* awaiting the bridge call, not after it
            // resolves -- a real, confirmed-live timing bug, not a
            // style preference. `self._live_deme_pair` (the Python-side
            // state `_drain_run_messages` reads) becomes non-`None` the
            // instant `Api.set_live_deme_pair` executes; setting this
            // flag only once the *full* JS-await-Python-JS round trip
            // completed was strictly later, and let a tick whose own
            // payload genuinely carried `pairPanel` still draw as the
            // overview because the flag itself had not caught up yet.
            showingLiveDemePair = true;
            await window.pywebview.api.set_live_deme_pair(x, y);
        },
        onShowOverview: async () => {
            // Same reordering, same reason -- and here it is doubly
            // correct: a tick processed before the bridge call's own
            // Python-side state actually clears would otherwise still
            // carry a *stale* `pairPanel`, which this flag flipping
            // first now correctly overrides in favor of the overview,
            // exactly what "Show overview" asked for.
            showingLiveDemePair = false;
            // Unlike "Show pair", the default view's own `panels` is
            // always present in every push already received -- redraw
            // immediately from the last one, no need to wait for the
            // next tick.
            if (lastProgressPayload) {
                drawProgressPanels(lastProgressPayload);
            }
            await window.pywebview.api.set_live_deme_pair(null, null);
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
    window.fim.enterCompletedState(payload, false);
};

window.fim.onRunCancelled = function onRunCancelled(generation) {
    // Interruption is not a state transition (design §3.2.1): the
    // scatter/progress indicator simply stop updating and stay exactly
    // as they last rendered, with a banner overlaid on top.
    window.fim.showRunBanner(
        `Run cancelled at generation ${generation}; no artifacts were written.`
    );
    cancelButton.disabled = true;
};

window.fim.onRunError = function onRunError(message) {
    window.fim.showRunBanner(message);
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
    window.fim.enterCompletedState(payload, true);
};

window.fim.onBatchCancelled = function onBatchCancelled(payload) {
    window.fim.showRunBanner(
        `Batch cancelled (replicate ${payload.replicateIndex}, ` +
            `generation ${payload.generation}); no artifacts were written.`
    );
    cancelButton.disabled = true;
};

window.fim.onBatchError = function onBatchError(message) {
    window.fim.showRunBanner(message);
    cancelButton.disabled = true;
};
