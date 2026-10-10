"""Convergence rules and run-loop monitoring.

This package answers "when has this simulation run been going on long
enough?" It is organized into four modules:

- `fim.convergence.defaults` — derives a default burn-in and generation cap
  from the model's own relaxation time (the slowest locus sets it), since no
  fixed number is right for every migration and mutation regime.
- `fim.convergence.window_statistics` — how precisely an evidence window's
  mean is known, allowing for the correlation between neighboring
  generations (Geyer's estimator), and Geweke's start-versus-end check.
- `fim.convergence.criteria` — the rule a replicate batch applies across
  replicates (a confidence interval tight enough).
- `fim.convergence.monitor` — the stateful classes that drive a run:
  `BurnInMonitor` (burn in, then average a single run until its watched
  statistics reach the requested precision) and `ConvergenceMonitor` (the
  replicate batch). Both enforce a hard cap so a run that never reaches the
  precision still cannot run forever. `ConvergenceOutcome` and `StopReason`
  describe the result.

The public names from the modules are re-exported here.
"""

from fim.convergence.criteria import (
    ConfidenceIntervalCriterion,
    ConvergenceCriterion,
)
from fim.convergence.defaults import (
    DerivedConvergence,
    burn_in_multiple,
    derive_burn_in,
    derive_convergence_defaults,
)
from fim.convergence.monitor import (
    BurnInMonitor,
    ConvergenceMonitor,
    ConvergenceOutcome,
    StopReason,
)

__all__ = [
    "BurnInMonitor",
    "ConfidenceIntervalCriterion",
    "ConvergenceCriterion",
    "ConvergenceMonitor",
    "ConvergenceOutcome",
    "DerivedConvergence",
    "StopReason",
    "burn_in_multiple",
    "derive_burn_in",
    "derive_convergence_defaults",
]
