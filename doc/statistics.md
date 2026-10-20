# Statistics reference

Select a statistic's underlined name in a desktop statistics table to open
its definition here. The value and its full-precision tooltip remain in
the table; the color swatch controls whether its trajectory is plotted.
This reference is available in the application without a network connection.
Links to the longer mathematical guides, Python API, and source open in
your browser and require access to the repository.

Each entry gives the reported identifier, interpretation, formula, and
implementation. For a scripted analysis, start with the
[Python guide](python-api.md#statistics-and-errors). For interpretation of
diversity and differentiation, see the
[differentiation measures guide](jost-differentiation-measures.md).

## Notation and aggregation

For one locus, `p_ia` is the frequency of allele `a` in deme `i`, `d` is
the number of demes, and `w_i` is a normalized deme weight. Frequencies
sum to 1 within each deme. The pooled frequency is
`p_a = sum_i(w_i * p_ia)`. Unless stated otherwise, formulas below use
equal deme weights, `w_i = 1/d`, as the simulator's reports do.
The `deme_weighting` setting applies only to `E_ST`, `Delta`, and `MI`;
`size` weights these measures by deme size. It does not change the
weighting of the other reported measures.

The formulas define statistics of population frequencies, not
finite-sample estimators. `N` in the model's mathematics denotes gene
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
`D = d/(d-1) * (H_T-H_S)/(1-H_S)`.
With equal deme weights, 0 denotes identical allele frequencies and 1
denotes disjoint allele sets. `D` is not a measure of nearness to fixation.

[Mathematical guide](jost-differentiation-measures.md) ·
[Python API: jost_d](../src/fim/API.md#fim.statistics.differentiation.jost_d) ·
[Source](../src/fim/statistics/differentiation.py)

## `G_ST`

Nei's heterozygosity ratio: `G_ST = (H_T-H_S)/H_T`.
Its interpretation depends on within-deme diversity; high allelic
differentiation need not imply high `G_ST`. The statistic is undefined
when `H_T = 0`, including fixation for the same allele in every deme.

[Mathematical guide](jost-differentiation-measures.md) ·
[Python API: g_st](../src/fim/API.md#fim.statistics.differentiation.g_st) ·
[Source](../src/fim/statistics/differentiation.py)

## `H_S`

Mean within-deme expected heterozygosity:
`H_S = sum_i(w_i * (1-sum_a(p_ia^2)))`.
It is the probability that two independent gene copies drawn within a
deme carry different alleles, averaged over demes.

[Mathematical guide](jost-differentiation-measures.md) ·
[Python API: h_s](../src/fim/API.md#fim.statistics.differentiation.h_s) ·
[Source](../src/fim/statistics/differentiation.py)

## `H_T`

Expected heterozygosity of the pooled allele-frequency distribution:
`H_T = 1-sum_a(p_a^2)`. Pooling includes both within- and between-deme
variation; it is not the mean of the individual deme heterozygosities.

[Mathematical guide](jost-differentiation-measures.md) ·
[Python API: h_t](../src/fim/API.md#fim.statistics.differentiation.h_t) ·
[Source](../src/fim/statistics/differentiation.py)

## `H_ST`

Partitioned between-deme heterozygosity:
`H_ST = (H_T-H_S)/(1-H_S)`.
It satisfies `H_T = H_S + H_ST - H_S*H_ST`. With equal weights,
`D = d/(d-1) * H_ST`. This is not Hedrick's standardized `G'_ST`.

[Mathematical guide](jost-differentiation-measures.md) ·
[Python API: h_st](../src/fim/API.md#fim.statistics.differentiation.h_st) ·
[Source](../src/fim/statistics/differentiation.py)

## `E_ST`

Entropy differentiation: `E_ST = (S_T-S_S)/S_w`, where
`S_w = -sum_i(w_i * ln(w_i))`. With equal weights, `S_w = ln(d)`.
Entropy weights rare alleles more strongly than heterozygosity does.
The `deme_weighting` setting controls the weights for this measure.

[Mathematical guide](jost-differentiation-measures.md) ·
[Python API: e_st](../src/fim/API.md#fim.statistics.differentiation.e_st) ·
[Source](../src/fim/statistics/differentiation.py)

## `K_ST`

Allele-number differentiation:
`K_ST = (A_T/A_S-1)/(d-1)`, where here `A_T` is the number of distinct
alleles in the pooled population and `A_S` is the mean number present
per deme. Presence means strictly positive frequency. These are
order-zero allele counts, not the effective counts displayed below.
Equal deme weights are required.

[Mathematical guide](jost-differentiation-measures.md) ·
[Python API: k_st](../src/fim/API.md#fim.statistics.differentiation.k_st) ·
[Source](../src/fim/statistics/differentiation.py)

## `A_CGD`

Caballero-Garcia-Dorado allelic distance. For a pair of demes, count
alleles present in exactly one deme and divide by 2; `A_CGD` is the
mean over distinct unordered pairs. Frequencies affect presence but
do not otherwise weight the distance. This is a count, not a proportion.

[Mathematical guide](jost-differentiation-measures.md) ·
[Python API: allelic_distance](../src/fim/API.md#fim.statistics.differentiation.allelic_distance) ·
[Source](../src/fim/statistics/differentiation.py)

## `Delta`

Gregorius's distance-oriented differentiation, displayed as delta with
subscript G. Compare each deme's frequency vector with the weighted
mixture of all remaining demes using total variation distance
`0.5 * sum_a(abs(p_ia-p_rest,a))`, then average over demes.
The mixture and outer average use the configured deme weights.
For two equally weighted demes, this is their total variation distance.
For more than two, it is not the average of all pairwise distances.

[Mathematical guide](jost-differentiation-measures.md) ·
[Python API: gregorius_delta](../src/fim/API.md#fim.statistics.differentiation.gregorius_delta) ·
[Source](../src/fim/statistics/differentiation.py)

## `MI`

Allele-by-deme mutual information, displayed as `I`:
`MI = S_T-S_S`, in nats. It measures the statistical association between
allele identity and deme membership. Dividing by deme-weight entropy
gives `E_ST`; `MI` itself is not normalized to the unit interval.
The entropies in this calculation use the configured deme weights.

[Mathematical guide](jost-differentiation-measures.md) ·
[Python API: mutual_information](../src/fim/API.md#fim.statistics.differentiation.mutual_information) ·
[Source](../src/fim/statistics/differentiation.py)

## `Gs`

Within-deme gene identity: `Gs = 1-H_S`. Sampling is with replacement.
Identity here means identity in allelic state, not a recorded pedigree
relationship.

[Mathematical guide](finite-island-model-introduction.md) ·
[Python API: gs](../src/fim/API.md#fim.statistics.differentiation.gs) ·
[Source](../src/fim/statistics/differentiation.py)

## `Gd`

Mean between-deme gene identity. With equal weights,
`Gd = (d*(1-H_T)-(1-H_S))/(d-1)`.
It is the mean probability of an allelic match between gene copies
drawn from distinct demes.

[Mathematical guide](finite-island-model-introduction.md) ·
[Python API: gd](../src/fim/API.md#fim.statistics.differentiation.gd) ·
[Source](../src/fim/statistics/differentiation.py)

## `D_m`

Nei's mean pairwise between-deme gene diversity:
`D_m = d/(d-1) * (H_T-H_S)`. Unlike `D`, it does not divide by within-deme
identity. Equal deme weights are required.

[Mathematical guide](jost-differentiation-measures.md) ·
[Python API: d_m](../src/fim/API.md#fim.statistics.differentiation.d_m) ·
[Source](../src/fim/statistics/differentiation.py)

## `R_ST`

Nei's relative between-deme diversity: `R_ST = D_m/H_S`.
It is undefined when `H_S = 0`. This identifier denotes Nei's diversity
ratio, not the similarly named microsatellite statistic based on
allele-size variance.

[Mathematical guide](jost-differentiation-measures.md) ·
[Python API: r_st](../src/fim/API.md#fim.statistics.differentiation.r_st) ·
[Source](../src/fim/statistics/differentiation.py)

## `G_ST_NEI_LOG`

Nei's logarithmic `G'_ST`:
`ln(J_S/J_T)/(-ln(J_T))`, with `J_S = 1-H_S` and `J_T = 1-H_T`.
This is distinct from Hedrick's standardized statistic despite their
shared notation. The reported value is undefined at `H_T = 0` or
`H_S = 1`.

[Mathematical guide](jost-differentiation-measures.md) ·
[Python API: g_st_log](../src/fim/API.md#fim.statistics.differentiation.g_st_log) ·
[Source](../src/fim/statistics/differentiation.py)

## `G_ST_HEDRICK`

Hedrick's standardized `G'_ST = G_ST/G_ST(max)`, where
`G_ST(max) = (d-1)*(1-H_S)/(d-1+H_S)`.
It rescales `G_ST` by its maximum at the given within-deme diversity
and deme count. The report returns an undefined value when `G_ST`
is undefined.

[Mathematical guide](jost-differentiation-measures.md) ·
[Python API: g_st_prime](../src/fim/API.md#fim.statistics.differentiation.g_st_prime) ·
[Source](../src/fim/statistics/differentiation.py)

## `F_ST`

Overall coancestry-based fixation index:
`F_ST = (Gs-Gd)/(1-Gd)`.
The report uses equal deme weights and pooled heterozygosities across
loci. It returns an undefined value when `Gd = 1`.

[Mathematical guide](jost-differentiation-measures.md) ·
[Python API: derived_differentiation](../src/fim/API.md#fim.statistics.differentiation.derived_differentiation) ·
[Source](../src/fim/statistics/differentiation.py)

## `F_ST_PAIR`

Pairwise coancestry-based fixation index for the selected demes:
`F_ST_PAIR = ((J_X+J_Y)/2-J_XY)/(1-J_XY)`, with
`J_X = sum_a(p_Xa^2)` and `J_XY = sum_a(p_Xa*p_Ya)`.
Only the selected pair contributes. The implementation returns 0
when both demes are fixed for the same allele.

[Mathematical guide](jost-differentiation-measures.md) ·
[Python API: pairwise_f_st](../src/fim/API.md#fim.statistics.differentiation.pairwise_f_st) ·
[Source](../src/fim/statistics/differentiation.py)

## Nei distances

The `NEI_I_*` identifiers report normalized gene identity, `I`;
`NEI_D_*` report distance, `-ln(I)`. These distances are distinct from
Jost's `D`. Each identifier specifies three choices:

| Identifier part | Meaning |
|---|---|
| `PAIR` | The two demes selected for the scatter plot |
| `ALL` | All demes, with equal weight |
| `GEO` | Geometric mean of within-deme identities in the denominator |
| `ARITH` | Arithmetic mean in the denominator |
| No suffix | Average identities over loci, then compute the ratio |
| `LOCUS_MEAN` | Average per-locus distances; the corresponding identity is their geometric mean |

For a pair, `I = J_XY/sqrt(J_X*J_Y)` under `GEO`, or
`I = J_XY/((J_X+J_Y)/2)` under `ARITH`. The all-demes forms use mean
between-deme identity in the numerator and the specified mean of all
within-deme identities in the denominator.

No shared alleles gives `I = 0` and infinite distance. With `LOCUS_MEAN`,
one locus with zero shared identity makes the mean distance infinite.
For more than two demes, the geometric normalization can produce
`I > 1` and negative distance when deme diversities differ. These values
are properties of that normalization, not negative evolutionary time.
Saved JSON represents non-finite distances as `null`; the desktop
displays infinity explicitly.

[Mathematical derivation and worked examples](nei-distances.md) ·
[Python API: nei_family_from_identities](../src/fim/API.md#fim.statistics.genetic_distance.nei_family_from_identities) ·
[Source](../src/fim/statistics/genetic_distance.py)

## Effective allele counts

The displayed <sup>H</sup>D<sub>S</sub> and
<sup>H</sup>D<sub>T</sub> are inverse-concentration effective
allele counts: `1/(1-H_S)` and `1/(1-H_T)`, respectively.
Explore uses `A_S` and `A_T` for these predicted counts. They give the
number of equally frequent alleles with the same heterozygosity, not
the number of distinct allele labels and not effective population size.

[Mathematical guide](jost-differentiation-measures.md) ·
[Python API: effective_allele_count](../src/fim/API.md#fim.statistics.differentiation.effective_allele_count) ·
[Source](../src/fim/statistics/differentiation.py)

## Entropy

Explore's `S_S` is mean within-deme Shannon entropy and `S_T` is pooled
Shannon entropy. For frequencies `p_a`, entropy is
`-sum_a(p_a * ln(p_a))`, omitting zero-frequency terms. Units are nats.
Explore shows theoretical predictions under the assumptions stated in
its model description, not measurements from a saved trajectory.

[Mathematical guide](jost-differentiation-measures.md) ·
[Python API: theory_for_configuration](../src/fim/API.md#fim.statistics.theory.theory_for_configuration) ·
[Source](../src/fim/statistics/theory.py)

## Identity recovery

Explore's retention rate, equilibrium identity, and half-life describe
the mutation-negligible identity-recovery approximation. The retention
rate controls the exponential approach to the predicted identity;
the half-life is the generations required to halve the departure from
that equilibrium. These are theoretical predictions, not fitted
convergence times.

[Model assumptions](finite-island-model-introduction.md) ·
[Python API](../src/fim/API.md#fim.statistics.differentiation.identity_recovery_trajectory) ·
[Source](../src/fim/statistics/differentiation.py)

## Mutation negligible equilibrium

Explore's indicator tests whether mutation is negligible relative to
migration in the equilibrium approximation. It is a regime check,
not a measured statistic or proof that mutation has no effect.

[Model assumptions](finite-island-model-introduction.md) ·
[Python API](../src/fim/API.md#fim.statistics.differentiation.mutation_negligible_equilibrium) ·
[Source](../src/fim/statistics/differentiation.py)

## Related references

- [Command reference and desktop workflow](usage.md)
- [Configuration reference](configuration.md)
- [Convergence and uncertainty](convergence.md)
- [Python scripting guide](python-api.md)
- [Generated Python API](../src/fim/API.md)
- [Statistic catalog source](../src/fim/statistics/catalog.py)
