# A long-locus batch under the generational engine

## What this demonstrates

This companion timing workload runs 16 replicates with 35 demes and the
`generational` engine. Its seven-base finite-alleles locus has more
possible states than the preceding large-deme example. As there, a
101-generation convergence window and `replicate_tolerance: null` make
every one of the 16 replicates run exactly 100 generations, so the
amount of work is fixed.

The run takes about 25 seconds on ordinary development hardware, a
little longer than the large-deme example's 20. The across-replicate
mean `D` is 0.0237 ± 0.0020 (95% confidence interval).

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
