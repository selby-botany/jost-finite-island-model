# Convergence defaults

How `fim` decides when a run is finished, and why the default window and
generation cap are derived from your model instead of being fixed numbers.

## Contents

- [If you only read one section](#if-you-only-read-one-section)
- [Why does my run take so long?](#why-does-my-run-take-so-long)
- [Quick reference](#quick-reference)
- [How the numbers are derived](#how-the-numbers-are-derived)
- [Where the formula comes from](#where-the-formula-comes-from)
- [Why the window is three relaxation times](#why-the-window-is-three-relaxation-times)
- [Checking the numbers yourself](#checking-the-numbers-yourself)
- [Limits](#limits)

## If you only read one section

A finished run is one where the statistic has stayed steady for as long as the
population needs to forget where it started. That time can be tens of
thousands of generations. If a run finishes in a hundred generations, the
result is almost certainly not the equilibrium you wanted; check the
`convergence_window` and `max_generations` values.

## Why does my run take so long?

*For everyone, including non-technical readers.*

A simulated population does not settle at once. When several islands begin
with different genes, it takes a long time for gene flow and mutation to shape
what you finally see. With very little migration between islands, "a long
time" means tens of thousands of generations.

`fim` waits until the numbers have stayed steady for a stretch of generations
that matches how slowly your population changes. You do not have to choose
that stretch: leave the setting on `auto`. The app shows the expected length
next to the **Run simulation** button before you start, for example:

> Convergence: window 59,078 generations, cap 295,390 (derived; this model
> needs about 19,693 generations to forget its starting state)

If a run stops at the cap without settling, the message says how long the
model needs, so you can decide whether to allow more generations.

## Quick reference

*For sysops and technicians who need a reminder, not a tutorial.*

| Setting | Default | Meaning |
|---|---|---|
| `convergence_window` | `auto` | Generations the statistic must stay steady: `max(50, ceil(3 tau))` — the *starting* size of the evidence window; it grows past this on its own if the trend flattens before the mean is precise (see below) |
| `max_generations` | `auto` | Safety cap: `max(200000, ceil(15 tau))`, at most 10,000,000 |
| `precision` | `0.01` | How much the two halves of the window may differ, and (halved) how precisely the window's own mean must be known |

`tau` is the relaxation time. Any whole number you write replaces the derived
value. If you set only one of the two, the other adapts: a derived window is
clamped to an explicit cap, and a derived cap is raised to five windows.

Symptoms of a bad value:

- **Window too short:** the run ends quickly and D is still falling at the end
  of the trajectory plot.
- **Cap too small:** the run ends "hit the cap" and the message names a
  relaxation time larger than the cap.
- **Cap too large:** only a run that never settles takes longer to end.

`fim run` prints the derived values unless `--quiet`. They are also written as
plain integers to `manifest.json`, so any run can be reproduced exactly by
passing those integers.

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
only when a window or cap is derived. Explicit migration matrices and unequal deme sizes use the slowest mode
of the identity recursion (next section), a `d² × d²` eigenproblem limited to
24 demes. No migration and no mutation has no relaxation time, and `auto` is
refused. The code is `fim.convergence.defaults`.

The `200000` floor on `max_generations` is not derived from `tau` at all —
it comes from measuring how long the evidence window (above) actually needs
to grow for a single locus's own noise to average out, in two scenarios
whose relaxation times sit two orders of magnitude apart (`tau` 85 and
19,693) yet which both needed close to 130,000 generations regardless. A
model that genuinely needs longer than that (a very large `d` or very slow
mutation) still gets `15 tau` once that exceeds the floor.

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

## Why the window is three relaxation times

The stopping rule takes the last `w` generations, compares the mean of the
first half with the mean of the second half, and stops when they differ by at
most the tolerance `tol`. Near equilibrium a statistic behaves as
`x* + Δ e^(−t/τ)`. If `R` is the true remaining distance from `x*` at the end
of the window, the halves differ by `R · f(w/τ)` with
`f(r) = e^r · (2/r) · (1 − e^(−r/2))²`, so the rule accepts any `R` up to
`tol / f(w/τ)`:

| `w / τ` | Remaining distance accepted, in units of `tol` |
|---|---|
| 0.0026 (the old fixed window of 50 at `d=5`, `m=0.0001`) | 768 |
| 0.25 | 7.1 |
| 0.5 | 3.1 |
| 1 | 1.19 |
| 2 | 0.34 |
| 3 | 0.12 |

With the old window the rule accepted a distance larger than the whole range of
D, so runs stopped after about a hundred generations. A window of one `tau`
makes the tolerance mean roughly what it says. One stochastic run also carries
sampling noise, so the multiple was measured, not only derived: at three
relaxation times the mean stopping D is within 0.05 of the analytic
equilibrium in every regime tested (Golden Part VI, Dear-Nolan low, a ring, and
unequal mutation rates), and the slowest run stopped after 9.0 `tau`, so the
cap is 15 `tau`. The data are in
`test/validation/convergence-defaults-evidence.json`.

## Is the reported value actually precise enough?

*For everyone.* The trend check above answers "has this stopped moving in
one direction." It does not answer "is the number I am about to read
actually known to the tolerance I asked for" — a statistic that has
genuinely stopped trending can still wobble, generation to generation, by
more than the tolerance, and two neighboring halves of a window can land
close together by chance long before that wobble has been averaged away.
A single locus watched by a single replicate is the case this bites
hardest: `doc/examples/golden-part-vi`'s own README shows a real run that
did exactly this — "converged" at generation 400 on a lucky half-window
match, at a value that was not actually close to the model's long-run
average.

`fim` now checks for this directly. Alongside the trend check, it asks
whether the trailing window's own mean is known to half the configured
tolerance, correcting for how correlated consecutive generations are (an
effective-sample-size estimate from the window's own lag-1
autocorrelation — `fim.convergence.window_statistics`). A run only stops
with `"statistic converged"` once **both** checks pass.

The derived `3 tau` window is only ever the *starting* point for that
second check, not its final size: once the trend has genuinely flattened,
`fim` keeps that same window growing, generation by generation, for as
long as it takes to become precise — a single locus can need a window
many times longer than `3 tau` to know its own mean to a tight tolerance,
and no fixed multiple of `tau` predicts how much longer in advance (two
real scenarios needing close to 130,000 generations despite their own
relaxation times sitting two orders of magnitude apart — see
[How the numbers are derived](#how-the-numbers-are-derived)). A run that
still hits `max_generations` without ever reaching that precision reports
`"hit the cap"` honestly, the same as a run that never stopped trending —
now meaning the growing window itself ran out of room, not merely that
the original small one was never rechecked.

Every scalar run's `report.json` carries the evidence either way, in
`window_statistics`, one entry per statistic recorded — `mean` (the
window average, a better estimate than the single reported point value
even when not yet precise enough), `standard_error`, `effective_sample_size`,
`window`, and `noise_adequate`. The CLI prints the watched statistic's own
line after every run; the GUI's Run card tooltip shows the same numbers.

## Checking the numbers yourself

- `dev/bin/relaxation-table` prints the true relaxation time from the identity
  recursion beside the closed form and the migration gap, for six reference
  models. The unit tests in `test/convergence/test_defaults.py` fail if the
  closed form is off by more than 5%.
- `dev/bin/calibrate-convergence-defaults REGIME --k K --c C` reruns the
  measurement that chose the multiples.
- `test/validation/test_convergence_defaults.py` (slow) runs Golden Part VI and
  Dear-Nolan low with the shipped defaults.

## Limits

- The closed form is exact for the symmetric island model only.
- Unequal mutation rates use their mean and unequal deme sizes use their sum,
  which are approximations.
- The `200000` floor is measured from two regimes, not derived from first
  principles — a third, faster-mixing regime needed far less (12,000), so
  the floor has real margin, but a configuration this project has not yet
  measured could plausibly still need more before `noise_adequate` is
  reached, in which case it reports the cap honestly rather than a false
  convergence (see [Is the reported value actually precise
  enough?](#is-the-reported-value-actually-precise-enough)).
- The window's own growth is throttled (checked at doubling intervals, not
  every generation) to keep the check itself cheap — a run can therefore
  run a little past the generation it first became precise enough before
  that is actually confirmed and reported.
- The Dear-Nolan high-migration scenario (100 demes of 2,000 gene copies) is
  not covered by the replicated measurement because a run costs about 0.4
  seconds per generation. Its relaxation time is dominated by mutation
  (about 499 generations) and matches the recursion.
- The finite-alleles mutation model changes the mutation term and is not
  covered by the closed form.
