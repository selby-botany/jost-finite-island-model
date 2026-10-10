"""Tests for `fim.convergence.window_statistics`."""

from __future__ import annotations

import math
import random
import statistics

import numpy as np
import pytest

from fim.convergence.window_statistics import (
    WindowStatistics,
    geweke_z,
    geyer_window_statistics,
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

    result = geyer_window_statistics(values)

    ordinary = statistics.stdev(values) / math.sqrt(len(values))
    assert result.standard_error == pytest.approx(ordinary, rel=0.15)
    assert result.effective_sample_size == pytest.approx(len(values), rel=0.15)
    assert result.lag1_autocorrelation == pytest.approx(0.0, abs=0.05)


@pytest.mark.parametrize("phi", [0.5, 0.8, 0.95])
def test_correlated_draws_match_the_known_ar1_standard_error(phi: float) -> None:
    """A known AR(1) process's asymptotic `Var(mean) = sigma_x^2/n * (1+phi)/(1-phi)`.

    The textbook variance of the sample mean of `n` consecutive draws from a
    stationary AR(1) process, `n` large: the estimated standard error tracks
    the true one, within the estimator's own asymptotic spread.
    """
    length = 20_000
    values = _ar1_series(phi=phi, sigma=0.1, length=length, seed=int(phi * 1000) + 7)
    stationary_variance = (0.1**2) / (1.0 - phi * phi)
    exact_tau = (1.0 + phi) / (1.0 - phi)
    true_standard_error = math.sqrt(stationary_variance / length * exact_tau)

    result = geyer_window_statistics(values)

    assert result.lag1_autocorrelation == pytest.approx(phi, abs=0.03)
    # SE scales as sqrt(tau_int), so its relative error is half of tau_int's.
    spread = _relative_spread(truncation=exact_tau, length=length)
    assert result.standard_error == pytest.approx(
        true_standard_error, rel=spread / 2.0 + 0.02
    )


def test_a_flat_window_is_exactly_known_even_when_its_mean_rounds() -> None:
    """Forty-three copies of 0.4 have no spread, however the mean rounds."""
    result = geyer_window_statistics([0.4] * 43)
    assert result == WindowStatistics(
        mean=0.4,
        standard_error=0.0,
        standard_deviation=0.0,
        effective_sample_size=43.0,
        lag1_autocorrelation=0.0,
        window=43,
    )


def test_meets_needs_both_a_small_enough_error_and_enough_effective_values() -> None:
    """`meets` is `standard_error <= target` and `ESS >= floor`."""
    result = geyer_window_statistics(
        [0.5, 0.51, 0.49, 0.50, 0.505, 0.495, 0.502, 0.498]
    )
    error = result.standard_error
    ess = result.effective_sample_size
    assert result.meets(error * 1.01, ess * 0.99)
    assert not result.meets(error * 0.99, ess * 0.99)
    assert not result.meets(error * 1.01, ess * 1.01)


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
    # The retired single-lag formula, written out: it saw only `rho(1)`.
    centered = np.asarray(series) - np.mean(series)
    rho1 = float(np.dot(centered[:-1], centered[1:]) / np.dot(centered, centered))
    lag1_tau = (1.0 + rho1) / (1.0 - rho1)
    lag1_standard_error = result.standard_deviation / math.sqrt(length / lag1_tau)
    assert abs(result.tau_int / exact - 1.0) <= _relative_spread(
        truncation=exact, length=length
    )
    lag1_expected = (1.0 + 0.5 * (fast_phi + slow_phi)) / (
        1.0 - 0.5 * (fast_phi + slow_phi)
    )
    assert lag1_tau == pytest.approx(lag1_expected, rel=0.1)
    assert lag1_tau < 0.2 * exact
    assert result.standard_error > 2.0 * lag1_standard_error


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


def test_geweke_z_is_zero_for_a_constant_window() -> None:
    """Nothing differs between the start and the end of a flat window."""
    assert geweke_z([0.3] * 60) == 0.0


def test_geweke_z_of_a_constant_step_is_infinite_with_the_sign_of_the_gap() -> None:
    """Both segments exactly known but different: `z` is `-inf` (start below end)."""
    assert geweke_z([0.0] * 10 + [1.0] * 90) == -math.inf
    assert geweke_z([1.0] * 10 + [0.0] * 90) == math.inf


def test_geweke_z_matches_the_hand_value_on_two_known_segments() -> None:
    """Hand-worked: `1..4` against `5..8`, each half of an 8-value window.

    Each segment has standard error `sqrt(0.625)` (see the `1, 2, 3, 4` case
    above) and the means are `2.5` and `6.5`, so `z = -4 / sqrt(1.25)`.
    """
    z = geweke_z([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0], first_fraction=0.5)
    assert z == pytest.approx(-4.0 / math.sqrt(1.25))


def test_geweke_z_uses_the_first_tenth_and_the_last_half_by_default() -> None:
    """The default segments are exactly the slices `Geweke (1992)` names."""
    series = _ar1_series(phi=0.8, sigma=0.1, length=200, seed=3)
    start = geyer_window_statistics(series[:20])
    end = geyer_window_statistics(series[100:])
    expected = (start.mean - end.mean) / math.hypot(
        start.standard_error, end.standard_error
    )
    assert geweke_z(series) == pytest.approx(expected, rel=1e-12)


def test_geweke_z_flags_a_start_that_has_not_settled() -> None:
    """A decaying transient inside the window gives a large positive `z`."""
    noise = _ar1_series(phi=0.5, sigma=0.05, length=400, seed=9, mean=0.0)
    window = [
        value + 2.0 * math.exp(-index / 40.0) for index, value in enumerate(noise)
    ]
    assert geweke_z(window) > 3.0
    assert abs(geweke_z(noise)) < 3.0


@pytest.mark.parametrize("fraction", [0.0, -0.1, 1.5])
def test_geweke_z_refuses_a_fraction_outside_zero_to_one(fraction: float) -> None:
    """A fraction must be in `(0, 1]`."""
    with pytest.raises(ValueError, match="fraction"):
        geweke_z([0.5] * 100, first_fraction=fraction)


def test_geweke_z_refuses_a_window_too_short_for_its_segments() -> None:
    """Ten values leave a one-value first segment, too few for an autocorrelation."""
    with pytest.raises(ValueError, match="too short"):
        geweke_z([0.5] * 10)
