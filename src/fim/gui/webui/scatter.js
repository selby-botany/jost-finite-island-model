"use strict";

/* Dependency-free Canvas 2D scatter renderer (design doc §3.5, §3.10;
 * extended by `20260822-claude-sonnet-5-visualization-and-config-
 * editors-design.md` §3.1-§3.2, the axis-domain fix below, and the
 * botanist GUI redesign doc `20260907-claude-sonnet-5-botanist-gui-
 * redesign.md` §7.4's own ring-marker shape).
 *
 * Draws exactly what `fim.viz.scatter.marker_groups` already computes:
 * one point per unique (x, y) coordinate, sized by how many source rows
 * landed on it (`30 + 18*sqrt(count)`, matching the CLI's own
 * `plot_frequency_scatter` marker-size formula so the GUI's live view
 * and the CLI's saved `scatter.png` read the same way), and labeled
 * with its own coincidence count once it exceeds one -- the exact
 * visual encoding the reference visualization (`Dear-NolanMarch17Final.
 * pdf` Figs. 1-2) uses (design §0.5), now including that same
 * reference's own `0.0`-`1.0` probability-scale tick marks on both axes
 * for a genuine deme-frequency panel. The most frequent allele in
 * either displayed deme draws as a hollow `tab:blue` ring, not merely a
 * differently colored disc: color alone distinguishing "most frequent"
 * from "other" was itself once a reported defect (§11.4's own "color is
 * never the only channel" principle) -- other alleles stay ordinary
 * filled `tab:orange` dots.
 *
 * `panel.kind` (`fim.viz.scatter._panel`'s own field) decides the axis
 * domain: `"frequency"` (the default, and the only `kind` the run view
 * ever draws since the simplify-main-plot change) is bounded `[0, 1]`
 * by construction, gets the fixed probability-scale ticks and the
 * `x=y` reference diagonal; `"pca"` is an unbounded principal-component
 * projection -- real values are routinely negative or outside `[0, 1]`
 * entirely, so it gets an auto-scaled domain fit to the panel's own
 * points instead, numeric (not probability-labeled) ticks, and no
 * diagonal (two different principal components have no "equal" relation
 * the way two demes' frequencies of the same allele do). `kind: "pca"`
 * is no longer produced by anything the run view calls automatically,
 * but stays supported here for whoever calls `pca_project`/`pca_
 * summary` directly.
 *
 * The bridge always ships exactly one already-grouped `{x, y, count,
 * common}` panel -- always the Deme 1/Deme 2 pair by default, or
 * whichever pair the "Compare demes directly" selector requested (any
 * further reduction a high deme count needs happens server-side, per
 * design §3.5's "the client never does linear algebra" rule) -- so this
 * module only ever draws one panel's points at a time; it holds no
 * model of what `d` means beyond the `kind` discriminator above.
 */

const MARKER_BASE_RADIUS = 3;
const MARKER_COUNT_SCALE = 1.6;
const COLOR_COMMON = "#1f6fb2";
const COLOR_RARE = "#d97a26";

// The panel most recently drawn to `runCanvas`. `syncCanvasSize`
// reads this to redraw after a resize without a second bridge call.
let _currentPanel = null;

/**
 * Point `canvas`'s drawing buffer at its current CSS layout size.
 *
 * Canvas HTML `width`/`height` attributes define the drawing-buffer
 * resolution independently of the CSS layout size, so a canvas drawn
 * before the browser has applied a pending layout change renders into
 * a stale buffer and is then stretched or squeezed into the real box.
 * Every draw therefore syncs first rather than trusting the graph
 * stage's own `ResizeObserver` (`run-graph-stage.js`) to correct it a
 * frame later: that correction is asynchronous, so relying on it means
 * the very first paint of a completed run is visibly wrong-resolution
 * until the next frame.
 *
 * Returns `true` when the buffer was actually changed. A zero CSS size
 * (an element not laid out yet) is left alone -- there is no useful
 * size to adopt, and a zero-sized buffer cannot be drawn into at all.
 *
 * @param {HTMLCanvasElement} canvas
 * @returns {boolean}
 */
function resizeCanvasToCssSize(canvas) {
    const cssW = canvas.clientWidth;
    const cssH = canvas.clientHeight;
    if (cssW === 0 || cssH === 0) {
        return false;
    }
    if (canvas.width === cssW && canvas.height === cssH) {
        return false;
    }
    canvas.width = cssW;
    canvas.height = cssH;
    return true;
}

/**
 * Update `canvas.width`/`canvas.height` to match the element's current
 * CSS layout size and redraw the stored panels.
 *
 * Called once at startup; resize-driven repaints are the graph
 * stage's own job (`run-graph-stage.js`'s per-pane observer wiring),
 * uniform across every pane. Ordinary draws do not depend on this
 * either way: they sync their own size via `resizeCanvasToCssSize`.
 */
function syncCanvasSize() {
    if (!resizeCanvasToCssSize(runCanvas)) {
        return;
    }
    if (_currentPanel) {
        drawScatter(runCanvas, _currentPanel);
    }
}

// Wire the initial size sync after the state scripts have declared
// `runCanvas` (scatter.js loads before them, so the `load` event is
// the earliest safe attachment point). Resize-driven repaints are the
// graph stage's own job (`run-graph-stage.js`'s per-pane
// `ResizeObserver` wiring, uniform across every pane) -- this module
// used to keep its own observer for that, before the stage existed.
window.addEventListener("load", () => {
    if (typeof runCanvas !== "undefined" && runCanvas) {
        syncCanvasSize();
    }
    // The scatter is the Run card's primary graph and is offered on
    // every state the card has, so it is available from the start; with
    // no panel drawn yet the repaint below is simply a no-op.
    if (window.fim && window.fim.registerGraphDraw) {
        window.fim.registerGraphDraw("scatter", () => {
            if (_currentPanel) {
                drawScatter(runCanvas, _currentPanel);
            }
        });
        window.fim.setGraphAvailable("scatter", true);
    }
});

// The one panel always fills the whole canvas (simplify-main-plot
// design: no small-multiples grid any more), so it gets generous room
// for tick labels and an axis title on every side.
const SINGLE_PANEL_PADDING = 44;
const SINGLE_PANEL_TICK_FONT = 10;
const TICK_LENGTH = 4;

// The reference visualization's own tick spacing
// (`Dear-NolanMarch17Final.pdf` Figs. 1-2) -- every `"frequency"` panel
// is bounded `[0, 1]` by construction, so this scale is fixed and
// identical on every one of them, never computed per-panel.
const PROBABILITY_TICK_VALUES = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0];
const COMPACT_PROBABILITY_TICK_VALUES = [0.0, 0.5, 1.0];

// An unbounded (`"pca"`) panel has no natural tick spacing -- this many
// evenly spaced ticks across the panel's own auto-scaled domain, purely
// for visual orientation ("what range of values is this"), not a claim
// about any particular meaningful value.
const AUTO_TICK_COUNT = 5;
const COMPACT_AUTO_TICK_COUNT = 3;
const COMPACT_PLOT_SIZE_THRESHOLD = 420;

// Fraction of the data's own span added as margin on every side of an
// auto-scaled (`"pca"`) domain, so the outermost points never sit
// exactly on the axis frame.
const DOMAIN_PADDING_FRACTION = 0.08;

/**
 * Draw one panel filling the whole canvas as a 0-1 by 0-1 (or, for a
 * `"pca"` panel, auto-scaled) scatter.
 *
 * @param {HTMLCanvasElement} canvas
 * @param {{x_label?: string, y_label?: string, kind?: string,
 *     points: Array<{x: number, y: number, count: number, common: boolean}>}} panel
 */
function drawScatter(canvas, panel) {
    _currentPanel = panel;
    resizeCanvasToCssSize(canvas);
    const context = canvas.getContext("2d");
    context.clearRect(0, 0, canvas.width, canvas.height);
    drawScatterCell(
        context,
        { x: 0, y: 0, width: canvas.width, height: canvas.height },
        panel,
        {
            padding: SINGLE_PANEL_PADDING,
            tickFontSize: SINGLE_PANEL_TICK_FONT,
            markerScale: 1,
        }
    );
}

/**
 * Return the axis domain for one panel's own points.
 *
 * A `"frequency"` panel (the default `kind`) is always exactly `[0, 1]`
 * on both axes, by construction. A `"pca"` panel has no such guarantee
 * -- its domain is fit to the panel's own actual point coordinates,
 * with a small margin so the outermost points are not drawn exactly on
 * the axis frame. `points.length === 0` (nothing reported yet, e.g. a
 * live batch progress tick before any replicate has reported) falls
 * back to a small, arbitrary `[-1, 1]` domain -- never divides by a
 * zero-width span.
 *
 * @param {Array<{x: number, y: number}>} points
 * @param {boolean} bounded
 */
function computeDomain(points, bounded) {
    if (bounded) {
        return { xMin: 0, xMax: 1, yMin: 0, yMax: 1 };
    }
    if (points.length === 0) {
        return { xMin: -1, xMax: 1, yMin: -1, yMax: 1 };
    }
    const xValues = points.map((point) => point.x);
    const yValues = points.map((point) => point.y);
    const rawXMin = Math.min(...xValues);
    const rawXMax = Math.max(...xValues);
    const rawYMin = Math.min(...yValues);
    const rawYMax = Math.max(...yValues);
    // `|| 1`: every point sharing one exact coordinate on an axis (a
    // degenerate zero-width span) still gets a real margin instead of a
    // zero-size domain that would divide by zero below.
    const xPad = (rawXMax - rawXMin) * DOMAIN_PADDING_FRACTION || 1;
    const yPad = (rawYMax - rawYMin) * DOMAIN_PADDING_FRACTION || 1;
    return {
        xMin: rawXMin - xPad,
        xMax: rawXMax + xPad,
        yMin: rawYMin - yPad,
        yMax: rawYMax + yPad,
    };
}

/**
 * Draw one grouped point set into an arbitrary sub-rectangle of a canvas.
 *
 * `drawScatter`'s own shared routine (one rectangle: the whole canvas)
 * -- axis frame, optional `x=y` reference diagonal, axis ticks, axis
 * titles, and the grouped points themselves, with the domain and
 * diagonal both driven by `panel.kind` (see this module's own
 * top-of-file docstring). Takes an arbitrary sub-rectangle rather than
 * assuming the whole canvas so a future multi-panel layout could still
 * reuse it, though nothing calls it that way today.
 *
 * @param {CanvasRenderingContext2D} context
 * @param {{x: number, y: number, width: number, height: number}} rect
 * @param {{x_label?: string, y_label?: string, kind?: string,
 *     points: Array<{x: number, y: number, count: number, common: boolean}>}} panel
 * @param {{padding: number, tickFontSize: number,
 *     markerScale: number}} opts
 */
function drawScatterCell(context, rect, panel, opts) {
    const bounded = panel.kind !== "pca";
    const domain = computeDomain(panel.points, bounded);
    const side = Math.min(rect.width, rect.height);
    const adaptivePadding =
        side < 520 ? Math.max(28, Math.floor(side * 0.09)) : opts.padding;
    const plotSize = side - 2 * adaptivePadding;
    const compact = plotSize < COMPACT_PLOT_SIZE_THRESHOLD;
    const originX = rect.x + adaptivePadding;
    const originY = rect.y + rect.height - adaptivePadding;

    const toCanvasX = (value) =>
        originX + ((value - domain.xMin) / (domain.xMax - domain.xMin)) * plotSize;
    const toCanvasY = (value) =>
        originY - ((value - domain.yMin) / (domain.yMax - domain.yMin)) * plotSize;

    // Axis frame -- the plot rectangle's own fixed pixel bounds, not
    // mapped through the domain: unlike a `"frequency"` panel's own
    // `[0, 1]` bounds, a `"pca"` panel's domain does not place "the top
    // of the frame" at any particular data value.
    context.strokeStyle = "#9a9a9a";
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(originX, originY);
    context.lineTo(originX, originY - plotSize);
    context.moveTo(originX, originY);
    context.lineTo(originX + plotSize, originY);
    context.stroke();

    if (bounded) {
        // The `x=y` reference diagonal: meaningful only for two demes'
        // own frequencies of the same allele, not for two principal
        // components (design §4.2's own "no meaningful diagonal" call).
        context.save();
        context.setLineDash([4, 4]);
        context.strokeStyle = "#bbbbbb";
        context.beginPath();
        context.moveTo(toCanvasX(0), toCanvasY(0));
        context.lineTo(toCanvasX(1), toCanvasY(1));
        context.stroke();
        context.restore();
    }

    drawAxisTicks(
        context,
        originX,
        originY,
        plotSize,
        opts.tickFontSize,
        domain,
        bounded,
        compact
    );

    const markerScale = opts.markerScale;
    for (const point of panel.points) {
        const cx = toCanvasX(point.x);
        const cy = toCanvasY(point.y);
        const radius =
            markerScale *
            (MARKER_BASE_RADIUS + MARKER_COUNT_SCALE * Math.sqrt(point.count));
        context.beginPath();
        context.arc(cx, cy, radius, 0, 2 * Math.PI);
        if (point.common) {
            // A hollow ring, not merely a differently colored disc
            // (`fim.viz.scatter`'s own `_scatter_on_axis` docstring):
            // color is never the only channel distinguishing "most
            // frequent" from "other" here.
            context.lineWidth = Math.max(2, radius * 0.35);
            context.strokeStyle = COLOR_COMMON;
            context.globalAlpha = 0.9;
            context.stroke();
            context.globalAlpha = 1;
        } else {
            context.fillStyle = COLOR_RARE;
            context.globalAlpha = 0.75;
            context.fill();
            context.globalAlpha = 1;
            context.strokeStyle = "#000000";
            context.lineWidth = 0.4;
            context.stroke();
        }
        if (point.count > 1 && !compact) {
            context.fillStyle = "#1a1a1a";
            context.font = `${opts.tickFontSize}px -apple-system, sans-serif`;
            context.textAlign = "left";
            context.textBaseline = "alphabetic";
            context.fillText(String(point.count), cx + radius + 2, cy - radius);
        }
    }

    // Only for a `"frequency"` panel: a `"pca"` panel disables frequency
    // highlighting entirely (a principal component is not a deme, so
    // "most frequent in either deme" names nothing there), so a color key
    // would explain a distinction that panel does not draw.
    //
    // Deliberately *not* suppressed on a compact panel, unlike tick
    // density. Fewer ticks still leave a readable plot; an unexplained
    // color does not, and a small panel is if anything where a viewer is
    // least able to infer the rule from the data.
    if (bounded) {
        drawMarkerLegend(context, originX, originY, plotSize, opts.tickFontSize);
    }
}

/**
 * Draw the color key explaining what a blue marker means.
 *
 * The canvas counterpart of `_add_marker_legend` in `fim/viz/scatter.py`,
 * and deliberately worded identically: the same plot rendered to
 * `scatter.png` and rendered on screen must not explain itself
 * differently.
 *
 * Without this, the on-screen plot drew two colors and defined neither --
 * the exact ambiguity that made the original "common allele" marker a
 * reported defect rather than merely an unclear one.
 *
 * @param {CanvasRenderingContext2D} context
 * @param {number} originX Left edge of the plot area, in canvas pixels.
 * @param {number} originY Bottom edge of the plot area, in canvas pixels.
 * @param {number} plotSize Side length of the plot area, in canvas pixels.
 * @param {number} fontSize Tick font size, reused for legend text.
 */
function drawMarkerLegend(context, originX, originY, plotSize, fontSize) {
    const lineHeight = fontSize + 4;
    // Top-left of the plot area: the `x=y` diagonal runs corner to
    // corner, so the upper-left is the region least likely to sit on top
    // of data in a bounded frequency panel.
    let y = originY - plotSize + lineHeight;

    context.save();
    context.font = `${fontSize}px -apple-system, sans-serif`;
    context.textAlign = "left";
    context.textBaseline = "middle";

    // Common: a hollow-ring swatch, matching the shape actually drawn
    // above -- not a filled disc like the "Other alleles" swatch below,
    // the same reasoning `fim.viz.scatter._add_marker_legend` uses its
    // own `Line2D` (rather than `Patch`) handle for.
    const commonRadius = fontSize * 0.35;
    const commonSwatchX = originX + 6;
    context.beginPath();
    context.arc(commonSwatchX + commonRadius, y, commonRadius, 0, 2 * Math.PI);
    context.lineWidth = Math.max(1.5, commonRadius * 0.5);
    context.strokeStyle = COLOR_COMMON;
    context.globalAlpha = 0.9;
    context.stroke();
    context.globalAlpha = 1;
    context.fillStyle = "#1a1a1a";
    context.fillText(
        "Most frequent allele in either deme (ring; ties: first)",
        commonSwatchX + 2 * commonRadius + 5,
        y
    );
    y += lineHeight;

    // Rare: an ordinary filled dot.
    const rareRadius = fontSize * 0.35;
    const rareSwatchX = originX + 6;
    context.beginPath();
    context.arc(rareSwatchX + rareRadius, y, rareRadius, 0, 2 * Math.PI);
    context.fillStyle = COLOR_RARE;
    context.globalAlpha = 0.75;
    context.fill();
    context.globalAlpha = 1;
    context.strokeStyle = "#000000";
    context.lineWidth = 0.4;
    context.stroke();
    context.fillStyle = "#1a1a1a";
    context.fillText("Other alleles", rareSwatchX + 2 * rareRadius + 5, y);

    context.restore();
}

/**
 * Format one auto-scaled tick value, with more decimal places for a
 * narrower domain -- a fixed `.toFixed(1)` (right for the `[0, 1]`
 * `"frequency"` case) would round an entire narrow-range `"pca"` axis
 * down to indistinguishable repeated values.
 *
 * @param {number} value
 * @param {number} range - `domain.xMax - domain.xMin` (or the `y` pair).
 */
function formatAutoTick(value, range) {
    if (range < 0.1) {
        return value.toFixed(3);
    }
    if (range < 10) {
        return value.toFixed(2);
    }
    return value.toFixed(1);
}

/**
 * Return nicely-rounded tick values spanning `[min, max]` -- the
 * standard "1, 2 or 5 times a power of ten" steps of scientific plots
 * (d3's own convention), so an axis reads the same way at any scale:
 * generations 0-67 tick at every 10, generations 0-10000 at every
 * 2000, a `[0, 1]` probability axis at every 0.2. Both endpoints are
 * included whenever they land on the step; a degenerate (empty or
 * zero-width) domain returns no ticks rather than dividing by zero.
 *
 * @param {number} min
 * @param {number} max
 * @param {number} targetCount - Roughly how many ticks to aim for; the
 *     step rounding means the actual count lands within about a factor
 *     of two of it.
 * @returns {number[]}
 */
function niceAxisTicks(min, max, targetCount) {
    if (!(max > min) || targetCount < 2) {
        return [];
    }
    const rawStep = (max - min) / (targetCount - 1);
    const power = Math.floor(Math.log10(rawStep));
    const base = 10 ** power;
    const error = rawStep / base;
    const step =
        error >= 7.5 ? 10 * base : error >= 3.5 ? 5 * base : error >= 1.5 ? 2 * base : base;
    // Round every tick back to the step's own decimal precision:
    // `index * step` otherwise leaves floating dust (`3 * 0.2` is
    // 0.6000000000000001), harmless to a pixel position but wrong for
    // any caller comparing tick values for equality.
    const decimals = Math.max(0, -power);
    const ticks = [];
    const start = Math.ceil(min / step - 1e-9);
    const end = Math.floor(max / step + 1e-9);
    for (let index = start; index <= end; index += 1) {
        ticks.push(Number((index * step).toFixed(decimals)));
    }
    return ticks;
}

/**
 * Draw plain tick marks and labels along one axis -- the shared
 * mechanic behind every graph's own axes, so "ticks" look and sit the
 * same on a scatter, a trajectory, and a bar plot alike.
 * `orientation: "x"` draws marks dropping below the horizontal axis
 * (labels beneath); `"y"` draws marks reaching left of the vertical
 * axis (labels left). `format` maps a tick value to its label text;
 * `null` draws marks only (a categorical axis whose own labels already
 * exist, e.g. deme numbers beneath stacked bars). Caller sets the
 * stroke/fill colors beforehand; both are preserved.
 *
 * @param {CanvasRenderingContext2D} context
 * @param {"x"|"y"} orientation
 * @param {number} axisX - The vertical axis's own x (for `"y"`), or
 *     the horizontal axis's left edge (unused for `"x"`).
 * @param {number} axisY - The horizontal axis's own y (for `"x"`), or
 *     the vertical axis's bottom edge (unused for `"y"`).
 * @param {(value: number) => number} toPixel - Tick value to pixel
 *     coordinate along the axis.
 * @param {number[]} ticks
 * @param {number} fontSize
 * @param {((value: number) => string)|null} format
 */
function drawAxisTickMarks(
    context,
    orientation,
    axisX,
    axisY,
    toPixel,
    ticks,
    fontSize,
    format
) {
    context.save();
    context.lineWidth = 1;
    context.font = `${fontSize}px -apple-system, sans-serif`;
    for (const value of ticks) {
        const position = toPixel(value);
        context.beginPath();
        if (orientation === "x") {
            context.moveTo(position, axisY);
            context.lineTo(position, axisY + TICK_LENGTH);
        } else {
            context.moveTo(axisX - TICK_LENGTH, position);
            context.lineTo(axisX, position);
        }
        context.stroke();
        if (format) {
            if (orientation === "x") {
                context.textAlign = "center";
                context.textBaseline = "top";
                context.fillText(format(value), position, axisY + TICK_LENGTH + 1);
            } else {
                context.textAlign = "right";
                context.textBaseline = "middle";
                context.fillText(format(value), axisX - TICK_LENGTH - 2, position);
            }
        }
    }
    context.restore();
}

/**
 * Draw the axis tick marks and labels -- the fixed `0.0`-`1.0`
 * probability scale for a bounded (`"frequency"`) panel, matching
 * `Dear-NolanMarch17Final.pdf` Figs. 1-2's own tick spacing exactly;
 * nicely-rounded 1/2/5-times-a-power-of-ten steps fit to the panel's
 * own domain otherwise (`niceAxisTicks`, the same convention the other
 * graphs' own numeric axes use).
 *
 * @param {CanvasRenderingContext2D} context
 * @param {number} originX
 * @param {number} originY
 * @param {number} plotSize
 * @param {number} fontSize
 * @param {{xMin: number, xMax: number, yMin: number, yMax: number}} domain
 * @param {boolean} bounded
 * @param {boolean} compact
 */
function drawAxisTicks(
    context,
    originX,
    originY,
    plotSize,
    fontSize,
    domain,
    bounded,
    compact
) {
    const targetCount = compact ? COMPACT_AUTO_TICK_COUNT : AUTO_TICK_COUNT;
    const xTicks = bounded
        ? compact
            ? COMPACT_PROBABILITY_TICK_VALUES
            : PROBABILITY_TICK_VALUES
        : niceAxisTicks(domain.xMin, domain.xMax, targetCount + 1);
    const yTicks = bounded
        ? compact
            ? COMPACT_PROBABILITY_TICK_VALUES
            : PROBABILITY_TICK_VALUES
        : niceAxisTicks(domain.yMin, domain.yMax, targetCount + 1);
    const xRange = domain.xMax - domain.xMin;
    const yRange = domain.yMax - domain.yMin;
    const formatX = (value) => (bounded ? value.toFixed(1) : formatAutoTick(value, xRange));
    const formatY = (value) => (bounded ? value.toFixed(1) : formatAutoTick(value, yRange));
    const toPixelX = (value) => originX + ((value - domain.xMin) / xRange) * plotSize;
    const toPixelY = (value) => originY - ((value - domain.yMin) / yRange) * plotSize;

    context.save();
    context.strokeStyle = "#9a9a9a";
    context.fillStyle = "#6b6b6b";
    drawAxisTickMarks(context, "x", originX, originY, toPixelX, xTicks, fontSize, formatX);
    drawAxisTickMarks(context, "y", originX, originY, toPixelY, yTicks, fontSize, formatY);
    context.restore();
}
