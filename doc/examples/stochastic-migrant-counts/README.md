# Stochastic migrant counts

## What this demonstrates

With `migrant_sampling: stochastic`, each deme's migrant count is drawn
from a binomial distribution instead of using a deterministic
`rate * N` fraction. This adds explicit migration-count randomness to
the run.

The batch ran 11 replicates in about two minutes on a development machine and
stopped because the 95% confidence interval for `D` was within the default
±0.01. Each replicate burned in for 1,225 generations (5 relaxation times of
231) and then averaged over a 4,624-generation window. Across the replicates
`D` = 0.082 ± 0.010 and `G_ST` = 0.0531 ± 0.0016 (95% confidence half-widths).
The island model's identity recursion predicts a `D` of 0.059 for these
parameters. The batch's average of `D`'s values sits above it, and so does the
same configuration with `migrant_sampling: continuous` (0.077 ± 0.010 over 20
replicates): binomial migrant counts make no detectable difference to `D` here.
The gap to 0.059 is the one between averaging `D`'s values and computing `D`
from the averaged heterozygosities, which gives 0.057 for continuous migration
and 0.065 for stochastic (see the section "Which average?" in
[Convergence](../../convergence.md)). To repeat the comparison, remove
`migrant_sampling` or set it to `continuous` while keeping the same seed and
other parameters.

## Run

```console
fim run doc/examples/stochastic-migrant-counts/config.yaml \
    --output results/stochastic-migrant-counts --quiet
```

See the [usage-guide explanation](../../usage.md#stochastic-migrant-counts)
and the [configuration reference](../../configuration.md).
