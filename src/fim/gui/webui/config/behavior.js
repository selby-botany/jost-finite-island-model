"use strict";

/* Timing, layout and interaction parameters that are not about how one
 * plot is drawn (see `plots.js`) or what colors it uses (`palette.js`).
 * Durations are milliseconds and distances CSS pixels unless a name
 * says otherwise.
 */

/* ---- Run graph stage ----------------------------------------------- */

/* Relative column widths of the panes on the Run card, used as CSS grid
 * `fr` units: the trajectory gets more room than the square scatter, and
 * the bar-style graphs a little more than it.
 */
const GRAPH_WEIGHT_SCATTER = 1;
const GRAPH_WEIGHT_TRAJECTORY = 1.5;
const GRAPH_WEIGHT_WIDE = 1.3;

// What a fresh install shows together, matching `fim.gui.preferences.
// DEFAULT_RUN_GRAPHS`; replaced by the saved choice once the bridge is up.
const DEFAULT_GRAPH_KEYS = ["scatter", "trajectory"];
const DEFAULT_GRAPH_COLUMNS = 2;

// A pane narrower than this is unreadable, so the effective column count
// drops until every column can have at least this much (rows then follow).
const MIN_PANE_WIDTH_PX = 220;
const GRAPH_GAP_PX = 16;

// The trajectory canvas is 4:3, so a pane's height moves by this much per
// pixel of its width; the scatter canvas is square, so it moves by one.
const TRAJECTORY_HEIGHT_PER_WIDTH = 0.75;

/* ---- Graph zoom frame ---------------------------------------------- */

// The `+`/`-` buttons step the zoom factor by `ZOOM_STEP` within
// `[ZOOM_MIN, ZOOM_MAX]`; 1 is the opening ("Fit") size.
const ZOOM_STEP = 0.25;
const ZOOM_MIN = 0.5;
const ZOOM_MAX = 4;

// The opening size is never smaller than this, however small the frame.
const ZOOM_FRAME_MIN_WIDTH_PX = 320;
const ZOOM_FRAME_MIN_HEIGHT_PX = 240;

/* ---- Scrubber ------------------------------------------------------- */

// A watchable cadence: fast enough to read as motion rather than a
// slideshow, slow enough that individual frames (up to
// `GUI_ANIMATION_MAX_FRAMES` of them) do not blur past unreadably -- the
// same value the Tk-era `AnimationScreen` and `animation.js` used.
const STEP_INTERVAL_MS = 150;

// A single-frame set has nothing to play or drag through -- every
// control stays disabled and there is nowhere to move to.
const MINIMUM_FRAMES_TO_ANIMATE = 2;

/* ---- Axis range widget ---------------------------------------------- */

// Resolution of the two-thumb range slider: positions are integers in
// `[0, AXIS_RANGE_SLIDER_STEPS]` mapped onto the axis domain.
const AXIS_RANGE_SLIDER_STEPS = 1000;

// A new axis range starts with this many values.
const AXIS_RANGE_DEFAULT_COUNT = 4;

// Field text switches to exponent form below `LOW` or at/above `HIGH`
// in magnitude; exponent form keeps `EXPONENT_DIGITS` significant
// digits, plain form `PLAIN_DIGITS` -- short, but exact enough to
// re-parse into the same number.
const AXIS_RANGE_EXPONENT_LOW = 0.001;
const AXIS_RANGE_EXPONENT_HIGH = 100000;
const AXIS_RANGE_EXPONENT_DIGITS = 4;
const AXIS_RANGE_PLAIN_DIGITS = 6;

/* ---- Field help tooltip --------------------------------------------- */

// Closest the bubble may sit to the window edge, and its gap above the
// field's title.
const FIELD_TOOLTIP_EDGE_MARGIN_PX = 4;
const FIELD_TOOLTIP_GAP_PX = 4;

/* ---- Sweep and Explore planning ------------------------------------- */

// Wait this long after the last edit before asking for a new plan, so
// typing a value does not fire a request per keystroke.
const SWEEP_PLAN_DEBOUNCE_MS = 300;
const EXPLORE_SWEEP_DEBOUNCE_MS = 250;

// How many plan rows to draw; a larger plan says how many it omitted.
const SWEEP_PLAN_ROW_LIMIT = 200;

// How many points an Explore response curve is sampled at before it is
// resampled to equal steps of the statistic's own change.
const EXPLORE_RESPONSE_SAMPLES = 200;

/* Mirrors `fim.gui.app._EQUILIBRIUM_SWEEP_DOMAINS` (a test asserts they
 * agree), with each axis's default spacing and whether it is a count.
 */
const EXPLORE_SWEEP_AXES = {
    N: { domain: [10, 5000], log: true, integer: true },
    d: { domain: [2, 50], log: false, integer: true },
    m: { domain: [0.0001, 0.5], log: true, integer: false },
    mu: { domain: [0.000001, 0.1], log: true, integer: false },
};

// A deme count opens on 2 to 16: a handful of islands up to a dozen or
// so, the range most studies of this model use.
const DEME_COUNT_DEFAULT_RANGE = { start: 2, stop: 16, count: 4 };

/* A new Explore interval for a rate or size spans a decade (`FACTOR`)
 * either side of the entered value, clamped to the axis domain, with
 * `COUNT` values.
 */
const EXPLORE_INTERVAL_DECADE_FACTOR = 10;
const EXPLORE_INTERVAL_COUNT = 5;

// Sensible first range per key on the sweep screen, so a new axis opens
// on something useful rather than on the full display domain (which
// spans decades).
const SWEEP_DEFAULT_RANGES = {
    m: { start: 0.0001, stop: 0.1, count: 4 },
    mu: { start: 0.000001, stop: 0.001, count: 4 },
    N: { start: 10, stop: 1000, count: 4 },
    d: { ...DEME_COUNT_DEFAULT_RANGE },
};

// A sweep axis with no entry above: its value count, and the range used
// when it has no display domain either.
const SWEEP_DEFAULT_COUNT = 4;
const SWEEP_FALLBACK_RANGE = { start: 1, stop: 10, count: SWEEP_DEFAULT_COUNT };

// How many differences a reproducibility comparison lists before the
// rest are left out of the banner.
const REPRODUCIBILITY_DIFFERENCE_LIMIT = 6;

// Axis range values are rounded to this many significant digits to shed
// floating-point dust (snapping a sweep list), or this many when the
// slider position is converted back to a value.
const AXIS_RANGE_SNAP_DIGITS = 12;
const AXIS_RANGE_SLIDER_VALUE_DIGITS = 6;

/* ---- Open-run table -------------------------------------------------- */

// A column resize handle never drags a column narrower than this.
const OPEN_RUN_MIN_COLUMN_WIDTH_PX = 48;

// Index of the statistics summary cell in a replicate row (the one cell
// that is long enough to need its full text as a hover title).
const OPEN_RUN_SUMMARY_COLUMN_INDEX = 4;

/* ---- Number display -------------------------------------------------- */

// Significant digits of a statistic value shown in the results panels.
// The server formats the six named statistics with `%.6g`; a raw float
// the server sends unformatted (the order-profile values, the effective
// allele counts) is rounded the same way here so the panel reads evenly.
const REPORT_VALUE_SIGNIFICANT_DIGITS = 6;

// Decimal places of a window mean and its standard error in a statistic's
// tooltip, and of a meter's value in the statistics table.
const WINDOW_STATISTIC_DECIMALS = 4;
const METER_VALUE_DECIMALS = 2;

// Decimal places of a running sum in an input grid (a migration-matrix
// row sum, an initial-frequency cell sum).
const GRID_SUM_DECIMALS = 3;

// Significant digits for an Explore value when the server's payload does
// not say.
const EXPLORE_DEFAULT_SIGNIFICANT_DIGITS = 4;

/* ---- Thresholds ------------------------------------------------------ */

// Choosing a pair of demes to compare needs at least two demes; with
// fewer, the selector is hidden.
const DEMES_NEEDED_FOR_PAIR = 2;

// The compare screen needs at least this many runs checked.
const COMPARE_MINIMUM_RUNS = 2;

// A confidence interval computed from only 2 or 3 independent replicates
// is mathematically correct but can be enormous -- Student's-t with 1
// degree of freedom (`sampleCount === 2`) has a two-tailed 95% critical
// value near 12.7, so a perfectly ordinary difference between two
// replicates' own values can produce a `low`/`high` many times wider
// than the statistic's own natural range. Originally confirmed live
// against `pooled_convergence_histories`'s own pre-carry-forward
// behavior: the tail end of a real, staggered-stopping batch's own `D`
// band reached `[-2.98, 3.54]` for a statistic that never otherwise
// leaves roughly `[0, 1]`, because `sampleCount` shrank sharply as
// replicates finished and dropped out of the pool. That engine-level
// bug is now fixed at its own source (each replicate's own final value
// is held constant once it stops, rather than dropped -- that
// function's own docstring), which already keeps `sampleCount`
// constant across every generation of one statistic's own completed
// history. This *relative* threshold (a point counts only once its own
// `sampleCount` is at least half of the largest `sampleCount` seen
// anywhere in the current view) is what is left worth guarding
// against: the *live* view's own accumulator (`run-view-running.js`'s
// `liveBatchTrajectory`) still legitimately starts thin and grows
// tick by tick as more replicates begin reporting, a real, still-
// occurring case this same domain calculation is shared with. An
// *absolute* cutoff was tried first and rejected: it would incorrectly
// exclude a small (fewer than the cutoff) but otherwise perfectly
// ordinary completed batch's own band from the domain entirely, since
// carry-forward means every one of its points shares that same small
// count uniformly, not just a thin tail.
const MIN_SAMPLE_COUNT_FRACTION_FOR_DOMAIN = 0.5;
