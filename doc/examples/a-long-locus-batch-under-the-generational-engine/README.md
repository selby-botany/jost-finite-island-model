# A long-locus batch under the generational engine

## What this demonstrates

This companion timing workload runs 16 replicates with 35 demes and the
`generational` engine. Its seven-base finite-alleles locus has more
possible states than the preceding large-deme example. As there, a
`precision: 0.0` (never reached), a burn-in of 1 and `stop_batch_early:
false` make every one of the 16 replicates run exactly 100 generations, so
the amount of work is fixed.

The run takes about 55 seconds on a busy development machine, a
little longer than the large-deme example's 40. The across-replicate
mean `D` is 0.0262 ± 0.0030 (95% confidence interval).

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
