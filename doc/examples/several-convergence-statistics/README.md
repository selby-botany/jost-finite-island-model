# Several convergence statistics

## What this demonstrates

The run watches both `D` and `G_ST`. With
`convergence_combinator: any`, the run can stop when either watched
statistic meets the convergence rule; `all` requires every watched
statistic to meet it.

The run converges at generation 6,059, after about half a minute on a
busy development machine. Its report's `converged_on` is
`["G_ST"]`: at the stop, `G_ST`'s trailing-window mean was known
precisely (0.0660 ± 0.0038, one standard error), while `D`'s was not
(0.101 ± 0.010), so only `G_ST` had settled, and `any` needs only one.
This example is about simulation stopping behavior, not a biological
claim that the measures are equivalent.

## Run

```console
fim run doc/examples/several-convergence-statistics/config.yaml \
    --output results/several-convergence-statistics --quiet
```

See the [usage-guide explanation](../../usage.md#several-convergence-statistics)
and [convergence defaults](../../convergence.md).
