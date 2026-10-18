"""What a sweep is expected to cost, before it runs.

A point's cost is not known until it runs, because nobody knows in advance
how soon the convergence monitor will settle. What is known is a band: a
replicate runs for at least its burn-in and at most its generation cap, and a
batch runs at least its minimum replicate count and at most `n_replicates`.
`PointCost` and `SweepCost` carry those bands, honestly wide, in the unit the
engine actually spends: replicate-generations. Seconds and bytes come from
measuring the engine (a later layer builds them on these types), and a sweep
that has started refines the band from the points that have finished.

Seconds and bytes come from a `ThroughputProbe`: a short timed burst of one
point's own configuration, kept apart from the model by a `Protocol` so a test
supplies exact figures and never a clock. Points are grouped by shape class
(deme count, engine, loci, population size), the few biggest classes are
measured, and every other point takes the nearest measured class.

The static bands above run nothing; only `EngineProbe` runs a simulation, and
it throws its work away.
"""

from __future__ import annotations

import json
import logging
import math
import platform
import shutil
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from fim import __version__
from fim.config.defaults import DEFAULT_PAIRWISE_MAX_DEMES
from fim.model.params import SimulationParams

if TYPE_CHECKING:
    from fim.sweep import SweepPlan, SweepPoint, SweepSpec

logger = logging.getLogger(__name__)

DEFAULT_PROBE_GENERATIONS = 200
"""Generations in the shorter of the two timed bursts a probe runs (the longer
is twice this)."""

DEFAULT_PROBED_CLASSES = 6
"""Most shape classes one plan measures; the rest take the nearest."""


@dataclass(frozen=True, slots=True)
class Band:
    """A range a quantity is expected to fall in.

    Attributes:
        low: The least it can be.
        high: The most it can be.
        calibrated: A better single estimate, once points have finished and
            their actual figures are known; `None` before that.
    """

    low: float
    high: float
    calibrated: float | None = None

    def __post_init__(self) -> None:
        """Refuse a band whose ends are the wrong way round."""
        if self.low > self.high:
            raise ValueError(f"a band's low {self.low} is above its high {self.high}")

    def __add__(self, other: Band) -> Band:
        """Add two bands end to end (the calibrated estimate is dropped)."""
        return Band(self.low + other.low, self.high + other.high)

    def __mul__(self, other: Band) -> Band:
        """Multiply two bands of non-negative quantities end to end."""
        return Band(self.low * other.low, self.high * other.high)

    def scaled(self, factor: float) -> Band:
        """Return the band with both ends multiplied by `factor >= 0`."""
        return Band(self.low * factor, self.high * factor)

    def to_dict(self) -> dict[str, float | None]:
        """Return the band as plain values, for JSON."""
        return {"low": self.low, "high": self.high, "calibrated": self.calibrated}


@dataclass(frozen=True, slots=True)
class PointCost:
    """What one point is expected to cost.

    Attributes:
        run_id: The point's id.
        generations: Generations per replicate: its burn-in up to its cap.
        replicates: Replicates the point runs: the batch minimum up to
            `n_replicates` (a single run is exactly one).
        work: Replicate-generations for the whole point, the product of the
            two bands above.
        seconds: Wall seconds for the whole point, once a probe has measured
            (or been matched to) its shape class; else `None`.
        trace_bytes: Size of its trajectory log, or `None`.
        statistic_bytes: Size of its convergence history and deme-pair
            statistics, or `None`.
        probe: Where its throughput came from: `measured` (its own shape
            class was timed), `nearest` (the closest timed class), or
            `unprobed`.
    """

    run_id: str
    generations: Band
    replicates: Band
    work: Band
    seconds: Band | None = None
    trace_bytes: Band | None = None
    statistic_bytes: Band | None = None
    probe: Literal["measured", "nearest", "unprobed"] = "unprobed"


@dataclass(frozen=True, slots=True)
class SweepCost:
    """What a sweep is expected to cost.

    Attributes:
        points: Each point's cost, in grid order.
        work: Replicate-generations for the whole sweep.
        points_at_once: How many points run at the same time, or `None` if
            it is not yet known.
        wall_seconds: Wall-clock time for the sweep with that many points at
            once, or `None` if it was not probed.
        disk_bytes: Disk the sweep's results take with the full trajectory
            log kept, or `None`.
        statistics_bytes: Disk if only the statistics were kept (no
            trajectory log), or `None`.
        classes_measured: How many shape classes were timed.
    """

    points: tuple[PointCost, ...]
    work: Band
    points_at_once: int | None = None
    wall_seconds: Band | None = None
    disk_bytes: Band | None = None
    statistics_bytes: Band | None = None
    classes_measured: int = 0

    def to_dict(self) -> dict[str, object]:
        """Return the cost as plain values, for the page and for JSON."""
        return {
            "points": len(self.points),
            "replicates": self.replicate_band().to_dict(),
            "generations": self.generation_band().to_dict(),
            "work": self.work.to_dict(),
            "pointsAtOnce": self.points_at_once,
            "wallSeconds": _band_dict(self.wall_seconds),
            "diskBytes": _band_dict(self.disk_bytes),
            "statisticsBytes": _band_dict(self.statistics_bytes),
            "probe": {
                "measured": sum(p.probe == "measured" for p in self.points),
                "nearest": sum(p.probe == "nearest" for p in self.points),
                "classes": self.classes_measured,
            },
        }

    def generation_band(self) -> Band:
        """Return the narrowest band holding every point's generations."""
        return Band(
            min((p.generations.low for p in self.points), default=0.0),
            max((p.generations.high for p in self.points), default=0.0),
        )

    def replicate_band(self) -> Band:
        """Return the narrowest band holding every point's replicates."""
        return Band(
            min((p.replicates.low for p in self.points), default=0.0),
            max((p.replicates.high for p in self.points), default=0.0),
        )


def point_cost(run_id: str, params: SimulationParams) -> PointCost:
    """Return the static cost band of one resolved point.

    Args:
        run_id: The point's id.
        params: The point's resolved configuration, whose automatic burn-in
            and generation cap are already derived from its own migration,
            mutation and sizes.

    Returns:
        A replicate lasts at least `convergence_burn_in` (or the cap, if the
        cap is smaller) and at most `max_generations` generations. A batch
        that may stop early runs at least `replicate_minimum` replicates
        (never more than `n_replicates`); otherwise, and for a single run, it
        runs
        `n_replicates` in full.
    """
    # A cap set below the burn-in ends the run at the cap, so the least a
    # replicate can run is the smaller of the two.
    cap = float(params.max_generations)
    generations = Band(min(float(params.convergence_burn_in), cap), cap)
    most = float(params.n_replicates)
    adaptive = params.stop_batch_early and params.n_replicates > 1
    least = (
        float(min(params.replicate_minimum, params.n_replicates)) if adaptive else most
    )
    replicates = Band(least, most)
    return PointCost(run_id, generations, replicates, generations * replicates)


def static_cost(
    spec: SweepSpec, plan: SweepPlan, *, points_at_once: int | None = None
) -> SweepCost:
    """Return the static cost bands of a plan's points.

    Args:
        spec: The sweep specification (kept for the layers that add seconds
            and bytes, which depend on its base configuration).
        plan: The enumerated plan; only its valid points cost anything.
        points_at_once: How many points will run at the same time (see
            `fim.sweep_run.resolve_concurrency`), if known.

    Returns:
        Each point's cost and the sweep's total work.
    """
    del spec
    costs = tuple(
        point_cost(point.run_id, SimulationParams.from_mapping(point.params))
        for point in plan.points
    )
    work = Band(0.0, 0.0)
    for cost in costs:
        work = work + cost.work
    return SweepCost(costs, work, points_at_once)


def _band_dict(band: Band | None) -> dict[str, float | None] | None:
    """Return a band as plain values, or `None` for a band not worked out."""
    return None if band is None else band.to_dict()


@dataclass(frozen=True, slots=True)
class Throughput:
    """What running one shape class costs, per unit of work.

    Attributes:
        seconds_per_replicate_generation: Wall seconds to step one replicate
            one generation.
        trace_bytes_per_generation: Trajectory-log bytes one replicate adds
            each generation.
        statistic_bytes_per_generation: Convergence-history bytes one
            replicate adds each generation.
        pairwise_bytes_per_pair: Bytes `pairwise.json` spends on one deme
            pair of one replicate.
    """

    seconds_per_replicate_generation: float
    trace_bytes_per_generation: float
    statistic_bytes_per_generation: float
    pairwise_bytes_per_pair: float

    def to_dict(self) -> dict[str, float]:
        """Return the figures as plain values, for the cache."""
        return {
            "seconds": self.seconds_per_replicate_generation,
            "trace": self.trace_bytes_per_generation,
            "statistic": self.statistic_bytes_per_generation,
            "pair": self.pairwise_bytes_per_pair,
        }


class ThroughputProbe(Protocol):
    """Measures the throughput of one configuration."""

    def measure(self, params: SimulationParams, generations: int) -> Throughput:
        """Run a short burst of `params` and report its throughput.

        Args:
            params: One point's resolved configuration.
            generations: The length of the shorter timed burst.
        """
        ...


@dataclass(frozen=True, slots=True, order=True)
class ShapeClass:
    """What decides how fast a configuration runs and how big its files are.

    Attributes:
        d: Number of demes.
        engine: The configured engine backend.
        loci: Number of loci.
        locus_length: Length of the longest locus.
        size_bucket: Base-2 logarithm of the largest deme's gene copies,
            rounded: populations within a factor of about 1.4 share a bucket.
    """

    d: int
    engine: str
    loci: int
    locus_length: int
    size_bucket: int

    @classmethod
    def of(cls, params: SimulationParams) -> ShapeClass:
        """Return the shape class of a resolved configuration."""
        copies = params.gene_copies
        largest = max(copies) if isinstance(copies, tuple | list) else copies
        return cls(
            d=params.d,
            engine=str(params.engine_backend),
            loci=len(params.loci),
            locus_length=max(locus.length for locus in params.loci),
            size_bucket=round(math.log2(max(int(largest), 1))),
        )

    @property
    def key(self) -> str:
        """Return a text key for this class (the cache's key)."""
        return (
            f"d{self.d}|{self.engine}|loci{self.loci}x{self.locus_length}"
            f"|n2^{self.size_bucket}"
        )

    def distance(self, other: ShapeClass) -> float:
        """Return how far apart two classes are, in (log d, log N)."""
        return math.hypot(
            math.log2(max(self.d, 1)) - math.log2(max(other.d, 1)),
            self.size_bucket - other.size_bucket,
        )


def nearest_class(target: ShapeClass, measured: Sequence[ShapeClass]) -> ShapeClass:
    """Return the measured class closest to `target`.

    The same engine is always preferred (engines differ in speed by far more
    than shapes do); within it, the least distance in `(log d, log N)` wins,
    and a tie goes to the class that sorts first, so the answer never depends
    on the order the classes were found in.

    Args:
        target: The class to place.
        measured: At least one measured class.

    Raises:
        ValueError: `measured` is empty.
    """
    if not measured:
        raise ValueError("there is no measured class to match")
    same_engine = [c for c in measured if c.engine == target.engine]
    pool = same_engine or list(measured)
    return min(pool, key=lambda c: (target.distance(c), c))


class ThroughputCache:
    """Per-machine memory of measured throughput, so repeating a plan is instant.

    Keyed by software version, machine and shape class; a damaged or
    unwritable file is ignored (the figures are measured again).

    Args:
        path: The cache file.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._entries: dict[str, dict[str, float]] = self._load()
        self._dirty = False

    @staticmethod
    def key_for(shape: ShapeClass) -> str:
        """Return the cache key of a shape class on this machine."""
        return f"{__version__}|{platform.machine()}|{shape.key}"

    def get(self, shape: ShapeClass) -> Throughput | None:
        """Return the cached throughput of a class, or `None`."""
        entry = self._entries.get(self.key_for(shape))
        if not isinstance(entry, dict):
            return None
        try:
            return Throughput(
                float(entry["seconds"]),
                float(entry["trace"]),
                float(entry["statistic"]),
                float(entry["pair"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def put(self, shape: ShapeClass, throughput: Throughput) -> None:
        """Remember a class's throughput."""
        self._entries[self.key_for(shape)] = throughput.to_dict()
        self._dirty = True

    def save(self) -> None:
        """Write the cache if it changed; a failure is logged, never raised."""
        if not self._dirty:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_name(self._path.name + ".tmp")
            temporary.write_text(
                json.dumps(self._entries, sort_keys=True), encoding="utf-8"
            )
            temporary.replace(self._path)
        except OSError as error:
            logger.debug(
                "could not write the throughput cache %s: %s", self._path, error
            )
        else:
            self._dirty = False

    def _load(self) -> dict[str, dict[str, float]]:
        """Read the cache file; a missing or damaged file is an empty cache."""
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return loaded if isinstance(loaded, dict) else {}


@dataclass(frozen=True, slots=True)
class _Burst:
    """What one timed burst produced."""

    seconds: float
    generations: int
    trace_bytes: int
    statistic_bytes: int
    pairwise_bytes: int


class EngineProbe:
    """Measures throughput by running two short bursts of a real configuration.

    Each burst runs one replicate of the point's own configuration into a
    temporary directory through the real saved-run path (so bytes include the
    real codec), then deletes it. Two bursts, one twice the length of the
    other, are timed and their difference is used, so costs that do not grow
    with generations (starting the engine, the scatter plot) cancel out.

    Args:
        timer: A clock returning seconds; the test seam.
        scratch: Directory to make the temporary directories in; the
            system's when `None`.
    """

    def __init__(
        self,
        *,
        timer: Callable[[], float] = time.perf_counter,
        scratch: Path | None = None,
    ) -> None:
        self._timer = timer
        self._scratch = scratch

    def measure(self, params: SimulationParams, generations: int) -> Throughput:
        """Time bursts of `generations` and twice that, and return the rates."""
        short = self._burst(params, generations)
        long = self._burst(params, 2 * generations)
        extra = long.generations - short.generations

        def rate(longer: float, shorter: float) -> float:
            if extra > 0 and longer > shorter:
                return (longer - shorter) / extra
            # Both bursts stopped at the same generation (the run converged),
            # or timing noise swallowed the difference: the long burst's
            # total, fixed costs included, is the honest upper figure.
            return max(0.0, longer / max(long.generations, 1))

        pairs = max(params.d * (params.d - 1) // 2, 1)
        return Throughput(
            seconds_per_replicate_generation=rate(long.seconds, short.seconds),
            trace_bytes_per_generation=rate(long.trace_bytes, short.trace_bytes),
            statistic_bytes_per_generation=rate(
                long.statistic_bytes, short.statistic_bytes
            ),
            pairwise_bytes_per_pair=long.pairwise_bytes / pairs,
        )

    def _burst(self, params: SimulationParams, generations: int) -> _Burst:
        """Run one replicate for up to `generations` and measure it.

        Only the engine run (with its trajectory log) is timed; the artifacts
        are written afterwards, untimed, just to measure their size.
        """
        from fim.engine import RunResult, simulate_params  # noqa: PLC0415 - heavy
        from fim.engine.identity import deterministic_run_id  # noqa: PLC0415
        from fim.persistence.artifacts import (  # noqa: PLC0415
            run_artifact_targets,
            write_run_artifacts,
        )
        from fim.persistence.binary_store import (  # noqa: PLC0415
            BinaryLogStore,
            log_options,
        )

        scratch = Path(tempfile.mkdtemp(prefix="fim-probe-", dir=self._scratch))
        try:
            # A burst is shorter than most burn-ins; give it a valid one so
            # the engine does not warn that it will stop before averaging.
            burst = replace(
                params,
                n_replicates=1,
                max_generations=generations,
                convergence_burn_in=min(params.convergence_burn_in, generations // 2),
            )
            run = scratch / "run"
            run.mkdir()
            targets = run_artifact_targets(run)
            started = self._timer()
            with BinaryLogStore(
                targets["trajectory"], **log_options(burst.expert)
            ) as store:
                output = simulate_params(
                    params=burst, store=store, run_id=deterministic_run_id(burst)
                )
            elapsed = self._timer() - started
            if not isinstance(output, RunResult):
                raise RuntimeError("a single-replicate burst returned a batch")
            write_run_artifacts(
                output, run, pairwise_max_demes=DEFAULT_PAIRWISE_MAX_DEMES
            )

            def size(name: str) -> int:
                path = run / name
                return path.stat().st_size if path.is_file() else 0

            return _Burst(
                seconds=elapsed,
                generations=int(output.report["generation"]),
                trace_bytes=size("trajectory.tlog"),
                statistic_bytes=size("convergence.jsonl"),
                pairwise_bytes=size("pairwise.json"),
            )
        finally:
            shutil.rmtree(scratch, ignore_errors=True)


def probed_cost(
    spec: SweepSpec,
    plan: SweepPlan,
    probe: ThroughputProbe,
    *,
    points_at_once: int | None = None,
    workers_per_point: int = 1,
    generations: int = DEFAULT_PROBE_GENERATIONS,
    max_classes: int = DEFAULT_PROBED_CLASSES,
    cache: ThroughputCache | None = None,
    pairwise_max_demes: int = DEFAULT_PAIRWISE_MAX_DEMES,
) -> SweepCost:
    """Add seconds and bytes to the static bands of a plan.

    Points are grouped by shape class. The `max_classes` classes with the
    most work are measured (each by its first point in grid order, or taken
    from `cache`); every other point takes the nearest measured class.

    Args:
        spec: The sweep specification.
        plan: The enumerated plan.
        probe: Measures a configuration's throughput.
        points_at_once: How many points run at the same time (the wall-clock
            time divides the work over them); one if `None`.
        workers_per_point: Processes sharing one point's replicates (a lineal
            batch), which speeds a point up by that factor.
        generations: Length of the shorter timed burst.
        max_classes: Most shape classes to measure.
        cache: Remembered measurements, read and extended.
        pairwise_max_demes: Largest deme count for which a run saves its
            deme-pair statistics.

    Returns:
        The static cost with seconds, trace and statistic bytes, wall-clock
        time and disk added.
    """
    base = static_cost(spec, plan, points_at_once=points_at_once)
    configs = [SimulationParams.from_mapping(point.params) for point in plan.points]
    shapes = [ShapeClass.of(params) for params in configs]
    work_by_class: dict[ShapeClass, float] = {}
    first_of: dict[ShapeClass, SimulationParams] = {}
    for shape, params, cost in zip(shapes, configs, base.points, strict=True):
        work_by_class[shape] = work_by_class.get(shape, 0.0) + cost.work.high
        first_of.setdefault(shape, params)
    order = sorted(work_by_class, key=lambda c: (-work_by_class[c], c))[:max_classes]
    measured: dict[ShapeClass, Throughput] = {}
    for shape in order:
        found = cache.get(shape) if cache is not None else None
        if found is None:
            found = probe.measure(first_of[shape], generations)
            if cache is not None:
                cache.put(shape, found)
        measured[shape] = found
    if cache is not None:
        cache.save()
    sources = sorted(measured)
    points = tuple(
        _priced(
            cost,
            params,
            shape,
            measured,
            sources,
            workers_per_point,
            pairwise_max_demes,
        )
        for cost, params, shape in zip(base.points, configs, shapes, strict=True)
    )
    return replace(
        base,
        points=points,
        wall_seconds=_wall_seconds(points, points_at_once or 1),
        disk_bytes=_total(
            band for p in points for band in (p.trace_bytes, p.statistic_bytes) if band
        ),
        statistics_bytes=_total(p.statistic_bytes for p in points if p.statistic_bytes),
        classes_measured=len(measured),
    )


def _priced(
    cost: PointCost,
    params: SimulationParams,
    shape: ShapeClass,
    measured: Mapping[ShapeClass, Throughput],
    sources: Sequence[ShapeClass],
    workers: int,
    pairwise_max_demes: int,
) -> PointCost:
    """Return `cost` with the seconds and bytes of its shape class added."""
    own = shape in measured
    figures = measured[shape] if own else measured[nearest_class(shape, sources)]
    pairs = params.d * (params.d - 1) // 2 if params.d <= pairwise_max_demes else 0
    statistics = cost.work.scaled(figures.statistic_bytes_per_generation) + (
        cost.replicates.scaled(pairs * figures.pairwise_bytes_per_pair)
    )
    return replace(
        cost,
        seconds=cost.work.scaled(
            figures.seconds_per_replicate_generation / max(workers, 1)
        ),
        trace_bytes=cost.work.scaled(figures.trace_bytes_per_generation),
        statistic_bytes=statistics,
        probe="measured" if own else "nearest",
    )


def _total(bands: Iterable[Band]) -> Band:
    """Add up bands."""
    total = Band(0.0, 0.0)
    for band in bands:
        total = total + band
    return total


def _wall_seconds(points: Sequence[PointCost], at_once: int) -> Band:
    """Bound the wall-clock time of running `points` `at_once` at a time.

    Independent jobs on `m` machines finish no sooner than the larger of
    (total work / m) and the longest job, and list scheduling finishes no
    later than total / m + (1 - 1/m) * longest (Graham's bound).
    """
    times = [p.seconds for p in points if p.seconds is not None]
    if not times:
        return Band(0.0, 0.0)
    machines = max(at_once, 1)
    total = _total(times)
    longest_low = max(t.low for t in times)
    longest_high = max(t.high for t in times)
    return Band(
        max(total.low / machines, longest_low),
        total.high / machines + (1 - 1 / machines) * longest_high,
    )


def format_duration(seconds: float) -> str:
    """Write a duration the way a person would say it.

    Args:
        seconds: A non-negative number of seconds.

    Returns:
        For example `"40 s"`, `"12 min"`, `"3 h 10 min"` or `"2 d 4 h"`;
        the two largest units that are not zero, rounded to the smaller one.
    """
    total = max(0, round(seconds))
    if total < 60:  # noqa: PLR2004
        return f"{total} s"
    minutes = round(total / 60)
    if minutes < 60:  # noqa: PLR2004
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:  # noqa: PLR2004
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h" if hours else f"{days} d"


def format_bytes(size: float) -> str:
    """Write a size in decimal units, to two significant figures.

    Args:
        size: A non-negative number of bytes.

    Returns:
        For example `"820 B"`, `"4.2 MB"` or `"140 GB"`.
    """
    value = float(max(size, 0.0))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":  # noqa: PLR2004
            digits = f"{value:.0f}" if value >= 10 or unit == "B" else f"{value:.1f}"  # noqa: PLR2004
            return f"{digits} {unit}"
        value /= 1000
    raise AssertionError("unreachable")  # pragma: no cover


def band_text(band: Band, formatter: Callable[[float], str]) -> str:
    """Write a band as "a to b", or just "a" when both ends read the same."""
    low, high = formatter(band.low), formatter(band.high)
    return low if low == high else f"{low} to {high}"


def order_costliest_first(points: Sequence[SweepPoint]) -> list[SweepPoint]:
    """Order points longest-job-first by their work band.

    For independent jobs on parallel workers, starting the longest first is
    the standard rule for keeping the last finisher close to the best
    possible. Points are ranked by the top of their work band, then by its
    bottom (a longer burn-in first), and ties keep grid order, so the order
    is a pure function of the points. A point's stored result does not
    depend on when it ran.

    Args:
        points: Planned points, in any order.

    Returns:
        The same points, most work first, grid order within equal work.
    """
    work = {
        point.run_id: point_cost(
            point.run_id, SimulationParams.from_mapping(point.params)
        ).work
        for point in points
    }
    return sorted(
        points,
        key=lambda point: (
            -work[point.run_id].high,
            -work[point.run_id].low,
            point.index,
        ),
    )


@dataclass(frozen=True, slots=True)
class Actual:
    """What a finished point really cost, read back from its manifests.

    Attributes:
        run_id: The point's id.
        seconds: Wall seconds from the run's start to its end.
        work: Replicate-generations actually run, summed over replicates.
        ended_at: When it ended (UTC text).
    """

    run_id: str
    seconds: float
    work: float
    ended_at: str


def _timestamp(text: object) -> datetime | None:
    """Parse a manifest timestamp such as `2026-10-17T16:51:55.222224Z`."""
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _read_object(path: Path) -> dict[str, object] | None:
    """Return the JSON object in `path`, or `None` if it cannot be read."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def read_actual(run_id: str, directory: Path) -> Actual | None:
    """Read how long a finished point took and how much it ran.

    The duration is the batch (or single run) manifest's `started_at` to
    `ended_at`; the work is the sum of each replicate's `generation_count`
    (a single run's own). Nothing is stored for this: the inputs are
    already in the manifests every run writes.

    Args:
        run_id: The point's id.
        directory: The point's run directory.

    Returns:
        The actual cost, or `None` if the manifests cannot be read.
    """
    manifest = _read_object(directory / "manifest.json")
    if manifest is None:
        return None
    started, ended = (
        _timestamp(manifest.get("started_at")),
        _timestamp(manifest.get("ended_at")),
    )
    if started is None or ended is None:
        return None
    sources = [
        _read_object(path)
        for path in sorted(directory.glob("replicate-*/manifest.json"))
    ] or [manifest]
    work = 0.0
    for source in sources:
        convergence = source.get("convergence") if source else None
        count = (
            convergence.get("generation_count")
            if isinstance(convergence, dict)
            else None
        )
        if not isinstance(count, int | float):
            return None
        work += float(count)
    return Actual(
        run_id,
        max((ended - started).total_seconds(), 0.0),
        work,
        str(manifest["ended_at"]),
    )


@dataclass(frozen=True, slots=True)
class Remaining:
    """What is left of a sweep, with the estimate calibrated by what has run.

    Attributes:
        points: Points not yet finished.
        work: Replicate-generations still to come, from the plan's bands.
        calibrated_seconds: Wall seconds to finish, from the speed and the
            fraction of its cap that the finished points actually used;
            `None` until at least one point has finished.
        finished_points: Points the calibration is based on.
        plan_seconds: The plan's own (probe-based) wall-clock band for the
            remaining points, or `None` if the plan was not probed.
    """

    points: int
    work: Band
    calibrated_seconds: float | None
    finished_points: int
    plan_seconds: Band | None


def remaining_cost(cost: SweepCost, finished: Mapping[str, Actual]) -> Remaining:
    """Estimate what is left of a sweep from the points that have finished.

    The ratio of the finished points' actual seconds to the top of their
    predicted work band says how fast this machine runs and how much of its
    cap a point really uses, in one number; applied to the top of the
    remaining points' work bands it gives the time still to come, divided
    over the points run at once. It sharpens as more points finish.

    Args:
        cost: The plan's cost (`static_cost` or `probed_cost`).
        finished: Actual figures of the finished points, by run id.

    Returns:
        The points and work left, and the calibrated and planned time.
    """
    done = [p for p in cost.points if p.run_id in finished]
    left = [p for p in cost.points if p.run_id not in finished]
    work = _total(p.work for p in left)
    predicted = sum(p.work.high for p in done)
    spent = sum(finished[p.run_id].seconds for p in done)
    machines = max(cost.points_at_once or 1, 1)
    calibrated = spent / predicted * work.high / machines if predicted > 0 else None
    planned = [p.seconds for p in left if p.seconds is not None]
    return Remaining(
        points=len(left),
        work=work,
        calibrated_seconds=calibrated,
        finished_points=len(done),
        plan_seconds=_wall_seconds(left, machines)
        if planned and len(planned) == len(left)
        else None,
    )
