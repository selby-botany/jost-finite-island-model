# An adaptive replicate batch with a confidence interval

## What this demonstrates

The batch allows up to 50 replicates, requires at least 10, and stops
when the 95% confidence-interval half-width for each watched statistic
is within `precision`. The sequential command makes the run
order explicit; it does not change the computed results.

With this seed, the batch stops at the 10-replicate minimum: `D`'s interval
is 0.281 ± 0.015 and `G_ST`'s is 0.231 ± 0.004, both inside the requested
0.03, so the other 40 possible replicates are not run. The model's expected
`D` for these parameters is 0.287, inside the interval. This is a seeded
demonstration of adaptive stopping, not a general prediction of how many
replicates a different configuration will need: a larger `precision` could not
stop sooner than the minimum, and a smaller one runs more replicates.

Each replicate is a run that burns in and then averages for a fixed window,
and contributes the average. The first wave of eight replicates averages for
a guess of 20 relaxation times (5,834 generations); from the noise those
measured, the window that reaches the precision with the replicates the batch
aims for is the shortest allowed, 5 relaxation times (1,459 generations), which
the last two replicates use. (The window's rule is in the
[convergence guide](../../convergence.md#batches-replicates-that-average).) The
saved trajectories are thinned (every generation to 5,000, then one in ten)
to keep this example small.

The batch takes about three minutes with a few worker processes on a
development machine, and longer with `--sequential`.

## Why eight loci and `precision: 0.03`

One `precision` states the batch's goal: the interval across replicates is
plus or minus 0.03 at 95% confidence. Each replicate pools eight loci. An
earlier version used one locus per replicate at the default 0.01, and a single
locus is noisy: each replicate then needed tens of thousands of generations
and the batch took hours. Eight loci make each replicate much less scattered
(standard deviation 0.02 across replicates), so 0.03 is reached with the
minimum of 10.

## Run

```console
fim run doc/examples/an-adaptive-replicate-batch-with-a-confidence-interval/config.yaml \
    --output results/an-adaptive-replicate-batch-with-a-confidence-interval --sequential --quiet
```

See the [usage-guide explanation](../../usage.md#an-adaptive-replicate-batch-with-a-confidence-interval)
and the [configuration reference](../../configuration.md#stop_batch_early).
