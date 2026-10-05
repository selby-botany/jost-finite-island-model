# Stochastic migrant counts

## What this demonstrates

With `migrant_sampling: stochastic`, each deme's migrant count is drawn
from a binomial distribution instead of using a deterministic
`rate * N` fraction. This adds explicit migration-count randomness to
the run.

The run converges at generation 701 with `D` near 0.0089. To compare
against continuous migration, remove `migrant_sampling` or set it to
`continuous` while keeping the same seed and other parameters. Each
single-locus result remains one stochastic outcome.

## Run

```console
fim run doc/examples/stochastic-migrant-counts/config.yaml \
    --output results/stochastic-migrant-counts --quiet
```

See the [usage-guide explanation](../../usage.md#stochastic-migrant-counts)
and the [configuration reference](../../configuration.md).
