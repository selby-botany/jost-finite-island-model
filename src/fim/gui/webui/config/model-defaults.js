"use strict";

/* Values that describe the model's own vocabulary and the starting
 * contents of its input grids. Several mirror a Python default; the
 * comment on each says which, since the two sides are not linked by
 * code and must be kept in step by hand.
 */

// Display name for a ploidy value, as shown in summaries of the
// configuration. A ploidy outside this list is shown as "ploidy N".
const PLOIDY_NAMES = { 1: "haploid", 2: "diploid", 3: "triploid", 4: "tetraploid" };

// The warning beside each expensive statistic (the catalog's `"opt_in"`
// history: E_ST, K_ST, A_CGD, δG, I) wherever one can be chosen, in
// Settings' "Statistics shown" and Configure's convergence checkboxes.
// Showing one turns on `track_expensive_statistics`
// (`fim.statistics.catalog.expensive_statistics_requested`); watching one
// computes it every generation (`fim.engine._statistics_to_compute`).
const EXPENSIVE_STATISTIC_NOTE =
    "Expensive: while shown or watched it is computed every generation, " +
    "which makes runs take longer.";

/* ---- Starting contents of the input grids --------------------------- */

/* Every grid's rule for a brand-new cell is the same: it is "already
 * valid the moment it appears", so nothing warns before the user has
 * typed anything.
 */

// A new locus row's length: the library's own default
// (`fim.config.defaults.DEFAULT_LOCUS_LENGTH`).
const DEFAULT_LOCUS_LENGTH = 200;

// A new per-deme population size row (when `d` grew past the values
// `field-N` holds) starts at this size, not a bare zero.
const DEFAULT_N_VALUE = 225;

// A new initial-frequency cell: "this deme starts fixed for allele 0",
// a mapping that already sums to 1.
const DEFAULT_P0_CELL_TEXT = "0:1";

// A new migration matrix is an identity matrix of this size: each deme
// keeps `SELF_RETENTION` of its individuals and exchanges none, so every
// row already sums to 1.
const DEFAULT_MATRIX_SELF_RETENTION = 1;
const DEFAULT_MATRIX_SIZE = 2;

// How far a migration-matrix row sum or an initial-frequency cell sum may
// stray from exactly 1 before it is flagged. Typed decimals do not add
// up exactly in floating point, so exact equality would flag valid input.
const ROW_SUM_TOLERANCE = 1e-6;
const P0_CELL_SUM_TOLERANCE = 1e-6;

/* ---- Other values mirrored from Python ------------------------------- */

// The sigma-band window filled in when the band is switched on with the
// field empty (`fim.gui.config_form._DEFAULT_SIGMA_BAND_WINDOW`, which
// is text because it goes straight into a form field).
const DEFAULT_SIGMA_BAND_WINDOW = "100";

// Prefix of a user-saved preset's id, which distinguishes it from a
// built-in one (`fim.gui.app._USER_PRESET_ID_PREFIX`).
const USER_PRESET_ID_PREFIX = "user:";

// The trailing-window mean estimator (`trailingWindowEstimate`) repeats
// `fim.convergence.window_statistics` exactly, so the page's mean and
// standard error match the monitor's. Fewer points than this give no
// estimate (`MINIMUM_NOISE_CHECK_WINDOW`); a mean is noise-adequate once
// its standard error is at most this fraction of the tolerance
// (`NOISE_TOLERANCE_FRACTION`); and the lag-1 correlation is clamped
// just below 1 so a window that has not decorrelated at all gets a very
// large, finite standard error (`_MAXIMUM_LAG1_CORRELATION`).
const MINIMUM_WINDOW_ESTIMATE_POINTS = 8;
const NOISE_TOLERANCE_FRACTION = 0.5;
const MAXIMUM_LAG1_CORRELATION = 1 - 1e-9;
// The floor on the integrated autocorrelation time, so a perfectly
// anticorrelated window (lag-1 correlation -1) does not divide by zero
// (the `1e-9` in `window_statistics`'s own `tau_int`).
const MINIMUM_INTEGRATED_AUTOCORRELATION_TIME = 1e-9;

// How the trajectory graph can draw each statistic, and the one a fresh
// install uses (`fim.gui.preferences.TRAJECTORY_DISPLAYS` and
// `DEFAULT_TRAJECTORY_DISPLAY`).
const TRAJECTORY_DISPLAYS = ["every_generation", "trailing_mean", "cumulative_mean"];
const DEFAULT_TRAJECTORY_DISPLAY = "every_generation";
