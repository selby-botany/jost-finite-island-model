"""Public deterministic finite-island-model simulation engine.

What this file does, in plain terms: it is the part of the program that
actually *runs* the simulation, generation by generation, and decides
when to stop. Everything else in the `fim` package either sets up the
inputs this file consumes (`fim.model.*` — validated parameters, the
starting population) or does something with the outputs this file
produces (`fim.persistence.*` writes them to disk, `fim.viz.*` plots
them, `fim.cli`/`fim.gui` present them to a person). This module has no
knowledge of files, plots, or user interfaces at all — it only computes.

If you have not already read
[the finite island model introduction](../../doc/finite-island-model-introduction.md),
this is a good place to stop and do that first; it explains, from zero,
what a "finite island model" is and why anyone would want to simulate
one. The short version, just enough to follow the code below: a
population is split into several separate sub-populations, each called
a **deme**, connected by occasional **migration** (some individuals move
from one deme to another each generation). Within each deme, which two
gene copies (**alleles**) a given offspring inherits at each genetic
location (**locus**) is partly a matter of chance — a small deme can
drift, purely by chance, toward one allele becoming common and another
rare or extinct, a process called **genetic drift** — and partly subject
to a small, constant chance of a brand-new mutation appearing
(**mutation**). Migration, mutation, and drift pull in different
directions: migration tends to make demes more genetically similar to
each other (since they keep exchanging alleles), while drift tends to
make them diverge (since each deme's own random luck runs independently
of the others'). The whole point of running this simulation is to watch
how genetically different the demes become over time under some chosen
combination of population size, migration rate, and mutation rate — the
kind of question real population geneticists ask about real,
physically separated populations of plants or animals.

"How different the demes have become" is not a single number; this
project reports several different ways of measuring it side by side —
Jost's D, Nei's G_ST, and others, named throughout this file as short,
capitalized labels (`D`, `G_ST`, `E_ST`, `K_ST`, `H_S`, `H_T`, `H_ST`).
Each measures something related but genuinely different, and they can
disagree about how large "the" differentiation is for the very same
simulated population — which is itself one of the scientific points this
whole project exists to illustrate, following
[Jost et al. (2018)](../../doc/jost-differentiation-measures.md). This
file never explains what any one of those measures actually *means*; it
only ever computes, reports, and averages them (delegating the actual
formulas to `fim.statistics.differentiation`). Read the linked guide for
that.

A single run does not go on forever. Two different, unrelated ideas both
called "stopping" appear in this file, and it is easy to conflate them,
so they are named distinctly throughout:

- **Convergence** (`ConvergenceMonitor`, `TrailingWindowCriterion`): a
  single simulated population's own statistics, tracked generation by
  generation, are watched for having stopped changing meaningfully —
  once they hold roughly steady for a stretch of generations in a row
  (a "trailing window"), the run stops there rather than continuing to
  a fixed, arbitrarily large generation count for no further scientific
  reason. If the statistics never settle down, the run still stops once
  it reaches `SimulationParams.max_generations`, a hard cap — that
  outcome is reported honestly as "did not converge," not disguised as
  success. `_run_one` below is the function that runs one simulated
  population generation by generation until one of these two things
  happens.
- **Replication / the adaptive replicate stop** (`_replicate_monitor`,
  `ConfidenceIntervalCriterion`): because the model is stochastic (its
  own randomness is part of what it simulates — the same setup run
  twice with two different random seeds gives two different, both
  individually valid, outcomes), a single run's own final numbers are
  only one sample from many possible ones. Running the *same*
  configuration many times over with different seeds — each independent
  run called a **replicate** — and looking at the spread across
  replicates is how this project answers "how confident can we be in
  this differentiation estimate," the same way a scientist would not
  trust a single coin flip to tell them whether a coin is fair. By
  default, a requested number of replicates (`SimulationParams.
  n_replicates`) simply all run. Optionally
  (`SimulationParams.stop_batch_early`), the batch instead stops
  *early*, as soon as enough replicates have run to pin down each
  watched statistic's own across-replicate confidence interval (see
  `fim.statistics.interval`) to within a chosen tolerance — running
  further replicates past that point would only narrow an already-
  narrow-enough interval, at the cost of more computing time for no
  real gain in confidence. Replicates are always considered in
  replicate order (1, 2, 3, ...), on every engine backend, never in the
  order they happen to finish: which replicates converge soonest is
  itself correlated with their statistics, so keeping the fastest ones
  would bias the estimate (`run_batch`'s own docstring).

Determinism matters throughout: the same configuration and the same
random seed always produce the exact same generation-by-generation
trajectory, on any machine, every time. That is not a performance detail
— it is what makes a result reproducible and verifiable by someone else,
which for simulated *scientific* results is exactly as important as
being able to show your work for a hand calculation. Every replicate
gets its own seed, deterministically derived from the base seed
(`fim.engine.identity.replicate_seed`), so a batch of replicates is itself fully
reproducible too, not just each individual replicate in isolation.
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
