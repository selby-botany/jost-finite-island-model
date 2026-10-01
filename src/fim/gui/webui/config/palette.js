"use strict";

/* Every color the GUI's canvas drawing hard-codes.
 *
 * Colors that should follow the light/dark theme are not here: those are
 * CSS custom properties (`--fim-border`, `--fim-muted`, ...) that the
 * drawing code reads at draw time. What is here is deliberately the same
 * in both themes -- data colors (a statistic keeps its hue wherever it
 * appears) and chart furniture drawn in mid-greys that read on either
 * background.
 *
 * Hex values only, `#rrggbb`: `heatmapRgb` parses the ramps by slicing.
 */

/* ---- Chart furniture: neutral greys and ink ------------------------ */

// Axis frames, tick marks and the origin badge's outline.
const CHART_AXIS_COLOR = "#9a9a9a";

// Tick labels and the origin badge's dot.
const CHART_AXIS_LABEL_COLOR = "#6b6b6b";

// Light guide lines, such as the scatter's `x = y` diagonal.
const CHART_GUIDE_COLOR = "#bbbbbb";

// Near-black text and marker outlines.
const CHART_INK_COLOR = "#1a1a1a";

// Pure black, for the thinnest marker outlines.
const CHART_BLACK = "#000000";

// Fill behind small in-plot text labels.
const CHART_BADGE_FILL = "#f1f1f1";

/* ---- Scatter plot -------------------------------------------------- */

// The most frequent allele in either displayed deme.
const COLOR_COMMON = "#ff0000";

// Every other allele.
const COLOR_RARE = "#d97a26";

// The zoom-drag selection rectangle: the common-allele red, so it reads
// as "this area", with a faint fill that leaves the points underneath
// visible.
const SCATTER_ZOOM_BOX_STROKE = COLOR_COMMON;
const SCATTER_ZOOM_BOX_FILL = "rgba(255, 0, 0, 0.12)";

// Sequential, colour-blind-safe (ColorBrewer YlGnBu) ramp for counts of
// 1, 2-3, 4-7, 8-15 and 16 or more.
const COUNT_RAMP = ["#c7e9b4", "#7fcdbb", "#41b6c4", "#2c7fb8", "#253494"];
