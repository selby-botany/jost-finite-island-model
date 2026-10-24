"""Write the files that make up a saved run.

A completed run is a directory of files: the streamed trajectory log, the
report, the final-state scatter plot, the per-generation convergence history,
the deme-pair statistics, and a manifest that records a checksum of each. This
module is the one place that knows those names and writes them, for the
command line, the desktop app, saved runs, and sweeps alike.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from matplotlib import pyplot as plt

from fim.config.defaults import DEFAULT_PAIRWISE_MAX_DEMES
from fim.engine.records import RunResult
from fim.model.params import HistoryFormat, SimulationParams
from fim.persistence.binary_store import (
    EQUILIBRIUM_LOG_FILENAME,
    TRAJECTORY_LOG_FILENAME,
    log_options,
)
from fim.persistence.history import (
    COMPACT_FILENAME,
    DECIMATED_FILENAME,
    HISTORY_FILENAME,
    fractional_bits,
    write_compact_history,
    write_decimated_history,
)
from fim.persistence.manifest import hash_file, write_manifest
from fim.persistence.pairwise import pairwise_payload, write_pairwise
from fim.persistence.report import write_jsonl_rows, write_report
from fim.persistence.retention import TrajectoryRetention
from fim.persistence.taper import taper_log
from fim.viz import plot_frequency_scatter

__all__ = [
    "convergence_history_rows",
    "run_artifact_targets",
    "taper_finished_trajectory",
    "write_decimated",
    "write_history",
    "write_run_artifacts",
]


def run_artifact_targets(
    directory: Path, *, history_format: HistoryFormat = "exact"
) -> dict[str, Path]:
    """Return every documented scalar-run artifact path in one directory.

    The single source for these filenames, used by whatever writes a run
    and by anything that checks a completed run's output, so the two cannot
    disagree about where an artifact lives. Every path is returned whether
    or not a given run produced it: the dict names where a thing would live.
    `equilibrium_trajectory` is written only by an equilibrium-split run,
    streamed by the engine beside `trajectory.tlog`.

    Args:
        directory: The run directory.
        history_format: Which statistic history file the run writes
            (`"exact"`: `convergence.jsonl`, `"compact"`: `convergence.fhist`).

    Returns:
        Artifact name to path.
    """
    return {
        "trajectory": directory / TRAJECTORY_LOG_FILENAME,
        "equilibrium_trajectory": directory / EQUILIBRIUM_LOG_FILENAME,
        "manifest": directory / "manifest.json",
        "report": directory / "report.json",
        "scatter": directory / "scatter.png",
        "convergence": directory
        / (COMPACT_FILENAME if history_format == "compact" else HISTORY_FILENAME),
        "pairwise": directory / "pairwise.json",
    }


def convergence_history_rows(result: RunResult) -> list[dict[str, object]]:
    """Return one row per generation, for `convergence.jsonl`.

    Each row is ``{"generation": N, "D": ..., "H_S": ..., ...}``. A statistic
    whose history is shorter than the generation list (only $G_{ST}$ can be, at
    a currently monomorphic locus) is left out of every row, because which
    generations it is missing is not recoverable from the monitor's record.

    Args:
        result: A finished engine result.

    Returns:
        The rows, in generation order.
    """
    aligned = {
        name: values
        for name, values in result.convergence_histories.items()
        if len(values) == len(result.convergence_generations)
    }
    return [
        {"generation": generation, **{name: aligned[name][index] for name in aligned}}
        for index, generation in enumerate(result.convergence_generations)
    ]


def taper_finished_trajectory(result: RunResult, trajectory: Path) -> None:
    """Taper a finished run's log if its configuration asks for tapering.

    The log is written whole while the run goes (the rule needs the run's
    end), so this is the one place that cuts it down: before the artifact
    digests are taken. A run that does not taper is left alone.

    Args:
        result: A finished engine result.
        trajectory: The run's `trajectory.tlog`.
    """
    retention = TrajectoryRetention.from_params(result.params)
    if retention.statistics:
        # No trajectory log is kept: the frames were only ever written so the
        # run could be checkpointed and resumed.
        trajectory.unlink(missing_ok=True)
        trajectory.with_name(EQUILIBRIUM_LOG_FILENAME).unlink(missing_ok=True)
        return
    if not retention.tapered or not trajectory.is_file():
        return
    final = int(result.report["generation"])
    taper_log(
        trajectory,
        retention.kept_generations(final),
        options=log_options(result.params.expert),
    )


def write_history(result: RunResult, path: Path) -> None:
    """Write a run's statistic history in the format its configuration asks for.

    A compact history is rounded to a step set by the run's precision (the
    smallest of `precision` and any per-statistic precision).

    Args:
        result: A finished engine result.
        path: Where the history goes (`run_artifact_targets`' "convergence").

    Raises:
        HistoryError: A compact history holds a value too large for the step.
    """
    rows = convergence_history_rows(result)
    if path.suffix != Path(COMPACT_FILENAME).suffix:
        write_jsonl_rows(path, rows)
        return
    generations = [int(str(row["generation"])) for row in rows]
    names = [name for name in (rows[0] if rows else {}) if name != "generation"]
    columns = {name: [_number(row[name]) for row in rows] for name in names}
    write_compact_history(
        path,
        generations,
        columns,
        bits=fractional_bits(_smallest_precision(result.params)),
    )


def write_decimated(result: RunResult, path: Path) -> None:
    """Write the decimated statistic history a statistics-only run keeps.

    At most 2,048 generations of every statistic, at full precision.

    Args:
        result: A finished engine result.
        path: Where it goes (`history.json` in the run directory; it is not
            among `run_artifact_targets`, which names what every run can have).
    """
    rows = convergence_history_rows(result)
    generations = [int(str(row["generation"])) for row in rows]
    names = [name for name in (rows[0] if rows else {}) if name != "generation"]
    write_decimated_history(
        path,
        generations,
        {name: [_number(row[name]) for row in rows] for name in names},
    )


def _number(value: object) -> float | None:
    """Return a history value as a float, or `None` if it is not a number."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _smallest_precision(params: SimulationParams) -> float:
    """Return the smallest precision a run was asked for, overall or per statistic."""
    levels = [params.precision]
    levels.extend(float(level) for _, level in params.statistic_precision or ())
    return min(level for level in levels if level > 0)


def write_run_artifacts(
    result: RunResult,
    directory: Path,
    *,
    pairwise_max_demes: int = DEFAULT_PAIRWISE_MAX_DEMES,
) -> dict[str, Path]:
    """Write one run's report, plots, histories, and, last, its manifest.

    `trajectory.tlog` is not written here: the `TrajectoryStore` passed to the
    engine streamed it generation by generation, so it already exists. Every
    other artifact is written and flushed first; `manifest.json` goes last,
    with each sibling's checksum and size (`fim.persistence.manifest.
    hash_file`), the record `verify_trajectory_integrity` later checks. A
    flush means the data reached the operating system's cache, not the disk
    (no `fsync`), so the ordering protects against a process dying mid-write,
    not against power loss; see `fim.paths.atomic_directory`.

    Args:
        result: A finished engine result.
        directory: The run directory.
        pairwise_max_demes: Largest deme count for which every deme pair's
            statistics are written.

    Returns:
        The artifact paths (see `run_artifact_targets`).
    """
    targets = run_artifact_targets(
        directory, history_format=result.params.history_format
    )
    taper_finished_trajectory(result, targets["trajectory"])
    write_report(targets["report"], result.report)
    figure = plot_frequency_scatter(
        result.final_state, result.params, targets["scatter"]
    )
    plt.close(figure)
    # The monitor's per-generation history, so reopening a run restores its
    # trajectory curves.
    statistics_only = TrajectoryRetention.from_params(result.params).statistics
    if statistics_only:
        write_decimated(result, directory / DECIMATED_FILENAME)
    else:
        write_history(result, targets["convergence"])
    # Every deme pair's Nei identities at the final generation, so any pair
    # can be compared later.
    write_pairwise(
        targets["pairwise"],
        pairwise_payload(result.final_state, max_demes=pairwise_max_demes),
    )
    digested_names = (
        ["report", "scatter", "history", "pairwise"]
        if statistics_only
        else ["trajectory", "report", "scatter", "convergence", "pairwise"]
    )
    if targets["equilibrium_trajectory"].is_file():
        digested_names.append("equilibrium_trajectory")
    where = {**targets, "history": directory / DECIMATED_FILENAME}
    manifest = replace(
        result.manifest,
        artifacts={name: hash_file(where[name]) for name in digested_names},
    )
    write_manifest(targets["manifest"], manifest)
    return targets
