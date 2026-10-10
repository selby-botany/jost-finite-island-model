# An adaptive replicate batch with a confidence interval

## What this demonstrates

The batch allows up to 50 replicates, requires at least 10, and stops
when the 95% confidence-interval half-width for the watched statistic
is within `precision`. The sequential command makes the run
order explicit; it does not change the computed results.

With this seed, the batch stops at 20 replicates because `D`'s interval
reaches 0.267 ± 0.029, inside the requested 0.03. The other 30 possible
replicates are not run. The model's expected `D` for these parameters is
0.287, inside the interval. This is a seeded demonstration of adaptive
stopping, not a general prediction of how many replicates a different
configuration will need: here the interval is still too wide at the
10-replicate minimum, so the stop rule waits until the twentieth. A
larger `precision` stops sooner; a smaller one runs more replicates.

The batch takes about two to three minutes with a few worker processes
on a busy development machine, and longer with `--sequential`.

## Why eight loci and `precision: 0.03`

One `precision` answers "how precise?" twice: each replicate averages
over time until its own `D` is known to about that, and the batch adds
replicates until the interval across replicates is that narrow. Each
replicate pools eight loci. An earlier version used one locus per
replicate at the default 0.01, and a single locus is noisy: each
replicate then needed tens of thousands of generations and the batch took
hours. Eight loci make each replicate both quicker to settle (most stop
within a few thousand generations; the median is about 1,800, the slowest
about 14,800) and much less scattered (standard deviation 0.06 across
replicates), so 0.03 is reached with 20 replicates.

## Run

```console
fim run doc/examples/an-adaptive-replicate-batch-with-a-confidence-interval/config.yaml \
    --output results/an-adaptive-replicate-batch-with-a-confidence-interval --sequential --quiet
```

See the [usage-guide explanation](../../usage.md#an-adaptive-replicate-batch-with-a-confidence-interval)
and the [configuration reference](../../configuration.md#stop_batch_early).
