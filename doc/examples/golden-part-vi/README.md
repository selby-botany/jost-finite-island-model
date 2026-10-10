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

## One locus, one run: the average earns its error bar

A single locus, watched by a single replicate, is exactly the case that
once exposed a real defect in `fim`'s own convergence rule: a short
trailing window can *look* stable purely because drift's own
generation-to-generation wobble happens to cancel out for a moment, not
because the run has settled near its true long-run value. An earlier version
of this example fell into that trap: it reported "converged" at generation 400
with D = 0.611, a number that looked like a clean match to the published
ensemble mean (0.604) but was one lucky window among many unlucky ones nearby.

`fim` now avoids it by construction. A run burns in (here 449 generations,
about 5.3 relaxation times of 85), then averages, and stops only when the
average of each watched statistic is known to the requested
[precision](../../configuration.md#precision), correcting for how correlated
consecutive generations
([Convergence](../../convergence.md)). This example watches **D**
(`convergence_statistic: D`), so the run has to settle D. At generation 176,575
the averages over the 176,127-generation window are **D = 0.6080 ± 0.0043**
and **G<sub>ST</sub> = 0.1731 ± 0.0009** (one standard error; every recorded
statistic gets its average and error bar over the same window): both very close
to the published 0.604 and 0.176, with error bars that say how much to trust
them. The run takes longer than the old rule's 400 generations, and that is the
point: `D` swings widely here (a standard deviation of 0.16 from one
generation to the next), so averaging it to ±0.009 (the 95% interval the
precision of 0.01 asks for) takes a long window.

## The real calibration: many loci, many replicates

The genuine test that `fim`'s own mechanics reproduce Jost's theory is not
this one single-locus run at all: it is `test/validation/
test_simulator_equilibrium.py`'s Golden Part VI scenario, averaged over 60
independently seeded replicates of 8 loci each, which lands within 0.04 of
the analytic equilibrium D (`test/validation/convergence-defaults-
evidence.json`). This single-locus example exists to show a complete,
minimal, reproducible configuration whose own D now genuinely converges —
not to stand in for that calibration.

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
