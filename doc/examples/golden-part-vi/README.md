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

A single-locus run from a random start reaches a nearby but distinct
equilibrium whose exact values depend on which allele-frequency trajectory
the seed follows — see `report.json` in this directory for the reproducible
single-run result.

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

Finishes in under one second. `results/golden-part-vi/report.json` will
match `report.json` in this directory exactly.

## Expected output

```json
{
  "converged": true,
  "converged_on": "D",
  "generation": 400,
  "G_ST": 0.24024462671952998,
  "D": 0.610873025590792,
  "E_ST": 0.38259945507022347,
  "K_ST": 0.22807017543859642,
  "Gs": 0.40835,
  "Gd": 0.15890000000000004,
  "Delta": 0.5483333333333333,
  "A_CGD": 1.25,
  "MI": 0.5303954671313937,
  "H_S": 0.59165,
  "H_T": 0.7787375,
  "H_ST": 0.45815476919309406,
  "reason": "statistic converged"
}
```

D converges at generation 400 to **0.611**, close to the published ensemble
mean (D ≈ 0.604). G<sub>ST</sub> is **0.240** against the published
G<sub>ST</sub> ≈ 0.176: a single locus samples one trajectory through
allele-frequency space, so it scatters around the ensemble mean. The
convergence window and generation cap are left on `auto`; they are derived from
this model's relaxation time (about 85 generations), so the run watches D for
a window of 254 generations. See [Convergence defaults](../../convergence.md)
and `test/validation/test_simulator_equilibrium.py` for the full multi-locus
calibration test.

## Files in this directory

| File | Description |
|---|---|
| `config.yaml` | Complete simulation configuration |
| `report.json` | Exact output from `fim run` at this config and seed |
| `manifest.json` | Run metadata: parameters, timing, artifact checksums |
| `README.md` | This document |
