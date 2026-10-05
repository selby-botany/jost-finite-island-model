# Within-run sigma band

## What this demonstrates

After `D` meets the run's convergence criteria, the simulator records an
additional 30-generation window. The report summarizes that extension
with a two-standard-deviation band (`sigma_band_multiplier: 2.0`).

The extension window's mean and spread answer how much one run continues
to vary after convergence. This differs from a confidence interval over
independent replicate runs. The example converges at generation 1,429;
the band is calculated from the following generations.

## Run

```console
fim run doc/examples/within-run-sigma-band/config.yaml \
    --output results/within-run-sigma-band --quiet
```

See the [usage-guide explanation](../../usage.md#within-run-sigma-band)
and [convergence defaults](../../convergence.md).
