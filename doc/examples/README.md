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

Every example finishes in seconds on a laptop, except the high-migration
scenario's direct write-up is deliberately a lightweight 5-replicate
reproduction of the test case rather than the full Monte Carlo sweep used in
calibration.

## Examples

### [golden-part-vi](golden-part-vi/README.md)

**Jost (2008) Part VI** — the primary calibration anchor for `fim`.
Four demes, N = 100, moderate migration (m = 0.01) and mutation
(mu = 0.005). Produces an intermediate differentiation regime:
G_ST ≈ 0.125, D ≈ 0.370 at this seed. Published ensemble values
(100 replicates, multi-locus engineered start): G_ST ≈ 0.176, D ≈ 0.604.

### [dear-nolan-low](dear-nolan-low/README.md)

**Dear-Nolan low-migration botanical scenario** — five isolated plant
patches, N = 100, very low migration (m = 0.0001) and negligible mutation
(mu = 0.000001). Drift dominates: demes fix independently, G_ST → 1.0
at generation 981. Published ensemble values: G_ST ≈ 0.970, D ≈ 0.038.

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
