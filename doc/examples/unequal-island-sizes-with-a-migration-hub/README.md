# Unequal island sizes with a migration hub

## What this demonstrates

Four demes have unequal population sizes and an explicit asymmetric
migration matrix. Deme 4 is both the largest deme and a migration hub.
The example shows that `N` can be a per-deme list and `m` can describe
each source-to-destination rate rather than one shared migration rate.

The batch ran 10 replicates in about five minutes on a development machine
(eight loci per replicate are slow to simulate) and stopped at the minimum of
10 because the 95% confidence interval for `D` was already within the default
±0.01. Each replicate burned in for 1,777 generations (5 relaxation times of
335) and then averaged over a 6,707-generation window. Across the replicates,
`D` = 0.0652 ± 0.0028 and `G_ST` = 0.0243 ± 0.0002 (95% confidence
half-widths). Compare the recorded `parameters.N` and `parameters.m` in the
manifest with a uniform-size, uniform-migration run to see the effect of the
hub. `deme_weighting` changes `E_ST`, but not `D` or `K_ST`: with
`deme_weighting: size` the large hub deme pulls `E_ST` down relative to the
default equal weighting.

## Why eight loci

The example tracks eight independent loci and pools them. Between-locus
variation reduces the influence of a single locus's drift trajectory on the
reported replicate average. Each replicate averages over the 6,707-generation
window associated with the batch precision. The saved trajectory is thinned
(every generation to 5,000, then one in ten) to keep the example small; the
statistics and report are unaffected.

## Run

```console
fim run doc/examples/unequal-island-sizes-with-a-migration-hub/config.yaml \
    --output results/unequal-island-sizes-with-a-migration-hub --quiet
```

See the [usage-guide explanation](../../usage.md#unequal-island-sizes-with-a-migration-hub)
and the [configuration reference](../../configuration.md).
