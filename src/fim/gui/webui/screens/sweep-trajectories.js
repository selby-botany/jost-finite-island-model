/* Sweep results: how each point approached equilibrium.
 *
 * The "How each point got there" view of the results card (the "View"
 * select in `index.html`) draws the statistic against generation, one curve
 * per value of the "Curves by" axis. Each curve is one point's replicates,
 * reduced on the Python side to at most 512 generations (`fim.sweep_
 * trajectory`): the mean over the replicates still running, the interval
 * of that mean, and how many replicates contribute.
 *
 * This file shares the results card's controls, state and helpers with
 * `sweep-results.js` (all classic scripts share one global scope); it adds
 * only what the trajectory chart needs.
 */

// The last chart's spec, and the sample the keyboard is on: `{curve, position}`.
let sweepTrajectoryLastSpec = null;
let sweepTrajectoryFocus = null;

/**
 * Whether the results card is showing trajectories rather than final values.
 * @returns {boolean}
 */
function sweepShowsTrajectories() {
    return sweepResultsViewSelect.value === "trajectory";
}

/**
 * Whether the x axis (generation) is drawn on a log scale.
 * @param {object[]} curves
 * @returns {boolean}
 */
function sweepTrajectoryIsLog(curves) {
    const scale = sweepResultsXScale.value;
    if (scale !== "auto") {
        return scale === "log";
    }
    const last = Math.max(
        1,
        ...curves.flatMap((curve) => curve.points.map((point) => point.generations.at(-1)))
    );
    return last >= SWEEP_TRAJECTORY_AUTO_LOG_FROM;
}

/**
 * Fetch and draw the trajectory overlay for the current controls.
 * @param {number} sequence
 * @param {string} byKey The axis that splits the curves, or "".
 * @param {string} statistic
 */
async function renderSweepTrajectories(sequence, byKey, statistic) {
    const held = sweepHeldValues();
    const reply = await window.pywebview.api.get_sweep_trajectories(
        sweepResultsStudyId,
        statistic,
        byKey || null,
        held
    );
    if (sequence !== sweepResultsRenderSequence) {
        return;
    }
    if (!reply.ok) {
        sweepResultsHit = null;
        sweepTrajectoryLastSpec = null;
        sweepTrajectoryFocus = null;
        const context = sweepResultsCanvas.getContext("2d");
        context.clearRect(0, 0, sweepResultsCanvas.width, sweepResultsCanvas.height);
        sweepResultsReadout.textContent = reply.message;
        return;
    }
    const single = STATISTIC_TRAJECTORY_COLORS[statistic] || SWEEP_FALLBACK_COLOR;
    const curves = reply.series.slice(0, SWEEP_MAX_SERIES).map((curve, position) => ({
        label: byKey ? `${byKey} = ${sweepFormatValue(curve.byValue)}` : null,
        color: byKey ? SWEEP_SERIES_COLORS[position] : single,
        shape: byKey ? SWEEP_SERIES_SHAPES[position] : "circle",
        byValue: curve.byValue,
        points: curve.points,
        theory: null,
    }));
    if (sweepResultsTheory.checked && SWEEP_THEORY_STATISTICS.has(statistic)) {
        const coordinates = curves.map((curve) => ({
            ...held,
            ...(byKey ? { [byKey]: curve.byValue } : {}),
        }));
        const predicted = await sweepTheoryAt(coordinates);
        if (sequence !== sweepResultsRenderSequence) {
            return;
        }
        curves.forEach((curve, index) => {
            curve.theory = predicted[index][statistic] ?? null;
        });
    }
    sweepTrajectoryFocus = null;
    redrawSweepTrajectories({
        curves,
        statistic,
        interval: sweepResultsInterval.value,
        log: sweepTrajectoryIsLog(curves),
        yScale: sweepResultsYScale.value,
    });
    const drawn = curves.reduce((count, curve) => count + curve.points.length, 0);
    const cut =
        reply.series.length > SWEEP_MAX_SERIES
            ? ` Showing the first ${SWEEP_MAX_SERIES} of ${reply.series.length} curves; hold an axis for the rest.`
            : "";
    sweepResultsReadout.textContent =
        (drawn === 0
            ? "No finished points with a recorded history at these settings."
            : "Hover for the values at a generation; click to open that run. " +
              "A dashed stretch has fewer than half the replicates still running.") + cut;
}

/**
 * Draw the overlay again from a spec, with the keyboard focus.
 * @param {object} spec
 */
function redrawSweepTrajectories(spec) {
    sweepTrajectoryLastSpec = spec;
    sweepResultsHit = drawSweepTrajectories(sweepResultsCanvas, {
        ...spec,
        focus: sweepTrajectoryFocus,
    });
}

/**
 * The values a sample's band reaches, for fitting the y axis.
 * @param {object} point A trajectory point.
 * @param {number} position Index of the sample.
 * @param {string} interval "ci", "sd" or "both".
 * @returns {number[]}
 */
function sweepTrajectoryReach(point, position, interval) {
    const mean = point.mean[position];
    if (mean === null) {
        return [];
    }
    const reach = [mean];
    if (interval !== "sd") {
        reach.push(point.low[position] ?? mean, point.high[position] ?? mean);
    }
    if (interval !== "ci") {
        const spread = point.std[position] ?? 0;
        reach.push(mean - spread, mean + spread);
    }
    return reach;
}

/**
 * Work out the y axis for the values to be drawn.
 * @param {number[]} values
 * @param {string} statistic
 * @param {string} yScale "auto", "linear" or "log".
 * @param {number} top Pixel row of the top edge.
 * @param {number} height Pixel height of the frame.
 * @returns {{toY: function, ticks: number[], log: boolean}}
 */
function sweepTrajectoryYAxis(values, statistic, yScale, top, height) {
    const positive = values.filter((value) => value > 0);
    const log = yScale === "log" && positive.length > 0;
    if (log) {
        const lowExponent = Math.floor(Math.log10(Math.min(...positive)));
        const highExponent = Math.max(
            Math.ceil(Math.log10(Math.max(...positive))),
            lowExponent + SWEEP_LOG_MIN_DECADES
        );
        const toY = (value) =>
            value > 0
                ? top +
                  height -
                  Math.min(
                      1,
                      Math.max(0, (Math.log10(value) - lowExponent) / (highExponent - lowExponent))
                  ) *
                      height
                : top + height;
        return { toY, ticks: sweepLogTicks(lowExponent, highExponent), log };
    }
    let low = Math.min(...values, Infinity);
    let high = Math.max(...values, -Infinity);
    if (statistic !== "" && isProportionStatistic(statistic)) {
        low = 0;
        high = 1;
    } else if (!(high > low)) {
        low = Number.isFinite(low) ? low - 1 : 0;
        high = Number.isFinite(high) ? high + 1 : 1;
    }
    return {
        toY: (value) => top + height - ((value - low) / (high - low)) * height,
        ticks: sweepTicks(low, high),
        log,
    };
}

/**
 * Draw the overlay and return its pixel samples for hit testing.
 * @param {HTMLCanvasElement} canvas
 * @param {object} spec `{curves, statistic, interval, log, yScale, focus}`.
 * @returns {{kind: "trajectory", pixels: object[], statistic: string}}
 */
function drawSweepTrajectories(canvas, spec) {
    const context = canvas.getContext("2d");
    const style = getComputedStyle(document.documentElement);
    const muted = style.getPropertyValue("--fim-muted").trim() || THEME_FALLBACK_MUTED;
    const border = style.getPropertyValue("--fim-border").trim() || THEME_FALLBACK_BORDER;
    const foreground =
        style.getPropertyValue("--fim-foreground").trim() || THEME_FALLBACK_FOREGROUND;
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.font = FONT_AXIS_LARGE;
    const left = SWEEP_LINE_MARGIN.left;
    const top = SWEEP_LINE_MARGIN.top;
    const width = canvas.width - left - SWEEP_LINE_MARGIN.right;
    const height = canvas.height - top - SWEEP_LINE_MARGIN.bottom;
    const points = spec.curves.flatMap((curve) => curve.points);

    const reached = points.flatMap((point) =>
        point.generations.flatMap((_, position) =>
            sweepTrajectoryReach(point, position, spec.interval)
        )
    );
    const theory = spec.curves.map((curve) => curve.theory).filter((value) => value !== null);
    const yAxis = sweepTrajectoryYAxis(
        [...reached, ...theory],
        spec.statistic,
        spec.yScale,
        top,
        height
    );
    const lastGeneration = Math.max(
        1,
        ...points.map((point) => point.generations.at(-1))
    );
    const firstGeneration = spec.log ? 1 : 0;
    const scale = spec.log ? Math.log : (value) => value;
    const span = scale(lastGeneration) - scale(firstGeneration) || 1;
    const toX = (generation) =>
        left +
        SWEEP_X_INSET +
        ((scale(Math.max(generation, firstGeneration)) - scale(firstGeneration)) / span) *
            (width - 2 * SWEEP_X_INSET);

    context.strokeStyle = border;
    context.fillStyle = muted;
    context.textAlign = "right";
    for (const tick of yAxis.ticks) {
        const y = yAxis.toY(tick);
        context.beginPath();
        context.moveTo(left, y);
        context.lineTo(left + width, y);
        context.stroke();
        context.fillText(
            window.fim.heatmap.format(tick),
            left - SWEEP_Y_LABEL_GAP,
            y + SWEEP_Y_LABEL_BASELINE
        );
    }
    context.textAlign = "center";
    const xTicks = spec.log
        ? sweepLogTicks(0, Math.ceil(Math.log10(lastGeneration))).filter(
              (tick) => tick <= lastGeneration
          )
        : sweepTicks(0, lastGeneration);
    for (const tick of xTicks) {
        context.fillText(
            window.fim.heatmap.format(tick),
            toX(tick),
            top + height + SWEEP_X_LABEL_DROP
        );
    }
    context.fillStyle = foreground;
    context.fillText(
        `generation${spec.log ? " (log scale)" : ""}`,
        left + width / 2,
        canvas.height - SWEEP_X_TITLE_RISE
    );
    context.save();
    context.translate(SWEEP_Y_TITLE_INSET, top + height / 2);
    context.rotate(-QUARTER_TURN);
    context.fillText(
        `${statisticDisplayName(spec.statistic)}${yAxis.log ? " (log scale)" : ""}`,
        0,
        0
    );
    context.restore();

    const pixels = [];
    spec.curves.forEach((curve, curveIndex) => {
        drawSweepTrajectoryTheory(context, curve, yAxis.toY, left, width);
        curve.points.forEach((point) => {
            drawSweepTrajectoryBand(context, point, curve.color, spec, toX, yAxis.toY);
            drawSweepTrajectoryBurnIn(context, point, curve.color, toX, top, height);
        });
        curve.points.forEach((point, pointIndex) => {
            const own = drawSweepTrajectoryLine(context, point, curve.color, toX, yAxis.toY);
            pixels.push(
                ...own.map((pixel) => ({ ...pixel, curve: curveIndex, point: pointIndex }))
            );
            const last = own.at(-1);
            if (last) {
                drawSweepMarker(
                    context,
                    last.x,
                    last.y,
                    sweepHealthMarker(point.health),
                    curve.color,
                    curve.shape,
                    SWEEP_SERIES_MARKER_RADIUS
                );
            }
        });
    });
    drawSweepLegend(
        context,
        canvas,
        spec.curves,
        points.map((point) => ({ health: point.health })),
        foreground
    );
    const focused = spec.focus
        ? pixels.find(
              (pixel) =>
                  pixel.curve === spec.focus.curve && pixel.position === spec.focus.position
          )
        : null;
    if (focused) {
        context.strokeStyle = foreground;
        context.lineWidth = SWEEP_FOCUS_LINE_WIDTH;
        context.beginPath();
        context.arc(focused.x, focused.y, SWEEP_FOCUS_RADIUS, 0, TAU);
        context.stroke();
        context.lineWidth = AXIS_LINE_WIDTH;
    }
    return { kind: "trajectory", pixels, statistic: spec.statistic };
}

/**
 * Draw one point's band: the interval of the mean as a shaded area, the
 * spread across replicates as dashed edges (or as the shaded area when it
 * is the only interval chosen).
 * @param {CanvasRenderingContext2D} context
 * @param {object} point
 * @param {string} color
 * @param {{interval: string}} spec
 * @param {function} toX
 * @param {function} toY
 */
function drawSweepTrajectoryBand(context, point, color, spec, toX, toY) {
    const rows = point.generations
        .map((generation, position) => ({ generation, position }))
        .filter(({ position }) => point.mean[position] !== null);
    if (rows.length < 2) {
        return;
    }
    const edges = (lowOf, highOf) => {
        context.beginPath();
        rows.forEach(({ generation, position }, index) => {
            const x = toX(generation);
            const y = toY(highOf(position));
            if (index === 0) {
                context.moveTo(x, y);
            } else {
                context.lineTo(x, y);
            }
        });
        for (let index = rows.length - 1; index >= 0; index -= 1) {
            const { generation, position } = rows[index];
            context.lineTo(toX(generation), toY(lowOf(position)));
        }
        context.closePath();
    };
    context.save();
    if (spec.interval !== "sd") {
        context.fillStyle = color;
        context.globalAlpha = BAND_ALPHA;
        edges(
            (position) => point.low[position] ?? point.mean[position],
            (position) => point.high[position] ?? point.mean[position]
        );
        context.fill();
    }
    if (spec.interval !== "ci") {
        context.globalAlpha = 1;
        context.strokeStyle = color;
        context.lineWidth = AXIS_LINE_WIDTH;
        context.setLineDash(DASH_REFERENCE_LINE);
        for (const sign of [-1, 1]) {
            context.beginPath();
            rows.forEach(({ generation, position }, index) => {
                const x = toX(generation);
                const y = toY(point.mean[position] + sign * (point.std[position] ?? 0));
                if (index === 0) {
                    context.moveTo(x, y);
                } else {
                    context.lineTo(x, y);
                }
            });
            context.stroke();
        }
    }
    context.restore();
}

/**
 * Draw a point's mean as a line, dashed wherever fewer than half its
 * replicates are still running, and return its pixel samples.
 * @param {CanvasRenderingContext2D} context
 * @param {object} point
 * @param {string} color
 * @param {function} toX
 * @param {function} toY
 * @returns {Array<{x: number, y: number, position: number}>}
 */
function drawSweepTrajectoryLine(context, point, color, toX, toY) {
    const own = [];
    point.generations.forEach((generation, position) => {
        const mean = point.mean[position];
        if (mean !== null) {
            own.push({ x: toX(generation), y: toY(mean), position });
        }
    });
    context.save();
    context.strokeStyle = color;
    context.lineWidth = CURVE_LINE_WIDTH;
    for (let index = 1; index < own.length; index += 1) {
        context.setLineDash(point.quorum[own[index].position] ? [] : DASH_MODEL_CURVE);
        context.beginPath();
        context.moveTo(own[index - 1].x, own[index - 1].y);
        context.lineTo(own[index].x, own[index].y);
        context.stroke();
    }
    context.restore();
    return own;
}

/**
 * Mark where a point's burn-in ended with a dotted vertical line.
 * @param {CanvasRenderingContext2D} context
 * @param {object} point
 * @param {string} color
 * @param {function} toX
 * @param {number} top
 * @param {number} height
 */
function drawSweepTrajectoryBurnIn(context, point, color, toX, top, height) {
    if (point.burnIn === null || point.burnIn > point.generations.at(-1)) {
        return;
    }
    context.save();
    context.strokeStyle = color;
    context.lineWidth = AXIS_LINE_WIDTH;
    context.setLineDash(DASH_DOTTED);
    context.beginPath();
    context.moveTo(toX(point.burnIn), top);
    context.lineTo(toX(point.burnIn), top + height);
    context.stroke();
    context.restore();
}

/**
 * Draw a curve's theory value as a dashed horizontal line.
 * @param {CanvasRenderingContext2D} context
 * @param {object} curve
 * @param {function} toY
 * @param {number} left
 * @param {number} width
 */
function drawSweepTrajectoryTheory(context, curve, toY, left, width) {
    if (curve.theory === null) {
        return;
    }
    context.save();
    context.strokeStyle = curve.color;
    context.lineWidth = OVERLAY_LINE_WIDTH;
    context.setLineDash(DASH_THEORY_CURVE);
    context.beginPath();
    context.moveTo(left, toY(curve.theory));
    context.lineTo(left + width, toY(curve.theory));
    context.stroke();
    context.restore();
}

/**
 * The readout text for one sample of a curve.
 * @param {object} curve
 * @param {object} point The curve's trajectory point.
 * @param {number} position Index of the sample.
 * @param {string} statistic
 * @returns {string}
 */
function sweepTrajectoryReadout(curve, point, position, statistic) {
    const where = curve.label ? `${curve.label}, ` : "";
    const generation = `generation ${sweepFormatValue(point.generations[position])}`;
    const mean = point.mean[position];
    const low = point.low[position];
    const high = point.high[position];
    const interval =
        low !== null && high !== null && point.count[position] > 1
            ? ` [${sweepFormatValue(low)}, ${sweepFormatValue(high)}]`
            : "";
    const running = `${point.count[position]} of ${point.nReplicates} replicates running`;
    const thin = point.quorum[position] ? "" : " (fewer than half: less sure)";
    return `${where}${generation}: ${statisticDisplayName(statistic)} ${sweepFormatValue(mean)}${interval}, ${running}${thin}`;
}

/**
 * The sample under the pointer, if any.
 * @param {number} x Canvas pixel x.
 * @param {number} y Canvas pixel y.
 * @returns {object|null} A pixel of `sweepResultsHit.pixels`.
 */
function sweepTrajectorySampleAt(x, y) {
    let best = null;
    let bestDistance = SWEEP_HIT_RADIUS_PX;
    for (const pixel of sweepResultsHit.pixels) {
        const distance = Math.hypot(pixel.x - x, pixel.y - y);
        if (distance < bestDistance) {
            best = pixel;
            bestDistance = distance;
        }
    }
    return best;
}

/**
 * Show the readout for a sample.
 * @param {object} pixel An entry of `sweepResultsHit.pixels`.
 */
function showSweepTrajectorySample(pixel) {
    const curve = sweepTrajectoryLastSpec.curves[pixel.curve];
    const point = curve.points[pixel.point];
    sweepResultsReadout.textContent = sweepTrajectoryReadout(
        curve,
        point,
        pixel.position,
        sweepTrajectoryLastSpec.statistic
    );
}

/**
 * Move the keyboard focus along the overlay: left and right along a
 * curve, up and down between curves; Enter opens the point's run.
 * @param {KeyboardEvent} event
 */
function moveSweepTrajectoryFocus(event) {
    const pixels = sweepResultsHit.pixels;
    const filled = [...new Set(pixels.map((pixel) => pixel.curve))].sort((a, b) => a - b);
    if (filled.length === 0) {
        return;
    }
    const ownOf = (curve) => pixels.filter((pixel) => pixel.curve === curve);
    if (event.key === "Enter" && sweepTrajectoryFocus !== null) {
        const curve = sweepTrajectoryLastSpec.curves[sweepTrajectoryFocus.curve];
        const result = sweepResultsByIndex().get(curve.points[0].index);
        if (result) {
            openSweepPoint(result);
        }
        event.preventDefault();
        return;
    }
    const step = { ArrowRight: [0, 1], ArrowLeft: [0, -1], ArrowDown: [1, 0], ArrowUp: [-1, 0] }[
        event.key
    ];
    if (step === undefined) {
        return;
    }
    event.preventDefault();
    let curve = filled[0];
    let place = 0;
    if (sweepTrajectoryFocus !== null) {
        curve = filled[Math.min(filled.length - 1, Math.max(0, filled.indexOf(sweepTrajectoryFocus.curve) + step[0]))];
        const current = ownOf(sweepTrajectoryFocus.curve).findIndex(
            (pixel) => pixel.position === sweepTrajectoryFocus.position
        );
        place = Math.max(0, current) + step[1] * SWEEP_TRAJECTORY_KEY_STEP;
    }
    const own = ownOf(curve);
    const chosen = own[Math.min(own.length - 1, Math.max(0, place))];
    sweepTrajectoryFocus = { curve, position: chosen.position };
    showSweepTrajectorySample(chosen);
    redrawSweepTrajectories(sweepTrajectoryLastSpec);
}
