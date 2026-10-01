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
