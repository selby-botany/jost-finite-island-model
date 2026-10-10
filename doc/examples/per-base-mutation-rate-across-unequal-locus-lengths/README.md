# Per-base mutation rate across unequal locus lengths

## What this demonstrates

`mu_b` sets a per-base mutation probability. The simulator derives each
locus's per-locus `mu` from its own length, so the two loci here (50 and
500 bases) have different mutation rates while sharing one per-base
rate.

The manifest records the expanded per-locus rates, 0.0009995 for the
50-base locus and 0.0099503 for the 500-base one. This demonstrates
configuration behavior rather than a particular literature result.

The run converges at generation 17,345, after about twenty seconds on a
development machine. It burns in for 1,251 generations, then averages over a
16,095-generation window: `D` = 0.209 ± 0.013 (one standard error) and `G_ST`
= 0.059 ± 0.001, pooled over both loci. The burn-in is sized from the slowest
locus: the 50-base locus mutates ten times less than the 500-base one, so it
takes about ten times longer to forget its starting state (a relaxation time
of 250 generations), and the derived burn-in and cap follow it. An earlier
version averaged the two rates, derived too short a burn-in, and stopped at
generation 231 with `D` still on its approach from the starting state.

## Why the precision is 0.03

The configuration sets `precision: 0.03`, three times the
default. A run stops once the average of each watched statistic is known to
plus or minus the precision at 95% confidence (a standard error of at most
0.03 / 1.96 ≈ 0.015). The 500-base locus mutates fast enough to hold many
alleles, which makes each generation slow to simulate, and the window needed
grows as one over the square of the precision, so the default 0.01 would take
about nine times as long. This example is about the derived rates in the
manifest, not about a precise `D`, so it accepts a mean known to about ±0.03.

## Run

```console
fim run doc/examples/per-base-mutation-rate-across-unequal-locus-lengths/config.yaml \
    --output results/per-base-mutation-rate-across-unequal-locus-lengths --quiet
```

See the [usage-guide explanation](../../usage.md#per-base-mutation-rate-across-unequal-locus-lengths)
and the [configuration reference](../../configuration.md).
