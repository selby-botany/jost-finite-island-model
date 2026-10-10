"use strict";

/* Inline field tooltips (botanist GUI design doc `20260907-claude-
 * sonnet-5-botanist-gui-redesign.md` §4.6): every Configure field and
 * mode-selector group carries a hover/focus tooltip, closing open-
 * issues-rollup item 37 ("tooltips fully scoped, never shipped, never
 * tracked as deferred"). The Settings dialog's own execution/
 * convergence-selection defaults carry the same tooltip mechanism,
 * outside §4.6's own Configure-only scope.
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
    N: "Individuals per deme. The simulator counts gene copies, so this is " +
        "multiplied by the ploidy you chose (diploid: 2 gene copies per " +
        "individual). Per-deme mode gives each deme its own count.",
    d: "Number of demes (islands). At least 2.",
    seed: "Seed for this run's random number generator. The same seed and " +
        "configuration always produce byte-identical results.",
    ploidy: "How many gene copies each individual carries: haploid 1, " +
        "diploid 2, triploid 3, tetraploid 4. New configurations start " +
        "diploid (change that in Settings). Choose it first; the " +
        "simulator counts gene copies, so it multiplies your individuals " +
        "by this number.",
    n_mode: "Same for every deme uses one count of individuals for all " +
        "demes; per-deme lets each deme have its own.",
    m_mode: "How migration between demes is configured: a single scalar " +
        "rate, a named spatial topology (ring/linear/torus), or a full " +
        "d-by-d matrix.",
    m_rate: "Migration rate — the fraction of each deme's own frequency " +
        "vector replaced by migrants each generation (symmetric island " +
        "model).",
    m_topology: "ring wraps around in a circle; linear is a bounded chain " +
        "with no wraparound; torus is a rows-by-columns grid that wraps in " +
        "both directions, so no deme is on an edge. Each deme migrates only " +
        "with its actual neighbors (two for a ring, four for a torus).",
    m_topology_rate: "Each deme's total outgoing migration fraction, split " +
        "evenly among its actual neighbors.",
    m_topology_rows: "Torus only: grid rows. Rows times columns must equal " +
        "d, and each side must be at least 3. Demes are numbered row by " +
        "row.",
    m_topology_columns: "Torus only: grid columns. Rows times columns must " +
        "equal d, and each side must be at least 3.",
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
    equilibrium_convergence_window: "The fewest generations the ancestral " +
        "population's own pre-run simulation runs, however quickly it " +
        "reaches equilibrium.",
    equilibrium_convergence_tolerance: "How close to equilibrium the " +
        "ancestral population must be, in H_S units. Sets how long its " +
        "pre-run simulation lasts: about ln(1/tolerance) relaxation times.",
    equilibrium_max_generations: "Safety cap on the ancestral " +
        "population's own pre-run simulation, independent of the main " +
        "run's own max generations. A run needing more stops with an error.",
    cs_group: "Which statistic(s) to watch for convergence: any global " +
        "statistic. Checking more than one reveals the choice of when to " +
        "stop, at the foot of this panel. E_ST, K_ST, A_CGD, Delta and MI " +
        "(the allele statistics) are slow: watching one computes it every " +
        "generation, and the burn-in is derived for the identity " +
        "statistics, so these may need a longer one.",
    convergence_combinator: "With several watched statistics: all (the " +
        "default) keeps the run going until every one is stable and " +
        "precise. any stops as soon as one of them is, so the others may " +
        "still be trending or imprecise when the run ends: their reported " +
        "values and window means are not converged estimates. With one " +
        "watched statistic the two are the same.",
    convergence_burn_in: "Generations discarded before averaging starts: " +
        "the time the population needs to forget where it started. Leave it " +
        "as auto and the app sets it from how slowly this population " +
        "forgets its starting state (its migration, mutation and size) and " +
        "the precision. A run that finishes in a hundred generations is a " +
        "warning sign, not good news.",
    convergence_estimate: "Which expected value a run estimates for D and " +
        "G_ST. Mean of values (the default): the average of the statistic " +
        "itself, which is what published replicate means are. Value of " +
        "means: the statistic computed from the averaged heterozygosities, " +
        "which is what the closed form predicts and stays defined near " +
        "fixation. Automatic: the second when the statistic is undefined " +
        "or its denominator is tiny in the averaging window. The two " +
        "differ slightly (about 0.01 for D at one locus); every report " +
        "carries both.",
    statistic_precision: "Optional. A plus-or-minus for single watched " +
        "statistics, written NAME=number and separated by commas, in the " +
        "statistic's own units. It replaces the run's precision for those " +
        "statistics. Allele counts and distances have no natural scale, so " +
        "without an entry they use a relative precision.",
    precision_method: "How a batch reaches its precision. Interval: add " +
        "replicates until the interval across them is plus or minus the " +
        "precision. Planned replicates: run exactly the number of " +
        "replicates you chose, each long enough that their interval is " +
        "plus or minus the precision.",
    replicate_averaging_window: "Generations each replicate of a batch " +
        "averages after its burn-in. auto matches the window to the batch " +
        "(the first replicates measure the noise, the rest use the window " +
        "that reaches the precision). A number is used by every " +
        "replicate.",
    trajectory_retention: "Which generations of a run's trajectory are " +
        "saved. Full keeps every generation (the default). Thinned keeps " +
        "every generation until the run has been averaging for a while, " +
        "then one in every stride, plus the first, the last burn-in " +
        "generation and the last. The statistics, the graph and the report " +
        "are unaffected; the scrubber and re-analysis see only the kept " +
        "generations.",
    trajectory_stride: "With thinning on, one generation in this many is " +
        "kept once thinning starts.",
    trajectory_thinning_start: "The first generation thinning may skip. " +
        "auto keeps every generation until the burn-in plus two relaxation " +
        "times, and at least the first 100,000, so short runs are never " +
        "thinned.",
    precision: "How precisely to estimate each watched statistic: plus or " +
        "minus this amount, in the statistic's own units, at the " +
        "confidence level. A single run averages over time until it gets " +
        "there; a batch adds replicates until it gets there. Smaller is " +
        "more precise and takes longer.",
    max_generations: "Hard cap on generations. Leave it as auto and the " +
        "app sets it to a comfortable multiple of the time this " +
        "population needs to settle. Reaching it without converging is " +
        "still reported as a valid, non-converged outcome.",
    // A native `<select>` has nowhere to hang a per-option tooltip, so
    // all four options' one-line descriptions live in this one field
    // help string -- the same "one shared help string per field" shape
    // every other entry here uses. Results are identical whichever is
    // picked (all three are bit-identical for a given seed on one
    // machine; the compiled array-native choice gives up only
    // cross-machine bit-for-bit reproducibility), so the wording keeps
    // the choice about speed, which is all it actually is.
    engine_backend: "Which engine runs the simulation. auto picks the " +
        "fastest for your configuration and is the recommended choice. " +
        "lineal is the single-threaded reference implementation; " +
        "generational is thread-parallel; generational-vector is " +
        "array-native and fastest for large configurations. Every choice " +
        "computes the same statistics, and on one machine gives the same " +
        "results for the same seed.",
    n_replicates: "Number of independently seeded replicate runs. 1 means " +
        "a single ordinary run with no batching.",
    stop_batch_early: "Stop adding replicates once the precision is " +
        "reached: every watched statistic's cross-replicate confidence " +
        "interval is plus or minus the precision or narrower. Unchecked: " +
        "always run the full number of replicates.",
    replicate_minimum: "Fewest replicates run before the batch may stop " +
        "early, guarding against an early lucky-tight fluke.",
    confidence: "How sure the plus-or-minus is, and the confidence level " +
        "of the cross-replicate interval reported once a batch finishes. " +
        "95% is the usual choice.",
    max_workers: "How many replicates run in parallel. Not a model " +
        "parameter — defaults to this machine's own CPU count.",
    max_concurrent_replicates: "Caps how many replicate lanes run at once " +
        "under the generational/generational-vector engines. Unlike " +
        "parallel workers, this is a real model parameter, and has no " +
        "effect under lineal. Blank means every requested replicate at " +
        "once.",
    jit: "Whether the generational engine JIT-compiles its inner loop " +
        "with numba. lineal never accepts anything but off; " +
        "generational-vector always uses numba regardless of this " +
        "setting.",
    auto_vector_min_d: "The deme-count threshold the auto engine uses to " +
        "prefer generational-vector over generational. Only meaningful " +
        "when the execution engine above is auto; ignored otherwise.",
    auto_vector_max_capacity: "The locus-capacity ceiling (4 to the power " +
        "of the locus length) the auto engine uses to prefer " +
        "generational-vector under finite alleles. Not used with " +
        "infinite alleles. Only meaningful when the execution engine " +
        "above is auto; ignored otherwise.",
    significant_digits: "How many digits every displayed statistic rounds " +
        "to. Cosmetic only — saved files always keep full precision.",
    dark_mode_override: "Follow system matches your OS's own light/dark " +
        "setting. Light or Dark overrides it for this app only, applied " +
        "immediately.",
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
 * Show the tooltip bubble anchored just above `anchor`. Field titles
 * (labels/legends) sit above their data-entry widgets, so anchoring below
 * `anchor` -- as an earlier version did -- placed the bubble directly over
 * the input it was meant to explain. Anchoring above the title instead
 * keeps the input clear; `tooltip.offsetHeight` requires the element to
 * already be unhidden, hence the `hidden = false` before measuring.
 * @param {HTMLElement} anchor
 * @param {string} text
 */
function showFieldTooltip(anchor, text) {
    const tooltip = ensureTooltipElement();
    tooltip.textContent = text;
    tooltip.hidden = false;
    const rect = anchor.getBoundingClientRect();
    tooltip.style.left = `${Math.max(FIELD_TOOLTIP_EDGE_MARGIN_PX, rect.left)}px`;
    tooltip.style.top = `${Math.max(
        FIELD_TOOLTIP_EDGE_MARGIN_PX,
        rect.top - tooltip.offsetHeight - FIELD_TOOLTIP_GAP_PX
    )}px`;
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
 * Wire every field label and `[data-field-help]` legend inside Configure,
 * plus `#screen-run` (nothing there carries a tooltip today, scanned
 * anyway so a field added there later is caught the same way Configure's
 * own are) and the Settings dialog's own execution/convergence-selection
 * defaults (`#modal-settings`). Called once, at parse time -- unlike
 * almost everything else this page wires, this markup is static HTML
 * present from first load, not built by a later bridge call, so there is
 * nothing to wait for.
 *
 * Configure's own labels use the `field-<key>` id convention, so their
 * `FIELD_HELP` key is derived by slicing that prefix off; Settings' own
 * fields don't share that id convention (`settings-*` -- a second,
 * independent set of controls from Configure's own identically-named-
 * in-spirit fields, `index.html`'s own comment above `#modal-settings`
 * has the full account), so they carry an explicit `data-field-
 * help="<key>"` on the `<label>` itself instead, the same opt-in
 * mechanism a composite `<fieldset>`'s own `<legend>` already used for
 * its group-level tooltip. Scoped to `#screen-run`/`#modal-settings`
 * rather than a tighter per-fieldset scope only because that's this
 * file's own established per-screen/per-dialog scoping granularity
 * (`#screen-configure`, above).
 */
function wireAllFieldTooltips() {
    document
        .querySelectorAll('#screen-configure label[for^="field-"]')
        .forEach((label) => {
            wireFieldTooltip(label, label.htmlFor.slice("field-".length));
        });
    document
        .querySelectorAll(
            "#screen-configure legend[data-field-help], " +
                "#screen-run legend[data-field-help], " +
                "#modal-settings legend[data-field-help]"
        )
        .forEach((legend) => {
            wireGroupTooltip(legend, legend.dataset.fieldHelp);
        });
    document
        .querySelectorAll(
            "#screen-run label[data-field-help], " +
                "#modal-settings label[data-field-help]"
        )
        .forEach((label) => {
            wireFieldTooltip(label, label.dataset.fieldHelp);
        });
}

wireAllFieldTooltips();
