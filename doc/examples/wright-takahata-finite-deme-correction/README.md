# Wright-Takahata finite-deme correction

## What this demonstrates

This example uses eight demes so that finite-deme effects are visible. It
watches `G_ST` and runs as the default batch of replicates, so its result is a
mean with a confidence interval to compare with the model's exact expectation.

The finite-deme theoretical prediction differs from the infinite-island
approximation. The correction depends on finite deme count; it is not a claim
that every stochastic single-locus replicate must equal the expectation.

The batch ran 10 replicates in about nine minutes on a development machine and
stopped at the minimum of 10 because the 95% confidence interval for `G_ST` was
already within ±0.02. The relaxation time is 1,685 generations (eight demes
with `m` = 0.003 relax slowly), so each replicate burned in for 8,424
generations and then averaged over a 33,696-generation window. Across the
replicates `G_ST` = 0.1930 ± 0.0027 (95% confidence half-width, an interval of
0.190 to 0.196), which contains the model's exact expectation of 0.195. `D` is
not watched here: its interval across replicates is 0.343 ± 0.018, so its
average is known less precisely than the watched statistic's.

## A settled statistic still wanders

The batch judges how precisely each average is known, not how flat a
replicate's line is. Open a replicate to see it as a single run: `G_ST` settles
quickly (its autocorrelation time is about 180 generations) while `D` does not.
Total diversity swings on a timescale of about 1,900 generations and `D` swings
with it, between about 0.05 and 0.76 in a single run of this configuration. That
is a property of the model, not a defect, and a longer run does not make the
line flatter, only the average better known.

`D` is a nonlinear function of the heterozygosities, so averaging its values
and computing it from the averaged heterozygosities give different numbers:
here 0.343 and 0.311 (see the section "Which average?" in
[Convergence](../../convergence.md)).
The statistics panel underlines the statistics a batch watches, and the run
messages explain the others.

## Why the precision is 0.02

The configuration sets `precision: 0.02`, twice the default. A batch stops
once the 95% confidence interval across replicates is within ±0.02 for each
watched statistic. The default 0.01 would need windows about four times as
long; at 0.02 `G_ST` is known to about ±0.003, which still separates the
finite-deme expectation from the infinite-island one.

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
