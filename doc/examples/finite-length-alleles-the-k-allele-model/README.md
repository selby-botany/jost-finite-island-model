# Finite-length alleles (the K-allele model)

## What this demonstrates

The infinite-alleles default gives each mutation a new identity. This
configuration selects `finite_alleles` and a length-3 locus, which has
64 possible states. With two initial alleles and a relatively high
mutation rate, mutations can recur to states already present in the
population during an interactive run.

The batch ran 20 replicates in about twenty seconds on a development
machine and stopped because the 95% confidence interval for `D` was within
±0.02. Each replicate burned in for 117 generations (5 relaxation times of 23)
and then averaged over a 467-generation window. Across the replicates, `D` =
0.602 ± 0.019 and `G_ST` = 0.0638 ± 0.0027 (95% confidence half-widths). This
is a small illustration of recurrent mutation in a finite state space, not a
distance-based stepwise mutation model.

## Why the precision is 0.02

The configuration sets `precision: 0.02`, twice the default. A batch stops
once the 95% confidence interval across replicates is within ±0.02 for each
watched statistic, and each replicate averages over a window matched to that
precision (see [Batches](../../convergence.md#batches-replicates-that-average)).
The default 0.01 would need windows about four times as long; at 0.02 `D` is
known to ±0.019, which is plenty for a demonstration of recurrent mutation.

## Related literature

Kimura M, Crow JF (1964). The number of alleles that can be maintained
in a finite population. *Genetics* 49:725–738.
[doi:10.1093/genetics/49.4.725](https://doi.org/10.1093/genetics/49.4.725).
The finite-allele state space is related to this classical model; the
example's specific parameters are chosen for a compact demonstration.

## Run

```console
fim run doc/examples/finite-length-alleles-the-k-allele-model/config.yaml \
    --output results/finite-length-alleles-the-k-allele-model --quiet
```

See the [usage-guide explanation](../../usage.md#finite-length-alleles-the-k-allele-model)
and the [configuration reference](../../configuration.md).
