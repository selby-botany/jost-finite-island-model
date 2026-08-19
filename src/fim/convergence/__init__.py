"""Convergence criteria and run-loop monitoring."""

from fim.convergence.criteria import (
    AllCriterion,
    AnyCriterion,
    ConfidenceIntervalCriterion,
    ConvergenceCriterion,
    TrailingWindowCriterion,
    trailing_window_stable,
)
from fim.convergence.monitor import (
    ConvergenceMonitor,
    ConvergenceOutcome,
    StopReason,
)

__all__ = [
    "AllCriterion",
    "AnyCriterion",
    "ConfidenceIntervalCriterion",
    "ConvergenceCriterion",
    "ConvergenceMonitor",
    "ConvergenceOutcome",
    "StopReason",
    "TrailingWindowCriterion",
    "trailing_window_stable",
]
