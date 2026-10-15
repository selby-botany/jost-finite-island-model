"""The grouped, immutable specification of a simulation run.

`RunSpec` holds everything that determines a run's numbers; `Execution` and
`Recording` are passed beside it and never change a result. See the public
API redesign design for the reasoning behind the grouping.
"""

from __future__ import annotations

from fim.spec.execution import Execution, Recording
from fim.spec.model import (
    EquilibriumSplit,
    ExplicitFrequencies,
    FiniteAlleles,
    FoundingAlleles,
    InfiniteAlleles,
    InitialCondition,
    Locus,
    Model,
    Mutation,
    Population,
)
from fim.spec.policy import Analysis, Auto, Replication, Stopping
from fim.spec.run import RunSpec

__all__ = [
    "Analysis",
    "Auto",
    "EquilibriumSplit",
    "Execution",
    "ExplicitFrequencies",
    "FiniteAlleles",
    "FoundingAlleles",
    "InfiniteAlleles",
    "InitialCondition",
    "Locus",
    "Model",
    "Mutation",
    "Population",
    "Recording",
    "Replication",
    "RunSpec",
    "Stopping",
]
