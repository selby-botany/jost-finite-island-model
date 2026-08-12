# The finite island model: a plain-language introduction

- [The finite island model: a plain-language introduction](#the-finite-island-model-a-plain-language-introduction)
  - [Who this document is for](#who-this-document-is-for)
  - [1. Why scientists model population differentiation](#1-why-scientists-model-population-differentiation)
  - [2. Wright's island model: the starting point](#2-wrights-island-model-the-starting-point)
    - [2.1 The setup](#21-the-setup)
    - [2.2 Why "finite" needs saying out loud](#22-why-finite-needs-saying-out-loud)
  - [3. The model as a repeating random process](#3-the-model-as-a-repeating-random-process)
    - [3.1 What is being tracked](#31-what-is-being-tracked)
    - [3.2 One generation, in two steps](#32-one-generation-in-two-steps)
    - [3.3 Variations found in the literature](#33-variations-found-in-the-literature)
  - [4. Software that implements this model today](#4-software-that-implements-this-model-today)
  - [5. Reading Lou Jost's papers with this model in hand](#5-reading-lou-josts-papers-with-this-model-in-hand)
  - [6. A note on sources](#6-a-note-on-sources)
  - [Appendix A: is this a physics model in disguise?](#appendix-a-is-this-a-physics-model-in-disguise)
  - [Appendix B: the same idea runs modern ancestry software](#appendix-b-the-same-idea-runs-modern-ancestry-software)
  - [Metadata](#metadata)

## Who this document is for

No background in genetics, statistics, or programming is assumed. Some
comfort with basic probability (what a coin flip's randomness means, what an
average is) and basic algebra is enough to follow every section through
Part 5. The two appendices go further into physics and statistics
respectively and say so before they start; skipping them loses nothing
needed for the main thread.

This document draws only on material anyone can look up: published papers
(with DOIs), Wikipedia, and the public project pages and source-code
repositories of the software it names. Nothing here depends on private
correspondence with any of the researchers whose work is described. Where a
claim could not be checked against a public source, that is said explicitly
rather than left implicit.

The goal is to leave you able to read Lou Jost's papers on genetic
differentiation with the underlying model already in hand, instead of
having to reconstruct it from the papers' own compressed descriptions.

---

## 1. Why scientists model population differentiation

Biologists often want to answer a simple-sounding question: **how
genetically different are two, or more, populations of the same species?**

The answer matters in several ways:

- **Conservation.** If two frog populations are genetically almost
  identical, losing one is a setback. If they are strongly differentiated,
  losing one destroys variation that cannot be recovered from the other.
- **Taxonomy.** Whether two plant populations are different varieties or
  different species often turns on exactly this question.
- **Evolutionary biology.** Watching differentiation build up over time
  shows how quickly isolation turns one population into two lineages.

Answering it quantitatively takes two separate pieces of machinery:

1. A **model of the process** that produces genetic differences between
   populations over time. This is the island model, the subject of this
   document.
2. A **statistic** computed from real or simulated data that reports how
   much differentiation exists. This is where Lou Jost's own contribution —
   a measure he calls **D** — enters, as a proposed replacement for the
   older **F<sub>ST</sub>** and **G<sub>ST</sub>** family of statistics. (A companion document,
   [the differentiation-measures guide](20260810-claude-sonnet-5-jost-differentiation-measures.md),
   covers that half in detail.)

The island model is the *generative* half of the pair: it is what gets
simulated to find out what differentiation values are even possible or
expected under a given set of assumptions, and to check whether a proposed
statistic behaves sensibly against a mechanistic ground truth that is fully
known, because it was built by hand rather than measured from nature.

---

## 2. Wright's island model: the starting point

The finite island model is a variant of a much older and simpler model due
to Sewall Wright (1931, 1943), usually just called *the island model*.
Understanding the plain version first makes the word "finite" easy to
place.

### 2.1 The setup

- There are **d** separate subpopulations, called demes or, by metaphor,
  islands, each holding a fixed number **N** of individuals. Most species of
  conservation interest are diploid — each individual carries two copies of
  every gene, one from each parent — so each island holds $2N$ gene copies
  at any one location in the genome.
- Time moves in **discrete, non-overlapping generations**: generation
  $t$ is entirely replaced by generation $t+1$. There is no smooth,
  continuous change to track — only a sequence of discrete snapshots, each
  produced from the one before it by a fixed rule.
- Each generation, a fraction **m** of each island's gene pool is replaced
  by migrants. In the *classic* island model, those migrants come from an
  infinitely large, well-mixed pool that represents the *average* allele
  frequency across all the other islands combined. Mathematically, this
  means the source that supplies migrants never itself changes through
  drift or depletion — it behaves like a fixed, external reservoir that
  never runs low or shifts composition.
- After migration, each island rebuilds its population by drawing $2N$
  gene copies at random from its own (post-migration) local pool. This
  drawing step is where **genetic drift** happens: with only a finite
  number of individuals, allele frequencies wander randomly from one
  generation to the next, purely because of which individuals happened to
  have offspring — the same way a modest number of coin flips will drift
  away from an exact 50/50 split of heads and tails, just from chance.

### 2.2 Why "finite" needs saying out loud

Here is the detail that gives the model its name. In Wright's original
formulation, only the **individual islands** are finite in size ($N$
each); the *total* collection of all islands that supplies migrants is
implicitly **infinite** — migrants always arrive from a fixed background
frequency that itself never drifts. This is a convenient mathematical
fiction: it makes it possible to write a simple, closed-form formula for the
equilibrium value of **F<sub>ST</sub>** (the classic "differentiation" statistic),
precisely because the migrant source never changes.

The **finite island model** removes that fiction. There are only a *finite*
number of islands, **D**, each of finite size $N$, and no infinite
external reservoir standing behind them. Migrants in generation $t$ are
drawn from the actual, finite, currently-drifting collection of all the
*other* islands' current frequencies. Three consequences follow:

- The whole set of islands together forms a **finite, closed system** with
  no fixed background to lean on.
- Because it is closed and finite, the *entire* collection of islands also
  drifts as a whole and, given long enough with low enough migration, can
  eventually converge on a single allele everywhere — losing all variation
  — unless new mutations keep replenishing it.
- This changes how differentiation statistics *behave at equilibrium*. The
  classic **F<sub>ST</sub>** formula assumes an infinite source, which caps
  **F<sub>ST</sub>** at a ceiling set purely by $N$ and $m$; once the source is
  finite, the achievable range of **F<sub>ST</sub>**-like statistics depends on
  **D** as well. More importantly, **F<sub>ST</sub>** and /**G<sub>ST</sub>** statistics have a
  known pathology: their maximum attainable value is *capped well below 1*
  whenever the genetic marker being used is highly variable (many alleles,
  high internal diversity) — even when the islands share *no* alleles at
  all, which is complete differentiation by any intuitive standard. This is
  the core of Jost's critique of these statistics: they conflate "these
  populations share no alleles" with "these populations are only
  moderately different," because the statistic's own ceiling depends on how
  much diversity exists *within* each population, not on how different the
  populations are *from each other*. His **D** statistic is built to
  separate those two things, and the finite island model is the testing
  ground where that claim gets checked, because it lets diversity and
  differentiation be dialed independently and the result observed directly.

**This is the single most important idea to take away**: the finite island
model is a tool for generating data with a known, exact history — exactly
how much migration, drift, and diversity went into producing it — so that a
candidate summary statistic (**F<sub>ST</sub>**, **G<sub>ST</sub>**, **D**, **G'<sub>ST</sub>**, and
so on) can be checked against that known history rather than against
another guess.

---

## 3. The model as a repeating random process

Set aside islands and alleles for a moment. The mechanics of this model are
easiest to see as an ordinary repeating process: a set of numbers gets
updated by a fixed rule involving some randomness, once per tick, over and
over.

### 3.1 What is being tracked

At generation $t$, the complete state of the model is a table of allele
frequencies — one row per island, one column per possible allele at the
genetic location being tracked:

- **D** = number of islands.
- $k$ = number of distinct alleles possible at that location.
- $p_{t,i,j}$ = the frequency (a number between 0 and 1) of allele $j$
  on island $i$ at generation $t$, with the frequencies in every row
  summing to exactly 1: $\sum_j p_{t,i,j} = 1$ for each island $i$.

For a location with only two possible alleles (a "SNP," short for single
nucleotide polymorphism, the most common type of genetic marker in modern
studies), $k = 2$ and the whole row compresses to one number per island,
since the second allele's frequency is always $1$ minus the first.
Other markers, especially the short repeated-sequence markers called
*microsatellites*, can have many alleles — $k$ in the range of 5 to 20 is
common — which is one reason Jost's statistics are built to generalize
cleanly past the two-allele case, unlike some of the older **F<sub>ST</sub>**
variants.

### 3.2 One generation, in two steps

Producing generation $t+1$ from generation $t$ is two operations
applied to the whole table: one deterministic blending step, and one
random re-sampling step.

```math
p_{t+1} = \mathrm{Drift}\bigl(\mathrm{Migrate}(p_t)\bigr)
```

**Step A — migration (a weighted blend, given the finite pool that
generation).**

For each island $i$, compute a post-migration frequency as a weighted
average of the island's own pre-migration frequency and the frequency of
the "migrant pool" it draws from:

```math
p_{\mathrm{mig},i} = (1 - m)\, p_{t,i} \;+\; m\, \bar p_i
```

Here $\bar p_i$ is the frequency vector of the migrant pool available to
island $i$. Two common choices for what $\bar p_i$ is, both still
called "the finite island model":

- **The island model proper** (the default meaning of the term, absent
  other qualifiers). $\bar p_i$ is the average allele frequency across
  *all the other islands* — either an unweighted mean, or a mean weighted
  by island size if they differ. Every island exchanges migrants with one
  shared pool built from everyone else.
- **The stepping-stone model** (a different, spatial relative, easily
  confused with the island model in casual reading). $\bar p_i$ is the
  frequency of only the islands that are *physically adjacent* — a
  one- or two-dimensional lattice of islands exchanging migrants only with
  their neighbors. Under this model, differentiation grows with geographic
  distance; under the plain island model it does not, since every island is
  "equally distant" from every other. Jost's use of the finite island model
  is normally the plain, non-spatial, all-to-all version.

This step is written above as a deterministic blend *given* the current
frequencies — but because $\bar p_i$ is itself built from $p_t$, which
was produced by the *previous* generation's random drift step, the entire
process is random from end to end, even though this one step, in
isolation, involves no dice roll. Some more detailed simulators add an
extra layer of randomness here too — sampling the actual number of
migrating individuals, rather than treating migration as an idealized
continuous fraction — which is worth knowing about even though the
simplest version of the model skips it.

**Step B — drift (the actual random step).**

Each island's next generation is formed by drawing $2N$ gene copies from
a probability distribution set by $p_{\mathrm{mig},i}$. For a two-allele
marker this is a **binomial draw** — the same kind of randomness as
counting heads in a batch of biased coin flips:

```math
X_i \sim \mathrm{Binomial}\bigl(2N,\ p_{\mathrm{mig},i}\bigr)
```

```math
p_{t+1,i} = \frac{X_i}{2N}
```

For a marker with more than two alleles ($k > 2$), the same idea
generalizes to a **multinomial draw** — like rolling a $k$-sided weighted
die $2N$ times and counting how often each face comes up:

```math
\mathbf{X}_i \sim \mathrm{Multinomial}\bigl(2N,\ \mathbf{p}_{\mathrm{mig},i}\bigr)
```

```math
\mathbf{p}_{t+1,i} = \frac{\mathbf{X}_i}{2N}
```

($\mathbf{X}_i$ here is a list of $k$ counts, one per allele, rather
than a single number.)

That is the entire model: one weighted blend and one random draw, applied
every generation, for as many generations as wanted, independently on
every island. Real studies usually track many independent genetic
locations in parallel — they behave as independent repeats of the same
process, assuming the locations are far enough apart in the genome not to
be inherited together — so in practice the same two-step rule just runs
many times over, once per location.

### 3.3 Variations found in the literature

- **Mutation.** With some small probability $\mu$ each generation, a
  mutation step can be added between migration and drift, changing an
  allele's identity outright. This matters for studies of long-run
  equilibrium between mutation, migration, and drift; it usually is not
  needed for simulations covering only a few dozen or a few hundred
  generations from a defined starting point, where migration and drift
  alone dominate the outcome.
- **Unequal island sizes.** Letting $N$ vary by island changes both the
  drift step (each island draws its own $2N_i$ copies) and the weighting
  used to build the migrant pool $\bar p_i$.
- **Unequal or asymmetric migration.** Letting the migration rate $m$
  vary by island, or specifying a full matrix of pairwise migration rates
  between specific island pairs, generalizes the model into what is
  sometimes called a general migration-matrix model; the plain island
  model is the special case where every rate is the same.
- **Selection.** Not part of the base model, which assumes every allele is
  *neutral* — meaning no allele carries any survival or reproductive
  advantage over another. Differentiation-statistic research, including
  Jost's, deliberately targets neutral genetic markers, precisely so that
  observed differentiation reflects only demography (migration and drift)
  and not natural selection. Where selection is added, an extra step
  reweighting frequencies by fitness is inserted before drift.

---

## 4. Software that implements this model today

Several existing, currently maintained tools implement some version of
this model and are freely available to try.

**[quantiNemo 2](https://www2.unil.ch/popgen/softwares/quantinemo/)**, from
Jérôme Goudet's lab (Neuenschwander, Michaud & Goudet, *Bioinformatics*,
2019, DOI:
[10.1093/bioinformatics/bty737](https://doi.org/10.1093/bioinformatics/bty737);
source at
[github.com/jgx65/quantinemo](https://github.com/jgx65/quantinemo)), is an
individual-based, forward-time population-genetics simulator for
structured populations connected by migration, configured through a text
settings file. It has a C++ core with both a slower, full-featured
individual-based mode and a faster population-based mode, and it is under
active development.

**[hierfstat](https://cran.r-project.org/package=hierfstat)**, an R
package also from Goudet's group (source:
[github.com/jgx65/hierfstat](https://github.com/jgx65/hierfstat)), includes
functions — `sim.freq.t`, `sim.genot.t`, and the more general
`sim.freq.metapop.t` — that simulate exactly the discrete-time,
migration-plus-drift process described in Part 3 above, generation by
generation, with an arbitrary migration matrix (including stepping-stone
layouts) as an option. `hierfstat` remains actively maintained: CRAN lists
version 0.5-11 (2022-05-03), and its GitHub repository shows commits as
recently as late 2024.

Tools in this category are, understandably, usually built to answer "what
is the equilibrium differentiation statistic?" rather than "show me every
generation's allele frequencies" — running many replicate simulations to
put a confidence interval on one final summary number is the typical use
case, and the intermediate, generation-by-generation history is treated as
disposable working state along the way rather than as an output in its
own right. Someone who specifically wants that full history — to test a
new statistic against a known trajectory, generation by generation, rather
than only against a final equilibrium value — will generally need to
either configure one of these tools to retain more of its own internal
state than it does by default, or implement the (mechanically simple, as
Part 3 shows) two-step update directly.

---

## 5. Reading Lou Jost's papers with this model in hand

With the mechanics above established, Jost's key papers on differentiation
statistics become considerably easier to follow. Every citation below was
checked directly against PubMed and can be trusted for title, authors,
journal, year, and DOI.

- **Jost L, "**G<sub>ST</sub>** and its relatives do not measure
  differentiation."** *Molecular Ecology* 17(18):4015–26, 2008. DOI:
  [10.1111/j.1365-294x.2008.03887.x](https://doi.org/10.1111/j.1365-294x.2008.03887.x).
  PMID: 19238703. The foundational critique. In the language of Part 2:
  under the finite island model, as the diversity within a population goes
  up, the *maximum possible* value **G<sub>ST</sub>** can reach — even at complete
  differentiation between islands, meaning zero shared alleles — goes
  *down*, sometimes sharply. A highly diverse marker showing **G<sub>ST</sub>** =
  0.2 might represent *complete* differentiation, while a low-diversity
  marker showing the same 0.2 represents only mild differentiation. The
  statistic is therefore not comparable across markers or studies with
  different diversity levels (Wikipedia's
  [Fixation index](https://en.wikipedia.org/wiki/Fixation_index) article
  independently corroborates this point: "the interpretation of **F<sub>ST</sub>**
  can be difficult when the data analyzed are highly polymorphic ...
  **F<sub>ST</sub>** can have an arbitrarily low upper bound"). Jost proposes
  **D**, defined to isolate the "true" differentiation component
  independent of within-population diversity — a companion document,
  [the differentiation-measures guide](20260810-claude-sonnet-5-jost-differentiation-measures.md),
  walks through exactly how.
- **The reply/rebuttal exchange** that followed in the same journal,
  confirmed via PubMed's "Comment in/on" links on the 2008 paper:
  - Ryman N, Leimar O, "**G<sub>ST</sub>** is still a useful measure of genetic
    differentiation — a comment on Jost's **D**." *Molecular Ecology*
    18(10):2084–7, 2009. DOI:
    [10.1111/j.1365-294X.2009.04187.x](https://doi.org/10.1111/j.1365-294X.2009.04187.x).
  - Heller R, Siegismund HR, a further 2009 comment in the same exchange
    (PMID 19645078), and Gerlach G et al., "Calculations of population
    differentiation based on ****G<sub>ST</sub>**** and **D**: forget **G<sub>ST</sub>** but not
    all of statistics!" *Molecular Ecology* 19(18):3845–52, 2010. DOI:
    [10.1111/j.1365-294X.2010.04784.x](https://doi.org/10.1111/j.1365-294X.2010.04784.x).
  - Whitlock MC, "**G'<sub>ST</sub>** and **D** do not replace **F<sub>ST</sub>**."
    *Molecular Ecology* 20(6):1083–91, 2011. DOI:
    [10.1111/j.1365-294X.2010.04996.x](https://doi.org/10.1111/j.1365-294X.2010.04996.x).
    This paper proposes the standardized **G'<sub>ST</sub>** as a partial fix to
    the classic **G<sub>ST</sub>** ceiling problem, while arguing that **D** and
    **G'<sub>ST</sub>** answer a different question from **F<sub>ST</sub>** and should not
    fully replace it. Read together, this whole exchange is the applied
    debate over what a differentiation statistic should mean, and all
    sides generally argue by simulating some version of a finite island
    model and comparing candidate statistics against the same known ground
    truth.
- **Jost L, Archer F, Flanagan S, Gaggiotti O, Hoban S, Latch E,
  "Differentiation measures for conservation genetics."** *Evolutionary
  Applications* 11(7):1139–48, 2018. PMID: 30026802. A synthesis, useful as
  a map of the whole statistic family (**F<sub>ST</sub>**, **G<sub>ST</sub>**, **G'<sub>ST</sub>**,
  **D**, and others) and when each is — and is not — an appropriate choice.
  It is written for a conservation-genetics audience deciding which number
  to report, which makes it the best single paper to hand a newcomer. See
  the companion document,
  [the differentiation-measures guide](20260810-claude-sonnet-5-jost-differentiation-measures.md),
  for a full plain-language walkthrough of this paper.

The throughline across all of them: **every one of these arguments is made
or tested by simulating a finite island model and computing multiple
candidate statistics against the same known, generated ground truth.**

---

## 6. A note on sources

This document combines standard, textbook population-genetics knowledge
(Wright's island model, the Wright-Fisher drift process — see
[Population genetics](https://en.wikipedia.org/wiki/Population_genetics)
and [Fixation index](https://en.wikipedia.org/wiki/Fixation_index) on
Wikipedia for independent corroboration of the base mathematical model in
Parts 2–3) with citations checked directly against PubMed's public
E-utilities API (exact titles, authors, journals, years, volume/page
numbers, and DOIs for every paper named in Part 5 were confirmed this way).
The details on quantiNemo and hierfstat in Part 4 were likewise checked
against those projects' own public project pages and source-code
repositories. Nothing in this document relies on private correspondence
with any of the people whose work it describes; where a claim could not be
verified against a public source, it has been left out rather than
included as an assumption.

---

## Appendix A: is this a physics model in disguise?

*Optional. This appendix uses ideas from statistical mechanics — symmetry
groups, mean-field models — and assumes some prior exposure to that
language. Skipping it costs nothing needed for the rest of this document.*

The finite island model's coupling structure — each island's next state
depends on a weighted combination of some set of other islands' states,
plus a random local update — resembles what physicists call a Markov
random field. A natural question, for anyone arriving from a
statistical-mechanics background: is it, more specifically, a Markov random
field whose local variable lives in some continuous rotation group —
$SO(n)$ for some $n$ — with allele frequency playing the role of a
continuous angular/positional variable, the way a spin lives on a circle
or sphere in an $O(n)$-symmetric physics model? And, separately: what
actually *is* the symmetry group of the finite island model, given that
some kind of discrete transform seems to be involved?

*(This restates the question as originally intended: an earlier version
of this exchange named the group $U(1)$ specifically, but the group
actually being recalled — a rotation group $SO(n)$ acting on some
$n$-dimensional space, connected to a discrete transform of the model — is
the more precise target. The analysis below addresses that corrected
question directly; the conclusion is unchanged.)*

The coupling-structure analogy holds up well, but no continuous rotation
group survives as an exact symmetry of the model itself. It helps to
separate three different questions: the *graph structure* of who is
coupled to whom; the *ambient continuous symmetry* that appears when the
model is transformed into a more convenient coordinate system; and the
*actual symmetry group* of the physically constrained state space, once
that continuous symmetry is broken by a hard boundary.

**Coupling structure.** Symmetric all-to-all migration, where every island
exchanges with the population-wide average frequency, is exactly a
complete-graph coupling — the population-genetics equivalent of a
mean-field spin model (the Curie–Weiss model, infinite-range Ising
interaction on a complete graph, being the classic physics example). The
stepping-stone model, coupling each island only to its physically adjacent
neighbors, is the finite-range or lattice analogue. So "island model ≈
mean-field random field, stepping-stone model ≈ local/lattice random
field" is a genuine and useful correspondence.

**Where an $SO(n)$ really does appear: the multi-allele sphere embedding.**
This is the legitimate half of the corrected intuition, and it is worth
taking seriously rather than dismissing. For a two-allele marker
(frequency $p \in [0, 1]$), Wright's 1945 arcsine square-root transform,
$\theta = \arcsin(\sqrt{p})$ — used extensively by Kimura in solving the
Wright–Fisher diffusion equation — turns the state-dependent diffusion
coefficient $p(1-p)$ into a *constant* coefficient on $\theta \in
[0, \pi/2]$, so the drift process looks like Brownian motion confined to
a quarter-circle arc. This has a direct, well-known generalization to a
marker with $k$ alleles (covered in standard mathematical population
genetics references, e.g. Warren Ewens's *Mathematical Population
Genetics*): writing $z_i = \sqrt{p_i}$ for each allele's frequency, the
constraint $\sum_i p_i = 1$ becomes $\sum_i z_i^2 = 1$ — that is, the
vector $z = (z_1, \ldots, z_k)$ lives on the unit sphere $S^{k-1}$ sitting
inside $\mathbb{R}^k$. Under this transform, the Wright–Fisher diffusion
generator becomes (proportional to) the **Laplace–Beltrami operator on
that sphere** — literally the same differential operator that governs
heat diffusion on a curved surface, with eigenfunctions given by spherical
harmonics. The natural continuous symmetry group of a round sphere in
$\mathbb{R}^k$ is exactly $SO(k)$ (or $O(k)$, including reflections) — an
$n$-dimensional rotation group with $n = k$, the number of alleles. So:
yes, there is a real $SO(n)$ here, with $n$ tied to the number of alleles
at the locus (not, on current evidence, to $4L$ or any other function of
the number of loci $L$ specifically — see the note below on why that part
of the original recollection does not carry through cleanly).

**Why that $SO(k)$ does not survive as a symmetry of the actual model.**
The catch is the same one that sank $U(1)$: the physically meaningful
region is not the *whole* sphere, only the positive orthant of it, since
each $z_i = \sqrt{p_i} \geq 0$ by definition — an allele frequency cannot
be negative. That positive-orthant patch covers only $1/2^k$ of the
sphere's surface, and its boundary faces (where some $z_i = 0$, i.e. some
allele has gone extinct on that island) are exactly the absorbing
fixation states already identified in the two-allele case. A generic
rotation in $SO(k)$ does not map this patch to itself — it would rotate
some coordinates negative, which is not a physically meaningful
allele-frequency state at all. The only elements of $O(k)$ that *do* map
the positive orthant back to itself are **permutations of the $k$
coordinates** (relabeling which allele is called "1," "2," and so on) —
which is exactly the discrete transform the corrected question pointed
toward. In other words: the model genuinely lives on (a patch of) a
sphere with an ambient continuous $SO(k)$ symmetry, but the physical
boundary — genetic drift's absorbing fixation states — breaks that
continuous symmetry down to the finite subgroup of coordinate
permutations, $S_k$, the same discrete label-permutation group identified
in the two-allele analysis (there, the k = 2 case: $S_2 \cong
\mathbb{Z}_2$).

**On the multi-locus case and "$n = 4L$."** If several independent,
unlinked loci are tracked simultaneously (as real analyses do), the full
state space becomes a product of one such sphere per locus — an ambient
symmetry of $SO(k_1) \times SO(k_2) \times \cdots \times SO(k_L)$ (or
$SO(k)^L$ if every locus has the same number of alleles $k$), possibly
with an extra factor of $S_L$ if the loci themselves are statistically
exchangeable. This product structure is a legitimate and probably closer
match to what "$n$ related to $L$" was reaching for than any single fixed
formula — but no derivation here produces literally $n = 4L$, and forcing
that specific number would be fabricating precision that the model does
not actually have. Recorded here as an honest "no clean match found"
rather than papered over.

| Symmetry | Group | Present in the model? |
|---|---|---|
| Relabeling the **k** alleles at one locus | **S<sub>k</sub>** | Yes, absent mutation bias or selection |
| Relabeling the **D** islands | **S<sub>d</sub>** | Yes, if all island sizes and migration rates are equal |
| Allele-frequency reflection (two-allele case, $k=2$) | $\mathbb{Z}_2 \cong S_2$ | Yes — the $k=2$ special case of the row above |
| Ambient rotation of the $k$-allele sphere embedding | $SO(k)$ | Only before the positive-orthant constraint is applied — broken by absorbing fixation states |
| Continuous phase rotation ($U(1)$, the original framing) | — | No — not the right ambient group in the first place; also broken by absorbing states even where it would apply |

**Bottom line (unchanged by this correction):** the finite island model is
best described as a mean-field (or, under stepping-stone migration,
lattice) Markov random field with a discrete label-permutation symmetry
group, $S_k \times S_d$ — a mean-field Potts-model analogy. What the
correction adds is *why* a rotation group shows up in the first place: the
multi-allele generalization of Wright's arcsine transform genuinely
embeds the model on a sphere with ambient continuous symmetry $SO(k)$,
not $U(1)$ — but the requirement that allele frequencies stay
non-negative restricts the model to one small patch of that sphere, and
the absorbing boundary of that patch (fixation) breaks $SO(k)$ down to
the same finite permutation group, $S_k$, identified all along. The
discrete transform the corrected question pointed to is precisely this
boundary-induced symmetry breaking, not a separate phenomenon.

---

## Appendix B: the same idea runs modern ancestry software

*Optional. This appendix assumes some prior exposure to statistics
(mixture models, maximum likelihood) or machine learning. Skipping it costs
nothing needed for the rest of this document.*

A second natural question: does the finite island model's shape — discrete
groups, each with its own allele-frequency vector, individuals drawn from
those group frequencies — relate to standard statistical clustering
techniques? Yes, directly, and it is one of the best-known cross-links in
this field: the allele-frequency table this document treats as *simulated
ground truth* (Part 3) is exactly the *unknown parameter* that a
well-known family of population-genetics tools tries to *infer* from real
genetic data.

**The direct connection: STRUCTURE and its descendants.** Pritchard JK,
Stephens M, Donnelly P, "Inference of population structure using
multilocus genotype data." *Genetics* 155(2):945–59, 2000. PMID: 10835412.
DOI:
[10.1093/genetics/155.2.945](https://doi.org/10.1093/genetics/155.2.945).
This paper introduced STRUCTURE, the software that popularized model-based
genetic clustering, and its generative model is essentially the *inverse*
of the finite island model:

- Assume $K$ discrete, unobserved source populations, each with its own
  allele-frequency vector at every genetic location — exactly the
  frequency table from Part 3.
- Each observed individual's genetic data is generated by (softly)
  assigning it to one or a mixture of the $K$ populations, then sampling
  alleles from that population's frequency vector.
- Statistical inference — originally by Markov-chain Monte Carlo sampling
  — recovers *both* the per-population allele frequencies *and* each
  individual's population membership from the observed data alone, with
  nothing about population membership given in advance.

Faster descendants followed, most notably Alexander DH, Novembre J, Lange
K, "Fast model-based estimation of ancestry in unrelated individuals."
*Genome Research* 19(9):1655–64, 2009. PMID: 19648217. DOI:
[10.1101/gr.094052.109](https://doi.org/10.1101/gr.094052.109). This paper
introduced ADMIXTURE, which fits the same underlying model as STRUCTURE by
direct likelihood maximization instead of random sampling, trading some
flexibility for large speed gains on genome-scale data sets.

**The same object, worn two different ways:**

| | Finite island model | STRUCTURE-family clustering |
|---|---|---|
| Direction | Forward: given demography, simulate the frequency table forward through time | Inverse: given observed genetic data, infer the frequency table (and each individual's group) that could have produced it |
| Role of the frequency table | Known ground truth, produced by migration and drift | Unknown parameter, estimated from data |
| Groups | Physical demes, connected by an explicit migration process | Statistical clusters — a fixed mixture, with no migration process linking them |

This connection is directly useful: **a validated forward simulator with a
known frequency table is exactly the tool needed to test a clustering
method's ability to recover the true population structure** — generate
data under a known finite island model, feed it to STRUCTURE or ADMIXTURE,
and check whether the inferred groups and frequencies match the known
answer.

**The broader family.** STRUCTURE's underlying model is a finite mixture
model — the same statistical structure as a Gaussian mixture model, but
with categorical rather than continuous outcomes. This is also, as is
often noted, structurally identical to Latent Dirichlet Allocation, the
widely used topic-modeling technique from natural-language processing:
"documents" play the role of "individuals," "topics" play the role of
"source populations," and "words" play the role of "alleles." (STRUCTURE,
published in 2000, predates the LDA paper by Blei, Ng & Jordan, published
in 2003, by a few years.) Stochastic block models, used for clustering
nodes in a network, follow the same shape again: discrete latent group
labels, group-conditional outcome probabilities, and graph-based coupling
— here, the finite island model's migration graph plays the same role as
an observed network's edges. One practical consequence shows up across all
of these: because permuting the $K$ group labels leaves the underlying
mathematics completely unchanged, iterative fitting procedures can — and
in practice sometimes do — swap label identities partway through a run, a
well-known headache in this literature usually called *label switching*,
and a direct consequence of the same discrete symmetry discussed in
Appendix A.

---

## Metadata

```text
generator-name: Claude Code
generator-version: Claude Sonnet 5
generator-model-token: claude-sonnet-5
generator-provider: Anthropic
generation-date: 2026-08-10
generator-responsibility: other
```
