# Unequal island sizes with a migration hub

## What this demonstrates

Four demes have unequal population sizes and an explicit asymmetric
migration matrix. Deme 4 is both the largest deme and a migration hub.
The example shows that `N` can be a per-deme list and `m` can describe
each source-to-destination rate rather than one shared migration rate.

The reported run converges at generation 1,006 with `D` near 0.0551.
Compare the recorded `parameters.N` and `parameters.m` in the manifest
with a uniform-size, uniform-migration run to see the effect of the
hub. `deme_weighting` changes `E_ST`, but not `D` or `K_ST`.

## Run

```console
fim run doc/examples/unequal-island-sizes-with-a-migration-hub/config.yaml \
    --output results/unequal-island-sizes-with-a-migration-hub --quiet
```

See the [usage-guide explanation](../../usage.md#unequal-island-sizes-with-a-migration-hub)
and the [configuration reference](../../configuration.md).
