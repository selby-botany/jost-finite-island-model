# Worked examples

Runnable demonstrations and reproducible simulation scenarios drawn from
the `fim` validation suite. Every example has its own directory with a
`README.md`, a `config.yaml`, and the committed output of its own run:
`manifest.json` and `report.json` for a single run, or `manifest.json`,
`summary.json`, and one `replicate-NNN/` directory per replicate for a
batch. Every result artifact is retained, including scatter plots, pairwise
matrices, convergence histories, and full trajectories. JSONL files are
losslessly compressed and split into parts no larger than 50 MiB for Git.
The desktop app restores them automatically when you open an example.

The desktop app ships every example as a read-only run in its Examples
experiment, grouped by the classes in [`classes.yaml`](classes.yaml)
(see [The Examples experiment](../usage.md#the-examples-experiment)).
The [Configure card examples](#configure-card-examples) are also
mirrored inline in `doc/usage.md`.

## Running an example

```console
fim run doc/examples/<example>/config.yaml \
    --output results/<example> --quiet
```

The `report.json` (or `summary.json`) you get matches the reference for
the same backend and numerical environment. On every backend, FFT/BLAS
reductions used for evidence-window diagnostics can round differently
across platforms. Archive checks allow only tightly bounded rounding in
`window_statistics` floats (relative `1e-12`, absolute `1e-14`); keys,
discrete values, other report fields, and local backend parity remain exact.
The local reference explicitly selects the backend that `auto` resolved to,
so an accelerated case does not also rerun a slower archived backend.
Separate engine tests enforce exact cross-backend parity.
Older vector archives can also use a different random stream; their
statistical comparison is described in the vector example's README.
Most examples finish in a few seconds to
about eight minutes. The calibration examples take longer: Dear-Nolan low
about half an hour, Golden Part VI about two and a half minutes, and
Dear-Nolan high about two minutes. These times were measured with several
examples running at once on a development machine; an idle machine is
faster. Six examples thin their saved trajectory (every generation to
5,000, then one in ten) to keep the committed files small.

For maintainers: `dev/bin/regenerate-example-outputs` reruns the examples
and replaces their committed outputs, and `test/test_doc_examples.py`
(slow) fails when a fresh run no longer matches them (see
[Maintainer scripts](../../dev/bin/README.md#regenerate-example-outputs)).

## Examples

### [golden-part-vi](golden-part-vi/README.md)

**Jost (2008) Part VI** — the primary calibration anchor for `fim`.
Four demes, N = 100, moderate migration (m = 0.01) and mutation
(mu = 0.005). D converges at generation 176,575, averaged over a
176,127-generation window: D = 0.608 ± 0.004 and G_ST = 0.173 ± 0.001.
Published ensemble values (100 replicates, multi-locus engineered
start): G_ST ≈ 0.176, D ≈ 0.604 — see the example's own README for why
one locus needs such a long window, and where the real, calibrated
multi-locus/multi-replicate agreement lives.

### [dear-nolan-low](dear-nolan-low/README.md)

**Dear-Nolan low-migration botanical scenario** — five isolated plant
patches, N = 100, very low migration (m = 0.0001) and negligible mutation
(mu = 0.000001). **This example does not converge within its cap:** it runs the full
derived cap, 399,728 generations (about half an hour), and reports
`converged: false` honestly: D's average is 0.042 ± 0.006 but only about 15
independent samples back it, and the run projects about 1.1 million
generations for the requested precision. Jost's published targets (200
runs) are D ≈ 0.04 and G_ST ≈ 0.97; the run's averages are D 0.042
(published ≈ 0.04) and G_ST 0.964 (published ≈ 0.97). D and G_ST are
not equally noisy for this scenario, see its own README.

### [dear-nolan-high](dear-nolan-high/README.md)

**Dear-Nolan high-migration botanical scenario** — the exact equilibrium
validation case from `test/validation/test_simulator_equilibrium.py`.
The test derives a near-equilibrium initial state
(`_dn2_equilibrium_start`) so the engine is started at the fixed point
rather than slowly integrating from an undifferentiated state;
`reproduce.py` writes that state into `config.yaml` as an explicit `p_0`.
Five replicates of 30 generations land at mean G_ST 0.0219 ± 0.0001 and
mean D 0.908 ± 0.003, matching the predicted equilibrium (G_ST 0.0220,
D 0.909) and the published G_ST ≈ 0.02 and D ≈ 0.90.

## Configure card examples

Each directory contains the canonical `config.yaml` and a README with the
scenario's explanation. The YAML blocks in `doc/usage.md` are generated
from those files, keeping every example visible in the guide and GUI Help.

- [Unequal island sizes with a migration hub](unequal-island-sizes-with-a-migration-hub/README.md)
- [Stepping-stone (spatial) migration](stepping-stone-spatial-migration/README.md)
- [Literature distance statistics from an explicit founder split](literature-distance-statistics-from-an-explicit-founder-split/README.md)
- [Equilibrium-split founding](equilibrium-split-founding/README.md)
- [Stochastic migrant counts](stochastic-migrant-counts/README.md)
- [Finite-length alleles (the K-allele model)](finite-length-alleles-the-k-allele-model/README.md)
- [Wright-Takahata finite-deme correction](wright-takahata-finite-deme-correction/README.md)
- [Kimura-Weiss isolation by distance](kimura-weiss-isolation-by-distance/README.md)
- [Per-base mutation rate across unequal locus lengths](per-base-mutation-rate-across-unequal-locus-lengths/README.md)
- [Several convergence statistics](several-convergence-statistics/README.md)
- [Within-run sigma band](within-run-sigma-band/README.md)
- [An adaptive replicate batch with a confidence interval](an-adaptive-replicate-batch-with-a-confidence-interval/README.md)
- [A large-d batch under generational-vector](a-large-d-batch-under-generational-vector/README.md)
- [A long-locus batch under the generational engine](a-long-locus-batch-under-the-generational-engine/README.md)

## Further reading

- [Configuration reference](../configuration.md) — every parameter with
  defaults and constraints
- [Usage guide](../usage.md) — all `fim` commands, output schemas, and
  worked examples with inline YAML generated from each example's
  [standalone config](README.md#configure-card-examples)
- Calibration validation tests:
  `test/validation/test_simulator_equilibrium.py` — the equilibrium tests,
  including the Dear-Nolan low- and high-migration scenarios
- Calibration evidence:
  `test/validation/statistical-calibration-evidence.json` — raw
  Monte-Carlo results for all three scenarios (Part VI, DN-low, DN-high)
