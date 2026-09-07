"use strict";

/* Explore (design doc `20260907-claude-sonnet-5-botanist-gui-redesign.md`
 * §5): no-simulation-needed theoretical equilibrium/identity-recovery
 * predictions from `(N, m, mu, d)` alone, computed directly from `fim.
 * statistics`'s own equilibrium/identity-recovery family
 * (`Api.get_equilibrium_predictions`/`get_equilibrium_sweep`). Reachable
 * from any screen via the File menu (`fim.menu.explore`, `app.js`),
 * returning to whichever screen was showing before it -- the same
 * "Back" contract `help.js` already established for `screen-help`.
 *
 * Every field commits on `change` (blur, or Enter), not on every
 * keystroke -- the same "recompute once the value is actually settled"
 * discipline `config-modals.js`'s own dialog-close-triggered `render
 * InitialPreview` call already uses, adapted here to a plain on-screen
 * field with no wrapping dialog to close.
 */

const exploreBanner = document.getElementById("explore-banner");
const exploreBackButton = document.getElementById("explore-back-button");
const exploreN = document.getElementById("explore-n");
const exploreD = document.getElementById("explore-d");
const exploreM = document.getElementById("explore-m");
const exploreMu = document.getElementById("explore-mu");
const exploreAxis = document.getElementById("explore-axis");
const exploreCanvas = document.getElementById("explore-canvas");
const explorePredictions = document.getElementById("explore-predictions");

let exploreReturnScreen = "screen-run";
let exploreSeeded = false;
let _currentSweep = null;

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
    identity_recovery_half_life: "Half-life (generations)",
};

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
 * Draw the swept prediction curve: `D` and `G_ST` as two lines against
 * `sweep.points`' own `x`, on a fixed `[0, 1]` y-domain
 * (`PROBABILITY_TICK_VALUES`, declared in `scatter.js` — both predicted
 * statistics live on the same probability scale a frequency scatter
 * panel's own axes already use), an x-domain fit to the swept range
 * itself, log-scaled for `m`/`mu` (both span several orders of
 * magnitude, the same reason `_geometric_sweep` spaces them
 * geometrically rather than linearly server-side), and a dashed
 * vertical marker at the configuration's own current value.
 *
 * @param {HTMLCanvasElement} canvas
 * @param {{axis: string, current: number,
 *     points: Array<{x: number, D: number|null, G_ST: number|null}>}} sweep
 */
function drawSweepCurve(canvas, sweep) {
    const context = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    context.clearRect(0, 0, width, height);
    if (!sweep.points || sweep.points.length === 0) {
        return;
    }

    const plotLeft = 40;
    const plotRight = width - 12;
    const plotTop = 12;
    const plotBottom = height - 28;

    const logScale = sweep.axis === "m" || sweep.axis === "mu";
    const xs = sweep.points.map((point) => point.x);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const domainMin = logScale ? Math.log10(minX) : minX;
    const domainMax = logScale ? Math.log10(maxX) : maxX;

    function xToPixel(x) {
        const value = logScale ? Math.log10(x) : x;
        const fraction = domainMax === domainMin ? 0 : (value - domainMin) / (domainMax - domainMin);
        return plotLeft + fraction * (plotRight - plotLeft);
    }

    function yToPixel(y) {
        return plotBottom - y * (plotBottom - plotTop);
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

    // Y-axis ticks: the same fixed [0, 1] probability scale a frequency
    // scatter panel's own axes use (`PROBABILITY_TICK_VALUES`, scatter.js).
    context.fillStyle = mutedColor;
    context.font = "10px sans-serif";
    context.textAlign = "right";
    context.textBaseline = "middle";
    for (const tick of PROBABILITY_TICK_VALUES) {
        context.fillText(tick.toFixed(1), plotLeft - 6, yToPixel(tick));
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

    /**
     * @param {"D"|"G_ST"} key
     * @param {string} color
     */
    function drawLine(key, color) {
        context.strokeStyle = color;
        context.lineWidth = 2;
        context.beginPath();
        let started = false;
        for (const point of sweep.points) {
            const value = point[key];
            if (value === null || value === undefined) {
                // A gap in the underlying prediction (e.g. `D` at
                // `mu == 0`) breaks the line rather than interpolating
                // across an undefined value.
                started = false;
                continue;
            }
            const x = xToPixel(point.x);
            const y = yToPixel(Math.min(1, Math.max(0, value)));
            if (started) {
                context.lineTo(x, y);
            } else {
                context.moveTo(x, y);
                started = true;
            }
        }
        context.stroke();
    }
    drawLine("D", "#1f6fb2");
    drawLine("G_ST", "#d97a26");

    // The configuration's own current value on this axis.
    context.setLineDash([4, 3]);
    context.strokeStyle = mutedColor;
    context.lineWidth = 1;
    const currentX = xToPixel(sweep.current);
    context.beginPath();
    context.moveTo(currentX, plotTop);
    context.lineTo(currentX, plotBottom);
    context.stroke();
    context.setLineDash([]);
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
        exploreCanvas.getContext("2d").clearRect(0, 0, exploreCanvas.width, exploreCanvas.height);
        window.__fimExploreReady = true;
        return;
    }
    exploreBanner.hidden = true;
    explorePredictions.hidden = false;
    for (const [name, label] of Object.entries(EXPLORE_PREDICTION_LABELS)) {
        const row = document.getElementById(`explore-stat-${name}`);
        if (row !== null) {
            applyStatRow(row, buildPointMeter(label, predictionsResult.predictions[name]));
        }
    }

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
    window.__fimExploreReady = true;
}

for (const field of [exploreN, exploreD, exploreM, exploreMu, exploreAxis]) {
    field.addEventListener("change", () => {
        window.__fimExploreReady = false;
        refreshExplore();
    });
}

exploreBackButton.addEventListener("click", () => {
    window.fim.showScreen(exploreReturnScreen);
});

/**
 * Show Explore, recording the screen shown before it so "Back" returns
 * there. The four fields are seeded once per launch, from the same
 * `get_starter_form` values a brand-new Configure form starts with
 * (`N`/`d`/`m_rate`/`mu_value`) — not hardcoded here a second time,
 * so the two can never quietly drift apart — and are left exactly as
 * the user set them on every subsequent visit within this launch.
 */
window.fim.showExplore = async function showExplore() {
    const currentlyVisible = document.querySelector(".screen:not([hidden])");
    if (currentlyVisible !== null && currentlyVisible.id !== "screen-explore") {
        exploreReturnScreen = currentlyVisible.id;
    }
    if (!exploreSeeded) {
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
