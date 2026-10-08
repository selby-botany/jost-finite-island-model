# Finite-length alleles (the K-allele model)

## What this demonstrates

The infinite-alleles default gives each mutation a new identity. This
configuration selects `finite_alleles` and a length-3 locus, which has
64 possible states. With two initial alleles and a relatively high
mutation rate, mutations can recur to states already present in the
population during an interactive run.

The run converges at generation 8,977, after about a minute on a busy
development machine. Its trailing-window mean `D` is 0.611 ± 0.006 (one
standard error); the final generation's own value is 0.862. This is a
small illustration of recurrent mutation in a finite state space, not a
distance-based stepwise mutation model.

## Why the tolerance is 0.02

The configuration sets `convergence_tolerance: 0.02`, twice the default.
A run stops only once its trailing-window mean is known to half the
tolerance. At the default 0.01 this single locus needs about 18,000
generations and a few minutes; at 0.02 the mean is known to about ±0.01,
which is plenty for a demonstration of recurrent mutation.

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
