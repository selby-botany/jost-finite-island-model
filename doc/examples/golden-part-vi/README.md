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

## Why this one run does not reach that precision — and why that is honest

A single locus, watched by a single replicate, is exactly the case this
project's own convergence rule cannot resolve to a tight tolerance no
matter how long it runs. A short trailing window can *look* stable — two
neighboring halves land close together — purely because drift's own
generation-to-generation wobble happens to cancel out for a moment, not
because the run has actually settled near its true long-run value. An
earlier version of this example fell into exactly that trap: it reported
"converged" at generation 400 with D = 0.611, a number that looked like a
clean match to the published ensemble mean (0.604) but was, in fact, one
lucky window among many unlucky ones nearby — the true picture, visible
only by watching many more generations, is that a single-locus D keeps
wandering with a standard deviation around 0.1 indefinitely; generation
400 was never special.

`fim` now checks for that directly: alongside the trend check, it asks
whether the trailing window's own mean is actually known to the
requested [convergence_tolerance](../../configuration.md#convergence_tolerance),
correcting for how correlated consecutive generations are
(`fim.convergence.window_statistics`,
[Convergence defaults](../../convergence.md)). For this exact
configuration, the derived window (254 generations, three times the
model's own relaxation time) is nowhere near enough independent
information to know D to ±0.005: even a 10,000-generation run — the
derived cap — never satisfies that, and honestly reports **hitting the
cap**, not convergence, in `report.json`.

That report's own `window_statistics.D` still says something worth
reading: the trailing window's *mean*, 0.5697, sits closer to the
published 0.604 than the single reported point value, 0.5216 — averaging
over the window's own noise helps, even short of the requested precision.
Reaching the requested precision from a single locus and single replicate
would need a window some 30–50 times longer than the one derived here (an
open recalibration question, `20260927-claude-sonnet-5-noise-aware-
convergence-design.md`, `selby/restricted`) — resolving this cleanly, the
way the published ensemble does, instead uses many loci and many
replicates (see the next paragraph), which is the actual, calibrated
validation this project relies on, not this one convenience-sized run.

## The real calibration: many loci, many replicates

The genuine test that `fim`'s own mechanics reproduce Jost's theory is not
this one convenience-sized run at all: it is
`test/validation/test_simulator_equilibrium.py`'s Golden Part VI scenario,
averaged over 60 independently seeded replicates of 8 loci each, which
lands within 0.04 of the analytic equilibrium D
(`test/validation/convergence-defaults-evidence.json`). This single-locus
example exists to show a complete, minimal, reproducible configuration —
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

Finishes in under a minute (10,000 generations, each one written to the
trajectory file). `results/golden-part-vi/report.json` will
match `report.json` in this directory exactly.

## Expected output

```json
{
  "converged": false,
  "converged_on": "D",
  "generation": 10000,
  "G_ST": 0.22949039831716092,
  "D": 0.5216117216117216,
  "reason": "hit the cap",
  "window_statistics": {
    "D": {
      "mean": 0.5696931947724891,
      "standard_error": 0.029458217468817414,
      "noise_adequate": false,
      "window": 254
    }
  }
}
```

(Abbreviated here to the fields this document discusses; the real
`report.json` in this directory has every field, every watched
statistic's own `window_statistics` entry, and full floating-point
precision.)

## Files in this directory

| File | Description |
|---|---|
| `config.yaml` | Complete simulation configuration |
| `report.json` | Exact output from `fim run` at this config and seed |
| `manifest.json` | Run metadata: parameters, timing, artifact checksums |
| `README.md` | This document |
