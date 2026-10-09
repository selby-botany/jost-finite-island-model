"""How precisely a trailing window's own mean is known, given correlated noise.

`fim.convergence.criteria.trailing_window_stable` answers "has this stopped
*trending*" by comparing the two halves of a window — a good, cheap proxy for
"the transient has died out," but it says nothing about whether the window's
own mean is actually known to the requested tolerance once that transient is
gone. A single-locus, single-replicate run's per-generation statistic keeps
wobbling by drift alone, generation after generation, at an amplitude that
does not shrink just because the population has relaxed — two neighboring
windows drawn from the *same* stationary process can land on opposite sides
of a small tolerance purely by chance. This module answers the question the
window criterion does not: given a window of correlated values, how precise
is their mean, actually?

The complication is that a window's own values are not independent draws —
generation `t` and generation `t + 1` share almost the entire population
that produced them, so they are strongly correlated at short lag and that
correlation only fades over roughly `tau` (the model's own relaxation time,
`fim.convergence.defaults`). Averaging `W` correlated values does not shrink
the uncertainty by `1 / sqrt(W)` the way averaging `W` independent ones
would; it shrinks by `1 / sqrt(W / tau_int)`, where `tau_int` (the
*integrated autocorrelation time*) counts how many of those `W` values are
worth, in information, one independent draw. This module estimates
`tau_int` from the window itself, from its lag-1 autocorrelation alone: a
window is well-approximated, over a short enough span, as a first-order
autoregressive (AR(1)) process, for which `tau_int = (1 + rho) / (1 - rho)`
is exact (`rho` the lag-1 correlation) — the standard "effective sample
size" formula for a first-order process (see, for instance, the "batch
means"/spectral-variance literature on Markov-chain output analysis; a
single-lag estimate is the simplest member of that family, not the most
precise one — Geyer's 1992 initial-sequence estimators sum many lags for a
tighter bound, at a cost this module's own O(1)-per-generation budget
(`fim.convergence.monitor.ConvergenceMonitor`) cannot afford every
generation of a run that may need millions).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from fim.config.convergence import NOISE_TOLERANCE_FRACTION
from fim.config.numerics import MAXIMUM_LAG1_CORRELATION, MINIMUM_WINDOW_VALUES


@dataclass(frozen=True, slots=True)
class WindowStatistics:
    """How well a window's own mean is known, given its internal correlation.

    Attributes:
        mean: The window's own sample mean.
        standard_error: The estimated standard error of `mean` — not
            `standard_deviation / sqrt(len(window))`, the independent-draws
            formula, but the same divided by `sqrt(effective_sample_size /
            len(window))` instead, correcting for `lag1_autocorrelation`.
        standard_deviation: The window's own sample standard deviation
            (Bessel-corrected), ignoring correlation — the quantity a sigma
            band already reports (`fim.engine._sigma_band_summary`); kept
            alongside `standard_error` so a caller never has to choose one
            over the other.
        effective_sample_size: How many independent draws this window's
            `len(window)` correlated values are worth, in information.
        lag1_autocorrelation: The estimated correlation between neighboring
            values, clamped to `MAXIMUM_LAG1_CORRELATION`.
        window: `len(window)` this was computed from, carried along so a
            caller does not have to keep the original sequence around too.
    """

    mean: float
    standard_error: float
    standard_deviation: float
    effective_sample_size: float
    lag1_autocorrelation: float
    window: int

    def noise_adequate(self, tolerance: float) -> bool:
        """Return whether `mean` is known to within `tolerance`.

        Args:
            tolerance: The statistic's own configured convergence
                tolerance (`SimulationParams.convergence_tolerance`).

        Returns:
            `True` when `standard_error` is at most
            `NOISE_TOLERANCE_FRACTION` of `tolerance`.
        """
        return self.standard_error <= tolerance * NOISE_TOLERANCE_FRACTION


def window_statistics(values: Sequence[float]) -> WindowStatistics:
    """Estimate a window's own mean and its correlation-corrected precision.

    Args:
        values: A trailing window of a statistic's own per-generation
            values, in chronological order. At least
            `MINIMUM_NOISE_CHECK_WINDOW` long — a caller with a shorter
            window should not call this at all (see that constant's own
            docstring), not pass a short one and expect a meaningful answer.

    Returns:
        The window's own mean, standard deviation, correlation-corrected
        standard error, effective sample size, and lag-1 autocorrelation.

    Raises:
        ValueError: If `values` has fewer than 3 entries (an autocorrelation
            of anything shorter is undefined, not merely unreliable).
    """
    count = len(values)
    if count < MINIMUM_WINDOW_VALUES:
        raise ValueError("window_statistics needs at least 3 values")
    mean = math.fsum(values) / count
    centered = [value - mean for value in values]
    sum_squares = math.fsum(value * value for value in centered)
    # Bessel-corrected sample variance -- `count - 1`, the same correction
    # `fim.statistics.interval.confidence_interval` applies for the identical
    # reason (an unbiased estimate from a sample whose own mean was
    # estimated from the same data).
    variance = sum_squares / (count - 1)
    standard_deviation = math.sqrt(variance)
    if standard_deviation == 0.0:
        # A perfectly flat window (every value identical, most concretely a
        # fixed statistic like D at an allele-free locus) has no correlation
        # to speak of and no uncertainty left to estimate: exactly known.
        return WindowStatistics(
            mean=mean,
            standard_error=0.0,
            standard_deviation=0.0,
            effective_sample_size=float(count),
            lag1_autocorrelation=0.0,
            window=count,
        )
    # Lag-1 sample autocorrelation, the Pearson correlation between the
    # window's own values and themselves shifted by one generation. Divided
    # by `sum_squares` rather than by `(count - 1) * variance` (they differ
    # only by that same `count - 1` factor either side of the fraction) to
    # avoid computing `variance` twice.
    cross_products = math.fsum(
        centered[index] * centered[index + 1] for index in range(count - 1)
    )
    lag1 = max(-1.0, min(cross_products / sum_squares, MAXIMUM_LAG1_CORRELATION))
    # The AR(1) closed form (this module's own docstring): `tau_int = (1 +
    # rho) / (1 - rho)`. A negative (anticorrelated, oscillating) window
    # gives `tau_int < 1` -- more effective samples than raw observations,
    # correctly, since an oscillating series' own mean is *better* known
    # than that many independent draws would be. Clamped to at least `1.0`
    # only in the sense that `tau_int` itself is never allowed below the
    # value implied by `lag1 = -1` (`tau_int = 0`), which would divide by
    # zero below; real data essentially never reaches it.
    tau_int = max((1.0 + lag1) / (1.0 - lag1), 1e-9)
    effective_sample_size = count / tau_int
    standard_error = standard_deviation / math.sqrt(effective_sample_size)
    return WindowStatistics(
        mean=mean,
        standard_error=standard_error,
        standard_deviation=standard_deviation,
        effective_sample_size=effective_sample_size,
        lag1_autocorrelation=lag1,
        window=count,
    )
