"""Public deterministic finite-island-model simulation engine."""

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
    """Final run-level scalar report."""

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
    """Return the terminal state, report, convergence trace, and manifest."""

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

    Args:
        N: Gene-copy count, repeated from ``params`` for the public signature.
        m: Migration rate or matrix, repeated from ``params``.
        mu: Mutation probability, repeated from ``params``.
        d: Deme count, repeated from ``params``.
        params: Full validated run configuration and open parameter bag.
        store: Optional trajectory backend, shared by every replicate.
            Memory storage is the library default. Mutually exclusive with
            `store_factory`, and must be ``None`` whenever `max_workers`
            is set — a single store instance cannot safely be shared
            across worker processes; use `store_factory` instead.
        run_id: Optional stable run identifier.
        clock: Injectable UTC clock used only for manifest timestamps. Under
            `max_workers`, it crosses a process boundary and so must be
            picklable — a module-level function, not a closure or lambda.
        max_workers: Opt-in replicate-batch parallelism. ``None`` (the
            default) preserves the exact prior sequential loop. Set to run
            replicates in batches of up to this many concurrent worker
            processes — real OS processes, not threads, since this
            project's per-generation state is Python-object sparse maps
            that do not release the GIL. An adaptive `replicate_tolerance`
            stop is still checked strictly in ascending replicate order
            after each whole batch completes, so a batch can overshoot the
            exact minimal replicate count by at most ``max_workers - 1``.
        store_factory: Builds one fresh trajectory store per replicate,
            given that replicate's `run_id`. Meaningful for any batch
            (``n_replicates`` greater than one) — sequential or parallel
            — wherever every replicate needs its own store rather than
            one shared instance; required in practice under
            `max_workers`, where a worker process cannot share the
            parent's `store`. Must itself be picklable (a module-level
            function, or `functools.partial` over one) whenever
            `max_workers` is set. ``None`` (the default) falls back to
            `store` (or a private `InMemoryTrajectoryStore` if that is
            also unset).

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
    """Return a stable run ID derived only from replayable parameters."""
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

    Args:
        state: State to analyze.
        params: Parameters controlling supported deme weighting.
        run_id: Run identity included in the report.
        converged: Whether statistical stability fired.
        reason: Plain-language terminal reason.

    Returns:
        Run metadata plus named scalar statistics averaged across loci.
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


def replicate_summary(
    results: Sequence[RunResult],
    *,
    confidence: float = 0.95,
) -> dict[str, ConfidenceInterval]:
    """Return each reported statistic's across-replicate confidence interval.

    Each replicate is an independent draw of its own final ``D``, ``G_ST``,
    and so on; this is a closed-form Student's-t interval on the sample
    mean of those draws (`fim.statistics.interval.confidence_interval`),
    not a resampling scheme — the replicates are already independent by
    construction, so nothing further is needed to treat them as a sample.

    Args:
        results: Two or more independently seeded replicate results, as
            returned by `fim` when `SimulationParams.n_replicates` is
            greater than one.
        confidence: Two-tailed confidence level; see
            `fim.statistics.interval.confidence_interval`.

    Returns:
        One `ConfidenceInterval` per statistic name in `FinalReport`
        (``D``, ``G_ST``, ``E_ST``, ``K_ST``, ``H_S``, ``H_T``).
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
    summary: dict[str, ConfidenceInterval] = {}
    for statistic in ("D", "G_ST", "E_ST", "K_ST", "H_S", "H_T"):
        values = [
            value
            for result in results
            if (value := _final_report_statistic(result.report, statistic)) is not None
        ]
        if len(values) < _MINIMUM_REPLICATE_SUMMARY_COUNT:
            continue
        summary[statistic] = confidence_interval(values, confidence=confidence)
    return summary


def _build_finite_allele_spaces(
    state: ModelState,
    params: SimulationParams,
) -> dict[int, FiniteAlleleSpace]:
    """Construct one finite-allele state space per locus, seeded from generation zero.

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

    `ProcessPoolExecutor` ships `clock` and `store_factory` across a
    process boundary by pickling them; a closure or lambda fails that
    silently deep inside worker-process spawn machinery, surfacing as
    raw pickling noise with no indication of which argument was at
    fault (R24). Checking here instead fails at the call site, before a
    single worker process is started, naming the offending argument.

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

    Batches, rather than one unbounded pool submission, so an adaptive
    `monitor` can still stop the whole run early: replicates within one
    batch run concurrently, but the stopping decision is always applied
    afterward, strictly in ascending replicate order — the same order
    the sequential loop uses — so a batch can overshoot an exact minimal
    replicate count by at most ``max_workers - 1``, never reorder which
    replicate's values feed the decision.
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

    Module-level, not a closure, so `ProcessPoolExecutor` can pickle and
    ship it to a worker process. `store_factory` must itself be
    picklable for the same reason — a module-level function or
    `functools.partial` over one, never a lambda or closure.
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
    """Execute one scalar replicate."""
    started_at = _format_timestamp(clock())
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
    finite_alleles = (
        FiniteAlleleRegistry(_build_finite_allele_spaces(state, params))
        if params.mutation_model == "finite_alleles"
        else None
    )
    store.write_generation(run_id, state.generation, state.to_rows(run_id))
    monitor.record(
        state.generation,
        _convergence_values(state, params),
    )
    while not monitor.should_stop():
        state = step(state, params, registry, rng, finite_alleles=finite_alleles)
        store.write_generation(run_id, state.generation, state.to_rows(run_id))
        monitor.record(
            state.generation,
            _convergence_values(state, params),
        )

    outcome = monitor.outcome()
    if outcome.reason is None:
        raise RuntimeError("stopped convergence monitor has no reason")
    report = report_for_state(
        state,
        params,
        run_id=run_id,
        converged=outcome.converged,
        reason=outcome.reason.value,
    )
    ended_at = _format_timestamp(clock())
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

    Computes each locus's full differentiation report exactly once and reads
    every watched statistic from that same cached set of reports, rather than
    recomputing per-locus statistics once per watched name — the several-
    statistic case (design §9) costs one extra dictionary lookup per
    statistic per locus, not another pass over the state.

    A watched statistic that is undefined this generation (only ``G_ST``
    can be, and only when every tracked locus is currently monomorphic —
    see `_mean_g_st_across_loci`) is omitted from the returned mapping
    rather than represented by a substitute value; `ConvergenceMonitor`
    treats an omitted statistic as simply not advancing its own trailing
    window this generation, which is honest about there being no
    observation to add, rather than fabricating one.
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

    ``None`` whenever `SimulationParams.replicate_tolerance` is unset —
    the opt-in sentinel that keeps every prior release's fixed-count
    batch loop byte-identical when this feature is not configured.
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
    """Read one named field from a final run report."""
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
    raise ValueError(f"unsupported statistic: {statistic}")


def _format_timestamp(value: datetime) -> str:
    """Return an unambiguous UTC ISO-8601 timestamp."""
    if value.tzinfo is None:
        raise ValueError("manifest clock must return a timezone-aware datetime")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _mean(values: Sequence[float]) -> float:
    """Return an accurate mean of a nonempty float sequence."""
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
    """Compute one locus's scalar statistics."""
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
    """Read one validated convergence-statistic field."""
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
    """Return the current UTC time for manifest metadata only."""
    return datetime.now(UTC)


def _validate_public_signature(
    N: PopulationSize,
    m: Migration,
    mu: MutationRate,
    d: int,
    params: SimulationParams,
) -> None:
    """Reject disagreement between named arguments and the parameter bag."""
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
