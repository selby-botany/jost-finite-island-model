"use strict";

/* The custom-locus-ID grid editor (botanist GUI design doc
 * `20260907-claude-sonnet-5-botanist-gui-redesign.md` §4.4): one row per
 * locus, each with its own editable `locus_id` and `length`, kept in
 * sync with `field-loci_json` (the hidden field `config_form.
 * loci_to_payload`/`loci_from_params` actually read and write) in both
 * directions -- editing a row updates that hidden field; loading a
 * configuration (`applyFormValues`, via `window.fim.rebuildLociGrid`)
 * rebuilds the visible rows from it. Mirrors `migration-matrix.js`'s own
 * read/sync/rebuild shape exactly, applied here to a variable-length
 * list of rows instead of a fixed d-by-d grid.
 */

const lociGrid = document.getElementById("loci-grid");
const lociJsonField = document.getElementById("field-loci_json");
const lociAddRowButton = document.getElementById("loci-add-row-button");

// A brand-new row (no value carried over from a loaded configuration)
// defaults to the next sequential locus ID and the library's own
// default locus length (`fim.model.params.DEFAULT_LOCUS_LENGTH`) -- the
// same "already valid the moment it appears" starting point the
// migration-matrix grid's own identity-matrix default favors.
const DEFAULT_LOCUS_LENGTH = 200;

/**
 * Read the grid's own current rows as `{locus_id, length}` objects,
 * parsing each cell as an integer (0 for anything that does not parse,
 * matching the migration-matrix grid's own `Number(...) || 0` fallback
 * -- an obviously-invalid placeholder rather than silently dropping the
 * row, so `_parse_loci_json`'s own server-side validation still has a
 * concrete, reportable value to reject).
 * @returns {{locus_id: number, length: number}[]}
 */
function readLociGridValues() {
    return Array.from(lociGrid.querySelectorAll("tbody tr")).map((row) => ({
        locus_id: parseInt(row.querySelector(".loci-id-cell").value, 10) || 0,
        length: parseInt(row.querySelector(".loci-length-cell").value, 10) || 0,
    }));
}

/**
 * Write the grid's own current rows into `field-loci_json` -- called
 * after every cell edit, and after every add/remove-row action. Both
 * the `p_0` grid's own columns and the fixed-per-deme preview's own
 * locus count derive from `currentLocusIds()` (`p0-grid.js`), so every
 * locus-ID change here must also refresh both -- each guarded, since
 * neither script may have run yet the very first time this module's
 * own top-level code builds nothing (no row exists to edit before
 * then).
 */
function syncLociJsonField() {
    lociJsonField.value = JSON.stringify(readLociGridValues());
    if (window.fim.rebuildP0Grid) {
        window.fim.rebuildP0Grid();
    }
    if (window.fim.updateFixedPerDemePreview) {
        window.fim.updateFixedPerDemePreview();
    }
}

/**
 * Append one new row to the grid, defaulting to the next sequential
 * locus ID (one past the highest already present, or `1` for the very
 * first row) and the library's own default length.
 * @param {number} [locusId]
 * @param {number} [length]
 */
function addLociRow(locusId, length) {
    const existing = readLociGridValues();
    const nextId =
        locusId !== undefined
            ? locusId
            : Math.max(0, ...existing.map((row) => row.locus_id)) + 1;
    const row = document.createElement("tr");

    const idCell = document.createElement("td");
    const idInput = document.createElement("input");
    idInput.type = "text";
    idInput.className = "loci-id-cell";
    idInput.value = String(nextId);
    idInput.addEventListener("input", syncLociJsonField);
    idCell.appendChild(idInput);
    row.appendChild(idCell);

    const lengthCell = document.createElement("td");
    const lengthInput = document.createElement("input");
    lengthInput.type = "text";
    lengthInput.className = "loci-length-cell";
    lengthInput.value = String(length !== undefined ? length : DEFAULT_LOCUS_LENGTH);
    lengthInput.addEventListener("input", syncLociJsonField);
    lengthCell.appendChild(lengthInput);
    row.appendChild(lengthCell);

    const removeCell = document.createElement("td");
    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.textContent = "Remove";
    removeButton.setAttribute("aria-label", `Remove locus ${nextId}`);
    removeButton.addEventListener("click", () => {
        // A grid with no rows at all has no valid JSON shape
        // (`_parse_loci_json` rejects an empty list outright) -- leave
        // the last remaining row in place rather than letting "Remove"
        // produce a state "Run simulation" can only ever reject.
        if (lociGrid.querySelectorAll("tbody tr").length > 1) {
            row.remove();
            syncLociJsonField();
        }
    });
    removeCell.appendChild(removeButton);
    row.appendChild(removeCell);

    lociGrid.querySelector("tbody").appendChild(row);
    syncLociJsonField();
}

/**
 * Rebuild every row from `field-loci_json`'s own current value (already
 * set by the time this runs, by `applyFormValues` or by direct editing)
 * -- the "loading a configuration" direction. A malformed, empty, or
 * absent JSON value builds a single default row instead of leaving the
 * grid blank (matching `_parse_loci_json`'s own "must be nonempty"
 * rule, so the grid is never left in a state that could not itself be
 * submitted).
 */
window.fim.rebuildLociGrid = function rebuildLociGrid() {
    lociGrid.querySelector("tbody").replaceChildren();
    let rows = [];
    try {
        const parsed = JSON.parse(lociJsonField.value || "[]");
        if (Array.isArray(parsed) && parsed.length > 0) {
            rows = parsed;
        }
    } catch {
        rows = [];
    }
    if (rows.length === 0) {
        addLociRow(1, DEFAULT_LOCUS_LENGTH);
        return;
    }
    for (const row of rows) {
        addLociRow(row.locus_id, row.length);
    }
};

// Selecting "custom locus IDs" mode (whether for the first time this
// launch, or returning to it after switching away) rebuilds from
// whatever is already in `field-loci_json` -- a single default row the
// first time, the user's own already-entered rows every time after.
document
    .querySelector('input[name="loci_mode"][value="custom"]')
    .addEventListener("change", () => {
        window.fim.rebuildLociGrid();
    });

lociAddRowButton.addEventListener("click", () => {
    addLociRow();
});
