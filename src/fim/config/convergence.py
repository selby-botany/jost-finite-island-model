"""Convergence policy constants.

Every value here is a choice a careful person could make differently, so
each is a policy constant: named, documented with its evidence, and an
Expert Setting (`fim.config.expert`) a run's configuration can change.

The rule these constants steer is "burn in, then average" (design
`20261005-claude-opus-5-5-simplified-convergence-rule-design.md`,
`selby/restricted`): wait out the burn-in, then average each watched
statistic over an evidence window that grows until its standard error is
small enough and its effective sample size large enough.

See `README.md` in this directory for the table of every constant.
"""

from __future__ import annotations

from typing import Final

BURN_IN_MINIMUM_RELAXATION_TIMES: Final = 5.0
"""Fewest relaxation times `tau` the burn-in lasts: the floor of `k`.

The burn-in is `ceil(k * tau)` generations with
`k = max(BURN_IN_MINIMUM_RELAXATION_TIMES, ln(2 / precision))`. The slowest
mode's leftover from a worst-case unit offset is `e^-k`, so `k >= ln(2 /
precision)` leaves at most `precision / 2` of bias; the floor of 5 keeps a
loose precision from shortening the burn-in below what Run B needed (the
average's bias at `5.3 tau` was -0.00016; design 3.5). The equilibrium-split
ancestral phase already uses the same shape, `tau ln(1 / tolerance)`.

Kind: policy.
"""

FIRST_CHECK_RELAXATION_TIMES: Final = 2.0
"""First check, in relaxation times after the burn-in ends.

About one integrated autocorrelation time of `D`: an earlier check cannot
pass the effective-sample-size floor, so it would only waste work.

Kind: policy.
"""

FIRST_CHECK_MINIMUM: Final = 50
"""Fewest generations after the burn-in before the first check.

The historical default window, kept as a floor for models whose `tau` is
tiny.

Kind: policy.
"""

MINIMUM_EFFECTIVE_SAMPLE_SIZE: Final = 50.0
"""Smallest effective sample size an evidence window must hold to be trusted.

A low standard-error estimate from a window with few independent values is
itself unreliable (design 3.4: floors of 30 gave 1% to 21% misses, 50 gave 0%
to 2%, 100 gave 0% but doubled the run). The floor sets a minimum run of
`50 * tau_int` generations after the burn-in for every statistic, whatever
the precision.

Kind: policy.
"""

CHECK_GROWTH: Final = 2.0
"""Factor by which the evidence window grows between checks (doubling).

Doubling had the lowest miss rate measured (design 8.3, 8.5) and costs
`O(L log L)` in total. Its overshoot past the length actually needed is at
most 2x and typically 1.44x.

Kind: policy.
"""

FRACTIONAL_BURN_IN: Final = 0.1
"""Share of a run discarded as burn-in when no relaxation time is available.

A standard practice in Markov-chain output analysis: the evidence window
starts at `floor(0.1 * t)` at each check. Used only for a model with no
migration and no mutation, or an explicit migration matrix beyond the
eigenvalue route, when `convergence_burn_in` is `auto` (design 6.4).

Kind: policy.
"""

CAP_RELAXATION_MULTIPLE: Final = 15.0
"""Default `max_generations`, in relaxation times, beyond the burn-in.

`max_generations` is `max(MINIMUM_MAX_GENERATIONS, burn_in + ceil(15 tau))`,
so a slow model is never capped inside its own burn-in. Only binding once
`15 tau` exceeds `MINIMUM_MAX_GENERATIONS`; a fast-relaxing model's cap is set
by that floor, since `15 tau` was never a measurement of how long a
single-locus run's own noise takes to average out.

Kind: policy.
"""

MINIMUM_MAX_GENERATIONS: Final = 200_000
"""Smallest derived cap.

Two independent single-locus regimes (Golden Part VI, `tau = 85`; Dear-Nolan
low, `tau = 19,693`, two orders of magnitude apart) both needed close to
130,000 generations for their own `D` to become known to 0.01, despite that
spread in `tau`: this floor is a small multiple of that measured need, not
derived from `tau` at all. A model that settles sooner is unaffected, since
the run stops when its precision is reached; the floor only raises how long
a run may keep averaging before it is reported as not having reached the
precision. See `20260927-claude-sonnet-5-noise-aware-convergence-design.md`
(`selby/restricted`) for the measurement.

Kind: policy.
"""

ABSOLUTE_MAX_GENERATIONS: Final = 10_000_000
"""Ceiling on a derived cap, so a nearly isolated system stays finite.

Kind: policy (safety).
"""

GEWEKE_FIRST_FRACTION: Final = 0.1
"""Share of an evidence window, from its start, that Geweke's `z` compares.

Kind: convention (Geweke 1992: the first 10% against the last 50%).
"""

GEWEKE_LAST_FRACTION: Final = 0.5
"""Share of an evidence window, from its end, that Geweke's `z` compares.

Kind: convention (Geweke 1992: the first 10% against the last 50%).
"""

START_DRIFT_ALERT_Z: Final = 3.0
"""Absolute Geweke `z` above which the report says the burn-in may be too short.

The design measured the diagnostic as a stopping guard and found it blocked 5
to 11 of 138 to 179 stationary stops (false alarms) without lowering the miss
rate, so it only labels the result (design 6.5).

Kind: policy.
"""

REPLICATE_WAVE_MULTIPLE: Final = 2.0
"""Replicate waves a batch aims for: `R_target = max(replicate_minimum, m * W)`.

`W` is how many replicates run at once. Sizing the batch to a small multiple
of `W` keeps every worker busy through whole waves, and the averaging window
of a replicate is matched to reach the requested precision with that many
replicates (design 9.1).

Kind: policy.
"""

BATCH_WIDTH: Final = 8
"""Replicates assumed to run at once, when `max_concurrent_replicates` is unset.

The first wave of replicates, which measures the noise the later windows are
matched from, is this wide. It is a fixed number, not the machine's CPU count,
so a configuration gives the same windows and the same results on every
machine and under every backend (design 9.1 matched the window to the
workers; a worker count that changed the results would break reproducibility).

Kind: policy.
"""

AVERAGING_MULTIPLE_MINIMUM: Final = 5.0
"""Smallest matched averaging window, in relaxation times.

Below this a replicate's own average is barely better than a snapshot: its
window holds only a few independent values (design 9).

Kind: policy.
"""

AVERAGING_MULTIPLE_MAXIMUM: Final = 100.0
"""Largest matched averaging window, in relaxation times.

Keeps a replicate finite when its statistic is so noisy that the matched
window would be enormous; more replicates then serve better than a longer
window.

Kind: policy.
"""

FIRST_WAVE_AVERAGING_MULTIPLE: Final = 20.0
"""Averaging window, in relaxation times, of the first wave of replicates.

The first wave runs before anything is known about the statistic's noise, so
it averages for this guess; the matched window of every later replicate is
measured from it (design 9.1).

Kind: policy.
"""
