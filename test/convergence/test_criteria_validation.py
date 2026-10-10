"""Validation tests for convergence criteria."""

from __future__ import annotations

import math

import pytest

from fim.convergence.criteria import ConfidenceIntervalCriterion
from fim.convergence.monitor import ConvergenceMonitor


@pytest.mark.parametrize(
    ("minimum_count", "tolerance", "confidence", "message"),
    [
        (1, 0.0, 0.95, "minimum_count must be at least"),
        (2, -1.0, 0.95, "finite and non-negative"),
        (2, math.inf, 0.95, "finite and non-negative"),
        (2, 0.0, 0.80, "confidence must be"),
    ],
)
def test_confidence_interval_criterion_rejects_invalid_configuration(
    minimum_count: int,
    tolerance: float,
    confidence: float,
    message: str,
) -> None:
    """Constructing the criterion validates every public argument."""
    with pytest.raises(ValueError, match=message):
        ConfidenceIntervalCriterion(minimum_count, tolerance, confidence)


def test_confidence_interval_criterion_requires_the_minimum_count() -> None:
    """Stability is never declared from fewer than `minimum_count` values."""
    criterion = ConfidenceIntervalCriterion(minimum_count=5, tolerance=1.0)
    assert not criterion.is_stable([1.0, 1.0, 1.0])


def test_confidence_interval_criterion_detects_a_tight_and_a_loose_sample() -> None:
    """An identical-valued sample is tight; a widely spread one is not."""
    criterion = ConfidenceIntervalCriterion(minimum_count=3, tolerance=0.01)
    assert criterion.is_stable([0.5, 0.5, 0.5, 0.5])
    assert not criterion.is_stable([0.0, 1.0, 0.0, 1.0])


def test_confidence_interval_criterion_composes_with_the_monitor() -> None:
    """The new criterion plugs into `ConvergenceMonitor` like any other."""
    monitor = ConvergenceMonitor(
        ConfidenceIntervalCriterion(minimum_count=3, tolerance=0.0),
        max_generations=10,
    )
    monitor.record(0, 0.5)
    monitor.record(1, 0.5)
    outcome = monitor.record(2, 0.5)
    assert outcome.stopped
    assert outcome.converged


def test_monitor_rejects_records_after_convergence() -> None:
    """A terminal monitor cannot accept observations after its decision."""
    monitor = ConvergenceMonitor(
        ConfidenceIntervalCriterion(minimum_count=2, tolerance=0.0), max_generations=5
    )
    monitor.record(0, 1.0)
    outcome = monitor.record(1, 1.0)
    assert outcome.stopped
    with pytest.raises(RuntimeError, match="stopped"):
        monitor.record(2, 1.0)
