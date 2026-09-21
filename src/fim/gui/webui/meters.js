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

// Two of the literature-derived supplemental statistics
// (`fim.engine._EXPENSIVE_OPT_IN_STATISTICS`) do not follow the plain
// `X_YZ` subscript shape `formatStatisticLabel` otherwise handles
// generically -- their own report field names ("Delta", "MI") are not
// what the differentiation literature actually calls them (Gregorius's
// own δ, conventionally subscripted "G" for "Gregorius" to distinguish
// it from any other δ; Sherwin's own mutual information, conventionally
// "I"). `A_CGD` needs no entry here -- `A<sub>CGD</sub>`, the generic
// split, already matches its own literature notation exactly.
const STATISTIC_LABEL_OVERRIDES = {
    Delta: "δ<sub>G</sub>",
    MI: "I",
    // Explore's own predicted quantities. The four identity/regime
    // names are ordinary prose, not `X_YZ` statistic names, so generic
    // subscript splitting would mangle them (`identity_recovery_half_
    // life` into `identity<sub>recovery_half_life</sub>`); they are
    // listed here so every screen gets the same label from the same
    // one function rather than each keeping its own display map.
    identity_recovery_half_life: "Half-life",
    identity_recovery_rate: "Retention rate",
    identity_recovery_equilibrium: "Equilibrium identity",
    mutation_negligible_equilibrium: "Mutation negligible",
};

/* Short plain-text gloss for each named quantity, shown in the row's
 * own hover tooltip (`buildPointMeter`/`buildCiMeter`/`buildOmitted
 * Meter`, below) rather than in the table itself.
 *
 * These were previously baked into Explore's own visible row labels as
 * a parenthetical, which made its table look nothing like Results' --
 * and, worse, broke the subscript rendering both tables rely on, since
 * `formatStatisticLabel` splits on the *first* underscore and so read
 * "H_S (within-deme heterozygosity)" as the base `H` subscripted by
 * everything after it. Keeping the gloss out of the label fixes both
 * at once: the two tables now render identically, and every name is a
 * clean `X_YZ` that subscripts correctly.
 *
 * Wording follows `doc/jost-differentiation-measures.md` Part VIII's
 * own two-family framing -- `G_ST`/`H_ST` measure nearness to
 * fixation, `D`/`E_ST`/`K_ST` measure allelic differentiation -- so a
 * reader who hovers a row is told which question that number answers,
 * not merely what its letters stand for.
 */
const STATISTIC_DESCRIPTIONS = {
    D: "allelic differentiation, weighting alleles by frequency (Jost's D, q=2)",
    G_ST: "nearness to fixation (Nei's G_ST), not a measure of differentiation",
    E_ST: "allelic differentiation, weighting every allele by its information (q=1)",
    K_ST: "allelic differentiation, counting alleles unique to a deme (q=0)",
    H_S: "within-deme heterozygosity",
    H_T: "pooled heterozygosity across all demes",
    H_ST: "nearness to fixation, Hedrick's maximum-standardized form",
    A_CGD: "coancestry-based genetic distance",
    Delta: "Gregorius's δ — mean pairwise allelic differentiation over deme pairs",
    MI: "Sherwin's mutual information between allele and deme",
    S_S: "within-deme entropy, in nats",
    S_T: "pooled entropy across all demes, in nats",
    A_S: "effective number of alleles per deme",
    A_T: "effective number of alleles pooled across demes",
    identity_recovery_half_life:
        "generations for identity to fall halfway to its equilibrium",
    identity_recovery_rate: "per-generation identity retention rate",
    identity_recovery_equilibrium: "identity by descent at equilibrium",
    mutation_negligible_equilibrium:
        "whether mutation is negligible against migration at equilibrium",
};

/**
 * The short gloss for `name`, or the empty string for a name with none
 * (the two effective-allele rows, which pass their own).
 *
 * @param {string} name
 * @returns {string}
 */
function statisticDescription(name) {
    return STATISTIC_DESCRIPTIONS[name] || "";
}

/**
 * Render a statistic name's own `_`-suffix as a real subscript -- `"G_ST"`
 * becomes `G<sub>ST</sub>` (TeX's own `_` subscript convention, not a
 * literal underscore character on screen), `"D"` (no `_`) is returned
 * unchanged. `STATISTIC_LABEL_OVERRIDES` (above) takes precedence for the
 * two names that generic splitting gets wrong. Every statistic name
 * reaching this function comes from a fixed, hardcoded set
 * (`results.js`'s/`batch-results.js`'s own `STATISTIC_NAMES`), never
 * user input, so returning HTML for a caller to assign via `innerHTML`
 * is safe here.
 *
 * @param {string} name
 * @returns {string}
 */
function formatStatisticLabel(name) {
    if (name in STATISTIC_LABEL_OVERRIDES) {
        return STATISTIC_LABEL_OVERRIDES[name];
    }
    const underscoreIndex = name.indexOf("_");
    if (underscoreIndex === -1) {
        return name;
    }
    const base = name.slice(0, underscoreIndex);
    const subscript = name.slice(underscoreIndex + 1);
    return `${base}<sub>${subscript}</sub>`;
}

/**
 * The plain-text form of `formatStatisticLabel`'s own HTML -- a native
 * `title` tooltip cannot render markup, so `<sub>ST</sub>` has to come
 * back as the `_ST` a reader already recognizes rather than being
 * flattened to a run-together `ST`.
 *
 * Restoring the underscore matters: stripping tags alone turned `H_S`
 * into `HS` and `δ<sub>G</sub>` into `δG`, neither of which names
 * anything.
 *
 * @param {string} name
 * @returns {string}
 */
function plainStatisticLabel(name) {
    return formatStatisticLabel(name)
        .replace(/<sub>([^<]*)<\/sub>/g, "_$1")
        .replace(/<sup>([^<]*)<\/sup>/g, "$1")
        .replace(/<[^>]*>/g, "");
}

/**
 * Assemble one row's own hover tooltip: the leading value clause, then
 * the statistic's short gloss when it has one.
 *
 * Every table row on both Results and Explore goes through here, so
 * the two screens' tooltips differ only in that clause -- a batch
 * summary's carries its confidence interval, a point value does not.
 *
 * @param {string} valueClause - e.g. `"H_S = 0.348"`.
 * @param {string} description - May be empty.
 * @returns {string}
 */
function withStatisticDescription(valueClause, description) {
    return description ? `${valueClause} — ${description}` : valueClause;
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
    // The color-key column, always present and always first so every
    // row on both Results and Explore lines up on the same three
    // columns -- `decorateTrajectoryStatisticRow` (Results) and
    // `decorateExploreStatisticRow` (Explore) fill it with a swatch
    // for a row whose statistic is plottable, and it stays empty for
    // one that is not (the two effective-allele rows). Leaving it out
    // of the non-plottable rows instead would shift their name and
    // value one column left.
    const toggleCell = document.createElement("td");
    toggleCell.className = "stat-plot-toggle";
    cells.appendChild(toggleCell);
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
function buildOmittedMeter(name, omittedText, description) {
    const cells = buildStatCells(name, "—");
    cells.tooltip = withStatisticDescription(
        omittedText,
        description === undefined ? statisticDescription(name) : description
    );
    return cells;
}

/**
 * The cross-replicate confidence interval's own fixed caption (botanist
 * GUI design doc §7.2: re-labeled "everywhere it appears... as
 * 'uncertainty across N independent replicates,' so it is never
 * visually confusable with" the within-run σ band). One function, one
 * wording, every call site this meter reaches -- not independently
 * retyped at each one (`format_statistic`'s own docstring makes the
 * identical argument for Python-side formatting; this is the same
 * discipline applied to a JS-side caption).
 *
 * @param {number} sampleCount
 * @returns {string}
 */
function ciCaption(sampleCount) {
    return `uncertainty across ${sampleCount} independent replicates`;
}

/**
 * Build one table row's cells for a statistic with a confidence
 * interval (a batch summary statistic). The value column shows the
 * mean to two digits; hovering the row shows `"mean [low, high] --
 * uncertainty across N independent replicates; half-width H, equivalent
 * sample standard deviation S"` at full `format_statistic` precision --
 * the two numbers botanist GUI design doc §7.2 asks this tooltip for
 * ("the meter's tooltip states both the confidence-interval half-width
 * and the equivalent sample standard deviation").
 *
 * Spelled out rather than written as `σ`, deliberately: §7.2's own
 * governing requirement is that the cross-replicate interval never be
 * "visually confusable with" the *within-run* σ band, which owns that
 * glyph in this GUI (the `2σ`/`3σ` multiplier, the shaded trajectory
 * region). "half-width" rather than `±` for the same reason in
 * miniature -- `±` is an instruction to add and subtract, true only for
 * a symmetric interval.
 *
 * `halfWidth`/`sampleStd` are present together or absent together
 * (`fim.gui.app._interval_payload`: absent for an interval with no
 * honest symmetric summary, currently only a percentile-bootstrap one).
 * When absent, the whole clause is dropped and the tooltip is exactly
 * the `mean [low, high] -- caption` form this project already shipped,
 * whose `low`/`high` are the authoritative bounds in that case. Not a
 * degraded placeholder: a reader who never sees the clause is told
 * nothing false, and `low`/`high` already answer "how uncertain is
 * this".
 *
 * `interval.mean`/`.low`/`.high`/`.halfWidth`/`.sampleStd` all arrive
 * pre-formatted for display (`format_statistic`, a `%.6g`-style string)
 * -- `mean` is parsed back into a number here only for the two-digit
 * value column, never reformatted for the tooltip.
 *
 * @param {string} name
 * @param {{mean: string, low: string, high: string, sampleCount: number,
 *     halfWidth?: string, sampleStd?: string}} interval
 * @returns {DocumentFragment}
 */
function buildCiMeter(name, interval, description) {
    const cells = buildStatCells(name, formatToTwoDigits(interval.mean));
    const caption = ciCaption(interval.sampleCount);
    let tooltip =
        `${interval.mean} [${interval.low}, ${interval.high}] — ${caption}`;
    if (interval.sampleStd !== undefined && interval.sampleStd !== null) {
        tooltip +=
            `; half-width ${interval.halfWidth}` +
            `, equivalent sample standard deviation ${interval.sampleStd}`;
    }
    cells.tooltip = withStatisticDescription(
        tooltip,
        description === undefined ? statisticDescription(name) : description
    );
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
 * @param {string} [description] - Overrides the `STATISTIC_DESCRIPTIONS`
 *     lookup, for a row whose `name` is a ready-made HTML label rather
 *     than a key (the two effective-allele rows).
 * @returns {DocumentFragment}
 */
function buildPointMeter(name, formattedValue, description) {
    const cells = buildStatCells(name, formatToTwoDigits(formattedValue));
    const plainName = plainStatisticLabel(name);
    cells.tooltip = withStatisticDescription(
        `${plainName} = ${formattedValue}`,
        description === undefined ? statisticDescription(name) : description
    );
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
