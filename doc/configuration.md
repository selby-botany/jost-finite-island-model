# `fim` configuration reference

`fim run` accepts one YAML mapping. Unknown keys, incompatible shapes, and
out-of-range values are errors. See [Using `fim`](usage.md) for commands and
the [project overview](../README.md) for installation.

## Contents

- [Complete example](#complete-example)
- [Required model keys](#required-model-keys)
- [Loci](#loci)
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
Per-deme lists model unequal island sizes and are fully supported in
version 1.0.0: every stage of the update pipeline (`migrate`, `mutate`,
`drift`), the founding-allele-count bound (checked against the smallest
`N_i`), and `deme_weighting: size` all use each deme's own configured
gene-copy count.

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

### `mu`

- **Type:** number in `[0, 1]`
- **Required:** yes
- **Meaning:** per-gene-copy mutation probability per generation

Every mutation produces a globally novel allele identity.

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

Do not combine the two forms. Length is retained as locus data for future
per-base-pair mutation models; differentiation statistics do not read it.

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

- **Type:** one of `D`, `G_ST`, `E_ST`, `K_ST`, `H_S`, `H_T`
- **Default:** `D`

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
