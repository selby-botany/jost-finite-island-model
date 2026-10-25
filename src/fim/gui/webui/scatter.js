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
 * either displayed deme draws as a solid, bright-red dot, the same size
 * and shape as any other point -- other alleles stay ordinary filled
 * `tab:orange` dots.
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

/* How the points are drawn (design `20260923-claude-sonnet-5-multi-graph-
 * run-card-and-scatter-encoding-design.md` Part B). Alleles at exactly the
 * same coordinates arrive merged into one point with a `count`; the
 * original encoding made the marker's radius grow with that count, which
 * in a pooled batch lets the pile of alleles absent from both demes at
 * (0, 0) -- the least informative point -- become the largest mark on the
 * plot. Every alternative is offered (Settings, "Scatter plot points")
 * so the botanist can choose by looking:
 *
 *   circles      the original: radius grows with count.
 *   color        fixed-size marks; count is a sequential colour ramp in
 *                five log-spaced steps, with a key.
 *   badge        original circles, but the (0, 0) pile is drawn as a
 *                small labelled badge instead of a circle.
 *   color-badge  both of the above (the default).
 *   density      a square-binned density map; positions are binned.
 *   dots         one small translucent dot per point whose opacity
 *                accumulates with count (overlap darkens, and saturates).
 *   trail        the original circles, with the last few scrubber frames
 *                faded in behind them so movement is visible.
 */
function getScatterState(tabId = window.fim.workspace.selectedId()) {
    const documentState = window.fim.workspace.get(tabId);
    if (!documentState) {
        return null;
    }
    if (!documentState.scatter) {
        documentState.scatter = {
            style: DEFAULT_SCATTER_STYLE,
            currentPanel: null,
            zoomDomain: null,
            lastGeometry: null,
            dragStart: null,
        };
    }
    return documentState.scatter;
}

let activeScatterState = getScatterState();

/**
 * Return the ramp step (0-4) for a merged point's count.
 *
 * @param {number} count
 * @returns {number}
 */
function countStep(count) {
    return Math.min(COUNT_RAMP.length - 1, Math.floor(Math.log2(Math.max(1, count))));
}

/**
 * Choose how the scatter plot draws its points, and redraw now.
 *
 * @param {string} style one of `SCATTER_STYLES`; anything else is ignored.
 */
window.fim.setScatterStyle = function setScatterStyle(
    style,
    tabId = window.fim.workspace.selectedId()
) {
    if (!SCATTER_STYLES.includes(style)) {
        return;
    }
    const state = getScatterState(tabId);
    if (!state) {
        return;
    }
    state.style = style;
    if (
        tabId === window.fim.workspace.selectedId() &&
        state.currentPanel &&
        typeof runCanvas !== "undefined" &&
        runCanvas
    ) {
        drawScatter(runCanvas, state.currentPanel);
    }
};

/**
 * Report the current style, for tests and callers that need to ask.
 *
 * @returns {string}
 */
window.fim.getScatterStyle = function getScatterStyle() {
    return activeScatterState
        ? activeScatterState.style
        : DEFAULT_SCATTER_STYLE;
};

window.fim.getScatterState = function getScatterStateForSelectedDocument() {
    return activeScatterState
        ? {
              style: activeScatterState.style,
              hasZoom: activeScatterState.zoomDomain !== null,
              panel: activeScatterState.currentPanel,
          }
        : null;
};

window.fim.clearScatterPanel = function clearScatterPanel() {
    if (!activeScatterState) {
        return;
    }
    activeScatterState.currentPanel = null;
    activeScatterState.lastGeometry = null;
};

window.fim.workspace.onSelectionChange((selectedId) => {
    activeScatterState =
        selectedId === null ? null : getScatterState(selectedId);
    if (
        activeScatterState &&
        activeScatterState.currentPanel &&
        typeof runCanvas !== "undefined" &&
        runCanvas
    ) {
        drawScatter(runCanvas, activeScatterState.currentPanel);
    }
});

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
    if (activeScatterState && activeScatterState.currentPanel) {
        drawScatter(runCanvas, activeScatterState.currentPanel);
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
            if (activeScatterState && activeScatterState.currentPanel) {
                drawScatter(runCanvas, activeScatterState.currentPanel);
            }
        });
        window.fim.setGraphAvailable("scatter", true);
    }
    if (typeof runCanvas !== "undefined" && runCanvas) {
        wireScatterZoomInteraction(runCanvas);
    }
});

/**
 * Clear any chosen zoom sub-rectangle, back to the panel's own full
 * extent. Called by the zoom frame's own "Reset view" button and, from
 * `run-graph-stage.js`, whenever the zoom frame closes or a freshly
 * opened run resets the whole stage -- a stale zoom rectangle chosen
 * against one run's own points should never silently carry into
 * another's.
 *
 * @returns {void}
 */
window.fim.resetScatterZoomView = function resetScatterZoomView() {
    if (!activeScatterState || activeScatterState.zoomDomain === null) {
        return;
    }
    activeScatterState.zoomDomain = null;
    if (
        activeScatterState.currentPanel &&
        typeof runCanvas !== "undefined" &&
        runCanvas
    ) {
        drawScatter(runCanvas, activeScatterState.currentPanel);
    }
};

/**
 * Whether a zoom sub-rectangle is currently active -- the zoom frame's
 * own "Reset view" button is shown only then, so it is never offered
 * with nothing for it to do.
 *
 * @returns {boolean}
 */
window.fim.hasScatterZoomView = function hasScatterZoomView() {
    return activeScatterState !== null && activeScatterState.zoomDomain !== null;
};

/**
 * Let dragging a rectangle on the zoomed pane choose a data-space
 * sub-rectangle to view instead of the panel's own full extent --
 * reported live: the zoom frame's own canvas-pixel `+`/`-`/`Fit`
 * controls (`run-graph-stage.js`) make the whole plot bigger, but a
 * point already close to another in *data* space is exactly as close
 * together at any pixel size, so a botanist trying to see what several
 * near-origin points are actually doing had no way to do it.
 *
 * Scoped to the zoom frame alone (`.graph-zoom-pane`, added by `open
 * GraphZoom`), checked fresh on every `mousedown`: the *inline* pane
 * shares this exact canvas element (`moveIntoZoomFrame` relocates the
 * real node, `run-graph-stage.js`'s own comment), and a `wheel`/drag
 * gesture over a small inline plot is indistinguishable from an attempt
 * to scroll the page past it -- only the enlarged, single-purpose zoom
 * view is worth claiming the gesture on. Only meaningful for a
 * `"frequency"` panel (`drawScatter`'s own `domainOverride` guard
 * already excludes `"pca"`); dragging over an auto-scaled panel simply
 * does nothing.
 *
 * @param {HTMLCanvasElement} canvas
 * @returns {void}
 */
function wireScatterZoomInteraction(canvas) {
    canvas.addEventListener("mousedown", (event) => {
        const state = activeScatterState;
        // `.graph-zoom-pane` (`openGraphZoom`, `run-graph-stage.js`) is
        // set on the pane frame -- `#run-scatter-card`, an ancestor of
        // this canvas -- not the canvas element itself.
        if (!state || !canvas.closest(".graph-zoom-pane") || state.lastGeometry === null) {
            return;
        }
        if (!state.currentPanel || state.currentPanel.kind === "pca") {
            return;
        }
        const box = canvas.getBoundingClientRect();
        state.dragStart = {
            x: ((event.clientX - box.left) / box.width) * canvas.width,
            y: ((event.clientY - box.top) / box.height) * canvas.height,
        };
    });
    canvas.addEventListener("mousemove", (event) => {
        const state = activeScatterState;
        if (!state || state.dragStart === null) {
            return;
        }
        const box = canvas.getBoundingClientRect();
        const current = {
            x: ((event.clientX - box.left) / box.width) * canvas.width,
            y: ((event.clientY - box.top) / box.height) * canvas.height,
        };
        drawScatter(canvas, state.currentPanel);
        const context = canvas.getContext("2d");
        context.save();
        context.strokeStyle = SCATTER_ZOOM_BOX_STROKE;
        context.fillStyle = SCATTER_ZOOM_BOX_FILL;
        context.lineWidth = SCATTER_ZOOM_BOX_LINE_WIDTH;
        const x = Math.min(state.dragStart.x, current.x);
        const y = Math.min(state.dragStart.y, current.y);
        const width = Math.abs(current.x - state.dragStart.x);
        const height = Math.abs(current.y - state.dragStart.y);
        context.fillRect(x, y, width, height);
        context.strokeRect(x, y, width, height);
        context.restore();
    });
    window.addEventListener("mouseup", (event) => {
        const state = activeScatterState;
        if (!state || state.dragStart === null) {
            return;
        }
        const start = state.dragStart;
        state.dragStart = null;
        const box = canvas.getBoundingClientRect();
        const end = {
            x: ((event.clientX - box.left) / box.width) * canvas.width,
            y: ((event.clientY - box.top) / box.height) * canvas.height,
        };
        if (
            Math.abs(end.x - start.x) < ZOOM_DRAG_MINIMUM_PX ||
            Math.abs(end.y - start.y) < ZOOM_DRAG_MINIMUM_PX ||
            state.lastGeometry === null
        ) {
            // Too small to be a deliberate drag -- redraw plainly, erasing
            // the selection-box preview `mousemove` left on the canvas.
            if (state.currentPanel) {
                drawScatter(canvas, state.currentPanel);
            }
            return;
        }
        const { domain, originX, originY, plotSize } = state.lastGeometry;
        const toDataX = (px) => domain.xMin + ((px - originX) / plotSize) * (domain.xMax - domain.xMin);
        const toDataY = (py) => domain.yMin + ((originY - py) / plotSize) * (domain.yMax - domain.yMin);
        const xValues = [toDataX(start.x), toDataX(end.x)].sort((a, b) => a - b);
        const yValues = [toDataY(start.y), toDataY(end.y)].sort((a, b) => a - b);
        state.zoomDomain = {
            xMin: xValues[0],
            xMax: xValues[1],
            yMin: yValues[0],
            yMax: yValues[1],
        };
        if (state.currentPanel) {
            drawScatter(canvas, state.currentPanel);
        }
    });
}

/**
 * Draw one panel filling the whole canvas as a 0-1 by 0-1 (or, for a
 * `"pca"` panel, auto-scaled) scatter.
 *
 * @param {HTMLCanvasElement} canvas
 * @param {{x_label?: string, y_label?: string, kind?: string,
 *     points: Array<{x: number, y: number, count: number, common: boolean}>,
 *     highlighted_points?: Array<{x: number, y: number, count: number, common: boolean}>}} panel
 */
function drawScatter(canvas, panel) {
    const state = activeScatterState;
    if (!state) {
        return;
    }
    state.currentPanel = panel;
    resizeCanvasToCssSize(canvas);
    const context = canvas.getContext("2d");
    context.clearRect(0, 0, canvas.width, canvas.height);
    state.lastGeometry = drawScatterCell(
        context,
        { x: 0, y: 0, width: canvas.width, height: canvas.height },
        panel,
        {
            padding: SINGLE_PANEL_PADDING,
            tickFontSize: SINGLE_PANEL_TICK_FONT,
            markerScale: 1,
            domainOverride: panel.kind !== "pca" ? state.zoomDomain : null,
        }
    );
    renderScatterKey(panel.kind !== "pca");
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
        return { ...SCATTER_FREQUENCY_DOMAIN };
    }
    if (points.length === 0) {
        return { ...SCATTER_EMPTY_DOMAIN };
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
    const xPad = (rawXMax - rawXMin) * DOMAIN_PADDING_FRACTION || SCATTER_DEGENERATE_PADDING;
    const yPad = (rawYMax - rawYMin) * DOMAIN_PADDING_FRACTION || SCATTER_DEGENERATE_PADDING;
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
 *     points: Array<{x: number, y: number, count: number, common: boolean}>,
 *     highlighted_points?: Array<{x: number, y: number, count: number, common: boolean}>}} panel
 * @param {{padding: number, tickFontSize: number, markerScale: number,
 *     domainOverride?: {xMin: number, xMax: number, yMin: number, yMax: number}}} opts
 *     `domainOverride`, when given, replaces `computeDomain`'s own
 *     result -- the zoom-drag interaction below (`applyScatterZoomView`)
 *     is the one caller that sets it, to show a chosen sub-rectangle of
 *     the data instead of the panel's own full, auto-fit extent.
 * @returns {{domain: {xMin: number, xMax: number, yMin: number, yMax: number},
 *     originX: number, originY: number, plotSize: number}}
 */
function drawScatterCell(context, rect, panel, opts) {
    const bounded = panel.kind !== "pca";
    const domain = opts.domainOverride ?? computeDomain(panel.points, bounded);
    const side = Math.min(rect.width, rect.height);
    const adaptivePadding =
        side < SCATTER_SMALL_CANVAS_SIDE
            ? Math.max(
                  SCATTER_SMALL_PADDING_MIN,
                  Math.floor(side * SCATTER_SMALL_PADDING_FRACTION)
              )
            : opts.padding;
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
    context.strokeStyle = CHART_AXIS_COLOR;
    context.lineWidth = AXIS_LINE_WIDTH;
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
        context.setLineDash(SCATTER_DIAGONAL_DASH);
        context.strokeStyle = CHART_GUIDE_COLOR;
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

    const style = activeScatterState.style;
    const geometry = { context, toCanvasX, toCanvasY, originX, originY, plotSize, compact, opts };
    if (style === "trail" && bounded) {
        drawTrail(geometry, panel);
    }
    if (style === "density" && bounded) {
        drawDensity(geometry, panel.points);
    }
    const marks = markPointsFor(style, bounded);
    let originPile = null;
    for (const point of panel.points) {
        if (marks.originBadge && point.x === 0 && point.y === 0) {
            originPile = point;
            continue;
        }
        if (style === "density" && bounded && !point.common) {
            continue;
        }
        drawPoint(geometry, point, style, marks);
    }
    if (originPile !== null) {
        drawOriginBadge(geometry, originPile);
    }
    if (style === "top-allele" && bounded) {
        // `panel.highlighted_points` (`fim.viz.scatter._panel`): each
        // source's own most-frequent allele per displayed deme, grouped
        // the identical way `panel.points` groups every row -- two
        // sources' own top alleles landing at the same coordinate merge
        // into one marker with `count: 2`. Drawn last, on top of the
        // small, fixed-size, muted markers the main loop above already
        // drew for every point (`markPointsFor`'s own `fixed: true` for
        // this style), with plain (non-fixed) marks so `drawPoint`'s
        // own `point.common` branch sizes each one by its own `count`
        // the same way `circles`/`badge`/`dots`/`trail` already size an
        // ordinary point -- every entry here is `common: true` by
        // construction, so this never falls through to an "ordinary
        // point" fill.
        const topAlleleMarks = {
            ramp: false,
            originBadge: false,
            fixed: false,
            dots: false,
            muted: false,
        };
        for (const point of panel.highlighted_points ?? []) {
            drawPoint(geometry, point, style, topAlleleMarks);
        }
    }
    // Handed back so a caller driving interactive zoom/pan (below) can
    // invert a canvas pixel back to a data coordinate without
    // duplicating this function's own padding/domain math a second
    // time -- the one thing about this draw a caller outside it
    // legitimately needs to know.
    return { domain, originX, originY, plotSize };
}

/**
 * Decide how one style marks its points.
 *
 * @param {string} style
 * @param {boolean} bounded a `"frequency"` panel (the only kind with a
 *     meaningful origin pile); a `"pca"` panel always draws original circles.
 * @returns {{ramp: boolean, originBadge: boolean, fixed: boolean, dots: boolean,
 *     muted: boolean}}
 */
function markPointsFor(style, bounded) {
    if (!bounded) {
        return {
            ramp: false,
            originBadge: false,
            fixed: false,
            dots: false,
            muted: false,
        };
    }
    return {
        ramp: style === "color" || style === "color-badge" || style === "density",
        originBadge:
            style === "badge" || style === "color-badge" || style === "top-allele",
        // `top-allele` draws every ordinary point small and fixed-size,
        // whether or not it is also `common` -- its own `highlighted_
        // points` pass (`drawScatterCell`, below) draws the real,
        // count-scaled "most frequent" markers afterward, on top.
        fixed:
            style === "color" || style === "color-badge" || style === "density" ||
            style === "top-allele",
        dots: style === "dots",
        // One plain, muted fill for every ordinary point, instead of
        // `fixed`'s own default sequential count ramp -- `top-allele`'s
        // own small, de-emphasized points carry no count information of
        // their own to show (unlike `color`/`color-badge`/`density`,
        // which use the ramp precisely to show it), so a ramp color
        // here would only suggest one that is not there.
        muted: style === "top-allele",
    };
}

/**
 * Draw one merged point in the chosen style.
 *
 * @param {object} geometry the plot's mapping and context (see caller)
 * @param {{x: number, y: number, count: number, common: boolean}} point
 * @param {string} style
 * @param {{ramp: boolean, fixed: boolean, dots: boolean, muted: boolean}} marks
 */
function drawPoint(geometry, point, style, marks) {
    const { context, toCanvasX, toCanvasY, compact, opts } = geometry;
    const cx = toCanvasX(point.x);
    const cy = toCanvasY(point.y);
    let radius =
        opts.markerScale * (MARKER_BASE_RADIUS + MARKER_COUNT_SCALE * Math.sqrt(point.count));
    if (marks.fixed) {
        radius = opts.markerScale * COLOR_MARKER_RADIUS;
    }
    if (marks.dots) {
        radius = opts.markerScale * (COLOR_MARKER_RADIUS - SCATTER_DOTS_RADIUS_REDUCTION);
    }
    context.beginPath();
    context.arc(cx, cy, radius, 0, TAU);
    if (marks.fixed || marks.dots || point.common) {
        if (point.common) {
            context.fillStyle = COLOR_COMMON;
            context.globalAlpha = 1;
        } else if (marks.dots) {
            context.fillStyle = COLOR_RARE;
            context.globalAlpha = 1 - Math.pow(1 - DOTS_BASE_ALPHA, point.count);
        } else if (marks.muted) {
            context.fillStyle = COLOR_RARE;
            context.globalAlpha = 1;
        } else {
            context.fillStyle = COUNT_RAMP[countStep(point.count)];
            context.globalAlpha = 1;
        }
        context.fill();
        context.globalAlpha = 1;
        context.strokeStyle = CHART_INK_COLOR;
        context.lineWidth = SCATTER_OUTLINE_WIDTH;
        context.stroke();
    } else {
        context.fillStyle = COLOR_RARE;
        context.globalAlpha = SCATTER_RARE_ALPHA;
        context.fill();
        context.globalAlpha = 1;
        context.strokeStyle = CHART_BLACK;
        context.lineWidth = SCATTER_RARE_OUTLINE_WIDTH;
        context.stroke();
    }
    if (point.count > 1 && !compact && !marks.fixed && !marks.dots) {
        context.fillStyle = CHART_INK_COLOR;
        context.font = `${opts.tickFontSize}px ${FONT_FAMILY_SYSTEM}`;
        context.textAlign = "left";
        context.textBaseline = "alphabetic";
        context.fillText(
            String(point.count),
            cx + radius + SCATTER_COUNT_LABEL_OFFSET,
            cy - radius
        );
    }
}

/**
 * Draw the pile of alleles at exactly (0, 0) as a labelled badge.
 *
 * (0, 0) means "this allele exists somewhere in the population but in
 * neither of the two demes shown". It is a real fact but a background
 * one, and in a pooled batch it grows with the replicate count, so it is
 * named in words instead of being drawn as the biggest mark on the plot.
 *
 * @param {object} geometry
 * @param {{count: number}} point
 */
function drawOriginBadge(geometry, point) {
    const { context, toCanvasX, toCanvasY, originX, originY, opts } = geometry;
    const cx = toCanvasX(0);
    const cy = toCanvasY(0);
    context.save();
    context.beginPath();
    context.arc(cx, cy, SCATTER_ORIGIN_DOT_RADIUS, 0, TAU);
    context.fillStyle = CHART_AXIS_LABEL_COLOR;
    context.fill();
    const text = `${point.count} at origin`;
    context.font = `${opts.tickFontSize}px ${FONT_FAMILY_SYSTEM}`;
    const textWidth = context.measureText(text).width;
    const padX = SCATTER_BADGE_PADDING_X;
    const height = opts.tickFontSize + SCATTER_BADGE_EXTRA_HEIGHT;
    const left = originX + SCATTER_BADGE_INSET;
    const top = originY - SCATTER_BADGE_INSET - height;
    context.fillStyle = CHART_BADGE_FILL;
    context.strokeStyle = CHART_AXIS_COLOR;
    context.lineWidth = AXIS_LINE_WIDTH;
    context.beginPath();
    if (typeof context.roundRect === "function") {
        context.roundRect(left, top, textWidth + 2 * padX, height, SCATTER_BADGE_CORNER_RADIUS);
    } else {
        context.rect(left, top, textWidth + 2 * padX, height);
    }
    context.fill();
    context.stroke();
    context.fillStyle = CHART_INK_COLOR;
    context.textAlign = "left";
    context.textBaseline = "middle";
    context.fillText(text, left + padX, top + height / 2);
    context.restore();
}

/**
 * Draw a square-binned density map of the merged points, under any
 * highlighted ring.
 *
 * @param {object} geometry
 * @param {Array<{x: number, y: number, count: number}>} points
 */
function drawDensity(geometry, points) {
    const { context, originX, originY, plotSize, compact } = geometry;
    const bins = compact ? COMPACT_DENSITY_BINS : DENSITY_BINS;
    const totals = new Map();
    for (const point of points) {
        const bx = Math.min(bins - 1, Math.floor(point.x * bins));
        const by = Math.min(bins - 1, Math.floor(point.y * bins));
        const key = by * bins + bx;
        totals.set(key, (totals.get(key) ?? 0) + point.count);
    }
    const cell = plotSize / bins;
    context.save();
    for (const [key, total] of totals) {
        const bx = key % bins;
        const by = Math.floor(key / bins);
        context.fillStyle = COUNT_RAMP[countStep(total)];
        context.fillRect(originX + bx * cell, originY - (by + 1) * cell, cell, cell);
    }
    context.restore();
}

/**
 * Draw the last few scrubber frames faded behind the current one, so the
 * plot shows movement rather than a snapshot. Only frames of the same
 * deme pair are used (the scrubber decides; `getScrubberTrailPanels`
 * returns nothing for a pair chosen by hand).
 *
 * @param {object} geometry
 * @param {object} panel the panel being drawn now
 */
function drawTrail(geometry, panel) {
    const { context, toCanvasX, toCanvasY } = geometry;
    if (!window.fim.getScrubberTrailPanels) {
        return;
    }
    const earlier = window.fim.getScrubberTrailPanels(panel, TRAIL_FRAMES);
    context.save();
    earlier.forEach((trailPanel, index) => {
        // Oldest faintest, newest closest to the current frame.
        context.globalAlpha =
            SCATTER_TRAIL_ALPHA_MIN +
            (SCATTER_TRAIL_ALPHA_SPAN * (index + 1)) / (earlier.length + 1);
        context.fillStyle = COLOR_RARE;
        for (const point of trailPanel.points) {
            context.beginPath();
            context.arc(
                toCanvasX(point.x),
                toCanvasY(point.y),
                SCATTER_TRAIL_DOT_RADIUS,
                0,
                TAU
            );
            context.fill();
        }
    });
    context.restore();
}

/**
 * Fill the key beneath the Run card's scatter: what a ringed marker is,
 * and either what the other markers are or what each count color means.
 *
 * A row of HTML under the plot, not text drawn inside the canvas, where
 * it sat on top of the data. The wording matches `_add_marker_legend` in
 * `fim/viz/scatter.py`, so the plot rendered to `scatter.png` and the one
 * on screen do not explain themselves differently. A `"pca"` panel has no
 * key: it disables frequency highlighting, so there is nothing to explain.
 *
 * @param {boolean} bounded a `"frequency"` panel (the only kind with a key)
 */
function renderScatterKey(bounded) {
    const key = document.getElementById("run-scatter-key");
    if (key === null) {
        return;
    }
    key.replaceChildren();
    if (!bounded) {
        return;
    }
    const add = (className, text, color, fullText = text) => {
        const item = document.createElement("span");
        item.className = "scatter-key-item";
        // The full sentence, worded as in the saved `scatter.png`'s legend
        // (a test keeps the two in step), is the hover text; the visible
        // label is short enough to fit a narrow pane.
        item.title = fullText;
        const swatch = document.createElement("span");
        swatch.className = className;
        if (color !== undefined) {
            swatch.style.setProperty("--swatch-color", color);
        }
        item.append(swatch, text);
        key.appendChild(item);
    };
    add(
        "scatter-key-dot",
        "Most frequent",
        COLOR_COMMON,
        "Most frequent allele in either deme (ties: first)"
    );
    if (markPointsFor(activeScatterState.style, true).ramp) {
        const label = document.createElement("span");
        label.className = "scatter-key-label";
        label.textContent = "Alleles here:";
        key.appendChild(label);
        COUNT_RAMP.forEach((color, index) => {
            add("scatter-key-swatch", COUNT_RAMP_LABELS[index], color);
        });
        return;
    }
    add("scatter-key-dot", "Other alleles", COLOR_RARE);
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
    const rule = AUTO_TICK_DECIMALS.find((entry) => range < entry.below);
    return value.toFixed(rule ? rule.decimals : AUTO_TICK_DEFAULT_DECIMALS);
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
    const bracket = NICE_STEP_BREAKS.find((entry) => error >= entry.from);
    const step = (bracket ? bracket.multiple : 1) * base;
    // Round every tick back to the step's own decimal precision:
    // `index * step` otherwise leaves floating dust (`3 * 0.2` is
    // 0.6000000000000001), harmless to a pixel position but wrong for
    // any caller comparing tick values for equality.
    const decimals = Math.max(0, -power);
    const ticks = [];
    const start = Math.ceil(min / step - TICK_EDGE_EPSILON);
    const end = Math.floor(max / step + TICK_EDGE_EPSILON);
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
 * Every mark is drawn, but a label is left off when it would come within
 * `TICK_LABEL_MIN_GAP` pixels of another label already drawn on this axis
 * or of a span in `avoid` (the caller's own corner labels), so a long axis
 * or a narrow plot never prints one label over another.
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
 * @param {Array<{start: number, end: number}>} [avoid] - Pixel spans along
 *     the axis (x for `"x"`, y for `"y"`) that no tick label may touch.
 */
function drawAxisTickMarks(
    context,
    orientation,
    axisX,
    axisY,
    toPixel,
    ticks,
    fontSize,
    format,
    avoid = []
) {
    context.save();
    context.lineWidth = AXIS_LINE_WIDTH;
    context.font = `${fontSize}px ${FONT_FAMILY_SYSTEM}`;
    const taken = avoid.map((span) => ({ ...span }));
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
        if (!format) {
            continue;
        }
        const text = format(value);
        // The label's extent along the axis: its width for x, its height
        // for y, centered on the mark either way.
        const half =
            orientation === "x" ? context.measureText(text).width / 2 : fontSize / 2;
        const span = { start: position - half, end: position + half };
        const clear = taken.every(
            (other) =>
                span.end + TICK_LABEL_MIN_GAP <= other.start ||
                span.start >= other.end + TICK_LABEL_MIN_GAP
        );
        if (!clear) {
            continue;
        }
        taken.push(span);
        if (orientation === "x") {
            context.textAlign = "center";
            context.textBaseline = "top";
            context.fillText(text, position, axisY + TICK_LENGTH + TICK_LABEL_GAP_X);
        } else {
            context.textAlign = "right";
            context.textBaseline = "middle";
            context.fillText(text, axisX - TICK_LENGTH - TICK_LABEL_GAP_Y, position);
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
    const formatX = (value) =>
        bounded ? value.toFixed(PROBABILITY_TICK_DECIMALS) : formatAutoTick(value, xRange);
    const formatY = (value) =>
        bounded ? value.toFixed(PROBABILITY_TICK_DECIMALS) : formatAutoTick(value, yRange);
    const toPixelX = (value) => originX + ((value - domain.xMin) / xRange) * plotSize;
    const toPixelY = (value) => originY - ((value - domain.yMin) / yRange) * plotSize;

    context.save();
    context.strokeStyle = CHART_AXIS_COLOR;
    context.fillStyle = CHART_AXIS_LABEL_COLOR;
    drawAxisTickMarks(context, "x", originX, originY, toPixelX, xTicks, fontSize, formatX);
    drawAxisTickMarks(context, "y", originX, originY, toPixelY, yTicks, fontSize, formatY);
    context.restore();
}
