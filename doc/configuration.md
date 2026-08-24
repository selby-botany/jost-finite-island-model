# `fim` configuration reference

`fim run` accepts one YAML mapping. Unknown keys, incompatible shapes, and
out-of-range values are errors. See [Using `fim`](usage.md) for commands and
the [project overview](../README.md) for installation.

## Contents

- [Complete example](#complete-example)
- [Required model keys](#required-model-keys)
- [Loci](#loci)
- [Mutation model](#mutation-model)
- [Initial conditions](#initial-conditions)
- [Convergence](#convergence)
- [Analysis and execution](#analysis-and-execution)
- [Validation summary](#validation-summary)

## Complete example

```yaml
N: 450
d: 20
m: 0.001
mu: 0.00003
seed: 20260814
loci:
  - locus_id: 1
    length: 200
initial_allele_count: 2
initial_concentration: 1.0
deme_weighting: size
convergence_statistic: D
convergence_combinator: all
convergence_window: 50
convergence_tolerance: 0.01
max_generations: 10000
n_replicates: 1
```

## Required model keys

### `N`

- **Type:** positive integer, or a list of `d` positive integers
- **Required:** yes
- **Meaning:** gene copies per deme

`N` is deliberately ploidy-neutral. Pass `2 * individuals` for a diploid
autosomal locus and pass census individuals unchanged for a haploid locus.
Per-deme lists model unequal island sizes: every stage of the update
pipeline (`migrate`, `mutate`, `drift`), the founding-allele-count bound
(checked against the smallest N<sub>i</sub>), and deme_weighting: size all use
each deme's own configured gene-copy count.

```yaml
N: [120, 450, 60]   # three demes, unequal gene-copy counts
d: 3
```

A scalar `N` and a list of `d` equal values are numerically identical;
prefer the scalar form when every deme is the same size.

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
`k`'s exact source weights, and row `k`'s weights need not equal row `j`'s.
Every row is independently configurable, including rows that are not
symmetric with any other row.

```yaml
m:
  - [0.90, 0.05, 0.05]   # deme 1 retains most of its own frequency
  - [0.10, 0.80, 0.10]   # deme 2 blends evenly with both neighbors
  - [0.00, 0.20, 0.80]   # deme 3 exchanges only with deme 2
d: 3
```

Two things worth being precise about, because both differ from the scalar
case:

- **A matrix's rows are the authoritative weights — they are not scaled by
  `N`.** The scalar path automatically derives a size-weighted migrant pool
  from each deme's own gene-copy count; a matrix already states each
  destination's exact source mix, so `N` (scalar or per-deme) has nothing
  left to contribute to migration and does not change the result. If unequal
  deme sizes should also drive migration weighting, build that weighting into
  the matrix itself. (This describes the default `migrant_sampling:
  continuous` blend. Under the opt-in migrant_sampling: stochastic, below,
  `N` re-enters the picture — not to change the mean, but to set how much a
  given generation's actual migrant count can vary around it.)
- **The scalar form is the matrix's symmetric special case, not a different
  mechanism.** For equal-size demes, a scalar rate `m` is numerically
  identical to the full matrix with `1 - m` on the diagonal and `m / (d - 1)`
  on every off-diagonal entry — prefer the scalar form when migration truly
  is symmetric and every deme is the same size; reach for a matrix only when
  it is not.

#### Sparse and spatial (stepping-stone) migration

Writing out a `d` by `d` matrix by hand stops being realistic once `d` grows
past a handful of demes, and it is actively the wrong shape for a spatial
topology, where almost every entry is `0` — each deme migrates only with a
couple of neighbors, not the whole population. Two compact alternatives to
the dense matrix cover this:

**A sparse neighbor map.** Give only the nonzero off-diagonal weights, keyed
by deme (one-based) and neighbor (one-based); each deme's self-retention is
implied as `1` minus its listed weights, exactly like the scalar case. A
deme absent from the map migrates with nobody.

```yaml
m:
  1: {2: 0.01}
  2: {1: 0.01, 3: 0.01}
  3: {2: 0.01, 4: 0.01}
  4: {3: 0.01}
d: 4
```

This is fully general — weights need not be symmetric, and this is the
right form for any irregular adjacency (real geography, an arbitrary graph),
not only the two named topologies below.

**Named topology sugar**, for the common stepping-stone case: a compact
`{topology, rate}` mapping that expands to the sparse form above.
`rate` is every deme's total outgoing migration fraction, split evenly among
its actual neighbors — the same meaning `m` already has as a scalar, applied
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

Both the sparse map and the topology sugar are config-file conveniences:
they expand to the ordinary dense matrix at load time (visible as such in
`report.json`/`manifest.json` and in to_dict()), so nothing downstream —
`migrate()`, statistics, persistence — needs to know a sparse form was ever
involved. Building a `SimulationParams` directly in Python (bypassing
from_mapping) still needs an already-dense matrix; call
fim.model.topology.stepping_stone_neighbors and
fim.model.topology.dense_matrix_from_neighbors yourself to get one.

Migration topologies that no fixed matrix can express — a 2D lattice, or
neighbor selection that changes over a run — are outside the current
configuration surface. Both have named landing spots in the design
document (§9.2, §11); neither is silently missing.

### `mu`

- **Type:** number in `[0, 1]`, or a list of exactly one rate per locus
- **Required:** yes, unless μ<sub>b</sub> (`μ<sub>b</sub>`) is given instead (the
  two are mutually
  exclusive)
- **Meaning:** per-gene-copy mutation probability per generation

A scalar applies identically to every locus, regardless of `length`, and
is the right choice whenever every locus should mutate at the same rate.
A list gives each locus its own explicit rate, positionally matched to
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

Configuring `mu` directly means picking one number per locus by hand, with
no built-in relationship to that locus's `length` — nothing stops two
loci of very different lengths from sharing an identical `mu`, which
contradicts the model's own reasoning for why longer loci should mutate
more often (more sites for a copying error to land on).

μ<sub>b</sub> (`μ<sub>b</sub>`) is the alternative: a single per-base-pair mutation
probability,
from which each locus's own rate is derived using its own `length`,
following the differentiation-measures guide's Eq. 5 relation exactly
(not its linear `μ<sub>b</sub> * length` approximation):

```math
\mathit{mu} = 1 - (1 - \mathit{mu\_b})^{\mathit{length}}
```

```yaml
mu_b: 0.00003
loci:
  - locus_id: 1
    length: 10       # mu ≈ 0.0003 here
  - locus_id: 2
    length: 8000      # mu ≈ 0.216 here — the same mu_b, a much higher mu
```

μ<sub>b</sub> is a config-file convenience, expanded to an explicit per-locus
`mu` at load time — like every other shorthand in this reference (the
compact n<sub>loci</sub>/locus_lengths locus form, the migration sparse map,
the stepping-stone topology mapping), to_dict()/`manifest.json` always
record the expanded `mu`, never μ<sub>b</sub> itself.

By default, every mutation produces a globally novel allele identity; see
[mutation_model](#mutation_model) below for the opt-in alternative.

### `seed`

- **Type:** non-negative integer
- **Required:** yes; there is no default
- **Meaning:** seed for the run's NumPy `PCG64` generator

A negative seed is rejected: `PCG64` has no equivalent upper bound to
reject against in turn, so non-negativity is the entire legal range.

## Loci

### `loci`

- **Type:** nonempty list of mappings
- **Default:** one locus with locus_id: 1 and `length: 200`

Each entry has a positive `length` and an optional positive locus_id that
defaults to its one-based position. IDs must be unique.

As an alternative to `loci`, use:

- n<sub>loci</sub> — positive locus count, default `1`;
- locus_lengths — one positive integer shared by all loci, or exactly
  n<sub>loci</sub> integers, default `200`.

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

- infinite_alleles (the default): every mutation event produces a label
  never seen before, anywhere, ever. A good approximation once a locus
  spans many base pairs — see
  [the differentiation-measures guide](jost-differentiation-measures.md#distance-between-alleles-is-a-different-model) —
  but increasingly unrealistic for a short locus, where the same state
  can plausibly arise more than once by chance (a *recurrence*).
- finite_alleles: each locus gets a bounded state space of exactly
  4<sup>length</sup> possible states (the differentiation-measures guide's own
  worked reasoning: "a single-character locus admits at most four
  alleles"). A mutation event's target is drawn uniformly from the other
  `capacity - 1` states — never its own current state, but possibly one
  already present elsewhere in the run. This still imposes no ordering or
  distance between alleles; it only gives the label space a ceiling. See
  [the simulator design, §3.2 and §9](fim-simulator-design.md#32-alleles-loci-and-identity)
  for the full reasoning, including why this is a *different*, and
  deliberately not chosen, direction from a stepwise (microsatellite)
  mutation model.

```yaml
mutation_model: finite_alleles
loci:
  - locus_id: 1
    length: 1     # capacity 4 — recurrence becomes likely quickly
```

finite_alleles interacts with `length`, initial_allele_count, and
p<sub>0</sub>: every locus's starting allele IDs — the founding range
0 .. initial_allele_count - 1, or an explicit p<sub>0</sub>'s specific IDs —
must fit inside that locus's own 4<sup>length</sup> capacity, checked
independently per locus. A locus this short with the library default
initial_allele_count: 2 always fits (capacity is at least 4); it is
easiest to violate by combining a short `length` with an explicit p<sub>0</sub>
using IDs that were only ever meant for a longer locus.

## Initial conditions

Generation 0 — whether drawn from initial_concentration or supplied
explicitly via p<sub>0</sub> — is a continuous prior, not a state the run's `N`
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

### p<sub>0</sub>

- **Type:** optional nested list: `d` demes, each containing one mapping per
  configured locus
- **Default:** absent

Allele keys are integer IDs and each mapping must sum to 1. When present,
p<sub>0</sub> is used verbatim instead of a Dirichlet draw. Newly mutated allele IDs
are allocated above both the reserved mutation range and the highest supplied
ID, so they cannot collide with explicit labels.

```yaml
p_0:
  - - 0: 0.75
      1: 0.25
  - - 0: 0.10
      1: 0.90
```

## Convergence

### convergence_statistic

- **Type:** one of `D`, G<sub>ST</sub>, E<sub>ST</sub>, K<sub>ST</sub>, H<sub>S</sub>, H<sub>T</sub>, or a list of
  several of them
- **Default:** `D`

A list watches several statistics at once — each keeps its own independent
trailing-window history against the same convergence_window and
convergence_tolerance — combined by convergence_combinator. A name may
not repeat.

```yaml
convergence_statistic: [D, G_ST]
convergence_combinator: any   # stop once either statistic settles
```

### convergence_combinator

- **Type:** `all` or `any`
- **Default:** `all`

Only meaningful when convergence_statistic is a list: `all` requires every
watched statistic to be simultaneously stable before stopping (a strict
reading of "several statistics need to agree"); `any` stops as soon as one
of them is. With a single statistic — the default — the two are the same
value by construction, so this key has no effect and needs no attention.

### convergence_window

- **Type:** integer at least 2
- **Default:** `50`

The monitor compares the means of the first and second halves of the trailing
window. An odd window splits as \lfloor{window / 2\rfloor observations in the first half
and one more in the second (a window of `5` compares `2` against `3`) — legal,
but the two halves are then unevenly sized, unlike an even window. Rejected
if it exceeds max_generations + 1 — generation 0 is
always recorded before the run loop's first step, so a run watching
max_generations records at most that many generations; a window
larger than that could never fill before the hard cap stops the run,
so convergence could never be detected.

### convergence_tolerance

- **Type:** non-negative finite number
- **Default:** `0.01`

The statistic converges when the half-window mean difference is at most this
value.

### max_generations

- **Type:** positive integer
- **Default:** `10000`

This safety cap always ends a run. Reaching it is reported as a valid
non-converged outcome.

## Analysis and execution

### deme_weighting

- **Type:** `size` or `equal`
- **Default:** `size`

This setting controls E<sub>ST</sub>. Jost's `D` and K<sub>ST</sub> use equal deme weighting by
definition. When every deme is the same size, both settings produce the same
E<sub>ST</sub>.

### n<sub>replicates</sub>

- **Type:** positive integer
- **Default:** `1`

n<sub>replicates</sub> runs that many independently seeded scalar runs — seeds
`seed`, `seed + 1`, and so on — through both the library API
(`fim.engine.fim`, returning one `RunResult` per replicate) and the CLI
(`fim run`, writing one `replicate-NNN/` subdirectory per replicate; see
[Using `fim`](usage.md#run-a-simulation)). With replicate_tolerance unset
(the default), exactly n<sub>replicates</sub> run. With it set, n<sub>replicates</sub>
instead becomes the hard cap on an adaptive stop — see replicate_tolerance
below.

### replicate_tolerance

- **Type:** non-negative finite number, or omitted
- **Default:** unset (fixed-count batching; see n<sub>replicates</sub>)

Opt-in early stopping for a replicate batch (n<sub>replicates</sub> greater than
one): once at least replicate_minimum replicates have run, stop as soon
as every statistic named in convergence_statistic has an across-replicate
Student's-t confidence interval (mean of that statistic's own final value
across replicates so far) with a half-width at most replicate_tolerance
— combined across several watched statistics by convergence_combinator,
exactly like within-run convergence. n<sub>replicates</sub> is still the hard cap:
reaching it without tightening ends the batch anyway, a valid,
non-adaptively-stopped result. This is the mechanism that answers "how many
replicate runs are needed for a confidence interval" without guessing a
fixed count in advance — see each statistic's realized interval in
`summary.json` (CLI) or fim.engine.replicate_summary (library).

```yaml
n_replicates: 200          # hard cap
replicate_tolerance: 0.02  # stop once every watched statistic is this tight
replicate_minimum: 20
```

### replicate_minimum

- **Type:** integer at least 2
- **Default:** `10`

The fewest replicates before replicate_tolerance is even checked — the
replicate-layer analog of convergence_window, guarding against a
lucky-early-tight fluke from too small a sample. Only meaningful when
replicate_tolerance is set. Whenever it is set together with
n<sub>replicates</sub> greater than one, replicate_minimum is rejected if it
exceeds n<sub>replicates</sub>: adaptive stopping could never be evaluated
before the replicate cap ends the batch.

### replicate_confidence

- **Type:** `0.90`, `0.95`, or `0.99`
- **Default:** `0.95`

The two-tailed confidence level used by replicate_tolerance's interval,
and by `summary.json`/replicate_summary's reported intervals. Only
meaningful when replicate_tolerance is set.

### migrant_sampling

- **Type:** `continuous` or `stochastic`
- **Default:** `continuous`

Controls how many gene copies migrate each generation, independently of
which `m` shape is configured above.

- `continuous` (the default): each deme's migrant count is exactly
  N<sub>i</sub> * rate
  (or, for a matrix row, N<sub>i</sub> times that row's non-self weight) — a fixed
  fraction, not a random draw.
- `stochastic`: each deme's migrant count is instead drawn fresh every
  generation from Binomial(N<sub>i</sub>, rate) — mean N<sub>i</sub> * rate, matching the
  continuous case in expectation, but varying generation to generation.
  Migrant *composition* is unaffected either way: migrants still carry
  exactly the deterministic, weighted pool average. Requires a concrete
  `N` (always true for a CLI run; a direct `fim.model.operators.migrate`
  call needs population_size).

```yaml
migrant_sampling: stochastic
```

This is the finite island model variant described in
[the finite island model introduction, §3.2](finite-island-model-introduction.md#32-one-generation-in-two-steps):
sampling the actual migrant count instead of treating migration as an
idealized continuous fraction. It adds one random process to the pipeline
(how many migrate) without duplicating the one already there (`drift`
still resamples every gene copy exactly once per generation) — see
[the simulator design, §9](fim-simulator-design.md#9-extensibility-where-the-next-what-if-lands)
for why the two don't compound.

## Validation summary

| Condition | Result |
|---|---|
| `N < 1`, `d < 2` | rejected |
| `m` or `mu` outside `[0, 1]` | rejected |
| missing `seed`, or `seed < 0` | rejected |
| empty or duplicate loci | rejected |
| frequency vector not summing to 1 | rejected |
| unknown key | rejected by name |
| matrix/list shape not matching `d` | rejected |
| unrecognized or repeated convergence_statistic entry | rejected |
| `m` sparse-map deme/neighbor id outside `[1..d]`, a self-loop, or weights summing past `1` | rejected |
| `m` topology mapping missing `topology`/`rate`, an unknown key, or an unrecognized topology name | rejected |
| `m` ring topology with `d < 3` | rejected |
| migrant_sampling not `continuous` or `stochastic` | rejected |
| mutation_model not infinite_alleles or finite_alleles | rejected |
| finite_alleles with a locus's starting allele IDs exceeding its 4<sup>length</sup> capacity | rejected |
| both `mu` and μ<sub>b</sub> given, or neither | rejected |
| `mu` list length not matching the locus count | rejected |
| μ<sub>b</sub> outside `[0, 1]` | rejected |
| replicate_tolerance negative or non-finite | rejected |
| replicate_minimum less than 2 | rejected |
| convergence_window greater than max_generations + 1 | rejected |
| replicate_minimum greater than n<sub>replicates</sub> (with replicate_tolerance set and n<sub>replicates</sub> > 1) | rejected |
| replicate_confidence not `0.90`, `0.95`, or `0.99` | rejected |
