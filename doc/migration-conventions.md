# Migration and identity conventions: mapping `fim` to the literature

## Contents

- [Who this document is for](#who-this-document-is-for)
- [Notation and comparison procedure](#notation-and-comparison-procedure)
- [1. `fim`'s four conventions](#1-fims-four-conventions)
- [2. Per-paper mappings](#2-per-paper-mappings)
  - [2.1 Ryman & Leimar (2008)](#21-ryman--leimar-2008)
  - [2.2 Crow & Aoki (1984)](#22-crow--aoki-1984)
- [3. Why this document exists](#3-why-this-document-exists)
- [Metadata](#metadata)

Mathematical symbols and operations use $\TeX$; literal configuration
keys, Python names, and report fields use backticks. For example,
$m$ is a migration fraction, while `m` is the configuration key
that holds it. Each displayed equation has one unique tag beginning
with $\text{M}$ for this migration-conventions reference.

## Who this document is for

Anyone comparing `fim`'s own output — a simulated run, or the exact
gene-identity recursion implemented in
[`test_simulator_equilibrium.py`](../test/validation/test_simulator_equilibrium.py) —
against a published population-genetics paper. Every
comparison of this kind needs a mapping between two independently
chosen sets of conventions before the numbers mean the same thing; get
the mapping wrong, or skip it, and an entirely correct simulator and an
entirely correct paper can disagree substantially while both sides are
right (§2.1). This document derives that mapping once rather than
leaving it implicit in each test — the arrangement that let the
[Crow & Aoki discrepancy](fim-simulator-test-plan.md#appendix-a-open-issues)
go unexplained for as long as it did.

The aim is not to make a published number match at any cost. First
translate the paper's definitions into the simulator's definitions.
Then compare the resulting recursions or statistics. If a discrepancy
remains, report it as unresolved rather than tuning parameters until
the desired number appears.

## Notation and comparison procedure

Use one locus and a fixed generation while making each comparison.
Let $d$ be the number of demes, $N_k$ the number of individuals in
deme $k$, $c$ the number of gene copies per individual, and $n_k$
the number of copies at that locus:

```math
n_k=cN_k \tag{M.1}
```

For haploids, $c=1$; for diploids, $c=2$. This conversion is made
before using any formula stated in gene copies.
For example, 100 diploid individuals have $n=200$ gene copies at
one autosomal locus. Tracking another locus does not double $n$.

Write $p_k(a)$ for the frequency of allele $a$ in deme $k$.
The with-replacement within-deme identity is the probability that
two independent draws from that frequency distribution match:

```math
J^{\mathrm{wr}}_k=\sum_a p_k(a)^2 \tag{M.2}
```

The **$\mathrm{wr}$** superscript means with replacement: the two
draws may select the same physical copy. Write $J^{\mathrm{dist}}_k$
for the probability that two distinct copies match, sampled without
replacement. These are both valid identity probabilities, but they
are not interchangeable at finite population size.

For each published comparison, check the assumptions in this order:

1. Convert individuals to gene copies, if needed.
2. Match which demes and copy fractions contribute to a destination's
   parental pool.
3. Match whether identity draws permit selecting one physical copy
   twice.
4. Align the generation boundary and mutation step.
5. Only then compare recursions, trajectories, or statistics.

Changing only one convention at a time makes each correction
auditable. A fit at one equilibrium point is weaker evidence than
agreement across a trajectory, because distinct recursions can share
one fixed point.

## 1. `fim`'s four conventions

Four independent choices, each of which some cited paper makes
differently. None is inherently more correct; each formalizes
the same biology under a different convention.

### 1.1 Population unit

The configuration key `N` accepts individual counts and `ploidy`;
the engine uses $n_k=cN_k$ gene copies per deme and locus.
See the [configuration reference](configuration.md#n).
A paper that gives an individual count must be converted before
substituting its value into a gene-copy equation. This factor is
not an extra factor to apply when the paper already defines its
$N$ as a copy count.

For example, a paper's 100 diploid individuals and `fim`'s
`N: 200, ploidy: haploid` represent the same 200 copies at a locus.
By contrast, `N: 100, ploidy: diploid` also represents 200 copies.
Comparing the bare numbers 100 and 200 without their units would
make the drift terms differ by a factor of two.

### 1.2 Migration source

In the equal-size scalar island model, `fim` retains a fraction
$1-m_{\mathrm{fim}}$ locally and distributes its incoming fraction
equally among the other $d-1$ demes:

```math
M^{\mathrm{fim}}_{kj}=
\begin{cases}
1-m_{\mathrm{fim}},&j=k,\\
m_{\mathrm{fim}}/(d-1),&j\ne k.
\end{cases} \tag{M.3}
```

$M_{kj}$ is the share of destination $k$'s parental pool supplied
by source deme $j$. Every row sums to one: the diagonal supplies
$1-m_{\mathrm{fim}}$, and the other $d-1$ entries sum to
$m_{\mathrm{fim}}$.

Some papers instead designate fraction $m_{\mathrm{paper}}$ for
redrawing from a pool of all $d$ demes, including home:

```math
M^{\mathrm{paper}}_{kj}
=(1-m_{\mathrm{paper}})\mathbf 1_{\{j=k\}}
+\frac{m_{\mathrm{paper}}}{d} \tag{M.4}
```

The indicator $\mathbf 1_{\{j=k\}}$ is one for the home source and
zero otherwise. The home weight is therefore
$1-m_{\mathrm{paper}}+m_{\mathrm{paper}}/d$; every other source
has weight $m_{\mathrm{paper}}/d$. Equate an off-diagonal source
weight in (M.3) and (M.4):

```math
\frac{m_{\mathrm{fim}}}{d-1}
=\frac{m_{\mathrm{paper}}}{d} \tag{M.5}
```

Solving for either rate gives the conversion:

```math
m_{\mathrm{paper}}=m_{\mathrm{fim}}\frac d{d-1},
\qquad
m_{\mathrm{fim}}=m_{\mathrm{paper}}\frac{d-1}{d} \tag{M.6}
```

This conversion equates the **complete matrix row**, not just
the word "migration." For $d=4$ and $m_{\mathrm{fim}}=0.06$,
the paper's equivalent redraw rate is $0.08$.
The paper's home weight is $0.92$ and each other weight $0.02$;
`fim`'s home weight is $0.94$ and each of three other weights
is $0.02$. The destination-1 row in each matrix is
$(0.94,0.02,0.02,0.02)$.

For unequal deme sizes, `fim`'s scalar all-other pool is
copy-size-weighted; for a general matrix, each row already gives
the full weights. Do not apply (M.6) to a spatial topology merely
because its migration parameter has the same name. See
the [migration definition in the design](fim-simulator-design.md#34-the-generation-update-pipeline)
and [configuration reference](configuration.md#m).

### 1.3 Identity sampling

Suppose a deme contains exactly $n$ copies and its observed
frequency of allele $a$ is $p(a)$. Two independent draws with
replacement match with probability:

```math
J^{\mathrm{wr}}=\sum_a p(a)^2 \tag{M.7}
```

There are $n^2$ ordered pairs of copy positions if repetition
is allowed. Of those, $n$ are self-pairs and always match.
The remaining $n(n-1)$ are ordered distinct-copy pairs;
their matching fraction is $J^{\mathrm{dist}}$. Thus:

```math
J^{\mathrm{wr}}=
\frac1n+\left(1-\frac1n\right)J^{\mathrm{dist}} \tag{M.8}
```

The first term is the probability of selecting the same physical
copy twice. The coefficient of $J^{\mathrm{dist}}$ is the chance
of selecting different copies. Solve for the paper's distinct-pair
identity:

```math
J^{\mathrm{dist}}=
\frac{nJ^{\mathrm{wr}}-1}{n-1},\qquad n>1 \tag{M.9}
```

For counts 7 and 3 among ten copies,
$J^{\mathrm{wr}}=(7/10)^2+(3/10)^2=0.58$.
There are seven self-pairs, plus $7\cdot6+3\cdot2=48$
matching ordered distinct pairs among $10\cdot9=90$:
$J^{\mathrm{dist}}=48/90=0.5333\ldots$.
Equation (M.9) gives the same result:
$(10\cdot0.58-1)/9=0.5333\ldots$.

Between-deme draws already refer to distinct physical copies
because they come from different demes, so their identity
probability requires no same-deme correction. When $n=1$,
there are no distinct pairs within the deme and (M.9) is
undefined; do not divide by zero or invent a value for that
sampling question.

### 1.4 Operator order and generation boundaries

`fim` advances a generation as Migrate → Drift → Mutate:
migration changes parental frequencies, drift draws whole copies,
and mutation independently replaces newly drawn labels.
The design document gives the
[generation pipeline](fim-simulator-design.md#34-the-generation-update-pipeline).
Ryman & Leimar write their corresponding cycle as
Drift → Migrate → Mutate. A different written starting point
does not automatically mean a different model: for a deterministic
linear migration map and the paired identity state, the two
cycle descriptions can be aligned by translating the state at
the generation boundary.

The relevant state translation is (M.8) for within-deme identity.
Between-deme identity is already a distinct-copy probability.
With the migration-rate conversion (M.6), the comparison in §2.1
shows the resulting recursions agree generation by generation.
Do not add an arbitrary one-generation lag after applying this
translation; that would compare different biological phases.
This equivalence is established for the specific symmetric,
equal-size, infinite-alleles recursions under discussion, not for
any possible pair of operators that happen to be reordered.

## 2. Per-paper mappings

### 2.1 Ryman & Leimar (2008)

Their island model uses $s$ for the number of islands, $N$ for
the number of diploid individuals per island, $2N$ gene copies
per locus, $m_{\mathrm{paper}}$ for migration from a pool that
includes home, and $u$ for the per-copy infinite-alleles mutation
probability. To compare against `fim`, use $d=s$ and the
gene-copy count:

```math
n=2N \tag{M.10}
```

This is a unit conversion, not an extra factor on a value that
already counts copies. The table and derivations below use $n$
for `fim`'s per-deme gene-copy count to avoid overloading the
paper's individual-count symbol $N$.

#### Match the migration coefficients

For the paper's self-inclusive redraw, a lineage in island $i$
has source probabilities $1-m_{\mathrm{paper}}+
m_{\mathrm{paper}}/s$ for source $i$, and
$m_{\mathrm{paper}}/s$ for each other source.
For two copies starting in that same island, the probability
their ancestral sources coincide is:

```math
a=\left(1-m+\frac ms\right)^2
+(s-1)\left(\frac ms\right)^2
=(1-m)^2+\frac{m(2-m)}s \tag{M.11}
```

Here $m=m_{\mathrm{paper}}$. The first squared term is the
chance both trace to the home island. The remaining $s-1$
squared terms cover their matching on any one of the other
islands. Expanding and collecting the $1/s$ terms gives the
rightmost form.

For two copies starting in different islands $i$ and $j$,
there are two ways for both ancestral sources to be $i$ or $j$,
plus the $s-2$ other common sources:

```math
b=2\left(1-m+\frac ms\right)\frac ms
+(s-2)\left(\frac ms\right)^2
=\frac{m(2-m)}s \tag{M.12}
```

The rightmost equality follows by expanding the first term:
$2m(1-m)/s+2m^2/s^2+(s-2)m^2/s^2
=2m(1-m)/s+m^2/s$.
Thus $a$ is a same-island source-match probability and $b$
is a different-island source-match probability.

Set $s=d$ and convert $m$ with (M.6). For $d=4$ and
$m_{\mathrm{fim}}=0.06$, the paper uses $m=0.08$.
Equation (M.12) gives $b=0.08(1.92)/4=0.0384$;
(M.11) gives $a=0.92^2+0.0384=0.8848$.
These coefficients describe pair ancestry under the paper's
migration convention; they are not themselves allele frequencies.

#### Translate identity and write the paper's recursion

Let $J_0$ be distinct-copy identity within an island and $J_1$
identity between islands. Drift in one deme first changes the
within identity to:

```math
\widetilde J_0=\frac1n+\left(1-\frac1n\right)J_0 \tag{M.13}
```

The probability $1/n$ of sampling the same parental copy
creates the first term. If two copies are distinct, probability
$1-1/n$, their identity is the previous distinct-pair value.
Between-island identity has no same-deme self-pair term.

Pairwise migration then mixes the same-island and different-island
cases using $a$ and $b$. Finally, both copies must avoid independent
mutation to remain identical. With $\alpha=(1-u)^2$:

```math
J'_0=\alpha\left[a\widetilde J_0+(1-a)J_1\right] \tag{M.14}
```

```math
J'_1=\alpha\left[b\widetilde J_0+(1-b)J_1\right] \tag{M.15}
```

For $J'_0$, migration traces both copies to the same island
with probability $a$, where their match probability is
$\widetilde J_0$; otherwise they trace to different islands
with identity $J_1$. Equation (M.15) uses probability $b$ for
the corresponding same-island ancestry when the current demes
are different. In both cases, mutation preserves the old label
only if both copies avoid mutation.

`fim` stores $J^{\mathrm{wr}}$ within each deme. Convert it to
the paper's distinct-copy state with (M.9), while retaining
between-deme identity:

```math
J_0=J^{\mathrm{dist}}
=\frac{nJ^{\mathrm{wr}}-1}{n-1},
\qquad J_1=J^{\mathrm{wr}}_{\mathrm{between}} \tag{M.16}
```

For $n=10$ and $J^{\mathrm{wr}}=0.58$, this gives
$J_0=0.5333\ldots$. Then apply (M.13)–(M.15) with the
mapped migration rate. This state translation, together with
(M.6), is why the different written cycle boundaries do not
require an extra generation of lag in the tested comparison.

Once the identity convention is aligned, calculate each
differentiation statistic from the corresponding within and
between identities using the same paper formulas. Do not confuse
$J_0$ or $J_1$ with $G_{ST}$: they are identity inputs to the
statistics, not differentiation statistics themselves.
Because between-deme draws are already distinct, no conversion
is applied to $J_1$.

#### Numerical verification and size of the corrections

The mapping is checked by comparing the paper's Eqs. 2–3 as
transcribed in
[`test_simulator_equilibrium.py`](../test/validation/test_simulator_equilibrium.py)
against `fim`'s exact identity recursion. The test grid includes
$n\in\{200,2000,20000\}$, $d\in\{2,5,10,50\}$,
migration rates in $\{0,10^{-4},10^{-3},10^{-2},10^{-1}\}$,
and mutation rates in $\{0,10^{-6},10^{-4},10^{-3}\}$.
The test source records a broader 240-combination analysis,
sampled at 714 generations across each 5000-generation trajectory;
the committed regression exercises representative parameter
combinations at 1, 10, 100, 1000, and 5000 generations.
Paper units and migration rates are mapped before comparison.
Across the grid, differences in $G_{ST}$ and $D$ stay at
floating-point accumulation scale under the shared
$(1-u)^2$ mutation rule.

Without those mappings, the broader comparison recorded up to
58% relative error; after mapping, the historical maximum residual
was about 0.1%. The current regression does not rerun every point
of that larger analysis.

An unmapped migration comparison can be large. At $d=10$,
$m_{\mathrm{paper}}/m_{\mathrm{fim}}=10/9$; at $d=2$
the ratio is 2. These rates are not numerically equal because
one redraw distributes mass across $d$ sources and the other
only across $d-1$ external sources.

The identity conversion is an $O(1/n)$ correction to the
with-replacement probability. When identity is near one early
in a trajectory, even that small absolute correction can be
large relative to a small differentiation statistic. The
validation analysis recorded a 22% relative gap in $G_{ST}$
at $n=2000$, $d=10$, $u=10^{-4}$, generation 10, before
conversion. This is why checking only a large-population
equilibrium can conceal a convention mismatch that matters
for trajectory comparisons.

The mutation update is also part of the agreement. For two
distinct copies under infinite alleles, the exact shared-label
survival factor is:

```math
\alpha=(1-u)^2 \tag{M.17}
```

Either copy mutating makes its label new, so both must avoid
mutation. A former proportional-update rule introduced the
extra term $u(1-u)/n$; after replacing the textbook factor,
the old comparison residual reached 0.11% for $G_{ST}$ and
0.45% for $D$. Those historical residuals are not the current
recursion's tolerance.

### 2.2 Crow & Aoki (1984)

**Unresolved.** Crow & Aoki's Table 1 reports $G_{ST}=0.172$
for a two-dimensional stepping-stone (torus) scenario with
$n=9$ demes, 20 individuals per deme, $M=1.0$ expected migrants,
and mutation rate $10^{-5}$. The test uses the project's
interpretation $m=M/N=1/20=0.05$, divided evenly among each
deme's four neighbors. The exact identity recursion under that
matrix gives $G_{ST}\approx0.324$, without random sampling
noise. The applicable source is
[`test_simulator_equilibrium.py`](../test/validation/test_simulator_equilibrium.py),
test `test_pairwise_identity_recursion_applied_to_the_crow_aoki_torus`.

Applying a spatial analogue of the self-inclusive pool
convention—distributing the pool across the home deme and its
four neighbors—gives approximately $0.374$, farther from
the reported $0.172$. This rejects that specific migration
mapping as the explanation; it does not identify the paper's
actual convention.

An exploratory rate near $m=0.12$ gives approximately $0.172$,
while $m=0.2$ gives approximately $0.115$. This is a clue
that the rate interpretation may matter, not evidence that
$0.12$ is the authors' intended value. The paper describes its
stepping-stone values as unpublished numerical calculations
and does not specify the exact division of migrants among
the four neighbors. The discrepancy therefore remains unresolved.
See [the test plan's Appendix A](fim-simulator-test-plan.md#appendix-a-open-issues)
for the broader record.

The island-model conversion (M.6) cannot be transplanted blindly
to this topology. The island derivation assumes equal weighting
of all $d-1$ other demes; a torus has only four local neighbors.
One must specify the entire row of the spatial migration matrix,
including whether the home deme participates, before comparing
rates.

## 3. Why this document exists

Every literature comparison in this project used to re-derive its own
mapping implicitly in a test docstring. A shared reference makes the
assumptions inspectable and prevents a locally correct equation from
being compared under incompatible units or definitions. Add a new
subsection for each paper: list its variables and source-pool rule,
derive any conversion, show the identity definition, state the
operator phase, and record both positive and unresolved checks.

Numerical agreement is evidence only after the translation is
derived independently of the desired target. Pin deterministic
recursion comparisons with fixed tolerances. For stochastic
simulations, separately account for Monte Carlo uncertainty;
do not use a seed search to force agreement.

---

## Metadata

```text
generator-name: Claude Code
generator-version: Claude Sonnet 5
generator-model-token: claude-sonnet-5
generator-provider: Anthropic
generation-date: 2026-09-04
generator-responsibility: design
```

Expanded the literature-mapping reference with TeX notation,
numbered derivations, and worked examples for units, migration
source weights, and identity sampling. Restated the Ryman & Leimar
recursion mapping without relying on private notes, clarified the
unresolved Crow & Aoki result, and added explicit comparison
guidance. Checked equations and parameter claims against the
repository's identity-recursion tests and migration operators.

```text
generator-name: Copilot SDK in VS Code
generator-version: gpt-6-luna
generator-model-token: gpt-6-luna
generator-provider: OpenAI
generation-date: 2026-10-18
generator-responsibility: revision
```
