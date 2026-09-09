"use strict";

/* The per-deme population-size grid editor (botanist GUI design doc
 * `20260907-claude-sonnet-5-botanist-gui-redesign.md` §4.1, §4.4): a
 * `d`-row table, one editable `N` per deme, kept in sync with the one
 * real `field-N` in both directions -- editing a row writes a comma-
 * separated list into it; switching into per-deme mode (or `d`
 * changing while already there) rebuilds the table from whatever is
 * already in it. Unlike the migration-matrix/loci/p0 grids, there is no
 * separate hidden field for this one: `config_form.form_values_to_
 * payload` already accepts a bare scalar or a comma-separated per-deme
 * list in `field-N` itself (`test_form_values_to_payload_accepts_a_per_
 * deme_n_list`, predating this toggle) -- `n_mode` is a purely client-
 * side display choice with no `config_form.py` field of its own.
 *
 * Switching modes never rewrites `field-N`'s own value on its own --
 * only which of `n-same-fields`/`n-per-deme-fields` is visible changes.
 * Entering per-deme mode reads whatever is already there (a single
 * scalar replicated across every row, or an already-per-deme list) to
 * seed the table; entering "same for every deme" mode simply reveals
 * the plain field showing that same current value as-is, whatever
 * shape it happens to be in -- consistent with the other grids' own
 * "never silently discard already-entered data on a mode switch" rule.
 */

const nFieldN = document.getElementById("field-N");
const nSameFields = document.getElementById("n-same-fields");
const nPerDemeFields = document.getElementById("n-per-deme-fields");
const nPerDemeGrid = document.getElementById("n-per-deme-grid");

// A brand-new row (more rows than `field-N` currently has values for --
// `d` grew) repeats the last known value, matching the migration-matrix
// grid's own "pad with a sensible default, never a bare zero" instinct.
const DEFAULT_N_VALUE = 450;

/**
 * Parse `field-N`'s own current text into per-deme numbers, resized to
 * exactly `count` entries -- a single scalar replicates across every
 * row; an existing per-deme list is padded (repeating its own last
 * value) or truncated to fit, the same grow/shrink behavior `buildP0
 * Grid`'s own by-position matching already uses.
 * @param {number} count
 * @returns {number[]}
 */
function parseFieldNForGrid(count) {
    const pieces = nFieldN.value
        .split(",")
        .map((piece) => piece.trim())
        .filter((piece) => piece !== "")
        .map((piece) => Number(piece) || 0);
    if (pieces.length === 0) {
        return Array.from({ length: count }, () => DEFAULT_N_VALUE);
    }
    const last = pieces[pieces.length - 1];
    return Array.from({ length: count }, (_, index) => pieces[index] ?? last);
}

/**
 * Read the grid's own current rows.
 * @returns {number[]}
 */
function readNPerDemeValues() {
    return Array.from(nPerDemeGrid.querySelectorAll("tbody .n-per-deme-cell")).map(
        (input) => Number(input.value) || 0
    );
}

/**
 * Write the grid's own current rows into `field-N`, comma-separated --
 * called after every cell edit.
 */
function syncFieldNFromGrid() {
    nFieldN.value = readNPerDemeValues().join(", ");
}

/**
 * Rebuild the table from `field-d`'s own current value and whatever
 * `field-N` already holds (via `parseFieldNForGrid`) -- both the "mode
 * switched to per-deme" and the "d changed while already in per-deme
 * mode" direction.
 */
window.fim.rebuildNPerDemeGrid = function rebuildNPerDemeGrid() {
    const dField = document.getElementById("field-d");
    const d = Math.max(1, parseInt(dField.value, 10) || 0);
    const values = parseFieldNForGrid(d);

    const body = document.createElement("tbody");
    for (let row = 0; row < d; row += 1) {
        const tr = document.createElement("tr");
        const rowHeader = document.createElement("th");
        rowHeader.textContent = `Deme ${row + 1}`;
        tr.appendChild(rowHeader);
        const td = document.createElement("td");
        const input = document.createElement("input");
        input.type = "text";
        input.className = "n-per-deme-cell";
        input.value = String(values[row]);
        input.setAttribute("aria-label", `N for deme ${row + 1}`);
        input.addEventListener("input", syncFieldNFromGrid);
        td.appendChild(input);
        tr.appendChild(td);
        body.appendChild(tr);
    }
    nPerDemeGrid.querySelector("tbody").replaceWith(body);
    syncFieldNFromGrid();
};

/**
 * Show whichever of the scalar field/per-deme grid `n_mode` currently
 * selects -- `field-N` itself always stays in the DOM either way
 * (`form="input-form"` needs it present to be collected regardless of
 * which visual mode is showing), only its own container's visibility
 * changes.
 */
function syncNModeVisibility() {
    const mode = document.querySelector('input[name="n_mode"]:checked').value;
    nSameFields.hidden = mode !== "same";
    nPerDemeFields.hidden = mode !== "per_deme";
    if (mode === "per_deme") {
        window.fim.rebuildNPerDemeGrid();
    }
}

document.querySelectorAll('input[name="n_mode"]').forEach((radio) => {
    radio.addEventListener("change", syncNModeVisibility);
});

// Changing `d` resizes the grid's own row count the same way `d`
// already resizes the p_0 grid -- wired unconditionally, not only while
// per-deme mode is currently visible, so switching to it later already
// reflects whatever `d` was last set to.
document.getElementById("field-d").addEventListener("input", () => {
    if (document.querySelector('input[name="n_mode"]:checked').value === "per_deme") {
        window.fim.rebuildNPerDemeGrid();
    }
});

window.fim.syncNModeVisibility = syncNModeVisibility;

/**
 * Choose `n_mode` from `field-N`'s own just-loaded value, rather than
 * leaving whichever mode was previously selected -- called from
 * `applyFormValues` (`config-modals.js`) after the plain field-copy
 * loop above has already set `field-N` itself. A loaded per-deme list
 * (more than one comma-separated value) selects "per-deme" and rebuilds
 * the grid from it; a bare scalar selects "same for every deme" -- so a
 * loaded configuration always shows in whichever mode actually matches
 * its own `N` shape, not whatever the form happened to be showing
 * before the load.
 */
window.fim.syncNModeFromFieldValue = function syncNModeFromFieldValue() {
    const pieceCount = nFieldN.value
        .split(",")
        .map((piece) => piece.trim())
        .filter((piece) => piece !== "").length;
    const mode = pieceCount > 1 ? "per_deme" : "same";
    const radio = document.querySelector(`input[name="n_mode"][value="${mode}"]`);
    radio.checked = true;
    syncNModeVisibility();
};
