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

/* ---- Heat map ------------------------------------------------------- */

// Space around the cells for axis labels (left, bottom), the title and
// the color bar with its labels (right).
const HEATMAP_MARGIN = { left: 76, right: 78, top: 14, bottom: 58 };

// A plot area is never drawn smaller than this, so a tiny canvas still
// gets cells rather than a negative size.
const HEATMAP_MIN_PLOT_PX = 10;

// Column labels are thinned until neighbours are at least this far
// apart, and row labels likewise.
const HEATMAP_MIN_LABEL_SPACING_PX = 46;
const HEATMAP_MIN_ROW_LABEL_SPACING_PX = 16;

// Label offsets: column labels below the plot, row labels left of it
// (plus a baseline nudge to center them on the row), the x title above
// the canvas bottom, and the y title in from the left edge.
const HEATMAP_COLUMN_LABEL_DROP = 16;
const HEATMAP_ROW_LABEL_GAP = 6;
const HEATMAP_ROW_LABEL_BASELINE = 4;
const HEATMAP_X_TITLE_RISE = 10;
const HEATMAP_Y_TITLE_INSET = 14;

// Hatching for a cell with no value: stroke spacing along the cell.
const HEATMAP_HATCH_STEP = 6;

// The color bar: gap from the plot, width, label gap, and the baseline
// drop of its top label.
const HEATMAP_COLORBAR_GAP = 16;
const HEATMAP_COLORBAR_WIDTH = 14;
const HEATMAP_COLORBAR_LABEL_GAP = 4;
const HEATMAP_COLORBAR_TOP_LABEL_DROP = 10;
const HEATMAP_COLORBAR_TITLE_RISE = 2;

// Dot marking a planned point: radius and ring width.
const HEATMAP_MARKER_RADIUS = 3.5;
const HEATMAP_MARKER_LINE_WIDTH = 1.5;

// Outline of the selected cell, drawn inside the cell by this inset.
const HEATMAP_SELECTED_LINE_WIDTH = 2;
const HEATMAP_SELECTED_INSET = 1;

// Value labels switch to exponent form at or above `HIGH` or below
// `LOW` in magnitude, and keep this many significant digits.
const HEATMAP_FORMAT_EXPONENT_HIGH = 1000;
const HEATMAP_FORMAT_EXPONENT_LOW = 0.01;
const HEATMAP_FORMAT_SIGNIFICANT_DIGITS = 3;

/* ---- Supplemental graphs (composition, spectrum, isolation) -------- */

// Pixels of space around each plot frame: left and bottom hold tick
// labels and the axis title. The isolation plot's left is wider for its
// longer identity tick labels.
const ALLELE_COMPOSITION_PLOT_MARGIN = { left: 36, right: 12, top: 12, bottom: 28 };
const FREQUENCY_SPECTRUM_PLOT_MARGIN = { left: 36, right: 12, top: 12, bottom: 28 };
const IBD_PLOT_MARGIN = { left: 42, right: 12, top: 12, bottom: 28 };

// Gap between the stacked bars of the allele-composition plot.
const ALLELE_COMPOSITION_BAR_GAP = 4;

// Each spectrum bar is inset this far on both sides so neighbours do
// not touch.
const SPECTRUM_BAR_INSET = 1;

// Target tick count on the spectrum's count axis.
const SPECTRUM_COUNT_TICK_TARGET = 5;

// Radius of a point on the isolation-by-distance plot, and how far
// above the canvas bottom its x-axis title sits.
const IBD_POINT_RADIUS = 4;
const IBD_TITLE_RISE = 12;

/* ---- Trajectory graphs (run, batch and compare) --------------------- */

// Space around the plot frame: left holds the value labels, bottom the
// generation labels.
const TRAJECTORY_PLOT_MARGIN = { left: 42, right: 12, top: 12, bottom: 22 };

// The order-profile (q) curve's frame is a little tighter on the left
// and has room below for the `q=` tick labels.
const DIFFERENTIATION_Q_PLOT_MARGIN = { left: 40, right: 12, top: 12, bottom: 28 };
const DIFFERENTIATION_Q_POINT_RADIUS = 3;

// Corner labels (the exact value and generation endpoints): decimals,
// gap left of the frame for value labels, drop below it for generations.
const TRAJECTORY_CORNER_DECIMALS = 2;
const TRAJECTORY_Y_LABEL_GAP = 6;
const TRAJECTORY_X_LABEL_DROP = 4;

// Interior ticks: roughly this many per axis, value labels to this many
// decimals.
const TRAJECTORY_TICK_TARGET = 6;
const TRAJECTORY_TICK_DECIMALS = 1;

// A statistic's curve: normal width; widths and opacities when another
// statistic is highlighted (the highlighted one thickens, the rest fade).
const TRAJECTORY_LINE_WIDTH = 2;
const TRAJECTORY_HIGHLIGHT_LINE_WIDTH = 4;
const TRAJECTORY_FADED_ALPHA = 0.35;

/* ---- Explore response curve ------------------------------------------ */

// Extra left margin (over a bare tick-label width) for the rotated y
// title, and extra bottom margin for the x title beneath the ticks.
const EXPLORE_PLOT_MARGIN = { left: 54, right: 12, top: 12, bottom: 46 };

// Label offsets: y tick labels left of the frame, x tick labels below
// it, the x title above the canvas bottom, the rotated y title in from
// the canvas left.
const EXPLORE_Y_LABEL_GAP = 6;
const EXPLORE_X_LABEL_DROP = 4;
const EXPLORE_X_TITLE_RISE = 2;
const EXPLORE_Y_TITLE_INSET = 12;

// Value ticks aimed for on a family that fits its axis to the data.
const EXPLORE_VALUE_TICK_TARGET = 6;

// A series that does not vary gets this fraction of its own magnitude
// as room either side (or 1 either side for a flat zero).
const EXPLORE_FLAT_PADDING_FRACTION = 0.5;
const EXPLORE_FLAT_PADDING_ZERO = 1;

// Tick labels switch to exponent form below `LOW` or at/above `HIGH` in
// magnitude; plain labels keep this many significant digits.
const EXPLORE_TICK_EXPONENT_LOW = 0.001;
const EXPLORE_TICK_EXPONENT_HIGH = 1000;
const EXPLORE_TICK_SIGNIFICANT_DIGITS = 2;
const EXPLORE_TICK_EXPONENT_DIGITS = 1;

// The range input's own thumb width in CSS pixels. This number is set
// in `app.css` (`.explore-scrubber input[type="range"]::-webkit-slider-
// thumb`); the scrubber needs it to line its pointer up with the
// thumb's travel, which is inset by half a thumb at each end.
const EXPLORE_SCRUB_THUMB_WIDTH_PX = 14;

/* ---- Sweep results (line chart and heat map) ------------------------- */

// Space around the one-axis line chart's frame for labels and titles.
const SWEEP_LINE_MARGIN = { left: 64, right: 24, top: 16, bottom: 52 };

// The canvas is never narrower than this, and is this tall: a line chart
// is shorter than the heat map, which needs room for its color bar.
const SWEEP_CANVAS_MIN_WIDTH = 320;
const SWEEP_LINE_CANVAS_HEIGHT = 320;
const SWEEP_HEATMAP_CANVAS_HEIGHT = 380;

// How many points the closed-form curve is sampled at.
const SWEEP_THEORY_SAMPLES = 60;

// An axis reads best on a log scale when its consecutive ratios agree to
// within `TOLERANCE` and the whole span is `MIN_SPAN` or more (two values
// have no ratios to compare, so they need the larger `TWO_VALUE_SPAN`).
const SWEEP_LOG_RATIO_TOLERANCE = 0.05;
const SWEEP_LOG_MIN_SPAN = 5;
const SWEEP_LOG_TWO_VALUE_SPAN = 20;

// Value-axis ticks: about this many intervals, rounded to this many
// significant digits to shed floating-point dust.
const SWEEP_TICK_INTERVALS = 4;
const SWEEP_TICK_PRECISION = 12;

// Inset of a numeric x axis from the frame, so the end points are not
// drawn on it, and the least room per categorical x label.
const SWEEP_X_INSET = 12;
const SWEEP_MIN_X_LABEL_SPACING_PX = 56;

// Label offsets: y tick labels left of the frame (plus a baseline nudge),
// x labels below it, the x title above the canvas bottom, and the
// rotated y title in from the canvas left.
const SWEEP_Y_LABEL_GAP = 6;
const SWEEP_Y_LABEL_BASELINE = 4;
const SWEEP_X_LABEL_DROP = 16;
const SWEEP_X_TITLE_RISE = 10;
const SWEEP_Y_TITLE_INSET = 14;

// A point's marker radius, and how close the pointer must be to pick it.
const SWEEP_POINT_RADIUS = 4;
const SWEEP_HIT_RADIUS_PX = 14;

/* ---- Explore surface and slices -------------------------------------- */

// The heat map canvas: never narrower than the minimum, always this tall.
const EXPLORE_SURFACE_CANVAS_MIN_WIDTH = 320;
const EXPLORE_SURFACE_CANVAS_HEIGHT = 360;

// The two slice charts beside it are smaller.
const EXPLORE_SLICE_CANVAS_MIN_WIDTH = 240;
const EXPLORE_SLICE_CANVAS_HEIGHT = 200;
