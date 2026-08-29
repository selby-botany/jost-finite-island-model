# Differentiation measures for conservation genetics: a plain-language guide

A plain-language guide to:

> Jost L, Archer F, Flanagan S, Gaggiotti O, Hoban S, Latch E (2018).
> "Differentiation measures for conservation genetics."
> *Evolutionary Applications* 11(7):1139–1148.
> DOI: [10.1111/eva.12590](https://doi.org/10.1111/eva.12590) ·
> PMID: [30026802](https://pubmed.ncbi.nlm.nih.gov/30026802/)

Quotations and equation numbers refer to the publicly available
accepted-article version of the paper, which carries a notice that it
precedes copyediting, typesetting, and proofreading. A short note near the
end of this document records the handful of places where recomputing the
paper's own numbers turned up a discrepancy from the printed text.

- [Differentiation measures for conservation genetics: a plain-language guide](#differentiation-measures-for-conservation-genetics-a-plain-language-guide)
  - [Who this document is for, and how to read it](#who-this-document-is-for-and-how-to-read-it)
  - [The one-paragraph version](#the-one-paragraph-version)
  - [Part I — Ground floor: the biology, from zero](#part-i--ground-floor-the-biology-from-zero)
  - [Part II — Measuring diversity inside one group](#part-ii--measuring-diversity-inside-one-group)
  - [Part III — Two groups or more: "differentiation" splits in two](#part-iii--two-groups-or-more-differentiation-splits-in-two)
  - [Part IV — Worked examples](#part-iv--worked-examples)
  - [Part V — Why the classical reasoning is wrong](#part-v--why-the-classical-reasoning-is-wrong)
  - [Part VI — What controls what, if the model runs to equilibrium](#part-vi--what-controls-what-if-the-model-runs-to-equilibrium)
  - [Part VII — The two-allele special case](#part-vii--the-two-allele-special-case)
  - [Part VIII — What the paper recommends](#part-viii--what-the-paper-recommends)
  - [Part IX — Misconceptions the paper is correcting](#part-ix--misconceptions-the-paper-is-correcting)
  - [Part X — Using these measures outside population genetics](#part-x--using-these-measures-outside-population-genetics)
  - [Appendix A: notation and formula sheet](#appendix-a-notation-and-formula-sheet)
  - [Appendix B: errata and ambiguities in the accepted-article text](#appendix-b-errata-and-ambiguities-in-the-accepted-article-text)
  - [Appendix C: where this paper sits in Jost's larger program](#appendix-c-where-this-paper-sits-in-josts-larger-program)
  - [Appendix D: works cited](#appendix-d-works-cited)
  - [Appendix E: the differentiation debate, 2008–2011](#appendix-e-the-differentiation-debate-20082011)
  - [Metadata](#metadata)

---

## Who this document is for, and how to read it

No genetics background is assumed — not "a little rusty," none. Part I
builds the vocabulary from nothing: what a gene is, what an allele is, what
it means for a population to be "fixed." Everything after that uses only
arithmetic, one step of algebra, and a single logarithm.

This document is longer than the paper it summarizes, on purpose. The
paper is eight dense pages written for population geneticists who already
know what F<sub>ST</sub> is and have opinions about it. Most of the extra
length here is worked arithmetic that the paper states as a bare result,
spelled out step by step.

Everything in this document is drawn from the published paper itself, from
Wikipedia, and from other publicly cited sources named along the way — no
part of it depends on correspondence with the paper's authors.

Suggested reading paths:

| If you want | Read |
|---|---|
| The gist in five minutes | The one-paragraph version, then Part IX |
| To understand the argument | Parts I–V |
| To compute the measures yourself | Parts II–IV, Appendix A |
| To apply the same math to non-genetic data | Part X (read Parts II and V first) |

Every numeric claim in Part IV was recomputed from the paper's own
definitions rather than copied from its text; see Appendix B for the one
place a recomputation disagrees with a printed value.

---

## The one-paragraph version

Geneticists have two families of statistics that both get called "measures
of differentiation," and they measure genuinely different things. The
older family (G<sub>ST</sub>, F<sub>ST</sub>, θ) measures **how close each
subpopulation is to having lost all its internal variation** — call it
"nearness to fixation." The newer family (Jost's `D`, entropy
differentiation E<sub>ST</sub>, allele-count differentiation K<sub>ST</sub>)
measures **how few alleles the subpopulations have in common** — "allelic
differentiation." These two quantities have no necessary relationship: one
can sit at 1.0 while the other sits near 0.0, and not because of sampling
error, but because of what they fundamentally are. Conservation managers
almost always want the second family (should both populations be saved, or
is one a near-copy of the other?), but the literature has habitually
handed them the first. The paper shows, with worked counterexamples, that
this substitution produces exactly backwards conservation advice — and it
overturns a widely repeated rule of thumb along the way: under the finite
island model (a standard model of migration between subpopulations,
covered in a
[companion document](finite-island-model-introduction.md)),
the absolute number of migrants per generation, `Nm`, controls nearness
to fixation, **not** allelic differentiation, which instead is controlled
by m / (μ(d-1)), where μ is the mutation rate and `d` is the
number of subpopulations.

---

## Part I — Ground floor: the biology, from zero

### Genes, loci, and alleles

An organism's genome is a long string over a four-letter alphabet
(`A`, `C`, `G`, `T`) — think of it as a very long array of characters,
copied, with rare errors, from parent to offspring.

- A **locus** (plural **loci**) is a *region* of that array — a contiguous
  interval, fixed by where it starts and how far it runs. It says **where to
  look**, never what is found there. A locus may span a single character or
  a run of thousands.
- An **allele** is a *value* that can occupy that region — the whole
  sequence a given copy carries across the interval, read as one
  indivisible label. If some individuals carry `AAGCT` over a
  five-character locus and others carry `AAGGT`, those are two alleles of
  that locus.
- A **gene** is a locus whose sequence codes for something functional.
  Whether a locus is a gene or not changes no formula in this paper — it
  changes only how much you should *care* about the answer, which turns
  out to be one of the paper's practical points.

The analogy that survives the whole document: a locus is a **field**, an
allele is a **value that field can hold**, and a population is a
**collection of records**. Every measure discussed here is a statistic
computed from the histogram of values in one field, across a collection of
records.

Alleles are treated throughout as **unordered categorical labels** — `AAGCT`
is not "greater than" `AAGGT`, merely different. Every measure here depends
only on the *frequencies* of the labels, never on any similarity between
them. That assumption is also the main limit on reusing these measures on
non-genetic data — see Part X.

### Distance between alleles is a different model

This is the point that most often trips people up when they first meet the
paper: a genotype or allele label can indeed be given a metric in a different
model, and then mutation becomes something like a transition in a space of
sequence states. That is a perfectly sensible model for **sequence
divergence** or **evolutionary distance**, but it is not the model used here.

The paper is not asking, "How far apart are these allele labels in a
sequence or mutational metric?" It is asking, "How different are the
frequency distributions of the labels across demes, and how much of the
variation is unique to one deme rather than shared across them?" The metric
space model and the categorical-label model answer different questions.

For a sequence-space model, one typically needs additional structure:

- a way to compare allele labels to each other,
- a mutation model that says how one label turns into another,
- and usually a distance or similarity matrix that depends on the biology of
 the locus.

With that extra structure, one can define actual genetic distance, pairwise
sequence divergence, or a diffusion-like mutation process in a continuous or
discrete state space. In that setting, mutation really is a transition in a
state-space, and distance can matter very much.

This paper deliberately does not choose that structure. It keeps only the
coarsest information the conservation question needs: the frequency of each
label in each deme, and whether the labels are shared or unique across demes.
The formulas in Parts II–V then depend on **overlap, frequency weights, and
partitioning of diversity**, not on any assumed distance between alleles.

That is why a sequence-distance model can be useful in some applications and
yet leave no trace in the formulas here: it is a different model with a
different inferential target. In the Jost family, a mutation is not defined as
"a rotation in allele space"; it is simply a change in the frequency vector
that can create or destroy new labels. The paper's statistics are built to
track that population-level effect without introducing a metric that may not
exist, or may not be biologically stable, for the marker being used.

A useful way to phrase the distinction is this: in a full mutation model, the
allele itself is a state with internal structure, and one must model how that
state changes over time or how it relates to other states. In the Jost model,
by contrast, the allele is treated as a category label, and the entire state
for a locus is the list of category frequencies in a deme. The model says
nothing about how similar or distant those labels are; it is built only from
whether they are the same label, how often they appear, and how much of the
category distribution is shared versus unique to each deme. That choice is
not a simplification for convenience alone: it is what keeps the statistics
portable across markers whose underlying mutation processes and sequence
spaces are themselves very different.

**Where does the locus's length go?** It vanishes the moment alleles are
named, and returns in exactly one place. Once the interval is fixed, each
distinct sequence across it becomes a single categorical label, and every
statistic in Parts II–V sees nothing but the frequencies of those labels. A
one-character locus and a five-thousand-character locus that happen to
produce the same frequency vector give identical values of `H`,
G<sub>ST</sub>, `D`, E<sub>ST</sub>, and K<sub>ST</sub>.
Length sits upstream of the statistics: it is part of how alleles are
*individuated*, not part of any formula that consumes them.

Length re-enters only in the model half (Part VI), through the mutation
rate. A longer interval offers proportionally more sites at which a copying
error can land, so μ ≈ μ<sub>b</sub> · L for a per-base-pair rate
μ<sub>b</sub> and length `L` — which is the whole content of Eq. 5. The
consequence is sharp, and it is one more instance of the dissociation this
paper is built around: **locus length is invisible to G<sub>ST</sub> and
decisive for `D`.** Length acts only through μ, and μ cancels out
of the equilibrium G<sub>ST</sub> (Eq. 2) whenever μ \ll m, while
it sits in the denominator of the equilibrium `D` (Eq. 4).

Length has one further role, an assumption rather than a formula: it governs
whether **infinite alleles** is reasonable. A single-character locus admits
at most four alleles, so "every mutation yields a never-before-seen allele"
is plainly false; across thousands of characters the space of possible
sequences is astronomically large and the assumption is nearly exact. Hence
the paper's qualifier that infinite alleles is a valid approximation for
loci comprising many base pairs.

### Diploidy, homozygous, heterozygous

Most organisms of conservation interest are **diploid**: each individual
carries **two** copies of every locus, one from each parent.

- **Homozygous** at a locus: the two copies are the same allele.
- **Heterozygous** at a locus: the two copies are different alleles.

A group of `N` diploid individuals therefore contains `2N` gene copies at
any given locus. That factor of 2 is the only reason `2` and `4` keep
appearing in the formulas below; it carries no conceptual weight.

### Populations and demes

A **population** is all the individuals of one species in the area of
interest. A **deme** (or subpopulation) is a sub-group that mostly breeds
within itself — an island, a mountain valley, a forest fragment cut off by
a highway. "Mostly" does real work: demes usually still exchange a trickle
of individuals.

The paper uses `d` for the number of demes and `N` for individuals per
deme. Habitat destruction takes one large interbreeding population and
shatters it into small, isolated demes; each small deme then loses
variation quickly (see drift, below). Whether that loss is recoverable
depends on whether the fragments still hold *different* variants — exactly
the question these statistics answer.

### The only state variable that matters here

For a single locus in a single deme, the complete state used by every
formula in this document is a **probability vector**: the list of allele
frequencies.

| Deme | Allele frequencies |
|---|---|
| 1 | A: 0.70, B: 0.25, C: 0.05 |
| 2 | A: 0.10, D: 0.90 |

Entries are non-negative and sum to 1 within each deme. It is worth being
precise about **why** they sum to 1, because the reason is narrower than it
first appears. The normalization runs over **gene copies at one locus**,
not over the genome: each of the deme's `2N` copies carries exactly one
sequence across the interval, so the alleles are mutually exclusive and
exhaustive *per copy*, and their frequencies partition the copies. This
holds locus by locus and asks nothing whatever of any other locus — not
disjointness, not independence, not a carving of the genome into loci. Two
deliberately overlapping loci would each still have frequencies summing
to 1; you would be double-counting the shared characters, but neither
vector's normalization would break.

Independence across loci is a genuine requirement, but it buys something
else entirely: the right to **combine** loci — averaging G<sub>ST</sub>
across many markers to estimate `Nm`, taking harmonic means of `D` and
μ, and attaching confidence intervals (all in Part VI). For that
purpose non-overlap is necessary and nowhere near sufficient: two disjoint
loci a few thousand characters apart on the same chromosome are inherited
together and stay strongly correlated through linkage. Real independence
needs physical separation — different chromosomes, or enough distance for
recombination to break the association — and even then every locus in a
species shares one population history, which is part of why the paper
hedges its cross-locus averaging with "provided all other assumptions of
the island model are met."

The genome, in short, is not canonically carved into loci. Loci are
**chosen** by the analyst, and in practice chosen disjoint and far apart.
The frequency vector above requires neither property; the multi-locus
machinery requires both, and more.

That vector is the entire input — individual genotypes, pedigrees, and
geography are all discarded before any of these statistics is computed. That discarding is itself one
of the paper's arguments: F<sub>ST</sub> was *originally* defined in terms of
pedigrees, and G<sub>ST</sub> — its allele-frequency stand-in — is therefore
not quite measuring the same thing (Part III).

### The forces that move allele frequencies

Between one generation and the next, a deme's frequency vector changes
under up to four processes; only the first three appear in this paper's
models.

**Genetic drift** is finite sampling noise. The next generation's `2N`
gene copies are drawn from the current pool; whichever alleles happen to
get drawn more often become more common, for no reason at all. This is a
random walk with absorbing boundaries at 0 and 1. Two properties matter:

- Drift is **stronger in small demes** — the variance of the frequency
  change per generation scales roughly as `p(1-p)/(2N)`.
- Drift is **irreversible in aggregate**: it always erodes variation
  within a deme, because 0 and 1 are absorbing. Left alone, every deme
  eventually ends up with a single allele.

**Migration (gene flow)** replaces a fraction `m` of a deme's gene pool
each generation with arrivals from elsewhere. Migration pushes demes
*towards each other*, counteracting drift's tendency to make them differ.
Since `m` is a per-generation proportion, `Nm` is roughly the number of
migrant individuals per generation.

**Mutation** introduces brand-new alleles at rate μ per locus per
generation. Mutation is the only source of *new* variation — drift and
migration only redistribute what already exists. Typical rates span many
orders of magnitude, which is central to the paper's argument:

| Marker type | Rough mutation rate μ per generation |
|---|---|
| Single nucleotide site | 10<sup>-9</sup> – 10<sup>-8</sup> |
| Typical protein-coding gene | 10<sup>-6</sup> – 10<sup>-5</sup> |
| Microsatellite | 10<sup>-4</sup> – 10<sup>-3</sup> |

**Selection** improves the survival or reproduction of some alleles over
others. The models here assume **neutral** loci — no selection — which is
standard, and which the paper flags as a limitation when the loci that
actually matter to a decision are functional ones.

### Fixation

A deme is **fixed** at a locus when every gene copy in it is the same
allele — the frequency vector is a one-hot vector. All internal variation
at that locus is gone; only mutation or immigration can restore it.

This word is the hinge of the whole paper. Two facts to hold onto:

1. Fixation is a property of **one deme in isolation**. A deme is fixed or
   it is not; the question does not mention any other deme.
2. Whether two demes are fixed for the **same** allele or for **different**
   alleles is a completely separate question.

The older family of statistics answers question 1 (averaged across demes).
The newer family answers question 2. Conflating them is the error this
paper exists to correct.

### Markers: what actually gets measured

Researchers rarely sequence whole genomes for this purpose. They pick
**markers** — loci that are cheap to type — and hope those markers
represent the genome.

- **SNP** (single nucleotide polymorphism): one character position, so at
  most 4 alleles and in practice usually 2. Cheap, abundant, very low
  μ.
- **Microsatellite**: a short repeated motif whose repeat count varies,
  e.g. `CACACACA…`. Many alleles, very high μ. Historically the
  workhorse marker of this field.
- **MHC loci** and similar functional genes: not neutral, directly tied to
  fitness (disease resistance, in the MHC case). Expensive but, the paper
  argues, the ones that ought to drive decisions.

The gap between "the fast-mutating marker that was measured" and "the
functional locus that is actually of interest" becomes decisive in Part
VI: allelic differentiation depends strongly on μ, so a marker with a
very different mutation rate from the locus of real interest gives a very
different answer.

---

## Part II — Measuring diversity inside one group

### Expected heterozygosity

Given one deme's frequency vector p = (p<sub>1</sub>, p<sub>2</sub>, …, p<sub>k</sub>), the
standard diversity statistic is **expected heterozygosity**:

```math
H = 1 - \sum_i p_i^2
```

Interpretation: draw two gene copies at random, with replacement, from the
deme's pool. Σ_i p<sub>i</sub>^2 is the probability they are the **same**
allele, so `H` is the probability they are **different**. The name comes
from diploidy — under random mating, `H` is the fraction of individuals
expected to be heterozygous at that locus. Statisticians will recognize it
as the Gini–Simpson index; information theorists as one minus the
collision probability; ecologists call the same quantity Simpson
diversity.

Range and behavior:

- `H = 0` exactly when the deme is fixed.
- `H` increases with more alleles and with more even frequencies.
- `H < 1` always, and H → 1 only as the number of alleles
  → ∞.

That last point is the seed of the whole problem. `H` is bounded above
by 1 no matter how much variation exists, so once a deme has, say, 20
equally common alleles, `H = 0.95` and there is very little room left on
the scale — piling on another 20 alleles moves `H` only from `0.95` to
`0.975`. The measure saturates.

### Gene identity, the mirror image

Nei's **gene identity** is the complement:

```math
J = \sum_i p_i^2 = 1 - H
```

`J` is the probability two random gene copies match. Working in `J`
instead of `H` makes several later formulas much cleaner, and gives
Jost's `D` a form simple enough to read off directly (Part III).

### Why heterozygosity is a bad diversity, and how to fix it

`H` is a perfectly good *probability*, but a poor *diversity*, because
diversities get compared as ratios and `H` does not behave sensibly under
ratios. The fix, due to Kimura and Crow (1964), is the **effective number
of alleles**:

```math
{}^{H}D = \frac{1}{1 - H} = \frac{1}{J}
```

Read {}^{H}D as "the number of *equally common* alleles a deme would
need, to have this much heterozygosity." Check it: if a deme has `n`
equally common alleles, each at frequency `1/n`, then
J = n · (1/n)^2 = 1/n, so {}^{H}D = n exactly. The
transformation inverts perfectly on the uniform case, which is what makes
it interpretable.

Comparing the two scales on the same data makes the saturation problem
concrete:

| Deme composition | `H` | {}^{H}D (effective alleles) |
|---|---|---|
| 2 equally common alleles | 0.500 | 2.0 |
| 5 equally common alleles | 0.800 | 5.0 |
| 20 equally common alleles | 0.950 | 20.0 |
| 40 equally common alleles | 0.975 | 40.0 |

On the `H` scale, going from 20 alleles to 40 looks like a trivial change
(0.950 → 0.975). On the {}^{H}D scale, it is what it actually is: a
doubling. **Diversity doubled; heterozygosity moved by 2.5%.** Every "most
of the diversity is within populations" claim in the conservation
literature that was computed on the `H` scale is suspect for exactly
this reason — Part V makes that precise.

### Hill numbers and the order q

The effective number of alleles generalizes to a one-parameter family, the
**Hill numbers**, indexed by an order q \geq 0 that sets how much
weight rare alleles get:

```math
{}^{q}D = \left(\sum_i p_i^q\right)^{1/(1-q)} \quad (q \neq 1)
```

```math
{}^{1}D = \exp\left(-\sum_i p_i \ln p_i\right) \quad (\text{the } q \to 1 \text{ limit})
```

All of them return `n` for `n` equally common alleles; they differ only
in how they treat unevenness.

| `q` | Name | What it counts | Rare alleles |
|---|---|---|---|
| `0` | Allele richness | Every allele present, however rare | Full weight |
| `1` | Shannon diversity, \exp(entropy) | Alleles weighted by their frequency | Proportional weight |
| `2` | Effective number of alleles, `1/(1-H)` | Alleles weighted by squared frequency | Nearly ignored |

`q = 2` is the heterozygosity case above — check it by substituting
`q = 2` into the general formula:
(Σ_i p<sub>i</sub>^2)<sup>1/(1-2)</sup> = (Σ_i p<sub>i</sub>^2)<sup>-1</sup> = 1/J. This ladder
matters because the paper's three recommended differentiation measures are
exactly the `q = 2`, `q = 1`, and `q = 0` members of one family (Part
III). They are not three rival proposals — they are three settings of one
dial, and the paper recommends reporting several precisely because the
*disagreement between them* is informative (Part IV).

---

## Part III — Two groups or more: "differentiation" splits in two

### The two building blocks: H<sub>S</sub> and H<sub>T</sub>

With `d` demes, two heterozygosities get computed:

- H<sub>S</sub> — the mean **within-deme** heterozygosity: compute `H`
  separately for each deme, then average. "How much variation does a
  typical deme hold internally?"
- H<sub>T</sub> — the **total** heterozygosity: pool all demes into one combined
  frequency vector, then compute `H` once. "How much variation is there
  altogether?"

One detail changes answers: when comparing *relative* allele frequencies,
the paper gives **each deme equal statistical weight** — the pooled
frequency of an allele is the unweighted mean of its per-deme frequencies,
not a count-weighted mean. (For E<sub>ST</sub>, size weighting is available
and is in fact one of its selling points; see below.)

Necessarily H<sub>T</sub> \geq H<sub>S</sub>: pooling different demes can only add
variation. The gap H<sub>T</sub> - H<sub>S</sub> is the raw material both families of
statistics work with — and the two families normalize that same gap by
**different denominators**. That one choice of denominator is the whole
disagreement.

### Family 1: fixation measures

F<sub>ST</sub> (Wright, 1943, 1965) was originally defined in pedigree terms:
the probability that two homologous genes drawn at random from a
subpopulation both descend from a single ancestral gene *in that
subpopulation*. Notice what this definition mentions — ancestry within one
deme — and what it does not mention — any other deme, or any actual
allele identity.

G<sub>ST</sub> (Nei, 1973) is the multi-allele version expressed purely in
allele frequencies:

```math
G_{ST} = \frac{H_T - H_S}{H_T} = 1 - \frac{H_S}{H_T}
```

θ (Weir & Cockerham, 1984) is an unbiased *estimator* of
F<sub>ST</sub> that corrects for finite sample size.

Because G<sub>ST</sub> is a function only of allele frequencies, the paper
notes it is **not measuring exactly the same thing as Wright's original**
F<sub>ST</sub> — it is a frequency-based stand-in, used throughout the paper
for analytical simplicity, on the premise that F<sub>ST</sub> and θ
behave broadly similarly.

Denominator: H<sub>T</sub>.

Wright himself, quoted in the paper, was unambiguous about what this
family measures (Wright, 1978, p. 84):

> "…is thus not a measure of the degree of differentiation in the sense
> implied in the extreme case by the absence of any common allele. It
> measures differentiation within the total array in the sense of the
> extent to which the process of fixation has gone toward completion… it
> must again be borne in mind that it measures the degree of completion of
> the process of fixation, not absolute differentiation."

Wright had a good reason to want exactly that. Allelic differentiation
necessarily varies from locus to locus with the mutation rate. Wright
deliberately wanted a statistic sensitive **only to demographic
variables** — population size and migration rate — which affect every
locus equally. Nearness to fixation has that property; allelic
differentiation does not. The older family is not a failed attempt at the
newer one — it is a successful attempt at something else.

G<sub>ST</sub> is **undefined** when all demes are fixed for the same allele,
since then H<sub>T</sub> = 0 and the formula divides by zero — an awkwardness
that recurs repeatedly in the paper's examples.

### Family 2: allelic differentiation measures

This family targets Wright's "absolute differentiation" — differentiation
"in the sense implied in the extreme case by the absence of any common
allele." The design contract:

- equals **1** if and only if the demes share **no** alleles at all;
- equals **0** if and only if the demes are **identical** (same alleles,
  same frequencies).

**Jost's D**, the `q = 2` member:

```math
D = \left[\frac{H_T - H_S}{1 - H_S}\right]\cdot\frac{d}{d-1}
  = 1 - \frac{J_{\mathrm{between}}}{J_{\mathrm{within}}}
  = 1 - \exp(-\mathrm{NGD})
```

where `J` is Nei's gene identity and \mathrm{NGD} is Nei's genetic
distance (Nei, 1972). Denominator: 1 - H<sub>S</sub>, i.e. J_{\mathrm{within}}.

The middle form is the one that makes `D` obvious:

```math
J_{\mathrm{within}} = \text{mean over demes of } \sum_i p_i^2
```

(the chance two copies drawn from the *same* deme match)

```math
J_{\mathrm{between}} = \text{mean over deme pairs of } \sum_i p_{A,i}\,p_{B,i}
```

(the chance two copies drawn from *different* demes match)

In words: **draw one gene copy from each of two different demes, and ask
how often they match — relative to how often two copies from the same
deme match.** If demes share nothing, J_{\mathrm{between}} = 0 and
`D = 1`. If demes are identical, `J_{\mathrm{between}} =
J_{\mathrm{within}}` and `D = 0`. No saturation, no ceiling imported
from `H`. The `d/(d-1)` factor rescales so the maximum is exactly 1
rather than `(d-1)/d`.

**Entropy differentiation** E<sub>ST</sub>, the `q = 1` member:

```math
E_{ST} = \frac{E_T - E_S}{E_w}
```

E<sub>T</sub> is the Shannon entropy of the pooled demes, E<sub>S</sub> the mean
within-deme entropy weighted by deme size, and E<sub>w</sub> the entropy of the
*relative deme sizes* — E<sub>w</sub> = \ln d for **d** equally sized demes.

Two advantages the paper claims for E<sub>ST</sub>:

1. It handles **unequal deme sizes** natively, which **D** does not
   (**D** gives all demes equal statistical weight by construction).
2. It has **stronger monotonicity**: adding a new private allele to a
   deme *always* increases E<sub>ST</sub>. That is not true of **D** — because
   **D** weights alleles by squared frequency, adding a rare private
   allele slightly dilutes the squared frequencies of *common unshared*
   alleles, and **D** can tick **down**. The paper flags this as a genuine
   wart on **D** and a reason to prefer E<sub>ST</sub> in conservation
   settings, where discovering a new unique allele should never make a
   differentiation index fall.

**Allele-number differentiation** K<sub>ST</sub>, the `q = 0` member:

```math
K_{ST} = 1 - \frac{K_T/K_S - d}{1 - d}
```

K<sub>T</sub> is the total number of distinct alleles across all demes; K<sub>S</sub>
is the unweighted mean number of alleles per deme. Frequencies are ignored
entirely — only presence/absence counts. K<sub>ST</sub> has the **strongest**
monotonicity of the three. Read it, roughly, as: the fraction of a typical
deme's alleles that are unique to it.

### One formula that generates the whole family

All three measures come from a single expression (the one-complement of a
similarity measure from Jost, Chao & Chazdon, 2011):

```math
\mathrm{Differentiation}_q
  = 1 - \frac{({}^{q}D_S / {}^{q}D_T)^{q-1} - (1/d)^{q-1}}{1 - (1/d)^{q-1}}
```

{}^{q}D<sub>T</sub> and {}^{q}D<sub>S</sub> are the total and mean-within-deme Hill
numbers of order **q**. Setting the dial: `q = 2` gives Jost's **D**;
`q = 1`, taken as a limit, gives E<sub>ST</sub>; `q = 0` gives K<sub>ST</sub>.

Two notes on that formula worth keeping:

- For q ≠ 0, 1, demes **must** be given equal statistical weight if
  the goal is to compare *relative* allele frequencies between demes.
- {}^{q}D<sub>T</sub> / {}^{q}D<sub>S</sub> is itself the **between-group diversity**
  (beta diversity) of order **q**. Every measure in this family is that
  beta diversity, normalized onto `[0, 1]`. Nothing exotic is being
  introduced — the family is a rescaling of a quantity ecologists have
  used for decades.

### Side-by-side summary

| | Fixation family (G<sub>ST</sub>) | Allelic differentiation family (`D`) |
|---|---|---|
| Formula | (H<sub>T</sub> - H<sub>S</sub>)/H<sub>T</sub> | [(H<sub>T</sub>-H<sub>S</sub>)/(1-H<sub>S</sub>)]· d/(d-1) |
| Denominator | H<sub>T</sub> | 1 - H<sub>S</sub> |
| Question answered | Have demes lost internal variation? | Do demes hold different alleles? |
| `= 1` when | All demes fixed (same or different alleles) | Demes share no alleles |
| `= 0` when | Demes identical **or** all have high `H` | Demes identical, and only then |
| Undefined when | All demes fixed for the same allele | Never |
| Depends on μ? | Barely, when μ \ll m | Strongly, always |
| Equilibrium driver | `Nm` (absolute migrant count) | m/(μ(d-1)) |
| Comparable across loci? | Yes (that is its design goal) | Only among loci with similar μ |
| Comparable across species? | Not reliably (H<sub>T</sub> varies) | Yes |
| Right tool for | Estimating demography, gene flow | Deciding which demes to protect |

---

## Part IV — Worked examples

All values below were recomputed from the definitions (see Appendix B for
the one place a recomputation disagrees with the paper's printed value).

### Ten fixed demes, three ways

Three hypothetical species, each with `d = 10` demes. In every case **all
ten demes are fixed**, so `H = 0` in each one and H<sub>S</sub> = 0
throughout — which makes G<sub>ST</sub> = (H<sub>T</sub> - 0)/H<sub>T</sub> = 1 identically, no
matter how the fixed alleles are distributed.

**Nine demes fixed for allele A, one for allele B:**

```math
\begin{aligned}
p_A &= 0.9, \quad p_B = 0.1 \\
H_T &= 1 - (0.9^2 + 0.1^2) = 0.18 \\
G_{ST} &= 0.18/0.18 = 1.0 \\
D &= \frac{0.18}{1}\cdot\frac{10}{9} = 0.20
\end{aligned}
```

**Five demes fixed for A, five for B:**

```math
\begin{aligned}
p_A &= p_B = 0.5 \\
H_T &= 1 - (0.25 + 0.25) = 0.5 \\
G_{ST} &= 0.5/0.5 = 1.0 \\
D &= \frac{0.5}{1}\cdot\frac{10}{9} = 0.5556
\end{aligned}
```

**All ten demes fixed for a different allele:**

```math
\begin{aligned}
H_T &= 1 - 10\cdot(0.1^2) = 0.9 \\
G_{ST} &= 0.9/0.9 = 1.0 \\
D &= \frac{0.9}{1}\cdot\frac{10}{9} = 1.0
\end{aligned}
```

| Scenario | H<sub>S</sub> | H<sub>T</sub> | G<sub>ST</sub> | `D` |
|---|---|---|---|---|
| 9 + 1 | 0.00 | 0.18 | **1.00** | **0.20** |
| 5 + 5 | 0.00 | 0.50 | **1.00** | **0.5556** |
| All different | 0.00 | 0.90 | **1.00** | **1.00** |

G<sub>ST</sub> is 1.00 in all three cases and cannot distinguish them — which
is correct, since all three are fully fixed. But the conservation
implications differ completely. A manager acting on G<sub>ST</sub> alone would
conclude every deme must be protected in every case. In fact, at this
locus, the first scenario needs only **two demes** to capture all the
variation there is, while the third needs **all ten**; every one is
irreplaceable.

**D** distinguishes them (0.20, 0.56, 1.00), and it has a direct,
intuitive reading. Write out all 10·9/2 = 45 pairs of demes; for
each pair, score 0 if the two demes are fixed for the same allele and 1 if
fixed for different alleles. In the first scenario, the one odd deme
differs from each of the other nine (nine differing pairs), and the
remaining 36 pairs are between identical demes:

```math
\bar{d}_{\text{pairs}} = \frac{9\cdot 1 + 36\cdot 0}{45} = 0.20
```

which matches **D** exactly. The same reading works for the other two
scenarios (`25/45 = 0.5556` and `45/45 = 1.0`). So **D is the mean
pairwise allelic differentiation over deme pairs** — a property worth
remembering, since it means the same **D** value carries the same meaning
whether it came from two demes or two hundred.

### Three species, GST near zero in all of them

The complementary embarrassment. Three species, each with `d = 2` demes,
all alleles equally frequent within a deme. Here G<sub>ST</sub> is near
**zero** in all three, while **D** sweeps its entire possible range.

**Species A — both demes share the same 20 alleles at the same
frequencies:**

```math
\begin{aligned}
H_S = H_T &= 0.95 \\
G_{ST} &= 0.00 \\
D &= 0.00
\end{aligned}
```

**Species B — each deme has 6 alleles, 3 shared and 3 private (9 distinct
total):**

```math
\begin{aligned}
H_S &= 0.8333 \\
H_T &= 1 - \bigl[3\cdot(1/6)^2 + 6\cdot(1/12)^2\bigr] = 0.875 \\
G_{ST} &= \frac{0.875 - 0.8333}{0.875} = 0.0476 \\
D &= \frac{0.041667}{0.166667}\cdot 2 = 0.50
\end{aligned}
```

**Species C — 20 equally common alleles per deme, none shared:**

```math
\begin{aligned}
H_S &= 0.95 \\
H_T &= 1 - 40\cdot(0.025^2) = 0.975 \\
G_{ST} &= \frac{0.975 - 0.95}{0.975} = 0.0256 \\
D &= \frac{0.025}{0.05}\cdot 2 = 1.00
\end{aligned}
```

| Species | Shared alleles | H<sub>S</sub> | H<sub>T</sub> | G<sub>ST</sub> | **D** |
|---|---|---|---|---|---|
| A | all 20 | 0.950 | 0.950 | **0.000** | **0.00** |
| B | 3 of 6 | 0.833 | 0.875 | **0.0476** | **0.50** |
| C | **none** | 0.950 | 0.975 | **0.0256** | **1.00** |

Read the last two rows against each other: **species C shares no alleles
at all and yet has a *lower* G<sub>ST</sub> than species B, which shares half
of them.** A manager ranking by G<sub>ST</sub> would rank species B as the
more structured one and, under scarce resources, would preserve both
demes of species B while letting one deme of species C go — precisely
backwards. Losing a deme of species C destroys half of that species'
allelic diversity irrecoverably; losing a deme of species B costs only its
3 private alleles.

### The "98% of diversity is within demes" trap

A population with two equally large demes, each holding many low-frequency
alleles, almost none shared. The paper reports H<sub>T</sub> = 0.97,
H<sub>S</sub> = 0.95, with effective numbers 38.8 and 20.4. Back-solving from
the effective numbers gives the more precise H<sub>T</sub> = 0.9742,
H<sub>S</sub> = 0.9510; the calculation below uses those.

**The standard — and wrong — analysis:**

```math
\text{"within-group share"} = H_S/H_T = 0.95/0.97 \approx 98\%
```

Conclusion typically drawn: 98% of the diversity is within demes,
differentiation is negligible, protecting one deme saves nearly
everything. Every step of this is wrong, and yet it is a completely
standard paragraph in the literature.

**The correct analysis** — convert to effective numbers of alleles first:

```math
\begin{aligned}
{}^{H}D_S &= \frac{1}{1 - 0.9510} = 20.4 \text{ effective alleles} \\
{}^{H}D_T &= \frac{1}{1 - 0.9742} = 38.8 \text{ effective alleles} \\
\beta &= {}^{H}D_T / {}^{H}D_S = 1.902
\end{aligned}
```

Pooling the two demes **nearly doubles** the diversity. With only two
demes, a ratio approaching its maximum of 2 means they have almost nothing
in common:

```math
D = \left(1 - \frac{1}{1.902}\right)\cdot\frac{2}{1} = 0.4742\cdot 2 = 0.948 \approx 0.95
```

| Statistic | Value | Story it tells |
|---|---|---|
| G<sub>ST</sub> | 0.024 | Demes are far from fixation — **true, and irrelevant** |
| "98% within-group" | — | An artifact of **H**'s ceiling — **meaningless** |
| {}^{H}D<sub>T</sub> / {}^{H}D<sub>S</sub> | 1.90 of a max 2.00 | Demes are nearly disjoint |
| **D** | **0.95** | Demes are 95% allelically differentiated |
| E<sub>ST</sub> | 0.90 | Same conclusion, frequency-weighted |
| K<sub>ST</sub> | 0.77 | 77% of each deme's alleles are unique to it |

Both demes must be protected — the G<sub>ST</sub>-driven analysis says the
opposite. The paper also flags that the ordering `D (0.95) > E<sub>ST</sub>
(0.90) > K<sub>ST</sub> (0.77)` is itself informative: **D** weights by squared
frequency (dominated by the most common alleles), E<sub>ST</sub> weights by
plain frequency (the average allele), and K<sub>ST</sub> ignores frequency
entirely (every allele counted equally). That ordering says the common
alleles are more differentiated than the average ones, which are in turn
more differentiated than the full allele list — the alleles the demes do
share are all at very low frequency, a conclusion no single index could
have delivered on its own.

### Where D and K<sub>ST</sub> disagree completely

Two demes sharing **all 11** of their alleles, but at nearly reversed
frequencies (allele 1 at 0.95 in deme 1 and 0.005 in deme 2; allele 11 the
mirror image; nine more alleles at 0.005 each in both demes):

```math
\begin{aligned}
H_S &= 1 - \bigl[0.95^2 + 10\cdot(0.005^2)\bigr] = 0.09725 \\
H_T &= 1 - \bigl[2\cdot(0.4775^2) + 9\cdot(0.005^2)\bigr] = 0.5438 \\
G_{ST} &= \frac{0.5438 - 0.09725}{0.5438} = 0.8211 \\
D &= \frac{0.44651}{0.90275}\cdot 2 = 0.99
\end{aligned}
```

```math
K_{ST} = 1 - \frac{11/11 - 2}{1 - 2} = 1 - 1 = 0
```

| Measure | Value | What it is reporting |
|---|---|---|
| **D** | **0.99** | The *common* alleles are not shared at all |
| K<sub>ST</sub> | **0.00** | Every allele is present in both demes; none is unique |

Neither is wrong; they answer different questions, and a manager needs
both answers. If the goal is preserving *allelic presence*, one deme
suffices — every allele exists in both. If the goal is preserving the
*genetic character* of the populations — which alleles are actually
common, and therefore which ones drive phenotype — the demes are nearly
disjoint and both must be kept.

### The dynamic example

A deliberately extreme time-series case, meant to make the point visible;
a realistic case shows the same pattern more mildly. An initially
continuous population suffers a severe bottleneck that splits it into 100
tiny demes with zero migration between them; the demes then recover to
`N = 10{,}000` individuals each, migration still zero. At the start, 99
demes are fixed for one allele and one for a different allele (that one
odd deme exists purely to keep G<sub>ST</sub> from dividing by zero). The
locus is neutral with a high mutation rate, μ = 0.001 per
generation, under the infinite-alleles model — every mutation produces an
allele never seen before.

With no migration, every new allele stays where it arose. The end state is
unambiguous: every deme eventually holds only private alleles, with high
internal diversity. Allelic differentiation runs from almost nothing (99
of 100 demes identical) to total.

| | at `t = 0` | at equilibrium | Direction |
|---|---|---|---|
| Truth (allelic differentiation) | near zero | maximal | ↑ |
| **D** | near 0 | → 1.00 | ↑ **correct** |
| G<sub>ST</sub> | 1.00 | ≈ 0.02 | ↓ **backwards** |

```math
D \approx \frac{1}{1 + m/[\mu(d-1)]} = \frac{1}{1+0} = 1.00 \text{ exactly}
```

```math
G_{ST} \approx \frac{1}{(d/(d-1))^2\cdot 4Nm + (d/(d-1))\cdot 4N\mu + 1}
       = \frac{1}{0 + 1.0101\cdot 40 + 1} = \frac{1}{41.4} = 0.0242
```

G<sub>ST</sub> **falls monotonically from unity to near zero over exactly the
interval in which the demes become completely differentiated** — the
opposite of what a differentiation measure should do. As a measure of
nearness to fixation it behaves perfectly: the demes started fixed and
ended diverse. (Hedrick's G\'_{ST}, a well-known rescaling of G<sub>ST</sub>
by its maximum attainable value, patches this only at the high-diversity
end; the paper notes it does not fix the equally serious failure at low
diversity, which this example spans.)

---

## Part V — Why the classical reasoning is wrong

The "98% within demes" reasoning above is standard, and the paper states
flatly that every step leading to it is mathematically and biologically
incorrect. Here are the steps, separately.

### The ceiling argument

A short proof with large consequences. Since H<sub>T</sub> \leq 1 always,

```math
G_{ST} = 1 - \frac{H_S}{H_T} \leq 1 - \frac{H_{S}}{1} = 1 - H_{S}
```

So if within-deme heterozygosity is H<sub>S</sub> = 0.95, then G<sub>ST</sub> cannot
exceed 0.05 — **no matter what**. The demes could share no alleles. They
could be different species, incapable of interbreeding at all. G<sub>ST</sub>
still cannot exceed 0.05.

This makes nonsense of the common habit of reading G<sub>ST</sub> against a
fixed verbal scale ("below 0.05 is negligible structure, above 0.25 is
strong"). With high-diversity markers such as microsatellites — chosen
*because* they are informative — the entire upper range of G<sub>ST</sub> is
mathematically unreachable. At the descriptive level this behavior is, the
paper stresses, **correct**: a population with low 1 - H<sub>S</sub> really is
far from fixation, and G<sub>ST</sub> is a nearness-to-fixation measure. The
defect lies entirely in the interpretation people give it.

### Heterozygosity is subadditive

The classical partition assumes H<sub>T</sub> = H<sub>S</sub> + H_{\mathrm{between}}, so
that H<sub>T</sub> - H<sub>S</sub> is "the between-group component" and H<sub>S</sub>/H<sub>T</sub> is
"the fraction of diversity found within demes." Shannon entropy really is
additive this way — **heterozygosity is not**; it is subadditive. The
correct partition into independent within- and between-group components
(Jost, 2007), with equal deme weights, is:

```math
H_{T} = H_{S} + H_{ST} - H_{S}\cdot H_{ST}
```

Solving for the between-group component gives:

```math
H_{ST} = \frac{H_{T} - H_{S}}{1 - H_{S}}
```

which is exactly the first bracket of **D**. So: **D is nothing more
than the correctly-partitioned between-group component of heterozygosity,
normalized onto `[0, 1]`.** It is not a rival index invented to compete
with G<sub>ST</sub> — it is what falls out of doing the partition correctly
instead of incorrectly. (The paper notes the same `D` also falls out of
partitioning *effective number of alleles*, where the partition is
multiplicative — two different correct routes, one destination.)

### The replication principle

The deeper requirement, and the reason effective numbers are mandatory
before taking ratios: if a diversity measure is to be compared as a
**ratio**, it must be **linear under pooling of equally large, equally
diverse, completely distinct groups**. Pool two groups that share nothing
and are equally diverse, and the measure must **double**. Testing both
candidates on species C from above (20 private alleles per deme, two
demes, nothing shared):

```math
\begin{aligned}
H\text{ scale: } &H_{S} = 0.950 \to H_{T} = 0.975 \quad (\times 1.026) \quad \text{fails} \\
{}^{H}D\text{ scale: } &{}^{H}D_{S} = 20.0 \to {}^{H}D_{T} = 40.0 \quad (\times 2.000) \quad \text{passes}
\end{aligned}
```

Heterozygosity moved by 2.6% where the true diversity doubled. Any ratio
taken on the **H** scale is therefore meaningless, and "98% of diversity
is within demes" is an artifact of the scale rather than a fact about the
population. This replication principle is the axiom underpinning Jost's
whole research program across ecology, genetics, and phylogenetics.

### D also has a ceiling, and that one is honest

For balance: **D** has a range restriction too. **When the number of
alleles is fewer than the number of demes, D cannot reach 1.00.** But
this is not the same kind of defect. With 3 alleles and 10 demes, the
pigeonhole principle guarantees some demes share an allele — so they are
*not* completely differentiated, and a measure of actual allelic
differentiation *should* report less than 1. The constraint is a true
fact about the data. G<sub>ST</sub>'s ceiling is different in kind: it depends
on H<sub>S</sub>, a property of within-deme diversity that has **nothing to do
with** how much the demes actually share.

---

## Part VI — What controls what, if the model runs to equilibrium

Parts I–V were purely descriptive — statistics computed on a snapshot, no
model, no assumptions. This part changes register: it asks what values
these statistics *settle to* under a specific generative model, which is
what lets researchers try to run the inference backwards, from statistic
to demography.

### The finite island model, briefly

The setting for both formulas below (fully covered in a
[companion document](finite-island-model-introduction.md)):
**d** demes of **N** diploid individuals each; discrete, non-overlapping
generations; each generation, a fraction **m** of each deme's gene pool is
replaced by migrants drawn from the other demes; mutation creates novel
alleles at rate μ under the infinite-alleles assumption (every
mutation produces an allele never seen before — a good approximation for
loci spanning many base pairs); reproduction resamples **2N** copies from
the local post-migration pool, which is where drift enters.

"Finite" is not decoration. Wright's original *infinite* island model
draws migrants from an infinitely large external reservoir that never
itself drifts, which is what makes the classical closed-form F<sub>ST</sub>
equilibrium easy to write down. The finite version removes that fiction:
there are only **d** demes and no reservoir, so migrants come from the
actual, currently-drifting other demes, and the whole metapopulation is a
closed finite system that drifts as a whole. The system settles into a
**stochastic equilibrium**: individual allele frequencies never stop
moving, but the *distribution* of summary statistics stabilizes.

Note the phrase "**d** demes of **N** diploid individuals **each**." Every
deme is the same size, and that single word is carrying more weight than
its length suggests. It is revisited below, once both equilibrium formulas
are on the table.

### Equilibrium GST: controlled by Nm

```math
G_{ST} \approx \frac{1}{(d/(d-1))^2\cdot 4Nm + (d/(d-1))\cdot 4N\mu + 1}
```

The `(d/(d-1))²` factor multiplying the migration term is the
finite-number-of-demes correction from Crow & Aoki (1984) — see
Appendix D — confirmed directly against their Eq. 7/Eq. 8
(`G_ST ≈ 1/(4Nmα+1)`, `α = [n/(n-1)]²`, their `n` this document's `d`),
not to this paper or to Wright's own original (infinite-island)
derivation; it vanishes as `d → ∞`, recovering Wright's classical
`1/(4Nm+1)`, exactly as Crow & Aoki's own paper notes. One boundary of
that attribution worth being precise about: Crow & Aoki's own Eq. 7
carries **no mutation term at all** — dropping it entirely is the
"pleasing result" their own paper highlights, valid under their stated
`μ ≪ m, 1/N ≪ 1` approximation. The `(d/(d-1))·4Nμ` mutation term
above is this project's own retention of a term Crow & Aoki chose to
drop, not something their paper itself provides; Crow & Aoki's own
references (their eq. 6, with mutation retained) instead credit
Takahata (1983) for the general finite-`K`-allele solution and Nei's
1975 textbook for an exact infinite-allele solution — see Appendix D.

When m \gg μ — the usual case for slowly mutating loci — the
4Nμ term is negligible and this collapses to a function of **Nm**
and **d** alone:

```math
G_{ST} \approx \frac{1}{(d/(d-1))^2\cdot 4Nm + 1}
```

Two consequences follow. First, G<sub>ST</sub> depends on the **absolute
number of migrants per generation**, `Nm`, not on **m** and **N**
separately — ten migrants into a deme of 100 and ten into a deme of 10,000
give the same expected G<sub>ST</sub>. Second, because μ has dropped
out, the expected G<sub>ST</sub> is **the same for all loci** with
μ \ll m, so estimates from many loci can legitimately be averaged
and used to estimate `Nm` — provided every island-model assumption
holds. This is Wright's design goal achieved: a statistic sensitive only
to demography, comparable across loci.

### Equilibrium D: controlled by the mutation ratio

```math
D \approx \frac{1}{1 + m/[\mu(d-1)]}
```

Note what is present and what is absent: **N does not appear at all.**
The controlling quantity is m/(μ(d-1)) — a ratio of migration rate to
mutation rate, scaled by deme count. The same quantity controls Nei's
genetic distance, and `D` is a simple monotonic function of it (Jost,
2009). Sanity checks: with no migration (`m = 0`), `D = 1`, and demes
diverge completely; with m \gg μ(d-1), D → 0 and migration
homogenizes them.

### Why this kills the standard inference

| | Controlled by | Contains `N`? | Contains μ? |
|---|---|---|---|
| G<sub>ST</sub> (nearness to fixation) | `Nm` | yes | barely, when μ \ll m |
| `D` (allelic differentiation) | m/(μ(d-1)) | **no** | **strongly** |

A widely repeated rule of thumb claims: *"when mutation rate is low, the
absolute number of migrants `Nm` determines the genetic differentiation
between demes."* It does not. `Nm` determines **nearness to fixation**.
Allelic differentiation is governed by an entirely different combination,
one in which **N** plays no part and μ plays a decisive one. This
matters practically, because the "one migrant per generation is enough"
rule of thumb that circulates in conservation genetics rests on the
refuted version.

Since **D**'s equilibrium depends strongly on μ, and μ spans
five orders of magnitude across marker types, the *same* pair of demes
shows different **D** at a microsatellite than at a coding gene. The paper
insists this is "a real effect, not a flaw in the differentiation
measures" — the demes really are more differentiated at the fast-mutating
locus. If the absolute magnitude of differentiation at a specific locus is
what matters, measure **D** at that locus, or at loci with comparable
μ; fast-mutating markers remain valid for *ranking* deme pairs, just
not for reporting an absolute value at a locus of different interest.

### The equal-deme-size assumption

Both equilibrium formulas assume every deme holds exactly **N**
individuals. This is one of the model's largest simplifications, and it is
worth separating from a different equal-size question that Part III has
already settled, because the two are easy to run together.

- **A weighting choice** (Part III): given real data from demes of
  differing size, do you weight each deme equally or in proportion to its
  size? **D** weights every deme equally by construction; E<sub>ST</sub>
  weights within-deme entropy by deme size, which is precisely why the
  paper recommends it "when relative sizes of the demes differ." This is a
  decision you make, and it is available to you regardless of any model.
- **A model assumption** (here): the *generative process* that produced
  the data had equal demes. Nothing you choose at analysis time repairs a
  mismatch here. This is what Eq. 2 and Eq. 4 rest on.

**What goes wrong.** Drift acts at rate `1/(2N)`, so unequal demes drift
at unequal rates: small demes lose variation and reach fixation far faster
than large ones, and H<sub>S</sub> becomes an average over demes with genuinely
different equilibrium heterozygosities rather than **d** draws from one
distribution. The standard result is that a subdivided population behaves
like one whose size is closer to the **harmonic** mean of the deme sizes
than the arithmetic mean — and the harmonic mean is dominated by the
*smallest* demes.

The gap is not subtle. Take `d = 10`, one deme of 10,000 and nine of 100:

```math
\bar{N}_{\text{arithmetic}} = \frac{10000 + 9(100)}{10} = 1090
\qquad
\bar{N}_{\text{harmonic}} = \frac{10}{\tfrac{1}{10000} + \tfrac{9}{100}} \approx 111
```

A factor of 9.8. Feeding each into Eq. 2 with `m = 0.01` and negligible
μ:

| `N` used | `4Nm` | Eq. 2 G<sub>ST</sub> |
|---|---|---|
| 1090 (arithmetic mean) | 43.6 | 0.018 |
| 111 (harmonic mean) | 4.44 | **0.153** |

Plugging in the *average* deme size understates G<sub>ST</sub> by roughly
**8.5-fold**. Run backwards — the direction people actually use it — the
same error inflates the estimate of `Nm` by about the same factor, and
"how many migrants per generation are these populations exchanging?" is
exactly the question `Nm` gets recruited to answer.

**`Nm` also stops being one number.** With unequal demes, a common
exchange *rate* of 1% moves 100 individuals out of the large deme and 1 out
of a small one; a common *count* of ten migrants means 0.1% for the large
deme and 10% for a small one. Whether `m` or `Nm` is the quantity held
constant across demes is a modeling decision with no default, and "the
absolute number of migrants per generation" quietly presumes it has been
made.

**`D` fares better, and for a structural reason.** Look again at Eq. 4:
`N` does not appear in it at all. Allelic differentiation at equilibrium
is set by the migration–mutation balance governing whether two lineages
drawn from *different* demes are identical, and the deme-size dependence
that dominates within-deme identity largely cancels in the ratio
J<sub>between</sub>/J<sub>within</sub> on which `D` is built. For the numbers above,
Eq. 4 returns D ≈ 0.083 whichever deme size you use, because it
never asks. This is the same robustness the paper is pointing at when it
calls `D` "more stable against variation in deme size."

Do not over-read it as immunity, though. Eq. 4 still assumes demes are
*exchangeable*, sharing one migration rate `m` and one mutation rate
μ; unequal sizes in real metapopulations usually come with unequal
migration rates, and then `m` is not a scalar and the derivation's
premise is gone. The ≈ is doing real work.

**What survives.** The reassuring structural point is that this assumption
is quarantined. Everything in Parts II–V — the ceiling argument
G<sub>ST</sub> \le 1 - H<sub>S</sub>, the subadditivity of heterozygosity, the
replication principle, and every worked example in Part IV — is computed
from allele frequency vectors alone and assumes **nothing whatever** about
deme sizes, migration, mutation, or equilibrium. The paper's central thesis
(that the two families measure different things, and that swapping one for
the other reverses conservation advice) is untouched. Equal-`N` threatens
only the Part VI inference machinery — which, as the next section records,
the paper already tells you not to lean on.

**Where the constraint gets relaxed.** The **structured coalescent** is the
standard modern framework: it accommodates arbitrary deme sizes and an
arbitrary migration matrix rather than one scalar `m`, at the cost of
closed forms. For the effective size of a subdivided population with
unequal demes, see Whitlock MC & Barton NH (1997), "The effective size of a
subdivided population," *Genetics* 146:427–441. Relaxations of the
migration *topology* rather than deme size — stepping-stone and
continent-island models — are a separate axis, and equally absent from
Eq. 2 and Eq. 4.

### The equilibrium caveat that undercuts all of it

Having derived these equilibrium relationships, the paper immediately
warns against leaning on them — this is not a hedge but a load-bearing
part of the argument. **Neither G<sub>ST</sub> nor `D` should be used to
estimate current migration**, common practice in conservation genetics
until recently. Present-day values reflect an accumulation of historic
*and* recent migration and population sizes; populations that are now
completely isolated can still show a low G<sub>ST</sub> simply because
isolation has not yet had time to leave its mark. Threatened populations
are, by definition, not at equilibrium — they are of concern precisely
*because* their deme numbers, sizes, and migration rates have recently
changed, and bottlenecks and fragmentation do not have to be recent to
still be distorting these statistics. `D` is somewhat more robust here,
since it is independent of within-group diversity and therefore more
stable against variation in deme size, but past variation in migration
rate still leaves a mark on it too.

The paper's own recommendation follows from this: work at the
**descriptive** level, measuring present-day magnitudes of `D`,
E<sub>ST</sub>, and K<sub>ST</sub> at the loci of actual interest, rather than
making inferences based on unverifiable equilibrium assumptions.

---

## Part VII — The two-allele special case

SNPs usually have only two alleles, and modern studies use them by the
thousand — a low-diversity regime worth treating separately.

**Many demes, two alleles.** The same ambiguity as the infinite-allele
case: with ten demes, nine fixed for one base and one for the other,
G<sub>ST</sub> = 1.00 while `D = 0.20` — numerically identical to the first
worked example above. Split five-five and G<sub>ST</sub> is still `1.00`
(all demes remain fixed), while `D = 0.5556`. Note that with two alleles
and ten demes, `D` **cannot** reach 1.00 — and, per the ceiling
discussion above, that is the honest answer: with fewer alleles than
demes, some demes necessarily share.

**Two demes, two alleles — the measures converge.** This is the case
where the distinction largely evaporates, and the paper says so plainly.
With only two alleles, G<sub>ST</sub>'s dependence on within-group
heterozygosity stops mattering much:

| Configuration | G<sub>ST</sub> | `D` |
|---|---|---|
| Both demes fixed, different alleles | 1.0000 | 1.0000 |
| Identical allele frequencies (not both fixed on the same allele) | 0 | 0 |
| One deme 50/50, other fixed | 0.3333 | 0.3333 |
| Both demes fixed for the **same** allele | undefined | 0 |

They differ in other configurations, but broadly both families give useful
answers for pairwise SNP analysis. `D` retains one edge: it is never
undefined. For **multi-deme** SNP analyses, or for any more polymorphic
locus, the two families diverge and the choice matters a great deal —
since real studies routinely do global multi-population analyses, this
narrow area of agreement covers less ground than it first appears to.
Where the goal is describing differentiation at multiple nested levels
(individuals within demes within regions), the paper points instead to
entropy-based measures, since entropy decomposes cleanly across
hierarchical levels.

---

## Part VIII — What the paper recommends

1. **Report both families, and label them by what they measure.**
   Interpret G<sub>ST</sub>, F<sub>ST</sub>, and θ as **nearness to
   fixation** (as Wright himself did), and `D`, E<sub>ST</sub>, K<sub>ST</sub>
   as **allelic differentiation**. They are complementary, not competing.
2. **`D` is not an estimator of F<sub>ST</sub> or G<sub>ST</sub>.** Treating it
   as a "corrected G<sub>ST</sub>" is a category error the paper names
   explicitly.
3. **For conservation questions about which demes to protect, use
   allelic differentiation measures.** In most threatened-species cases,
   this is the information that actually bears on the decision.
4. **Report the profile across `q`, not one index.** `D` (`q=2`),
   E<sub>ST</sub> (`q=1`), and K<sub>ST</sub> (`q=0`) weight allele
   frequencies differently, and their *disagreement* diagnoses the
   frequency structure of the data (see the worked examples above). `D`
   has the simplest connection to genetic models and is easiest to
   estimate reliably from small samples; E<sub>ST</sub> has the most robust
   monotonicity and partitioning properties, handles unequal deme sizes,
   and suits hierarchical analysis; K<sub>ST</sub> has the strongest
   monotonicity of all and answers "how many alleles are unique to this
   deme."
5. **Measure at the loci that matter.** For absolute magnitudes, use the
   functional loci of interest (e.g. MHC) or loci with similar mutation
   rates. Fast-mutating markers are valid for *ranking* deme pairs, not
   for absolute values; run both neutral and putatively adaptive markers
   where possible.
6. **Interpret magnitudes and confidence intervals, not p-values.**
   Statistical significance against an always-false null model is not the
   question; effect size is. Relatedly, H<sub>T</sub> varies across pairwise
   comparisons, so pairwise G<sub>ST</sub> values are not truly comparable to
   each other, whereas the same value of `D` means the same degree of
   allelic differentiation across deme pairs and even across species.
7. **Do not use either family to estimate current migration** (Part VI).
8. **Convert to effective numbers before any ratio comparison.**
   Diversity claims of the form "X% of diversity is within demes" must be
   computed on Hill numbers, never on raw heterozygosity.
9. **An acknowledged open problem.** The paper is explicit that "maximize
   genetic diversity" is not yet a well-posed conservation goal, because
   the *unit* of diversity is unsettled — SNPs, functional alleles, allele
   pairs, or whole genotypes? Preserving genotypes has real justification
   (crop collections do exactly this, to maintain phenotypes), but the
   number of multi-locus combinations explodes beyond any possibility of
   preserving them all. Left as future work.

---

## Part IX — Misconceptions the paper is correcting

| Common claim | Status | Where |
|---|---|---|
| "F<sub>ST</sub>/G<sub>ST</sub> measures how different populations are." | **False.** It measures nearness to fixation. | Part III |
| "`D` is a corrected or standardized F<sub>ST</sub>." | **False.** Different quantity, different question. | Part III, VIII |
| "High G<sub>ST</sub> means the demes hold different alleles." | **False.** G<sub>ST</sub>=1 when 9 of 10 demes are identical. | Part IV |
| "Low G<sub>ST</sub> means the demes are genetically similar." | **False.** Species C shares zero alleles at G<sub>ST</sub>=0.026. | Part IV |
| "G<sub>ST</sub> below 0.05 means negligible structure." | **Unsound.** G<sub>ST</sub>\le 1-H<sub>S</sub>, so with H<sub>S</sub>=0.95 it can never exceed 0.05. | Part V |
| "H<sub>T</sub>-H<sub>S</sub> is the between-group diversity." | **False.** `H` is subadditive; the correct form is H<sub>T</sub>=H<sub>S</sub>+H<sub>ST</sub>-H<sub>S</sub> H<sub>ST</sub>. | Part V |
| "98% of diversity is within demes, so protect one." | **False.** Artifact of the `H` scale; on effective alleles the demes are nearly disjoint. | Part IV, V |
| "`Nm` determines allelic differentiation when μ is low." | **False.** `Nm` determines nearness to fixation; m/(μ(d-1)) determines differentiation. | Part VI |
| "`D` varies across loci, so it is unreliable." | **False.** That variation is a real biological effect of differing μ. | Part VI |
| "Hedrick's G'_{ST} fixes G<sub>ST</sub>." | **Partial at best.** Addresses only the high-heterozygosity end, not the low. | Part IV |
| "G<sub>ST</sub> and `D` can be used to estimate current migration." | **False.** Both integrate historical demography. | Part VI |
| "These disagreements are estimation artifacts." | **False.** They hold for exact population values, with no sampling involved. | Part III |

---

## Part X — Using these measures outside population genetics

Nothing in Parts II–V is genetic. Those measures are functions of one
abstract object: **a set of groups, each carrying a frequency distribution
over categorical labels.** The genetics is packaging — the direction of
travel was, in fact, originally reversed: `D` came *into* genetics from
ecology, where the same formulas describe species in communities rather
than alleles in demes.

The substitution is mechanical:

| In this document | In the general case |
|---|---|
| Allele | Category / label / state |
| Locus | The variable being tabulated |
| Deme | Group, site, sample, class |
| Allele frequency vector | Composition of one group |
| H<sub>S</sub>, H<sub>T</sub> | Mean within-group and pooled diversity |
| `D`, E<sub>ST</sub>, K<sub>ST</sub> | How little the groups' repertoires overlap |

### What carries over

**The effective-numbers rule, without qualification.** Any claim of the
form "X% of the variation is within groups," computed on a raw index
bounded at 1 (Simpson, Gini–Simpson, plain heterozygosity), is
untrustworthy for the same reason as in Part V. Convert to Hill numbers
before taking any ratio — this is the single most portable result in the
paper.

**The saturation trap.** A variable with many categories behaves exactly
like a high-heterozygosity locus: the index saturates near its ceiling and
stops discriminating. Two groups with completely disjoint repertoires can
look nearly identical on a saturating index. Any procedure that *ranks
variables* by a saturating diversity index will systematically undervalue
the high-cardinality variables — often the most informative ones.

**The `q`-profile as a diagnostic.** Computing overlap at `q = 0`,
`1`, and `2` and reading the *ordering* transfers cleanly.
K<sub>ST</sub> \ll D says the groups share their rare categories but differ
in their typical ones; K<sub>ST</sub> \gg D says the reverse. Neither fact is
visible from a single number.

**Name the quantity by what it measures.** The paper's central practical
lesson is that a naming collision — two inequivalent quantities both
called "differentiation" — caused decades of wrong conclusions. The same
hazard attaches to any function called `distance`, `similarity`, or
`difference` without a statement of which of several inequivalent things
it computes.

**The relation to Jaccard and Sørensen.** K<sub>ST</sub> is the one-complement
of the multiple-community Sørensen index, and the general Eq. 6 formula
above is the one-complement of a general similarity measure (Jost, Chao &
Chazdon, 2011) — the familiar presence/absence similarity coefficients are
the `q = 0` end of this same family, and the general formula is the
generalization that adds frequency weighting to them.

### What does not carry over

**All of Part VI.** Those formulas are properties of Wright's finite
island model — mutation, migration, drift, discrete generations. Absent
that generative process, there is no μ, `m`, `N`, or
equilibrium, and none of those formulas means anything.

**G<sub>ST</sub> and the whole fixation family.** "Nearness to fixation" is a
statement about a population losing variation over generations; it has no
meaning for data with no generational process, and the conventional
F<sub>ST</sub> interpretive scales should not be imported.

**The exchangeable-labels assumption — the important caveat.** Every
measure here treats categories as unordered and equidistant. Two alleles
are simply different; neither is "closer" to a third. Much non-genetic
data violates this: ordinal categories have an order these measures
discard entirely; continuous variables are not categorical at all without
binning, and the binning choice would drive the answer; semantically
nested categories (labels drawn from an ontology or taxonomy) have real
similarity structure, which these measures ignore by construction. For
mixed-type data, general-purpose coefficients that respect variable type
(Gower's, and its relatives) remain the right default; the measures here
are the right tool only for the unordered-categorical part.

**Group weighting has no default.** `D` weights every group equally;
E<sub>ST</sub> can weight by group size. Outside genetics the analogous choice
is usually a substantive domain decision with no obvious answer, and it
changes results — make it explicitly and record it.

---

## Appendix A: notation and formula sheet

### Symbols

| Symbol | Meaning |
|---|---|
| `d` | Number of demes (subpopulations) |
| `N` | Diploid individuals per deme (`2N` gene copies) |
| `m` | Migration rate — fraction of a deme's gene pool replaced per generation |
| μ | Mutation rate per locus per generation |
| p<sub>i</sub> | Frequency of allele `i` |
| `H` | Expected heterozygosity of one deme |
| H<sub>S</sub> | Mean within-deme heterozygosity |
| H<sub>T</sub> | Total (pooled) heterozygosity |
| H<sub>ST</sub> | Correctly partitioned between-group heterozygosity |
| `J` | Nei's gene identity, `= 1 - H` |
| {}^{q}D | Hill number of order `q` (effective number of alleles) |
| E<sub>T</sub>, E<sub>S</sub>, E<sub>w</sub> | Total / mean-within / deme-size Shannon entropies |
| K<sub>T</sub>, K<sub>S</sub> | Total / mean-per-deme allele counts |
| `q` | Order of a diversity measure (rare-allele weighting) |
| \mathrm{NGD} | Nei's genetic distance |

### Within one deme

```math
H = 1 - \sum_i p_i^2 \qquad J = \sum_i p_i^2 = 1 - H \qquad {}^{H}D = \frac{1}{1-H} = \frac{1}{J}
```

```math
{}^{q}D = \left(\sum_i p_i^q\right)^{1/(1-q)}\ (q \neq 1), \qquad {}^{1}D = \exp\left(-\sum_i p_i \ln p_i\right)
```

### Across demes

```math
G_{ST} = \frac{H_{T} - H_{S}}{H_{T}} = 1 - \frac{H_{S}}{H_{T}}
```

```math
G_{ST} \approx \frac{1}{(d/(d-1))^2\cdot 4Nm + (d/(d-1))\cdot 4N\mu + 1}
```

```math
D = \left[\frac{H_{T}-H_{S}}{1-H_{S}}\right]\cdot\frac{d}{d-1}
  = 1 - \frac{J_{\mathrm{between}}}{J_{\mathrm{within}}}
  = 1 - \exp(-\mathrm{NGD})
```

```math
D \approx \frac{1}{1 + m/[\mu(d-1)]}
```

```math
\mathrm{Differentiation}_q
  = 1 - \frac{({}^{q}D_{S}/{}^{q}D_{T})^{q-1} - (1/d)^{q-1}}{1 - (1/d)^{q-1}}
```

```math
E_{ST} = \frac{E_{T} - E_{S}}{E_{w}} \qquad (E_{w} = \ln d \text{ for equal deme sizes})
```

```math
K_{ST} = 1 - \frac{K_{T}/K_{S} - d}{1 - d}
```

### Useful identities

```math
H_{T} = H_{S} + H_{ST} - H_{S}\cdot H_{ST}, \qquad H_{ST} = \frac{H_{T}-H_{S}}{1-H_{S}}
```

```math
D = \left(1 - \frac{1}{\beta}\right)\cdot\frac{d}{d-1}, \qquad \beta = {}^{H}D_{T}/{}^{H}D_{S}
```

```math
G_{ST} \leq 1 - H_S
```

---

## Appendix B: errata and ambiguities in the accepted-article text

The accepted-article version is explicitly marked as preceding copyediting and
proofreading. Recomputing its own numbers from its own definitions turned up
several small discrepancies. None affects the central argument; all are minor
editorial or typographical issues, and some may already have been corrected in the
final published version of record.

**1. Figure 2, middle scenario: `D = 0.5` should be `0.5556`.**

The text reports `D` values of "0.2, 0.5, and 1.0" for the three ten-deme
scenarios. The first and third are exact. The middle case (five demes fixed for
one allele and five fixed for another) computes as:

```math
H_{S} = 0, \qquad H_{T} = 0.5, \qquad D = (0.5/1)\cdot(10/9) = 0.5556.
```

The pairwise derivation the paper itself offers agrees: `25` differing pairs out
of `45` gives `25/45 = 0.5556`. The printed `0.5` appears to be a rounding or
transcription slip. Nothing in the argument depends on the exact figure — the
point is only that `D` differs across the three scenarios while G<sub>ST</sub> does
not.

**2. Equation 5 omits `L`.** The paper gives a refinement of the `D`
equilibrium formula for sets of loci with a roughly constant per-base-pair
mutation rate μ<sub>b</sub> and locus length `L` (harmonic mean, in base pairs):

```math
D \approx \frac{1}{1 + m/[(d-1)\cdot\mu_{b}\cdot L]}.
```

As printed in the accepted article, the `L` term is missing from the denominator,
even though the sentence immediately following the equation defines `L`.
Substituting μ ≈ μ<sub>b</sub>· L into Eq. 4 gives the intended form above,
which is the version used in this document and is clearly the intended reading of
the surrounding text.

**3. The discussion of "low G<sub>ST</sub> and high `m`" is a wording problem rather
than a mathematical one.** In the source sentence, "high `m`" must mean "high
inferred migration rate" rather than an actual high migration value in the
scenario being described, because the populations in question are explicitly
stipulated to be isolated. The point is that the inference is wrong, not that the
migration rate is truly high.

**4. Cross-reference slip in the SNP discussion.** In the bi-allelic SNP
section, the nine-plus-one configuration is compared to "Species A in Figure 2".
Figure 2's scenarios are unlabeled, while the species labels A/B/C belong to
Figure 3, where species A is the "all alleles shared" case (`D=0`). The intended
referent is the leftmost scenario of Figure 2, where `D=0.20`, which matches the
quoted value.

**5. Reference-list inconsistencies.** The bibliography contains a few mix-ups:

- `Gregorius and Roberds (1986)` is cited in text as `Gregorius et al. 1986`.
- Hedrick (2005) appears in the reference list and Figure 4 legend but is not
  cited in the body text.
- `Gilner et al. 2001` is almost certainly `Gliner et al. 2001`.
- The `Chao et al. (2015)` entry spells `Hseih` instead of `Hsieh`.

These are editorial inconsistencies, not substantive scientific errors, but they
are real and can confuse citation matching.

**6. Author-name inconsistency.** The author list gives "Frederick Archer"
while the corresponding contact address is `eric.archer@noaa.gov`. This is not a
mathematical issue, but it is a genuine name mismatch and could confuse readers
trying to reconcile the paper with the author metadata.

**7. Table S1 rounding.** The text gives H<sub>T</sub> = 0.97 and H<sub>S</sub> = 0.95 but
reports effective numbers `38.8` and `20.4`, which correspond to the more precise
values H<sub>T</sub> = 0.9742 and H<sub>S</sub> = 0.9510. Using the rounded two-decimal figures
gives `1/(1 - 0.97) = 33.3`, not `38.8`. Part IV.3 back-solves from the
effective numbers, which are the relevant precise figures.

---

## Appendix C: where this paper sits in Jost's larger program

This paper is one late node in a research program that has run since 2006 and
asks a single question: **how should biological diversity be measured?** The
program's answer, and its consequences, arrive in a recognisable order.

**The foundation (2006).** *Entropy and diversity* establishes that entropy
measures are not diversities — they are the *logarithms* of diversities. True
diversity is the exponential of entropy, an "effective number" of equally common
types. Part II of this document is that result applied to alleles.

**The partitioning machinery (2007).** *Partitioning diversity into independent
alpha and beta components* derives the correct decomposition of diversity into
within- and between-group parts, and shows the classical additive partition of
non-additive indices is invalid. This is where `D` itself is born, and it
supplies Part V.2 directly.

**The genetics polemic (2008–2009).** *G<sub>ST</sub> and its relatives do not measure
differentiation* carries the argument into population genetics and provoked a
sustained exchange (Heller & Siegismund 2009, Ryman & Leimar 2009, Meirmans &
Hedrick 2011, Whitlock 2011), to which Jost (2009) is the reply.

**The similarity framework (2011).** *Compositional similarity and beta
diversity* generalises the machinery into a parametric family of similarity
measures indexed by `q`; its Eq. 6.12 is the direct parent of this paper's
Eq. 6, and hence of `D`, E<sub>ST</sub>, and K<sub>ST</sub> together.

**The synthesis (2018).** The paper summarized here, alongside its companion
Gaggiotti et al. (2018) in the same special issue of *Evolutionary
Applications*, is the constructive successor to the 2008 polemic. Where the
2008 paper argued that G<sub>ST</sub> does not measure differentiation, this one grants
G<sub>ST</sub> a legitimate job — nearness to fixation — and argues that the two
families are complementary rather than rivals. The tone has shifted from
correction to synthesis, and the six-author list reflects a working group rather
than a lone critic.

**The through-line** is the **replication principle** (V.3): a measure that will
be compared as a ratio must double when you pool two equally diverse, completely
distinct groups. Nearly every specific error the program identifies — in
ecology, genetics, and phylogenetics alike — is a violation of that one axiom.

---

## Appendix D: works cited

Bibliographic details as given in the paper's reference list. DOIs are supplied
only where verifiable from the source text itself; the remaining entries are
listed with full citation details for lookup.

### The paper summarized here

- Jost L, Archer F, Flanagan S, Gaggiotti O, Hoban S, Latch E (2018).
  Differentiation measures for conservation genetics. *Evolutionary
  Applications* 11(7):1139–1148.
  DOI: [10.1111/eva.12590](https://doi.org/10.1111/eva.12590)

### Jost and collaborators

- Jost L (2006). Entropy and diversity. *Oikos* 113(2):363–375.
- Jost L (2007). Partitioning diversity into independent alpha and beta
  components. *Ecology* 88(10):2427–2439.
- Jost L (2008). G<sub>ST</sub> and its relatives do not measure differentiation.
  *Molecular Ecology* 17(18):4015–4026.
  DOI: [10.1111/j.1365-294X.2008.03887.x](https://doi.org/10.1111/j.1365-294X.2008.03887.x)
- Jost L (2009). `D` vs. G<sub>ST</sub>: response to Heller and Siegismund (2009) and
  Ryman and Leimar (2009). *Molecular Ecology* 18:2088–2091.
- Jost L (2010). The relation between evenness and diversity. *Diversity*
  2(2):207–232.
  DOI: [10.3390/d2020207](https://doi.org/10.3390/d2020207)
- Jost L, DeVries PJ, Walla T, Greeney H, Chao A, Ricotta C (2010).
  Partitioning diversity for conservation analyses. *Diversity and
  Distributions* 16:65–76.
- Jost L, Chao A, Chazdon RL (2011). Compositional similarity and beta
  diversity. Pages 66–84 in Magurran AE, McGill BJ (eds), *Biological
  Diversity: Frontiers in Measurement and Assessment*. Oxford University Press.
- Chao A, Chiu CH, Jost L (2010). Phylogenetic diversity measures based on Hill
  numbers. *Philosophical Transactions of the Royal Society B* 365:3599–3609.
- Chao A, Jost L, Hsieh TC, Ma KH, Sherwin B, Rollins LA (2015). Expected
  Shannon entropy and Shannon differentiation between subpopulations for neutral
  genes under the finite island model. *PLoS ONE*.
  DOI: [10.1371/journal.pone.0125471](https://doi.org/10.1371/journal.pone.0125471)
- Gaggiotti OE, Chao A, Peres-Neto P, Chiu C-H, Edwards C, Fortin M-J, Jost L,
  Richards CM, Selkoe KA (2018). Diversity from genes to ecosystems: a unifying
  framework to study variation across biological metrics and scales.
  *Evolutionary Applications*.

### Classical sources for the fixation family

- Wright S (1943). Isolation by distance. *Genetics* 28(2):114.
- Wright S (1965). The interpretation of population structure by `F`-statistics
  with special regard to systems of mating. *Evolution* 19(3):395–420.
- Wright S (1978). *Evolution and the Genetics of Populations, vol. 4:
  Variability Within and Among Populations.* (Source of the p. 84 quotation.)
- Crow JF, Kimura M (1970). *An Introduction to Population Genetics Theory.*
  Harper and Row, New York.
- Kimura M, Crow JF (1964). The number of alleles that can be maintained in a
  finite population. *Genetics* 49:725–738.
- Nei M (1972). Genetic distance between populations. *American Naturalist*
  106(949):283–292.
- Nei M (1973). Analysis of gene diversity in subdivided populations.
  *Proceedings of the National Academy of Sciences* 70(12):3321–3323.
- Crow JF, Aoki K (1984). Group selection for a polygenic behavioral trait:
  estimating the degree of population subdivision. *Proceedings of the
  National Academy of Sciences* 81(19):6073–6077. Confirmed directly
  (full text) as the source of the finite-deme `(d/(d-1))²` migration-term
  correction Part VI's Eq. 2 and Appendix A carry — added here because
  those sections previously stated the correction with no attribution at
  all. Their own Eq. 7 has no mutation term; see Part VI for the
  distinction from this document's own Eq. 2.
- Takahata N (1983). Gene identity and genetic differentiation of
  populations in the finite island model. *Genetics* 104(3):497–512.
  Cited by Crow & Aoki (1984) as the source of the general
  finite-`K`-allele solution (their eq. 6) their own Eq. 7 simplifies by
  dropping the mutation term — not independently verified here.
- Weir BS, Cockerham CC (1984). Estimating `F`-statistics for the analysis of
  population structure. *Evolution* 38(6):1358–1370.

### The surrounding debate

- Gerlach G, Jueterbock A, Kraemer P, Deppermann J, Harmand P (2010).
  Calculations of population differentiation based on G<sub>ST</sub> and `D`: forget
  G<sub>ST</sub> but not all of statistics! *Molecular Ecology* 19(18):3845–3852.
- Gregorius HR, Roberds JH (1986). Measurement of genetical differentiation
  among subpopulations. *Theoretical and Applied Genetics* 71:826–834.
- Gregorius HR (2010). Linking diversity and differentiation. *Diversity*
  2:370–394.
- Hedrick PW (2005). A standardized genetic differentiation measure.
  *Evolution* 59(8):1633–1638.
- Heller R, Siegismund HR (2009). Relationship between three measures of genetic
  differentiation G<sub>ST</sub>, D<sub>EST</sub> and G'_{ST}: how wrong have we been?
  *Molecular Ecology* 18(10):2080–2083.
- Meirmans PG, Hedrick PW (2011). Assessing population structure: F<sub>ST</sub> and
  related measures. *Molecular Ecology Resources* 11(1):5–18.
- Wang J (2012). On the measurements of genetic differentiation among
  populations. *Genetics Research* 94:275–289.
- Whitlock MC (2011). G'_{ST} and `D` do not replace F<sub>ST</sub>. *Molecular
  Ecology* 20(6):1083–1091.
- Whitlock MC, McCauley DE (1999). Indirect measures of gene flow and migration:
  F<sub>ST</sub> ≠ 1/(4Nm+1). *Heredity* 82:117–125.
  DOI: [10.1046/j.1365-2540.1999.00496.x](https://doi.org/10.1046/j.1365-2540.1999.00496.x)

### Other works cited in the argument

- Caballero A, Garcia-Dorado A (2013). Allelic diversity and its implications
  for the rate of adaptation. *Genetics* 195:1373–1384.
- Flanagan SP, Forester B, Latch EK, Aitken S, Hoban S (2017). Guidelines for
  using genomic assessment and monitoring of adaptive variation to inform species
  conservation. *Evolutionary Applications.*
- Funk WC, McKay JK, Hohenlohe PA, Allendorf FW (2012). Harnessing genomics for
  delineating conservation units. *Trends in Ecology & Evolution*
  27(9):489–496.
- Gliner JA, Morgan GA, Leech NL, Harmon RJ (2001). Problems with null
  hypothesis significance testing. *Journal of the American Academy of Child and
  Adolescent Psychiatry* 40(2):250–252. (Cited in the paper as "Gilner".)
- Leng L, Zhang DX (2013). Time matters: some interesting properties of the
  population differentiation measures G<sub>ST</sub> and `D` overlooked in the
  equilibrium perspective. *Journal of Systematics and Evolution* 51(1):44–60.
- Sherwin W (2010). Entropy and information approaches to genetic diversity and
  its expression: genomic geography. *Entropy* 12(7):1765–1798.
- Strand TM, Segelbacher G, Quintela M, Xiao L, Axelsson T, Höglund J (2012).
  Can balancing selection on MHC loci counteract genetic drift in small
  fragmented populations of black grouse? *Ecology and Evolution* 2(2):341–353.
- Vilas A, Pérez-Figueroa A, Quesada H, Caballero A (2015). Allelic diversity
  for neutral markers retains a higher adaptive potential for quantitative
  traits than expected heterozygosity. *Molecular Ecology* 24:4419–4432.

---

## Appendix E: the differentiation debate, 2008–2011

The 2008–2011 exchange over G<sub>ST</sub> and `D` is best read not as a contest over
which formula is numerically larger on a given dataset, but as a dispute over which
question the field was actually asking. Is the relevant question “how close are
these demes to fixation?” or “how different are the alleles they actually carry?”
The classical family and the Jost family answer different questions, and the
debate is one of the clearest instances in population genetics where a naming
collision produced a long-running misunderstanding.

**Jost's position.** In the 2008 polemic, Jost's argument was that the
classical G<sub>ST</sub> family, and by extension many uses of F<sub>ST</sub> and θ,
was being interpreted as if it measured absolute genetic differentiation when it
in fact measured the opposite: the degree to which a population has moved toward
fixation within the pooled array. The formal point was not trivial. The
classical family normalizes by H<sub>T</sub>, while the allelic family normalizes by
1-H<sub>S</sub>. Those are different denominators, and therefore different concepts. Once
this is acknowledged, the apparent paradoxes in the paper's worked examples are
no longer paradoxes at all: a population can be nearly fixed within demes and yet
retain almost no common allele content across demes, producing a very low
G<sub>ST</sub> and a very high `D`.

**The counter-argument.** The critics were not simply defending a formula; they
were defending a working interpretation that had become standard in a large body
of empirical work. In that interpretive regime, F<sub>ST</sub>-type statistics were used
as catch-all summaries of population structure, and they were often read as if
they told a manager, “how different are these demes?” The practical objection to
Jost was that, in many real settings, the difference between fixation-based and
allelic differentiation measures might be less consequential than the raw
heterozygosity levels and the sampling regime suggested. In other words, critics
argued that the classical measures were still useful working summaries, even if
they were not the right measure for every biological question.

**The key issue was not “which index is better” in the abstract.** It was the
question of domain: a statistic can be excellent for estimating demography and
near-fixation under drift–migration models while being unhelpful for conservation
decisions about retaining unique alleles or protecting demes with distinct
allele pools. The debate therefore looked like a fight over formulas, but it was
really a fight over the proper use of those formulas.

**Why the debate matter.** The 2008–2011 exchange helped define the terms on
which the field now operates. The modern view is narrower and more precise than
the original polemic: the classical family remains central to demographic
inference, particularly questions about effective migration, drift, and the
relative importance of population size and migration rate. The Jost family remains
central to allelic differentiation, total turnover, and conservation decisions
about which demes hold unique genetic material. Neither family is a drop-in
replacement for the other.

This is why the current paper is written in a synthetic tone rather than a
combative one. It does not say that G<sub>ST</sub> is useless. It says it is the wrong
index for a distinct question. The value of the debate is that it made that
separation explicit. The field has not simply chosen one side and discarded the
other; instead, it has largely accepted the more careful distinction: report the
measurement family by what it represents, and do not read a nearness-to-fixation
statistic as if it were a measure of absolute allelic dissimilarity.

**Where the controversy remains.** The disagreement persists in application, not
in the basic algebra. The debate is still alive when researchers ask which
measure to lead with in a particular study, or whether a high G<sub>ST</sub> can be
interpreted as meaningful differentiation in a management context. The paper's
position is that such interpretations are valid only when the correct question is
being asked. If the goal is estimating demography, G<sub>ST</sub> and F<sub>ST</sub> remain
highly informative. If the goal is protecting allelic diversity, the Jost family
is the more relevant object. The contemporary field largely treats these as
complementary quantities rather than rival replacements.

**Real-world impact.** The largest impact of the work has been methodological,
not regulatory: it changed the way conservation biologists frame management
questions. Programs concerned with preserving genetic diversity, identifying
management units, or prioritizing populations for restoration are explicitly
advised to distinguish fixation-based statistics from measures of allelic
turnover and unique diversity (Jost et al. 2010; Funk et al. 2012; Gaggiotti et
al. 2018; Flanagan et al. 2017). In other words, the practical consequence is
not that classical population-genetic tools were abandoned, but that they are now
read more carefully: a low G<sub>ST</sub> or a low F<sub>ST</sub> does not by itself imply
that demes are equivalent in the sense that matters for preserving unique
alleles. That distinction matters in real conservation decisions about habitat
fragmentation, translocation, and the prioritization of populations with
non-overlapping genetic repertoires.

The same point appears in the more recent literature on genomic monitoring and
adaptive variation, where authors emphasize that conservation decisions should
consider allelic richness and functional diversity, not just drift-based
summaries (Flanagan et al. 2017; Funk et al. 2012). Jost's work therefore had
its clearest applied effect in the conceptual and analytical language of
conservation genetics: it pushed the field toward reporting multiple measures, and
away from treating a single fixation statistic as a stand-in for all forms of
“differentiation.”

The real legacy of the 2008–2011 polemic is therefore not that one statistic
won and the other lost. It is that the field became much more explicit about the
fact that “differentiation” is not a single thing.

---

## Metadata

```text
generator-name: Claude Code
generator-version: Claude Sonnet 5
generator-model-token: claude-sonnet-5
generator-provider: Anthropic
generation-date: 2026-08-11
generator-responsibility: other
```
