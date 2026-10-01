"use strict";

/* Heat-map renderer, shared by every view that shows one number over two
 * parameters (`20260923-claude-sonnet-5-sweep-as-study-implementation-
 * plan.md`, `selby/restricted`, §7.4): the sweep results card today,
 * Explore's surface mode next. It knows nothing about sweeps or Explore.
 *
 * - One rectangle per cell, never smoothed: an integer axis such as the
 *   number of demes has no value between 8 and 9, and a blur would claim
 *   one.
 * - A sequential ramp (ColorBrewer YlGnBu, the family the scatter plot
 *   uses) for a value, or a diverging blue-white-orange ramp for a
 *   difference centred on zero. Both survive color-blindness, and the
 *   exact value is always available on hover and in the table beside the
 *   map, so color is never the only channel.
 * - A cell with no value (not yet run, or undefined) is hatched, not a
 *   color from the ramp.
 * - `drawHeatmap` returns the layout it drew, and `heatmapCellAt` turns a
 *   pointer position back into a cell, so callers own hover, click and
 *   keyboard behavior.
 */

/**
 * Parse `#rrggbb` into `[r, g, b]`.
 * @param {string} hex
 * @returns {number[]}
 */
function heatmapRgb(hex) {
    return [1, 3, 5].map((index) => parseInt(hex.slice(index, index + 2), 16));
}

/**
 * The color for `fraction` (0 to 1) along `ramp`, interpolated.
 * @param {number} fraction
 * @param {string[]} ramp
 * @returns {string}
 */
function heatmapRampColor(fraction, ramp) {
    const clamped = Math.min(1, Math.max(0, fraction));
    const position = clamped * (ramp.length - 1);
    const lower = Math.floor(position);
    const upper = Math.min(lower + 1, ramp.length - 1);
    const weight = position - lower;
    const from = heatmapRgb(ramp[lower]);
    const to = heatmapRgb(ramp[upper]);
    const mixed = from.map((channel, index) =>
        Math.round(channel + (to[index] - channel) * weight)
    );
    return `rgb(${mixed[0]}, ${mixed[1]}, ${mixed[2]})`;
}

/**
 * Format a tick or value compactly.
 * @param {number} value
 * @returns {string}
 */
function heatmapFormat(value) {
    if (value === 0) {
        return "0";
    }
    const magnitude = Math.abs(value);
    const digits = HEATMAP_FORMAT_SIGNIFICANT_DIGITS;
    const exponential =
        magnitude >= HEATMAP_FORMAT_EXPONENT_HIGH || magnitude < HEATMAP_FORMAT_EXPONENT_LOW;
    if (exponential) {
        return Number(value.toPrecision(digits)).toExponential().replace("e+", "e");
    }
    return String(Number(value.toPrecision(digits)));
}

/**
 * Draw a heat map on `canvas` and return the layout for hit testing.
 *
 * @param {HTMLCanvasElement} canvas Its `width`/`height` are the pixel size to draw at.
 * @param {{
 *   columns: string[], rows: string[],
 *   values: Array<Array<number|null>>,
 *   min: number, max: number, diverging?: boolean,
 *   xTitle?: string, yTitle?: string, valueTitle?: string,
 *   selected?: {row: number, column: number}|null,
 *   markers?: Array<{row: number, column: number}>
 * }} spec `values[row][column]`; `null` is a cell with no value.
 * `markers` are drawn as dots at fractional cell positions (a whole
 * number is a cell's left/top edge; add 0.5 for its centre), for example
 * the points a sweep would run.
 * @returns {{left: number, top: number, cellWidth: number, cellHeight: number,
 *   rows: number, columns: number}}
 */
function drawHeatmap(canvas, spec) {
    const context = canvas.getContext("2d");
    const style = getComputedStyle(document.documentElement);
    const muted = style.getPropertyValue("--fim-muted").trim() || THEME_FALLBACK_MUTED;
    const border = style.getPropertyValue("--fim-border").trim() || THEME_FALLBACK_BORDER;
    const foreground =
        style.getPropertyValue("--fim-foreground").trim() || THEME_FALLBACK_FOREGROUND;
    context.clearRect(0, 0, canvas.width, canvas.height);

    const left = HEATMAP_MARGIN.left;
    const top = HEATMAP_MARGIN.top;
    const plotWidth = Math.max(canvas.width - left - HEATMAP_MARGIN.right, HEATMAP_MIN_PLOT_PX);
    const plotHeight = Math.max(canvas.height - top - HEATMAP_MARGIN.bottom, HEATMAP_MIN_PLOT_PX);
    const columns = spec.columns.length;
    const rows = spec.rows.length;
    const cellWidth = plotWidth / columns;
    const cellHeight = plotHeight / rows;
    const ramp = spec.diverging ? HEATMAP_DIVERGING : HEATMAP_SEQUENTIAL;
    const span = spec.max - spec.min;

    context.font = FONT_AXIS_LARGE;
    for (let row = 0; row < rows; row += 1) {
        for (let column = 0; column < columns; column += 1) {
            const x = left + column * cellWidth;
            const y = top + row * cellHeight;
            const value = spec.values[row][column];
            if (value === null || value === undefined || Number.isNaN(value)) {
                drawHeatmapHatch(context, x, y, cellWidth, cellHeight, border);
            } else {
                const fraction = span > 0 ? (value - spec.min) / span : 0.5;
                context.fillStyle = heatmapRampColor(fraction, ramp);
                context.fillRect(x, y, cellWidth, cellHeight);
            }
        }
    }
    context.strokeStyle = border;
    context.lineWidth = AXIS_LINE_WIDTH;
    context.strokeRect(left, top, plotWidth, plotHeight);

    context.fillStyle = muted;
    context.textAlign = "center";
    const columnStride = Math.max(
        1,
        Math.ceil(HEATMAP_MIN_LABEL_SPACING_PX / Math.max(cellWidth, 1))
    );
    spec.columns.forEach((label, column) => {
        if (column % columnStride === 0) {
            context.fillText(
                label,
                left + (column + 0.5) * cellWidth,
                top + plotHeight + HEATMAP_COLUMN_LABEL_DROP
            );
        }
    });
    context.textAlign = "right";
    const rowStride = Math.max(
        1,
        Math.ceil(HEATMAP_MIN_ROW_LABEL_SPACING_PX / Math.max(cellHeight, 1))
    );
    spec.rows.forEach((label, row) => {
        if (row % rowStride === 0) {
            context.fillText(
                label,
                left - HEATMAP_ROW_LABEL_GAP,
                top + (row + 0.5) * cellHeight + HEATMAP_ROW_LABEL_BASELINE
            );
        }
    });

    context.fillStyle = foreground;
    context.textAlign = "center";
    if (spec.xTitle) {
        context.fillText(
            spec.xTitle,
            left + plotWidth / 2,
            canvas.height - HEATMAP_X_TITLE_RISE
        );
    }
    if (spec.yTitle) {
        context.save();
        context.translate(HEATMAP_Y_TITLE_INSET, top + plotHeight / 2);
        context.rotate(-QUARTER_TURN);
        context.fillText(spec.yTitle, 0, 0);
        context.restore();
    }

    drawHeatmapColorBar(context, canvas, spec, ramp, { top, plotHeight, muted, border });

    for (const marker of spec.markers || []) {
        context.beginPath();
        context.arc(
            left + marker.column * cellWidth,
            top + marker.row * cellHeight,
            HEATMAP_MARKER_RADIUS,
            0,
            TAU
        );
        context.fillStyle = HEATMAP_MARKER_FILL;
        context.fill();
        context.strokeStyle = HEATMAP_MARKER_STROKE;
        context.lineWidth = HEATMAP_MARKER_LINE_WIDTH;
        context.stroke();
    }

    if (spec.selected) {
        context.strokeStyle = foreground;
        context.lineWidth = HEATMAP_SELECTED_LINE_WIDTH;
        context.strokeRect(
            left + spec.selected.column * cellWidth + HEATMAP_SELECTED_INSET,
            top + spec.selected.row * cellHeight + HEATMAP_SELECTED_INSET,
            cellWidth - 2 * HEATMAP_SELECTED_INSET,
            cellHeight - 2 * HEATMAP_SELECTED_INSET
        );
    }
    return { left, top, cellWidth, cellHeight, rows, columns };
}

/**
 * Hatch one cell that has no value.
 * @param {CanvasRenderingContext2D} context
 * @param {number} x
 * @param {number} y
 * @param {number} width
 * @param {number} height
 * @param {string} color
 */
function drawHeatmapHatch(context, x, y, width, height, color) {
    context.save();
    context.beginPath();
    context.rect(x, y, width, height);
    context.clip();
    context.strokeStyle = color;
    context.lineWidth = AXIS_LINE_WIDTH;
    for (let offset = -height; offset < width; offset += HEATMAP_HATCH_STEP) {
        context.beginPath();
        context.moveTo(x + offset, y + height);
        context.lineTo(x + offset + height, y);
        context.stroke();
    }
    context.restore();
}

/**
 * Draw the color bar and its end labels to the right of the plot.
 * @param {CanvasRenderingContext2D} context
 * @param {HTMLCanvasElement} canvas
 * @param {{min: number, max: number, valueTitle?: string}} spec
 * @param {string[]} ramp
 * @param {{top: number, plotHeight: number, muted: string, border: string}} frame
 */
function drawHeatmapColorBar(context, canvas, spec, ramp, frame) {
    const barLeft = canvas.width - HEATMAP_MARGIN.right + HEATMAP_COLORBAR_GAP;
    const barWidth = HEATMAP_COLORBAR_WIDTH;
    for (let step = 0; step < frame.plotHeight; step += 1) {
        context.fillStyle = heatmapRampColor(1 - step / frame.plotHeight, ramp);
        context.fillRect(barLeft, frame.top + step, barWidth, 1);
    }
    context.strokeStyle = frame.border;
    context.strokeRect(barLeft, frame.top, barWidth, frame.plotHeight);
    context.fillStyle = frame.muted;
    context.textAlign = "left";
    const labelLeft = barLeft + barWidth + HEATMAP_COLORBAR_LABEL_GAP;
    context.fillText(
        heatmapFormat(spec.max),
        labelLeft,
        frame.top + HEATMAP_COLORBAR_TOP_LABEL_DROP
    );
    context.fillText(heatmapFormat(spec.min), labelLeft, frame.top + frame.plotHeight);
    if (spec.valueTitle) {
        context.fillText(
            spec.valueTitle,
            barLeft,
            frame.top - HEATMAP_COLORBAR_TITLE_RISE
        );
    }
}

/**
 * The cell under a pointer position, or `null` outside the plot.
 * @param {HTMLCanvasElement} canvas
 * @param {{left: number, top: number, cellWidth: number, cellHeight: number,
 *   rows: number, columns: number}} layout
 * @param {number} clientX
 * @param {number} clientY
 * @returns {{row: number, column: number}|null}
 */
function heatmapCellAt(canvas, layout, clientX, clientY) {
    const box = canvas.getBoundingClientRect();
    const x = ((clientX - box.left) * canvas.width) / Math.max(box.width, 1);
    const y = ((clientY - box.top) * canvas.height) / Math.max(box.height, 1);
    const column = Math.floor((x - layout.left) / layout.cellWidth);
    const row = Math.floor((y - layout.top) / layout.cellHeight);
    if (column < 0 || row < 0 || column >= layout.columns || row >= layout.rows) {
        return null;
    }
    return { row, column };
}

window.fim.heatmap = { draw: drawHeatmap, cellAt: heatmapCellAt, format: heatmapFormat };
