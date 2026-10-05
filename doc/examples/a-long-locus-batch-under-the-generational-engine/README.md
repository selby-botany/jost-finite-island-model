# A long-locus batch under the generational engine

## What this demonstrates

This companion timing workload runs 16 replicates with 35 demes and the
`generational` engine. Its seven-base finite-alleles locus has more
possible states than the preceding large-deme example. A pinned
100-generation cap keeps the comparison short.

This is an engine workload, not an equilibrium result or a universal
benchmark. Runtime depends on the machine and software environment.

## Run

```console
fim run doc/examples/a-long-locus-batch-under-the-generational-engine/config.yaml \
    --output results/a-long-locus-batch-under-the-generational-engine --quiet
```

See the [usage-guide explanation](../../usage.md#a-long-locus-batch-under-the-generational-engine)
and [engine-backend guidance](../../fim-simulator-design.md#46-choosing-an-engine-backend).
