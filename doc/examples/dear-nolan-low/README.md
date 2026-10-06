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
prints them, and stops once D has stayed steady for that long *and* the
window's own mean is actually known to the configured tolerance — not just
flat, but precise (see [Convergence defaults](../../convergence.md)'s own
"Is the reported value actually precise enough?"). Earlier versions stopped
this scenario after a few hundred generations, on the plateau, with a large
D. This run watches **D** specifically, and D turns out to be the harder of
the two statistics to pin down here: its own evidence window grows to
236,312 generations without quite reaching the requested precision, and the
run ends honestly reporting the cap, not convergence — see "Expected
output," below, for what that still says.

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

Takes about 20 to 25 minutes on ordinary development hardware, and longer on a
busy machine (295,390 generations — the derived cap — of 30 loci), and
writes a large `trajectory.jsonl`. `results/dear-nolan-low/
report.json` will match `report.json` in this directory exactly.

## Expected output

```json
{
  "converged": false,
  "converged_on": null,
  "generation": 295390,
  "G_ST": 0.9487036088409319,
  "D": 0.06748576189094388,
  "reason": "hit the cap",
  "window_statistics": {
    "D": {
      "mean": 0.0527925006091829,
      "standard_error": 0.010682169871340578,
      "noise_adequate": false,
      "window": 236312
    },
    "G_ST": {
      "mean": 0.9688172150934331,
      "standard_error": 0.002533421965157816,
      "noise_adequate": true,
      "window": 59078
    }
  }
}
```

(Abbreviated to the fields this document discusses; the real `report.json`
in this directory has every field, every recorded statistic's own
`window_statistics` entry, and full floating-point precision.)

The run ends at the cap, generation 295,390, so `converged_on` is `null`:
it converged on nothing. That is because **D** — the statistic
this example actually watches — is the more stubborn of the two to pin
down: its own evidence window grew to 236,312 generations, and its
standard error, 0.0107, is still a little over twice the 0.005 the
requested tolerance demands. Its window mean, **0.0528**, is nonetheless
close to the published ensemble D ≈ 0.038, and closer than the final
generation's own single point value, 0.0675.

`window_statistics.G_ST` tells a different story, computed the same way
(the fixed, un-grown 59,078-generation window, since only D gated this
run's own stop decision) but already `noise_adequate: true`: G<sub>ST</sub>'s
own mean, **0.969**, matches the published G<sub>ST</sub> ≈ 0.970 closely, and
its own noise (`effective_sample_size` near 292, against D's 8.5 at a
window 4x longer) is far smaller here — D and G<sub>ST</sub> are not equally
noisy for this scenario, even though both are computed from the same
identity matrix. H<sub>S</sub> ≈ 0.001 and H<sub>T</sub> ≈ 0.03 (also
`noise_adequate: true` at the base window) show the same picture as the
source: nearly every deme is fixed, and nearly all of them on the same
allele.

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
