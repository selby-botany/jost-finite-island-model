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
from fim.persistence.binary_store import (
    EQUILIBRIUM_LOG_FILENAME,
    TRAJECTORY_LOG_FILENAME,
)
from fim.persistence.manifest import hash_file, write_manifest
from fim.persistence.pairwise import pairwise_payload, write_pairwise
from fim.persistence.report import write_jsonl_rows, write_report
from fim.viz import plot_frequency_scatter

__all__ = [
    "convergence_history_rows",
    "run_artifact_targets",
    "write_run_artifacts",
]


def run_artifact_targets(directory: Path) -> dict[str, Path]:
    """Return every documented scalar-run artifact path in one directory.

    The single source for these filenames, used by whatever writes a run
    and by anything that checks a completed run's output, so the two cannot
    disagree about where an artifact lives. Every path is returned whether
    or not a given run produced it: the dict names where a thing would live.
    `equilibrium_trajectory` is written only by an equilibrium-split run,
    streamed by the engine beside `trajectory.tlog`.

    Args:
        directory: The run directory.

    Returns:
        Artifact name to path.
    """
    return {
        "trajectory": directory / TRAJECTORY_LOG_FILENAME,
        "equilibrium_trajectory": directory / EQUILIBRIUM_LOG_FILENAME,
        "manifest": directory / "manifest.json",
        "report": directory / "report.json",
        "scatter": directory / "scatter.png",
        "convergence": directory / "convergence.jsonl",
        "pairwise": directory / "pairwise.json",
    }


def convergence_history_rows(result: RunResult) -> list[dict[str, object]]:
    """Return one row per generation, for `convergence.jsonl`.

    Each row is ``{"generation": N, "D": ..., "H_S": ..., ...}``. A statistic
    whose history is shorter than the generation list (only `G_ST` can be, at
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
    targets = run_artifact_targets(directory)
    write_report(targets["report"], result.report)
    figure = plot_frequency_scatter(
        result.final_state, result.params, targets["scatter"]
    )
    plt.close(figure)
    # The monitor's per-generation history, so reopening a run restores its
    # trajectory curves.
    write_jsonl_rows(targets["convergence"], convergence_history_rows(result))
    # Every deme pair's Nei identities at the final generation, so any pair
    # can be compared later.
    write_pairwise(
        targets["pairwise"],
        pairwise_payload(result.final_state, max_demes=pairwise_max_demes),
    )
    digested_names = ["trajectory", "report", "scatter", "convergence", "pairwise"]
    if targets["equilibrium_trajectory"].is_file():
        digested_names.append("equilibrium_trajectory")
    manifest = replace(
        result.manifest,
        artifacts={name: hash_file(targets[name]) for name in digested_names},
    )
    write_manifest(targets["manifest"], manifest)
    return targets
