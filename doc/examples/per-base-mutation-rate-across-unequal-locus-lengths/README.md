# Per-base mutation rate across unequal locus lengths

## What this demonstrates

`mu_b` sets a per-base mutation probability. The simulator derives each
locus's per-locus `mu` from its own length, so the two loci here (50 and
500 bases) have different mutation rates while sharing one per-base
rate.

The manifest records the expanded per-locus rates, 0.0009995 for the
50-base locus and 0.0099503 for the 500-base one. This demonstrates
configuration behavior rather than a particular literature result.

The run converges at generation 231, after a few seconds on ordinary
development hardware, with a trailing-window mean `D` of 0.128 ± 0.009
(one standard error) pooled over both loci. This is a premature stop:
generation 231 is the first generation its 232-generation window can
fill, so the window still covers the approach from the starting state.
Treat that `D` as an early value, not an equilibrium estimate. The
planned convergence redesign addresses this; the configuration is left
unchanged until then.

## Why the tolerance is 0.03

The configuration sets `convergence_tolerance: 0.03`, three times the
default. A run stops only once its trailing-window mean is known to half
the tolerance. The 500-base locus mutates fast enough to hold many
alleles, which makes each generation slow to simulate, so at the default
0.01 the run needs about 60,000 generations and several minutes. This
example is about the derived rates in the manifest, not about a precise
`D`, so it accepts a mean known to about ±0.015.

## Run

```console
fim run doc/examples/per-base-mutation-rate-across-unequal-locus-lengths/config.yaml \
    --output results/per-base-mutation-rate-across-unequal-locus-lengths --quiet
```

See the [usage-guide explanation](../../usage.md#per-base-mutation-rate-across-unequal-locus-lengths)
and the [configuration reference](../../configuration.md).
