# Nei distances in `fim`: two denominators, pairs and all demes

`fim` reports Nei's genetic distance in eight forms, together with the
corresponding genetic identities. This guide defines each form, explains
why they can differ, and describes the conditions under which the
all-demes geometric distance is negative.

Mathematical symbols use $\TeX$; literal setting names, statistic keys,
and code identifiers use backticks. Each displayed equation has one
unique tag beginning with $\text{N}$ for this document.

- [Who this is for](#who-this-is-for)
- [The short version](#the-short-version)
- [What a Nei distance measures](#what-a-nei-distance-measures)
- [The two denominators](#the-two-denominators)
- [A pair of demes, or all of them](#a-pair-of-demes-or-all-of-them)
- [Several loci: two ways to combine them](#several-loci-two-ways-to-combine-them)
- [When the distance is infinite](#when-the-distance-is-infinite)
- [When the distance is negative](#when-the-distance-is-negative)
- [How the forms relate to Jost's D](#how-the-forms-relate-to-josts-d)
- [Where to find the values](#where-to-find-the-values)
- [Other measures captured alongside](#other-measures-captured-alongside)
- [A short mathematical refresher](#a-short-mathematical-refresher)
- [Formula sheet](#formula-sheet)
- [References](#references)

## Who this is for

Written first for a researcher reading `fim`'s results, with no
programming assumed. The derivations and worked examples are for anyone
who wants to check the arithmetic step by step. Statistic keys such as
`NEI_D_PAIR_ARITH` are the literal names used in saved results and in the
Settings dialog; they are not mathematical variables. The
[statistics reference](statistics.md) lists statistic definitions and
result identifiers.

## The short version

- Nei's distance compares how often two gene copies match **between**
  demes with how often they match **within** demes. Zero means the demes
  are alike; it grows as they diverge. It is infinite when the selected
  identity rule yields zero.
- The **denominator** (the "within" part) can be a geometric mean (Nei's
  1972 original) or an arithmetic mean (the version Jost uses). The
  arithmetic-denominator distance is never smaller; the two agree
  exactly when the demes are equally diverse.
- **Pair** forms compare the two demes chosen for the scatter plot.
  **All-demes** forms summarize every deme at once.
- With several loci, Nei's own rule combines loci first and takes one
  ratio (the default). The **per-locus mean** takes one distance per
  locus and averages them.
- The all-demes geometric form **can be negative**. That is not an error;
  it means within-deme diversity is very uneven across demes. See
  [When the distance is negative](#when-the-distance-is-negative).
- Every form is computed and saved for every run. Choose which ones to
  *see* in Settings, "Statistics shown"; the Nei family starts hidden.

## What a Nei distance measures

At one locus, let $p_{k,i}$ be the frequency of allele $i$ in deme $k$.
If two copies are sampled independently from that deme, both copies
carry allele $i$ with probability $p_{k,i}p_{k,i}$. Add those
non-overlapping allele cases to obtain the within-deme gene identity:

```math
J_k=\sum_i p_{k,i}^2 \tag{N.1}
```

For example, if deme X has frequencies $(0.75,0.25)$, then:

```math
J_X=0.75^2+0.25^2=0.5625+0.0625=0.625 \tag{N.2}
```

For one copy from deme X and one from deme Y, the probability that both
are allele $i$ is $p_{X,i}p_{Y,i}$. Summing over the alleles present in
either deme gives the between-deme identity:

```math
J_{XY}=\sum_i p_{X,i}p_{Y,i} \tag{N.3}
```

If Y has frequencies $(0.25,0.75)$ over the same two alleles, the
products are $0.75(0.25)=0.1875$ for the first allele and
$0.25(0.75)=0.1875$ for the second. Therefore:

```math
J_{XY}=0.1875+0.1875=0.375 \tag{N.4}
```

The between-deme identity is smaller here because each deme favors a
different allele. Nei's identity $I$ compares $J_{XY}$ with a chosen
average of the within-deme identities. Identical allele-frequency
distributions give $I=1$; less between-deme matching gives a smaller
identity.

The distance transforms identity with the negative natural logarithm:

```math
D=-\ln(I) \tag{N.5}
```

Since $-\ln(1)=0$, identical populations have distance zero. As $I$
falls toward zero, the distance grows; at $I=0$, the limiting distance
is infinite. Under Nei's mutation model, this logarithm makes the
distance increase approximately in proportion to elapsed time after
isolated populations separate.

## The two denominators

"A typical within-deme identity" can be averaged two ways.

| Form | Pair denominator | Source |
| --- | --- | --- |
| Geometric | $\sqrt{J_XJ_Y}$ | Nei (1972) |
| Arithmetic | $(J_X+J_Y)/2$ | Jost, L. (2026) private communication |

For positive within identities, the arithmetic-geometric mean
inequality says that the arithmetic mean across $d$ demes is at least
the geometric mean. The pair case is $d=2$:

```math
\frac{1}{d}\sum_{k=1}^{d}J_k\geq
\left(\prod_{k=1}^{d}J_k\right)^{1/d} \tag{N.6}
```

For a pair, equality holds exactly when $J_X=J_Y$; across all demes,
it holds exactly when every $J_k$ is equal. Both identities divide
the same between-deme identity by these denominators, so:

```math
I_{\mathrm{arith}}\leq I_{\mathrm{geo}} \tag{N.7}
```

For a pair, the cross-identity is an inner product of two frequency
vectors. Cauchy-Schwarz bounds it by the product of their lengths:

```math
J_{XY}\leq\sqrt{J_XJ_Y} \tag{N.6a}
```

Thus the pair geometric identity cannot exceed one; (N.7) bounds the
pair arithmetic identity as well. Across all demes,
$J_{k\ell}\leq(J_k+J_\ell)/2$ for each pair, so averaging every pair
also bounds the all-demes arithmetic numerator:

```math
J_{\mathrm{between}}\leq\frac{1}{d}\sum_{k=1}^{d}J_k
\tag{N.6b}
```

This proves that the all-demes arithmetic identity is at most one.
Only the all-demes geometric identity lacks that bound.

Applying $-\ln(\cdot)$ reverses that ordering:

- the **arithmetic identity is never larger** than the geometric one, and
  the **arithmetic distance is never smaller**;
- they **agree exactly when the two demes are equally diverse**
  ($J_X=J_Y$), and differ most when one deme is much more diverse than
  the other.

Comparing the two is therefore itself informative: a large gap between
them says the demes differ in diversity, not only in which alleles they
carry. For the example above, $J_X=J_Y=0.625$, so the denominators
coincide even though the allele frequencies differ. The two forms then
give the same identity:
$I=J_{XY}/0.625=0.375/0.625=0.6$. The distance is
$-\ln(0.6)\approx0.511$. A denominator difference appears only when
the within-deme identities differ.

## A pair of demes, or all of them

**Pair forms** (`NEI_D_PAIR_*`) compare two demes: the pair selected in the
scatter plot, or Demes 1 and 2 by default. Values update with the selected
pair in the live view, in completed runs, and across generations. A deme
compared with itself has distance 0.

**All-demes forms** (`NEI_D_ALL_*`) summarize every deme at once. At one
locus, for $d$ demes, the numerator averages $J_{k\ell}$ over all
unordered pairs; the denominator averages or geometrically combines the
$d$ within-deme identities:

```math
J_{\mathrm{between}}=\frac{2}{d(d-1)}
\sum_{k<\ell}J_{k\ell} \tag{N.8}
```

```math
I_{\mathrm{all,geo}}=
\frac{J_{\mathrm{between}}}
{\lparen\prod_{k=1}^{d}J_k\rparen^{1/d}} \tag{N.9}
```

```math
I_{\mathrm{all,arith}}=
\frac{J_{\mathrm{between}}}
{\frac{1}{d}\sum_{k=1}^{d}J_k} \tag{N.10}
```

For two demes, there is one unordered pair and the all-demes numerator
is exactly $J_{12}$. Its two within identities are $J_1$ and $J_2$, so
each all-demes form equals its matching pair form.

Every run also saves the pair values for **every** pair of demes in
`pairwise.json`, so a pair you did not look at during the run can still be
compared later (see [Where to find the values](#where-to-find-the-values)).

## Several loci: two ways to combine them

Most runs track $L$ loci, and the two rules for combining them can give
different answers. Write $J_{k,\ell}$ and $J_{XY,\ell}$ for the
within- and between-deme identities at locus $\ell$.

**Nei's pooled rule** averages each identity over loci first. For a
pair, these pooled quantities are:

```math
\overline J_X=\frac{1}{L}\sum_{\ell=1}^{L}J_{X,\ell} \tag{N.11}
```

```math
\overline J_Y=\frac{1}{L}\sum_{\ell=1}^{L}J_{Y,\ell} \tag{N.12}
```

```math
\overline J_{XY}=\frac{1}{L}\sum_{\ell=1}^{L}J_{XY,\ell}
\tag{N.13}
```

Then `fim` applies the chosen denominator once to those three pooled
identities and takes one logarithm:

```math
I_{\mathrm{pool,geo}}=
\frac{\overline J_{XY}}{\sqrt{\overline J_X\overline J_Y}}
\tag{N.14}
```

```math
I_{\mathrm{pool,arith}}=
\frac{\overline J_{XY}}{(\overline J_X+\overline J_Y)/2}
\tag{N.15}
```

The same sequence applies to all demes. First average each within-deme
identity and the between-deme identity across loci:

```math
\overline J_k=\frac{1}{L}\sum_{\ell=1}^{L}J_{k,\ell} \tag{N.16}
```

```math
\overline J_{\mathrm{between}}=
\frac{1}{L}\sum_{\ell=1}^{L}J_{\mathrm{between},\ell}
\tag{N.17}
```

Then insert those averages in the all-demes ratios (N.9) and (N.10).
A locus contributes according to how much identity it supplies; it is
not assigned an independent, equal-weight distance.

**Per-locus mean** (the `_LOCUS_MEAN` forms) instead computes one
identity and distance at each locus, then averages the distances. If
$I_\ell$ is the selected pair or all-demes identity for locus $\ell$:

```math
D_{\mathrm{locus\ mean}}=
\frac{1}{L}\sum_{\ell=1}^{L}-\ln(I_\ell)
\tag{N.18}
```

Using $\ln(\prod x_\ell)=\sum\ln(x_\ell)$ shows that the corresponding
reported identity is the geometric mean of the per-locus identities:

```math
I_{\mathrm{locus\ mean}}=
\exp(-D_{\mathrm{locus\ mean}})
=\left(\prod_{\ell=1}^{L}I_\ell\right)^{1/L}
\tag{N.19}
```

For example, if two loci have identities $0.8$ and $0.2$, their
per-locus mean distance is
$[-\ln(0.8)-\ln(0.2)]/2\approx0.9163$, and the equivalent identity
is $\sqrt{0.8(0.2)}=0.4$. This is not the arithmetic mean of the
locus identities (which would be $0.5$).

The two rules differ most when loci behave differently. If any single
locus has zero identity, its distance is infinite, so the per-locus mean
is infinite. The pooled rule can remain finite if other loci have
positive between-deme identity: a zero at one locus does not make the
average between-deme identity zero.

Cost: the per-locus mean reuses the identities Nei's rule already
computes; it adds one ratio and one logarithm per locus, which is not
measurable next to the rest of a run.

## When the distance is infinite

For a pair, $J_{XY,\ell}=0$ when the demes share no allele at locus
$\ell$. Under the pooled rule, $I=0$ only if the mean
$\overline J_{XY}$ is zero, which requires zero cross-identity at every
locus. Under the per-locus mean, one locus with $I_\ell=0$ is enough:
$-\ln(0)$ is infinite, and averaging that distance remains infinite.
The same distinction applies to all-demes identities, where the
per-locus numerator averages cross-identities over all deme pairs.

The screen shows **∞**, with the tooltip "infinite (no allele is
shared)". Strict JSON cannot represent infinity, so `report.json`
stores `null` for the distance; the matching identity in `NEI_I_...` is
finite and is zero in this case. A batch summary omits a distance that
is infinite in any replicate, because the mean then has no finite value.

## When the distance is negative

Only one form can be negative: the **all-demes geometric** form
(`NEI_D_ALL_GEO` and `NEI_D_ALL_GEO_LOCUS_MEAN`). Every pair form and every
arithmetic form stays at or above 0.

### What is going on

The all-demes geometric identity divides average between-deme sharing
by the geometric mean of the within-deme identities. By (N.6), a
geometric mean is no larger than the arithmetic mean. If one deme is
much more diverse than the others, its within identity is small and
pulls the geometric denominator downward. The numerator can then be
larger than that denominator, giving $I>1$. Since $\ln(I)>0$ for $I>1$,
$-\ln(I)$ is negative. This can happen only for the all-demes geometric
form; pair identities are bounded by one through the Cauchy-Schwarz
inequality, and the all-demes arithmetic identity is bounded by one.

### A worked example

One locus, three demes:

| Deme | Allele frequencies | Within-deme identity $J_k$ |
| --- | --- | --- |
| 1 | fixed for allele A | 1 |
| 2 | fixed for allele A | 1 |
| 3 | 1000 alleles, each at frequency $0.001$, including A | 0.001 |

The third deme's within identity is the sum of one thousand equal
squared frequencies. For any deme, its own identity is calculated from
the squared frequency of each allele:

```math
J_3=1000(0.001)^2=0.001 \tag{N.20}
```

The between-deme identities are $J_{12}=1$ (both demes are fixed for
A), $J_{13}=0.001$, and $J_{23}=0.001$ (A is the only shared allele
with deme 3). Average the three unordered pairs:

```math
J_{\mathrm{between}}=\frac{1+0.001+0.001}{3}
=\frac{1.002}{3}=0.334 \tag{N.21}
```

The geometric mean of $d$ positive values is their product raised to
the power $1/d$, as in the denominator of (N.9). For two values,
$d=2$, this is the familiar square root. Here there are three
within-deme identities, so $d=3$ and the exponent is $1/3$: taking
the cube root. This gives the single value whose product with itself
three times equals the original product. Since
$0.1\cdot0.1\cdot0.1=0.001$, the denominator is:

```math
J_{\mathrm{within,geo}}=(1\cdot1\cdot0.001)^{1/3}=0.1 \tag{N.22}
```

Divide the pair average by that denominator, then take the negative
logarithm:

```math
I_{\mathrm{all,geo}}=\frac{0.334}{0.1}=3.34 \tag{N.23}
```

```math
D_{\mathrm{all,geo}}=-\ln(3.34)\approx-1.206 \tag{N.24}
```

For comparison, the arithmetic denominator is
$(1+1+0.001)/3\approx0.667$. It gives identity approximately $0.501$
and distance approximately $0.692$, which is positive. The numerator
did not change; the choice of within-deme averaging changed the
denominator.

### How to read it

A negative value does not mean the demes are "closer than identical".
It means the question this form asks, "how does typical between-deme
sharing compare with the geometric mean of within-deme sharing", is being
dominated by a few very diverse demes. Read it as a signal that
**within-deme diversity is highly unequal**, and:

- look at the arithmetic form beside it, which is not pulled toward the
  most diverse deme and stays in its ordinary range;
- look at `H_S` and the effective number of alleles to see the uneven
  diversity directly;
- use pair forms to find which demes drive the difference.

`fim` reports the value as computed and never clamps it to 0, because
clamping would hide exactly this signal. A value within rounding of 0 is
shown as 0.

## How the forms relate to Jost's D

Let $J_{\mathrm{within}}$ be the arithmetic mean of within-deme
identities and let $J_{\mathrm{between}}$ be the mean between-deme
identity. Jost's differentiation statistic (reported under the literal
key `D`) is one minus their ratio:

```math
D_{\mathrm{Jost}}=
1-\frac{J_{\mathrm{between}}}{J_{\mathrm{within}}}
\tag{N.25}
```

The all-demes arithmetic Nei identity is that same ratio, and its
distance applies the negative logarithm. Therefore:

```math
I_{\mathrm{all,arith}}=1-D_{\mathrm{Jost}} \tag{N.26}
```

```math
D_{\mathrm{NEI,all,arith}}=-\ln(1-D_{\mathrm{Jost}}) \tag{N.27}
```

`fim`'s default `D` combines loci by the same pooled rule
([locus_aggregation](configuration.md#locus_aggregation) `ratio_of_means`),
so the relation holds exactly for every report; with `mean_of_ratios` it
holds locus by locus. The same relation, applied to two demes, links each
pair arithmetic distance to Jost's `D` for that pair. It is a useful check:
the two are computed by independent code in `fim`. The equality requires
matching locus aggregation and the arithmetic denominator; it does not
hold for the geometric forms.

## Where to find the values

| Place | What is there |
| --- | --- |
| Run card, statistics panel | Rows for the forms you choose to show. Pair forms sit under a heading naming the pair ("Demes 1 and 3"). Hover a row for its value and a one-sentence description. |
| Settings, "Statistics shown" | Turn forms on and off; the "Nei distances" preset shows Nei's-rule distances for pairs and all demes. "Choose…" in the panel's caption opens it directly. |
| `report.json` | All-demes forms: `NEI_D_ALL_GEO`, `NEI_D_ALL_GEO_LOCUS_MEAN`, `NEI_D_ALL_ARITH`, `NEI_D_ALL_ARITH_LOCUS_MEAN`, and the `NEI_I_ALL_*` identities. |
| `summary.json` (batch) | Mean and confidence interval of each all-demes form across replicates. |
| `pairwise.json` | Every pair's four identities (each distance is $-\ln(I)$) and pairwise $F_{\mathrm{ST}}$, at the final generation. See [usage](usage.md#pairwisejson). |

A run saved before these statistics existed still opens: the values are
computed from its saved trajectory when you open it. Its saved files are
not changed.

## Other measures captured alongside

Computed and saved for every run, hidden until you show them:

- `D_m` and `R_ST`: Nei's (1973) mean pairwise between-deme diversity, in
  heterozygosity units, and its ratio to within-deme diversity.
- `G_ST_NEI_LOG` and `G_ST_HEDRICK`: two different statistics both called
  $G'_{\mathrm{ST}}$ in the literature. Nei's is logarithmic, for large
  differentiation; Hedrick's divides $G_{\mathrm{ST}}$ by its largest
  possible value.
- `F_ST`: coancestry $F_{\mathrm{ST}}$ (Goudet & Weir 2023).
- `F_ST_PAIR`: pairwise $F_{\mathrm{ST}}$ for the scatter's pair, from
  those two demes' data alone; every pair is in `pairwise.json`.
- `Gs` and `Gd`: the within- and between-deme gene identities themselves.

None of these can be chosen as a convergence statistic: the convergence
monitor was designed for the long-standing differentiation measures.

## A short mathematical refresher

The formulas use only a few familiar operations:

- **Add mutually exclusive cases.** At a locus, two copies either both
  carry allele 1, both carry allele 2, and so on. Those cases cannot
  happen at once, so their probabilities add. This gives (N.1) within a
  deme and (N.3) between demes.
- **Multiply independent choices.** To match on allele $i$, the first
  draw must carry it and the second draw must carry it. Their
  probabilities multiply, giving $p_{X,i}p_{Y,i}$ in (N.3).
- **Compare like quantities.** A Nei identity divides a between-deme
  match rate by a within-deme reference match rate. The numerator is
  unchanged when choosing the geometric or arithmetic denominator;
  only the reference changes.
- **Use the logarithm to convert a ratio to a distance.** If $I=1$,
  then $-\ln(I)=0$. If $0<I<1$, then $\ln(I)<0$, so the distance is
  positive. If $I>1$, the distance is negative; that case is possible
  only for the all-demes geometric identity.
- **Keep aggregation order visible.** Averaging identities and then
  taking a ratio is not generally the same as taking one ratio per locus
  and averaging the resulting distances. Equations (N.11)–(N.19) spell
  out the two orders rather than hiding them in a formula name.

The worked three-deme example above combines frequency vectors, squared
probabilities, pair products, averages, a geometric mean, and the
logarithm. Each intermediate value is shown for independent verification.

## Formula sheet

For one locus, calculate the within identities with (N.1), the
between-deme identities with (N.3), and the all-demes between average
with (N.8). Select the geometric or arithmetic denominator with
(N.6), (N.9), or (N.10). For multiple loci, use the pooled route in
(N.11)–(N.17), or the per-locus route in (N.18) and (N.19).
Convert any selected identity to distance with (N.5).

The corresponding result keys use `PAIR` for a selected pair and `ALL`
for all demes; `GEO` and `ARITH` select the denominator. The optional
`_LOCUS_MEAN` suffix selects per-locus distance averaging. For example,
`NEI_D_PAIR_GEO_LOCUS_MEAN` is the geometric-denominator mean of
per-locus pair distances, while `NEI_D_ALL_ARITH` is the pooled
all-demes arithmetic-denominator distance.

The identities use `fim`'s with-replacement convention,
$J_k=\sum_i p_{k,i}^2$ (see [migration conventions](migration-conventions.md)).
The [statistics reference](statistics.md) maps saved identifiers to
definitions. The [Python API guide](python-api.md) and generated
[API reference](../src/fim/API.md) document programmatic access. The
pair and all-demes calculations are implemented by
[`nei_pair_distance()` and `nei_all_demes_distance()`](../src/fim/statistics/genetic_distance.py).

## References

- Nei M (1972). Genetic distance between populations. *American
  Naturalist* 106(949):283–292.
  DOI: [10.1086/282771](https://doi.org/10.1086/282771)
- Nei M (1973). Analysis of gene diversity in subdivided populations.
  *Proceedings of the National Academy of Sciences* 70(12):3321–3323.
  DOI: [10.1073/pnas.70.12.3321](https://doi.org/10.1073/pnas.70.12.3321)
- Hedrick PW (2005). A standardized genetic differentiation measure.
  *Evolution* 59(8):1633–1638.
- Goudet J, Weir BS (2023): the coancestry-based F_ST estimators.
  `fim.statistics.differentiation` cites the equations it implements
  (coancestry F_ST, Eq. 4 and 8; pairwise F_ST, Eq. 10).
- Jost L (2008). G_ST and its relatives do not measure differentiation.
  *Molecular Ecology* 17(18):4015–4026.
  DOI: [10.1111/j.1365-294X.2008.03887.x](https://doi.org/10.1111/j.1365-294X.2008.03887.x)
- Jost, L. (2026) private communication: the arithmetic-mean denominator.
