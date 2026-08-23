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
    label.innerHTML = formatStatisticLabel(name);
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
 * No separate `.ci-bar-label` element: the one `.ci-bar-value` span
 * carries `"<name> = <value>"` (`name` run through `formatStatisticLabel`
 * for its own `_`-suffix, e.g. `"G<sub>ST</sub> = 0.456"`), the exact
 * convention Screen 3's plain-text stats always used, extended to render
 * the subscript rather than a literal underscore. `test/gui/
 * test_results_screen.py`'s own `startswith("D = ")` assertion still
 * passes unmodified for `D` (no `_`, `formatStatisticLabel` returns it
 * as-is) -- a name that *does* have one now reads back through
 * `.textContent` without its underscore at all (`"GST = ..."`, not
 * `"G_ST = ..."`), since `<sub>` renders visually but contributes no
 * text of its own to close the gap the underscore used to fill; any
 * assertion against an underscore-bearing name's own `.textContent` was
 * updated alongside this change, not left to drift.
 *
 * @param {string} name - The statistic's own name (`"D"`, `"G_ST"`, ...).
 * @param {string} formattedValue - The bare, already `format_statistic`-
 *     formatted value, parsed only to place the mean tick -- never
 *     reformatted or shown a second time.
 * @returns {HTMLDivElement}
 */
function buildPointMeter(name, formattedValue) {
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
    value.innerHTML = `${formatStatisticLabel(name)} = ${formattedValue}`;
    row.appendChild(value);

    return row;
}
