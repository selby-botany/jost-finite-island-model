# Per-base mutation rate across unequal locus lengths

## What this demonstrates

`mu_b` sets a per-base mutation probability. The simulator derives each
locus's per-locus `mu` from its own length, so the two loci here (50 and
500 bases) have different mutation rates while sharing one per-base
rate.

The manifest records the expanded per-locus rates. This demonstrates
configuration behavior rather than a particular literature result.

## Run

```console
fim run doc/examples/per-base-mutation-rate-across-unequal-locus-lengths/config.yaml \
    --output results/per-base-mutation-rate-across-unequal-locus-lengths --quiet
```

See the [usage-guide explanation](../../usage.md#per-base-mutation-rate-across-unequal-locus-lengths)
and the [configuration reference](../../configuration.md).
