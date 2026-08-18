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
(checked against the smallest `N_i`), and `deme_weighting: size` all use
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
  the matrix itself.
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
`report.json`/`manifest.json` and in `to_dict()`), so nothing downstream —
`migrate()`, statistics, persistence — needs to know a sparse form was ever
involved. Building a `SimulationParams` directly in Python (bypassing
`from_mapping`) still needs an already-dense matrix; call
`fim.model.topology.stepping_stone_neighbors` and
`fim.model.topology.dense_matrix_from_neighbors` yourself to get one.

A dedicated `MigrantPoolStrategy` interface — pluggable neighbor-selection
logic beyond a precomputed matrix — remains unimplemented; so does a 2D
lattice topology. Both are documented, deferred landing spots (design
document §9, §12), not silently missing.

### `mu`

- **Type:** number in `[0, 1]`
- **Required:** yes
- **Meaning:** per-gene-copy mutation probability per generation, applied
  identically at every locus regardless of `length`

By default, every mutation produces a globally novel allele identity; see
[mutation_model](#mutation_model) below for the opt-in alternative.

### `seed`

- **Type:** integer
- **Required:** yes; there is no default
- **Meaning:** seed for the run's NumPy `PCG64` generator

## Loci

### `loci`

- **Type:** nonempty list of mappings
- **Default:** one locus with `locus_id: 1` and `length: 200`

Each entry has a positive `length` and an optional positive `locus_id` that
defaults to its one-based position. IDs must be unique.

As an alternative to `loci`, use:

- `n_loci` — positive locus count, default `1`;
- `locus_lengths` — one positive integer shared by all loci, or exactly
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

### `mutation_model`

- **Type:** `infinite_alleles` or `finite_alleles`
- **Default:** `infinite_alleles`

Controls what a mutation event turns an allele *into*, independently of
`mu` (which controls how *often* one happens).

- `infinite_alleles` (the default, and the only behavior in every release
  before this option existed): every mutation event produces a label
  never seen before, anywhere, ever. A good approximation once a locus
  spans many base pairs — see
  [the differentiation-measures guide](jost-differentiation-measures.md#distance-between-alleles-is-a-different-model) —
  but increasingly unrealistic for a short locus, where the same state
  can plausibly arise more than once by chance (a *recurrence*).
- `finite_alleles`: each locus gets a bounded state space of exactly
  `4 ** length` possible states (the differentiation-measures guide's own
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

`finite_alleles` interacts with `length`, `initial_allele_count`, and
`p_0`: every locus's starting allele IDs — the founding range
`0 .. initial_allele_count - 1`, or an explicit `p_0`'s specific IDs —
must fit inside that locus's own `4 ** length` capacity, checked
independently per locus. A locus this short with the library default
`initial_allele_count: 2` always fits (capacity is at least 4); it is
easiest to violate by combining a short `length` with an explicit `p_0`
using IDs that were only ever meant for a longer locus.

## Initial conditions

### `initial_allele_count`

- **Type:** positive integer no larger than the smallest `N`
- **Default:** `2`

Founding IDs are locus-relative `0` through `initial_allele_count - 1`.

### `initial_concentration`

- **Type:** positive number
- **Default:** `1.0`

The random default draws each deme/locus frequency vector independently from a
symmetric Dirichlet distribution. Smaller values are more uneven.

### `p_0`

- **Type:** optional nested list: `d` demes, each containing one mapping per
  configured locus
- **Default:** absent

Allele keys are integer IDs and each mapping must sum to 1. When present,
`p_0` is used verbatim instead of a Dirichlet draw. Newly mutated allele IDs
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

### `convergence_statistic`

- **Type:** one of `D`, `G_ST`, `E_ST`, `K_ST`, `H_S`, `H_T`, or a list of
  several of them
- **Default:** `D`

A list watches several statistics at once — each keeps its own independent
trailing-window history against the same `convergence_window` and
`convergence_tolerance` — combined by `convergence_combinator`. A name may
not repeat.

```yaml
convergence_statistic: [D, G_ST]
convergence_combinator: any   # stop once either statistic settles
```

### `convergence_combinator`

- **Type:** `all` or `any`
- **Default:** `all`

Only meaningful when `convergence_statistic` is a list: `all` requires every
watched statistic to be simultaneously stable before stopping (a strict
reading of "several statistics need to agree"); `any` stops as soon as one
of them is. With a single statistic — the default — the two are the same
value by construction, so this key has no effect and needs no attention.

### `convergence_window`

- **Type:** integer at least 2
- **Default:** `50`

The monitor compares the means of the first and second halves of the trailing
window.

### `convergence_tolerance`

- **Type:** non-negative finite number
- **Default:** `0.01`

The statistic converges when the half-window mean difference is at most this
value.

### `max_generations`

- **Type:** positive integer
- **Default:** `10000`

This safety cap always ends a run. Reaching it is reported as a valid
non-converged outcome.

## Analysis and execution

### `deme_weighting`

- **Type:** `size` or `equal`
- **Default:** `size`

This setting controls `E_ST`. Jost's `D` and `K_ST` use equal deme weighting by
definition. Equal-size v1 scenarios produce the same `E_ST` under either
setting.

### `n_replicates`

- **Type:** positive integer
- **Default:** `1`

The library API runs reproducible replicates with seeds `seed`,
`seed + 1`, and so on. The CLI requires `1` in version 1.0.0 so each output
directory retains the four-file scalar-run contract.

## Validation summary

| Condition | Result |
|---|---|
| `N < 1`, `d < 2` | rejected |
| `m` or `mu` outside `[0, 1]` | rejected |
| missing `seed` | rejected |
| empty or duplicate loci | rejected |
| frequency vector not summing to 1 | rejected |
| unknown key | rejected by name |
| matrix/list shape not matching `d` | rejected |
| unrecognized or repeated `convergence_statistic` entry | rejected |
| `m` sparse-map deme/neighbor id outside `1..d`, a self-loop, or weights summing past `1` | rejected |
| `m` topology mapping missing `topology`/`rate`, an unknown key, or an unrecognized topology name | rejected |
| `m` ring topology with `d < 3` | rejected |
| `mutation_model` not `infinite_alleles` or `finite_alleles` | rejected |
| `finite_alleles` with a locus's starting allele IDs exceeding its `4 ** length` capacity | rejected |
