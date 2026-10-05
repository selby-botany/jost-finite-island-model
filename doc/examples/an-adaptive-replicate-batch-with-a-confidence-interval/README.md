# An adaptive replicate batch with a confidence interval

## What this demonstrates

The batch allows up to 50 replicates, requires at least 10, and stops
when the 95% confidence-interval half-width for the watched statistic
is within `replicate_tolerance`. The sequential command makes the run
order explicit; it does not change the computed results.

With this seed, the batch stops at 16 replicates when `D`'s interval
half-width reaches about 0.0782, below the requested 0.08. The other
possible replicates are not run. This is a seeded demonstration of
adaptive stopping, not a general prediction of how many replicates a
different configuration will need.

## Run

```console
fim run doc/examples/an-adaptive-replicate-batch-with-a-confidence-interval/config.yaml \
    --output results/an-adaptive-replicate-batch-with-a-confidence-interval --sequential --quiet
```

See the [usage-guide explanation](../../usage.md#an-adaptive-replicate-batch-with-a-confidence-interval)
and the [configuration reference](../../configuration.md#replicate_tolerance).
