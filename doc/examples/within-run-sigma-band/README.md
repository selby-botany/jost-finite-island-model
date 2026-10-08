# Within-run sigma band

## What this demonstrates

After `D` meets the run's convergence criteria, the simulator records an
additional 30-generation window. The report summarizes that extension
with a two-standard-deviation band (`sigma_band_multiplier: 2.0`).

The extension window's mean and spread answer how much one run continues
to vary after convergence. This differs from a confidence interval over
independent replicate runs. The example converges at generation 13,909
with `D` = 0.163 (trailing-window mean 0.125 ± 0.003), after about three
minutes on a busy development machine. The band is calculated from the 30
generations that follow: `D` = 0.157 ± 0.063 (mean ± 2σ).

## Why eight loci

The example tracks eight independent loci and pools them. An earlier
version tracked one locus, and it stopped at generation 1,429 with a
trailing-window mean `D` of only 0.019, against about 0.13 expected for
this island model. That locus had drifted close to fixation (its
within-deme heterozygosity fell below 0.1), where `D` sits near zero and
barely moves, so the window looked steady and precise for the wrong
reason. Eight pooled loci are almost never all near fixation at once.
They still land a little below the prediction (0.125 against 0.132, about
two standard errors): one run's window mean is a single draw, and its
standard error is itself only an estimate. The cost is a much longer run
than one locus needs.

## Run

```console
fim run doc/examples/within-run-sigma-band/config.yaml \
    --output results/within-run-sigma-band --quiet
```

See the [usage-guide explanation](../../usage.md#within-run-sigma-band)
and [convergence defaults](../../convergence.md).
