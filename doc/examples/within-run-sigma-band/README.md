# Within-run sigma band

## What this demonstrates

A run burns in, then averages. Everything after the burn-in is its *evidence
window*, and the report describes it for every statistic: the mean, the
standard error of that mean, and the standard deviation (`σ`) of the
per-generation values.

The two numbers answer different questions. The standard error says how
precisely the run's average is known, and it shrinks as the run gets longer.
`σ` says how much `D` wanders from one generation to the next at equilibrium,
the spread you would see sampling one population at one time, and it does not
shrink. The trajectory graph shows `σ` as a shaded band over the evidence
window in the "Every generation" display (choose ±1 or ±2), and the standard
error as a band around the running mean in the two averaged displays.

In this example the burn-in is 1,522 generations (about 5.3 relaxation times
of 287). The run converges at generation 19,984, so the evidence window holds
18,463 generations: `D` = 0.127 ± 0.004 (1 standard error), with
`σ` = 0.035, so one generation's `D` lies within 0.127 ± 0.071 about 95% of
the time (2σ).

## Why eight loci

The example tracks eight independent loci and pools them. An earlier
version tracked one locus, and it stopped at generation 1,429 with a
trailing-window mean `D` of only 0.019, against about 0.13 expected for
this island model. That locus had drifted close to fixation (its
within-deme heterozygosity fell below 0.1), where `D` sits near zero and
barely moves, so the window looked steady and precise for the wrong
reason. Eight pooled loci are almost never all near fixation at once.
They still land a little below the prediction (0.127 against 0.132, about
one standard error): one run's window mean is a single draw, and its
standard error is itself only an estimate. The cost is a much longer run
than one locus needs.

## Run

```console
fim run doc/examples/within-run-sigma-band/config.yaml \
    --output results/within-run-sigma-band --quiet
```

See the [usage-guide explanation](../../usage.md#within-run-sigma-band)
and [convergence defaults](../../convergence.md).
