"""What a sweep's points produced: their statistics and how far to trust them.

A `PointRow` is one point of a sweep Study read back from its run: the
statistic of each kind with its interval (`StatisticCell`) and whether the
point's replicates settled (`PointHealth`). Everything is derived from the
small JSON files a run already wrote, so nothing here changes what a run
stores, and runs that already exist (committed examples included) read the
same way.

A replicate that reaches the generation cap before its statistics settle
still finishes, and its value may be biased. Health is how a long sweep says
which points to trust; a point that is not healthy is flagged, never
dropped (botanist question Q6).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from fim.convergence.monitor import StopReason

if TYPE_CHECKING:
    from fim.sweep import CoordinateValue
    from fim.sweep_run import PointState

logger = logging.getLogger(__name__)

HealthClass = Literal["healthy", "degraded", "unreliable"]
"""How far to trust a point: `healthy` (every replicate settled, within the
requested precision), `degraded` (some capped or unsettled, or wider than the
precision), or `unreliable` (every replicate hit the generation cap)."""


@dataclass(frozen=True, slots=True)
class StatisticCell:
    """One statistic of one point, with its across-replicate interval.

    Attributes:
        mean: The mean over replicates (the value itself for a single run).
        low: Lower end of the confidence interval, or `None` for a single run.
        high: Upper end, or `None` for a single run.
        half_width: Half the interval's width, or `None`.
        sample_std: Standard deviation across replicates, or `None`.
        sample_count: Replicates that had a defined value.
        confidence: The interval's confidence level, or `None`.
    """

    mean: float
    low: float | None = None
    high: float | None = None
    half_width: float | None = None
    sample_std: float | None = None
    sample_count: int = 1
    confidence: float | None = None

    @classmethod
    def from_summary_entry(cls, entry: Mapping[str, Any]) -> StatisticCell:
        """Build a cell from one statistic's entry in a batch `summary.json`."""

        def number(key: str) -> float | None:
            value = entry.get(key)
            return float(value) if isinstance(value, int | float) else None

        return cls(
            mean=float(entry["mean"]),
            low=number("low"),
            high=number("high"),
            half_width=number("half_width"),
            sample_std=number("sample_std"),
            sample_count=int(entry.get("sample_count", 0)),
            confidence=number("confidence"),
        )


@dataclass(frozen=True, slots=True)
class HealthPolicy:
    """The thresholds that turn replicate outcomes into a `HealthClass`.

    Attributes:
        precision_slack: A watched statistic's achieved half width may be up
            to this multiple of the run's `precision` before the point counts
            as degraded.
    """

    precision_slack: float = 1.0


DEFAULT_HEALTH_POLICY = HealthPolicy()
"""The policy used unless a caller passes another."""


@dataclass(frozen=True, slots=True)
class PointHealth:
    """How a point's replicates ended.

    Attributes:
        replicates: Replicates the point has.
        converged: Replicates that settled (or averaged their whole window).
        hit_cap: Replicates that stopped at the generation cap.
        other: Replicates that ended some other way (cancelled, interrupted).
        achieved_half_width: The interval half width of each watched
            statistic, from `summary.json` (empty for a single run).
        precision: The `precision` the run asked for, or `None` if unknown.
    """

    replicates: int
    converged: int
    hit_cap: int
    other: int
    achieved_half_width: Mapping[str, float] = field(default_factory=dict)
    precision: float | None = None

    def classify(self, policy: HealthPolicy = DEFAULT_HEALTH_POLICY) -> HealthClass:
        """Return how far to trust this point under `policy`."""
        if self.replicates in (0, self.hit_cap):
            return "unreliable"
        if self.hit_cap or self.other or self._wider_than_precision(policy):
            return "degraded"
        return "healthy"

    def to_dict(self) -> dict[str, object]:
        """Return the health as plain values, for JSON."""
        return {
            "replicates": self.replicates,
            "converged": self.converged,
            "hit_cap": self.hit_cap,
            "other": self.other,
            "achieved_half_width": dict(self.achieved_half_width),
            "precision": self.precision,
        }

    def _wider_than_precision(self, policy: HealthPolicy) -> bool:
        """Whether any watched statistic's interval is wider than asked for."""
        if self.precision is None:
            return False
        limit = self.precision * policy.precision_slack
        return any(width > limit for width in self.achieved_half_width.values())


@dataclass(frozen=True, slots=True)
class PointRow:
    """One point of a sweep, read back from its run.

    Attributes:
        index: The point's grid index.
        coordinates: The varied values, by axis key.
        run_id: The point's id.
        state: `done`, or `stale` for a run made by another software version.
        directory: The point's run directory.
        n_replicates: Replicates the run has (1 for a scalar run).
        seed: The run's seed (a batch's base seed).
        software_version: The version that made the run.
        engine_backend: The backend that ran it, if recorded.
        started_at: When it started (UTC text), if recorded.
        ended_at: When it ended, if recorded.
        statistics: Each statistic's cell.
        health: How the replicates ended, or `None` if it cannot be read.
        parameters: The run's resolved configuration.
    """

    index: int
    coordinates: Mapping[str, CoordinateValue]
    run_id: str
    state: PointState
    directory: Path
    n_replicates: int
    seed: int | None
    software_version: str | None
    engine_backend: str | None
    started_at: str | None
    ended_at: str | None
    statistics: Mapping[str, StatisticCell]
    health: PointHealth | None
    parameters: Mapping[str, object]


class HealthCache:
    """Disposable cache of the replicate outcome counts of a sweep's points.

    Counting outcomes opens one small manifest per replicate, which adds up
    over hundreds of points. The counts are kept beside the sweep's status
    file, keyed by run id and the digest of the run's own manifest, so a
    changed run is read again. Deleting the file loses nothing.

    Args:
        path: The cache file.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._entries: dict[str, dict[str, Any]] = self._load()
        self._dirty = False

    def get(
        self, run_id: str, digest: str
    ) -> tuple[int, int, int, tuple[str, ...]] | None:
        """Return `(converged, hit_cap, other, watched)` if cached for `digest`."""
        entry = self._entries.get(run_id)
        if not isinstance(entry, dict) or entry.get("digest") != digest:
            return None
        counts, watched = entry.get("counts"), entry.get("watched")
        if not isinstance(counts, list) or not isinstance(watched, list):
            return None
        try:
            converged, hit_cap, other = counts
        except ValueError:
            return None
        if not all(isinstance(value, int) for value in (converged, hit_cap, other)):
            return None
        return converged, hit_cap, other, tuple(str(name) for name in watched)

    def put(
        self,
        run_id: str,
        digest: str,
        counts: tuple[int, int, int],
        watched: tuple[str, ...],
    ) -> None:
        """Remember a point's counts."""
        self._entries[run_id] = {
            "digest": digest,
            "counts": list(counts),
            "watched": list(watched),
        }
        self._dirty = True

    def save(self) -> None:
        """Write the cache if it changed; a failure is logged, never raised."""
        if not self._dirty:
            return
        temporary = self._path.with_name(self._path.name + f".{os.getpid()}.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(self._entries, sort_keys=True), encoding="utf-8"
            )
            temporary.replace(self._path)
        except OSError as error:
            logger.debug("could not write the health cache %s: %s", self._path, error)
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)
        else:
            self._dirty = False

    def _load(self) -> dict[str, dict[str, Any]]:
        """Read the cache file; a missing or damaged file is an empty cache."""
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return loaded if isinstance(loaded, dict) else {}


def read_point_health(
    directory: Path, *, cache: HealthCache | None = None
) -> PointHealth | None:
    """Return how a finished point's replicates ended, or `None` if unreadable.

    The outcome counts come from each replicate's `manifest.json` (a scalar
    run's own manifest): a replicate `converged` if its monitor said so, and
    `hit_cap` if it stopped for the generation cap. The achieved half widths
    come from `summary.json`, for the statistics the monitor watched.

    Args:
        directory: The point's run directory.
        cache: Where to keep the outcome counts between reads.

    Returns:
        The point's health, or `None` if its manifests cannot be read.
    """
    try:
        manifest_bytes = (directory / "manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes)
    except (OSError, ValueError):
        return None
    if not isinstance(manifest, dict):
        return None
    run_id = str(manifest.get("run_id", directory.name))
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    cached = cache.get(run_id, digest) if cache is not None else None
    if cached is None:
        outcomes = _replicate_outcomes(directory, manifest)
        if outcomes is None:
            return None
        converged, hit_cap, other, watched = outcomes
        if cache is not None:
            cache.put(run_id, digest, (converged, hit_cap, other), watched)
    else:
        converged, hit_cap, other, watched = cached
    parameters = manifest.get("parameters")
    precision = parameters.get("precision") if isinstance(parameters, dict) else None
    return PointHealth(
        replicates=converged + hit_cap + other,
        converged=converged,
        hit_cap=hit_cap,
        other=other,
        achieved_half_width=_achieved_half_widths(directory, watched),
        precision=float(precision) if isinstance(precision, int | float) else None,
    )


def _achieved_half_widths(
    directory: Path, watched: tuple[str, ...]
) -> dict[str, float]:
    """Return the interval half width of each watched statistic of a batch."""
    try:
        summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    widths: dict[str, float] = {}
    for name in watched:
        entry = summary.get(name) if isinstance(summary, dict) else None
        width = entry.get("half_width") if isinstance(entry, dict) else None
        if isinstance(width, int | float):
            widths[name] = float(width)
    return widths


def _outcome_of(manifest: Mapping[str, Any]) -> tuple[str, tuple[str, ...]] | None:
    """Classify a manifest's convergence record: `converged`, `hit_cap`, `other`."""
    convergence = manifest.get("convergence")
    if not isinstance(convergence, dict):
        return None
    statistic = convergence.get("statistic")
    watched = (
        tuple(str(name) for name in statistic)
        if isinstance(statistic, list)
        else (statistic,)
        if isinstance(statistic, str)
        else ()
    )
    if convergence.get("converged") is True:
        return "converged", watched
    if convergence.get("reason") == StopReason.MAX_GENERATIONS.value:
        return "hit_cap", watched
    return "other", watched


def _replicate_outcomes(
    directory: Path, manifest: Mapping[str, Any]
) -> tuple[int, int, int, tuple[str, ...]] | None:
    """Count how the replicates of a run ended, reading their manifests."""
    sources: list[Mapping[str, Any]] = []
    for path in sorted(directory.glob("replicate-*/manifest.json")):
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(loaded, dict):
            return None
        sources.append(loaded)
    if not sources:
        sources.append(manifest)
    counts = {"converged": 0, "hit_cap": 0, "other": 0}
    watched: dict[str, None] = {}
    for source in sources:
        outcome = _outcome_of(source)
        if outcome is None:
            return None
        kind, names = outcome
        counts[kind] += 1
        watched.update(dict.fromkeys(names))
    return counts["converged"], counts["hit_cap"], counts["other"], tuple(watched)
