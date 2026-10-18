# Kimura-Weiss isolation by distance

## What this demonstrates

Twenty demes on a ring create several shortest-path distance classes.
Migration is restricted to neighboring demes, and the completed-run GUI
can group deme-pair genetic correlations by distance to show spatial
decay.

The batch ran 10 replicates in about six minutes on a development machine
and stopped at the minimum of 10 because the 95% confidence interval for `D`
was already within ±0.05. Each replicate burned in for 2,260 generations (5
relaxation times of 452) and then averaged over a 9,039-generation window.
Across the replicates, `D` = 0.563 ± 0.026 and `G_ST` = 0.1215 ± 0.0024 (95%
confidence half-widths), from one locus per replicate. The saved trajectories
are thinned (every generation to 5,000, then one in ten) to keep the example
small.

## Why the precision is 0.05

The configuration sets `precision: 0.05`, five times the default. Twenty demes
on a ring mix slowly, so a single locus's `D` wanders for a long time, and the
window each replicate averages over grows as one over the square of the
precision: at the default 0.01 the batch would need about 25 times as much
simulation (hours). The point of this example is the isolation-by-distance
pattern, which appears at any of these precisions, so it trades precision in
`D` (known to about ±0.03 rather than ±0.01) for a batch that finishes in
minutes. Lower the precision in your own copy when you need a more precise
value.

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
