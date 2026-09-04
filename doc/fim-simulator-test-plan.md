<!-- markdownlint-disable MD013 -->

# Finite island model simulator: test plan

- [Finite island model simulator: test plan](#finite-island-model-simulator-test-plan)
  - [Who this document is for](#who-this-document-is-for)
  - [1. The question this whole document answers](#1-the-question-this-whole-document-answers)
  - [2. Four independent kinds of evidence](#2-four-independent-kinds-of-evidence)
    - [2.1 Does the arithmetic hang together on its own?](#21-does-the-arithmetic-hang-together-on-its-own)
    - [2.2 Does it match textbook population-genetics theory?](#22-does-it-match-textbook-population-genetics-theory)
    - [2.3 Does it reproduce real, published worked examples?](#23-does-it-reproduce-real-published-worked-examples)
    - [2.4 Does it agree with a completely separate program?](#24-does-it-agree-with-a-completely-separate-program)
  - [3. Testing the actual product, not just the mathematics](#3-testing-the-actual-product-not-just-the-mathematics)
  - [4. The rule that makes all of this trustworthy](#4-the-rule-that-makes-all-of-this-trustworthy)
  - [5. Where to look for more detail](#5-where-to-look-for-more-detail)
  - [Appendix A: Open issues](#appendix-a-open-issues)
  - [Appendix B: Testing opportunities considered and deferred](#appendix-b-testing-opportunities-considered-and-deferred)
  - [Appendix C: Citations and references](#appendix-c-citations-and-references)
  - [Metadata](#metadata)

## Who this document is for

Anyone who wants to know whether this simulator's numbers can be
trusted — a botanist deciding whether to cite a result from it, a
collaborator reviewing the method, a reader of a paper that used it. No
programming background is assumed. Where a technical detail is
unavoidable, it is explained the first time it appears.

If a question about *how* a particular check is implemented remains
after reading this, the technical companion, `doc/
fim-simulator-detailed-test-plan.md`, has that level of detail; it is
written for a developer, not a botanist, and is not required reading to
trust the conclusions here.

## 1. The question this whole document answers

This program simulates a textbook model from population genetics — the
*finite island model* (see `doc/finite-island-model-introduction.md`
for the model itself, in plain language) — and then computes several
different numbers describing how genetically different a set of
populations have become from each other. Those numbers only mean
anything if the simulation and the arithmetic behind them are actually
correct.

That is a harder question to answer here than for most software,
because the thing being tested is *random on purpose*. A population
genetics simulation models genetic drift — the real, unavoidable
randomness of which individuals happen to reproduce each generation —
so running the same scenario twice never gives identical numbers, only
numbers that should land in the same *neighborhood*. "Did we get the
right answer" therefore cannot mean "did we get the same number as
before"; it has to mean something more careful: does the simulator's
output land where independent mathematics, independent literature, and
independent software all agree it should.

This document is the answer to that question, organized as four
independent lines of evidence, none of which alone would be convincing,
but which together are.

## 2. Four independent kinds of evidence

### 2.1 Does the arithmetic hang together on its own?

Before comparing the simulator to anything outside itself, the
underlying mathematics is checked against its own logical
consequences — the kind of check a mathematician calls verifying an
identity. For example: pooling two genetically identical, equally sized
groups of demes together should exactly double a certain diversity
measure, because that is what the formula defining it *means*; if the
software reports something else, the formula was implemented wrong,
regardless of what any real population would actually do. These checks
run thousands of times against randomly generated, valid inputs (not
just one or two examples chosen by hand), so a formula that works for
the developer's own test case but breaks on an input nobody thought to
try by hand still gets caught.

This layer also includes checks with a hand-worked, known correct
answer — a handful of textbook scenarios (ten demes, all fixed for the
same two alleles in different proportions; two demes sharing half their
genetic material and not the other half; and so on) where the right
answer for every reported statistic was worked out independently, on
paper, before ever running the code, then compared against what the
software actually produces.

### 2.2 Does it match textbook population-genetics theory?

Population genetics has well-established mathematical predictions for
what should happen, on average, once a set of populations connected by
migration has been evolving for a long time — an "equilibrium." Given
the population size, migration rate, mutation rate, and number of
demes, theory predicts what the genetic-differentiation statistics
should settle to. This simulator's own formulas for that prediction
(`fim.statistics.equilibrium_g_st`/`equilibrium_d` and their
relatives — see `doc/jost-differentiation-measures.md` Part VI for the
full derivation) are checked directly against an *exact* mathematical
recursion built independently, from first principles, for this
project's own particular generation-by-generation update rule
(migration, then mutation, then genetic drift, each generation) — not
against the simulator's own random output, a pure-mathematics check
that either the two agree or they do not. Only once that is settled is
the real, random simulator run and its own long-run average compared
against that same prediction, inside a statistically honest margin
(§4) — this is where the simulator itself, not just its supporting
formulas, first gets checked against theory.

### 2.3 Does it reproduce real, published worked examples?

The strongest, most literal test available for a scientific simulator:
plug in the exact parameters a published paper used, and check whether
this simulator's own output lands where that paper reported. Seven
independent sources are used, spanning six decades of the population-
genetics literature and several different research traditions,
specifically so that no single author's own conventions or assumptions
could be silently baked into agreement:

- **Jost (correspondence, via Dear-Nolan)** — two scenarios (a low-
  migration case, a high-migration case), each with published values for
  both of this project's two headline differentiation statistics,
  `G_ST` and `D`. This simulator reproduces both, for both scenarios.
- **Crow & Aoki (1984)** — a scenario where demes are arranged on a
  grid and only exchange migrants with their four nearest neighbors (a
  "stepping-stone" arrangement), rather than every deme mixing equally
  with every other deme. This is the one case in this section with an
  open discrepancy — see Appendix A.
- **Chao, Chiu, Jost, Sherwin & Rollins (2015)** — a genuinely different
  family of differentiation statistic (based on Shannon entropy, a
  measure from information theory, rather than the more classical
  heterozygosity-based measures used everywhere else in this project).
  This simulator reproduces the paper's own theoretical predictions for
  that statistic family directly.
- **Nei (1973)** — the paper that originally defined this project's own
  `G_ST` statistic. Three further measures from that same paper, not
  previously implemented, were added and checked directly against its
  own algebra.
- **Ryman & Leimar (2008)** — two further trajectory-located published
  numbers (most sources above give a single published value, not a
  curve): their Equation 4 reproduces all four heterozygosities their
  own Figure 1 discussion quotes, and their Equation 5 reproduces their
  own published mutation-free `G_ST(t)` landmark table, including "at
  `t=100`, `G_ST` is close to 0.04." See also §2.4, below, for this
  same paper's own independently derived recursion.
- **Whitlock (1992)** — a different kind of question altogether: not
  "what value does a statistic settle to," but "how many generations
  does it take to get there" after a disturbance. Shown to be an exact
  mathematical special case of this project's own existing internal
  machinery, not merely a plausible-looking approximation.
- **Kimura & Weiss (1964)**, cited via Whitlock & McCauley (1999) — a
  directional claim (stepping-stone arrangements should show *at least
  as much* genetic differentiation as an equally-mixed arrangement,
  never less, for the same migration rate), confirmed across seven
  varied scenarios.

Full citations for all six are in Appendix C.

### 2.4 Does it agree with a completely separate program?

Every check above still ultimately compares this project's own code
against either its own mathematics or a number a published paper
reported once. A genuinely independent check needs a second, separately
written computer program simulating the same underlying process — so
that two different programmers, working from the same textbook model
but writing every line of code independently, land on the same answer.

This project has one such check: **hierfstat**, an actively maintained R
package from a different research group (Goudet's group, whose own
tools are also discussed in `doc/finite-island-model-introduction.md`
§4). Run through a small, purpose-built comparison tool
(`dev/bin/compare-against-hierfstat`), hierfstat independently simulates
the same scenario this project's own theory already predicts a value
for, and the two programs' own results are compared side by side. The
first such comparison found this simulator's own long-run average
`G_ST` and `D`, hierfstat's own independently simulated values, and the
closed-form theoretical prediction all within a few percent of one
another — three independent sources of evidence for the very same
number, agreeing.

A second, differently shaped check exists for this project's own
gene-identity recursion specifically (`_iterate_identities` in the test
suite — see [the migration-conventions reference](migration-conventions.md)
for what it computes and why): **Ryman & Leimar (2008)**'s own
Equations 2 and 3 are an independently published derivation of the
identical recursion, by different authors reasoning from different
premises, not a second program simulating the same process. Once their
own migration and identity-sampling conventions are mapped onto this
project's (the same mapping the migration-conventions reference
documents), the two recursions agree to `float` noise over a
multi-thousand-generation trajectory — not merely at one equilibrium
point, and not merely a restatement of this project's own derivation
under different variable names.

## 3. Testing the actual product, not just the mathematics

Everything above concerns whether the underlying computation is
*correct*. A separate, equally necessary question is whether the
finished program actually does what a user expects when they run it:
does `fim run` on a small configuration produce exactly the files it is
supposed to (the trajectory, the summary report, the scatter plot);
does re-opening a previous run and re-analyzing it give the same answer
the original run reported; does the desktop application's own screens
respond correctly when a user clicks through them. These are covered by
a large, separate suite of functional and desktop-application tests —
not repeated here in detail, since they are a different kind of
question from "is the science right," but summarized, with pointers to
their own documentation, in §5 below.

## 4. The rule that makes all of this trustworthy

None of the evidence above means anything if a test's own outcome can
change from one run to the next without the underlying code changing —
a test like that reports a fact about the moment it happened to run,
not about whether the software is correct. This project treats a test
whose result can vary at random as a defect of exactly the same
seriousness as a test that checks the wrong thing, never as an
acceptable inconvenience of working with something inherently random.

That discipline is what makes the "does it land in the right
neighborhood" checks in §2.2-§2.4 meaningful rather than a coin flip
dressed up as science: before a single random simulation is ever run,
the *width* of the acceptable neighborhood is worked out mathematically
from how many independent repetitions ("replicates") are being
averaged together — the same way a poll reports a margin of error
calculated from its sample size, before anyone looks at what the poll
actually found. The specific number used to seed the random generator
is fixed only *after* that margin is settled, and is never changed
afterward to make a result look better. This means a run of these tests
always gives the identical result, every time, on the identical version
of the software — the randomness is real, but it is pinned down and
accounted for, not swept under the rug.

## 5. Where to look for more detail

- **The model itself, no computing background assumed**: `doc/
  finite-island-model-introduction.md`.
- **What each differentiation statistic actually measures, and why more
  than one exists**: `doc/jost-differentiation-measures.md`.
- **Exactly how each check above is implemented, for a developer**:
  `doc/fim-simulator-detailed-test-plan.md`.
- **Exactly what functions/classes a test (or any other outside code)
  is allowed to call**: `doc/fim-simulator-functional-api.md`.
- **The desktop application's own test coverage**: `doc/
  fim-gui-test-plan.md`.
- **How to configure and run the simulator**: `doc/usage.md` and `doc/
  configuration.md`.

## Appendix A: Open issues

Two genuine, currently unresolved gaps, reported plainly rather than
smoothed over:

**The Crow & Aoki (1984) stepping-stone scenario does not reproduce the
paper's own published number.** Crow & Aoki's Table 1 reports `G_ST =
0.172` for their stepping-stone (nearest-neighbor-migration) scenario.
This simulator, run at this project's own best-supported reading of the
paper's migration rate, instead lands consistently around `G_ST ~=
0.27-0.32` — confirmed two different ways (a real, long simulation run,
and an independent exact mathematical calculation with no
randomness at all), so this is not sampling noise. The most likely
explanation: Crow & Aoki's own paper states plainly that their
stepping-stone numbers came from unpublished "numerical calculations,"
with no formula given for exactly how a deme's stated migrant count is
divided among its four neighbors — an ordinary and reasonable reading
of "the same number of migrants" turns out not to be the only
defensible one, and a different, equally defensible reading of it does
reproduce `0.172` almost exactly. Nothing in the paper itself settles
which reading the authors actually used, and a change was deliberately
not made solely to force the numbers to match, since that would only
be curve-fitting rather than a real correction. A reader with intimate
knowledge of this specific literature, or access to the authors' own
working notes, may be able to resolve this; anyone who can is warmly
invited to.

One specific hypothesis has since been checked and ruled out: the same
migration-convention mismatch that explained an analogous gap against
Ryman & Leimar (2008) — a redrawn-migrant pool that includes the home
deme itself, versus this project's own pool of the four neighbors only
(see [the migration-conventions reference](migration-conventions.md)
for the full mapping) — does not explain this one. Applying that same
"pool includes self"
mapping to the torus moves the recursion's own exact prediction
*further* from `0.172` (to about `0.374`), not closer
(`test_crow_aoki_torus_under_the_papers_own_migration_convention`,
`test/validation/test_simulator_equilibrium.py`). The `m ~= 0.12`
scalar-rate reading two paragraphs above remains the closer lead.

**No test yet exists checking that the real simulator's own convergence
speed matches Whitlock's (1992) theoretical prediction of it.** §2.3
above already confirms Whitlock's formula is an exact mathematical
special case of this project's own internal machinery — a rigorous, but
purely mathematical, check. Confirming the same thing against the
actual random simulator (start it away from equilibrium, watch how many
generations it actually takes to get back close, compare to the
formula's prediction) is a materially harder kind of statistical test
than every other check in this document, since it involves measuring a
*number of generations* from noisy data rather than a value at one
fixed point in time — this project's existing methodology for
establishing an honest margin of error (§4) has not yet been extended
to that kind of measurement, and doing so properly is real, additional
work, not yet undertaken.

## Appendix B: Testing opportunities considered and deferred

Every item below was actively investigated this project's own
development history and set aside deliberately, each for a stated
reason — not overlooked:

- **Reproducing three real starling-genetics measurements exactly**
  (Chao et al. 2015's own field data). These specific published numbers
  are not clean theoretical predictions but the paper's own
  bias-corrected statistical *estimates* from a small real genetic
  sample — reproducing them exactly would mean building an entirely
  separate feature (a small-sample statistical estimator, a genuinely
  different kind of tool from anything this simulator currently does),
  not a quick test addition.
- **A second R package's own test data (mmod).** A different R package
  built specifically around this project's own `D` statistic has a
  small, genuinely well-documented test case with known expected
  values. Investigating it directly, however, found that its expected
  values are the same kind of small-sample statistical *estimate*
  described above, not the exact mathematical quantity this project
  computes from a fully known population — a subtly different number
  that happens to share the same name. If a small-sample estimator
  feature is ever built for this project, this same small test case
  would make an excellent starting example for it.
- **A second, heavier simulator (quantiNemo 2).** A actively maintained,
  more elaborate C++ simulator exists in this same research tradition
  (also discussed in `doc/finite-island-model-introduction.md`); a
  comparison against it, in the same spirit as the hierfstat comparison
  in §2.4, is a plausible future addition, not yet attempted.
- **A calibrated, formal version of the hierfstat comparison.** The
  current cross-check (§2.4) reports a comparison; it does not yet
  assert a pass/fail margin the way the literature-scenario checks in
  §2.3 do, because the margin of error appropriate for comparing two
  independent stochastic simulators against each other has not yet been
  worked out with the same rigor §4 requires elsewhere.
- **Extending Nei's (1973) formulas to nested population structure**
  (colonies grouped within subpopulations grouped within a total
  population, rather than this project's current flat structure of
  independent demes). This would require a change to how this project
  represents a population's own structure, a larger undertaking than
  adding a formula.
- **The rest of Whitlock's (1992) own paper** — how genetic
  differentiation behaves when population size, migration rate, or
  extinction/recolonization themselves fluctuate through time, rather
  than staying fixed for the length of a run. This project's simulator
  has no mechanism yet for any of those to vary within a single run;
  building that mechanism would be a substantial feature in its own
  right.
- **A real-simulator (not just closed-form) version of the migration/
  mutation monotonicity checks.** §2.2 confirms, both from the pure
  theory and from an exact mathematical recursion, that more migration
  should reduce genetic differentiation and more mutation should
  increase it (or the reverse, depending on which statistic is being
  read) — a real, replicated, statistically calibrated version of the
  same claim against the actual random simulator, matching this
  project's existing rigor for its other literature-scenario checks,
  remains a further, deliberately sequenced addition.

## Appendix C: Citations and references

- Crow JF, Aoki K (1984). Group selection for a polygenic behavioral
  trait: estimating the degree of population subdivision. *Proceedings
  of the National Academy of Sciences* 81(19):6073-6077.
- Chao A, Chiu C-H, Jost L, Sherwin WB, Rollins LA (2015). Expected
  Shannon entropy and Shannon differentiation between subpopulations
  for neutral genes under the finite island model. *PLoS ONE*
  10(6):e0125471.
- Kimura M, Weiss GH (1964). The stepping stone model of population
  structure and the decrease of genetic correlation with distance.
  *Genetics* 49(4):561-576. (Cited here via Whitlock & McCauley 1999,
  below; not independently re-verified against the 1964 primary text
  this session.)
- Nei M (1973). Analysis of gene diversity in subdivided populations.
  *Proceedings of the National Academy of Sciences* 70(12):3321-3323.
- Ryman N, Leimar O (2008). Effect of mutation on genetic
  differentiation among nonequilibrium populations. *Evolution*
  62(9):2250-2259. DOI
  [10.1111/j.1558-5646.2008.00453.x](https://doi.org/10.1111/j.1558-5646.2008.00453.x).
- Whitlock MC (1992). Temporal fluctuations in demographic parameters
  and the genetic variance among populations. *Evolution*
  46(3):608-615.
- Whitlock MC, McCauley DE (1999). Indirect measures of gene flow and
  migration: `F_ST` ≠ 1/(4Nm+1). *Heredity* 82(2):117-125.
- Wright S (1931). Evolution in Mendelian populations. *Genetics*
  16(2):97-159. (The origin of the island model itself; cited here via
  Whitlock & McCauley 1999's own account of it, not independently
  re-verified against the 1931 primary text this session.)
- See `doc/jost-differentiation-measures.md`'s own Appendix D for the
  full bibliography behind this project's core differentiation
  statistics (Jost's `D`, `G_ST`, and the wider family), and `doc/
  finite-island-model-introduction.md` §4 for the independent-software
  citations (quantiNemo 2, hierfstat).

---

## Metadata

```text
generator-name: Claude Code
generator-version: Claude Sonnet 5
generator-model-token: claude-sonnet-5
generator-provider: Anthropic
generation-date: 2026-08-30
generator-responsibility: primary
```
