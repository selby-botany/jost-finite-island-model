# Stepping-stone (spatial) migration

## What this demonstrates

Six demes form a ring, and each deme exchanges migrants with its two
neighbors. The `topology: ring` shorthand builds this sparse migration
graph without writing all 36 matrix entries. Replacing `ring` with
`linear` removes the wrap-around connection.

The batch ran 16 replicates in under two minutes on a development machine
and stopped because the 95% confidence interval for `D` was within ±0.02. Each
replicate burned in for 1,643 generations (5 relaxation times of 329) and then
averaged over a 6,572-generation window. Across the replicates, `D` = 0.146 ±
0.019 and `G_ST` = 0.0589 ± 0.0014 (95% confidence half-widths). In the GUI,
the resulting graph has distance classes for the isolation-by-distance
visualization.

## Why the precision is 0.02

The configuration sets `precision: 0.02`, twice the default. A batch stops
once the 95% confidence interval across replicates is within ±0.02 for each
watched statistic, and a single locus is noisy, so the default 0.01 would need
windows about four times as long. At 0.02 `D` is known to about ±0.02 instead
of ±0.01, which is enough to show the ring's effect. A looser precision still,
such as 0.05, finishes sooner but averages over shorter windows and gives a
visibly wider interval.

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
