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

## A single locus's own D genuinely converges; G<sub>ST</sub> does not, honestly

A single locus, watched by a single replicate, is exactly the case that
first exposed a real defect in `fim`'s own convergence rule: a short
trailing window can *look* stable — two neighboring halves land close
together — purely because drift's own generation-to-generation wobble
happens to cancel out for a moment, not because the run has actually
settled near its true long-run value. An earlier version of this example
fell into exactly that trap: it reported "converged" at generation 400
with D = 0.611, a number that looked like a clean match to the published
ensemble mean (0.604) but was, in fact, one lucky window among many
unlucky ones nearby.

`fim` now checks for that directly: alongside the trend check, it asks
whether the trailing window's own mean is actually known to the requested
[convergence_tolerance](../../configuration.md#convergence_tolerance),
correcting for how correlated consecutive generations are
(`fim.convergence.window_statistics`,
[Convergence defaults](../../convergence.md)). Once the trend genuinely
flattens, that evidence window keeps growing — not staying fixed at the
derived 254 generations — until the noise itself has been averaged down
enough, or the run's own cap arrives. For **D**, the statistic this
example actually watches (`convergence_statistic: D`), that window grew
to 130,048 generations before its own mean, **0.6237 ± 0.0042**, was
finally precise enough — very close to the published 0.604, and a real,
earned result, not a lucky one: the run stops at generation 130,194.

**G<sub>ST</sub> is a different story, deliberately left honest rather than
implied.** Only the statistic a run actually watches gets that same
growing treatment; `report.json`'s own `window_statistics.G_ST` still
reflects the plain, un-grown 254-generation window (mean 0.193, standard
error 0.018) — nowhere near the requested precision, and correctly marked
`"noise_adequate": false`. Watching **G_ST** as well (`convergence_
statistic: [D, G_ST]`) would earn it the same growing treatment D gets
here, at the cost of a longer run; that is a choice for a future revision
of this example, not implied by the one shown.

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

Takes a little over two minutes (130,194 generations, each one written to
the trajectory file). `results/golden-part-vi/report.json` will match
`report.json` in this directory exactly.

## Expected output

```json
{
  "converged": true,
  "converged_on": "D",
  "generation": 130194,
  "G_ST": 0.13905013986224318,
  "D": 0.5418424753867791,
  "reason": "statistic converged",
  "window_statistics": {
    "D": {
      "mean": 0.623650057980255,
      "standard_error": 0.004193292255722906,
      "noise_adequate": true,
      "window": 130048
    },
    "G_ST": {
      "mean": 0.19330519212198638,
      "standard_error": 0.018257804320953017,
      "noise_adequate": false,
      "window": 254
    }
  }
}
```

(Abbreviated here to the fields this document discusses; the real
`report.json` in this directory has every field, every recorded
statistic's own `window_statistics` entry, and full floating-point
precision.)

D converges to **0.624** (window mean), close to the published ensemble
mean (D ≈ 0.604). The final generation's own single point value, 0.542,
is real too but noisier — one stochastic draw, not the averaged estimate.
G<sub>ST</sub>'s own window mean, **0.193**, is close to the published
G<sub>ST</sub> ≈ 0.176 as well, even though its own window was never grown to
confirm that precision — a single locus samples one trajectory through
allele-frequency space, so both statistics scatter around their own
ensemble means; watching G<sub>ST</sub> directly, not just D, would confirm
whether that particular closeness holds up or is itself a lucky draw.

## Files in this directory

| File | Description |
|---|---|
| `config.yaml` | Complete simulation configuration |
| `report.json` | Exact output from `fim run` at this config and seed |
| `manifest.json` | Run metadata: parameters, timing, artifact checksums |
| `README.md` | This document |
