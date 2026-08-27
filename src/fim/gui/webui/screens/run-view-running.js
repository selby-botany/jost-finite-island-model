"use strict";

/* The unified run view's `running` state (unified-run-view design
 * §3.2.1, §3.2.3, §3.7, §8 Phase E) -- a live scatter (design doc §0.5,
 * §4.2's original "the botanists want to see scatterplots... live"),
 * folded out of the retired `screens/progress.js` into this state-
 * scoped file with no behavioral change beyond the merge itself.
 * Design §8 Phase G later added the live statistics table this state
 * did not originally have (`renderLiveStatistics`/`renderBatchSummary`
 * below, driven by `statistics` on every progress push) -- the same
 * `results-stats`/`batch-results-summary` `.stats-table`s
 * `enterCompletedState` shows for a finished run, kept visible and
 * updated in place from the moment a run starts rather than appearing
 * only once it ends.
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
 * panels`, simplify-main-plot design) always holds exactly one 2-D
 * panel, demes 1 and 2 by default -- that one panel is what gets drawn.
 *
 * The "Compare demes directly" choice (`app.js`'s shared `wireDemePair
 * Selector`) is the one case where the data is still live rather than a
 * fixed completed/sampled set: `Api.set_live_deme_pair` tells the
 * running simulation's own background thread which pair to start
 * including in every subsequent push (`pairPanel`, alongside the
 * existing `panels`), and this file decides which of the two to draw
 * (`showingLiveDemePair`) -- no per-tick or per-frame bridge call, only
 * one bridge call per selection change, the same shape every other
 * state's own selector already uses. There is no "Show overview" button
 * (simplify-main-plot design) -- selecting Deme 1/Deme 2 directly
 * requests exactly the same panel the default view already shows.
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
// user-chosen deme pair) rather than `panels` (the default Deme 1/Deme 2
// panel) -- `onShowPair` below sets this; every push's own handler
// reads it to decide which of the two to draw. Never resets to `false`
// mid-run (there is no "Show overview" button any more) -- selecting
// Deme 1/Deme 2 directly requests the same panel `panels` already holds.
let showingLiveDemePair = false;

/**
 * Enter `running`: reset every per-run tracking variable above, show
 * the progress indicator, hide `completed`'s own content, and enable
 * Cancel -- the shared entry point `run-view-controls.js`'s own
 * `onRunClicked` calls once `Api.start_run` confirms a run has
 * genuinely started.
 *
 * The statistics table itself is *not* hidden here (design §8 Phase G:
 * "the stats panel is always present and populated") -- whichever of
 * `results-stats`/`batch-results-summary` matches `isBatch` stays (or
 * becomes) the visible `.stats-table` for the run about to start, the
 * same one `onRunProgress`/`onBatchProgress` below update every tick
 * and `enterCompletedState` leaves showing once the run finishes, so
 * the table is continuously on screen and continuously current from
 * the moment "Run simulation" is clicked, never blank in between.
 *
 * @param {boolean} isBatch
 */
function enterRunningState(isBatch = false) {
    window.fim.setRunViewState("running");
    batchProgressHighWaterMark = 0;
    liveDemeSelectorWired = false;
    showingLiveDemePair = false;
    progressBar.value = 0;
    runProgress.hidden = false;
    if (initialStats) {
        initialStats.hidden = true;
    }
    resultsStats.hidden = isBatch;
    batchResultsTableEl.hidden = !isBatch;
    if (runPlotTitle) {
        runPlotTitle.textContent = "FIM simulation — in progress";
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
    drawScatter(runCanvas, panels[0]);
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
            // default panel because the flag itself had not caught up
            // yet.
            showingLiveDemePair = true;
            await window.pywebview.api.set_live_deme_pair(x, y);
        },
    });
}

/**
 * Update `results-stats`' six rows in place from one progress tick's
 * own live statistics (`runner.py`'s `on_generation`, formatted
 * server-side in `_drain_run_messages` exactly like a finished run's
 * own `payload.statistics`) -- the same `applyStatRow`/`buildPointMeter`
 * pair `enterCompletedState` uses, so the table reads identically
 * whether the run is still going or already done. A no-op when
 * `statistics` itself is absent -- every real push carries it
 * (`_drain_run_messages` always sets it), but a synthetic/partial test
 * payload calling `onRunProgress` directly for unrelated coverage
 * (`test_input_screen.py`'s own batch counterpart is exactly this
 * shape) should leave whatever the table already shows alone rather
 * than throwing trying to read `undefined[name]`.
 *
 * @param {Record<string, string> | undefined} statistics
 */
function renderLiveStatistics(statistics) {
    if (!statistics) {
        return;
    }
    for (const name of STATISTIC_NAMES) {
        const element = document.getElementById(`stat-${name}`);
        applyStatRow(element, buildPointMeter(name, statistics[name]));
    }
}

window.fim.onRunProgress = function onRunProgress(payload) {
    progressBar.max = payload.maxGenerations;
    progressBar.value = payload.generation;
    progressLabel.textContent = `${payload.generation} / ${payload.maxGenerations}`;
    if (!liveDemeSelectorWired) {
        wireLiveDemePairSelector(payload.demeCount);
        liveDemeSelectorWired = true;
    }
    renderLiveStatistics(payload.statistics);
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
    // `renderBatchSummary` (`run-view-completed.js`) already renders
    // "omitted (fewer than two defined replicates)" for any statistic
    // `payload.statistics` leaves out -- which, early in a batch, is
    // every statistic (`_push_batch_progress`'s own `reports_summary`
    // needs at least two currently-reporting replicates to define an
    // interval at all). That omitted-row rendering *is* "populated"
    // here, not a placeholder for it: the table always shows something
    // meaningful for the batch's current state, never blank.
    renderBatchSummary(payload.statistics);
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
