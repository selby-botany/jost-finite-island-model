# Kimura-Weiss isolation by distance

## What this demonstrates

Twenty demes on a ring create several shortest-path distance classes.
Migration is restricted to neighboring demes, and the completed-run GUI
can group deme-pair genetic correlations by distance to show spatial
decay.

The run converges at generation 2,539, after about a minute on a busy
development machine, with `D` = 0.475 and `G_ST` = 0.093 (trailing-window
means 0.516 ± 0.017 and 0.126 ± 0.003). These are values from one locus
and one seeded run, not ensemble estimates.

## Why the tolerance is 0.05

The configuration sets `precision: 0.05`, five times the
default. Twenty demes on a ring mix slowly, so a single locus's `D`
wanders for a long time, and at the default 0.01 the run needs about
175,000 generations: well over an hour, with a trajectory file of
several gigabytes. At 0.03 it still takes about two minutes. The point of
this example is the isolation-by-distance pattern, which appears at any
of these tolerances, so it trades precision in `D` (known to about
±0.025 rather than ±0.005) for a run that finishes in seconds. Lower the
tolerance in your own copy when you need the more precise value.

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
