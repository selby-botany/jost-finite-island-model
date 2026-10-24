# Configure

Configure edits one configuration: the model, its structure, the
stopping rule, and an optional sweep. Each field shows a short tooltip on
hover or keyboard focus. Press **F1** in a field to open its entry in the
[configuration reference](../configuration.md).

## Contents

- [Field and key correspondence](#field-and-key-correspondence)
- [Run settings](#run-settings)
- [Loaded run settings](#loaded-run-settings)
- [Load, save, and examples](#load-save-and-examples)
- [Naming the next run](#naming-the-next-run)
- [Run, sweep, and explore](#run-sweep-and-explore)
- [Validation](#validation)

## Field and key correspondence

The form and the YAML file use the same units. In particular, `N` is in
individuals in both. Saving the form writes these keys.

| Panel | Field or choice | YAML key | Notes |
|---|---|---|---|
| FIM parameters | ploidy | [`ploidy`](../configuration.md#ploidy) | Default comes from Settings; **Ask me each time** requires a choice before running |
| FIM parameters | N (individuals/deme): same for every deme, per-deme | [`N`](../configuration.md#n) | Individuals; multiplied by ploidy |
| FIM parameters | d (demes) | [`d`](../configuration.md#d) | At least 2 |
| FIM parameters | seed | [`seed`](../configuration.md#seed) | One PCG64 stream per run |
| m (migration) | scalar rate, named topology, full matrix | [`m`](../configuration.md#m) | Topology: ring, linear, or torus (rows × columns = d, each side ≥ 3) |
| μ (mutation rate) | specify μ directly | [`mu`](../configuration.md#mu) | Per gene copy per generation |
| μ (mutation rate) | derive from μb | [`mu_b`](../configuration.md#deriving-mu-from-a-per-base-rate) | Per-locus μ from μb and locus length |
| Structure | migrant sampling | [`migrant_sampling`](../configuration.md#migrant_sampling) | `continuous` or `stochastic` |
| Structure | mutation model | [`mutation_model`](../configuration.md#mutation_model) | `infinite_alleles` or `finite_alleles` |
| Structure | deme weighting | [`deme_weighting`](../configuration.md#deme_weighting) | Affects $E_{ST}$, $\delta_G$, $I$ only |
| initial conditions | Dirichlet draw | [`initial_allele_count`](../configuration.md#initial_allele_count), [`initial_concentration`](../configuration.md#initial_concentration) | Generation 0 is a prior |
| initial conditions | equilibrium split | [`equilibrium_convergence_window`, `equilibrium_convergence_tolerance`, `equilibrium_max_generations`](../configuration.md#equilibrium_convergence_window-equilibrium_convergence_tolerance-equilibrium_max_generations) | Ancestral pre-run, then split |
| initial conditions | explicit p₀ | [`p_0`](../configuration.md#p_0) | One frequency vector per deme |
| initial conditions | fixed per deme | [`p_0`](../configuration.md#p_0) | All different, all the same, or all but one |
| loci | length(s) only, custom locus IDs, **Add locus** | [`loci`](../configuration.md#loci) | Lengths matter for μb and finite alleles |
| convergence statistic(s) | statistic checkboxes | [`convergence_statistic`](../configuration.md#convergence_statistic) | Default `D` and `G_ST` |
| convergence statistic(s) | per-statistic precision | [`statistic_precision`](../configuration.md#statistic_precision) | `NAME=value`, comma-separated |
| stop the run when | all watched statistics have settled, any one has settled | [`convergence_combinator`](../configuration.md#convergence_combinator) | Shown with two or more watched statistics |
| convergence statistic(s) | stop the batch early | [`stop_batch_early`](../configuration.md#stop_batch_early) | Batches only; inert when `n_replicates` is 1 |
| convergence statistic(s) | replicate minimum | [`replicate_minimum`](../configuration.md#replicate_minimum) | Batches only |

Allele statistics ($E_{ST}$, $K_{ST}$, $A_{\mathrm{CGD}}$, $\delta_G$,
$I$) are slower. Watching one computes it every generation; the derived
burn-in targets the identity statistics. See
[`track_expensive_statistics`](../configuration.md#track_expensive_statistics).

## Run settings

**Run settings…** opens **Run settings for this configuration**: the
Settings dialog's execution, convergence, Expert, and storage controls,
scoped to this configuration only:

| Control | YAML key |
|---|---|
| execution engine | [`engine_backend`](../configuration.md#engine_backend) |
| n_replicates | [`n_replicates`](../configuration.md#n_replicates) |
| max generations | [`max_generations`](../configuration.md#max_generations) |
| confidence | [`confidence`](../configuration.md#confidence) |
| convergence burn-in | [`convergence_burn_in`](../configuration.md#convergence_burn_in) |
| precision | [`precision`](../configuration.md#precision) |
| expected value | [`convergence_estimate`](../configuration.md#convergence_estimate) |
| precision method | [`precision_method`](../configuration.md#precision_method) |
| replicate averaging window | [`replicate_averaging_window`](../configuration.md#replicate_averaging_window) |
| max concurrent replicates | [`max_concurrent_replicates`](../configuration.md#max_concurrent_replicates) |
| JIT | [`jit`](../configuration.md#jit) |
| auto-vector min d, auto-vector max capacity | [`auto_vector_min_d`](../configuration.md#auto_vector_min_d), [`auto_vector_max_capacity`](../configuration.md#auto_vector_max_capacity) |
| trajectory retention, trajectory stride, thinning start | [trajectory retention keys](../configuration.md#trajectory_retention-trajectory_stride-trajectory_thinning_start-trajectory_keep_last-trajectory_keep_burn_in) |

Defaults come from [Settings](settings.md#defaults-for-new-configurations).
**Apply to this run** validates the values against the current model and
applies them without changing saved Settings. For example,
`max_generations: auto` cannot be derived when both migration and
mutation are zero; the editor reports the error and leaves the
configuration unchanged.

**max workers** is a property of this computer, not of the run. It is
not written to the configuration and does not affect results.

## Loaded run settings

A configuration you load can supply its own run settings. This applies
to **Examples…** (**Load into Configure** and **Run it**),
**File > Load example…**, **Load configuration…**, and **Clone** on Home.
The loaded values apply **to this run only**; your Settings are not
changed. A run setting omitted from the file takes the simulator
default, as `fim run` would.

When any loaded value differs from your Settings, a notice on Configure
and on the run view lists each difference side by side:

- **Make these my Settings** saves the loaded values as your Settings.
  Max workers is not changed.
- **Edit this run…** opens the run settings editor. **Use saved
  Settings** fills it from your Settings; **Restore loaded settings**
  restores the loaded values. Neither applies until you choose **Apply
  to this run**.
- **Dismiss** hides the notice; the run still uses the loaded values. The
  **run settings** item in the parameter strip remains while values
  differ; select it to show the notice again.

If you change Settings later, the notice compares against the new
Settings. To adopt your Settings for this run, choose **Run settings…**,
then **Use saved Settings**, then **Apply to this run**. That copies a
snapshot; later changes to Settings do not alter it.

**File > New configuration** and Explore's **Run this for real** start
from your Settings. When the app restores the last submitted
configuration at launch, run settings come from Settings, not from that
configuration.

## Load, save, and examples

- **Load configuration…** and **Save configuration…** read and write
  YAML in the same format as `fim run`.
- **Examples…** opens the bundled worked examples. **View YAML** shows
  the configuration; **Load into Configure** gives an editable copy;
  **Open saved result** opens the finished result without computing.
  See [examples](organize.md#the-examples-experiment).
- **File > Load example…** opens the preset picker, which also lists
  presets you saved with **Save current as…**.

## Naming the next run

- **Run name** and **Run description** label the next run, then clear
  when it starts. They are hidden while **Sweep** is ticked, because a
  sweep names its own runs.
- The study selector at the bottom chooses where the run is filed.
  It starts at **Default study**. **New study…** creates a study with a
  name and an optional description.
- Names and descriptions are stored in `metadata.json` and are never part
  of the run ID. See [names and documentation](organize.md#names-descriptions-and-documentation).

## Run, sweep, and explore

- **Run** starts the configuration. With `n_replicates` above 1, the
  run is a batch.
- Before you start, the run view shows the derived burn-in and cap
  beside **Run simulation**. Check them against the model's timescale.
- If a completed result already exists for the same configuration and
  software version, the app reuses it instead of computing again.
- Tick **Sweep** and choose **Set up sweep…** to vary parameters. **Run**
  then executes the sweep in the chosen study. See [sweeps](sweeps.md).
- **Explore** opens closed-form predictions for the current N, d, m,
  and μ. See [Explore](explore.md).

## Validation

Invalid input is reported when you run. The app returns to Configure
and marks the field. Unknown or misspelled YAML keys are rejected, never
ignored. A configuration that is incomplete or invalid can still be kept
with **Save for later**; it is saved as a Draft.
