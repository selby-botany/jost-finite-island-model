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

`ResultTable` is the whole sweep as data: one row per point and statistic
(the tidy long form that R and Python group-by tools expect), or one row per
point on request, written as CSV or JSON Lines with a sidecar file that says
how to read it.
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import json
import logging
import os
import platform
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal

from fim import __version__
from fim.convergence.monitor import StopReason
from fim.statistics.catalog import spec as statistic_spec
from fim.statistics.theory import THEORY_STATISTICS, theory_at

if TYPE_CHECKING:
    from fim.sweep import CoordinateValue, SweepSpec
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

    def describe(self, policy: HealthPolicy = DEFAULT_HEALTH_POLICY) -> str:
        """Say in words why a point is not healthy; empty for a healthy one."""
        if self.classify(policy) == "healthy":
            return ""
        reasons: list[str] = []
        if self.hit_cap:
            reasons.append(
                f"{self.hit_cap} of {self.replicates} replicates stopped at the "
                "generation limit before settling"
            )
        if self.other:
            reasons.append(f"{self.other} ended another way")
        if self._wider_than_precision(policy):
            reasons.append("the interval is wider than the precision asked for")
        return "; ".join(reasons) or "no replicates"

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


DEFAULT_EXPORT_STATISTICS: Final = ("D", "G_ST")
"""The statistics an export writes unless told otherwise (botanist question
Q1: `D` and `G_ST` first, every statistic selectable)."""

ALL_STATISTICS: Final = "all"
"""Pass as `statistics` to write every statistic a sweep has."""

_HEAD_COLUMNS: Final = ("sweep_id", "point_index", "state")
_TAIL_COLUMNS: Final = (
    "run_id",
    "seed",
    "statistic",
    "mean",
    "ci_low",
    "ci_high",
    "ci_half_width",
    "ci_level",
    "sample_std",
    "sample_count",
    "replicates",
    "replicates_converged",
    "replicates_hit_cap",
    "health",
    "achieved_half_width",
    "software_version",
    "engine_backend",
)
_WIDE_POINT_COLUMNS: Final = (
    "run_id",
    "seed",
    "replicates",
    "replicates_converged",
    "replicates_hit_cap",
    "health",
    "software_version",
    "engine_backend",
)
_WIDE_STATISTIC_SUFFIXES: Final = (
    ("mean", "mean"),
    ("ci_low", "low"),
    ("ci_high", "high"),
    ("ci_half_width", "half_width"),
    ("sample_std", "sample_std"),
)

COLUMN_DESCRIPTIONS: Final[Mapping[str, str]] = {
    "sweep_id": "The sweep's Study id.",
    "point_index": "The point's position in the full grid, first axis slowest.",
    "state": "done, or the point's state if it has no result (waiting, failed, ...).",
    "gene_copies_per_deme": "N times the ploidy; the closed forms use gene copies.",
    "run_id": "The point's run id, a hash of its whole configuration.",
    "seed": "The run's seed (a batch's base seed).",
    "statistic": "The statistic's catalog key.",
    "mean": "Mean over replicates (the value itself for a single run).",
    "ci_low": "Lower end of the confidence interval of the mean; empty for one run.",
    "ci_high": "Upper end of the confidence interval of the mean; empty for one run.",
    "ci_half_width": "Half the interval's width; empty for one run.",
    "ci_level": "The interval's confidence level, a fraction.",
    "sample_std": "Standard deviation across replicates; empty for one run.",
    "sample_count": "Replicates that had a defined value of the statistic.",
    "replicates": "Replicates the point ran.",
    "replicates_converged": "Replicates whose watched statistics settled.",
    "replicates_hit_cap": "Replicates that stopped at the generation cap.",
    "health": "healthy, degraded or unreliable; a flagged point is never dropped.",
    "achieved_half_width": "Interval half width of a watched statistic; empty if not.",
    "software_version": "The fim version that made the run.",
    "engine_backend": "The engine backend that ran the point.",
    "theory": "Closed-form prediction at equilibrium; empty where there is none.",
    "residual": "mean minus theory; empty where there is no prediction.",
    "residual_relative": "residual divided by theory; empty if theory is 0 or absent.",
}
"""What each exported column means, for the sidecar and for readers."""

AXIS_DESCRIPTIONS: Final[Mapping[str, str]] = {
    "N": "Individuals per deme (the swept value of N).",
    "d": "Number of demes.",
    "m": "Migration rate per generation.",
    "mu": "Mutation rate per generation.",
    "topology": "Migration topology (island, ring, torus, ...).",
}
"""What a swept configuration key means, for the sidecar's column dictionary."""

Cell = float | int | str | None


def _text(value: object) -> str:
    """Format one value for CSV: shortest round-trip floats, empty for `None`."""
    if value is None:
        return ""
    return repr(value) if isinstance(value, float) else str(value)


def _gene_copies(spec: SweepSpec, coordinates: Mapping[str, Any]) -> int | None:
    """Return `N` gene copies per deme at a point, or `None` if it cannot be told."""
    from fim.model.params import PLOIDY_WORDS  # noqa: PLC0415

    ploidy = next(
        (n for n, word in PLOIDY_WORDS.items() if word == spec.base.get("ploidy")), None
    )
    size = coordinates.get("N", spec.base.get("N"))
    if ploidy is None or isinstance(size, bool) or not isinstance(size, int | float):
        return None
    return int(size) * ploidy


@dataclass(frozen=True, slots=True)
class TablePoint:
    """One planned point of a sweep and, if it has finished, its row.

    Attributes:
        index: The point's grid index.
        coordinates: The varied values, by axis key.
        run_id: The point's id.
        state: The point's state (`done`, `stale`, `failed`, `waiting`, ...).
        row: Its result, or `None` if the point has none.
    """

    index: int
    coordinates: Mapping[str, CoordinateValue]
    run_id: str
    state: str
    row: PointRow | None


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    """One finished point on a response curve.

    Attributes:
        x: The point's value on the curve's x axis.
        point_index: The point's grid index.
        run_id: The point's id.
        directory: The point's run directory.
        mean: The statistic's mean over replicates.
        ci_low: Lower end of the confidence interval of the mean, or `None`.
        ci_high: Upper end of the confidence interval, or `None`.
        sd_low: `mean` minus the standard deviation across replicates, or `None`.
        sd_high: `mean` plus the standard deviation, or `None`.
        sample_count: Replicates that had a defined value.
        replicates: Replicates the point ran.
        health: How far to trust the point, or `None` if unknown.
        health_reason: Why the point is flagged; empty if it is not.
    """

    x: CoordinateValue
    point_index: int
    run_id: str
    directory: Path
    mean: float
    ci_low: float | None
    ci_high: float | None
    sd_low: float | None
    sd_high: float | None
    sample_count: int
    replicates: int
    health: HealthClass | None
    health_reason: str


@dataclass(frozen=True, slots=True)
class Series:
    """One curve of a family: the points that share a value of the `by` axis.

    Attributes:
        by_value: The `by` axis value this curve holds, or `None` for a
            single curve.
        points: The finished points, in the x axis's own order.
    """

    by_value: CoordinateValue | None
    points: tuple[SeriesPoint, ...]


class ResultTable:
    """An immutable view of a sweep's points, ready to write as data.

    Args:
        study_id: The sweep Study's id.
        name: The Study's name.
        spec: The sweep specification.
        points: Every planned point, in grid order.
        policy: The thresholds that classify a point's health.
        plan_digest: The experiment's fingerprint (`SweepSpec.digest`), or
            `None` if not known.
        verification: What the last full re-run found (see
            `fim.sweep_run.read_verification`), or `None` if there was none.
    """

    def __init__(
        self,
        study_id: str,
        name: str,
        spec: SweepSpec,
        points: Sequence[TablePoint],
        policy: HealthPolicy = DEFAULT_HEALTH_POLICY,
        *,
        plan_digest: str | None = None,
        verification: Mapping[str, Any] | None = None,
    ) -> None:
        self.study_id = study_id
        self.name = name
        self.spec = spec
        self.points: tuple[TablePoint, ...] = tuple(points)
        self.policy = policy
        self.plan_digest = plan_digest
        self.verification = dict(verification) if verification is not None else None

    @classmethod
    def read(cls, study_id: str, *, results: Path | None = None) -> ResultTable:
        """Read a sweep Study's points and results.

        Args:
            study_id: A sweep Study.
            results: Optional results-directory override.

        Raises:
            ValueError: The Study is not a sweep.
        """
        from fim import sweep_run  # noqa: PLC0415 - sweep_run imports this module
        from fim.persistence import groups  # noqa: PLC0415

        study = groups.get_study(study_id, results=results)
        spec = sweep_run.sweep_spec_of(study)
        rows = {
            row.index: row
            for row in sweep_run.sweep_point_results(study, results=results)
        }
        points = [
            TablePoint(
                status.index,
                status.coordinates,
                status.run_id,
                status.state,
                rows.get(status.index) if status.state == "done" else None,
            )
            for status in sweep_run.sweep_point_statuses(study, results=results)
        ]
        stored = (study.sweep_spec or {}).get("digest")
        return cls(
            study.study_id,
            study.name,
            spec,
            points,
            plan_digest=stored if isinstance(stored, str) else spec.digest(),
            verification=sweep_run.read_verification(study_id, results=results),
        )

    @property
    def axis_keys(self) -> tuple[str, ...]:
        """The swept keys, in axis order."""
        return tuple(axis.key for axis in self.spec.axes)

    def statistic_names(self) -> tuple[str, ...]:
        """Return every statistic any finished point has, sorted."""
        names = {
            name
            for point in self.points
            if point.row is not None
            for name in point.row.statistics
        }
        return tuple(sorted(names))

    def columns(
        self,
        *,
        with_theory: bool = False,
        statistics: Sequence[str] | None = None,
        wide: bool = False,
    ) -> list[str]:
        """Return the column names of the long (default) or wide table."""
        axes = list(self.axis_keys)
        if "N" in axes:
            axes.append("gene_copies_per_deme")
        if not wide:
            columns = [*_HEAD_COLUMNS, *axes, *_TAIL_COLUMNS]
            if with_theory:
                columns.extend(("theory", "residual", "residual_relative"))
            return columns
        columns = [*_HEAD_COLUMNS, *axes, *_WIDE_POINT_COLUMNS]
        for name in self._selected(statistics):
            columns.extend(
                f"{name}_{suffix}" for suffix, _field in _WIDE_STATISTIC_SUFFIXES
            )
            if with_theory and name in THEORY_STATISTICS:
                columns.extend(
                    (f"{name}_theory", f"{name}_residual", f"{name}_residual_relative")
                )
        return columns

    def long_rows(
        self,
        statistics: Sequence[str] | str | None = None,
        *,
        with_theory: bool = False,
        include_unfinished: bool = False,
    ) -> Iterator[dict[str, Cell]]:
        """Yield one row per finished point and statistic.

        Args:
            statistics: Statistic keys, `ALL_STATISTICS`, or `None` for the
                defaults (`DEFAULT_EXPORT_STATISTICS`).
            with_theory: Add `theory`, `residual` and `residual_relative`.
            include_unfinished: Also yield one row per point with no result,
                its state filled and its statistic empty.

        Raises:
            ValueError: A requested statistic is in no finished point.
        """
        names = self._selected(statistics)
        for point in self.points:
            if point.row is None:
                if include_unfinished:
                    yield self._row(point, None, with_theory)
                continue
            for name in names:
                if name in point.row.statistics:
                    yield self._row(point, name, with_theory)

    def wide_rows(
        self,
        statistics: Sequence[str] | str | None = None,
        *,
        with_theory: bool = False,
        include_unfinished: bool = False,
    ) -> Iterator[dict[str, Cell]]:
        """Yield one row per point, with a column group per statistic."""
        names = self._selected(statistics)
        for point in self.points:
            if point.row is None and not include_unfinished:
                continue
            row = self._point_cells(point)
            row.update(
                {
                    f"{n}_{suffix}": None
                    for n in names
                    for suffix, _f in _WIDE_STATISTIC_SUFFIXES
                }
            )
            predicted = (
                theory_at(self.spec.base, point.coordinates) if with_theory else {}
            )
            for name in names:
                cell = point.row.statistics.get(name) if point.row else None
                if cell is not None:
                    for suffix, attribute in _WIDE_STATISTIC_SUFFIXES:
                        row[f"{name}_{suffix}"] = getattr(cell, attribute)
                if with_theory and name in THEORY_STATISTICS:
                    value = predicted.get(name)
                    row[f"{name}_theory"] = value
                    difference = (
                        cell.mean - value
                        if cell is not None and value is not None
                        else None
                    )
                    row[f"{name}_residual"] = difference
                    row[f"{name}_residual_relative"] = relative_difference(
                        difference, value
                    )
            yield row

    def series(
        self,
        x: str,
        statistic: str,
        *,
        by: str | None = None,
        held: Mapping[str, CoordinateValue] | None = None,
    ) -> list[Series]:
        """Group one statistic into response curves.

        Each curve is the statistic across the `x` axis; with `by`, one curve
        for each value of that axis. Every other axis must be held at a value.
        A point that has not finished is left out of its curve; a curve with
        no finished point is still returned, so a legend can show it.

        Args:
            x: The swept key along the curve.
            statistic: A statistic's catalog key.
            by: A second swept key, giving one curve per value, or `None`.
            held: A value for each axis that is neither `x` nor `by`.

        Returns:
            The curves, in the `by` axis's own order (one curve if `by` is
            `None`); points within a curve follow the `x` axis's order.

        Raises:
            ValueError: An axis is unknown or used twice, or an axis that is
                neither `x` nor `by` has no held value.
        """
        values = {axis.key: axis.values for axis in self.spec.axes}
        for key in (x, by):
            if key is not None and key not in values:
                raise ValueError(f"{key!r} is not an axis of this sweep")
        if by == x:
            raise ValueError("the curves cannot be split by the axis they run along")
        fixed = dict(held or {})
        others = [key for key in values if key not in (x, by)]
        missing = [key for key in others if key not in fixed]
        if missing:
            raise ValueError(f"hold {missing[0]!r} at one of its values")
        by_values: tuple[CoordinateValue | None, ...] = (
            values[by] if by is not None else (None,)
        )
        x_order = {value: place for place, value in enumerate(values[x])}
        curves: list[Series] = []
        for by_value in by_values:
            wanted = {key: fixed[key] for key in others}
            if by is not None:
                wanted[by] = by_value  # type: ignore[assignment]
            points = [
                self._series_point(point, x, statistic)
                for point in self.points
                if point.row is not None
                and statistic in point.row.statistics
                and all(point.coordinates.get(k) == v for k, v in wanted.items())
            ]
            curves.append(
                Series(
                    by_value,
                    tuple(sorted(points, key=lambda found: x_order.get(found.x, 0))),
                )
            )
        return curves

    def _series_point(self, point: TablePoint, x: str, statistic: str) -> SeriesPoint:
        """Build the curve point of a finished table point."""
        row = point.row
        assert row is not None  # the caller filtered on it
        cell = row.statistics[statistic]
        spread = cell.sample_std
        health = row.health
        return SeriesPoint(
            x=point.coordinates[x],
            point_index=point.index,
            run_id=point.run_id,
            directory=row.directory,
            mean=cell.mean,
            ci_low=cell.low,
            ci_high=cell.high,
            sd_low=None if spread is None else cell.mean - spread,
            sd_high=None if spread is None else cell.mean + spread,
            sample_count=cell.sample_count,
            replicates=row.n_replicates,
            health=health.classify(self.policy) if health is not None else None,
            health_reason=health.describe(self.policy) if health is not None else "",
        )

    def to_csv(
        self,
        path: Path,
        *,
        statistics: Sequence[str] | str | None = None,
        wide: bool = False,
        with_theory: bool = False,
        include_unfinished: bool = False,
        overwrite: bool = False,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> Path:
        """Write the table as CSV, and its sidecar beside it.

        Returns:
            The sidecar's path.

        Raises:
            FileExistsError: The table or its sidecar exists and `overwrite`
                is false.
            ValueError: A requested statistic is in no finished point.
        """
        names = self._selected(statistics)
        columns = self.columns(with_theory=with_theory, statistics=names, wide=wide)
        make = self.wide_rows if wide else self.long_rows
        rows = make(
            names, with_theory=with_theory, include_unfinished=include_unfinished
        )
        sidecar = self._guard(path, overwrite)

        def write(handle: Any) -> None:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(columns)
            writer.writerows(
                [_text(row.get(column)) for column in columns] for row in rows
            )

        _write_atomically(path, write)
        self._write_sidecar(
            sidecar,
            layout="wide" if wide else "long",
            names=names,
            columns=columns,
            with_theory=with_theory,
            include_unfinished=include_unfinished,
            clock=clock,
        )
        return sidecar

    def to_jsonl(
        self,
        path: Path,
        *,
        statistics: Sequence[str] | str | None = None,
        with_theory: bool = False,
        include_unfinished: bool = False,
        overwrite: bool = False,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> Path:
        """Write the long table as JSON Lines, and its sidecar beside it.

        Returns:
            The sidecar's path.

        Raises:
            FileExistsError: The table or its sidecar exists and `overwrite`
                is false.
            ValueError: A requested statistic is in no finished point.
        """
        names = self._selected(statistics)
        columns = self.columns(with_theory=with_theory, statistics=names)
        rows = self.long_rows(
            names, with_theory=with_theory, include_unfinished=include_unfinished
        )
        sidecar = self._guard(path, overwrite)

        def write(handle: Any) -> None:
            for row in rows:
                handle.write(json.dumps({c: row.get(c) for c in columns}) + "\n")

        _write_atomically(path, write)
        self._write_sidecar(
            sidecar,
            layout="jsonl",
            names=names,
            columns=columns,
            with_theory=with_theory,
            include_unfinished=include_unfinished,
            clock=clock,
        )
        return sidecar

    def metadata(
        self,
        *,
        layout: str,
        names: Sequence[str],
        columns: Sequence[str],
        with_theory: bool,
        include_unfinished: bool,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> dict[str, object]:
        """Return the sidecar content: how to read the table and where it came from."""
        return {
            "format_version": 1,
            "sweep": {"study_id": self.study_id, "name": self.name},
            "spec": self.spec.to_dict(),
            "plan_digest": self.plan_digest,
            "verification": self.verification,
            "points": [
                {
                    "index": p.index,
                    "coordinates": dict(p.coordinates),
                    "run_id": p.run_id,
                    "state": p.state,
                    "seed": p.row.seed if p.row else None,
                    "engine_backend": p.row.engine_backend if p.row else None,
                    "software_version": p.row.software_version if p.row else None,
                }
                for p in self.points
            ],
            "table": {
                "layout": layout,
                "statistics": list(names),
                "with_theory": with_theory,
                "include_unfinished": include_unfinished,
                "health_policy": {"precision_slack": self.policy.precision_slack},
            },
            "software_version": __version__,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "created": clock().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "statistics": {name: _statistic_info(name) for name in names},
            "columns": {c: self._describe(c) for c in columns},
        }

    def _describe(self, column: str) -> str:
        """Say what one column holds, for the sidecar."""
        if column in COLUMN_DESCRIPTIONS:
            return COLUMN_DESCRIPTIONS[column]
        if column in self.axis_keys:
            return AXIS_DESCRIPTIONS.get(
                column, f"The swept configuration key {column}."
            )
        for suffix in ("mean", "ci_low", "ci_high", "ci_half_width", "sample_std"):
            if column.endswith(f"_{suffix}"):
                statistic = column[: -len(suffix) - 1]
                return f"{COLUMN_DESCRIPTIONS[suffix]} (statistic {statistic})"
        for suffix in ("theory", "residual", "residual_relative"):
            if column.endswith(f"_{suffix}"):
                statistic = column[: -len(suffix) - 1]
                return f"{COLUMN_DESCRIPTIONS[suffix]} (statistic {statistic})"
        return f"Column {column}."

    def _guard(self, path: Path, overwrite: bool) -> Path:
        """Return the sidecar path, refusing to overwrite unless allowed."""
        sidecar = path.with_suffix(".meta.json")
        if not overwrite:
            for target in (path, sidecar):
                if target.exists():
                    raise FileExistsError(
                        f"{target} already exists; choose another name or overwrite it"
                    )
        return sidecar

    def _write_sidecar(
        self,
        sidecar: Path,
        *,
        layout: str,
        names: Sequence[str],
        columns: Sequence[str],
        with_theory: bool,
        include_unfinished: bool,
        clock: Callable[[], datetime],
    ) -> None:
        """Write the sidecar JSON."""
        document = self.metadata(
            layout=layout,
            names=names,
            columns=columns,
            with_theory=with_theory,
            include_unfinished=include_unfinished,
            clock=clock,
        )
        _write_atomically(
            sidecar, lambda handle: handle.write(json.dumps(document, indent=2) + "\n")
        )

    def _selected(self, statistics: Sequence[str] | str | None) -> tuple[str, ...]:
        """Resolve the statistics to write and check each exists.

        Raises:
            ValueError: A requested statistic is in no finished point.
        """
        available = self.statistic_names()
        if statistics == ALL_STATISTICS:
            return available
        if isinstance(statistics, str):
            statistics = (statistics,)
        if statistics is None:
            return tuple(n for n in DEFAULT_EXPORT_STATISTICS if n in available)
        if available:
            unknown = [name for name in statistics if name not in available]
            if unknown:
                raise ValueError(
                    f"no statistic {unknown[0]!r} in this sweep's results; "
                    f"available: {', '.join(available)}"
                )
        return tuple(statistics)

    def _point_cells(self, point: TablePoint) -> dict[str, Cell]:
        """Return the columns every row of a point shares."""
        row: dict[str, Cell] = {
            "sweep_id": self.study_id,
            "point_index": point.index,
            "state": point.state,
            "run_id": point.run_id,
        }
        for key in self.axis_keys:
            value = point.coordinates.get(key)
            row[key] = value if isinstance(value, float | int | str) else None
        if "N" in self.axis_keys:
            row["gene_copies_per_deme"] = _gene_copies(self.spec, point.coordinates)
        found = point.row
        if found is not None:
            health = found.health
            row.update(
                {
                    "seed": found.seed,
                    "replicates": found.n_replicates,
                    "replicates_converged": health.converged if health else None,
                    "replicates_hit_cap": health.hit_cap if health else None,
                    "health": health.classify(self.policy) if health else None,
                    "software_version": found.software_version,
                    "engine_backend": found.engine_backend,
                }
            )
        return row

    def _row(
        self, point: TablePoint, name: str | None, with_theory: bool
    ) -> dict[str, Cell]:
        """Build one long row; `name` is `None` for a point with no result."""
        row = self._point_cells(point)
        row["statistic"] = name
        found = point.row
        if name is None or found is None:
            return row
        cell = found.statistics[name]
        row.update(
            {
                "mean": cell.mean,
                "ci_low": cell.low,
                "ci_high": cell.high,
                "ci_half_width": cell.half_width,
                "ci_level": cell.confidence,
                "sample_std": cell.sample_std,
                "sample_count": cell.sample_count,
                "achieved_half_width": (
                    found.health.achieved_half_width.get(name) if found.health else None
                ),
            }
        )
        if with_theory and name in THEORY_STATISTICS:
            value = theory_at(self.spec.base, point.coordinates).get(name)
            row["theory"] = value
            difference = cell.mean - value if value is not None else None
            row["residual"] = difference
            row["residual_relative"] = relative_difference(difference, value)
        return row


def relative_difference(
    difference: float | None, reference: float | None
) -> float | None:
    """Return `difference / reference`, or `None` where it is not defined.

    Used for "simulation minus theory" as a fraction of the theory (botanist
    question Q8: the absolute difference is the default, the relative one is
    offered beside it, since some statistics span orders of magnitude).

    Args:
        difference: The absolute difference, or `None`.
        reference: What it is relative to (the theory), or `None`.

    Returns:
        The ratio, or `None` if either is `None` or `reference` is 0.
    """
    if difference is None or reference is None or reference == 0:
        return None
    return difference / reference


def _statistic_info(name: str) -> dict[str, str]:
    """Describe a statistic from the catalog, for the sidecar."""
    try:
        entry = statistic_spec(name)
    except KeyError:
        return {"label": name, "description": ""}
    return {"label": entry.label_text, "description": entry.description}


def _write_atomically(path: Path, write: Callable[[Any], object]) -> None:
    """Write a text file by writing a temporary one and renaming it."""
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            write(handle)
        temporary.replace(path)
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise
