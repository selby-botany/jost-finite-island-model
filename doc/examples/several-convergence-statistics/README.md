# Several convergence statistics

## What this demonstrates

The run watches both `D` and `G_ST`. With
`convergence_combinator: any`, the run can stop when either watched
statistic meets the convergence rule; `all` requires every watched
statistic to meet it.

The run converges at generation 2,327, after a few seconds on a
development machine. It burns in for 1,325 generations, then averages over a
1,003-generation window. Its report's `converged_on` is `["G_ST"]`: at the
stop, `G_ST`'s average was known to the requested precision (0.0606 ± 0.0047,
one standard error), while `D`'s was not (0.151 ± 0.020), so only `G_ST` had
settled, and `any` needs only one. Under the default `all` the run would have
continued until `D` was precise too. Stopping on `any` leaves the other
statistic imprecise: read `D`'s error bar before using it. This example is
about simulation stopping behavior, not a biological claim that the measures
are equivalent.

## Run

```console
fim run doc/examples/several-convergence-statistics/config.yaml \
    --output results/several-convergence-statistics --quiet
```

See the [usage-guide explanation](../../usage.md#several-convergence-statistics)
and [convergence defaults](../../convergence.md).
