# Using `fim`

This guide covers every `fim` command and output, not what the simulator
models or why — see [what this simulates](../README.md#what-this-simulates)
for that first. For parameter types and defaults, use the
[configuration reference](configuration.md). Return to the
[project overview](../README.md) for installation and documentation links.

## Contents

- [Create a configuration](#create-a-configuration)
- [Run a simulation](#run-a-simulation)
- [Why does my run take so long?](#why-does-my-run-take-so-long)
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

A simulated population does not settle at once. With little migration between
islands, gene flow and mutation take tens of thousands of generations to shape
what you finally see, so a run is only finished once its statistics have stayed
steady for about that long. You do not choose that stretch: `convergence_window`
and `max_generations` default to `auto`, derived from your migration, mutation
and population sizes.

`fim run` prints the derived values unless `--quiet`, and the desktop app shows
them next to **Run simulation** before you start. For five islands of 100 gene
copies with `m: 0.0001` and `mu: 0.000001` that is a window of about 59,000
generations. A run that finishes in a hundred generations is a warning sign,
not good news. The derivation and its evidence are in
[Convergence defaults](convergence.md); the settings are in
[configuration.md](configuration.md#convergence_window).

### Batches (n<sub>replicates</sub> greater than one)

A config's n<sub>replicates</sub> (see [configuration.md](configuration.md#nreplicates))
controls whether one run or a whole batch executes:

- **n<sub>replicates</sub>: 1**: the four-file scalar-run contract below,
  directly in the output directory. Set this explicitly for a single,
  ordinary run with no batching at all.
- **n<sub>replicates</sub> greater than one (the default: `200`, with
  [replicate_tolerance](configuration.md#replicate_tolerance)'s own default
  of `0.01` usually stopping well short of it)**: each replicate gets its
  own `replicate-NNN/` subdirectory, keeping that same four-file contract,
  plus a batch-level `manifest.json` and `summary.json` — see
  [Output schemas](#output-schemas).

An unconfigured run — no `n_replicates` key at all — is therefore a batch,
not a single scalar run: this is a deliberate default (an unattended run
reports a real confidence interval instead of a single, uncertainty-free
point estimate) but it means every config below that wants the plain
scalar behavior this guide describes states `n_replicates: 1` explicitly,
the same way [`fim init`](#create-a-configuration)'s own starter
configuration does.

A batch runs under any [engine_backend](configuration.md#engine_backend),
not only `lineal`; every backend writes the same `replicate-NNN/` +
`manifest.json` + `summary.json` layout. Whether a replicate's own
trajectory is bit-identical across backends for the same seed follows
`engine_backend`'s own documented parity rules (bit-for-bit with no
migration; statistically equivalent otherwise for
`generational-vector`) — running the same batch under a different
backend is not a way to reproduce one replicate's own exact trajectory
a different backend already produced, only its statistical behavior.

Under `engine_backend: lineal` (the only backend with a worker-process
pool), batch replicates run in parallel by default, one worker per
processor. `--workers N` sets an explicit worker count; `--sequential`
runs replicates one at a time — the worker count affects only how long the
batch takes, never its result. Both flags are rejected with a
usage error under any other `engine_backend`, which has no process pool
for them to mean anything about; use
[max_concurrent_replicates](configuration.md#max_concurrent_replicates)
instead (also settable for this run only via `--max-concurrent-replicates
N`, without editing the config file), the `generational`/
`generational-vector` path's own concurrency/memory-bounding control.

With replicate_tolerance unset in the config, exactly n<sub>replicates</sub>
replicates run. With it set, the batch can stop earlier, once every watched
statistic's across-replicate confidence interval has tightened enough (see
[configuration.md](configuration.md#replicate_tolerance)) — the number of
`replicate-NNN/` subdirectories written can then be less than n<sub>replicates</sub>.

## Worked examples

Each example below is a complete config and the command that runs it: save
the YAML, run the command, and the reported values match those shown here,
because the same seed, parameters, and version always give the same
`report.json` (see [Reproduce a run](#reproduce-a-run)). Each example uses
a small `N` and `d`, and lets [convergence_window and
max_generations](convergence.md) be derived from the model (the default), so
each run goes on until its statistic has stopped trending *and* its
trailing-window mean is known to half of convergence_tolerance. With one
locus that second condition can take tens of thousands of generations, so
several examples set a looser convergence_tolerance (0.02 to 0.05) or pool
eight loci, and each one's README says what that costs. Every example
here finishes in about a second to about a minute of wall-clock time on
ordinary development hardware. Most use one locus, so a single run's
numbers scatter widely around the model's expectation; use a batch
(`n_replicates`) when you want a stable value. Each uses a
seed distinct from [`fim init`](#create-a-configuration)'s starter config.
Each configuration starts with its labels (`name`, `description`,
`class`) and `_read_only: true`, which marks the copy shipped with the app
as read-only; delete that line in your own copy (see
[Run labels](configuration.md#run-labels)).
Each demonstrates one option, or one natural pair of options, from the
[configuration reference](configuration.md); a real study combines them
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
shows the example's own result, as shipped with the app, without running
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
engine_backend: lineal
convergence_statistic: D
n_replicates: 1   # a single scalar run; the default (200) would batch
```

```console
fim run hub-island.yaml --output results/hub-island --quiet
```

Converges at generation 1,136, after about 10 seconds, with D = 0.0498 and a
trailing-window mean D of 0.0540 ± 0.0016. The example pools eight loci: one
locus alone swings too widely for its D to settle honestly (see the
[example's README](examples/unequal-island-sizes-with-a-migration-hub/README.md)).
`manifest.json`'s `parameters.N`
and `parameters.m` record the exact per-deme sizes and matrix rows used —
compare them against a run with one shared `N/m` to see the effect of
unequal size and asymmetric connectivity on differentiation.

deme_weighting only affects E<sub>ST</sub> — D and K<sub>ST</sub> weight demes equally by
definition, regardless of this setting. With the unequal per-deme `N` above,
the default `equal` weighting gives E<sub>ST</sub> = 0.0676; adding
`deme_weighting: size` to the same configuration gives E<sub>ST</sub> = 0.0618
instead — deme 4's own 800-gene-copy weight pulls the size-weighted value
down, since it is both the largest deme and the best-connected one.

### Stepping-stone (spatial) migration

Six demes arranged on a ring, each migrating only with its two neighbors —
`fim.model.topology`'s compact sugar for a sparse migration matrix, instead
of hand-writing all 36 matrix entries:

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
engine_backend: lineal
convergence_statistic: D
convergence_tolerance: 0.02   # looser than the 0.01 default: about a minute
n_replicates: 1   # a single scalar run; the default (200) would batch
```

```console
fim run stepping-stone.yaml --output results/stepping-stone --quiet
```

Converges at generation 7,918, after under a minute, with a trailing-window
mean D of 0.133 ± 0.007. `convergence_tolerance: 0.02` keeps the run to a
minute; at the default 0.01 one locus needs about 32,000 generations (see
the [example's README](examples/stepping-stone-spatial-migration/README.md)).
Swap `topology: ring` for
`linear` to remove the wrap-around edge between deme 1 and deme 6. In
the GUI, the completed-run Literature visualizations panel includes an
isolation-by-distance plot whenever the migration graph has at least two
distance classes.

### Literature distance statistics from an explicit founder split

Three demes start fixed for three different alleles. This is a deliberately
small, deterministic demonstration of the supplemental statistics added from
the differentiation literature: Caballero-García-Dorado allelic distance
A<sub>CGD</sub>, Gregorius δ, and Sherwin mutual information `MI`.
track_expensive_statistics turns on their own per-generation tracking (D,
G<sub>ST</sub>, H<sub>S</sub>, and H<sub>T</sub> are always tracked for free;
this opts the remaining two — E<sub>ST</sub> and K<sub>ST</sub> — and the
three literature statistics above into the same treatment), so a run that
only converges on `D` still records all six for display — this example's
own final-report values are identical either way, since a report always
computes every statistic at the converged generation regardless of this
setting; the flag only changes what is available generation by generation
before that point. In the desktop app the key follows Settings' "Statistics
shown" instead: show A<sub>CGD</sub>, δ<sub>G</sub> or I there (they start
hidden, since computing them every generation makes runs take longer) to
follow them on the trajectory.

<!-- worked-example-config: examples/literature-distance-statistics-from-an-explicit-founder-split/config.yaml -->
```yaml
name: Distance statistics from a founder split
description: >-
  Three islands fixed for different alleles: a one-generation check of the
  literature statistics.
class: statistics-and-convergence
_read_only: true  # marks the shipped copy read-only; delete it in your own copy
N: 200
ploidy: haploid
d: 3
m: 0.0
mu: 0.0
seed: 20260914
loci:
  - locus_id: 1
    length: 100
p_0:
  - - 0: 1.0
  - - 1: 1.0
  - - 2: 1.0
engine_backend: lineal
convergence_statistic: D
convergence_window: 2
convergence_tolerance: 0.000001
max_generations: 1
track_expensive_statistics: true
n_replicates: 1   # a single scalar run; the default (200) would batch
```

```console
fim run literature-distance-statistics.yaml \
  --output results/literature-distance-statistics --quiet
```

Converges at generation 1 with D = G<sub>ST</sub> = E<sub>ST</sub> =
K<sub>ST</sub> = 1, A<sub>CGD</sub> = 1, Gregorius δ = 1, and
`MI = log(3) ≈ 1.099`. The equal values are not a claim that these
statistics are interchangeable; they are the easiest possible sanity check
for a complete three-way split. Change one deme's `p_0` cell to
`0:0.5,1:0.5` to see the distance-oriented statistics respond directly to
shared alleles.

### Equilibrium-split founding

The example above hand-picks generation-0 frequencies directly. This one
derives them instead: your demes are founded from a single shared ancestral
population rather than an independent Dirichlet draw per deme — the
ancestral population simulates alone until it reaches mutation-drift
equilibrium, then splits into your demes by sampling without replacement,
so the demes
already differ a little at generation 0 purely from which copies each one
happened to receive, a genuine founder effect rather than an assumption:

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
# Eight independent loci, pooled, and a looser tolerance for the main run:
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
engine_backend: lineal
convergence_statistic: D
convergence_tolerance: 0.03
n_replicates: 1   # a single scalar run; the default (200) would batch
```

```console
fim run equilibrium-split.yaml --output results/equilibrium-split --quiet
```

The ancestral phase runs 1,256 generations; the main run then converges at
generation 4,238, after about 35 seconds, with a trailing-window mean D of
0.279 ± 0.014 — real differentiation that grew from the ancestral-population
founder effect, with no explicit `p_0` anywhere in the file. The example pools
eight loci and sets `convergence_tolerance: 0.03`, because one locus here
needs about 30,000 generations to settle (see the
[example's README](examples/equilibrium-split-founding/README.md)). How long the ancestral phase runs is worked out from
the model: about ln(1 / equilibrium_convergence_tolerance) times the time the
ancestral population takes to forget its starting state, 1/(2μ + 1/N)
generations for its N gene copies. Loosen the tolerance for a shorter
ancestral phase (founded from a population a little further from
equilibrium), or tighten it for a longer one; either way, a phase needing
more than equilibrium_max_generations is a hard error, reported before the
phase starts, not an ordinary result. The ancestral phase is saved, every
generation of it, in
[`equilibrium_trajectory.jsonl`](#equilibrium_trajectoryjsonl) beside the
run's `trajectory.jsonl` — see
[equilibrium_convergence_window, equilibrium_convergence_tolerance,
equilibrium_max_generations](configuration.md#equilibrium_convergence_window-equilibrium_convergence_tolerance-equilibrium_max_generations).

### Stochastic migrant counts

By default, migration blends each deme's frequencies with an exact
`rate * N` fraction of its neighbors' — a deterministic step given that
generation's frequencies. migrant_sampling: stochastic instead draws the
migrant *count* from `Binomial(N, rate)`, adding a genuine, explicit source
of randomness some studies want counted:

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
engine_backend: lineal
convergence_statistic: D
n_replicates: 1   # a single scalar run; the default (200) would batch
```

```console
fim run stochastic-migrants.yaml --output results/stochastic-migrants --quiet
```

Converges at generation 11,802, after about a minute, with D = 0.032 and a
trailing-window mean D of 0.076 ± 0.005 (the model's expectation is 0.059; one
locus swings slowly, so a single run's window mean can sit this far from it,
more than its standard error suggests). Re-run with migrant_sampling
removed (or set to `continuous`, the default) at the same seed to compare
against the deterministic-migration baseline directly.

### Finite-length alleles (the K-allele model)

By default (mutation_model: infinite_alleles), every mutation receives a
globally unique identity — the standard population-genetics idealization
for a locus long enough that two independent mutations essentially never
land on the same state. finite_alleles instead bounds a locus to
4<sup>length</sup> states and lets a mutation recur to one already present
elsewhere in the run — deliberately exercised here with a very short
3-base locus (only 4<sup>3</sup> = 64 states) and a high `mu` so recurrence is
actually likely within the run, not just theoretically possible:

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
engine_backend: lineal
convergence_statistic: D
convergence_tolerance: 0.02   # looser than the 0.01 default: under a minute
n_replicates: 1   # a single scalar run; the default (200) would batch
```

```console
fim run finite-alleles.yaml --output results/finite-alleles --quiet
```

Converges at generation 4,521, after about 20 seconds, with a trailing-window
mean D of 0.609 ± 0.010 (`convergence_tolerance: 0.02` halves the run's
precision target to keep it short; see the
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
fixed moves G<sub>ST</sub> toward the infinite-island approximation.

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
engine_backend: lineal
convergence_statistic: G_ST
convergence_tolerance: 0.02   # looser than the 0.01 default: under a minute
n_replicates: 1   # a single scalar run; the default (200) would batch
```

```console
fim run finite-deme-correction.yaml \
  --output results/finite-deme-correction --quiet
```

Converges (on G<sub>ST</sub>) at generation 10,109, after about two minutes, with
a trailing-window mean G<sub>ST</sub> of 0.200 ± 0.007
(`convergence_tolerance: 0.02` keeps the run short; see the
[example's README](examples/wright-takahata-finite-deme-correction/README.md)).
The closed-form finite-deme prediction for these parameters
is about 0.141, below the corresponding infinite-island approximation of
about 0.238 because the `d / (d - 1)` correction is retained. A single locus
scatters widely around that expectation (the exact expectation for this
model is G<sub>ST</sub> 0.195 and D 0.319); a batch averages it out.

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
engine_backend: lineal
convergence_statistic: D
convergence_tolerance: 0.05   # looser than the 0.01 default: seconds, not hours
n_replicates: 1   # a single scalar run; the default (200) would batch
```

```console
fim run kimura-weiss-isolation-by-distance.yaml \
  --output results/kimura-weiss-isolation-by-distance --quiet
```

Converges at generation 2,539, after about a minute, with D = 0.475 and
G<sub>ST</sub> = 0.093. `convergence_tolerance: 0.05` is deliberately loose:
at the default 0.01 this one-locus ring needs about 175,000 generations,
well over an hour (see the
[example's README](examples/kimura-weiss-isolation-by-distance/README.md)).
Open the result in `fim-gui` and inspect the Literature visualizations panel:
the identity-decay plot groups deme pairs by shortest-path distance over the
non-zero migration edges and overlays a log-linear fit.

### Per-base mutation rate across unequal locus lengths

μ<sub>b</sub> (mutually exclusive with `mu`) is a single per-base-pair mutation
probability; each locus derives its own `mu` from μ<sub>b</sub> and its own
`length` via mu = 1 - (1 - μ<sub>b</sub>)<sup>length</sup> — so two loci of very
different lengths do not silently mutate at the same rate:

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
engine_backend: lineal
convergence_statistic: D
convergence_tolerance: 0.03   # looser than the 0.01 default: under a minute
n_replicates: 1   # a single scalar run; the default (200) would batch
```

```console
fim run mu-b.yaml --output results/mu-b --quiet
```

Converges at generation 7,481, after about 30 seconds, with a trailing-window
mean D of 0.220 ± 0.011 (`convergence_tolerance: 0.03` keeps the run short;
see the
[example's README](examples/per-base-mutation-rate-across-unequal-locus-lengths/README.md)).
`results/mu-b/manifest.json`'s
`parameters.mu` records the two derived rates — `0.0009995` for the
50-base locus and `0.0099503` for the 500-base one — the expanded,
canonical form μ<sub>b</sub> is sugar for; μ<sub>b</sub> itself is never stored.

### Several convergence statistics

Watch more than one statistic and decide whether stopping needs every one
of them stable (convergence_combinator: all, the default) or just one
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
engine_backend: lineal
convergence_statistic: [D, G_ST]
convergence_combinator: any
n_replicates: 1   # a single scalar run; the default (200) would batch
```

```console
fim run multi-statistic.yaml --output results/multi-statistic --quiet
```

Converges at generation 3,033, after about 10 seconds. `report.json`'s
converged_on lists the watched statistics that had actually settled when the
run stopped: here ["G<sub>ST</sub>"] alone, because G<sub>ST</sub>'s
trailing-window mean was already known precisely (0.0636 ± 0.0042) while D's
was not (0.097 ± 0.025), and `any` needs only one. A run that reaches its cap
records `null`, since it converged on nothing.

### Within-run sigma band

Once a run genuinely converges, keep going for a further window and report
how much the watched statistic still wobbles, generation to generation,
immediately after being declared stable — a different question from the
across-replicate confidence interval in the next example, which asks how
much independent replicates disagree with each other, not how noisy any one
of them still is:

<!-- worked-example-config: examples/within-run-sigma-band/config.yaml -->
```yaml
name: Within-run sigma band
description: >-
  Keeps running after convergence to show how much D still varies from one
  generation to the next.
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
engine_backend: lineal
convergence_statistic: D
sigma_band_multiplier: 2.0
sigma_band_window: 30
n_replicates: 1   # a single scalar run; the default (200) would batch
```

```console
fim run sigma-band.yaml --output results/sigma-band --quiet
```

Converges at generation 13,909, after about three minutes, with D = 0.163; the
following 30-generation extension reports D = 0.157 ± 0.063 (mean ± 2σ).
The example pools eight loci: with one locus, a stretch near fixation can
look steady for the wrong reason (see the
[example's README](examples/within-run-sigma-band/README.md)). The band's own mean
differs from the converged value itself — it is computed over the
*extension* window, not the generations that triggered convergence — and
is an honest report of how much a single statistic can still wobble over
just 30 generations, not a sign of anything wrong. `results/sigma-band/sigma_band_trajectory.jsonl` records
one `D` value per extension generation; `manifest.json`'s own `sigma_band`
field holds the summarized band shown here.

### An adaptive replicate batch with a confidence interval

Rather than guessing how many replicate runs a confidence interval needs,
set n<sub>replicates</sub> well above the plausible requirement and let
[replicate_tolerance](configuration.md#replicate_tolerance) decide when
enough have run:

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
# Eight independent loci, pooled, and a loose per-replicate tolerance: each
# replicate settles in about a thousand generations instead of about 15,000
# (see the README).
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
engine_backend: lineal
convergence_statistic: D
convergence_tolerance: 0.05
n_replicates: 50
replicate_minimum: 10
replicate_tolerance: 0.03
```

```console
fim run adaptive-batch.yaml --output results/adaptive-batch --sequential --quiet
```

Stops at 10 replicates, the required minimum — `D`'s 95% confidence interval
is already `0.287 +/- 0.026`, inside the requested `0.03` half-width, so the
remaining 40 possible replicates were never needed. (A smaller
`replicate_tolerance` makes the batch run past the minimum.) Each replicate pools
eight loci with `convergence_tolerance: 0.05`, so it settles in about a
thousand generations; one-locus replicates at the default tolerance would
take hours for the whole batch (see the
[example's README](examples/an-adaptive-replicate-batch-with-a-confidence-interval/README.md)).
`results/adaptive-batch/summary.json` reports every statistic's own
interval; `results/adaptive-batch/replicate-001/` through
`replicate-010/` each hold the ordinary scalar-run files for
that one replicate. Drop `--sequential` to run the same batch across a
worker process per CPU instead — the computed numbers are identical
either way (see [Batches](#batches-nreplicates-greater-than-one)); only
the wall-clock time differs.

### A large-`d` batch under generational-vector

Every example above names `engine_backend: lineal`, the single-threaded
reference implementation and `fim run`'s own default. The configuration
states it explicitly because the desktop app's own starting value for a
fresh form is `auto`, and `auto` never resolves to `lineal`: it always picks
`generational` or `generational-vector` (see
[engine_backend](configuration.md#engine_backend)). This
example and the next are the deliberate exception: each names a different
engine backend and runs a real, moderately long batch (a few seconds, not
instant) large enough for that backend's own advantage to actually show.
They are also the exception to the derived convergence defaults: each pins a
fixed amount of work, 16 replicates of exactly 100 generations
(`max_generations: 100` with a `convergence_window: 101` that can never
fill, and `replicate_tolerance: null` so no replicate is skipped), so a
timing comparison always does the same work. The population is nowhere
near equilibrium when it stops, so read these two as timing workloads, not
as results.
`generational-vector` keeps a dense, array-native representation of every
active replicate at once, and is the fastest measured choice once `d`
grows large enough — see [choosing an engine
backend](fim-simulator-design.md#46-choosing-an-engine-backend) for the
full measured comparison this example's own shape is drawn from:

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
# A fixed 100-generation horizon: a 101-generation window can never fill
# within 100 generations, so every replicate runs exactly to the cap.
convergence_window: 101
convergence_tolerance: 0.02
max_generations: 100
n_replicates: 16
replicate_tolerance: null   # always run all 16 replicates
```

```console
fim run vector-showcase.yaml --output results/vector-showcase --quiet
```

Ran in about 20 seconds on ordinary development hardware, all 16 replicates
to the 100-generation cap, with `D`'s 95% confidence interval at
`0.0264 +/- 0.0021`. In the desktop
app, loading this example runs it with its own `generational-vector`
engine and 16 replicates, whatever your Settings hold, and leaves your
Settings unchanged; a notice lists the differences and offers to make
them your Settings (see
[Loading a configuration does not change your Settings](#loading-a-configuration-does-not-change-your-settings)).

### A long-locus batch under the generational engine

The same batch shape as the example above, but favoring locus length over
deme count instead — `d: 35` with a 7-base locus (16384 possible allele
states under `finite_alleles`) rather than `d: 70` with a 5-base one —
and the plain `generational` engine, which this project's own measured
benchmarks find winning over `generational-vector` in exactly this kind of
region (moderate `d`, a longer locus):

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
# A fixed 100-generation horizon: a 101-generation window can never fill
# within 100 generations, so every replicate runs exactly to the cap.
convergence_window: 101
convergence_tolerance: 0.02
max_generations: 100
n_replicates: 16
replicate_tolerance: null   # always run all 16 replicates
```

```console
fim run generational-showcase.yaml --output results/generational-showcase --quiet
```

Ran in about 25 seconds on the same hardware, with `D`'s 95% confidence
interval at `0.0237 +/- 0.0020`. That is a little slower than the
`generational-vector` example above, although an earlier measurement on a
less loaded machine found it a little faster: neither backend is
universally faster, and which one wins depends on where a configuration
sits on the `d`/locus-length grid and on the machine itself. In the desktop
app, loading this example runs it with its own `generational` engine, the
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

By default every point gets its own seed (`base seed + point index times
n_replicates`), so no two points share a replicate seed. Set
`seed_policy: same` in the `sweep:` block to give every point the base seed.

```bash
fim sweep plan sweep.yaml            # list the points and their validity; runs nothing
fim sweep run sweep.yaml             # create the study and run every point
fim sweep resume STUDY_ID            # run only the points still missing
fim sweep resume STUDY_ID --retry-failed
fim sweep report STUDY_ID --statistic D --csv
fim sweep run sweep.yaml --points-at-once 4 --workers 2   # or --sequential
```

- `plan` checks every point first. A combination that cannot run (a torus
  whose `rows * columns` differs from `d`) is listed with its reason.
- A sweep of 100 points or more asks for `--yes`.
- **Points run at the same time, by default as many as fill the machine, each
  in its own process.** A single run, or a batch on any engine but `lineal`
  (including `auto`), uses about one core; a `lineal` batch of `r` replicates
  uses about `r`. So a sweep of single runs or generational batches runs up to
  one point per core, a sweep of small lineal batches a few at once, and a sweep
  of large lineal batches one point at a time.
  `--points-at-once N` sets the number, `--workers N` the worker processes each
  batch point uses, and `--sequential` runs one point at a time with one
  worker. In the app, the Sweep dialog's **Points at once** does the same, and
  the Settings value "max workers" applies. Results do not depend on the
  setting: each point is fixed by its own configuration and seed.
- A point's run id is a hash of its configuration, so resuming, or running a
  second sweep that overlaps the first, reuses the runs that already exist
  instead of computing them again. Reuse requires identical configurations,
  including the seed, so an overlapping sweep must use the same base seed and
  the same axis order, and a run made by the same software version. A point
  whose only run came from another version is recomputed and compared with it
  (see [Reproduce a run](#reproduce-a-run)).
- In the desktop app a sweep is part of Configure. Tick **Sweep**, choose
  what varies in the **Set up sweep…** dialog (a live count and, on request,
  every point), and press **Run** as always: with **Sweep** on, Run runs the
  configuration over those ranges into the study chosen at the bottom of
  Configure. That study then holds the sweep; a study holds at most one.
- A failed point is recorded and the sweep continues. Press Ctrl+C to stop;
  finished points stay in the study.
- Every point keeps its full trajectories. A sweep multiplies disk use by its
  number of points.
- Each point's run is named after the study and its own values, for example
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
fim stats run/trajectory.jsonl --q 0 --q 1 --q 2
```

JSON is printed to standard output. `--output` also writes the same result.
`--q 0` and `--q 2` always match the report's own K<sub>ST</sub> and `D`; `--q 1`
matches E<sub>ST</sub>, including the run's own deme_weighting setting — a
size-weighted E<sub>ST</sub> and an equal-weighted Differentiation_1 would
otherwise silently disagree on the same trajectory.

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

A desktop application — three screens over a small, static local web page
(`fim`'s own bundled `webview` renderer, the OS's native web view; no
browser, no server beyond the one opt-in release check below) — that runs
the same simulations and reads the same `results/` folder as the commands
above. Install it alongside `fim` (`python -m pip install .` already
provides both console scripts), or launch it from a packaged executable by
opening it with no arguments — for example by double-clicking
`fim-windows-x64.exe`/`fim.app`, or running `fim-gui`/`fim --graphical` from
a Linux install. n<sub>replicates</sub> in the configuration is the only thing that
decides whether a run goes through the scalar or the batch path — there is
no separate "batch mode" toggle. Every screen calls the identical underlying
function this guide already documents; nothing here is a second
implementation.

The run view — where a configuration is built, a run proceeds, and its
result is shown — is one screen in exactly one of three states at a time,
not three separate screens reached by navigating away from each other:
building/editing a configuration ("initial"), a run in progress
("running"), and a finished run's own summary ("completed"). "Run
simulation" moves from initial to running from wherever it is clicked,
including straight from a previous run's own completed view — the same
button starts the next run, reusing whatever the form already holds, with
no separate "New run" step. Cancelling a run, or a run ending in an error,
freezes the view exactly as it last rendered, with a banner on top,
rather than switching anywhere else:

A persistent rail (Home, Configure, Explore, Run, Results, Compare, and —
set apart at the bottom — Help) is always visible along the window's own
left edge, current destination highlighted, reachable from any screen
including mid-run; Run and Results both point at the same run view
described in the table below (a run in progress shows through either one,
a finished run's own summary too — splitting them into two genuinely
distinct layouts is still on this project's own GUI roadmap). A read-only
parameter strip beneath the title bar always shows the current N/d/m/mu;
clicking any of the four jumps straight to Configure. The same strip also
has Back, Forward, and Settings controls at its right edge. Back and Forward
walk through the screen history like a web browser; on a fresh startup there
is no previous screen, so Back is disabled. A native File/Run/Help menu bar
duplicates the everyday actions a mouse-and-rail user already has, for
keyboard-shortcut users: File covers configuration/run file-system actions
(New/Open/Save configuration, Open run…, Reveal output folder, Quit); Run
covers the simulation lifecycle (Run simulation, Cancel run); Help covers
this guide and the [configuration reference](configuration.md) (rendered
in-app — see the Help screen row below), a link to the full documentation on
GitHub, Check for updates, and About. Every menu item reuses the exact same
action the matching on-screen control already performs.

| Screen/state | What it does | Same as |
|---|---|---|
| Home | An Experiment/Study/Run tree: every Experiment expands to its own Studies, each expanding to its own Runs, each row carrying a config-summary and a final-statistics/outcome column read from that run's own `report.json`/`summary.json` — a batch row's outcome is its own confidence interval, and is expandable to its individual replicates, each independently reachable for re-analysis. Selecting a scalar or batch row and clicking "Open" (or double-clicking it directly) opens the identical Results card either way — a batch's own pooled statistics, table, and scatter, rebuilt fresh from its own persisted replicates, not only what a live batch's own completion shows. A Study row's own "Open…" goes one level up: every member run, and every replicate of every member batch, pooled together the same way — a mismatched parameter across members (say, two different `d` values) is never refused, only named in a "varies across members" note, since intentionally pooling runs in the same parameter neighborhood is a legitimate choice a botanist is free to make. A run always belongs to a Study; a botanist who never organizes anything still has one to start from — a default Study, inside a default Experiment, created automatically the first time it's needed. An Experiment row's own "Create study…" and a Study row's own "Create run…" (which opens Configure with that Study already selected) put creation on the row that receives it, rather than a separate step elsewhere; "Create experiment…" beside the filter bar is the one page-level exception, since a new Experiment has no row of its own yet to hang the action off of. The read-only [Examples experiment](#the-examples-experiment), holding the shipped worked examples, is always listed last; its rows carry a lock and offer no edits ([Read-only items](#read-only-items)). Deletion is Select/Select all/Delete: every row (Run, Study, Experiment) gets a checkbox, hidden until "Select" is toggled on, and "Delete selected" removes exactly what was checked — deleting a Study or Experiment cascades to its own Runs, named explicitly in the confirmation so a botanist never underestimates what is about to disappear. A recent-runs row, or browsing for a `trajectory.jsonl` directly, re-renders its summary and scatter (and, for a multi-generation run, its own scrubber) at any persisted generation, with the same optional differentiation-`q` sweep. Reachable from the rail's own Home button, or the File menu's "Open run…", from any screen | [Re-analyze a trajectory](#re-analyze-a-trajectory) |
| Configure | Two always-visible, independently scrollable panels: FIM parameters (ploidy — chosen first and never guessed, starting on diploid unless Settings' default ploidy says otherwise ("Ask me each time" leaves it blank, and a blank ploidy blocks the run) — then N, the number of *individuals* per deme, scalar or a per-deme table, then d, m, mu, seed: the values that together are "the finite island model"; the app multiplies individuals by ploidy into the gene-copy `N` the simulator and the YAML format use) and Structure (initial conditions, migrant sampling, mutation model, deme weighting, loci, which convergence statistic(s) to watch, replicate tolerance/minimum, and the within-run σ band) — one per [configuration reference](configuration.md) section, no dialog to open for any of them; every field and mode-selector group has a hover/focus tooltip. Default ploidy, the Run card's graph columns and scatter-plot style, execution engine, n<sub>replicates</sub>, max_generations, convergence window/tolerance, replicate confidence, and the batch-execution tuning fields (JIT, the `auto` engine's own two thresholds, parallel workers, max concurrent replicates) live in Settings instead, as defaults every fresh configuration starts from — loading a saved configuration or a worked example uses its own values for that run only and leaves Settings unchanged, with a notice listing any differences and a "Make these my Settings" button (see [Loading a configuration does not change your Settings](#loading-a-configuration-does-not-change-your-settings)). "Load configuration…"/"Save configuration…" read and write the exact YAML file format above, "Examples…" opens the Examples dialog (example classes on the left, the selected class's examples on the right with an excerpt of each one's explanation; "Load into Configure", or Return, fills the form and the Run name and description boxes — see [Worked examples](#worked-examples)), the File menu's "Load example…" opens the Presets picker (each preset also viewable as plain YAML, with a copy-to-clipboard action, and a loaded preset can be duplicated under a new name), and "▶ Run"/"🔮 Explore" jump to those destinations with the configuration exactly as shown. An invalid field on "Run simulation" (from anywhere) navigates here and marks the specific field, not only the section it lives in | [Create a configuration](#create-a-configuration) |
| Run view — running | The scatter plot and the trajectories side by side by default — a "Graphs" menu on the card chooses which graphs to show together (the choice is remembered), the columns they use are a Settings choice, and the statistics table beside them stays visible as both the colour legend and the on/off control for each trajectory line. A live scatter plot of the run's own current-generation frequencies (or, for a batch, every replicate's frequencies pooled onto one plot, filling in as replicates advance), with a generation progress indicator and a "Cancel" button — the window stays responsive throughout; the same axis selectors `completed` (below) has, live — picking a pair affects every subsequent push for the rest of the run, not just a one-time snapshot. For a scalar run, a statistic-vs-generation trajectory panel grows alongside the scatter as the run advances, plotting all six report statistics, each beside its own predicted-equilibrium reference line (D, G<sub>ST</sub>, E<sub>ST</sub> only — the three with a closed-form prediction). D, G<sub>ST</sub>, H<sub>S</sub>, H<sub>T</sub> and H<sub>ST</sub> also get a dash-dot closed-form curve: the value theory expects at every generation, starting from the state the run itself started in and settling at that statistic's equilibrium, so you can see whether the run is on track and how far drift has carried it. It is the model's expectation, so a run with few loci scatters around it and its D and G<sub>ST</sub> sit somewhat below it on average (a ratio of noisy quantities); with several loci the two agree closely. It covers unequal deme sizes and explicit migration matrices too (up to 24 demes), starting from the run's own seeded founding population. It is not drawn for per-locus mutation rates, for a founding population built by equilibration, or for a matrix with more than 24 demes, and it is shown and hidden together with its own statistic's on/off control. A "Show" chooser under the panel's title picks how each statistic is drawn: every generation's value; its trailing mean over the convergence window; or its cumulative mean, everything from where averaging began up to each generation — the run's own estimate as it accumulates, which homes in on the prediction as its band narrows. Both means carry a band of ± 2 standard errors, and the closed-form curve is averaged over the same windows, so the two still compare like with like while the run is relaxing. The cumulative mean begins where the convergence monitor began averaging a statistic, when a finished run records that, and otherwise after a burn-in of one convergence window. In both averaged displays a solid accent-colored line topped by a small flag marks where the window ending at the shown generation begins, with the window itself faintly shaded; it is drawn in a statistic's own color when statistics start averaging at different generations, and it is never dashed, so it cannot be confused with the scrubber's dashed grey marker. The choice is remembered, applies while the run is going and after it finishes, and changes nothing that is computed or saved. Hovering a statistic's row in the statistics panel shows, right after its value, the statistic's mean over the convergence window ending at the generation shown, with that mean's standard error and how many standard errors it lies from the prediction averaged over the same generations — for example `mean 0.5506 ± 0.0048 over generations 1,399–174,966 (173,568 generations; 1 SE); predicted 0.5448, +1.2 SE`. This is the result to read for a noisy statistic: a single generation's value can sit far from the prediction by drift alone, while the window mean shows whether the run agrees with theory. At the last generation of a finished run the window is the one the convergence monitor actually stopped on — it keeps widening a watched statistic's window until the mean is known to half the tolerance, so it can be far longer than the convergence window — and "not yet noise-adequate" marks a mean that is not yet known that well. The standard error allows for the correlation between neighbouring generations the same way the monitor does (a first-order autoregressive estimate), which reads somewhat low when a statistic relaxes on more than one time scale. After that comes how far the run is from its prediction at that one generation: Δ<sub>p</sub>, the observed value minus the predicted one at the generation shown (for example `ΔDₚ = +0.012, MSE = 4.1e-5`), and the mean squared error of the trajectory so far, from the first recorded generation to the one shown. Both are running values: they update with every live tick and follow the scrubber. The prediction is the closed-form expected trajectory when the run's model has one; otherwise (stochastic migrant counts, finite alleles, unequal deme sizes beyond the closed form's reach) it is the predicted equilibrium, the dashed line, which also covers E<sub>ST</sub>. The tooltip names which one it used. Cancelling, or the run ending in an error, leaves this same view showing exactly as it last rendered, with a banner on top | `run`'s own progress/error output, on one screen instead of terminal lines |
| Run view — completed | A scalar run's summary (all six named statistics, convergence outcome, each shown as a meter against the same `[0, 1]` scale the confidence-interval bars below use) beside the canonical scatter plot and the same trajectory panel described above — replaced, once the run finishes, by the real persisted trajectory, and showing the within-run σ band (a shaded region plus its own `mean [lower, upper]` caption) whenever [sigma_band_multiplier](configuration.md#sigma_band_multiplier) was set — or — for a batch — a pooled scatter across every replicate's final state beside a replicate table (status, final generation, every named statistic) and each statistic's across-replicate confidence interval as a meter, explicitly labeled "uncertainty across N independent replicates" so it is never confused with the within-run σ band; either way, one panel (Deme 1 vs. Deme 2 by default) with a labeled, numbered `0.0`-`1.0` probability scale on both axes; axis selectors on the plot choose which two demes to compare directly, and selecting Deme 1 vs. Deme 2 again returns to the default panel; the statistics panel's deme-pair rows (Nei distances and pairwise F<sub>ST</sub>, when shown) follow the same choice, live, after the run and while scrubbing; which statistics the panel, the results tables, the trajectory chart and the sweep charts show is a Settings choice ("Statistics shown", or "Choose…" in the panel's caption) that never changes what is computed or saved — see [Nei distances](#nei-distances); a scalar run with more than one persisted generation auto-populates a play/pause-and-scrub time slider over the persisted trajectory in the background, with no separate button to reach it; each batch replicate row's own "Open" button reaches this same view for that one replicate; "Open output folder" reveals the run's own artifacts (a batch's own `summary.json` and every replicate subdirectory, for a batch) | [Output schemas](#output-schemas), [Batch `summary.json` and `manifest.json`](#batch-summaryjson-and-manifestjson) |
| Explore | Four fields (N in individuals, d, m, mu; predictions use the form's ploidy to turn individuals into gene copies) and a theoretical-prediction table covering differentiation (D, G<sub>ST</sub>, E<sub>ST</sub>), equilibrium diversity (within-deme and pooled heterozygosity, Shannon entropy, and effective allele counts), and Whitlock identity-recovery metrics. The table also reports whether mutation is negligible at equilibrium and marks the typical-deme entropy as approximate, especially at d = 2. Values update when a field is committed — no simulation ever runs, so this remains immediate regardless of N or d. A sweep curve plots any of the predicted statistics across a fixed range of the selected field. Every statistic can be charted on every one of the four sweeps — click a table row to plot it. Because these are measured in different units (proportions, nats, effective alleles, generations), the chart shows one unit family at a time and switches families when you pick a statistic from another one. A statistic that does not depend on the swept field draws a flat line, which is itself informative: D does not vary with N at all, and the identity-recovery metrics do not vary with mutation rate. A slider beneath the chart moves the marker along the swept range and re-reads the whole table at that value, leaving your four fields untouched until you change them yourself. "▶ Run this for real" seeds Configure with these same four values and takes you there, with a new study pre-selected (change or clear it before running). Reachable from the rail's own Explore button, or "🔮 Explore" on Configure (which carries Configure's own current values over), from any screen; Back/Forward use the shared screen history | No CLI equivalent — a direct `fim.statistics` call from Python or a script is the closest terminal equivalent |
| Compare | Pick two or more previously completed runs from a recent-runs list, then overlay their final-state scatter panels as small multiples with a legend naming whichever configuration field(s) actually differ across the selection, plus a trajectory-over-generations overlay (one statistic at a time, one color per run, selectable from the same six named statistics) — "how does the conclusion change as I vary this one knob," on real simulated runs, no re-run needed. Reachable from the rail's own Compare button from any screen; Back/Forward use the shared screen history | No CLI equivalent — comparing several `trajectory.jsonl`/`report.json` files by hand is the closest terminal equivalent |
| Help | This guide and the [configuration reference](configuration.md), rendered in-app with working cross-links; every other doc opens on GitHub in the OS default browser instead. Reachable from the rail's own Help button, or the Help menu, from any screen; Back/Forward use the shared screen history | No CLI equivalent — the terminal reads these same two files directly |

A GUI-authored run with the same parameters and seed produces byte-identical
`trajectory.jsonl`/`report.json` to the same configuration run from the
terminal — see [Reproduce a run](#reproduce-a-run). The GUI performs network
access only for the same explicit, opt-in release check the terminal's `fim
update --check` performs (Help menu → "Check for updates") — otherwise, like
the CLI, none at all.

On Linux, the GUI needs WebKitGTK, a system package most desktop
distributions already have installed — see
[installation alternatives](../install/README.md) if `fim-gui` reports it
is missing rather than opening a window.

A double-clicked or `fim --graphical`-launched GUI takes no flags of its
own, so its operational log is configured from two environment
variables instead — `FIM_LOG_LEVEL` and `FIM_LOG_OPTIONS`, the exact
equivalents of `-l`/`-L` below, set before launching (a modified
shortcut's own "Target" field, or a wrapper script). See [operational
logging design](fim-logging-design.md) §5. The separate `fim-gui`
console-script entry point additionally accepts real flags of its own
— see [Where results, logs, and preferences are
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
labels. A run's name, description, and class live in its own
`metadata.json`, beside `manifest.json`, which they never change, and are
never part of the run's ID: renaming a configuration does not make it a
different run. The labels are written only when the run has no
`metadata.json` yet, so they never overwrite a name you changed later.

### The Examples experiment

Home always lists one more Experiment, **Examples**, last, after your own
work. It holds the worked examples that ship with the app, one Study per
example class (a class inside another is named "Parent — Child"), each
holding that class's examples as runs with their saved results. The app
writes these when it starts, into an `examples` folder inside your results
folder, and writes them again if you remove them; a newer version of the
app replaces them with its own. An example whose result has not been saved
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
  description boxes; running it makes a new run of your own.
Home's **Clone** action is available for regular runs too.
It fills the parameters, Run name, and Run description without changing
the source run or adding it to another Study. Modify the configuration
and run it to create your own result. Configure's Study selector starts
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

Use **Load into Configure** to run an editable copy. The copy is your own
run and never replaces the read-only example. Batch examples include every
kept replicate's full results and the shared batch summary.

### If the window closes but `fim` keeps running

Closing the window should end the program immediately. If something
inside it fails to stop, `fim` guards against being left running
invisibly — with no window to click and no obvious way to quit — by
forcing itself to exit about 20 seconds after the window closes. When
that happens it prints a line explaining why, so the cause can be
reported rather than guessed at:

```text
fim: shutdown did not complete within 20s; forcing exit
```

Your results are unaffected. Everything a run produces is written to its
output directory as the run proceeds, so a forced exit after the window
is already closed cannot lose any of it.

A diagnostic report is written at the same time, to `logs/fim.log` as well
as to the terminal — so it is kept even when `fim` was started from an icon
or shortcut with no terminal attached. If this happens to you, please report
it: the section of that file beginning `shutdown deadman fired` names what
failed to stop, and is what makes the cause findable. See
[known issues](../ISSUES.md#intermittent-hang-during-gui-shutdown).

If you are investigating such a shutdown yourself and need the process to
stay alive rather than be terminated, set `FIM_GUI_SHUTDOWN_TIMEOUT` to
the number of seconds to allow, or to `0` to wait indefinitely:

```console
FIM_GUI_SHUTDOWN_TIMEOUT=0 fim --graphical
```

### Loading a configuration does not change your Settings

The run settings — execution engine, n<sub>replicates</sub>,
max_generations, convergence window and tolerance, replicate confidence,
JIT, the `auto` engine's two thresholds, and max concurrent replicates —
live in Settings, not on the Configure form. A configuration you load can
name its own values for them. Every way of loading one works the same:

- **Examples…** → **Load into Configure**, and **Run it** on a saved example
- File menu → **Load example…** (the presets picker), built-in or your own
- **Load configuration…** (a YAML file)
- **Clone** on a run listed on Home

The loaded values are used **for this run only**. Your Settings stay
exactly as they were, so the next **New configuration** starts from your
Settings again. A value the loaded file does not name takes the
simulator's own default, the same value `fim run` would use for that
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
- **Dismiss** hides the notice. The run still uses the loaded values. A
  "run settings" item in the parameter strip at the top of the window
  stays while they differ; click it to see the notice again.

If you change Settings after loading, the notice is updated to compare
against your new Settings; the loaded configuration still keeps its own
values. To return to your Settings for the run, choose File menu →
**New configuration**, or load a configuration whose run settings match
yours. Explore's **Run this for real** also starts from your Settings.

When the app restores the last submitted configuration at launch, the run
settings come from your Settings, not from that configuration.

### Saved preferences

The GUI remembers these things between launches: Settings' own Significant
digits setting, which statistics are shown ("Statistics shown"; showing
E<sub>ST</sub>, K<sub>ST</sub>, A<sub>CGD</sub>, δ<sub>G</sub> or I also
makes every new run compute all five every generation, which takes longer —
see [track_expensive_statistics](configuration.md#track_expensive_statistics)), the
largest deme count for which runs save every pair's statistics, the light/dark override (absent/`null` means "follow the
OS," the default), whether the first-launch welcome panel has already been
dismissed, the startup behavior selected in Settings, the execution defaults
(execution engine, n<sub>replicates</sub>, and the rest of Settings' own
"how the computation runs" fields — see the Configure row above) every fresh
configuration starts from, and the last configuration you successfully
clicked "Run simulation" with. In Settings, "Restore the last submitted
configuration" keeps the existing behavior: a fresh launch's own Run view
starts from that saved configuration rather than from the built-in starter
values. "Restart from the starter configuration" uses the built-in starter
values on the next launch instead — with the saved execution defaults still
applied over them, the same as any other fresh configuration. A genuinely
first launch (welcome not yet dismissed) shows the welcome panel offering
"Examples…" (the Examples dialog) or "Start from scratch." File menu → "New
configuration" always resets to the built-in starter values (execution
defaults included) regardless of what is saved as the last submitted
configuration; it is the one action that ignores that saved configuration on
purpose. Loading a saved configuration file, a preset, a worked example or
a saved run never changes the saved execution defaults: see
[Loading a configuration does not change your Settings](#loading-a-configuration-does-not-change-your-settings).

Nothing scientific is stored here: a run's own configuration, seed, and
results always live in that run's own `manifest.json`/`trajectory.jsonl`
under `results/`, exactly as described throughout this guide. This file
holds only the GUI conveniences above.

It lives in the platform's normal per-user settings location — you do not
need to find or edit it for ordinary use:

| Platform | Location |
|---|---|
| macOS | `~/Library/Application Support/fim/preferences.json` |
| Windows | `%APPDATA%\fim\preferences.json` |
| Linux | `$XDG_CONFIG_HOME/fim/preferences.json` (usually `~/.config/fim/preferences.json`) |

If this file is ever unreadable — edited by hand into invalid JSON, or left
over from an incompatible future version — the GUI notices on the next
launch, shows a dismissible banner naming the problem, and starts from
built-in defaults instead of failing to open or guessing at a broken value.
The unreadable file is never deleted: it is renamed alongside itself with a
timestamp (`preferences.invalid-<timestamp>.json`) so nothing is lost, and a
fresh, working file is written in its place. Deleting the whole `fim`
folder shown above is always a safe way to reset both saved preferences
from scratch; it never touches anything under `results/`.

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
control this program's own operational log — separate from, and
unaffected by, `run`'s own `--quiet` and progress/artifact messages
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
setup that needs more than `-L`'s own inline options can express
(several loggers, several handlers, a custom formatter). It is not
layered with `-l`/`-L`: whichever one is actually given wins outright.

### Where results, logs, and preferences are written

By default, `fim` writes into `results/`/`logs/` beside a real checkout,
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
| `--preferences-file PATH` | `FIM_PREFERENCES_FILE` | The desktop app's own `preferences.json` (form defaults, named presets, Settings) — meaningful only to a later `fim --graphical`/`fim-gui` launch, accepted here regardless |

`fim-gui` (the separate desktop-app entry point) accepts the identical
four flags directly; `fim --graphical`/a double-clicked packaged build
honors the matching environment variables only, with no flags of its
own — set them before launching (a modified shortcut's own "Target"
field, or a wrapper script), the same mechanism already documented
above for `FIM_LOG_LEVEL`/`FIM_LOG_OPTIONS`. The desktop app's own
Settings dialog additionally offers a "Storage location" field for
results — see the in-app Help screen.

## Output schemas

### `trajectory.jsonl`

One JSON object is appended for every nonzero frequency:

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

### `equilibrium_trajectory.jsonl`

Written only by an [equilibrium-split](#equilibrium-split-founding) run: the
ancestral population's own trajectory, simulated before it is split into
your demes. Every generation of that ancestral phase is kept. The rows have
exactly the `trajectory.jsonl` fields above, so anything that reads
`trajectory.jsonl` reads this file too, with two differences:

- `deme` is always 1: the ancestral population is one deme holding all of
  your demes' gene copies.
- `generation` counts the ancestral phase on its own, from 0 (the starting
  draw) to the manifest's `equilibrium_generation_count`. Its last
  generation is the population that was split. The main run's own
  generation 0 in `trajectory.jsonl` is the moment just after that split,
  so ancestral generation `g` happened
  `equilibrium_generation_count - g` generations before it.

The file has the same `run_id` as the main trajectory, and the manifest
records its digest as `equilibrium_trajectory`. Runs with any other
starting condition do not have this file.

### `manifest.json`

The manifest records the complete parameter mapping, seed, software version,
UTC start/end timestamps, generation, watched statistic, and whether
convergence or the hard cap ended the run. It also carries:

- schema_version: the manifest's own shape version.
- convergence.generation_count: how many distinct generations the run
  actually wrote to `trajectory.jsonl` (`convergence.generation + 1` for
  every run, since no generation is ever skipped — recorded explicitly
  rather than left implicit).
- `artifacts`: the SHA-256 digest and byte count of `trajectory.jsonl`,
  `report.json`, `scatter.png`, and every other file the run wrote (for
  example `equilibrium_trajectory.jsonl`) as they existed at the moment
  the run finished writing and flushing them. `fim stats` recomputes and checks the
  trajectory's digest (and its generation count) before reading a single
  row, so a trajectory edited, truncated, or replaced after the run
  completed is refused with a clear error rather than re-analyzed
  silently.

output_directory (the whole four-file set for a scalar run, or the whole
`replicate-NNN/` plus `summary.json`/`manifest.json` tree for a batch) is
built in a hidden temporary location beside the target path and published
with a single atomic rename only once every file in it is flushed and
`manifest.json` has been written last with its `artifacts` digests. An
interrupted run — an exception, `^C`, or a killed process — therefore never
leaves a partial directory at the target path: it is either not there at
all, or complete. This guarantee covers process-level interruption, not
an unclean power loss: nothing in the write path calls `fsync`, so on
power loss a directory can look complete (the rename itself is atomic)
while some file inside it has content that never reached physical disk.

**Compatibility with `fim` 1.0.0 output.** `fim` 1.0.0, the only version
released before schema_version and `artifacts` existed, wrote manifests
with neither field. `fim stats` refuses such a manifest — it has no
digest to verify the trajectory against — with an error naming both the
cause and that there is no automated migration: re-run the same
configuration (the manifest's own `parameters`) with the current `fim`
to get a manifest this version can read and verify.

### `report.json`

The final report contains:

- run identity, generation, convergence flag, watched statistic, and reason;
- H<sub>S</sub>, H<sub>T</sub>, and the correctly partitioned H<sub>ST</sub>;
- G<sub>ST</sub> (`null` only when *every* tracked locus is fixed for the same
  allele in every deme; with several loci, a locus that is fixed on its
  own does not blank out the others — it is dropped and the remaining,
  genuinely polymorphic loci are averaged);
- Jost's `D`, entropy differentiation E<sub>ST</sub>, and allele-number
  differentiation K<sub>ST</sub>;
- literature-derived supplemental statistics:
  Caballero-García-Dorado allelic distance A<sub>CGD</sub>, Gregorius
  δ, and Sherwin mutual information `MI`;
- the within- and between-deme gene identities G<sub>s</sub> (`Gs`) and
  G<sub>d</sub> (`Gd`);
- Nei's (1973) D<sub>m</sub> (`D_m`) and R<sub>ST</sub> (`R_ST`), Nei's
  logarithmic G'<sub>ST</sub> (`G_ST_NEI_LOG`), Hedrick's standardized
  G'<sub>ST</sub> (`G_ST_HEDRICK`), and coancestry F<sub>ST</sub> (`F_ST`),
  each computed from the pooled H<sub>S</sub>/H<sub>T</sub> and `null` where
  undefined;
- Nei's genetic distance across all demes in four forms, and the matching
  identities: `NEI_D_ALL_GEO`, `NEI_D_ALL_GEO_LOCUS_MEAN`, `NEI_D_ALL_ARITH`,
  `NEI_D_ALL_ARITH_LOCUS_MEAN`, `NEI_I_ALL_*` (see
  [Nei distances](#nei-distances)). A distance is `null` when infinite; its
  identity is always a number.

A `report.json` written before a statistic existed simply lacks that key.
Opening such a run computes the missing values from its saved trajectory
without rewriting the file.

Multiple loci are independent repeats. H<sub>S</sub>, H<sub>T</sub>, H<sub>ST</sub>, E<sub>ST</sub>,
and K<sub>ST</sub> are always each locus's own arithmetic mean. `D` and G<sub>ST</sub>
instead follow [locus_aggregation](configuration.md#locus_aggregation):
by default (`ratio_of_means`), H<sub>S</sub>/H<sub>T</sub> are pooled across loci
first and one `D`/G<sub>ST</sub> is computed from those pooled values, not
averaged per-locus ratios; the opt-in `mean_of_ratios` restores the
per-locus-ratio-then-average behavior. `D` and K<sub>ST</sub> always use equal
deme weighting. deme_weighting affects E<sub>ST</sub>.

### `pairwise.json`

Every pair of demes' Nei identities and pairwise F<sub>ST</sub> at the run's
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
`fim`. The diagonal is not stored (identity 1, F<sub>ST</sub> 0). Each
distance is `-ln` of its identity; a pairwise F<sub>ST</sub> is `null` where
undefined (both demes fixed for the same allele). With more demes than
[`--pairwise-max-demes`](#run-a-simulation), `"mode"` is `"skipped"`, the
limit is recorded as `"max_demes"`, and there are no matrices.

### Nei distances

`fim` reports Nei's genetic distance with Nei's own geometric-mean
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
  (H<sub>S</sub>, effective number of alleles). Pair forms and arithmetic forms
  are never negative.

The arithmetic all-demes distance equals `-ln(1 - D)` for Jost's `D`.
[Nei distances in fim](nei-distances.md) has the full explanation, a worked
example of a negative value, and the formulas.

### `scatter.png`

- `d = 2`: direct Deme 1 versus Deme 2 scatter with a diagonal reference.
- `d = 3`: direct three-dimensional scatter.
- `4 <= d <= 6`: every pairwise deme projection.
- `d > 6`: direct Deme 1 versus Deme 2 scatter, same as `d = 2` — not a
  PCA projection.

One point represents one `(locus, allele)` pair. Coincident points are enlarged
and annotated.

#### What the marker colors mean

Points are colored to answer one question at a glance: which allele is the
most common one?

| Color | Meaning |
|---|---|
| Blue | The most frequent allele in either of the two demes shown |
| Orange | Every other allele |

Two blue points is normal and correct. Each of the two demes on the plot
gets its own most frequent allele marked, and they are often different
alleles — that difference is frequently the interesting result. You will see
a single blue point when both demes happen to agree on the same allele.

If two or more alleles are exactly tied for most frequent, only the first is
marked, so that one plot never implies more "most frequent" alleles than it
has demes. The choice is stable: the same data always marks the same allele,
and the legend states the rule.

Blue does not mean "important", "significant", or "above a cutoff". It marks
a maximum, and a maximum exists in every data set — including one where all
the alleles are rare and nearly equal.

### Batch `summary.json` and `manifest.json`

Written only for n<sub>replicates</sub> greater than one, alongside the
`replicate-NNN/` subdirectories, each of which holds the scalar-run files
above.

`summary.json` maps each reported statistic name (every global statistic in
[`report.json`](#reportjson): `D`, G<sub>ST</sub>, E<sub>ST</sub>,
K<sub>ST</sub>, H<sub>S</sub>, H<sub>T</sub>, H<sub>ST</sub>, A<sub>CGD</sub>,
Gregorius δ, `MI`, G<sub>s</sub>, G<sub>d</sub>, the pooled-heterozygosity
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

`half_width` and `sample_std` answer two different questions, and it is
worth keeping them apart:

- `half_width` is how precisely the **average** is known — the "± 0.021"
  half of a "0.643 ± 0.021" report. Running more replicates shrinks it.
- `sample_std` is how much the **replicates themselves** differ from one
  another. Running more replicates does not shrink it, because it
  describes the model's own run-to-run variability rather than your
  measurement of it.

`sample_std` can also be `null`, meaning "this interval has no single
honest value to report here." That happens only for an interval built by
resampling rather than by the ordinary Student's-t formula (see
`fim.engine.bootstrap_replicate_summary`), whose two sides are not
generally the same width — for such an interval, read `low` and `high`
as the real bounds rather than treating `mean` ± `half_width` as the
whole story. Every interval the command line writes today reports a real
number.

A `summary.json` written by an older version of this program has no
`sample_std` field at all. Nothing needs converting: every tool here
reads such a file as though the value were `null`.

G<sub>ST</sub> can have a smaller sample_count than the other statistics: a
replicate whose locus is monomorphic across every deme reports G<sub>ST</sub> as
`null` in its own `report.json`, and that replicate is excluded from
G<sub>ST</sub>'s interval rather than papered over with a substitute value. The
other statistics that can be `null` because they are undefined (R<sub>ST</sub>,
both G'<sub>ST</sub>, F<sub>ST</sub>) are treated the same way. A Nei distance
is different: `null` there means *infinite*, and a mean that includes an
infinite value has no finite value, so a Nei distance infinite in any
replicate is omitted from `summary.json`; its identity is still summarized.
A statistic left with fewer than two defined replicates is omitted from
`summary.json` entirely.

The batch's own `manifest.json` (distinct from each replicate's own) records
schema_version, the batch run_id, every replicate_run_ids entry,
replicate_count, the shared `parameters`, batch start/end timestamps, and
software_version — not a per-run convergence outcome, since each replicate
has its own. Like a scalar run's manifest, it also carries `artifacts`: the
SHA-256 digest and byte count of `summary.json` and of each replicate's own
`manifest.json` (keyed `replicate-NNN`), recorded once every one of them is
flushed, so an edited, truncated, or replaced batch-level artifact is
detectable the same way a scalar run's is. Under parallel execution — the
CLI default — an adaptive replicate_tolerance stop can leave a worker that
had already started its own `replicate-NNN/` directory before the stop was
decided; that directory is pruned before publishing, so the `replicate-*`
subdirectories actually present always equal replicate_run_ids exactly.

## Reproduce a run

1. Copy `manifest.json`.
2. Use its `parameters` object as a new YAML config.
3. Run the same `fim` version shown in software_version.
4. Compare `trajectory.jsonl` and `report.json` byte for byte.

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
the difference is written to `reproducibility.json` beside the new run: the
simulator guarantees bit-for-bit reproducibility, so a difference is a
finding, not a detail. `fim sweep` prints the same and exits with status 1.

Given the same version, parameters, and seed, those files are identical.
Manifest timestamps may differ.

## Troubleshooting

- **Unknown key:** compare the named key with
  [configuration.md](configuration.md); typos are never ignored.
- **Output already exists:** select a new output directory. `fim` refuses
  to publish into one that already exists at all, even if empty — never
  appended, overwritten, or reused.
- **Reached the cap:** the message names how many generations the model
  needs to forget its starting state. Inspect the trajectory and report, then
  increase max_generations, relax the tolerance, or select another
  convergence statistic based on the study's needs.
- **A run finished in a few hundred generations:** check whether
  convergence_window and max_generations were set to small fixed numbers.
  Leave them on `auto`; see
  [Why does my run take so long?](#why-does-my-run-take-so-long).
- **Windows warning:** verify the release checksum before running the unsigned
  executable. See [SECURITY.md](../SECURITY.md).
