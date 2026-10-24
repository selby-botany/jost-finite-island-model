# Statistics reference

Select a statistic's underlined name in a desktop statistics table to open
its definition here. The value and its full-precision tooltip remain in
the table; the color swatch controls whether its trajectory is plotted.
This reference is available in the application without a network connection.
Links to model background, specialized derivations, and the Python API open in
your browser and require access to the repository.

Each entry gives the reported identifier, interpretation, formula, and
implementation. For a scripted analysis, start with the
[Python guide](python-api.md#statistics-and-errors). This page documents
all supported statistics; API links lead to their implementation
documentation, which includes source links.

## Notation and aggregation

Mathematical symbols and formulas use $\TeX$; literal settings, API
identifiers, and reported keys use backticks. Displayed equations have
unique tags beginning with $\text{S}$. Section headings retain the
reported keys so desktop help links continue to identify each entry.

The desktop science views use the mathematical symbols, including in
tooltips, result messages, and chart readouts. For example, the saved key
`H_ST` is displayed as $H_{ST}$, `Delta` as $\delta_G$, and `MI` as $I$.
Native selection menus cannot reliably typeset scripts; their text spells
out "subscript" or "superscript", with the selected mathematical symbol
shown alongside. Configuration fields, command-line arguments, and saved
JSON/CSV keys retain their literal identifiers.

For one locus, $p_{i,a}$ is the frequency of allele $a$ in deme $i$,
$d$ is the number of demes, and $w_i$ is a normalized deme weight.
The weights satisfy $w_i\geq0$ and $\sum_{i=1}^{d}w_i=1$.
Frequencies sum to 1 within each deme. The pooled frequency is:

```math
p_a=\sum_{i=1}^{d}w_i p_{i,a} \tag{S.1}
```

Unless stated otherwise, formulas below use equal deme weights,
$w_i=1/d$, as the simulator's reports do.
The `deme_weighting` setting applies only to `E_ST`, `Delta`, and `MI`;
`size` weights these measures by deme size. It does not change the
weighting of the other reported measures.

The formulas define statistics of population frequencies, not
finite-sample estimators. $N$ in the model's mathematics denotes gene
copies; the desktop population field counts individuals and applies the
selected ploidy.

Locus and time aggregation are separate operations. In the default
multilocus report, `D` and `G_ST` use ratios of mean heterozygosities;
averaging per-locus ratios is a different estimand. The other measures
follow their documented aggregation rules. A convergence estimate may
also average over generations, and a batch summary averages across
replicates. Consult the
[report API](../src/fim/API.md#fim.engine.report.report_for_state),
[output schemas](usage.md#output-schemas), and
[convergence reference](convergence.md) before comparing these quantities.
An undefined value is not evidence of zero differentiation.

## `D`

Jost's frequency-weighted allelic differentiation:

```math
D=\frac{d}{d-1}\frac{H_T-H_S}{1-H_S} \tag{S.2}
```

With equal deme weights, 0 denotes identical allele frequencies and 1
denotes disjoint allele sets. $D$ is not a measure of nearness to fixation.

[Python API: jost_d](../src/fim/API.md#fim.statistics.differentiation.jost_d)

## `G_ST`

Nei's heterozygosity ratio:

```math
G_{ST}=\frac{H_T-H_S}{H_T} \tag{S.3}
```

Its interpretation depends on within-deme diversity; high allelic
differentiation need not imply high $G_{ST}$. The statistic is undefined
when $H_T=0$, including fixation for the same allele in every deme.

[Python API: g_st](../src/fim/API.md#fim.statistics.differentiation.g_st)

## `H_S`

Mean within-deme expected heterozygosity:

```math
H_S=\sum_{i=1}^{d}w_i\left(1-\sum_a p_{i,a}^2\right) \tag{S.4}
```

It is the probability that two independent gene copies drawn within a
deme carry different alleles, averaged over demes.

[Python API: h_s](../src/fim/API.md#fim.statistics.differentiation.h_s)

## `H_T`

Expected heterozygosity of the pooled allele-frequency distribution:

```math
H_T=1-\sum_a p_a^2 \tag{S.5}
```

Pooling includes both within- and between-deme
variation; it is not the mean of the individual deme heterozygosities.

[Python API: h_t](../src/fim/API.md#fim.statistics.differentiation.h_t)

## `H_ST`

Partitioned between-deme heterozygosity:

```math
H_{ST}=\frac{H_T-H_S}{1-H_S} \tag{S.6}
```

It satisfies the partition identity:

```math
H_T=H_S+H_{ST}-H_S H_{ST} \tag{S.7}
```

With equal weights, $D=dH_{ST}/(d-1)$. This is not Hedrick's
standardized $G^\prime_{ST}$.

[Python API: h_st](../src/fim/API.md#fim.statistics.differentiation.h_st)

## `E_ST`

Entropy differentiation:

```math
E_{ST}=\frac{S_T-S_S}{S_w} \tag{S.8}
```

Here $S_T$ is pooled Shannon entropy, $S_S$ is the weighted mean
within-deme entropy, and $S_w$ is the entropy of the deme weights:

```math
S_w=-\sum_{i=1}^{d}w_i\ln(w_i) \tag{S.9}
```

With equal weights, $S_w=\ln(d)$.
Entropy weights rare alleles more strongly than heterozygosity does.
The `deme_weighting` setting controls the weights for this measure.

[Python API: e_st](../src/fim/API.md#fim.statistics.differentiation.e_st)

## `K_ST`

Allele-number differentiation:

```math
K_{ST}=\frac{A_T/A_S-1}{d-1} \tag{S.10}
```

Here $A_T$ is the number of distinct
alleles in the pooled population and $A_S$ is the mean number present
per deme. Presence means strictly positive frequency. These are
order-zero allele counts, not the effective counts displayed below.
Equal deme weights are required.

[Python API: k_st](../src/fim/API.md#fim.statistics.differentiation.k_st)

## `A_CGD`

Caballero-Garcia-Dorado allelic distance. For a pair of demes, count
alleles present in exactly one deme and divide by 2; `A_CGD` is the
mean over distinct unordered pairs. Frequencies affect presence but
do not otherwise weight the distance. This is a count, not a proportion.

[Python API: allelic_distance](../src/fim/API.md#fim.statistics.differentiation.allelic_distance)

## `Delta`

Gregorius's distance-oriented differentiation, displayed as $\Delta_G$.
Compare each deme's frequency vector with the weighted
mixture of all remaining demes using total variation distance

```math
\operatorname{TV}(p_i,p_{\mathrm{rest},i})=
\frac{1}{2}\sum_a\left|p_{i,a}-p_{\mathrm{rest},i,a}\right| \tag{S.11}
```

Here $p_{\mathrm{rest},i,a}$ is the frequency of allele $a$ in the
weighted mixture excluding deme $i$. Average the distances over demes.
The mixture and outer average use the configured deme weights.
For two equally weighted demes, this is their total variation distance.
For more than two, it is not the average of all pairwise distances.

[Python API: gregorius_delta](../src/fim/API.md#fim.statistics.differentiation.gregorius_delta)

## `MI`

Allele-by-deme mutual information, displayed as $I$:

```math
I=S_T-S_S \tag{S.12}
```

Units are nats. It measures the statistical association between
allele identity and deme membership. Dividing by deme-weight entropy
gives `E_ST`; `MI` itself is not normalized to the unit interval.
The entropies in this calculation use the configured deme weights.

[Python API: mutual_information](../src/fim/API.md#fim.statistics.differentiation.mutual_information)

## `Gs`

Within-deme gene identity, reported as `Gs`:

```math
J_{\mathrm{within}}=1-H_S \tag{S.13}
```

Sampling is with replacement.
Identity here means identity in allelic state, not a recorded pedigree
relationship.

[Mathematical guide](finite-island-model-introduction.md) ·
[Python API: gs](../src/fim/API.md#fim.statistics.differentiation.gs)

## `Gd`

Mean between-deme gene identity. With equal weights,

```math
J_{\mathrm{between}}=\frac{d(1-H_T)-(1-H_S)}{d-1} \tag{S.14}
```

This quantity is reported as `Gd`.
It is the mean probability of an allelic match between gene copies
drawn from distinct demes.

[Mathematical guide](finite-island-model-introduction.md) ·
[Python API: gd](../src/fim/API.md#fim.statistics.differentiation.gd)

## `D_m`

Nei's mean pairwise between-deme gene diversity:

```math
D_m=\frac{d}{d-1}(H_T-H_S) \tag{S.15}
```

Unlike $D$, it does not divide by within-deme
identity. Equal deme weights are required.

[Python API: d_m](../src/fim/API.md#fim.statistics.differentiation.d_m)

## `R_ST`

Nei's relative between-deme diversity:

```math
R_{ST}=\frac{D_m}{H_S} \tag{S.16}
```

It is undefined when $H_S=0$. This identifier denotes Nei's diversity
ratio, not the similarly named microsatellite statistic based on
allele-size variance.

[Python API: r_st](../src/fim/API.md#fim.statistics.differentiation.r_st)

## `G_ST_NEI_LOG`

Nei's logarithmic $G^\prime_{ST}$:

```math
G'_{ST,\mathrm{Nei}}=\frac{\ln(J_S/J_T)}{-\ln(J_T)} \tag{S.17}
```

Here $J_S=1-H_S$ and $J_T=1-H_T$.
This is distinct from Hedrick's standardized statistic despite their
shared notation. The reported value is undefined at $H_T=0$ or
$H_S=1$.

[Python API: g_st_log](../src/fim/API.md#fim.statistics.differentiation.g_st_log)

## `G_ST_HEDRICK`

Hedrick's standardized $G^\prime_{ST}$ divides by the attainable maximum:

```math
G'_{ST,\mathrm{Hedrick}}=\frac{G_{ST}}{G_{ST}(\max)} \tag{S.18}
```

```math
G_{ST}(\max)=\frac{(d-1)(1-H_S)}{d-1+H_S} \tag{S.19}
```

It rescales $G_{ST}$ by its maximum at the given within-deme diversity
and deme count. The report returns an undefined value when `G_ST`
is undefined.

[Python API: g_st_prime](../src/fim/API.md#fim.statistics.differentiation.g_st_prime)

## `F_ST`

Overall coancestry-based fixation index:

```math
F_{ST}=\frac{J_{\mathrm{within}}-J_{\mathrm{between}}}
{1-J_{\mathrm{between}}} \tag{S.20}
```

The report uses equal deme weights and pooled heterozygosities across
loci. It returns an undefined value when $J_{\mathrm{between}}=1$.

[Python API: derived_differentiation](../src/fim/API.md#fim.statistics.differentiation.derived_differentiation)

## `F_ST_PAIR`

Pairwise coancestry-based fixation index for the selected demes:

```math
F_{ST,\mathrm{pair}}=\frac{(J_X+J_Y)/2-J_{XY}}{1-J_{XY}} \tag{S.21}
```

Here $J_X=\sum_a p_{X,a}^2$, $J_Y=\sum_a p_{Y,a}^2$, and
$J_{XY}=\sum_a p_{X,a}p_{Y,a}$.
Only the selected pair contributes. The implementation returns 0
when both demes are fixed for the same allele.

[Python API: pairwise_f_st](../src/fim/API.md#fim.statistics.differentiation.pairwise_f_st)

## Nei distances

The `NEI_I_*` identifiers report normalized gene identity, $I$;
`NEI_D_*` report distance:

```math
D_{\mathrm{Nei}}=-\ln(I) \tag{S.22}
```

These distances are distinct from Jost's $D$. The symbol $I$ in this
section denotes Nei identity, not the mutual information in (S.12).
Each identifier specifies three choices:

| Identifier part | Meaning |
|---|---|
| `PAIR` | The two demes selected for the scatter plot |
| `ALL` | All demes, with equal weight |
| `GEO` | Geometric mean of within-deme identities in the denominator |
| `ARITH` | Arithmetic mean in the denominator |
| No suffix | Average identities over loci, then compute the ratio |
| `LOCUS_MEAN` | Average per-locus distances; the corresponding identity is their geometric mean |

For a pair, the `GEO` and `ARITH` identities are, respectively:

```math
I_{\mathrm{pair,geo}}=\frac{J_{XY}}{\sqrt{J_XJ_Y}} \tag{S.23}
```

```math
I_{\mathrm{pair,arith}}=\frac{J_{XY}}{(J_X+J_Y)/2} \tag{S.24}
```

The all-demes forms use mean
between-deme identity in the numerator and the specified mean of all
within-deme identities in the denominator.

No shared alleles gives $I=0$ and infinite distance. With `LOCUS_MEAN`,
one locus with zero shared identity makes the mean distance infinite.
For more than two demes, the geometric normalization can produce
$I>1$ and negative distance when deme diversities differ. These values
are properties of that normalization, not negative evolutionary time.
Saved JSON represents non-finite distances as `null`; the desktop
displays infinity explicitly.

[Mathematical derivation and worked examples](nei-distances.md) ·
[Python API: nei_family_from_identities](../src/fim/API.md#fim.statistics.genetic_distance.nei_family_from_identities)

## Effective allele counts

The displayed ${}^{H}D_S$ and ${}^{H}D_T$ are
inverse-concentration effective allele counts:

```math
{}^{H}D_S=\frac{1}{1-H_S} \tag{S.25}
```

```math
{}^{H}D_T=\frac{1}{1-H_T} \tag{S.26}
```

Explore uses `A_S` and `A_T` for these predicted counts. They give the
number of equally frequent alleles with the same heterozygosity, not
the number of distinct allele labels and not effective population size.

[Python API: effective_allele_count](../src/fim/API.md#fim.statistics.differentiation.effective_allele_count)

## Entropy

Explore's `S_S` reports mean within-deme Shannon entropy $S_S$, and
`S_T` reports pooled Shannon entropy $S_T$. For frequencies $p_a$,
entropy is:

```math
S=-\sum_{a:p_a>0}p_a\ln(p_a) \tag{S.27}
```

Zero-frequency terms are omitted. Units are nats.
Explore shows theoretical predictions under the assumptions stated in
its model description, not measurements from a saved trajectory.

[Python API: theory_for_configuration](../src/fim/API.md#fim.statistics.theory.theory_for_configuration)

## Identity recovery

Explore's retention rate, equilibrium identity, and half-life describe
the mutation-negligible identity-recovery approximation. The retention
rate controls the exponential approach to the predicted identity;
the half-life is the generations required to halve the departure from
that equilibrium. These are theoretical predictions, not fitted
convergence times.

The Run card's trajectory graph offers the same approximation as an
opt-in overlay: `f₀`, drawn from a founder event (identity by descent
starting at 1) recovering toward equilibrium under migration alone, at
this configuration's own `N` and `m`. Off by default (the "f₀ (identity
recovery)" checkbox beside the graph; `set_show_identity_recovery_
trajectory`), because — like Explore's own numbers above — it is a
closed-form reference computed from the configuration alone, not a
measurement of the run it is drawn beside: it assumes zero mutation and
a single common ancestor at generation zero, neither of which a typical
run's own settings need match.

[Model assumptions](finite-island-model-introduction.md) ·
[Python API](../src/fim/API.md#fim.statistics.differentiation.identity_recovery_trajectory)

## Mutation negligible equilibrium

Explore's indicator tests whether mutation is negligible relative to
migration in the equilibrium approximation. It is a regime check,
not a measured statistic or proof that mutation has no effect.

[Model assumptions](finite-island-model-introduction.md) ·
[Python API](../src/fim/API.md#fim.statistics.differentiation.mutation_negligible_equilibrium)

## Worked example: two demes and two alleles

This example uses one locus and equal deme weights. Deme $X$ has
frequencies $(3/4,1/4)$ for alleles $A$ and $B$; deme $Y$ has
$(1/4,3/4)$. Pool the two demes by averaging each allele's frequency:

```math
p_A=\frac{1}{2}\frac{3}{4}+\frac{1}{2}\frac{1}{4} \tag{S.28}
```

Thus $p_A=1/2$; the same calculation gives $p_B=1/2$. To find
within-deme identity, square the frequencies and add:

```math
J_X=\left(\frac{3}{4}\right)^2+\left(\frac{1}{4}\right)^2 \tag{S.29}
```

This gives $J_X=10/16=5/8$. Reversing the frequencies gives
$J_Y=5/8$ too, so their mean is $J_{\mathrm{within}}=5/8$.
Its complement is $H_S=1-5/8=3/8$. For the pooled population:

```math
J_T=\left(\frac{1}{2}\right)^2+\left(\frac{1}{2}\right)^2 \tag{S.30}
```

Thus $J_T=1/2$ and $H_T=1/2$. The excess pooled heterozygosity is
$H_T-H_S=1/8$. Substituting in (S.2) and (S.3) gives:

```math
D=\frac{2}{1}\frac{1/8}{5/8} \tag{S.31}
```

```math
G_{ST}=\frac{1/8}{1/2} \tag{S.32}
```

The results are $D=2/5=0.4$ and $G_{ST}=1/4=0.25$. Both use the
same numerator, but different denominators; they are not interchangeable
scales of the same reported number.

For between-deme identity, multiply matching allele frequencies across
demes rather than squaring frequencies within a deme:

```math
J_{XY}=\frac{3}{4}\frac{1}{4}+\frac{1}{4}\frac{3}{4} \tag{S.33}
```

This gives $J_{\mathrm{between}}=J_{XY}=3/8$. The coancestry index is:

```math
F_{ST}=\frac{5/8-3/8}{1-3/8} \tag{S.34}
```

It evaluates to $2/5$. Equality with $D$ in this example is not a
general identity. Since the two within-deme identities are equal,
their arithmetic and geometric means both equal $5/8$, giving:

```math
I_{\mathrm{pair}}=\frac{3/8}{5/8} \tag{S.35}
```

Thus Nei identity is $3/5$, and its distance is:

```math
D_{\mathrm{Nei}}=-\ln(3/5) \tag{S.36}
```

The distance is approximately $0.510826$. The logarithm turns identity
into a distance; it does not make this number equal to Jost's $D$.

Entropy uses logarithms of the individual frequencies, not their squares.
Both demes have the same entropy:

```math
S_S=-\frac{3}{4}\ln(3/4)-\frac{1}{4}\ln(1/4) \tag{S.37}
```

This is approximately $0.562335$ nats. The pooled entropy is
$S_T=\ln(2)$, approximately $0.693147$ nats. Their difference is
mutual information:

```math
I=\ln(2)-\left[-\frac{3}{4}\ln(3/4)-\frac{1}{4}\ln(1/4)\right] \tag{S.38}
```

Here $I$ is approximately $0.130812$ nats, not the Nei identity
$3/5$ above. Normalizing by $S_w=\ln(2)$ gives:

```math
E_{ST}=\frac{I}{\ln(2)} \tag{S.39}
```

The result is approximately $0.188722$. Finally, both alleles occur
in both demes, so $K_{ST}=0$ and the reported allelic distance
`A_CGD` is 0 despite the frequency differences. In contrast, the
total variation distance is $(|3/4-1/4|+|1/4-3/4|)/2=1/2$.
These measures answer different questions about the same frequencies.

## Related references

- [Command reference and desktop workflow](usage.md)
- [Configuration reference](configuration.md)
- [Convergence and uncertainty](convergence.md)
- [Python scripting guide](python-api.md)
- [Generated Python API](../src/fim/API.md)
