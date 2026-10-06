# Wright-Takahata finite-deme correction

## What this demonstrates

This run uses eight demes so that finite-deme effects are visible. It
watches `G_ST`; the example's single-locus final value is noisy, so
compare batches or analytic expectations rather than treating one run
as a precise estimate.

The finite-deme theoretical prediction differs from the infinite-island
approximation. The correction depends on finite deme count and is not a
claim that this stochastic single-locus result must equal the
expectation.

The run converges on `G_ST` at generation 10,109, after under a minute on
ordinary development hardware. Its trailing-window mean `G_ST` is
0.205 ± 0.008 (one standard error), close to the model's exact
expectation of 0.195; the final generation's own value, 0.148, is one
noisy draw.

## Why the tolerance is 0.02

The configuration sets `convergence_tolerance: 0.02`, twice the default.
A run stops only once the watched statistic's trailing-window mean is
known to half the tolerance. At the default 0.01 this single locus needs
about 40,000 generations and several minutes; at 0.02 the mean is known
to about ±0.01, which still separates the finite-deme expectation from
the infinite-island one.

## Related literature

- Wright S (1931). Evolution in Mendelian populations. *Genetics*
  16(2):97–159. [doi:10.1093/genetics/16.2.97](https://doi.org/10.1093/genetics/16.2.97).
- Crow JF, Aoki K (1984). Group selection for a polygenic behavioral
  trait: estimating the degree of population subdivision.
  *Proceedings of the National Academy of Sciences* 81(19):6073–6077.
  [doi:10.1073/pnas.81.19.6073](https://doi.org/10.1073/pnas.81.19.6073).
- Takahata N (1983). Gene identity and genetic differentiation of
  populations in the finite island model. *Genetics* 104(3):497–512.
  [doi:10.1093/genetics/104.3.497](https://doi.org/10.1093/genetics/104.3.497).

These works provide the island-model and finite-deme context for the
correction; see the guide for the calculation used in this example.

## Run

```console
fim run doc/examples/wright-takahata-finite-deme-correction/config.yaml \
    --output results/wright-takahata-finite-deme-correction --quiet
```

See the [usage-guide explanation](../../usage.md#wright-takahata-finite-deme-correction)
and the [differentiation-measures guide](../../jost-differentiation-measures.md).
