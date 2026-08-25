# Dear-Nolan low-migration example

This example implements the low-migration scenario from the Dear-Nolan
botanical simulations: five demes of 100 individuals each with very low
migration and negligible mutation. At these parameters genetic drift
dominates and demes fix on distinct alleles, producing near-complete
differentiation.

## Biological context

The Dear-Nolan scenarios (referenced in the `fim` calibration suite) model
isolated plant populations where seed dispersal between sites is rare.
The low-migration configuration (m = 0.0001, &mu; = 0.000001) represents
patches so isolated that migration rarely occurs across the lifetime of a
study, and mutation is slow enough that new alleles arise only over
geological time.

In this regime the finite-island model behaves as a set of nearly isolated
populations. Drift brings each deme to fixation independently, so the
population-level allele pool retains diversity only because different demes
fix on different alleles. G<sub>ST</sub> approaches 1.0 as between-deme diversity
(H<sub>ST</sub>) approaches the total diversity H<sub>T</sub>.

Published ensemble values (engineered equilibrium start, multi-locus,
100 replicates): **G<sub>ST</sub> ≈ 0.970, D ≈ 0.038**.

A single-locus run from a random two-allele start demonstrates the same
fixation dynamic: by generation 981 with this seed all five demes have
fixed on a single shared allele (H<sub>S</sub> = 0) while between-deme diversity
(H<sub>ST</sub> ≈ 0.48) reflects the period before the last locus fixed.
G<sub>ST</sub> = 1.0 at that point because there is no within-deme diversity
left to compare against total diversity.

The small discrepancy from the published G<sub>ST</sub> ≈ 0.970 (not 1.0) arises
because the published value is a multi-locus ensemble average: most loci
have already fixed but a few remain polymorphic, pulling the mean below
1.0. A multi-locus configuration with an engineered near-equilibrium start
(as used in `test/validation/test_simulator_equilibrium.py`) reproduces
the published values.

## Parameters

| Parameter | Value | Meaning |
|---|---|---|
| N | 100 | Individuals per deme |
| d | 5 | Number of demes |
| m | 0.0001 | Very low symmetric migration rate |
| &mu; | 0.000001 | Negligible per-locus mutation rate |
| locus length | 200 | Allele-space size (infinite-alleles model) |
| seed | 20260825 | Exact RNG seed |

## Running the example

```console
fim run doc/examples/dear-nolan-low/config.yaml \
    --output results/dear-nolan-low --quiet
```

Finishes in about one second. `results/dear-nolan-low/report.json` will
match `report.json` in this directory exactly.

## Expected output

```json
{
  "converged": true,
  "converged_on": "G_ST",
  "generation": 981,
  "G_ST": 1.0,
  "D": 0.5999999999999999,
  "E_ST": 0.4181656600790515,
  "K_ST": 0.25,
  "H_S": 0.0,
  "H_T": 0.47999999999999987,
  "H_ST": 0.47999999999999987,
  "reason": "statistic converged"
}
```

G<sub>ST</sub> converges to **1.0** at generation 981: the within-deme heterozygosity
(H<sub>S</sub>) has reached zero — every deme is monomorphic. H<sub>T</sub> ≈ 0.48 reflects the
diversity still present across the total population because the allele that
fixed differs among some demes at convergence time.

## Relationship to the published calibration

The `fim` calibration test (`test_dear_nolan_low_migration_scenario_via_engine`)
uses:

- 26 loci with an engineered near-equilibrium initial-frequency distribution
- 12 replicates averaged together
- A different seed (884000)

That configuration holds the population near the mathematical fixed point
where both within-deme and between-deme allele-identity recursions are
balanced — the stationary distribution that a real isolated plant metapopulation
would occupy if observed long after colonization. The single-locus
random-start run here shows the *approach* to that regime: the inevitable
fixation that occurs when drift is unchecked.

## Files in this directory

| File | Description |
|---|---|
| `config.yaml` | Complete simulation configuration |
| `report.json` | Exact output from `fim run` at this config and seed |
| `manifest.json` | Run metadata: parameters, timing, artifact checksums |
| `README.md` | This document |
