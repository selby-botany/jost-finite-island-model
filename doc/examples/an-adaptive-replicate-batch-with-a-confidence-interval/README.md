# An adaptive replicate batch with a confidence interval

## What this demonstrates

The batch allows up to 50 replicates, requires at least 10, and stops
when the 95% confidence-interval half-width for the watched statistic
is within `replicate_tolerance`. The sequential command makes the run
order explicit; it does not change the computed results.

With this seed, the batch stops at 22 replicates, when `D`'s interval
reaches 0.280 ± 0.030 (just inside the requested 0.03). The other 28
possible replicates are not run. The model's expected `D` for these
parameters is 0.286, inside the interval. This is a seeded demonstration
of adaptive stopping, not a general prediction of how many replicates a
different configuration will need.

The batch takes about a minute on ordinary development hardware with one
worker process per processor (about two and a half minutes with three),
and several minutes with `--sequential`.

## Why eight loci and a loose per-replicate tolerance

Each replicate pools eight loci and uses `convergence_tolerance: 0.05`.
An earlier version used one locus at the default 0.01 per replicate.
Since the noise-adequacy check (2026-09), a run stops only once its
trailing-window mean is known to half the tolerance, and a single locus
is noisy: each replicate then needed tens of thousands of generations,
and the batch took hours. Even at 0.05, one-locus replicates needed
about 15,000 generations each, and the replicates disagreed so much
(standard deviation about 0.15) that the interval needed most of the 50.

Eight loci make each replicate both quicker to settle (most stop near
the first generation their window can fill, about 900) and much less
scattered (standard deviation 0.07), so a tighter
`replicate_tolerance`, 0.03 instead of 0.08, is still reached in about
two dozen replicates. The trade-off is per-replicate precision: each
replicate's own `D` is known only to about ±0.025. That is acceptable
here because this example is about the across-replicate interval, which
averages that noise away.

## Run

```console
fim run doc/examples/an-adaptive-replicate-batch-with-a-confidence-interval/config.yaml \
    --output results/an-adaptive-replicate-batch-with-a-confidence-interval --sequential --quiet
```

See the [usage-guide explanation](../../usage.md#an-adaptive-replicate-batch-with-a-confidence-interval)
and the [configuration reference](../../configuration.md#replicate_tolerance).
