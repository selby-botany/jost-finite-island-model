# Allele sharing after a founder split

## What this demonstrates

Three demes start with overlapping pairs of alleles at different frequencies:
80% allele 0 and 20% allele 1; 80% allele 1 and 20% allele 2; and 20% allele 0
and 80% allele 2. Migration shares these alleles, drift changes their
frequencies and can remove them, and mutation introduces new private alleles.
The split is an imposed initial condition, not a simulated ancestral founding
event. Use the separate equilibrium-split example for that process.

Watch the trajectory and allele-composition graphs to compare three questions:

- `A_CGD`: how many alleles occur in one population but not another?
  Presence matters, not frequency; the value can jump when an allele appears
  or disappears, and it is not bounded by 1.
- Gregorius's `Delta`: how different are each population's allele frequencies
  from those of the other populations combined?
- Sherwin's mutual information (`MI`): how much does knowing population
  membership tell you about allele identity? Values use natural logarithms
  (nats), not bits.

These are compared with `D` and `G_ST`, which summarize differentiation
differently. They need not track each other or have the same scale.

`track_expensive_statistics: true` makes the supplemental measures
available through the trajectory, not just the final report. The desktop
app sets that key from Settings, "Statistics shown" instead: show
A<sub>CGD</sub>, δ<sub>G</sub> and I there (they start hidden, since
computing them every generation makes runs take longer) to see them. For this
example, all five measures are watched: the run does not claim precision
for the supplemental measures merely because `D` has settled.

### Saved result

The seeded single run stops by convergence at generation 10,499, after a
389-generation burn-in, before its derived cap of 200,000. Its evidence
window contains 10,111 generations. Window means (not final-generation
draws) include `D = 0.3568`, `Delta = 0.4528`, and `A_CGD = 3.7508`.
The example took about 17 seconds on the development machine; speed varies
with hardware. A converged stochastic trace still fluctuates.

Mutation is high enough to maintain polymorphism in this small teaching
population; it is an illustrative rate, not an empirical estimate for a
particular species.

`precision: 0.05` is an absolute half-width for bounded statistics.
For unbounded watched statistics without a per-statistic override, the target
is `0.05 * max(1, abs(mean))`; it is not a shared vertical scale.

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
reproduce their empirical analyses or the selection experiments in
Caballero and García-Dorado.

**Normalization:** `A_CGD` here averages equation 5's pairwise distance over
distinct unordered deme pairs. Caballero and García-Dorado's equation 6
instead includes zero self-distances in the all-pairs average; multiply
this implementation's value by `(d - 1) / d` to obtain that convention.
For three demes fixed for different alleles the two values are 1 and 2/3.
That endpoint remains a useful formula check, but is no longer the runnable
example: with no migration or mutation it has no population dynamics.

## Why a single run

This example sets `n_replicates: 1` to keep a single trajectory easy to
follow, so the numbers quoted here, and the outputs saved with it, are those
of one run. The engine's default is `n_replicates: 200`, an adaptively
stopped batch that reports a confidence interval, and that is what a
researcher would normally use. One run's final numbers are one noisy draw:
for an estimate with an error bar, run a batch (see
[an adaptive replicate batch with a confidence interval](../an-adaptive-replicate-batch-with-a-confidence-interval/README.md)).

On Configure, choose **Run settings...** to change this run's replicate
count, generation cap, or precision without editing YAML or changing your
saved Settings. **Use saved Settings** fills the editor from your defaults;
**Restore loaded settings** brings back the example's original choices.

## Run

```console
fim run doc/examples/literature-distance-statistics-from-an-explicit-founder-split/config.yaml \
    --output results/literature-distance-statistics-from-an-explicit-founder-split --quiet
```

See the [usage-guide explanation](../../usage.md#literature-distance-statistics-from-an-explicit-founder-split)
and the [differentiation-measures guide](../../jost-differentiation-measures.md).
