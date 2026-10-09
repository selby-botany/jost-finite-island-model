# Equilibrium-split founding

## What this demonstrates

The simulator first evolves one ancestral population until it has
forgotten its starting draw, then samples its gene copies without
replacement to found three demes. This creates small, reproducible
differences among demes at generation zero from a shared ancestry rather
than assigning an independent initial frequency distribution to each
deme.

The ancestral phase runs for a burn-in derived from the model: with
`equilibrium_convergence_tolerance: 0.01`, the 600 ancestral gene copies
need 1,256 generations. The configuration allows at most 2,000
(`equilibrium_max_generations`); a burn-in longer than that cap is an
error, reported before anything is simulated. The ancestral phase is
saved, every generation of it, in `equilibrium_trajectory.tlog` beside
the main run's `trajectory.tlog`; this is the only example that writes
that file (see [`equilibrium_trajectory.tlog`](../../usage.md#equilibrium_trajectorytlog)).
The main run then converges at generation 1,374, with a 924-generation
evidence window, after about half a minute on a busy
development machine. That looks like a premature stop: the window opens
only about 450 generations after the founding split, while the demes are
still differentiating, and the planned convergence redesign addresses it
(the configuration is left unchanged until then). Its trailing-window
mean `D` is 0.241 ± 0.014 (one standard error), below the 0.287 that the island model's identity
recursion predicts for these parameters. The gap is larger than the
reported standard error suggests: the eight pooled loci still swing
slowly, and a window this short is one draw from those swings.

## Why eight loci and a tolerance of 0.03

The configuration pools eight loci and sets `convergence_tolerance: 0.03`
for the main run. With one locus, the main run's `D` swings slowly
between the three demes, and a run stops only once its trailing-window
mean is known to half the tolerance: at the default 0.01 it reached the
200,000-generation cap without getting there, and even at 0.05 it needed
about 30,000 generations and two minutes. Eight pooled loci average those
swings, and 0.03 keeps the run under a minute while its window still
starts after the founding generation. The founder effect this
example shows is unchanged: it comes from the split, not from the
number of loci.

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
