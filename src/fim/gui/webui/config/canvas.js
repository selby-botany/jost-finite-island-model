"use strict";

/* The shared vocabulary of the GUI's canvas drawing: fonts, tick
 * geometry and the like. Values that belong to one particular plot live
 * in `plots.js`; colors live in `palette.js`.
 */

/* ---- Fonts --------------------------------------------------------- */

// The scatter plot's font stack: the macOS system font first.
const FONT_FAMILY_SYSTEM = "-apple-system, sans-serif";

// The plain stack at two sizes: small for the line and bar charts' tick
// labels, large for the heat map and sweep chart's.
const AXIS_TICK_FONT_SIZE = 10;
const FONT_AXIS_SMALL = `${AXIS_TICK_FONT_SIZE}px sans-serif`;
const FONT_AXIS_LARGE = "12px sans-serif";

/* ---- Axes ---------------------------------------------------------- */

// Length of a tick mark, in pixels.
const TICK_LENGTH = 4;

// Gap between a tick mark and its label: below an x axis, beside a y
// axis. A y label sits a little further out because its text is
// right-aligned against the gap.
const TICK_LABEL_GAP_X = 1;
const TICK_LABEL_GAP_Y = 2;

// Stroke width of axis frames, tick marks and plain guide lines, and of
// a data curve drawn over them.
const AXIS_LINE_WIDTH = 1;
const CURVE_LINE_WIDTH = 2;

// Gap below a plot frame to the top of its centered category or end
// labels (`textBaseline = "top"`).
const AXIS_LABEL_DROP = 5;

// Dash patterns, as `[dash, gap, ...]` pixel lengths for `setLineDash`.
const DASH_MODEL_CURVE = [4, 4];

// A thin reference line over data: a predicted equilibrium, and the
// vertical "you are here" marker of a scrubber or an Explore value.
const DASH_REFERENCE_LINE = [4, 3];

// A closed-form expected curve: dash-dot, so it is never mistaken for a
// reference line.
const DASH_CLOSED_FORM = [8, 3, 2, 3];

// A fitted identity-recovery curve: dotted, a fourth distinct style.
const DASH_DOTTED = [1, 3];

// Width of a dashed overlay line, a little under a data curve's.
const OVERLAY_LINE_WIDTH = 1.5;

// Opacity of a translucent band (a sigma band, a pooled band) drawn
// behind the curves it surrounds.
const BAND_ALPHA = 0.2;

/* ---- Numeric tick selection ---------------------------------------- */

/* How `niceAxisTicks` rounds a raw tick step to 1, 2 or 5 times a power
 * of ten (d3's convention). `rawStep / 10^k` is compared against each
 * entry in order; the first `from` it reaches picks that `multiple`, and
 * anything below the last `from` uses 1. The breaks sit at the
 * geometric midpoints between the allowed multiples, so the rounded step
 * is never more than about a factor of two from the raw one.
 */
const NICE_STEP_BREAKS = [
    { from: 7.5, multiple: 10 },
    { from: 3.5, multiple: 5 },
    { from: 1.5, multiple: 2 },
];

// Slack for floating-point error when deciding whether the first or
// last tick lands exactly on the axis end (`0.6000000000000001`).
const TICK_EDGE_EPSILON = 1e-9;

/* Decimal places for an auto-scaled tick label by the axis range: the
 * first entry whose `below` the range is under applies. A narrow range
 * needs more digits or every tick would print the same.
 */
const AUTO_TICK_DECIMALS = [
    { below: 0.1, decimals: 3 },
    { below: 10, decimals: 2 },
];
const AUTO_TICK_DEFAULT_DECIMALS = 1;

// Decimal places on a probability (`[0, 1]`) axis: `0.0`, `0.2`, ...
const PROBABILITY_TICK_DECIMALS = 1;
