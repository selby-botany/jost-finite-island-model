"use strict";

/* Shared statistic table row (visualization-and-config-editors design
 * §3.3 revision) -- each named statistic renders as one `<tr>` of two
 * cells (name, value to two digits) inside a `.stats-table` beside the
 * plot, with the row's own `title` attribute carrying the full-precision
 * value (and CI, when there is one) as a native hover tooltip. Replaces
 * the earlier "dot + error bar" track widget (`buildCiBar`/
 * `buildOmittedCiBar` from `screens/batch-results.js`, later `buildCiMeter`/
 * `buildOmittedMeter`/`buildPointMeter`'s own first, row-of-tracks form)
 * with a table layout -- same three build functions, same call sites,
 * new DOM shape.
 *
 * A classic, non-module script sharing the page's one global scope
 * (`index.html` has no `type="module"` on any `<script>` tag), the same
 * shape every other `webui/*.js` file already uses.
 */

/**
 * Format a `format_statistic`-formatted value string to exactly two
 * decimal digits for the table's own value column. Falls back to the
 * original string unchanged if it does not parse as a number (should
 * not happen for these six statistics, but a fallback costs nothing).
 *
 * @param {string} formattedValue
 * @returns {string}
 */
function formatToTwoDigits(formattedValue) {
    const parsed = Number(formattedValue);
    return Number.isFinite(parsed) ? parsed.toFixed(2) : String(formattedValue);
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
 * Build the two `<td>` cells (name, value) shared by every row shape
 * below, as a `DocumentFragment` -- both slot-based call sites
 * (`replaceChildren` on a pre-existing `<tr>`) and the batch call site
 * (`appendChild` into a freshly created `<tr>`) can use a fragment the
 * same way, since both DOM APIs unpack a fragment into its children.
 * `tooltip` (the full-precision value, plus CI when there is one) goes
 * on the row's own `title` -- fragments cannot carry attributes
 * themselves, so the caller applies it after this returns.
 *
 * @param {string} name
 * @param {string} valueText - Already display-formatted (two digits,
 *     or the omitted placeholder).
 * @returns {DocumentFragment}
 */
function buildStatCells(name, valueText) {
    const cells = document.createDocumentFragment();
    const nameCell = document.createElement("td");
    nameCell.className = "stat-name";
    nameCell.innerHTML = formatStatisticLabel(name);
    cells.appendChild(nameCell);
    const valueCell = document.createElement("td");
    valueCell.className = "stat-value";
    valueCell.textContent = valueText;
    cells.appendChild(valueCell);
    return cells;
}

/**
 * Build one table row's cells for a statistic with no defined interval
 * to show (a batch summary statistic with fewer than two defined
 * replicates). The value column shows an em dash; the tooltip carries
 * the reason.
 *
 * @param {string} name
 * @param {string} omittedText
 * @returns {DocumentFragment}
 */
function buildOmittedMeter(name, omittedText) {
    const cells = buildStatCells(name, "—");
    cells.tooltip = omittedText;
    return cells;
}

/**
 * Build one table row's cells for a statistic with a confidence
 * interval (a batch summary statistic). The value column shows the
 * mean to two digits; hovering the row shows `"mean [low, high]"` at
 * full `format_statistic` precision.
 *
 * `interval.mean`/`.low`/`.high` arrive pre-formatted for display
 * (`format_statistic`, a `%.6g`-style string) -- parsed back into a
 * number here only for the two-digit value column, never reformatted
 * for the tooltip.
 *
 * @param {string} name
 * @param {{mean: string, low: string, high: string, sampleCount: number}} interval
 * @returns {DocumentFragment}
 */
function buildCiMeter(name, interval) {
    const cells = buildStatCells(name, formatToTwoDigits(interval.mean));
    cells.tooltip = `${interval.mean} [${interval.low}, ${interval.high}]`;
    return cells;
}

/**
 * Build one table row's cells for a single point value (scalar run or
 * p_0 preview, no CI to show). The value column shows the value to two
 * digits; hovering the row shows `"Name = value"` at full
 * `format_statistic` precision.
 *
 * @param {string} name - The statistic's own name (`"D"`, `"G_ST"`, ...).
 * @param {string} formattedValue - The already `format_statistic`-formatted
 *     value -- shown in the tooltip verbatim, rounded to two digits for
 *     the value column.
 * @returns {DocumentFragment}
 */
function buildPointMeter(name, formattedValue) {
    const cells = buildStatCells(name, formatToTwoDigits(formattedValue));
    const plainName = formatStatisticLabel(name).replace(/<[^>]*>/g, "");
    cells.tooltip = `${plainName} = ${formattedValue}`;
    return cells;
}

/**
 * Apply a `buildXMeter` fragment's own `.tooltip` to `row`'s `title`
 * attribute, then replace `row`'s children with the fragment's cells.
 * Every call site needs both steps together (a fragment alone cannot
 * carry the tooltip; `replaceChildren`/`appendChild` alone would drop
 * it) -- centralized here so no call site can do one without the other.
 *
 * @param {HTMLTableRowElement} row
 * @param {DocumentFragment} cells
 */
function applyStatRow(row, cells) {
    row.title = cells.tooltip || "";
    row.replaceChildren(cells);
}
