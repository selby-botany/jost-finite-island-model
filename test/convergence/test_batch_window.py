"""Tests of the matched replicate averaging window (design 9.1).

Every expected number is worked out in the test from the formulas of the
module, never from a run.
"""

from __future__ import annotations

import math

import pytest

from fim.convergence.batch_window import (
    BatchWindowPlanner,
    WindowNoise,
    lane_standard_error,
    matched_averaging_multiple,
    target_replicate_count,
)
from fim.statistics.interval import student_t_critical_value


def _planner(**changes: object) -> BatchWindowPlanner:
    """A planner for tau = 100, precision 0.01, a first wave of two."""
    settings: dict[str, object] = {
        "relaxation_time": 100.0,
        "statistics": ("D",),
        "precision": 0.01,
        "confidence": 0.95,
        "replicates": 16,
        "first_wave": 2,
        "first_wave_multiple": 20.0,
        "multiple_minimum": 5.0,
        "multiple_maximum": 100.0,
    }
    settings.update(changes)
    return BatchWindowPlanner(**settings)  # type: ignore[arg-type]


def test_the_target_replicate_count_is_a_multiple_of_the_width_above_the_minimum() -> (
    None
):
    """`max(minimum, ceil(multiple * width))`."""
    assert target_replicate_count(minimum=10, wave_multiple=2.0, width=8) == 16
    assert target_replicate_count(minimum=10, wave_multiple=2.0, width=3) == 10
    assert target_replicate_count(minimum=2, wave_multiple=1.5, width=3) == 5


def test_the_lane_standard_error_is_the_interval_target_per_replicate() -> None:
    """`SE = precision * sqrt(R) / t(R - 1)`; zero precision needs zero error."""
    expected = 0.01 * math.sqrt(16) / student_t_critical_value(15, 0.95)
    assert lane_standard_error(
        precision=0.01, confidence=0.95, replicates=16
    ) == pytest.approx(expected)
    assert lane_standard_error(precision=0.0, confidence=0.95, replicates=16) == 0.0
    with pytest.raises(ValueError, match="at least 2"):
        lane_standard_error(precision=0.01, confidence=0.95, replicates=1)


def test_the_matched_multiple_is_tau_int_times_the_variance_ratio() -> None:
    """`A = tau_int * (sigma / SE)^2`, in relaxation times, pooled over the wave."""
    wave = [
        {"D": WindowNoise(standard_deviation=0.1, tau_int=40.0)},
        {"D": WindowNoise(standard_deviation=0.1, tau_int=60.0)},
    ]
    lane_error = 0.02

    multiple = matched_averaging_multiple(
        wave,
        statistics=("D",),
        lane_error=lane_error,
        multiple_minimum=5.0,
        multiple_maximum=100.0,
        relaxation_time=100.0,
    )

    assert multiple == pytest.approx(50.0 * (0.1 / 0.02) ** 2 / 100.0)


def test_the_slowest_statistic_decides_and_the_result_is_clamped() -> None:
    """The largest window among the watched statistics, held to the clamp."""
    wave = [
        {
            "D": WindowNoise(0.1, 10.0),
            "G_ST": WindowNoise(0.1, 200.0),
        }
    ]
    exact = 200.0 * (0.1 / 0.05) ** 2 / 100.0
    assert matched_averaging_multiple(
        wave,
        statistics=("D", "G_ST"),
        lane_error=0.05,
        multiple_minimum=5.0,
        multiple_maximum=100.0,
        relaxation_time=100.0,
    ) == pytest.approx(exact)
    assert matched_averaging_multiple(
        wave,
        statistics=("D", "G_ST"),
        lane_error=0.05,
        multiple_minimum=5.0,
        multiple_maximum=6.0,
        relaxation_time=100.0,
    ) == pytest.approx(6.0)
    assert matched_averaging_multiple(
        wave,
        statistics=("D", "G_ST"),
        lane_error=0.05,
        multiple_minimum=20.0,
        multiple_maximum=100.0,
        relaxation_time=100.0,
    ) == pytest.approx(20.0)


def test_zero_error_asks_for_the_longest_window_and_exact_statistics_the_shortest() -> (
    None
):
    """No precision is the maximum; a flat statistic needs only the minimum."""
    noisy = [{"D": WindowNoise(0.1, 10.0)}]
    flat = [{"D": WindowNoise(0.0, 1.0)}]
    assert (
        matched_averaging_multiple(
            noisy,
            statistics=("D",),
            lane_error=0.0,
            multiple_minimum=5.0,
            multiple_maximum=100.0,
            relaxation_time=100.0,
        )
        == 100.0
    )
    assert (
        matched_averaging_multiple(
            flat,
            statistics=("D",),
            lane_error=0.5,
            multiple_minimum=5.0,
            multiple_maximum=100.0,
            relaxation_time=100.0,
        )
        == 5.0
    )


def test_the_first_wave_gets_the_guess_and_later_replicates_wait_for_the_match() -> (
    None
):
    """Indices below the wave width get `a_0 * tau`; the rest, `None` until measured."""
    planner = _planner()

    assert planner.window_for(0) == planner.window_for(1) == 2000
    assert planner.window_for(2) is None
    assert planner.matched_window is None
    assert planner.record(0, {"D": WindowNoise(0.1, 50.0)}) is None
    assert planner.window_for(2) is None

    matched = planner.record(1, {"D": WindowNoise(0.1, 50.0)})

    lane_error = lane_standard_error(precision=0.01, confidence=0.95, replicates=16)
    expected = math.ceil(50.0 * (0.1 / lane_error) ** 2)
    assert matched == expected
    assert planner.window_for(2) == planner.window_for(99) == expected
    assert planner.window_for(0) == 2000


def test_the_match_does_not_depend_on_the_order_the_wave_is_recorded_in() -> None:
    """Replicates finish in any order; the matched window is the same."""
    first = _planner()
    second = _planner()
    noise = [
        {"D": WindowNoise(0.10, 40.0)},
        {"D": WindowNoise(0.14, 80.0)},
    ]

    first.record(0, noise[0])
    first.record(1, noise[1])
    second.record(1, noise[1])
    second.record(0, noise[0])

    assert first.matched_window == second.matched_window


def test_a_fixed_window_is_used_for_everyone_and_nothing_is_measured() -> None:
    """The user's window is never matched, guessed, or clamped."""
    planner = _planner(fixed_window=777, window_limit=10)

    assert [planner.window_for(i) for i in (0, 1, 2, 50)] == [777] * 4
    assert planner.record(0, {"D": WindowNoise(0.1, 10.0)}) is None
    assert planner.matched_window is None


def test_the_window_limit_clamps_the_guess_and_the_match() -> None:
    """A guessed or matched window never runs past the generation cap."""
    planner = _planner(window_limit=300)

    assert planner.window_for(0) == 300
    planner.record(0, {"D": WindowNoise(0.5, 100.0)})
    assert planner.record(1, {"D": WindowNoise(0.5, 100.0)}) == 300
    assert planner.earliest_window == 300


def test_the_earliest_window_is_the_minimum_multiple() -> None:
    """A waiting replicate may average this long without overshooting."""
    assert _planner().earliest_window == 500


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"first_wave": 0}, "first_wave"),
        ({"relaxation_time": 0.0}, "relaxation_time"),
        ({"fixed_window": 0}, "fixed_window"),
        ({"window_limit": 0}, "window_limit"),
        ({"replicates": 1}, "at least 2"),
    ],
)
def test_the_planner_rejects_out_of_range_settings(
    changes: dict[str, object], message: str
) -> None:
    """Out-of-range counts are refused by name."""
    with pytest.raises(ValueError, match=message):
        _planner(**changes)
