"""Pluggable criteria for statistic-history stability across replicates.

A replicate batch cannot know in advance how many replicates a confidence
interval needs. This module defines the rule the batch loop applies after each
completed replicate: a `ConvergenceCriterion` answers one yes/no question,
"is this history tight enough to stop now?", so `fim.convergence.monitor.
ConvergenceMonitor` never needs to know which rule it is applying. The one
concrete rule is `ConfidenceIntervalCriterion`.

A single run is not judged by a criterion at all: it burns in and then
averages (`fim.convergence.monitor.BurnInMonitor`).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from fim.config.numerics import MINIMUM_REPLICATE_COUNT
from fim.statistics.interval import confidence_interval


class ConvergenceCriterion(Protocol):
    """Decide whether a statistic history is stable.

    A "protocol" here is Python's way of saying "any object with this
    one method" — this class is never instantiated directly and defines
    no behavior of its own; it exists purely so that
    `fim.convergence.monitor.ConvergenceMonitor` can accept *any*
    object that answers `is_stable` the same way, whether that object
    is `ConfidenceIntervalCriterion` or something built elsewhere entirely.
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


@dataclass(frozen=True, slots=True)
class ConfidenceIntervalCriterion:
    """Detect a tight-enough confidence interval on a growing sample.

    This criterion treats
    the *entire* supplied history as one growing i.i.d. sample — each
    entry is one independently seeded replicate run's own final scalar
    outcome — and asks whether that sample's Student's-t confidence
    interval has tightened to at most `tolerance`, an absolute
    half-width in the same units as the watched statistic. `minimum_count`
    guards against a lucky-early-tight fluke: stability is never declared
    from fewer than `minimum_count` observations.
    """

    minimum_count: int
    tolerance: float
    confidence: float = 0.95

    def __post_init__(self) -> None:
        """Validate criterion configuration on construction.

        Validation lives in this hook rather than in the field declarations
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
