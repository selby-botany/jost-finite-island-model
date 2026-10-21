# Dear-Nolan low-migration example

This example implements the low-migration scenario from the Dear-Nolan
botanical simulations: five demes of 100 individuals each with very low
migration and negligible mutation. It is meant to run to the population's
equilibrium, which takes tens of thousands of generations.

## Batch result

The batch ran 10 replicates and stopped at the minimum of 10
because the 95% confidence interval for `D` was within the requested ±0.01
(this example watches `D`). Each replicate burned in for 104,338 generations
(about 5.3 relaxation times of 19,693), averaged over a 393,853-generation
window, and stopped at generation 498,191. Across the replicates `D` averages
**0.0353 ± 0.0078** (95% confidence half-width, an interval of 0.0275 to
0.0431). The closed-form equilibrium for these parameters is
`1 / (1 + m / (mu (d - 1)))` = **0.0385**, and Jost's published target is
D ≈ 0.04: both lie inside the interval. The mean of `G_ST` over the
generations where it is defined is **0.954 ± 0.014** (0.939 to 0.968); computed
from the averaged heterozygosities (H<sub>S</sub> ≈ 0.0009, H<sub>T</sub> ≈
0.029) it is about 0.969, against theory 0.9695.

The configuration sets `max_generations: 1000000000`, so each replicate
reaches its precision criterion before the generation cap. At the reported
relaxation time of about 19,700 generations, an averaging window of roughly
394,000 generations contains only about 20 relaxation-time units. Replication
provides independent run-level estimates in addition to the within-run
averaging.

The committed outputs were produced with `engine_backend: auto`, which
resolves to `generational-vector` for this configuration. The batch took
about 55 minutes on a 10-core development machine with other jobs running.
The reference `lineal` engine produces the same result bit for bit and is
about 30 times slower under the measured conditions (see "Engine identity").

## Biological context

The Dear-Nolan scenarios (referenced in the `fim` calibration suite) model
isolated plant populations where seed dispersal between sites is rare.
The low-migration configuration (m = 0.0001, &mu; = 0.000001) gives
Nm = 0.01, which the traditional view reads as "very high differentiation".

At equilibrium, heterozygosity-based
differentiation that G<sub>ST</sub> and D measure. Each deme is nearly always
fixed for a single allele (H<sub>S</sub> ≈ 0.001), and because the total
diversity is also near zero (H<sub>T</sub> ≈ 0.03), almost all demes are fixed
for the *same* allele. In the published source results, 189 of 200 simulated loci
had two demes fixed for the same allele (about 95%). G<sub>ST</sub> is high
(the little diversity that exists is between demes) but D, which measures
differentiation of allele frequencies, is near zero: it is controlled by
m / [(d − 1) &mu;] = 25, not by Nm.

Published ensemble values (engineered equilibrium start, multi-locus,
100 replicates): **G<sub>ST</sub> ≈ 0.970, D ≈ 0.038**; Jost's published
targets for the same parameters over 200 runs are D ≈ 0.04 and
G<sub>ST</sub> ≈ 0.97.

## Why the run is long

A run from a random start passes through two stages. In the first few hundred
generations drift fixes each deme on one of its two founding alleles, usually
on *different* alleles in different demes. That state is a plateau: G<sub>ST</sub>
is exactly 1.0 and flat, and D is high (about 0.6). It is not the equilibrium.
Only when migration and mutation have had time to spread one allele across the
demes does D fall toward 0.04. The population needs about 19,700 generations
to forget its starting state (the relaxation time; see
[Convergence defaults](../../convergence.md)).

`config.yaml` leaves `convergence_burn_in` on `auto` and sets
`max_generations` far above any need. Each replicate derives a burn-in of 104,338
generations (about 5.3 relaxation times) and then averages over a window
matched to the batch's precision (see
[Batches](../../convergence.md#batches-replicates-that-average)). The example
watches **D** and stops when D's interval across replicates is within the
precision.

## Parameters

| Parameter | Value | Meaning |
|---|---|---|
| N | 100 | Individuals per deme |
| d | 5 | Number of demes |
| m | 0.0001 | Very low symmetric migration rate |
| &mu; | 0.000001 | Negligible per-locus mutation rate |
| loci | 30 | Independent loci of length 200 (infinite-alleles model) |
| convergence | `auto` | Burn-in derived from the model; cap set far above need (1,000,000,000) |
| seed | 20260825 | Exact RNG seed |

The 30 independent loci provide independent realizations of the demographic
process within each replicate.

## Running the example

```console
fim run doc/examples/dear-nolan-low/config.yaml \
    --output results/dear-nolan-low --quiet
```

Takes about 55 minutes on a 10-core development machine with
`engine_backend: auto` (ten replicates of about 500,000 generations of 30 loci
each), and writes about 68 MB of batch artifacts: `summary.json`, `manifest.json`
and one `replicate-NNN/` directory per replicate. `fim export` turns a
replicate's trajectory log into a multi-gigabyte `trajectory.jsonl`.
`results/dear-nolan-low/summary.json` will match `summary.json` in this
directory.

## Expected output

`summary.json` (abbreviated to the two statistics this document discusses):

```json
{
  "D": {
    "confidence": 0.95,
    "half_width": 0.0078,
    "high": 0.0431,
    "low": 0.0275,
    "mean": 0.0353,
    "sample_count": 10,
    "sample_std": 0.0109
  },
  "G_ST": {
    "confidence": 0.95,
    "half_width": 0.0144,
    "high": 0.9680,
    "low": 0.9392,
    "mean": 0.9536,
    "sample_count": 10,
    "sample_std": 0.0201
  }
}
```

`half_width` is how precisely the average across replicates is known;
`sample_std` is how much one replicate's average varies from another's (see
[Batch `summary.json`](../../usage.md#batch-summaryjson-and-manifestjson)). The
batch stops because `D`'s half-width, 0.0078, is within the requested 0.01.
Its mean, **0.0353**, brackets the theoretical 0.0385 and Jost's published
D ≈ 0.04.

`G_ST` is undefined in the generations where the total heterozygosity is zero
(every deme fixed for the same allele), so its mean averages only the defined
generations (0.954 ± 0.014); the value built from the averaged heterozygosities
is about 0.969, which matches the theoretical `G_ST` (0.96946) and Jost's
published ≈ 0.97. H<sub>S</sub> ≈ 0.001 and H<sub>T</sub> ≈ 0.03 show the same
picture as the published source results: nearly every deme is fixed, and nearly all of them on
the same allele.

### Backend validation

`engine_backend: auto` resolves to `generational-vector` for this
configuration. The automated example-reproduction test
(`test/validation/test_examples_auto_backend.py`) skips this long-running
batch. Engine-parity tests cover the configuration's model shape.

## Relationship to the published calibration

The `fim` calibration test (`test_dear_nolan_low_migration_scenario_via_engine`)
uses:

- 26 loci with an engineered near-equilibrium initial-frequency distribution
- 12 replicates averaged together
- A different seed (884000)

That configuration starts the population at the mathematical fixed point where
the within-deme and between-deme identity recursions balance. This example
starts from a random draw and records the approach to equilibrium.
`test/validation/
test_convergence_defaults.py` checks that a run with the default settings
stops near the same equilibrium.

## Files in this directory

| File | Description |
|---|---|
| `config.yaml` | Complete simulation configuration |
| `summary.json` | The batch's mean and confidence interval for every statistic |
| `replicate-NNN/` | Each replicate's own `manifest.json`, `report.json` and trajectory |
| `manifest.json` | Batch metadata: parameters, timing, the replicates that ran |
| `README.md` | This document |
