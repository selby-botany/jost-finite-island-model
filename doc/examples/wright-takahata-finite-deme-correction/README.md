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
