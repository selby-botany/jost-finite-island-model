# A large-d batch under generational-vector

## What this demonstrates

This is a timing workload, not an equilibrium example. It runs 16
replicates with 70 demes and the `generational-vector` engine, using a
short, explicitly pinned 100-generation cap so the workload finishes
quickly. The finite-alleles model uses a five-base locus.

The configuration is intended to exercise a larger deme count and the
vector backend. Its short run is not suitable for interpreting
equilibrium statistics.

## Run

```console
fim run doc/examples/a-large-d-batch-under-generational-vector/config.yaml \
    --output results/a-large-d-batch-under-generational-vector --quiet
```

See the [usage-guide explanation](../../usage.md#a-large-d-batch-under-generational-vector)
and [engine-backend guidance](../../fim-simulator-design.md#46-choosing-an-engine-backend).
