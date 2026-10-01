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

/* ---- Okabe-Ito data colors ------------------------------------------ */

/* The Okabe-Ito qualitative palette: eight hues chosen to stay distinct
 * under the common forms of color-blindness. The plots draw their data
 * series from it, so a series keeps one identity across every screen.
 * Only the hues a named constant needs are listed so far.
 */
const OKABE_ITO_VERMILLION = "#d55e00";

/* ---- Theme fallbacks ------------------------------------------------ */

/* Used only when a `--fim-*` custom property reads back empty (a canvas
 * drawn before the stylesheet applies). They mirror the light theme's
 * values in `app.css`, so a stale paint looks like the real one.
 */
const THEME_FALLBACK_MUTED = "#5d6863";
const THEME_FALLBACK_BORDER = "#cdd5d0";
const THEME_FALLBACK_FOREGROUND = "#242824";

/* ---- Heat maps ------------------------------------------------------ */

// Sequential ramp (ColorBrewer YlGnBu, the family the scatter plot's
// count ramp comes from), light for low values to dark for high.
const HEATMAP_SEQUENTIAL = [
    "#ffffd9",
    "#edf8b1",
    "#c7e9b4",
    "#7fcdbb",
    "#41b6c4",
    "#1d91c0",
    "#225ea8",
    "#253494",
    "#081d58",
];

// Diverging blue-white-orange ramp for a difference centred on zero.
const HEATMAP_DIVERGING = [
    "#2166ac",
    "#67a9cf",
    "#d1e5f0",
    "#f7f7f7",
    "#fddbc7",
    "#ef8a62",
    "#b2182b",
];

// A marker on a heat map: a white dot with a black ring, readable on
// every color of both ramps.
const HEATMAP_MARKER_FILL = "#ffffff";
const HEATMAP_MARKER_STROKE = "#000000";

/* ---- Supplemental graphs --------------------------------------------- */

// The beta-distribution overlay on the frequency spectrum, and the fitted
// curve on the isolation-by-distance plot: one "model" color for both.
const SUPPLEMENTAL_MODEL_COLOR = OKABE_ITO_VERMILLION;

// A bar segment whose allele arrived with no color of its own.
const ALLELE_FALLBACK_COLOR = "#999999";

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
