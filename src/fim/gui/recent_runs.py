"""Scan `results/` for recently completed runs, scalar and batch (design
doc §4.6).

Screen 6's recent-runs list is populated by scanning
`fim.paths.results_directory()` for `*/manifest.json`, reading each with
`fim.persistence.manifest.read_manifest` (scalar) or
`fim.persistence.manifest.read_batch_manifest` (batch) — the same files
`fim stats` and `fim run`'s own batch summary default to. A batch's
manifest is listed but labeled distinctly ("batch (14/20)", design §0,
§4.0 #9) rather than treated as something Screen 6 can open directly:
Screen 4's own "Open replicate" is the path to any one replicate's
trajectory, since a batch-level manifest has no single trajectory of
its own to verify or re-analyze (design §3.8, §4.6).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fim import paths
from fim.persistence.manifest import (
    BatchManifest,
    RunManifest,
    read_batch_manifest,
    read_manifest,
)


@dataclass(frozen=True, slots=True)
class RecentRun:
    """One run — scalar or batch — found under `results/`, ready to list.

    Args:
        run_id: The run's identity, from its manifest.
        directory: The run's own output directory — the parent of its
            `manifest.json`.
        ended_at: ISO-8601 completion timestamp, from the manifest —
            what `list_recent_runs` sorts by.
        label: The mock's own display text: a scalar run's
            `stop_reason` (e.g. "converged"), or a batch's
            "batch (replicate_count/n_replicates)" (design §4.6's own
            "batch (14/20)").
        is_batch: Distinguishes a `BatchManifest` entry from a
            `RunManifest` one — Screen 6 uses this to route "Open" to
            re-analysis for a scalar run, or refuse it for a batch
            (design §4.0 #9, §4.6).
    """

    run_id: str
    directory: Path
    ended_at: str
    label: str
    is_batch: bool


def list_recent_runs(results_directory: Path | None = None) -> list[RecentRun]:
    """Return every run under `results_directory`, newest first.

    Args:
        results_directory: Optional override (default:
            `fim.paths.results_directory()`).

    Returns:
        One `RecentRun` per `*/manifest.json` found one level below
        `results_directory` that parses as either manifest shape,
        sorted by `ended_at` descending (an ISO-8601 string, so lexical
        order already matches chronological order). Any file that
        fails to parse as either shape is skipped rather than failing
        the whole scan: one malformed entry should not hide every valid
        one.
    """
    root = (
        results_directory
        if results_directory is not None
        else paths.results_directory()
    )
    if not root.is_dir():
        return []
    found: list[RecentRun] = []
    for manifest_path in root.glob("*/manifest.json"):
        run = _recent_run_from_file(manifest_path)
        if run is not None:
            found.append(run)
    found.sort(key=lambda run: run.ended_at, reverse=True)
    return found


def _recent_run_from_batch_manifest(
    manifest: BatchManifest, directory: Path
) -> RecentRun:
    """Build a batch `RecentRun`, labeled distinctly (design §0, §4.0 #9)."""
    n_replicates = manifest.params().n_replicates
    return RecentRun(
        run_id=manifest.run_id,
        directory=directory,
        ended_at=manifest.ended_at,
        label=f"batch ({manifest.replicate_count}/{n_replicates})",
        is_batch=True,
    )


def _recent_run_from_file(manifest_path: Path) -> RecentRun | None:
    """Parse one `manifest.json` as either shape; `None` if it matches neither.

    Tries the scalar shape first — `read_manifest`/`RunManifest.from_dict`
    reject a batch manifest's file cleanly (its required-fields set
    differs), and vice versa for `read_batch_manifest` — so this never
    silently misreads one shape as the other.
    """
    try:
        return _recent_run_from_manifest(
            read_manifest(manifest_path), manifest_path.parent
        )
    except (ValueError, KeyError):
        pass
    try:
        return _recent_run_from_batch_manifest(
            read_batch_manifest(manifest_path), manifest_path.parent
        )
    except (ValueError, KeyError):
        return None


def _recent_run_from_manifest(manifest: RunManifest, directory: Path) -> RecentRun:
    """Build a scalar `RecentRun`, labeled with its own stop reason."""
    return RecentRun(
        run_id=manifest.run_id,
        directory=directory,
        ended_at=manifest.ended_at,
        label=manifest.stop_reason,
        is_batch=False,
    )
