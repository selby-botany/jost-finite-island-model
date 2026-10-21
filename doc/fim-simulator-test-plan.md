<!-- markdownlint-disable MD013 -->

# Finite island model simulator: test plan

- [Finite island model simulator: test plan](#finite-island-model-simulator-test-plan)
  - [Who this document is for](#who-this-document-is-for)
  - [1. Validation scope](#1-validation-scope)
  - [2. Four independent kinds of evidence](#2-four-independent-kinds-of-evidence)
    - [2.1 Algebraic and reference-value checks](#21-algebraic-and-reference-value-checks)
    - [2.2 Population-genetics theory](#22-population-genetics-theory)
    - [2.3 Literature comparisons](#23-literature-comparisons)
    - [2.4 Independent software and recursions](#24-independent-software-and-recursions)
  - [3. Testing the actual product, not just the mathematics](#3-testing-the-actual-product-not-just-the-mathematics)
  - [4. Reproducibility and tolerance policy](#4-reproducibility-and-tolerance-policy)
  - [5. Where to look for more detail](#5-where-to-look-for-more-detail)
  - [Appendix A: Open issues](#appendix-a-open-issues)
  - [Appendix B: Testing opportunities considered and deferred](#appendix-b-testing-opportunities-considered-and-deferred)
  - [Appendix C: Citations and references](#appendix-c-citations-and-references)

## Who this document is for

This document summarizes scientific validation, functional coverage, and
known limits for researchers and reviewers. Implementation details and
test locations are in the
[detailed test plan](fim-simulator-detailed-test-plan.md).

## 1. Validation scope

The simulator generates allele-frequency trajectories under the
[finite island model](finite-island-model-introduction.md) and computes
population-differentiation statistics from those trajectories. Validation
covers both the generative model and the statistics.

Genetic drift is stochastic: independent seeds produce different
trajectories, while a fixed seed and compatible numerical environment
reproduce a trajectory. Statistical checks compare ensembles with
theoretical or published expectations using specified tolerances.
The four categories below provide complementary evidence; their scope
and limitations differ.

## 2. Four independent kinds of evidence

### 2.1 Algebraic and reference-value checks

Property tests check algebraic identities over generated valid frequency
tables. For example, the replication principle doubles the ratio of
pooled to within-group Hill diversity when two equally sized, equally
diverse, completely disjoint groups are pooled.

Golden-value tests compare statistics with independently worked
calculations: demes fixed for two alleles in different proportions,
identical distributions, partially shared supports, and disjoint supports.
Definitions and calculations are in the
[differentiation-measures guide](jost-differentiation-measures.md).

### 2.2 Population-genetics theory

Closed-form predictions (`fim.statistics.equilibrium_g_st`,
`equilibrium_d`, and related functions) describe equilibrium averages
under their stated assumptions. Tests compare these approximations
with an exact identity recursion for the Migrate → Drift → Mutate
update order, then compare replicated engine output with that recursion
inside a calibrated tolerance (§4). The distinction between an
approximation and the finite-population recursion is retained in each
comparison. Part VI of the differentiation-measures guide gives the
formulas; [migration conventions](migration-conventions.md) specifies
the source-pool and identity-sampling conventions.

### 2.3 Literature comparisons

Literature checks cover published values, equations, and directional
claims. They do not all exercise the engine: some compare formulas or
test-only recursions. Seven sources are summarized below:

- **Jost (correspondence, via Dear-Nolan)** — two scenarios (a low-
  migration case, a high-migration case), each with published values for
  both of this project's two headline differentiation statistics,
  `G_ST` and `D`, compared with replicated engine output using tolerance
  bands. The source is correspondence, not a peer-reviewed publication.
- **Crow & Aoki (1984)** — a scenario where demes are arranged on a
  grid and only exchange migrants with their four nearest neighbors (a
  "stepping-stone" arrangement), rather than every deme mixing equally
  with every other deme. This is the one case in this section with an
  open discrepancy — see Appendix A.
- **Chao, Chiu, Jost, Sherwin & Rollins (2015)** — an additional
  family of differentiation statistics (based on Shannon entropy, a
  measure from information theory, rather than the more classical
  heterozygosity-based measures used everywhere else in this project).
  This simulator reproduces the paper's theoretical predictions for
  that statistic family directly.
- **Nei (1973)** — the paper that defined the
  `G_ST` statistic. Three further measures from the paper are checked
  directly against its algebra.
- **Ryman & Leimar (2008)** — two further trajectory-located published
  numbers (most sources above give a single published value, not a
  curve): their Equation 4 reproduces all four heterozygosities their
  Figure 1 discussion quotes, and their Equation 5 reproduces the
  published mutation-free `G_ST(t)` landmark table, including "at
  `t=100`, `G_ST` is close to 0.04." See also §2.4, below, for this
  paper's independently derived recursion.
- **Whitlock (1992)** — identity-recovery rate after a disturbance.
  Tests establish the infinite-island, mutation-free formula as an
  algebraic special case of the test-suite identity recursion.
- **Kimura & Weiss (1964)**, cited via Whitlock & McCauley (1999) — a
  directional claim (stepping-stone arrangements should show *at least
  as much* genetic differentiation as an equally-mixed arrangement,
  never less, for the same migration rate), confirmed across seven
  varied scenarios.

Full citations are in Appendix C.

### 2.4 Independent software and recursions

`dev/bin/compare-against-hierfstat` compares this simulator with
**hierfstat**, an independently maintained R package, under matched
scenarios. The retained comparison places both simulators' long-run
average `G_ST` and `D` within a few percent of the closed-form prediction.
This is a diagnostic comparison, not a calibrated pass/fail test
(Appendix B).

**Ryman & Leimar (2008)** supplies a separate analytic check on the
test-suite identity recursion (`_iterate_identities`). After mapping
migration and identity-sampling conventions, the implementation agrees
with their Equations 2 and 3 to floating-point precision over a
multi-thousand-generation trajectory. This validates a recursion,
not an independent stochastic simulator.

## 3. Testing the actual product, not just the mathematics

Functional tests cover CLI artifact production, re-analysis of saved
trajectories, manifest replay, configuration errors, and desktop
interactions. These test the application interfaces separately from the
scientific comparisons. See §5 for the detailed plans.

## 4. Reproducibility and tolerance policy

Tests must produce the same result for the same commit and supported
environment. Random changes in outcome are test defects.

Statistical tests specify sample counts and tolerance bands before
selecting fixed seeds. Bands use analytic sampling errors where available;
equilibrium tests also use independently characterized replicate spreads.
Seeds are not reselected to obtain a passing result. The
[detailed plan](fim-simulator-detailed-test-plan.md#71-deriving-a-tolerance-band-before-choosing-a-seed)
and [calibration evidence](statistical-calibration-evidence.md) document
the method. Fixed seeds make a check reproducible; they do not remove
sampling uncertainty or turn an approximate band into a proof.

## 5. Where to look for more detail

- [Model introduction](finite-island-model-introduction.md).
- [Differentiation definitions](jost-differentiation-measures.md).
- [Test implementation reference](fim-simulator-detailed-test-plan.md).
- [Functional API](fim-simulator-functional-api.md).
- [Desktop test plan](fim-gui-test-plan.md).
- [Usage](usage.md) and [configuration](configuration.md).

## Appendix A: Open issues

Two validation gaps remain:

**The Crow & Aoki (1984) stepping-stone comparison remains unresolved.**
Crow & Aoki's Table 1 reports $G_{ST}=0.172$ for their stepping-stone
(nearest-neighbor-migration) scenario.
The simulator and exact identity recursion give approximately
$G_{ST}$ between 0.27 and 0.32 under the documented migration-rate interpretation.
Agreement between the stochastic simulation and deterministic recursion
indicates that sampling noise does not explain the discrepancy.
The paper attributes the stepping-stone values to unpublished
"numerical calculations" without specifying how migrant counts are
divided among four neighbors. An alternative scalar-rate interpretation
near $m=0.12$ reproduces 0.172 more closely. The paper does not settle
which reading the authors used. The implementation retains its documented
migration convention rather than fitting the rate to the published result.
Resolving the discrepancy requires additional evidence about the
paper's numerical method.

One tested hypothesis is a redrawn-migrant pool that includes the home
deme itself, versus this project's pool of the four neighbors only
(see [the migration-conventions reference](migration-conventions.md)
for the full mapping). This convention does not explain the discrepancy:
applying the "pool includes self" mapping moves the exact prediction
*further* from `0.172` (to about `0.374`), not closer
(`test_crow_aoki_torus_under_the_papers_own_migration_convention`,
`test/validation/test_simulator_equilibrium.py`). The `m ~= 0.12`
scalar-rate interpretation remains closer to the published value.

**Engine-level identity-recovery speed is not tested against Whitlock
(1992).** The algebraic check in §2.3 validates the recursion's limiting
case, not the stochastic engine's recovery time. A replicated test would
need a recovery-time estimator and a calibrated tolerance for noisy
generation counts; neither is part of current coverage.

## Appendix B: Testing opportunities considered and deferred

The following checks are outside current validation coverage:

- **Starling measurements (Chao et al. 2015).** Three published field-data
  values are bias-corrected small-sample estimates, not population-level
  theoretical predictions. Reproducing them requires sample estimators
  outside this simulator's current statistic definitions.
- **mmod reference data.** The R package's `D` fixture also uses a
  small-sample estimator rather than the population quantity computed
  here. It is a potential fixture for a sample-estimation extension,
  not an oracle for the current population formulas.
- **A second simulator (quantiNemo 2).** An actively maintained,
  more elaborate C++ simulator exists in this same research tradition
  (also discussed in `doc/finite-island-model-introduction.md`); a
  comparison against it, in the same spirit as the hierfstat comparison
  in §2.4, is a plausible future addition, not yet attempted.
- **A calibrated, formal version of the hierfstat comparison.** The
  current cross-check (§2.4) reports a comparison; it does not yet
  assert a pass/fail margin the way the literature-scenario checks in
  §2.3 do, because the margin of error appropriate for comparing two
  independent stochastic simulators against each other has not yet been
  calibrated under the method in §4.
- **Extending Nei's (1973) formulas to nested population structure**
  (colonies grouped within subpopulations grouped within a total
  population, rather than this project's current flat structure of
  independent demes). This would require a change to how this project
  represents a population's own structure, a larger undertaking than
  adding a formula.
- **Time-varying demography in Whitlock (1992)** — how genetic
  differentiation behaves when population size, migration rate, or
  extinction/recolonization themselves fluctuate through time, rather
  than staying fixed for the length of a run. This project's simulator
  has no mechanism yet for any of those to vary within a single run;
  testing these results requires a time-varying model extension.
- **A real-simulator (not just closed-form) version of the migration/
  mutation monotonicity checks.** §2.2 confirms, both from the pure
  theory and from an exact mathematical recursion, that more migration
  should reduce genetic differentiation and more mutation should
  increase it (or the reverse, depending on which statistic is being
  read). A replicated, statistically calibrated version against the
  stochastic engine is not part of current coverage.

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
  below; the comparison uses that secondary account.)
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
  Whitlock & McCauley 1999's account.)
- See [the differentiation guide](jost-differentiation-measures.md), Appendix D, for the
  full bibliography behind this project's core differentiation
  statistics (Jost's `D`, `G_ST`, and the wider family), and
  [the model introduction](finite-island-model-introduction.md), §4, for independent-software
  citations (quantiNemo 2, hierfstat).
