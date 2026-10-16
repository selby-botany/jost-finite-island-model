# Dear-Nolan low-migration example

This example implements the low-migration scenario from the Dear-Nolan
botanical simulations: five demes of 100 individuals each with very low
migration and negligible mutation. It is meant to run to the population's
equilibrium, which takes tens of thousands of generations.

## Status: this example runs to convergence and agrees with theory

**This run converges.** It burns in for 104,338 generations, averages for the
2,520,831 that follow, and stops at generation 2,625,168 because `D` reached
the requested precision (`converged_on: D`, `"reason": "statistic converged"`).
`D` averages **0.0390 ± 0.0031** (one standard error; 74 effective samples,
above the floor of 50; Geweke `z` = 0.14, so the start of the window agrees with
its end). The closed-form equilibrium for these parameters is
`1 / (1 + m / (mu (d - 1)))` = **0.0385**, and Jost's published target is
D ≈ 0.04: the run is within a fifth of a standard error of theory. The
`G_ST` computed from the window's averaged heterozygosities (`value_of_means`)
is **0.9695 ± 0.0005**, against theory 0.9695.

The shipped configuration sets `max_generations: 1000000000`, a cap far above
any need, so the run stops when the rule says it has converged and never
because it ran out of generations. An earlier version of this example used the
derived cap (399,728 generations) and ended at the cap with `converged_on: null`,
because this scenario needs more than two million generations at this
precision: `D` swings over the population's relaxation time (about 19,700
generations), so even a window of millions of generations holds only about a hundred independent draws of it.

The committed outputs were produced with `engine_backend: auto`, which
resolves to `generational-vector` here. It took about three and a half minutes
on a 22-thread Linux machine. The reference `lineal` engine produces the same
result bit for bit, and takes an hour and three quarters (see "Engine identity").

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
`max_generations` far above any need. `fim run` derives a burn-in of 104,338
generations (about 5.3 relaxation times) and averages after the burn-in until
each watched statistic's average is known to the configured precision (see
[Convergence](../../convergence.md)). Earlier versions stopped this scenario
after a few hundred generations, on the plateau, with a large D. This run
watches **D** and **G<sub>ST</sub>** (the default) and stops when both are
known to the requested precision, after 2,625,168 generations.

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

## Why a single run

This example sets `n_replicates: 1` on purpose. A single run is the only kind
with a trajectory graph, a scrubber and a scatter plot to look at, and
showing those is the point of this example. The engine's default is
`n_replicates: 200`, which runs an adaptively stopped batch and reports a
confidence interval instead. One run's final numbers are one noisy draw: for
an estimate with an error bar, run a batch (see
[an adaptive replicate batch with a confidence interval](../an-adaptive-replicate-batch-with-a-confidence-interval/README.md)).

## Running the example

```console
fim run doc/examples/dear-nolan-low/config.yaml \
    --output results/dear-nolan-low --quiet
```

Takes about three and a half minutes on a 22-thread Linux machine with
`engine_backend: auto` (2,625,168 generations of 30 loci), and writes a
trajectory log of about 60 MB and a compressed convergence history of about
30 MB. The same configuration on `engine_backend: lineal` gives identical
reports and takes about an hour and three quarters (see "Engine identity"). `fim export` turns the trajectory log into
a multi-gigabyte `trajectory.jsonl`. `results/dear-nolan-low/report.json` will
match `report.json` in this directory.

## Expected output

```json
{
  "converged": true,
  "converged_on": "D",
  "generation": 2625168,
  "G_ST": 1.0,
  "D": 0.033333333333333326,
  "reason": "statistic converged",
  "window_statistics": {
    "D": {
      "mean": 0.03901182696223622,
      "standard_error": 0.0030980650495003553,
      "noise_adequate": true,
      "window": 2520831,
      "effective_sample_size": 74.1743505414768,
      "geweke_z": 0.13806535465442757
    },
    "G_ST": {
      "mean": 0.9527175907197101,
      "standard_error": 0.0037477397303991525,
      "noise_adequate": true,
      "window": 2294444,
      "effective_sample_size": 1155.1757755059016
    }
  }
}
```

(Abbreviated to the fields this document discusses; the real `report.json`
in this directory has every field, every recorded statistic's own
`window_statistics` entry, and full floating-point precision.)

The run stops at generation 2,625,168 because **D** became precise enough: over
its 2,520,831-generation window (generations 104,338 to 2,625,168) its standard
error, 0.0031, is below the 0.0051 that the requested precision (0.01 at 95%
confidence) demands, and its 74 effective samples are above the floor of 50.
Its window mean, **0.0390**, matches the theoretical 0.0385 and Jost's published
D ≈ 0.04. The final generation's own value, 0.0333, is one draw, not an
averaged estimate.

`G_ST` is estimated in two ways that differ here. In 226,387 of the window's
generations the total heterozygosity is zero (every deme fixed for the same
allele), so `G_ST` is undefined there. The `mean` above averages the defined
generations (0.9527 ± 0.0037); the `value_of_means` form, built from the
window's averaged heterozygosities, is 0.96945 ± 0.00045, which matches the
theoretical `G_ST` (0.96946) and Jost's published ≈ 0.97. H<sub>S</sub> ≈ 0.001
and H<sub>T</sub> ≈ 0.035 show the same picture as the source: nearly every deme
is fixed, and nearly all of them on the same allele.

### Engine identity

`engine_backend: auto` resolved to `generational-vector`. The `lineal`
reference reproduces the report exactly. The same configuration with only
`engine_backend` changed to `lineal`, run on the same 22-thread Linux machine,
stopped at the same generation (2,625,168) with an identical `report.json`
(every field equal, `run_id` aside, which differs because it hashes the
configuration). It took 1 hour 44 minutes, against 3 minutes 24 seconds for
`auto`: about 30 times as long. The automated slow test
(`test/validation/test_examples_auto_backend.py`) skips this example because of
that time; the other examples are rerun on `lineal` there, and the engine
parity tests cover this configuration's shape.

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
| `report.json` | Exact output from `fim run` at this config and seed |
| `manifest.json` | Run metadata: parameters, timing, artifact checksums |
| `README.md` | This document |
