"""Stateful convergence monitoring with an explicit hard-cap outcome.

`fim.convergence.criteria` defines the individual *rules* for deciding
whether a statistic's history has settled down; this module is the
class that actually drives a run using one of those rules, generation
by generation (or replicate by replicate, for a batch): it remembers
every value recorded so far, asks the configured criterion whether
things have stabilized after each new one arrives, and — separately —
always enforces a hard generation cap regardless of what the criterion
says, so that a statistic that genuinely never settles (a legitimate,
if unwanted, outcome for some parameter combinations) still cannot
run a simulation forever. `ConvergenceOutcome` is the small, immutable
record this class hands back describing which of those two things
happened, if either yet has.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from fim.convergence.criteria import ConvergenceCriterion

Combinator = Literal["any", "all"]


class StopReason(StrEnum):
    """Reason a simulation stopped.

    A run always stops for exactly one of these two reasons — there is
    no third way for the simulation loop to exit. `STATISTIC_CONVERGED`
    means the watched statistic(s) satisfied the configured
    `fim.convergence.criteria.ConvergenceCriterion` before the
    generation cap was reached; `MAX_GENERATIONS` means the cap was hit
    first. Reaching the cap is reported as a valid, non-error outcome
    (see `ConvergenceOutcome.converged`) — some parameter combinations
    genuinely never settle within any reasonable number of generations,
    and that is itself a real, useful finding about those parameters,
    not a failure of the tool.
    """

    STATISTIC_CONVERGED = "statistic converged"
    MAX_GENERATIONS = "hit the cap"


@dataclass(frozen=True, slots=True)
class ConvergenceOutcome:
    """Describe a monitor's terminal decision.

    Returned by `ConvergenceMonitor.record` after every observation,
    and retrievable at any time via `ConvergenceMonitor.outcome`. While
    a run is still in progress (neither converged nor capped yet) this
    is a "not stopped" placeholder with every other field `None`/
    `False`; once the run does stop, the four fields together are its
    complete, permanent answer to "why, and at which generation."

    Args:
        stopped: Whether the monitor has reached a terminal decision at
            all (``False`` for every observation until the run
            actually stops; once ``True``, it stays ``True`` and no
            further observations can be recorded — see
            `ConvergenceMonitor.record`).
        converged: Whether the watched statistic(s) actually stabilized
            (``True``), as opposed to the run instead being stopped by
            the hard generation cap (``False``). Only meaningful once
            `stopped` is ``True``.
        reason: Which of the two `StopReason` values applies, or
            ``None`` while the run is still in progress.
        generation: The generation number at which the run stopped, or
            ``None`` while still in progress.
    """

    stopped: bool
    converged: bool
    reason: StopReason | None
    generation: int | None


class ConvergenceMonitor:
    """Record one or more watched statistics and report why a run should stop.

    This is the class the run loop actually calls, once per generation
    (or, for a replicate batch, once per completed replicate): give it
    the newest value(s) via `record`, and it remembers the whole
    history, asks the configured `fim.convergence.criteria.
    ConvergenceCriterion` whether things have settled, and separately
    checks the hard generation cap — see this module's own docstring
    for that split of responsibility. Everything the caller needs to
    know about the result of that decision (whether the run should
    stop yet, and if so why) comes back as a `ConvergenceOutcome`.

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

    A statistic can be legitimately undefined on a given round (see
    `record`'s ``value`` argument): rather than raise or invent a
    substitute number, that round simply contributes nothing to that
    statistic's own history, so its stability is judged once enough
    *defined* rounds have accumulated — never sooner, from a padded
    history, and never blocked by a round where a different statistic
    happened to have no value.
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

        Nothing has been recorded yet immediately after construction —
        `outcome()` returns a "still running" placeholder, and
        `record()` must be called at least once before any stop
        decision can be made.

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
        """Return recorded generations in order.

        The generation number recorded alongside each `record()` call,
        in the same order they were recorded — parallel to `history`
        (or each series in `histories`), so pairing up
        ``zip(monitor.generations, monitor.history)`` reconstructs
        exactly what was passed to `record` each round.
        """
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
        """Return the current terminal or running outcome.

        Safe to call at any time, including before the first `record`
        call (see `ConvergenceOutcome`'s own docstring for what the
        "still running" placeholder looks like) and any number of
        times after the monitor has stopped — unlike `record`, calling
        this again never raises and never changes anything.
        """
        return self._outcome

    def reason(self) -> StopReason | None:
        """Return the terminal reason, or ``None`` while running.

        A convenience for reading just `outcome().reason` without
        needing the rest of the outcome — used, for example, when only
        the human-readable stop reason is needed for a report.
        """
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
                several, pass a mapping. The mapping need not cover every
                configured name: a statistic it omits simply is not
                appended to that statistic's own history this round —
                the caller's way of reporting "this statistic has no
                defined value for this round" without fabricating one or
                blocking the round's other, defined statistics. Every
                key the mapping *does* include, however, must name a
                configured statistic; an unrecognized name is far more
                likely a typo than an intentional omission, so it still
                raises.

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

        # Each watched statistic's own history is judged by the same
        # criterion, independently — one statistic's history says
        # nothing about another's — and `combinator` then decides
        # whether every one of those independent yes/no answers must
        # agree ("all") or just one of them needs to ("any"). See
        # `ConvergenceMonitor`'s own class docstring for why this is a
        # genuine no-op with only one watched statistic.
        per_statistic_stable = (
            self._criterion.is_stable(self._histories[name])
            for name in self._statistics
        )
        is_stable = (
            all(per_statistic_stable)
            if self._combinator == "all"
            else any(per_statistic_stable)
        )
        # Convergence is checked before the hard cap: if a statistic
        # happens to stabilize on the very generation the cap would
        # also have fired, the run is reported as having converged,
        # not as having merely run out of generations — the more
        # informative and more accurate of the two true facts about
        # what happened.
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
        """Return whether statistical convergence or the hard cap fired.

        A convenience for the run loop's own stop check — equivalent to
        `outcome().stopped`, without needing `converged`/`reason` too.
        """
        return self._outcome.stopped

    def _resolve_values(
        self,
        value: float | Mapping[str, float],
    ) -> dict[str, float]:
        """Normalize a bare float or a per-statistic mapping into full form.

        A mapping may be a partial or even empty subset of the configured
        statistics (see `record`'s ``value`` argument) — every key it
        includes is validated against the configured names, but no key is
        required to be present. A bare float is only legal while watching
        exactly one statistic, in which case it is treated as that one
        statistic's own value for this round — the shorthand every
        single-statistic caller (the ordinary case) uses instead of
        writing out a one-entry mapping every time.
        """
        if isinstance(value, Mapping):
            unknown = set(value) - set(self._statistics)
            if unknown:
                expected = ", ".join(sorted(self._statistics))
                names = ", ".join(sorted(unknown))
                raise ValueError(
                    f"record() values named unconfigured statistic(s) "
                    f"{names}; configured: {expected}"
                )
            return dict(value)
        if len(self._statistics) != 1:
            raise ValueError(
                "record() requires a mapping of statistic name to value "
                "while watching several statistics"
            )
        return {self._statistics[0]: value}
