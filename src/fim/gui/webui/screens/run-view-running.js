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
 * showing mean-generation progress and a *pooled* scatter
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
 * wall-clock *second* and two runs landing in the same one -- fixed in
 * the shared allocator with microsecond timestamps and bounded suffixes.
 * `Api.__init__`'s own `on_run_started`/`on_message` test hooks
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
// re-wiring on every single push (they can arrive many times a second)
// would rebuild the dropdowns and reset whatever pair the user already
// picked; wiring once, on the first push a fresh run makes, is enough
// (`demeCount` is the same for every push within one run).
// Live progress is model state, not page state: addressed job pushes can
// arrive while a different document is selected.
function createLiveRunState() {
    return {
        liveDemeSelectorWired: false,
        showingLiveDemePair: false,
        liveTrajectoryGenerations: [],
        liveTrajectoryHistories: {},
        liveBatchTrajectory: {},
        liveEquilibriumReference: null,
        liveIdentityRecoveryReference: null,
        liveClosedForm: null,
        liveConvergence: null,
        lastRenderedFrame: null,
        lastProgressSequence: -1,
        isBatch: false,
    };
}

function liveRunStateFor(tabId) {
    const documentState = window.fim.workspace.get(tabId);
    if (!documentState) {
        return null;
    }
    if (!documentState.liveRun) {
        documentState.liveRun = createLiveRunState();
    }
    return documentState.liveRun;
}

let activeLiveRunState = liveRunStateFor(
    window.fim.workspace.selectedId()
);

window.fim.workspace.onSelectionChange((selectedId) => {
    activeLiveRunState = selectedId === null
        ? null
        : liveRunStateFor(selectedId);
    if (activeLiveRunState) {
        window.fim.setWatchedStatistics(
            activeLiveRunState.liveConvergence
                ? activeLiveRunState.liveConvergence.statistics
                : []
        );
    }
});

// Whether the canvas is currently showing `pairPanel` (one explicit,
// user-chosen deme pair) rather than `panels` (the default Deme 1/Deme 2
// panel) -- `onShowPair` below sets this; every push's own handler
// reads it to decide which of the two to draw. Never resets to `false`
// mid-run (there is no "Show overview" button any more) -- selecting
// Deme 1/Deme 2 directly requests the same panel `panels` already holds.
// The live trajectory panel (botanist GUI design doc `20260907-claude-
// sonnet-5-botanist-gui-redesign.md` §6.2: "updating in lockstep as the
// run advances," not only once the run finishes). Accumulated entirely
// client-side, one point per progress push, from `payload.statistics`
// -- the same six-statistic snapshot `renderLiveStatistics` already
// reads on every tick, not a second bridge round trip. Deliberately all
// six report statistics, not only whichever `convergence_statistic` is
// being watched: design §6.2's own text names this as the explicit
// alternative to a legend-toggle ("...or all six report statistics,
// user-selectable via a small legend-toggle, each watched or not"),
// and it reuses `renderTrajectory`/`drawTrajectoryCurve`
// (`run-view-completed.js`) unchanged, sharing one global scope the
// same way `drawScatter` already does across files on this page.
// Replaced outright once the run finishes: `onRunDone` calls `render
// Trajectory` again with the real, authoritative `convergence_
// generations`/`convergence_histories` the engine's own convergence
// monitor recorded, not this client-side approximation -- this state
// exists only to have something to plot *while* the run is still
// going, when that authoritative history does not exist yet. Scalar-
// only -- a batch run has its own, differently-shaped accumulator
// (`liveBatchTrajectory`, immediately below) since it needs a band,
// not just a line (batch trajectory panel design `20260912-claude-
// sonnet-5-batch-trajectory-panel-design.md`, `selby/restricted`,
// commit 3).
// A live batch's own trajectory accumulator -- one entry per tracked
// statistic, each a growing array of `{generation, mean, low, high,
// sampleCount}` points, the identical shape `run-view-completed.js`'s
// own `renderBatchTrajectory`/`drawBatchTrajectoryCurve` already draws
// for a *completed* batch's own real, authoritative pooled history
// (commit 2) -- reused as-is here rather than a second drawing path,
// since a shared x-axis (every point in a live batch tick shares the
// identical `meanReportedGeneration`, unlike the completed case's own
// per-statistic-scoped generations) is simply a special case that
// shape already handles. Replaced outright once the batch finishes
// (`onBatchDone` calls `enterCompletedState`, whose own batch branch
// draws `payload.pooledConvergenceHistories` instead) -- this client-
// side approximation exists only for while the batch is still going,
// matching `liveTrajectoryGenerations`/`liveTrajectoryHistories`'s own
// identical "replaced once real data exists" scalar precedent above.
// The live trajectory's own predicted-equilibrium reference line (design
// §6.2's own closing paragraph: "the run's own progress toward... that
// prediction is visible while it is still happening, not only in
// retrospect on Results"). `Api.start_run`'s own `equilibrium` field
// (`_equilibrium_reference_payload`, `fim/gui/app.py`) is computed once,
// server-side, before the run's own background thread even starts, and
// handed to this page in `start_run`'s own return value -- cached here
// (`setLiveEquilibriumReference`, called from `run-view-controls.js`'s
// own `onRunClicked` once that bridge call resolves) and replayed on
// every subsequent `renderTrajectory` call for this run, rather than
// re-sent on every progress tick. `null` until that bridge call resolves
// (a run just entering `running` has not received it yet) and reset to
// `null` for every fresh run, the same per-run-reset shape
// `liveTrajectoryGenerations`/`liveTrajectoryHistories` above already
// establish.
// The live trajectory's own identity-recovery closed-form curve overlay —
// a second, different reference from `liveEquilibriumReference` above
// (see `_identity_recovery_reference_payload`, `fim/gui/app.py`, for what
// it is and why it is not the same thing). Cached and reset the same way
// and at the same two points as `liveEquilibriumReference`, immediately
// above.
// The live trajectory's own closed-form expected D(t)/G_ST(t) payload
// (`_closed_form_trajectory_payload`, `fim/gui/app.py`) — cached and reset
// exactly like the two references above.
// The live run's convergence window and tolerance (`Api.start_run`'s own
// `convergence` field, `_convergence_reference_payload`): what the
// trailing mean in the tooltips and the trajectory graph averages over.
// Cached and reset like the references above.
/**
 * Apply just the run-kind-dependent parts of the running state.
 *
 * Split out of `enterRunningState` so `run-view-controls.js`'s own
 * `onRunClicked` can correct its optimistic "scalar" guess once
 * `Api.start_run` reports the real answer, *without* re-running
 * everything else in that function. The rest of `enterRunningState`
 * resets state for a **new** run -- and the run is already under way
 * by the time that answer arrives: `Api.start_run` starts the
 * background thread (and its first `fim.onRunProgress` pushes land)
 * while `onRunClicked` is still awaiting the very same bridge call.
 *
 * Re-entering the whole running state at that point discards whatever
 * those early pushes already delivered: the live trajectory points so
 * far (`liveTrajectoryGenerations`/`liveTrajectoryHistories`), the
 * scatter frames already drawn (`clearRunCanvas`), the live scrubber's
 * own retained ticks (`resetScrubber`), the progress label -- and
 * re-hides the "Compare demes directly" selector a tick had already
 * wired, clearing `liveDemeSelectorWired` so it stays hidden until
 * some *later* tick happens to wire it again. `test_live_deme_pair_
 * selector_shows_a_chosen_pair_during_a_real_run` caught exactly that
 * on CI, intermittently: the selector read `hidden` right after the
 * first progress message, because the correction landed between that
 * tick and the test's own read.
 *
 * @param {boolean} isBatch
 */
function applyRunKind(isBatch) {
    resultsStats.hidden = isBatch;
    batchResultsTableEl.hidden = !isBatch;
    alleleCompositionCard.hidden = isBatch;
    frequencySpectrumCard.hidden = isBatch;
}

window.fim.applyRunKind = applyRunKind;

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
function enterRunningState(
    isBatch = false,
    tabId = window.fim.workspace.selectedId()
) {
    const liveRunState = liveRunStateFor(tabId);
    if (!liveRunState) {
        return;
    }
    Object.assign(liveRunState, createLiveRunState(), {
        isBatch,
    });
    if (window.fim.workspace.selectedId() !== tabId) {
        return;
    }
    activeLiveRunState = liveRunState;
    window.fim.setRunViewState("running");
    // `Api.start_run` has not resolved yet at this point; its reference
    // payloads are set for real once that bridge call resolves.
    // A genuinely new run starting is one of the two points the
    // trajectory legend's own display-only visibility toggle resets
    // (design §6.2's legend-toggle; `run-view-completed.js`'s own
    // `resetTrajectoryLegendVisibility` doc comment names both) --
    // guarded the same way `renderTrajectory` itself is, just below:
    // that file loads after this one.
    if (typeof window.fim.resetTrajectoryLegendVisibility === "function") {
        window.fim.resetTrajectoryLegendVisibility();
    }
    // Hides the panel immediately (`renderTrajectory`'s own empty-
    // arrays guard) rather than leaving a previous run's own trajectory
    // visible until the first tick of this one repopulates it -- the
    // same reason `clearRunCanvas()` below clears the scatter canvas
    // right away instead of waiting for the first push to overwrite it.
    // Guarded (`run-view-initial.js`'s own identical comment on why):
    // `renderTrajectory` is declared in `run-view-completed.js`, which
    // loads after this file.
    if (typeof renderTrajectory === "function") {
        renderTrajectory(
            activeLiveRunState.liveTrajectoryGenerations,
            activeLiveRunState.liveTrajectoryHistories
        );
    }
    progressBar.value = 0;
    // Same "never leave a previous run's own stale content on screen
    // until the first tick repopulates it" reasoning as the trajectory
    // panel just above -- a real, reported symptom this specific gap
    // produced: starting a new run briefly showed the previous run's
    // own final progress text until the first tick arrived.
    progressLabel.textContent = "";
    progressLabel.classList.remove("progress-label-busy");
    runProgress.hidden = false;
    if (initialStats) {
        initialStats.hidden = true;
    }
    applyRunKind(isBatch);
    // "<Experiment> — in progress"; `onRunClicked` adds the run's name.
    window.fim.setRunCardTitle({ phase: "running" });
    runCompleted.hidden = true;
    // `run-messages` sits beside `run-reason` at the foot of the run
    // view, not inside `runCompleted` -- hiding that above no longer
    // hides it for free (`run-view-initial.js`'s own `enterInitialState`
    // has the fuller account of why).
    runMessagesList.hidden = true;
    batchResultsTable.hidden = true;
    runResultsTable.hidden = true;
    // Both run kinds scrub live: every progress tick is retained, so
    // the view can be dragged back to an earlier generation while the
    // simulation keeps going (`scrubber.js`'s own "live" mode).
    scrubberControls.hidden = false;
    ibdCard.hidden = true;
    runDemePairSelector.hidden = true;
    cancelButton.disabled = false;
    openFolderButton.hidden = true;
    resultsBackButton.hidden = true;
    resultsHistoryBackButton.hidden = true;
    resultsHistoryForwardButton.hidden = true;
    window.fim.resetScrubber();
    window.fim.setScrubberMode("live");
    clearRunCanvas();
}

window.fim.enterRunningState = enterRunningState;

/**
 * Cache the live trajectory's own predicted-equilibrium reference line
 * for the run that just started -- called once, from `run-view-
 * controls.js`'s own `onRunClicked`, as soon as `Api.start_run`'s own
 * bridge call resolves (its `equilibrium` field, `_equilibrium_
 * reference_payload`, `fim/gui/app.py`). Every subsequent `onRunProgress`
 * tick replays this same cached value rather than re-sending or
 * recomputing it.
 *
 * @param {Object<string, string>|null|undefined} equilibrium
 */
window.fim.setLiveEquilibriumReference = function setLiveEquilibriumReference(
    equilibrium,
    tabId = window.fim.workspace.selectedId()
) {
    const state = liveRunStateFor(tabId);
    if (state) {
        state.liveEquilibriumReference = equilibrium ?? null;
        redrawCurrentLiveFrame(state);
    }
};

/**
 * Cache the live trajectory's own identity-recovery curve overlay for the
 * run that just started — same calling convention as `setLiveEquilibriumReference`
 * immediately above (`run-view-controls.js`'s own `onRunClicked`, once
 * `Api.start_run` resolves, its `identityRecovery` field).
 *
 * @param {{rate: number, equilibrium: number}|null|undefined} identityRecovery
 */
window.fim.setLiveIdentityRecoveryReference = function setLiveIdentityRecoveryReference(
    identityRecovery,
    tabId = window.fim.workspace.selectedId()
) {
    const state = liveRunStateFor(tabId);
    if (state) {
        state.liveIdentityRecoveryReference = identityRecovery ?? null;
        redrawCurrentLiveFrame(state);
    }
};

/**
 * Cache the live trajectory's closed-form expected-trajectory payload for
 * the run that just started — same calling convention as
 * `setLiveIdentityRecoveryReference` immediately above (its `closedForm`
 * field of `Api.start_run`'s result).
 *
 * @param {object|null|undefined} closedForm
 */
window.fim.setLiveClosedForm = function setLiveClosedForm(
    closedForm,
    tabId = window.fim.workspace.selectedId()
) {
    const state = liveRunStateFor(tabId);
    if (state) {
        state.liveClosedForm = closedForm ?? null;
        redrawCurrentLiveFrame(state);
    }
};

/**
 * Cache the live run's convergence window and tolerance — same calling
 * convention as `setLiveClosedForm` immediately above (the `convergence`
 * field of `Api.start_run`'s result; absent for a batch).
 *
 * @param {{window: number, burnIn: number, tolerance: number}|null|undefined} convergence
 */
window.fim.setLiveConvergence = function setLiveConvergence(
    convergence,
    tabId = window.fim.workspace.selectedId()
) {
    const state = liveRunStateFor(tabId);
    if (state) {
        state.liveConvergence = convergence ?? null;
    }
    window.fim.setWatchedStatistics(
        state?.liveConvergence ? state.liveConvergence.statistics : []
    );
    if (state) {
        redrawCurrentLiveFrame(state);
    }
};

function drawProgressPanels(state, payload) {
    // `pairPanel` is only ever present once a live pair has been
    // selected (`Api._drain_run_messages`/`_push_batch_progress` only
    // compute it then) -- `showingLiveDemePair` alone is not enough to
    // draw from it, since the very first push after "Show pair" is
    // clicked can still land before the *next* one carries a fresh
    // `pairPanel` for it.
    const panel = state.showingLiveDemePair && payload.pairPanel
        ? payload.pairPanel
        : payload.panels?.[0];
    if (!panel) {
        return;
    }
    window.fim.setGraphAvailable("scatter", true);
    drawScatter(runCanvas, panel);
}

/**
 * Wire the live "Compare demes directly" selector once, the first time
 * a fresh run's own progress push carries a `demeCount` -- see
 * `liveDemeSelectorWired`'s own comment for why only once per run.
 *
 * @param {number} demeCount
 */
function wireLiveDemePairSelector(demeCount, state, jobId = null) {
    runDemePairSelector.hidden = demeCount < DEMES_NEEDED_FOR_PAIR;
    window.fim.wireDemePairSelector({
        xSelect: runXDeme,
        ySelect: runYDeme,
        container: runDemePairSelector,
        selfComparisonNote: runDemePairSelfNote,
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
            state.showingLiveDemePair = true;
            await window.pywebview.api.set_live_deme_pair(x, y, jobId);
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
 * A statistic with a per-generation history shows its average over the
 * run's evidence window once the burn-in has passed (`estimateCellText`);
 * its tooltip gives that and the tick's own value. What the numbers mean is
 * explained in the run messages once the run has finished.
 *
 * @param {Record<string, string> | undefined} statistics
 * @param {number} [generation] the tick's generation; the latest when omitted.
 */
function renderLiveStatistics(state, statistics, generation) {
    if (!statistics) {
        return;
    }
    const index =
        generation === undefined
            ? null
            : state.liveTrajectoryGenerations.indexOf(generation);
    const estimateSource = {
        generations: state.liveTrajectoryGenerations,
        histories: state.liveTrajectoryHistories,
        convergence: state.liveConvergence,
        anchors: {},
        sums: {},
    };
    for (const name of STATISTIC_NAMES) {
        const element = document.getElementById(`stat-${name}`);
        applyStatRow(
            element,
            buildPointMeter(
                name,
                statistics[name],
                "",
                estimateCellText(estimateSource, name, index)
            )
        );
        // Until the burn-in has passed there is no evidence-window average
        // to show; the row keeps the last value, dimmed.
        element.classList.toggle(
            "stat-burn-in",
            TRAJECTORY_STATISTIC_NAMES.includes(name) &&
                estimateCellText(estimateSource, name, index) === undefined
        );
        if (typeof decorateTrajectoryStatisticRow === "function") {
            decorateTrajectoryStatisticRow(element, name);
        }
    }
}

function redrawCurrentLiveFrame(state) {
    const current = state.lastRenderedFrame;
    if (!current) {
        return;
    }
    renderLiveStatistics(state, current.statistics, current.generation);
    if (typeof renderTrajectory === "function") {
        renderTrajectory(
            state.liveTrajectoryGenerations,
            state.liveTrajectoryHistories,
            undefined,
            undefined,
            state.liveEquilibriumReference,
            state.liveIdentityRecoveryReference,
            state.liveClosedForm,
            current.isLiveHead ? null : current.generation,
            state.liveConvergence
                ? {
                      window: state.liveConvergence.window,
                      burnIn: state.liveConvergence.burnIn,
                      anchors: {},
                  }
                : null
        );
    }
}

/**
 * Append one progress tick to the live trajectory accumulators.
 *
 * `statistics[name]` is `format_statistic`'s own ready-to-show string
 * (`"undefined"` for a `None` value -- `G_ST` at a currently-
 * monomorphic locus, `renderTrajectory`'s own docstring), not a raw
 * number -- skipped for that one statistic on that one tick rather
 * than pushing a `NaN` into its history, the same "leave a statistic's
 * own history shorter than `generations` rather than plot it
 * misaligned" choice `renderTrajectory`'s own "plottable" filter
 * already makes for the completed view.
 * @param {Record<string, string> | undefined} statistics
 */
function accumulateLiveTrajectory(state, generation, statistics) {
    if (!statistics) {
        return;
    }
    state.liveTrajectoryGenerations.push(generation);
    // Only statistics with a per-generation history get a curve, so the
    // live chart and the completed one plot the same set.
    for (const name of TRAJECTORY_STATISTIC_NAMES) {
        const value = Number(statistics[name]);
        if (Number.isFinite(value)) {
            if (!state.liveTrajectoryHistories[name]) {
                state.liveTrajectoryHistories[name] = [];
            }
            state.liveTrajectoryHistories[name].push(value);
        }
    }
}

/**
 * Append one batch progress tick to `liveBatchTrajectory`, one point
 * per statistic that has a defined interval this tick (batch
 * trajectory panel design `20260912-claude-sonnet-5-batch-trajectory-
 * panel-design.md`, `selby/restricted`, commit 3: the live band,
 * reusing `renderBatchTrajectory`/`drawBatchTrajectoryCurve`'s own
 * per-statistic-scoped point shape -- see `liveBatchTrajectory`'s own
 * declaration comment for why that shape, built for the *completed*
 * case, already fits this live one too).
 *
 * `meanGeneration` is `_push_batch_progress`'s own `meanReportedGeneration`
 * -- an approximate, pooled x-axis (no single tick has one shared
 * "generation" the way a scalar run's own progress push does, since
 * replicates report at different generations by construction), shared
 * by every statistic's own point *this tick* (unlike the completed
 * case's own genuinely-different-per-statistic generations). `sample
 * Count` rides straight through from `statistics[name]` -- already the
 * exact `{mean, low, high, sampleCount}` shape `reports_summary`
 * produces and `_push_batch_progress` formats, no flattening needed
 * the way feeding the scalar accumulator used to require.
 *
 * A statistic missing from `statistics` this tick (fewer than two
 * currently-reporting replicates define it yet, `_push_batch_
 * progress`'s own documented threshold) simply gets no point appended
 * to its own array this tick -- unlike the scalar accumulator's shared-
 * array hazard this function used to inherit, one statistic's own gap
 * cannot shrink any *other* statistic's own array here, since each has
 * always been independently scoped from the start.
 * @param {number | undefined} meanGeneration
 * @param {Record<string, {mean: string, low: string, high: string,
 *     sampleCount: number}> | undefined} statistics
 */
/**
 * Keep `liveBatchTrajectory[name]`'s own generation-0 point current,
 * inserting it at index 0 the first time a name has one and
 * overwriting it in place afterward (batch trajectory panel design
 * `20260912-claude-sonnet-5-batch-trajectory-panel-design.md`, `selby/
 * restricted`, follow-up: without this, the live panel's own x-axis
 * could only ever start wherever the *first* two-or-more-replicates
 * tick happened to land -- plausibly well past generation 0 for a
 * fast-running batch that has already outrun a few poll intervals by
 * the time this page first looks -- silently understating how much of
 * the run's own early history was actually skipped, not shown).
 *
 * `initialStatistics` is `_push_batch_progress`'s own pooled generation-
 * 0 interval, across every replicate ever seen reporting so far --
 * unlike a tick's own `statistics`, its own `sampleCount` only ever
 * grows (a replicate's own generation-0 state never changes, so it
 * stays counted even after that replicate moves on or finishes),
 * which is exactly why this needs its own "update in place," not
 * `accumulateLiveBatchTrajectory`'s own "always append:" the same
 * generation would otherwise gain one redundant point per tick.
 * @param {Record<string, {mean: string, low: string, high: string,
 *     sampleCount: number}> | undefined} initialStatistics
 */
function setLiveBatchTrajectoryInitialPoint(
    initialStatistics,
    state = activeLiveRunState
) {
    if (!initialStatistics) {
        return;
    }
    for (const name of TRAJECTORY_STATISTIC_NAMES) {
        const interval = initialStatistics[name];
        if (!interval) {
            continue;
        }
        const point = {
            generation: 0,
            mean: interval.mean,
            low: interval.low,
            high: interval.high,
            sampleCount: interval.sampleCount,
        };
        if (!state.liveBatchTrajectory[name]) {
            state.liveBatchTrajectory[name] = [point];
        } else if (state.liveBatchTrajectory[name][0]?.generation === 0) {
            state.liveBatchTrajectory[name][0] = point;
        } else {
            state.liveBatchTrajectory[name].unshift(point);
        }
    }
}

function accumulateLiveBatchTrajectory(state, meanGeneration, statistics) {
    if (meanGeneration === undefined || !statistics) {
        return;
    }
    for (const name of TRAJECTORY_STATISTIC_NAMES) {
        const interval = statistics[name];
        if (!interval) {
            continue;
        }
        if (!state.liveBatchTrajectory[name]) {
            state.liveBatchTrajectory[name] = [];
        }
        state.liveBatchTrajectory[name].push({
            generation: meanGeneration,
            mean: interval.mean,
            low: interval.low,
            high: interval.high,
            sampleCount: interval.sampleCount,
        });
    }
}

function recordRunProgress(state, payload) {
    accumulateLiveTrajectory(
        state,
        payload.generation,
        payload.statistics
    );
}

function renderRunProgress(state, payload, jobId = null) {
    progressBar.max = payload.maxGenerations;
    progressBar.value = payload.generation;
    progressLabel.textContent = `${payload.generation} / ${payload.maxGenerations}`;
    if (!state.liveDemeSelectorWired) {
        wireLiveDemePairSelector(payload.demeCount, state, jobId);
        state.liveDemeSelectorWired = true;
    }

    const liveFrame = {
        generation: payload.generation,
        panels: payload.panels,
        pairPanel: payload.pairPanel,
        pairStatistics: payload.pairStatistics,
        statistics: payload.statistics,
        literatureVisuals: payload.literatureVisuals,
    };

    window.fim.appendLiveFrame(liveFrame, (f, _index, isLiveHead) => {
        state.lastRenderedFrame = {
            generation: f.generation,
            statistics: f.statistics,
            isLiveHead,
        };
        renderLiveStatistics(state, f.statistics, f.generation);
        renderPairStatistics(f.pairStatistics);
        if (typeof renderTrajectory === "function") {
            renderTrajectory(
                state.liveTrajectoryGenerations,
                state.liveTrajectoryHistories,
                undefined,
                undefined,
                state.liveEquilibriumReference,
                state.liveIdentityRecoveryReference,
                state.liveClosedForm,
                isLiveHead ? null : f.generation,
                state.liveConvergence
                    ? {
                          window: state.liveConvergence.window,
                          burnIn: state.liveConvergence.burnIn,
                          anchors: {},
                      }
                    : null
            );
        }
        drawProgressPanels(state, f);
        if (typeof renderSupplementalPanels === "function" && f.literatureVisuals) {
            renderSupplementalPanels(f.literatureVisuals);
        }
    });
}

window.fim.onRunProgress = function onRunProgress(payload) {
    if (window.fim.workspace.ids().length > 1 || !activeLiveRunState) {
        return;
    }
    activeLiveRunState.isBatch = false;
    recordRunProgress(activeLiveRunState, payload);
    renderRunProgress(activeLiveRunState, payload);
};

/**
 * Show a non-terminal note on the progress line, with a spinner: the
 * engine has finished and the run's files are still being written, as
 * when every deme pair's statistics are saved for a large run
 * (`fim.gui.runner.pairwise_status_text`).
 *
 * @param {string} text
 */
window.fim.onRunStatus = function onRunStatus(text) {
    progressLabel.textContent = text;
    progressLabel.classList.add("progress-label-busy");
};

window.fim.onRunDone = function onRunDone(payload) {
    cancelButton.disabled = true;
    progressLabel.classList.remove("progress-label-busy");
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

function recordBatchProgress(state, payload) {
    setLiveBatchTrajectoryInitialPoint(payload.initialStatistics, state);
    accumulateLiveBatchTrajectory(
        state,
        payload.meanReportedGeneration,
        payload.statistics
    );
}

function renderBatchProgress(state, payload, jobId = null) {
    const meanGeneration = payload.meanReportedGeneration ?? 0;
    progressBar.max = payload.maxGenerations;
    progressBar.value = meanGeneration;
    progressLabel.textContent = `mean completed generation ${meanGeneration}`;
    if (!state.liveDemeSelectorWired) {
        wireLiveDemePairSelector(payload.demeCount, state, jobId);
        state.liveDemeSelectorWired = true;
    }

    // The batch counterpart to `onRunProgress`'s own live frame: every
    // tick is retained so the scrubber can go back to an earlier pooled
    // snapshot while the batch is still running, rather than only ever
    // showing the newest one (`scrubber.js`'s own "live" mode).
    const liveFrame = {
        generation: meanGeneration,
        panels: payload.panels,
        pairPanel: payload.pairPanel,
        statistics: payload.statistics,
        literatureVisuals: payload.literatureVisuals,
    };

    window.fim.appendLiveFrame(liveFrame, (f, _index, isLiveHead) => {
        // `renderBatchSummary` (`run-view-completed.js`) already renders
        // "omitted (fewer than two defined replicates)" for any statistic
        // `f.statistics` leaves out -- which, early in a batch, is
        // every statistic (`_push_batch_progress`'s own `reports_summary`
        // needs at least two currently-reporting replicates to define an
        // interval at all). That omitted-row rendering *is* "populated"
        // here, not a placeholder for it: the table always shows something
        // meaningful for the batch's current state, never blank.
        renderBatchSummary(f.statistics);
        // `renderBatchTrajectory`, not `renderTrajectory` -- declared in
        // `run-view-completed.js`, which loads after this file, guarded the
        // same way that function's own scalar counterpart already is
        // immediately below `onRunProgress`. The accumulated band always
        // shows in full; only the scrub marker moves back with the frame.
        if (typeof renderBatchTrajectory === "function") {
            renderBatchTrajectory(
                state.liveBatchTrajectory,
                isLiveHead ? null : f.generation
            );
        }
        drawProgressPanels(state, f);
        if (typeof renderSupplementalPanels === "function" && f.literatureVisuals) {
            renderSupplementalPanels(f.literatureVisuals);
        }
    });
}

window.fim.onBatchProgress = function onBatchProgress(payload) {
    if (window.fim.workspace.ids().length > 1 || !activeLiveRunState) {
        return;
    }
    activeLiveRunState.isBatch = true;
    recordBatchProgress(activeLiveRunState, payload);
    renderBatchProgress(activeLiveRunState, payload);
};

const applyAddressedJobEvent = window.fim.onJobEvent;
window.fim.onJobEvent = function onJobEvent(event) {
    const accepted = applyAddressedJobEvent(event);
    if (!accepted) {
        return false;
    }
    if (
        event.type !== "progress" ||
        window.fim.workspace.ids().length < 2 ||
        !event.payload ||
        typeof event.payload !== "object" ||
        window.fim.jobs.isDocumentClosed(event.jobId)
    ) {
        return true;
    }

    const tabId = window.fim.workspace.documentForJob(event.jobId);
    const state = tabId === null ? null : liveRunStateFor(tabId);
    if (!state || event.sequence <= state.lastProgressSequence) {
        return true;
    }
    state.lastProgressSequence = event.sequence;

    if (event.payload.replicateCount > 1) {
        state.isBatch = true;
        recordBatchProgress(state, event.payload);
        if (tabId === window.fim.workspace.selectedId()) {
            renderBatchProgress(state, event.payload, event.jobId);
        }
        return true;
    }

    if (typeof event.payload.generation === "number") {
        state.isBatch = false;
        recordRunProgress(state, event.payload);
        if (tabId === window.fim.workspace.selectedId()) {
            renderRunProgress(state, event.payload, event.jobId);
        }
    }
    return true;
};

window.fim.onBatchDone = function onBatchDone(payload) {
    cancelButton.disabled = true;
    progressLabel.classList.remove("progress-label-busy");
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
