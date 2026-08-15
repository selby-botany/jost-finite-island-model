"""Pluggable criteria for statistic-history stability."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

MINIMUM_WINDOW = 2


class ConvergenceCriterion(Protocol):
    """Decide whether a statistic history is stable."""

    def is_stable(self, history: Sequence[float]) -> bool:
        """Return whether the supplied history satisfies this criterion."""
        ...


def trailing_window_stable(
    history: Sequence[float],
    window: int,
    tolerance: float,
) -> bool:
    """Compare the means of the two halves of a trailing window.

    Args:
        history: Ordered statistic values.
        window: Number of trailing observations to inspect.
        tolerance: Maximum absolute difference between half-window means.

    Returns:
        ``True`` only after a full stable window is available.
    """
    if window < MINIMUM_WINDOW:
        raise ValueError("window must be at least 2")
    if not math.isfinite(tolerance) or tolerance < 0.0:
        raise ValueError("tolerance must be finite and non-negative")
    if len(history) < window:
        return False
    trailing = history[-window:]
    midpoint = window // 2
    first = trailing[:midpoint]
    second = trailing[midpoint:]
    first_mean = math.fsum(first) / len(first)
    second_mean = math.fsum(second) / len(second)
    return abs(first_mean - second_mean) <= tolerance


@dataclass(frozen=True, slots=True)
class TrailingWindowCriterion:
    """Detect stability by comparing two halves of a trailing window."""

    window: int
    tolerance: float

    def __post_init__(self) -> None:
        """Validate criterion configuration on construction."""
        if self.window < MINIMUM_WINDOW:
            raise ValueError("window must be at least 2")
        if not math.isfinite(self.tolerance) or self.tolerance < 0.0:
            raise ValueError("tolerance must be finite and non-negative")

    def is_stable(self, history: Sequence[float]) -> bool:
        """Return whether the configured trailing window is stable."""
        return trailing_window_stable(history, self.window, self.tolerance)


@dataclass(frozen=True, slots=True)
class AnyCriterion:
    """Declare stability when any child criterion is stable."""

    criteria: tuple[ConvergenceCriterion, ...]

    def __post_init__(self) -> None:
        """Reject an empty combinator."""
        if not self.criteria:
            raise ValueError("AnyCriterion requires at least one criterion")

    def is_stable(self, history: Sequence[float]) -> bool:
        """Return whether any child criterion is stable."""
        return any(criterion.is_stable(history) for criterion in self.criteria)


@dataclass(frozen=True, slots=True)
class AllCriterion:
    """Declare stability only when every child criterion is stable."""

    criteria: tuple[ConvergenceCriterion, ...]

    def __post_init__(self) -> None:
        """Reject an empty combinator."""
        if not self.criteria:
            raise ValueError("AllCriterion requires at least one criterion")

    def is_stable(self, history: Sequence[float]) -> bool:
        """Return whether every child criterion is stable."""
        return all(criterion.is_stable(history) for criterion in self.criteria)
