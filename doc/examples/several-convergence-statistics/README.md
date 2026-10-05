# Several convergence statistics

## What this demonstrates

The run watches both `D` and `G_ST`. With
`convergence_combinator: any`, the run can stop when either watched
statistic meets the convergence rule; `all` requires every watched
statistic to meet it.

The run converges at generation 770. Its report identifies which
statistic or statistics triggered the stop. This example is about
simulation stopping behavior, not a biological claim that the measures
are equivalent.

## Run

```console
fim run doc/examples/several-convergence-statistics/config.yaml \
    --output results/several-convergence-statistics --quiet
```

See the [usage-guide explanation](../../usage.md#several-convergence-statistics)
and [convergence defaults](../../convergence.md).
