"""Stateful convergence monitoring with an explicit hard-cap outcome."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from fim.convergence.criteria import ConvergenceCriterion


class StopReason(StrEnum):
    """Reason a simulation stopped."""

    STATISTIC_CONVERGED = "statistic converged"
    MAX_GENERATIONS = "hit the cap"


@dataclass(frozen=True, slots=True)
class ConvergenceOutcome:
    """Describe a monitor's terminal decision."""

    stopped: bool
    converged: bool
    reason: StopReason | None
    generation: int | None


class ConvergenceMonitor:
    """Record one watched statistic and report why a run should stop."""

    def __init__(
        self,
        criterion: ConvergenceCriterion,
        *,
        max_generations: int,
    ) -> None:
        """Initialize an empty monitor.

        Args:
            criterion: Statistical stability rule.
            max_generations: Hard generation safety cap.
        """
        if max_generations < 1:
            raise ValueError("max_generations must be at least 1")
        self._criterion = criterion
        self._max_generations = max_generations
        self._generations: list[int] = []
        self._history: list[float] = []
        self._outcome = ConvergenceOutcome(False, False, None, None)

    @property
    def generations(self) -> tuple[int, ...]:
        """Return recorded generations in order."""
        return tuple(self._generations)

    @property
    def history(self) -> tuple[float, ...]:
        """Return recorded statistic values in order."""
        return tuple(self._history)

    def outcome(self) -> ConvergenceOutcome:
        """Return the current terminal or running outcome."""
        return self._outcome

    def reason(self) -> StopReason | None:
        """Return the terminal reason, or ``None`` while running."""
        return self._outcome.reason

    def record(self, generation: int, value: float) -> ConvergenceOutcome:
        """Record one ordered observation and update the stop decision.

        Args:
            generation: Non-negative generation number.
            value: Finite value of the watched statistic.

        Returns:
            The updated outcome.
        """
        if self._outcome.stopped:
            raise RuntimeError("cannot record after convergence monitoring stopped")
        if generation < 0:
            raise ValueError("generation must be non-negative")
        if self._generations and generation <= self._generations[-1]:
            raise ValueError("generations must be recorded in increasing order")
        if not math.isfinite(value):
            raise ValueError("convergence statistic must be finite")
        self._generations.append(generation)
        self._history.append(value)

        if self._criterion.is_stable(self._history):
            self._outcome = ConvergenceOutcome(
                stopped=True,
                converged=True,
                reason=StopReason.STATISTIC_CONVERGED,
                generation=generation,
            )
        elif generation >= self._max_generations:
            self._outcome = ConvergenceOutcome(
                stopped=True,
                converged=False,
                reason=StopReason.MAX_GENERATIONS,
                generation=generation,
            )
        return self._outcome

    def should_stop(self) -> bool:
        """Return whether statistical convergence or the hard cap fired."""
        return self._outcome.stopped
