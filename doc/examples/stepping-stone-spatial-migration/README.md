# Stepping-stone (spatial) migration

## What this demonstrates

Six demes form a ring, and each deme exchanges migrants with its two
neighbors. The `topology: ring` shorthand builds this sparse migration
graph without writing all 36 matrix entries. Replacing `ring` with
`linear` removes the wrap-around connection.

The run converges at generation 22,761, after about half a minute on a
development machine. It burns in for 1,643 generations, then averages over a
21,119-generation window: `D` = 0.156 ± 0.009 (one standard error) and `G_ST`
= 0.060 ± 0.001; the final generation's own `D`, 0.069, is one noisy draw. In
the GUI, the resulting graph has distance classes for the isolation-by-distance
visualization.

## Why the precision is 0.02

The configuration sets `precision: 0.02`, twice the default.
A run stops once the average of each watched statistic is known to plus or
minus the precision at 95% confidence, and a single locus is noisy, so the
default 0.01 would take about four times as long. At 0.02 `D` is known to about
±0.02 instead of ±0.01, which is enough to show the ring's effect. A looser
precision still, such as 0.05, finishes in seconds but averages over a short
window whose mean is visibly less reliable.

## Related literature

Kimura M, Weiss GH (1964). The stepping stone model of population
structure and the decrease of genetic correlation with distance.
*Genetics* 49(4):561–576.
[doi:10.1093/genetics/49.4.561](https://doi.org/10.1093/genetics/49.4.561).
Their spatial migration model motivates the ring topology; this small
configuration is a demonstration, not a fitted reproduction of their
analysis.

## Run

```console
fim run doc/examples/stepping-stone-spatial-migration/config.yaml \
    --output results/stepping-stone-spatial-migration --quiet
```

See the [usage-guide explanation](../../usage.md#stepping-stone-spatial-migration)
and the [configuration reference](../../configuration.md).
