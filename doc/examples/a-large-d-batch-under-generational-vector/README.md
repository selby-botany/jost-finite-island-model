# A large-d batch under generational-vector

## What this demonstrates

This is a timing workload, not an equilibrium example. It runs 16
replicates with 70 demes and the `generational-vector` engine, each for
exactly 100 generations, so the amount of work is the same every time.
The finite-alleles model uses a five-base locus.

Two settings fix the amount of work:

- `convergence_window: 101` is one more generation than the 100-generation
  cap can record, so no replicate can stop early by converging. Each one
  ends "at the cap", which is expected here.
- `stop_batch_early: false` turns off the adaptive replicate stop, so all
  16 replicates always run. Without it, the default tolerance of 0.01 stops
  the batch at the 10-replicate minimum.

The run takes about 40 seconds on a busy development machine. The
across-replicate mean `D` is 0.0275 ± 0.0016 (95% confidence interval).
The configuration is intended to exercise a larger deme count and the
vector backend. Its short run is not suitable for interpreting
equilibrium statistics: the population is nowhere near equilibrium after
100 generations.

The committed results are a scientific reference, not a cross-platform
bit-for-bit baseline. The vector engine uses BLAS floating-point reductions;
their rounding order can differ between machines and change a later random
draw's decision. Validation compares the archived batch using its confidence
intervals and requires exact agreement between `auto` and an explicitly
configured vector run on the same host.

## Run

```console
fim run doc/examples/a-large-d-batch-under-generational-vector/config.yaml \
    --output results/a-large-d-batch-under-generational-vector --quiet
```

See the [usage-guide explanation](../../usage.md#a-large-d-batch-under-generational-vector)
and [engine-backend guidance](../../fim-simulator-design.md#46-choosing-an-engine-backend).
