"use strict";

/* Values that describe the model's own vocabulary and the starting
 * contents of its input grids. Several mirror a Python default; the
 * comment on each says which, since the two sides are not linked by
 * code and must be kept in step by hand.
 */

// Display name for a ploidy value, as shown in summaries of the
// configuration. A ploidy outside this list is shown as "ploidy N".
const PLOIDY_NAMES = { 1: "haploid", 2: "diploid", 3: "triploid", 4: "tetraploid" };

/* ---- Starting contents of the input grids --------------------------- */

/* Every grid's rule for a brand-new cell is the same: it is "already
 * valid the moment it appears", so nothing warns before the user has
 * typed anything.
 */

// A new locus row's length: the library's own default
// (`fim.model.params.DEFAULT_LOCUS_LENGTH`).
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
