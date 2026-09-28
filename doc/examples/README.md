# Worked examples

Three reproducible simulation scenarios drawn from the `fim` validation
suite. Two are plain YAML demos; the high-migration Dear-Nolan case uses a
Python-generated near-equilibrium initial state, because the published
stationary condition is not representable as a simple YAML `p_0` table.

Each subdirectory contains the exact example input and the corresponding
results, plus a `README.md` explaining the biological context.

## Running an example

For the YAML examples:

```console
fim run doc/examples/<example>/config.yaml \
    --output results/<example> --quiet
```

For the Dear-Nolan high example, run the reproduction script directly:

```console
python3 doc/examples/dear-nolan-high/reproduce.py
```

Dear-Nolan low takes about 25 minutes; Golden Part VI takes a little over
two minutes; Dear-Nolan high finishes in seconds on a laptop. The
high-migration scenario's direct write-up is deliberately a lightweight
5-replicate reproduction of the test case rather than the full Monte
Carlo sweep used in calibration.

## Examples

### [golden-part-vi](golden-part-vi/README.md)

**Jost (2008) Part VI** — the primary calibration anchor for `fim`.
Four demes, N = 100, moderate migration (m = 0.01) and mutation
(mu = 0.005). D genuinely converges at generation 130,194, its own
evidence window grown to 130,048 generations to confirm precision — a
trailing-window D of 0.624 ± 0.004. G_ST is left honestly unconfirmed
(this example watches D alone), at 0.193 from the un-grown base window.
Published ensemble values (100 replicates, multi-locus engineered
start): G_ST ≈ 0.176, D ≈ 0.604 — see the example's own README for why
D reaches that precision here while G_ST does not, and where the real,
calibrated multi-locus/multi-replicate agreement lives.

### [dear-nolan-low](dear-nolan-low/README.md)

**Dear-Nolan low-migration botanical scenario** — five isolated plant
patches, N = 100, very low migration (m = 0.0001) and negligible mutation
(mu = 0.000001). Runs the full derived cap, 295,390 generations (about 25
minutes): almost all demes fixed for the same allele. D — the statistic
this example watches — is the harder of the two to pin down here, ending
honestly at the cap with a trailing-window mean of 0.053 (published
D ≈ 0.038); G_ST, computed the same way but not gated on, is already
precise at 0.969 (published G_ST ≈ 0.970) — D and G_ST are not equally
noisy for this scenario, see its own README for why.

### [dear-nolan-high](dear-nolan-high/README.md)

**Dear-Nolan high-migration botanical scenario** — the exact equilibrium
validation case from `test/validation/test_simulator_equilibrium.py`.
This is not a YAML-only config: the test derives a near-equilibrium
initial state (`_dn2_equilibrium_start`) so the engine is started at the
fixed point rather than slowly integrating from an undifferentiated state.
The reproduced 5-replicate sample lands at mean G_ST ≈ 0.0219 and
mean D ≈ 0.9079, in line with the published G_ST ≈ 0.02 and D ≈ 0.90.

## Further reading

- [Configuration reference](../configuration.md) — every parameter with
  defaults and constraints
- [Usage guide](../usage.md) — all `fim` commands, output schemas, and
  additional worked examples with inline YAML
- Calibration validation tests:
  `test/validation/test_simulator_equilibrium.py` — the equilibrium tests,
  including the Dear-Nolan low- and high-migration scenarios
- Calibration evidence:
  `test/validation/statistical-calibration-evidence.json` — raw
  Monte-Carlo results for all three scenarios (Part VI, DN-low, DN-high)
