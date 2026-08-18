"""Stateful convergence monitoring with an explicit hard-cap outcome."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from fim.convergence.criteria import ConvergenceCriterion

Combinator = Literal["any", "all"]


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
    """Record one or more watched statistics and report why a run should stop.

    A single statistic (the default) is this class's ordinary mode: every
    method behaves exactly as it did before several-statistic support
    existed. Passing more than one name in ``statistics`` is additive —
    each statistic keeps its own independent history, the same criterion is
    applied to each one separately, and ``combinator`` decides whether
    stopping requires every statistic to be simultaneously stable
    (``"all"``, design §9's "several statistics needed to agree") or just
    one of them (``"any"``). With exactly one statistic, ``all`` and
    ``any`` of a single Boolean are the same value, so the combinator is a
    genuine no-op in that case rather than a separately tested path.
    """

    def __init__(
        self,
        criterion: ConvergenceCriterion,
        *,
        max_generations: int,
        statistics: Sequence[str] = ("value",),
        combinator: Combinator = "all",
    ) -> None:
        """Initialize an empty monitor.

        Args:
            criterion: Statistical stability rule, applied independently to
                each watched statistic's own history.
            max_generations: Hard generation safety cap.
            statistics: Names of the statistic(s) to watch. Defaults to one
                unnamed statistic, matching ``record()``'s bare-float form.
            combinator: ``"all"`` requires every statistic to be stable
                before stopping; ``"any"`` requires only one.

        Raises:
            ValueError: If ``max_generations``, ``statistics``, or
                ``combinator`` is invalid.
        """
        if max_generations < 1:
            raise ValueError("max_generations must be at least 1")
        statistic_names = tuple(statistics)
        if not statistic_names:
            raise ValueError("statistics must not be empty")
        if len(set(statistic_names)) != len(statistic_names):
            raise ValueError("statistics must not repeat a name")
        if combinator not in {"any", "all"}:
            raise ValueError("combinator must be 'any' or 'all'")
        self._criterion = criterion
        self._max_generations = max_generations
        self._statistics = statistic_names
        self._combinator = combinator
        self._generations: list[int] = []
        self._histories: dict[str, list[float]] = {name: [] for name in statistic_names}
        self._outcome = ConvergenceOutcome(False, False, None, None)

    @property
    def generations(self) -> tuple[int, ...]:
        """Return recorded generations in order."""
        return tuple(self._generations)

    @property
    def history(self) -> tuple[float, ...]:
        """Return the primary (first-configured) statistic's recorded values.

        With one watched statistic — the ordinary case — this is that
        statistic's complete history. With several, it is only the first
        one named in ``statistics``; use ``histories`` for every statistic.
        """
        return tuple(self._histories[self._statistics[0]])

    @property
    def histories(self) -> Mapping[str, tuple[float, ...]]:
        """Return every watched statistic's recorded values, by name."""
        return {name: tuple(values) for name, values in self._histories.items()}

    def outcome(self) -> ConvergenceOutcome:
        """Return the current terminal or running outcome."""
        return self._outcome

    def reason(self) -> StopReason | None:
        """Return the terminal reason, or ``None`` while running."""
        return self._outcome.reason

    def record(
        self,
        generation: int,
        value: float | Mapping[str, float],
    ) -> ConvergenceOutcome:
        """Record one ordered observation and update the stop decision.

        Args:
            generation: Non-negative generation number.
            value: The watched statistic's finite value. A bare float is
                only accepted while watching exactly one statistic; with
                several, pass a mapping covering every configured name.

        Returns:
            The updated outcome.

        Raises:
            RuntimeError: If called after the monitor already stopped.
            ValueError: If ``generation`` or ``value`` is invalid.
        """
        if self._outcome.stopped:
            raise RuntimeError("cannot record after convergence monitoring stopped")
        if generation < 0:
            raise ValueError("generation must be non-negative")
        if self._generations and generation <= self._generations[-1]:
            raise ValueError("generations must be recorded in increasing order")

        values = self._resolve_values(value)
        for statistic, number in values.items():
            if not math.isfinite(number):
                raise ValueError(f"convergence statistic {statistic!r} must be finite")

        self._generations.append(generation)
        for statistic, number in values.items():
            self._histories[statistic].append(number)

        per_statistic_stable = (
            self._criterion.is_stable(self._histories[name])
            for name in self._statistics
        )
        is_stable = (
            all(per_statistic_stable)
            if self._combinator == "all"
            else any(per_statistic_stable)
        )
        if is_stable:
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

    def _resolve_values(
        self,
        value: float | Mapping[str, float],
    ) -> dict[str, float]:
        """Normalize a bare float or a per-statistic mapping into full form."""
        if isinstance(value, Mapping):
            if set(value) != set(self._statistics):
                expected = ", ".join(sorted(self._statistics))
                raise ValueError(f"record() values must cover exactly: {expected}")
            return dict(value)
        if len(self._statistics) != 1:
            raise ValueError(
                "record() requires a mapping of statistic name to value "
                "while watching several statistics"
            )
        return {self._statistics[0]: value}
