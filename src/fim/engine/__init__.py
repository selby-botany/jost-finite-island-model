"""Simulation engine for the finite island model.

The engine advances replicate trajectories under migration, mutation, and
genetic drift, computes differentiation statistics, and produces run
reports. Model parameters and initial states come from `fim.model`;
persistence and user interfaces are handled by separate package modules.

A trajectory ends when its convergence criterion is met or its generation
limit is reached. A replicate batch may stop when the requested confidence
interval precision is achieved. Replicates are accepted in index order,
independent of completion order, and their seeds are derived from the base
seed. See `doc/finite-island-model-introduction.md` for model background and
`doc/convergence.md` for stopping criteria.
"""

from __future__ import annotations

from fim.engine.api import (
    simulate_params,
)
from fim.engine.backends import (
    EngineBackend,
    GenerationalBackend,
    build_engine_backend,
)
from fim.engine.batch import (
    run_batch,
)
from fim.engine.concurrency import (
    default_replicate_concurrency,
)
from fim.engine.identity import (
    deterministic_run_id,
)
from fim.engine.lanes import (
    Advancer,
    ReplicaLane,
    SequentialAdvancer,
    ThreadedAdvancer,
    VectorizedAdvancer,
)
from fim.engine.lineal import (
    LinealBackend,
)
from fim.engine.records import (
    Clock,
    EngineBackendChoice,
    FinalReport,
    JitOption,
    RunResult,
    SimulationOutput,
)
from fim.engine.report import (
    report_for_state,
    report_statistic,
)
from fim.engine.summary import (
    bootstrap_replicate_summary,
    pooled_convergence_histories,
    pooled_convergence_histories_from_pairs,
    replicate_summary,
    reports_summary,
)
from fim.engine.tracking import (
    history_free_statistic_values,
    pair_statistic_values,
    tracked_statistic_values,
)

__all__ = [
    "Advancer",
    "Clock",
    "EngineBackend",
    "EngineBackendChoice",
    "FinalReport",
    "GenerationalBackend",
    "JitOption",
    "LinealBackend",
    "ReplicaLane",
    "RunResult",
    "SequentialAdvancer",
    "SimulationOutput",
    "ThreadedAdvancer",
    "VectorizedAdvancer",
    "bootstrap_replicate_summary",
    "build_engine_backend",
    "default_replicate_concurrency",
    "deterministic_run_id",
    "history_free_statistic_values",
    "pair_statistic_values",
    "pooled_convergence_histories",
    "pooled_convergence_histories_from_pairs",
    "replicate_summary",
    "report_for_state",
    "report_statistic",
    "reports_summary",
    "run_batch",
    "simulate_params",
    "tracked_statistic_values",
]
