# Unequal island sizes with a migration hub

## What this demonstrates

Four demes have unequal population sizes and an explicit asymmetric
migration matrix. Deme 4 is both the largest deme and a migration hub.
The example shows that `N` can be a per-deme list and `m` can describe
each source-to-destination rate rather than one shared migration rate.

The run converges at generation 1,136, after about 10 seconds on ordinary
development hardware. Its trailing-window mean `D` is 0.0540 ± 0.0016
(one standard error); the final generation's own value is 0.0498.
Compare the recorded `parameters.N` and `parameters.m` in the manifest
with a uniform-size, uniform-migration run to see the effect of the
hub. `deme_weighting` changes `E_ST`, but not `D` or `K_ST`: `E_ST` is
0.0676 with the default equal weighting and 0.0618 with
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
Its window mean, 0.025, was half the value eight loci give. Pooling
loci averages those slow swings away, so the run now stops later and
nearer the model's real value. The cost is about twice the running time.

## Run

```console
fim run doc/examples/unequal-island-sizes-with-a-migration-hub/config.yaml \
    --output results/unequal-island-sizes-with-a-migration-hub --quiet
```

See the [usage-guide explanation](../../usage.md#unequal-island-sizes-with-a-migration-hub)
and the [configuration reference](../../configuration.md).
