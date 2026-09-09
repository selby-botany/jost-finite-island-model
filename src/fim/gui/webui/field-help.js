"use strict";

/* Inline field tooltips (botanist GUI design doc `20260907-claude-
 * sonnet-5-botanist-gui-redesign.md` §4.6): every Configure field and
 * mode-selector group carries a hover/focus tooltip, closing open-
 * issues-rollup item 37 ("tooltips fully scoped, never shipped, never
 * tracked as deferred").
 *
 * `FIELD_HELP` is the "single field-help content source" §4.6 itself
 * calls for -- a plain object, one entry per `config_form.py` field
 * name or mode-selector group, kept honest against real field names by
 * `test_field_help.py`'s own regression test (every key must name a
 * real `label[for="field-<key>"]`/`legend[data-field-help]` this screen
 * actually has, and vice versa -- both directions, so a field added
 * later without a tooltip is caught too, not only a stale key for a
 * field that no longer exists). Shared with the in-app Help pages only
 * in the
 * sense §4.6 cares about -- "never independently drift" -- by staying
 * *shorter than, and consistent with*, `doc/configuration.md`'s own
 * per-field prose rather than a byte-for-byte duplicate of it: a
 * tooltip is a one-line reminder, not the full reference, and the two
 * are written by the same author, from the same source document,
 * side by side, rather than one being generated from the other.
 *
 * A field whose value comes from an opt-in-shorthand expansion states
 * that provenance directly in its own tooltip (§4.6's own example, μ_b
 * -> per-locus mu) rather than only in the longer reference.
 */
const FIELD_HELP = {
    N: "Gene copies per deme — 2× individuals for a diploid locus, " +
        "individuals unchanged for haploid. Per-deme mode gives each deme " +
        "its own size.",
    d: "Number of demes (islands). At least 2.",
    seed: "Seed for this run's random number generator. The same seed and " +
        "configuration always produce byte-identical results.",
    n_mode: "Same for every deme uses one N for all demes; per-deme " +
        "lets each deme have its own population size.",
    m_mode: "How migration between demes is configured: a single scalar " +
        "rate, a named spatial topology (ring/linear), or a full d-by-d " +
        "matrix.",
    m_rate: "Migration rate — the fraction of each deme's own frequency " +
        "vector replaced by migrants each generation (symmetric island " +
        "model).",
    m_topology: "ring wraps around in a circle; linear is a bounded chain " +
        "with no wraparound. Each deme migrates only with its actual " +
        "neighbors.",
    m_topology_rate: "Each deme's total outgoing migration fraction, split " +
        "evenly among its actual neighbors.",
    mu_mode: "Specify the per-generation mutation rate directly, or derive " +
        "it from a per-base rate that scales with each locus's own length.",
    mu_value: "Per-gene-copy mutation probability per generation, applied " +
        "identically to every locus.",
    mu_b_value: "Per-base-pair mutation probability; each locus's own mu " +
        "is derived from this and its length (Eq. 5) — edit this field, " +
        "not mu directly.",
    migrant_sampling: "continuous: each generation's migrant count is an " +
        "exact fraction of N. stochastic: it is drawn fresh from a " +
        "Binomial distribution every generation.",
    mutation_model: "infinite_alleles: every mutation creates a brand-new " +
        "allele. finite_alleles: mutations draw from a bounded set of " +
        "4^length possible states.",
    deme_weighting: "How E_ST weights demes: by population size, or " +
        "equally regardless of size. D and K_ST always weight demes " +
        "equally.",
    loci_mode: "length(s) only: one shared or per-locus length, " +
        "sequential IDs. custom locus IDs: pick your own ID and length " +
        "per locus.",
    locus_lengths: "One length shared by every locus, or one length per " +
        "locus, comma-separated.",
    initial_conditions_mode: "How generation 0 starts: a random Dirichlet " +
        "draw, a simulated ancestral population split, explicit " +
        "frequencies, or every deme fixed for one allele.",
    initial_allele_count: "Number of founding allele IDs (0 through this " +
        "minus 1) the Dirichlet draw starts from.",
    initial_concentration: "Concentration parameter of the Dirichlet draw " +
        "— smaller values produce more uneven starting frequencies.",
    equilibrium_convergence_window: "Trailing window, in generations of " +
        "the ancestral population's own pre-run simulation, whose first " +
        "and second halves are compared for stability.",
    equilibrium_convergence_tolerance: "The ancestral population is " +
        "considered stable once its trailing window's two half-means " +
        "differ by at most this much.",
    equilibrium_max_generations: "Safety cap on the ancestral " +
        "population's own pre-run simulation, independent of the main " +
        "run's own max generations.",
    cs_group: "Which statistic(s) to watch for convergence. Checking more " +
        "than one reveals the combinator below.",
    convergence_combinator: "Only meaningful with more than one watched " +
        "statistic: all requires every one to be stable; any stops once " +
        "one is.",
    convergence_window: "Trailing window, in generations, whose first and " +
        "second halves are compared for stability.",
    convergence_tolerance: "The run is considered converged once the " +
        "trailing window's two half-means differ by at most this much.",
    max_generations: "Hard cap on generations. Reaching it without " +
        "converging is still reported as a valid, non-converged outcome.",
    n_replicates: "Number of independently seeded replicate runs. 1 means " +
        "a single ordinary run with no batching.",
    replicate_tolerance: "Stop the batch early once every watched " +
        "statistic's cross-replicate confidence interval is at most this " +
        "wide. Blank means the 0.01 default; explicitly cleared disables " +
        "early stopping.",
    replicate_minimum: "Fewest replicates run before replicate tolerance " +
        "is even checked, guarding against an early lucky-tight fluke.",
    replicate_confidence: "Confidence level for the cross-replicate " +
        "interval reported once a batch finishes.",
    max_workers: "How many replicates run in parallel. Not a model " +
        "parameter — defaults to this machine's own CPU count.",
    significant_digits: "How many digits every displayed statistic rounds " +
        "to. Cosmetic only — saved files always keep full precision.",
};

window.FIM_FIELD_HELP = FIELD_HELP;

let tooltipElement = null;

/**
 * Lazily create the one floating tooltip bubble every field/group
 * shares -- appended to `document.body`, not near any one field, so
 * its own position (set per-show, below) is never constrained by
 * whichever `.configure-panel`'s own `overflow: auto`/`max-height`
 * happens to clip it.
 * @returns {HTMLElement}
 */
function ensureTooltipElement() {
    if (tooltipElement === null) {
        tooltipElement = document.createElement("div");
        tooltipElement.className = "field-tooltip";
        tooltipElement.hidden = true;
        document.body.appendChild(tooltipElement);
    }
    return tooltipElement;
}

/**
 * Show the tooltip bubble anchored just below `anchor`.
 * @param {HTMLElement} anchor
 * @param {string} text
 */
function showFieldTooltip(anchor, text) {
    const tooltip = ensureTooltipElement();
    tooltip.textContent = text;
    tooltip.hidden = false;
    const rect = anchor.getBoundingClientRect();
    tooltip.style.left = `${Math.max(4, rect.left)}px`;
    tooltip.style.top = `${rect.bottom + 4}px`;
}

function hideFieldTooltip() {
    if (tooltipElement !== null) {
        tooltipElement.hidden = true;
    }
}

/**
 * Wire one field's own `<label>` (hover) and its associated input
 * (focus, for a keyboard-only user who tabs to the field itself rather
 * than pointing at its label) to show `FIELD_HELP[key]`.
 * @param {HTMLLabelElement} labelElement
 * @param {string} key
 */
function wireFieldTooltip(labelElement, key) {
    const text = FIELD_HELP[key];
    if (!text) {
        return;
    }
    labelElement.addEventListener("mouseenter", () =>
        showFieldTooltip(labelElement, text)
    );
    labelElement.addEventListener("mouseleave", hideFieldTooltip);
    const input = document.getElementById(labelElement.htmlFor);
    if (input !== null) {
        input.addEventListener("focus", () => showFieldTooltip(labelElement, text));
        input.addEventListener("blur", hideFieldTooltip);
    }
}

/**
 * Wire one mode-selector group's own `<legend>` (hover, and focus for a
 * keyboard-only user -- a `<legend>` is not natively focusable, so this
 * opts it into the tab order the same way `modal-presets`'s own "Close"
 * button already needed to, `app.js`'s `wireModal` docstring has the
 * full WKWebView-specific reasoning) to show `FIELD_HELP[key]`.
 * @param {HTMLElement} legendElement
 * @param {string} key
 */
function wireGroupTooltip(legendElement, key) {
    const text = FIELD_HELP[key];
    if (!text) {
        return;
    }
    legendElement.tabIndex = 0;
    legendElement.addEventListener("mouseenter", () =>
        showFieldTooltip(legendElement, text)
    );
    legendElement.addEventListener("mouseleave", hideFieldTooltip);
    legendElement.addEventListener("focus", () =>
        showFieldTooltip(legendElement, text)
    );
    legendElement.addEventListener("blur", hideFieldTooltip);
}

/**
 * Wire every field label and `[data-field-help]` legend inside
 * Configure. Called once, at parse time -- unlike almost everything
 * else this page wires, Configure's own field markup is static HTML
 * present from first load, not built by a later bridge call, so there
 * is nothing to wait for.
 */
function wireAllFieldTooltips() {
    document
        .querySelectorAll('#screen-configure label[for^="field-"]')
        .forEach((label) => {
            wireFieldTooltip(label, label.htmlFor.slice("field-".length));
        });
    document
        .querySelectorAll("#screen-configure legend[data-field-help]")
        .forEach((legend) => {
            wireGroupTooltip(legend, legend.dataset.fieldHelp);
        });
}

wireAllFieldTooltips();
