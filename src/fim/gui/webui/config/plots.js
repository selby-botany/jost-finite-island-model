"use strict";

/* Per-plot geometry and drawing parameters, one section per plot.
 *
 * Distances are canvas pixels unless a name ends otherwise. Colors come
 * from `palette.js`, shared drawing vocabulary from `canvas.js`.
 */

/* ---- Probability axes (shared by every plot of a frequency) -------- */

/* The reference visualization's own tick spacing (`Dear-NolanMarch17Final.
 * pdf` Figs. 1-2). Every `"frequency"` axis is bounded `[0, 1]` by
 * construction, so this scale is fixed and identical on all of them.
 */
const PROBABILITY_TICK_VALUES = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0];

// The same axis on a small plot, where five intervals would crowd.
const COMPACT_PROBABILITY_TICK_VALUES = [0.0, 0.5, 1.0];

/* ---- Scatter plot: points ------------------------------------------ */

/* How the points are drawn, as offered in Settings ("Scatter plot
 * points"). Mirrors `fim.gui.preferences.SCATTER_STYLES`; the Python
 * side validates a saved choice against its own copy.
 */
const SCATTER_STYLES = ["circles", "color", "badge", "color-badge", "density", "dots", "trail"];
const DEFAULT_SCATTER_STYLE = "color-badge";

/* Marker radius is `BASE + SCALE * sqrt(count)`, so the marker's area
 * grows roughly in step with the number of alleles it stands for.
 */
const MARKER_BASE_RADIUS = 3;
const MARKER_COUNT_SCALE = 1.6;

// Fixed radius of the marks in the `color`, `color-badge` and `density`
// styles, where color rather than size carries the count.
const COLOR_MARKER_RADIUS = 4;

// The `dots` style draws slightly smaller marks than the fixed ones.
const SCATTER_DOTS_RADIUS_REDUCTION = 0.5;

// Opacity of one point in the `dots` style; overlapping points compound
// (`1 - (1 - alpha)^count`) so a pile darkens and saturates.
const DOTS_BASE_ALPHA = 0.18;

// Labels for the five `COUNT_RAMP` steps (counts 1, 2-3, 4-7, 8-15, 16+).
const COUNT_RAMP_LABELS = ["1", "2-3", "4-7", "8-15", "16+"];

// Outline of a ramp, dots or most-frequent marker.
const SCATTER_OUTLINE_WIDTH = 0.6;

// Fill opacity and outline of an ordinary marker (the `circles` family).
const SCATTER_RARE_ALPHA = 0.75;
const SCATTER_RARE_OUTLINE_WIDTH = 0.4;

// Gap between a marker's edge and its count label.
const SCATTER_COUNT_LABEL_OFFSET = 2;

/* ---- Scatter plot: origin badge ------------------------------------ */

// The pile of alleles at exactly (0, 0) is drawn as a labelled badge:
// a dot on the origin, and a rounded label inset from the axes corner.
const SCATTER_ORIGIN_DOT_RADIUS = 3;
const SCATTER_BADGE_INSET = 8;
const SCATTER_BADGE_PADDING_X = 4;
const SCATTER_BADGE_EXTRA_HEIGHT = 6;
const SCATTER_BADGE_CORNER_RADIUS = 4;

/* ---- Scatter plot: density map and trail --------------------------- */

// Square bins per axis for the `density` style, fewer on a compact plot.
const DENSITY_BINS = 20;
const COMPACT_DENSITY_BINS = 10;

// Earlier scrubber frames faded in behind the current one (`trail`).
const TRAIL_FRAMES = 4;
const SCATTER_TRAIL_DOT_RADIUS = 2.5;

// Trail frame opacity runs from `MIN` (oldest) up to `MIN + SPAN` just
// below the current frame, so movement reads as a fading wake.
const SCATTER_TRAIL_ALPHA_MIN = 0.12;
const SCATTER_TRAIL_ALPHA_SPAN = 0.28;

/* ---- Scatter plot: frame, axes and domain -------------------------- */

// The one panel fills the whole canvas, so it gets generous room for
// tick labels and an axis title on every side.
const SINGLE_PANEL_PADDING = 44;
const SINGLE_PANEL_TICK_FONT = 10;

// A canvas whose shorter side is under this shrinks its padding to
// `SMALL_PADDING_FRACTION` of that side (never under `SMALL_PADDING_MIN`)
// so a small inline pane still has room to plot.
const SCATTER_SMALL_CANVAS_SIDE = 520;
const SCATTER_SMALL_PADDING_MIN = 28;
const SCATTER_SMALL_PADDING_FRACTION = 0.09;

// A plot area narrower than this is "compact": fewer ticks, fewer bins
// and no count labels.
const COMPACT_PLOT_SIZE_THRESHOLD = 420;

// Dash pattern (dash, gap) of the `x = y` reference diagonal.
const SCATTER_DIAGONAL_DASH = [4, 4];

// Ticks across an unbounded (`"pca"`) panel's auto-scaled domain; purely
// for orientation, not a claim about any meaningful value.
const AUTO_TICK_COUNT = 5;
const COMPACT_AUTO_TICK_COUNT = 3;

// Fraction of the data's span added as margin on every side of an
// auto-scaled domain, so the outermost points never sit on the frame.
const DOMAIN_PADDING_FRACTION = 0.08;

// Domain of an auto-scaled panel with no points yet, and the margin
// used when every point shares one coordinate (a zero-width span).
const SCATTER_EMPTY_DOMAIN = { xMin: -1, xMax: 1, yMin: -1, yMax: 1 };
const SCATTER_DEGENERATE_PADDING = 1;

// Domain of a `"frequency"` panel: a probability on both axes.
const SCATTER_FREQUENCY_DOMAIN = { xMin: 0, xMax: 1, yMin: 0, yMax: 1 };

/* ---- Scatter plot: zoom drag --------------------------------------- */

// A drag shorter than this in either dimension is an accidental click,
// not a "zoom to this rectangle" gesture: well above a click's few
// pixels of jitter, below the smallest rectangle a deliberate drag draws.
const ZOOM_DRAG_MINIMUM_PX = 12;
const SCATTER_ZOOM_BOX_LINE_WIDTH = 1;
