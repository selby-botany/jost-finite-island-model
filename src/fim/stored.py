"""Open a saved run again: `open_run(directory)`.

A saved run is the directory `simulate` (or `fim run`) published. Opening it
reads the manifests, reports, and convergence histories into the same types
a fresh run returns (`Report`, `Stop`, `Histories`, `Summary`), and gives
access to the stored trajectory for re-analysis. Nothing is loaded that is
not asked for: the trajectory log stays on disk until read.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fim.errors import StoreError
from fim.persistence.manifest import (
    BatchManifest,
    RunManifest,
    read_batch_manifest,
    read_manifest,
    verify_trajectory_integrity,
)
from fim.persistence.store import TrajectoryRow
from fim.persistence.tlog_reader import read_rows
from fim.reanalyze import (
    ReanalyzedGeneration,
    read_persisted_convergence_history,
    reanalyze_trajectory,
)
from fim.results import Histories, Report, Stop, Summary, stop_from_report
from fim.spec.codec import RunConfig
from fim.spec.run import RunSpec

__all__ = ["StoredReplicate", "StoredRun", "open_run"]


@dataclass(frozen=True, slots=True)
class StoredReplicate:
    """One saved replicate.

    Args:
        index: Position in the batch, from 0.
        run_id: The replicate's run ID.
        directory: Where its files are.
        manifest: Its manifest (parameters, timing, artifact digests).
        report: Its statistics at the last generation.
        stop: Why it ended.
        histories: How its tracked statistics moved, or `None` for a run
            saved before histories were kept.
    """

    index: int
    run_id: str
    directory: Path
    manifest: RunManifest
    report: Report
    stop: Stop
    histories: Histories | None

    @property
    def trajectory_path(self) -> Path:
        """Return the stored trajectory log's path."""
        return self.directory / "trajectory.tlog"

    def rows(
        self, generations: list[int] | None = None
    ) -> dict[int, list[TrajectoryRow]]:
        """Read stored rows grouped by generation.

        Args:
            generations: Only these generations, or `None` for all.

        Returns:
            ``{generation: rows}``.
        """
        return read_rows(self.trajectory_path, self.run_id, generations)

    def verify(self) -> None:
        """Check the trajectory still matches its manifest's digest.

        Raises:
            ValueError: The file was edited, truncated, or replaced.
        """
        verify_trajectory_integrity(self.trajectory_path, self.manifest)

    def reanalyze(self, generation: int | None = None) -> ReanalyzedGeneration:
        """Recompute the statistics of one stored generation.

        Args:
            generation: The generation, or `None` for the last stored.

        Returns:
            The recomputed statistics.
        """
        return reanalyze_trajectory(
            self.trajectory_path,
            manifest_path=self.directory / "manifest.json",
            generation=generation,
        )


@dataclass(frozen=True, slots=True)
class StoredRun:
    """A saved run, opened.

    Args:
        directory: The run directory.
        run_id: The run's ID.
        spec: What was simulated, read from the manifest.
        replicates: The saved replicates, in order.
        summary: Across-replicate intervals (empty for a single run).
    """

    directory: Path
    run_id: str
    spec: RunSpec
    replicates: tuple[StoredReplicate, ...]
    summary: Summary

    def only(self) -> StoredReplicate:
        """Return the sole replicate.

        Raises:
            ValueError: The run has more than one replicate.
        """
        if len(self.replicates) != 1:
            raise ValueError(
                f"run has {len(self.replicates)} replicates; use .replicates"
            )
        return self.replicates[0]


def _replicate(index: int, directory: Path) -> StoredReplicate:
    """Open one replicate directory."""
    manifest = read_manifest(directory / "manifest.json")
    report: dict[str, Any] = json.loads(
        (directory / "report.json").read_text(encoding="utf-8")
    )
    persisted = read_persisted_convergence_history(
        directory / "trajectory.tlog", manifest
    )
    histories = (
        None
        if persisted is None
        else Histories(
            generations=tuple(persisted[0]),
            series={name: tuple(values) for name, values in persisted[1].items()},
        )
    )
    return StoredReplicate(
        index=index,
        run_id=manifest.run_id,
        directory=directory,
        manifest=manifest,
        report=Report(report),
        stop=stop_from_report(report),
        histories=histories,
    )


def open_run(directory: Path | str) -> StoredRun:
    """Open a saved run.

    Args:
        directory: A directory published by `simulate` with a recording
            directory, or by `fim run`.

    Returns:
        The opened run.

    Raises:
        StoreError: The directory holds no manifest.
    """
    root = Path(directory)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise StoreError(f"{root} is not a saved run (no manifest.json)")
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    if "replicate_run_ids" in document:
        batch: BatchManifest = read_batch_manifest(manifest_path)
        folders = sorted(root.glob("replicate-*"))
        replicates = tuple(_replicate(i, folder) for i, folder in enumerate(folders))
        summary_path = root / "summary.json"
        summary = Summary(
            json.loads(summary_path.read_text(encoding="utf-8"))
            if summary_path.is_file()
            else {}
        )
        return StoredRun(
            directory=root,
            run_id=batch.run_id,
            spec=RunConfig.from_mapping(batch.parameters).spec,
            replicates=replicates,
            summary=summary,
        )
    replicate = _replicate(0, root)
    return StoredRun(
        directory=root,
        run_id=replicate.run_id,
        spec=RunConfig.from_mapping(replicate.manifest.parameters).spec,
        replicates=(replicate,),
        summary=Summary({}),
    )
