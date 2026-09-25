"""Tests for `TrailingWindowTracker`: the O(1) form of the trailing check."""

from __future__ import annotations

import random
from collections.abc import Sequence

import pytest

from fim.convergence.criteria import (
    TrailingWindowCriterion,
    TrailingWindowTracker,
    trailing_window_stable,
)
from fim.convergence.monitor import ConvergenceMonitor


def _series(kind: str, length: int, seed: int) -> list[float]:
    """Return a deterministic test series of one named shape."""
    rng = random.Random(seed)
    if kind == "uniform":
        return [rng.random() for _ in range(length)]
    if kind == "decay":
        return [0.5 * 0.999**index + rng.gauss(0.0, 1e-4) for index in range(length)]
    if kind == "cancellation":
        # Large opposite-signed values whose sum loses precision in naive
        # summation, exercising the exactness of both implementations.
        return [(-1.0) ** index * 1e16 + rng.random() for index in range(length)]
    if kind == "tiny":
        return [rng.random() * 5e-324 * 1e3 for _ in range(length)]
    raise AssertionError(kind)


@pytest.mark.parametrize("kind", ["uniform", "decay", "cancellation", "tiny"])
@pytest.mark.parametrize("window", [2, 3, 7, 50, 51])
def test_tracker_decides_identically_to_the_reference_at_every_step(
    kind: str, window: int
) -> None:
    """Every step's decision equals `trailing_window_stable`'s, exactly."""
    history: list[float] = []
    tracker = TrailingWindowTracker(window, 0.01)
    for value in _series(kind, 400, seed=window):
        history.append(value)
        tracker.push(value)
        assert tracker.is_stable() == trailing_window_stable(history, window, 0.01)


def test_tracker_is_false_until_the_window_fills() -> None:
    """A window that has not filled is never stable."""
    tracker = TrailingWindowTracker(4, 1.0)
    for _ in range(3):
        tracker.push(0.5)
        assert not tracker.is_stable()
    tracker.push(0.5)
    assert tracker.is_stable()


def test_tracker_accepts_integers() -> None:
    """The monitor may be given ints; they are exact too."""
    tracker = TrailingWindowTracker(2, 0.0)
    tracker.push(3)  # type: ignore[arg-type]
    tracker.push(3)  # type: ignore[arg-type]
    assert tracker.is_stable()


@pytest.mark.parametrize(("window", "tolerance"), [(1, 0.1), (5, -0.1)])
def test_tracker_validates_its_arguments(window: int, tolerance: float) -> None:
    """Bad arguments are rejected at construction, like the criterion."""
    with pytest.raises(ValueError, match=r"window|tolerance"):
        TrailingWindowTracker(window, tolerance)


class _PlainCriterion:
    """A trailing-window rule that is not a `TrailingWindowCriterion`.

    Forces the monitor's full-history path, the reference behavior.
    """

    def __init__(self, window: int, tolerance: float) -> None:
        self._window = window
        self._tolerance = tolerance

    def is_stable(self, history: Sequence[float]) -> bool:
        """Delegate to the reference function."""
        return trailing_window_stable(history, self._window, self._tolerance)


@pytest.mark.parametrize("window", [2, 5, 40, 41])
def test_monitor_stops_at_the_same_generation_on_either_path(window: int) -> None:
    """The tracker path and the full-history path stop together."""
    stops = []
    for criterion in (
        TrailingWindowCriterion(window, 0.005),
        _PlainCriterion(window, 0.005),
    ):
        monitor = ConvergenceMonitor(criterion, max_generations=2000)
        for generation, value in enumerate(_series("decay", 2001, seed=window)):
            if monitor.record(generation, value).stopped:
                break
        stops.append(monitor.outcome())
    assert stops[0] == stops[1]
    assert stops[0].converged
