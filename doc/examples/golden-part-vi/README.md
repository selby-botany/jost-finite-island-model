# Golden example — Jost (2008) Part VI

This example reproduces the canonical parameter set from Part VI of
[Jost (2008)](https://doi.org/10.1111/j.1365-294X.2008.03887.x): four
demes of 100 individuals each, moderate migration, and a mutation rate
high enough that allelic diversity is maintained at equilibrium. It is the
primary validation anchor for the `fim` simulator.

## Biological context

Jost's Part VI parameters sit in an intermediate differentiation regime:
migration is frequent enough to homogenize allele frequencies somewhat
(G<sub>ST</sub> ~ 0.18), but not so frequent that the demes approach panmixia. At
these parameters the traditional G<sub>ST</sub> underestimates differentiation relative
to D because overall heterozygosity is high — the central empirical
observation motivating D as a replacement statistic.

Published equilibrium values (100-replicate ensemble, multi-locus engineered
start): **G<sub>ST</sub> ≈ 0.176, D ≈ 0.604**.

## One locus per replicate: uncertainty across runs

A single-locus statistic can have substantial generation-to-generation
variation. The batch estimates its uncertainty across replicates rather
than treating a short, apparently stable segment of one stochastic
trajectory as evidence of equilibrium. Each replicate burns in (here 449
generations, about 5.3 relaxation times of 85), averages over a
1,693-generation window, and stops. The batch then adds replicates until the
95% confidence interval across them is within the requested
[precision](../../configuration.md#precision), here ±0.01
([Convergence](../../convergence.md)). The batch watches **D** and needed 32
replicates, above the minimum of 10, to meet its precision criterion. It took
under three minutes on a development machine. Across the replicates
**D = 0.6247 ± 0.0098** and **G<sub>ST</sub> = 0.1742 ± 0.0023** (95%
confidence half-widths). The interval for G<sub>ST</sub>, 0.172 to 0.177,
contains the published 0.176. The interval for D, 0.615 to 0.635, lies a little
above the published 0.604; computing D from the averaged heterozygosities
H<sub>S</sub> and H<sub>T</sub> instead gives 0.614, because D is a nonlinear
function of them and the average of its values differs from its value at the
averages (see the section "Which average?" in
[Convergence](../../convergence.md)). `D` swings widely here (a standard
deviation of 0.16 from one generation to the next in a single run), which is
why 32 replicates were needed.

## Calibration evidence

The one-locus example is distinct from the replicated calibration tests.
`test/validation/test_simulator_equilibrium.py`
checks the Golden Part VI scenario against the analytic equilibrium, and
`test/validation/test_convergence_rule_calibration.py` runs the convergence
rule on it for 12 fixed seeds of 4 loci each: the averages scatter by 0.008
around 0.603, and the analytic D is 0.603. The evidence is recorded in
`test/validation/convergence-rule-calibration-evidence.json`. This example
provides a reproducible single-locus batch and does not replace the
multi-seed calibration.

## Parameters

| Parameter | Value | Meaning |
|---|---|---|
| N | 100 | Individuals per deme |
| d | 4 | Number of demes |
| m | 0.01 | Symmetric migration rate |
| &mu; | 0.005 | Per-locus mutation rate |
| locus length | 200 | Allele-space size (infinite-alleles model) |
| seed | 20260825 | Exact RNG seed |

## Running the example

```console
fim run doc/examples/golden-part-vi/config.yaml \
    --output results/golden-part-vi --quiet
```

Takes about two and a half minutes on a development machine (176,575
generations of one locus), and writes a trajectory file of about 20 MB: the
configuration thins the saved trajectory (every generation to 5,000, then one
in ten), which leaves the statistics and the report unchanged and makes the file
about a tenth of the size. `results/golden-part-vi/report.json` will match
`report.json` in this directory exactly.

## Expected output

```json
{
  "converged": true,
  "converged_on": "D",
  "generation": 176575,
  "G_ST": 0.21210048501430168,
  "D": 0.6205,
  "reason": "statistic converged",
  "window_statistics": {
    "D": {
      "mean": 0.6080,
      "standard_error": 0.0043,
      "noise_adequate": true,
      "window": 176127
    },
    "G_ST": {
      "mean": 0.1731,
      "standard_error": 0.0009,
      "noise_adequate": true,
      "window": 176127
    }
  }
}
```

(Abbreviated and rounded to the fields discussed here; the complete
`report.json` in this directory has every field, every recorded statistic's
own `window_statistics` entry, and full floating-point precision.)

Both averages match the published ensemble means (D ≈ 0.604, G<sub>ST</sub> ≈
0.176) within a few standard errors. Only D was required to meet the
precision target; G<sub>ST</sub> also meets it. The final-generation values,
D = 0.6205 and G<sub>ST</sub> = 0.2121, are individual stochastic
observations rather than averages over the evidence window.

## Files in this directory

| File | Description |
|---|---|
| `config.yaml` | Complete simulation configuration |
| `report.json` | Exact output from `fim run` at this config and seed |
| `manifest.json` | Run metadata: parameters, timing, artifact checksums |
| `README.md` | This document |
