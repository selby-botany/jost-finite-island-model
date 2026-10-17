# Unequal island sizes with a migration hub

## What this demonstrates

Four demes have unequal population sizes and an explicit asymmetric
migration matrix. Deme 4 is both the largest deme and a migration hub.
The example shows that `N` can be a per-deme list and `m` can describe
each source-to-destination rate rather than one shared migration rate.

The run converges at generation 44,847, after about seven minutes on a
development machine (eight loci are slow to simulate). It burns in for 1,777
generations, then averages over a 43,071-generation window: `D` = 0.0626 ±
0.0022 (one standard error) and `G_ST` = 0.0245 ± 0.0001; the final
generation's own `D` is 0.0602. Compare the recorded `parameters.N` and
`parameters.m` in the manifest with a uniform-size, uniform-migration run to
see the effect of the hub. `deme_weighting` changes `E_ST`, but not `D` or
`K_ST`: the final generation's `E_ST` is 0.082 with the default equal
weighting, and `deme_weighting: size` lowers it, because the large hub deme
pulls the size-weighted value down.

## Why eight loci

The example tracks eight independent loci and pools them. An earlier
version tracked one locus, and that locus's diversity swung widely: its
within-deme heterozygosity moved between about 0.13 and 0.70 within a
thousand generations, so its average over a short window said little about
the long run. Pooling loci averages many of those slow swings away, and the
run now averages for as long as its precision needs (43,071 generations here)
instead of stopping on the first window that fills. The saved trajectory is
thinned (every generation to 5,000, then one in ten) to keep the example
small; the statistics and the report are unaffected.

## Why a single run

This example sets `n_replicates: 1`. It was written when a run was always one
run, and the numbers quoted here, and the outputs saved with it, are those of
one run. The engine's default is `n_replicates: 200`, an adaptively stopped
batch that reports a confidence interval, and that is what a researcher would
normally use. One run's final numbers are one noisy draw: for an estimate with
an error bar, run a batch (see
[an adaptive replicate batch with a confidence interval](../an-adaptive-replicate-batch-with-a-confidence-interval/README.md)).

## Run

```console
fim run doc/examples/unequal-island-sizes-with-a-migration-hub/config.yaml \
    --output results/unequal-island-sizes-with-a-migration-hub --quiet
```

See the [usage-guide explanation](../../usage.md#unequal-island-sizes-with-a-migration-hub)
and the [configuration reference](../../configuration.md).
