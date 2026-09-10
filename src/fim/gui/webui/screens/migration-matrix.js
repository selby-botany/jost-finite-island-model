"use strict";

/* The migration-matrix grid editor (botanist GUI design doc
 * `20260907-claude-sonnet-5-botanist-gui-redesign.md` §4.4): a d-by-d
 * table of directly-editable cells, kept in sync with `field-m_matrix_
 * json` (the hidden field `config_form.m_to_payload`/`m_from_params`
 * actually read and write) in both directions -- editing a cell updates
 * that hidden field; loading a configuration (`applyFormValues`, via
 * `window.fim.rebuildMigrationMatrixGrid`) rebuilds the visible cells
 * from it. Replaces the earlier, read-only "loaded from file" badge: a
 * matrix-shaped `m` (a full matrix, a sparse map, or an expanded
 * stepping-stone topology -- `m_from_params`'s own docstring) is now
 * genuinely editable, not frozen behind a size summary.
 */

const mMatrixGrid = document.getElementById("m-matrix-grid");
const mMatrixJsonField = document.getElementById("field-m_matrix_json");

// A brand-new grid cell (no value carried over from a loaded
// configuration) defaults to "this deme keeps everything, migrates with
// no one" -- an identity matrix, the same trivially-valid starting
// point `initial_allele_count`/`initial_concentration`'s own library
// defaults already favor: every row already sums to 1, so nothing
// warns the moment the grid first appears.
const DEFAULT_MATRIX_SELF_RETENTION = 1;
const DEFAULT_MATRIX_SIZE = 2;
const ROW_SUM_TOLERANCE = 1e-6;

/**
 * Read the grid's own current cell values as a `size`-by-`size` array.
 * @returns {number[][]}
 */
function readMatrixGridValues() {
    return Array.from(mMatrixGrid.querySelectorAll("tbody tr")).map((row) =>
        Array.from(row.querySelectorAll(".matrix-cell")).map(
            (input) => Number(input.value) || 0
        )
    );
}

/**
 * Recompute and show each row's own sum, highlighting a row that does
 * not (very nearly) sum to 1 -- a live version of the same row-sum
 * check `SimulationParams.from_mapping` itself enforces server-side, so
 * a mistake is visible while editing, not only after "Run simulation".
 */
function updateMatrixRowSums() {
    for (const row of mMatrixGrid.querySelectorAll("tbody tr")) {
        const cells = Array.from(row.querySelectorAll(".matrix-cell"));
        const sum = cells.reduce(
            (total, input) => total + (Number(input.value) || 0),
            0
        );
        const sumCell = row.querySelector(".matrix-row-sum");
        sumCell.textContent = sum.toFixed(3);
        // Toggled on the whole `tr`, not just the sum cell, so the row
        // label and every input in an invalid row "pop" in the same
        // saturated, bold red as the sum itself, not just the sum alone.
        row.classList.toggle(
            "matrix-row-invalid",
            Math.abs(sum - 1) > ROW_SUM_TOLERANCE
        );
    }
}

/**
 * Write the grid's own current values into `field-m_matrix_json`, and
 * refresh the row-sum indicators -- called after every cell edit.
 */
function syncMatrixJsonField() {
    mMatrixJsonField.value = JSON.stringify(readMatrixGridValues());
    updateMatrixRowSums();
}

/**
 * Build a fresh `size`-by-`size` grid, taking each cell from `values`
 * where present, or `DEFAULT_MATRIX_SELF_RETENTION`/`0` (identity)
 * otherwise -- the same "grow pads with a sensible default, shrink
 * simply drops the extra rows/columns" resizing behavior the design
 * doc's own §4.1 describes for the per-deme N table, applied here to
 * `d` changes while matrix mode is active.
 * @param {number} size
 * @param {number[][]} values
 */
function buildMatrixGrid(size, values) {
    mMatrixGrid.replaceChildren();
    const table = document.createElement("table");

    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    headRow.appendChild(document.createElement("th"));
    for (let col = 0; col < size; col += 1) {
        const th = document.createElement("th");
        th.textContent = `Deme ${col + 1}`;
        headRow.appendChild(th);
    }
    const sumHeader = document.createElement("th");
    sumHeader.textContent = "sum";
    headRow.appendChild(sumHeader);
    head.appendChild(headRow);
    table.appendChild(head);

    const body = document.createElement("tbody");
    for (let row = 0; row < size; row += 1) {
        const tr = document.createElement("tr");
        const rowHeader = document.createElement("th");
        rowHeader.textContent = `Deme ${row + 1}`;
        tr.appendChild(rowHeader);
        for (let col = 0; col < size; col += 1) {
            const td = document.createElement("td");
            const input = document.createElement("input");
            input.type = "text";
            input.className = "matrix-cell";
            const existingRow = values[row];
            const existingValue =
                existingRow && existingRow[col] !== undefined
                    ? existingRow[col]
                    : row === col
                      ? DEFAULT_MATRIX_SELF_RETENTION
                      : 0;
            input.value = String(existingValue);
            input.addEventListener("input", syncMatrixJsonField);
            td.appendChild(input);
            tr.appendChild(td);
        }
        const sumCell = document.createElement("td");
        sumCell.className = "matrix-row-sum";
        tr.appendChild(sumCell);
        body.appendChild(tr);
    }
    table.appendChild(body);
    mMatrixGrid.appendChild(table);
    syncMatrixJsonField();
}

/**
 * Rebuild the grid from `field-d`'s own current value and `field-
 * m_matrix_json`'s own current value (both already set by the time this
 * runs, by `applyFormValues` or by direct editing) -- the "loading a
 * configuration" direction, and the "d changed" direction alike. A
 * malformed or absent JSON value builds a fresh identity matrix instead
 * of leaving the grid blank.
 */
window.fim.rebuildMigrationMatrixGrid = function rebuildMigrationMatrixGrid() {
    const dField = document.getElementById("field-d");
    const size = Math.max(DEFAULT_MATRIX_SIZE, parseInt(dField.value, 10) || 0);
    let values = [];
    try {
        const parsed = JSON.parse(mMatrixJsonField.value || "[]");
        if (Array.isArray(parsed)) {
            values = parsed;
        }
    } catch {
        values = [];
    }
    buildMatrixGrid(size, values);
};

// Selecting "full matrix" mode (whether for the first time this launch,
// or returning to it after switching away) rebuilds from whatever is
// already in `field-m_matrix_json` -- an identity matrix the first
// time, the user's own already-entered values every time after.
document
    .querySelector('input[name="m_mode"][value="matrix"]')
    .addEventListener("change", () => {
        window.fim.rebuildMigrationMatrixGrid();
    });

// Changing `d` resizes the grid, preserving already-entered values
// where indices still exist -- wired unconditionally, not only while
// matrix mode is currently visible, so switching to matrix mode later
// already reflects whatever `d` was last set to.
document.getElementById("field-d").addEventListener("input", () => {
    window.fim.rebuildMigrationMatrixGrid();
});
