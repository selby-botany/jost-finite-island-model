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
  "generation": 148,
  "G_ST": 0.12498550148299113,
  "D": 0.36986368539766584,
  "E_ST": 0.38611672455255747,
  "K_ST": 0.4871794871794872,
  "H_S": 0.6601,
  "H_T": 0.7543875,
  "H_ST": 0.2773977640482494,
  "reason": "statistic converged"
}
```

D converges at generation 148 to **0.370** and G<sub>ST</sub> to **0.125**. The
single-locus values sit below the published ensemble means (D ≈ 0.604,
G<sub>ST</sub> ≈ 0.176) because a single locus samples one trajectory through
allele-frequency space; the ensemble mean requires many replicates and
a multi-locus configuration. See
`test/validation/test_simulator_equilibrium.py` for the full multi-locus
calibration test.

## Files in this directory

| File | Description |
|---|---|
| `config.yaml` | Complete simulation configuration |
| `report.json` | Exact output from `fim run` at this config and seed |
| `manifest.json` | Run metadata: parameters, timing, artifact checksums |
| `README.md` | This document |
