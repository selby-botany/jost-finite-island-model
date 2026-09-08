"use strict";

/* The explicit p_0 grid editor (botanist GUI design doc
 * `20260907-claude-sonnet-5-botanist-gui-redesign.md` §4.4): a d-by-
 * locus table of small per-cell allele-frequency mappings, kept in sync
 * with `field-p0_json` (the hidden field `config_form.
 * initial_conditions_to_payload`/`initial_conditions_from_params`
 * actually read and write) in both directions -- editing a cell updates
 * that hidden field; loading a configuration (`applyFormValues`, via
 * `window.fim.rebuildP0Grid`) rebuilds the visible cells from it.
 * Unlike the migration-matrix and loci grids, this one has no "add row"
 * control of its own: its shape is entirely governed by `field-d` (row
 * count) and the loci selector (column count/IDs), so it rebuilds
 * whenever either changes rather than growing/shrinking under direct
 * user control. Replaces the earlier read-only "loaded from file"
 * summary (`p0_summary_from_params`, removed): a loaded explicit `p_0`
 * is now genuinely editable, not frozen behind a deme/locus-count
 * badge.
 */

const p0Grid = document.getElementById("p0-grid");
const p0JsonField = document.getElementById("field-p0_json");

// A brand-new cell (no value carried over from a loaded configuration)
// defaults to "this deme starts fixed for allele 0" -- already a valid,
// sum-to-1 mapping the moment it appears, the same "obviously valid
// default" philosophy the migration-matrix grid's own identity-matrix
// default and the loci grid's own default length already follow.
const DEFAULT_P0_CELL_TEXT = "0:1";
const P0_CELL_SUM_TOLERANCE = 1e-6;

/**
 * Return the loci configuration's own current locus IDs, in column
 * order -- from the real, always-populated `#loci-grid` rows
 * (`loci-grid.js`'s own `readLociGridValues`, kept in sync regardless
 * of which loci mode is currently visible) when custom mode is active,
 * or synthesized as sequential `1..count` from the comma-separated
 * `locus_lengths` text otherwise, mirroring `config_form.
 * loci_from_params`'s own "sequential, 1-based" convention for that
 * mode.
 * @returns {number[]}
 */
function currentLocusIds() {
    const lociMode = document.querySelector('input[name="loci_mode"]:checked').value;
    if (lociMode === "custom") {
        return readLociGridValues().map((row) => row.locus_id);
    }
    const lengthsField = document.getElementById("field-locus_lengths");
    const count = lengthsField.value
        .split(",")
        .map((piece) => piece.trim())
        .filter((piece) => piece !== "").length;
    return Array.from({ length: Math.max(1, count) }, (_, index) => index + 1);
}

/**
 * Parse one cell's own compact `alleleId:frequency[,alleleId:frequency
 * ...]` text into a plain `{alleleId: frequency}` object -- allele IDs
 * stay strings (matching `_parse_p0_json`'s own expectation, and JSON's
 * own object-key convention) rather than being parsed to numbers here.
 * @param {string} text
 * @returns {Object<string, number>}
 */
function parseP0CellText(text) {
    const mapping = {};
    const trimmed = text.trim();
    if (trimmed === "") {
        return mapping;
    }
    for (const pair of trimmed.split(",")) {
        const [rawAlleleId, rawFrequency] = pair.split(":");
        if (rawAlleleId === undefined || rawFrequency === undefined) {
            continue;
        }
        mapping[rawAlleleId.trim()] = Number(rawFrequency.trim()) || 0;
    }
    return mapping;
}

/**
 * Format a `{alleleId: frequency}` mapping back into its own cell text.
 * @param {Object<string, number>} mapping
 * @returns {string}
 */
function formatP0CellText(mapping) {
    return Object.entries(mapping)
        .map(([alleleId, frequency]) => `${alleleId}:${frequency}`)
        .join(",");
}

/**
 * Recompute and show one cell's own frequency sum, highlighting a
 * nonempty cell that does not (very nearly) sum to 1 -- a live version
 * of the same per-locus sum check `SimulationParams.from_mapping`
 * itself enforces server-side, mirroring the migration-matrix grid's
 * own row-sum indicator.
 * @param {HTMLInputElement} input
 * @param {HTMLElement} sumSpan
 */
function updateP0CellSum(input, sumSpan) {
    const mapping = parseP0CellText(input.value);
    const alleleIds = Object.keys(mapping);
    if (alleleIds.length === 0) {
        sumSpan.textContent = "";
        sumSpan.classList.remove("p0-cell-sum-invalid");
        return;
    }
    const sum = Object.values(mapping).reduce((total, value) => total + value, 0);
    sumSpan.textContent = sum.toFixed(3);
    sumSpan.classList.toggle(
        "p0-cell-sum-invalid",
        Math.abs(sum - 1) > P0_CELL_SUM_TOLERANCE
    );
}

/**
 * Read the grid's own current cell values as a deme-by-locus array of
 * `{alleleId: frequency}` mappings -- `_parse_p0_json`'s own expected
 * shape verbatim.
 * @returns {Object<string, number>[][]}
 */
function readP0GridValues() {
    return Array.from(p0Grid.querySelectorAll("tbody tr")).map((row) =>
        Array.from(row.querySelectorAll(".p0-cell")).map((input) =>
            parseP0CellText(input.value)
        )
    );
}

/**
 * Write the grid's own current values into `field-p0_json` -- called
 * after every cell edit.
 */
function syncP0JsonField() {
    p0JsonField.value = JSON.stringify(readP0GridValues());
}

/**
 * Build a fresh `d`-by-`locusIds.length` grid, taking each cell from
 * `existingDemes` where present (matched by position, not by locus ID
 * -- the same "grow pads with a default, shrink simply drops the extra
 * rows/columns" resizing behavior the migration-matrix grid's own
 * `buildMatrixGrid` already uses) or `DEFAULT_P0_CELL_TEXT` otherwise.
 * @param {number} d
 * @param {number[]} locusIds
 * @param {Object<string, number>[][]} existingDemes
 */
function buildP0Grid(d, locusIds, existingDemes) {
    p0Grid.replaceChildren();
    const table = document.createElement("table");

    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    headRow.appendChild(document.createElement("th"));
    for (const locusId of locusIds) {
        const th = document.createElement("th");
        th.textContent = `Locus ${locusId}`;
        headRow.appendChild(th);
    }
    head.appendChild(headRow);
    table.appendChild(head);

    const body = document.createElement("tbody");
    for (let row = 0; row < d; row += 1) {
        const tr = document.createElement("tr");
        const rowHeader = document.createElement("th");
        rowHeader.textContent = `Deme ${row + 1}`;
        tr.appendChild(rowHeader);
        for (let col = 0; col < locusIds.length; col += 1) {
            const td = document.createElement("td");
            const input = document.createElement("input");
            input.type = "text";
            input.className = "p0-cell";
            const existingDeme = existingDemes[row];
            const existingLocus = existingDeme && existingDeme[col];
            input.value = existingLocus
                ? formatP0CellText(existingLocus)
                : DEFAULT_P0_CELL_TEXT;
            const sumSpan = document.createElement("span");
            sumSpan.className = "p0-cell-sum";
            input.addEventListener("input", () => {
                updateP0CellSum(input, sumSpan);
                syncP0JsonField();
            });
            td.appendChild(input);
            td.appendChild(sumSpan);
            tr.appendChild(td);
            updateP0CellSum(input, sumSpan);
        }
        body.appendChild(tr);
    }
    table.appendChild(body);
    p0Grid.appendChild(table);
    syncP0JsonField();
}

/**
 * Rebuild the grid from `field-d`'s own current value, the loci
 * selector's own current locus IDs, and `field-p0_json`'s own current
 * value (all already set by the time this runs, by `applyFormValues`
 * or by direct editing) -- the "loading a configuration" direction, and
 * the "d or loci changed" direction alike. A malformed or absent JSON
 * value builds a grid of defaults instead of leaving it blank.
 */
window.fim.rebuildP0Grid = function rebuildP0Grid() {
    const dField = document.getElementById("field-d");
    const d = Math.max(1, parseInt(dField.value, 10) || 0);
    const locusIds = currentLocusIds();
    let existingDemes = [];
    try {
        const parsed = JSON.parse(p0JsonField.value || "[]");
        if (Array.isArray(parsed)) {
            existingDemes = parsed;
        }
    } catch {
        existingDemes = [];
    }
    buildP0Grid(d, locusIds, existingDemes);
};

// Selecting "explicit p_0" mode (whether for the first time this
// launch, or returning to it after switching away) rebuilds from
// whatever is already in `field-p0_json` -- a grid of defaults the
// first time, the user's own already-entered values every time after.
document
    .querySelector('input[name="initial_conditions_mode"][value="explicit_p0"]')
    .addEventListener("change", () => {
        window.fim.rebuildP0Grid();
    });

// Changing `d` resizes the grid's own row count, preserving already-
// entered values where indices still exist -- wired unconditionally,
// not only while explicit mode is currently visible, so switching to
// it later already reflects whatever `d` was last set to.
document.getElementById("field-d").addEventListener("input", () => {
    window.fim.rebuildP0Grid();
});

// Changing the loci configuration's own shorthand form resizes the
// grid's own column count the same way; the custom-grid direction is
// wired from `loci-grid.js`'s own `syncLociJsonField` instead, since
// that is the single place every custom-grid edit already funnels
// through.
document.getElementById("field-locus_lengths").addEventListener("input", () => {
    window.fim.rebuildP0Grid();
});
document.querySelectorAll('input[name="loci_mode"]').forEach((radio) => {
    radio.addEventListener("change", () => {
        window.fim.rebuildP0Grid();
    });
});
