"""Tests for `fim.convergence.window_statistics`."""

from __future__ import annotations

import math
import random
import statistics

import pytest

from fim.convergence.window_statistics import (
    MINIMUM_NOISE_CHECK_WINDOW,
    NOISE_TOLERANCE_FRACTION,
    WindowStatistics,
    window_statistics,
)


def _ar1_series(
    *, phi: float, sigma: float, length: int, seed: int, mean: float = 0.5
) -> list[float]:
    """Generate a stationary AR(1) series: `x_t = mean + phi (x_{t-1} - mean) + eps`.

    Args:
        phi: Lag-1 autocorrelation of the stationary process, in `(-1, 1)`.
        sigma: Innovation standard deviation.
        length: Number of points to generate.
        seed: RNG seed, for a reproducible series.
        mean: The process's own long-run mean.

    Returns:
        A `length`-long series, started from its own stationary distribution
        (not from a cold start) so every point is equally representative.
    """
    rng = random.Random(seed)
    stationary_sigma = sigma / math.sqrt(1.0 - phi * phi)
    value = mean + rng.gauss(0.0, stationary_sigma)
    series = []
    for _ in range(length):
        series.append(value)
        value = mean + phi * (value - mean) + rng.gauss(0.0, sigma)
    return series


def test_independent_draws_give_the_ordinary_standard_error() -> None:
    """`phi = 0` (no correlation) reduces to `sd / sqrt(n)`."""
    values = _ar1_series(phi=0.0, sigma=0.1, length=4000, seed=1)

    result = window_statistics(values)

    ordinary = statistics.stdev(values) / math.sqrt(len(values))
    assert result.standard_error == pytest.approx(ordinary, rel=0.15)
    assert result.effective_sample_size == pytest.approx(len(values), rel=0.15)
    assert result.lag1_autocorrelation == pytest.approx(0.0, abs=0.05)


@pytest.mark.parametrize("phi", [0.5, 0.8, 0.95, -0.4])
def test_correlated_draws_match_the_known_ar1_standard_error(phi: float) -> None:
    """A known AR(1) process's asymptotic `Var(mean) = sigma_x^2/n * (1+phi)/(1-phi)`.

    The textbook result for the variance of the sample mean of `n`
    consecutive draws from a stationary AR(1) process, `n` large — this
    module's own `tau_int = (1 + rho) / (1 - rho)` (with `rho` the sample
    lag-1 correlation, estimating `phi`) reproduces exactly this factor, so
    the estimated standard error should track the true one for large `n`.
    """
    length = 20_000
    values = _ar1_series(phi=phi, sigma=0.1, length=length, seed=int(phi * 1000) + 7)
    stationary_variance = (0.1**2) / (1.0 - phi * phi)
    true_standard_error = math.sqrt(
        stationary_variance / length * (1.0 + phi) / (1.0 - phi)
    )

    result = window_statistics(values)

    assert result.lag1_autocorrelation == pytest.approx(phi, abs=0.03)
    assert result.standard_error == pytest.approx(true_standard_error, rel=0.2)


def test_constant_window_is_exactly_known() -> None:
    """No variation at all means no uncertainty, not a division by zero."""
    result = window_statistics([0.5] * 20)

    assert result == WindowStatistics(
        mean=0.5,
        standard_error=0.0,
        standard_deviation=0.0,
        effective_sample_size=20.0,
        lag1_autocorrelation=0.0,
        window=20,
    )


def test_perfectly_alternating_window_is_known_better_than_its_own_length() -> None:
    """An oscillating (anticorrelated) window's mean is better known than i.i.d.

    Two neighboring points nearly cancel each other's noise, so the standard
    error of the mean is *smaller* than `sd / sqrt(n)`, the independent-draws
    baseline -- a negative `lag1_autocorrelation` correctly reports more
    effective samples than raw observations, not fewer.
    """
    rng = random.Random(3)
    values = [
        0.5 + ((-1) ** index) * 0.2 + rng.gauss(0.0, 0.01) for index in range(200)
    ]

    result = window_statistics(values)

    assert result.lag1_autocorrelation < -0.9
    assert result.effective_sample_size > len(values)
    ordinary = statistics.stdev(values) / math.sqrt(len(values))
    assert result.standard_error < ordinary


def test_noise_adequate_uses_half_the_tolerance() -> None:
    """`noise_adequate` is `standard_error <= tolerance * NOISE_TOLERANCE_FRACTION`."""
    result = window_statistics([0.5, 0.51, 0.49, 0.50, 0.505, 0.495, 0.502, 0.498])
    boundary = result.standard_error / NOISE_TOLERANCE_FRACTION

    assert result.noise_adequate(boundary * 1.01)
    assert not result.noise_adequate(boundary * 0.99)


@pytest.mark.parametrize("values", [[], [0.1], [0.1, 0.2]])
def test_too_few_values_is_refused(values: list[float]) -> None:
    """Fewer than three values leaves no lag-1 correlation to estimate."""
    with pytest.raises(ValueError, match="at least 3"):
        window_statistics(values)


def test_minimum_noise_check_window_is_at_least_three() -> None:
    """The monitor's own skip threshold must not be shorter than this module needs."""
    assert MINIMUM_NOISE_CHECK_WINDOW >= 3
