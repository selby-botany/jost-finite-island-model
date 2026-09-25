# Dear-Nolan low-migration example

This example implements the low-migration scenario from the Dear-Nolan
botanical simulations: five demes of 100 individuals each with very low
migration and negligible mutation. It runs to the population's equilibrium,
which takes tens of thousands of generations.

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
100 replicates): **G<sub>ST</sub> ≈ 0.970, D ≈ 0.038**.

## Why the run is long

A run from a random start passes through two stages. In the first few hundred
generations drift fixes each deme on one of its two founding alleles, usually
on *different* alleles in different demes. That state is a plateau: G<sub>ST</sub>
is exactly 1.0 and flat, and D is high (about 0.6). It is not the equilibrium.
Only when migration and mutation have had time to spread one allele across the
demes does D fall toward 0.04. The population needs about 19,700 generations
to forget its starting state (the relaxation time; see
[Convergence defaults](../../convergence.md)).

`config.yaml` therefore leaves `convergence_window` and `max_generations` on
`auto`. `fim run` derives a window of 59,078 generations and a cap of 295,390,
prints them, and stops once D has stayed steady for that long. Earlier
versions stopped this scenario after a few hundred generations, on the
plateau, with a large D.

## Parameters

| Parameter | Value | Meaning |
|---|---|---|
| N | 100 | Individuals per deme |
| d | 5 | Number of demes |
| m | 0.0001 | Very low symmetric migration rate |
| &mu; | 0.000001 | Negligible per-locus mutation rate |
| loci | 30 | Independent loci of length 200 (infinite-alleles model) |
| convergence | `auto` | Window and cap derived from the model |
| seed | 20260825 | Exact RNG seed |

Thirty independent loci stand in for many separate simulation runs, as in the
source correspondence (each locus is an independent replicate of the same
demographic process).

## Running the example

```console
fim run doc/examples/dear-nolan-low/config.yaml \
    --output results/dear-nolan-low --quiet
```

Takes several minutes (about 97,000 generations of 30 loci) and writes a large
`trajectory.jsonl`. `results/dear-nolan-low/report.json` will match
`report.json` in this directory exactly.

## Expected output

```json
{
  "converged": true,
  "converged_on": "D",
  "generation": 97462,
  "G_ST": 0.9922101418926843,
  "D": 0.1383187273219821,
  "E_ST": 0.10253455467966041,
  "K_ST": 0.07222222222222223,
  "Gs": 0.999132,
  "Gd": 0.8609333333333334,
  "Delta": 0.13906666666666664,
  "A_CGD": 0.13333333333333333,
  "MI": 0.1650229996359928,
  "H_S": 0.0008679999999999992,
  "H_T": 0.1114269333333333,
  "H_ST": 0.11080559092091388,
  "reason": "statistic converged"
}
```

The run stopped at generation 97,462. G<sub>ST</sub> is 0.992 and D is 0.138,
against the published ensemble values 0.970 and 0.038. This is one run of 30
loci, and D is a noisy statistic at that size: a single locus that still
differs between demes moves D by several hundredths. Across independent runs
the mean is close to the published value (six replicates of ten loci gave a
mean D of 0.040 with a standard error of 0.020; the measurements are in
`test/validation/convergence-defaults-evidence.json`). H<sub>S</sub> ≈ 0.0009
and H<sub>T</sub> ≈ 0.11 show the same picture as the source: nearly every
deme is fixed, and nearly all of them on the same allele.

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
