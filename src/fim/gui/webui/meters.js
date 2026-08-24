"use strict";

/* Shared statistic meter (visualization-and-config-editors design §3.3) --
 * a compact "dot + error bar" widget: a thin horizontal [0, 1] track with
 * a dot at the mean and vertical whisker caps at the CI low/high bounds.
 * Hover shows the exact values. Extracted from `screens/batch-results.js`'s
 * original `percentageWithin`/`buildCiBar`/`buildOmittedCiBar` and later
 * redesigned for compactness.
 *
 * A classic, non-module script sharing the page's one global scope
 * (`index.html` has no `type="module"` on any `<script>` tag), the same
 * shape every other `webui/*.js` file already uses.
 */

// D/G_ST/E_ST/K_ST/H_S/H_T are every named differentiation/heterozygosity
// statistic this project reports, and each is naturally bounded to
// [0, 1] by construction (Jost's D, Nei's G_ST, and the heterozygosities
// alike) -- the one fixed scale every meter below is drawn against, with
// no per-statistic dynamic scaling needed.
const METER_MIN = 0.0;
const METER_MAX = 1.0;

/**
 * Return where `value` sits between `min` and `max`, as a 0-100 percentage.
 *
 * @param {number} value
 * @param {number} min
 * @param {number} max
 * @returns {number}
 */
function percentageWithin(value, min, max) {
    const clamped = Math.min(Math.max(value, min), max);
    return ((clamped - min) / (max - min)) * 100;
}

/**
 * Render a statistic name's own `_`-suffix as a real subscript -- `"G_ST"`
 * becomes `G<sub>ST</sub>` (TeX's own `_` subscript convention, not a
 * literal underscore character on screen), `"D"` (no `_`) is returned
 * unchanged. Every statistic name reaching this function comes from a
 * fixed, hardcoded set (`results.js`'s/`batch-results.js`'s own
 * `STATISTIC_NAMES`), never user input, so returning HTML for a caller
 * to assign via `innerHTML` is safe here.
 *
 * @param {string} name
 * @returns {string}
 */
function formatStatisticLabel(name) {
    const underscoreIndex = name.indexOf("_");
    if (underscoreIndex === -1) {
        return name;
    }
    const base = name.slice(0, underscoreIndex);
    const subscript = name.slice(underscoreIndex + 1);
    return `${base}<sub>${subscript}</sub>`;
}

/**
 * Build one meter row for a statistic with no defined interval to show.
 *
 * @param {string} name
 * @param {string} omittedText
 * @returns {HTMLDivElement}
 */
function buildOmittedMeter(name, omittedText) {
    const row = document.createElement("div");
    row.className = "ci-bar";
    const label = document.createElement("span");
    label.className = "ci-bar-label";
    label.innerHTML = formatStatisticLabel(name);
    row.appendChild(label);
    const omitted = document.createElement("span");
    omitted.className = "ci-bar-omitted";
    omitted.textContent = omittedText;
    row.appendChild(omitted);
    return row;
}

/**
 * Build a compact dot+error-bar meter for a statistic with a CI.
 *
 * A thin horizontal track spans [0, 1]. A filled dot marks the mean.
 * Vertical whisker caps mark low and high. Hovering the track shows
 * `"mean [low, high]"` — no separate text row below.
 *
 * `interval.mean`/`.low`/`.high` arrive pre-formatted for display
 * (`format_statistic`, a `%.6g`-style string) -- parsed back into a
 * number here only to compute geometry, never reformatted.
 *
 * @param {string} name
 * @param {{mean: string, low: string, high: string, sampleCount: number}} interval
 * @returns {HTMLDivElement}
 */
function buildCiMeter(name, interval) {
    const low = percentageWithin(Number(interval.low), METER_MIN, METER_MAX);
    const high = percentageWithin(Number(interval.high), METER_MIN, METER_MAX);
    const mean = percentageWithin(Number(interval.mean), METER_MIN, METER_MAX);

    const row = document.createElement("div");
    row.className = "ci-bar";

    const label = document.createElement("span");
    label.className = "ci-bar-label";
    label.innerHTML = formatStatisticLabel(name);
    row.appendChild(label);

    // Track wrapper holds all geometric elements and carries the tooltip.
    const track = document.createElement("div");
    track.className = "ci-bar-track";
    track.title = `${interval.mean} [${interval.low}, ${interval.high}]`;

    const whiskerLow = document.createElement("div");
    whiskerLow.className = "ci-bar-whisker";
    whiskerLow.style.left = `${low}%`;
    track.appendChild(whiskerLow);

    const whiskerHigh = document.createElement("div");
    whiskerHigh.className = "ci-bar-whisker";
    whiskerHigh.style.left = `${high}%`;
    track.appendChild(whiskerHigh);

    const dot = document.createElement("div");
    dot.className = "ci-bar-dot";
    dot.style.left = `${mean}%`;
    track.appendChild(dot);

    row.appendChild(track);
    return row;
}

/**
 * Build a compact dot meter for a single point value (scalar run).
 *
 * A filled dot on the track marks the mean; no CI interval to show.
 * Hovering shows `"Name = value"`. No separate text row below.
 *
 * No separate `.ci-bar-label` element: the label is shown only in the
 * tooltip, and the row carries just label + track (same grid shape as
 * `buildCiMeter`).
 *
 * @param {string} name - The statistic's own name (`"D"`, `"G_ST"`, ...).
 * @param {string} formattedValue - The already `format_statistic`-formatted
 *     value, parsed only to place the dot -- never reformatted.
 * @returns {HTMLDivElement}
 */
function buildPointMeter(name, formattedValue) {
    const row = document.createElement("div");
    row.className = "ci-bar";

    const label = document.createElement("span");
    label.className = "ci-bar-label";
    label.innerHTML = formatStatisticLabel(name);
    row.appendChild(label);

    const track = document.createElement("div");
    track.className = "ci-bar-track";
    track.title = `${formatStatisticLabel(name).replace(/<[^>]*>/g, "")} = ${formattedValue}`;

    const parsed = Number(formattedValue);
    if (Number.isFinite(parsed)) {
        const position = percentageWithin(parsed, METER_MIN, METER_MAX);
        const dot = document.createElement("div");
        dot.className = "ci-bar-dot";
        dot.style.left = `${position}%`;
        track.appendChild(dot);
    }
    row.appendChild(track);

    return row;
}
