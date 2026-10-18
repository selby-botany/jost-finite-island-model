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

## One locus per replicate: the batch earns its error bar

A single locus, watched by a single replicate, is exactly the case that
once exposed a real defect in `fim`'s own convergence rule: a short
trailing window can *look* stable purely because drift's own
generation-to-generation wobble happens to cancel out for a moment, not
because the run has settled near its true long-run value. An earlier version
of this example fell into that trap: it reported "converged" at generation 400
with D = 0.611, a number that looked like a clean match to the published
ensemble mean (0.604) but was one lucky window among many unlucky ones nearby.

`fim` now avoids it by construction. Each replicate burns in (here 449
generations, about 5.3 relaxation times of 85), averages over a
1,693-generation window, and stops. The batch then adds replicates until the
95% confidence interval across them is within the requested
[precision](../../configuration.md#precision), here ±0.01
([Convergence](../../convergence.md)). This example watches **D**, so the batch
has to settle D: it needed 32 replicates, where the minimum is 10, and took
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
why 32 replicates were needed. The earlier single-run version of this example
averaged one long window (176,127 generations) and reported D = 0.608 ± 0.004
(one standard error).

## The real calibration: many seeds, many loci

The genuine test that `fim`'s own mechanics reproduce Jost's theory is not
this one single-locus run. `test/validation/test_simulator_equilibrium.py`
checks the Golden Part VI scenario against the analytic equilibrium, and
`test/validation/test_convergence_rule_calibration.py` runs the convergence
rule on it for 12 fixed seeds of 4 loci each: the averages scatter by 0.008
around 0.603, the analytic D is 0.603, and no run misses its target by more
than its error bar allows (`test/validation/convergence-rule-calibration-
evidence.json`). This single-locus example exists to show a complete,
minimal, reproducible configuration whose own D genuinely converges, not to
stand in for that calibration.

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

(Abbreviated and rounded to the fields this document discusses; the real
`report.json` in this directory has every field, every recorded statistic's
own `window_statistics` entry, and full floating-point precision.)

Both averages match the published ensemble means (D ≈ 0.604, G<sub>ST</sub> ≈
0.176) within a few standard errors, and both error bars were earned by the
run (only D's was required to meet the precision; G<sub>ST</sub>'s happens
to). The final generation's own single values, D = 0.6205 and G<sub>ST</sub> =
0.2121, are real too but noisier: one stochastic draw each, not averaged
estimates.

## Files in this directory

| File | Description |
|---|---|
| `config.yaml` | Complete simulation configuration |
| `report.json` | Exact output from `fim run` at this config and seed |
| `manifest.json` | Run metadata: parameters, timing, artifact checksums |
| `README.md` | This document |
