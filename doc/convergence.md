# Convergence: burn in, then average

How `fim` decides when a run is finished, and why the burn-in and the
generation cap are derived from your model instead of being fixed numbers.

## Contents

- [If you only read one section](#if-you-only-read-one-section)
- [Why does my run take so long?](#why-does-my-run-take-so-long)
- [Quick reference](#quick-reference)
- [How a run decides it is finished](#how-a-run-decides-it-is-finished)
- [How the numbers are derived](#how-the-numbers-are-derived)
- [Where the formula comes from](#where-the-formula-comes-from)
- [How precise is the reported value?](#how-precise-is-the-reported-value)
- [Batches: replicates that average](#batches-replicates-that-average)
- [Which average? Two forms for each differentiation measure](#which-average-two-forms-for-each-differentiation-measure)
- [Checking the numbers yourself](#checking-the-numbers-yourself)
- [Limits](#limits)
- [Notation and implementation map](#notation-and-implementation-map)
- [Metadata](#metadata)

Mathematical quantities use $\TeX$, such as $\tau$ for a relaxation time.
Literal configuration keys, report fields, and Python names use backticks,
such as `precision` and `standard_error`. Equations are numbered within
this document as (C.1), (C.2), and so on; the prefix means
**convergence reference**, not Appendix C of the
[simulator design](fim-simulator-design.md). That design's Appendices C–F
provide additional algebra, probability, and linear-algebra refreshers.

## If you only read one section

A run first waits out a **burn-in**: the time the population needs to forget
where it started. Only then does it start **averaging**, and it keeps
averaging until the average is known to the precision you asked for (plus or
minus `precision`, at `confidence`). The result is the average, with its error
bar. If the run reaches its generation cap before the average is that
precise, it says so ("hit the cap") and still reports the average it has,
with the error bar it really has. A run that finishes in a hundred
generations is not necessarily wrong, but a small generation count alone
is no evidence of equilibrium. Check `convergence_burn_in`,
`max_generations`, and the reported uncertainty against the model's
timescale.

There are two separate questions:

1. **Has the initial condition's influence decayed enough?** Burn-in
   addresses this possible bias.
2. **Has enough evidence been collected to estimate the equilibrium
   average precisely?** The averaging window and its error bar address
   sampling uncertainty.

A long burn-in does not automatically answer the second question.
A small error bar does not repair a burn-in that ended too early.
The population need not stop changing for its equilibrium **average**
to be estimable.

## Why does my run take so long?

*For everyone, including non-technical readers.*

A simulated population does not settle at once. When several islands begin
with different genes, it takes a long time for gene flow and mutation to shape
what you finally see. With very little migration between islands, "a long
time" means tens of thousands of generations. After that the numbers still
wobble from generation to generation by drift, so `fim` averages them over a
long stretch until the average is steady to the precision you chose.

You do not have to choose the lengths: leave the settings on `auto`. The app
shows the expected burn-in next to the **Run simulation** button before you
start, for example:

> Convergence: burn-in 104,338 generations, cap 399,728 (derived; this model
> needs about 19,693 generations to forget its starting state)

If a run stops at the cap without reaching the precision, the report says how
precise the average is, so you can decide whether to allow more generations,
ask for a larger allowed error, use more independent loci, or run several
replicates. Replicates can reduce elapsed time when parallel resources
are available, but each still pays its own burn-in cost; a batch is not
guaranteed to require less total computation.

## Quick reference

*For sysops and technicians who need a reminder, not a tutorial.*

| Setting | Default | Meaning |
| --- | --- | --- |
| `precision` | `0.01` | Plus or minus, in each watched statistic's own units, at `confidence` |
| `confidence` | `0.95` | Nominal coverage of the interval procedure (0.90, 0.95 or 0.99), not a guarantee about one interval |
| `convergence_burn_in` | `auto` | First included generation, derived from $\lceil k\tau\rceil$ with $k=\max(5,\ln(2/\varepsilon))$ |
| `max_generations` | `auto` | Safety cap: $\max(200000,b+\lceil15\tau\rceil)$, at most 10,000,000 |

$\varepsilon$ denotes `precision`, $b$ the derived burn-in boundary, and
$\tau$ the estimated relaxation time of the slowest locus.
The table gives the base identity-statistic policy; an expert multiplier
can lengthen burn-in for spectrum statistics. Its shipped value is 1,
so enabling a spectrum statistic alone does not currently extend burn-in.
Any valid explicit integer you write
replaces the derived value. If you set only one of the two, the other adapts: a
derived cap leaves $15\tau$ after an explicit burn-in, subject to the
10,000,000-generation ceiling. The
`expert:` mapping changes the policy constants (the floor of 5, the factor
15, the first check, the effective-sample-size floor and the rest; see
[configuration.md](configuration.md#expert)).

Related settings, each described in [configuration.md](configuration.md):
`convergence_statistic` (default `D` and `G_ST`, stopping when both have
settled; any global statistic may be watched, an unbounded one such as an allele
count gets a relative precision), `statistic_precision` (a plus-or-minus for one
statistic), `convergence_estimate` (mean of values or value of means, below),
`precision_method` and `replicate_averaging_window` (batches, below), and
`trajectory_retention` (thin a very long run's trajectory file).

Symptoms of a bad value:

- **Burn-in too short:** the average starts inside the transient and is
  biased.
- **Cap too small:** the run ends "hit the cap", possibly before the burn-in
  even ends (then there is no average at all); the run logs a warning.
- **Cap too large:** only a run that never reaches its precision takes longer
  to end.

`fim run` prints the derived values unless `--quiet`. They are also written as
plain integers to `manifest.json`. Reusing them preserves the schedule;
exact trajectory reproduction additionally requires the seed, model
parameters, software version, and compatible numerical platform.

## How a run decides it is finished

*For sysops, technicians and developers.*

```text
resolve burn-in and cap from configuration or model-derived defaults
start the evidence window at the burn-in boundary, excluding generation 0
first check after the configured initial evidence span
at each check, for every watched statistic:
    estimate its mean, standard error, and effective sample size
    pass if both the error target and the effective-sample-size floor pass
stop if all (or any, under `convergence_combinator: any`) pass
otherwise grow the evidence window before the next check
stop as capped at max_generations
```

- Nothing is averaged before the burn-in, and generation 0 is never in an
  average.
- $z$ is the two-sided normal critical value (about 1.96 at 95%), so `precision`
  keeps the meaning "plus or minus this much".
- The effective sample size, reported as `effective_sample_size`, is how many independent values the
  window's correlated generations are worth; a standard error from fewer than
  50 is not trusted by the default rule. Geyer's initial positive sequence
  estimator sums an estimated sequence of autocorrelations up to a
  data-dependent cutoff, rather than using only neighboring generations
  (see
  [How precise is the reported value?](#how-precise-is-the-reported-value)).
- Every watched statistic is judged at every check, so the stop generation
  never depends on the order they are listed in.
- With no relaxation time (no migration and no mutation, or an explicit
  matrix beyond 24 demes) and `convergence_burn_in: auto`, the window starts
  at the first tenth of the run at each check.
- At the stop, every recorded statistic, watched or not, gets its mean and
  standard error over the same window, in `report.json`'s
  `window_statistics`.

### Window boundaries and check scheduling

Let $t$ be the current generation and $b\geq1$ the first included
generation. The evidence window includes both endpoints:

```math
W=t-b+1 \tag{C.1}
```

$W$ counts observations, not elapsed gaps between them. If $b=100$
and $t=150$, the window contains 51 observations:
generations $100,101,\ldots,150$. Generation 99 is excluded.
The name `burn_in` is therefore best read as a boundary; it does not
mean that generation $b$ is discarded too.

With default expert settings and an available $\tau$, the initial
span and first check are:

```math
\Delta_0=\max\left(50,\lceil2\tau\rceil\right) \tag{C.2}
```

```math
t_0=b+\Delta_0 \tag{C.3}
```

The ceiling $\lceil x\rceil$ means round upward to a whole generation.
For $b=100$ and $\tau=20$, the maximum in (C.2) chooses 50 rather
than 40, so the first check is at generation 150.
If that check fails, the implementation schedules:

```math
t_{\mathrm{next}}=\max\left(t+1,\ b+\lceil gW\rceil\right),\qquad g=2
\tag{C.4}
```

Here $g$ is the default growth factor. For the 51-observation
window above, the next check is at $100+2\cdot51=202$,
which includes 103 observations. This endpoint convention explains
why "double the window" is a description of the policy rather
than an exact doubling of the observation count.
The run still ends at its cap if a scheduled check lies beyond it.

See [the check-scheduling limit](#check-scheduling-limit) for the
consequences of checking only at selected generations.

When no model-derived time is available, the fallback boundary at
a check is $\lfloor0.1t\rfloor$. Only generation-1 and later
values are recorded by the run for this averaging.
The floor $\lfloor x\rfloor$ rounds downward.
Unlike a fixed burn-in, this window's start moves as the run grows;
it is a fallback policy, not a demonstrated equilibrium time.

## How the numbers are derived

*For developers and reviewers.*

### Units and the scalar island approximation

Let $N_k$ count individuals in deme $k$, $c$ count gene copies per
individual from ploidy, and $n_k=cN_k$ count copies **at one locus**.
The total copy count is:

```math
n_{\mathrm{total}}=\sum_{k=1}^{d}n_k \tag{C.5}
```

Do not add copies across loci. Twenty diploid demes of 225 individuals
have $n_k=450$ and $n_{\mathrm{total}}=9000$ at each locus.
For the scalar island model, each destination keeps fraction $1-m$
and receives fraction $m$ from the other demes. With equal sizes and
$m>0$, the implemented approximation is:

```math
T=n_{\mathrm{total}}+\frac{d-1}{2m} \tag{C.6}
```

```math
\tau=\frac1{2\mu_{\min}+1/T} \tag{C.7}
```

$T$ is a pairwise ancestral coalescence timescale: time for two
lineages to reach a common ancestral copy. It is not the final
generation number, nor the time for every copy's entire genealogy
to coalesce. $\mu_{\min}=\min_l\mu_l$ is the smallest per-locus
mutation probability, supplied by `mu` or derived from `mu_b`.
With shared sizes and migration, the lowest mutation rate gives the
slowest-decaying identity mode and sets the common burn-in scale.

The denominator in (C.7) is a sum of **rates**, each in inverse
generations. Its reciprocal is in generations. Adding the times
$T$ and $1/(2\mu_{\min})$ would instead describe waiting for
two successive events, not two alternative ways to forget identity.
These are discrete-generation, small-rate approximations, not exact
identities for arbitrary migration and mutation probabilities.

For five haploid demes of 100 copies each, $m=0.0001$ and
$\mu_{\min}=0.000001$, calculate:

```math
T=5\cdot100+\frac{5-1}{2\cdot0.0001}=500+20000=20500 \tag{C.8}
```

```math
\tau=\frac1{0.000002+1/20500}\approx19692.60 \tag{C.9}
```

The small rate $0.000002$ is added to approximately $0.00004878$.
The resulting decay is slow even though a generation is cheap to
simulate. At $m=0$, the implementation sets the coalescence-rate
contribution to zero without dividing by zero. Positive mutation
then gives $\tau=1/(2\mu_{\min})$. With both rates zero this route
has no finite time and refuses a derived cap.

The relaxation time is computed whenever a supported model permits it,
not only when burn-in or cap is automatic. Explicit matrices and
unequal sizes use the identity operator in the next section, with a
$d^2$-by-$d^2$ eigenproblem limited to 24 demes.
The source is [`defaults.py`](../src/fim/convergence/defaults.py).

The relevant limits are
[the scalar approximation](#scalar-approximation-limit),
[unequal sizes and the eigenproblem guard](#matrix-and-size-limit),
[selection of the slowest locus](#slowest-locus-limit), and
[the high-migration reference scenario](#high-migration-evidence-limit).

### From a decay scale to a burn-in

Let $\varepsilon>0$ denote requested `precision`. A unit-amplitude
mode that decays exponentially has remaining magnitude
$\exp(-b/\tau)$ after $b$ generations. Allocate half the requested
precision to that residual as a policy heuristic:

```math
\exp(-b/\tau)\leq\varepsilon/2 \tag{C.10}
```

Taking natural logs, which preserve inequalities between positive
numbers, gives:

```math
-b/\tau\leq\ln(\varepsilon/2) \tag{C.11}
```

Multiplication by the negative number $-\tau$ reverses the inequality:

```math
b\geq\tau\ln(2/\varepsilon) \tag{C.12}
```

The implementation adds a minimum multiple:

```math
k=\max\left(5,\ln(2/\varepsilon)\right) \tag{C.13}
```

```math
b=\max\left(1,\min\left(C_{\max},\lceil a k\tau\rceil\right)\right) \tag{C.14}
```

$C_{\max}=10000000$ is the default hard ceiling.
$a=1$ for the base identity-statistic policy; watching an
allele-spectrum statistic can invoke `spectrum_burn_in_multiplier`
instead. The floor of five relaxation times prevents a loose
precision from shortening the burn-in too much. In the measured
two-mode example motivating the policy, bias at about $5.3\tau$
was $-0.00016$; that measurement is evidence for a default, not
a universal bound.

For $\varepsilon=0.01$, $\ln200\approx5.298317$, so the
logarithm exceeds the floor. With (C.9) and $a=1$,
$b=\lceil104337.661877\ldots\rceil=104338$.
For $\varepsilon=0.001$ the multiple grows to $\ln2000\approx7.600902$.
For $\varepsilon=0.1$ it stays at five because $\ln20<5$.
Zero precision is handled specially: the derived burn-in reaches
the ceiling rather than evaluating $\ln(2/0)$.

The unit-offset argument explains the policy; it is **not** a
proof that every nonlinear statistic's bias is bounded by
$\varepsilon/2$. Mode amplitudes, nonlinear ratios, mutation model,
and clipping at the ceiling limit that interpretation. In particular,
clipping a requested burn-in cannot preserve a bound that required
a longer run.

### From burn-in to a safety cap

The base derived cap is:

```math
C=\min\left(C_{\max},\max\left(200000,b+\lceil15\tau\rceil\right)\right)
\tag{C.15}
```

For the same example, $\lceil15\tau\rceil=295390$ and
$C=104338+295390=399728$. The 200,000 floor does not apply
because that sum is larger. For a much faster model it can
dominate. The GUI example near the start of this document uses
these values; (C.8)–(C.15) show the precise
arithmetic for the stated example.

The floor is empirical, not derived from $\tau$. Measurements of
single-locus $D$ at target precision 0.01 needed close to
130,000 generations in two regimes with $\tau$ near 85 and 19,693.
Fifteen relaxation times alone would leave too little averaging in
the fast regime. Conversely, the cap is not a promise that the
precision will be reached: the uncertainty check still decides.

See [the empirical-cap limit](#empirical-cap-limit) for the scope
of the measurements supporting the floor.

## Where the formula comes from

*For the mathematically curious. The only linear algebra needed is explained
here.*

### One number: follow the error instead of the state

Start with a deterministic recurrence:

```math
x_{t+1}=\lambda x_t+c_0,\qquad 0<\lambda<1 \tag{C.16}
```

$t$ counts generations, $\lambda$ is the fraction of the old value
retained, and $c_0$ is a constant addition. The subscript distinguishes
this constant from ploidy $c$. A fixed point $x_*$ is a value that
the recurrence leaves unchanged. Substituting it gives
$x_*=\lambda x_*+c_0$. Subtract $\lambda x_*$ and divide:

```math
x_*=\frac{c_0}{1-\lambda} \tag{C.17}
```

Now define the error $e_t=x_t-x_*$. Subtract the fixed-point equation
from the update. The constant terms cancel:

```math
e_{t+1}=\lambda(x_t-x_*)=\lambda e_t \tag{C.18}
```

Apply that rule twice to obtain $e_2=\lambda^2e_0$; repeat $t$
times:

```math
e_t=\lambda^t e_0 \tag{C.19}
```

For $\lambda=0.9$, $c_0=0.02$, and $x_0=0.8$, the fixed point
is $0.2$ and the initial error is $0.6$. The next two states are
$0.74$ and $0.686$, whose errors $0.54$ and $0.486$ are
exactly $0.9$ times their predecessors. After ten steps the error
is $0.6(0.9)^{10}\approx0.20921$.

An exact exponential decay scale solves $\lambda^\tau=e^{-1}$:

```math
\tau_{\log}=-\frac1{\ln\lambda} \tag{C.20}
```

This follows by taking logs: $\tau\ln\lambda=-1$.
The code uses a closely related **gap timescale**:

```math
\tau_{\mathrm{gap}}=\frac1{1-\lambda} \tag{C.21}
```

To connect them, put $\delta=1-\lambda$. For small $\delta$,
$\ln(1-\delta)\approx-\delta$, so (C.20) is approximately
$1/\delta$, which is (C.21).
At $\lambda=0.9$ the scales are about 9.4912 and 10 generations.
At $\lambda=0.5$ they are about 1.4427 and 2, so the approximation
is less close. This document uses $\tau$ for the **implemented**
timescale; it does not identify the two definitions as exactly equal.

For a single positive mode the gap choice is conservative relative
to the exact exponential scale, because
$\ln\lambda\leq-(1-\lambda)$ implies
$\lambda^t\leq\exp[-t(1-\lambda)]$.
That inequality does not by itself bound every multivariate
nonlinear statistic.

### Many numbers: eigenvectors are shapes that keep their shape

A vector collects several quantities. A matrix specifies which
weighted combinations form their next values:

```math
\boldsymbol x_{t+1}=A\boldsymbol x_t+\boldsymbol c \tag{C.22}
```

This is an **affine** update: a linear transformation plus a
constant vector. Subtracting its fixed point again cancels the
constant, leaving $\boldsymbol e_{t+1}=A\boldsymbol e_t$.
An eigenvector $\boldsymbol v\ne0$ is a direction that the
matrix scales without changing its shape:

```math
A\boldsymbol v=\lambda\boldsymbol v \tag{C.23}
```

For example, take:

```math
A=\begin{pmatrix}0.8&0.1\\0.1&0.8\end{pmatrix} \tag{C.24}
```

Multiplication gives two particularly simple directions:

```math
A\begin{pmatrix}1\\1\end{pmatrix}
=\begin{pmatrix}0.9\\0.9\end{pmatrix}
=0.9\begin{pmatrix}1\\1\end{pmatrix} \tag{C.25}
```

```math
A\begin{pmatrix}1\\-1\end{pmatrix}
=\begin{pmatrix}0.7\\-0.7\end{pmatrix}
=0.7\begin{pmatrix}1\\-1\end{pmatrix} \tag{C.26}
```

The first direction moves the coordinates together; the second
moves them oppositely. Their eigenvalues, 0.9 and 0.7, are the
retained fractions per step. Decompose one initial error:

```math
\begin{pmatrix}0.6\\0.2\end{pmatrix}
=0.4\begin{pmatrix}1\\1\end{pmatrix}
+0.2\begin{pmatrix}1\\-1\end{pmatrix} \tag{C.27}
```

Applying $A$ once gives $(0.50,0.22)^{\mathsf T}$:
the common component becomes $0.4\cdot0.9=0.36$ and the
contrast becomes $0.2\cdot0.7=0.14$. Applying it $t$ times gives:

```math
\boldsymbol e_t=
0.4(0.9)^t\begin{pmatrix}1\\1\end{pmatrix}
+0.2(0.7)^t\begin{pmatrix}1\\-1\end{pmatrix} \tag{C.28}
```

The faster contrast vanishes sooner. The slow common component
dominates later, provided it was present in the initial error
and the measured quantity can see it.

```text
Starting discrepancy
    +-- common component, multiplied by 0.9 each step --+
    |                                                   +--> sum at step t
    +-- contrast component, multiplied by 0.7 each step -+
             fades sooner
```

The spectral radius $\rho$ is the largest absolute eigenvalue:

```math
\rho(A)=\max_j|\lambda_j| \tag{C.29}
```

The implementation takes $\tau=1/(1-\rho)$, provided $\rho<1$.
A radius of one leaves a nondecaying mode and no finite derived
time. The eigenvector decomposition above applies directly when
there is a full basis of eigenvectors, as in this example.
More general matrices can have transient effects or polynomial
factors as well as exponential decay; the radius describes an
asymptotic rate, not a universal finite-time error bound.

### Pairwise identity: why two migration weights appear

Fix one locus under infinite alleles. For a realized frequency table,
define $J_{ij}=\sum_a p_i(a)p_j(a)$: the probability that independent
draws from demes $i$ and $j$ have the same label. On the diagonal,
both draws are from the **same distribution with replacement**.
They can select the very same physical copy, so this is frequency
identity, not distinct-copy identity $F$ in the simulator design.

Equilibrium here concerns matching probabilities and frequency
structure, not an unchanging set of absolute allele IDs. See
[the allele-label stationarity limit](#allele-label-stationarity-limit).

For a deterministic row-stochastic parental mixing matrix $M$,
the post-migration identity table is:

```math
K=MJM^{\mathsf T} \tag{C.30}
```

The superscript $\mathsf T$ means transpose: interchange rows and
columns. It is not a generation index or the coalescence time $T$.
To see why both sides are needed, expand one entry:

```math
K_{ij}=\sum_{r=1}^{d}\sum_{s=1}^{d}M_{ir}M_{js}J_{rs} \tag{C.31}
```

The first offspring's parent comes from source $r$ with weight
$M_{ir}$; the second comes from source $s$ with weight $M_{js}$.
For that source pair, labels match with probability $J_{rs}$.
Multiply these factors, then add over every possible source pair.
Mixing a single allele-frequency row needs one weight; matching
two sampled copies needs two.

This derivation uses a deterministic $M$. See
[the stochastic-migration limit](#stochastic-migration-limit)
before applying it to randomly drawn migrant fractions.

For a concrete calculation, use:

```math
M=\begin{pmatrix}0.8&0.2\\0.1&0.9\end{pmatrix} \tag{C.32a}
```

```math
J=\begin{pmatrix}0.68&0.32\\0.32&0.68\end{pmatrix} \tag{C.32b}
```

The $J$ table comes from frequency rows $(0.8,0.2)$ and
$(0.2,0.8)$, so it is an actual identity table rather than
an arbitrary collection of probabilities.
First multiply on the left:

```math
MJ=\begin{pmatrix}0.608&0.392\\0.356&0.644\end{pmatrix} \tag{C.33}
```

For example, the upper-left entry is
$0.8\cdot0.68+0.2\cdot0.32=0.608$.
Now mix the second parent with the transposed matrix:

```math
K=(MJ)M^{\mathsf T}
=\begin{pmatrix}0.5648&0.4136\\0.4136&0.6152\end{pmatrix} \tag{C.34}
```

Its upper-left entry is $0.608\cdot0.8+0.392\cdot0.2=0.5648$.
As a separate check, $MP$ gives frequency rows $(0.68,0.32)$
and $(0.26,0.74)$. Their squared sums and cross-product give
exactly (C.34).
Unlike $M$ and $P$, rows of $J$ or $K$ need **not** sum to one.
Their entries are separate matching probabilities, not alternatives
partitioning a single draw.

### Drift and mutation: distinguish self-pairs from distinct pairs

Write the probability that two independently selected copies both
avoid mutation as:

```math
\alpha=(1-\mu)^2=1-2\mu+\mu^2 \tag{C.35}
```

For distinct copies in different demes, independent mutation can
preserve a match only when both avoid mutation:

```math
\mathbb E[J'_{ij}\mid P]=\alpha K_{ij},\qquad i\ne j \tag{C.36}
```

The prime denotes the next generation. The expectation averages over
drift and mutation conditional on the current frequency table $P$.
Fresh infinite-alleles mutations cannot match another copy's label.

That assumption is essential. See
[the finite-alleles limit](#finite-alleles-limit) for recurrent
mutations that can create matching labels.

For two draws **within** destination $i$, the chance of selecting
the same observed offspring copy twice is $1/n_i$. Such a
self-pair always matches, even if that offspring mutated.
Otherwise two distinct offspring must both avoid mutation and
have matching parental labels:

```math
\mathbb E[J'_{ii}\mid P]=\frac1{n_i}
+\left(1-\frac1{n_i}\right)\alpha K_{ii} \tag{C.37}
```

This order of factors matters. Multiplying the entire right-hand
side by $\alpha$ would incorrectly allow mutation to make a copy
different from itself. The actual engine still executes
Migrate → Drift → Mutate; (C.36)–(C.37) summarize its matching
probabilities, not a proposed rearrangement of the operators.

For (C.34), take $n_1=n_2=10$ and $\mu=0.1$, so $\alpha=0.81$.
The upper-left output is
$0.1+0.9\cdot0.81\cdot0.5648=0.5117392$.
The off-diagonal output is $0.81\cdot0.4136=0.335016$.
The full conditional expectation is:

```math
\mathbb E[J'\mid P]=
\begin{pmatrix}0.5117392&0.335016\\0.335016&0.5484808\end{pmatrix}
\tag{C.38}
```

This table is not one random output population. It is the mean
identity table over possible next populations.
Linearity lets the same recurrence act on $\mathbb E[J]$ across
generations, which is the key simplification.

### Flattening the table into one vector

Arrange the four entries of a two-deme table in row order:

```math
\boldsymbol j=(J_{11},J_{12},J_{21},J_{22})^{\mathsf T} \tag{C.39}
```

Flattening changes the addressing, not the biology.
Each coefficient in (C.31) becomes one entry of a larger matrix.
For the $M$ in (C.32a), that matrix is:

```math
B=M\otimes M=
\begin{pmatrix}
0.64&0.16&0.16&0.04\\
0.08&0.72&0.02&0.18\\
0.08&0.02&0.72&0.18\\
0.01&0.09&0.09&0.81
\end{pmatrix} \tag{C.40}
```

The symbol $\otimes$ denotes a Kronecker product. Here it simply
lists the products of the two parental source weights.
For example, row 2 describes destination pair $(1,2)$:
its weights are $0.8\cdot0.1$, $0.8\cdot0.9$,
$0.2\cdot0.1$, and $0.2\cdot0.9$.
Multiplying $B\boldsymbol j$ reconstructs the flattened $K$.

Let $D_n$ be diagonal, with entry $1-1/n_i$ at the address of
$J_{ii}$ and 1 at all off-diagonal addresses. Let $\boldsymbol c$
contain $1/n_i$ at diagonal addresses and zero elsewhere.
For the ten-copy example:

```math
D_n=\operatorname{diag}(0.9,1,1,0.9) \tag{C.41a}
```

```math
\boldsymbol c=(0.1,0,0,0.1)^{\mathsf T} \tag{C.41b}
```

The operator and its affine recurrence are:

```math
A=\alpha D_n(M\otimes M) \tag{C.42}
```

```math
\mathbb E[\boldsymbol j_{t+1}]
=A\mathbb E[\boldsymbol j_t]+\boldsymbol c \tag{C.43}
```

Apply $B$ first, multiply each output coordinate by its
diagonal damping factor, then by $\alpha$, and finally add
$\boldsymbol c$. This reproduces all entries of (C.38).
The constant vector determines the fixed point but disappears
when tracking the error from that fixed point. Thus
[`recursion_relaxation_time()`](../src/fim/convergence/defaults.py)
needs the spectral radius of $A$, not the fixed point itself.
For $d$ demes there are $d^2$ ordered pair addresses, hence a
$d^2$-by-$d^2$ matrix. General dense eigensolver cost grows roughly
as $(d^2)^3=d^6$, which motivates the 24-deme guard.

Within- and pooled heterozygosities are linear combinations of
these identities. But $D$ and $G_{ST}$ are ratios of random
heterozygosities: $\mathbb E[f(J)]$ is generally **not**
$f(\mathbb E[J])$. The identity timescale therefore informs
their burn-in; it does not prove that every statistic has
exactly the same relaxation or sampling-error behavior.
Allele richness and spectrum statistics require further caution.

### Why a one-lineage migration gap is not enough

Migration alone follows the location of **one** ancestral lineage.
Identity also depends on what happens to **two** lineages.
They must reach a common ancestor, or mutation must erase
their shared identity. A location can become well mixed before
two particular ancestral lines have coalesced.

For the equal-size island matrix, the nonconstant migration
eigenvalue is $1-md/(d-1)$, so its positive gap is
$md/(d-1)$. With five demes and $m=0.0001$, the inverse
gap is $1/0.000125=8000$ generations. This is far below the
approximately 19,693-generation identity scale in (C.9).
In the low-migration regime their ratio is roughly $d/2$,
not a universal factor for every topology.
For asymmetric or periodic matrices, even the interpretation
of a "smallest nonzero eigenvalue" needs care; the symmetric
island comparison is the simple example, not a general algorithm.

### What the coalescence approximation assumes

Two lineages currently in different demes each migrate with
probability $m$. To first order in small $m$, one migrates into
the other's deme at rate $2m/(d-1)$. Simultaneous migration
contributes terms of order $m^2$ omitted from that approximation.
The reciprocal gives the approximate time $(d-1)/(2m)$
for the pair to meet in a deme.

Meeting in a deme is not yet choosing the same parental copy:
they can separate again. The classical island coalescence
timescale adds the total-population term to obtain (C.6).
The addition can be derived using two ancestral states:

```text
different demes -- rate 2m/(d-1) --> same deme -- rate 1/n --> coalesced
different demes <-- rate 2m -------- same deme
```

Here $n$ is the equal per-deme copy count. Let $T_{\mathrm{same}}$
and $T_{\mathrm{different}}$ be the expected remaining times
to coalescence from those states, ignoring mutation while deriving
the coalescence part. In the continuous-time, small-migration
approximation:

1. A pair in different demes waits on average $(d-1)/(2m)$
   to enter the same-deme state. Thus
   $T_{\mathrm{different}}=T_{\mathrm{same}}+(d-1)/(2m)$.
2. In the same-deme state, coalescence has rate $1/n$ and
   separation has rate $2m$. Write $r=1/n+2m$ for their total.
   The mean wait to the next event is $1/r$.
   With probability $(2m)/r$ the event is separation,
   requiring an additional $T_{\mathrm{different}}$.
   With probability $(1/n)/r$ coalescence ends the wait.
   Consequently
   $T_{\mathrm{same}}=1/r+(2m/r)T_{\mathrm{different}}$.
3. Multiply by $r$ and rearrange to get
   $T_{\mathrm{same}}/n=1+2m(T_{\mathrm{different}}-T_{\mathrm{same}})$.
4. Substitute the difference from step 1. The right-hand
   side becomes $1+2m(d-1)/(2m)=d$.
   Therefore $T_{\mathrm{same}}=nd=n_{\mathrm{total}}$.
5. Step 1 now gives
   $T_{\mathrm{different}}=n_{\mathrm{total}}+(d-1)/(2m)$,
   the $T$ used in (C.6).

The model explicitly allows separation before coalescence;
the total-population term is not simply one local waiting time.
These waiting-time equations are exact for the stated
continuous-time approximation, not for arbitrary probabilities
in the discrete-generation engine. Using this pairwise mean
as the inverse slow decay rate is itself an approximation.

Mutation supplies another route to loss of identity.
For two copies the exact survival is (C.35), with loss
$2\mu-\mu^2\approx2\mu$. Treating coalescence and mutation
as small independent hazards gives an effective rate
$1/T+2\mu$ and its reciprocal (C.7). This explains both
why rates add and why the implemented closed form is approximate.

## How precise is the reported value?

*For everyone.* A statistic that has stopped trending still varies from
generation to generation. Neighboring generations have shared ancestry
and similar frequencies, so they are not independent observations.
Counting every generation as independent can make an average's error
bar much too small. The following derivation explains the correction.

### Averages, deviations, and correlated observations

Within a post-burn-in window, write the statistic values as
$Y_1,\ldots,Y_W$. The subscript counts observations within this
window, not absolute generations. Their average is:

```math
\bar Y=\frac1W\sum_{u=1}^{W}Y_u \tag{C.44}
```

The bar means average; $\sum$ means add the indicated terms.
For $(0.2,0.4,0.6)$, this gives $0.4$.
Let $\sigma^2$ be the variance of a stationary individual
observation, and $\rho_h$ the correlation between observations
separated by $h$ generations. Stationarity here means that the
distribution and lag correlations no longer depend on the
absolute starting generation.

Expand the square of the centered sum. There are $W$ individual
variance terms. For each separation $h$, there are $W-h$
distinct pairs, and both orders of each pair appear:

```math
\operatorname{Var}\left(\sum_{u=1}^{W}Y_u\right)
=W\sigma^2+2\sigma^2\sum_{h=1}^{W-1}(W-h)\rho_h \tag{C.45}
```

For $W=4$, lag 1 has pairs $(1,2),(2,3),(3,4)$,
lag 2 has $(1,3),(2,4)$, and lag 3 has $(1,4)$.
This is the origin of the factor $W-h$, not an arbitrary
weight. Dividing the sum by $W$ divides its variance by $W^2$:

```math
\operatorname{Var}(\bar Y)=
\frac{\sigma^2}{W}\left[1+2\sum_{h=1}^{W-1}
\left(1-\frac hW\right)\rho_h\right] \tag{C.46}
```

If every $\rho_h=0$, only the familiar $\sigma^2/W$ remains.
For a long window with correlations that decay fast enough
for their sum to exist, the bracket approaches the
integrated autocorrelation factor:

```math
\tau_{\mathrm{int}}=1+2\sum_{h=1}^{\infty}\rho_h \tag{C.47}
```

The convention includes the leading 1 and both lag directions.
Some references define this quantity with a different factor
of two; use (C.47) when comparing values here.
For observations at every generation, $\tau_{\mathrm{int}}$
can be interpreted as generations per equivalent independent
observation. It is **not** the model-derived slow-mode
relaxation time $\tau$. The former depends on the statistic
and its whole correlation curve; the latter is a model timescale.

Replace the actual observation count by its equivalent-information
count, the effective sample size:

```math
W_{\mathrm{eff}}\approx\frac W{\tau_{\mathrm{int}}} \tag{C.48}
```

Taking the square root of (C.46), with the long-window
approximation, gives the standard error of the mean:

```math
\operatorname{SE}(\bar Y)\approx
\frac{\sigma}{\sqrt{W_{\mathrm{eff}}}}
=\sigma\sqrt{\frac{\tau_{\mathrm{int}}}{W}} \tag{C.49}
```

For $W=10000$, $\sigma=0.1$, and $\tau_{\mathrm{int}}=100$,
the effective count is only 100. The standard error is
$0.1/\sqrt{100}=0.01$, not the independent-draw value
$0.1/\sqrt{10000}=0.001$. Individual values still vary
on scale $0.1$. The standard deviation describes that spread;
the standard error describes uncertainty in their average.

### Why the estimator uses more than lag 1

The estimator matters. A single-lag estimate (the correlation of neighboring
values) gives the full sum only under an assumed geometric
correlation curve, as for a stationary first-order autoregression.
If $\rho_h=r^h$ with $|r|<1$, the geometric series gives:

```math
\tau_{\mathrm{int}}=1+2\frac r{1-r}=\frac{1+r}{1-r} \tag{C.50}
```

A mixture need not follow that curve. Consider:

```math
\rho_h=0.9(0.5)^h+0.1(0.99)^h \tag{C.51}
```

Its lag-1 correlation is $0.549$. Substituting that one
number into (C.50) gives about 3.435. But summing both
geometric tails gives $1+2(0.9+9.9)=22.6$.
The small slow component contributes most of the long-run
uncertainty even though its lag-1 contribution is only 0.099.
Lag 1 alone cannot identify the tail's shape.

In the repository's seeded two-mode measurement whose true
$\tau_{\mathrm{int}}$ is 469, the single-lag formula gave
about 75 and Geyer's estimate gave 438. Those are results for
one measured series, not deterministic guarantees for all
series with slow modes.

[`geyer_window_statistics()`](../src/fim/convergence/window_statistics.py)
implements the initial positive sequence method.
First center the observed values and estimate their lag correlations:

```math
\widehat\rho_h=
\frac{\sum_{u=1}^{W-h}(Y_u-\bar Y)(Y_{u+h}-\bar Y)}
{\sum_{u=1}^{W}(Y_u-\bar Y)^2} \tag{C.52}
```

A hat means an estimate from finite data. The implementation uses
this shared-denominator normalization, with $\widehat\rho_0=1$,
not a separate $W-h$ divisor at every lag.
It computes the sums using a zero-padded fast Fourier transform;
that changes computational cost, not the sums being estimated.
A constant window is handled separately to avoid a zero denominator:
its estimated spread and standard error are zero and its effective
count is $W$. A finite constant sample is not proof of global
equilibrium; burn-in and the other diagnostics still matter.

Pair consecutive lag estimates:

```math
\widehat\Gamma_j=\widehat\rho_{2j}+\widehat\rho_{2j+1}
 \tag{C.53}
```

Starting at $j=0$, keep complete pairs until the first
nonpositive pair. If the last kept index is $q$, calculate:

```math
\widehat\tau_{\mathrm{int}}=
\max\left(1,-1+2\sum_{j=0}^{q}\widehat\Gamma_j\right) \tag{C.54}
```

The empty retained sum is zero. Why the $-1$?
The first pair includes $\widehat\rho_0=1$; doubling all
pairs counts it twice, so subtract one to recover the
leading 1 in (C.47).
For pair sums $1.8,1.1,0.4,-0.05$, retain only the first
three. Their sum is 3.3, giving
$-1+2\cdot3.3=5.6$.
The lower clamp of 1 prevents crediting a negatively correlated
sample with more effective observations than actual observations.

The method limits noisy long-lag contributions rather than
literally summing every estimated lag. Initial-positive-sequence
theory is motivated by suitable reversible stationary processes;
using it for these recorded statistics remains an estimation
procedure, not a proof that every slow component has been found.
Too little evidence can hide a tail from this estimator too.

See [the finite-evidence limit](#finite-evidence-limit) for why an
apparently quiet window is not a proof of equilibrium.

The code uses the sample standard deviation
$s=\sqrt{\sum_u(Y_u-\bar Y)^2/(W-1)}$, then reports
$W_{\mathrm{eff}}=W/\widehat\tau_{\mathrm{int}}$ and
$\widehat{\operatorname{SE}}=s/\sqrt{W_{\mathrm{eff}}}$.
The finite-window weights in (C.46) explain the exact variance;
(C.52)–(C.54) describe the particular estimator actually used.

### Turning precision into a stopping test

Let $\gamma$ be `confidence`, and $\Phi$ the cumulative
distribution function of a standard normal random variable.
The two-sided critical value is:

```math
z_\gamma=\Phi^{-1}\left(\frac{1+\gamma}{2}\right) \tag{C.55}
```

For $\gamma=0.95$, each tail has probability 0.025 and
the quantile is 0.975, giving $z_\gamma\approx1.959964$.
The approximate interval is
$\bar Y\pm z_\gamma\widehat{\operatorname{SE}}$.
Its half-width must not exceed $\varepsilon$:

```math
\widehat{\operatorname{SE}}\leq\frac{\varepsilon}{z_\gamma}
\quad\hbox{and}\quad W_{\mathrm{eff}}\geq50 \tag{C.56}
```

At precision 0.01 and 95% confidence, the target standard
error is about 0.005102, **not** 0.01.
With $s=0.1$, at least approximately
$(1.959964\cdot0.1/0.01)^2=384.15$ effective
observations are needed for the error target, in addition to
the effective-count floor. If $\tau_{\mathrm{int}}=100$,
that suggests roughly 38,415 recorded generations.
The monitor checks observed estimates, not this forecast.

A per-statistic `statistic_precision` overrides the shared target.
For unbounded watched statistics configured for relative precision,
the default absolute tolerance is replaced by
$\varepsilon\max(1,|\bar Y|)$; for a count averaging 200
with precision 0.01 this permits a half-width of 2.
An explicit per-statistic override instead retains its supplied
absolute units.

Nominal 95% coverage describes a statistical procedure under
its assumptions, not a 95% guarantee attached to each realized
interval. Repeated adaptive checks, approximate correlations,
and inadequate burn-in can affect coverage. The implementation
does not claim a simultaneous 95% guarantee across all watched
statistics or all check times.

### Reading the evidence and the start-versus-end diagnostic

Every scalar run's `report.json` carries the evidence either way, in
`window_statistics`, one entry per statistic recorded: `mean` (the window
average, the estimate to read), `standard_error`, `standard_deviation`,
`effective_sample_size`, `window` (its length), `window_start` and
`window_end` (the generations it spans), `burn_in`, `estimator`,
`noise_adequate` (whether the requested precision was reached), the
`target_standard_error` and `minimum_ess` it was judged against, and
`geweke_z`. Every statistic shares the one window, so the entries are
consistent whichever statistic decided the stop. `geweke_z` compares the start
of the window with its end in units of their combined standard error; an
absolute value above 3 (the Expert Setting `start_drift_alert_z`) means the
averaging may have begun before the model forgot its starting state, so the
burn-in may have been too short. It is a diagnostic only: it never stops or
continues a run, and it is absent for a window too short to split.

The start-versus-end statistic compares the first 10% of the
window with its last 50%, using an autocorrelation-corrected
standard error for each segment:

```math
z_{\mathrm{start/end}}=
\frac{\bar Y_{\mathrm{start}}-\bar Y_{\mathrm{end}}}
{\sqrt{\operatorname{SE}_{\mathrm{start}}^2+
\operatorname{SE}_{\mathrm{end}}^2}} \tag{C.57}
```

For means 0.45 and 0.40 and segment standard errors 0.01
and 0.012, the denominator is approximately 0.01562,
giving $z\approx3.20$. This exceeds the default alert
threshold. The two segment errors are added in quadrature,
not simply added. The diagnostic treats their cross-dependence
as negligible; it is not a definitive equilibrium test.
If both errors are zero, the code returns zero for equal
means and signed infinity for unequal means.

### Projecting the length of an unfinished run

A run that reaches its cap without the requested precision also reports
`projected_generations`: how long the run would have needed. The standard error
of a mean falls approximately as one over the square root of the
window length if its underlying noise and correlation stay the same.
Writing $s_e$ for the current standard error and $s_*$ for its
target, the required precision window is:

```math
W_{\mathrm{precision}}=W\left(\frac{s_e}{s_*}\right)^2 \tag{C.58}
```

To derive this, use
$s_*=s_e\sqrt{W/W_{\mathrm{precision}}}$,
divide by $s_e$, square, and solve for the new length.
Halving the standard error requires four times the window;
reducing it by ten requires a hundred times the window.
The effective-count floor separately requires:

```math
W_{\mathrm{ESS}}=50\,\widehat\tau_{\mathrm{int}} \tag{C.59}
```

The reported projection uses the longer requirement, rounded
up and added to the start:

```math
t_{\mathrm{projected}}=
b+\left\lceil\max(W_{\mathrm{precision}},W_{\mathrm{ESS}})\right\rceil
\tag{C.60}
```

For $W=10000$, $s_e=0.02$, $s_*=0.005$,
and $\widehat\tau_{\mathrm{int}}=100$,
the two requirements are 160,000 and 5,000.
If $b=100000$, the reported projection is generation
260,000. It is a planning estimate, including the implementation's
endpoint convention, not an exact promise about the next successful
check. Unknown slow modes can change the forecast.
At zero target error there is no finite nonzero-noise projection.

The monitor makes
the same projection after every check that fails and logs a warning once, as soon
as the projection passes the cap, so a run that cannot finish in time says so
long before it ends. The command line repeats it in the cap note: "At this
precision D needs about N generations; more loci or more replicates reach it
faster". The CLI prints the watched statistic's own line after
every run; the GUI's Run card tooltip shows the same numbers.
Under `convergence_combinator: all`, the overall projection takes
the latest of the watched-statistic projections; under `any`,
it takes the earliest available one.

## Batches: replicates that average

*For everyone.* A batch (`n_replicates` above 1) reaches the precision by
averaging twice: each replicate averages over time after its own burn-in, and
the batch then averages across replicates. A replicate has no precision check
of its own. It burns in, averages for its window, and stops; its window mean is
the number it contributes. The last generation's value, one noisy draw, no
longer stands in for the replicate.

*For analysts.* How long a replicate should average is a trade. A long window
makes each replicate precise and needs few replicates; a short one needs many.
`fim` matches the window to the batch
([`replicate_averaging_window`](configuration.md#replicate_averaging_window)):

1. The first wave (8 replicates by default) averages for a guess, 20
   relaxation times.
2. The wave's windows estimate the variance $\sigma^2$ and
   integrated autocorrelation factor $\tau_{\mathrm{int}}$ of each
   watched statistic; the planner averages each quantity across the wave.
3. The planner chooses an intended replicate count $R$ (twice the
   wave width, or `replicate_minimum` if larger) and computes the
   per-replicate error target and averaging window derived below.
   The longest requirement among watched statistics sets the
   window, held between 5 and 100 relaxation times.
4. Every later replicate averages for that window. A later replicate runs up to
   the shortest window it could receive and waits for step 3, so the result is
   the same on every backend. The wave width is a number in the configuration,
   not the processor count, so it is the same on every machine too.

Each replicate's window is recorded in its manifest. With
[`precision_method: planned_replicates`](configuration.md#precision_method) the
batch runs exactly `n_replicates` replicates and the window is sized so their
interval is plus or minus the precision.

The assigned window is a generation span. Consistently with
the monitor's inclusive endpoints, a replicate starting at
$b$ stops at $b+A_w$ and can include $A_w+1$ observations.
This one-observation difference is negligible for the planning
approximation, but matters when reproducing the exact schedule.

For ordinary mean-of-values statistics, `summary.json` reports the
mean of the replicates' window means with its Student's-$t$
interval. For $D$ and $G_{ST}$ under the value of means, the replicates' window
means of `H_S` and `H_T` are pooled first and the statistic is taken once;
averaging the statistic inside each replicate and then across replicates would
give a number that depends on the window length. The interval then comes from
the delta method over replicates. The early stop of a batch judges the
per-replicate values, a close approximation of the same interval.

### The across-replicate interval

Let $Z_r$ be one replicate's post-burn-in average, and let $R$
be the number of completed independent replicates. Average those
summaries with equal weight:

```math
\bar Z=\frac1R\sum_{r=1}^{R}Z_r \tag{C.61}
```

Independence is **between replicates** with separate random streams,
not between the correlated generations within a replicate.
For $R\geq2$, estimate the spread of replicate summaries:

```math
s_Z^2=\frac1{R-1}\sum_{r=1}^{R}(Z_r-\bar Z)^2 \tag{C.62}
```

Subtract the mean, square each departure, and add them.
The divisor $R-1$ accounts for estimating the mean from the same
data; the departures must sum to zero.
The standard error of $\bar Z$ is $s_Z/\sqrt R$.
With confidence $\gamma$, the interval is:

```math
\bar Z\ \mathbin{\pm}\
t_{(1+\gamma)/2,R-1}\frac{s_Z}{\sqrt R} \tag{C.63}
```

The $t$ here denotes a Student's-$t$ critical value, **not**
a generation index. The second subscript is its degrees of
freedom. At 95% confidence and $R=25$, the critical value is
about 2.064. If $\bar Z=0.4$ and $s_Z=0.1$, the standard
error is 0.02 and the interval is approximately
$0.4\pm0.04128$, or $(0.35872,0.44128)$.
More variable replicate means require either more replicates
or longer within-replicate windows.

The Student interval is exact for independent normally distributed
summaries of equal variance; its use with finite simulation summaries,
adaptive windows, and early batch stopping is approximate.
Equal weighting makes every replicate estimate count once, even
when window lengths differ. Weighting by total generations would
instead give longer replicates more influence.

### Solving for a matched averaging window

For planning only, suppose each replicate mean has the same
standard error $s_{\mathrm{lane}}$. Setting the batch half-width
equal to the requested precision gives:

```math
\varepsilon=
t_{(1+\gamma)/2,R-1}\frac{s_{\mathrm{lane}}}{\sqrt R} \tag{C.64}
```

Multiply by $\sqrt R$ and divide by the critical value:

```math
s_{\mathrm{lane}}=
\frac{\varepsilon\sqrt R}{t_{(1+\gamma)/2,R-1}} \tag{C.65}
```

Now use (C.49) for a replicate with averaging-window length $A_w$:
$s_{\mathrm{lane}}^2=\sigma^2\tau_{\mathrm{int}}/A_w$.
Solving for length gives:

```math
A_w=\tau_{\mathrm{int}}
\left(\frac{\sigma}{s_{\mathrm{lane}}}\right)^2 \tag{C.66}
```

For $R=25$, $\varepsilon=0.01$, $\gamma=0.95$,
$\sigma=0.1$, and $\tau_{\mathrm{int}}=100$,
the lane target is approximately $0.05/2.064=0.024225$.
The resulting window is approximately
$100(0.1/(0.05/2.064))^2=1704.0384$ generations,
using the displayed rounded critical value throughout.
If the model relaxation time is 100 generations, this
is about $17.04\tau$, between the default clamps of
$5\tau=500$ and $100\tau=10000$.
The planner rounds the chosen length upward to a whole generation.

The first wave's noise estimates are only estimates.
For every watched statistic the planner averages window
variances and autocorrelation factors across that wave,
uses their product in (C.66), and takes the largest
result. It does not fit a new biological timescale.
The cap's available post-burn-in space can shorten an automatic
window further. An explicitly fixed window is not silently
clamped; a run that cannot finish it before the cap reports that fact.

Under `precision_method: planned_replicates`, the intended
count is the requested `n_replicates`; under early stopping,
it is a planning target, not a promised final batch size.
The realized interval must still be checked and reported.
This is why a matched window is not itself a precision guarantee.

## Which average? Two forms for each differentiation measure

*For analysts.* $D$ and $G_{ST}$ are functions of two heterozygosities,
$X=f(H_S,H_T)$. Averaging a nonlinear function need not equal
applying it to averages. This is a nonlinear averaging gap;
Jensen's inequality gives a direction only when the needed convexity
or concavity assumptions hold. No such direction is assumed here.
A run can target either of two quantities:

- **Mean of values** (default): the average of $X$ over the evidence window.
  It is what a sampled population shows on average, and what published
  replicate means are.
- **Value of means**: $f$ of the window's average $H_S$ and $H_T$. It is the
  form obtained by substituting predicted mean heterozygosities
  into a formula. Its standard error comes from the delta method,
  using the covariance and autocorrelation of both heterozygosities.

Choose with [`convergence_estimate`](configuration.md#convergence_estimate):
`mean_of_values`, `value_of_means` or `auto`. The choice drives the stop, the
headline `mean` and the interval. Whatever the choice, each of `D` and `G_ST`
in `window_statistics` also carries `mean_of_values` and `value_of_means` (each
`{mean, standard_error}`), `selected_form`, and `undefined_generations`: the
window generations in which the statistic had no value, which the first form
drops (`G_ST` where `H_T` is zero). The value of means can remain defined
when some individual values are not, so `auto` selects it when any generation is undefined or when more
than 1% of the window has a denominator below 0.01. Statistics that are not
functions of the identities have one form.

In the measured single-locus example motivating this option, the gap
was about 0.011 for $D$ and 0.0001 for $G_{ST}$, near the
default precision for $D$. Those are not universal constants.
With independent pooled loci and a well-behaved denominator,
the gap commonly shrinks on the order of the reciprocal locus count.

### Calculate both forms from the same observations

Use $S_u=H_{S,u}$ and $T_u=H_{T,u}$ for one window observation;
these symbols are heterozygosities, not copy totals.
Let $\bar S$ and $\bar T$ be their respective window averages.
The two estimators are:

```math
\widehat X_{\mathrm{mean\ of\ values}}
=\frac1W\sum_{u=1}^{W}f(S_u,T_u) \tag{C.67}
```

```math
\widehat X_{\mathrm{value\ of\ means}}=f(\bar S,\bar T) \tag{C.68}
```

Consider two equally weighted observations with
$(S_1,T_1)=(0,1/2)$ and $(S_2,T_2)=(1/2,3/4)$.
These are attainable two-deme states: private fixed alleles
give the first pair, and two private equally frequent alleles
per deme give the second.
For $G_{ST}$ the function is:

```math
f_G(S,T)=1-\frac ST,\qquad T>0 \tag{C.69}
```

Its observed values are 1 and $1/3$, so their mean is:

```math
\widehat G_{\mathrm{mean\ of\ values}}
=\frac{1+1/3}{2}=\frac23 \tag{C.70}
```

But $\bar S=1/4$ and $\bar T=5/8$, giving:

```math
\widehat G_{\mathrm{value\ of\ means}}
=1-\frac{1/4}{5/8}=\frac35 \tag{C.71}
```

The difference $2/3-3/5=1/15$ is not rounding error.
The first divides each observation by its own total diversity
before averaging. The second pools the diversity components
before dividing. More observations reduce sampling uncertainty
in each target; they do not force the two population targets
to coincide.

For Jost's statistic, write $a_d=d/(d-1)$:

```math
f_D(S,T)=a_d\frac{T-S}{1-S},\qquad d>1,\ S<1 \tag{C.72}
```

Apply the same distinction to (C.72). In the particular two
private-allele observations above, both $D$ forms equal one.
This equality is a feature of that example, not a general
identity for $D$.

For a nonzero $D$ gap, keep two demes but use observations
$(S,T)=(0,1/2)$ and $(1/2,1/2)$ instead. The first again
has distinct fixed alleles; the second has identical rows
$(1/2,1/2)$ in both demes. The two $D$ values are 1 and 0,
averaging to $1/2$. Their component means are
$(\bar S,\bar T)=(1/4,1/2)$, so applying (C.72) once gives
$2(1/2-1/4)/(1-1/4)=2/3$, a different target.

Undefined observations need special care. Dropping generations
with $T=0$ from (C.67) means the mean-of-values $G_{ST}$
describes only generations where it is defined.
Equation (C.68) can still use their $(S,T)=(0,0)$ contributions
in the component means. If every generation has $T=0$,
then $\bar T=0$ and that form is undefined too. It must not
be interpreted as automatically providing a finite answer.

### The delta method: propagate uncertainty through a ratio

The first-order change of a smooth function near
$(\bar S,\bar T)$ is:

```math
f(\bar S+\delta S,\bar T+\delta T)-f(\bar S,\bar T)
\approx f_S\delta S+f_T\delta T \tag{C.73}
```

$f_S$ and $f_T$ are partial derivatives: rates of change
when one input changes while the other stays fixed.
Evaluate both at the estimated component means.
The approximation replaces the curved surface locally by a
plane. Applying variance to this linear approximation gives:

```math
\operatorname{Var}[f(\bar S,\bar T)]
\approx f_S^2\operatorname{Var}(\bar S)
+f_T^2\operatorname{Var}(\bar T)
+2f_Sf_T\operatorname{Cov}(\bar S,\bar T) \tag{C.74}
```

Covariance measures whether the component means rise together.
Ignoring it would treat two measurements of the same populations
as unrelated. Autocorrelation also matters: the variances
here are variances of **means**, not of individual generations.

For (C.69), differentiation gives:

```math
(f_{G,S},f_{G,T})=\left(-\frac1T,\frac S{T^2}\right) \tag{C.75}
```

The first derivative is immediate because $T$ is fixed.
For the second, differentiating $T^{-1}$ gives $-T^{-2}$;
the minus in $-ST^{-1}$ cancels it.
For (C.72), the quotient rule gives:

```math
(f_{D,S},f_{D,T})
=\left(a_d\frac{T-1}{(1-S)^2},\frac{a_d}{1-S}\right)
\tag{C.76}
```

For its first component, differentiating $(T-S)/(1-S)$
produces numerator $-(1-S)+(T-S)=T-1$ over
$(1-S)^2$. These derivatives reveal why a small denominator
can amplify input errors.

The code avoids separately estimating every cross-lag
covariance by constructing a linear series:

```math
L_u=f_S S_u+f_T T_u \tag{C.77}
```

It applies the same Geyer estimator to $L_u$. The standard
error of $\bar L$ approximates the error of
$f(\bar S,\bar T)$ by (C.73), including component covariance
and temporal correlation. It then reports the nonlinear value
$f(\bar S,\bar T)$ as the mean, not the generally different
mean of $L_u$.

At $(\bar S,\bar T)=(1/4,5/8)$ the $G_{ST}$ derivatives
are $(-1.6,0.64)$. Suppose the **component means** have
variances 0.0004 and 0.0009 and covariance 0.0003.
Equation (C.74) gives
$2.56(0.0004)+0.4096(0.0009)-2.048(0.0003)=0.00077824$,
with standard error about 0.027897.
This is an illustrative covariance calculation, not an
error estimate from the two-observation example.
The delta method is local and approximate; near undefined
ratios, linearization may be inadequate.

For batch value-of-means summaries, replace the temporal
component observations by the per-replicate component means.
Average those components across replicates first, then apply
$f$ once. Apply covariance propagation across independent
replicates for its interval. Averaging the per-replicate
nonlinear values instead targets a different quantity.

## Checking the numbers yourself

- [`dev/bin/relaxation-table`](../dev/bin/relaxation-table) prints the
  identity operator's gap timescale beside the scalar approximation and the
  migration gap for six reference models. The unit tests in
  [`test_defaults.py`](../test/convergence/test_defaults.py) fail if the
  closed form is off by more than 5%.
- [`test_burn_in_monitor.py`](../test/convergence/test_burn_in_monitor.py)
  pins the schedule (burn-in, first check, doubling) and the stop rule
  on exact series, and
  [`test_window_statistics.py`](../test/convergence/test_window_statistics.py)
  checks the estimator against
  hand-worked values and known autoregressive series.
- [`test_convergence_rule_calibration.py`](../test/validation/test_convergence_rule_calibration.py)
  checks committed calibration evidence and seeded scenarios, including
  Golden Part VI and the Dear-Nolan low example. Its slow checks exercise
  the combined simulation and stopping rule.
- [`test_convergence_docs.py`](../test/test_convergence_docs.py) checks that
  the documented burn-in and cap formulas contain the shipped policy
  constants, including their $\TeX$ rendering here.

These checks address different claims. Matching the table verifies the
policy calculation; matching an analytic covariance series verifies an
estimator; matching an equilibrium simulation adds scientific evidence
for the combined system. None substitutes for the other two.
Fixed-seed checks can be reproduced without rerunning until a favorable
sample appears.

## Limits

Each limit links back to its main explanation; that explanation also
links here so the qualification can be read in context.

### Scalar approximation limit

The scalar closed form is an approximation for the equal-size
symmetric island model, checked within 5% of the identity operator
in the reference tests. It is not an exact discrete-generation
formula for all parameter values.

Return to [units and the scalar island approximation](#units-and-the-scalar-island-approximation).

### Matrix and size limit

Unequal sizes do **not** merely substitute their sum into that scalar
formula. They use the size-weighted scalar migration matrix or the
explicitly supplied matrix and the $d^2$ identity eigenproblem,
provided $d\leq24$. An explicit matrix is not reweighted by sizes.
Larger cases needing that route require explicit run limits and
cannot receive this model-derived timescale.

Return to [the choice of timescale calculation](#units-and-the-scalar-island-approximation).
The [flattened operator derivation](#flattening-the-table-into-one-vector)
explains why the eigenproblem grows so quickly.

### Slowest-locus limit

The slowest locus is selected by its smallest mutation rate within
this identity approximation, not by observing that every possible
statistic has settled.

Return to [the mutation-rate choice in the scalar approximation](#units-and-the-scalar-island-approximation).

### Empirical-cap limit

The 200,000 floor is measured from two regimes, not derived from first
principles. A third, faster-mixing regime needed far less (12,000), so the
floor has real margin, but a configuration not yet measured could need more
before its precision is reached, in which case the run reports the cap
honestly, with the average and error bar it has, rather than a false
convergence.

Return to [the safety-cap calculation](#from-burn-in-to-a-safety-cap).

### Check-scheduling limit

Checks occur at approximately doubled window lengths, not every
generation, to keep checking cheap. If precision first becomes
adequate between checks and remains adequate, the next check can
require nearly twice as much evidence, apart from integer endpoint
effects. Estimated precision need not improve monotonically.

Return to [window boundaries and check scheduling](#window-boundaries-and-check-scheduling).

### High-migration evidence limit

The Dear-Nolan high-migration scenario (100 demes of 2,000 gene copies) is
not covered by the cited replicated measurement; at that measurement's
backend and hardware it cost about 0.4 seconds per generation.
That is not a current cross-backend performance guarantee.
Its scalar timescale is dominated by mutation (about 499 generations).
The dense 100-deme recursion exceeds the implementation guard, so
that scale should not be described as a fresh direct eigensolver check.

Return to [the scalar timescale and its calculation routes](#units-and-the-scalar-island-approximation).

### Finite-alleles limit

The finite-alleles mutation model changes the mutation term and is not
covered by the infinite-alleles derivation: independent mutations can
produce matching labels. A timescale supplied by the current defaults
must therefore be read as a heuristic for that model.

Return to [the drift-and-mutation identity derivation](#drift-and-mutation-distinguish-self-pairs-from-distinct-pairs).

### Stochastic-migration limit

Stochastic migrant amounts also need a second-moment qualification.
Conditional on a realized mixing matrix, (C.30) applies, but averaging
generally requires $\mathbb E[MJM^{\mathsf T}]$, not
$\mathbb E[M]J\mathbb E[M]^{\mathsf T}$. In particular, the
two weights for a same-deme pair share a random migrant fraction.
The defaults do not build that augmented second-moment operator;
do not interpret their timescale as an exact recursion for this variant.

Return to [the two-parent migration-weight derivation](#pairwise-identity-why-two-migration-weights-appear).

### Finite-evidence limit

A constant or apparently stationary evidence window can miss rare
transitions. The error bar is conditional on the stationarity and
correlation assumptions used to estimate it. Increasing the
effective-count floor cannot prove those assumptions.

Return to [the autocorrelation estimator and its cutoff](#why-the-estimator-uses-more-than-lag-1).

### Allele-label stationarity limit

Infinite-alleles stationarity refers to frequency structure and
statistics modulo allele relabeling. Absolute newly assigned allele
IDs keep increasing when mutation continues; those labels do not
have a stationary registry distribution.

Return to [what the pairwise identity table measures](#pairwise-identity-why-two-migration-weights-appear).

## Notation and implementation map

| Mathematics | Meaning | Configuration, report, or implementation |
| --- | --- | --- |
| $N_k$, $n_k=cN_k$ | Individuals and copies at one locus | `N`, `ploidy`, engine gene-copy sizes |
| $\mu_l$, $\mu_{\min}$ | Mutation probability per locus and its smallest value | `mu`, or conversion from `mu_b` |
| $\tau$ | Model-derived gap or scalar-approximation timescale | `relaxation_time` in [`defaults.py`](../src/fim/convergence/defaults.py) |
| $b$, $C$ | First included generation and hard generation cap | `convergence_burn_in`, `max_generations` |
| $\varepsilon$, $\gamma$ | Allowed half-width and nominal confidence | `precision`, `confidence` |
| $W$, $W_{\mathrm{eff}}$ | Window observation count and effective sample size | `window`, `effective_sample_size` |
| $\sigma$, $\widehat{\operatorname{SE}}$ | Individual spread and estimated mean uncertainty | `standard_deviation`, `standard_error` |
| $\widehat\tau_{\mathrm{int}}$ | Estimated integrated correlation factor | `WindowStatistics.tau_int`, equal to `window / effective_sample_size` |
| $J$, $M$, $A$ | Pair identities, migration weights, and flattened identity operator | [`recursion_relaxation_time()`](../src/fim/convergence/defaults.py) |
| $Z_r$, $A_w$ | Replicate summary and its averaging length | `replicate_averaging_window`, [`batch_window.py`](../src/fim/convergence/batch_window.py) |
| $f(S,T)$ | A nonlinear differentiation function of heterozygosities | `convergence_estimate`, [`window_statistics.py`](../src/fim/convergence/window_statistics.py) |

The evidence-window schedule and per-statistic verdicts live in
[`BurnInMonitor`](../src/fim/convergence/monitor.py).
The parameter reference, not the notation table, is authoritative
for accepted literal keys and expert overrides:
[configuration reference](configuration.md).

## Metadata

Expanded the existing convergence reference with $\TeX$ notation, numbered
derivations, local symbol definitions, and worked calculations. Checked
the explanations against the relaxation-defaults, monitor, window-estimator,
and batch-window implementations. Distinguished mathematical approximations,
implemented policy, empirical measurements, and unsupported assumptions.
The generating model identifier is not exposed in this session and is
recorded as unavailable rather than inferred from earlier documents.

```text
generator-name: Copilot SDK in VS Code
generator-version: unavailable
generator-model-token: unavailable
generator-provider: OpenAI
generation-date: 2026-10-18
generator-responsibility: revision
```
