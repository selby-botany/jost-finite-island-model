"use strict";

/* The "fixed per deme" initial-condition preview (botanist GUI design
 * doc `20260907-claude-sonnet-5-botanist-gui-redesign.md` §4.3): a
 * plain-text description of exactly what `config_form.
 * _fixed_per_deme_p0` will expand the checked sub-choice into at
 * submit time, computed live from `field-d` and the loci selector's
 * own current locus count -- unlike the equilibrium-split preview
 * above it, this needs no bridge round trip (no simulation runs; the
 * expansion is a pure, small function of `d`, the locus count, and the
 * choice, mirrored here in JS purely for display), so it updates on
 * every keystroke/click rather than only when the modal closes.
 */

const fixedPerDemePreview = document.getElementById("fixed-per-deme-preview");

/**
 * Describe one sub-choice's own per-deme fixation pattern for a given
 * `d`, in the same terms `_fixed_per_deme_p0`'s own docstring uses.
 * @param {number} d
 * @param {string} choice
 * @returns {string}
 */
function describeFixedPerDeme(d, choice) {
    if (choice === "all_different") {
        const pairs = Array.from(
            { length: d },
            (_, deme) => `deme ${deme + 1} → allele ${deme}`
        );
        return `Fixes: ${pairs.join("; ")}.`;
    }
    if (choice === "all_same") {
        return `Fixes all ${d} deme(s) for allele 0.`;
    }
    if (choice === "all_but_one") {
        if (d < 2) {
            // `_fixed_per_deme_p0` still runs for d=1 (every deme
            // "but the last" is an empty set), producing the same
            // single-deme-fixed-for-allele-1 result "all different"
            // would for d=1 -- described plainly rather than as a
            // vacuous "demes 1-0" range.
            return "Fixes the one deme for allele 1.";
        }
        return `Fixes deme(s) 1-${d - 1} for allele 0, deme ${d} for allele 1.`;
    }
    return "";
}

/**
 * Recompute and show the preview text from the current `d`, loci
 * count, and checked sub-choice. Called on every relevant change --
 * `field-d`, the loci selector (mode radios, `locus_lengths` text, or
 * a custom-grid edit via `loci-grid.js`'s own `syncLociJsonField`), and
 * the `fixed_per_deme_choice` radios themselves.
 */
window.fim.updateFixedPerDemePreview = function updateFixedPerDemePreview() {
    const dField = document.getElementById("field-d");
    const d = Math.max(1, parseInt(dField.value, 10) || 0);
    const lociCount = currentLocusIds().length;
    const choice = document.querySelector(
        'input[name="fixed_per_deme_choice"]:checked'
    ).value;
    const lociNote =
        lociCount > 1 ? ` Applied identically to all ${lociCount} loci.` : "";
    fixedPerDemePreview.textContent = describeFixedPerDeme(d, choice) + lociNote;
};

document.getElementById("field-d").addEventListener("input", () => {
    window.fim.updateFixedPerDemePreview();
});
document.getElementById("field-locus_lengths").addEventListener("input", () => {
    window.fim.updateFixedPerDemePreview();
});
document.querySelectorAll('input[name="loci_mode"]').forEach((radio) => {
    radio.addEventListener("change", () => {
        window.fim.updateFixedPerDemePreview();
    });
});
document.querySelectorAll('input[name="fixed_per_deme_choice"]').forEach((radio) => {
    radio.addEventListener("change", () => {
        window.fim.updateFixedPerDemePreview();
    });
});
