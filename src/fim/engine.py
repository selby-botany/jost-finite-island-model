"""Public deterministic finite-island-model simulation engine."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, TypeAlias, TypedDict

import numpy as np

from fim import __version__
from fim.convergence.criteria import TrailingWindowCriterion
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
from fim.persistence.manifest import RunManifest
from fim.persistence.store import InMemoryTrajectoryStore, TrajectoryStore
from fim.statistics.differentiation import DifferentiationReport, statistics_report

Clock: TypeAlias = Callable[[], datetime]


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
) -> SimulationOutput:
    """Run the finite island model until convergence or the hard cap.

    Args:
        N: Gene-copy count, repeated from ``params`` for the public signature.
        m: Migration rate or matrix, repeated from ``params``.
        mu: Mutation probability, repeated from ``params``.
        d: Deme count, repeated from ``params``.
        params: Full validated run configuration and open parameter bag.
        store: Optional trajectory backend. Memory storage is the library default.
        run_id: Optional stable run identifier.
        clock: Injectable UTC clock used only for manifest timestamps.

    Returns:
        One result, or one independently seeded result per configured replicate.

    Raises:
        ValueError: If the named arguments disagree with ``params``.
    """
    _validate_public_signature(N, m, mu, d, params)
    trajectory_store = store if store is not None else InMemoryTrajectoryStore()
    run_clock = clock if clock is not None else _utc_now
    if params.n_replicates == 1:
        return _run_one(
            params,
            trajectory_store,
            run_id or deterministic_run_id(params),
            run_clock,
        )

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
        results.append(
            _run_one(
                replicate_params,
                trajectory_store,
                replicate_run_id,
                run_clock,
            )
        )
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
        "G_ST": _mean_optional(tuple(report["G_ST"] for report in locus_reports)),
        "D": _mean(tuple(report["D"] for report in locus_reports)),
        "E_ST": _mean(tuple(report["E_ST"] for report in locus_reports)),
        "K_ST": _mean(tuple(report["K_ST"] for report in locus_reports)),
        "H_S": _mean(tuple(report["H_S"] for report in locus_reports)),
        "H_T": _mean(tuple(report["H_T"] for report in locus_reports)),
        "H_ST": _mean(tuple(report["H_ST"] for report in locus_reports)),
    }


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
        run_id=run_id,
        parameters=params.to_dict(),
        started_at=started_at,
        ended_at=ended_at,
        converged=outcome.converged,
        convergence_statistic=params.convergence_statistic,
        stop_reason=outcome.reason.value,
        generation=state.generation,
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
    """Return every watched statistic's value, each averaged across loci.

    Computes each locus's full differentiation report exactly once and reads
    every watched statistic from that same cached set of reports, rather than
    recomputing per-locus statistics once per watched name — the several-
    statistic case (design §9) costs one extra dictionary lookup per
    statistic per locus, not another pass over the state.
    """
    locus_reports = tuple(
        _statistics_for_locus(state, params, locus_index)
        for locus_index in range(state.locus_count)
    )
    return {
        statistic: _mean_statistic_across_loci(
            locus_reports, statistic, state.generation
        )
        for statistic in params.convergence_statistics
    }


def _mean_statistic_across_loci(
    locus_reports: Sequence[DifferentiationReport],
    statistic: str,
    generation: int,
) -> float:
    """Return one statistic's per-locus reports averaged into a single value."""
    values: list[float] = []
    for report in locus_reports:
        value = _report_statistic(report, statistic)
        if value is None:
            if statistic == "G_ST" and report["H_T"] == 0.0:
                value = 0.0
            else:
                raise ValueError(f"{statistic} is undefined at generation {generation}")
        values.append(value)
    return _mean(tuple(values))


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


def _mean_optional(values: Sequence[float | None]) -> float | None:
    """Return the mean when every locus defines the statistic."""
    if any(value is None for value in values):
        return None
    return _mean(tuple(value for value in values if value is not None))


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
