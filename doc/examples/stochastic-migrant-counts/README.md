# Stochastic migrant counts

## What this demonstrates

With `migrant_sampling: stochastic`, each deme's migrant count is drawn
from a binomial distribution instead of using a deterministic
`rate * N` fraction. This adds explicit migration-count randomness to
the run.

The run converges at generation 11,802, after about a minute on a busy
development machine. Its trailing-window mean `D` is 0.076 ± 0.005 (one
standard error), above the 0.059 that the island model's identity
recursion predicts for these parameters, and the final generation's own
value, 0.032, is one noisy draw. The gap is larger than the reported
standard error suggests: one locus swings slowly, and a standard error
computed from generation-to-generation correlation alone understates
swings that slow, so a single locus's window mean can sit this far from
the expectation (a batch averages it out; see
[Convergence defaults](../../convergence.md)). To compare
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
