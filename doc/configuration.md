# `fim` configuration reference

`fim run` accepts one YAML mapping. Unknown keys, incompatible shapes, and
out-of-range values are errors. See [Using `fim`](usage.md) for commands and
the [project overview](../README.md) for installation.

Mathematical quantities use $\TeX$; YAML keys, literal values, and API
identifiers use backticks. Display equations have unique local
$\text{CFG}$ tags.

## Contents

- [Settings index](#settings-index)
- [Complete example](#complete-example)
- [Required model keys](#required-model-keys)
- [Loci](#loci)
- [Mutation model](#mutation-model)
- [Initial conditions](#initial-conditions)
- [Convergence](#convergence)
- [Analysis and execution](#analysis-and-execution)
- [Engine backend and JIT](#engine-backend-and-jit)
- [Run labels](#run-labels)
- [Internal attributes](#internal-attributes)
- [Validation summary](#validation-summary)

## Settings index

Every setting, in alphabetical order, with the section that describes it.

| Setting | Section |
|---|---|
| [`_read_only`](#_read_only) | Internal attributes |
| [`auto_vector_max_capacity`](#auto_vector_max_capacity) | Engine backend and JIT |
| [`auto_vector_min_d`](#auto_vector_min_d) | Engine backend and JIT |
| [`class`](#class) | Run labels |
| [`confidence`](#confidence) | Analysis and execution |
| [`convergence_burn_in`](#convergence_burn_in) | Convergence |
| [`convergence_combinator`](#convergence_combinator) | Convergence |
| [`convergence_estimate`](#convergence_estimate) | Convergence |
| [`convergence_statistic`](#convergence_statistic) | Convergence |
| [`d`](#d) | Required model keys |
| [`deme_weighting`](#deme_weighting) | Analysis and execution |
| [`description`](#description) | Run labels |
| [`engine_backend`](#engine_backend) | Engine backend and JIT |
| [`equilibrium_convergence_tolerance`](#equilibrium_convergence_window-equilibrium_convergence_tolerance-equilibrium_max_generations) | Initial conditions |
| [`equilibrium_convergence_window`](#equilibrium_convergence_window-equilibrium_convergence_tolerance-equilibrium_max_generations) | Initial conditions |
| [`equilibrium_max_generations`](#equilibrium_convergence_window-equilibrium_convergence_tolerance-equilibrium_max_generations) | Initial conditions |
| [`expert`](#expert) | Convergence |
| [`history_format`](#history_format) | Analysis and execution |
| [`initial_allele_count`](#initial_allele_count) | Initial conditions |
| [`initial_concentration`](#initial_concentration) | Initial conditions |
| [`jit`](#jit) | Engine backend and JIT |
| [`loci`](#loci) | Loci |
| [`locus_aggregation`](#locus_aggregation) | Analysis and execution |
| [`m`](#m) | Required model keys |
| [`max_concurrent_replicates`](#max_concurrent_replicates) | Engine backend and JIT |
| [`max_generations`](#max_generations) | Convergence |
| [`migrant_sampling`](#migrant_sampling) | Analysis and execution |
| [`mu`](#mu) | Required model keys |
| [`mutation_model`](#mutation_model) | Mutation model |
| [`N`](#n) | Required model keys |
| [`n_replicates`](#n_replicates) | Analysis and execution |
| [`name`](#name) | Run labels |
| [`p_0`](#p_0) | Initial conditions |
| [`ploidy`](#ploidy) | Required model keys |
| [`precision`](#precision) | Convergence |
| [`precision_method`](#precision_method) | Analysis and execution |
| [`replicate_averaging_window`](#replicate_averaging_window) | Analysis and execution |
| [`replicate_minimum`](#replicate_minimum) | Analysis and execution |
| [`seed`](#seed) | Required model keys |
| [`statistic_precision`](#statistic_precision) | Convergence |
| [`stop_batch_early`](#stop_batch_early) | Analysis and execution |
| [`track_expensive_statistics`](#track_expensive_statistics) | Convergence |
| [`trajectory_keep_burn_in`](#trajectory_retention-trajectory_stride-trajectory_thinning_start-trajectory_keep_last-trajectory_keep_burn_in) | Convergence |
| [`trajectory_keep_last`](#trajectory_retention-trajectory_stride-trajectory_thinning_start-trajectory_keep_last-trajectory_keep_burn_in) | Convergence |
| [`trajectory_retention`](#trajectory_retention-trajectory_stride-trajectory_thinning_start-trajectory_keep_last-trajectory_keep_burn_in) | Convergence |
| [`trajectory_stride`](#trajectory_retention-trajectory_stride-trajectory_thinning_start-trajectory_keep_last-trajectory_keep_burn_in) | Convergence |
| [`trajectory_thinning_start`](#trajectory_retention-trajectory_stride-trajectory_thinning_start-trajectory_keep_last-trajectory_keep_burn_in) | Convergence |

## Complete example

```yaml
N: 225
ploidy: diploid
d: 20
m: 0.001
mu: 0.0000003
seed: 20260814
loci:
  - locus_id: 1
    length: 200
initial_allele_count: 2
initial_concentration: 1.0
deme_weighting: equal
convergence_statistic: D
convergence_combinator: all
precision: 0.01
max_generations: auto      # derived from the model; see below
n_replicates: 200   # the default: an adaptively stopped batch; 1 asks for a single run
engine_backend: auto   # recommended choice; the library default is lineal
```

## Required model keys

### `N`

- **Type:** positive integer, or a list of `d` positive integers
- **Required:** yes
- **Meaning:** individuals per deme

`N` counts individuals per deme. The simulator multiplies it by
[`ploidy`](#ploidy) to obtain the gene-copy count:
`N: 225` with `ploidy: diploid` is 450 gene copies per deme. That conversion
happens once, when the configuration is read; everything after it, including
every statistic and the analytic formulas, works in gene copies.

Per-deme lists model unequal island sizes: every stage of the update
pipeline (`migrate`, `drift`, `mutate`), the founding-allele-count bound
(checked against the smallest $N_i$), and deme_weighting: size (when chosen) all use
each deme's gene-copy count.

```yaml
N: [120, 450, 60]   # three demes, unequal individual counts
ploidy: haploid
d: 3
```

A scalar `N` and a list of `d` equal values are numerically identical;
prefer the scalar form when every deme is the same size.

### `ploidy`

- **Type:** `haploid`, `diploid`, `triploid`, or `tetraploid`
- **Required:** yes
- **Meaning:** gene copies per individual (1, 2, 3 or 4)

Specifies gene copies per individual. Use a named value, not a number.
For example, `N: 225` with `ploidy: diploid` represents 450 gene copies.
Numeric ploidy values are rejected to prevent ambiguous interpretation of
configurations that use `N` as a gene-copy count.

```yaml
N: 225            # individuals per deme
ploidy: diploid   # 2 gene copies each, so 450 gene copies per deme
```

Literature configurations that state haploid reproductive individuals, such as
Jost (2008) Part VI and the Dear-Nolan scenarios, say `ploidy: haploid`, so
their individual and gene-copy counts are equal.

Because it is part of the recorded configuration, two runs that differ only
in `ploidy` get different auto-generated run ids, even when their gene-copy
counts are equal: 450 haploid and 225 diploid individuals are the same
genetic process but different recorded population descriptions.

### `d`

- **Type:** integer at least 2
- **Required:** yes
- **Meaning:** number of demes

### `m`

- **Type:** number in `[0, 1]`, or a `d` by `d` row-stochastic matrix
- **Required:** yes
- **Meaning:** migration rate or complete migration weights

A scalar uses the symmetric finite island model: each deme retains `1 - m` of
its frequency vector and receives `m` from the size-weighted average of all
other demes. Matrix rows must each sum to 1.

A full matrix models asymmetric migration — row `k` gives destination deme
`k`'s source weights, and row `k`'s weights need not equal row `j`'s.
Every row is independently configurable, including rows that are not
symmetric with any other row.

```yaml
m:
  - [0.90, 0.05, 0.05]   # deme 1 retains 90% and receives 5% from each neighbor
  - [0.10, 0.80, 0.10]   # deme 2 blends evenly with both neighbors
  - [0.00, 0.20, 0.80]   # deme 3 exchanges only with deme 2
d: 3
```

Matrix and scalar forms have the following relationship:

- **A matrix's rows are the authoritative weights — they are not scaled by
  `N`.** The scalar path automatically derives a size-weighted migrant pool
  from each deme's gene-copy count; a matrix directly specifies the
  destination's source mix, so `N` does not alter that blend. If unequal
  deme sizes should also drive migration weighting, build that weighting into
  the matrix itself. (This describes the default `migrant_sampling:
  continuous` blend. Under the opt-in migrant_sampling: stochastic, below,
  `N` re-enters the picture — not to change the mean, but to set how much a
  generation's migrant count can vary around it.)
- **The scalar form is the matrix's symmetric special case, not a different
  mechanism.** For equal-size demes, a scalar rate `m` is numerically
  identical to the full matrix with `1 - m` on the diagonal and `m / (d - 1)`
  on every off-diagonal entry. Prefer the scalar form for equal-size demes
  with symmetric migration.

#### Sparse and spatial (stepping-stone) migration

Sparse migration graphs can be specified without writing every zero in a
dense matrix. Two compact forms are available:

**A sparse neighbor map.** Give only the nonzero off-diagonal weights, keyed
by deme (one-based) and neighbor (one-based); each deme's self-retention is
implied as `1` minus its listed weights. A deme absent from the map has
no incoming migration.

```yaml
m:
  1: {2: 0.01}
  2: {1: 0.01, 3: 0.01}
  3: {2: 0.01, 4: 0.01}
  4: {3: 0.01}
d: 4
```

Weights need not be symmetric. Use this form for irregular adjacency
graphs as well as regular topologies.

**Named topologies**, for common stepping-stone cases: a compact
`{topology, rate}` mapping (plus `rows` and `columns` for a torus) that
expands to the sparse form above.
`rate` is every deme's total outgoing migration fraction, split evenly among
its neighbors — the same meaning `m` has as a scalar, applied
locally instead of globally.

```yaml
m:
  topology: ring     # or: linear
  rate: 0.01
d: 100
```

- `ring` — a circular chain; deme `d`'s next neighbor wraps back to deme
  `1`. Every deme has exactly two neighbors. Requires `d` at least `3`.
- `linear` — a bounded chain, no wraparound. The two end demes have one
  neighbor instead of two, so an end deme's entire `rate` goes to its
  single neighbor rather than being split.
- `torus` — a `rows` by `columns` grid that wraps in both directions, so
  no deme is on an edge: every deme has exactly four neighbors (up, down,
  left, right) and splits its `rate` four ways. Demes are numbered row by
  row (deme `1` is the top-left cell, deme `columns` the top-right, deme
  `columns + 1` the start of the second row). `rows * columns` must equal
  `d`, and each side must be at least `3` (a side of 2 would make a
  deme's up and down neighbors the same deme).

  ```yaml
  m:
    topology: torus
    rate: 0.01
    rows: 10
    columns: 10
  d: 100
  ```

Both sparse maps and named topologies are configuration-file shorthand:
they expand to the ordinary dense matrix at load time (visible as such in
`manifest.json` and `to_dict()`). Operators, statistics, and persistence
receive the expanded matrix. A `SimulationParams` constructed directly in
Python requires a dense matrix; use
`fim.model.topology.stepping_stone_neighbors` and
`fim.model.topology.dense_matrix_from_neighbors` to construct one.
See the [topology API](fim-simulator-functional-api.md#3-fimmodeltopology--build-a-migration-matrix).

Migration topologies that no fixed matrix can express — neighbor
selection that changes over a run — are outside the current configuration
surface. A bounded 2D lattice can be expressed as a sparse neighbor map;
`torus` covers the wrapped case. See the
[migration conventions](migration-conventions.md) for matrix orientation
and comparisons with the literature.

### `mu`

- **Type:** number in `[0, 1]`, or a list of exactly one rate per locus
- **Required:** yes, unless `mu_b` is given instead (the
  two are mutually
  exclusive)
- **Meaning:** per-gene-copy mutation probability per generation: after
  drift draws a deme's `N` new gene copies, each one, independently,
  mutates with this probability (the textbook Wright-Fisher model)

A scalar applies identically to every locus, regardless of `length`, and
is the right choice whenever every locus should mutate at the same rate.
A list gives each locus a rate, positionally matched to
`loci`:

```yaml
mu: [0.001, 0.01]
loci:
  - locus_id: 1
    length: 50
  - locus_id: 2
    length: 500
```

#### Deriving `mu` from a per-base rate

`mu` specifies per-locus probabilities independently of length.
Alternatively, `mu_b` specifies a shared per-base-pair mutation probability.
Write $\mu_b$ for the probability configured by `mu_b`, $\ell$ for
the positive integer `length`, and $\mu_\ell$ for the derived per-locus
probability. Each base avoids mutation with probability $1-\mu_b$;
all $\ell$ bases avoid it with probability $(1-\mu_b)^\ell$.
Taking the complement gives:

```math
\mu_\ell=1-(1-\mu_b)^\ell \tag{CFG.1}
```

```yaml
mu_b: 0.00003
loci:
  - locus_id: 1
    length: 10       # mu ≈ 0.0003 here
  - locus_id: 2
    length: 8000      # mu ≈ 0.216 here — the same mu_b, a much higher mu
```

This uses the full probability relation, not the linear approximation
$\ell\mu_b$. At load time, `mu_b` expands to a per-locus `mu` list.
`to_dict()` and `manifest.json` record that list, not `mu_b`.

By default, every mutation produces a globally novel allele identity; see
[mutation_model](#mutation_model) below for the opt-in alternative.

### `seed`

- **Type:** non-negative integer
- **Required:** yes; there is no default
- **Meaning:** seed for the run's NumPy `PCG64` generator

Negative seeds are rejected; there is no upper bound.

## Loci

### `loci`

- **Type:** nonempty list of mappings
- **Default:** one locus with locus_id: 1 and `length: 200`

Each entry has a positive `length` and an optional positive locus_id that
defaults to its one-based position. IDs must be unique.

As an alternative to `loci`, use:

- `n_loci` — positive locus count, default `1`;
- locus_lengths — one positive integer shared by all loci, or exactly
  `n_loci` integers, default `200`.

Do not combine the two forms. Differentiation statistics never read
`length` directly; it acts only through the mutation model, below.

Per-locus length varies freely — nothing requires every locus to share
one value.

```yaml
loci:
  - locus_id: 1
    length: 50      # a short marker
  - locus_id: 2
    length: 8000    # a much longer one
```

The equivalent compact form:

```yaml
n_loci: 2
locus_lengths: [50, 8000]
```

## Mutation model

### mutation_model

- **Type:** infinite_alleles or finite_alleles
- **Default:** infinite_alleles

Controls what a mutation event turns an allele *into*, independently of
`mu` (which controls how *often* one happens).

- infinite_alleles (the default): each mutation produces a globally unique
  label. This approximates mutation at a locus that
  spans many base pairs — see
  [the differentiation-measures guide](jost-differentiation-measures.md#distance-between-alleles-is-a-different-model) —
  but increasingly unrealistic for a short locus, where the same state
  can plausibly arise more than once by chance (a *recurrence*).
- finite_alleles: each locus gets a bounded state space of exactly
  $4^\ell$ possible states, where $\ell$ is its `length`, corresponding to four bases per sequence
  position. A mutation target is drawn uniformly from the other
  `capacity - 1` states — never its current state, but possibly one
  already present elsewhere in the run. This still imposes no ordering or
  distance between alleles; it only gives the label space a ceiling. See
  [the simulator design, §3.2 and §9](fim-simulator-design.md#32-alleles-loci-and-identity)
  for the assumptions and the distinction from a stepwise (microsatellite)
  mutation model, which is not implemented.

```yaml
mutation_model: finite_alleles
loci:
  - locus_id: 1
    length: 1     # capacity 4 — recurrence becomes likely quickly
```

finite_alleles interacts with `length`, initial_allele_count, and
p_0: every locus's starting allele IDs — the founding range
0 .. initial_allele_count - 1, or an explicit p_0's specific IDs —
must fit inside that locus's $4^{\ell}$ capacity, checked
independently per locus. The default `initial_allele_count: 2` fits any
positive length; explicit `p_0` IDs must also satisfy the capacity bound.

## Initial conditions

Generation 0 — whether drawn from initial_concentration or supplied
explicitly via p_0 — is a continuous prior, not a state the run's `N`
gene copies could themselves produce; only generation 1 onward, once
`drift` has resampled at `N` gene copies, lands on that discrete
`1/N` lattice. See the [model contract](../README.md#model-contract)
and [simulator design §3.3](fim-simulator-design.md#33-initial-conditions).

### initial_allele_count

- **Type:** positive integer no larger than the smallest `N`
- **Default:** `2`

Founding IDs are locus-relative `0` through initial_allele_count - 1.

### initial_concentration

- **Type:** positive number
- **Default:** `1.0`

The random default draws each deme/locus frequency vector independently from a
symmetric Dirichlet distribution. Smaller values are more uneven.

### p_0

- **Type:** optional nested list: `d` demes, each containing one mapping per
  configured locus
- **Default:** absent

Allele keys are integer IDs and each mapping must sum to 1. When present,
p_0 is used verbatim instead of a Dirichlet draw. Newly mutated allele IDs
are allocated above both the reserved mutation range and the highest supplied
ID, so they cannot collide with explicit labels.

```yaml
p_0:
  - - 0: 0.75
      1: 0.25
  - - 0: 0.10
      1: 0.90
```

### equilibrium_convergence_window, equilibrium_convergence_tolerance, equilibrium_max_generations

- **Type:** integer of at least 2; number greater than 0; positive integer
- **Default:** absent (all three)

Together these select the **equilibrium-split** starting condition instead
of the Dirichlet draw above. Set all three, or none of them: a partial
configuration is rejected.

Equilibrium-split derives starting frequencies from a simulated ancestral
population. First, one population holds the sum of all demes' gene-copy
counts and undergoes drift and mutation without migration. After its
model-derived equilibration period, it splits into `d` demes by sampling
without replacement, allocating each deme its configured gene-copy count.
This sampling introduces founder variation at generation 0.

The first-phase duration is derived from the model, not detected from a
flat trajectory. At equilibrium, population diversity
($H_S$, which equals $H_T$ when there is only one deme)
fluctuates around its expectation. The model describes the rate at which
dependence on the initial state decays. With $N_{\mathrm{anc}}$ ancestral
gene copies and mutation rate $\mu$, its approximate relaxation time is:

```math
\tau_{\mathrm{anc}}\approx\frac{1}{2\mu+1/N_{\mathrm{anc}}} \tag{CFG.2}
```

This is the same idea as [Convergence defaults](convergence.md), for one deme. The first phase
runs until the population's expected diversity, from any starting frequencies,
is within your tolerance of its equilibrium value. Define:

```math
\theta=2N_{\mathrm{anc}}\mu \tag{CFG.3}
```

The approximate equilibrium diversity is:

```math
H_{\mathrm{eq}}\approx\frac{\theta}{1+\theta} \tag{CFG.4}
```

For tolerance $\varepsilon$, reaching it takes about $\ln(1/\varepsilon)$
relaxation times. With several loci, the locus with the smallest $\mu$ decides.

The three keys control only the first phase:

- **equilibrium_convergence_tolerance** — how close to equilibrium the
  ancestral population must be, in $H_S$ units: the largest
  expected difference between its diversity and the equilibrium value. It
  sets how long the first phase runs; 0.01 means about 4.6 relaxation times,
  0.05 about 3. It must be greater than 0.
- **equilibrium_convergence_window** — the fewest generations the first phase
  runs, even if the derived duration is shorter. It is
  separate from the main run's burn-in, because this phase runs at a different
  population size than the main run. It cannot exceed
  equilibrium_max_generations.
- **equilibrium_max_generations** — the safety limit on the first phase.

Unlike the main `max_generations` cap, an ancestral duration exceeding
`equilibrium_max_generations` is an error. The run stops before the phase
begins and reports the required generation count.

For example, three demes of 200 haploid individuals ($N_{\mathrm{anc}}=600$)
with $\mu=0.001$
relax in about 273 generations, so a tolerance of 0.01 needs 1,256
generations, and the example below (tolerance 0.005) needs 1,445.

The run saves every generation of the first phase in
[`equilibrium_trajectory.tlog`](usage.md#equilibrium_trajectorytlog),
beside `trajectory.tlog`. Its generations are numbered from 0 within the
first phase, separately from the main run's, and the manifest records how
many there were (`equilibrium_generation_count`) and the diversity at the
split (`equilibrium_final_heterozygosity`).

Equilibrium-split cannot be combined with an explicit p_0 — a run cannot both
fix its starting frequencies and derive them — and it uses a separate random
number stream, derived from `seed`, so the same `seed` always reproduces the
same founding populations.

```yaml
equilibrium_convergence_window: 50
equilibrium_convergence_tolerance: 0.005
equilibrium_max_generations: 5000
```

## Convergence

### convergence_statistic

- **Type:** the name of any global statistic, or a list of several of them
  (the statistics the app lists under "Statistics shown", except the
  per-pair ones). Identity statistics: `D`, `G_ST`, `H_S`, `H_T`, `H_ST`,
  `Gs`, `Gd`, `D_m`, `R_ST`, `G_ST_NEI_LOG`, `G_ST_HEDRICK`, `F_ST` and the
  all-demes Nei identities and distances (`NEI_I_ALL_GEO`, `NEI_D_ALL_ARITH`,
  and so on). Allele-spectrum statistics: `E_ST`, `K_ST`, `A_CGD`, `Delta`
  (Gregorius's $\delta_G$), `MI` (Sherwin mutual information).
- **Default:** `[D, G_ST]`

By default a run watches both and stops when both have settled
(convergence_combinator `all`): $D$ is the statistic most studies report, and
$G_{ST}$ is the one that is slowest and noisiest at low diversity, so a run that
has settled on $D$ alone can still be imprecise on $G_{ST}$. Name one statistic
(`convergence_statistic: D`) to judge the run on it alone.

Every watched statistic is computed every generation, whatever the display
settings say. The allele-spectrum statistics are expensive (a pass over every
allele per locus per generation) and are not covered by the relaxation time,
which is derived for the identity statistics: they depend on allele counts and
rare alleles, which can relax more slowly. The Expert Setting
`spectrum_burn_in_multiplier` lengthens the burn-in when one is watched; it is 1
until the difference is measured. Only $D$ and $G_{ST}$ have both expected-value
forms (convergence_estimate); every other statistic is the mean of values.

A statistic with an open end ($A_{\mathrm{CGD}}$, $\delta_G$, $I$, $D_m$,
$R_{ST}$ and the Nei
family) cannot share one absolute precision with the proportions, so its target
is relative: `precision * max(1, |window mean|)`, a relative precision with an
absolute floor. statistic_precision overrides it.

A list watches several statistics at once — each keeps an independent
history, judged over the same burn-in and evidence window against the same
precision — combined by convergence_combinator. A name may
not repeat.

```yaml
convergence_statistic: [D, G_ST]
convergence_combinator: any   # stop once either statistic settles
```

### statistic_precision

- **Type:** mapping of a watched statistic's name to its precision
- **Default:** none (every statistic uses `precision`, or the relative
  precision above)

Gives one watched statistic a precision target in that statistic's
units, replacing `precision` for it. The name must be a watched statistic and
the value non-negative.

In the app this is the optional "per-statistic precision" field under the
convergence statistics on Configure, written `A_CGD=0.5, D=0.02`.

```yaml
convergence_statistic: [D, A_CGD]
precision: 0.01
statistic_precision:
  A_CGD: 0.5            # known to within half an allele
```

### convergence_combinator

- **Type:** `all` or `any`
- **Default:** `all`

Only meaningful when convergence_statistic is a list: `all` requires every
watched statistic to be simultaneously stable before stopping (a strict
criterion); `any` stops as soon as one settles. With a single watched
statistic, the two settings are equivalent. The default watched set
contains `D` and `G_ST`.

Under `any`, the statistics that did not trigger the stop may still be
imprecise when the run ends: their reported window means are not converged
estimates. `report.json`'s
`converged_on` names the statistics that had settled. Use `all` when
every watched statistic's value matters.

### convergence_burn_in

- **Type:** positive integer, or `auto`
- **Default:** `auto`

The generations discarded before averaging starts: the time the population
needs to forget where it started. Nothing can stop a run before its burn-in
ends (except the cap), and generation 0 is never averaged.

**`auto` (the default) derives the burn-in from the model.** How long a run
must be watched depends on how fast the population forgets its starting
state, its *relaxation time* `tau`:

```text
T   = N_total + (d - 1) / (2 m)      # mean time for two gene copies to coalesce
tau = 1 / (2 mu + 1 / T)             # mutation is a second way to lose identity
burn_in         = ceil(k tau), k = max(5, ln(2 / precision))
max_generations = max(200000, burn_in + ceil(15 tau))
```

`N_total` is the sum of every deme's gene copies and `mu` is the smallest
mutation rate over loci (the slowest locus sets the time). That closed form is
for the symmetric island model (scalar `m`, equal deme sizes). An explicit
migration matrix or unequal sizes use the slowest mode of the identity
recursion instead, computed for up to 24 demes. Above that, or with no
migration and no mutation, there is no `tau`: `auto` then discards the first
tenth of the run at each check, and a derived `max_generations` is refused, so
give it explicitly.

For example, five demes of 100 gene copies with `m: 0.0001` and `mu: 0.000001`
have `tau` of about 19,700 generations, so at the default precision the burn-in
is about 104,000 generations and the cap about 399,000. A run ending after
100 generations cannot provide an equilibrium estimate for that configuration.

Choose a positive integer to override. Too short a burn-in starts averaging
inside the transient and biases the average. Why the formula has this form,
and how its numbers were chosen, is in [Convergence](convergence.md).

```yaml
convergence_burn_in: 5000
```

### convergence_estimate

Which expected value a run estimates for $D$ and $G_{ST}$, the two statistics
that are functions of the heterozygosities $H_S$ and $H_T$:

| Value | Estimate | Use it for |
|---|---|---|
| `mean_of_values` (default) | The average of statistic values over the evidence window | Comparing with published replicate means |
| `value_of_means` | The statistic computed from the window's averaged $H_S$ and $H_T$ | Checking the simulator against the closed form |
| `auto` | `value_of_means` when the statistic is undefined in any window generation, or its denominator is tiny in too many of them; otherwise `mean_of_values` | Runs near fixation |

The two differ by a small, systematic amount (the average of a ratio is not
the ratio of the averages); one locus at the reference scale shows 0.011 for
$D$ and 0.0001 for $G_{ST}$. The choice drives the stopping decision, the headline
number and the standard error. The report carries both forms for `D` and
`G_ST` under `window_statistics`, with `selected_form` and
`undefined_generations`. `auto` is judged at each check, so the form reported
at the stop is the form the stop was judged on; it does not default because
the headline would then change meaning between points of a sweep. The `auto`
thresholds are the Expert Settings `estimate_auto_denominator` and
`estimate_auto_fraction`.

In the app this is a Settings default (Settings, Convergence: "expected
value"); a loaded configuration retains its value.

```yaml
convergence_estimate: mean_of_values   # or value_of_means, or auto
```

### precision

- **Type:** non-negative finite number
- **Default:** `0.01`

How precisely to estimate each watched statistic: plus or minus this amount,
in the statistic's units, at the `confidence` level. The same target
applies to single runs and batches.

- A single run burns in and then averages each watched statistic over a
  growing window until the average's standard error is at most
  `precision / z` (`z` is 1.96 at 95% confidence) and the window holds at least
  50 effective independent values. The burn-in also lengthens as the precision
  tightens (see convergence_burn_in).
- A replicate batch stops early once every watched statistic's
  across-replicate confidence interval is this narrow (see
  stop_batch_early).

Smaller is more precise and takes longer.

```yaml
precision: 0.02
```

### track_expensive_statistics

- **Type:** boolean
- **Default:** `false`

Controls whether $E_{ST}$ / $K_{ST}$ / $A_{\mathrm{CGD}}$/Delta/MI are
computed every generation for display, even when none of them is being
watched for convergence. D, $G_{ST}$, $H_S$, and $H_T$
are always tracked and available for display regardless of this setting
and regardless of convergence_statistic — each is either the shared
$H_S$ / $H_T$ input every other statistic derives from, or an
O(1) step once those are known, so computing them costs nothing extra.
$E_{ST}$, $K_{ST}$, $A_{\mathrm{CGD}}$, Delta, and MI each require
an additional pass over frequency tables. Enabling this setting computes
all five every generation for live plots and tables, increasing runtime.
The cost grows with deme and allele counts; omitting $E_{ST}$ and
$K_{ST}$ reduced convergence-check time by about 38% in a
many-allele reference configuration.

Statistics named in `convergence_statistic` are always computed, regardless
of this setting. Leave it `false` unless supplementary live histories are
needed. See the [statistics reference](statistics.md) for definitions.

```yaml
track_expensive_statistics: true
```

In the desktop app there is no control for this key on Configure. The app
sets it on every run, batch and sweep it starts, and in every preset or
YAML file it saves, from Settings' "Statistics shown": `true` while
$E_{ST}$, $K_{ST}$, $A_{\mathrm{CGD}}$, $\delta_G$ or I is
shown, `false` once all five are hidden. Showing any one of them computes
all five every generation, so runs take longer; the extra time is small
for a few demes and grows with the deme count (in one measurement, about a
fifth more at 100 demes and two thirds more at 500). None of the five is
shown on a fresh install. A value in a loaded YAML file, preset or reopened
run is replaced by the Settings choice when the app runs it; `fim run`
and `fim sweep` use the file's value.

### max_generations

- **Type:** positive integer, or `auto`
- **Default:** `auto`

This safety cap always ends a run. Reaching it is reported as a valid
outcome that did not reach the requested precision, with the average and error
bar the run has. `auto` derives it as `max(200000, burn_in + ceil(15 tau))`, at
most 10,000,000 (at least 200,000, so a single locus has room to average its
variation down to the configured precision — see [How precise is the reported
value?](convergence.md#how-precise-is-the-reported-value)); see
[convergence_burn_in](#convergence_burn_in). A cap at or below the burn-in ends
the run before any averaging, and the run logs a warning. Setting only a
burn-in raises the derived cap to leave `15 tau` after it.

### history_format

- **Type:** `exact` or `compact`
- **Default:** `exact`

A run keeps, for every generation, the value of each statistic (`D`, `G_ST`,
the heterozygosities): the statistic history that the app draws as the
trajectory graph and that a sweep's trajectory view averages across replicates.
`exact` writes it as `convergence.jsonl`, every value at full precision, about
70 bytes a generation. `compact` writes `convergence.fhist` instead: each value
is rounded to a fixed step of about one hundredth of `precision` (a power of
two, so a stored value is exact in binary), and the differences between
successive generations are packed to the narrowest integer that holds them and
compressed. A real 355,000-generation replicate takes 0.3 MB in the compact
form and 25 MB in the exact one. At the default precision (0.01) a value is
within 0.00002 of the one computed, far inside the interval the run is asked
to resolve. `report.json`, `summary.json` and every reported number are
computed inside the run and are never rounded. The setting is outside a run's
identity and is recorded in `manifest.json` only for a compact run. Sweeps
write the compact form unless asked otherwise.

### trajectory_retention, trajectory_stride, trajectory_thinning_start, trajectory_keep_last, trajectory_keep_burn_in

- **trajectory_retention:** `full`, `thinned`, `tapered` or `statistics`; default `full`
- **trajectory_stride:** integer at least 1; default `10`
- **trajectory_thinning_start:** positive integer, or `auto`; default `auto`
- **trajectory_keep_last:** integer at least 1; default `1000`
- **trajectory_keep_burn_in:** `true` or `false`; default `false`

A long run's trajectory file (the per-generation allele frequencies) is the
largest thing it writes: about 27 KB a generation for the biggest examples. The
default keeps every generation. `thinned` keeps generation 0, every generation
before `trajectory_thinning_start`, then one generation in every
`trajectory_stride` counted from the start, plus the last burn-in generation
and the final generation. `auto` starts thinning at the later of 100,000
generations (the Expert Setting `thinning_minimum_start`) and the burn-in plus
two relaxation times (`thinning_transient_relaxation_times`), so a shorter run
keeps everything even with thinning on, and the transient is never thinned.

Only the trajectory file is thinned. The per-generation statistics
(`convergence.jsonl`), the graph and its bands, and every number in the report
are unaffected. Which generations are kept depends only on the generation
number, so a rerun keeps the same frames and the replicates of a batch align.
The scrubber and animation, which show at most a hundred frames, snap to the
nearest kept generation; re-analysis (`fim stats`, opening a run) works at the
kept generations and says so for a skipped one. The settings are recorded in
`manifest.json` only for a thinned run. In the app they are Settings, Storage.

`tapered` keeps the end of a run in full and thins the past further the older
it is: the last `trajectory_keep_last` generations, then every second
generation for the next stretch of that length, every fourth for the next, and
so on, so the number of frames grows with the logarithm of the run's length
instead of the length itself (about 12,000 frames for a run of 2.6 million
generations, against 295,000 for `thinned` at the shipped stride). Generation 0
and the last burn-in generation are kept; the other burn-in frames are dropped
unless `trajectory_keep_burn_in` is `true`, which tapers them like the rest.
An equilibrium run is stationary, so a scatter plot or a scrubber looks at the
end; use `keep_burn_in` for a transient study. The rule needs the run's end, so
the log is written whole while the run goes and cut down the moment the
replicate stops (a single run: when it is published): the disk needed while a
batch runs is the full log of the replicates still running, and a replicate
that has stopped is already small. What is kept depends only on the generation
number and the run's end, so a rerun keeps the same frames and the replicates of
equal length align. The settings are recorded in `manifest.json` only for a
tapered run.

```yaml
trajectory_retention: tapered
trajectory_keep_last: 2000
```

`statistics` keeps no trajectory log at all, the floor of the ladder for a
sweep where only the numbers matter. The finished run holds `report.json` (or a
batch's `summary.json`), `scatter.png`, `pairwise.json`, and `history.json`: the
statistic history at full precision for at most 2,048 generations (generation 0,
then closely spaced early generations and evenly spaced later ones). The app
opens such a run as a report with no per-generation trajectory; a batch still
shows its pooled convergence curve, drawn from the replicates' `history.json`
files on one shared generation grid. Re-analysis and the scrubber say that the
run kept statistics only. Every reported number is the same as in
a `full` run of the same seed. While a batch runs, a replicate that has stopped
keeps only its final frame; the full log of each replicate still running is
needed until it stops.

A sweep reuses a point only if the run it already has kept at least what the
sweep asks: a `full` run serves every level, a tapered run serves a tapered
request with a smaller `trajectory_keep_last` and `statistics`, a `thinned`
run serves the same thinning or a coarser one, and an exact history serves a
compact request but not the reverse. Otherwise the point is computed again and
compared with the old run on the artifacts both kept the same way.

```yaml
trajectory_retention: thinned
trajectory_stride: 20
```

### expert

- **Type:** mapping of Expert Setting names to values
- **Default:** empty (every Expert Setting at its default)

Overrides for convergence and storage policy constants. The defaults and
their calibration are documented in [Convergence](convergence.md).
Only nondefault values are written to `manifest.json`.
Convergence-policy overrides affect run identity; storage-policy overrides
do not. See [run identity](python-api.md#what-decides-a-runs-identity).

| Setting | Default | Range | Controls |
|---|---|---|---|
| `burn_in_minimum_relaxation_times` | `5` | at least 1 | Floor $k_{\min}$ of the burn-in multiple $k=\max(k_{\min},\ln(2/\varepsilon))$, where $\varepsilon$ is `precision` |
| `first_check_relaxation_times` | `2` | greater than 0 | First check, in relaxation times after the burn-in |
| `first_check_minimum` | `50` | whole number, at least 3 | Fewest generations after the burn-in before the first check |
| `minimum_effective_sample_size` | `50` | at least 10 | Effective independent values a window needs before its standard error is trusted |
| `check_growth` | `2` | greater than 1 | Factor by which the window grows between checks |
| `fractional_burn_in` | `0.1` | between 0 and 1, exclusive | Share of the run discarded when there is no relaxation time |
| `cap_relaxation_multiple` | `15` | greater than 0 | Cap beyond the burn-in, in relaxation times |
| `cap_minimum` | `200000` | whole number, at least 1 | Smallest derived `max_generations` |
| `cap_maximum` | `10000000` | whole number, at least `cap_minimum` | Largest derived `max_generations` |
| `start_drift_alert_z` | `3` | greater than 0 | Absolute start-of-window `z` above which the report says the burn-in may have been too short |
| `estimate_auto_denominator` | `0.01` | between 0 and 1, exclusive | Denominator ($H_T$ for `G_ST`, $1-H_S$ for `D`) below which a generation is degenerate for `convergence_estimate: auto` |
| `estimate_auto_fraction` | `0.01` | between 0 and 1, exclusive | Share of window generations that may be degenerate before `auto` uses the value of means |
| `batch_width` | `8` | whole number, at least 1 | Replicates assumed to run at once when `max_concurrent_replicates` is unset: the width of the first wave a batch matches its windows from |
| `spectrum_burn_in_multiplier` | `1` | at least 1 | Factor on the burn-in when an allele-spectrum statistic (`E_ST`, `K_ST`, `A_CGD`, `Delta`, `MI`) is watched |
| `thinning_transient_relaxation_times` | `2` | at least 0 | With thinning on, the run is kept in full until this many relaxation times after the burn-in |
| `thinning_minimum_start` | `100000` | whole number, at least 1 | Thinning never starts before this generation, so shorter runs keep every generation |
| `log_key_every` | `256` | whole number, at least 1 | Generations between keyframes in the trajectory log: smaller makes the log larger and seeking faster |
| `log_sync_seconds` | `2` | greater than 0 | Seconds between the trajectory log's disk syncs: the most a power loss can take |
| `log_block_generations` | `512` | whole number, at least 1 | Generations per trajectory-log block: larger writes more efficiently, smaller loses less when a run is cut off |
| `replicate_wave_multiple` | `2` | greater than 0 | Replicate waves a batch aims for: the matched window is sized for `max(replicate_minimum, multiple * width)` replicates |
| `averaging_multiple_minimum` | `5` | greater than 0 | Shortest matched replicate window, in relaxation times |
| `averaging_multiple_maximum` | `100` | at least `averaging_multiple_minimum` | Longest matched replicate window, in relaxation times |
| `first_wave_averaging_multiple` | `20` | greater than 0 | Window of the first wave of replicates, in relaxation times |

```yaml
expert:
  minimum_effective_sample_size: 100   # a more cautious standard error
  check_growth: 1.5                    # check more often
```

An unknown name or a value outside its range is refused by name.

In the desktop app, Settings has a collapsed "Expert settings" section with one
row per setting: its value, its default and range, what it does, and a Reset
button; a changed row is marked. Save keeps them with the other run defaults,
so they apply to every new run; a loaded configuration keeps its `expert:`
mapping for its run, and the notice that lists how its run settings differ from
yours lists the expert ones too.

## Analysis and execution

### deme_weighting

- **Type:** `size` or `equal`
- **Default:** `equal`

This setting controls $E_{ST}$, $\delta_G$ (`Delta`), and mutual
information (`MI`). Choose `size` to weight each deme by its gene-copy count;
`equal` gives every deme the same weight. The settings agree when all demes
have the same size.

Other reported measures use equal deme treatment, including
$H_S$, $H_T$, $G_{ST}$, Jost's `D`, and $K_{ST}$.
See the [statistics reference](statistics.md) for definitions and
implementation links.

### locus_aggregation

- **Type:** `ratio_of_means` or `mean_of_ratios`
- **Default:** `ratio_of_means`

Controls how `D` and $G_{ST}$ combine across loci in the final report,
when more than one locus is tracked. Every other reported statistic
($H_S$, $H_T$, $H_{ST}$, $E_{ST}$, $K_{ST}$) is unaffected — each is already a
linear mean across loci, with no such ambiguity to resolve.

- `ratio_of_means` (the default): average $H_S$ / $H_T$ across loci
  first, then compute one $D$ / $G_{ST}$ from those pooled values. This
  matches what the exact gene-identity recursion predicts and avoids a
  small-denominator instability a per-locus ratio can have.
- `mean_of_ratios`: compute $D$ / $G_{ST}$ at each locus independently,
  then average the ratios. Use this when matching an analysis or publication
  that applies this estimator. The mean of ratios generally differs from
  the ratio of means; see
  [differentiation measures](jost-differentiation-measures.md) for formulas.

The convergence monitor uses the same locus-aggregation setting for watched
$D$ / $G_{ST}$ as the final report.

```yaml
n_loci: 2
locus_lengths: [50, 500]
locus_aggregation: mean_of_ratios   # average per-locus ratios
```

### n_replicates

- **Type:** positive integer
- **Default:** `200`

n_replicates runs that many independently seeded scalar runs — replicate
`i` uses child `i` of `numpy.random.SeedSequence(seed)` — through both the library API
(`fim.engine.simulate_params`, returning one `RunResult` per replicate) and the CLI
(`fim run`, writing one `replicate-NNN/` subdirectory per replicate; see
[Using `fim`](usage.md#run-a-simulation)). This is a hard cap, not a
target. With `stop_batch_early` enabled (the default), a batch can stop
before this cap when its interval meets the requested precision.
Set `n_replicates: 1` for a single run; `stop_batch_early` has no effect
in that case.

### stop_batch_early

- **Type:** boolean
- **Default:** `true`

Early stopping for a replicate batch (n_replicates greater than one):
once at least replicate_minimum replicates have run, stop as soon as
every statistic named in convergence_statistic has an across-replicate
Student's-t confidence interval (mean of per-replicate window means
of that statistic, over the replicates so far) with a half-width at most
precision — combined
across several watched statistics by convergence_combinator, as for
within-run convergence. n_replicates is still the hard cap:
reaching it without tightening ends the batch anyway, a valid,
cap-limited result. See each statistic's achieved interval in
`summary.json` (CLI) or `fim.engine.replicate_summary` (library).

Replicates are considered in replicate order — replicate 1, then 2, then
3, and so on — on every engine_backend, never in the order they happen
to finish. A `generational` batch advances many replicates at once, and
one that converges quickly can finish before a lower-numbered one; it
waits until every lower-numbered replicate has finished before it
counts. How quickly a replicate converges is related to its
statistics, so selecting the first replicates to finish would bias the
result. Because of this rule, the same configuration and seed keep the
same replicates and write the same `summary.json` under `lineal` and
`generational`.

`false` disables the adaptive stop entirely — n_replicates then
always runs in full.

```yaml
n_replicates: 200          # hard cap
precision: 0.02            # stop once every watched statistic is this tight
replicate_minimum: 20
```

```yaml
n_replicates: 16
stop_batch_early: false    # always run all 16
```

### replicate_averaging_window

- **Type:** positive integer, or `auto`
- **Default:** `auto`

How many generations each replicate of a batch averages, after its burn-in.
A replicate of a batch burns in, averages for its window, and stops; it has
no within-replicate precision check, because the batch reaches precision across
replicates. Each replicate contributes its window mean to the interval, in
the form `convergence_estimate` selects.

`auto` matches the window to the batch. The first wave of replicates (as many
as run at once: `max_concurrent_replicates`, else the Expert Setting
`batch_width`, 8 by default) averages for `first_wave_averaging_multiple`
relaxation times, 20 by default. When the wave ends, its windows give each
watched statistic's standard deviation `sigma` and integrated autocorrelation
time `tau_int`. Write $\sigma$ and $\tau_{\mathrm{int}}$ for these estimates,
$\varepsilon$ for the requested `precision`, and $R$ for the number of
replicates the batch aims for (the larger of `replicate_minimum` and
`replicate_wave_multiple` times the wave width). The standard error needed
for one replicate is:

```math
\operatorname{SE}=\frac{\varepsilon\sqrt{R}}{t_{R-1}} \tag{CFG.5}
```

Here $t_{R-1}$ is the two-sided Student critical value for the configured
`confidence` and $R-1$ degrees of freedom. The estimated generation window
required to achieve that error is:

```math
W=\tau_{\mathrm{int}}\left(\frac{\sigma}{\operatorname{SE}}\right)^2 \tag{CFG.6}
```

The slowest watched
statistic decides, and the window is held between `averaging_multiple_minimum`
and `averaging_multiple_maximum` relaxation times (5 and 100). Every later
replicate uses it; replicates with different windows are pooled with equal
weight. A later replicate runs until the shortest window it could receive and
then waits for the wave to be measured, so the result never depends on which
backend ran it. The wave width is a number in the configuration, not the
machine's processor count, so the same configuration gives the same windows
and the same results everywhere.

A positive number is used by every replicate and nothing is measured. A model
with no relaxation time (and so no burn-in) keeps the within-run rule in every
replicate.

A single run (`n_replicates: 1`) ignores `auto` and judges itself by the
within-run rule. A single run with a positive number is a *fixed-window run*:
it burns in, averages that many generations, and stops, with no precision
check. Each replicate records its window in its parameters, so
the replicate's manifest reproduces it exactly.

```yaml
replicate_averaging_window: auto     # match the window to the batch
replicate_averaging_window: 20000    # or: every replicate averages 20,000
```

### precision_method

- **Type:** `interval` or `planned_replicates`
- **Default:** `interval`

How a batch reaches its precision. `interval` adds replicates until the
interval across them is plus or minus `precision` (with stop_batch_early on),
each replicate averaging for a window matched to the batch. `planned_replicates`
runs exactly n_replicates, with no early stop, and sizes every
replicate's window so that their interval is plus or minus `precision`: you
choose the number of replicates, the engine chooses how long each one averages.
It needs n_replicates of at least 2.

In the app, precision_method and replicate_averaging_window are Settings
defaults (Settings, Convergence); a loaded configuration retains its values.

```yaml
n_replicates: 16
precision_method: planned_replicates   # 16 replicates, each long enough for +/- precision
```

### replicate_minimum

- **Type:** integer at least 2
- **Default:** `10`

Minimum replicates before an early-stop check. This reduces the risk of
stopping on an artificially narrow interval from a small sample.
Applies only when `stop_batch_early` is enabled. Values above
`n_replicates` are capped at `n_replicates`.

### confidence

- **Type:** `0.90`, `0.95`, or `0.99`
- **Default:** `0.95`

Two-tailed confidence level used for precision targets and the intervals in
`summary.json` and `replicate_summary`.

### migrant_sampling

- **Type:** `continuous` or `stochastic`
- **Default:** `continuous`

Controls how many gene copies migrate each generation, independently of
which `m` shape is configured above.

- `continuous` (the default): each deme's migrant count is exactly
  $N_i r_i$, where $N_i$ is its gene-copy count and $r_i$ its migrant fraction
  (or, for a matrix row, $N_i$ times that row's non-self weight) — a fixed
  fraction, not a random draw.
- `stochastic`: each deme's migrant count is instead drawn fresh every
  generation from $\mathrm{Binomial}(N_i,r_i)$ — mean $N_i r_i$, matching the
  continuous case in expectation, but varying generation to generation.
  Migrant *composition* is unaffected either way: migrants still carry
  exactly the deterministic, weighted pool average. Requires a concrete
  `N` (always true for a CLI run; a direct `fim.model.operators.migrate`
  call needs population_size). Every `engine_backend` runs it, with the
  same results: `generational-vector` draws migrant counts inside
  its compiled step, in the same order as `lineal`, so a stochastic run
  matches the other two exactly for the same seed.

```yaml
migrant_sampling: stochastic
```

This is the finite island model variant described in
[the finite island model introduction, §3.2](finite-island-model-introduction.md#32-one-generation-in-two-steps):
sampling migrant counts instead of treating migration as an
idealized continuous fraction. It adds one random process to the pipeline
(how many migrate) without duplicating the one already there (`drift`
still resamples every gene copy exactly once per generation) — see
[the simulator design, §9](fim-simulator-design.md#9-extensibility-where-the-next-what-if-lands)
for why the two don't compound.

## Engine backend and JIT

Backend, JIT, and automatic-selection thresholds control execution without
changing simulated results on the same platform. Resource use and runtime
can differ. `max_concurrent_replicates` also determines the first-wave
width of adaptive batches and can therefore affect their averaging windows
and results; see its entry below.

### engine_backend

- **Type:** `lineal`, `generational`, `generational-vector`, or `auto`
- **Default:** `lineal`

Selects the engine implementation:

- `lineal` (the default): the reference
  implementation. Replicates run sequentially or in separate worker
  processes; see
  [n_replicates](#n_replicates).
- `generational`: the same computation, restructured to advance every
  replicate one generation at a time together, spread across threads
  instead of processes. Produces bit-for-bit identical results to
  `lineal` for the same inputs.
- `generational-vector`: array-based state and one compiled step per
  generation. It runs both mutation models (`infinite_alleles` and
  `finite_alleles`), a scalar or full-matrix `m`, and either
  migrant_sampling setting. Requires the optional `numba` dependency;
  install `fim[jit]` to add it. For the same seed on the same machine,
  it matches `lineal` and `generational`: every trajectory row
  (allele identity and frequency, bit for bit), the report, and the final
  state are identical, under both mutation models, with or without
  migration, for any number of loci. (The compiled arithmetic is checked
  against Python's only on the development platform, so across machines
  results agree statistically, as they already do for `jit: numba`.) It
  keeps one table of `loci x demes x alleles` numbers per replicate in
  memory; a configuration whose table cannot fit — a very high
  mutation rate with many demes and loci, or a finite-alleles locus
  that is very long — stops at the start with a message that names the
  remedies. The ceiling is 2 GiB per replicate, and the
  `FIM_VECTOR_MEMORY_CEILING_BYTES` environment variable (a number of
  bytes) raises or lowers it. With several replicates running at once
  each holds a table; `max_concurrent_replicates` bounds how many.
- `auto`: picks `generational` or `generational-vector` automatically —
  never `lineal`. It picks `generational-vector` when
  `jit` is `off`, `numba` is installed, `d` is at least
  `auto_vector_min_d` and, under `mutation_model: finite_alleles` only,
  every locus fits under `auto_vector_max_capacity` (both below).
  Otherwise it picks `generational`. Because both give identical
  results, the choice changes how fast a run finishes, nothing else, and
  a computer without `numba` uses `generational` instead of an
  error.

Performance depends on deme count, locus capacity, and hardware.
The vector engine measured 30–300 times faster than `lineal` on the
infinite-alleles worked examples. See the
[backend selection guide](fim-simulator-design.md#46-choosing-an-engine-backend)
and [benchmark results](fim-engine-backend-benchmarks.md) for workloads,
measurements, and limits.

```yaml
engine_backend: generational-vector
mutation_model: infinite_alleles
migrant_sampling: continuous
```

**In the desktop app:** **Execution engine** in Settings supplies the
default for new configurations; its initial value is `auto`.
**Run settings...** on Configure changes the backend for the pending run.
Loading a configuration uses its backend without changing saved Settings.
The manifest records the resolved backend, not the literal `auto`.

**In `fim init`'s starter configuration:** `engine_backend: auto` is
explicit. Set it to `lineal`, or omit it, to use the reference backend.

If the app or checkout you are using does not have the optional `numba`
dependency installed, a direct `generational-vector` request reports that
it is missing. `auto` falls back to `generational`.
Packaged beta downloads include `numba`.

`jit`, `auto_vector_min_d`, and `auto_vector_max_capacity` also have
Settings fields. `jit` appears when the
Settings execution engine is `generational` (`lineal`
accepts only `off`; `generational-vector` always requires
`numba` regardless of this setting). `auto_vector_min_d` and
`auto_vector_max_capacity` appear only when it is set to `auto`, the engine
either threshold affects.

### jit

- **Type:** `off` or `numba`
- **Default:** `off`

Whether the chosen engine_backend should JIT-compile
random draws using the optional `numba` dependency
`generational-vector` always needs. Meaningful only under
`engine_backend: generational` (speeds up part of that engine's
work, although marshaling elsewhere can dominate runtime); `lineal` accepts
only `off`; `generational-vector` always requires `numba`
regardless of this setting and accepts only `off` here.
Under `engine_backend: auto`, `jit: numba` selects `generational` (the
one engine that offers the toggle) rather than `generational-vector`.

### auto_vector_min_d

- **Type:** integer at least 1
- **Default:** `2`

The deme-count threshold `engine_backend: auto` uses to choose
`generational-vector` over `generational`. Ignored under every other
`engine_backend` value. The default covers all supported deme counts.
In a 104-point finite-alleles grid, the vector engine won at every tested
`d` within the default capacity ceiling. Infinite-alleles measurements
found a 5–300-fold advantage over `generational` from `d = 2`.
See the [backend selection guide](fim-simulator-design.md#46-choosing-an-engine-backend)
for measurements, including regions above the capacity ceiling, and
[`benchmark-engines`](../dev/bin/README.md#benchmark-engines) to recalibrate
on the target hardware.

### auto_vector_max_capacity

- **Type:** integer at least 1
- **Default:** `4096`

The per-locus capacity ceiling `engine_backend: auto` uses alongside
`auto_vector_min_d`, **under `mutation_model: finite_alleles` only** —
`generational-vector` is chosen only when `d` meets its threshold
and every locus's capacity ($4^{\ell}$) is at most this
value; a single locus above it falls back to `generational` regardless
of `d`. A finite-alleles table is `capacity` columns wide however few
states are in use, which is why it needs a ceiling; an infinite-alleles
table is as wide as the alleles alive at once, so `auto` does not apply
this ceiling there. Ignored under every other `engine_backend` value.
Measured alongside `auto_vector_min_d` on the joint grid: the
largest capacity at which `generational-vector` won at every tested
`d` — the same `dev/bin/benchmark-engines` maintainer tool re-measures
this axis too (`--sweep loci-length`).

### max_concurrent_replicates

- **Type:** integer at least 1, or omitted
- **Default:** unset — resolves to this machine's CPU count under
  `generational`, or 8 (the batch-width design assumes, below) under
  `generational-vector`, never to every requested replicate at once

Caps active replicate lanes for `generational` and `generational-vector`.
With a cap, lanes are built as slots become available. When unset, the
resolved default above is used instead; `generational-vector` has no
way to run more than one lane's own work at a time regardless (its
`advance` steps every active lane in a single loop), so a wider window
there buys no throughput, only memory — see the maintainer design
`20261022-claude-sonnet-5-replicate-concurrency-unification-design.md`
(`selby/restricted`) for the full reasoning. `--workers`/`--sequential`
(not a `SimulationParams` field — see
[usage.md](usage.md#run-a-simulation)) is the `lineal` backend's own,
separate concurrency control: it uses worker processes rather than
this lane window, with its own, independently-resolved default
(this machine's CPU count).

This matters most under `generational-vector`: that backend keeps a
dense, per-locus array cached for every currently active replicate at
once, so an unbounded batch's memory use scales directly with
`n_replicates` — a run that comfortably fits at `n_replicates: 10` can
run out of memory at `n_replicates: 200` on the identical configuration
otherwise. Setting `max_concurrent_replicates` to a small number (`4`,
say) bounds that memory use to a small, fixed multiple regardless of
the total requested replicate count. Values above `n_replicates` are
capped at `n_replicates`.

```yaml
n_replicates: 200
engine_backend: generational-vector
max_concurrent_replicates: 4
mutation_model: infinite_alleles
migrant_sampling: continuous
```

For adaptive averaging, this value also determines the first-wave width
used to size later replicate windows. It can therefore change results and
is part of run identity. Unlike backend, JIT, and storage settings, it is
not solely an execution choice. See
[`replicate_averaging_window`](#replicate_averaging_window) and
[Python run identity](python-api.md#what-decides-a-runs-identity).

**On the command line:** `fim run --max-concurrent-replicates N`
overrides this field for one invocation, without editing the config
file — see [usage.md](usage.md#run-a-simulation). **In the desktop
app:** a "max concurrent replicates (blank = unset)" field sits beside
"max workers" in the Settings dialog, alongside this section's other
execution-flavored defaults.

## Run labels

A label describes a run without being part of it. The three label keys,
`name`, `description`, and `class`, are all optional. They are **not** part
of the run's ID: labels are excluded from the identity mapping, so changing a
run's name, description, or class never turns it into a different run.
The labels are kept in the run's `metadata.json` file, beside
`manifest.json`, never in the manifest's parameters.

`fim run` checks the labels before the run starts, and writes them to
`metadata.json` when the run finishes. If the run directory already has a
`metadata.json`, that file is kept as it is: a name given to the run later
wins over the one in the configuration. `fim run --name` and
`--description` override the configuration's `name` and `description` for
that one run.

```yaml
name: Ring of eight islands
description: >
  Eight demes in a ring, to see how far D falls below the
  island-model value when migrants only reach their neighbors.
class: migration
```

### `name`

- **Type:** text, or `null`
- **Required:** no
- **Meaning:** a short name for the run, shown wherever the run is listed

### `description`

- **Type:** text, or `null`
- **Required:** no
- **Meaning:** a longer description, shown with the run's details

Surrounding white space is removed, so a YAML block scalar (`>` or `|`)
works. Blank text is rejected; leave the key out instead.

### `class`

- **Type:** a class ID, or `null`
- **Required:** no
- **Meaning:** the group the run belongs to, such as `getting-started`

Classes are defined once, in `doc/examples/classes.yaml`; the worked
examples use them to build the Examples dialog's tree. A class ID is
lowercase words joined by hyphens. An ID that is not listed in that file is
rejected. A packaged copy of `fim` does not include `doc/`, so there it
checks only that the ID is lowercase words joined by hyphens, and logs a
warning.

## Internal attributes

A top-level key that starts with an underscore (`_`) is an internal
attribute. Internal attributes are written by `fim` itself for the runs it
ships; you do not normally set one. Only the attributes documented here are
accepted. Any other key that starts with `_` is rejected as an unknown key,
so a typo is reported instead of ignored.

Unlike a label, an internal attribute **is** part of the run's ID.

### `_read_only`

- **Type:** boolean
- **Default:** `false`
- **Meaning:** the run is a shipped example that cannot be edited

A read-only run cannot be renamed, described, given another class, deleted,
or removed from a study. You can still open it, reanalyze it, and copy its
configuration. Each worked example's `config.yaml` carries
`_read_only: true`.

Because `_read_only: true` is part of the run's ID, a shipped example and
an editable run of the same model have different identities. Loading an
example into Configure drops every `_` key, so the resulting run is an
ordinary run that you can edit. A configuration without `_read_only`, or
with `_read_only: false`, uses the ordinary editable-run identity.

## Validation summary

| Condition | Result |
|---|---|
| `N < 1`, `d < 2` | rejected |
| `ploidy` missing, a number, or not one of the four words | rejected, with the fix |
| `m` or `mu` outside `[0, 1]` | rejected |
| missing `seed`, or `seed < 0` | rejected |
| empty or duplicate loci | rejected |
| frequency vector not summing to 1 | rejected |
| unknown key | rejected by name |
| `name`, `description`, or `class` not text, or blank | rejected |
| `class` not lowercase words joined by hyphens, or not listed in `doc/examples/classes.yaml` | rejected |
| a key starting with `_` other than `_read_only` | rejected by name |
| `_read_only` not a boolean | rejected |
| matrix/list shape not matching `d` | rejected |
| unrecognized or repeated convergence_statistic entry | rejected |
| `m` sparse-map deme/neighbor id outside `[1..d]`, a self-loop, or weights summing past `1` | rejected |
| `m` topology mapping missing `topology`/`rate`, an unknown key, or an unrecognized topology name | rejected |
| `m` torus topology missing `rows`/`columns`, a side below `3`, `rows * columns` not equal to `d`, or `rows`/`columns` given with `ring`/`linear` | rejected |
| `m` ring topology with `d < 3` | rejected |
| migrant_sampling not `continuous` or `stochastic` | rejected |
| mutation_model not infinite_alleles or finite_alleles | rejected |
| finite_alleles with a locus's starting allele IDs exceeding its $4^{\ell}$ capacity | rejected |
| both `mu` and `mu_b` given, or neither | rejected |
| `mu` list length not matching the locus count | rejected |
| `mu_b` outside `[0, 1]` | rejected |
| precision negative or non-finite | rejected |
| statistic_precision naming an unwatched statistic, or a negative value | rejected |
| stop_batch_early not a boolean | rejected |
| trajectory_retention not `full`, `thinned`, `tapered` or `statistics` | rejected |
| history_format not `exact` or `compact` | rejected |
| trajectory_keep_last less than 1 or not a whole number | rejected |
| trajectory_keep_burn_in not a boolean | rejected |
| trajectory_stride less than 1 or not a whole number | rejected |
| trajectory_thinning_start not a positive integer or `auto` | rejected |
| precision_method not `interval` or `planned_replicates` | rejected |
| precision_method `planned_replicates` with n_replicates `1` | rejected |
| replicate_averaging_window not a positive integer or `auto` | rejected |
| replicate_averaging_window set for a model with no burn-in | rejected |
| replicate_minimum less than 2 | rejected |
| convergence_burn_in not a positive integer or `auto` | rejected |
| track_expensive_statistics not a boolean | rejected |
| replicate_minimum greater than n_replicates | silently capped at n_replicates |
| confidence not `0.90`, `0.95`, or `0.99` | rejected |
| deme_weighting not `size` or `equal` | rejected |
| locus_aggregation not `ratio_of_means` or `mean_of_ratios` | rejected |
| engine_backend not `lineal`, `generational`, `generational-vector`, or `auto` | rejected |
| jit not `off` or `numba` | rejected |
| auto_vector_min_d less than 1 | rejected |
| auto_vector_max_capacity less than 1 | rejected |
| jit: numba with engine_backend: lineal or generational-vector | rejected |
| engine_backend: generational-vector with either mutation_model, scalar or matrix `m`, either migrant_sampling | accepted |
| max_concurrent_replicates less than 1 | rejected |
| max_concurrent_replicates greater than n_replicates | silently capped at n_replicates |
| one or two of the three equilibrium\_ keys given instead of all three | rejected |
| any equilibrium\_ key given together with p_0 | rejected |
| equilibrium_convergence_window less than 2 | rejected |
| equilibrium_convergence_tolerance zero, negative or non-finite | rejected |
| equilibrium_max_generations less than 1 | rejected |
| equilibrium_convergence_window greater than equilibrium_max_generations | rejected |
| the ancestral phase needs more than equilibrium_max_generations generations | run fails before the phase starts (not a benign outcome) |
