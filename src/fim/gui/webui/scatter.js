"use strict";

/* Dependency-free Canvas 2D scatter renderer (design doc §3.5, §3.10).
 *
 * Draws exactly what `fim.viz.scatter.marker_groups` already computes:
 * one point per unique (x, y) coordinate, sized by how many source rows
 * landed on it (`30 + 18*sqrt(count)`, matching the CLI's own
 * `plot_frequency_scatter` marker-size formula so the GUI's live view
 * and the CLI's saved `scatter.png` read the same way), colored
 * `tab:blue`/`tab:orange` by the same common-allele threshold, and
 * labeled with its own coincidence count once it exceeds one -- the
 * exact visual encoding the reference visualization
 * (`Dear-NolanMarch17Final.pdf` Figs. 1-2) uses (design §0.5).
 *
 * The bridge always ships already-grouped `{x, y, count, common}`
 * points (never raw ungrouped coordinates, and never anything above
 * two dimensions -- any PCA projection or pairwise-pair selection a
 * high deme count needs happens server-side, per design §3.5's "the
 * client never does linear algebra" rule), so this module only ever
 * draws points; it holds no model of what `d` means.
 */

const MARKER_BASE_RADIUS = 3;
const MARKER_COUNT_SCALE = 1.6;
const COLOR_COMMON = "#1f6fb2";
const COLOR_RARE = "#d97a26";
const PADDING = 36;

/**
 * Draw one grouped point set onto a canvas as a 0-1 by 0-1 scatter.
 *
 * @param {HTMLCanvasElement} canvas
 * @param {Array<{x: number, y: number, count: number, common: boolean}>} points
 * @param {{xLabel?: string, yLabel?: string}} [options]
 */
function drawScatter(canvas, points, options) {
    const opts = options || {};
    const context = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    const plotSize = Math.min(width, height) - 2 * PADDING;
    const originX = PADDING;
    const originY = height - PADDING;

    context.clearRect(0, 0, width, height);

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

    context.fillStyle = "#6b6b6b";
    context.font = "11px -apple-system, sans-serif";
    context.textAlign = "center";
    context.fillText(opts.xLabel || "Deme 1", originX + plotSize / 2, height - 8);
    context.save();
    context.translate(12, originY - plotSize / 2);
    context.rotate(-Math.PI / 2);
    context.fillText(opts.yLabel || "Deme 2", 0, 0);
    context.restore();

    for (const point of points) {
        const cx = toCanvasX(point.x);
        const cy = toCanvasY(point.y);
        const radius = MARKER_BASE_RADIUS + MARKER_COUNT_SCALE * Math.sqrt(point.count);
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
            context.font = "10px -apple-system, sans-serif";
            context.textAlign = "left";
            context.fillText(String(point.count), cx + radius + 2, cy - radius);
        }
    }
}
