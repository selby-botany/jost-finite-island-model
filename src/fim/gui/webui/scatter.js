"use strict";

/* Dependency-free Canvas 2D scatter renderer (design doc §3.5, §3.10;
 * extended by `20260822-claude-sonnet-5-visualization-and-config-
 * editors-design.md` §3.1-§3.2, and again by the axis-domain fix below).
 *
 * Draws exactly what `fim.viz.scatter.marker_groups` already computes:
 * one point per unique (x, y) coordinate, sized by how many source rows
 * landed on it (`30 + 18*sqrt(count)`, matching the CLI's own
 * `plot_frequency_scatter` marker-size formula so the GUI's live view
 * and the CLI's saved `scatter.png` read the same way), colored
 * `tab:blue`/`tab:orange` by the same common-allele threshold, and
 * labeled with its own coincidence count once it exceeds one -- the
 * exact visual encoding the reference visualization
 * (`Dear-NolanMarch17Final.pdf` Figs. 1-2) uses (design §0.5), now
 * including that same reference's own `0.0`-`1.0` probability-scale
 * tick marks on both axes for a genuine deme-frequency panel.
 *
 * `panel.kind` (`fim.viz.scatter._panel`'s own field) decides the axis
 * domain: `"frequency"` (the default) is bounded `[0, 1]` by
 * construction, gets the fixed probability-scale ticks and the `x=y`
 * reference diagonal; `"pca"` is an unbounded principal-component
 * projection -- real values are routinely negative or outside `[0, 1]`
 * entirely, so it gets an auto-scaled domain fit to the panel's own
 * points instead, numeric (not probability-labeled) ticks, and no
 * diagonal (two different principal components have no "equal" relation
 * the way two demes' frequencies of the same allele do). Drawing every
 * panel with the same fixed `[0, 1]` domain regardless of `kind` was a
 * real, reported bug: real PCA points rendered as if they carried
 * "negative probability," because they were mapped through a domain
 * that never applied to them in the first place.
 *
 * The bridge always ships already-grouped `{x, y, count, common}`
 * points (never raw ungrouped coordinates -- any PCA projection or
 * pairwise-pair selection a high deme count needs happens server-side,
 * per design §3.5's "the client never does linear algebra" rule), so
 * this module only ever draws points; it holds no model of what `d`
 * means beyond the `kind` discriminator above.
 */

const MARKER_BASE_RADIUS = 3;
const MARKER_COUNT_SCALE = 1.6;
const COLOR_COMMON = "#1f6fb2";
const COLOR_RARE = "#d97a26";

// The panels most recently drawn to `runCanvas`. `syncCanvasSize`
// reads these to redraw after a resize without a second bridge call.
let _currentPanels = null;

/**
 * Update `canvas.width`/`canvas.height` to match the element's current
 * CSS layout size and redraw the stored panels.
 *
 * Canvas HTML attributes define the drawing-buffer resolution
 * independently of the CSS layout size.  Keeping them in sync ensures
 * drawings are never stretched or clipped as the window resizes.
 * Called once at startup and then by the ResizeObserver below.
 */
function syncCanvasSize() {
    const cssW = runCanvas.clientWidth;
    const cssH = runCanvas.clientHeight;
    if (cssW === 0 || cssH === 0) {
        return;
    }
    runCanvas.width = cssW;
    runCanvas.height = cssH;
    if (_currentPanels && _currentPanels.length > 0) {
        if (_currentPanels.length === 1) {
            drawScatter(runCanvas, _currentPanels[0]);
        } else {
            drawScatterGrid(runCanvas, _currentPanels);
        }
    }
}

// Wire the ResizeObserver after the state scripts have declared
// `runCanvas` (scatter.js loads before them, so the `load` event is
// the earliest safe attachment point).
window.addEventListener("load", () => {
    if (typeof runCanvas !== "undefined" && runCanvas) {
        syncCanvasSize();
        new ResizeObserver(syncCanvasSize).observe(runCanvas);
    }
});

// A single panel filling the whole canvas gets generous room for tick
// labels and an axis title on every side; a small-multiples grid cell
// (`drawScatterGrid`) is a fraction of that size, so its own padding and
// font sizes shrink to match -- proportions chosen so a `3 <= d <= 6`
// grid's tick labels stay legible without crowding out the points
// themselves (visualization-and-config-editors design §6's own
// "confirm coincidence-count labels and tick-mark text stay legible at
// that size" risk item -- this is the deliberately generous starting
// point that risk item asks to verify against a real display).
const SINGLE_PANEL_PADDING = 44;
const GRID_CELL_PADDING = 32;
const SINGLE_PANEL_TICK_FONT = 10;
const GRID_CELL_TICK_FONT = 8;
const GRID_CELL_MARKER_SCALE = 0.6;
const GRID_COLUMNS_MAX = 3;
const TICK_LENGTH = 4;

// The reference visualization's own tick spacing
// (`Dear-NolanMarch17Final.pdf` Figs. 1-2) -- every `"frequency"` panel
// is bounded `[0, 1]` by construction, so this scale is fixed and
// identical on every one of them, never computed per-panel.
const PROBABILITY_TICK_VALUES = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0];

// An unbounded (`"pca"`) panel has no natural tick spacing -- this many
// evenly spaced ticks across the panel's own auto-scaled domain, purely
// for visual orientation ("what range of values is this"), not a claim
// about any particular meaningful value.
const AUTO_TICK_COUNT = 5;

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
    _currentPanels = [panel];
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
 * Draw several panels onto one canvas as a small-multiples grid.
 *
 * The same `columns = min(3, count)`, `rows = ceil(count / columns)`
 * layout `fim.viz.scatter._plot_pairwise` already computes server-side
 * (visualization-and-config-editors design §3.1) -- reused here as the
 * client layout too, rather than invented separately.
 *
 * @param {HTMLCanvasElement} canvas
 * @param {Array<{x_label?: string, y_label?: string, kind?: string,
 *     points: Array<{x: number, y: number, count: number, common: boolean}>}>} panels
 */
function drawScatterGrid(canvas, panels) {
    _currentPanels = panels;
    const context = canvas.getContext("2d");
    context.clearRect(0, 0, canvas.width, canvas.height);
    const columns = Math.min(GRID_COLUMNS_MAX, panels.length);
    const rows = Math.ceil(panels.length / columns);
    const cellWidth = canvas.width / columns;
    const cellHeight = canvas.height / rows;
    panels.forEach((panel, index) => {
        const column = index % columns;
        const row = Math.floor(index / columns);
        drawScatterCell(
            context,
            {
                x: column * cellWidth,
                y: row * cellHeight,
                width: cellWidth,
                height: cellHeight,
            },
            panel,
            {
                padding: GRID_CELL_PADDING,
                tickFontSize: GRID_CELL_TICK_FONT,
                markerScale: GRID_CELL_MARKER_SCALE,
            }
        );
    });
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
 * The shared routine `drawScatter` (one rectangle: the whole canvas) and
 * `drawScatterGrid` (one rectangle per panel) both call -- axis frame,
 * optional `x=y` reference diagonal, axis ticks, axis titles, and the
 * grouped points themselves, identically either way, with the domain
 * and diagonal both driven by `panel.kind` (see this module's own
 * top-of-file docstring).
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
    const plotSize = Math.min(rect.width, rect.height) - 2 * opts.padding;
    const originX = rect.x + opts.padding;
    const originY = rect.y + rect.height - opts.padding;

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

    drawAxisTicks(context, originX, originY, plotSize, opts.tickFontSize, domain, bounded);

    const markerScale = opts.markerScale;
    for (const point of panel.points) {
        const cx = toCanvasX(point.x);
        const cy = toCanvasY(point.y);
        const radius =
            markerScale *
            (MARKER_BASE_RADIUS + MARKER_COUNT_SCALE * Math.sqrt(point.count));
        context.beginPath();
        context.arc(cx, cy, radius, 0, 2 * Math.PI);
        context.fillStyle = point.common ? COLOR_COMMON : COLOR_RARE;
        context.globalAlpha = 0.75;
        context.fill();
        context.globalAlpha = 1;
        context.strokeStyle = "#000000";
        context.lineWidth = 0.4;
        context.stroke();
        if (point.count > 1) {
            context.fillStyle = "#1a1a1a";
            context.font = `${opts.tickFontSize}px -apple-system, sans-serif`;
            context.textAlign = "left";
            context.textBaseline = "alphabetic";
            context.fillText(String(point.count), cx + radius + 2, cy - radius);
        }
    }
}

/**
 * Return `count` evenly spaced values across `[min, max]`, inclusive of
 * both ends. Not "nice round numbers" -- purely even spacing across an
 * auto-scaled (`"pca"`) domain, matching what `computeDomain` itself
 * already produces from the panel's own real data.
 *
 * @param {number} min
 * @param {number} max
 * @param {number} count
 */
function evenlySpacedTicks(min, max, count) {
    const step = (max - min) / (count - 1);
    const ticks = [];
    for (let index = 0; index < count; index += 1) {
        ticks.push(min + step * index);
    }
    return ticks;
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
 * Draw the axis tick marks and labels -- the fixed `0.0`-`1.0`
 * probability scale for a bounded (`"frequency"`) panel, matching
 * `Dear-NolanMarch17Final.pdf` Figs. 1-2's own tick spacing exactly;
 * an auto-scaled numeric scale fit to the panel's own domain otherwise.
 *
 * @param {CanvasRenderingContext2D} context
 * @param {number} originX
 * @param {number} originY
 * @param {number} plotSize
 * @param {number} fontSize
 * @param {{xMin: number, xMax: number, yMin: number, yMax: number}} domain
 * @param {boolean} bounded
 */
function drawAxisTicks(context, originX, originY, plotSize, fontSize, domain, bounded) {
    const xTicks = bounded
        ? PROBABILITY_TICK_VALUES
        : evenlySpacedTicks(domain.xMin, domain.xMax, AUTO_TICK_COUNT);
    const yTicks = bounded
        ? PROBABILITY_TICK_VALUES
        : evenlySpacedTicks(domain.yMin, domain.yMax, AUTO_TICK_COUNT);
    const xRange = domain.xMax - domain.xMin;
    const yRange = domain.yMax - domain.yMin;
    const formatX = (value) => (bounded ? value.toFixed(1) : formatAutoTick(value, xRange));
    const formatY = (value) => (bounded ? value.toFixed(1) : formatAutoTick(value, yRange));

    context.save();
    context.strokeStyle = "#9a9a9a";
    context.fillStyle = "#6b6b6b";
    context.font = `${fontSize}px -apple-system, sans-serif`;
    context.lineWidth = 1;
    for (const value of xTicks) {
        const x = originX + ((value - domain.xMin) / xRange) * plotSize;
        context.beginPath();
        context.moveTo(x, originY);
        context.lineTo(x, originY + TICK_LENGTH);
        context.stroke();
        context.textAlign = "center";
        context.textBaseline = "top";
        context.fillText(formatX(value), x, originY + TICK_LENGTH + 1);
    }
    for (const value of yTicks) {
        const y = originY - ((value - domain.yMin) / yRange) * plotSize;
        context.beginPath();
        context.moveTo(originX - TICK_LENGTH, y);
        context.lineTo(originX, y);
        context.stroke();
        context.textAlign = "right";
        context.textBaseline = "middle";
        context.fillText(formatY(value), originX - TICK_LENGTH - 2, y);
    }
    context.restore();
}
