"""Tests for operational stochastic-equilibrium detection."""

import pytest

from fim.convergence.criteria import TrailingWindowCriterion, trailing_window_stable
from fim.convergence.monitor import ConvergenceMonitor, StopReason


def test_constant_sequence_is_stable_when_window_fills() -> None:
    """A fixed statistic converges at the first full window."""
    assert trailing_window_stable([0.5, 0.5, 0.5, 0.5], 4, 0.0)


def test_linear_drift_is_not_stable() -> None:
    """A moving half-window mean does not converge."""
    assert not trailing_window_stable([0.0, 0.1, 0.2, 0.3], 4, 0.05)


@pytest.mark.parametrize(
    ("history", "tolerance", "expected"),
    [
        ([0.9, 1.1, 0.9, 1.1], 0.0, True),
        ([0.0, 0.0, 1.0, 1.0], 0.9, False),
    ],
)
def test_oscillation_uses_half_window_means(
    history: list[float],
    tolerance: float,
    expected: bool,
) -> None:
    """Oscillation is judged by the documented half-mean rule."""
    assert trailing_window_stable(history, 4, tolerance) is expected


def test_monitor_distinguishes_convergence_from_cap() -> None:
    """Terminal outcomes remain valid and explain why the run stopped."""
    converged = ConvergenceMonitor(
        TrailingWindowCriterion(4, 0.0),
        max_generations=10,
    )
    for generation in range(4):
        converged.record(generation, 0.5)

    capped = ConvergenceMonitor(
        TrailingWindowCriterion(4, 0.0),
        max_generations=3,
    )
    for generation, value in enumerate([0.0, 0.1, 0.2, 0.3]):
        capped.record(generation, value)

    assert converged.reason() is StopReason.STATISTIC_CONVERGED
    assert capped.reason() is StopReason.MAX_GENERATIONS
    assert converged.outcome().converged
    assert not capped.outcome().converged
