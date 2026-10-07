# Literature distance statistics from an explicit founder split

## What this demonstrates

Three demes start fixed for three different alleles, with no migration or
mutation. The one-generation run is a deterministic sanity check for
supplemental distance-oriented measures: Caballero-García-Dorado allelic
distance (`A_CGD`), Gregorius's `Delta`, and Sherwin's mutual information
(`MI`). The run also reports the standard differentiation statistics.

`track_expensive_statistics: true` makes the supplemental measures
available through the trajectory, not just the final report. The desktop
app sets that key from Settings, "Statistics shown" instead: show
A<sub>CGD</sub>, δ<sub>G</sub> and I there (they start hidden, since
computing them every generation makes runs take longer) to see them. For this
complete three-way split, the distances and differentiation measures
equal 1 and `MI` equals `log(3)`. This is an intentionally simple check,
not evidence that the measures are interchangeable.

## Related literature

- Caballero A, García-Dorado A (2013). Allelic diversity and its
  implications for the rate of adaptation. *Genetics* 195:1373–1384.
  [doi:10.1534/genetics.113.158410](https://doi.org/10.1534/genetics.113.158410).
- Gregorius HR (2010). Linking diversity and differentiation.
  *Diversity* 2:370–394.
  [doi:10.3390/d2030370](https://doi.org/10.3390/d2030370).
- Sherwin WB (2010). Entropy and information approaches to genetic
  diversity and its expression: genomic geography. *Entropy*
  12(7):1765–1798.
  [doi:10.3390/e12071765](https://doi.org/10.3390/e12071765).

The example exercises measures motivated by these works; it does not
reproduce their empirical analyses.

## Run

```console
fim run doc/examples/literature-distance-statistics-from-an-explicit-founder-split/config.yaml \
    --output results/literature-distance-statistics-from-an-explicit-founder-split --quiet
```

See the [usage-guide explanation](../../usage.md#literature-distance-statistics-from-an-explicit-founder-split)
and the [differentiation-measures guide](../../jost-differentiation-measures.md).
