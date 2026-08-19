"""Validation and combinator tests for convergence criteria."""

from __future__ import annotations

import math

import pytest

from fim.convergence.criteria import (
    AllCriterion,
    AnyCriterion,
    ConfidenceIntervalCriterion,
    TrailingWindowCriterion,
    trailing_window_stable,
)
from fim.convergence.monitor import ConvergenceMonitor


@pytest.mark.parametrize(
    ("window", "tolerance", "message"),
    [
        (1, 0.0, "window must be at least"),
        (2, -1.0, "finite and non-negative"),
        (2, math.inf, "finite and non-negative"),
    ],
)
def test_trailing_window_rejects_invalid_configuration(
    window: int,
    tolerance: float,
    message: str,
) -> None:
    """The functional criterion validates both public numeric arguments."""
    with pytest.raises(ValueError, match=message):
        trailing_window_stable([1.0, 1.0], window, tolerance)


def test_trailing_window_requires_a_complete_window() -> None:
    """A partial history is never reported as stable."""
    assert not trailing_window_stable([1.0], 2, 0.0)


def test_criterion_constructor_and_combinators_validate_children() -> None:
    """Configured and composite criteria reject invalid empty definitions."""
    with pytest.raises(ValueError, match="window"):
        TrailingWindowCriterion(1, 0.0)
    with pytest.raises(ValueError, match="tolerance"):
        TrailingWindowCriterion(2, -1.0)
    with pytest.raises(ValueError, match="AnyCriterion"):
        AnyCriterion(())
    with pytest.raises(ValueError, match="AllCriterion"):
        AllCriterion(())


def test_any_and_all_criteria_short_circuit_on_child_results() -> None:
    """Any and all expose normal Boolean composition over child criteria."""
    stable = TrailingWindowCriterion(2, 0.0)
    unstable = TrailingWindowCriterion(2, 0.1)

    assert AnyCriterion((stable, unstable)).is_stable([1.0, 1.0])
    assert not AllCriterion((stable, unstable)).is_stable([1.0, 1.2])


def test_monitor_rejects_invalid_records_and_records_history() -> None:
    """Monitor inputs are ordered, finite, and immutable after stopping."""
    monitor = ConvergenceMonitor(TrailingWindowCriterion(4, 0.0), max_generations=4)
    with pytest.raises(ValueError, match="max_generations"):
        ConvergenceMonitor(TrailingWindowCriterion(2, 0.0), max_generations=0)
    with pytest.raises(ValueError, match="non-negative"):
        monitor.record(-1, 1.0)
    monitor.record(0, 1.0)
    with pytest.raises(ValueError, match="increasing"):
        monitor.record(0, 1.0)
    with pytest.raises(ValueError, match="finite"):
        monitor.record(1, math.nan)
    assert monitor.generations == (0,)
    assert monitor.history == (1.0,)
    assert not monitor.should_stop()


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
    monitor = ConvergenceMonitor(TrailingWindowCriterion(2, 0.0), max_generations=2)
    monitor.record(0, 1.0)
    outcome = monitor.record(1, 1.0)
    assert outcome.stopped
    with pytest.raises(RuntimeError, match="stopped"):
        monitor.record(2, 1.0)
