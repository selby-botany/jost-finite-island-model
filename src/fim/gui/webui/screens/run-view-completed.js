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

// `STATISTIC_NAMES` (every global statistic) and the other statistic
// lists come from the catalog (`statistics-catalog.js`), not from a
// list kept here.

// The two effective-allele rows (botanist GUI design doc §7.7), shared
// between the scalar completed view's own `renderEffectiveAlleles` and
// the batch summary's own `renderBatchSummary` below -- one label
// source, not two independently retyped strings that could drift apart.
// `<sup>H</sup>D<sub>S</sub>`/`<sup>H</sup>D<sub>T</sub>` matches the
// differentiation-measures guide's own notation (`doc/jost-
// differentiation-measures.md`) for the effective number of alleles
// derived from `H_S`/`H_T` via `{}^{H}D = 1/(1-H)`.
const EFFECTIVE_ALLELE_LABELS = [
    ["<sup>H</sup>D<sub>S</sub>", "H_S", "effective number of alleles per deme"],
    [
        "<sup>H</sup>D<sub>T</sub>",
        "H_T",
        "effective number of alleles pooled across demes",
    ],
];

// Shown only once this run's own H_S crosses the threshold where G_ST's
// own ratio-of-heterozygosities construction can under-report real
// differentiation (`Api._effective_allele_summary`'s own `gStCaution`
// flag) -- one entry among `renderRunMessages`'s own consolidated list,
// not a separately positioned element beside the statistics table
// (reported live: that older placement broke the table's own layout).
const G_ST_CAUTION_TEXT =
    "Gₓₛₜ is compressed toward zero when within-deme diversity " +
    "is this high, even between demes that share no alleles at all " +
    "— Jost's D does not have this artifact.";

// The two "always present" info lines (design: the run's own outcome,
// and the derived-convergence-settings note when the window/cap were
// auto-derived) -- set once per `enterCompletedState` call, then carried
// through every later `renderRunMessages` call (a scrub tick, an
// effective-allele re-render) so a transient warning's own comings and
// goings never drop them. Empty for whichever state has not populated
// them yet.
let baseRunMessages = [];

// The statistic(s) a completed scalar run stopped on (`report.
// converged_on`), so a run that stopped on a statistic the researcher has
// hidden can say so; `null` when no such run is shown.
let completedConvergedOn = null;

// The deme pair the completed view's scatter shows (`[x, y]`, 1-based),
// its final-generation statistics, and, once fetched, its per-frame
// statistics for the scrubber (`null` until then; the default pair's
// ride on the scrubber's own frames).
let completedPair = [1, 2];
let completedFinalPairStatistics = null;
let completedPairFrameStatistics = null;

// The scrubber frame last shown, so pair values fetched later can be
// painted onto it (`loadPairFrameStatistics`).
let lastScrubbedPairFrame = null;

/**
 * Fetch the chosen pair's statistics for every scrubber frame, once, and
 * repaint the frame on screen. Only reached when someone scrubs a pair
 * other than the default, so choosing a pair costs no extra bridge call
 * until it is needed. Counted in `window.__fimScrubberPending`, like the
 * scrubber's own fetches.
 */
async function loadPairFrameStatistics() {
    const outputDirectory = window.fim.getCompletedOutputDirectory();
    if (outputDirectory === null || completedPairFrameStatistics !== null) {
        return;
    }
    const [x, y] = completedPair;
    completedPairFrameStatistics = [];
    window.__fimScrubberPending = (window.__fimScrubberPending || 0) + 1;
    try {
        const result = await window.pywebview.api.get_animation_deme_pair_frames(
            outputDirectory,
            x,
            y
        );
        if (completedPair[0] !== x || completedPair[1] !== y) {
            return;
        }
        completedPairFrameStatistics = result.ok
            ? result.frames.map((frame) => frame.pairStatistics)
            : null;
        if (lastScrubbedPairFrame) {
            const { frame, index, isFinal } = lastScrubbedPairFrame;
            renderPairStatistics(
                pairStatisticsForFrame(frame, index, isFinal),
                "not known at this generation for this pair"
            );
        }
    } finally {
        window.__fimScrubberPending -= 1;
    }
}

/**
 * The pair statistics to show for one scrubber frame.
 *
 * @param {object} frame
 * @param {number} index
 * @param {boolean} isFinal
 * @returns {object|null}
 */
function pairStatisticsForFrame(frame, index, isFinal) {
    if (isFinal && completedFinalPairStatistics) {
        return completedFinalPairStatistics;
    }
    if (completedPair[0] === 1 && completedPair[1] === 2) {
        return frame.pairStatistics || null;
    }
    if (completedPairFrameStatistics === null) {
        loadPairFrameStatistics();
        return null;
    }
    return completedPairFrameStatistics[index] || null;
}

/**
 * A note when the shown run stopped on a statistic that is hidden: the
 * stop reason names a statistic nothing on screen shows otherwise.
 * Never forces it visible; that would defeat the choice.
 *
 * @returns {Array<{severity: "info", text: string}>}
 */
function hiddenStopMessages() {
    if (completedConvergedOn === null) {
        return [];
    }
    const watched = Array.isArray(completedConvergedOn)
        ? completedConvergedOn
        : [completedConvergedOn];
    const hidden = watched.filter((key) => statisticSpec(key) && !isStatisticShown(key));
    if (hidden.length === 0) {
        return [];
    }
    const names = hidden.map((key) => plainStatisticLabel(key)).join(", ");
    return [
        {
            severity: "info",
            text:
                `This run stopped on a statistic you have hidden: ${names}. ` +
                'Show it with "Choose statistics…".',
        },
    ];
}

/**
 * Repaint what depends on the shown set: the trajectory curves, the
 * toggle states and the run messages (`statistics-catalog.js` calls this
 * after `setShownStatistics`).
 */
window.fim.onShownStatisticsChanged = function onShownStatisticsChanged() {
    repaintTrajectory();
    refreshTrajectoryStatisticRowStates();
    renderRunMessages();
};

/**
 * Replace `baseRunMessages` -- the "always present" info lines a later
 * `renderRunMessages` call folds in automatically.
 * @param {Array<{severity: "info"|"warning"|"error", text: string}>} messages
 */
function setBaseRunMessages(messages) {
    baseRunMessages = messages;
}

/**
 * Rebuild the Run card's own consolidated message area from
 * `baseRunMessages` plus whatever this call adds -- one bulleted `<li>`
 * per message, colored by `severity` (`.run-message-info`/`-warning`/
 * `-error`, `app.css`). Replaces every message-related element this
 * page used to position independently (the G_ST caution note beside the
 * statistics table, chiefly) with the one list a botanist reads in one
 * place.
 * @param {Array<{severity: "info"|"warning"|"error", text: string}>} [extraMessages]
 */
// One literal class string per severity -- not a template literal
// (`` `run-message run-message-${severity}` ``) interpolating `severity`
// into the class name, which `dev/bin/check-webui-assets`'s own static
// scan cannot resolve against the real, separately-defined `app.css`
// rules it names.
const RUN_MESSAGE_CLASSES = {
    info: "run-message run-message-info",
    warning: "run-message run-message-warning",
    error: "run-message run-message-error",
};

function renderRunMessages(extraMessages = []) {
    const messages = [...baseRunMessages, ...hiddenStopMessages(), ...extraMessages];
    runMessagesList.replaceChildren();
    runMessagesList.hidden = messages.length === 0;
    for (const { severity, text } of messages) {
        const item = document.createElement("li");
        item.className = RUN_MESSAGE_CLASSES[severity];
        const dot = document.createElement("span");
        dot.className = "run-message-dot";
        item.append(dot, text);
        runMessagesList.appendChild(item);
    }
}

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
// Statistics whose trajectory curve starts hidden come from the
// catalog's `default_plotted` (`DEFAULT_HIDDEN_TRAJECTORY_STATISTICS`,
// `statistics-catalog.js`): `A_CGD`, `Delta` (δ_G) and `MI` (I) are not
// on the `[0, 1]` scale the report statistics share, so one of them can
// stretch the shared y-axis until every other curve is a flat band near
// the bottom. They stay in the legend and the table, one click away.

let hiddenTrajectoryStatistics = new Set(DEFAULT_HIDDEN_TRAJECTORY_STATISTICS);

/**
 * Whether `name`'s curve is left off the trajectory chart: hidden with
 * the legend toggle, or not among the statistics shown at all.
 *
 * @param {string} name
 * @returns {boolean}
 */
function isTrajectoryHidden(name) {
    return hiddenTrajectoryStatistics.has(name) || !isStatisticShown(name);
}

// The catalog arrives after this script loads: the default-hidden
// curves and the results tables' statistic columns exist only then.
onStatisticCatalogReady(() => {
    hiddenTrajectoryStatistics = new Set(DEFAULT_HIDDEN_TRAJECTORY_STATISTICS);
    wireResultsTableHeaders(document.getElementById("batch-results-table"), 2);
    wireResultsTableHeaders(runResultsTableEl, 1);
});

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
// The scrub position that accompanied `lastPooledConvergenceHistories`,
// cached for the same reason as the rest of the pair: a repaint that
// only knows the data (a legend toggle, a zoom-frame resize) would
// otherwise redraw the batch panel *without* its position marker while
// the scrubber's own label still names a generation -- reported from
// the zoom frame, where every resize dropped the marker until the next
// scrubber nudge. The scalar path never had this gap, since
// `lastTrajectoryRenderArgs` already carries its scrub argument.
let lastBatchScrubGeneration = null;
let activeTrajectoryRenderMode = null;

/**
 * Reset the trajectory legend's own hidden-statistic set to this
 * panel's own default (`DEFAULT_HIDDEN_TRAJECTORY_STATISTICS`, i.e.
 * every `[0, 1]`-scaled statistic shown and the differently-scaled
 * ones hidden) -- called whenever a genuinely new run starts or a
 * different persisted run is opened (see `hiddenTrajectoryStatistics`'s
 * own comment for why this is not reset on every `enterCompletedState`
 * call).
 */
window.fim.resetTrajectoryLegendVisibility = function resetTrajectoryLegendVisibility() {
    hiddenTrajectoryStatistics = new Set(DEFAULT_HIDDEN_TRAJECTORY_STATISTICS);
};

const resultsRunId = document.getElementById("results-run-id");
const resultsOutcome = document.getElementById("results-outcome");
const resultsStats = document.getElementById("results-stats");
const runPlotRow = document.getElementById("run-plot-row");
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
const runMessagesList = document.getElementById("run-messages");
// `batchResultsTableEl` is the `<table>` whose own `hidden` attribute
// gates visibility; `batchResultsSummary` is its `<tbody>`, where
// `renderBatchSummary` rebuilds rows -- kept as two names rather than
// one so every existing `batchResultsSummary.appendChild`/
// `.replaceChildren` call below keeps working unchanged.
const batchResultsTableEl = document.getElementById("batch-results-summary");
const batchResultsSummary = document.getElementById("batch-results-summary-body");
const batchResultsTableBody = document.getElementById("batch-results-table-body");
// The scalar counterpart to `batchResultsTableBody`: one row per
// sampled generation rather than one row per replicate.
const runResultsTableEl = document.getElementById("run-results-table");
const runResultsTableBody = document.getElementById("run-results-table-body");

// The two results tables' column headers double as the statistic
// toggles (`wireResultsTableHeaders`), wired once the catalog has built
// them (`onStatisticCatalogReady`, above).
const resultsBackButton = document.getElementById("results-back-button");
const resultsHistoryBackButton = document.getElementById("results-history-back-button");
const resultsHistoryForwardButton = document.getElementById(
    "results-history-forward-button"
);

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
let completedClosedForm = null;
// The just-shown run's own `report.window_statistics` (design doc
// `20260927-claude-sonnet-5-noise-aware-convergence-design.md`,
// `selby/restricted`) -- how precisely each recorded statistic's
// trailing-window mean was actually known when the run stopped, keyed by
// statistic name. `null` for a batch (no single trailing window; the
// pooled confidence interval already answers the analogous question) or
// before any run has been shown at all.
let completedWindowStatistics = null;
let completedGenerationCount = null;
let completedFinalStatistics = null;
let completedFinalLiteratureVisuals = null;
// The batch entry's own counterpart to `completedFinalStatistics`: the
// authoritative final pooled summary and effective-allele rows, kept
// so a scrub tick that lands back on the final frame restores them
// exactly (`updateScrubbedBatchSummary`), rather than re-deriving them
// from the per-generation pooled points -- which could never restore
// `effectiveAlleles`'s own rows anyway, those having no per-generation
// source. Set by `enterCompletedState`'s own batch branch, nulled by
// its scalar one, matching the lifetime every `completed*` variable
// above already observes.
let completedBatchSummary = null;
let completedBatchEffectiveAlleles = null;
// The scalar entry's own final `effectiveAlleles` payload, retained for
// the same reason and on the same lifetime: a scrub tick back at the
// final frame restores the two derived rows (`renderEffectiveAlleles`)
// verbatim rather than re-deriving them from the pooled histories it
// never came from.
let completedEffectiveAlleles = null;
// The scalar per-generation results table's own one extra fact: the
// stop reason to print on its own final row. Kept beside the scrub-
// replay state above because it shares that lifetime exactly -- set by
// `enterCompletedState`'s own scalar branch, nulled by its batch one.
let completedReportReason = null;

// The statistic under the pointer or keyboard focus in the stats panel
// or a results-table header. Its curve is drawn heavier and the others
// fade, so a row, a column and a line are visibly the same statistic.
let highlightedTrajectoryStatistic = null;

/**
 * Line width and opacity for one statistic's curve given the current
 * highlight.
 *
 * @param {string} name
 * @returns {{width: number, alpha: number}}
 */
function trajectoryEmphasis(name) {
    if (highlightedTrajectoryStatistic === null) {
        return { width: TRAJECTORY_LINE_WIDTH, alpha: 1 };
    }
    return name === highlightedTrajectoryStatistic
        ? { width: TRAJECTORY_HIGHLIGHT_LINE_WIDTH, alpha: 1 }
        : { width: TRAJECTORY_LINE_WIDTH, alpha: TRAJECTORY_FADED_ALPHA };
}

/**
 * Highlight (or, with `null`, stop highlighting) one statistic across
 * the graph, its stats-panel row and its results-table column.
 *
 * @param {string|null} name
 */
function setHighlightedTrajectoryStatistic(name) {
    if (highlightedTrajectoryStatistic === name) {
        return;
    }
    highlightedTrajectoryStatistic = name;
    for (const element of document.querySelectorAll(
        "[data-statistic], [data-trajectory-statistic]"
    )) {
        const own = element.dataset.statistic ?? element.dataset.trajectoryStatistic;
        element.classList.toggle("stat-highlight", name !== null && own === name);
    }
    repaintTrajectory();
}

/**
 * Wire hover and keyboard focus on `element` to the shared highlight.
 *
 * @param {HTMLElement} element
 * @param {string} name
 */
function wireStatisticHighlight(element, name) {
    element.addEventListener("mouseenter", () => setHighlightedTrajectoryStatistic(name));
    element.addEventListener("mouseleave", () => setHighlightedTrajectoryStatistic(null));
    element.addEventListener("focus", () => setHighlightedTrajectoryStatistic(name));
    element.addEventListener("blur", () => setHighlightedTrajectoryStatistic(null));
}

/**
 * Make a results table's statistic column headers the same toggle the
 * stats panel rows are: a colored bar (hollow when hidden), click or
 * Enter/Space to show or hide the curve, hover to highlight it.
 *
 * @param {HTMLTableElement} table
 * @param {number} offset - Index of the first statistic column.
 */
function wireResultsTableHeaders(table, offset) {
    const headers = table.querySelectorAll("thead th");
    STATISTIC_NAMES.forEach((name, index) => {
        const header = headers[offset + index];
        // A statistic with no curve gets no toggle (see
        // `decorateTrajectoryStatisticRow`).
        if (!header || !TRAJECTORY_STATISTIC_NAMES.includes(name)) {
            return;
        }
        header.dataset.statistic = name;
        header.classList.add("stat-column-header");
        header.style.setProperty(
            "--stat-color",
            STATISTIC_TRAJECTORY_COLORS[name] || "var(--fim-muted)"
        );
        header.tabIndex = 0;
        header.setAttribute("role", "button");
        header.title = `Show or hide ${name} on the graph`;
        header.addEventListener("click", () => toggleTrajectoryStatistic(name));
        header.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                toggleTrajectoryStatistic(name);
            }
        });
        wireStatisticHighlight(header, name);
    });
}

/**
 * Tag a results table body's statistic cells so a hidden statistic
 * dims its whole column, then repaint the toggle state.
 *
 * @param {HTMLElement} tbody
 * @param {number} offset - Index of the first statistic column.
 */
function tagStatisticCells(tbody, offset) {
    for (const row of tbody.children) {
        STATISTIC_NAMES.forEach((name, index) => {
            const cell = row.children[offset + index];
            if (cell) {
                if (TRAJECTORY_STATISTIC_NAMES.includes(name)) {
                    cell.dataset.statistic = name;
                }
                cell.dataset.shownKey = name;
            }
        });
    }
    applyStatisticVisibility();
    refreshTrajectoryStatisticRowStates();
}

/**
 * Build a statistic row's own hover description, with a trailing-window
 * precision note appended when one is available.
 *
 * `completedWindowStatistics[name]` (design doc `20260927-claude-sonnet-5-
 * noise-aware-convergence-design.md`, `selby/restricted`) is a materially
 * better estimate than the single point value the table itself shows --
 * the mean of `window` generations, not one of them -- whether or not it
 * also happened to satisfy the noise-adequacy gate; this surfaces it in
 * the same place the CLI's own `_print_window_statistics` prints it,
 * without adding a second visible column to the table.
 *
 * @param {string} name
 * @returns {string} The base statistic description, unchanged, when
 *     `completedWindowStatistics` has no entry for `name`; otherwise that
 *     description plus a trailing-window mean/standard-error clause.
 */
function windowStatisticsDescription(name) {
    const base = statisticDescription(name);
    const stats = completedWindowStatistics && completedWindowStatistics[name];
    if (!stats) {
        return base;
    }
    const precision = stats.noise_adequate ? "" : ", not yet noise-adequate";
    const note =
        ` — window mean ${stats.mean.toFixed(WINDOW_STATISTIC_DECIMALS)} ± ` +
        `${stats.standard_error.toFixed(WINDOW_STATISTIC_DECIMALS)} (1σ, last ` +
        `${stats.window.toLocaleString()} generations${precision})`;
    return base + note;
}

/**
 * Add or refresh one statistic row's plot color tile and display-toggle
 * behavior. The statistic value cells are still rebuilt by `applyStatRow`
 * on every progress/completed/scrub update; this only attaches the row
 * to the trajectory visibility state.
 *
 * @param {HTMLTableRowElement} row
 * @param {string} name
 * @returns {HTMLTableRowElement}
 */
function decorateTrajectoryStatisticRow(row, name) {
    // Only a statistic with a per-generation history has a curve to show
    // or hide; the rest (`Gs`, `Gd`, the Nei family) keep the empty color
    // column every row has, so all rows still line up.
    if (!TRAJECTORY_STATISTIC_NAMES.includes(name)) {
        return row;
    }
    if (row.dataset.trajectoryStatistic !== name) {
        row.dataset.trajectoryStatistic = name;
        wireStatisticHighlight(row, name);
        row.addEventListener("click", () => toggleTrajectoryStatistic(name));
        row.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                toggleTrajectoryStatistic(name);
            }
        });
    }
    let toggleCell = row.querySelector(".stat-plot-toggle");
    if (toggleCell === null) {
        toggleCell = document.createElement("td");
        toggleCell.className = "stat-plot-toggle";
        row.insertBefore(toggleCell, row.firstChild);
    }
    toggleCell.replaceChildren();
    const swatch = document.createElement("span");
    swatch.className = "stat-plot-swatch";
    swatch.style.backgroundColor =
        STATISTIC_TRAJECTORY_COLORS[name] || "var(--fim-muted)";
    toggleCell.appendChild(swatch);
    updateTrajectoryStatisticRowState(row, name);
    return row;
}

/**
 * Repaint every plot-toggle row's pressed/hidden state from the shared
 * visibility set.
 */
function refreshTrajectoryStatisticRowStates() {
    for (const row of document.querySelectorAll("[data-trajectory-statistic]")) {
        updateTrajectoryStatisticRowState(row, row.dataset.trajectoryStatistic);
    }
    for (const element of document.querySelectorAll("[data-statistic]")) {
        const visible = !hiddenTrajectoryStatistics.has(element.dataset.statistic);
        element.classList.toggle("stat-plot-hidden", !visible);
        if (element.classList.contains("stat-column-header")) {
            element.setAttribute("aria-pressed", String(visible));
        }
    }
}

/**
 * Toggle a statistic curve from any statistic table row and redraw the
 * active trajectory panel. The table values stay present and continue
 * updating because only the canvas inputs are filtered.
 *
 * @param {string} name
 */
/**
 * Repaint the trajectory panel from whichever data it last drew.
 *
 * The two render paths each cache their own arguments, so this needs
 * no data of its own -- it is both the legend-toggle re-render and the
 * graph stage's registered repaint for this pane.
 *
 * @returns {void}
 */
function repaintTrajectory() {
    if (activeTrajectoryRenderMode === "scalar" && lastTrajectoryRenderArgs) {
        renderTrajectory(...lastTrajectoryRenderArgs);
    }
    if (activeTrajectoryRenderMode === "batch" && lastPooledConvergenceHistories) {
        renderBatchTrajectory(lastPooledConvergenceHistories, lastBatchScrubGeneration);
    }
}

function toggleTrajectoryStatistic(name) {
    if (hiddenTrajectoryStatistics.has(name)) {
        hiddenTrajectoryStatistics.delete(name);
    } else {
        hiddenTrajectoryStatistics.add(name);
    }
    repaintTrajectory();
    refreshTrajectoryStatisticRowStates();
}

/**
 * Apply ARIA/CSS state for one plot-toggle row.
 *
 * @param {HTMLTableRowElement} row
 * @param {string} name
 */
function updateTrajectoryStatisticRowState(row, name) {
    const visible = !hiddenTrajectoryStatistics.has(name);
    row.classList.add("stat-plot-toggle-row");
    row.classList.toggle("stat-plot-hidden", !visible);
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.setAttribute("aria-pressed", String(visible));
}

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
        renderRunMessages();
        return;
    }
    const [
        [withinLabel, withinKey, withinDescription],
        [totalLabel, totalKey, totalDescription],
    ] = EFFECTIVE_ALLELE_LABELS;
    applyStatRow(
        neSRow,
        buildPointMeter(withinLabel, effectiveAlleles[withinKey], withinDescription)
    );
    applyStatRow(
        neTRow,
        buildPointMeter(totalLabel, effectiveAlleles[totalKey], totalDescription)
    );
    renderRunMessages(
        effectiveAlleles.gStCaution ? [{ severity: "warning", text: G_ST_CAUTION_TEXT }] : []
    );
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

    const plotLeft = DIFFERENTIATION_Q_PLOT_MARGIN.left;
    const plotRight = width - DIFFERENTIATION_Q_PLOT_MARGIN.right;
    const plotTop = DIFFERENTIATION_Q_PLOT_MARGIN.top;
    const plotBottom = height - DIFFERENTIATION_Q_PLOT_MARGIN.bottom;

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
    context.lineWidth = AXIS_LINE_WIDTH;
    context.beginPath();
    context.moveTo(plotLeft, plotTop);
    context.lineTo(plotLeft, plotBottom);
    context.lineTo(plotRight, plotBottom);
    context.stroke();

    context.strokeStyle = borderColor;
    context.fillStyle = mutedColor;
    drawAxisTickMarks(
        context,
        "y",
        plotLeft,
        plotBottom,
        yToPixel,
        PROBABILITY_TICK_VALUES,
        AXIS_TICK_FONT_SIZE,
        (value) => value.toFixed(PROBABILITY_TICK_DECIMALS)
    );
    drawAxisTickMarks(
        context,
        "x",
        plotLeft,
        plotBottom,
        xToPixel,
        points.map((point) => point.order),
        AXIS_TICK_FONT_SIZE,
        (order) => `q=${order}`
    );

    context.strokeStyle = accentColor;
    context.lineWidth = CURVE_LINE_WIDTH;
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
        context.arc(x, y, DIFFERENTIATION_Q_POINT_RADIUS, 0, TAU);
        context.fill();
    });
    context.stroke();
}

/**
 * Draw the interior tick marks and labels a trajectory panel's own
 * corner labels leave out: nicely-rounded reference values (1/2/5
 * times a power of ten -- `niceAxisTicks`, the standard scientific-plot
 * convention) along both axes, so intermediate positions read at a
 * glance at any scale -- a `[0, 1]` statistic axis ticks at every 0.2,
 * a generations axis at every 10 for a 67-generation run or every 2000
 * for a 10000-generation one. The corner labels themselves (the exact
 * domain endpoints, including a sigma band's own overshoot past 1)
 * are unchanged: a nice tick that coincides with a corner is skipped
 * here, not drawn twice.
 *
 * Shared by `drawTrajectoryCurve` and `drawBatchTrajectoryCurve`, whose
 * axis blocks are identical.
 *
 * @param {CanvasRenderingContext2D} context - Colors already set.
 * @param {number} plotLeft
 * @param {number} plotBottom
 * @param {number} minGeneration
 * @param {number} maxGeneration
 * @param {number} minValue
 * @param {number} maxValue
 * @param {(value: number) => number} xToPixel
 * @param {(value: number) => number} yToPixel
 */
function drawTrajectoryAxisTicks(
    context,
    plotLeft,
    plotBottom,
    minGeneration,
    maxGeneration,
    minValue,
    maxValue,
    xToPixel,
    yToPixel
) {
    const yTicks = niceAxisTicks(minValue, maxValue, TRAJECTORY_TICK_TARGET).filter(
        (value) => value > minValue && value < maxValue
    );
    // Generations are whole numbers -- a sub-integer step (a very short
    // run) keeps only whichever nice values happen to be integers, or
    // no interior ticks at all when none are (a 2-generation run).
    const xTicks = niceAxisTicks(minGeneration, maxGeneration, TRAJECTORY_TICK_TARGET).filter(
        (value) =>
            Number.isInteger(value) && value > minGeneration && value < maxGeneration
    );
    drawAxisTickMarks(
        context,
        "y",
        plotLeft,
        plotBottom,
        yToPixel,
        yTicks,
        AXIS_TICK_FONT_SIZE,
        (v) => v.toFixed(TRAJECTORY_TICK_DECIMALS)
    );
    drawAxisTickMarks(
        context,
        "x",
        plotLeft,
        plotBottom,
        xToPixel,
        xTicks,
        AXIS_TICK_FONT_SIZE,
        (v) => String(v)
    );
}

/**
 * Read the closed-form expected trajectory off a sampled curve.
 *
 * The general-model half of `_closed_form_trajectory_payload`: the server
 * solved the whole identity matrix from the run's seeded starting
 * population and sampled the result at `closedForm.generations` (every
 * generation up to 100, then geometric spacing to the run's cap), so this
 * only interpolates linearly between the two samples around each plotted
 * generation, and holds the last value beyond the grid.
 *
 * @param {{generations: number[], statistics: Object<string, number[]>}} closedForm
 * @param {number[]} generations the generations being plotted, ascending.
 * @returns {Object<string, number[]>} one array per statistic, each the
 *     same length as `generations`; empty on malformed input.
 */
function sampledClosedFormTrajectories(closedForm, generations) {
    const grid = closedForm.generations;
    const names = Object.keys(closedForm.statistics);
    if (
        !Array.isArray(grid) ||
        grid.length === 0 ||
        names.some((name) => closedForm.statistics[name].length !== grid.length)
    ) {
        return {};
    }
    const series = Object.fromEntries(names.map((name) => [name, []]));
    let upper = 0;
    for (const generation of generations) {
        while (upper < grid.length - 1 && grid[upper] < generation) {
            upper += 1;
        }
        const lower = Math.max(0, upper - 1);
        const span = grid[upper] - grid[lower];
        const fraction =
            span > 0 ? Math.min(1, Math.max(0, (generation - grid[lower]) / span)) : 1;
        for (const name of names) {
            const values = closedForm.statistics[name];
            series[name].push(values[lower] + fraction * (values[upper] - values[lower]));
        }
    }
    return Object.values(series).every((values) => values.every(Number.isFinite))
        ? series
        : {};
}

/**
 * Evaluate the closed-form expected trajectory of every identity-based
 * statistic at each of `generations`.
 *
 * The page-side half of `fim.statistics.identity_recursion` (the
 * server sends the solved recursion, `_closed_form_trajectory_payload`;
 * see `IdentityRecursion.identities_after` for the same arithmetic in
 * Python, which a test holds this function to). The two expected
 * identities start where the run itself did — read from its own first
 * recorded `H_S` and `H_T` — and relax as `x* + V diag(lambda^t) V^-1
 * (x0 - x*)`, `t` being generations since that first point. `D`,
 * `G_ST`, `H_S`, `H_T` and `H_ST` are then functions of the two
 * identities alone.
 *
 * @param {object|null|undefined} closedForm `_closed_form_trajectory_
 *     payload`'s result: either the solved two-variable ingredients
 *     (evaluated here) or, for a model with unequal deme sizes or a
 *     migration matrix, a sampled curve (`sampledClosedFormTrajectories`).
 * @param {number[]} generations
 * @param {Object<string, number[]>} histories the run's own histories;
 *     only `H_S` and `H_T` are read, and only when both cover every one
 *     of `generations`.
 * @returns {Object<string, number[]>} one array per statistic, each the
 *     same length as `generations`; empty when there is nothing to
 *     evaluate (no payload, no starting state, or a non-finite result).
 */
function closedFormTrajectories(closedForm, generations, histories) {
    if (closedForm && closedForm.statistics) {
        return sampledClosedFormTrajectories(closedForm, generations);
    }
    const hS = histories.H_S;
    const hT = histories.H_T;
    if (
        !closedForm ||
        generations.length === 0 ||
        !hS ||
        !hT ||
        hS.length !== generations.length ||
        hT.length !== generations.length
    ) {
        return {};
    }
    const demes = closedForm.demes;
    const [fixedWithin, fixedBetween] = closedForm.fixedPoint;
    const [highRate, lowRate] = closedForm.eigenvalues;
    const vectors = closedForm.eigenvectors;
    const inverse = closedForm.inverse;
    const within0 = 1 - hS[0];
    const between0 = (demes * (1 - hT[0]) - within0) / (demes - 1);
    const offsetWithin = within0 - fixedWithin;
    const offsetBetween = between0 - fixedBetween;
    const highWeight = inverse[0][0] * offsetWithin + inverse[0][1] * offsetBetween;
    const lowWeight = inverse[1][0] * offsetWithin + inverse[1][1] * offsetBetween;
    const series = { D: [], G_ST: [], H_S: [], H_T: [], H_ST: [] };
    for (const generation of generations) {
        const steps = generation - generations[0];
        const high = highWeight * Math.pow(highRate, steps);
        const low = lowWeight * Math.pow(lowRate, steps);
        const within = fixedWithin + vectors[0][0] * high + vectors[0][1] * low;
        const between = fixedBetween + vectors[1][0] * high + vectors[1][1] * low;
        const expectedHS = 1 - within;
        const expectedHT = 1 - (within + (demes - 1) * between) / demes;
        const expectedHST = expectedHT - expectedHS;
        const values = {
            D: within > 0 ? 1 - between / within : 0,
            G_ST: expectedHT > 0 ? expectedHST / expectedHT : 0,
            H_S: expectedHS,
            H_T: expectedHT,
            H_ST: expectedHST,
        };
        if (!Object.values(values).every(Number.isFinite)) {
            return {};
        }
        for (const [name, value] of Object.entries(values)) {
            series[name].push(value);
        }
    }
    return series;
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
 * @param {Object<string, number[]>|null|undefined} closedFormSeries the
 *     expected trajectory of each drawn identity-based statistic
 *     (`closedFormTrajectories`, below), one array per statistic, each
 *     the same length as `generations` — drawn in that statistic's own
 *     color as a dash-dot line, so it reads as "this statistic, as
 *     theory expects it" beside its solid simulated curve. `renderTrajectory`
 *     already dropped any statistic the legend toggle hides, so a hidden
 *     measure takes its closed-form curve with it. `null`/`undefined`
 *     or empty draws nothing extra.
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
    closedFormSeries,
    scrubGeneration
) {
    const context = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    context.clearRect(0, 0, width, height);
    if (generations.length === 0) {
        return;
    }

    const plotLeft = TRAJECTORY_PLOT_MARGIN.left;
    const plotRight = width - TRAJECTORY_PLOT_MARGIN.right;
    const plotTop = TRAJECTORY_PLOT_MARGIN.top;
    const plotBottom = height - TRAJECTORY_PLOT_MARGIN.bottom;

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
    if (closedFormSeries) {
        for (const values of Object.values(closedFormSeries)) {
            allValues.push(...values);
        }
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
    const { min: minValue, max: maxValue } = window.fim.numericExtent(allValues, 0, 1);

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
    context.lineWidth = AXIS_LINE_WIDTH;
    context.beginPath();
    context.moveTo(plotLeft, plotTop);
    context.lineTo(plotLeft, plotBottom);
    context.lineTo(plotRight, plotBottom);
    context.stroke();

    context.fillStyle = mutedColor;
    context.font = FONT_AXIS_SMALL;
    context.textAlign = "right";
    context.textBaseline = "middle";
    const valueLabelRight = plotLeft - TRAJECTORY_Y_LABEL_GAP;
    context.fillText(maxValue.toFixed(TRAJECTORY_CORNER_DECIMALS), valueLabelRight, plotTop);
    context.fillText(minValue.toFixed(TRAJECTORY_CORNER_DECIMALS), valueLabelRight, plotBottom);
    context.textBaseline = "top";
    context.fillText(`gen ${maxGeneration}`, plotRight, plotBottom + TRAJECTORY_X_LABEL_DROP);
    context.textAlign = "left";
    context.fillText(`gen ${minGeneration}`, plotLeft, plotBottom + TRAJECTORY_X_LABEL_DROP);

    context.strokeStyle = borderColor;
    context.fillStyle = mutedColor;
    drawTrajectoryAxisTicks(
        context,
        plotLeft,
        plotBottom,
        minGeneration,
        maxGeneration,
        minValue,
        maxValue,
        xToPixel,
        yToPixel
    );

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
            context.globalAlpha = BAND_ALPHA;
            const top = yToPixel(Number(interval.upper));
            const bottom = yToPixel(Number(interval.lower));
            context.fillRect(bandLeft, top, plotRight - bandLeft, bottom - top);
            context.globalAlpha = 1;
        }
    }

    for (const [name, values] of Object.entries(histories)) {
        const emphasis = trajectoryEmphasis(name);
        context.strokeStyle = STATISTIC_TRAJECTORY_COLORS[name] || mutedColor;
        context.lineWidth = emphasis.width;
        context.globalAlpha = emphasis.alpha;
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
        context.globalAlpha = 1;
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
        context.lineWidth = OVERLAY_LINE_WIDTH;
        context.setLineDash(DASH_REFERENCE_LINE);
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

    // The closed-form expected trajectories: each drawn statistic's own
    // color, dash-dot, so one measure's simulated curve, its flat
    // equilibrium line and its expected trajectory read as the same
    // statistic three ways. Drawn over the solid curves (thin, like the
    // equilibrium line) so the run's scatter around theory stays visible.
    if (closedFormSeries) {
        context.lineWidth = OVERLAY_LINE_WIDTH;
        context.setLineDash(DASH_CLOSED_FORM);
        for (const [name, values] of Object.entries(closedFormSeries)) {
            context.strokeStyle = STATISTIC_TRAJECTORY_COLORS[name] || mutedColor;
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
        context.lineWidth = OVERLAY_LINE_WIDTH;
        context.setLineDash(DASH_DOTTED);
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
    // already established (`DASH_REFERENCE_LINE`, muted color, one
    // pixel wide, full plot height), reused verbatim rather than
    // inventing a third dashed-vertical-line convention on this page.
    // The curve itself is never redrawn/truncated for this — the design
    // note's own explicit choice is a moving marker over the whole,
    // unchanged curve, not a progressive reveal that hides its ending.
    if (scrubGeneration !== null && scrubGeneration !== undefined) {
        context.setLineDash(DASH_REFERENCE_LINE);
        context.strokeStyle = mutedColor;
        context.lineWidth = AXIS_LINE_WIDTH;
        const x = xToPixel(scrubGeneration);
        context.beginPath();
        context.moveTo(x, plotTop);
        context.lineTo(x, plotBottom);
        context.stroke();
        context.setLineDash([]);
    }
}

// Whether this run produced a trajectory at all -- one of the two
// conditions `updateRunPlotRowLayoutClass` (below) ORs together. Set by
// `setTrajectoryFrameHidden`, read nowhere else: kept as its own flag
// (rather than re-deriving it from `#run-trajectory-frame`'s own
// `hidden`) so the class can be recomputed from either trigger without
// each one needing to know how the other one's condition is stored.
let trajectoryAvailableForLayout = false;

/**
 * Recompute `#run-plot-row`'s own `run-plot-row-has-trajectory` class --
 * kept under its original name for the smaller diff, even though what it
 * actually means is "the graphs must never be allowed to push the
 * statistics table onto its own line below them" -- from every condition
 * that can make that true: a trajectory panel competing for the row
 * (`setTrajectoryFrameHidden`, below), or more than one graph pane shown
 * at once (`run-graph-stage.js`'s own `syncRunGraphStage`, the multi-
 * graph "Graphs (N)" picker). Either alone can make `.run-visual-
 * column`'s own natural width exceed what is left once the statistics
 * table -- which doubles as the trajectory legend and the per-statistic
 * show/hide control -- takes its own share.
 *
 * Reported live, more than once: this class's own protective CSS rules
 * (`app.css`) used to apply only while a trajectory panel was actually
 * competing for the row, so a botanist who picked several non-trajectory
 * graphs together (the reported case: three supplemental panels, no
 * trajectory in sight) hit the exact same underlying overflow with
 * nothing there to prevent it -- the statistics panel dropped out from
 * beside the graphs to its own line below them ("unglued... unmoored").
 * Deliberately still not unconditional: `test_run_view_initial_state_
 * canvas_is_unaffected_by_the_trajectory_fix` (`test/gui/test_results_
 * screen.py`) already confirmed live that applying these rules with
 * nothing actually competing for the row measurably shrinks the
 * `initial` p_0 preview's own canvas for no reason -- only exactly the
 * two conditions that can cause the real overflow apply the class.
 *
 * Exposed on `window.fim` so `run-graph-stage.js`'s own `syncRunGraphStage`
 * -- which changes the *other* condition, the visible pane count -- can
 * call it too, before its own `applyGraphLayout()` measures the row's
 * width; guarded there with a `typeof` check for load order, the same
 * precedent `showScreen`'s own `updateRailHighlight` call already sets
 * (`app.js`).
 */
function updateRunPlotRowLayoutClass() {
    const paneCount =
        typeof window.fim.getVisibleGraphs === "function"
            ? window.fim.getVisibleGraphs().length
            : 1;
    runPlotRow.classList.toggle(
        "run-plot-row-has-trajectory",
        trajectoryAvailableForLayout || paneCount > 1
    );
}

window.fim.updateRunPlotRowLayoutClass = updateRunPlotRowLayoutClass;

/**
 * Declare whether the trajectory graph has anything to show.
 *
 * The graph stage owns `#run-trajectory-frame`'s own `hidden` (one
 * graph is on screen at a time), so this only reports availability and
 * keeps `#run-plot-row`'s own layout class in sync
 * (`updateRunPlotRowLayoutClass`, above) as one of the two "this run
 * needs the fixed, never-wrap layout" signals. Every caller
 * (`renderTrajectory`, `renderBatchTrajectory`) goes through this rather
 * than touching either directly.
 *
 * @param {boolean} hidden
 */
function setTrajectoryFrameHidden(hidden) {
    trajectoryAvailableForLayout = !hidden;
    updateRunPlotRowLayoutClass();
    window.fim.registerGraphDraw("trajectory", repaintTrajectory);
    window.fim.setGraphAvailable("trajectory", !hidden);
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
 * @param {object|null|undefined} closedForm `Api.start_run`'s own
 *     `closedForm` field (`_closed_form_trajectory_payload`: the solved
 *     identity recursion's fixed point, eigenvalues, eigenvectors and
 *     inverse) or `Api.open_run`'s identical field; `null`/`undefined`
 *     for a batch or a configuration with no two-variable reduction
 *     (per-deme `N`, a migration matrix, per-locus `mu`).
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
    closedForm,
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
        closedForm,
        scrubGeneration,
    ];
    const hasCurve = generations && histories && generations.length > 0;
    if (!hasCurve && !sigmaBand) {
        activeTrajectoryRenderMode = null;
        setTrajectoryFrameHidden(true);
        runTrajectoryLegend.replaceChildren();
        runTrajectorySigmaBandCaption.hidden = true;
        runTrajectorySigmaBandCaption.replaceChildren();
        return;
    }
    activeTrajectoryRenderMode = "scalar";
    const effectiveGenerations = hasCurve
        ? generations
        : [Math.max(0, generationCount - sigmaBand.window), generationCount];
    const effectiveHistories = hasCurve ? histories : {};
    setTrajectoryFrameHidden(false);
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
        Object.entries(plottable).filter(([name]) => !isTrajectoryHidden(name))
    );
    const visiblePlottableEquilibrium = Object.fromEntries(
        Object.entries(plottableEquilibrium).filter(([name]) => !isTrajectoryHidden(name))
    );
    // The closed-form expected trajectories follow the same per-statistic
    // visibility as the simulated curve they accompany: a statistic that
    // is not plotted, or that the toggle hides, gets none. They need the
    // run's own first `H_S`/`H_T` as the starting state, so a panel with
    // no curve (a reopened run showing only its sigma band) has none.
    const closedFormSeries = hasCurve
        ? Object.fromEntries(
              Object.entries(
                  closedFormTrajectories(closedForm, effectiveGenerations, plottable)
              ).filter(
                  ([name]) =>
                      name in visiblePlottable && !isTrajectoryHidden(name)
              )
          )
        : {};
    drawTrajectoryCurve(
        canvas,
        effectiveGenerations,
        visiblePlottable,
        sigmaBand,
        visiblePlottableEquilibrium,
        identityRecovery,
        closedFormSeries,
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
    refreshTrajectoryStatisticRowStates();
    runTrajectoryLegend.replaceChildren();
    if (Object.keys(closedFormSeries).length > 0) {
        const item = document.createElement("span");
        item.className = "legend-item";
        const swatch = document.createElement("span");
        swatch.className = "swatch swatch-dashdot";
        item.appendChild(swatch);
        item.appendChild(
            document.createTextNode("expected trajectory (closed form)")
        );
        runTrajectoryLegend.appendChild(item);
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
        item.className = "legend-item";
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
 * Compute the y-axis domain `drawBatchTrajectoryCurve` plots against --
 * factored out into its own pure function specifically so a test can
 * assert on it directly (a hand-built payload in, a `{minValue,
 * maxValue}` out), rather than only indirectly through rendered canvas
 * pixels.
 *
 * A point's own `mean` always contributes to the domain, regardless of
 * `sampleCount`; its own `low`/`high` contribute only once `sampleCount`
 * reaches `MIN_SAMPLE_COUNT_FRACTION_FOR_DOMAIN` of the largest
 * `sampleCount` seen anywhere in `visiblePooled` (see that constant's
 * own comment for why a *relative*, not absolute, threshold) -- an
 * unstable, thin-sample band still *draws* at its own true, possibly
 * enormous width (canvas silently clips whatever falls outside
 * `[plotTop, plotBottom]`, the same as it would for any other value
 * outside the visible area), it simply never gets to decide how far
 * the axis itself stretches for every other, better-supported point.
 * @param {Record<string, Array<{mean: string, low: string, high: string,
 *     sampleCount: number}>>} visiblePooled
 * @returns {{minValue: number, maxValue: number}}
 */
function computeBatchTrajectoryValueDomain(visiblePooled) {
    const allPoints = Object.values(visiblePooled).flat();
    const maxSampleCount = window.fim.numericExtent(
        allPoints.map((point) => point.sampleCount),
        undefined,
        0
    ).max;
    const minSampleCountForDomain = maxSampleCount * MIN_SAMPLE_COUNT_FRACTION_FOR_DOMAIN;
    const allValues = allPoints.flatMap((point) => {
        const values = [Number(point.mean)];
        if (point.sampleCount >= minSampleCountForDomain) {
            values.push(Number(point.low), Number(point.high));
        }
        return values;
    });
    // The domain always includes [0, 1], matching `drawTrajectoryCurve`'s
    // own identical reasoning: every named statistic's own natural range
    // starts there, so this reads against the same fixed floor/ceiling a
    // reader of any other statistic on this page already expects.
    const { min: minValue, max: maxValue } = window.fim.numericExtent(allValues, 0, 1);
    return { minValue, maxValue };
}

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
 * @param {number|null} [scrubGeneration] the generation currently being
 *     inspected, drawn as the same dashed vertical marker
 *     `drawTrajectoryCurve` uses; `null`/omitted while the view tracks
 *     the newest tick, so no marker is drawn at all.
 */
function drawBatchTrajectoryCurve(canvas, visiblePooled, scrubGeneration) {
    const context = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    context.clearRect(0, 0, width, height);
    const names = Object.keys(visiblePooled);
    if (names.length === 0) {
        return;
    }

    const plotLeft = TRAJECTORY_PLOT_MARGIN.left;
    const plotRight = width - TRAJECTORY_PLOT_MARGIN.right;
    const plotTop = TRAJECTORY_PLOT_MARGIN.top;
    const plotBottom = height - TRAJECTORY_PLOT_MARGIN.bottom;

    const allGenerations = names.flatMap((name) =>
        visiblePooled[name].map((point) => point.generation)
    );
    const { min: minGeneration, max: maxGeneration } =
        window.fim.numericExtent(allGenerations);
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
    context.lineWidth = AXIS_LINE_WIDTH;
    context.beginPath();
    context.moveTo(plotLeft, plotTop);
    context.lineTo(plotLeft, plotBottom);
    context.lineTo(plotRight, plotBottom);
    context.stroke();

    context.fillStyle = mutedColor;
    context.font = FONT_AXIS_SMALL;
    context.textAlign = "right";
    context.textBaseline = "middle";
    const valueLabelRight = plotLeft - TRAJECTORY_Y_LABEL_GAP;
    context.fillText(maxValue.toFixed(TRAJECTORY_CORNER_DECIMALS), valueLabelRight, plotTop);
    context.fillText(minValue.toFixed(TRAJECTORY_CORNER_DECIMALS), valueLabelRight, plotBottom);
    context.textBaseline = "top";
    context.fillText(`gen ${maxGeneration}`, plotRight, plotBottom + TRAJECTORY_X_LABEL_DROP);
    context.textAlign = "left";
    context.fillText(`gen ${minGeneration}`, plotLeft, plotBottom + TRAJECTORY_X_LABEL_DROP);

    context.strokeStyle = borderColor;
    context.fillStyle = mutedColor;
    drawTrajectoryAxisTicks(
        context,
        plotLeft,
        plotBottom,
        minGeneration,
        maxGeneration,
        minValue,
        maxValue,
        xToPixel,
        yToPixel
    );

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
        context.globalAlpha = BAND_ALPHA;
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

        const emphasis = trajectoryEmphasis(name);
        context.strokeStyle = color;
        context.lineWidth = emphasis.width;
        context.globalAlpha = emphasis.alpha;
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
        context.globalAlpha = 1;
    }

    // The same dashed "you are looking at this generation" marker the
    // scalar panel draws (`drawTrajectoryCurve`), so a live batch's own
    // scrubber reads identically to a scalar run's.
    if (scrubGeneration !== null && scrubGeneration !== undefined) {
        context.setLineDash(DASH_REFERENCE_LINE);
        context.strokeStyle = mutedColor;
        context.lineWidth = AXIS_LINE_WIDTH;
        const x = xToPixel(scrubGeneration);
        context.beginPath();
        context.moveTo(x, plotTop);
        context.lineTo(x, plotBottom);
        context.stroke();
        context.setLineDash([]);
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
 * @param {number|null} [scrubGeneration] forwarded to `drawBatchTrajectoryCurve`
 *     -- the generation a live batch's own scrubber is currently parked
 *     on, or `null`/omitted while it tracks the newest tick.
 */
function renderBatchTrajectory(pooledConvergenceHistories, scrubGeneration) {
    lastPooledConvergenceHistories = pooledConvergenceHistories;
    lastBatchScrubGeneration = scrubGeneration ?? null;
    const names = pooledConvergenceHistories ? Object.keys(pooledConvergenceHistories) : [];
    if (names.length === 0) {
        activeTrajectoryRenderMode = null;
        setTrajectoryFrameHidden(true);
        runTrajectoryLegend.replaceChildren();
        return;
    }
    activeTrajectoryRenderMode = "batch";
    setTrajectoryFrameHidden(false);
    const canvas = runTrajectoryCanvas;
    canvas.width = canvas.clientWidth || canvas.width;
    canvas.height = canvas.clientHeight || canvas.height;
    const visiblePooled = Object.fromEntries(
        names
            .filter((name) => !isTrajectoryHidden(name))
            .map((name) => [name, pooledConvergenceHistories[name]])
    );
    drawBatchTrajectoryCurve(canvas, visiblePooled, scrubGeneration);
    refreshTrajectoryStatisticRowStates();
    runTrajectoryLegend.replaceChildren();
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
        const shown = Number(value).toPrecision(REPORT_VALUE_SIGNIFICANT_DIGITS);
        line.textContent = `q=${order}: ${shown}`;
        resultsDifferentiationQLines.appendChild(line);
        points.push({ order: Number(order), value: Number(value) });
    }
    points.sort((a, b) => a.order - b.order);
    const canvas = resultsDifferentiationQCanvas;
    canvas.width = canvas.clientWidth || canvas.width;
    canvas.height = canvas.clientHeight || canvas.height;
    drawDifferentiationQCurve(canvas, points);
}

/**
 * Render the batch summary's six named-statistic rows, plus the same
 * two effective-allele rows (botanist GUI design doc §7.7) the scalar
 * completed view's own `renderEffectiveAlleles` shows beside `H_S`/
 * `H_T` — `effectiveAlleles`'s own two entries are already the
 * equivalent cross-replicate confidence interval
 * (`Api._effective_allele_interval_summary`), not a second, separately
 * computed point value, so this reuses `buildCiMeter`/`buildOmittedMeter`
 * exactly like the six rows above them.
 *
 * @param {Record<string, object>|undefined} summary
 * @param {{H_S: object, H_T: object, gStCaution: boolean}|undefined} effectiveAlleles
 */
function renderBatchSummary(summary, effectiveAlleles) {
    // `summary`/`effectiveAlleles` default to `{}` rather than requiring
    // every caller to guard them: every real "done" push carries both
    // (`_batch_done_payload`'s own fields), but `_push_batch_progress`'s
    // own live-tick payload carries only `statistics` (this same
    // transform is never computed mid-batch, mirroring the scalar run's
    // own "only a finished run gets this readout" scope) and a
    // synthetic/partial test payload calling `onBatchProgress` directly
    // for unrelated coverage (`test_input_screen.py`'s own high-water-
    // mark test is exactly this shape) supplies neither — every row
    // renders as "omitted" rather than throwing trying to read
    // `undefined[name]`.
    const rows = summary || {};
    const effectiveRows = effectiveAlleles || {};
    batchResultsSummary.replaceChildren();
    for (const name of STATISTIC_NAMES) {
        const interval = rows[name];
        const cells =
            interval === undefined
                ? buildOmittedMeter(name, OMITTED_SUMMARY_TEXT)
                : buildCiMeter(name, interval);
        const row = document.createElement("tr");
        applyStatRow(row, cells);
        decorateTrajectoryStatisticRow(row, name);
        row.dataset.shownKey = name;
        row.hidden = !isStatisticShown(name);
        batchResultsSummary.appendChild(row);
    }
    for (const [label, key, description] of EFFECTIVE_ALLELE_LABELS) {
        const interval = effectiveRows[key];
        const cells =
            interval === undefined
                ? buildOmittedMeter(label, OMITTED_SUMMARY_TEXT, description)
                : buildCiMeter(label, interval, description);
        const row = document.createElement("tr");
        applyStatRow(row, cells);
        batchResultsSummary.appendChild(row);
    }
    renderRunMessages(
        effectiveRows.gStCaution ? [{ severity: "warning", text: G_ST_CAUTION_TEXT }] : []
    );
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

/**
 * Flag a results row whose run stopped at the generation cap instead of
 * converging: a warning background plus a "⚠" in the first cell, so the
 * cue survives color-blindness. Not an error, only a caution that the
 * statistics are not settled values.
 *
 * @param {HTMLTableRowElement} row
 * @param {string} reason - The engine's stop reason, e.g. "hit the cap".
 */
function markNotConverged(row, reason) {
    row.classList.add("row-warning");
    const label = `Not converged (${reason})`;
    row.title = label;
    const mark = document.createElement("span");
    mark.className = "row-warning-mark";
    mark.setAttribute("role", "img");
    mark.setAttribute("aria-label", label);
    mark.textContent = "⚠";
    const first = row.firstElementChild;
    first.insertBefore(mark, first.firstChild);
}

function renderBatchTable(replicates, p0Statistics) {
    batchResultsTableBody.replaceChildren();
    // p_0 baseline row — the initial conditions the entire batch shared.
    // Column order: Generation | Replicate | ...stats | Open
    if (p0Statistics) {
        const baseRow = document.createElement("tr");
        // Italic: the inputs the batch started from, not an output.
        baseRow.classList.add("row-initial");
        const baseCells = [
            0,
            // Placeholder for Replicate column — shared by the whole batch.
            "",
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
        // Column order: Generation | Replicate | ...stats
        const cells = [
            replicate.generation,
            replicateLabel(replicate.replicateId),
            ...STATISTIC_NAMES.map((name) => replicate.statistics[name]),
        ];
        for (const value of cells) {
            const cell = document.createElement("td");
            cell.textContent = String(value);
            row.appendChild(cell);
        }
        if (!replicate.converged) {
            markNotConverged(row, replicate.reason);
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
    tagStatisticCells(batchResultsTableBody, 2);
}

/**
 * Render the scalar run's own per-generation results table -- the
 * single-replicate counterpart to `renderBatchTable` immediately
 * above, closing a real gap a user reported between the two completed
 * views: a batch got a full-width table of its own results, a single
 * run got only the point-value stats panel beside the plot.
 *
 * A batch's rows are its replicates. A single run has exactly one, so
 * listing replicates here would say nothing the stats panel does not
 * already say; its rows are generations instead. They are *the
 * scrubber's own* sampled generations (`frameGenerations`, at most
 * `GUI_ANIMATION_MAX_FRAMES`, `fim.gui.animation.select_sample_
 * generations`) rather than every recorded generation, for two
 * reasons: a row and a scrub position then always denote the same
 * instant, and a 10,000-generation run does not build 10,000 table
 * rows.
 *
 * Statistic values come from the retained per-generation histories the
 * scrubber already reads (`completedTrajectoryHistories`), looked up
 * through the same `nearestGeneration` match `updateScrubbedTrajectory`
 * uses, so a table cell and the stats panel at that same scrub
 * position always agree. The final row is the exception, and
 * deliberately so: it shows `completedFinalStatistics` -- the run's
 * own authoritative, server-formatted final values -- exactly as
 * `updateScrubbedTrajectory`'s own `isFinalFrame` branch does.
 *
 * Renders nothing and stays hidden when there is no per-generation
 * history at all (a reopened run, `Api.open_run`, whose payload
 * carries no `convergenceHistories` -- the same named scope boundary
 * `updateScrubbedTrajectory` already observes).
 *
 * @param {number[]} frameGenerations - The scrubber's own frame
 *     generations, ascending.
 */
function renderScalarTable(frameGenerations, frameStatistics = []) {
    runResultsTableBody.replaceChildren();
    if (
        !completedTrajectoryGenerations ||
        completedTrajectoryGenerations.length === 0 ||
        !frameGenerations ||
        frameGenerations.length === 0
    ) {
        runResultsTableEl.hidden = true;
        return;
    }
    const lastIndex = frameGenerations.length - 1;
    for (const [index, generation] of frameGenerations.entries()) {
        const isFinal = index === lastIndex;
        const scrubGeneration = nearestGeneration(
            completedTrajectoryGenerations,
            generation
        );
        const scrubIndex = completedTrajectoryGenerations.indexOf(scrubGeneration);
        const values = STATISTIC_NAMES.map((name) => {
            if (isFinal && completedFinalStatistics) {
                return completedFinalStatistics[name];
            }
            // A statistic with no per-generation history takes the
            // frame's own value (`Api.get_animation_frames`).
            if (frameStatistics[index] && name in frameStatistics[index]) {
                return frameStatistics[index][name];
            }
            const history = completedTrajectoryHistories
                ? completedTrajectoryHistories[name]
                : undefined;
            const value = history && scrubIndex >= 0 ? history[scrubIndex] : undefined;
            // Same raw-float-to-display rounding `updateScrubbedTrajectory`
            // applies to these same history values, for the same reason:
            // `ConvergenceMonitor.histories` holds unformatted floats.
            return Number.isFinite(value)
                ? Number(value).toPrecision(REPORT_VALUE_SIGNIFICANT_DIGITS)
                : "—";
        });
        const row = document.createElement("tr");
        for (const value of [generation, ...values]) {
            const cell = document.createElement("td");
            cell.textContent = value === undefined || value === null ? "—" : String(value);
            row.appendChild(cell);
        }
        if (generation === 0) {
            // Italic: the inputs the run started from, not an output.
            row.classList.add("row-initial");
        } else if (isFinal && completedReportReason === "hit the cap") {
            markNotConverged(row, completedReportReason);
        }
        runResultsTableBody.appendChild(row);
    }
    tagStatisticCells(runResultsTableBody, 1);
    runResultsTableEl.hidden = false;
}

function drawCompletedOverview(panels) {
    if (!panels || panels.length === 0) {
        return;
    }
    if (window.fim.setGraphAvailable) {
        window.fim.setGraphAvailable("scatter", true);
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
 * Answer one completed-state scrubber tick: update the stats
 * table (every named-statistic row, plus the two effective-allele rows
 * derived from H_S/H_T at that same generation) and the trajectory
 * panel's own scrub-position marker to match
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
            applyStatRow(
                element,
                buildPointMeter(
                    name,
                    completedFinalStatistics[name],
                    windowStatisticsDescription(name)
                )
            );
            decorateTrajectoryStatisticRow(element, name);
        }
        // The two derived rows restore from the retained final payload,
        // not from a history lookup -- they were computed from the
        // run's own final report server-side.
        renderEffectiveAlleles(completedEffectiveAlleles);
        renderTrajectory(
            completedTrajectoryGenerations,
            completedTrajectoryHistories,
            completedSigmaBand,
            completedGenerationCount,
            completedEquilibrium,
            completedIdentityRecovery,
            completedClosedForm,
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
                buildPointMeter(name, Number(value).toPrecision(REPORT_VALUE_SIGNIFICANT_DIGITS))
            );
        } else {
            applyStatRow(element, buildOmittedMeter(name, OMITTED_SCRUB_TEXT));
        }
        decorateTrajectoryStatisticRow(element, name);
    }
    // The two derived effective-allele rows track the scrub too: each
    // is a closed-form transform (`1 / (1 - H)`) of this same run's own
    // H_S/H_T history value at this generation, exact for a scalar run
    // (no aggregation involved, unlike the batch panel's own pooled
    // rows, which omit instead). `H === 1` has no finite answer --
    // omitted rather than rendered as "Infinity". The G_ST caution
    // note, computed from the final state, hides until the final
    // frame's own restore brings it back.
    const [
        [withinLabel, withinHistoryKey, withinDescription],
        [totalLabel, totalHistoryKey, totalDescription],
    ] = EFFECTIVE_ALLELE_LABELS;
    for (const [rowId, label, historyKey, description] of [
        ["stat-Ne_S", withinLabel, withinHistoryKey, withinDescription],
        ["stat-Ne_T", totalLabel, totalHistoryKey, totalDescription],
    ]) {
        const history = completedTrajectoryHistories[historyKey];
        const value = history && scrubIndex >= 0 ? history[scrubIndex] : undefined;
        const cells =
            Number.isFinite(value) && value < 1
                ? buildPointMeter(
                      label,
                      (1 / (1 - value)).toPrecision(REPORT_VALUE_SIGNIFICANT_DIGITS),
                      description
                  )
                : buildOmittedMeter(label, OMITTED_SCRUB_TEXT, description);
        applyStatRow(document.getElementById(rowId), cells);
    }
    renderRunMessages();
    renderTrajectory(
        completedTrajectoryGenerations,
        completedTrajectoryHistories,
        completedSigmaBand,
        completedGenerationCount,
        completedEquilibrium,
        completedIdentityRecovery,
        completedClosedForm,
        scrubGeneration
    );
}

/**
 * Answer one completed-batch scrubber tick: re-render the pooled
 * statistics panel to match `frameGeneration` -- the batch counterpart
 * to `updateScrubbedTrajectory`, which updates the *scalar* panel.
 * Reported directly: scrubbing a reopened batch back to generation 1
 * moved the trajectory's own scrub marker while the statistics panel
 * kept showing the final values, the card again presenting two
 * different times at once.
 *
 * Each statistic's own per-generation pooled points
 * (`lastPooledConvergenceHistories` -- real monitor output for a batch
 * that just finished live, rebuilt from disk for a reopened one,
 * `fim.reanalyze.replicate_convergence_history`) are already exactly
 * `buildCiMeter`'s own input shape (`{mean, low, high, sampleCount}`,
 * `_pooled_histories_payload`), so the scrubbed row is the same meter
 * the final row is, rather than a second, driftable rendering. A
 * statistic with no pooled point at all (never tracked this run) --
 * and the two effective-allele rows, which have no per-generation
 * source at any point -- render as omitted, never a stale or padded
 * value; the G_ST caution note, computed from the run's own final
 * state, hides until the final frame restores it.
 *
 * At the final frame the real, authoritative final summary
 * (`completedBatchSummary`, retained by `enterCompletedState`) comes
 * back verbatim -- the per-generation pooled points are a close
 * approximation of it but not its equal (they were computed by a
 * different aggregation path over the same replicates), and exactness
 * is what that one frame is for, the identical division of labor
 * `updateScrubbedTrajectory`'s own `isFinalFrame` branch already
 * established for the scalar panel.
 *
 * A no-op when this completed batch has no pooled histories at all
 * (fewer than two replicates to pool, `pooled_convergence_histories`'s
 * own minimum) -- the panel then keeps its final summary, the only
 * values there are.
 *
 * @param {number} frameGeneration - `frame.generation` for the
 *     currently-scrubbed animation frame.
 * @param {boolean} isFinalFrame - whether this is the scrubber's last
 *     frame (see `updateScrubbedTrajectory`'s own docstring for why the
 *     index comparison, not a generation-number comparison).
 */
function updateScrubbedBatchSummary(frameGeneration, isFinalFrame) {
    if (!lastPooledConvergenceHistories) {
        return;
    }
    if (isFinalFrame) {
        renderBatchSummary(completedBatchSummary, completedBatchEffectiveAlleles);
        return;
    }
    batchResultsSummary.replaceChildren();
    for (const name of STATISTIC_NAMES) {
        const points = lastPooledConvergenceHistories[name];
        // Each statistic's own point list carries its own generation
        // per point (a replicate whose own history dropped an interior
        // gap shifts every later point's own alignment), so the nearest
        // match is found per point, not by index into a shared list.
        let point;
        let bestDistance = Infinity;
        for (const candidate of points || []) {
            const distance = Math.abs(candidate.generation - frameGeneration);
            if (distance < bestDistance) {
                bestDistance = distance;
                point = candidate;
            }
        }
        const cells = point
            ? buildCiMeter(name, point)
            : buildOmittedMeter(name, OMITTED_SCRUB_TEXT);
        const row = document.createElement("tr");
        applyStatRow(row, cells);
        decorateTrajectoryStatisticRow(row, name);
        row.dataset.shownKey = name;
        row.hidden = !isStatisticShown(name);
        batchResultsSummary.appendChild(row);
    }
    for (const [label, , description] of EFFECTIVE_ALLELE_LABELS) {
        const row = document.createElement("tr");
        applyStatRow(row, buildOmittedMeter(label, OMITTED_SCRUB_TEXT, description));
        batchResultsSummary.appendChild(row);
    }
    renderRunMessages();
}

/**
 * Show or hide the scrubber controls, repainting the graph stage
 * whenever that actually changes.
 *
 * The scrubber shares `#run-graph-body`'s own grid column with the
 * graph panes, so its own appearance *widens* that column: measured on
 * a reopened run, the trajectory canvas is 302px wide while the
 * scrubber is hidden and 541px once it shows. Both scrubber wirings
 * below reach that change only after their own `get_animation_frames`/
 * `get_batch_animation_frames` bridge call resolves -- long after
 * `enterCompletedState` already drew every graph at the narrower
 * width. A canvas keeps whatever buffer it was last drawn at, so
 * without an explicit repaint here the graph stays a stretched, blurry
 * copy of its own pre-scrubber size.
 *
 * The stage's own per-pane `ResizeObserver` (`run-graph-stage.js`) is
 * meant to cover exactly this, and does under macOS/WebKit -- but not
 * under the WebKitGTK build CI runs, where `test_a_reopened_runs_
 * graphs_repaint_at_the_real_pane_size` caught the stretched buffer
 * twice (a 300px buffer against a 553px pane, then 325 against 553
 * once `9371d71` moved `showScreen` ahead of the draws). Repainting at
 * the one point that actually changes the width removes the dependence
 * on the observer firing at all; the observer stays as the backstop
 * for window resizes and the zoom frame.
 *
 * @param {boolean} hidden
 */
function setScrubberControlsHidden(hidden) {
    if (scrubberControls.hidden === hidden) {
        return;
    }
    scrubberControls.hidden = hidden;
    if (window.fim.redrawActiveGraph) {
        window.fim.redrawActiveGraph();
    }
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
        setScrubberControlsHidden(true);
        runResultsTableEl.hidden = true;
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
            setScrubberControlsHidden(true);
            runResultsTableEl.hidden = true;
            window.fim.resetScrubber();
            return;
        }
        setScrubberControlsHidden(false);
        // The results table's own rows are exactly these frames' own
        // generations (`renderScalarTable`'s own docstring), so it is
        // built here, where that sampled list first exists client-side,
        // rather than in `enterCompletedState`, which never sees it.
        renderScalarTable(
            result.frames.map((frame) => frame.generation),
            result.frames.map((frame) => frame.statistics || null)
        );
        window.fim.setScrubberFrames(
            result.frames,
            (frame, index) => {
                const isFinal = index === result.frames.length - 1;
                drawCompletedOverview(frame.panels);
                updateScrubbedTrajectory(frame.generation, isFinal);
                // Statistics with no per-generation history come with the
                // frame itself (the final frame's are already exact).
                if (!isFinal) {
                    renderHistoryFreeStatistics(frame.statistics);
                }
                lastScrubbedPairFrame = { frame, index, isFinal };
                renderPairStatistics(
                    pairStatisticsForFrame(frame, index, isFinal),
                    "loading the values for this pair…"
                );
                if (isFinal && completedFinalLiteratureVisuals) {
                    renderSupplementalPanels(completedFinalLiteratureVisuals);
                } else if (frame.literatureVisuals) {
                    renderSupplementalPanels(frame.literatureVisuals);
                }
            },
            // `enterCompletedState` has already drawn the run's *final*
            // panels, so the scrubber starts at the end, not the
            // beginning. Reported directly: a run re-opened from the
            // Home list showed its converged plot above a scrubber
            // reading "Generation 0 (frame 1 / 67)".
            result.frames.length - 1
        );
    } finally {
        window.__fimScrubberPending -= 1;
    }
}

/**
 * The batch counterpart to `wireCompletedScrubber` -- a completed
 * batch's own scrubber replays its *pooled* scatter across replicates,
 * generation by generation (batch trajectory panel design `20260912-
 * claude-sonnet-5-batch-trajectory-panel-design.md`, `selby/
 * restricted`, scoped to the completed view only -- there is no live-
 * run scrubber for either run type today, a separate, larger, deferred
 * design). Shares `scrubberControls`/`window.fim.setScrubberFrames`
 * unchanged with the scalar case; only the bridge call
 * (`get_batch_animation_frames`, not `get_animation_frames`) and the
 * per-tick redraw differ -- `updateScrubbedBatchSummary` answers the
 * statistics panel (`updateScrubbedTrajectory`'s own batch
 * counterpart), and the pooled trajectory's own scrub marker moves
 * through `renderBatchTrajectory`'s own `scrubGeneration` argument.
 *
 * @param {string} outputDirectory
 */
async function wireCompletedBatchScrubber(outputDirectory) {
    window.__fimScrubberPending = (window.__fimScrubberPending || 0) + 1;
    try {
        const result = await window.pywebview.api.get_batch_animation_frames(
            outputDirectory
        );
        // Same staleness guard as `wireCompletedScrubber`'s own --
        // this call is un-awaited by its own caller, so the state may
        // already have moved on by the time it resolves.
        if (
            window.fim.getRunViewState() !== "completed" ||
            window.fim.getCompletedOutputDirectory() !== outputDirectory
        ) {
            return;
        }
        if (!result.ok || result.frames.length === 0) {
            setScrubberControlsHidden(true);
            window.fim.resetScrubber();
            return;
        }
        setScrubberControlsHidden(false);
        // As in `wireCompletedScrubber`: the pooled final panels are
        // already on the canvas, so the scrubber starts at the end.
        window.fim.setScrubberFrames(
            result.frames,
            (frame, index) => {
                drawCompletedOverview(frame.panels);
                // Every panel on the card follows the scrubber, not
                // the scatter alone. Reported directly: scrubbing a
                // completed batch back through its history moved the
                // scatter while the allele composition and spectrum
                // stayed at the final generation, so the card showed
                // two different times at once with nothing to say so.
                if (frame.literatureVisuals) {
                    renderSupplementalPanels(frame.literatureVisuals);
                }
                const isFinal = index === result.frames.length - 1;
                if (lastPooledConvergenceHistories) {
                    renderBatchTrajectory(
                        lastPooledConvergenceHistories,
                        isFinal ? null : frame.generation
                    );
                }
                updateScrubbedBatchSummary(frame.generation, isFinal);
            },
            result.frames.length - 1
        );
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
    if (window.fim.resetGraphStage) {
        window.fim.resetGraphStage();
    }
    // Shown before anything below draws, not after: every graph render
    // in this function sizes its own canvas buffer from `clientWidth`,
    // and a reopened run reaches here with the Home card still showing
    // and this one hidden -- drawn then, each canvas keeps the default
    // 300x150 buffer and CSS stretches it to the real pane size (a
    // reported "blurry right after opening" defect). The same
    // show-first order the live-run path already follows
    // (`run-view-controls.js`'s own `run-button` handler) -- and the
    // same behavior `aadb8b23` established for the scatter's own first
    // frame. The graph stage's own per-pane `ResizeObserver` wiring
    // remains the backstop for any future draw path that still lands
    // here hidden.
    window.fim.showScreen("screen-run");
    // The always-visible parameter strip otherwise still reflects
    // whatever the Configure form's own current values happen to be,
    // not this run's own -- see `updateParameterStripFromSummary`'s own
    // doc comment (`nav-rail.js`) for the reported defect this fixes.
    if (payload.configSummary && window.fim.updateParameterStripFromSummary) {
        window.fim.updateParameterStripFromSummary(payload.configSummary);
    }
    window.fim.setCompletedOutputDirectory(payload.outputDirectory);
    // `undefined` (a batch's own payload carries no such key at all) is
    // normalized to `null` here rather than left as `undefined` -- the
    // getter's own contract (`app.js`'s own doc comment) promises
    // `string|null`, matching `completedOutputDirectory`'s identical
    // shape immediately above.
    window.fim.setCompletedTrajectoryPath(payload.trajectoryPath ?? null);
    // Whatever Configure-time note or "Saved to <path>" confirmation
    // `run-reason` last showed is retired the moment a run's own
    // completed messages take over that same page position -- otherwise
    // a stale Configure-time convergence-window note (for whichever
    // configuration happened to be in the form then, not necessarily
    // this run's own) could sit directly above this run's own, possibly
    // different, `convergenceNote` line below, reading as two answers
    // to the same question.
    runReason.textContent = "";
    runProgress.hidden = true;
    if (initialStats) {
        initialStats.hidden = true;
    }
    if (runPlotTitle) {
        // `directoryName`, not `runId` (a deterministic content hash of
        // the configuration, unrelated to the directory name): the
        // directory is what "Open output folder" reveals, and is what a
        // botanist actually needs to find this run on disk.
        runPlotTitle.textContent = payload.directoryName
            ? `FIM simulation — ${payload.directoryName}`
            : "FIM simulation — completed";
    }
    runCompleted.hidden = false;
    cancelButton.disabled = true;
    openFolderButton.hidden = false;
    resultsBackButton.hidden = false;
    resultsHistoryBackButton.hidden = false;
    resultsHistoryForwardButton.hidden = false;
    if (typeof window.fim.syncHistoryControls === "function") {
        window.fim.syncHistoryControls();
    }
    resultsStats.hidden = isBatch;
    batchResultsTableEl.hidden = !isBatch;
    batchResultsTable.hidden = !isBatch;
    // Hidden until `wireCompletedScrubber` actually has frames to build
    // rows from (scalar branch only) -- never left showing the previous
    // run's own rows while this one's are still in flight.
    runResultsTableEl.hidden = true;
    runResultsTableBody.replaceChildren();
    resultsRunId.textContent = payload.directoryName ?? payload.runId;
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
        // A reopened Study's own `parameterMismatches` (`Api.open_
        // study`, `20260919-claude-sonnet-5-unified-batch-and-study-
        // results-reopen-design.md`, `selby/restricted`, §2) is the
        // one thing a batch's own "done"/reopen payload never carries
        // -- shown here, the one line this view always clears for an
        // ordinary batch anyway, rather than a second, dedicated note
        // area built just for this one field.
        resultsOutcome.hidden = false;
        resultsOutcome.textContent = payload.parameterMismatches
            ? `Pooled anyway — varies across members: ${Object.keys(
                  payload.parameterMismatches
              ).join(", ")}`
            : "";
        setBaseRunMessages(
            payload.convergenceNote
                ? [{ severity: "info", text: payload.convergenceNote }]
                : []
        );
        renderBatchSummary(payload.summary, payload.effectiveAlleles);
        renderBatchTable(payload.replicates, payload.p0Statistics);
        // A batch's own completed scrubber replays the pooled scatter
        // across replicates, generation by generation
        // (`wireCompletedBatchScrubber`'s own docstring) -- added
        // alongside the trajectory panel below, both parts of the same
        // batch trajectory panel design `20260912-claude-sonnet-5-
        // batch-trajectory-panel-design.md` (`selby/restricted`).
        wireCompletedBatchScrubber(payload.outputDirectory);
        // `renderBatchTrajectory` (not `renderTrajectory`, which
        // assumes one shared generation list every statistic's own
        // history aligns against -- an assumption `payload.
        // pooledConvergenceHistories`'s own per-point-scoped
        // generations do not hold) draws the real, authoritative
        // cross-replicate aggregate.
        completedTrajectoryGenerations = null;
        completedTrajectoryHistories = null;
        completedSigmaBand = null;
        completedEquilibrium = null;
        completedIdentityRecovery = null;
        completedClosedForm = null;
        completedWindowStatistics = null;
        completedGenerationCount = null;
        completedFinalStatistics = null;
        completedFinalLiteratureVisuals = null;
        completedReportReason = null;
        completedConvergedOn = null;
        completedPair = [1, 2];
        completedFinalPairStatistics = null;
        completedPairFrameStatistics = null;
        renderPairStatistics(null, "loading…");
        if (payload.demeCount >= DEMES_NEEDED_FOR_PAIR) {
            // Counted with the scrubber's own fetches, so anything waiting
            // for "settled" (`window.__fimScrubberPending`) waits for this
            // bridge call too instead of tearing the page down under it.
            window.__fimScrubberPending = (window.__fimScrubberPending || 0) + 1;
            window.pywebview.api
                .get_batch_pair_statistics(payload.outputDirectory, 1, 2)
                .then(renderPairSummary)
                .finally(() => {
                    window.__fimScrubberPending -= 1;
                });
        }
        completedEffectiveAlleles = null;
        completedBatchSummary = payload.summary || null;
        completedBatchEffectiveAlleles = payload.effectiveAlleles || null;
        renderBatchTrajectory(payload.pooledConvergenceHistories);
    } else {
        const report = payload.report;
        const reason = report.reason.charAt(0).toUpperCase() + report.reason.slice(1);
        // Kept updated for test compatibility (`results-outcome`'s own
        // textContent), but not shown directly -- the identical text is
        // this state's own first `run-messages` info line instead
        // (`setBaseRunMessages`, below), consolidated with the derived-
        // convergence-settings note rather than living in two places at
        // once.
        resultsOutcome.hidden = true;
        resultsOutcome.textContent = `${reason}: generation ${report.generation}`;
        setBaseRunMessages([
            { severity: "info", text: resultsOutcome.textContent },
            ...(payload.convergenceNote
                ? [{ severity: "info", text: payload.convergenceNote }]
                : []),
        ]);
        completedReportReason = reason;
        completedConvergedOn = report.converged_on ?? null;
        completedPair = [1, 2];
        completedFinalPairStatistics = payload.pairStatistics || null;
        completedPairFrameStatistics = null;
        renderPairStatistics(completedFinalPairStatistics, "no second deme to compare");
        // Set before the row loop just below reads it (`windowStatistics
        // Description`), not after -- the two used to run in the other
        // order, which meant every row's own tooltip always showed the
        // *previous* run's window statistics (or none, on the first run
        // of a session), never this one's.
        completedWindowStatistics = report.window_statistics || null;
        for (const name of STATISTIC_NAMES) {
            const value = payload.statistics[name];
            const element = document.getElementById(`stat-${name}`);
            applyStatRow(
                element,
                buildPointMeter(name, value, windowStatisticsDescription(name))
            );
            decorateTrajectoryStatisticRow(element, name);
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
        completedClosedForm = payload.closedForm || null;
        completedGenerationCount = payload.generationCount;
        completedFinalStatistics = payload.statistics;
        completedFinalLiteratureVisuals = payload.literatureVisuals || null;
        completedBatchSummary = null;
        completedBatchEffectiveAlleles = null;
        completedEffectiveAlleles = payload.effectiveAlleles || null;
        renderTrajectory(
            payload.convergenceGenerations,
            payload.convergenceHistories,
            payload.sigmaBand,
            payload.generationCount,
            payload.equilibrium,
            payload.identityRecovery,
            payload.closedForm
        );
        wireCompletedScrubber(payload.outputDirectory, payload.generationCount);
    }

    renderSupplementalPanels(payload.literatureVisuals);
    const panels = payload.panels;
    drawCompletedOverview(panels);
    runDemePairSelector.hidden = !panels || payload.demeCount < DEMES_NEEDED_FOR_PAIR;
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
                // The pair rows follow the pair the scatter now shows.
                completedPair = [x, y];
                completedPairFrameStatistics = null;
                if (isBatch) {
                    window.__fimScrubberPending = (window.__fimScrubberPending || 0) + 1;
                    try {
                        renderPairSummary(
                            await window.pywebview.api.get_batch_pair_statistics(
                                outputDirectory,
                                x,
                                y
                            )
                        );
                    } finally {
                        window.__fimScrubberPending -= 1;
                    }
                    return;
                }
                completedFinalPairStatistics = result.ok ? result.pairStatistics : null;
                renderPairStatistics(completedFinalPairStatistics);
            },
        });
    }

};

window.fim.returnToInitialState = function returnToInitialState() {
    if (window.fim.resetGraphStage) {
        window.fim.resetGraphStage();
    }
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

