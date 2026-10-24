# Using `fim`

This guide covers commands and output files. The desktop app has its
own [desktop app guide](gui/README.md), also bundled in the app. See
[what this simulates](../README.md#what-this-simulates) for the model,
the [configuration reference](configuration.md) for parameter types and
defaults, and the [statistics reference](statistics.md) for definitions,
interpretation, and implementation links. Return to the
[project overview](../README.md) for installation and documentation links.

Mathematical quantities use $\TeX$; configuration keys, report identifiers,
and commands use backticks. Display equations have unique local
$\text{U}$ tags.

## Contents

- [Create a configuration](#create-a-configuration)
- [Run a simulation](#run-a-simulation)
- [Why does my run take so long?](#why-does-my-run-take-so-long)
- [Save, pause, and resume a run](#save-pause-and-resume-a-run)
- [Worked examples](#worked-examples)
- [Sweep a parameter](#sweep-a-parameter)
- [Re-analyze a trajectory](#re-analyze-a-trajectory)
- [Check for updates](#check-for-updates)
- [Desktop GUI (`fim-gui`)](#desktop-gui-fim-gui)
- [Global flags](#global-flags)
- [Output schemas](#output-schemas)
- [Reproduce a run](#reproduce-a-run)
- [Troubleshooting](#troubleshooting)

## Create a configuration

```console
fim init [--output PATH] [--force]
```

`fim init` writes the documented development scenario. `N` is individuals
per deme and `ploidy` says how many gene copies each carries, so `N: 225` with
`ploidy: diploid` is 450 gene copies. Without `--output`, the path is
`project-root/results/example-run.yaml`, where `project-root` is the root
of this checkout when you are running from one, a `fim` folder in your
home directory for the packaged desktop app, and otherwise (a plain `pip
install`, run from a terminal) your current working directory. Existing
files are protected unless `--force` is present.

## Run a simulation

```console
fim run CONFIG [-o DIRECTORY | --output DIRECTORY] [--quiet]
    [--workers N] [--sequential] [--max-concurrent-replicates N]
    [--pairwise-max-demes N]
```

`CONFIG` is a YAML file described in
[configuration.md](configuration.md). Without `--output`, a timestamped
directory is created under `project-root/results/`. The timestamp affects only
the folder name and manifest metadata; it never affects the trajectory,
statistics, convergence decision, or deterministic run_id.

`--pairwise-max-demes N` (default 1024) is the largest deme count for which
the run saves every deme pair's statistics in [`pairwise.json`](#pairwisejson).
The file grows with the square of the deme count: about 0.5 MB at 100 demes
and 52 MB at 1024, per run and per batch replicate, and about a second to
write at the largest size. Above the limit the file records only that the
matrices were skipped; any single pair can still be recomputed from the
saved trajectory, which the desktop GUI does on demand.

`--quiet` suppresses progress and artifact-path messages. Validation errors
name the offending key or value and return status 2. A run that reaches
max_generations also returns status 0 because it is a valid, inspectable
non-converged result.

### Why does my run take so long?

Low migration or mutation can require tens of thousands of generations for
dependence on initial conditions to decay. A run first discards a burn-in,
then averages until its stopping rule is satisfied.
`convergence_burn_in` and `max_generations` default to `auto`, derived from
migration, mutation, and population sizes; explicit values override them.

`fim run` prints the derived values unless `--quiet`, and the desktop app shows
them next to **Run simulation** before you start. For five islands of 100 gene
copies with `m: 0.0001` and `mu: 0.000001` that is a burn-in of about 104,000
generations. A 100-generation run cannot provide an equilibrium estimate
for that configuration. The derivation and evidence are in
[Convergence](convergence.md); the settings are in
[configuration.md](configuration.md#convergence_burn_in).

### Batches (n_replicates greater than one)

A config's n_replicates (see [configuration.md](configuration.md#n_replicates))
controls whether one run or a whole batch executes:

- **n_replicates: 1**: scalar-run artifacts directly in the output directory.
- **n_replicates greater than one (the default: `200`, with
  [stop_batch_early](configuration.md#stop_batch_early) and a
  [precision](configuration.md#precision) of `0.01` usually stopping well
  short of it)**: each replicate gets a
  `replicate-NNN/` subdirectory containing scalar-run artifacts,
  plus a batch-level `manifest.json` and `summary.json` — see
  [Output schemas](#output-schemas).

Omitting `n_replicates` selects a batch with an across-replicate confidence
interval. Set `n_replicates: 1` for a single run.
The [`fim init`](#create-a-configuration) configuration states
`n_replicates: 200` explicitly. Most worked examples use batches; examples
of within-run behavior use one replicate.

A batch runs under any [engine_backend](configuration.md#engine_backend),
not only `lineal`; every backend writes the same `replicate-NNN/` +
`manifest.json` + `summary.json` layout. Backend parity follows the
documented `engine_backend` rules: `lineal`,
`generational` and `generational-vector` produce the same trajectory,
bit for bit, for the same seed on the same machine, so running the same
batch under a different backend reproduces every replicate (and
across different machines, statistically — the compiled arithmetic
`generational-vector` and `jit: numba` use is checked against Python's
only on the development platform).

Under `engine_backend: lineal` (the only backend with a worker-process
pool), batch replicates run in parallel by default, one worker per
processor. `--workers N` sets an explicit worker count; `--sequential`
runs replicates one at a time — the worker count affects only how long the
batch takes, never its result. Both flags are rejected with a
usage error under other backends, which do not use process pools. Use
[max_concurrent_replicates](configuration.md#max_concurrent_replicates)
instead (also settable for this run only via `--max-concurrent-replicates
N`, without editing the config file), the `generational`/
`generational-vector` concurrency and memory control. In adaptive batches,
this setting also affects averaging-window selection and can change results.

With stop_batch_early off in the config, exactly n_replicates
replicates run. With it on (the default), the batch can stop earlier, once every watched
statistic's across-replicate confidence interval has tightened enough (see
[configuration.md](configuration.md#stop_batch_early)) — the number of
`replicate-NNN/` subdirectories written can then be less than n_replicates.

## Save, pause, and resume a run

`fim run` runs to the end in one go. For a run you may need to stop and
continue later, save it as a Run object instead:

```console
fim object create my-run.yaml        # draft if invalid, planned if valid
fim object start OBJECT_ID           # runs in the foreground
fim object pause OBJECT_ID           # from another terminal: stop at a checkpoint
fim object resume OBJECT_ID          # continue exactly where it stopped
fim object list                      # every saved run and its state
```

A resumed run reproduces an uninterrupted run. Ctrl-C,
SIGTERM, a crash, or a power failure loses at most the work since the last
checkpoint (1,000 generations or 60 seconds). The desktop app's
**Save for later** and **Saved runs** use the same objects. See
[saved runs: pause, resume, and recover](run-lifecycle.md) for every state,
command, and recovery case.

**Sweep setup is not stored with a saved run.** If you edit a sweep setup,
**Save and close** saves the run configuration but keeps the close dialog
open with an explanation. Choose **Keep working** to retain the setup in
the current session, or **Close without saving** to discard that setup.
Saving the run configuration alone does not clear the sweep's unsaved-edit
warning.

## Worked examples

Each example below is a complete config and the command that runs it: save
the YAML, run the command, and the reported values match those shown here,
because the same seed, parameters, and version always give the same
`report.json` on the same platform (see [Reproduce a run](#reproduce-a-run)).
Most use model-derived [burn-in and generation caps](convergence.md);
timing examples use fixed averaging windows. Several use looser precision
(0.02–0.05) or eight pooled loci to limit runtime. Their READMEs document
the trade-offs and measurements. Runtime depends on backend, hardware,
and workload. Single-locus results can vary widely; use replicates to
estimate the mean and its uncertainty. Each example uses a
seed distinct from [`fim init`](#create-a-configuration)'s starter config.
Each configuration starts with its labels (`name`, `description`,
`class`) and `_read_only: true`, which protects the bundled copy;
delete that line in an editable copy (see
[Run labels](configuration.md#run-labels)).
Each demonstrates one option, or one natural pair of options, from the
[configuration reference](configuration.md); studies can combine them
freely.

In the [desktop app](#desktop-gui-fim-gui), you do not need to copy the
YAML: click **Examples…** on the Configure screen (or on the welcome panel
the first time the app opens). The Examples dialog lists the example
classes on the left and the examples in the selected class on the right,
each with a short description and an excerpt of its explanation. It also
holds the longer calibration examples from
[`doc/examples/`](examples/README.md). Use the arrow keys or the mouse to
choose one, then press Return or click **Load into Configure**. The
example's values fill the form, and its name and description fill the
"Run name" and "Run description" boxes, which you can change before you
run it. Its run settings (engine, replicates and so on) are used for this
run only; your Settings do not change, and a notice lists any that differ
(see [Loading a configuration does not change your Settings](gui/configure.md#loaded-run-settings)).
**View YAML** shows the example's configuration file, ready to
copy. An example that the form cannot represent (a different `mu` for each
locus that no single per-base rate `mu_b` produces, for example) or that has
no configuration file says so in the dialog, and
**Load into Configure** stays unavailable for it. **Open saved result**
shows the bundled result without running
anything (see [Opening a saved example](gui/organize.md#opening-a-saved-example)); it is
unavailable for an example whose result has not been saved yet.

### Unequal island sizes with a migration hub

Four demes of very different size, connected by an explicit `d x d`
migration matrix rather than one shared rate — a small "hub" topology
where deme 4 is both the largest and the best-connected:

<!-- worked-example-config: examples/unequal-island-sizes-with-a-migration-hub/config.yaml -->
```yaml
name: Unequal islands with a hub
description: >-
  Three small islands and one large, well-connected hub island, joined by an
  explicit migration matrix.
class: migration
_read_only: true  # marks the shipped copy read-only; delete it in your own copy
N: [200, 200, 200, 800]
ploidy: haploid
d: 4
m:
  - [0.95, 0.02, 0.02, 0.01]
  - [0.02, 0.95, 0.02, 0.01]
  - [0.02, 0.02, 0.95, 0.01]
  - [0.01, 0.01, 0.01, 0.97]
mu: 0.001
seed: 20260819
# Eight independent loci, pooled: one locus alone swings too widely for its
# D to settle honestly (see the README).
loci:
  - locus_id: 1
    length: 100
  - locus_id: 2
    length: 100
  - locus_id: 3
    length: 100
  - locus_id: 4
    length: 100
  - locus_id: 5
    length: 100
  - locus_id: 6
    length: 100
  - locus_id: 7
    length: 100
  - locus_id: 8
    length: 100
engine_backend: auto
convergence_statistic: D
# Thinned storage: every generation up to 5,000, then one in ten, so the saved
# trajectory is a fraction of the size; the statistics and the report are
# unaffected.
trajectory_retention: thinned
trajectory_stride: 10
trajectory_thinning_start: 5000
```

```console
fim run hub-island.yaml --output results/hub-island --quiet
```

The batch of 10 replicates takes about five minutes and gives an average D of
0.0652 ± 0.0028 across replicates. The example pools eight loci: one locus
alone has greater sampling variation (see the
[example's README](examples/unequal-island-sizes-with-a-migration-hub/README.md)).
`manifest.json`'s `parameters.N`
and `parameters.m` record the per-deme sizes and matrix rows used —
compare them against a run with one shared `N/m` to see the effect of
unequal size and asymmetric connectivity on differentiation.

`deme_weighting` affects $E_{ST}$, $\delta_G$, and mutual
information; `D` and $K_{ST}$ weight demes equally by
definition, regardless of this setting. With the unequal per-deme `N` above,
the default `equal` weighting and `deme_weighting: size` give different
$E_{ST}$ — deme 4's 800-gene-copy weight pulls the size-weighted value
down, since it is both the largest deme and the best-connected one.

### Stepping-stone (spatial) migration

Six demes arranged on a ring, each migrating only with its two neighbors —
specified through a named topology rather than all 36 matrix entries:

<!-- worked-example-config: examples/stepping-stone-spatial-migration/config.yaml -->
```yaml
name: Stepping-stone migration
description: >-
  Six islands on a ring, each exchanging migrants only with its two
  neighbors.
class: migration
_read_only: true  # marks the shipped copy read-only; delete it in your own copy
N: 150
ploidy: haploid
d: 6
m:
  topology: ring
  rate: 0.05
mu: 0.001
seed: 20260819
loci:
  - locus_id: 1
    length: 100
engine_backend: auto
convergence_statistic: D
precision: 0.02   # looser than the 0.01 default: about a minute
```

```console
fim run stepping-stone.yaml --output results/stepping-stone --quiet
```

The batch of 16 replicates takes under two minutes and gives an average D of
0.146 ± 0.019 across replicates. `precision: 0.02` keeps the batch short; the
default 0.01 would take about four times as long (see
the [example's README](examples/stepping-stone-spatial-migration/README.md)).
Swap `topology: ring` for
`linear` to remove the wrap-around edge between deme 1 and deme 6. In
the GUI, the completed-run Literature visualizations panel includes an
isolation-by-distance plot whenever the migration graph has at least two
distance classes.

### Literature distance statistics from an explicit founder split

Three demes start with partially shared alleles at different frequencies.
Migration shares alleles, mutation creates private alleles, and drift changes
their abundance and survival. Compare Caballero-García-Dorado allelic distance
$A_{\mathrm{CGD}}$ (presence/absence), Gregorius $\delta_G$ (frequency distance), and
Sherwin mutual information $I$ (`MI`; information about population membership)
with `D` and $G_{ST}$. The split is imposed, not simulated from an
ancestral population.

All five measures are watched, so the stopping verdict applies to all of
them. `track_expensive_statistics` also records the other supplemental
measures. In the desktop app that flag follows Settings' **Statistics shown**;
watched statistics are computed regardless. Show $A_{\mathrm{CGD}}$,
$\delta_G$, and I there to see their trajectories.

<!-- worked-example-config: examples/literature-distance-statistics-from-an-explicit-founder-split/config.yaml -->
```yaml
name: Allele sharing after a founder split
description: >-
  Follow partially shared founder alleles as migration, mutation, and drift
  reveal the differences between presence, frequency, and information measures.
class: statistics-and-convergence
_read_only: true  # marks the shipped copy read-only; delete it in your own copy
N: 100
ploidy: haploid
d: 3
m: 0.02
mu: 0.005
seed: 20260914
loci:
  - locus_id: 1
    length: 100
p_0:
  - - 0: 0.8
      1: 0.2
  - - 1: 0.8
      2: 0.2
  - - 0: 0.2
      2: 0.8
engine_backend: auto
convergence_statistic: [D, G_ST, A_CGD, Delta, MI]
convergence_combinator: all
precision: 0.05
max_generations: auto
track_expensive_statistics: true
n_replicates: 1   # single run: a one-generation computation from an explicit founder state, so there is nothing to average over replicates
```

```console
fim run literature-distance-statistics.yaml \
  --output results/literature-distance-statistics --quiet
```

The saved run converges at generation 10,499, with evidence-window means
$D=0.3568$, $\delta_G=0.4528$, and $A_{\mathrm{CGD}}=3.7508$; these are not its
final-generation values. It uses one replicate and took about 17 seconds
on the development machine. Choose **Run settings...** on Configure to
change the replicate count, cap, or precision for your run only.

Unlike $\delta_G$ and $D$, $A_{\mathrm{CGD}}$ is not bounded by 1;
$I$ (`MI`) uses natural-log units.
The implementation averages allelic distance over distinct deme pairs,
whereas Caballero and García-Dorado's all-pairs equation 6 includes
self-pairs. See the [example's explanation and literature references](
examples/literature-distance-statistics-from-an-explicit-founder-split/README.md)
for the normalization and the different questions these measures answer.

### Equilibrium-split founding

This example derives generation-0 frequencies from a shared ancestral
population rather than specifying them directly or using independent
Dirichlet draws. The ancestral population undergoes drift and mutation for
a model-derived equilibration period, then splits into demes by sampling
without replacement. Sampling introduces founder variation at generation 0:

<!-- worked-example-config: examples/equilibrium-split-founding/config.yaml -->
```yaml
name: Equilibrium-split founding
description: >-
  Islands founded by splitting one ancestral population that has already
  settled.
class: mutation-and-founding
_read_only: true  # marks the shipped copy read-only; delete it in your own copy
N: 200
ploidy: haploid
d: 3
m: 0.005
mu: 0.001
seed: 20260916
# Eight independent loci, pooled, and a looser precision for the main run:
# one locus here needs about 30,000 generations to settle (see the README).
loci:
  - locus_id: 1
    length: 100
  - locus_id: 2
    length: 100
  - locus_id: 3
    length: 100
  - locus_id: 4
    length: 100
  - locus_id: 5
    length: 100
  - locus_id: 6
    length: 100
  - locus_id: 7
    length: 100
  - locus_id: 8
    length: 100
equilibrium_convergence_window: 20
equilibrium_convergence_tolerance: 0.01
equilibrium_max_generations: 2000   # the derived burn-in is 1,256 generations
engine_backend: auto
convergence_statistic: D
precision: 0.03
# Thinned storage: every generation up to 5,000, then one in ten, so the saved
# trajectory is a fraction of the size; the statistics and the report are
# unaffected.
trajectory_retention: thinned
trajectory_stride: 10
trajectory_thinning_start: 5000
```

```console
fim run equilibrium-split.yaml --output results/equilibrium-split --quiet
```

The ancestral phase runs 1,256 generations in each replicate; the batch of 10
replicates takes about fifteen minutes and gives an average D of
0.285 ± 0.012 across replicates (the model's expectation is 0.287).
Initial frequencies are derived without an explicit `p_0`. The example pools
eight loci and sets `precision: 0.03`, because one locus here
needs about 30,000 generations to settle (see the
[example's README](examples/equilibrium-split-founding/README.md)). How long the ancestral phase runs is worked out from
the model. Write $\varepsilon$ for `equilibrium_convergence_tolerance`
and $N_{\mathrm{anc}}$ for the ancestral gene-copy count. The approximate
relaxation time in generations is:

```math
\tau_{\mathrm{anc}}\approx\frac{1}{2\mu+1/N_{\mathrm{anc}}} \tag{U.1}
```

The phase lasts about $\ln(1/\varepsilon)$ times that relaxation time.
Loosen the tolerance for a shorter
ancestral phase (founded from a population a little further from
equilibrium), or tighten it for a longer one; either way, a phase needing
more than equilibrium_max_generations is a hard error, reported before the
phase starts, not an ordinary result. The ancestral phase is saved, every
generation of it, in
[`equilibrium_trajectory.tlog`](#equilibrium_trajectorytlog) beside the
run's `trajectory.tlog` — see
[equilibrium_convergence_window, equilibrium_convergence_tolerance,
equilibrium_max_generations](configuration.md#equilibrium_convergence_window-equilibrium_convergence_tolerance-equilibrium_max_generations).

### Stochastic migrant counts

By default, migration blends each deme's frequencies with an exact
`rate * N` fraction of its neighbors' — a deterministic step given that
generation's frequencies. migrant_sampling: stochastic instead draws the
migrant count from $\mathrm{Binomial}(N,r)$, where $N$ is the gene-copy count
and $r$ the migrant fraction, adding variation in migrant number:

<!-- worked-example-config: examples/stochastic-migrant-counts/config.yaml -->
```yaml
name: Stochastic migrant counts
description: >-
  Draws each generation's number of migrants at random instead of using a
  fixed fraction.
class: migration
_read_only: true  # marks the shipped copy read-only; delete it in your own copy
N: 100
ploidy: haploid
d: 4
m: 0.05
mu: 0.001
seed: 20260819
migrant_sampling: stochastic
loci:
  - locus_id: 1
    length: 100
engine_backend: auto
convergence_statistic: D
```

```console
fim run stochastic-migrants.yaml --output results/stochastic-migrants --quiet
```

The batch of 11 replicates takes about two minutes and gives an average D of
0.082 ± 0.010 across replicates (the model's expectation is 0.059, which the
average of D's values sits above; see the
[example's README](examples/stochastic-migrant-counts/README.md)). Re-run with migrant_sampling
removed (or set to `continuous`, the default) at the same seed to compare
against the deterministic-migration baseline directly. That batch gives an
average D of 0.077 ± 0.010.

### Finite-length alleles (the K-allele model)

By default (mutation_model: infinite_alleles), every mutation receives a
globally unique identity — the standard population-genetics idealization
for a locus long enough that two independent mutations essentially never
land on the same state. finite_alleles instead bounds a locus to
$4^\ell$ states, where $\ell$ is the locus's `length`, and lets a mutation recur to one already present
elsewhere in the run. This example uses a 3-base locus
($4^3=64$ states) and high `mu` to make recurrent mutation likely:

<!-- worked-example-config: examples/finite-length-alleles-the-k-allele-model/config.yaml -->
```yaml
name: Finite-length alleles
description: >-
  A three-base locus with only 64 possible alleles, so mutations can recur.
class: mutation-and-founding
_read_only: true  # marks the shipped copy read-only; delete it in your own copy
N: 100
ploidy: haploid
d: 3
m: 0.02
mu: 0.02
seed: 20260819
mutation_model: finite_alleles
initial_allele_count: 2
loci:
  - locus_id: 1
    length: 3
engine_backend: auto
convergence_statistic: D
precision: 0.02   # looser than the 0.01 default: under a minute
```

```console
fim run finite-alleles.yaml --output results/finite-alleles --quiet
```

The batch of 20 replicates takes about twenty seconds and gives an average D
of 0.602 ± 0.019 across replicates (`precision: 0.02` is twice the default
plus-or-minus, which keeps the batch short; see the
[example's README](examples/finite-length-alleles-the-k-allele-model/README.md)).
This is the Kimura-Crow
finite-allele setting in miniature: `length: 3` gives 64 possible allele
states, so recurrent mutation is visible enough for the completed-run
frequency spectrum to be useful. See
[configuration.md](configuration.md#mutation_model) for how this differs
from a distance-based (stepwise) mutation model, which `fim` does not
implement.

### Wright-Takahata finite-deme correction

Wright's finite-island model and Takahata's multiallelic identity treatment
carry an explicit finite-`d` correction. This example keeps `d` small enough
that the correction matters; increasing `d` while holding `N`, `m`, and `mu`
fixed moves $G_{ST}$ toward the infinite-island approximation.

<!-- worked-example-config: examples/wright-takahata-finite-deme-correction/config.yaml -->
```yaml
name: Wright-Takahata finite-island correction
description: >-
  Eight islands, few enough that the finite-island correction to G_ST
  matters.
class: statistics-and-convergence
_read_only: true  # marks the shipped copy read-only; delete it in your own copy
N: 500
ploidy: haploid
d: 8
m: 0.003
mu: 0.0002
seed: 20260914
loci:
  - locus_id: 1
    length: 100
engine_backend: auto
convergence_statistic: G_ST
precision: 0.02   # looser than the 0.01 default: under a minute
```

```console
fim run finite-deme-correction.yaml \
  --output results/finite-deme-correction --quiet
```

The batch of 10 replicates takes about nine minutes and gives an average
$G_{ST}$ of $0.193\pm0.003$ across replicates
(`precision: 0.02` keeps the batch short; see the
[example's README](examples/wright-takahata-finite-deme-correction/README.md)).
The closed-form finite-deme prediction for these parameters
is about 0.141, below the corresponding infinite-island approximation of
about 0.238 because the `d / (d - 1)` correction is retained. A single locus
scatters widely around that expectation (the exact expectation for this
model is $G_{ST}=0.195$ and $D=0.319$); the batch averages it out, and its
interval for $G_{ST}$ contains 0.195.

### Kimura-Weiss isolation by distance

A 20-deme ring gives enough graph-distance classes for the GUI's
isolation-by-distance panel to show the short-distance decay pattern from
Kimura and Weiss. The run is still small enough for an interactive example,
but large enough that distances 1 through 10 exist on the ring.

<!-- worked-example-config: examples/kimura-weiss-isolation-by-distance/config.yaml -->
```yaml
name: Kimura-Weiss isolation by distance
description: >-
  Twenty islands on a ring, where near neighbors are more alike than distant
  islands.
class: migration
_read_only: true  # marks the shipped copy read-only; delete it in your own copy
N: 200
ploidy: haploid
d: 20
m:
  topology: ring
  rate: 0.05
mu: 0.001
seed: 20260914
loci:
  - locus_id: 1
    length: 100
engine_backend: auto
convergence_statistic: D
precision: 0.05   # looser than the 0.01 default: seconds, not hours
# Thinned storage: every generation up to 5,000, then one in ten, so the saved
# trajectory is a fraction of the size; the statistics and the report are
# unaffected.
trajectory_retention: thinned
trajectory_stride: 10
trajectory_thinning_start: 5000
```

```console
fim run kimura-weiss-isolation-by-distance.yaml \
  --output results/kimura-weiss-isolation-by-distance --quiet
```

The batch of 10 replicates takes about six minutes and gives averages
$D=0.563$ and $G_{ST}=0.122$ across replicates. `precision: 0.05` is
loose to limit runtime; the default 0.01 would take hours in this example (see
the [example's README](examples/kimura-weiss-isolation-by-distance/README.md)).
Open the result in `fim-gui` and inspect the Literature visualizations panel:
the identity-decay plot groups deme pairs by shortest-path distance over the
non-zero migration edges and overlays a log-linear fit.

### Per-base mutation rate across unequal locus lengths

`mu_b` (mutually exclusive with `mu`) configures a single per-base-pair
mutation probability $\mu_b$. Each locus derives its `mu` from its
`length`, written $\ell$, through:

```math
\mu_\ell=1-(1-\mu_b)^\ell \tag{U.2}
```

The exponent is the number of bases that must all avoid mutation.

<!-- worked-example-config: examples/per-base-mutation-rate-across-unequal-locus-lengths/config.yaml -->
```yaml
name: Per-base mutation rate
description: >-
  One per-base mutation rate gives a 50-base and a 500-base locus different
  mutation rates.
class: mutation-and-founding
_read_only: true  # marks the shipped copy read-only; delete it in your own copy
N: 150
ploidy: haploid
d: 3
m: 0.02
mu_b: 0.00002
seed: 20260819
loci:
  - locus_id: 1
    length: 50
  - locus_id: 2
    length: 500
engine_backend: auto
convergence_statistic: D
precision: 0.03   # looser than the 0.01 default: under a minute
```

```console
fim run mu-b.yaml --output results/mu-b --quiet
```

The batch of 10 replicates takes about a minute and gives an average D of
0.199 ± 0.014 across replicates (the window is sized from the slowest locus,
the 50-base one, so it is long enough to leave the approach from the starting
state; `precision: 0.03` keeps the batch short; see the
[example's README](examples/per-base-mutation-rate-across-unequal-locus-lengths/README.md)).
`results/mu-b/manifest.json`'s
`parameters.mu` records the two derived rates — `0.0009995` for the
50-base locus and `0.0099503` for the 500-base one — the expanded,
canonical form represented by `mu_b`; the per-base input is not stored.

### Several convergence statistics

Watch more than one statistic and decide whether stopping needs every one
of them stable (convergence_combinator: all, the default) or one
(`any`). In the desktop app this is the "stop the run when" pair of
radio buttons at the foot of Configure's "convergence statistic(s)"
panel, shown once two or more statistics are checked.

Choose `any` with care. The run stops as soon as one watched statistic is
stable and precise, so the others may still be trending or imprecise at
that moment: their reported values and trailing-window means are then
not converged estimates, only snapshots. `all` waits for every watched
statistic. With one watched statistic the two are the same.

<!-- worked-example-config: examples/several-convergence-statistics/config.yaml -->
```yaml
name: Several convergence statistics
description: >-
  Watches D and G_ST together and stops when either one settles.
class: statistics-and-convergence
_read_only: true  # marks the shipped copy read-only; delete it in your own copy
N: 150
ploidy: haploid
d: 3
m: 0.02
mu: 0.001
seed: 20260819
loci:
  - locus_id: 1
    length: 100
engine_backend: auto
convergence_statistic: [D, G_ST]
convergence_combinator: any
n_replicates: 1   # single run: this example is about one run's own stopping rule (`convergence_combinator: any`)
```

```console
fim run multi-statistic.yaml --output results/multi-statistic --quiet
```

Converges at generation 2,327, after a few seconds. `report.json`'s
`converged_on` lists the watched statistics that had settled when the
run stopped: here `["G_ST"]` alone, because $G_{ST}$'s
average was already known precisely (0.0606 ± 0.0047) while D's
was not (0.151 ± 0.020), and `any` needs only one. A run that reaches its cap
records `null`, since it converged on nothing.

### Within-run sigma band

A run burns in and then averages; everything after the burn-in is its
evidence window. The report gives, for the window, both the precision of the
average (the standard error) and generation-to-generation variation
(the standard deviation, $\sigma$). The across-replicate confidence interval
in the next example instead measures uncertainty in a mean across
independent replicates:

<!-- worked-example-config: examples/within-run-sigma-band/config.yaml -->
```yaml
name: Within-run sigma band
description: >-
  Shows how much D still varies from one generation to the next once the run
  has settled, as a band over the evidence window.
class: statistics-and-convergence
_read_only: true  # marks the shipped copy read-only; delete it in your own copy
N: 150
ploidy: haploid
d: 4
m: 0.02
mu: 0.001
seed: 20260916
# Eight independent loci, pooled: one locus alone drifts close to fixation
# for long stretches, where D looks deceptively steady (see the README).
loci:
  - locus_id: 1
    length: 100
  - locus_id: 2
    length: 100
  - locus_id: 3
    length: 100
  - locus_id: 4
    length: 100
  - locus_id: 5
    length: 100
  - locus_id: 6
    length: 100
  - locus_id: 7
    length: 100
  - locus_id: 8
    length: 100
engine_backend: auto
convergence_statistic: D
# Thinned storage: every generation up to 5,000, then one in ten, so the saved
# trajectory is a fraction of the size; the statistics and the report are
# unaffected.
trajectory_retention: thinned
trajectory_stride: 10
trajectory_thinning_start: 5000
n_replicates: 1   # single run: this example is about one run's own sigma band over its evidence window
```

```console
fim run sigma-band.yaml --output results/sigma-band --quiet
```

Converges at generation 19,984, after about a minute and a half. The evidence
window starts at the burn-in (generation 1,522) and holds 18,463 generations:
$D=0.127\pm0.004$ (one standard error), with $\sigma=0.035$, so a single
generation's $D$ lies within $0.127\pm0.071$ about 95% of the time
($2\sigma$). The example
pools eight loci: with one locus, a stretch near fixation can look steady for
the wrong reason (see the
[example's README](examples/within-run-sigma-band/README.md)). In the app, the
trajectory graph draws $\sigma$ as a shaded band over the window in the "Every
generation" display, and the standard error as a band around the running mean
in the two averaged displays; the Band control chooses ±1 or ±2. The numbers
are in `report.json` under `window_statistics`.

### An adaptive replicate batch with a confidence interval

Set `n_replicates` as an upper limit and let
[`precision`](configuration.md#precision) determine when the
across-replicate interval is narrow enough:

<!-- worked-example-config: examples/an-adaptive-replicate-batch-with-a-confidence-interval/config.yaml -->
```yaml
name: Adaptive replicate batch
description: >-
  Runs replicates, up to 50, until the confidence interval for D is narrow
  enough.
class: replicates-and-engines
_read_only: true  # marks the shipped copy read-only; delete it in your own copy
N: 100
ploidy: haploid
d: 5
m: 0.01
mu: 0.001
seed: 20260819
# Eight independent loci, pooled, and a loose precision: a single locus is so
# noisy that each replicate would need tens of thousands of generations (see
# the README).
loci:
  - locus_id: 1
    length: 100
  - locus_id: 2
    length: 100
  - locus_id: 3
    length: 100
  - locus_id: 4
    length: 100
  - locus_id: 5
    length: 100
  - locus_id: 6
    length: 100
  - locus_id: 7
    length: 100
  - locus_id: 8
    length: 100
engine_backend: auto
convergence_statistic: D
precision: 0.03   # plus or minus, for the batch's interval across replicates
# Thinned storage: every generation up to 5,000, then one in ten, so the saved
# trajectory is a fraction of the size; the statistics and the report are
# unaffected.
trajectory_retention: thinned
trajectory_stride: 10
trajectory_thinning_start: 5000
n_replicates: 50
replicate_minimum: 10
```

```console
fim run adaptive-batch.yaml --output results/adaptive-batch --quiet
```

Stops at 10 replicates, the required minimum — `D`'s 95%
confidence interval reaches `0.291 +/- 0.012`, inside the requested `0.03`
half-width (the model's expected `D` is 0.287), so the remaining 40 possible
replicates were never needed. (A larger `precision` stops sooner; a smaller one
runs more replicates.) Each replicate pools eight loci and is itself averaged to
`precision: 0.03`, so most settle within a few thousand generations; one-locus
replicates would take hours for the whole batch (see the
[example's README](examples/an-adaptive-replicate-batch-with-a-confidence-interval/README.md)).
`results/adaptive-batch/summary.json` reports every statistic's
interval; `results/adaptive-batch/replicate-001/` through
`replicate-010/` each hold the ordinary scalar-run files for
that one replicate. Under `engine_backend: auto` the replicates advance
together in one process. To spread the same batch across worker processes
instead, name `engine_backend: lineal` and add `--workers N` (or
`--sequential` for one at a time) — the computed numbers are identical either
way (see [Batches](#batches-n_replicates-greater-than-one)); only the
wall-clock time differs.

### A large-`d` batch under generational-vector

Every example above names `engine_backend: auto`, the recommended choice and
the desktop app's starting value for a fresh form. `auto` never resolves to
`lineal` (the library default): it picks `generational` or
`generational-vector` (see [engine_backend](configuration.md#engine_backend)),
which reproduce `lineal` bit for bit for the same seed, so each example's
committed output matches `lineal`
(`test/validation/test_examples_auto_backend.py` checks this by rerunning each
example on `lineal`). This example and the next explicitly select their
backends. Each performs fixed work: 16 replicates, a one-generation burn-in,
and a 99-generation averaging window, with `stop_batch_early: false`.
These are timing workloads, not equilibrium estimates.
`generational-vector` keeps a dense, array-native representation of every
active replicate at once, and is the fastest measured choice once `d`
grows large enough — see [choosing an engine
backend](fim-simulator-design.md#46-choosing-an-engine-backend) for the
measurements motivating this workload:

<!-- worked-example-config: examples/a-large-d-batch-under-generational-vector/config.yaml -->
```yaml
name: Large-d batch, vector engine
description: >-
  A short 16-replicate timing workload with 70 islands under the
  generational-vector engine.
class: replicates-and-engines
_read_only: true  # marks the shipped copy read-only; delete it in your own copy
N: 500
ploidy: haploid
d: 70
m: 0.05
mu: 0.001
seed: 20260916
mutation_model: finite_alleles
migrant_sampling: continuous
loci:
  - locus_id: 1
    length: 5
engine_backend: generational-vector
convergence_statistic: D
# A fixed 100-generation horizon: each replicate burns in for one generation,
# averages for 99 more, and stops, whatever its noise (a fixed window, no
# precision check).
convergence_burn_in: 1
replicate_averaging_window: 99
n_replicates: 16
stop_batch_early: false   # always run all 16 replicates
```

```console
fim run vector-showcase.yaml --output results/vector-showcase --quiet
```

Ran in about 10 seconds on a development machine, all 16 replicates
for exactly 100 generations (a fixed averaging window), with `D`'s 95%
confidence interval at `0.0465 +/- 0.0020`. In the desktop
app, loading this example uses its `generational-vector`
engine and 16 replicates, whatever your Settings hold, and leaves your
Settings unchanged; a notice lists the differences and offers to make
them your Settings (see
[Loading a configuration does not change your Settings](gui/configure.md#loaded-run-settings)).

### A long-locus batch under the generational engine

The same batch shape as the example above, but favoring locus length over
deme count instead — `d: 35` with a 7-base locus (16384 possible allele
states under `finite_alleles`) rather than `d: 70` with a 5-base one —
and the `generational` engine. Benchmarks favor this backend in regions
with moderate `d` and longer loci:

<!-- worked-example-config: examples/a-long-locus-batch-under-the-generational-engine/config.yaml -->
```yaml
name: Long-locus batch, generational engine
description: >-
  A short 16-replicate timing workload with 35 islands and a seven-base
  locus under the generational engine.
class: replicates-and-engines
_read_only: true  # marks the shipped copy read-only; delete it in your own copy
N: 500
ploidy: haploid
d: 35
m: 0.05
mu: 0.001
seed: 20260916
mutation_model: finite_alleles
migrant_sampling: continuous
loci:
  - locus_id: 1
    length: 7
engine_backend: generational
convergence_statistic: D
# A fixed 100-generation horizon: each replicate burns in for one generation,
# averages for 99 more, and stops, whatever its noise (a fixed window, no
# precision check).
convergence_burn_in: 1
replicate_averaging_window: 99
n_replicates: 16
stop_batch_early: false   # always run all 16 replicates
```

```console
fim run generational-showcase.yaml --output results/generational-showcase --quiet
```

Ran in about 35 seconds on the same hardware, with `D`'s 95% confidence
interval at `0.0451 +/- 0.0024`. These two examples have different model
sizes, so their runtimes are not a controlled backend comparison.
Backend performance depends on the configuration and hardware; see the
[benchmark reference](fim-engine-backend-benchmarks.md).
In the desktop app, loading this example uses its `generational` engine, the
same way the previous example runs with `generational-vector`, without
changing your Settings.

## Sweep a parameter

A sweep runs one configuration over a range of one or more parameters and
keeps every point as an ordinary run inside one study. A sweep file is a normal
configuration plus a `sweep:` block:

```yaml
N: 450
ploidy: haploid
d: 20
m: 0.001
mu: 0.0000003
seed: 20260814
n_replicates: 200
sweep:
  name: Migration and number of demes
  axes:
    m: { start: 0.0001, stop: 0.1, count: 7, scale: log }
    d: [4, 8, 16]
```

Each axis is a list of values or a `start`, `stop` and `count` range
(`scale` is `linear` or `log`). Any setting that is one number, one word or
one on/off switch can be swept: the common ones are `N`, `d`, `m`, `mu`,
`topology` (`island`, `ring`, `linear`, `torus`) and `deme_weighting`, and the
rest include `n_loci`, `locus_lengths`, `initial_allele_count`,
`mutation_model`, `migrant_sampling`, `ploidy` and `precision`. A switch takes
`true` and `false`. Settings that are lists or groups (`p_0`, `mu_b`, `loci`,
`expert`) and `seed` (the sweep sets seeds, see below) cannot be swept.
`fim sweep keys` lists every key with its type and legal values (`--json` for a
program). A point whose settings do not fit together is reported by `plan`, not
discovered while the sweep runs.

`Nm` is the number of migrants per deme per generation. Use it to hold that
number fixed while `N` changes, or to sweep it directly: the sweep sets `m`
to `Nm / N`, where `N` is **individuals per deme** (the same `N` as
everywhere else, whatever the ploidy). `Nm` replaces `m` (a sweep cannot vary
both), needs a single `N` in the base, and a value that would need `m` above 1
is reported by `plan` as invalid. `plan` shows the `m` it worked out, and the
stored sweep records that `N` counts individuals:

```yaml
sweep:
  axes:
    N: [20, 40, 80]
    Nm: [0.5, 2]          # m = 0.025 .. 0.1, whatever N is
```

By default the axes form a grid: every combination, the first axis changing
slowest. Two other designs are available with `strategy` in the `sweep:` block:

```yaml
sweep:
  strategy: zip          # the axes advance together; equal lengths required
  axes:
    d: [4, 8, 16]
    m: [0.1, 0.05, 0.01]
```

```yaml
sweep:                    # a hand-picked set: one mapping per point
  points:
    - { d: 8, m: 0.01 }
    - { d: 2, m: 0.1 }
```

A long list can live in a CSV file with a header row of keys:
`points_file: design.csv` (relative to the sweep file). A repeated point is
dropped, keeping the first. A space-filling design (a Latin hypercube or a
Sobol sequence) is made with a few lines of your own and written to that CSV;
the sweep then runs it as a list. The seeds follow the order of the points.

A sweep of more than 5,000 points stores a fingerprint and the count instead
of the list; the points are worked out again from the settings when needed.

In a sweep, `N` counts **individuals per deme** on an axis and in the base
alike, the number the desktop app shows; the file's `ploidy` turns it into gene
copies for each point.

A sweep keeps every generation of every point unless told otherwise, but the
largest sweeps need not. A `recording:` block in the `sweep:` section, or the
flags of `fim sweep plan` and `fim sweep run`, choose what each point keeps:

```yaml
sweep:
  recording:
    retention: tapered     # full (default), thinned, tapered or statistics
    keep_last: 1000        # tapered: the last generations kept in full
    keep_burn_in: false    # tapered: keep the burn-in frames too
    history: compact       # compact (the sweep default) or exact
  axes: { d: [4, 8, 16] }
```

```bash
fim sweep run sweep.yaml --recording tapered --keep-last 2000 --history exact
fim sweep plan sweep.yaml --recording statistics   # the disk for every level, side by side
```

`tapered` keeps the last generations in full and thins the past further back,
so a point's frames grow with the logarithm of its length;
`statistics` keeps no trajectory log (the point still has its report, summary,
scatter plot and a decimated history of at most 2,048 generations); `thinned`
keeps every Nth generation after a start. A point whose existing run kept less
than the sweep asks for is computed again; a run that kept more is reused. The
statistic history is `compact` in a sweep by default: each value rounded to
about a hundredth of the precision, roughly 80 times smaller, which changes no
answer the sweep reports. `exact` writes full precision (what a single `fim run`
does). The plan prints the disk each level would take; a sweep whose estimate
for the level it keeps reaches 50 GB (change it with `--confirm-disk SIZE`)
needs `--yes`. The desktop Sweep dialog's **Keep** choice shows the same
figures; see [desktop sweeps](gui/sweeps.md#recording-and-disk).

A sweep can also be made smaller after it has run, once its answers are in:

```bash
fim sweep compact STUDY_ID --dry-run                  # what would change
fim sweep compact STUDY_ID --keep corners             # statistics only, except the corner points
fim sweep compact STUDY_ID --to tapered --keep-last 2000
```

`--keep` names the points to leave exactly as they are (`corners` of the grid,
`every=K` for every Kth point, or `none`, the default); the others are reduced
to `--to`: `statistics` (the trajectory logs are dropped; the report, summary,
scatter plot, deme-pair statistics and a decimated history stay) or `tapered`
(a tapered log, the same file a run that had tapered itself would have written).
No reported number changes and every run's manifest is rewritten to match, so
the results card, exports and trajectory view keep working. A run that another
study also lists is refused unless `--all-studies` is given, a run already
small enough is left alone (so the command is safe to repeat), a thinned run
cannot be tapered, and the sweep must not be running.

By default each point receives a distinct seed (`base seed + point index times
n_replicates`), so no two points share a replicate seed. Set
`seed_policy: same` in the `sweep:` block to give every point the base seed.

```bash
fim sweep plan sweep.yaml            # list the points and their validity; runs nothing
fim sweep run sweep.yaml             # create the study and run every point
fim sweep resume STUDY_ID            # run only the points still missing
fim sweep resume STUDY_ID --retry-failed
fim sweep resume STUDY_ID --rerun   # also recompute the completed points
fim sweep replan STUDY_ID [--dry-run]   # after an upgrade changed run ids
fim sweep status STUDY_ID [--watch SECONDS]   # where it stands, time left
fim sweep run sweep.yaml --pilot 2   # the 2 costliest points first, then ask
fim sweep report STUDY_ID --statistic D --csv
fim sweep export STUDY_ID results.csv [--statistic D --statistic G_ST] [--wide] [--with-theory]
fim sweep compare STUDY_A STUDY_B [--statistic D] [--on m] [--csv out.csv]
fim sweep trajectories STUDY_ID --csv out.csv [--statistic D]   # how each point approached equilibrium
fim sweep keys [--json]               # every parameter a sweep can vary
fim sweep compact STUDY_ID [--keep corners] [--to tapered]   # make a finished sweep smaller
fim sweep run sweep.yaml --points-at-once 4 --workers 2   # or --sequential
```

- `trajectories` writes how each point approached equilibrium, not only where
  it ended: for each finished point, at most 512 generations (generation 0, then
  closely spaced early generations and evenly spaced later ones), with the mean
  over the replicates still running, the interval of that mean, the spread, and
  how many replicates contribute. `quorum` is `no` where fewer than half the
  replicates are still running, so the later part of a curve is less sure. The
  first read of a long sweep reads every replicate's history once (about 35
  megabytes a second); the answer is kept in a cache that is safe to delete.
- `plan` checks every point first. A combination that cannot run (a torus
  whose `rows * columns` differs from `d`) is listed with its reason.
- A sweep of 100 points or more asks for `--yes`.
- `plan` states the work as a range, in replicate-generations (one replicate
  stepped one generation): at least the burn-in for the fewest replicates a
  point may stop at, at most the generation cap for every replicate. It is a
  range because convergence time is not known in advance.
  `plan` (and `run`, before it starts) then times the engine for a few seconds
  and adds how long the sweep may take and how much disk it may use, for
  example `time about 35 s to 1 h 50 min (1 point(s) at once on 10 core(s))`
  and `disk about 62 MB to 12 GB with the full trajectory log; 17 MB to 3.2 GB
  statistics only`. The probe runs the selected
  configuration briefly and discards its output, measuring this computer. Points
  of a similar size share one measurement, and the results are remembered
  beside your preferences file (`sweep-throughput.json`) for reuse.
  `--no-probe` skips the measurement and prints the work range only.
  The desktop Sweep dialog shows the same range, time, and disk figures.
- A sweep starts the points with the longest estimated duration first.
  Scheduling does not change point configurations or results.
  Each finished point's line reports its duration.
- `fim sweep status STUDY_ID` shows the points by state, whether a process is
  running the sweep (it reads the sweep's lock, including from another
  terminal), the last point finished, any failures with the command to retry
  them, and the estimated time remaining. The estimate uses completed
  points' measured durations relative to their planned upper estimates,
  scaled to the remaining points. Early convergence can shorten execution.
  `--watch SECONDS` repeats it until the sweep is finished.
- To document a sweep for reproduction, record the `digest sha256:...`
  printed by `plan`. It identifies the experiment's base settings,
  axes, seed policy, and point IDs; key order does not affect the digest.
  `fim sweep spec STUDY_ID > sweep.yaml` writes the sweep file that
  regenerates a study, with every axis written out as its exact values, and
  `fim sweep plan sweep.yaml` then prints the same digest.
  `fim sweep verify STUDY_ID` recomputes every finished point, compares each
  with its stored result bit for bit, prints one line saying whether they all
  matched, and records the date, the software version and the verdict in
  `verified.json` beside the sweep's status file (exit status 1 if any
  differed; both runs are then kept). The export's `.meta.json` file carries
  the digest, that record, and for each point its seed, engine and software
  version, so the table can be read without the app.
- Limit sweep resource use with
  `--max-hours H` (time), `--max-new-points N` (newly computed points,
  excluding reused results), and `--max-disk SIZE` (for example `50GB`,
  counted as the size of completed points). These options apply to
  `run` and `resume`. At a limit the run starts no more points,
  pauses the ones in flight where it
  can, says which limit it reached, and exits with status 3 (0 means done, 1 a
  failure, 130 a cancellation). Unstarted points remain pending rather than
  failed. Continue with `fim sweep resume`, optionally setting a new limit.
  A limit equal to the remaining work is not reached.
- `fim sweep run FILE --pilot K` runs the K points that may take longest first,
  prints each one's planned range against what it took and the time left for
  the rest, and asks before running the rest (`--yes` answers yes). The pilot
  points remain stored whether or not the rest proceeds.
  Continue a stopped pilot with `fim sweep resume`.
- **Points run at the same time, by default as many as fill the machine, each
  in a separate process.** A single run, or a batch on any engine but `lineal`
  (including `auto`), uses about one core; a `lineal` batch of `r` replicates
  uses about `r`. So a sweep of single runs or generational batches runs up to
  one point per core, a sweep of small lineal batches a few at once, and a sweep
  of large lineal batches one point at a time.
  `--points-at-once N` sets the number, `--workers N` the worker processes each
  batch point uses, and `--sequential` runs one point at a time with one
  worker. In the app, the Sweep dialog's **Points at once** does the same.
  Results do not depend on the
  setting: each point is fixed by its configuration and seed.
- A point that fails for a transient reason, such as a worker process the
  system ended, is tried again once; `--max-attempts N` sets how many tries in
  all. A failure the engine reports is never tried again, because the same
  configuration fails the same way. A pool of workers that breaks is replaced,
  so the points after the failed one still run.
- `fim sweep export` writes the sweep's results as a table you can open in R,
  Python or a spreadsheet, with a `results.meta.json` file beside it that holds
  the sweep file's settings, the software version, and what every column means.
  The default is one row for each point and statistic (the "tidy" form that R
  and Python group-by tools expect) for `D` and `G_ST`; `--statistic NAME`
  (repeat it) or `--all-statistics` chooses others, and `--wide` writes one
  row per point instead. Each row gives the mean, its confidence interval, the
  spread across replicates, the seed and the software version. `--with-theory`
  adds the closed-form prediction and the difference from it (`D`, `G_ST`,
  `E_ST`, `H_S` and `H_T` have one). A `.jsonl` file name writes JSON Lines.
  Points with no result yet are left out unless you pass `--include-unfinished`.
  The command will not replace a file that exists unless you pass `--force`.
  The desktop sweep results screen offers the same table as **Save
  table…**; see [desktop sweeps](gui/sweeps.md#results).
- `fim sweep compare` lines up two or more sweeps on the axes they all vary and
  prints one statistic from each at every value they share, so you can ask
  whether a ring behaves like an island or whether 100 individuals per deme
  differ from 1,000. Name a sweep in the output with `STUDY=label`. It refuses,
  and says why, when the sweeps share no axis, share no value of an axis, have
  different ploidy, or one varies an axis the others do not. It lists the base
  settings that differ between the sweeps, which is the difference you are
  investigating. For exactly two sweeps it adds their difference (first minus
  second) with a 95% interval from Welch's method, which allows each sweep a
  different spread. That interval assumes the two sweeps' replicates are
  independent, so the command examines recorded seeds and
  says whether they were different, shared (two sweeps built from the same
  seed and grid use the same seeds, point for point), or partly shared. It
  does not adjust the method for shared seeds. The comparison is recomputed
  on request rather than stored.
  For the desktop comparison screen, see
  [compare sweeps](gui/compare.md#compare-sweeps).
- Each point has a health mark. A point is `unreliable` if every replicate
  stopped at the generation limit before its statistics settled, `degraded` if
  some did or if the interval is wider than the `precision` you asked for, and
  `healthy` otherwise. The mark is in the `health` column of the export and in
  `fim sweep report`. A point that is not healthy is flagged, never left out.
  The desktop app draws the marks on its charts; see
  [point health](gui/sweeps.md#point-health).
- A sweep holds a lock on its Study while it runs. A second `fim sweep resume`
  (or the app) on the same Study stops at once with a message that another
  process is running it, instead of both writing the same files. The system
  releases the lock when the process exits, including after a crash.
- A run id is a hash of the point's configuration, so a release that changes
  what the hash covers makes a stored sweep refuse to run ("the stored sweep
  plan no longer matches"). `fim sweep replan STUDY_ID` enumerates the points
  again from the stored specification, matches each to its old entry by its
  coordinates, and rewrites the plan; recorded failures follow their points.
  `--dry-run` shows what would change first. Runs already in the Study are not
  touched: one that no longer matches a point stays attached as an ordinary
  run, is listed, and its point runs again.
- A point's run id is a hash of its configuration, so resuming, or running a
  second sweep that overlaps the first, reuses the runs that already exist
  instead of computing them again. Reuse requires identical configurations,
  including the seed, so an overlapping sweep must use the same base seed and
  the same axis order, and a run made by the same software version. A point
  whose only run came from another version is recomputed and compared with it
  (see [Reproduce a run](#reproduce-a-run)).
- `fim sweep resume STUDY_ID --rerun` recomputes the points that are already
  complete, from the stored sweep specification, and compares each with the
  run it has. Use it to confirm that a sweep still reproduces, for example
  after you reinstall or update `fim` on the same version. A result that
  matches bit for bit replaces the old run, so the study keeps one run per
  point. A result that differs keeps both runs, writes `reproducibility.json`
  beside the new one, and makes the command exit with status 1. Points that
  are missing or failed follow the usual rules (`--retry-failed` for failed
  points). If you cancel a rerun, run it again to start the recompute over;
  plain `resume` only runs the points still missing.
- In the desktop app, a sweep is set up in Configure and continued, retried,
  or recomputed from Home; see [desktop sweeps](gui/sweeps.md).
- A failed point is recorded and the sweep continues. Press Ctrl+C to stop;
  finished points stay in the study.
- Every point keeps its full trajectories. A sweep multiplies disk use by its
  number of points.
- Each point's run is named after the study and its parameter values, for example
  `Migration and number of demes sweep m=0.001, d=8`, and its description says
  where it sits in the sweep (`Point 8 of 21 of the sweep "Migration and
  number of demes": m=0.001 of m=[0.0001..0.1] (7 values); d=8 of
  d=[4, 8, 16].`). A reused run keeps a name it already had.

## Re-analyze a trajectory

```console
fim stats TRAJECTORY [--manifest PATH] [--generation N]
    [--q ORDER ...] [-o PATH | --output PATH]
```

The default manifest is `manifest.json` beside `TRAJECTORY`. The final
generation is analyzed unless `--generation` selects another persisted
generation. Repeat `--q` to evaluate several Hill-number differentiation
orders without re-running the simulation:

```console
fim stats run/trajectory.tlog --q 0 --q 1 --q 2
```

JSON is printed to standard output. `--output` also writes the same result.
`--q 0` and `--q 2` match the report's $K_{ST}$ and `D`; `--q 1`
matches $E_{ST}$ using the recorded `deme_weighting`.
See [differentiation measures](jost-differentiation-measures.md) for the
Hill-number family and [statistics](statistics.md) for implementation links.

## Check for updates

```console
fim update --check
```

This explicit command queries the latest GitHub Release and prints its download
page when a newer version exists. It does not download or modify anything.
This is the application's only network path; `run`, `stats`, and `init` are
offline.

## Desktop GUI (`fim-gui`)

```console
fim-gui
```

The desktop app renders local HTML, CSS, and JavaScript in the operating
system's native web view. It uses the same simulation functions and
`results/` folder as the command line. `python -m pip install .` provides
both `fim` and `fim-gui`. Packaged executables open the GUI with no
arguments: double-click `fim-windows-x64.exe` or `fim.app`, or run
`fim-gui`/`fim --graphical` on Linux.

The desktop app has its own guide, bundled offline in the app under
**Help > Desktop help**: the [desktop app guide](gui/README.md). It covers:

| Topic | Desktop guide page |
|---|---|
| Screens, open documents, shortcuts, and menus | [Desktop app guide](gui/README.md) |
| Units, migration convention, generation order, and stopping semantics | [Implementation conventions](gui/conventions.md) |
| Configure fields and their YAML keys, run settings, loaded run settings | [Configure](gui/configure.md) |
| The run view, trajectories, references, and hover readouts | [Run and results](gui/run-and-results.md) |
| Closed-form predictions and sweep-point selection | [Explore](gui/explore.md) |
| Sweep setup, results, and point health | [Sweeps](gui/sweeps.md) |
| Comparing runs and sweeps | [Compare runs and sweeps](gui/compare.md) |
| Home, names, saved runs, examples, and read-only items | [Home and organization](gui/organize.md) |
| Settings and saved preferences | [Settings and saved preferences](gui/settings.md) |
| Closing the window, shutdown, and other problems | [Troubleshooting](gui/troubleshooting.md) |

A double-clicked or `fim --graphical`-launched GUI accepts no logging
flags. Set `FIM_LOG_LEVEL` and `FIM_LOG_OPTIONS` before launching; they
correspond to `-l` and `-L` below. The separate `fim-gui` entry point also
accepts directory flags; see
[Where results, logs, and preferences are
written](#where-results-logs-and-preferences-are-written), below.

## Global flags

```console
fim --help
fim --version
fim -l debug run myrun.yaml
fim -l info -L file=none run myrun.yaml
```

`--version` comes from the same `version.txt` value used by packages,
manifests, and release tags.

`-l`/`--log LEVEL` (`debug`, `info`, `warn`, `error`, or `critical`;
default `warning`) and `-L`/`--log-options KEY=VALUE[,KEY=VALUE]...`
control the operational log, independently of
`run --quiet` and progress/artifact messages
above. Every run already writes a rotated log file at
`project-root/logs/fim.log` by default, whether or not `-l`/`-L` is
given at all; `-l debug` raises what reaches it (and the terminal),
`-L file=none` turns the file off entirely. Both flags must appear
*before* the subcommand name (`fim -l debug run ...`, not
`fim run ... -l debug`). Full flag reference, every `-L` key, and where
each log call in the source lives:
[operational logging design](fim-logging-design.md).

`--logging-config PATH` (or the `FIM_LOGGING_CONFIG` environment
variable) replaces `-l`/`-L` entirely for one invocation, reading a
standard-library `logging.config.dictConfig` YAML file instead — for a
setup beyond the inline `-L` options
(several loggers, several handlers, a custom formatter). It is not
combined with `-l`/`-L`: the selected configuration replaces the other.

### Where results, logs, and preferences are written

By default, `fim` writes into `results/`/`logs/` in a source checkout,
or a `fim/` folder in your home directory for a packaged build (see
[operational logging design](fim-logging-design.md) §6). Four flags,
each with a matching environment variable, override this independently
of one another — every one must appear *before* the subcommand name,
the same as `-l`/`-L` above:

| Flag | Environment variable | Overrides |
|---|---|---|
| `--root PATH` | `FIM_HOME` | Both `results/` and `logs/` at once, unless a more specific flag/variable below also applies |
| `-R`/`--results-directory PATH` | `FIM_RESULTS_DIRECTORY` | Where runs, batches, Studies, and Experiments are written |
| `--log-directory PATH` | `FIM_LOG_DIRECTORY` | Where `fim.log` is written |
| `--preferences-file PATH` | `FIM_PREFERENCES_FILE` | Desktop `preferences.json` (form defaults, named presets, Settings); affects GUI launches |

`fim-gui` (the separate desktop-app entry point) accepts the identical
four flags directly. For `fim --graphical` or a double-clicked build,
set matching environment variables before launching, as with
`FIM_LOG_LEVEL`/`FIM_LOG_OPTIONS`.
Settings additionally provides a **Storage location** field for
results; see [storage location](gui/settings.md#storage-location).

## Output schemas

### `trajectory.tlog`

The run's trajectory, as a compact binary log: every nonzero allele frequency
of every generation, most generations stored as only what changed since the
one before. You rarely open it yourself; the app and `fim stats` read it
directly. To get the text form (`trajectory.jsonl`, below), export it:

```console
fim export results/my-run
```

[The trajectory log](trajectory-log.md) explains the size, what happens when
a run is interrupted, how to export, and the file format.

### `trajectory.jsonl`

Produced only by `fim export`, from `trajectory.tlog`. One JSON object per
nonzero frequency, byte for byte what `json.dumps` writes with sorted keys:

```json
{"allele_id":0,"deme":1,"frequency":0.5,"generation":0,"locus_id":1,"run_id":"run-example"}
```

| Field | Type | Meaning |
|---|---|---|
| run_id | string | Deterministic identity for one parameter set and seed |
| `generation` | integer | Generation, beginning at 0 |
| `deme` | integer | One-based deme number |
| locus_id | integer | Configured positive locus identifier |
| allele_id | integer | Opaque identity-only allele label |
| `frequency` | number | Positive allele frequency |

### `equilibrium_trajectory.tlog`

Written only by an [equilibrium-split](#equilibrium-split-founding) run: the
ancestral trajectory, simulated before splitting into
your demes, as a binary log like `trajectory.tlog`. Every generation of that
ancestral phase is kept. Its rows (export it with `fim export` to see them as
`equilibrium_trajectory.jsonl`) have exactly the `trajectory.jsonl` fields
above, so anything that reads the trajectory reads this file too, with two
differences:

- `deme` is always 1: the ancestral population is one deme holding all of
  your demes' gene copies.
- `generation` counts the ancestral phase separately, from 0 (the starting
  draw) to the manifest's `equilibrium_generation_count`. Its last
  generation is the population that was split. The main run's
  generation 0 in `trajectory.tlog` is the moment after that split,
  so ancestral generation `g` happened
  `equilibrium_generation_count - g` generations before it.

The file has the same `run_id` as the main trajectory, and the manifest
records its digest as `equilibrium_trajectory`. Runs with any other
starting condition do not have this file.

### `manifest.json`

The manifest records the complete parameter mapping, seed, software version,
UTC start/end timestamps, generation, watched statistic, and whether
convergence or the hard cap ended the run. It also carries:

- `schema_version`: manifest format version.
- `convergence.generation_count`: number of generations stored in
  `trajectory.tlog`. With full retention this is
  `convergence.generation + 1`; thinning reduces the stored count.
- `artifacts`: the SHA-256 digest and byte count of `trajectory.tlog`,
  `report.json`, `scatter.png`, and every other file the run wrote (for
  example `equilibrium_trajectory.tlog`) as they existed at the moment
  the run finished writing and flushing them. `fim stats` recomputes and checks the
  trajectory's digest (and its generation count) before reading a single
  row, so a trajectory edited, truncated, or replaced after the run
  completed is refused with a clear error rather than re-analyzed
  silently.

output_directory (scalar-run artifacts, or the
`replicate-NNN/` plus `summary.json`/`manifest.json` tree for a batch) is
built in a hidden temporary location beside the target path and published
with a single atomic rename only once every file in it is flushed and
`manifest.json` has been written last with its `artifacts` digests. An
interrupted run — an exception, `^C`, or a killed process — therefore never
leaves a partial directory at the target path: it is either not there at
all, or complete. Directory publication is separate from trajectory durability. The log
writer syncs committed blocks; see
[trajectory durability](trajectory-log.md#for-sysops-size-interruption-and-recovery).
Atomic rename alone does not guarantee that every output artifact survives
an unclean power loss.

**Compatibility with `fim` 1.0.0 output.** `fim` 1.0.0, the only version
released before schema_version and `artifacts` existed, wrote manifests
with neither field. `fim stats` refuses such a manifest — it has no
digest to verify the trajectory against — with an error naming both the
cause and that there is no automated migration: re-run the same
configuration (the manifest's `parameters`) with the current `fim`
to get a manifest this version can read and verify.

### `report.json`

The final report contains:

- run identity, generation, convergence flag, watched statistic, and reason;
- $H_S$, $H_T$, and the correctly partitioned $H_{ST}$;
- $G_{ST}$ (`null` only when *every* tracked locus is fixed for the same
  allele in every deme; under per-locus ratio averaging, undefined loci
  are excluded rather than replacing defined values);
- Jost's `D`, entropy differentiation $E_{ST}$, and allele-number
  differentiation $K_{ST}$;
- literature-derived supplemental statistics:
  Caballero-García-Dorado allelic distance $A_{\mathrm{CGD}}$, Gregorius
  $\delta_G$ (`Delta`), and Sherwin mutual information $I$ (`MI`);
- the within- and between-deme gene identities $G_s$ (`Gs`) and
  $G_d$ (`Gd`);
- Nei's (1973) $D_m$ (`D_m`) and $R_{ST}$ (`R_ST`), Nei's
  logarithmic $G'_{ST}$ (`G_ST_NEI_LOG`), Hedrick's standardized
  $G'_{ST}$ (`G_ST_HEDRICK`), and coancestry $F_{ST}$ (`F_ST`),
  each computed from the pooled $H_S$/$H_T$ and `null` where
  undefined;
- Nei's genetic distance across all demes in four forms, and the matching
  identities: `NEI_D_ALL_GEO`, `NEI_D_ALL_GEO_LOCUS_MEAN`, `NEI_D_ALL_ARITH`,
  `NEI_D_ALL_ARITH_LOCUS_MEAN`, `NEI_I_ALL_*` (see
  [Nei distances](#nei-distances)). A distance is `null` when infinite; its
  identity is always a number.

A `report.json` may lack statistics added after its software version.
Opening the run computes missing values from its saved trajectory
without rewriting the file.

Multiple loci are independent repeats. $H_S$, $H_T$, $H_{ST}$, $E_{ST}$,
and $K_{ST}$ are arithmetic means across loci. `D` and $G_{ST}$
instead follow [locus_aggregation](configuration.md#locus_aggregation):
by default (`ratio_of_means`), $H_S$/$H_T$ are pooled across loci
first and one `D`/$G_{ST}$ is computed from those pooled values, not
averaged per-locus ratios; the opt-in `mean_of_ratios` restores the
per-locus-ratio-then-average behavior. `D` and $K_{ST}$ always use equal
deme weighting. `deme_weighting` affects $E_{ST}$, $\delta_G$,
and mutual information.

### `pairwise.json`

Every pair of demes' Nei identities and pairwise $F_{ST}$ at the run's
final generation, so any pair can be compared later or analyzed outside
`fim`. Written for every scalar run and every batch replicate, and digested
in the manifest.

```json
{
  "schema_version": 1,
  "generation": 1234,
  "deme_count": 4,
  "mode": "full",
  "encoding": "upper-triangle-row-major",
  "matrices": {
    "NEI_I_PAIR_GEO": [0.91, 0.87, 0.80, 0.93, 0.85, 0.88],
    "NEI_I_PAIR_GEO_LOCUS_MEAN": [],
    "NEI_I_PAIR_ARITH": [],
    "NEI_I_PAIR_ARITH_LOCUS_MEAN": [],
    "F_ST_PAIR": []
  }
}
```

(The empty lists stand for lists of the same length.) Each list is the upper
triangle of a symmetric matrix, row by row: pairs (1, 2), (1, 3), (1, 4),
(2, 3), (2, 4), (3, 4) for four demes, numbered from 1 as everywhere else in
`fim`. The diagonal is not stored (identity 1, $F_{ST}$ 0). Each
distance is $-\ln(I)$ for its identity $I$; a pairwise $F_{ST}$ is `null` where
undefined (both demes fixed for the same allele). With more demes than
[`--pairwise-max-demes`](#run-a-simulation), `"mode"` is `"skipped"`, the
limit is recorded as `"max_demes"`, and there are no matrices.

### Nei distances

`fim` reports Nei's genetic distance with the geometric-mean
denominator and with the arithmetic-mean denominator (Jost, L. (2026)
private communication), for the pair of demes the scatter plot shows and
across all demes, combining loci by Nei's rule or by averaging per-locus
distances. In the desktop GUI the forms start hidden: choose them in
Settings, "Statistics shown", or with "Choose…" in the statistics panel's
caption. The pair rows name the pair they describe and follow the scatter
plot's deme selectors, live, after the run, and while scrubbing.

Two results need a word of explanation:

- **∞** means the demes share no allele, so the identity is 0.
- A **negative** all-demes geometric distance is not an error. That form
  divides the average between-deme identity by the *geometric* mean of the
  within-deme identities, which one very diverse deme can drag far down.
  It signals strongly unequal within-deme diversity: compare the arithmetic
  form beside it, which stays non-negative, and the within-deme diversity
  ($H_S$, effective number of alleles). Pair forms and arithmetic forms
  are never negative.

The arithmetic all-demes distance equals $-\ln(1 - D)$ for Jost's $D$.
[Nei distances in fim](nei-distances.md) has the full explanation, a worked
example of a negative value, and the formulas.

### `scatter.png`

- $d=2$: direct Deme 1 versus Deme 2 scatter with a diagonal reference.
- $d=3$: direct three-dimensional scatter.
- $4 \le d \le 6$: every pairwise deme projection.
- $d>6$: direct Deme 1 versus Deme 2 scatter, same as $d=2$ — not a
  PCA projection.

One point represents one `(locus, allele)` pair. Coincident points are enlarged
and annotated.

#### What the marker colors mean

Color identifies the most frequent allele in each displayed deme:

| Color | Meaning |
|---|---|
| Blue | The most frequent allele in either of the two demes shown |
| Orange | Every other allele |

There are two blue points when the demes have different most-frequent
alleles, and one when they share the same most-frequent allele.

For ties, only the first allele is marked. The choice is deterministic and
stated in the legend.

Blue indicates a frequency maximum, not statistical significance or a
frequency threshold.

### Batch `summary.json` and `manifest.json`

Written only for n_replicates greater than one, alongside the
`replicate-NNN/` subdirectories, each of which holds the scalar-run files
above.

`summary.json` maps each reported statistic name (every global statistic in
[`report.json`](#reportjson): `D`, $G_{ST}$, $E_{ST}$,
$K_{ST}$, $H_S$, $H_T$, $H_{ST}$, $A_{\mathrm{CGD}}$,
Gregorius $\delta_G$, Sherwin's $I$ (`MI`), $G_s$, $G_d$, the pooled-heterozygosity
measures and the all-demes Nei forms) to its across-replicate confidence
interval:

```json
{
  "D": {
    "mean": 0.643,
    "half_width": 0.021,
    "low": 0.622,
    "high": 0.664,
    "sample_std": 0.133,
    "sample_count": 40,
    "confidence": 0.95
  }
}
```

`half_width` and `sample_std` describe different sources of uncertainty:

- `half_width` is how precisely the **average** is known — the "± 0.021"
  half of a "0.643 ± 0.021" report. Running more replicates shrinks it.
- `sample_std` is how much the **replicates themselves** differ from one
  another. Running more replicates does not shrink it, because it
  describes run-to-run variability rather than
  measurement of it.

`sample_std` can be `null` for intervals built by resampling rather than
the Student's-t formula (`fim.engine.bootstrap_replicate_summary`).
Resampling intervals can be asymmetric; use `low` and `high` rather than
assuming bounds of `mean ± half_width`. CLI-generated intervals use the
Student's-t form and report a numeric `sample_std`.

A `summary.json` written by an older version of this program has no
`sample_std` field at all. Nothing needs converting: every tool here
reads such a file as though the value were `null`.

$G_{ST}$ can have a smaller sample_count than the other statistics: a
replicate whose locus is monomorphic across every deme reports $G_{ST}$ as
`null` in `report.json`, and that replicate is excluded from
$G_{ST}$'s interval. The
other statistics that can be `null` because they are undefined ($R_{ST}$,
both $G'_{ST}$, $F_{ST}$) are treated the same way. A Nei distance
is different: `null` there means *infinite*, and a mean that includes an
infinite value has no finite value, so a Nei distance infinite in any
replicate is omitted from `summary.json`; its identity is still summarized.
A statistic left with fewer than two defined replicates is omitted from
`summary.json` entirely.

The batch-level `manifest.json` (distinct from replicate manifests) records
schema_version, the batch run_id, every replicate_run_ids entry,
replicate_count, the shared `parameters`, batch start/end timestamps, and
software_version. Convergence outcomes are recorded per replicate.
The batch manifest also carries `artifacts`: the
SHA-256 digest and byte count of `summary.json` and each replicate's
`manifest.json` (keyed `replicate-NNN`), recorded once every one of them is
flushed, so an edited, truncated, or replaced batch-level artifact is
detectable the same way a scalar run's is. Under parallel execution — the
CLI default — an adaptive early stop can leave a worker that
had already started a `replicate-NNN/` directory before the stop was
decided; that directory is pruned before publishing, so the `replicate-*`
published subdirectories match `replicate_run_ids`.

## Reproduce a run

1. Copy `manifest.json`.
2. Use its `parameters` object as a new YAML config.
3. Run the same `fim` version shown in software_version.
4. Compare `trajectory.tlog` and `report.json` byte for byte on the same
   platform. Desktop logs are canonicalized on close; if canonicalization
   reports a failure, compare exported `trajectory.jsonl` instead.

**Across software versions.** One configuration and seed give the same result,
bit for bit, every time within a software version (checked for single runs and
for batches, run sequentially and in parallel). A run records the version that
made it, and a run is only reused, by **Run** or by a sweep, when that version
matches the current one. A configuration whose only run came from another
version is recomputed, and the new run is compared with the old one using the
digests each run records for its trajectory, report and summary (the scatter
image is left out, since its bytes can change without any result changing). If
they match, the old run is replaced by the new one and you are told. If they
do not match, a warning names the values that changed, both runs are kept, and
the difference is written to `reproducibility.json` beside the new run.
`fim sweep` reports the difference and exits with status 1.

Reproduction requires the same version, parameters, seed, and compatible
platform arithmetic. Compiled backends are checked for bit parity on the
development platform; cross-platform results are not guaranteed byte
identical. Manifest timestamps may differ. See
[`engine_backend`](configuration.md#engine_backend).

## Troubleshooting

- **Unknown key:** compare the named key with
  [configuration.md](configuration.md); typos are never ignored.
- **Output already exists:** select a new output directory. `fim` refuses
  to publish into one that already exists at all, even if empty — never
  appended, overwritten, or reused.
- **Reached the cap:** the message names how many generations the model
  needs to forget its starting state. Inspect the trajectory and report, then
  increase max_generations, ask for less precision (a larger `precision`), or select another
  convergence statistic based on the study's needs.
- **A run finished in a few hundred generations:** check whether
  convergence_burn_in and max_generations were set to small fixed numbers.
  Leave them on `auto`; see
  [Why does my run take so long?](#why-does-my-run-take-so-long).
- **Windows warning:** verify the release checksum before running the unsigned
  executable. See [SECURITY.md](../SECURITY.md).
