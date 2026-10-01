"use strict";

/* Values that describe the model's own vocabulary and the starting
 * contents of its input grids. Several mirror a Python default; the
 * comment on each says which, since the two sides are not linked by
 * code and must be kept in step by hand.
 */

// Display name for a ploidy value, as shown in summaries of the
// configuration. A ploidy outside this list is shown as "ploidy N".
const PLOIDY_NAMES = { 1: "haploid", 2: "diploid", 3: "triploid", 4: "tetraploid" };
