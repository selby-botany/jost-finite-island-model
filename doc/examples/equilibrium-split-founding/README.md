# Equilibrium-split founding

## What this demonstrates

The simulator first evolves one ancestral population until it satisfies
the configured equilibrium criteria, then samples its gene copies
without replacement to found three demes. This creates small,
reproducible differences among demes at generation zero from a shared
ancestry rather than assigning an independent initial frequency
distribution to each deme.

The ancestral phase allows at most 500 generations here. It must meet its
own equilibrium criteria before the main run begins; reaching that cap
without settling is an error. The main run converges at generation 1,091
with `D` near 0.191.

## Related literature

Greenbaum G, et al. (2014). Allelic richness following population
founding events: a stochastic modeling framework incorporating gene
flow and genetic drift. *PLOS ONE* 9(12):e115203.
[doi:10.1371/journal.pone.0115203](https://doi.org/10.1371/journal.pone.0115203).
This paper studies genetic consequences of population founding; the
configuration demonstrates `fim`'s shared-ancestor split and is not a
reproduction of the paper's model.

## Run

```console
fim run doc/examples/equilibrium-split-founding/config.yaml \
    --output results/equilibrium-split-founding --quiet
```

See the [usage-guide explanation](../../usage.md#equilibrium-split-founding)
and the [configuration reference](../../configuration.md).
