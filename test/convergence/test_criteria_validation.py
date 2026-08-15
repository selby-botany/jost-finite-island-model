"""Validation and combinator tests for convergence criteria."""

from __future__ import annotations

import math

import pytest

from fim.convergence.criteria import (
    AllCriterion,
    AnyCriterion,
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


def test_monitor_rejects_records_after_convergence() -> None:
    """A terminal monitor cannot accept observations after its decision."""
    monitor = ConvergenceMonitor(TrailingWindowCriterion(2, 0.0), max_generations=2)
    monitor.record(0, 1.0)
    outcome = monitor.record(1, 1.0)
    assert outcome.stopped
    with pytest.raises(RuntimeError, match="stopped"):
        monitor.record(2, 1.0)
