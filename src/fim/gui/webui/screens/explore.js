"use strict";

/* Explore (design doc `20260907-claude-sonnet-5-botanist-gui-redesign.md`
 * §5): no-simulation-needed theoretical equilibrium/identity-recovery
 * predictions from `(N, m, mu, d)` alone, computed directly from `fim.
 * statistics`'s own equilibrium/identity-recovery family
 * (`Api.get_equilibrium_predictions`/`get_equilibrium_sweep`). Reachable
 * from any screen via the File menu (`fim.menu.explore`, `app.js`);
 * shared screen history owns Back/Forward behavior.
 *
 * Every field commits on `change` (blur, or Enter), not on every
 * keystroke -- the same "recompute once the value is actually settled"
 * discipline `config-modals.js`'s own dialog-close-triggered `render
 * InitialPreview` call already uses, adapted here to a plain on-screen
 * field with no wrapping dialog to close.
 */

const exploreBanner = document.getElementById("explore-banner");
const exploreBackButton = document.getElementById("explore-back-button");
const exploreRunForRealButton = document.getElementById("explore-run-for-real-button");
const exploreN = document.getElementById("explore-n");
const exploreD = document.getElementById("explore-d");
const exploreM = document.getElementById("explore-m");
const exploreMu = document.getElementById("explore-mu");
const exploreAxis = document.getElementById("explore-axis");
const exploreCanvas = document.getElementById("explore-canvas");
const exploreLegend = document.getElementById("explore-legend");
const exploreScrubRange = document.getElementById("explore-scrub-range");
const exploreScrubLabel = document.getElementById("explore-scrub-label");
const exploreScrubReset = document.getElementById("explore-scrub-reset");
const explorePredictions = document.getElementById("explore-predictions");

let exploreSeeded = false;
let _currentSweep = null;
// The committed configuration's own prediction payload, kept so the
// table can be restored exactly when the scrubber returns to it.
let _committedPredictions = null;

// Display label for each predicted quantity's own table row --
// `meters.js`'s `formatStatisticLabel` only special-cases the `X_YZ`
// subscript shape the six `FinalReport` names use (and passes any other
// string through unchanged); `identity_recovery_half_life` is not one
// of those six, so it gets an explicit, readable label instead of a
// mis-split subscript.
const EXPLORE_PREDICTION_LABELS = {
    D: "D",
    G_ST: "G_ST",
    E_ST: "E_ST",
    H_S: "H_S (within-deme heterozygosity)",
    H_T: "H_T (pooled heterozygosity)",
    S_S: "S_S (within-deme entropy, nats)",
    S_T: "S_T (pooled entropy, nats)",
    A_S: "A_S (effective alleles/deme)",
    A_T: "A_T (effective alleles pooled)",
    identity_recovery_half_life: "Half-life (generations)",
    identity_recovery_rate: "Identity retention rate",
    identity_recovery_equilibrium: "Equilibrium identity",
    mutation_negligible_equilibrium: "Mutation negligible at equilibrium",
};

// Which unit family each predicted quantity lives in. Every statistic
// can be drawn against every one of the four sweep axes -- each is a
// closed-form function of `(N, m, mu, d)` -- but they are not all
// measured in the same thing, and overlaying nats on proportions would
// make the y-axis mean nothing. So the chart plots one family at a
// time: picking a statistic from another family switches the chart to
// that family rather than silently mixing incomparable scales onto one
// axis.
const EXPLORE_STATISTIC_UNITS = {
    D: "proportion",
    G_ST: "proportion",
    E_ST: "proportion",
    H_S: "proportion",
    H_T: "proportion",
    identity_recovery_rate: "proportion",
    identity_recovery_equilibrium: "proportion",
    S_S: "nats",
    S_T: "nats",
    A_S: "alleles",
    A_T: "alleles",
    identity_recovery_half_life: "generations",
    mutation_negligible_equilibrium: "flag",
};

// Per-family y-axis presentation. `fixed` families keep a meaningful
// absolute scale (a proportion is always readable against [0, 1], and
// reading one at a zoomed-in auto-fit scale would exaggerate a trivial
// difference into a dramatic-looking curve); the rest have no natural
// ceiling -- entropy, effective allele count, and a half-life in
// generations are all unbounded above -- so those fit their axis to
// whatever the sweep actually produced.
const EXPLORE_UNIT_FAMILIES = {
    proportion: { title: "Differentiation / diversity (proportion)", fixed: true },
    nats: { title: "Entropy (nats)", fixed: false },
    alleles: { title: "Effective alleles", fixed: false },
    generations: { title: "Generations", fixed: false },
    flag: { title: "Mutation negligible (1 = yes)", fixed: true },
};

// Line color per series. `STATISTIC_TRAJECTORY_COLORS` (run-view-
// completed.js, loaded first -- index.html's own <script> order) is the
// project's one statistic-color language: a color learned as "D" on
// Results reads as "D" here too. Names it does not cover (the entropy,
// effective-allele, and identity-recovery predictions, none of which
// are `FinalReport` statistics) draw from the same Okabe-Ito palette.
// Colors need only be distinct *within* a unit family, since only one
// family is ever on the canvas at once.
const EXPLORE_EXTRA_SERIES_COLORS = {
    S_S: "#0072b2",
    S_T: "#d55e00",
    A_S: "#0072b2",
    A_T: "#d55e00",
    identity_recovery_rate: "#cc79a7",
    identity_recovery_equilibrium: "#8c564b",
    identity_recovery_half_life: "#009e73",
    mutation_negligible_equilibrium: "#7570b3",
};

// The family on the canvas now, and which of its members are drawn.
// Opens on the three differentiation statistics Explore has always
// charted, so the default view is unchanged; every other statistic is
// one click away on its own table row.
let exploreUnitFamily = "proportion";
let exploreVisibleSeries = new Set(["D", "G_ST", "E_ST"]);

// Where the dashed marker sits, as an index into `sweep.points`, or
// `null` while it tracks the committed configuration. Owned by the
// axis scrubber below.
let exploreScrubIndex = null;

/**
 * Index into `sweep.points` the dashed marker is currently on.
 * @param {{current_index: number, points: Array<object>}} sweep
 * @returns {number}
 */
function exploreMarkerIndex(sweep) {
    if (exploreScrubIndex === null) {
        return sweep.current_index ?? 0;
    }
    return Math.min(Math.max(exploreScrubIndex, 0), sweep.points.length - 1);
}

/**
 * Line color for one plotted series.
 * @param {string} name
 * @returns {string}
 */
function exploreSeriesColor(name) {
    return STATISTIC_TRAJECTORY_COLORS[name] ?? EXPLORE_EXTRA_SERIES_COLORS[name] ?? "#666666";
}

/**
 * The currently plotted series, in `EXPLORE_PREDICTION_LABELS` order so
 * the legend and the table agree on ordering.
 * @returns {string[]}
 */
function exploreSeriesInPlot() {
    return Object.keys(EXPLORE_PREDICTION_LABELS).filter((name) =>
        exploreVisibleSeries.has(name)
    );
}

/**
 * Read one statistic at one swept point as a plain number.
 *
 * `mutation_negligible_equilibrium` is a predicate rather than a
 * measurement; it plots as a 0/1 step line so that "where does mutation
 * stop mattering along this axis" is visible as a position on the axis
 * rather than only as a yes/no in the table.
 *
 * @param {object} point
 * @param {string} name
 * @returns {number|null} `null` where the prediction is undefined at
 *     this point -- a gap in the line, never a zero.
 */
function exploreSeriesValue(point, name) {
    const value = point[name];
    if (value === null || value === undefined) {
        return null;
    }
    return typeof value === "boolean" ? (value ? 1 : 0) : value;
}


/**
 * Read the four field values as the plain strings the bridge expects.
 * `get_equilibrium_predictions`/`get_equilibrium_sweep` parse and
 * range-check them server-side; nothing here validates them first.
 * @returns {{n: string, m: string, mu: string, d: string}}
 */
function collectExploreValues() {
    return { n: exploreN.value, m: exploreM.value, mu: exploreMu.value, d: exploreD.value };
}

/**
 * Format one swept x-value for the canvas's own axis-tick labels.
 * @param {number} value
 * @returns {string}
 */
function formatExploreTick(value) {
    if (value !== 0 && (Math.abs(value) < 0.001 || Math.abs(value) >= 1000)) {
        return value.toExponential(1);
    }
    return Number.isInteger(value) ? String(value) : value.toPrecision(2);
}

/**
 * Resize `exploreCanvas`'s own drawing buffer to match its current CSS
 * layout size, then redraw the most recently fetched sweep, if any --
 * the same "buffer size follows layout size, then redraw" shape
 * `scatter.js`'s own `syncCanvasSize` already established, so a plot
 * never renders stretched or blank after a resize (or, here, after the
 * screen's own `hidden` attribute is cleared: a `[hidden]` ancestor's
 * `display: none` collapses `clientWidth`/`clientHeight` to `0`, so this
 * cannot run usefully until `showExplore` has already revealed the
 * screen).
 */
function syncExploreCanvasSize() {
    const cssW = exploreCanvas.clientWidth;
    const cssH = exploreCanvas.clientHeight;
    if (cssW === 0 || cssH === 0) {
        return;
    }
    exploreCanvas.width = cssW;
    exploreCanvas.height = cssH;
    if (_currentSweep) {
        drawSweepCurve(exploreCanvas, _currentSweep);
    }
}

new ResizeObserver(syncExploreCanvasSize).observe(exploreCanvas);

/**
 * Rebuild the sweep curve's own legend -- one swatch per plotted series,
 * the same "swatch span plus a text node" shape `run-view-completed.js`'s
 * own `renderTrajectory` already builds its legend from. Unlike that
 * screen's, this legend does change between redraws (toggling a table
 * row adds or removes a series, and picking one from another unit family
 * replaces the whole set), so it is rebuilt on every draw rather than
 * once at module load.
 */
function renderExploreLegend() {
    exploreLegend.replaceChildren();
    for (const name of exploreSeriesInPlot()) {
        const item = document.createElement("span");
        const swatch = document.createElement("span");
        swatch.className = "swatch";
        swatch.style.backgroundColor = exploreSeriesColor(name);
        item.appendChild(swatch);
        item.appendChild(document.createTextNode(name));
        exploreLegend.appendChild(item);
    }
}

/**
 * Choose readable axis ticks spanning `[minValue, maxValue]`.
 *
 * Steps are rounded to a 1/2/5 multiple of a power of ten so an
 * unbounded family (entropy in nats, effective alleles, a half-life in
 * generations) still gets labels a reader can hold in their head.
 *
 * @param {number} minValue
 * @param {number} maxValue
 * @param {number} targetCount Approximate number of ticks wanted.
 * @returns {number[]} At least two ticks, ascending.
 */
function exploreAxisTicks(minValue, maxValue, targetCount) {
    let low = minValue;
    let high = maxValue;
    if (!(high > low)) {
        // A series that does not vary on this axis -- `D` against `N`,
        // or the identity-recovery family against `mu`. A flat line is
        // the most informative thing on the screen here, so give it
        // room either side rather than collapsing the axis onto it.
        const magnitude = Math.abs(high) > 0 ? Math.abs(high) * 0.5 : 1;
        low = high - magnitude;
        high = high + magnitude;
    }
    const rawStep = (high - low) / Math.max(targetCount - 1, 1);
    const power = Math.pow(10, Math.floor(Math.log10(rawStep)));
    const normalized = rawStep / power;
    const niceMultiple = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
    const step = niceMultiple * power;
    const first = Math.floor(low / step) * step;
    const last = Math.ceil(high / step) * step;
    const ticks = [];
    for (let value = first; value <= last + step * 1e-9; value += step) {
        ticks.push(value);
    }
    return ticks.length >= 2 ? ticks : [low, high];
}

/**
 * Y-axis ticks for the current unit family over `points`.
 *
 * A `fixed` family keeps its absolute scale ([0, 1]); the rest fit the
 * axis to the values this sweep actually produced.
 *
 * @param {Array<object>} points
 * @returns {number[]}
 */
function exploreValueTicks(points) {
    if (EXPLORE_UNIT_FAMILIES[exploreUnitFamily].fixed) {
        return PROBABILITY_TICK_VALUES;
    }
    const values = [];
    for (const point of points) {
        for (const name of exploreSeriesInPlot()) {
            const value = exploreSeriesValue(point, name);
            if (value !== null && Number.isFinite(value)) {
                values.push(value);
            }
        }
    }
    if (values.length === 0) {
        return PROBABILITY_TICK_VALUES;
    }
    return exploreAxisTicks(Math.min(...values), Math.max(...values), 6);
}


/**
 * Return `#explore-axis`'s own `<option>` label text for `axisKey` --
 * the exact wording already shown in that dropdown for whichever field
 * is being swept, read directly from the DOM rather than a second,
 * separately maintained label table that could quietly drift from it.
 * @param {string} axisKey
 * @returns {string}
 */
function exploreAxisLabel(axisKey) {
    for (const option of exploreAxis.options) {
        if (option.value === axisKey) {
            return option.textContent;
        }
    }
    return axisKey;
}

/**
 * Draw the swept prediction curve: every currently selected series
 * (`exploreSeriesInPlot`) as a line against `sweep.points`' own `x`, on
 * a y-domain set by the active unit family (`exploreValueTicks` -- a
 * fixed `[0, 1]` for proportions, auto-fit for the unbounded families),
 * an x-domain fit to the swept range itself, log-scaled for `m`/`mu`
 * (both span several orders of magnitude, the same reason
 * `_geometric_sweep` spaces them geometrically rather than linearly
 * server-side), a dashed vertical marker at the scrubber's own current
 * position, and axis titles naming both what is swept (the x-axis,
 * `exploreAxisLabel`) and what the y-domain measures.
 *
 * Any statistic may be plotted on any of the four axes; several are
 * constant along some of them, and those flat lines carry the point
 * rather than failing to make one -- `equilibrium_d` has no `N` term,
 * so `D` level beneath a falling `G_ST` across two orders of magnitude
 * of population size is this project's central claim, drawn.
 *
 * @param {HTMLCanvasElement} canvas
 * @param {{axis: string, current: number, current_index: number,
 *     points: Array<object>}} sweep
 */
function drawSweepCurve(canvas, sweep) {
    const context = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    context.clearRect(0, 0, width, height);
    renderExploreLegend();
    if (!sweep.points || sweep.points.length === 0) {
        return;
    }

    // Extra left margin (over a bare tick-label width) for the rotated
    // y-axis title; extra bottom margin (over a bare tick-label height)
    // for the x-axis title beneath the existing numeric ticks.
    const plotLeft = 54;
    const plotRight = width - 12;
    const plotTop = 12;
    const plotBottom = height - 46;

    const logScale = sweep.axis === "m" || sweep.axis === "mu";
    const xs = sweep.points.map((point) => point.x);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const domainMin = logScale ? Math.log10(minX) : minX;
    const domainMax = logScale ? Math.log10(maxX) : maxX;

    const valueTicks = exploreValueTicks(sweep.points);
    const valueMin = valueTicks[0];
    const valueMax = valueTicks[valueTicks.length - 1];

    function xToPixel(x) {
        const value = logScale ? Math.log10(x) : x;
        const fraction = domainMax === domainMin ? 0 : (value - domainMin) / (domainMax - domainMin);
        return plotLeft + fraction * (plotRight - plotLeft);
    }

    function yToPixel(y) {
        const span = valueMax - valueMin;
        const fraction = span === 0 ? 0.5 : (y - valueMin) / span;
        return plotBottom - fraction * (plotBottom - plotTop);
    }

    const style = getComputedStyle(document.documentElement);
    const borderColor = style.getPropertyValue("--fim-border").trim();
    const mutedColor = style.getPropertyValue("--fim-muted").trim();

    // Axes.
    context.strokeStyle = borderColor;
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(plotLeft, plotTop);
    context.lineTo(plotLeft, plotBottom);
    context.lineTo(plotRight, plotBottom);
    context.stroke();

    // Y-axis ticks: the active unit family's own scale.
    context.fillStyle = mutedColor;
    context.font = "10px sans-serif";
    context.textAlign = "right";
    context.textBaseline = "middle";
    for (const tick of valueTicks) {
        context.fillText(formatExploreTick(tick), plotLeft - 6, yToPixel(tick));
    }

    // X-axis ticks: first, middle, and last swept value only -- a
    // log-scaled axis spanning several orders of magnitude reads better
    // from a few labeled points than from evenly spaced ones.
    context.textAlign = "center";
    context.textBaseline = "top";
    const tickPoints = [
        sweep.points[0],
        sweep.points[Math.floor(sweep.points.length / 2)],
        sweep.points[sweep.points.length - 1],
    ];
    for (const point of tickPoints) {
        context.fillText(formatExploreTick(point.x), xToPixel(point.x), plotBottom + 4);
    }

    // X-axis title: whichever field is being swept, in `#explore-axis`'s
    // own <option> wording (`exploreAxisLabel`) -- naming the four
    // possible swept quantities the same way Configure's own field
    // labels/`FIELD_HELP` already do, rather than inventing new wording
    // for the same concept a second time.
    context.textAlign = "center";
    context.textBaseline = "bottom";
    context.fillText(exploreAxisLabel(sweep.axis), (plotLeft + plotRight) / 2, height - 2);

    // Y-axis title: what the plotted family measures. Only one unit
    // family is ever on the canvas at a time, so this single title is
    // always accurate for every line currently drawn -- which is the
    // whole reason the chart groups by family instead of overlaying
    // proportions, nats, and generations on one meaningless axis.
    context.save();
    context.translate(12, (plotTop + plotBottom) / 2);
    context.rotate(-Math.PI / 2);
    context.textAlign = "center";
    context.textBaseline = "alphabetic";
    context.fillText(EXPLORE_UNIT_FAMILIES[exploreUnitFamily].title, 0, 0);
    context.restore();

    /**
     * @param {string} key
     * @param {string} color
     */
    function drawLine(key, color) {
        context.strokeStyle = color;
        context.lineWidth = 2;
        context.beginPath();
        let started = false;
        for (const point of sweep.points) {
            const value = exploreSeriesValue(point, key);
            if (value === null || !Number.isFinite(value)) {
                // A gap in the underlying prediction (e.g. `D` at
                // `mu == 0`) breaks the line rather than interpolating
                // across an undefined value.
                started = false;
                continue;
            }
            const x = xToPixel(point.x);
            const y = yToPixel(Math.min(valueMax, Math.max(valueMin, value)));
            if (started) {
                context.lineTo(x, y);
            } else {
                context.moveTo(x, y);
                started = true;
            }
        }
        context.stroke();
    }
    for (const name of exploreSeriesInPlot()) {
        drawLine(name, exploreSeriesColor(name));
    }

    // The scrubber's own position on this axis -- the committed
    // configuration's value until the slider is moved off it.
    context.setLineDash([4, 3]);
    context.strokeStyle = mutedColor;
    context.lineWidth = 1;
    const markerPoint = sweep.points[exploreMarkerIndex(sweep)];
    const currentX = xToPixel(markerPoint.x);
    context.beginPath();
    context.moveTo(currentX, plotTop);
    context.lineTo(currentX, plotBottom);
    context.stroke();
    context.setLineDash([]);
}

/**
 * Add or remove one statistic from the plot.
 *
 * Picking a statistic whose unit family is not the one on the canvas
 * switches the chart to that family and starts it with just that
 * statistic, rather than overlaying (say) a half-life in generations on
 * a proportion axis. Within a family, toggling is additive, so any
 * combination of comparable series can be compared directly.
 *
 * @param {string} name
 */
function toggleExploreSeries(name) {
    const family = EXPLORE_STATISTIC_UNITS[name];
    if (family === undefined) {
        return;
    }
    if (family !== exploreUnitFamily) {
        exploreUnitFamily = family;
        exploreVisibleSeries = new Set([name]);
    } else if (exploreVisibleSeries.has(name)) {
        exploreVisibleSeries.delete(name);
    } else {
        exploreVisibleSeries.add(name);
    }
    refreshExploreRowStates();
    if (_currentSweep) {
        drawSweepCurve(exploreCanvas, _currentSweep);
    }
}

/**
 * Apply the plot-toggle affordance and pressed state to one table row --
 * the same `stat-plot-toggle-row`/`stat-plot-hidden` pair, and the same
 * ARIA treatment, `run-view-completed.js` already gives the trajectory
 * panel's own per-statistic toggles, so the interaction is learned once
 * and works on both screens.
 *
 * @param {HTMLTableRowElement} row
 * @param {string} name
 */
function updateExploreRowState(row, name) {
    const plotted = exploreVisibleSeries.has(name);
    row.classList.add("stat-plot-toggle-row");
    row.classList.toggle("stat-plot-hidden", !plotted);
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.setAttribute("aria-pressed", String(plotted));
}

/**
 * Re-apply every prediction row's own plot-toggle state.
 */
function refreshExploreRowStates() {
    for (const name of Object.keys(EXPLORE_PREDICTION_LABELS)) {
        const row = document.getElementById(`explore-stat-${name}`);
        if (row !== null) {
            updateExploreRowState(row, name);
        }
    }
}

/**
 * Fill the prediction table from one set of already-formatted values.
 *
 * @param {Object<string, string|boolean>} predictions
 * @param {Object<string, string>} [qualifications]
 */
function renderExplorePredictionRows(predictions, qualifications) {
    for (const [name, label] of Object.entries(EXPLORE_PREDICTION_LABELS)) {
        const row = document.getElementById(`explore-stat-${name}`);
        if (row !== null) {
            const rawValue = predictions[name];
            const value = typeof rawValue === "boolean"
                ? (rawValue ? "yes" : "no")
                : rawValue;
            applyStatRow(row, buildPointMeter(label, value));
            const qualification = qualifications?.[name];
            if (qualification) {
                row.title = `${row.title}; ${qualification}`;
            }
        }
    }
    refreshExploreRowStates();
}

/**
 * Format one raw swept value the way the bridge's own `format_statistic`
 * would have, so a scrubbed row and a committed row show the same number
 * of digits for the same quantity.
 *
 * @param {number|boolean|null} value
 * @param {number} digits Significant digits, from the sweep payload.
 * @returns {string|boolean}
 */
function formatExploreSweepValue(value, digits) {
    if (typeof value === "boolean") {
        return value;
    }
    if (value === null || value === undefined || !Number.isFinite(value)) {
        return "undefined";
    }
    return String(Number(value.toPrecision(digits)));
}

/**
 * Redraw the prediction table for wherever the scrubber currently sits.
 *
 * Reads straight out of the sweep payload rather than calling the bridge
 * again: `get_equilibrium_sweep` already carries every statistic at
 * every point, so dragging the slider costs no round trip at all.
 * With the scrubber parked at the committed configuration, this falls
 * back to the committed payload so the two presentations agree exactly.
 */
function renderExploreScrubbedPredictions() {
    if (_currentSweep === null) {
        return;
    }
    if (exploreScrubIndex === null) {
        if (_committedPredictions !== null) {
            renderExplorePredictionRows(
                _committedPredictions.predictions,
                _committedPredictions.qualifications
            );
        }
        return;
    }
    const point = _currentSweep.points[exploreMarkerIndex(_currentSweep)];
    const digits = _currentSweep.digits ?? 4;
    const predictions = {};
    for (const name of Object.keys(EXPLORE_PREDICTION_LABELS)) {
        predictions[name] = formatExploreSweepValue(point[name], digits);
    }
    renderExplorePredictionRows(predictions, _committedPredictions?.qualifications);
}

/**
 * Point the scrubber's controls at the current sweep and marker
 * position, and say in words which parameter value is being shown --
 * the scrubbed state has to be legible as *not* the committed
 * configuration, or the table silently lies about what was typed into
 * the four fields.
 */
function syncExploreScrubber() {
    if (_currentSweep === null) {
        exploreScrubRange.disabled = true;
        exploreScrubReset.disabled = true;
        exploreScrubLabel.textContent = "";
        return;
    }
    const index = exploreMarkerIndex(_currentSweep);
    exploreScrubRange.disabled = false;
    exploreScrubRange.max = String(_currentSweep.points.length - 1);
    exploreScrubRange.value = String(index);
    exploreScrubReset.disabled = exploreScrubIndex === null;
    const shown = formatExploreTick(_currentSweep.points[index].x);
    const axisName = exploreAxisLabel(_currentSweep.axis);
    exploreScrubLabel.textContent =
        exploreScrubIndex === null
            ? `${axisName} = ${shown} (current)`
            : `${axisName} = ${shown} (exploring)`;
    explorePredictions.classList.toggle(
        "explore-scrubbed",
        exploreScrubIndex !== null
    );
}

/**
 * Move the marker to `index` and update everything that follows from it.
 * @param {number|null} index `null` returns to the committed value.
 */
function setExploreScrubIndex(index) {
    exploreScrubIndex = index;
    if (_currentSweep) {
        drawSweepCurve(exploreCanvas, _currentSweep);
    }
    syncExploreScrubber();
    renderExploreScrubbedPredictions();
}

/**
 * Recompute and redraw everything Explore shows, from the four fields'
 * current values: the predictions table, then the sweep curve for
 * whichever axis is selected. An invalid field (non-numeric, or out of
 * range) shows its message in the banner and blanks the rest, exactly
 * like every other screen's own validation-failure presentation.
 */
async function refreshExplore() {
    const values = collectExploreValues();
    const predictionsResult = await window.pywebview.api.get_equilibrium_predictions(
        values.n,
        values.m,
        values.mu,
        values.d
    );
    if (!predictionsResult.ok) {
        exploreBanner.textContent = predictionsResult.message;
        exploreBanner.hidden = false;
        explorePredictions.hidden = true;
        _currentSweep = null;
        _committedPredictions = null;
        syncExploreScrubber();
        exploreCanvas.getContext("2d").clearRect(0, 0, exploreCanvas.width, exploreCanvas.height);
        window.__fimExploreReady = true;
        return;
    }
    exploreBanner.hidden = true;
    explorePredictions.hidden = false;
    _committedPredictions = predictionsResult;
    renderExplorePredictionRows(
        predictionsResult.predictions,
        predictionsResult.qualifications
    );

    const sweepResult = await window.pywebview.api.get_equilibrium_sweep(
        exploreAxis.value,
        values.n,
        values.m,
        values.mu,
        values.d
    );
    _currentSweep = sweepResult.ok ? sweepResult : null;
    syncExploreCanvasSize();
    if (!sweepResult.ok) {
        exploreCanvas.getContext("2d").clearRect(0, 0, exploreCanvas.width, exploreCanvas.height);
    }
    syncExploreScrubber();
    window.__fimExploreReady = true;
}

exploreScrubRange.addEventListener("input", () => {
    setExploreScrubIndex(Number(exploreScrubRange.value));
});

exploreScrubReset.addEventListener("click", () => {
    setExploreScrubIndex(null);
});

for (const field of [exploreN, exploreD, exploreM, exploreMu, exploreAxis]) {
    field.addEventListener("change", () => {
        window.__fimExploreReady = false;
        // A changed configuration (or a changed axis) invalidates any
        // scrubbed position: the marker returns to the newly committed
        // value rather than staying parked at a stale point index.
        exploreScrubIndex = null;
        refreshExplore();
    });
}

// Prediction rows double as plot toggles. Registered once at module
// load: `applyStatRow` replaces a row's children on every refresh, but
// never the `<tr>` itself, so these listeners survive each redraw.
for (const name of Object.keys(EXPLORE_PREDICTION_LABELS)) {
    const row = document.getElementById(`explore-stat-${name}`);
    if (row === null) {
        continue;
    }
    row.addEventListener("click", () => toggleExploreSeries(name));
    row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            toggleExploreSeries(name);
        }
    });
}

exploreBackButton.addEventListener("click", () => {
    window.fim.navigateBack();
});

/**
 * Explore-to-Study/Run handoff (`20260918-claude-sonnet-5-explore-to-
 * study-run-handoff-design.md`, `selby/restricted`, §1/§8, Option B):
 * seeds Configure from this screen's own current N/d/m/mu, navigates
 * there, and pre-selects "New study…" on Configure's own `run-study-
 * select` -- a soft nudge (§2), never forced; one extra click abandons
 * it back to "No study," identical to any other visit.
 */
exploreRunForRealButton.addEventListener("click", async () => {
    const values = collectExploreValues();
    const result = await window.pywebview.api.get_starter_form_with_overrides({
        N: values.n,
        d: values.d,
        m_rate: values.m,
        mu_value: values.mu,
    });
    if (!result.ok) {
        exploreBanner.textContent = result.message;
        exploreBanner.hidden = false;
        return;
    }
    await window.fim.showConfigureScreen();
    applyFormValues(result.values);
    await revalidate();
    window.fim.preselectNewStudy();
});

/**
 * Show Explore. `overrides`, when given, is Configure's own current
 * form values (`N`/`d`/`m_rate`/`mu_value` -- `collectFormValues`'s own
 * shape) — the symmetric fix for the existing "🔮 Explore" button
 * (`nav-rail.js`), which previously navigated here without carrying
 * over whatever Configure was actually showing at the time, relying
 * instead on this function's own once-per-launch seed below (§"Current
 * state" of the handoff design document, above, flags this as the same
 * underlying gap in the opposite direction). Absent `overrides`, the
 * four fields are seeded once per launch, from the same `get_starter_
 * form` values a brand-new Configure form starts with — not hardcoded
 * here a second time, so the two can never quietly drift apart — and
 * are left exactly as the user set them on every subsequent visit
 * within this launch.
 * @param {{N: string, d: string, m_rate: string, mu_value: string}} [overrides]
 */
window.fim.showExplore = async function showExplore(overrides) {
    if (overrides) {
        exploreSeeded = true;
        exploreN.value = overrides.N;
        exploreD.value = overrides.d;
        exploreM.value = overrides.m_rate;
        exploreMu.value = overrides.mu_value;
    } else if (!exploreSeeded) {
        exploreSeeded = true;
        const starter = await window.pywebview.api.get_starter_form();
        exploreN.value = starter.N;
        exploreD.value = starter.d;
        exploreM.value = starter.m_rate;
        exploreMu.value = starter.mu_value;
    }
    window.fim.showScreen("screen-explore");
    window.__fimExploreReady = false;
    await refreshExplore();
};
