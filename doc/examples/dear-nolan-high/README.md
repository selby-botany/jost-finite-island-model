# Dear-Nolan high-migration example

This example runs 100 well-connected islands from the ordinary two-allele
founder population all the way to the published high-migration equilibrium.
It does not start at equilibrium and does not use a starting state derived
from the simulator's identity recursion (that stationarity check lives
separately; see "Validation and maintenance" below).

## Batch result

The batch ran 10 replicates and stopped at the minimum of 10 because the
95% confidence interval for both `D` and `G_ST` was within the requested
±0.01 (this example watches both). Each replicate burned in for 2,643
generations (about 5.3 relaxation times of 499, dominated by mutation at
this migration rate) and then averaged over a 9,977-generation window,
stopping at generation 12,619. Across the replicates `D` averages
**0.9016 ± 0.0055** (95% confidence half-width, an interval of 0.8961 to
0.9071) against the published target of D ≈ 0.90–0.91. `G_ST` averages
**0.02193 ± 0.00004** (0.021892 to 0.021975) against the published target
of G_ST ≈ 0.02. Both published targets lie inside their intervals.

The committed outputs were produced with `engine_backend: auto`, which
resolves to `generational-vector` for this configuration.

## Biological question

Each island has 2,000 haploid individuals. In every generation the
migration rate is 0.01 and the mutation probability per gene copy is
0.001 (Nm = 20, the traditional view reads this as "well connected").
There is one locus, of length 400; the infinite-alleles mutation model
gives each mutation a new allele identity. The founder population uses
the ordinary seeded initialization: each island draws its frequencies for
the same two alleles independently — founder populations, not identical
island frequencies and not populations prepared at the expected
equilibrium.

`G_ST` and `D` answer different questions and approach their targets at
different rates: `G_ST` settles well before `D` does, so agreement with
`G_ST` alone is not evidence that the population has reached the expected
differentiation. Each replicate's saved statistic history, and the pooled
curve the desktop app draws from them, let you check both.

The batch keeps statistics only (`trajectory_retention: statistics`): a
full trajectory of this run is about 1 GiB per replicate, too large to
ship. Each replicate keeps its report and a decimated `history.json` of
at most 2,048 generations instead, so the app opens the batch as a report
without allele-frequency scatter or per-generation reanalysis. Run the
configuration with `trajectory_retention` removed to get the full
trajectories.

## Why the batch takes a while

`max_concurrent_replicates` is unset, so every one of the 200 requested
replicate lanes advances together (see
[configuration](../../configuration.md#max_concurrent_replicates)) — the
wave width that sizes the averaging window is the full 200, not a smaller
first wave. That means the wall-clock cost of this regeneration reflects
advancing 200 lanes of 2,000 individuals across 100 demes through the
full burn-in-plus-window (12,619 generations) before the batch can even
check whether 10 replicates already meet the requested precision, even
though only the first 10 (in replicate order) are kept. On the development
machine that took about 7 hours 38 minutes (single core, `generational-vector`).
`max_concurrent_replicates` is part of a run's identity (it changes the
window the batch settles on), so the shipped configuration leaves it at
its default rather than trading a different, faster result for a quicker
reproduction.

## Parameters

| Parameter | Value | Meaning |
|---|---|---|
| N | 2000 | Individuals per island |
| d | 100 | Number of islands |
| m | 0.01 | Migration rate |
| &mu; | 0.001 | Per-locus mutation rate |
| loci | 1 | One locus of length 400 (infinite-alleles model) |
| initial_allele_count | 2 | Each island starts from the same two founder alleles, independently |
| convergence | `auto` | Burn-in derived from the model; watches `D` and `G_ST` |
| seed | 992000 | Exact RNG seed |

## Running the example

```console
fim run doc/examples/dear-nolan-high/config.yaml \
    --output results/dear-nolan-high --quiet
```

The shipped configuration is read-only in the desktop app. Copy it and
remove `_read_only` to change the parameters.

`engine_backend: auto` selects the modern vector backend when the optional
Numba dependency is installed. Without Numba, it falls back to the much
slower generational backend. For a source installation, install the
project's `jit` extra in the project environment to enable vector
execution.

`results/dear-nolan-high/summary.json` will match `summary.json` in this
directory.

## Expected output

`summary.json` (both statistics this document discusses):

```json
{
  "D": {
    "confidence": 0.95,
    "half_width": 0.0055,
    "high": 0.9071,
    "low": 0.8961,
    "mean": 0.9016,
    "sample_count": 10,
    "sample_std": 0.0077
  },
  "G_ST": {
    "confidence": 0.95,
    "half_width": 0.00004,
    "high": 0.021975,
    "low": 0.021892,
    "mean": 0.021933,
    "sample_count": 10,
    "sample_std": 0.00006
  }
}
```

`half_width` is how precisely the average across replicates is known;
`sample_std` is how much one replicate's average varies from another's
(see [Batch `summary.json`](../../usage.md#batch-summaryjson-and-manifestjson)).
The batch stops because both `D`'s and `G_ST`'s half-widths are within the
requested 0.01.

## Validation and maintenance

The founder-convergence test
([`test_dear_nolan_founder.py`](../../../test/TESTS.md#validation.test_dear_nolan_founder))
runs this configuration's population as one seeded, fixed-horizon
5000-generation run through the real simulator and checks both
published-value bounds, in the final state and in the mean of the
trailing 500 generations. It pins its own horizon (burn-in, window,
replicate count) rather than the example's, because the example itself
runs as the default adaptive batch above. The seed, horizon, and bounds
are fixed; the outcome must not depend on external services, execution
order, or the time the test runs.

The separate internal stationarity test in
[`test_simulator_equilibrium.py`](../../../test/TESTS.md#validation.test_simulator_equilibrium)
still constructs a near-equilibrium start from the identity recursion and
checks preservation of the exact-recursion fixed point over 30
generations — a regression check for a biased operator, deliberately not
the user-facing example.

After changing the configuration, regenerate its committed outputs with:

```console
dev/bin/regenerate-example-outputs dear-nolan-high
dev/bin/build-examples-catalog
```

## Files in this directory

| File | Description |
|---|---|
| `config.yaml` | Complete simulation configuration |
| `summary.json` | The batch's mean and confidence interval for every statistic |
| `replicate-NNN/` | Each replicate's own `manifest.json`, `report.json`, `pairwise.json`, `scatter.png` and decimated statistic `history.json` |
| `manifest.json` | Batch metadata: parameters, timing, the replicates that ran |
| `README.md` | This document |

For the mathematical definitions, see the
[differentiation-measures guide](../../jost-differentiation-measures.md)
and [migration-conventions reference](../../migration-conventions.md).
