"""Pluggable criteria for statistic-history stability.

A finite-island simulation cannot know in advance how many generations
it will take for its statistics (D, G_ST, and so on) to stop drifting
and settle down — that number depends on the parameters of the
specific run (population sizes, migration and mutation rates) in a way
that is not known ahead of time. Rather than guessing a fixed number of
generations and hoping it is enough, this module defines "convergence
criteria": small, swappable rules that look at a statistic's history so
far and answer one yes/no question — "has this settled down enough to
stop now?" — every generation, so a run can stop exactly when its own
answer is actually stable, whether that takes 50 generations or 5,000.

Every criterion in this file implements the same `ConvergenceCriterion`
protocol (a single `is_stable` method), so `fim.convergence.monitor.
ConvergenceMonitor` — the class that actually drives a run's stop
decision — never needs to know *which* rule it is applying, only that
whatever object it was given can answer that one question. This module
provides two concrete rules (`TrailingWindowCriterion`, the ordinary
within-run default, and `ConfidenceIntervalCriterion`, used for
replicate batches — see each class's own docstring for when to use
which) plus two combinators (`AnyCriterion`, `AllCriterion`) for
requiring several statistics — or several different rules on the same
statistic — to agree before declaring convergence.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from fim.statistics.interval import confidence_interval

MINIMUM_WINDOW = 2
MINIMUM_REPLICATE_COUNT = 2


class ConvergenceCriterion(Protocol):
    """Decide whether a statistic history is stable.

    A "protocol" here is Python's way of saying "any object with this
    one method" — this class is never instantiated directly and defines
    no behavior of its own; it exists purely so that
    `fim.convergence.monitor.ConvergenceMonitor` can accept *any*
    object that answers `is_stable` the same way, whether that object
    is `TrailingWindowCriterion`, `ConfidenceIntervalCriterion`, one of
    the two combinators below, or something built elsewhere entirely.
    """

    def is_stable(self, history: Sequence[float]) -> bool:
        """Return whether the supplied history satisfies this criterion.

        Args:
            history: The watched statistic's own recorded values so
                far, oldest first — exactly what
                `fim.convergence.monitor.ConvergenceMonitor` has
                accumulated for one statistic up to the current
                generation or replicate.

        Returns:
            ``True`` once, in this criterion's own judgment, the
            history has settled down enough to justify stopping;
            ``False`` while it should keep collecting more values.
        """
        ...


def trailing_window_stable(
    history: Sequence[float],
    window: int,
    tolerance: float,
) -> bool:
    """Compare the means of the two halves of a trailing window.

    This is the plain, direct way to ask "has this number stopped
    changing?" without any statistical machinery: look at the most
    recent `window` values, split that block in half, average each
    half separately, and see how close the two averages are to each
    other. A statistic that is still trending up or down noticeably
    generation to generation will show a real gap between its earlier
    and later half; one that has settled into its long-run value will
    not, since both halves are then just noisy samples of the same
    underlying number. `tolerance` is how close counts as "close
    enough," in the watched statistic's own units (for example, 0.01
    on a statistic that itself ranges from 0 to 1).

    An odd `window` splits as `window // 2` observations in the first
    half and one more in the second (e.g. a window of 5 compares 2
    against 3) — a legal configuration, not an error, but it means the
    tolerance is being compared against unevenly sized samples for an
    odd window and evenly sized ones for an even window.

    Args:
        history: Ordered statistic values, oldest first.
        window: Number of trailing (most recent) observations to
            inspect; must be at least 2, since splitting anything
            smaller in half leaves an empty side to average.
        tolerance: Maximum absolute difference between half-window
            means that still counts as "stable."

    Returns:
        ``True`` only once `history` holds at least `window` values
        *and* the two halves' means are within `tolerance` of each
        other; ``False`` beforehand, however small `tolerance` is —
        a window that has not yet fully filled cannot be judged stable
        or unstable at all.

    Raises:
        ValueError: If `window` is smaller than 2, or `tolerance` is
            negative or not a finite number (``NaN`` or infinity).
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
    """Detect stability by comparing two halves of a trailing window.

    This is the ordinary, default convergence rule used *within* one
    simulation run (as opposed to `ConfidenceIntervalCriterion`, used
    *across* several replicate runs of the same parameters) — the
    `convergence_window`/`convergence_tolerance` configuration fields
    documented in `doc/configuration.md` configure exactly this class.
    A thin, `ConvergenceCriterion`-shaped wrapper around
    `trailing_window_stable`, above — see that function's own
    docstring for what "stable" actually means here and why it is
    judged this way; this class exists only so a caller can hold one
    pre-configured object (with `window`/`tolerance` already fixed)
    and call `is_stable(history)` on it repeatedly, rather than passing
    all three arguments to the bare function every time.
    """

    window: int
    tolerance: float

    def __post_init__(self) -> None:
        """Validate criterion configuration on construction.

        Dataclass field validation cannot happen in the field
        declarations themselves, so `__post_init__` (a hook the
        `dataclass` decorator calls automatically right after every
        field is set) is where it happens instead — the same reason
        `ConfidenceIntervalCriterion`, `AnyCriterion`, and
        `AllCriterion`, below, each define one too. Rejecting an
        invalid `window`/`tolerance` here, at construction time,
        surfaces a configuration mistake immediately rather than
        letting it silently produce a criterion that can never
        actually detect stability once a run is already under way.
        """
        if self.window < MINIMUM_WINDOW:
            raise ValueError("window must be at least 2")
        if not math.isfinite(self.tolerance) or self.tolerance < 0.0:
            raise ValueError("tolerance must be finite and non-negative")

    def is_stable(self, history: Sequence[float]) -> bool:
        """Return whether the configured trailing window is stable."""
        return trailing_window_stable(history, self.window, self.tolerance)


@dataclass(frozen=True, slots=True)
class ConfidenceIntervalCriterion:
    """Detect a tight-enough confidence interval on a growing sample.

    Unlike `TrailingWindowCriterion`, which compares two halves of a
    trailing window of near-instantaneous values, this criterion treats
    the *entire* supplied history as one growing i.i.d. sample — each
    entry is one independently seeded replicate run's own final scalar
    outcome — and asks whether that sample's Student's-t confidence
    interval has tightened to at most `tolerance`, an absolute
    half-width in the same units as the watched statistic, exactly like
    `TrailingWindowCriterion.tolerance`. `minimum_count` guards against a
    lucky-early-tight fluke the same way `TrailingWindowCriterion.window`
    guards a single-generation coincidence: stability is never declared
    from fewer than `minimum_count` observations.
    """

    minimum_count: int
    tolerance: float
    confidence: float = 0.95

    def __post_init__(self) -> None:
        """Validate criterion configuration on construction.

        See `TrailingWindowCriterion.__post_init__` for why validation
        lives in this hook rather than in the field declarations
        themselves.
        """
        if self.minimum_count < MINIMUM_REPLICATE_COUNT:
            raise ValueError("minimum_count must be at least 2")
        if not math.isfinite(self.tolerance) or self.tolerance < 0.0:
            raise ValueError("tolerance must be finite and non-negative")
        if self.confidence not in (0.90, 0.95, 0.99):
            raise ValueError("confidence must be 0.90, 0.95, or 0.99")

    def is_stable(self, history: Sequence[float]) -> bool:
        """Return whether the sample's confidence interval is tight enough.

        See `fim.statistics.interval` for what a confidence interval is
        and why the Student's-t method is used to compute one; this
        method's whole job is deciding whether that computed interval
        (specifically its `half_width`, the "± 3%" half of a "52% ±
        3%"-style report) has narrowed to at most `tolerance` yet.
        """
        if len(history) < self.minimum_count:
            return False
        interval = confidence_interval(history, confidence=self.confidence)
        return interval["half_width"] <= self.tolerance


@dataclass(frozen=True, slots=True)
class AnyCriterion:
    """Declare stability when any child criterion is stable.

    A "combinator" here means an object that is itself a
    `ConvergenceCriterion` (it has an `is_stable` method, exactly like
    `TrailingWindowCriterion` or `ConfidenceIntervalCriterion`), but
    computes its own answer by asking several *other* criteria and
    combining their answers, rather than looking at the history
    directly itself — this is what makes it possible to require, say,
    "either a trailing window has settled *or* a confidence interval
    has tightened enough" as a single rule, by wrapping one of each
    inside an `AnyCriterion`. Note the distinction from
    `fim.convergence.monitor.ConvergenceMonitor`'s own ``combinator``
    setting: that combinator decides how *several statistics* (e.g.
    both D and G_ST) must agree, each judged by the *same* criterion,
    while `AnyCriterion`/`AllCriterion` instead combine several
    *criteria* applied to the *same* one statistic's history. The two
    can be nested together when a project genuinely needs both at once.
    """

    criteria: tuple[ConvergenceCriterion, ...]

    def __post_init__(self) -> None:
        """Reject an empty combinator.

        A combinator with zero child criteria could never mean
        anything sensible — "any of these" and "all of these" are both
        undefined once there is nothing to check — so this is caught
        immediately at construction rather than silently producing an
        object whose `is_stable` would need a special-cased answer.
        """
        if not self.criteria:
            raise ValueError("AnyCriterion requires at least one criterion")

    def is_stable(self, history: Sequence[float]) -> bool:
        """Return whether any child criterion is stable."""
        return any(criterion.is_stable(history) for criterion in self.criteria)


@dataclass(frozen=True, slots=True)
class AllCriterion:
    """Declare stability only when every child criterion is stable.

    The stricter counterpart to `AnyCriterion`, above — see that
    class's own docstring for what a "combinator" is here and how this
    differs from `fim.convergence.monitor.ConvergenceMonitor`'s own,
    differently scoped ``combinator`` setting.
    """

    criteria: tuple[ConvergenceCriterion, ...]

    def __post_init__(self) -> None:
        """Reject an empty combinator.

        See `AnyCriterion.__post_init__` for why an empty combinator is
        rejected immediately rather than left to define `is_stable`'s
        behavior on zero children.
        """
        if not self.criteria:
            raise ValueError("AllCriterion requires at least one criterion")

    def is_stable(self, history: Sequence[float]) -> bool:
        """Return whether every child criterion is stable."""
        return all(criterion.is_stable(history) for criterion in self.criteria)
