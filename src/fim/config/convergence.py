"""Convergence policy constants.

Every value here is a choice a careful person could make differently, so
each is a policy constant: named, documented with its evidence, and (once
Expert Settings exist) adjustable. Retired constants stay until the rule
that uses them is replaced.

See `README.md` in this directory for the table of every constant.
"""

from __future__ import annotations

from typing import Final

WINDOW_RELAXATION_MULTIPLE: Final = 3.0
"""Default `convergence_window`, in units of the relaxation time `tau`.

Set by `dev/bin/calibrate-convergence-defaults` and recorded in
`test/validation/convergence-defaults-evidence.json`. The noise-free
analysis (design Appendix A.6) already accepts a residual of a third of
`convergence_tolerance` at `2 tau`, but a single stochastic run also
carries sampling noise. Golden Part VI (60 replicates, 8 loci) stops
0.14 below its analytic D at `1 tau`, 0.060 at `2 tau` (outside the 0.05
acceptance) and 0.040 at `3 tau`; a longer window does not improve on that
(0.039 at `4 tau`), because the remaining offset comes from estimating D
over a finite number of loci, not from stopping early. The slower regimes
measured (Dear-Nolan low, ring, unequal mutation rates) are within 0.025
at `2 tau` and within 0.01 at `4 tau`.

Kind: policy.
"""

CAP_RELAXATION_MULTIPLE: Final = 15.0
"""Default `max_generations`, in units of `tau`.

A run needs its window plus the time to settle. The slowest stop measured
was `10.2 tau` (Golden Part VI at a window of `4 tau`; `9.0 tau` at the
shipped `3 tau`), so `15 tau` leaves a margin of about 1.5 and no measured
run ended at the cap. Only binding once `15 tau` exceeds `MINIMUM_MAX_
GENERATIONS`'s own floor (its own docstring has why that floor is now
large) -- a fast-relaxing model's cap is set by the floor instead, since
`15 tau` alone was never a measurement of how long a single-locus run's
own noise takes to average out, only of how long the *trend* takes to
settle.

Kind: policy.
"""

MINIMUM_WINDOW: Final = 50
"""Smallest derived window: the historical default, kept as a floor.

Kind: policy.
"""

MINIMUM_MAX_GENERATIONS: Final = 200_000
"""Smallest derived cap.

Set by the same evidence as `WINDOW_RELAXATION_MULTIPLE`'s own docstring,
extended: `fim.convergence.monitor.ConvergenceMonitor`'s noise-adequacy
gate lets the evidence window actually used to judge stability grow past
`convergence_window` on its own, generation by generation, whenever a
single, fast-relaxing (small `tau`) model's own per-generation noise
still leaves the trailing-window mean short of the requested tolerance --
the single-locus case the original `10_000` floor (this project's own
pre-derived-defaults historical default) was never measured against. Two
independent single-locus, single-replicate regimes (Golden Part VI,
`tau = 85`; Dear-Nolan low, `tau = 19,693` -- two orders of magnitude
apart in `tau`) both needed close to 130,000 generations for their own
`D` to become genuinely noise-adequate, despite that wide spread in
`tau`: this floor is a small multiple of that measured need, not derived
from `tau` at all (a third, well-resolved regime, a 10-deme ring, settled
at 12,000, comfortably under this floor on its own). A model that settles
long before this floor is unaffected -- the adaptive window still stops
the instant it is genuinely adequate, this floor only raises how long a
run is *allowed* to keep growing that window before giving up
honestly. See `20260927-claude-sonnet-5-noise-aware-convergence-design.md`
(`selby/restricted`) for the full measurement.

Kind: policy.
"""

ABSOLUTE_MAX_GENERATIONS: Final = 10_000_000
"""Ceiling on a derived cap, so a nearly isolated system stays finite.

Kind: policy (safety).
"""

NOISE_TOLERANCE_FRACTION: Final = 0.5
"""A window's own trailing-window mean is only judged noise-adequate once its
standard error is at most this fraction of the configured tolerance — half,
so that a mean landing anywhere within one standard error of the true value
is still within tolerance of it (a one-sigma bound, not a five- or
ninety-five-percent one; see the design note this module implements,
`20260927-...-noise-aware-convergence-design.md`, `selby/restricted`, for
why a stricter multiple was not chosen).

Kind: policy.
"""

MINIMUM_NOISE_CHECK_WINDOW: Final = 8
"""Below this many values, a lag-1 correlation estimate is too noisy itself to
trust (a handful of points can look arbitrarily correlated or
anticorrelated by chance) — `fim.convergence.monitor.ConvergenceMonitor`
skips the noise-adequacy gate entirely under this window length, matching
the trend-only check's own original behavior for a short window.

Kind: policy.
"""
