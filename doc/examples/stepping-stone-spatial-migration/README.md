# Stepping-stone (spatial) migration

## What this demonstrates

Six demes form a ring, and each deme exchanges migrants with its two
neighbors. The `topology: ring` shorthand builds this sparse migration
graph without writing all 36 matrix entries. Replacing `ring` with
`linear` removes the wrap-around connection.

The run converges at generation 16,516, after about a minute on ordinary
development hardware. Its trailing-window mean `D` is 0.132 ± 0.006 (one
standard error) and its mean `G_ST` 0.058; the final generation's own
`D`, 0.041, is one noisy draw. In the GUI, the resulting graph has
distance classes for the isolation-by-distance visualization.

## Why the tolerance is 0.02

The configuration sets `convergence_tolerance: 0.02`, twice the default.
A run stops only once its trailing-window mean is known to half the
tolerance, and a single locus is noisy, so at the default 0.01 this run
needs about 32,000 generations and several minutes. At 0.02 the mean is
known to about ±0.01 instead of ±0.005, which is enough to show the
ring's effect. A looser tolerance still, such as 0.05, finishes in
seconds but stops on a short window whose mean (about 0.18 here) is
visibly less reliable.

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
