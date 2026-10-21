# Within-run sigma band

## What this demonstrates

A run burns in, then averages. Everything after the burn-in is its *evidence
window*, and the report describes it for every statistic: the mean, the
standard error of that mean, and the standard deviation (`σ`) of the
per-generation values.

The two numbers answer different questions. The standard error says how
precisely the run's average is known, and it shrinks as the run gets longer.
`σ` measures how much `D` varies across generations at equilibrium; it does
not shrink as the evidence window grows. The trajectory graph shows `σ` as a
shaded band over the evidence
window in the "Every generation" display (choose ±1 or ±2), and the standard
error as a band around the running mean in the two averaged displays.

In this example the burn-in is 1,522 generations (about 5.3 relaxation times
of 287). The run converges at generation 19,984, so the evidence window holds
18,463 generations: `D` = 0.127 ± 0.004 (1 standard error), with
`σ` = 0.035, so one generation's `D` lies within 0.127 ± 0.071 about 95% of
the time (2σ).

## Why eight loci

The example tracks eight independent loci and pools them. A single locus
can approach fixation, where within-deme heterozygosity and `D` may both
remain low for an extended period. Such a trajectory can yield a narrow
within-run error estimate while remaining unrepresentative of the
multi-locus expectation. Pooling loci reduces the probability that all are
near fixation at once. The observed mean is 0.127 against a prediction of
0.132, about one standard error below the prediction. A single run's window
mean is one realization, and its standard error is estimated from that run.

## Why a single run

This example is a single run because its subject is the σ band over one run's
own evidence window. A batch has no single evidence window: each replicate
averages over a window matched to the whole batch, and the batch reports an
interval across replicates instead. The engine's default is `n_replicates:
200`, an adaptively stopped batch that reports a confidence interval, and that
is what most examples use; see [an adaptive replicate batch with a confidence
interval](../an-adaptive-replicate-batch-with-a-confidence-interval/README.md).

## Run

```console
fim run doc/examples/within-run-sigma-band/config.yaml \
    --output results/within-run-sigma-band --quiet
```

See the [usage-guide explanation](../../usage.md#within-run-sigma-band)
and [convergence defaults](../../convergence.md).
