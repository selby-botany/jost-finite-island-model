# Kimura-Weiss isolation by distance

## What this demonstrates

Twenty demes on a ring create several shortest-path distance classes.
Migration is restricted to neighboring demes, and the completed-run GUI
can group deme-pair genetic correlations by distance to show spatial
decay.

The run converges at generation 2,754 with `D` near 0.615 and `G_ST`
near 0.132. These are values from one locus and one seeded run, not
ensemble estimates.

## Related literature

Kimura M, Weiss GH (1964). The stepping stone model of population
structure and the decrease of genetic correlation with distance.
*Genetics* 49(4):561–576.
[doi:10.1093/genetics/49.4.561](https://doi.org/10.1093/genetics/49.4.561).
The example illustrates the spatial structure behind the model; it is
not a numerical reproduction of the paper's results.

## Run

```console
fim run doc/examples/kimura-weiss-isolation-by-distance/config.yaml \
    --output results/kimura-weiss-isolation-by-distance --quiet
```

See the [usage-guide explanation](../../usage.md#kimura-weiss-isolation-by-distance).
