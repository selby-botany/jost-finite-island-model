# Stepping-stone (spatial) migration

## What this demonstrates

Six demes form a ring, and each deme exchanges migrants with its two
neighbors. The `topology: ring` shorthand builds this sparse migration
graph without writing all 36 matrix entries. Replacing `ring` with
`linear` removes the wrap-around connection.

The run converges at generation 1,748 with `D` near 0.0287. In the GUI,
the resulting graph has distance classes for the isolation-by-distance
visualization.

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
