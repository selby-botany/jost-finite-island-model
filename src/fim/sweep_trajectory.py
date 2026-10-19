"""How each point of a sweep approached equilibrium, in a fixed small size.

A sweep's results card shows where each point ended up. This module answers
the other question the botanist asks: *how* it got there, and how fast.
Every replicate keeps a statistic history, one row per generation, which for
a long run is hundreds of megabytes across a sweep. A **point trajectory**
reduces one point's replicates to at most `MAX_SAMPLES` generations, each
with the mean across the replicates still running, the interval of that
mean, the spread, and how many replicates contribute, so the overlay of
all points is a few thousand numbers however long the runs were.

The digest is a *view*, not a record: it is computed on demand by streaming
each replicate's history once (bounded memory), and cached beside the sweep's
other disposable status files, keyed by the history files' own checksums so a
changed run recomputes. Deleting the cache loses nothing.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from fim.persistence.history import (
    history_path,
    iter_history_rows,
    replicate_directories,
    sample_history,
)
from fim.statistics.interval import confidence_interval

if TYPE_CHECKING:
    from fim.sweep_table import ResultTable, TablePoint

MAX_SAMPLES: Final = 512
"""Most generations in one point trajectory."""

QUORUM_FRACTION: Final = 0.5
"""A sample drawn from fewer than this fraction of a point's replicates is
drawn dashed: the others have stopped, so the interval is less sure."""

CACHE_SCHEMA: Final = 1
"""Version of the cached trajectory file; a different one recomputes."""


@dataclass(frozen=True, slots=True)
class TrajectorySeries:
    """One statistic's samples across a point's replicates.

    Every tuple has one entry per sampled generation of the trajectory.

    Attributes:
        mean: Mean over the replicates still running at that generation.
        low: Lower end of the interval of that mean (the mean itself when
            only one replicate contributes).
        high: Upper end of the interval.
        std: Standard deviation across those replicates (0 for one).
        count: How many replicates contribute at that generation.
    """

    mean: tuple[float, ...]
    low: tuple[float, ...]
    high: tuple[float, ...]
    std: tuple[float, ...]
    count: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PointTrajectory:
    """A point's replicates reduced to a fixed small number of generations.

    Attributes:
        run_id: The point's run id.
        generations: The sampled generations, ascending, starting at 0.
        statistics: Each statistic's series, by catalog key.
        replicates: How many replicates the point has.
        burn_in: The last burn-in generation, or `None` if the run has no
            fixed one.
        confidence: The confidence level of the intervals.
    """

    run_id: str
    generations: tuple[int, ...]
    statistics: Mapping[str, TrajectorySeries]
    replicates: int
    burn_in: int | None
    confidence: float

    def has_quorum(self, position: int, statistic: str) -> bool:
        """Return whether enough replicates contribute at a sampled position.

        Args:
            position: Index into `generations`.
            statistic: A statistic in `statistics`.
        """
        needed = math.ceil(self.replicates * QUORUM_FRACTION)
        return self.statistics[statistic].count[position] >= needed

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-ready form, the cache's file content."""
        return {
            "schema": CACHE_SCHEMA,
            "run_id": self.run_id,
            "generations": list(self.generations),
            "replicates": self.replicates,
            "burn_in": self.burn_in,
            "confidence": self.confidence,
            "statistics": {
                name: {
                    "mean": list(series.mean),
                    "low": list(series.low),
                    "high": list(series.high),
                    "std": list(series.std),
                    "count": list(series.count),
                }
                for name, series in self.statistics.items()
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PointTrajectory:
        """Rebuild a trajectory from `to_dict`.

        Raises:
            ValueError: The mapping is not a current trajectory.
        """
        if value.get("schema") != CACHE_SCHEMA:
            raise ValueError("unsupported trajectory schema")
        try:
            return cls(
                run_id=str(value["run_id"]),
                generations=tuple(int(g) for g in value["generations"]),
                statistics={
                    str(name): TrajectorySeries(
                        mean=tuple(float(x) for x in series["mean"]),
                        low=tuple(float(x) for x in series["low"]),
                        high=tuple(float(x) for x in series["high"]),
                        std=tuple(float(x) for x in series["std"]),
                        count=tuple(int(x) for x in series["count"]),
                    )
                    for name, series in value["statistics"].items()
                },
                replicates=int(value["replicates"]),
                burn_in=None if value["burn_in"] is None else int(value["burn_in"]),
                confidence=float(value["confidence"]),
            )
        except (KeyError, TypeError) as error:
            raise ValueError(f"malformed trajectory: {error}") from error


def trajectory_grid(final: int, maximum: int = MAX_SAMPLES) -> tuple[int, ...]:
    """Return the generations a trajectory samples, for a run ending at `final`.

    Generation 0, then a union of linear and logarithmic spacing up to
    `final` (logarithmic spacing shows the early transient, linear the
    plateau), de-duplicated and never more than `maximum` entries. A run
    short enough to fit is sampled at every generation.

    Args:
        final: The longest replicate's final generation.
        maximum: The most samples to return, at least 2.

    Raises:
        ValueError: `maximum` is below 2.
    """
    if maximum < 2:  # noqa: PLR2004 - the grid needs both ends
        raise ValueError("a trajectory needs at least two samples")
    if final <= 0:
        return (0,)
    if final + 1 <= maximum:
        return tuple(range(final + 1))
    linear = (maximum - 1) // 2
    logarithmic = maximum - 1 - linear
    chosen = {0, final}
    chosen.update(round(final * step / linear) for step in range(1, linear + 1))
    chosen.update(
        round(final ** (step / logarithmic)) for step in range(1, logarithmic + 1)
    )
    return tuple(sorted(chosen))


def point_trajectory(
    run_directory: Path,
    run_id: str,
    *,
    cache: TrajectoryCache | None = None,
    maximum: int = MAX_SAMPLES,
) -> PointTrajectory | None:
    """Reduce a point's replicates to a trajectory, from the cache if it holds one.

    Args:
        run_directory: The point's run directory.
        run_id: The point's run id.
        cache: Where finished trajectories are kept, or `None` for none.
        maximum: The most generations to sample.

    Returns:
        The trajectory, or `None` when no replicate has a readable history
        (a run that predates the history file, or one kept without it).
    """
    folders = replicate_directories(run_directory)
    manifests = [_read_manifest(folder) for folder in folders]
    key = _cache_key(folders, manifests, maximum)
    if cache is not None and key is not None:
        cached = cache.get(run_id, key)
        if cached is not None:
            return cached
    finals = [
        _final_generation(folder, manifest)
        for folder, manifest in zip(folders, manifests, strict=True)
    ]
    known = [final for final in finals if final is not None]
    if not known:
        return None
    grid = trajectory_grid(max(known), maximum)
    sampled = [
        sample_history(history_path(folder), grid)
        for folder in folders
        if history_path(folder).is_file()
    ]
    if not sampled:
        return None
    confidence = _confidence_of(manifests)
    found = PointTrajectory(
        run_id=run_id,
        generations=grid,
        statistics=_combine(sampled, len(grid), confidence),
        replicates=len(folders),
        burn_in=_burn_in_of(manifests),
        confidence=confidence,
    )
    if cache is not None and key is not None:
        cache.put(run_id, key, found)
    return found


class TrajectoryCache:
    """A disposable cache of point trajectories, one JSON file per run id.

    Args:
        directory: Where the files live; created on the first write.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def get(self, run_id: str, key: str) -> PointTrajectory | None:
        """Return the cached trajectory for `run_id` if it was made with `key`."""
        try:
            stored = json.loads(self._path(run_id).read_text(encoding="utf-8"))
            if stored.get("key") != key:
                return None
            return PointTrajectory.from_dict(stored["trajectory"])
        except (OSError, ValueError, KeyError, AttributeError):
            return None

    def put(self, run_id: str, key: str, trajectory: PointTrajectory) -> None:
        """Store a trajectory; a cache that cannot be written is ignored."""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            payload = {"key": key, "trajectory": trajectory.to_dict()}
            temporary = self._path(run_id).with_suffix(".tmp")
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            temporary.replace(self._path(run_id))
        except OSError:
            return

    def _path(self, run_id: str) -> Path:
        return self.directory / f"{run_id}.json"


@dataclass(frozen=True, slots=True)
class TrajectoryEntry:
    """One point's trajectory with the point it belongs to."""

    point: TablePoint
    trajectory: PointTrajectory


@dataclass(frozen=True, slots=True)
class TrajectoryCurve:
    """The trajectories of the points that share a value of the `by` axis.

    Attributes:
        by_value: The value the curves are split by, or `None` for one group.
        entries: The points' trajectories, in the sweep's point order.
    """

    by_value: float | int | str | None
    entries: tuple[TrajectoryEntry, ...]


def table_trajectories(
    table: ResultTable,
    *,
    by: str | None = None,
    held: Mapping[str, float | int | str] | None = None,
    cache: TrajectoryCache | None = None,
) -> list[TrajectoryCurve]:
    """Read the trajectories of a sweep's finished points, grouped for an overlay.

    Args:
        table: The sweep's results.
        by: The swept key whose values make one group each, or `None`.
        held: A value for every other swept key (so one point per group
            value remains); omitted keys keep every value.
        cache: Where finished trajectories are kept.

    Returns:
        One group per value of `by` (or one group), in the key's own order;
        a finished point with no readable history is left out.

    Raises:
        ValueError: `by` or a held key is not an axis of this sweep.
    """
    axes = {axis.key: axis.values for axis in table.spec.axes}
    for key in ([by] if by is not None else []) + list(held or {}):
        if key not in axes:
            raise ValueError(f"{key!r} is not an axis of this sweep")
    fixed = dict(held or {})
    groups: Sequence[Any] = axes[by] if by is not None else (None,)
    curves = []
    for value in groups:
        wanted = dict(fixed)
        if by is not None:
            wanted[by] = value
        entries = []
        for point in table.points:
            if point.row is None or any(
                point.coordinates.get(key) != want for key, want in wanted.items()
            ):
                continue
            found = point_trajectory(point.row.directory, point.run_id, cache=cache)
            if found is not None:
                entries.append(TrajectoryEntry(point, found))
        curves.append(TrajectoryCurve(value, tuple(entries)))
    return curves


def write_trajectories_csv(
    table: ResultTable,
    statistic: str,
    path: Path,
    *,
    cache: TrajectoryCache | None = None,
) -> int:
    """Write every finished point's sampled trajectory of one statistic as CSV.

    Long format, one row per point and sampled generation: the sweep id, the
    point's index and coordinates, its run id, the generation, the mean over
    the replicates still running, the interval of the mean, the spread, how
    many replicates contribute, whether that is a quorum, and the point's
    health.

    Args:
        table: The sweep's results.
        statistic: A statistic's catalog key.
        path: The file to write (replaced atomically).
        cache: Where finished trajectories are kept.

    Returns:
        The number of rows written; 0 (and no file) when no finished point has
        a history of `statistic`.
    """
    keys = table.axis_keys
    header = [
        "sweep_id",
        "point_index",
        *keys,
        "run_id",
        "statistic",
        "generation",
        "mean",
        "ci_low",
        "ci_high",
        "sample_std",
        "count",
        "replicates",
        "quorum",
        "health",
    ]
    rows: list[list[object]] = []
    for curve in table_trajectories(table, cache=cache):
        for entry in curve.entries:
            series = entry.trajectory.statistics.get(statistic)
            if series is None:
                continue
            row = entry.point.row
            health = None if row is None or row.health is None else row.health
            label = "" if health is None else health.classify(table.policy)
            for position, generation in enumerate(entry.trajectory.generations):
                rows.append(
                    [
                        table.study_id,
                        entry.point.index,
                        *(entry.point.coordinates.get(key, "") for key in keys),
                        entry.point.run_id,
                        statistic,
                        generation,
                        series.mean[position],
                        series.low[position],
                        series.high[position],
                        series.std[position],
                        series.count[position],
                        entry.trajectory.replicates,
                        "yes"
                        if entry.trajectory.has_quorum(position, statistic)
                        else "no",
                        label,
                    ]
                )
    if not rows:
        return 0
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    temporary.replace(path)
    return len(rows)


def _read_manifest(folder: Path) -> Mapping[str, Any]:
    """Return a replicate's manifest, or an empty mapping if unreadable."""
    try:
        loaded = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _cache_key(
    folders: Sequence[Path], manifests: Sequence[Mapping[str, Any]], maximum: int
) -> str | None:
    """Return what a cached trajectory must match, or `None` if not cacheable.

    The key is the replicates' recorded history checksums (so a changed run
    recomputes) and the sample cap; a replicate whose manifest records no
    checksum makes the point uncacheable.
    """
    digests = []
    for manifest in manifests:
        artifacts = manifest.get("artifacts")
        entry = artifacts.get("convergence") if isinstance(artifacts, dict) else None
        digest = entry.get("sha256") if isinstance(entry, dict) else None
        if not isinstance(digest, str):
            return None
        digests.append(digest)
    text = json.dumps([len(folders), maximum, digests])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _final_generation(folder: Path, manifest: Mapping[str, Any]) -> int | None:
    """Return a replicate's last generation, from its manifest or its history."""
    convergence = manifest.get("convergence")
    generation = (
        convergence.get("generation") if isinstance(convergence, dict) else None
    )
    if isinstance(generation, int) and not isinstance(generation, bool):
        return generation
    path = history_path(folder)
    if not path.is_file():
        return None
    last: int | None = None
    for generation, _ in iter_history_rows(path):
        last = generation
    return last


def _combine(
    sampled: Sequence[Mapping[str, Sequence[float | None]]],
    length: int,
    confidence: float,
) -> dict[str, TrajectorySeries]:
    """Combine the replicates' samples into one series per statistic."""
    names = sorted({name for replicate in sampled for name in replicate})
    combined: dict[str, TrajectorySeries] = {}
    for name in names:
        mean, low, high, std, count = [], [], [], [], []
        for position in range(length):
            values = [
                column[position]
                for replicate in sampled
                if (column := replicate.get(name)) is not None
                and column[position] is not None
            ]
            present = [v for v in values if v is not None]
            if not present:
                mean.append(math.nan)
                low.append(math.nan)
                high.append(math.nan)
                std.append(math.nan)
                count.append(0)
                continue
            interval = _interval(present, confidence)
            mean.append(interval[0])
            low.append(interval[1])
            high.append(interval[2])
            std.append(interval[3])
            count.append(len(present))
        combined[name] = TrajectorySeries(
            tuple(mean), tuple(low), tuple(high), tuple(std), tuple(count)
        )
    return combined


def _interval(
    values: Sequence[float], confidence: float
) -> tuple[float, float, float, float]:
    """Return `(mean, low, high, std)`; one value has no interval and no spread."""
    if len(values) < 2:  # noqa: PLR2004 - an interval needs two values
        return values[0], values[0], values[0], 0.0
    try:
        result = confidence_interval(values, confidence=confidence)
    except ValueError:  # a level the t table lacks, or a non-finite value
        result = confidence_interval(values)
    spread = result["sample_std"]
    return (
        result["mean"],
        result["low"],
        result["high"],
        0.0 if spread is None else spread,
    )


def _confidence_of(manifests: Sequence[Mapping[str, Any]]) -> float:
    """Return the confidence level the point was run at (0.95 if unrecorded)."""
    for manifest in manifests:
        parameters = manifest.get("parameters")
        level = parameters.get("confidence") if isinstance(parameters, dict) else None
        if isinstance(level, int | float) and not isinstance(level, bool):
            return float(level)
    return 0.95


def _burn_in_of(manifests: Sequence[Mapping[str, Any]]) -> int | None:
    """Return the run's burn-in generation if it recorded one."""
    for manifest in manifests:
        parameters = manifest.get("parameters")
        value = (
            parameters.get("convergence_burn_in")
            if isinstance(parameters, dict)
            else None
        )
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None
