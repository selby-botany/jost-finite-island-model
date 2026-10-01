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
 */
const OKABE_ITO_BLUE = "#0072b2";
const OKABE_ITO_VERMILLION = "#d55e00";
const OKABE_ITO_BLUISH_GREEN = "#009e73";
const OKABE_ITO_REDDISH_PURPLE = "#cc79a7";
const OKABE_ITO_ORANGE = "#e69f00";
const OKABE_ITO_SKY_BLUE = "#56b4e9";
const OKABE_ITO_YELLOW = "#f0e442";
const OKABE_ITO_BLACK = "#000000";

// Two further hues for series beyond the eight above: ColorBrewer Dark2's
// purple and Tableau10's brown, chosen only for staying visually distinct
// from every Okabe-Ito color, not for membership in one named palette.
const DARK2_PURPLE = "#7570b3";
const TABLEAU_BROWN = "#8c564b";

/* ---- Statistics ------------------------------------------------------ */

/* One color per named statistic, so a statistic keeps its hue in the
 * trajectory legend and curve, the stats panel, the sweep chart and
 * Explore alike (botanist GUI design doc §11.3's "disciplined statistic
 * color language"). `A_CGD` takes the palette's eighth and last color
 * (yellow); `Delta` and `MI` exhaust it, so they borrow the two extra
 * hues above.
 */
const STATISTIC_TRAJECTORY_COLORS = {
    D: OKABE_ITO_BLUE,
    G_ST: OKABE_ITO_VERMILLION,
    E_ST: OKABE_ITO_BLUISH_GREEN,
    K_ST: OKABE_ITO_REDDISH_PURPLE,
    H_S: OKABE_ITO_ORANGE,
    H_T: OKABE_ITO_SKY_BLUE,
    H_ST: OKABE_ITO_BLACK,
    A_CGD: OKABE_ITO_YELLOW,
    Delta: TABLEAU_BROWN,
    MI: DARK2_PURPLE,
};

/* ---- Compare screen -------------------------------------------------- */

/* One color per compared run rather than per statistic: the Okabe-Ito
 * hues without yellow, which is faint on a white plot. The list cycles
 * if more runs are selected than colors, so two runs may share a color
 * but the screen never runs out. Kept apart from
 * `STATISTIC_TRAJECTORY_COLORS` because the two encode unrelated things
 * -- run identity here, statistic identity there -- and design principle
 * §11.1 says a data-encoding palette is not borrowed for anything else.
 */
const COMPARE_RUN_COLORS = [
    OKABE_ITO_BLUE,
    OKABE_ITO_VERMILLION,
    OKABE_ITO_BLUISH_GREEN,
    OKABE_ITO_REDDISH_PURPLE,
    OKABE_ITO_ORANGE,
    OKABE_ITO_SKY_BLUE,
    OKABE_ITO_BLACK,
];

/* ---- Explore screen -------------------------------------------------- */

/* Line colors for the Explore predictions that are not report statistics
 * (`STATISTIC_TRAJECTORY_COLORS` covers those, so a color learned as "D"
 * on Results reads as "D" here too). Only one unit family is ever on the
 * canvas at a time, so these need only be distinct within a family,
 * which is why the entropy (`S_*`) and effective-allele (`A_*`)
 * predictions can reuse the same two hues.
 */
const EXPLORE_EXTRA_SERIES_COLORS = {
    S_S: OKABE_ITO_BLUE,
    S_T: OKABE_ITO_VERMILLION,
    A_S: OKABE_ITO_BLUE,
    A_T: OKABE_ITO_VERMILLION,
    identity_recovery_rate: OKABE_ITO_REDDISH_PURPLE,
    identity_recovery_equilibrium: TABLEAU_BROWN,
    identity_recovery_half_life: OKABE_ITO_BLUISH_GREEN,
    mutation_negligible_equilibrium: DARK2_PURPLE,
};

// A series with no assigned color at all.
const EXPLORE_FALLBACK_SERIES_COLOR = "#666666";

/* ---- Sweep results ---------------------------------------------------- */

// A sweep line for a statistic with no assigned color.
const SWEEP_FALLBACK_COLOR = OKABE_ITO_BLUE;

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
