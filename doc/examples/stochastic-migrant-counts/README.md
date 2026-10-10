# Stochastic migrant counts

## What this demonstrates

With `migrant_sampling: stochastic`, each deme's migrant count is drawn
from a binomial distribution instead of using a deterministic
`rate * N` fraction. This adds explicit migration-count randomness to
the run.

The run converges at generation 60,743, after about half a minute on a
development machine. It burns in for 1,225 generations, then averages over a
59,519-generation window: `D` = 0.071 ± 0.005 (one standard error) and `G_ST`
= 0.0515 ± 0.0008. The island model's identity recursion predicts a `D` of
0.059 for these parameters; the average sits about two standard errors above
it, and the final generation's own value, 0.093, is one noisy draw. One locus
swings slowly, and the run's own error bar (from the autocorrelation of all
its generations, not only neighboring ones) is the honest measure of how far a
single locus's average can sit from the expectation; a batch averages it
out (see [Convergence](../../convergence.md)). To compare against continuous
migration, remove `migrant_sampling` or set it to `continuous` while keeping
the same seed and other parameters. Each single-locus result remains one
stochastic outcome.

## Run

```console
fim run doc/examples/stochastic-migrant-counts/config.yaml \
    --output results/stochastic-migrant-counts --quiet
```

See the [usage-guide explanation](../../usage.md#stochastic-migrant-counts)
and the [configuration reference](../../configuration.md).
