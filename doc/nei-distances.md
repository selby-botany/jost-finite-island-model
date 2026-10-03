# Nei distances in `fim`: two denominators, pairs and all demes

`fim` reports Nei's genetic distance in eight forms, and the matching
genetic identities. This guide explains what each form measures, why the
forms can disagree, and how to read one result that surprises almost
everyone the first time: a *negative* distance.

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
- [Formula sheet](#formula-sheet)
- [References](#references)
- [Metadata](#metadata)

## Who this is for

Written first for a researcher reading `fim`'s results, with no
programming assumed; the formula sheet near the end is for anyone checking
the arithmetic. Code names (`NEI_D_PAIR_ARITH` and so on) are the keys used
in saved results and in the Settings dialog.

## The short version

- Nei's distance compares how often two gene copies match **between**
  demes with how often they match **within** demes. Zero means the demes
  are alike; it grows as they diverge; it is infinite when they share no
  allele at all.
- The **denominator** (the "within" part) can be a geometric mean (Nei's
  1972 original) or an arithmetic mean (the version Jost uses). The
  arithmetic form is never smaller; the two agree exactly when the demes
  are equally diverse.
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

Pick two gene copies at random, at one locus. The chance that they are the
same allele is their **gene identity**:

- `J_X`, both copies from deme X: the sum of the squared allele
  frequencies in X;
- `J_XY`, one copy from deme X and one from deme Y: the sum, over alleles,
  of the frequency in X times the frequency in Y.

If X and Y were the same population, picking from "X and Y" would match as
often as picking from "X and X". Nei's **identity** `I` is the ratio of
the between-deme identity to a typical within-deme identity. It is 1 for
identical demes and falls toward 0 as they diverge. The **distance** is
`D = -ln(I)`: 0 for identical demes, larger as they diverge. The logarithm
makes the distance grow roughly in proportion to time since two isolated
populations separated, under Nei's mutation model.

## The two denominators

"A typical within-deme identity" can be averaged two ways.

| Form | Pair denominator | Source |
| --- | --- | --- |
| Geometric | `sqrt(J_X * J_Y)` | Nei (1972) |
| Arithmetic | `(J_X + J_Y) / 2` | Jost, L. (2026) private communication |

An arithmetic mean is never smaller than a geometric mean of the same
numbers, and the two are equal only when the numbers are equal. So:

- the **arithmetic identity is never larger** than the geometric one, and
  the **arithmetic distance is never smaller**;
- they **agree exactly when the two demes are equally diverse**
  (`J_X = J_Y`), and differ most when one deme is much more diverse than
  the other.

Comparing the two is therefore itself informative: a large gap between
them says the demes differ in diversity, not only in which alleles they
carry.

## A pair of demes, or all of them

**Pair forms** (`NEI_D_PAIR_*`) compare two demes: whichever two the
scatter plot shows, Deme 1 and Deme 2 unless you choose others. The values
follow your choice live, in a finished run, and while scrubbing through
generations. A deme compared with itself has distance 0.

**All-demes forms** (`NEI_D_ALL_*`) summarize every deme at once. The
numerator is `J_between`, the average between-deme identity over every
pair of demes. The denominator is the arithmetic or geometric mean of all
the within-deme identities `J_1 ... J_d`. With two demes, each all-demes
form is exactly the matching pair form.

Every run also saves the pair values for **every** pair of demes in
`pairwise.json`, so a pair you did not look at during the run can still be
compared later (see [Where to find the values](#where-to-find-the-values)).

## Several loci: two ways to combine them

Most runs track several loci, and the two rules for combining them can
give different answers.

- **Nei's rule** (the default forms, such as `NEI_D_PAIR_ARITH`): average
  each identity over the loci first, then take one ratio and one
  logarithm. A locus with little variation contributes little.
- **Per-locus mean** (the `_LOCUS_MEAN` forms): compute one distance per
  locus and average the distances. Each locus counts equally.

They differ most when loci behave differently. The sharpest case: if any
single locus shares no allele between the demes, that locus's distance is
infinite, so the per-locus mean is infinite too, while Nei's rule stays
finite because the other loci still share alleles.

Cost: the per-locus mean reuses the identities Nei's rule already
computes; it adds one ratio and one logarithm per locus, which is not
measurable next to the rest of a run.

## When the distance is infinite

If two demes share no allele at a locus (Nei's rule: at every locus), the
identity is 0 and the distance is infinite. The screen shows **∞**, with
the tooltip "infinite (no allele is shared)". Saved results cannot hold
infinity (they are strict JSON), so `report.json` and `summary.json` store
`null` for the distance; the identity beside it (`NEI_I_...`) is always a
finite number, 0 in this case, so nothing is lost. A batch's summary leaves
out a distance that is infinite in any replicate, since such a mean has no
finite value.

## When the distance is negative

Only one form can be negative: the **all-demes geometric** form
(`NEI_D_ALL_GEO` and `NEI_D_ALL_GEO_LOCUS_MEAN`). Every pair form and every
arithmetic form stays at or above 0.

### What is going on

The all-demes geometric identity divides the **average** between-deme
identity by the **geometric mean** of the within-deme identities. A
geometric mean is pulled strongly toward its smallest values. When one
deme is far more diverse than the others, its within-deme identity is
tiny, and it drags the geometric mean down so far that the average
between-deme identity can end up larger. The identity is then above 1 and
its logarithm, the distance, is below 0.

### A worked example

One locus, three demes:

| Deme | Alleles | Within-deme identity `J` |
| --- | --- | --- |
| 1 | fixed for allele A | 1 |
| 2 | fixed for allele A | 1 |
| 3 | 1000 alleles, equally common, including A | 0.001 |

- Between-deme identities: demes 1 and 2 match every time (1); deme 3
  matches deme 1 or 2 only through allele A (0.001 each). Average: 0.334.
- Geometric mean of `J`: the cube root of `1 x 1 x 0.001`, which is 0.1.
- Identity: 0.334 / 0.1 = 3.34. Distance: `-ln(3.34)` = **-1.21**.
- Arithmetic mean of `J` for comparison: 0.667. Identity 0.501, distance
  **0.69**: ordinary, and positive.

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

Jost's `D` is `1 - J_between / J_within`, with `J_within` the arithmetic
mean of the within-deme identities. That is `1 -` the all-demes arithmetic
identity, so with Nei's rule:

`NEI_D_ALL_ARITH = -ln(1 - D)`

`fim`'s default `D` combines loci by the same pooled rule
([locus_aggregation](configuration.md#locus_aggregation) `ratio_of_means`),
so the relation holds exactly for every report; with `mean_of_ratios` it
holds locus by locus. The same relation, applied to two demes, links each
pair arithmetic distance to Jost's `D` for that pair. It is a useful check:
the two are computed by independent code in `fim`.

## Where to find the values

| Place | What is there |
| --- | --- |
| Run card, statistics panel | Rows for the forms you choose to show. Pair forms sit under a heading naming the pair ("Demes 1 and 3"). Hover a row for its value and a one-sentence description. |
| Settings, "Statistics shown" | Turn forms on and off; the "Nei distances" preset shows Nei's-rule distances for pairs and all demes. "Choose…" in the panel's caption opens it directly. |
| `report.json` | All-demes forms: `NEI_D_ALL_GEO`, `NEI_D_ALL_GEO_LOCUS_MEAN`, `NEI_D_ALL_ARITH`, `NEI_D_ALL_ARITH_LOCUS_MEAN`, and the `NEI_I_ALL_*` identities. |
| `summary.json` (batch) | Mean and confidence interval of each all-demes form across replicates. |
| `pairwise.json` | Every pair's four identities (each distance is `-ln` of its identity) and pairwise F_ST, at the final generation. See [usage](usage.md#pairwisejson). |

A run saved before these statistics existed still opens: the values are
computed from its saved trajectory when you open it. Its saved files are
not changed.

## Other measures captured alongside

Computed and saved for every run, hidden until you show them:

- `D_m` and `R_ST`: Nei's (1973) mean pairwise between-deme diversity, in
  heterozygosity units, and its ratio to within-deme diversity.
- `G_ST_NEI_LOG` and `G_ST_HEDRICK`: two different statistics both called
  G'_ST in the literature. Nei's is logarithmic, for large
  differentiation; Hedrick's divides G_ST by its largest possible value.
- `F_ST`: coancestry F_ST (Goudet & Weir 2023).
- `F_ST_PAIR`: pairwise F_ST for the scatter's pair, from those two demes'
  data alone; every pair is in `pairwise.json`.
- `Gs` and `Gd`: the within- and between-deme gene identities themselves.

None of these can be chosen as a convergence statistic: the convergence
monitor was designed for the long-standing differentiation measures.

## Formula sheet

At one locus, `p_k,i` is the frequency of allele `i` in deme `k`.

```text
J_k        = sum_i p_k,i^2                       within deme k
J_kl       = sum_i p_k,i p_l,i                   between demes k and l
J_between  = mean over pairs k < l of J_kl

Pair, demes X and Y:
  geometric   I = J_XY / sqrt(J_X J_Y)
  arithmetic  I = J_XY / ((J_X + J_Y) / 2)

All d demes:
  geometric   I = J_between / (J_1 J_2 ... J_d)^(1/d)
  arithmetic  I = J_between / ((J_1 + ... + J_d) / d)

Distance      D = -ln(I)        (infinite when I = 0)

Nei's rule:      average every J over loci, then one ratio
Per-locus mean:  D = mean over loci of -ln(I_locus);
                 the reported identity is exp(-D)

Pairwise F_ST:   ((J_X + J_Y) / 2 - J_XY) / (1 - J_XY)
```

Identities use `fim`'s with-replacement convention (`sum p^2`), as the
rest of `fim` does ([migration conventions](migration-conventions.md)).

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

## Metadata

```text
generator-name: Claude Code
generator-version: Claude Opus 5.5
generator-model-token: claude-opus-5-5
generator-provider: Anthropic
generation-date: 2026-10-03
generator-responsibility: documentation
```
