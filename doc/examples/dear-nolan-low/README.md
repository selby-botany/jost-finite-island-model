# Dear-Nolan low-migration example

This example implements the low-migration scenario from the Dear-Nolan
botanical simulations: five demes of 100 individuals each with very low
migration and negligible mutation. It is meant to run to the population's
equilibrium, which takes tens of thousands of generations.

## Status: this example reaches its cap, honestly

**This run does not reach the requested precision for `D` before its cap.** It
burns in for 104,338 generations, averages for the 295,391 that follow, and
stops at the cap (399,728 generations) with `converged_on: null` and
`"reason": "hit the cap"`. It still reports the averages it has, each with the
error bar it actually has. That is the intended behavior for a scenario this
slow: a run at this precision needs more than a million generations (the
report's `projected_generations` for `D` is 1,101,457), and the run says so
instead of pretending.

`D` is the slow statistic here. At low migration, `D` swings over the
population's relaxation time (about 19,700 generations), so the 295,391
generations it averaged hold only about 15 independent draws of it
(`effective_sample_size` 14.8); the rule wants 50. `G_ST` is far less noisy:
its average meets the precision. The start of the window also differs from its
end for `D` (Geweke `z` = 4.0, above the alert level of 3), a sign that the
slowest tail of the burn-in may not have fully decayed. To reach the precision,
raise `max_generations`, use more loci, or ask for less precision; the
[Convergence](../../convergence.md) guide says how.

Jost's published targets for these parameters (d = 5, N = 100, m = 0.0001,
mu = 0.000001, averaged over 200 runs) are **D ≈ 0.04 and G<sub>ST</sub> ≈
0.97**. The committed run's averages land close to them (D = 0.0420 ± 0.0060,
G<sub>ST</sub> = 0.9640 ± 0.0036; see "Expected output"), and the error bars
say how much to trust each one.

The committed outputs here were generated on the textbook mutation model
(each gene copy mutates independently; each generation runs migrate, drift,
then mutate). The configuration thins nothing: the run is long but writes only
about 10 MB of trajectory.

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

`config.yaml` therefore leaves `convergence_burn_in` and `max_generations` on
`auto`. `fim run` derives a burn-in of 104,338 generations (about 5.3
relaxation times) and a cap of 399,728, prints them, and averages after the
burn-in until each watched statistic's average is known to the configured
precision (see [Convergence](../../convergence.md)). Earlier versions stopped
this scenario after a few hundred generations, on the plateau, with a large
D. This run watches **D** and **G<sub>ST</sub>** (the default), and `D` never
reaches the requested precision here: the run ends at the cap instead of
converging (see "Status," above, and "Expected output," below).

## Parameters

| Parameter | Value | Meaning |
|---|---|---|
| N | 100 | Individuals per deme |
| d | 5 | Number of demes |
| m | 0.0001 | Very low symmetric migration rate |
| &mu; | 0.000001 | Negligible per-locus mutation rate |
| loci | 30 | Independent loci of length 200 (infinite-alleles model) |
| convergence | `auto` | Burn-in and cap derived from the model |
| seed | 20260825 | Exact RNG seed |

Thirty independent loci stand in for many separate simulation runs, as in the
source correspondence (each locus is an independent replicate of the same
demographic process).

## Running the example

```console
fim run doc/examples/dear-nolan-low/config.yaml \
    --output results/dear-nolan-low --quiet
```

Takes about half an hour on a development machine (399,728 generations, the
derived cap, of 30 loci; 1,653 s with three examples running at once), and
less on an idle one, and writes a trajectory log of about 10 MB (`fim export`
turns it into a multi-gigabyte `trajectory.jsonl`).
`results/dear-nolan-low/report.json` will match `report.json` in this
directory exactly.

## Expected output

```json
{
  "converged": false,
  "converged_on": null,
  "generation": 399728,
  "G_ST": 0.9950647563784285,
  "D": 0.033272391955738154,
  "reason": "hit the cap",
  "window_statistics": {
    "D": {
      "mean": 0.04199332710152649,
      "standard_error": 0.0059656816705982016,
      "noise_adequate": false,
      "window": 295391,
      "effective_sample_size": 14.81222946079856,
      "projected_generations": 1101457,
      "geweke_z": 3.99485041785269
    },
    "G_ST": {
      "mean": 0.9639864876693424,
      "standard_error": 0.0035756415736778514,
      "noise_adequate": true,
      "window": 288703,
      "effective_sample_size": 498.33099792451367
    }
  }
}
```

(Abbreviated to the fields this document discusses; the real `report.json`
in this directory has every field, every recorded statistic's own
`window_statistics` entry, and full floating-point precision.)

The run ends at the cap, generation 399,728, so `converged_on` is `null`: it
converged on nothing. That is because **D** never became precise enough:
over its 295,391-generation window (generations 104,338 to 399,728), its
standard error, 0.0060, is still above the 0.0051 that the requested precision
(0.01 at 95% confidence) demands, and its 15 effective samples are below the
floor of 50. Its window mean, **0.0420**, is nonetheless close to Jost's
published D ≈ 0.04. The final generation's own value, 0.0333, is near it too,
but a single generation is one draw, not an averaged estimate.

`window_statistics.G_ST` tells a different story: the same window, but
`noise_adequate: true`. G<sub>ST</sub>'s mean, **0.964**, is close to Jost's
published G<sub>ST</sub> ≈ 0.97, and its noise (`effective_sample_size` near
498, against D's 15 over the same window) is far smaller here: D and
G<sub>ST</sub> are not equally noisy for this scenario, even though both are
computed from the same identity matrix. H<sub>S</sub> ≈ 0.001 and H<sub>T</sub>
≈ 0.035 show the same picture as the source: nearly every deme is fixed, and
nearly all of them on the same allele.

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
