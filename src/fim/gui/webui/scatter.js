"use strict";

/* Dependency-free Canvas 2D scatter renderer (design doc §3.5, §3.10;
 * extended by `20260822-claude-sonnet-5-visualization-and-config-
 * editors-design.md` §3.1-§3.2).
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
 * tick marks on both axes (visualization-and-config-editors design §3.2).
 *
 * The bridge always ships already-grouped `{x, y, count, common}`
 * points (never raw ungrouped coordinates, and never anything above
 * two dimensions -- any PCA projection or pairwise-pair selection a
 * high deme count needs happens server-side, per design §3.5's "the
 * client never does linear algebra" rule), so this module only ever
 * draws points; it holds no model of what `d` means.
 *
 * `drawScatter` (one panel filling the whole canvas) and `drawScatterGrid`
 * (several panels as small multiples, one canvas partitioned into a
 * grid -- visualization-and-config-editors design §3.1, the same
 * `columns = min(3, count)` layout `fim.viz.scatter._plot_pairwise`
 * already uses server-side for the CLI's own `scatter.png`) both share
 * one per-cell drawing routine, `drawScatterCell`, so the two never
 * drift into two different visual encodings of the same data.
 */

const MARKER_BASE_RADIUS = 3;
const MARKER_COUNT_SCALE = 1.6;
const COLOR_COMMON = "#1f6fb2";
const COLOR_RARE = "#d97a26";

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
const SINGLE_PANEL_LABEL_FONT = 11;
const GRID_CELL_LABEL_FONT = 9;
const GRID_CELL_MARKER_SCALE = 0.6;
const GRID_COLUMNS_MAX = 3;
const TICK_LENGTH = 4;

// The reference visualization's own tick spacing
// (`Dear-NolanMarch17Final.pdf` Figs. 1-2) -- every quantity plotted here
// is a frequency, bounded `[0, 1]` by construction, so this scale is
// fixed and identical on every panel, never computed per-panel.
const TICK_VALUES = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0];

/**
 * Draw one grouped point set filling the whole canvas as a 0-1 by 0-1 scatter.
 *
 * @param {HTMLCanvasElement} canvas
 * @param {Array<{x: number, y: number, count: number, common: boolean}>} points
 * @param {{xLabel?: string, yLabel?: string}} [options]
 */
function drawScatter(canvas, points, options) {
    const context = canvas.getContext("2d");
    context.clearRect(0, 0, canvas.width, canvas.height);
    drawScatterCell(
        context,
        { x: 0, y: 0, width: canvas.width, height: canvas.height },
        points,
        Object.assign(
            {
                padding: SINGLE_PANEL_PADDING,
                tickFontSize: SINGLE_PANEL_TICK_FONT,
                labelFontSize: SINGLE_PANEL_LABEL_FONT,
                markerScale: 1,
            },
            options || {}
        )
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
 * @param {Array<{x_label: string, y_label: string,
 *     points: Array<{x: number, y: number, count: number, common: boolean}>}>} panels
 */
function drawScatterGrid(canvas, panels) {
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
            panel.points,
            {
                xLabel: panel.x_label,
                yLabel: panel.y_label,
                padding: GRID_CELL_PADDING,
                tickFontSize: GRID_CELL_TICK_FONT,
                labelFontSize: GRID_CELL_LABEL_FONT,
                markerScale: GRID_CELL_MARKER_SCALE,
            }
        );
    });
}

/**
 * Draw one grouped point set into an arbitrary sub-rectangle of a canvas.
 *
 * The shared routine `drawScatter` (one rectangle: the whole canvas) and
 * `drawScatterGrid` (one rectangle per panel) both call -- axis frame,
 * `x=y` reference diagonal, probability-scale ticks, axis titles, and
 * the grouped points themselves, identically either way.
 *
 * @param {CanvasRenderingContext2D} context
 * @param {{x: number, y: number, width: number, height: number}} rect
 * @param {Array<{x: number, y: number, count: number, common: boolean}>} points
 * @param {{xLabel?: string, yLabel?: string, padding: number,
 *     tickFontSize: number, labelFontSize: number, markerScale: number}} opts
 */
function drawScatterCell(context, rect, points, opts) {
    const plotSize = Math.min(rect.width, rect.height) - 2 * opts.padding;
    const originX = rect.x + opts.padding;
    const originY = rect.y + rect.height - opts.padding;

    const toCanvasX = (value) => originX + value * plotSize;
    const toCanvasY = (value) => originY - value * plotSize;

    // Axis frame and the x=y reference diagonal (matches
    // `_scatter_on_axis`'s own `reference=True` default).
    context.strokeStyle = "#9a9a9a";
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(originX, originY);
    context.lineTo(originX, toCanvasY(1));
    context.moveTo(originX, originY);
    context.lineTo(toCanvasX(1), originY);
    context.stroke();

    context.save();
    context.setLineDash([4, 4]);
    context.strokeStyle = "#bbbbbb";
    context.beginPath();
    context.moveTo(toCanvasX(0), toCanvasY(0));
    context.lineTo(toCanvasX(1), toCanvasY(1));
    context.stroke();
    context.restore();

    drawProbabilityScale(context, originX, originY, plotSize, opts.tickFontSize);

    context.fillStyle = "#6b6b6b";
    context.font = `${opts.labelFontSize}px -apple-system, sans-serif`;
    context.textAlign = "center";
    context.textBaseline = "alphabetic";
    context.fillText(
        opts.xLabel || "Deme 1",
        originX + plotSize / 2,
        rect.y + rect.height - 4
    );
    context.save();
    context.translate(rect.x + opts.labelFontSize + 2, originY - plotSize / 2);
    context.rotate(-Math.PI / 2);
    context.fillText(opts.yLabel || "Deme 2", 0, 0);
    context.restore();

    const markerScale = opts.markerScale;
    for (const point of points) {
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
 * Draw the fixed `0.0`-`1.0` probability-scale tick marks on both axes.
 *
 * Matches `Dear-NolanMarch17Final.pdf` Figs. 1-2's own tick spacing
 * (visualization-and-config-editors design §3.2) -- every axis here is a
 * frequency, so the same six ticks apply to every panel, on both axes,
 * unconditionally.
 *
 * @param {CanvasRenderingContext2D} context
 * @param {number} originX
 * @param {number} originY
 * @param {number} plotSize
 * @param {number} fontSize
 */
function drawProbabilityScale(context, originX, originY, plotSize, fontSize) {
    context.save();
    context.strokeStyle = "#9a9a9a";
    context.fillStyle = "#6b6b6b";
    context.font = `${fontSize}px -apple-system, sans-serif`;
    context.lineWidth = 1;
    for (const value of TICK_VALUES) {
        const x = originX + value * plotSize;
        context.beginPath();
        context.moveTo(x, originY);
        context.lineTo(x, originY + TICK_LENGTH);
        context.stroke();
        context.textAlign = "center";
        context.textBaseline = "top";
        context.fillText(value.toFixed(1), x, originY + TICK_LENGTH + 1);

        const y = originY - value * plotSize;
        context.beginPath();
        context.moveTo(originX - TICK_LENGTH, y);
        context.lineTo(originX, y);
        context.stroke();
        context.textAlign = "right";
        context.textBaseline = "middle";
        context.fillText(value.toFixed(1), originX - TICK_LENGTH - 2, y);
    }
    context.restore();
}
