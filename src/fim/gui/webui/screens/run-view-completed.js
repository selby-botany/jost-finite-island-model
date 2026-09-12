"use strict";

/* The unified run view's `completed` state (unified-run-view design
 * §3.2.1, §3.2.4, §3.7, §8 Phase E) -- unchanged content from the two
 * retired screens it replaces (`screens/results.js`, `screens/
 * batch-results.js`), plus the scrubber (`scrubber.js`) folded directly
 * in where the old, separate "Animate" button/screen used to be
 * (design §3.2.4: "there is no separate Animate button... the one time
 * slider simply keeps existing"). `enterCompletedState(payload, isBatch)`
 * is the one shared entry point every caller uses -- a live scalar run
 * finishing (`run-view-running.js`'s own `onRunDone`), a live batch
 * finishing (`onBatchDone`), and re-analyzing a persisted run
 * (`open-run.js`, always scalar) alike -- branching internally on
 * `isBatch` rather than being two unrelated code paths (design §3.2.5's
 * "one state model, not two" applied to this merge too).
 *
 * Every statistic arrives already formatted server-side
 * (`format_statistic`, matching `cli._format_optional`), so this file
 * does no numeric formatting of its own beyond the one field that is
 * never server-formatted (`Differentiation_q`, only ever present on
 * `Api.open_run`'s own payload). Each statistic renders through
 * `meters.js`'s shared `buildPointMeter`/`buildCiMeter`/
 * `buildOmittedMeter`.
 *
 * The scrubber is scalar-only (a batch's own `completed` view is a
 * pooled *final*-state scatter across replicates, design §2.4 -- there
 * is no one trajectory of its own to play back) and only when there is
 * more than one generation to scrub through; it always shows the
 * default projection, deliberately not pair-aware for this phase (the
 * "Compare demes directly" selector below only ever affects the
 * currently-drawn static frame, not future scrubbing) -- keeping these
 * two mechanisms independent, exactly as uncoupled as they were when
 * they lived on two separate screens, avoids a real interaction
 * question ("does choosing a pair apply to every frame or just this
 * one?") this phase's own "no new capabilities" scope does not need to
 * answer yet.
 */

const STATISTIC_NAMES = ["D", "G_ST", "E_ST", "K_ST", "H_S", "H_T"];

// A fixed, colorblind-safe qualitative palette (Okabe-Ito), one color
// per named statistic — botanist GUI design doc §11.3's own "disciplined
// statistic color language" is not otherwise built yet; this is a
// narrow, self-contained first use of the same idea, scoped to this one
// legend/curve rather than a page-wide system.
const STATISTIC_TRAJECTORY_COLORS = {
    D: "#0072b2",
    G_ST: "#d55e00",
    E_ST: "#009e73",
    K_ST: "#cc79a7",
    H_S: "#e69f00",
    H_T: "#56b4e9",
};

// See `wireCompletedScrubber`'s own comment: counts its own in-flight
// `get_animation_frames` calls. Zero means settled.
window.__fimScrubberPending = 0;

// The trajectory legend's own display-only visibility toggle (design
// §6.2: "user-selectable via a small legend-toggle, each watched or
// not"). Clicking a legend entry hides that one statistic's own curve
// (and its predicted-equilibrium companion, if drawn) from the canvas
// -- a pure client-side filter over `histories`, which already holds
// every tracked statistic's real value regardless of this set
// (`fim.engine._watched_statistic_values`); nothing here ever stops
// recording, or re-requests, any statistic's own data. Scoped to the
// six report statistics only -- the identity-recovery curve overlay
// (its own, separate legend entry, built after this loop) is not one
// of them and is never toggled by this mechanism. Reset only when a
// genuinely new run starts (`run-view-running.js`'s own
// `enterRunningState`) or a different persisted run is opened
// (`open-run.js`'s own call into `Api.open_run`) -- deliberately not on
// the ordinary running->completed transition of the *same* run, so a
// mid-run choice to hide a noisy curve survives into that run's own
// completed view rather than silently reverting the instant the run
// finishes. Module-scope, page-session-only state, matching this
// file's own `showingLiveDemePair`-style precedent (`run-view-
// running.js`) for "a user-driven, page-local display toggle" -- no
// need to survive a reload.
let hiddenTrajectoryStatistics = new Set();

// The most recent `renderTrajectory` call's own arguments, so a legend
// click can re-render the panel with the same underlying data (whatever
// scrub position, if any, was last shown), only a different visibility
// set, without its caller needing to re-invoke this file with a live
// history it may no longer have close at hand.
let lastTrajectoryRenderArgs = null;

// The batch counterpart to `lastTrajectoryRenderArgs` (batch trajectory
// panel design `20260912-claude-sonnet-5-batch-trajectory-panel-
// design.md`, `selby/restricted`, commit 2) -- `renderBatchTrajectory`'s
// own last `pooledConvergenceHistories` argument, cached the same way
// and for the same reason: a legend click's own toggle re-render.
// Shares `hiddenTrajectoryStatistics` with the scalar panel (only one
// of the two views ever shows at once, so there is no risk of one
// view's own toggle silently fighting the other's).
let lastPooledConvergenceHistories = null;

/**
 * Reset the trajectory legend's own hidden-statistic set to "everything
 * visible" -- called whenever a genuinely new run starts or a different
 * persisted run is opened (see `hiddenTrajectoryStatistics`'s own
 * comment for why this is not reset on every `enterCompletedState`
 * call).
 */
window.fim.resetTrajectoryLegendVisibility = function resetTrajectoryLegendVisibility() {
    hiddenTrajectoryStatistics = new Set();
};

const resultsRunId = document.getElementById("results-run-id");
const resultsOutcome = document.getElementById("results-outcome");
const resultsStats = document.getElementById("results-stats");
const runTrajectoryFrame = document.getElementById("run-trajectory-frame");
const runTrajectoryCanvas = document.getElementById("run-trajectory-canvas");
const runTrajectoryLegend = document.getElementById("run-trajectory-legend");
const runTrajectorySigmaBandCaption = document.getElementById(
    "run-trajectory-sigma-band-caption"
);
const resultsDifferentiationQ = document.getElementById("results-differentiation-q");
const resultsDifferentiationQCanvas = document.getElementById(
    "results-differentiation-q-canvas"
);
const resultsDifferentiationQLines = document.getElementById(
    "results-differentiation-q-lines"
);
// Item 6's re-analysis controls (relocated here from the old open-run
// screen, `open-run.js`'s own former `generationMode`/`openButton`
// logic) -- scalar-only, hidden for a batch's own `completed` view
// (`enterCompletedState`, below).
const resultsReanalyzeControls = document.getElementById("results-reanalyze-controls");
const resultsGenerationValueInput = document.getElementById("results-generation-value");
const resultsDifferentiationOrdersInput = document.getElementById(
    "results-differentiation-orders"
);
const resultsReanalyzeButton = document.getElementById("results-reanalyze-button");
const gStCautionNote = document.getElementById("g-st-caution-note");
// `batchResultsTableEl` is the `<table>` whose own `hidden` attribute
// gates visibility; `batchResultsSummary` is its `<tbody>`, where
// `renderBatchSummary` rebuilds rows -- kept as two names rather than
// one so every existing `batchResultsSummary.appendChild`/
// `.replaceChildren` call below keeps working unchanged.
const batchResultsTableEl = document.getElementById("batch-results-summary");
const batchResultsSummary = document.getElementById("batch-results-summary-body");
const batchResultsTableBody = document.getElementById("batch-results-table-body");
const resultsBackButton = document.getElementById("results-back-button");

// Design §4.4's own "a statistic omitted from summary.json still
// renders as explicitly omitted, not blank" -- `_batch_done_payload`
// leaves `name` out of `summary` entirely rather than sending a
// null/undefined placeholder, matching `replicate_summary`'s own
// documented "omitted entirely rather than raising, since a single
// point has no interval."
const OMITTED_SUMMARY_TEXT = "omitted (fewer than two defined replicates)";

// Design §6.3's own "a scrubber... letting a user drag back through
// already-computed history," extended to the completed-state scrubber
// replaying a finished run (this file's own scrubber, not the live one
// `run-view-running.js` already handles): only the watched statistic(s)
// have any per-generation value at all (`ConvergenceMonitor.record`
// only ever records the statistics actually being watched), so the
// other five are shown as not known at that generation, rather than a
// possibly-misleading final value, whenever scrubbing away from the
// final frame -- reusing `buildOmittedMeter`'s established "a statistic
// with nothing to show" rendering (`renderBatchSummary`'s own use of it,
// just above) rather than inventing new wording/markup for this second
// case of the same underlying idea.
const OMITTED_SCRUB_TEXT = "not known at this generation";

// Retained per-completed-entry state answering the scrubber's own
// per-tick statistics/trajectory-marker update (`updateScrubbedTrajectory`,
// below) -- `payload.convergenceGenerations`/`convergenceHistories`/
// `sigmaBand`/`equilibrium`/`generationCount`/`statistics` were
// previously read once by `enterCompletedState` and hedge straight into
// `renderTrajectory`, with nothing kept anywhere a later scrub tick
// could read them back from. `null` specifically means "no scalar run's
// own scrub-replay state is currently loaded" (a batch entry, which
// never wires a scrubber at all, or before any scalar run has
// completed) -- reset on every `enterCompletedState` call, the same
// per-run-reset shape `run-view-running.js`'s own
// `liveTrajectoryGenerations`/`liveTrajectoryHistories` already
// establish. A reopened run (`Api.open_run`) has no `convergenceGenerations`/
// `convergenceHistories` of its own (`renderTrajectory`'s own docstring)
// -- `completedTrajectoryGenerations` stays `null` for that case even
// though its own scrubber can still wire (`generationCount` alone
// decides that), so `updateScrubbedTrajectory` deliberately leaves the
// table/marker exactly as they were for that one narrow case (a real,
// named scope boundary, not an oversight): there is no per-generation
// history of any kind to answer a scrub tick with there, only the one
// single reanalyzed generation the whole payload already describes.
let completedTrajectoryGenerations = null;
let completedTrajectoryHistories = null;
let completedSigmaBand = null;
let completedEquilibrium = null;
let completedIdentityRecovery = null;
let completedGenerationCount = null;
let completedFinalStatistics = null;

/**
 * Render the two effective-allele-count rows (botanist GUI design doc
 * §7.7) and show/hide the G_ST caution note alongside them.
 *
 * `effectiveAlleles` is tolerated as absent (both call sites that build
 * a scalar "completed" payload -- `_drain_run_messages`'s own `"done"`
 * push and `Api.open_run` -- already include it, but a defensive
 * fallback here, matching `renderDifferentiationQ`'s own established
 * "hide, don't throw, on a payload shape this render function does not
 * recognize" precedent, means a future payload-builder that has not
 * caught up yet degrades to "rows hidden" rather than aborting the rest
 * of this function -- which silently skipped `wireCompletedScrubber`
 * entirely the one time this actually happened, a real regression this
 * fallback exists to make structurally impossible again.
 *
 * @param {{H_S: string, H_T: string, gStCaution: boolean}|undefined} effectiveAlleles
 */
function renderEffectiveAlleles(effectiveAlleles) {
    const neSRow = document.getElementById("stat-Ne_S");
    const neTRow = document.getElementById("stat-Ne_T");
    if (!effectiveAlleles) {
        neSRow.replaceChildren();
        neTRow.replaceChildren();
        gStCautionNote.hidden = true;
        return;
    }
    applyStatRow(
        neSRow,
        buildPointMeter("Effective alleles (within-deme)", effectiveAlleles.H_S)
    );
    applyStatRow(
        neTRow,
        buildPointMeter("Effective alleles (total)", effectiveAlleles.H_T)
    );
    gStCautionNote.hidden = !effectiveAlleles.gStCaution;
}

/**
 * Draw the swept `(order, value)` curve — a diversity-order profile in
 * the sense botanist GUI design doc `20260907-claude-sonnet-5-botanist-
 * gui-redesign.md` §7.7 describes: `D`/`G_ST`/`K_ST` are all one family,
 * evaluated at different `q`, not unrelated numbers that happen to
 * disagree. Deliberately not a generalized version of `explore.js`'s
 * own `drawSweepCurve` (a single linear-x-axis line, no log scale, no
 * "current value" marker needed here) — the two draw different enough
 * data shapes that sharing one function would need more parameters than
 * it would save code.
 * @param {HTMLCanvasElement} canvas
 * @param {Array<{order: number, value: number}>} points
 */
function drawDifferentiationQCurve(canvas, points) {
    const context = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    context.clearRect(0, 0, width, height);
    if (points.length === 0) {
        return;
    }

    const plotLeft = 40;
    const plotRight = width - 12;
    const plotTop = 12;
    const plotBottom = height - 28;

    const orders = points.map((point) => point.order);
    const minOrder = Math.min(...orders);
    const maxOrder = Math.max(...orders);

    function xToPixel(order) {
        const fraction =
            maxOrder === minOrder ? 0 : (order - minOrder) / (maxOrder - minOrder);
        return plotLeft + fraction * (plotRight - plotLeft);
    }

    function yToPixel(value) {
        return plotBottom - value * (plotBottom - plotTop);
    }

    const style = getComputedStyle(document.documentElement);
    const borderColor = style.getPropertyValue("--fim-border").trim();
    const mutedColor = style.getPropertyValue("--fim-muted").trim();
    const accentColor = style.getPropertyValue("--fim-accent").trim();

    context.strokeStyle = borderColor;
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(plotLeft, plotTop);
    context.lineTo(plotLeft, plotBottom);
    context.lineTo(plotRight, plotBottom);
    context.stroke();

    context.fillStyle = mutedColor;
    context.font = "10px sans-serif";
    context.textAlign = "right";
    context.textBaseline = "middle";
    for (const tick of PROBABILITY_TICK_VALUES) {
        context.fillText(tick.toFixed(1), plotLeft - 6, yToPixel(tick));
    }
    context.textAlign = "center";
    context.textBaseline = "top";
    for (const point of points) {
        context.fillText(`q=${point.order}`, xToPixel(point.order), plotBottom + 4);
    }

    context.strokeStyle = accentColor;
    context.lineWidth = 2;
    context.beginPath();
    points.forEach((point, index) => {
        const x = xToPixel(point.order);
        const y = yToPixel(Math.min(1, Math.max(0, point.value)));
        if (index === 0) {
            context.moveTo(x, y);
        } else {
            context.lineTo(x, y);
        }
        context.fillStyle = accentColor;
        context.beginPath();
        context.arc(x, y, 3, 0, 2 * Math.PI);
        context.fill();
    });
    context.stroke();
}

/**
 * Draw one or more named statistic-vs-generation curves on shared axes
 * (botanist GUI design doc §6.2's own "how it got here" trajectory
 * panel) — deliberately not a generalized version of `drawDifferentiation
 * QCurve` just below (a single, always-[0,1] curve against a `q`-order
 * x-axis, no per-series color/legend) — the two draw different enough
 * data shapes that sharing one function would need more parameters than
 * it would save code.
 * @param {HTMLCanvasElement} canvas
 * @param {number[]} generations
 * @param {Object<string, number[]>} histories one array per statistic,
 *     each already the same length as `generations` (`renderTrajectory`,
 *     below, filters out any that is not before this ever runs).
 * @param {{multiplier: number, window: number, band: Object<string,
 *     {mean: string, sigma: string, lower: string, upper: string}>}|null|undefined} sigmaBand
 *     design doc §7.2's own within-run sigma band (`20260910-claude-
 *     sonnet-5-gui-sigma-band-design.md`'s own approach C1) — `null`/
 *     `undefined` (a run that never requested one, or a screen with no
 *     band data of its own to show at all) draws nothing extra.
 * @param {Object<string, number>} [equilibrium] design §6.2's own
 *     predicted-equilibrium reference line — one already-`Number`-
 *     parsed, already-in-scope value per statistic (`renderTrajectory`,
 *     below, both parses `Api.get_equilibrium_predictions`'s own
 *     formatted-string values and restricts this to statistics the
 *     panel is actually plotting before this ever runs); omitted or
 *     empty draws nothing extra.
 * @param {{rate: number, equilibrium: number}|null|undefined} identityRecovery
 *     `Api._identity_recovery_reference_payload`'s own result — a
 *     second, different closed-form reference from `equilibrium` above:
 *     a full curve, `f0(generation) = equilibrium * (1 - rate **
 *     generation)`, not a single asymptote value, drawn in its own
 *     fixed color (`--fim-accent`, not one of `STATISTIC_TRAJECTORY_
 *     COLORS` — it is not any one of the six report statistics) and its
 *     own dotted style, evaluated at every one of `generations` so it
 *     shares this curve's own x-axis exactly, with no second sampling
 *     grid to keep in sync. `null`/`undefined` (a batch, a non-scalar
 *     `N`/`m`, or a panel not otherwise showing anything to plot this
 *     against) draws nothing extra.
 * @param {number|null} [scrubGeneration] the completed-state scrubber's
 *     own currently-scrubbed generation (`updateScrubbedTrajectory`,
 *     below, already resolves this to the nearest generation this
 *     panel's own `generations` actually contains) — drawn as one more
 *     vertical dashed marker, the same `setLineDash`/color convention
 *     `explore.js`'s own `drawSweepCurve` already established for "the
 *     configuration's own current value" there; `null`/`undefined`
 *     (not currently scrubbing, or no scrubber at all) draws nothing
 *     extra.
 */
function drawTrajectoryCurve(
    canvas,
    generations,
    histories,
    sigmaBand,
    equilibrium,
    identityRecovery,
    scrubGeneration
) {
    const context = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    context.clearRect(0, 0, width, height);
    if (generations.length === 0) {
        return;
    }

    const plotLeft = 42;
    const plotRight = width - 12;
    const plotTop = 12;
    const plotBottom = height - 22;

    const minGeneration = generations[0];
    const maxGeneration = generations[generations.length - 1];
    const allValues = Object.values(histories).flat();
    if (sigmaBand) {
        for (const interval of Object.values(sigmaBand.band)) {
            allValues.push(Number(interval.lower), Number(interval.upper));
        }
    }
    if (equilibrium) {
        allValues.push(...Object.values(equilibrium));
    }
    if (identityRecovery) {
        allValues.push(
            ...generations.map(
                (generation) =>
                    identityRecovery.equilibrium *
                    (1 - identityRecovery.rate ** generation)
            )
        );
    }
    // The domain always includes [0, 1] even if every plotted value
    // happens to sit inside it already — every named statistic's own
    // natural range starts there, so a run that never leaves, say,
    // [0.1, 0.3] still reads against the same fixed floor/ceiling a
    // reader of any other statistic meter on this page already expects,
    // rather than an auto-scaled domain that would make a small,
    // ordinary wobble look dramatic. The sigma band's own `lower`/
    // `upper` are folded into this same domain calculation (just
    // above) so a wide band is never clipped by axes sized only for
    // the curve itself.
    const minValue = Math.min(0, ...allValues);
    const maxValue = Math.max(1, ...allValues);

    function xToPixel(generation) {
        const fraction =
            maxGeneration === minGeneration
                ? 0
                : (generation - minGeneration) / (maxGeneration - minGeneration);
        return plotLeft + fraction * (plotRight - plotLeft);
    }
    function yToPixel(value) {
        const fraction =
            maxValue === minValue ? 0 : (value - minValue) / (maxValue - minValue);
        return plotBottom - fraction * (plotBottom - plotTop);
    }

    const style = getComputedStyle(document.documentElement);
    const borderColor = style.getPropertyValue("--fim-border").trim();
    const mutedColor = style.getPropertyValue("--fim-muted").trim();
    const accentColor = style.getPropertyValue("--fim-accent").trim();

    context.strokeStyle = borderColor;
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(plotLeft, plotTop);
    context.lineTo(plotLeft, plotBottom);
    context.lineTo(plotRight, plotBottom);
    context.stroke();

    context.fillStyle = mutedColor;
    context.font = "10px sans-serif";
    context.textAlign = "right";
    context.textBaseline = "middle";
    context.fillText(maxValue.toFixed(2), plotLeft - 6, plotTop);
    context.fillText(minValue.toFixed(2), plotLeft - 6, plotBottom);
    context.textBaseline = "top";
    context.fillText(`gen ${maxGeneration}`, plotRight, plotBottom + 4);
    context.textAlign = "left";
    context.fillText(`gen ${minGeneration}`, plotLeft, plotBottom + 4);

    // The sigma band itself: a translucent rect from `lower` to
    // `upper`, spanning the trailing window the extension actually
    // covered (`maxGeneration - window` through `maxGeneration`,
    // clamped to the plotted range — a window longer than what is
    // actually shown here still draws, just starting at the plot's own
    // left edge rather than off-canvas). Drawn behind every curve
    // (before the stroke loop below), the same "shaded region behind a
    // solid line reads as uncertainty around it" grammar the sigma-
    // band design doc's own approach C1 names.
    if (sigmaBand) {
        const bandLeft = xToPixel(Math.max(minGeneration, maxGeneration - sigmaBand.window));
        for (const [name, interval] of Object.entries(sigmaBand.band)) {
            context.fillStyle = STATISTIC_TRAJECTORY_COLORS[name] || mutedColor;
            context.globalAlpha = 0.2;
            const top = yToPixel(Number(interval.upper));
            const bottom = yToPixel(Number(interval.lower));
            context.fillRect(bandLeft, top, plotRight - bandLeft, bottom - top);
            context.globalAlpha = 1;
        }
    }

    for (const [name, values] of Object.entries(histories)) {
        context.strokeStyle = STATISTIC_TRAJECTORY_COLORS[name] || mutedColor;
        context.lineWidth = 2;
        context.beginPath();
        values.forEach((value, index) => {
            const x = xToPixel(generations[index]);
            const y = yToPixel(value);
            if (index === 0) {
                context.moveTo(x, y);
            } else {
                context.lineTo(x, y);
            }
        });
        context.stroke();
    }

    // The predicted-equilibrium reference line (design §6.2's own closing
    // paragraph) -- a light dashed horizontal line at the predicted value,
    // in the same statistic's own color as its simulated curve, so the
    // two read as "this statistic, two ways" rather than as unrelated
    // marks. Drawn last (on top of the solid curves, unlike the sigma
    // band's own translucent fill, which draws *behind* them) since a
    // thin dashed line is never wide enough to obscure the data it is
    // annotating.
    if (equilibrium) {
        context.lineWidth = 1.5;
        context.setLineDash([4, 3]);
        for (const [name, value] of Object.entries(equilibrium)) {
            context.strokeStyle = STATISTIC_TRAJECTORY_COLORS[name] || mutedColor;
            const y = yToPixel(value);
            context.beginPath();
            context.moveTo(plotLeft, y);
            context.lineTo(plotRight, y);
            context.stroke();
        }
        context.setLineDash([]);
    }

    // The identity-recovery closed-form curve (a second, different
    // theoretical reference from the equilibrium line just above — see
    // this function's own `identityRecovery` parameter doc): drawn in a
    // fixed, non-statistic color (this is not `D`/`G_ST`/any of the six
    // report statistics) and a dotted, not dashed, style, so the three
    // dashed-vertical/dashed-horizontal conventions already on this
    // canvas (equilibrium, sigma band caption marker, scrub marker) are
    // never confusable with this fourth, genuinely different kind of
    // line. Evaluated at every one of `generations` directly (`f0(t) =
    // equilibrium * (1 - rate**t)`), not a second, independently sampled
    // series.
    if (identityRecovery) {
        context.strokeStyle = accentColor;
        context.lineWidth = 1.5;
        context.setLineDash([1, 3]);
        context.beginPath();
        generations.forEach((generation, index) => {
            const value =
                identityRecovery.equilibrium *
                (1 - identityRecovery.rate ** generation);
            const x = xToPixel(generation);
            const y = yToPixel(value);
            if (index === 0) {
                context.moveTo(x, y);
            } else {
                context.lineTo(x, y);
            }
        });
        context.stroke();
        context.setLineDash([]);
    }

    // The completed-state scrubber's own current-position marker (this
    // feature's own design note, read alongside design §6.2/§6.3): a
    // vertical dashed line at the scrubbed generation, drawn last (on
    // top of the curves, the sigma band, and the equilibrium overlay
    // alike) so it always reads clearly regardless of what it crosses —
    // the same "current value" grammar `explore.js`'s own `drawSweepCurve`
    // already established (`setLineDash([4, 3])`, muted color, one
    // pixel wide, full plot height), reused verbatim rather than
    // inventing a third dashed-vertical-line convention on this page.
    // The curve itself is never redrawn/truncated for this — the design
    // note's own explicit choice is a moving marker over the whole,
    // unchanged curve, not a progressive reveal that hides its ending.
    if (scrubGeneration !== null && scrubGeneration !== undefined) {
        context.setLineDash([4, 3]);
        context.strokeStyle = mutedColor;
        context.lineWidth = 1;
        const x = xToPixel(scrubGeneration);
        context.beginPath();
        context.moveTo(x, plotTop);
        context.lineTo(x, plotBottom);
        context.stroke();
        context.setLineDash([]);
    }
}

/**
 * Show (or hide) the trajectory panel for the just-shown completed run.
 *
 * A live scalar run's own already-computed `RunResult.convergence_
 * generations`/`convergence_histories` (`_drain_run_messages`'s own
 * `"done"` push) draw a real curve; a reopened run (`Api.open_run`)
 * has neither — re-analysis recomputes one chosen generation, never a
 * full history (`fim.reanalyze`'s own module docstring) — so `generations`/
 * `histories` are `undefined` for that path, a real, named scope
 * boundary, not an oversight.
 *
 * `sigmaBand` (design §7.2, sigma-band design doc `20260910-claude-
 * sonnet-5-gui-sigma-band-design.md`'s own approach B1/C1) is `Api.
 * _sigma_band_payload`'s own result, from either payload shape alike —
 * `null`/`undefined` for a run that never requested one, and always
 * `undefined` for a batch (no `sigmaBand` key on that payload shape at
 * all). When a band exists but there is no real curve to anchor it to
 * (a reopened run, slice 4 of that design's own commit schedule), the
 * panel still shows — axes sized to the band's own trailing window
 * alone (`generationCount - sigmaBand.window` through
 * `generationCount`), the shaded region and its caption, simply no
 * line running through it, rather than requiring a curve that does
 * not exist just to show a band that does.
 * @param {number[]|undefined} generations
 * @param {Object<string, number[]>|undefined} histories
 * @param {{multiplier: number, window: number, band: object}|null|undefined} sigmaBand
 * @param {number|undefined} generationCount the run's own final
 *     generation — only read to size the axes for the band-alone case
 *     above; ignored whenever real `generations`/`histories` exist.
 * @param {Object<string, string>|null|undefined} equilibrium design
 *     §6.2's own closing paragraph: "the trajectory panel also draws
 *     the predicted equilibrium as a light dashed reference line."
 *     `Api.start_run`'s own `equilibrium` field (a live run, cached
 *     client-side and replayed on every tick — `run-view-running.js`'s
 *     own `setLiveEquilibriumReference`) or `Api.open_run`'s identical
 *     field (a reopened run) — `_equilibrium_reference_payload`'s own
 *     `{"D", "G_ST", "E_ST"}` shape, each `format_statistic`-formatted
 *     exactly like every other statistic this page draws; `null`/
 *     `undefined` for a batch (never computed) or a configuration whose
 *     `N`/`m`/`mu` are not all plain scalars.
 * @param {{rate: number, equilibrium: number}|null|undefined} identityRecovery
 *     see `drawTrajectoryCurve`'s own parameter of the same name —
 *     `Api.start_run`'s own `identityRecovery` field (a live run, cached
 *     client-side — `run-view-running.js`'s own `setLiveIdentityRecoveryReference`)
 *     or `Api.open_run`'s identical field (a reopened run); `null`/
 *     `undefined` for a batch or a non-scalar `N`/`m`.
 * @param {number|null} [scrubGeneration] see `drawTrajectoryCurve`'s own
 *     parameter of the same name — threaded straight through unchanged;
 *     `undefined` for every existing caller (a live tick, a fresh
 *     completed entry) draws no marker at all, exactly as before this
 *     parameter existed.
 */
function renderTrajectory(
    generations,
    histories,
    sigmaBand,
    generationCount,
    equilibrium,
    identityRecovery,
    scrubGeneration
) {
    // Cached so a legend click (`buildTrajectoryLegendItem`, below) can
    // re-render with the identical underlying data and scrub position,
    // only a different `hiddenTrajectoryStatistics` set, without
    // needing this data handed to it again from outside.
    lastTrajectoryRenderArgs = [
        generations,
        histories,
        sigmaBand,
        generationCount,
        equilibrium,
        identityRecovery,
        scrubGeneration,
    ];
    const hasCurve = generations && histories && generations.length > 0;
    if (!hasCurve && !sigmaBand) {
        runTrajectoryFrame.hidden = true;
        runTrajectoryLegend.replaceChildren();
        runTrajectorySigmaBandCaption.hidden = true;
        runTrajectorySigmaBandCaption.replaceChildren();
        return;
    }
    const effectiveGenerations = hasCurve
        ? generations
        : [Math.max(0, generationCount - sigmaBand.window), generationCount];
    const effectiveHistories = hasCurve ? histories : {};
    runTrajectoryFrame.hidden = false;
    const canvas = runTrajectoryCanvas;
    canvas.width = canvas.clientWidth || canvas.width;
    canvas.height = canvas.clientHeight || canvas.height;
    // A statistic whose own history is shorter than `generations` went
    // undefined on at least one recorded tick (only `G_ST` can, at a
    // currently-monomorphic locus — `_convergence_values`'s own
    // docstring) — skipped here rather than drawn with its own values
    // misaligned against the wrong generation numbers; a future slice
    // can thread each statistic's own generation list through
    // separately to lift this.
    const plottable = Object.fromEntries(
        Object.entries(effectiveHistories).filter(
            ([, values]) => values.length === effectiveGenerations.length
        )
    );
    // The predicted-equilibrium overlay draws a reference line only for
    // a statistic the panel is already plotting *something* for — a
    // real simulated curve (`plottable`, the ordinary case), or, when
    // there is no curve at all (a reopened run with no live monitor of
    // its own, `hasCurve` false), whichever statistics the sigma band
    // is already showing a trailing window for. `equilibrium` on its
    // own never reveals a panel that would otherwise stay hidden (the
    // guard above is unchanged) — it only ever adds to a panel already
    // shown for another reason, the same "nothing to show, don't draw
    // anything" discipline the sigma band itself established, applied
    // here to the one case (reopened, no sigma band requested) design
    // §6.2's own text does not directly address: recomputing a
    // prediction with nothing plotted alongside it to compare against
    // would be a number with no context, not a finding.
    const equilibriumScopeNames = hasCurve
        ? Object.keys(plottable)
        : sigmaBand
          ? Object.keys(sigmaBand.band)
          : [];
    const plottableEquilibrium = equilibrium
        ? Object.fromEntries(
              Object.entries(equilibrium)
                  .filter(([name]) => equilibriumScopeNames.includes(name))
                  .map(([name, formatted]) => [name, Number(formatted)])
                  .filter(([, value]) => Number.isFinite(value))
          )
        : {};
    // The legend-toggle feature (design §6.2): `hiddenTrajectoryStatistics`
    // filters only what actually reaches the canvas -- `plottable`/
    // `plottableEquilibrium` themselves stay unfiltered, since the legend
    // built below still needs an entry for *every* trackable statistic,
    // hidden or not, so a hidden one's own legend item remains there to
    // click again and bring it back. Display-only: `sigmaBand` and
    // `identityRecovery` (two separate overlays on the same canvas) are
    // deliberately never filtered by this set, matching this feature's
    // own "never affects anything else drawn on the same canvas" scope.
    const visiblePlottable = Object.fromEntries(
        Object.entries(plottable).filter(([name]) => !hiddenTrajectoryStatistics.has(name))
    );
    const visiblePlottableEquilibrium = Object.fromEntries(
        Object.entries(plottableEquilibrium).filter(
            ([name]) => !hiddenTrajectoryStatistics.has(name)
        )
    );
    drawTrajectoryCurve(
        canvas,
        effectiveGenerations,
        visiblePlottable,
        sigmaBand,
        visiblePlottableEquilibrium,
        identityRecovery,
        scrubGeneration
    );
    runTrajectorySigmaBandCaption.replaceChildren();
    if (sigmaBand) {
        for (const [name, interval] of Object.entries(sigmaBand.band)) {
            const item = document.createElement("li");
            item.textContent =
                `${name}: ${interval.mean} [${interval.lower}, ${interval.upper}] ` +
                `(${sigmaBand.multiplier}σ, last ${sigmaBand.window} generations)`;
            runTrajectorySigmaBandCaption.appendChild(item);
        }
    }
    runTrajectorySigmaBandCaption.hidden =
        runTrajectorySigmaBandCaption.children.length === 0;
    runTrajectoryLegend.replaceChildren();
    for (const name of Object.keys(plottable)) {
        runTrajectoryLegend.appendChild(
            buildTrajectoryLegendItem(name, `${name} (simulated)`, "swatch")
        );
    }
    // A second, dashed-swatch legend entry per statistic actually drawn
    // as a predicted-equilibrium line (design §6.2's own mockup text:
    // "┈┈┈┈ predicted equilibrium" vs. "— D (simulated)") -- distinct
    // enough from the solid-swatch entries above that "predicted" and
    // "simulated" are never visually confusable, per design principle 5
    // ("a plot's legend is a contract"). Toggled by the identical name's
    // own legend entry above, not a second, independent toggle -- hiding
    // "D (simulated)" hides its own "D (predicted equilibrium)" companion
    // too (`buildTrajectoryLegendItem`'s own shared `hiddenTrajectory
    // Statistics` set).
    for (const name of Object.keys(plottableEquilibrium)) {
        runTrajectoryLegend.appendChild(
            buildTrajectoryLegendItem(
                name,
                `${name} (predicted equilibrium)`,
                "swatch swatch-dashed"
            )
        );
    }
    // The identity-recovery closed-form curve's own legend entry (see
    // `drawTrajectoryCurve`'s own `identityRecovery` parameter doc for
    // the full "what this is and why it is not called D" explanation) —
    // drawn whenever the panel is showing anything at all to plot it
    // against (`effectiveGenerations` always exists at this point, the
    // early-return above already guaranteed that), not scoped to any one
    // statistic the way the equilibrium entries above are, since this
    // curve is not any one of the six report statistics.
    if (identityRecovery) {
        const item = document.createElement("span");
        const swatch = document.createElement("span");
        swatch.className = "swatch swatch-dotted";
        swatch.style.borderColor = "var(--fim-accent)";
        item.appendChild(swatch);
        item.appendChild(
            document.createTextNode(
                "f₀ (identity recovery, theoretical founder event)"
            )
        );
        runTrajectoryLegend.appendChild(item);
    }
}

/**
 * Build one clickable/keyboard-focusable trajectory-legend entry, wired
 * to toggle `name`'s own visibility in `hiddenTrajectoryStatistics` and
 * re-render the panel in place (design §6.2's own legend-toggle: display
 * only, never touching what is recorded or requested). Shared by both
 * the "(simulated)" and "(predicted equilibrium)" legend loops in
 * `renderTrajectory` above -- the same statistic name drives both, so
 * one click hides both entries for that name together, not two
 * independent toggles a user could get out of sync.
 *
 * @param {string} name a tracked statistic name (`STATISTIC_NAMES`).
 * @param {string} label the full text shown beside the swatch.
 * @param {string} swatchClassName `"swatch"` (solid) or `"swatch
 *     swatch-dashed"` (the predicted-equilibrium overlay's own style).
 * @returns {HTMLSpanElement}
 */
function buildTrajectoryLegendItem(name, label, swatchClassName) {
    const hidden = hiddenTrajectoryStatistics.has(name);
    const item = document.createElement("span");
    item.className = hidden ? "legend-item legend-item-hidden" : "legend-item";
    item.tabIndex = 0;
    item.setAttribute("role", "button");
    item.setAttribute("aria-pressed", String(!hidden));
    const swatch = document.createElement("span");
    swatch.className = swatchClassName;
    if (swatchClassName === "swatch") {
        swatch.style.backgroundColor = STATISTIC_TRAJECTORY_COLORS[name] || "var(--fim-muted)";
    } else {
        swatch.style.borderColor = STATISTIC_TRAJECTORY_COLORS[name] || "var(--fim-muted)";
    }
    item.appendChild(swatch);
    item.appendChild(document.createTextNode(label));
    const toggle = () => {
        if (hiddenTrajectoryStatistics.has(name)) {
            hiddenTrajectoryStatistics.delete(name);
        } else {
            hiddenTrajectoryStatistics.add(name);
        }
        if (lastTrajectoryRenderArgs) {
            renderTrajectory(...lastTrajectoryRenderArgs);
        }
    };
    item.addEventListener("click", toggle);
    item.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            toggle();
        }
    });
    return item;
}

// A confidence interval computed from only 2 or 3 independent replicates
// is mathematically correct but can be enormous -- Student's-t with 1
// degree of freedom (`sampleCount === 2`) has a two-tailed 95% critical
// value near 12.7, so a perfectly ordinary difference between two
// replicates' own values can produce a `low`/`high` many times wider
// than the statistic's own natural range. Confirmed live: the tail end
// of a real, staggered-stopping batch's own `D` band reached `[-2.98,
// 3.54]` for a statistic that never otherwise leaves roughly `[0, 1]`.
// Left in the *data* unchanged (`pooled_convergence_histories` never
// drops a point just because its own interval is wide -- design §
// "a real, honest picture... not an artifact to smooth over") but
// excluded from the *domain* calculation below: a single unstable
// late point should not squash every earlier, better-supported point
// into an unreadable sliver at the plot's own vertical center. The
// mean is never excluded regardless of sample count -- only the
// low/high band's own contribution to the axis range is gated, so the
// central tendency's own story is always visible even when its
// uncertainty band is not fully.
const _MIN_SAMPLE_COUNT_FOR_DOMAIN = 4;

/**
 * Draw a completed batch's own pooled trajectory (batch trajectory
 * panel design `20260912-claude-sonnet-5-batch-trajectory-panel-
 * design.md`, `selby/restricted`, commit 2) -- one mean line plus a
 * shaded low/high band per statistic, each on its *own* generation
 * axis rather than one shared list (`pooled_convergence_histories`'s
 * own docstring: different statistics can have different generation
 * coverage once replicates start dropping out, so there is no single
 * shared list every statistic's own points would otherwise need to
 * align against). Deliberately not `drawTrajectoryCurve` extended in
 * place -- that function's own domain/axis math assumes one shared
 * `generations` array indexing every statistic's own `histories`
 * entry at the same position, an assumption this payload's own shape
 * does not hold.
 *
 * @param {HTMLCanvasElement} canvas
 * @param {Record<string, Array<{generation: number, mean: string,
 *     low: string, high: string, sampleCount: number}>>} visiblePooled
 *     Already filtered to the statistics currently visible
 *     (`hiddenTrajectoryStatistics`) -- this function draws exactly
 *     what it is given, the same division of responsibility
 *     `drawTrajectoryCurve` already established for its own
 *     `visiblePlottable` argument.
 */
/**
 * Compute the y-axis domain `drawBatchTrajectoryCurve` plots against --
 * factored out into its own pure function specifically so a test can
 * assert on it directly (a hand-built payload in, a `{minValue,
 * maxValue}` out), rather than only indirectly through rendered canvas
 * pixels.
 *
 * A point's own `mean` always contributes to the domain, regardless of
 * `sampleCount`; its own `low`/`high` contribute only once `sampleCount`
 * reaches `_MIN_SAMPLE_COUNT_FOR_DOMAIN` (see that constant's own
 * comment for the confirmed-live case this excludes) -- an unstable,
 * thin-sample band still *draws* at its own true, possibly enormous
 * width (canvas silently clips whatever falls outside `[plotTop,
 * plotBottom]`, the same as it would for any other value outside the
 * visible area), it simply never gets to decide how far the axis
 * itself stretches for every other, better-supported point.
 * @param {Record<string, Array<{mean: string, low: string, high: string,
 *     sampleCount: number}>>} visiblePooled
 * @returns {{minValue: number, maxValue: number}}
 */
function computeBatchTrajectoryValueDomain(visiblePooled) {
    const allValues = Object.values(visiblePooled).flatMap((points) =>
        points.flatMap((point) => {
            const values = [Number(point.mean)];
            if (point.sampleCount >= _MIN_SAMPLE_COUNT_FOR_DOMAIN) {
                values.push(Number(point.low), Number(point.high));
            }
            return values;
        })
    );
    // The domain always includes [0, 1], matching `drawTrajectoryCurve`'s
    // own identical reasoning: every named statistic's own natural range
    // starts there, so this reads against the same fixed floor/ceiling a
    // reader of any other statistic on this page already expects.
    return {
        minValue: Math.min(0, ...allValues),
        maxValue: Math.max(1, ...allValues),
    };
}

function drawBatchTrajectoryCurve(canvas, visiblePooled) {
    const context = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    context.clearRect(0, 0, width, height);
    const names = Object.keys(visiblePooled);
    if (names.length === 0) {
        return;
    }

    const plotLeft = 42;
    const plotRight = width - 12;
    const plotTop = 12;
    const plotBottom = height - 22;

    const allGenerations = names.flatMap((name) =>
        visiblePooled[name].map((point) => point.generation)
    );
    const minGeneration = Math.min(...allGenerations);
    const maxGeneration = Math.max(...allGenerations);
    const { minValue, maxValue } = computeBatchTrajectoryValueDomain(visiblePooled);

    function xToPixel(generation) {
        const fraction =
            maxGeneration === minGeneration
                ? 0
                : (generation - minGeneration) / (maxGeneration - minGeneration);
        return plotLeft + fraction * (plotRight - plotLeft);
    }
    function yToPixel(value) {
        const fraction =
            maxValue === minValue ? 0 : (value - minValue) / (maxValue - minValue);
        return plotBottom - fraction * (plotBottom - plotTop);
    }

    const style = getComputedStyle(document.documentElement);
    const borderColor = style.getPropertyValue("--fim-border").trim();
    const mutedColor = style.getPropertyValue("--fim-muted").trim();

    context.strokeStyle = borderColor;
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(plotLeft, plotTop);
    context.lineTo(plotLeft, plotBottom);
    context.lineTo(plotRight, plotBottom);
    context.stroke();

    context.fillStyle = mutedColor;
    context.font = "10px sans-serif";
    context.textAlign = "right";
    context.textBaseline = "middle";
    context.fillText(maxValue.toFixed(2), plotLeft - 6, plotTop);
    context.fillText(minValue.toFixed(2), plotLeft - 6, plotBottom);
    context.textBaseline = "top";
    context.fillText(`gen ${maxGeneration}`, plotRight, plotBottom + 4);
    context.textAlign = "left";
    context.fillText(`gen ${minGeneration}`, plotLeft, plotBottom + 4);

    for (const name of names) {
        const points = visiblePooled[name];
        const color = STATISTIC_TRAJECTORY_COLORS[name] || mutedColor;
        // The shaded band: `high` left-to-right, then `low` back
        // right-to-left, closing one filled polygon -- the standard
        // "confidence band" fill technique, needed here (rather than
        // `drawTrajectoryCurve`'s own single `fillRect`) because a
        // pooled band's own width is not constant across generations,
        // shrinking whenever a replicate stops contributing.
        context.fillStyle = color;
        context.globalAlpha = 0.2;
        context.beginPath();
        points.forEach((point, index) => {
            const x = xToPixel(point.generation);
            const y = yToPixel(Number(point.high));
            if (index === 0) {
                context.moveTo(x, y);
            } else {
                context.lineTo(x, y);
            }
        });
        for (let index = points.length - 1; index >= 0; index -= 1) {
            const point = points[index];
            context.lineTo(xToPixel(point.generation), yToPixel(Number(point.low)));
        }
        context.closePath();
        context.fill();
        context.globalAlpha = 1;

        context.strokeStyle = color;
        context.lineWidth = 2;
        context.beginPath();
        points.forEach((point, index) => {
            const x = xToPixel(point.generation);
            const y = yToPixel(Number(point.mean));
            if (index === 0) {
                context.moveTo(x, y);
            } else {
                context.lineTo(x, y);
            }
        });
        context.stroke();
    }
}

/**
 * Render a completed batch's own pooled trajectory panel, including its
 * legend -- the batch counterpart to `renderTrajectory`, called from
 * `enterCompletedState`'s own batch branch instead of that function
 * (batch trajectory panel design `20260912-claude-sonnet-5-batch-
 * trajectory-panel-design.md`, `selby/restricted`, commit 2).
 *
 * @param {Record<string, Array<{generation: number, mean: string,
 *     low: string, high: string, sampleCount: number}>> | undefined} pooledConvergenceHistories
 */
function renderBatchTrajectory(pooledConvergenceHistories) {
    lastPooledConvergenceHistories = pooledConvergenceHistories;
    const names = pooledConvergenceHistories ? Object.keys(pooledConvergenceHistories) : [];
    if (names.length === 0) {
        runTrajectoryFrame.hidden = true;
        runTrajectoryLegend.replaceChildren();
        return;
    }
    runTrajectoryFrame.hidden = false;
    const canvas = runTrajectoryCanvas;
    canvas.width = canvas.clientWidth || canvas.width;
    canvas.height = canvas.clientHeight || canvas.height;
    const visiblePooled = Object.fromEntries(
        names
            .filter((name) => !hiddenTrajectoryStatistics.has(name))
            .map((name) => [name, pooledConvergenceHistories[name]])
    );
    drawBatchTrajectoryCurve(canvas, visiblePooled);
    runTrajectoryLegend.replaceChildren();
    for (const name of names) {
        runTrajectoryLegend.appendChild(
            buildBatchTrajectoryLegendItem(name, `${name} (pooled across replicates)`)
        );
    }
}

/**
 * The batch counterpart to `buildTrajectoryLegendItem` -- identical in
 * every way except which render function a toggle click re-invokes
 * (`renderBatchTrajectory`, via `lastPooledConvergenceHistories`,
 * rather than `renderTrajectory`). Kept as a separate function, not a
 * shared one taking a callback, so each stays a plain, directly
 * readable "build this legend item" function -- the one real
 * difference between them is small enough that threading a callback
 * through would read as more indirection than the two call sites
 * actually save.
 * @param {string} name a tracked statistic name (`STATISTIC_NAMES`).
 * @param {string} label the full text shown beside the swatch.
 * @returns {HTMLSpanElement}
 */
function buildBatchTrajectoryLegendItem(name, label) {
    const hidden = hiddenTrajectoryStatistics.has(name);
    const item = document.createElement("span");
    item.className = hidden ? "legend-item legend-item-hidden" : "legend-item";
    item.tabIndex = 0;
    item.setAttribute("role", "button");
    item.setAttribute("aria-pressed", String(!hidden));
    const swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.style.backgroundColor = STATISTIC_TRAJECTORY_COLORS[name] || "var(--fim-muted)";
    item.appendChild(swatch);
    item.appendChild(document.createTextNode(label));
    const toggle = () => {
        if (hiddenTrajectoryStatistics.has(name)) {
            hiddenTrajectoryStatistics.delete(name);
        } else {
            hiddenTrajectoryStatistics.add(name);
        }
        renderBatchTrajectory(lastPooledConvergenceHistories);
    };
    item.addEventListener("click", toggle);
    item.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            toggle();
        }
    });
    return item;
}

function renderDifferentiationQ(report) {
    // Only `Api.open_run`'s own payload can carry this (design §4.6's
    // q-sweep field) -- a live run's own `"done"` push never does, so
    // this element stays hidden for the common case.
    const sweep = report.Differentiation_q;
    if (!sweep) {
        resultsDifferentiationQ.hidden = true;
        resultsDifferentiationQLines.replaceChildren();
        return;
    }
    resultsDifferentiationQ.hidden = false;
    resultsDifferentiationQLines.replaceChildren();
    const points = [];
    for (const [order, value] of Object.entries(sweep)) {
        const line = document.createElement("p");
        line.className = "field-stat";
        // `value` is a raw float here (`fim.reanalyze.differentiation_q_
        // for_state`'s own return type, sent unformatted since only the
        // six named statistics go through `format_statistic` server-
        // side) -- `toPrecision` mirrors that same `%.6g`-style rounding
        // client-side for this one, not-yet-server-formatted field.
        line.textContent = `q=${order}: ${Number(value).toPrecision(6)}`;
        resultsDifferentiationQLines.appendChild(line);
        points.push({ order: Number(order), value: Number(value) });
    }
    points.sort((a, b) => a.order - b.order);
    const canvas = resultsDifferentiationQCanvas;
    canvas.width = canvas.clientWidth || canvas.width;
    canvas.height = canvas.clientHeight || canvas.height;
    drawDifferentiationQCurve(canvas, points);
}

function renderBatchSummary(summary) {
    // `summary` defaults to `{}` rather than requiring every caller to
    // guard it: every real push carries it (`onBatchDone`'s own
    // payload, `_push_batch_progress`'s own `statistics` field), but a
    // synthetic/partial test payload calling `onBatchProgress` directly
    // for unrelated coverage (`test_input_screen.py`'s own high-water-
    // mark test is exactly this shape) should render every statistic
    // as "omitted" rather than throwing trying to read `undefined[name]`.
    const rows = summary || {};
    batchResultsSummary.replaceChildren();
    for (const name of STATISTIC_NAMES) {
        const interval = rows[name];
        const cells =
            interval === undefined
                ? buildOmittedMeter(name, OMITTED_SUMMARY_TEXT)
                : buildCiMeter(name, interval);
        const row = document.createElement("tr");
        applyStatRow(row, cells);
        batchResultsSummary.appendChild(row);
    }
}

/**
 * Extract a short, human-readable replicate label from its full
 * `replicateId` (`"{run_id}-r{index:03}"`, `batch_runner.
 * replicate_output_directory`'s own naming convention). Multiple
 * replicates can legitimately share the same "Generation" value --
 * independent replicates converging at the same generation is
 * unremarkable, not a bug -- so the table needs something else to
 * tell those rows apart. Falls back to the id unchanged if it does not
 * match the expected suffix shape, rather than guessing.
 *
 * @param {string} replicateId
 */
function replicateLabel(replicateId) {
    const match = /-r(\d+)$/.exec(replicateId || "");
    return match ? `#${Number(match[1])}` : replicateId || "";
}

function renderBatchTable(replicates, p0Statistics) {
    batchResultsTableBody.replaceChildren();
    // p_0 baseline row — the initial conditions the entire batch shared.
    // Column order: Generation | Replicate | Outcome | ...stats | Open
    if (p0Statistics) {
        const baseRow = document.createElement("tr");
        const baseCells = [
            0,
            // Placeholder for Replicate column — shared by the whole batch.
            "",
            "initial",
            ...STATISTIC_NAMES.map((name) => p0Statistics[name]),
        ];
        for (const value of baseCells) {
            const cell = document.createElement("td");
            cell.textContent = String(value);
            baseRow.appendChild(cell);
        }
        // Placeholder for the "Open" column.
        baseRow.appendChild(document.createElement("td"));
        batchResultsTableBody.appendChild(baseRow);
    }
    // Sort by generation ascending, then by replicate index ascending so
    // replicates that converge at the same generation appear in a
    // predictable, stable order rather than insertion/completion order.
    const sorted = [...replicates].sort((a, b) => {
        const genDiff = a.generation - b.generation;
        if (genDiff !== 0) {
            return genDiff;
        }
        // Extract the numeric suffix from the replicateId ("-r001" etc.)
        // for a numeric, not lexicographic, secondary sort.
        const numA = Number(/-r(\d+)$/.exec(a.replicateId || "")?.[1] ?? 0);
        const numB = Number(/-r(\d+)$/.exec(b.replicateId || "")?.[1] ?? 0);
        return numA - numB;
    });
    for (const replicate of sorted) {
        const row = document.createElement("tr");
        // `replicate.reason` is always exactly `"statistic converged"`
        // when `converged` is true (`StopReason`'s own two-value enum) --
        // showing it alongside "Converged" said the same thing twice.
        // The `false` case keeps its own reason (`"hit the cap"`), which
        // adds real information "Not converged" alone does not carry.
        const outcome = replicate.converged
            ? "Converged"
            : `Not converged (${replicate.reason})`;
        // Column order: Generation | Replicate | Outcome | ...stats
        const cells = [
            replicate.generation,
            replicateLabel(replicate.replicateId),
            outcome,
            ...STATISTIC_NAMES.map((name) => replicate.statistics[name]),
        ];
        for (const value of cells) {
            const cell = document.createElement("td");
            cell.textContent = String(value);
            row.appendChild(cell);
        }
        // "Open replicate" (design §4.4): the exact same operation as
        // "Open a run…" over one replicate's own trajectory --
        // `replicate.trajectoryPath` is already joined server-side
        // (`_batch_done_payload`'s own docstring), so this click never
        // does any path logic of its own.
        const openCell = document.createElement("td");
        const openButton = document.createElement("button");
        openButton.type = "button";
        openButton.textContent = "Open";
        openButton.addEventListener("click", async () => {
            const result = await window.pywebview.api.open_run({
                trajectoryPath: replicate.trajectoryPath,
            });
            if (result.ok) {
                // A different persisted run being opened is one of the
                // two points the trajectory legend's own visibility
                // toggle resets (`resetTrajectoryLegendVisibility`'s own
                // doc comment, above, names both).
                window.fim.resetTrajectoryLegendVisibility();
                window.fim.enterCompletedState(result, false);
            }
        });
        openCell.appendChild(openButton);
        row.appendChild(openCell);
        batchResultsTableBody.appendChild(row);
    }
}

function drawCompletedOverview(panels) {
    if (!panels || panels.length === 0) {
        return;
    }
    drawScatter(runCanvas, panels[0]);
}

/**
 * Return whichever entry of `generations` (ascending, no duplicates --
 * `ConvergenceMonitor.generations`'s own invariant) sits closest to
 * `target` -- the nearest-match lookup `updateScrubbedTrajectory` needs
 * because an animation frame's own `generation` (sampled from every
 * *persisted* generation, `fim.gui.animation.select_sample_generations`)
 * is not guaranteed to coincide with an entry the convergence monitor
 * recorded (its own, separately sampled, generation list) -- confirmed
 * during this feature's own live verification that the two arrays are,
 * in fact, sampled identically for an ordinary run (the monitor records
 * every generation; persistence, and so the frame sampler, draws from
 * that exact same complete set), so an exact match is the overwhelmingly
 * common case in practice, but this still resolves correctly the day
 * the two diverge. Ties -- `target` sitting exactly midway between two
 * recorded generations -- resolve to the earlier (smaller) one, simply
 * because this scans `generations` ascending and only replaces its
 * current best on a strictly *smaller* distance; no run in this
 * codebase's own examples produces an exact tie to argue for the other
 * direction, so "whichever the simplest possible loop naturally prefers"
 * stood in for a real tie-breaking rule.
 *
 * @param {number[]} generations
 * @param {number} target
 * @returns {number}
 */
function nearestGeneration(generations, target) {
    let best = generations[0];
    let bestDistance = Math.abs(best - target);
    for (const generation of generations) {
        const distance = Math.abs(generation - target);
        if (distance < bestDistance) {
            best = generation;
            bestDistance = distance;
        }
    }
    return best;
}

/**
 * Answer one completed-state scrubber tick: update the six-row stats
 * table and the trajectory panel's own scrub-position marker to match
 * `frameGeneration` (botanist GUI design doc §6.3's "a scrubber...
 * letting a user drag back through already-computed history," applied
 * here to the completed-state scrubber replaying a finished run, not
 * only a live one). A no-op when this completed entry has no retained
 * per-generation history at all (`completedTrajectoryGenerations` still
 * `null` -- a reopened run, or a batch, which never wires a scrubber to
 * call this in the first place) -- see that variable's own comment for
 * why that one case is a deliberate, named scope boundary rather than
 * something this function tries to answer too.
 *
 * @param {number} frameGeneration - `frame.generation` for the
 *     currently-scrubbed animation frame.
 * @param {boolean} isFinalFrame - whether this is the scrubber's last
 *     frame (`index === frameCount - 1`) -- `pre_render_frames` always
 *     samples the final persisted generation as its own last frame, so
 *     this index comparison is a simpler, equally correct final-frame
 *     test than comparing generation numbers, and it sidesteps the
 *     nearest-match rounding above entirely for the one case (the run's
 *     own real, final, authoritative statistics) where exactness matters
 *     most.
 */
function updateScrubbedTrajectory(frameGeneration, isFinalFrame) {
    if (!completedTrajectoryGenerations || completedTrajectoryGenerations.length === 0) {
        return;
    }
    if (isFinalFrame) {
        // Back at the run's own final state -- restore the real,
        // authoritative statistics exactly as `enterCompletedState`
        // first rendered them, and drop the scrub marker (the curve's
        // own end already sits at this same generation, so a marker
        // there would only ever redraw on top of it).
        for (const name of STATISTIC_NAMES) {
            const element = document.getElementById(`stat-${name}`);
            applyStatRow(element, buildPointMeter(name, completedFinalStatistics[name]));
        }
        renderTrajectory(
            completedTrajectoryGenerations,
            completedTrajectoryHistories,
            completedSigmaBand,
            completedGenerationCount,
            completedEquilibrium,
            completedIdentityRecovery,
            null
        );
        return;
    }
    const scrubGeneration = nearestGeneration(completedTrajectoryGenerations, frameGeneration);
    const scrubIndex = completedTrajectoryGenerations.indexOf(scrubGeneration);
    for (const name of STATISTIC_NAMES) {
        const element = document.getElementById(`stat-${name}`);
        const history = completedTrajectoryHistories[name];
        // Only the watched statistic(s) have a history at all
        // (`completedTrajectoryHistories`'s own comment); and even a
        // watched one can be undefined on some particular recorded tick
        // (`G_ST` at a currently-monomorphic locus, `renderTrajectory`'s
        // own docstring) -- both cases render exactly the same way here
        // as "not known at this generation," never a stale or padded
        // value.
        const value = history && scrubIndex >= 0 ? history[scrubIndex] : undefined;
        if (Number.isFinite(value)) {
            applyStatRow(
                element,
                // `value` is a raw float here, not yet `format_statistic`-
                // formatted (`ConvergenceMonitor.histories`'s own type) --
                // `toPrecision(6)` mirrors that same `%.6g`-style rounding
                // client-side, the identical established precedent
                // `renderDifferentiationQ`'s own comment already uses for
                // this exact situation (a not-yet-server-formatted field).
                buildPointMeter(name, Number(value).toPrecision(6))
            );
        } else {
            applyStatRow(element, buildOmittedMeter(name, OMITTED_SCRUB_TEXT));
        }
    }
    renderTrajectory(
        completedTrajectoryGenerations,
        completedTrajectoryHistories,
        completedSigmaBand,
        completedGenerationCount,
        completedEquilibrium,
        completedIdentityRecovery,
        scrubGeneration
    );
}

/**
 * Fetch and wire the scrubber over a just-completed scalar run's own
 * persisted trajectory -- the direct successor to the old, separate
 * "Animate" button's own `Api.get_animation_frames` call, now made
 * automatically rather than needing a second click (design §3.2.4).
 *
 * @param {string} outputDirectory
 * @param {number} generationCount
 */
async function wireCompletedScrubber(outputDirectory, generationCount) {
    if (generationCount <= 1) {
        scrubberControls.hidden = true;
        window.fim.resetScrubber();
        return;
    }
    // A counter, not a boolean: `completed` reached twice in one window
    // (e.g. re-running from `completed`) can leave the *first* run's own
    // fetch still in flight when the second's starts -- a plain
    // "settled = true" written by whichever of the two resolves first
    // would falsely read as fully settled while the other is still
    // pending. `window.__fimScrubberPending` counts calls actually in
    // flight; zero is the only state a caller should read as settled.
    window.__fimScrubberPending = (window.__fimScrubberPending || 0) + 1;
    try {
        const result = await window.pywebview.api.get_animation_frames(outputDirectory);
        // The state may already have moved on (a new run started, or a
        // different completed run opened) by the time this resolves --
        // never draw stale frames into whatever the canvas now shows.
        if (
            window.fim.getRunViewState() !== "completed" ||
            window.fim.getCompletedOutputDirectory() !== outputDirectory
        ) {
            return;
        }
        if (!result.ok || result.frames.length === 0) {
            scrubberControls.hidden = true;
            window.fim.resetScrubber();
            return;
        }
        scrubberControls.hidden = false;
        window.fim.setScrubberFrames(result.frames, (frame, index) => {
            drawCompletedOverview(frame.panels);
            updateScrubbedTrajectory(frame.generation, index === result.frames.length - 1);
        });
    } finally {
        window.__fimScrubberPending -= 1;
    }
}

/**
 * Enter `completed`: render a just-finished (or re-opened) run's own
 * summary. The one shared entry point every caller uses.
 *
 * @param {object} payload - `_drain_run_messages`'s own `"done"` shape
 *     (scalar) or `_batch_done_payload`'s (batch) -- both carry
 *     `outputDirectory`/`runId`/`panels`/`demeCount`, diverging only in
 *     the statistics/table fields this function reads conditionally.
 * @param {boolean} isBatch
 */
window.fim.enterCompletedState = function enterCompletedState(payload, isBatch) {
    window.fim.setRunViewState("completed");
    window.fim.setCompletedOutputDirectory(payload.outputDirectory);
    // `undefined` (a batch's own payload carries no such key at all) is
    // normalized to `null` here rather than left as `undefined` -- the
    // getter's own contract (`app.js`'s own doc comment) promises
    // `string|null`, matching `completedOutputDirectory`'s identical
    // shape immediately above.
    window.fim.setCompletedTrajectoryPath(payload.trajectoryPath ?? null);
    runProgress.hidden = true;
    if (initialStats) {
        initialStats.hidden = true;
    }
    if (runPlotTitle) {
        runPlotTitle.textContent = payload.runId
            ? `FIM simulation — ${payload.runId}`
            : "FIM simulation — completed";
    }
    runCompleted.hidden = false;
    cancelButton.disabled = true;
    openFolderButton.hidden = false;
    resultsBackButton.hidden = false;
    resultsStats.hidden = isBatch;
    batchResultsTableEl.hidden = !isBatch;
    batchResultsTable.hidden = !isBatch;
    // Item 6: a batch has no single trajectory of its own to re-analyze
    // (the exact same "no single trajectory" boundary `open-run.js`'s
    // own single-click row handler already draws for a batch row).
    resultsReanalyzeControls.hidden = isBatch;
    resultsRunId.textContent = payload.runId;
    // `wireCompletedScrubber` (scalar branch, below) fetches animation
    // frames over a real, un-awaited-by-any-caller bridge call --
    // `window.__fimScrubberPending` (see that function) is how a test
    // knows the last thing this entry into `completed` does in the
    // background has actually finished, the same settled-flag shape
    // `conftest.py`'s own docstring already establishes for five other
    // bridge calls (`refreshRecentRuns`, `cancel_run`, "Open output
    // folder", the external-doc link, the `openExternal` menu dispatch)
    // -- a test destroying the window as soon as it sees `completed` is
    // otherwise racing this call exactly like those five once did. A
    // batch run never reaches `wireCompletedScrubber` at all, so there
    // is nothing to increment here for that branch.
    if (isBatch) {
        resultsOutcome.textContent = "";
        renderBatchSummary(payload.summary);
        renderBatchTable(payload.replicates, payload.p0Statistics);
        scrubberControls.hidden = true;
        window.fim.resetScrubber();
        // A batch's own `completed` view is a pooled final-state
        // scatter across replicates (this file's own module docstring)
        // -- still nothing for a scrub tick to ever answer (there is no
        // scrubber to wire for a batch at all), but, as of batch
        // trajectory panel design `20260912-claude-sonnet-5-batch-
        // trajectory-panel-design.md` (`selby/restricted`) commit 2, no
        // longer nothing to plot: `renderBatchTrajectory` (not
        // `renderTrajectory`, which assumes one shared generation list
        // every statistic's own history aligns against -- an assumption
        // `payload.pooledConvergenceHistories`'s own per-point-scoped
        // generations do not hold) draws the real, authoritative
        // cross-replicate aggregate.
        completedTrajectoryGenerations = null;
        completedTrajectoryHistories = null;
        completedSigmaBand = null;
        completedEquilibrium = null;
        completedIdentityRecovery = null;
        completedGenerationCount = null;
        completedFinalStatistics = null;
        renderBatchTrajectory(payload.pooledConvergenceHistories);
    } else {
        // A different run just opened (or a live run just finished) --
        // any generation/sweep choice left over from whatever this
        // card showed before must not silently apply to it too. A
        // re-analysis of *this* run (`resultsReanalyzeButton`'s own
        // handler) re-enters this same branch on success, which is
        // exactly why that handler's own chosen generation/sweep values
        // are read into `values` *before* this call, not after.
        document.querySelector(
            'input[name="results_generation_mode"][value="final"]'
        ).checked = true;
        resultsGenerationValueInput.value = "";
        resultsDifferentiationOrdersInput.value = "";
        const report = payload.report;
        const reason = report.reason.charAt(0).toUpperCase() + report.reason.slice(1);
        resultsOutcome.textContent = `${reason}: generation ${report.generation}`;
        for (const name of STATISTIC_NAMES) {
            const value = payload.statistics[name];
            const element = document.getElementById(`stat-${name}`);
            applyStatRow(element, buildPointMeter(name, value));
        }
        renderEffectiveAlleles(payload.effectiveAlleles);
        renderDifferentiationQ(report);
        // Retained so a later scrub tick (`updateScrubbedTrajectory`,
        // via `wireCompletedScrubber`'s own `setScrubberFrames` callback,
        // below) can answer "what did the stats table/trajectory marker
        // look like at this other generation" without a second bridge
        // round trip -- `payload.convergenceGenerations`/
        // `convergenceHistories` are `undefined` for a reopened run
        // (`Api.open_run`), which normalizes to `null` here exactly like
        // `enterCompletedState`'s own batch branch above, the signal
        // `updateScrubbedTrajectory` reads as "nothing to answer a scrub
        // tick with."
        completedTrajectoryGenerations = payload.convergenceGenerations || null;
        completedTrajectoryHistories = payload.convergenceHistories || null;
        completedSigmaBand = payload.sigmaBand || null;
        completedEquilibrium = payload.equilibrium || null;
        completedIdentityRecovery = payload.identityRecovery || null;
        completedGenerationCount = payload.generationCount;
        completedFinalStatistics = payload.statistics;
        renderTrajectory(
            payload.convergenceGenerations,
            payload.convergenceHistories,
            payload.sigmaBand,
            payload.generationCount,
            payload.equilibrium,
            payload.identityRecovery
        );
        wireCompletedScrubber(payload.outputDirectory, payload.generationCount);
    }

    const panels = payload.panels;
    drawCompletedOverview(panels);
    runDemePairSelector.hidden = !panels || payload.demeCount < 2;
    if (panels && panels.length > 0) {
        window.fim.wireDemePairSelector({
            xSelect: runXDeme,
            ySelect: runYDeme,
            container: runDemePairSelector,
            selfComparisonNote: runDemePairSelfNote,
            demeCount: payload.demeCount,
            onShowPair: async (x, y) => {
                const outputDirectory = window.fim.getCompletedOutputDirectory();
                if (outputDirectory === null) {
                    return;
                }
                const getPanel = isBatch
                    ? window.pywebview.api.get_batch_deme_pair_panel
                    : window.pywebview.api.get_deme_pair_panel;
                const result = await getPanel(outputDirectory, x, y);
                if (result.ok) {
                    drawScatter(runCanvas, result.panel);
                }
            },
        });
    }

    window.fim.showScreen("screen-run");
};

window.fim.returnToInitialState = function returnToInitialState() {
    resultsBackButton.hidden = true;
    window.fim.showScreen("screen-run");
    window.fim.enterInitialState();
};

// Wire the Back button once on load -- navigates from `completed` back
// to `initial` (p_0 preview), the same as clicking "Run simulation"
// would do for a fresh start but without actually starting a run.
resultsBackButton.addEventListener("click", () => {
    window.fim.returnToInitialState();
});

/**
 * Read `#results-reanalyze-controls`'s own generation-mode radio group
 * -- the Results-card counterpart to the old open-run screen's own
 * (now removed) `generationMode` helper, same "default to final if
 * somehow nothing is checked" fallback.
 * @returns {"final"|"choose"}
 */
function resultsGenerationMode() {
    const checked = document.querySelector(
        'input[name="results_generation_mode"]:checked'
    );
    return checked ? checked.value : "final";
}

// Item 6: re-analyze whichever run is currently showing (live-just-
// finished or reopened -- `window.fim.getCompletedTrajectoryPath()`
// covers both, `enterCompletedState` sets it from `payload.
// trajectoryPath` either way) at a different persisted generation, or
// with a differentiation-q sweep, without leaving the Results card.
// Literal reuse of `Api.open_run`, the exact bridge call the old open-
// run screen's own "Open" button used to make before item 6 moved
// these controls here -- re-entering `completed` on success re-renders
// this same screen with the new report, not a second rendering path.
resultsReanalyzeButton.addEventListener("click", async () => {
    const trajectoryPath = window.fim.getCompletedTrajectoryPath();
    if (trajectoryPath === null) {
        window.fim.showRunBanner("no trajectory to re-analyze");
        return;
    }
    const result = await window.pywebview.api.open_run({
        trajectoryPath,
        generationMode: resultsGenerationMode(),
        generation: resultsGenerationValueInput.value,
        differentiationOrders: resultsDifferentiationOrdersInput.value,
    });
    if (!result.ok) {
        window.fim.showRunBanner(result.message);
        return;
    }
    window.fim.showRunBanner("");
    // A different generation/sweep of the same run is re-analyzed here
    // exactly like a different persisted run being opened is (`open-
    // run.js`'s own `openTrajectory`) -- both are "the trajectory
    // legend no longer describes what's on screen" moments (design
    // §6.2's legend-toggle; `resetTrajectoryLegendVisibility`'s own doc
    // comment names both).
    window.fim.resetTrajectoryLegendVisibility();
    window.fim.enterCompletedState(result, false);
});
