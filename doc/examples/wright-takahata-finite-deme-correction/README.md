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

The run converges on `G_ST` at generation 21,910, after about a minute
on a development machine. The burn-in is 8,424 generations (5 relaxation
times of 1,685: eight demes with `m` = 0.003 relax slowly), and the average
over the 13,487-generation window that follows gives `G_ST` = 0.193 ± 0.007
(one standard error), close to the model's exact expectation of 0.195; the
final generation's own value, 0.291, is one noisy draw. `D` is not watched
here, so its own error bar (0.29 ± 0.06) was never required to be small.

## Why the precision is 0.02

The configuration sets `precision: 0.02`, twice the default.
A run stops once the watched statistic's average is known to plus or minus
the precision at 95% confidence (a standard error of at most about 0.010). The
default 0.01 would take about four times as long; at 0.02 `G_ST` is known to
about ±0.013, which still separates the finite-deme expectation from the
infinite-island one.

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
