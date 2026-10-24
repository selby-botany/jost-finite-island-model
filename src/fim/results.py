"""What a run returns: one `Result` holding its replicates and their summary.

A run always returns a `Result`, whether it had one replicate or two hundred.
Each replicate is a `Replicate` with its `Stop` (why it ended), its `Report`
(the statistics at its last generation), its `Histories` (how the watched
statistics moved), and its final state. The batch as a whole has a `Summary`
(the across-replicate intervals), a `BatchStop` (why no more replicates were
added), and a `Provenance` (what produced the numbers).

These are plain frozen values. They hold no open files: a replicate's saved
trajectory is read through a `TrajectoryReader`, which cannot write.
"""

from __future__ import annotations

import platform
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, TypeAlias

import numpy as np

from fim import __version__
from fim.config.numerics import MINIMUM_REPLICATE_COUNT
from fim.engine.records import RunResult
from fim.engine.summary import replicate_summary
from fim.model.state import ModelState
from fim.persistence.store import TrajectoryRow, TrajectoryStore
from fim.spec.codec import from_params
from fim.spec.execution import Execution
from fim.spec.run import RunSpec
from fim.statistics.catalog import report_keys
from fim.statistics.interval import ConfidenceInterval

BatchStop: TypeAlias = Literal["single", "precision_reached", "replicate_cap"]
"""Why a batch ended: it had one replicate, reached its precision before the
replicate cap, or ran every replicate it was allowed."""

_BOOKKEEPING = ("run_id", "generation", "converged", "converged_on", "reason")


@dataclass(frozen=True, slots=True)
class Stop:
    """Why one replicate ended.

    Args:
        converged: Whether the watched statistics settled, rather than the
            generation cap ending the run.
        reason: The stop reason as the engine names it.
        generation: The last generation reached.
        watched: The statistics whose stability ended the run (empty if it
            hit the cap).
    """

    converged: bool
    reason: str
    generation: int
    watched: tuple[str, ...]


class Report(Mapping[str, float | None]):
    """The statistics of one replicate at its last generation.

    Behaves as a read-only mapping from statistic ID (as listed in the
    statistics catalog, for example ``"D"`` or ``"G_ST"``) to its value;
    ``None`` means the statistic is undefined for this replicate ($G_{ST}$
    when a locus is monomorphic everywhere). `to_dict` returns exactly the
    contents of the run's `report.json`.

    The four statistics every run tracks are also attributes: `D`, `G_ST`,
    `H_S`, and `H_T`.
    """

    __slots__ = ("_final", "_values")

    def __init__(self, final: Mapping[str, Any]) -> None:
        """Wrap a final report.

        Args:
            final: A `FinalReport`, or the same mapping read back from JSON.
        """
        self._final: dict[str, Any] = dict(final)
        self._values: Mapping[str, float | None] = MappingProxyType(
            {key: self._final[key] for key in report_keys() if key in self._final}
        )

    def __getitem__(self, key: str) -> float | None:
        """Return a statistic's value, or raise `KeyError` naming valid IDs."""
        try:
            return self._values[key]
        except KeyError:
            valid = ", ".join(self._values)
            raise KeyError(f"unknown statistic {key!r}; valid IDs: {valid}") from None

    def __iter__(self) -> Iterator[str]:
        """Iterate statistic IDs in catalog order."""
        return iter(self._values)

    def __len__(self) -> int:
        """Return the number of statistics reported."""
        return len(self._values)

    def __repr__(self) -> str:
        """Return a short summary of the headline statistics."""
        return (
            f"Report(D={self.D!r}, G_ST={self.G_ST!r}, "
            f"H_S={self.H_S!r}, H_T={self.H_T!r})"
        )

    @property
    def D(self) -> float | None:  # noqa: N802
        """Jost's differentiation $D$."""
        return self["D"]

    @property
    def G_ST(self) -> float | None:  # noqa: N802
        """Nei's $G_{ST}$; `None` where it is undefined."""
        return self["G_ST"]

    @property
    def H_S(self) -> float | None:  # noqa: N802
        """Mean within-deme heterozygosity."""
        return self["H_S"]

    @property
    def H_T(self) -> float | None:  # noqa: N802
        """Total heterozygosity."""
        return self["H_T"]

    @property
    def windows(self) -> Mapping[str, Mapping[str, Any]]:
        """Return the evidence-window statistics, by statistic ID."""
        return MappingProxyType(self._final.get("window_statistics", {}))

    def to_dict(self) -> dict[str, Any]:
        """Return the report as the dict written to `report.json`."""
        return dict(self._final)


@dataclass(frozen=True, slots=True)
class Histories:
    """How the tracked statistics moved over one replicate.

    Args:
        generations: The generations at which the convergence check
            recorded an observation.
        series: For each tracked statistic ID, its value at each of those
            generations.
    """

    generations: tuple[int, ...]
    series: Mapping[str, tuple[float, ...]]

    def __getitem__(self, name: str) -> tuple[float, ...]:
        """Return one statistic's values, aligned with `generations`."""
        return self.series[name]

    @property
    def names(self) -> tuple[str, ...]:
        """Return the tracked statistic IDs."""
        return tuple(self.series)


class TrajectoryReader:
    """Read-only access to the rows a replicate stored.

    Args:
        store: The store the replicate wrote to.
        run_id: The replicate's run ID in that store.
    """

    __slots__ = ("_run_id", "_store")

    def __init__(self, store: TrajectoryStore, run_id: str) -> None:
        """Remember where the rows are."""
        self._store = store
        self._run_id = run_id

    @property
    def run_id(self) -> str:
        """Return the replicate's run ID."""
        return self._run_id

    def rows(self) -> Iterator[TrajectoryRow]:
        """Yield the stored rows in order."""
        return iter(self._store.read(self._run_id))


@dataclass(frozen=True, slots=True)
class Replicate:
    """One independently seeded replicate.

    Args:
        index: Position in the batch, from 0.
        seed: The seed this replicate used.
        run_id: The replicate's run ID.
        stop: Why it ended.
        report: Its statistics at the last generation.
        histories: How its tracked statistics moved.
        final_state: The population at the last generation.
        trajectory: Reader over its stored rows.
    """

    index: int
    seed: int
    run_id: str
    stop: Stop
    report: Report
    histories: Histories
    final_state: ModelState
    trajectory: TrajectoryReader


class Summary(Mapping[str, ConfidenceInterval]):
    """The across-replicate interval of each statistic.

    A read-only mapping from statistic ID to its `ConfidenceInterval`. It is
    empty for a single replicate, and a statistic with fewer than two defined
    replicates is absent.
    """

    __slots__ = ("_intervals",)

    def __init__(self, intervals: Mapping[str, ConfidenceInterval]) -> None:
        """Wrap a mapping of intervals."""
        self._intervals = MappingProxyType(dict(intervals))

    def __getitem__(self, key: str) -> ConfidenceInterval:
        """Return a statistic's interval."""
        return self._intervals[key]

    def __iter__(self) -> Iterator[str]:
        """Iterate statistic IDs."""
        return iter(self._intervals)

    def __len__(self) -> int:
        """Return the number of statistics with an interval."""
        return len(self._intervals)


@dataclass(frozen=True, slots=True)
class Provenance:
    """What produced a result's numbers.

    Args:
        software_version: The `fim` version.
        python: The Python version.
        numpy: The NumPy version.
        platform: The operating system and machine.
        requested: The `Execution` that was asked for.
        backend: The backend that actually ran (never ``"auto"``).
        jit: The JIT setting that ran.
        started_at: When the first replicate started (UTC text).
        ended_at: When the last replicate ended (UTC text).
        derived: Values the engine derived from ``"auto"`` settings, by name.
    """

    software_version: str
    python: str
    numpy: str
    platform: str
    requested: Execution
    backend: str
    jit: str
    started_at: str
    ended_at: str
    derived: Mapping[str, float | int]


@dataclass(frozen=True, slots=True)
class Result:
    """Everything a run produced.

    Args:
        run_id: The run's ID (see `fim.spec.run_identity`).
        spec: What was simulated.
        replicates: The replicates, in order.
        summary: Across-replicate intervals.
        batch_stop: Why no more replicates were added.
        provenance: What produced the numbers.
    """

    run_id: str
    spec: RunSpec
    replicates: tuple[Replicate, ...]
    summary: Summary
    batch_stop: BatchStop
    provenance: Provenance

    def only(self) -> Replicate:
        """Return the sole replicate.

        Raises:
            ValueError: The run had more than one replicate.
        """
        if len(self.replicates) != 1:
            raise ValueError(
                f"result has {len(self.replicates)} replicates; use .replicates"
            )
        return self.replicates[0]


def stop_from_report(report: Mapping[str, Any]) -> Stop:
    """Return the `Stop` recorded in a final report.

    Args:
        report: A `FinalReport`, or the same mapping read from `report.json`.

    Returns:
        Why the replicate ended.
    """
    watched = report["converged_on"]
    return Stop(
        converged=report["converged"],
        reason=report["reason"],
        generation=report["generation"],
        watched=(
            ()
            if watched is None
            else (watched,)
            if isinstance(watched, str)
            else tuple(watched)
        ),
    )


def _replicate_of(index: int, result: RunResult) -> Replicate:
    """Return the `Replicate` of one engine result."""
    return Replicate(
        index=index,
        seed=result.params.seed,
        run_id=result.run_id,
        stop=stop_from_report(result.report),
        report=Report(result.report),
        histories=Histories(
            generations=tuple(result.convergence_generations),
            series={
                name: tuple(values)
                for name, values in result.convergence_histories.items()
            },
        ),
        final_state=result.final_state,
        trajectory=TrajectoryReader(result.store, result.run_id),
    )


def result_from_runs(
    output: RunResult | Sequence[RunResult],
    *,
    run_id: str,
    params: Any,
    spec: RunSpec | None = None,
) -> Result:
    """Build a `Result` from what the engine returned.

    Args:
        output: One engine result, or one per replicate.
        run_id: The run's ID.
        params: The `SimulationParams` the run was started with (the batch's,
            not a replicate's).
        spec: The specification the caller wrote, with its ``"auto"`` values
            as written. If omitted it is read back from `params`, which
            reports derived values as numbers.

    Returns:
        The `Result`.
    """
    runs = (output,) if isinstance(output, RunResult) else tuple(output)
    derived_spec, execution, _ = from_params(params)
    spec = spec if spec is not None else derived_spec
    replicates = tuple(_replicate_of(i, run) for i, run in enumerate(runs))
    summary = (
        Summary(replicate_summary(runs, confidence=params.confidence))
        if len(runs) >= MINIMUM_REPLICATE_COUNT
        else Summary({})
    )
    first = runs[0].manifest
    batch_stop: BatchStop
    if len(runs) == 1:
        batch_stop = "single"
    elif params.stop_batch_early and len(runs) < params.n_replicates:
        batch_stop = "precision_reached"
    else:
        batch_stop = "replicate_cap"
    derived: dict[str, float | int] = {
        name: getattr(params, name) for name in sorted(params.auto_derived)
    }
    if params.relaxation_time is not None:
        derived["relaxation_time"] = params.relaxation_time
    provenance = Provenance(
        software_version=first.software_version or __version__,
        python=sys.version.split()[0],
        numpy=np.__version__,
        platform=platform.platform(),
        requested=execution,
        backend=first.engine_backend or params.engine_backend,
        jit=first.jit or params.jit,
        started_at=min(run.manifest.started_at for run in runs),
        ended_at=max(run.manifest.ended_at for run in runs),
        derived=MappingProxyType(derived),
    )
    return Result(
        run_id=run_id,
        spec=spec,
        replicates=replicates,
        summary=summary,
        batch_stop=batch_stop,
        provenance=provenance,
    )


__all__ = [
    "BatchStop",
    "Histories",
    "Provenance",
    "Replicate",
    "Report",
    "Result",
    "Stop",
    "Summary",
    "TrajectoryReader",
    "result_from_runs",
    "stop_from_report",
]
