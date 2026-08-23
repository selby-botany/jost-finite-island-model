"use strict";

/* Shared statistic meter (visualization-and-config-editors design §3.3) --
 * a horizontal `[0, 1]` track with a mean tick and, when there is a real
 * confidence interval to show, a shaded low-to-high fill. Extracted
 * unchanged from `screens/batch-results.js`'s own original `percentage
 * Within`/`buildCiBar`/`buildOmittedCiBar` (a pure refactor, not a
 * redesign) so `screens/results.js`'s scalar Screen 3 can show its six
 * statistics through the identical widget instead of plain text --
 * "one consistent way this app shows where a number sits on `[0, 1]`,"
 * not two (design §3.3).
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
    label.textContent = name;
    row.appendChild(label);
    const omitted = document.createElement("span");
    omitted.className = "ci-bar-omitted";
    omitted.textContent = omittedText;
    row.appendChild(omitted);
    return row;
}

/**
 * Build one meter row for a statistic with a real confidence interval.
 *
 * `interval.mean`/`.low`/`.high` arrive pre-formatted for display
 * (`format_statistic`, a `%.6g`-style string) -- parsed back into a
 * number here only to compute bar *geometry*, never to reformat the
 * label text itself (design §3.3's own "the client never reimplements
 * Python's own display formatting" rule, carried over unchanged from
 * this function's own pre-extraction form).
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
    label.textContent = name;
    row.appendChild(label);

    const track = document.createElement("div");
    track.className = "ci-bar-track";
    const fill = document.createElement("div");
    fill.className = "ci-bar-fill";
    fill.style.left = `${low}%`;
    fill.style.width = `${Math.max(high - low, 0)}%`;
    track.appendChild(fill);
    const meanMark = document.createElement("div");
    meanMark.className = "ci-bar-mean";
    meanMark.style.left = `${mean}%`;
    track.appendChild(meanMark);
    row.appendChild(track);

    const value = document.createElement("span");
    value.className = "ci-bar-value";
    value.textContent =
        `${interval.mean} [${interval.low}, ${interval.high}] (n=${interval.sampleCount})`;
    row.appendChild(value);

    return row;
}

/**
 * Build one meter row for a single point value -- a scalar run's own
 * statistic (design §3.3's "point only" mode): a mean tick with no
 * shaded interval, since a single run has no confidence interval to show.
 *
 * No separate `.ci-bar-label` element: `text` already carries the
 * statistic's own name (`"D = 0.0421"`, the exact `"<name> = <value>"`
 * convention Screen 3's plain-text stats always used), so `#stat-D`'s
 * own aggregate `textContent` is unchanged from before this meter
 * existed -- `test/gui/test_results_screen.py`'s own `startswith("D = ")`
 * assertion keeps working without modification, a deliberate choice, not
 * an oversight (a redundant `<span>D</span>D = 0.0421` would be a
 * regression, not a richer view).
 *
 * @param {string} text - The full `"<name> = <value>"` string to show.
 * @param {string} formattedValue - The bare, already `format_statistic`-
 *     formatted value, parsed only to place the mean tick -- never
 *     reformatted or shown a second time.
 * @returns {HTMLDivElement}
 */
function buildPointMeter(text, formattedValue) {
    const row = document.createElement("div");
    row.className = "ci-bar";

    const track = document.createElement("div");
    track.className = "ci-bar-track";
    const parsed = Number(formattedValue);
    if (Number.isFinite(parsed)) {
        const position = percentageWithin(parsed, METER_MIN, METER_MAX);
        const mark = document.createElement("div");
        mark.className = "ci-bar-mean";
        mark.style.left = `${position}%`;
        track.appendChild(mark);
    }
    row.appendChild(track);

    const value = document.createElement("span");
    value.className = "ci-bar-value";
    value.textContent = text;
    row.appendChild(value);

    return row;
}
