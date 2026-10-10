# Finite-length alleles (the K-allele model)

## What this demonstrates

The infinite-alleles default gives each mutation a new identity. This
configuration selects `finite_alleles` and a length-3 locus, which has
64 possible states. With two initial alleles and a relatively high
mutation rate, mutations can recur to states already present in the
population during an interactive run.

The run converges at generation 6,771, after about ten seconds on a
development machine. It burns in for 117 generations (5 relaxation times of
23), then averages: `D` = 0.614 ± 0.009 (one standard error) and `G_ST` =
0.066 ± 0.001, over a 6,655-generation window; the final generation's own `D`
is 0.593. This is a small illustration of recurrent mutation in a finite state
space, not a distance-based stepwise mutation model.

## Why the precision is 0.02

The configuration sets `precision: 0.02`, twice the default.
A run stops once the average of each watched statistic is known to plus or
minus the precision at 95% confidence, that is, once its standard error is at
most 0.02 / 1.96 ≈ 0.010. The error of an average falls as one over the square
root of the window length, so the default 0.01 would take about four times as
long; at 0.02 `D` is known to about ±0.017, which is plenty for a
demonstration of recurrent mutation.

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
