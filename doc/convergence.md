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
- [Which average? Two forms for `D` and `G_ST`](#which-average-two-forms-for-d-and-g_st)
- [Checking the numbers yourself](#checking-the-numbers-yourself)
- [Limits](#limits)

## If you only read one section

A run first waits out a **burn-in**: the time the population needs to forget
where it started. Only then does it start **averaging**, and it keeps
averaging until the average is known to the precision you asked for (plus or
minus `precision`, at `confidence`). The result is the average, with its error
bar. If the run reaches its generation cap before the average is that
precise, it says so ("hit the cap") and still reports the average it has,
with the error bar it really has. A run that finishes in a hundred
generations is almost certainly not the equilibrium you wanted; check
`convergence_burn_in` and `max_generations`.

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

> Convergence: burn-in 104,400 generations, cap 399,000 (derived; this model
> needs about 19,693 generations to forget its starting state)

If a run stops at the cap without reaching the precision, the report says how
precise the average is, so you can decide whether to allow more generations,
ask for less precision, use more loci, or run several replicates (a batch
reaches a given precision sooner than one long run).

## Quick reference

*For sysops and technicians who need a reminder, not a tutorial.*

| Setting | Default | Meaning |
|---|---|---|
| `precision` | `0.01` | Plus or minus, in each watched statistic's own units, at `confidence` |
| `confidence` | `0.95` | How sure the plus-or-minus is (0.90, 0.95 or 0.99) |
| `convergence_burn_in` | `auto` | Generations discarded before averaging: `ceil(k tau)` with `k = max(5, ln(2 / precision))` |
| `max_generations` | `auto` | Safety cap: `max(200000, burn_in + ceil(15 tau))`, at most 10,000,000 |

`tau` is the relaxation time of the slowest locus. Any whole number you write
replaces the derived value. If you set only one of the two, the other adapts: a
derived cap is raised to leave `15 tau` after an explicit burn-in. The
`expert:` mapping changes the policy constants (the floor of 5, the factor
15, the first check, the effective-sample-size floor and the rest; see
[configuration.md](configuration.md#expert)).

Symptoms of a bad value:

- **Burn-in too short:** the average starts inside the transient and is
  biased.
- **Cap too small:** the run ends "hit the cap", possibly before the burn-in
  even ends (then there is no average at all); the run logs a warning.
- **Cap too large:** only a run that never reaches its precision takes longer
  to end.

`fim run` prints the derived values unless `--quiet`. They are also written as
plain integers to `manifest.json`, so any run can be reproduced exactly by
passing those integers.

## How a run decides it is finished

*For sysops, technicians and developers.*

```text
burn_in = ceil(k * tau)                  # k = max(5, ln(2 / precision))
start   = burn_in                        # the evidence window is [start, t]
first check at start + max(50, ceil(2 tau))
at each check, for every watched statistic:
    mean, SE, ESS = Geyer's estimate over the window [start, t]
    pass if SE <= precision / z(confidence) and ESS >= 50
stop if all (or any, under `convergence_combinator: any`) pass
next check after the window has doubled
stop as capped at max_generations
```

- Nothing is averaged before the burn-in, and generation 0 is never in an
  average.
- `z` is the normal quantile of the confidence (1.96 at 95%), so `precision`
  keeps the meaning "plus or minus this much".
- `ESS`, the effective sample size, is how many independent values the
  window's correlated generations are worth; a standard error from fewer than
  50 is not trusted. Geyer's initial positive sequence estimator sums the whole
  autocorrelation function, so a slow second mode is seen (see
  [How precise is the reported value?](#how-precise-is-the-reported-value)).
- Every watched statistic is judged at every check, so the stop generation
  never depends on the order they are listed in.
- With no relaxation time (no migration and no mutation, or an explicit
  matrix beyond 24 demes) and `convergence_burn_in: auto`, the window starts
  at the first tenth of the run at each check.
- At the stop, every recorded statistic, watched or not, gets its mean and
  standard error over the same window, in `report.json`'s
  `window_statistics`.

## How the numbers are derived

*For developers and reviewers.*

Symmetric island model (scalar `m`, equal deme sizes), closed form:

```text
T   = N_total + (d - 1) / (2 m)
tau = 1 / (2 mu + 1 / T)
```

`N_total` is the sum of all deme sizes and `mu` is the smallest mutation rate
over loci: the slowest locus is the last to forget its starting state, so it
sets the time. The relaxation time is computed whenever the model has one, not
only when a burn-in or cap is derived. Explicit migration matrices and unequal
deme sizes use the slowest mode of the identity recursion (next section), a
`d² × d²` eigenproblem limited to 24 demes. No migration and no mutation has no
relaxation time, and a derived cap is refused. The code is
`fim.convergence.defaults`.

The burn-in multiple is `k = max(5, ln(2 / precision))`: the slowest mode's
leftover from a worst-case unit offset is `e^-k`, so `k >= ln(2 / precision)`
leaves at most `precision / 2` of bias, and a tighter precision lengthens the
burn-in on its own. The floor of 5 keeps a loose precision from shortening it
below what a measured two-mode model needed (the average's bias at 5.3 `tau`
was -0.00016).

The `200000` floor on `max_generations` is not derived from `tau` at all —
it comes from measuring how many generations a single locus's `D` needs before
its average is known to 0.01, in two scenarios whose relaxation times sit two
orders of magnitude apart (`tau` 85 and 19,693) yet which both needed close to
130,000 generations. A model that genuinely needs longer than that (a very
large `d` or very slow mutation) still gets the burn-in plus `15 tau` once
that exceeds the floor.

## Where the formula comes from

*For the mathematically curious. The only linear algebra needed is explained
here.*

**One number.** Suppose a quantity is updated each generation by
`x' = λx + c` with `0 < λ < 1`. It settles at `x* = c / (1 − λ)`. The distance
`e = x − x*` obeys `e' = λe`, so `e_t = λ^t e_0`. With `λ = 1 − ε` for small
`ε`, `λ^t ≈ e^(−εt)`: exponential decay with time constant `tau = 1 / (1 − λ)`.

**Many numbers.** If `x` is a vector and `x' = Ax + c`, the distance still
obeys `e' = Ae`. For most matrices there are special directions `v` (the
eigenvectors) that `A` only scales: `Av = λv`. Write the starting distance as a
sum of those directions and each decays as its own `λ^t`, independently. The
slowest, the one with the largest `|λ|` (the spectral radius `ρ`), is the only
one left after a while, so the whole system relaxes with `tau = 1 / (1 − ρ)`.

**The model as such a recurrence.** Track `J`, the `d × d` matrix whose entry
`J[i][j]` is the probability that a gene copy drawn from deme `i` and one from
deme `j` are identical (the diagonal is within-deme identity). One generation
is:

- Migrate: `J → M J Mᵀ`, where `M` is the row-stochastic migration matrix.
- Mutate: multiply by `(1 − mu)²`, about `1 − 2 mu`.
- Drift: each diagonal entry becomes `1/N_i + (1 − 1/N_i) ×` (the value above).

The constant `1/N` makes the update affine, and its linear part flattens to a
`d² × d²` matrix whose spectral radius gives `tau`. `H_S`, `H_T`, `G_ST` and
`D` are smooth functions of `J`, so each settles at the same rate.

**Why not the migration gap alone?** The smallest nonzero eigenvalue of
`I − M` is how fast one gene copy's *location* forgets its start. Identity is
lost only when two copies find each other and coalesce, or mutate. Meeting
takes far longer than mixing, so `1 / gap` is too small by about `d / 2`.

**The closed form.** Follow two gene copies in different demes backward in
time. They join one deme at rate `2m / (d − 1)` (mean wait `(d − 1) / (2m)`),
and the classical island-model result adds the whole population's coalescence
time: `T = N_total + (d − 1) / (2m)`. Mutation destroys identity at an
independent rate `2 mu`, and independent rates add, giving
`tau = 1 / (2 mu + 1 / T)`.

## How precise is the reported value?

*For everyone.* A statistic that has stopped trending still wobbles from
generation to generation, and neighboring generations are almost the same
population, so they are not independent draws. Averaging `W` such values does
not shrink the uncertainty by `1 / sqrt(W)`; it shrinks by
`1 / sqrt(W / tau_int)`, where the *integrated autocorrelation time* `tau_int`
says how many consecutive generations are worth one independent draw. `fim`
estimates `tau_int` from the window itself and reports the standard error
that follows.

The estimator matters. A single-lag estimate (the correlation of neighboring
values) is exact only for a first-order process. A statistic with two
relaxation times, a fast mode and a slow one, looks nearly decorrelated at lag
1 and is badly underestimated: on a seeded two-mode series whose true
`tau_int` is 469, the single-lag formula gives about 75 and Geyer's estimator
gives 438. That is why the error bars of earlier versions read too small.
`fim.convergence.window_statistics` implements Geyer's (1992) initial positive
sequence estimator.

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
continues a run, and it is absent for a window too short to split. The CLI prints the watched statistic's own line after
every run; the GUI's Run card tooltip shows the same numbers.

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
2. The wave's windows give `sigma`, the standard deviation of a watched
   statistic, and `tau_int`, its integrated autocorrelation time.
3. The standard error one replicate needs is `precision * sqrt(R) / t(R - 1)`
   for the `R` replicates the batch aims for (twice the wave width, or
   `replicate_minimum` if larger). The window that reaches it is
   `tau_int * (sigma / SE)^2`, set by the slowest watched statistic and held
   between 5 and 100 relaxation times.
4. Every later replicate averages for that window. A later replicate runs up to
   the shortest window it could receive and waits for step 3, so the result is
   the same on every backend. The wave width is a number in the configuration,
   not the processor count, so it is the same on every machine too.

Each replicate's window is recorded in its manifest. With
[`precision_method: planned_replicates`](configuration.md#precision_method) the
batch runs exactly `n_replicates` replicates and the window is sized so their
interval is plus or minus the precision.

`summary.json` is the mean of the replicates' window means with its Student's-t
interval. For `D` and `G_ST` under the value of means, the replicates' window
means of `H_S` and `H_T` are pooled first and the statistic is taken once;
averaging the statistic inside each replicate and then across replicates would
give a number that depends on the window length. The interval then comes from
the delta method over replicates. The early stop of a batch judges the
per-replicate values, a close approximation of the same interval.

## Which average? Two forms for `D` and `G_ST`

*For analysts.* `D` and `G_ST` are functions of two heterozygosities,
`X = f(H_S, H_T)`, and the average of a function is not the function of the
averages (Jensen's gap). A run can therefore estimate either of two expected
values:

- **Mean of values** (default): the average of `X` over the evidence window.
  It is what a sampled population shows on average, and what published
  replicate means are.
- **Value of means**: `f` of the window's average `H_S` and `H_T`. It is what
  the closed form predicts. Its standard error comes from the delta method,
  using the covariance and autocorrelation of both heterozygosities.

Choose with [`convergence_estimate`](configuration.md#convergence_estimate):
`mean_of_values`, `value_of_means` or `auto`. The choice drives the stop, the
headline `mean` and the interval. Whatever the choice, each of `D` and `G_ST`
in `window_statistics` also carries `mean_of_values` and `value_of_means` (each
`{mean, standard_error}`), `selected_form`, and `undefined_generations`: the
window generations in which the statistic had no value, which the first form
drops (`G_ST` where `H_T` is zero). Only the value of means stays defined
there, so `auto` switches to it when any generation is undefined or when more
than 1% of the window has a denominator below 0.01. Statistics that are not
functions of the identities have one form.

On one locus the gap is about 0.011 for `D` and 0.0001 for `G_ST`, near the
default precision for `D`; with many loci it shrinks roughly as one over the
number of loci pooled.

## Checking the numbers yourself

- `dev/bin/relaxation-table` prints the true relaxation time from the identity
  recursion beside the closed form and the migration gap, for six reference
  models. The unit tests in `test/convergence/test_defaults.py` fail if the
  closed form is off by more than 5%.
- `test/convergence/test_burn_in_monitor.py` pins the schedule (burn-in, first
  check, doubling) and the stop rule on exact series, and
  `test/convergence/test_window_statistics.py` checks the estimator against
  hand-worked values and known autoregressive series.
- `test/validation/test_convergence_defaults.py` (slow) runs Golden Part VI and
  Dear-Nolan low with the shipped defaults.

## Limits

- The closed form is exact for the symmetric island model only.
- Unequal deme sizes use their sum, an approximation; the slowest locus (the
  smallest mutation rate) sets `tau`.
- The `200000` floor is measured from two regimes, not derived from first
  principles. A third, faster-mixing regime needed far less (12,000), so the
  floor has real margin, but a configuration not yet measured could need more
  before its precision is reached, in which case the run reports the cap
  honestly, with the average and error bar it has, rather than a false
  convergence.
- Checks happen when the window has doubled, not every generation, to keep the
  checking cheap; a run can therefore run up to a doubling past the length it
  first needed (typically 1.4 times, at most 2 times).
- The Dear-Nolan high-migration scenario (100 demes of 2,000 gene copies) is
  not covered by the replicated measurement because a run costs about 0.4
  seconds per generation. Its relaxation time is dominated by mutation
  (about 499 generations) and matches the recursion.
- The finite-alleles mutation model changes the mutation term and is not
  covered by the closed form.
