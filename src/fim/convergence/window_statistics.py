"""How precisely an evidence window's own mean is known, given correlated noise.

A run that has burned in is judged by the mean of each watched statistic over
an evidence window, and by how well that mean is known. A window's values are
not independent draws: generation `t` and generation `t + 1` share almost the
whole population that produced them, so they are strongly correlated at short
lag, and the correlation fades only over roughly `tau`, the model's
relaxation time (`fim.convergence.defaults`). Averaging `W` correlated values
does not shrink the uncertainty by `1 / sqrt(W)`; it shrinks by
`1 / sqrt(W / tau_int)`, where the *integrated autocorrelation time*
`tau_int` counts how many of those `W` values are worth one independent draw.

`geyer_window_statistics` estimates `tau_int` from the whole autocorrelation
function with Geyer's (1992) initial positive sequence, which sees a slow
second mode that a single-lag estimate misses. `geweke_z` compares the start
of a window with its end as a check that the burn-in was long enough.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from fim.config.convergence import GEWEKE_FIRST_FRACTION, GEWEKE_LAST_FRACTION
from fim.config.numerics import MINIMUM_WINDOW_VALUES


@dataclass(frozen=True, slots=True)
class WindowStatistics:
    """How well a window's own mean is known, given its internal correlation.

    Attributes:
        mean: The window's own sample mean.
        standard_error: The estimated standard error of `mean`: not
            `standard_deviation / sqrt(len(window))`, the independent-draws
            formula, but `standard_deviation / sqrt(effective_sample_size)`,
            which corrects for the window's autocorrelation.
        standard_deviation: The window's own sample standard deviation
            (Bessel-corrected), ignoring correlation — the quantity a sigma
            band already reports (`fim.engine._sigma_band_summary`); kept
            alongside `standard_error` so a caller never has to choose one
            over the other.
        effective_sample_size: How many independent draws this window's
            `len(window)` correlated values are worth, in information.
        lag1_autocorrelation: The estimated correlation between neighboring
            values.
        window: `len(window)` this was computed from, carried along so a
            caller does not have to keep the original sequence around too.
    """

    mean: float
    standard_error: float
    standard_deviation: float
    effective_sample_size: float
    lag1_autocorrelation: float
    window: int

    @property
    def tau_int(self) -> float:
        """The integrated autocorrelation time: `window / effective_sample_size`."""
        return self.window / self.effective_sample_size

    def meets(
        self, target_standard_error: float, minimum_effective_sample_size: float
    ) -> bool:
        """Return whether the mean is known well enough to stop on.

        Args:
            target_standard_error: The largest acceptable standard error.
            minimum_effective_sample_size: The smallest effective sample size
                that makes the standard error itself trustworthy.

        Returns:
            `True` when `standard_error` is at most the target and
            `effective_sample_size` is at least the floor.
        """
        return (
            self.standard_error <= target_standard_error
            and self.effective_sample_size >= minimum_effective_sample_size
        )


def geyer_window_statistics(
    values: Sequence[float] | npt.NDArray[np.float64],
) -> WindowStatistics:
    """Estimate a window's mean and its standard error by Geyer's method.

    The lag-1 formula of `window_statistics` is exact only for a first-order
    autoregressive process. A statistic with two relaxation times (the sum of a
    fast and a slow mode, as a two-locus or ring model gives) is
    underestimated by it, because the slow mode shows up as a small step at
    lag 1 and a long tail beyond. Geyer's (1992) initial positive sequence
    estimator sums the whole autocorrelation function instead: pair the lags,
    `Gamma_m = rho(2m) + rho(2m + 1)`, add the pairs while they stay positive
    (the sum of a true autocorrelation function's pairs is positive, so the
    first non-positive pair is noise), and set
    `tau_int = -1 + 2 * sum(Gamma_m)`.

    The autocorrelation comes from one FFT of the centered window, zero-padded
    to twice its length so the circular correlation equals the linear one, so a
    window of `L` values costs `O(L log L)`.

    Args:
        values: The window's per-generation values, in order; at least
            `MINIMUM_WINDOW_VALUES` of them.

    Returns:
        The mean, the sample standard deviation, the effective sample size
        `window / tau_int` (with `tau_int` at least 1, so a negatively
        correlated window is never credited with more draws than it has), the
        standard error `SD / sqrt(ESS)` and the lag-1 autocorrelation.

    Raises:
        ValueError: If `values` has fewer than `MINIMUM_WINDOW_VALUES` entries.
    """
    count = len(values)
    if count < MINIMUM_WINDOW_VALUES:
        raise ValueError(
            f"geyer_window_statistics needs at least {MINIMUM_WINDOW_VALUES} values"
        )
    array = np.asarray(values, dtype=np.float64)
    if float(np.ptp(array)) == 0.0:
        # A flat window has nothing left to estimate: exactly known. Tested on
        # the values themselves, because the mean of identical doubles can
        # differ from them by a rounding error that would look like noise.
        return WindowStatistics(
            mean=float(array[0]),
            standard_error=0.0,
            standard_deviation=0.0,
            effective_sample_size=float(count),
            lag1_autocorrelation=0.0,
            window=count,
        )
    mean = math.fsum(array.tolist()) / count
    centered = array - mean
    sum_squares = float(np.dot(centered, centered))
    standard_deviation = math.sqrt(sum_squares / (count - 1))
    # Autocovariance by FFT, normalized so that lag 0 is 1. Zero-padding to at
    # least twice the length turns the circular correlation into the linear one.
    transform = np.fft.rfft(centered, 2 * count)
    autocovariance = np.fft.irfft(transform * np.conj(transform), 2 * count)[:count]
    correlation = autocovariance / autocovariance[0]
    # Initial positive sequence: whole pairs of lags only, while positive.
    pair_count = count // 2
    pairs = correlation[: 2 * pair_count : 2] + correlation[1 : 2 * pair_count : 2]
    non_positive = np.flatnonzero(pairs <= 0.0)
    kept = pairs if non_positive.size == 0 else pairs[: non_positive[0]]
    tau_int = max(-1.0 + 2.0 * float(np.sum(kept)), 1.0)
    effective_sample_size = count / tau_int
    return WindowStatistics(
        mean=mean,
        standard_error=standard_deviation / math.sqrt(effective_sample_size),
        standard_deviation=standard_deviation,
        effective_sample_size=effective_sample_size,
        lag1_autocorrelation=max(-1.0, min(float(correlation[1]), 1.0)),
        window=count,
    )


def geweke_z(
    values: Sequence[float],
    *,
    first_fraction: float = GEWEKE_FIRST_FRACTION,
    last_fraction: float = GEWEKE_LAST_FRACTION,
) -> float:
    """Return Geweke's (1992) `z` for the start of a window against its end.

    The mean of the first `first_fraction` of the window is compared with the
    mean of the last `last_fraction`, in units of their combined standard
    error (each from `geyer_window_statistics`, so each is corrected for its
    own autocorrelation). If the averaging window began before the
    population had forgotten its starting state, the start differs from the
    end and `|z|` is large. The report carries it as a diagnostic; it does not
    stop or continue a run (design 6.5).

    Args:
        values: The window's per-generation values, in order.
        first_fraction: Share of the window, from its start, to compare.
        last_fraction: Share of the window, from its end, to compare.

    Returns:
        `(mean_start - mean_end) / sqrt(se_start**2 + se_end**2)`. Zero when
        the means are equal and both segments are exactly known; infinite,
        with the sign of the difference, when the means differ and both are
        exactly known.

    Raises:
        ValueError: If a fraction is not in `(0, 1]`, or a segment would hold
            fewer than `MINIMUM_WINDOW_VALUES` values.
    """
    for name, fraction in (("first", first_fraction), ("last", last_fraction)):
        if not 0.0 < fraction <= 1.0:
            raise ValueError(f"{name}_fraction must be in (0, 1]")
    count = len(values)
    first_count = int(count * first_fraction)
    last_count = int(count * last_fraction)
    if min(first_count, last_count) < MINIMUM_WINDOW_VALUES:
        raise ValueError(
            f"a window of {count} values is too short for Geweke's z: each "
            f"segment needs at least {MINIMUM_WINDOW_VALUES} values"
        )
    start = geyer_window_statistics(values[:first_count])
    end = geyer_window_statistics(values[count - last_count :])
    difference = start.mean - end.mean
    spread = math.hypot(start.standard_error, end.standard_error)
    if spread == 0.0:
        return 0.0 if difference == 0.0 else math.copysign(math.inf, difference)
    return difference / spread
