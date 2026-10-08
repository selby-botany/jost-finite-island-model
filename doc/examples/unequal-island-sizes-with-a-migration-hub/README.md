# Unequal island sizes with a migration hub

## What this demonstrates

Four demes have unequal population sizes and an explicit asymmetric
migration matrix. Deme 4 is both the largest deme and a migration hub.
The example shows that `N` can be a per-deme list and `m` can describe
each source-to-destination rate rather than one shared migration rate.

The run converges at generation 1,006, after about 20 seconds on a busy
development machine. Its trailing-window mean `D` is 0.0468 ± 0.0019
(one standard error); the final generation's own value is 0.0644.
Compare the recorded `parameters.N` and `parameters.m` in the manifest
with a uniform-size, uniform-migration run to see the effect of the
hub. `deme_weighting` changes `E_ST`, but not `D` or `K_ST`: `E_ST` is
0.0815 with the default equal weighting and 0.0764 with
`deme_weighting: size`, because the large hub deme pulls the
size-weighted value down.

## Why eight loci

The example tracks eight independent loci and pools them. An earlier
version tracked one locus, and that locus's diversity swung widely: its
within-deme heterozygosity moved between about 0.13 and 0.70 within a
thousand generations. The convergence check judged its noise from
generation-to-generation correlation alone, which misses swings that
slow, so the run stopped at generation 1,006, the first generation its
window could fill, with a window that still held the starting state.
Its window mean, 0.025, was about half the value eight loci give. Pooling
loci averages many of those slow swings away, but this run still stops at
generation 1,006, the first generation its 1,007-generation window can
fill, so its window mean is one window's average, not a long-run
estimate. The convergence rule that allows this is planned for
redesign.

## Run

```console
fim run doc/examples/unequal-island-sizes-with-a-migration-hub/config.yaml \
    --output results/unequal-island-sizes-with-a-migration-hub --quiet
```

See the [usage-guide explanation](../../usage.md#unequal-island-sizes-with-a-migration-hub)
and the [configuration reference](../../configuration.md).
