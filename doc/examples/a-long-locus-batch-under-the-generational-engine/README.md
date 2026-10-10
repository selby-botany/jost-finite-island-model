# A long-locus batch under the generational engine

## What this demonstrates

This companion timing workload runs 16 replicates with 35 demes and the
`generational` engine. Its seven-base finite-alleles locus has more
possible states than the preceding large-deme example. As there, a fixed
window (`convergence_burn_in: 1` and `replicate_averaging_window: 99`) and
`stop_batch_early: false` make every one of the 16 replicates run exactly 100
generations, so the amount of work is fixed.

The run takes about 35 seconds on a development machine, a
few times longer than the large-deme example's 10. The across-replicate
mean `D` is 0.0461 ± 0.0027 (95% confidence interval), the mean of the
replicates' averages over their 100 generations.

This is an engine workload, not an equilibrium result or a universal
benchmark. Runtime depends on the machine, its load, and the software
environment, and which of the two examples finishes first can change
with them.

## Run

```console
fim run doc/examples/a-long-locus-batch-under-the-generational-engine/config.yaml \
    --output results/a-long-locus-batch-under-the-generational-engine --quiet
```

See the [usage-guide explanation](../../usage.md#a-long-locus-batch-under-the-generational-engine)
and [engine-backend guidance](../../fim-simulator-design.md#46-choosing-an-engine-backend).
