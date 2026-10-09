"""Tests for `fim.convergence.window_statistics`."""

from __future__ import annotations

import math
import random
import statistics

import numpy as np
import pytest

from fim.config.convergence import MINIMUM_NOISE_CHECK_WINDOW, NOISE_TOLERANCE_FRACTION
from fim.convergence.window_statistics import (
    WindowStatistics,
    geyer_window_statistics,
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


def _relative_spread(*, truncation: float, length: int) -> float:
    """Asymptotic relative standard deviation of a `tau_int` estimate.

    Geyer (1992) and Madras and Sokal (1988): summing the autocorrelation to a
    truncation lag `M` leaves a relative spread of `sqrt(2 (2 M + 1) / n)`.
    The tests take `M` as five times the true `tau_int` and allow 2.5 of these.
    """
    return 2.5 * math.sqrt(2.0 * (2.0 * 5.0 * truncation + 1.0) / length)


def test_geyer_flat_window_is_exactly_known() -> None:
    """A constant window has no uncertainty and its full length in draws."""
    result = geyer_window_statistics([0.25] * 40)
    assert result.mean == 0.25
    assert result.standard_error == 0.0
    assert result.effective_sample_size == 40.0
    assert result.tau_int == 1.0


def test_geyer_strictly_alternating_window_is_credited_no_extra_draws() -> None:
    """Hand-worked: eight alternating values.

    The biased autocorrelation is `rho(k) = (-1)^k (8 - k) / 8`, so each pair
    `rho(2m) + rho(2m + 1)` is `1/8`, all four are positive and
    `-1 + 2 * 4/8 = 0`. `tau_int` is never below 1, so the effective sample
    size is the window length.
    """
    result = geyer_window_statistics([1.0, -1.0] * 4)
    assert result.tau_int == 1.0
    assert result.effective_sample_size == 8.0
    assert result.standard_error == pytest.approx(
        result.standard_deviation / math.sqrt(8)
    )


def test_geyer_on_a_short_known_sequence_matches_the_hand_value() -> None:
    """Hand-worked: `1, 2, 3, 4`.

    Centered `-1.5, -0.5, 0.5, 1.5`; the biased autocovariances are `1.25`,
    `0.3125`, `-0.375`, `-0.5625`, so `rho = 1, 0.25, -0.3, -0.45`. The first
    pair is `1.25` (positive) and the second `-0.75` stops the sum:
    `tau_int = -1 + 2 * 1.25 = 1.5`. The sample variance is `5 / 3`.
    """
    result = geyer_window_statistics([1.0, 2.0, 3.0, 4.0])
    assert result.mean == 2.5
    assert result.tau_int == pytest.approx(1.5)
    assert result.effective_sample_size == pytest.approx(4.0 / 1.5)
    assert result.lag1_autocorrelation == pytest.approx(0.25)
    assert result.standard_deviation == pytest.approx(math.sqrt(5.0 / 3.0))
    assert result.standard_error == pytest.approx(
        math.sqrt(5.0 / 3.0) / math.sqrt(4.0 / 1.5)
    )


@pytest.mark.parametrize("count", [0, 1, 2])
def test_geyer_refuses_a_window_too_short_for_an_autocorrelation(count: int) -> None:
    """Fewer than three values define no autocorrelation."""
    with pytest.raises(ValueError, match="at least 3"):
        geyer_window_statistics([0.5] * count)


def test_geyer_tau_int_matches_the_exact_ar1_value() -> None:
    """A seeded AR(1) series: `tau_int = (1 + phi) / (1 - phi)` is 19 at 0.9."""
    phi, length = 0.9, 200_000
    exact = (1.0 + phi) / (1.0 - phi)
    series = _ar1_series(
        phi=phi, sigma=math.sqrt(1.0 - phi * phi), length=length, seed=1
    )
    result = geyer_window_statistics(series)
    bound = _relative_spread(truncation=exact, length=length)
    assert abs(result.tau_int / exact - 1.0) <= bound


def test_geyer_sees_a_slow_mode_the_lag_one_formula_misses() -> None:
    """The sum of two AR(1) processes with time constants 20 and 450.

    Each has unit variance, so the exact `tau_int` of the sum is the average
    of the two: `(39 + 899) / 2 = 469`. The lag-1 formula sees only the
    correlation of neighboring values, which is dominated by the weighted
    average of the two lag-1 correlations, `(0.95 + 1 - 1/450) / 2`, and
    gives about 75; this pins the failure of the old estimator.
    """
    fast_phi, slow_phi, length = 1.0 - 1.0 / 20.0, 1.0 - 1.0 / 450.0, 1_000_000
    exact = 0.5 * (
        (1.0 + fast_phi) / (1.0 - fast_phi) + (1.0 + slow_phi) / (1.0 - slow_phi)
    )
    fast = _ar1_series(
        phi=fast_phi,
        sigma=math.sqrt(1.0 - fast_phi**2),
        length=length,
        seed=11,
        mean=0.0,
    )
    slow = _ar1_series(
        phi=slow_phi,
        sigma=math.sqrt(1.0 - slow_phi**2),
        length=length,
        seed=111,
        mean=0.0,
    )
    series = [first + second for first, second in zip(fast, slow, strict=True)]
    result = geyer_window_statistics(series)
    old = window_statistics(series)
    lag1_tau = (1.0 + old.lag1_autocorrelation) / (1.0 - old.lag1_autocorrelation)
    assert abs(result.tau_int / exact - 1.0) <= _relative_spread(
        truncation=exact, length=length
    )
    lag1_expected = (1.0 + 0.5 * (fast_phi + slow_phi)) / (
        1.0 - 0.5 * (fast_phi + slow_phi)
    )
    assert lag1_tau == pytest.approx(lag1_expected, rel=0.1)
    assert lag1_tau < 0.2 * exact
    assert result.standard_error > 2.0 * old.standard_error


@pytest.mark.parametrize("length", [3500, 20_300])
def test_geyer_standard_error_covers_the_spread_of_a_slow_square_wave(
    length: int,
) -> None:
    """A square wave of period 1000 plus small noise, at eight phases.

    The migration-hub shape: the window mean moves with where the window
    starts. The true spread of the mean over all 1000 phases is computed
    exactly from the noise-free wave, and the Geyer standard error at every
    tested phase is at least that.
    """
    period = 1000
    index = np.arange(length)

    def wave(phase: int) -> np.ndarray:
        return np.where((index + phase) % period < period // 2, 1.0, -1.0)

    true_spread = float(np.std([wave(phase).mean() for phase in range(period)]))
    noise = np.random.default_rng(5)
    for phase in range(0, period, 125):
        series = wave(phase) + noise.normal(0.0, 0.2, length)
        assert geyer_window_statistics(series.tolist()).standard_error >= true_spread
