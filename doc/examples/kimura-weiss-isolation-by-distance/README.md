# Kimura-Weiss isolation by distance

## What this demonstrates

Twenty demes on a ring create several shortest-path distance classes.
Migration is restricted to neighboring demes, and the completed-run GUI
can group deme-pair genetic correlations by distance to show spatial
decay.

The run converges at generation 31,250, after about five minutes on a
development machine. It burns in for 2,260 generations (5 relaxation times of
452), then averages over a 28,991-generation window: `D` = 0.565 ± 0.011 and
`G_ST` = 0.119 ± 0.002 (one standard error). The final generation's own `D`
and `G_ST`, 0.538 and 0.136, are single draws. These are values from one locus
and one seeded run, not ensemble estimates. The saved trajectory is thinned
(every generation to 5,000, then one in ten) to keep the example small.

## Why the precision is 0.05

The configuration sets `precision: 0.05`, five times the
default. Twenty demes on a ring mix slowly, so a single locus's `D`
wanders for a long time, and the window needed grows as one over the square of
the precision: at the default 0.01 the run would need about 25 times as long
(hours, with a trajectory file of several gigabytes). The point of this example
is the isolation-by-distance pattern, which appears at any of these
precisions, so it trades precision in `D` (known to about ±0.02 rather than
±0.004) for a run that finishes in minutes. Lower the precision in your own
copy when you need the more precise value, and consider `trajectory_retention:
thinned` to keep the file small.

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
