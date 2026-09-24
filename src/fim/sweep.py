"""Parameter sweeps: what varies, which points result, and which are valid.

A sweep is a Study generated from a specification instead of assembled by
hand (`20260923-claude-sonnet-5-sweep-as-study-implementation-plan.md`,
`selby/restricted`). This module is the pure half: it turns a
`SweepSpec` (a complete base configuration plus the axes that vary) into
an ordered list of validated points, each a whole configuration with a
deterministic `run_id`. Nothing here runs a simulation, touches disk, or
imports the GUI, so the command line, the desktop app and any future
service share it.

Three ideas carry the design:

- **Plan before running.** `enumerate_points` builds and validates every
  point up front. A combination that cannot be run (a torus whose
  `rows * columns` differs from `d`) is reported with its reason, never
  discovered at point 47.
- **Points are content-addressed.** A point's `run_id` is the hash of its
  whole configuration (`fim.engine.deterministic_run_id`), so a resumed or
  overlapping sweep can recognize a point it already computed.
- **`N` counts individuals.** An `N` axis means individuals per deme, the
  number the botanist sees everywhere else in the app; the point's own
  `N` (gene copies, as every configuration mapping stores it) is that
  value times the base `ploidy`. Ploidy always divides the result, so an
  `N` axis never yields an indivisible point.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from fim.engine import deterministic_run_id
from fim.model.params import PARAMETER_DEFAULTS, SimulationParams

SWEEP_SPEC_VERSION: Final = 1
"""Schema version of the stored `sweep_spec` mapping."""

SIZE_CONFIRMATION_THRESHOLD: Final = 100
"""Points at or above which a plan should ask before running."""

SEED_POLICIES: Final = ("spaced", "same")
"""`spaced`: independent points; `same`: every point uses the base seed."""

STRATEGIES: Final = ("grid",)
"""Only the full grid exists; the field leaves room for later strategies."""

_SIGNIFICANT_DIGITS: Final = 12
"""Digits kept when expanding a range, so ids do not depend on float noise."""

AxisKind = Literal["int", "float", "choice"]
Scale = Literal["linear", "log"]
SeedPolicy = Literal["spaced", "same"]
CoordinateValue = int | float | str


@dataclass(frozen=True, slots=True)
class SweepKey:
    """One parameter a sweep may vary, with the metadata its controls need.

    Attributes:
        key: Name used in a spec and in a sweep file.
        label: Short human label.
        kind: `int`, `float`, or `choice`.
        unit: What one value counts, for display.
        scale: Default spacing for a range over this key.
        minimum: Smallest legal value, or `None` for a choice.
        maximum: Largest legal value, or `None`.
        choices: The legal values of a `choice` key.
        display_domain: Default range a chart or a range control shows.
        closed_form: Whether Explore has a closed-form prediction for it.
    """

    key: str
    label: str
    kind: AxisKind
    unit: str
    scale: Scale = "linear"
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    display_domain: tuple[float, float] | None = None
    closed_form: bool = False


SWEEPABLE_KEYS: Final[dict[str, SweepKey]] = {
    "N": SweepKey(
        key="N",
        label="N (individuals per deme)",
        kind="int",
        unit="individuals",
        scale="log",
        minimum=1,
        display_domain=(5.0, 2500.0),
        closed_form=True,
    ),
    "d": SweepKey(
        key="d",
        label="d (demes)",
        kind="int",
        unit="demes",
        scale="linear",
        minimum=2,
        display_domain=(2.0, 50.0),
        closed_form=True,
    ),
    "m": SweepKey(
        key="m",
        label="m (migration rate)",
        kind="float",
        unit="probability",
        scale="log",
        minimum=0.0,
        maximum=1.0,
        display_domain=(0.0001, 0.5),
        closed_form=True,
    ),
    "mu": SweepKey(
        key="mu",
        label="μ (mutation rate)",
        kind="float",
        unit="probability",
        scale="log",
        minimum=0.0,
        maximum=1.0,
        display_domain=(0.000001, 0.1),
        closed_form=True,
    ),
    "topology": SweepKey(
        key="topology",
        label="migration topology",
        kind="choice",
        unit="topology",
        choices=("island", "ring", "linear", "torus"),
    ),
    "deme_weighting": SweepKey(
        key="deme_weighting",
        label="deme weighting",
        kind="choice",
        unit="weighting",
        choices=("equal", "size"),
    ),
}
"""Every parameter a sweep may vary. A table, not a free-for-all."""


@dataclass(frozen=True, slots=True)
class SweepAxis:
    """One varied parameter and the exact values it takes, in order."""

    key: str
    values: tuple[CoordinateValue, ...]

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-serializable stored form."""
        return {"key": self.key, "values": list(self.values)}


@dataclass(frozen=True, slots=True)
class SweepSpec:
    """What is held fixed (`base`) and what varies (`axes`).

    Attributes:
        base: A complete configuration mapping, exactly what
            `SimulationParams.from_mapping` accepts. Its `N` is gene
            copies, like every configuration mapping; it is overwritten
            when `N` is an axis, and `ploidy` is then required.
        axes: The varied keys, first axis slowest in the enumeration.
        strategy: `grid` (every combination).
        seed_policy: `spaced` gives point `i` the seed `base seed + i *
            n_replicates`, so no two points share a replicate seed;
            `same` gives every point the base seed.
    """

    base: Mapping[str, Any]
    axes: tuple[SweepAxis, ...]
    strategy: str = "grid"
    seed_policy: SeedPolicy = "spaced"

    def __post_init__(self) -> None:
        """Validate the axes against the base and the sweepable-key table."""
        if self.strategy not in STRATEGIES:
            raise ValueError(f"sweep strategy must be one of {list(STRATEGIES)}")
        if self.seed_policy not in SEED_POLICIES:
            raise ValueError(f"sweep seed_policy must be one of {list(SEED_POLICIES)}")
        if not self.axes:
            raise ValueError("a sweep needs at least one axis")
        keys = [axis.key for axis in self.axes]
        if len(set(keys)) != len(keys):
            raise ValueError("a sweep axis key may appear only once")
        for axis in self.axes:
            if axis.key not in SWEEPABLE_KEYS:
                allowed = ", ".join(sorted(SWEEPABLE_KEYS))
                raise ValueError(
                    f"cannot sweep {axis.key!r}; the sweepable keys are {allowed}"
                )
            if not axis.values:
                raise ValueError(f"sweep axis {axis.key!r} has no values")
        _check_base_supports_axes(self.base, set(keys))

    @property
    def grid_size(self) -> int:
        """Return the number of points in the full grid."""
        return math.prod(len(axis.values) for axis in self.axes)

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-serializable stored form (without the points)."""
        return {
            "spec_version": SWEEP_SPEC_VERSION,
            "strategy": self.strategy,
            "base": dict(self.base),
            "axes": [axis.to_dict() for axis in self.axes],
            "seed_policy": self.seed_policy,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SweepSpec:
        """Rebuild a spec from its stored form (`to_dict`).

        Raises:
            ValueError: The mapping is malformed or its version is unknown.
        """
        version = value.get("spec_version")
        if version != SWEEP_SPEC_VERSION:
            raise ValueError(f"unsupported sweep spec_version: {version!r}")
        base = value.get("base")
        if not isinstance(base, Mapping):
            raise ValueError("sweep spec 'base' must be an object")
        raw_axes = value.get("axes")
        if not isinstance(raw_axes, Sequence) or isinstance(raw_axes, str):
            raise ValueError("sweep spec 'axes' must be a list")
        axes = tuple(_stored_axis(entry) for entry in raw_axes)
        return cls(
            base=dict(base),
            axes=axes,
            strategy=str(value.get("strategy", "grid")),
            seed_policy=_seed_policy(value.get("seed_policy", "spaced")),
        )


@dataclass(frozen=True, slots=True)
class SweepPoint:
    """One valid point: a whole configuration and its deterministic id.

    Attributes:
        index: Position in the full grid (first axis slowest), counting
            invalid points, so a point's place never depends on which
            others turned out valid.
        coordinates: The varied values, by axis key.
        params: The configuration mapping for `SimulationParams.from_mapping`.
        run_id: `deterministic_run_id` of the validated configuration.
    """

    index: int
    coordinates: Mapping[str, CoordinateValue]
    params: Mapping[str, Any]
    run_id: str

    def to_dict(self) -> dict[str, object]:
        """Return the stored plan entry (the plan, not the parameters)."""
        return {
            "index": self.index,
            "coordinates": dict(self.coordinates),
            "run_id": self.run_id,
        }


@dataclass(frozen=True, slots=True)
class InvalidPoint:
    """A grid position whose configuration cannot be built, with the reason."""

    index: int
    coordinates: Mapping[str, CoordinateValue]
    reason: str


@dataclass(frozen=True, slots=True)
class SweepPlan:
    """Every point of a sweep, valid and not, before anything runs.

    Attributes:
        points: Valid points in grid order, de-duplicated by `run_id`.
        invalid: Grid positions that cannot run, each with its reason.
        collapsed: How many valid points shared a `run_id` with an
            earlier one and were dropped.
        grid_size: The full grid's size.
    """

    points: tuple[SweepPoint, ...]
    invalid: tuple[InvalidPoint, ...]
    collapsed: int
    grid_size: int

    @property
    def needs_confirmation(self) -> bool:
        """Return whether the plan is large enough to ask before running."""
        return len(self.points) >= SIZE_CONFIRMATION_THRESHOLD


def enumerate_points(spec: SweepSpec) -> SweepPlan:
    """Enumerate and validate every point of `spec`, without running any.

    Args:
        spec: The sweep specification.

    Returns:
        The plan: valid points in fixed order (first axis slowest), the
        invalid ones with their reasons, and the count collapsed because
        two points resolved to the same configuration.
    """
    points: list[SweepPoint] = []
    invalid: list[InvalidPoint] = []
    seen: set[str] = set()
    collapsed = 0
    keys = [axis.key for axis in spec.axes]
    combinations = itertools.product(*(axis.values for axis in spec.axes))
    for index, combination in enumerate(combinations):
        coordinates = dict(zip(keys, combination, strict=True))
        try:
            mapping = _point_mapping(spec, index, coordinates)
            params = SimulationParams.from_mapping(mapping)
        except (ValueError, TypeError, KeyError) as error:
            invalid.append(InvalidPoint(index, coordinates, str(error)))
            continue
        run_id = deterministic_run_id(params)
        if run_id in seen:
            collapsed += 1
            continue
        seen.add(run_id)
        points.append(SweepPoint(index, coordinates, mapping, run_id))
    return SweepPlan(tuple(points), tuple(invalid), collapsed, spec.grid_size)


def work_estimate(spec: SweepSpec, plan: SweepPlan) -> dict[str, int]:
    """Return an upper bound on the work a plan represents.

    The cost of a point is not known until a pilot runs, so this is the
    number of points times replicates times the generation cap: a ceiling
    that convergence usually undercuts.

    Returns:
        `points`, `replicates` per point, `max_generations`, and
        `replicate_generations` (their product).
    """
    replicates = _integer_setting(spec.base, "n_replicates")
    generations = _integer_setting(spec.base, "max_generations")
    return {
        "points": len(plan.points),
        "replicates": replicates,
        "max_generations": generations,
        "replicate_generations": len(plan.points) * replicates * generations,
    }


def expand_axis(key: str, definition: object) -> SweepAxis:
    """Build an axis from a list of values or a `{start, stop, count}` range.

    A range is `{"start", "stop", "count", "scale": "linear" | "log"}`;
    `scale` defaults to the key's own default. The result stores the
    expanded values, so what ran is unambiguous. An integer key rounds
    and de-duplicates a range (a log range over `d` repeats small
    integers); an explicit list keeps its values and rejects a repeat.

    Raises:
        ValueError: The key is not sweepable, or the definition is
            malformed or has a value outside the key's legal range.
    """
    sweepable = SWEEPABLE_KEYS.get(key)
    if sweepable is None:
        allowed = ", ".join(sorted(SWEEPABLE_KEYS))
        raise ValueError(f"cannot sweep {key!r}; the sweepable keys are {allowed}")
    if isinstance(definition, Mapping):
        values = _range_values(sweepable, definition)
    elif isinstance(definition, Sequence) and not isinstance(definition, str):
        values = tuple(_coerce(sweepable, item) for item in definition)
        if len(set(values)) != len(values):
            raise ValueError(f"sweep axis {key!r} lists a value more than once")
    else:
        raise ValueError(
            f"sweep axis {key!r} must be a list of values or a start/stop/count range"
        )
    if not values:
        raise ValueError(f"sweep axis {key!r} has no values")
    return SweepAxis(key, values)


def spec_from_config(config: Mapping[str, Any]) -> tuple[SweepSpec, str | None]:
    """Split a sweep file into its `SweepSpec` and the optional name.

    A sweep file is an ordinary configuration plus a `sweep:` block; the
    block is removed before the rest becomes the base, so the ordinary
    configuration validator never sees it. The `sweep:` block holds
    `axes` (a mapping of key to a list or a range, in order),
    and optionally `name`, `seed_policy` and `strategy`.

    Raises:
        ValueError: The `sweep:` block is missing or malformed.
    """
    block = config.get("sweep")
    if not isinstance(block, Mapping):
        raise ValueError("a sweep file needs a 'sweep:' block")
    unknown = set(block) - {"name", "axes", "seed_policy", "strategy"}
    if unknown:
        names = ", ".join(sorted(str(key) for key in unknown))
        raise ValueError(f"unknown sweep key(s): {names}")
    raw_axes = block.get("axes")
    if not isinstance(raw_axes, Mapping) or not raw_axes:
        raise ValueError("sweep 'axes' must be a mapping of key to values or range")
    base = {key: value for key, value in config.items() if key != "sweep"}
    name = block.get("name")
    if name is not None and not isinstance(name, str):
        raise ValueError("sweep 'name' must be text")
    spec = SweepSpec(
        base=base,
        axes=tuple(expand_axis(str(key), value) for key, value in raw_axes.items()),
        strategy=str(block.get("strategy", "grid")),
        seed_policy=_seed_policy(block.get("seed_policy", "spaced")),
    )
    return spec, name


def _apply_axis(mapping: dict[str, Any], key: str, value: CoordinateValue) -> None:
    """Apply one axis value to a point's configuration mapping in place."""
    if key == "N":
        mapping["N"] = int(value) * int(mapping["ploidy"])
    elif key in {"d", "mu", "deme_weighting"}:
        mapping[key] = value
    elif key == "m":
        current = mapping["m"]
        mapping["m"] = (
            {**current, "rate": value} if isinstance(current, Mapping) else value
        )
    elif key == "topology":
        mapping["m"] = _with_topology(mapping["m"], str(value))


def _with_topology(current: object, topology: str) -> object:
    """Return `m` re-expressed on `topology`, keeping the migration rate."""
    rate = current["rate"] if isinstance(current, Mapping) else current
    if topology == "island":
        return rate
    replaced: dict[str, Any] = {"topology": topology, "rate": rate}
    if isinstance(current, Mapping) and topology == "torus":
        for extra in ("rows", "columns"):
            if extra in current:
                replaced[extra] = current[extra]
    return replaced


def _check_base_supports_axes(base: Mapping[str, Any], keys: set[str]) -> None:
    """Reject a base that the chosen axes cannot be applied to."""
    if "N" in keys and "ploidy" not in base:
        raise ValueError(
            "an N axis counts individuals, so the base configuration needs a "
            "ploidy (1 to 4)"
        )
    if keys & {"m", "topology"}:
        migration = base.get("m")
        if migration is None:
            raise ValueError("the base configuration needs an m to sweep m or topology")
        if isinstance(migration, Sequence) and not isinstance(migration, str | Mapping):
            raise ValueError("cannot sweep m or topology over a migration matrix")
    if "mu" in keys and "mu_b" in base:
        raise ValueError("cannot sweep mu when the base configuration sets mu_b")


def _coerce(sweepable: SweepKey, value: object) -> CoordinateValue:
    """Return `value` as this key's type, or raise a `ValueError` naming it."""
    key = sweepable.key
    if sweepable.kind == "choice":
        if not isinstance(value, str) or value not in sweepable.choices:
            raise ValueError(f"{key} must be one of {list(sweepable.choices)}")
        return value
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{key} values must be numbers, not {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{key} values must be finite")
    number: float = float(value)
    if sweepable.kind == "int":
        if number != int(number):
            raise ValueError(f"{key} values must be whole numbers, not {value!r}")
        number = int(number)
    _check_bounds(sweepable, number)
    return int(number) if sweepable.kind == "int" else _clean(number)


def _check_bounds(sweepable: SweepKey, number: float) -> None:
    """Raise if `number` is outside the key's legal range."""
    if sweepable.minimum is not None and number < sweepable.minimum:
        raise ValueError(f"{sweepable.key} must be at least {sweepable.minimum:g}")
    if sweepable.maximum is not None and number > sweepable.maximum:
        raise ValueError(f"{sweepable.key} must be at most {sweepable.maximum:g}")


def _integer_setting(mapping: Mapping[str, Any], key: str) -> int:
    """Return `mapping[key]` (or its default) as an integer, or raise."""
    value = mapping.get(key, PARAMETER_DEFAULTS.get(key))
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be a whole number")
    return value


def _clean(value: float) -> float:
    """Round to `_SIGNIFICANT_DIGITS` so expanded values are platform-stable."""
    return float(f"{value:.{_SIGNIFICANT_DIGITS}g}")


def _point_mapping(
    spec: SweepSpec, index: int, coordinates: Mapping[str, CoordinateValue]
) -> dict[str, Any]:
    """Return the configuration mapping for one grid position."""
    mapping: dict[str, Any] = dict(spec.base)
    for key, value in coordinates.items():
        _apply_axis(mapping, key, value)
    if spec.seed_policy == "spaced":
        replicates = _integer_setting(mapping, "n_replicates")
        mapping["seed"] = _integer_setting(spec.base, "seed") + index * replicates
    return mapping


def _range_values(
    sweepable: SweepKey, definition: Mapping[Any, Any]
) -> tuple[CoordinateValue, ...]:
    """Expand a `{start, stop, count, scale}` range for a numeric key."""
    key = sweepable.key
    if sweepable.kind == "choice":
        raise ValueError(f"{key} takes a list of choices, not a range")
    unknown = set(definition) - {"start", "stop", "count", "scale"}
    if unknown:
        names = ", ".join(sorted(str(name) for name in unknown))
        raise ValueError(f"unknown range key(s) for {key}: {names}")
    missing = {"start", "stop", "count"} - set(definition)
    if missing:
        raise ValueError(f"the {key} range needs {', '.join(sorted(missing))}")
    count = definition["count"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError(f"the {key} range count must be a whole number of at least 1")
    start = float(_coerce(sweepable, definition["start"]))
    stop = float(_coerce(sweepable, definition["stop"]))
    scale = definition.get("scale", sweepable.scale)
    if scale not in {"linear", "log"}:
        raise ValueError(f"the {key} range scale must be 'linear' or 'log'")
    if scale == "log" and (start <= 0 or stop <= 0):
        raise ValueError(f"a log range for {key} needs start and stop above 0")
    raw = _spaced(start, stop, count, log=scale == "log")
    values: list[CoordinateValue] = []
    for number in raw:
        value: CoordinateValue = (
            round(number) if sweepable.kind == "int" else _clean(number)
        )
        if value not in values:
            values.append(value)
    return tuple(values)


def _spaced(start: float, stop: float, count: int, *, log: bool) -> list[float]:
    """Return `count` values from `start` to `stop`, both exact."""
    if count == 1:
        return [start]
    if log:
        ratio = (stop / start) ** (1.0 / (count - 1))
        middle = [start * ratio**step for step in range(1, count - 1)]
    else:
        step_size = (stop - start) / (count - 1)
        middle = [start + step_size * step for step in range(1, count - 1)]
    return [start, *middle, stop]


def _seed_policy(value: object) -> SeedPolicy:
    """Return a validated seed policy."""
    if value == "spaced":
        return "spaced"
    if value == "same":
        return "same"
    raise ValueError(f"sweep seed_policy must be one of {list(SEED_POLICIES)}")


def _stored_axis(entry: object) -> SweepAxis:
    """Rebuild one stored axis, re-validating its values."""
    if not isinstance(entry, Mapping) or "key" not in entry or "values" not in entry:
        raise ValueError("each sweep axis needs a key and its values")
    key = str(entry["key"])
    sweepable = SWEEPABLE_KEYS.get(key)
    if sweepable is None:
        raise ValueError(f"cannot sweep {key!r}")
    values = entry["values"]
    if not isinstance(values, Sequence) or isinstance(values, str):
        raise ValueError(f"sweep axis {key!r} values must be a list")
    return SweepAxis(key, tuple(_coerce(sweepable, item) for item in values))
