# Dear-Nolan low-migration example

This example implements the low-migration scenario from the Dear-Nolan
botanical simulations: five demes of 100 individuals each with very low
migration and negligible mutation. It is meant to run to the population's
equilibrium, which takes tens of thousands of generations.

## Status: this example runs as a batch and agrees with theory

**The batch converges.** It ran 10 replicates and stopped at the minimum of 10
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

The shipped configuration sets `max_generations: 1000000000`, a cap far above
any need, so each replicate stops when its window is done and never because it
ran out of generations. An earlier version of this example, run as a single
run with the derived cap (399,728 generations), ended at the cap with
`converged_on: null`, because this scenario needs more than two million
generations in one run at this precision: `D` swings over the population's
relaxation time (about 19,700 generations), so even a window of millions of
generations holds only about a hundred independent draws of it. A batch spreads
that work over replicates, each averaging over about 394,000 generations.

The committed outputs were produced with `engine_backend: auto`, which
resolves to `generational-vector` here. The batch took about 55 minutes on a
10-core development machine with other jobs running (the earlier single-run
version took about three and a half minutes on a 22-thread Linux machine). The
reference `lineal` engine produces the same result bit for bit, and is about
30 times slower (see "Engine identity").

## Biological context

The Dear-Nolan scenarios (referenced in the `fim` calibration suite) model
isolated plant populations where seed dispersal between sites is rare.
The low-migration configuration (m = 0.0001, &mu; = 0.000001) gives
Nm = 0.01, which the traditional view reads as "very high differentiation".

At equilibrium the opposite happens for the heterozygosity-based
differentiation that G<sub>ST</sub> and D measure. Each deme is nearly always
fixed for a single allele (H<sub>S</sub> ≈ 0.001), and because the total
diversity is also near zero (H<sub>T</sub> ≈ 0.03), almost all demes are fixed
for the *same* allele. In the source correspondence, 189 of 200 simulated loci
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

`config.yaml` therefore leaves `convergence_burn_in` on `auto` and sets
`max_generations` far above any need. Each replicate derives a burn-in of 104,338
generations (about 5.3 relaxation times) and then averages over a window
matched to the batch's precision (see
[Batches](../../convergence.md#batches-replicates-that-average)). Earlier
versions stopped this scenario after a few hundred generations, on the plateau,
with a large D. This example watches **D** and stops when D's interval across
replicates is within the precision.

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

Thirty independent loci stand in for many separate simulation runs, as in the
source correspondence (each locus is an independent replicate of the same
demographic process).

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
picture as the source: nearly every deme is fixed, and nearly all of them on
the same allele.

### Engine identity

`engine_backend: auto` resolved to `generational-vector`. The `lineal`
reference reproduced the report exactly when this example was a single run: the
same configuration with only `engine_backend` changed to `lineal`, run on the
same 22-thread Linux machine, stopped at the same generation (2,625,168) with an
identical `report.json` (every field equal, `run_id` aside, which differs
because it hashes the configuration). It took 1 hour 44 minutes, against 3
minutes 24 seconds for `auto`: about 30 times as long. The batch has not been
rerun on `lineal`. The automated slow test
(`test/validation/test_examples_auto_backend.py`) skips this example because of
that time; the other examples are rerun on `lineal` there, and the engine
parity tests cover this configuration's shape. The single run's report is kept
as validation data for the convergence rule (`test/validation/`).

## Relationship to the published calibration

The `fim` calibration test (`test_dear_nolan_low_migration_scenario_via_engine`)
uses:

- 26 loci with an engineered near-equilibrium initial-frequency distribution
- 12 replicates averaged together
- A different seed (884000)

That configuration starts the population at the mathematical fixed point where
the within-deme and between-deme identity recursions balance, so it needs no
long approach. This example starts from a random draw and shows the approach
itself, which is why it runs for so long. `test/validation/
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
