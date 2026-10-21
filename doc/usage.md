# Using `fim`

This guide covers commands, desktop workflows, and output files. See
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
- [Names, descriptions, and documentation](#names-descriptions-and-documentation)
- [The Examples experiment](#the-examples-experiment)
- [Read-only items](#read-only-items)
- [Opening a saved example](#opening-a-saved-example)
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
`project-root/results/example-run.yaml`. Existing files are protected unless
`--force` is present.

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
(see [Loading a configuration does not change your Settings](#loading-a-configuration-does-not-change-your-settings)).
**View YAML** shows the example's configuration file, ready to
copy. An example that the form cannot represent (a different `mu` for each
locus that no single per-base rate `mu_b` produces, for example) or that has
no configuration file says so in the dialog, and
**Load into Configure** stays unavailable for it. **Open saved result**
shows the bundled result without running
anything (see [Opening a saved example](#opening-a-saved-example)); it is
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
Sherwin mutual information `MI` (information about population membership)
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

Unlike $\delta_G$ and $D$, $A_{\mathrm{CGD}}$ is not bounded by 1; `MI` uses natural-log units.
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
[Loading a configuration does not change your Settings](#loading-a-configuration-does-not-change-your-settings)).

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
(`scale` is `linear` or `log`). The keys you can sweep are `N`, `d`, `m`, `mu`,
`topology` (`island`, `ring`, `linear`, `torus`) and `deme_weighting`.

In a sweep, `N` counts **individuals per deme** on an axis and in the base
alike, the number the desktop app shows; the file's `ploidy` turns it into gene
copies for each point.

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
fim sweep run sweep.yaml --points-at-once 4 --workers 2   # or --sequential
```

- `plan` checks every point first. A combination that cannot run (a torus
  whose `rows * columns` differs from `d`) is listed with its reason.
- A sweep of 100 points or more asks for `--yes`.
- `plan` states the work as a range, in replicate-generations (one replicate
  stepped one generation): at least the burn-in for the fewest replicates a
  point may stop at, at most the generation cap for every replicate. It is a
  range because convergence time is not known in advance.
  The same range appears in the app's
  Sweep dialog.
  `plan` (and `run`, before it starts) then times the engine for a few seconds
  and adds how long the sweep may take and how much disk it may use, for
  example `time about 35 s to 1 h 50 min (1 point(s) at once on 10 core(s))`
  and `disk about 62 MB to 12 GB with the full trajectory log; 17 MB to 3.2 GB
  statistics only`. The probe runs the selected
  configuration briefly and discards its output, measuring this computer. Points
  of a similar size share one measurement, and the results are remembered
  beside your preferences file (`sweep-throughput.json`) for reuse.
  `--no-probe` skips the measurement and prints the work range only.
  The app's Sweep dialog shows the same three lines (time, disk, and what was
  measured) under the plan, for the points that still have to run; it says
  "Measuring the engine…" for the first few seconds of a new size, and a
  running sweep's progress view shows the time left and how long each point
  took.
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
  worker. In the app, the Sweep dialog's **Points at once** does the same, and
  the Settings value "max workers" applies. Results do not depend on the
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
  In the app, the sweep results screen has the same two things as buttons:
  **Save table…** writes the table (and its `.meta.json` file) for every
  statistic you have chosen to show, with the closed-form columns, and **Save
  chart…** saves the chart as it is drawn as a PNG image.
  In the app, the sweep results screen draws one curve for a sweep over one
  axis. To compare values of a second axis, set **and** to "(none: a line)"
  and choose **Curves by**: each value gets a color, marker shape,
  error bars, and optional theory curve. **Error
  bars** show the 95% interval of the mean (the default), the spread across
  replicates, or both. Axes are linear or log; "Automatic" uses a log axis for a
  geometric series of values. With the chart selected, the arrow keys move from
  point to point and Enter opens that point's run. The heat map's **Show** menu
  also offers the difference from theory as a fraction of the theory, next to
  the absolute difference; `fim sweep export --with-theory` writes both.
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
  In the app, comparing starts on Home: an experiment row has **Compare
  sweeps…** for the sweep studies inside it, and **Select** lets you tick any
  sweep studies, from any experiment, and choose **Compare selected sweeps…**.
  The comparison screen draws one curve per sweep with a distinct color and
  marker shape, over the axis you pick (the other shared axes are held at a
  value), with the error-bar, scale and theory controls of the results screen.
  With exactly two sweeps a second panel shows their difference with its
  interval and a zero line. Each theory curve uses its sweep's
  settings. A point flagged as degraded or unreliable stays flagged. Nothing is
  saved: reopening the screen recomputes the comparison.
- Each point has a health mark. A point is `unreliable` if every replicate
  stopped at the generation limit before its statistics settled, `degraded` if
  some did or if the interval is wider than the `precision` you asked for, and
  `healthy` otherwise. The mark is in the `health` column of the export and in
  `fim sweep report`. A point that is not healthy is flagged, never left out.
  In the app, the sweep results screen draws a `degraded` point as a hollow ring
  and an `unreliable` one as a ring with an X through it, on the chart and on
  the heat map, with a key on the chart. The table has a **health** column that
  says why, and pointing at a point or cell shows the same reason.
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
- In the desktop app a sweep is part of Configure. Tick **Sweep**, choose
  what varies in the **Set up sweep…** dialog (a live count and, on request,
  every point), and press **Run**. With **Sweep** enabled, Run executes the
  configuration over those ranges in the study chosen at the bottom of
  Configure. That study then holds the sweep; a study holds at most one.
- On Home, a study that holds a sweep has two more buttons beside **Continue
  sweep**. **Retry failed (N)** appears while N points have failed and runs
  only those points (`fim sweep resume --retry-failed`). **Recompute all…**
  asks you to confirm, then recomputes every finished point and compares each
  with its stored run (`fim sweep resume --rerun`). Both open the sweep's
  progress view.
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
both `fim` and `fim-gui`. Packaged executables open the GUI with no arguments:
double-click `fim-windows-x64.exe` or `fim.app`, or run
`fim-gui`/`fim --graphical` on Linux.

`n_replicates` selects a single run or batch; there is no separate batch-mode
control. The run view has initial, running, and completed states.
**Run simulation** starts from the current configuration, including when a
previous result is displayed. Cancellation or an error preserves the last
rendered view and adds a status banner.

The left navigation rail provides Home, Configure, Explore, Run, Results,
Compare, and Help throughout a run. Run and Results share the run view.
The parameter strip shows N/d/m/mu; selecting a value opens Configure.
Back and Forward traverse screen history, and Settings opens preferences.

The native File/Run/Help menus provide the same actions as on-screen
controls. File covers configuration and output files; Run starts and
cancels simulations. Help opens this guide, the
[configuration reference](configuration.md), and **Statistics reference**
([definitions and implementation links](statistics.md)) offline in the app.
It also provides the full documentation on GitHub, Check for updates, and
About.

| Screen/state | What it does | Same as |
|---|---|---|
| Home | Browse Experiments, Studies, runs, and batch replicates. Open results, clone configurations, and manage membership. Rows show summaries from `report.json`/`summary.json`; batch results are reconstructed from saved replicates. Default Study/Experiment groups are created when needed. A Study's pooled results can combine different parameters, identified by a “varies across members” note. The protected Examples experiment appears last. **Select** exposes deletion checkboxes; confirmation names runs affected by group deletion. Opening a trajectory supports retained-generation analysis and differentiation-`q` sweeps | [Re-analyze a trajectory](#re-analyze-a-trajectory), [Read-only items](#read-only-items) |
| Configure | Edit parameters and structure in independently scrolling panels with hover/focus help. Parameters include ploidy, `N` in individuals (scalar or per-deme), `d`, `m`, `mu`, and seed. Both YAML and the form use individuals; the engine converts to gene copies. Ploidy starts from Settings (initially diploid); “Ask me each time” requires selection before running. Structure covers initial conditions, sampling/mutation models, weighting, loci, watched statistics, and batch stopping. Settings supplies execution, convergence, storage, Expert, plot, and concurrency defaults; **Run settings...** edits the pending run. Load/save YAML, select an example, or open the File menu's preset picker (with YAML copying and duplication). **Run**/**Explore** use current inputs; invalid input returns here and marks the field | [Create a configuration](#create-a-configuration), [Loading settings](#loading-a-configuration-does-not-change-your-settings) |
| Run view — running | Follow live allele frequencies, statistic trajectories, progress, and cancellation. **Graphs** selects visible graphs; Settings controls columns. The statistics table is the trajectory legend and line-visibility control. Batch scatter plots pool advancing replicates. Deme selectors apply to subsequent updates. Cancellation or an error preserves the last view with a status banner | [Run a simulation](#run-a-simulation), [Batches](#batches-n_replicates-greater-than-one) |
| Run view — completed | Inspect reports, stop outcomes, scatter plots, and persisted histories. Scalar runs show within-run $\sigma$ bands and playback/scrubbing when multiple generations are retained. Batch results pool final-state scatter and list each replicate's status, final generation, and statistics, with intervals labeled “uncertainty across N independent replicates”. **Open** on a replicate displays scalar results; **Open output folder** reveals artifacts. Deme selectors (initially 1 versus 2) update scatter and pairwise statistics during execution, completion, and scrubbing. Frequency axes span 0–1. **Statistics shown**/**Choose…** controls visible statistics, not saved final reports; supplementary histories can add computation. Statistic names open their [reference sections](statistics.md) | [Output schemas](#output-schemas), [Supplementary histories](configuration.md#track_expensive_statistics) |
| Explore | Evaluate analytic predictions from `N` (individuals), `d`, `m`, and `mu`, converting by the form's ploidy. The table includes differentiation, diversity, entropy, effective allele counts, identity-recovery metrics, and mutation-negligibility information. Typical-deme entropy is approximate, especially at $d=2$. Committing a field updates predictions without simulation. Select a statistic row to chart it over any parameter range; charts group compatible units (proportions, nats, allele counts, generations). Parameter-independent predictions appear flat: analytic `D` is independent of `N`, and recovery metrics of mutation rate. The slider evaluates along the range without changing inputs. **Run this for real** copies inputs to Configure with a new Study selected | [Statistics reference](statistics.md), [Python statistics API](fim-simulator-functional-api.md#4-fimstatistics--differentiation-and-diversity-measures) |
| Compare | Select at least two completed runs. Scatter panels identify differing parameters, and a trajectory overlay shows one statistic with one color per run. No rerun is required | No direct CLI equivalent; compare exported trajectories and reports in an analysis tool |
| Help | Read this guide, the [configuration reference](configuration.md), and the [statistics reference](statistics.md) offline with cross-links. Other documents open on GitHub in the default browser. Available from the rail or Help menu; Back/Forward uses screen history | The same three Markdown guides are available in the repository |

**Trajectory references and averages.** Scalar trajectories include
analytic equilibrium lines for `D`, `G_ST`, and `E_ST`, and dash-dot
expected trajectories for `D`, `G_ST`, `H_S`, `H_T`, and `H_ST` where
available. Expected trajectories start from the seeded founding state and
support unequal deme sizes and explicit migration matrices up to 24 demes.
They are omitted for per-locus mutation rates, equilibrium-split founding,
and larger matrices. These are model expectations, not individual
stochastic paths; with few loci, the mean of a ratio can differ from a
ratio of expectations. See
[convergence estimates](configuration.md#convergence_estimate).

**Show** selects per-generation values, trailing means, or cumulative
means. **Band** selects ±1 or ±2 (default) standard errors for means;
the per-generation display instead shows standard deviations over the
evidence window. Reference curves are averaged over matching windows.
Cumulative averaging begins at the recorded averaging start, or after one
convergence window if that information is absent.
Solid colored lines with flags mark averaging-window starts; the windows
are shaded. Per-statistic colors distinguish different starts, and the
scrubber uses a dashed gray marker. Display choices are remembered and do
not alter computed or saved results.

Hover a statistic row for its window mean, standard error, generation
range, and standardized difference from the window-averaged prediction:

```text
mean 0.5506 ± 0.0048 over generations 1,399–174,966
(173,568 generations; 1 SE); predicted 0.5448, +1.2 SE
```

At the final generation, the displayed window is the monitor's stopping
window. It may be longer than the configured convergence window because
the monitor widens it to assess precision. “Not yet noise-adequate” marks
a mean that has not met that precision requirement.
The first-order autoregressive standard-error estimate accounts for
neighboring-generation correlation, but can underestimate uncertainty when
relaxation involves several time scales.

The row also shows $\Delta_p$ (observed minus predicted at the selected
generation) and trajectory mean squared error, for example
$\Delta D_p=+0.012$, $\mathrm{MSE}=4.1\times10^{-5}$.
Both update during execution and scrubbing.
The prediction uses the expected trajectory when supported, otherwise
the equilibrium line (including `E_ST`). The tooltip identifies which
reference applies. See the [statistics reference](statistics.md) for
definitions and API/source links.

A GUI-authored run with the same parameters and seed produces byte-identical
`report.json` and `trajectory.tlog` to the same configuration run from the
terminal — see [Reproduce a run](#reproduce-a-run). The GUI performs network
access only for the same explicit, opt-in release check the terminal's `fim
update --check` performs (Help menu → "Check for updates") — otherwise, like
the CLI, none at all.

On Linux, the GUI needs WebKitGTK, a system package most desktop
distributions already have installed — see
[installation alternatives](../install/README.md) if `fim-gui` reports it
is missing rather than opening a window.

A double-clicked or `fim --graphical`-launched GUI accepts no logging
flags. Set `FIM_LOG_LEVEL` and `FIM_LOG_OPTIONS` before launching;
they correspond to `-l` and `-L` below. See the
[operational logging design](fim-logging-design.md) §5.
The separate `fim-gui` entry point also accepts directory flags; see
[Where results, logs, and preferences are
written](#where-results-logs-and-preferences-are-written), below.

### Names, descriptions, and documentation

Hundreds of runs are hard to tell apart later, so every Experiment, Study,
and Run can say what it is and why it exists:

| | Name | Description | Documentation |
|---|---|---|---|
| Experiment | Required | One line, optional | Longer notes, optional |
| Study | Required | One line, optional | Longer notes, optional |
| Run | Optional (the folder name stands in) | One line, optional | — |

- **Description** is the short summary. Hover any Experiment, Study, or Run
  name (in Home, in the Run card's title, in Configure's study choice, and on
  a sweep's screen) to see it.
- **Documentation** is free-form text for the longer story: the question, the
  rationale, the setup, what was found.
- **To read or change them**, click the name, or the **ⓘ** button beside it,
  to open the details dialog. **Save** keeps the changes; **Cancel**, Escape,
  or a click outside the dialog discards them. Leaving a field empty clears
  it.
- **When creating**: Home's "Create experiment…" and Configure's
  "New study…" each take an optional description beside the
  name. Configure's **Run name** and **Run description** boxes name the next
  run; they empty once it starts. A sweep names its runs itself (see
  [Sweep a parameter](#sweep-a-parameter)), so the boxes are hidden while
  **Sweep** is ticked.
- **The Run card's title** names the Experiment first, then what is shown:
  `Ring vs island — Baseline (run-20261005-101500-000000)` for a finished
  run, `… — initial conditions (p₀)` before one, `… — in progress` while one
  runs. The Experiment is the one holding the study chosen in Configure, or
  the one holding the run once it finishes.

From the terminal, `fim study create` and `fim experiment create` take
`--description` and `--documentation`. A run gets its name and
description either from `fim run --name` and `--description`, or from the
`name` and `description` [run labels](configuration.md#run-labels) in its
configuration file (with an optional `class`); the options win over the
labels. A run's name, description, and class live in
`metadata.json`, beside `manifest.json`, which they never change, and are
never part of the run's ID: renaming a configuration does not make it a
different run. The labels are written only when the run has no
`metadata.json` yet, so they never overwrite a name you changed later.

### The Examples experiment

Home lists the **Examples** experiment after user-created experiments.
It holds the worked examples bundled with the app, one Study per
example class (a class inside another is named "Parent — Child"), each
holding that class's examples as runs with their saved results. The app
writes these when it starts, into an `examples` folder inside your results
folder, and writes them again if you remove them; a newer version of the
app replaces them with its bundled versions. An example whose result has not been saved
yet is not listed here, but is still in the Examples dialog, ready to load
into Configure.

### Read-only items

The Examples experiment, its Studies, and their runs are **read-only**. A
lock beside the name marks them in Home, in the details dialog, and in the
Run card's title. You can open, view, compare, and clone their run
configurations, but not
rename, describe, document, or delete them, or add or remove members:
those controls are unavailable, and hovering one says why. To work with
one:

- **A run:** click **Clone** on its Home row, or load its configuration
  from the Examples dialog. Configure receives an editable copy,
  with the example's name and description in the Run name and Run
  description boxes; running it creates an editable result.
Home's **Clone** action is available for regular runs too.
It fills the parameters, Run name, and Run description without changing
the source run or adding it to another Study. Modify the configuration
and run it to create a separate result. Configure's Study selector starts
at **Default study**, where runs go unless you choose another destination.
Its configuration buttons are ordered **Load configuration…**,
**Save configuration…**, then **Examples…**.

Home's Experiment, Study, and Run rows do not offer **Create study…**,
**Create run…**, **Open…**, **Delete runs…**, or **Copy** actions.
Expand a group to see its runs, double-click a run to open its saved
result, or use **Clone** to prepare an editable configuration. Details
buttons, selection controls, and sweep-specific actions remain available.

"Select all" never selects a
read-only item, and a deletion that names one is refused as a whole:
nothing at all is deleted. From the terminal, the same edits stop with an
error naming the read-only item. A configuration marks its run read-only
with [`_read_only`](configuration.md#_read_only); only the shipped examples
should use it.

An example linked into an editable Study is different: its checkbox
selects only that Study's link. **Delete selected** removes the link,
not the example or its saved results. The confirmation states that the
example is kept. Its row in the Examples experiment remains protected,
and links in other Studies are unchanged.

### Opening a saved example

Open an example from Home (double-click its row) or with **Open saved
result** in the Examples dialog. It includes the complete finished run:
statistics, messages, scatter plot, statistic trajectories, other graphs,
and a scrubber for reviewing earlier generations. No simulation is needed.
Large examples may take longer to open while their saved data is prepared.

Every result is retained. To keep the download manageable, trajectory
files are stored losslessly compressed; the app reconstructs them
automatically. You do not need to unpack files or manage archive parts.

Use **Load into Configure** to run an editable copy without replacing the
read-only example. Batch examples include every
kept replicate's full results and the shared batch summary.

### Closing the window

When you close the window, `fim` asks first if closing would lose something.
This happens when:

- The Configure form has changes you have not saved.
- A saved run is in progress.
- A run, batch, or sweep that you started with **Run simulation** or
  **Sweep** is in progress.

The dialog tells you what is at stake and offers these choices:

| Button | What it does |
|---|---|
| **Save and close** | Keeps the configuration under **Saved runs** on Home, then closes. A configuration that is not complete yet is saved as a Draft. A configuration that is ready to run is saved as Planned. |
| **Close without saving** | Closes and drops the unsaved changes. |
| **Pause and close** | Shown when only a saved run is in progress. The run stops at a checkpoint, and you resume it from **Saved runs** on Home. |
| **Stop and close** | Shown when only a run or sweep that cannot be resumed is in progress. Closing stops it. |
| **Keep working** | Leaves the window open. |

Choosing **Save and close** or **Close without saving** also pauses a running
saved run, or stops any other running work. If the save fails, the dialog
stays open and shows the reason, so nothing is lost.

If nothing would be lost, the window closes at once.

### If the window closes but `fim` keeps running

If shutdown has not completed 20 seconds after the window closes,
`fim` records diagnostics and forces an exit with status `3`:

```text
fim: shutdown did not complete within 20s; forcing exit
```

Completed results remain on disk. A saved run resumes from its latest
committed checkpoint; work since that checkpoint may need to be repeated.
Unsaved runs stopped before publication do not produce a completed result.

A diagnostic report is written at the same time, to `logs/fim.log` as well
as to the terminal, including when launched without a terminal.
Report the section beginning `shutdown deadman fired`, together with
your platform and launch method. See
[known issues](../ISSUES.md#intermittent-hang-during-gui-shutdown).

For shutdown investigation, set `FIM_GUI_SHUTDOWN_TIMEOUT` to a longer
timeout in seconds, or `0` to disable forced exit:

```console
FIM_GUI_SHUTDOWN_TIMEOUT=0 fim --graphical
```

### Loading a configuration does not change your Settings

The run settings — execution engine, n_replicates,
max_generations, convergence burn-in, precision, confidence,
JIT, the `auto` engine's two thresholds, and max concurrent replicates —
have defaults in Settings. **Run settings...** on Configure edits their
effective values for the pending configuration only. A configuration you
load can supply values for them. This applies to:

- **Examples…** → **Load into Configure**, and **Run it** on a saved example
- File menu → **Load example…** (built-in or user-saved presets)
- **Load configuration…** (a YAML file)
- **Clone** on a run listed on Home

The loaded values are used **for this run only**. Your Settings stay
unchanged, so **New configuration** starts from saved defaults.
A setting omitted from the loaded file takes the
simulator default, as in `fim run` for that
file.

When any loaded run setting differs from your Settings, a notice appears
on Configure and on the Run card before you run. It lists each setting
that differs, with this run's value and your Settings value side by side.
For example, if your Settings use the `auto` engine with 200 replicates
and you load an example that uses the `lineal` engine with 1 replicate,
the notice lists both settings.

- **Make these my Settings** saves the loaded values as your Settings, so
  new configurations use them too. Max workers is not changed: it
  describes your computer, and no configuration names it.
- **Edit this run...** opens the per-run editor with the effective values.
  **Apply to this run** validates and applies the edits without changing
  saved defaults. Closing without applying discards edits.
- In the editor, **Use saved Settings** fills the controls from your saved
  defaults; **Restore loaded settings** restores the originally loaded
  values, even after edits. Neither action applies until you choose
  **Apply to this run**.
- **Dismiss** hides the notice. The run still uses the loaded values. A
  "run settings" item in the parameter strip at the top of the window
  stays while they differ; click it to see the notice again.

If you change Settings after loading, the notice is updated to compare
against the new Settings; the loaded configuration retains its
values. To use your Settings for the run, choose **Run settings...** →
**Use saved Settings** → **Apply to this run**. This copies a snapshot:
later edits to saved Settings do not silently change it. The editor is
available even when no values differ. File menu → **New configuration**
and Explore's **Run this for real** start from your Settings.

The editor validates settings against the current model. For example,
`max_generations: auto` cannot be derived for a model with both migration
and mutation zero: supply an explicit cap or change the model. The editor
reports the error and keeps the pending configuration unchanged.

When the app restores the last submitted configuration at launch, the run
settings come from your Settings, not from that configuration.

### Saved preferences

The GUI saves display preferences, startup behavior, execution defaults,
and the last submitted configuration between launches:

- Significant digits and **Statistics shown**. Showing $E_{ST}$,
  $K_{ST}$, $A_{\mathrm{CGD}}$, $\delta_G$, or I enables computation
  of all five each generation; see
  [`track_expensive_statistics`](configuration.md#track_expensive_statistics).
- The pairwise-output deme limit and light/dark choice. An absent or
  `null` theme follows the operating system.
- Whether the welcome panel has been dismissed.
- Startup configuration policy and execution/convergence defaults.
- The configuration last submitted with **Run simulation**.

**Restore the last submitted configuration** opens that configuration at
launch; **Restart from the starter configuration** uses built-in model
values. Both apply saved execution defaults. The first-launch welcome
panel offers **Examples…** or **Start from scratch**.
File → **New configuration** uses starter model values and saved
execution defaults, ignoring the last submitted configuration.
Loading a file, preset, worked example, or saved run does not change saved
defaults; see
[Loading a configuration does not change your Settings](#loading-a-configuration-does-not-change-your-settings).

Preferences supply defaults and interface state; they are not the record
of a completed run. Its configuration, seed, and results remain in
`manifest.json`, `trajectory.tlog`, and related artifacts under `results/`.

It lives in the platform's normal per-user settings location — you do not
need to find or edit it for ordinary use:

| Platform | Location |
|---|---|
| macOS | `~/Library/Application Support/fim/preferences.json` |
| Windows | `%APPDATA%\fim\preferences.json` |
| Linux | `$XDG_CONFIG_HOME/fim/preferences.json` (usually `~/.config/fim/preferences.json`) |

If preferences contain invalid JSON or an unsupported format, the next
launch shows a dismissible error banner and uses built-in defaults.
The invalid file is retained as `preferences.invalid-<timestamp>.json`
beside a newly written preferences file. Removing the preferences folder
resets saved settings without changing `results/`.

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
results — see the in-app Help screen.

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
  $\delta_G$, and Sherwin mutual information `MI`;
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
Gregorius $\delta_G$, `MI`, $G_s$, $G_d$, the pooled-heterozygosity
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
