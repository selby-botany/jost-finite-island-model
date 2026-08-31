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
  (`SimulationParams.replicate_tolerance`), the batch instead stops
  *early*, as soon as enough replicates have run to pin down each
  watched statistic's own across-replicate confidence interval (see
  `fim.statistics.interval`) to within a chosen tolerance — running
  further replicates past that point would only narrow an already-
  narrow-enough interval, at the cost of more computing time for no
  real gain in confidence.

Determinism matters throughout: the same configuration and the same
random seed always produce the exact same generation-by-generation
trajectory, on any machine, every time. That is not a performance detail
— it is what makes a result reproducible and verifiable by someone else,
which for simulated *scientific* results is exactly as important as
being able to show your work for a hand calculation. Every replicate
gets its own seed, deterministically derived from the base seed (`params.
seed + replicate_index`), so a batch of replicates is itself fully
reproducible too, not just each individual replicate in isolation.
"""

from __future__ import annotations

import hashlib
import json
import math
import pickle
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, TypeAlias, TypedDict

import numpy as np

from fim import __version__
from fim.convergence.criteria import (
    ConfidenceIntervalCriterion,
    TrailingWindowCriterion,
)
from fim.convergence.monitor import ConvergenceMonitor
from fim.model.allele import (
    MINTED_ID_START,
    AlleleRegistry,
    FiniteAlleleRegistry,
    FiniteAlleleSpace,
)
from fim.model.initial import generate_initial_state
from fim.model.locus import finite_allele_capacity
from fim.model.operators import step
from fim.model.params import Migration, MutationRate, PopulationSize, SimulationParams
from fim.model.state import ModelState
from fim.persistence.manifest import CURRENT_SCHEMA_VERSION, RunManifest
from fim.persistence.store import InMemoryTrajectoryStore, TrajectoryStore
from fim.statistics.differentiation import DifferentiationReport, statistics_report
from fim.statistics.interval import ConfidenceInterval, confidence_interval

Clock: TypeAlias = Callable[[], datetime]

_MINIMUM_REPLICATE_SUMMARY_COUNT = 2


class FinalReport(TypedDict):
    """Final run-level scalar report.

    The one-paragraph summary of a finished run: how differentiated the
    simulated population had become when the run stopped, plus the
    bookkeeping needed to explain *why* it stopped there. `report_for_
    state` builds one of these from whatever generation a run actually
    ended on (the last generation reached, whether that was because the
    statistics stabilized or because the hard generation cap was hit);
    `RunResult.report` carries it as part of a completed run's full
    result. This is a `TypedDict` (a plain Python dictionary, but one
    whose exact set of keys and each key's value type is declared up
    front) rather than a class with named attributes, because it is
    written to disk as JSON verbatim (see
    `fim.persistence.report.write_report`) — a dictionary already has
    the shape JSON needs, with nothing extra to convert.

    Fields:
        run_id: This run's own identifier — either supplied by the
            caller, or (see `deterministic_run_id`) computed from the
            run's own parameters, so the exact same configuration always
            gets the exact same id.
        generation: The generation number the run actually stopped at —
            not necessarily `SimulationParams.max_generations`; see
            `converged` and `reason` below for why it may have stopped
            earlier or later.
        converged: Whether the run stopped because its watched
            statistic(s) settled down on their own (`True`), or because
            it hit the hard generation cap without ever settling
            (`False`) — see this module's own docstring, above, for
            what "settled down" (convergence) means here.
        converged_on: Which statistic name (or names, for the multi-
            statistic case) the convergence check was actually watching
            for this run — a record of what "settled down" was judged
            against, since a different choice here can legitimately stop
            a run at a different generation for the identical
            trajectory.
        reason: A short, human-readable phrase naming the specific
            reason the run stopped (e.g. "statistic converged" or "hit
            the cap") — meant to be read directly by a person looking at
            a results table, not parsed by code.
        G_ST, D, E_ST, K_ST, H_S, H_T, H_ST: The six differentiation/
            heterozygosity measures this project reports (see this
            module's own docstring for what each name means, in outline,
            and the linked
            [differentiation-measures guide](../../doc/jost-differentiation-measures.md)
            for the full explanation), each averaged across every
            genetic locus the run tracked (see `report_for_state`).
            `G_ST` alone can be `None`: it is undefined at a locus with
            no genetic variation left to measure (see
            `_mean_g_st_across_loci`), and if *every* tracked locus is in
            that state, there is no defined value left to average at
            all. Every other field is always a real number.
    """

    run_id: str
    generation: int
    converged: bool
    converged_on: str | list[str]
    reason: str
    G_ST: float | None
    D: float
    E_ST: float
    K_ST: float
    H_S: float
    H_T: float
    H_ST: float


@dataclass(frozen=True, slots=True)
class RunResult:
    """Everything one finished simulation run produced.

    Whatever calls `fim()` gets back one of these per run (one, or a
    tuple of them for a multi-replicate batch — see `fim`'s own
    docstring). It bundles four different things a caller might want,
    each aimed at a different next step:

    Fields:
        run_id: This run's own identifier — the same value as
            `report["run_id"]`, repeated here directly so a caller never
            has to reach into `report` just to find out which run this
            was.
        params: The exact, fully validated configuration this run
            actually used (population size, migration rate, mutation
            rate, and everything else) — kept alongside the result so
            the run remains self-describing without a caller needing to
            have hung onto the original configuration separately.
        final_state: The complete simulated population as it stood at
            the very last generation — every deme's allele frequencies,
            at every locus. This is what a scatter plot of the final
            state (`fim.viz.scatter`) is drawn from.
        report: The `FinalReport` summarizing that final state into the
            handful of named numbers a person actually wants to read —
            see `FinalReport`'s own docstring.
        convergence_generations: Every generation number at which the
            convergence check actually recorded an observation (see
            `_run_one`) — together with `convergence_history`/
            `convergence_histories` below, this is the run's own
            "trajectory," letting a caller plot how a watched statistic
            moved over time on its way to its final value, not just what
            that final value turned out to be.
        convergence_history: The single watched statistic's own value at
            each of those generations, in the common case of watching
            just one statistic — see `SimulationParams.
            convergence_statistic`.
        convergence_histories: The same per-generation history as
            `convergence_history`, but keyed by statistic name, for the
            less common case of watching several statistics
            simultaneously (design §9) — present either way, so a
            caller does not need to know in advance which of the two
            shapes a given run used.
        manifest: The `RunManifest` recording this run's own bookkeeping
            metadata — when it started and ended, what software version
            produced it, and (once written to disk) the checksums
            proving the persisted files have not been altered since.
            See `fim.persistence.manifest`.
        store: The `TrajectoryStore` this run wrote its per-generation
            state to as it went — kept here so a caller that wants to
            read that trajectory back later (to animate it, or re-
            analyze an earlier generation) already has a handle to it,
            without needing to separately track down which store this
            particular run used.
    """

    run_id: str
    params: SimulationParams
    final_state: ModelState
    report: FinalReport
    convergence_generations: tuple[int, ...]
    convergence_history: tuple[float, ...]
    convergence_histories: Mapping[str, tuple[float, ...]]
    manifest: RunManifest
    store: TrajectoryStore


SimulationOutput: TypeAlias = RunResult | tuple[RunResult, ...]


def fim(
    N: PopulationSize,
    m: Migration,
    mu: MutationRate,
    d: int,
    *,
    params: SimulationParams,
    store: TrajectoryStore | None = None,
    run_id: str | None = None,
    clock: Clock | None = None,
    max_workers: int | None = None,
    store_factory: Callable[[str], TrajectoryStore] | None = None,
) -> SimulationOutput:
    """Run the finite island model until convergence or the hard cap.

    This is the one function everything else in the `fim` package
    (the command line, the desktop app, every test) ultimately calls to
    actually run a simulation. Given a validated configuration
    (`params`), it simulates one population's generations one at a time
    — migration, then mutation, then drift, each generation, following
    `fim.model.operators.step` — until either its watched statistic(s)
    settle down (convergence) or the hard generation cap is reached,
    then returns everything about the finished run. If
    `SimulationParams.n_replicates` is more than one, it does that whole
    thing repeatedly, once per independently seeded replicate, either
    running every requested replicate or (with `SimulationParams.
    replicate_tolerance` set) stopping early once enough replicates have
    run to pin down the answer confidently — see this module's own
    docstring, above, for what "convergence" and "replicate" mean here
    and why both kinds of stopping exist. Everything below this point is
    about the mechanical details of calling this function — which
    arguments exist and why, not what the simulation itself does.

    `N`, `m`, `mu`, and `d` (the four arguments almost every configuration
    is described by — the same shorthand a genetics paper would use) are
    each also already present, in identical form, inside `params`
    itself; both must be given, and must agree, because in practice most
    callers already have a `SimulationParams` object in hand (built once,
    validated once, and passed around everywhere) but a signature built
    entirely out of `params.whatever` is much harder to read at a glance
    than one that names the handful of values that actually matter
    scientifically. `_validate_public_signature` checks the two never
    silently disagree.

    Args:
        N: Gene-copy count, repeated from ``params`` for the public signature.
        m: Migration rate or matrix, repeated from ``params``.
        mu: Mutation probability, repeated from ``params``.
        d: Deme count, repeated from ``params``.
        params: Full validated run configuration and open parameter bag —
            everything about how to run the simulation that is not
            already covered by `N`/`m`/`mu`/`d` above (how many
            generations to allow, when to consider it converged, how
            many replicates to run, and so on). See `fim.model.params.
            SimulationParams`.
        store: Where to write each generation's own simulated state as
            the run proceeds — a `TrajectoryStore` (see `fim.
            persistence.store`); the default writes to memory only
            (nothing touches disk unless a caller supplies a real,
            file-backed store). One store is shared across every
            replicate in a *sequential* batch (each replicate's own rows
            distinguished by its own `run_id`); it must be left `None`
            whenever `max_workers` is set below, since two separate OS
            processes cannot safely write through the very same Python
            object — use `store_factory` instead for that case. Mutually
            exclusive with `store_factory`.
        run_id: This run's own identifier, if the caller wants a
            specific one; left unset, one is computed automatically from
            the run's own parameters (see `deterministic_run_id`) so the
            same configuration always gets the same id.
        clock: Where "now" comes from, for the timestamps recorded in a
            run's own manifest — almost never supplied by a normal
            caller (the real wall clock is the default); tests supply a
            fake one to get a fixed, reproducible timestamp instead of
            "whenever the test happened to run." Under `max_workers`
            (below), this value has to be sent across a process
            boundary to reach each worker, which in Python means it must
            be "picklable" — capable of being serialized to bytes and
            reconstructed on the other side. An ordinary named function
            defined at module level is picklable; a closure or a
            `lambda` is not (Python's own pickling mechanism cannot
            reconstruct one), so only the former is accepted here when
            `max_workers` is set.
        max_workers: Opt-in replicate-batch parallelism, for running
            several replicates' worth of generations at the same time
            instead of one after another. ``None`` (the default) runs
            every replicate strictly one at a time, exactly as every
            prior release of this project did. A number here instead
            runs replicates in batches of up to that many at once, using
            real, separate operating-system processes rather than
            threads — a deliberate choice, since each generation's own
            simulated state is ordinary Python objects (nested
            dictionaries of allele frequencies), and CPython (the
            standard Python interpreter) only ever lets one thread run
            Python code at a time regardless of how many exist (a
            well-known limitation called the Global Interpreter Lock, or
            GIL) — separate *processes*, each with its own interpreter,
            are the only way to get real, simultaneous computation for
            work shaped like this. An adaptive `replicate_tolerance`
            stop (see this module's own docstring, above) is still
            checked strictly in ascending replicate order after each
            whole batch completes, so a batch can overshoot the exact
            minimal replicate count by at most ``max_workers - 1``
            replicates — a small, bounded, and deliberate trade-off for
            the parallelism, not an approximation of the stopping rule
            itself.
        store_factory: A function that builds one fresh trajectory store
            per replicate, given that replicate's own `run_id` — used
            instead of one shared `store` whenever every replicate needs
            its own independent place to write (any batch of more than
            one replicate can use this; it is required, not just
            useful, once `max_workers` is set, since a worker process
            cannot share the parent process's own `store` object at
            all). Whenever `max_workers` is set, this function must
            itself be picklable in the same sense `clock` above is — an
            ordinary module-level function, or `functools.partial` built
            from one, never a closure or a `lambda`. Left unset
            (``None``, the default), every replicate falls back to
            sharing whatever `store` was given (or a private, in-memory
            store if `store` was not given either).

    Returns:
        One result, or one independently seeded result per replicate.
        With `SimulationParams.replicate_tolerance` unset (the default),
        exactly `n_replicates` replicates run, exactly as in every prior
        release. With it set, replicates stop accumulating as soon as
        every watched statistic's across-replicate confidence interval
        tightens to at most `replicate_tolerance` (see
        `replicate_summary`), or `n_replicates` is reached, whichever
        comes first — so the returned tuple can be shorter than
        `n_replicates`.

    Raises:
        ValueError: If the named arguments disagree with ``params``,
            `store` and `store_factory` are both given, or `max_workers`
            is combined with a non-``None`` `store`.
    """
    _validate_public_signature(N, m, mu, d, params)
    if store is not None and store_factory is not None:
        raise ValueError("store and store_factory are mutually exclusive")
    run_clock = clock if clock is not None else _utc_now
    # From here down, this function is a three-way dispatch, in order of
    # increasing complexity: a single run (`n_replicates == 1`, the
    # common case) goes straight to `_run_one`; a multi-replicate batch
    # with `max_workers` set hands off to the parallel-worker-process
    # path (`_run_batch_parallel`); everything else is a multi-replicate
    # batch run the plain way, one replicate after another, in the loop
    # at the bottom of this function.
    if params.n_replicates == 1:
        trajectory_store = store if store is not None else InMemoryTrajectoryStore()
        return _run_one(
            params,
            trajectory_store,
            run_id or deterministic_run_id(params),
            run_clock,
        )

    monitor = _replicate_monitor(params)
    if max_workers is not None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        if store is not None:
            raise ValueError(
                "max_workers requires store=None; a single store instance "
                "cannot be shared across worker processes — pass "
                "store_factory instead"
            )
        _require_picklable("clock", run_clock)
        if store_factory is not None:
            _require_picklable("store_factory", store_factory)
        return _run_batch_parallel(
            params,
            max_workers,
            store_factory,
            run_id,
            run_clock,
            monitor,
        )

    trajectory_store = store if store is not None else InMemoryTrajectoryStore()
    # Replicate i is an independent scalar run with seed + i, preserving the
    # scalar trajectory for the first result and deterministic batch ordering.
    results: list[RunResult] = []
    for replicate_index in range(params.n_replicates):
        replicate_params = replace(
            params,
            seed=params.seed + replicate_index,
            n_replicates=1,
        )
        replicate_run_id = (
            f"{run_id}-r{replicate_index + 1:03}"
            if run_id is not None
            else deterministic_run_id(replicate_params)
        )
        # A supplied `store_factory` gives every replicate its own fresh
        # store, exactly like the parallel path's workers; otherwise every
        # replicate reuses the one shared `trajectory_store`, unchanged
        # from every prior release.
        replicate_store = (
            store_factory(replicate_run_id)
            if store_factory is not None
            else trajectory_store
        )
        result = _run_one(
            replicate_params,
            replicate_store,
            replicate_run_id,
            run_clock,
        )
        results.append(result)
        if monitor is not None:
            outcome = monitor.record(
                replicate_index + 1,
                _replicate_stopping_values(result, params.convergence_statistics),
            )
            if outcome.stopped:
                break
    return tuple(results)


def deterministic_run_id(params: SimulationParams) -> str:
    """Return a stable run ID derived only from replayable parameters.

    Used whenever a run is started without a caller-supplied `run_id`
    (see `fim`'s own docstring above). The id is not random — it is a
    short hash of the run's own configuration, written out in a
    fixed, unambiguous order (`sort_keys=True`) so the exact same
    configuration always produces the exact same id, on any machine,
    every time. That means two people who independently run the
    identical configuration end up with matching run ids without ever
    having to coordinate — a convenient side effect of this project's
    own emphasis on determinism (see this module's own docstring, above)
    rather than something engineered for its own sake.
    """
    canonical = json.dumps(
        params.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"run-{hashlib.sha256(canonical).hexdigest()[:16]}"


def report_for_state(
    state: ModelState,
    params: SimulationParams,
    *,
    run_id: str,
    converged: bool,
    reason: str,
) -> FinalReport:
    """Compute the final report independently of the run loop.

    This is the function that actually builds a `FinalReport` (see that
    class's own docstring for what each field in the result means) from
    a simulated population state — reading off its allele frequencies at
    every locus, computing every named differentiation statistic at each
    one (`_statistics_for_locus`), and averaging each statistic across
    loci into the single reported number. `_run_one` calls this once, at
    the very end of a run, to build that run's own final report; the
    GUI also calls it directly to describe the *starting* population
    (generation zero, before any migration/mutation/drift has happened
    yet) for its live preview — hence "independently of the run loop" in
    this function's own name: it only needs a state and the parameters
    that produced it, not a whole run in progress.

    Args:
        state: The population state to summarize.
        params: The run's own parameters — read here only for which
            statistic(s) were being watched (`converged_on`, in the
            result) and how demes should be weighted when averaging
            (`SimulationParams.deme_weighting`; see
            `_statistics_for_locus`).
        run_id: This run's own identifier, copied into the report
            verbatim so the report is self-describing on its own,
            without needing to be paired with anything else to know
            which run it came from.
        converged: Whether this state was reached because the watched
            statistic(s) settled down on their own, or because a run
            simply hit its generation cap — see `FinalReport.converged`.
        reason: The short, human-readable phrase explaining why the run
            stopped where it did — see `FinalReport.reason`.

    Returns:
        A `FinalReport`: the run's own bookkeeping (id, generation,
        whether/why it stopped where it did) plus every named
        differentiation/heterozygosity statistic, each already averaged
        across every genetic locus the run tracked.
    """
    locus_reports = tuple(
        _statistics_for_locus(state, params, locus_index)
        for locus_index in range(state.locus_count)
    )
    return {
        "run_id": run_id,
        "generation": state.generation,
        "converged": converged,
        "converged_on": (
            params.convergence_statistic
            if isinstance(params.convergence_statistic, str)
            else list(params.convergence_statistic)
        ),
        "reason": reason,
        "G_ST": _mean_g_st_across_loci(locus_reports),
        "D": _mean(tuple(report["D"] for report in locus_reports)),
        "E_ST": _mean(tuple(report["E_ST"] for report in locus_reports)),
        "K_ST": _mean(tuple(report["K_ST"] for report in locus_reports)),
        "H_S": _mean(tuple(report["H_S"] for report in locus_reports)),
        "H_T": _mean(tuple(report["H_T"] for report in locus_reports)),
        "H_ST": _mean(tuple(report["H_ST"] for report in locus_reports)),
    }


def reports_summary(
    reports: Sequence[FinalReport],
    *,
    confidence: float = 0.95,
) -> dict[str, ConfidenceInterval]:
    """Return each named statistic's across-report confidence interval.

    A **confidence interval** is a range around a sample's own average
    that expresses how much uncertainty is left after averaging only a
    finite number of independent draws — the same idea as reporting a
    poll result as "52% ± 3%" rather than a single bare number, where
    the "± 3%" is exactly this kind of interval. Here, each element of
    `reports` is one independent replicate's own final value for a given
    statistic (see this module's own docstring, above, for what a
    "replicate" is and why running several of them matters); this
    function averages each statistic across every replicate that defines
    it and reports how much that average could plausibly still be off,
    given only this many replicates. `confidence=0.95` (the default)
    means: if this same batch of replicates were run over and over, the
    true underlying average would fall inside the reported interval
    about 95% of the time — the conventional choice in most sciences,
    not a value with any special significance to this project.

    This is the shared math behind `replicate_summary` (a completed
    batch's own final reports), extracted so the GUI's live batch
    progress panel (`fim.gui.app._push_batch_progress`) can summarize
    each currently-reporting replicate's *live*, in-progress report the
    same way — the math does not care whether a report is final or
    mid-run.

    Unlike `replicate_summary`, this never raises for fewer than two
    reports: a live tick's own reporting-replicate count is expected to
    start below two and grow as the batch proceeds, not something to
    treat as invalid input. Zero or one report simply summarizes to
    nothing yet (an empty dict), the same "omitted, not blank" contract
    `replicate_summary`'s own per-statistic dropping already establishes
    for a monomorphic ``G_ST``, extended here to the "not enough reports
    at all" case too.

    Args:
        reports: Zero or more independently seeded reports.
        confidence: Two-tailed confidence level; see
            `fim.statistics.interval.confidence_interval`.

    Returns:
        One `ConfidenceInterval` per statistic name in `FinalReport`
        (``D``, ``G_ST``, ``E_ST``, ``K_ST``, ``H_S``, ``H_T``, ``H_ST``)
        with at least two defined values across `reports`; a statistic
        short of that (including every statistic, given fewer than two
        reports overall) is omitted entirely.
    """
    summary: dict[str, ConfidenceInterval] = {}
    for statistic in ("D", "G_ST", "E_ST", "K_ST", "H_S", "H_T", "H_ST"):
        values = [
            value
            for report in reports
            if (value := _final_report_statistic(report, statistic)) is not None
        ]
        if len(values) < _MINIMUM_REPLICATE_SUMMARY_COUNT:
            continue
        summary[statistic] = confidence_interval(values, confidence=confidence)
    return summary


def replicate_summary(
    results: Sequence[RunResult],
    *,
    confidence: float = 0.95,
) -> dict[str, ConfidenceInterval]:
    """Return each reported statistic's across-replicate confidence interval.

    See `reports_summary`'s own docstring, just above, for what a
    confidence interval is and why it matters here; this function is the
    version of that same idea meant for a *completed* batch's own final
    results, rather than a batch still in progress.

    Each replicate is an independent draw of its own final ``D``, ``G_ST``,
    and so on; this is a closed-form Student's-t interval on the sample
    mean of those draws (`fim.statistics.interval.confidence_interval`) —
    a standard, textbook statistical method for exactly this situation
    (a small number of independent measurements, whose own true
    variability is not known in advance and must be estimated from the
    measurements themselves), computed directly from a formula rather
    than by any kind of simulation or repeated resampling — the
    replicates are already independent by construction (each one used
    its own distinct random seed; see this module's own docstring,
    above), so nothing further is needed to treat them as a valid
    statistical sample. A thin wrapper over `reports_summary`'s own
    shared math, adding only
    the "fewer than two results at all is invalid input" check a
    completed batch's own results warrant (unlike a live, still-growing
    tick's reporting-replicate count — see that function's own
    docstring for why it does not raise this same case itself).

    Args:
        results: Two or more independently seeded replicate results, as
            returned by `fim` when `SimulationParams.n_replicates` is
            greater than one.
        confidence: Two-tailed confidence level; see
            `fim.statistics.interval.confidence_interval`.

    Returns:
        One `ConfidenceInterval` per statistic name in `FinalReport`
        (``D``, ``G_ST``, ``E_ST``, ``K_ST``, ``H_S``, ``H_T``, ``H_ST``).
        ``G_ST`` is undefined for a replicate whose locus is monomorphic
        across every deme (``H_T == 0``); such replicates are dropped
        from ``G_ST``'s own sample rather than papered over with a
        substitute value, so its `ConfidenceInterval.sample_count` can be
        smaller than the other statistics' — and a statistic left with
        fewer than two defined replicates is omitted entirely rather
        than raising, since a single point has no interval.

    Raises:
        ValueError: If fewer than two results are supplied.
    """
    if len(results) < _MINIMUM_REPLICATE_SUMMARY_COUNT:
        raise ValueError("replicate_summary requires at least two results")
    return reports_summary([result.report for result in results], confidence=confidence)


def _build_finite_allele_spaces(
    state: ModelState,
    params: SimulationParams,
) -> dict[int, FiniteAlleleSpace]:
    """Construct one finite-allele state space per locus, seeded from generation zero.

    By default, a new mutation always creates a brand-new allele
    identity that has never existed before in the run (the "infinite
    alleles" model — a standard simplifying assumption in population
    genetics, reasonable when mutations are rare enough that the same
    exact mutation recurring twice by chance is negligible). This
    project also supports an opt-in alternative, the "finite alleles" (or
    "K-allele") model (`SimulationParams.mutation_model ==
    "finite_alleles"`, design §9): at each locus, only a fixed, finite
    set of allele identities can ever exist — every distinct DNA
    sequence of that locus's own length is one possible allele, so a
    locus `n` bases long admits `4**n` of them, one possibility per base
    for each of the four DNA letters (see `finite_allele_capacity`). A
    mutation instead picks uniformly among whichever of that fixed set
    is not the current allele, which lets the *same* allele recur
    independently at different points in the run, closer to how a real,
    physically limited genetic sequence actually behaves. This function
    builds the
    bookkeeping (`FiniteAlleleSpace`, one per locus) that tracks and
    enforces that fixed set for a single run, seeded from whichever
    alleles are already present in the population at generation zero.

    Args:
        state: The run's generated generation-zero state.
        params: Validated run parameters.

    Returns:
        One `FiniteAlleleSpace` per locus, keyed by `LocusSpec.locus_id`.
    """
    return {
        locus.locus_id: FiniteAlleleSpace(
            finite_allele_capacity(locus.length),
            (
                allele_id
                for deme in state.frequencies
                for allele_id in deme[locus_index]
            ),
        )
        for locus_index, locus in enumerate(params.loci)
    }


def _require_picklable(name: str, value: object) -> None:
    """Reject an unpicklable `max_workers` argument before any worker spawns.

    "Pickling" is Python's own built-in way of converting an object into
    a stream of bytes that can be sent somewhere else and turned back
    into an equivalent object later — the mechanism `ProcessPoolExecutor`
    uses under the hood to hand `clock` and `store_factory` (see `fim`'s
    own docstring) across the boundary from this process into each
    separate worker process it starts. Most ordinary Python values (and
    ordinary, named functions) can be pickled without any trouble; a
    closure (a function defined inside another function, capturing some
    of that outer function's own local state) or a `lambda` generally
    cannot, since there is no way to describe "the specific place in the
    code where this was defined, plus whatever local variables it
    captured" as a self-contained stream of bytes. Without this check,
    handing in an unpicklable value would fail anyway — but only later,
    silently, deep inside worker-process spawn machinery, surfacing as
    raw pickling error noise with no indication of which of `fim`'s own
    arguments was actually at fault. Checking here instead fails
    immediately at the call site, before a single worker process is
    even started, naming the exact offending argument by name.

    Args:
        name: The public keyword argument's name, for the error message.
        value: The callable to verify.

    Raises:
        ValueError: If `value` cannot be pickled.
    """
    try:
        pickle.dumps(value)
    except (AttributeError, pickle.PicklingError, TypeError) as error:
        raise ValueError(
            f"{name} must be picklable to cross a max_workers process "
            "boundary — a module-level function, or functools.partial "
            f"over one, never a closure or lambda: {error}"
        ) from error


def _run_batch_parallel(
    params: SimulationParams,
    max_workers: int,
    store_factory: Callable[[str], TrajectoryStore] | None,
    run_id: str | None,
    clock: Clock,
    monitor: ConvergenceMonitor | None,
) -> tuple[RunResult, ...]:
    """Run replicates in parallel worker-process batches of `max_workers`.

    This is `fim`'s own `max_workers` path — see that function's
    docstring for why real, separate operating-system processes are used
    at all here rather than something lighter-weight. This function's
    own job is just the bookkeeping around that: hand out replicates
    `max_workers` at a time to a pool of worker processes
    (`ProcessPoolExecutor`, from Python's own standard library), collect
    each one's result as it finishes, and — if an adaptive replicate
    stop is configured (`monitor`; see this module's own docstring,
    above) — decide after each whole batch of workers finishes whether
    enough replicates have now run to stop early.

    Replicates run in these fixed-size batches, rather than all being
    submitted to the worker pool at once, specifically so that adaptive
    stopping decision stays meaningful: the decision is always applied
    strictly in ascending replicate order (replicate 1's result seen
    before replicate 2's, and so on), the exact same order the
    sequential (non-parallel) loop already uses, so parallelizing the
    *computation* never reorders which replicate's own values actually
    fed the stopping decision. The one real trade-off for that
    guarantee: a batch can overshoot the exact minimal replicate count
    by up to ``max_workers - 1`` extra replicates, since the whole batch
    a stopping replicate happened to fall in has already been started by
    the time the decision is made.
    """
    results: list[RunResult] = []
    replicate_index = 0
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        while replicate_index < params.n_replicates:
            batch_end = min(replicate_index + max_workers, params.n_replicates)
            futures = {}
            for index in range(replicate_index, batch_end):
                replicate_params = replace(
                    params,
                    seed=params.seed + index,
                    n_replicates=1,
                )
                replicate_run_id = (
                    f"{run_id}-r{index + 1:03}"
                    if run_id is not None
                    else deterministic_run_id(replicate_params)
                )
                futures[index] = executor.submit(
                    _run_replicate_worker,
                    replicate_params,
                    replicate_run_id,
                    store_factory,
                    clock,
                )
            for index in range(replicate_index, batch_end):
                result = futures[index].result()
                results.append(result)
                if monitor is not None:
                    outcome = monitor.record(
                        index + 1,
                        _replicate_stopping_values(
                            result, params.convergence_statistics
                        ),
                    )
                    if outcome.stopped:
                        return tuple(results)
            replicate_index = batch_end
    return tuple(results)


def _run_replicate_worker(
    params: SimulationParams,
    run_id: str,
    store_factory: Callable[[str], TrajectoryStore] | None,
    clock: Clock,
) -> RunResult:
    """Run one replicate inside a worker process.

    This is the actual function each `ProcessPoolExecutor` worker
    process runs, submitted once per replicate by `_run_batch_parallel`
    above. It has to be defined here, at module level, rather than as a
    closure inside `_run_batch_parallel` itself, for the same "must be
    picklable to cross a process boundary" reason explained in
    `_require_picklable`'s own docstring — `ProcessPoolExecutor` sends
    this very function, by reference, to each worker process it starts,
    and only a plain, named, module-level function can be sent that way.
    """
    store = (
        store_factory(run_id)
        if store_factory is not None
        else InMemoryTrajectoryStore()
    )
    return _run_one(params, store, run_id, clock)


def _run_one(
    params: SimulationParams,
    store: TrajectoryStore,
    run_id: str,
    clock: Clock,
) -> RunResult:
    """Execute one scalar replicate.

    This is the actual generation-by-generation simulation loop this
    whole module exists to run — everything above this function in the
    file (`fim`, the batch-parallel machinery) exists only to call this,
    once per replicate. In outline, it:

    1. Builds a starting population (generation zero) from `params`
       (`generate_initial_state`) and a fresh, independent random-number
       stream seeded from `params.seed` — the single source of all this
       replicate's own randomness, so the same seed always reproduces
       the exact same sequence of migration/mutation/drift outcomes.
    2. Sets up the bookkeeping every subsequent generation will need:
       an `AlleleRegistry` to hand out a fresh, never-before-used
       identity to each new mutation under the default "infinite
       alleles" model (see `_build_finite_allele_spaces`'s own docstring
       for the alternative "finite alleles" model), and a
       `ConvergenceMonitor` to watch whichever statistic(s)
       `params` says to watch for the run settling down (see this
       module's own docstring, above, for what "convergence" means
       here).
    3. Repeats, once per generation: advance the population by one
       generation (`fim.model.operators.step` — migration, then
       mutation, then drift), persist that generation's own state to
       `store` (so it can be replayed, plotted, or animated later), and
       feed that generation's statistics to the convergence monitor —
       until the monitor says to stop, either because the watched
       statistic(s) have settled down or because `params.
       max_generations` (the hard cap) has been reached.
    4. Summarizes the final generation into a `FinalReport`
       (`report_for_state`) and packages everything — final state,
       report, the full per-generation history of the watched
       statistic(s), and a `RunManifest` recording when this run
       started and ended — into the `RunResult` this function returns.

    Called directly by `fim()` for the ordinary, sequential case, and
    (wrapped by `_run_replicate_worker`) once per replicate inside each
    `ProcessPoolExecutor` worker process for the parallel-batch case —
    this function itself has no idea which of the two is calling it, and
    does not need to.
    """
    started_at = _format_timestamp(clock())
    # `PCG64` is the specific, high-quality pseudo-random-number
    # algorithm NumPy recommends as its modern default; seeding it here,
    # once, from `params.seed`, is what makes every downstream random
    # choice in this replicate (the initial population, every
    # generation's own migration/mutation/drift outcomes) fully
    # reproducible from that one seed alone.
    rng = np.random.Generator(np.random.PCG64(params.seed))
    monitor = ConvergenceMonitor(
        TrailingWindowCriterion(
            params.convergence_window,
            params.convergence_tolerance,
        ),
        max_generations=params.max_generations,
        statistics=params.convergence_statistics,
        combinator=params.convergence_combinator,
    )

    # Generation zero: the starting population before any migration,
    # mutation, or drift has happened. Every allele already present here
    # was assigned an id by `generate_initial_state`, independently of
    # the `AlleleRegistry` below (which only hands out ids for *new*
    # mutations from generation one onward) — so the registry's own
    # numbering has to start strictly after the highest id already in
    # use, or a freshly minted mutation could collide with (and be
    # mistaken for) an allele that was already present at the start.
    state = generate_initial_state(params, rng)
    highest_initial_id = max(
        (
            int(allele_id)
            for deme in state.frequencies
            for locus in deme
            for allele_id in locus
        ),
        default=MINTED_ID_START - 1,
    )
    registry = AlleleRegistry(start=max(MINTED_ID_START, highest_initial_id + 1))
    # `finite_alleles` stays `None` under the default "infinite alleles"
    # mutation model (every new mutation gets a brand-new id from
    # `registry` above, with no fixed ceiling on how many distinct
    # alleles can ever exist) — `fim.model.operators.step` reads this
    # `None` as "no finite-alleles bookkeeping in effect" and falls back
    # to that default behavior on its own.
    finite_alleles = (
        FiniteAlleleRegistry(_build_finite_allele_spaces(state, params))
        if params.mutation_model == "finite_alleles"
        else None
    )
    # Generation zero is itself persisted and fed to the convergence
    # monitor before the loop below ever runs a single generation, so a
    # run that (unusually) already satisfies its own convergence
    # criterion at the very start still gets a correctly recorded
    # generation-zero observation, and a later replay of the persisted
    # trajectory always has a real starting frame to show, not a gap.
    store.write_generation(run_id, state.generation, state.to_rows(run_id))
    monitor.record(
        state.generation,
        _convergence_values(state, params),
    )
    # The main loop: advance one generation, persist it, check whether
    # the population's statistics have settled down or the hard
    # generation cap has been reached — `monitor.should_stop()` is the
    # single place either of those two conditions is actually decided.
    while not monitor.should_stop():
        state = step(state, params, registry, rng, finite_alleles=finite_alleles)
        store.write_generation(run_id, state.generation, state.to_rows(run_id))
        monitor.record(
            state.generation,
            _convergence_values(state, params),
        )

    outcome = monitor.outcome()
    if outcome.reason is None:
        # Unreachable in practice: `monitor.should_stop()` returning
        # `True` (the only way to exit the `while` loop above) is
        # `ConvergenceMonitor`'s own guarantee that a stop reason has
        # already been recorded. Guarded explicitly anyway, rather than
        # assumed, so a future bug in that guarantee surfaces here, at
        # the one place a missing reason would otherwise silently
        # propagate into a malformed `FinalReport`, instead of failing
        # confusingly somewhere far downstream.
        raise RuntimeError("stopped convergence monitor has no reason")
    report = report_for_state(
        state,
        params,
        run_id=run_id,
        converged=outcome.converged,
        reason=outcome.reason.value,
    )
    ended_at = _format_timestamp(clock())
    # The manifest is this run's own permanent record — not the
    # scientific results themselves (that is `report`, above), but the
    # surrounding bookkeeping proving *when* and *how* those results
    # were produced: which exact software version ran, when it started
    # and finished. Built here with `artifacts` left at its own default
    # of `None` (see `RunManifest`'s own docstring) — this function
    # never writes anything to disk itself, so it has no on-disk file
    # digests to report yet; a caller that does persist this run's own
    # files (`fim.cli._write_run_artifacts`) fills those in afterward,
    # once every file is completely written, so a later reader can
    # confirm none of them were altered since.
    manifest = RunManifest(
        schema_version=CURRENT_SCHEMA_VERSION,
        run_id=run_id,
        parameters=params.to_dict(),
        started_at=started_at,
        ended_at=ended_at,
        converged=outcome.converged,
        convergence_statistic=params.convergence_statistic,
        stop_reason=outcome.reason.value,
        generation=state.generation,
        generation_count=len(monitor.generations),
        software_version=__version__,
    )
    return RunResult(
        run_id=run_id,
        params=params,
        final_state=state,
        report=report,
        convergence_generations=monitor.generations,
        convergence_history=monitor.history,
        convergence_histories=monitor.histories,
        manifest=manifest,
        store=store,
    )


def _convergence_values(
    state: ModelState,
    params: SimulationParams,
) -> dict[str, float]:
    """Return every watched, currently-defined statistic's value, averaged across loci.

    Called once per generation by `_run_one`'s own main loop, to feed
    the convergence monitor whatever it needs to judge "has this
    settled down yet" (see this module's own docstring, above, for what
    convergence means here). `params.convergence_statistics` names which
    statistic(s) to actually watch — usually just one (`D`, most
    commonly), but this project also supports watching several at once
    and requiring either all of them, or any one of them, to settle
    before calling the run converged (`SimulationParams.
    convergence_combinator`, design §9).

    Computes each locus's full differentiation report exactly once and reads
    every watched statistic from that same cached set of reports, rather than
    recomputing per-locus statistics once per watched name — the several-
    statistic case costs one extra dictionary lookup per statistic per
    locus this way, not another whole pass over the state.

    A watched statistic that is undefined this generation (only ``G_ST``
    can be, and only when every tracked locus is currently
    "monomorphic" — has only a single allele left, with no genetic
    variation remaining to measure at all; see `_mean_g_st_across_loci`)
    is omitted from the returned mapping rather than represented by a
    substitute value; `ConvergenceMonitor` treats an omitted statistic as
    simply not advancing its own trailing window this generation, which
    is honest about there being no observation to add, rather than
    fabricating one.
    """
    locus_reports = tuple(
        _statistics_for_locus(state, params, locus_index)
        for locus_index in range(state.locus_count)
    )
    values: dict[str, float] = {}
    for statistic in params.convergence_statistics:
        value = _mean_statistic_across_loci(locus_reports, statistic)
        if value is not None:
            values[statistic] = value
    return values


def _replicate_monitor(params: SimulationParams) -> ConvergenceMonitor | None:
    """Return the adaptive replicate-batch monitor, or ``None`` when unused.

    This is the *other* kind of stopping this module implements — see
    this module's own docstring, above, for why "convergence" (one
    population's own generations settling down) and "the adaptive
    replicate stop" (a whole batch of independent replicates deciding
    it has run enough of them) are two different, unrelated ideas,
    despite both being built out of the same underlying `ConvergenceMonitor`
    machinery here (a `ConfidenceIntervalCriterion` in place of the
    `TrailingWindowCriterion` `_run_one` uses for the first kind — the
    same monitor class, watching a different kind of stability).

    Returns ``None`` whenever `SimulationParams.replicate_tolerance` is
    left unset — the opt-in sentinel meaning "always run exactly
    `n_replicates` replicates, never stop early," which is also what
    every prior release of this project did unconditionally, before the
    adaptive stop existed at all; `None` here is what keeps that
    original, simpler behavior exactly unchanged for anyone who has not
    opted into the newer feature.
    """
    if params.replicate_tolerance is None:
        return None
    criterion = ConfidenceIntervalCriterion(
        minimum_count=params.replicate_minimum,
        tolerance=params.replicate_tolerance,
        confidence=params.replicate_confidence,
    )
    return ConvergenceMonitor(
        criterion,
        max_generations=params.n_replicates,
        statistics=params.convergence_statistics,
        combinator=params.convergence_combinator,
    )


def _replicate_stopping_values(
    result: RunResult,
    statistics: Sequence[str],
) -> dict[str, float]:
    """Return each watched statistic's value for this replicate, for the batch monitor.

    Feeds `_replicate_monitor`'s own monitor exactly the way
    `_convergence_values` feeds `_run_one`'s within-run monitor — the
    same "one observation per unit being watched" pattern, just applied
    to a whole finished replicate's own final report here instead of one
    generation's own current state.

    A statistic this replicate leaves undefined (only ``G_ST``, and only
    when every one of this replicate's tracked loci was monomorphic — see
    `_mean_g_st_across_loci`) is omitted rather than raised or
    substituted: this replicate simply contributes nothing to that
    statistic's own stopping-criterion window this round, exactly as
    `ConvergenceMonitor.record` already treats any statistic missing from
    its ``value`` mapping. This is deliberately the same rule
    `replicate_summary` uses for the *published* interval — drop an
    undefined replicate from that statistic's own sample rather than
    fabricate a value — so the stopping decision is judged against the
    same sample the summary will report, never a different one.
    """
    values: dict[str, float] = {}
    for statistic in statistics:
        value = _final_report_statistic(result.report, statistic)
        if value is not None:
            values[statistic] = value
    return values


def _mean_statistic_across_loci(
    locus_reports: Sequence[DifferentiationReport],
    statistic: str,
) -> float | None:
    """Return one statistic's per-locus reports averaged into a single value.

    Only ``G_ST`` can be undefined at a locus — a monomorphic locus's
    total heterozygosity is zero, leaving nothing to differentiate; every
    other field in `DifferentiationReport` is always defined for valid
    input. ``G_ST`` delegates to `_mean_g_st_across_loci`, which drops any
    undefined locus from the average and returns ``None`` only when every
    locus is undefined — exactly the rule `report_for_state` uses for the
    final report, so the within-run convergence watcher's notion of
    "G_ST this generation" never disagrees with what the report will say.
    """
    if statistic == "G_ST":
        return _mean_g_st_across_loci(locus_reports)
    values: list[float] = []
    for report in locus_reports:
        value = _report_statistic(report, statistic)
        if value is None:
            # Unreachable given DifferentiationReport's own contract
            # (every field but G_ST is always defined); guarded rather
            # than asserted so a future statistic that can legitimately
            # go undefined is forced to update this function, not
            # silently mis-averaged.
            raise ValueError(f"{statistic} is unexpectedly undefined")
        values.append(value)
    return _mean(tuple(values))


def _final_report_statistic(report: FinalReport, statistic: str) -> float | None:
    """Read one named field from a final run report.

    A small, explicit lookup table rather than `report[statistic]`
    directly: `statistic` is an arbitrary string (ultimately from user-
    supplied configuration, by way of `SimulationParams.
    convergence_statistics`), and indexing a `TypedDict` with a name
    that turns out not to be one of its declared fields would raise
    Python's own generic, unhelpful `KeyError` rather than the specific,
    named-statistic error this function raises instead.
    """
    fields: Mapping[str, float | None] = {
        "D": report["D"],
        "G_ST": report["G_ST"],
        "E_ST": report["E_ST"],
        "K_ST": report["K_ST"],
        "H_S": report["H_S"],
        "H_T": report["H_T"],
        "H_ST": report["H_ST"],
    }
    if statistic not in fields:
        raise ValueError(f"unsupported statistic: {statistic}")
    return fields[statistic]


def _format_timestamp(value: datetime) -> str:
    """Return an unambiguous UTC ISO-8601 timestamp.

    Used for the `started_at`/`ended_at` fields `_run_one` records in a
    run's own manifest. ISO-8601 (`2026-08-26T12:00:00Z`) is a widely
    used, sortable, unambiguous way to write a specific moment in time,
    including its timezone — writing every timestamp in UTC (Coordinated
    Universal Time, the "Z" suffix) specifically means a run started on
    one machine, in one timezone, is directly comparable to a run
    started on a different machine in a different timezone, with no
    conversion needed and no risk of the two being silently
    misinterpreted as if they shared a timezone when they do not.
    """
    if value.tzinfo is None:
        raise ValueError("manifest clock must return a timezone-aware datetime")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _mean(values: Sequence[float]) -> float:
    """Return an accurate mean of a nonempty float sequence.

    Uses `math.fsum` rather than Python's plain `sum`, then divides:
    ordinary floating-point addition accumulates small rounding errors
    as more numbers are added together, and `fsum` specifically avoids
    that by tracking the arithmetic more carefully internally — for the
    typically small handful of values averaged in this module (loci, or
    replicates), the difference is negligible either way, but there is
    no real cost to always using the more careful version.
    """
    if not values:
        raise ValueError("cannot average no values")
    return math.fsum(values) / len(values)


def _mean_g_st_across_loci(
    locus_reports: Sequence[DifferentiationReport],
) -> float | None:
    """Return ``G_ST`` averaged across loci, dropping any locus where it is undefined.

    ``g_st`` is undefined at a locus whose total heterozygosity (``H_T``)
    is zero — a fully monomorphic locus has nothing to differentiate. Such
    a locus contributes nothing to the average, rather than a fabricated
    ``0.0`` (which would understate real differentiation at the other,
    genuinely polymorphic, loci) or voiding the whole multi-locus average
    over a single monomorphic locus among several. Returns ``None`` only
    when *every* locus is undefined, since there is then no defined value
    left to average — the honest reading of "this run/replicate has no
    G_ST", matching the rule `fim.engine.replicate_summary` already
    applies when building the published cross-replicate sample.
    """
    defined = [report["G_ST"] for report in locus_reports if report["G_ST"] is not None]
    if not defined:
        return None
    return _mean(tuple(defined))


def _statistics_for_locus(
    state: ModelState,
    params: SimulationParams,
    locus_index: int,
) -> DifferentiationReport:
    """Compute one locus's scalar statistics.

    Builds the input `fim.statistics.differentiation.statistics_report`
    actually expects — one dictionary per deme, mapping each allele
    identity present at this locus to its frequency (what fraction of
    that deme's gene copies at this locus currently carry it) — from
    this state's own internal representation, then hands that off to do
    the actual math. This function itself computes nothing statistical;
    it only reshapes data.

    `deme_weighting` controls whether every deme counts equally toward
    the reported statistics regardless of its own population size
    (`params.deme_weighting == "equal"`), or whether a larger deme
    counts proportionally more than a smaller one (`"size"`, the
    default, weighting by `params.population_sizes`) — the same
    "should a bigger group's own numbers count for more" choice that
    comes up whenever averaging across groups of unequal size, with no
    universally correct answer; which is more appropriate depends on the
    actual scientific question being asked.
    """
    table: list[Mapping[Any, Any]] = [
        {
            int(allele_id): frequency
            for allele_id, frequency in state.frequency_map(
                deme_index,
                locus_index,
            ).items()
        }
        for deme_index in range(state.deme_count)
    ]
    weights: Sequence[float] | None = (
        params.population_sizes if params.deme_weighting == "size" else None
    )
    return statistics_report(table, weights)


def _report_statistic(
    report: DifferentiationReport,
    statistic: str,
) -> float | None:
    """Read one validated convergence-statistic field.

    The `_convergence_values`/`_mean_statistic_across_loci` counterpart
    to `_final_report_statistic` above, reading from one locus's own
    live `DifferentiationReport` (computed this generation) rather than
    a whole run's already-finished `FinalReport` — otherwise the exact
    same "look up one named statistic safely" job, for the same reason:
    a plain `report[statistic]` would raise Python's own generic
    `KeyError` for an unrecognized name, rather than the specific,
    named-statistic error this function raises instead.
    """
    if statistic == "D":
        return report["D"]
    if statistic == "G_ST":
        return report["G_ST"]
    if statistic == "E_ST":
        return report["E_ST"]
    if statistic == "K_ST":
        return report["K_ST"]
    if statistic == "H_S":
        return report["H_S"]
    if statistic == "H_T":
        return report["H_T"]
    raise ValueError(f"unsupported convergence statistic: {statistic}")


def _utc_now() -> datetime:
    """Return the current UTC time for manifest metadata only.

    `fim`'s own default for its `clock` argument (see that function's
    docstring for why a caller — almost always just a test — would ever
    supply a different one). Wrapped in its own named function, rather
    than passing `datetime.now` directly with `UTC` bound some other
    way, specifically so this remains a plain, ordinary, picklable
    module-level function — required whenever `max_workers` is set (see
    `_require_picklable`), since this is exactly the kind of value that
    check exists to accept.
    """
    return datetime.now(UTC)


def _validate_public_signature(
    N: PopulationSize,
    m: Migration,
    mu: MutationRate,
    d: int,
    params: SimulationParams,
) -> None:
    """Reject disagreement between named arguments and the parameter bag.

    `fim()` accepts `N`/`m`/`mu`/`d` both as their own named arguments
    and, redundantly, already inside `params` (see `fim`'s own docstring
    for why) — this function is what actually enforces that the two
    copies agree, rather than silently trusting whichever one a caller
    happened to update if they ever changed one without the other.
    """
    mismatches: list[str] = []
    if N != params.N:
        mismatches.append("N")
    if m != params.m:
        mismatches.append("m")
    if mu != params.mu:
        mismatches.append("mu")
    if d != params.d:
        mismatches.append("d")
    if mismatches:
        raise ValueError(
            "named fim arguments disagree with params: " + ", ".join(mismatches)
        )
