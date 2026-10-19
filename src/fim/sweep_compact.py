"""Make a finished sweep smaller after the fact.

A sweep that kept every frame of every point can be cut down once its answers
are in, without changing any reported number: a point's trajectory log can be
tapered (the last generations in full, the past thinned further back), or
dropped altogether, leaving the report, summary, scatter plot, deme-pair
statistics and a decimated statistic history.

`compact_sweep` does this for the points of one sweep Study. It leaves the
points named by `--keep` (the corners of the grid, every Kth point) as they
are, reduces the rest to a target level, rewrites each run's manifest with the
new settings and digests, and refuses a run that another Study also lists
(that Study's results would change too) unless told otherwise. A run already at
or below the target is left alone, so running it twice changes nothing, and a
tapered log is the same file a run that had tapered itself would have written.
The sweep must not be running.
"""

from __future__ import annotations

import os
from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final, Literal

from fim.persistence import groups
from fim.persistence.binary_store import (
    EQUILIBRIUM_LOG_FILENAME,
    TRAJECTORY_LOG_FILENAME,
    log_options,
)
from fim.persistence.history import (
    COMPACT_FILENAME,
    DECIMATED_FILENAME,
    HISTORY_FILENAME,
    history_path,
    read_history_columns,
    replicate_directories,
    write_decimated_history,
)
from fim.persistence.manifest import (
    hash_file,
    read_batch_manifest,
    read_manifest,
    write_batch_manifest,
    write_manifest,
)
from fim.persistence.object_lock import LockUnavailableError, exclusive_lock
from fim.persistence.recording import Recording, recording_of_run
from fim.persistence.retention import tapered_generations
from fim.persistence.taper import taper_log
from fim.sweep_run import (
    SweepLockedError,
    sweep_lock_path,
    sweep_point_results,
    sweep_spec_of,
)

KeepRule = Literal["corners", "none"] | str

TAPERING_KEYS: Final = ("trajectory_keep_last", "trajectory_keep_burn_in")
"""Configuration keys that mean something only for a tapered run."""


@dataclass(frozen=True, slots=True)
class CompactTarget:
    """What compaction reduces a point to.

    Attributes:
        level: `tapered` or `statistics`.
        keep_last: With `tapered`, the last generations kept in full.
        keep_burn_in: With `tapered`, whether burn-in frames are kept.
    """

    level: Literal["tapered", "statistics"] = "statistics"
    keep_last: int = 1000
    keep_burn_in: bool = False

    def __post_init__(self) -> None:
        """Refuse a level compaction cannot produce, or a bad `keep_last`."""
        if self.level not in ("tapered", "statistics"):
            raise ValueError("compaction targets are 'tapered' and 'statistics'")
        if self.keep_last < 1:
            raise ValueError("keep_last must be at least 1")

    def recording(self) -> Recording:
        """Return the recording a run at this target holds."""
        return Recording(
            level=self.level,
            keep_last=self.keep_last,
            keep_burn_in=self.keep_burn_in,
        )


@dataclass(frozen=True, slots=True)
class CompactReport:
    """What a compaction did, or would do.

    Attributes:
        changed: Run ids reduced.
        kept: Run ids left as they were because `keep` named them.
        already_small: Run ids already at or below the target.
        refused: Run ids another Study also lists, with the Studies' ids.
        read_only: Run ids that are shipped, read-only examples.
        bytes_before: Size of the changed runs before.
        bytes_after: Their size after (equal to `bytes_before` for a dry run).
        dry_run: Whether nothing was written.
    """

    changed: tuple[str, ...]
    kept: tuple[str, ...]
    already_small: tuple[str, ...]
    refused: Mapping[str, tuple[str, ...]]
    read_only: tuple[str, ...]
    bytes_before: int
    bytes_after: int
    dry_run: bool


def parse_keep(text: str) -> KeepRule:
    """Check a `--keep` rule: `corners`, `none` or `every=K`.

    Raises:
        ValueError: The rule is none of these.
    """
    if text in {"corners", "none"}:
        return text
    if text.startswith("every="):
        value = text.removeprefix("every=")
        if value.isdigit() and int(value) >= 1:
            return text
    raise ValueError("--keep must be corners, none, or every=K with K at least 1")


def kept_points(
    coordinates: Mapping[str, Mapping[str, Any]],
    axes: Mapping[str, tuple[Any, ...]],
    rule: KeepRule,
) -> set[str]:
    """Return the run ids a `--keep` rule leaves alone.

    Args:
        coordinates: Each finished point's coordinates by run id, in grid order.
        axes: Each axis's values, by key.
        rule: `corners` (every coordinate is its axis's first or last value),
            `every=K` (the 1st, K+1st, ... point) or `none`.
    """
    if rule == "none":
        return set()
    if rule == "corners":
        return {
            run_id
            for run_id, point in coordinates.items()
            if all(
                point.get(key) in {values[0], values[-1]}
                for key, values in axes.items()
            )
        }
    step = int(str(rule).removeprefix("every="))
    return {run_id for index, run_id in enumerate(coordinates) if index % step == 0}


def compact_sweep(
    study_id: str,
    *,
    keep: KeepRule = "none",
    target: CompactTarget | None = None,
    all_studies: bool = False,
    dry_run: bool = False,
    results: Path | None = None,
) -> CompactReport:
    """Reduce the finished points of a sweep Study to a target level.

    Args:
        study_id: A sweep Study that is not running.
        keep: Which points to leave alone (see `kept_points`).
        target: The level to reduce the other points to.
        all_studies: Also reduce a run another Study lists.
        dry_run: Report what would change and write nothing.
        results: Optional results-directory override.

    Returns:
        What changed, or would.

    Raises:
        ValueError: The Study is not a sweep.
        ClosedGroupError: The Study is closed.
        SweepLockedError: A process is running the sweep.
    """
    wanted = target or CompactTarget()
    study = groups.get_study(study_id, results=results)
    groups.refuse_closed(study, "compacted")
    spec = sweep_spec_of(study)
    rows = sweep_point_results(study, results=results)
    axes = {axis.key: axis.values for axis in spec.axes}
    leave = kept_points({row.run_id: row.coordinates for row in rows}, axes, keep)
    try:
        with exclusive_lock(sweep_lock_path(study_id, results=results), blocking=False):
            return _compact(
                study_id, rows, leave, wanted, all_studies, dry_run, results
            )
    except LockUnavailableError:
        raise SweepLockedError(
            f"another process is running sweep {study_id}; stop it before compacting"
        ) from None


def _compact(
    study_id: str,
    rows: Collection[Any],
    leave: set[str],
    target: CompactTarget,
    all_studies: bool,
    dry_run: bool,
    results: Path | None,
) -> CompactReport:
    """Reduce each selected run; the caller holds the sweep lock."""
    changed: list[str] = []
    kept: list[str] = []
    small: list[str] = []
    refused: dict[str, tuple[str, ...]] = {}
    read_only: list[str] = []
    before = after = 0
    for row in rows:
        directory: Path = row.directory
        if row.run_id in leave:
            kept.append(row.run_id)
        elif groups.is_run_read_only(directory):
            read_only.append(row.run_id)
        elif target.recording().satisfies(recording_of_run(directory)):
            small.append(row.run_id)
        else:
            others = tuple(
                other.study_id
                for other in groups.studies_containing_run(directory, results=results)
                if other.study_id != study_id
            )
            if others and not all_studies:
                refused[row.run_id] = others
                continue
            size = _tree_bytes(directory)
            before += size
            if not dry_run:
                compact_run(directory, target)
            after += size if dry_run else _tree_bytes(directory)
            changed.append(row.run_id)
    return CompactReport(
        changed=tuple(changed),
        kept=tuple(kept),
        already_small=tuple(small),
        refused=refused,
        read_only=tuple(read_only),
        bytes_before=before,
        bytes_after=after,
        dry_run=dry_run,
    )


def compact_run(directory: Path, target: CompactTarget) -> None:
    """Reduce one finished run (a single run or a batch) to `target`.

    The files are changed first and the manifests last, so a run that is
    stopped halfway has a manifest that still names what it had; running
    compaction again finishes the job.

    Args:
        directory: The run directory.
        target: The level to reduce it to.
    """
    batch = (directory / "manifest.json").is_file() and (
        replicate_directories(directory) != [directory]
    )
    folders = replicate_directories(directory)
    for folder in folders:
        _compact_replicate(folder, target)
    if batch:
        manifest = read_batch_manifest(directory / "manifest.json")
        digests = dict(manifest.artifacts or {})
        for folder in folders:
            digests[folder.name] = hash_file(folder / "manifest.json")
        write_batch_manifest(
            directory / "manifest.json",
            replace(
                manifest,
                parameters=_parameters(manifest.parameters, target),
                artifacts=digests,
            ),
        )


def _compact_replicate(folder: Path, target: CompactTarget) -> None:
    """Reduce one replicate (or a single run) and rewrite its manifest."""
    manifest = read_manifest(folder / "manifest.json")
    if target.recording().satisfies(recording_of_run(folder)):
        return
    artifacts = dict(manifest.artifacts or {})
    trajectory = folder / TRAJECTORY_LOG_FILENAME
    if target.level == "tapered":
        if recording_of_run(folder).level == "thinned":
            raise ValueError(
                f"{folder.name} was thinned, so it lacks frames a tapered log "
                "keeps; reduce it to statistics instead"
            )
        parameters = manifest.params()
        taper_log(
            trajectory,
            tapered_generations(
                manifest.generation,
                target.keep_last,
                parameters.convergence_burn_in,
                keep_burn_in=target.keep_burn_in,
            ),
            options=log_options(parameters.expert),
        )
        artifacts["trajectory"] = hash_file(trajectory)
        stale: list[Path] = []
    else:
        source = history_path(folder)
        decimated = folder / DECIMATED_FILENAME
        if source.is_file() and source != decimated:
            generations, columns = read_history_columns(source)
            write_decimated_history(decimated, generations, columns)
        artifacts.pop("trajectory", None)
        artifacts.pop("equilibrium_trajectory", None)
        artifacts.pop("convergence", None)
        artifacts["history"] = hash_file(decimated)
        stale = [
            trajectory,
            folder / EQUILIBRIUM_LOG_FILENAME,
            folder / HISTORY_FILENAME,
            folder / COMPACT_FILENAME,
        ]
    write_manifest(
        folder / "manifest.json",
        replace(
            manifest,
            parameters=_parameters(manifest.parameters, target),
            artifacts=artifacts,
        ),
    )
    for path in stale:
        path.unlink(missing_ok=True)


def _parameters(parameters: Mapping[str, Any], target: CompactTarget) -> dict[str, Any]:
    """Return a manifest's parameters with the recording settings of `target`."""
    changed = {
        key: value for key, value in parameters.items() if key not in TAPERING_KEYS
    }
    changed["trajectory_retention"] = target.level
    if target.level == "tapered":
        changed["trajectory_keep_last"] = target.keep_last
        changed["trajectory_keep_burn_in"] = target.keep_burn_in
    return changed


def _tree_bytes(path: Path) -> int:
    """Return the total size of the files under `path` (0 if it is gone)."""
    total = 0
    for folder, _names, files in os.walk(path):
        for name in files:
            try:
                total += (Path(folder) / name).stat().st_size
            except OSError:
                continue
    return total
