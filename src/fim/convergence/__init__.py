"""Convergence criteria and run-loop monitoring.

This package answers "when has this simulation run been going on long
enough?" It is organized into two modules:

- `fim.convergence.criteria` — the individual, swappable *rules* for
  judging whether a statistic's history has settled down (a trailing-
  window comparison for a single run, a confidence-interval check
  across replicates, and two combinators for requiring several rules
  or several statistics to agree). See that module's own docstring for
  why no single fixed generation count could work for every run.
- `fim.convergence.monitor` — the stateful class (`ConvergenceMonitor`)
  that actually drives a run using one of those rules: it accumulates
  the watched statistic's history generation by generation, asks the
  configured rule whether to stop, and separately enforces a hard
  generation safety cap so a run that genuinely never settles still
  cannot run forever. `ConvergenceOutcome` and `StopReason` describe
  its result.

Every public name from both modules is re-exported here.
"""

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
