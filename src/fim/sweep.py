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
- **`N` counts individuals.** `N` means individuals per deme in the base
  configuration and on an axis alike, the number the botanist sees
  everywhere else in the app; the base's required `ploidy` turns it into
  gene copies when a point is built (`SimulationParams.from_mapping`).
"""

from __future__ import annotations

import csv
import hashlib
import itertools
import json
import math
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

import yaml

from fim.engine import deterministic_run_id
from fim.model.params import PARAMETER_DEFAULTS, PLOIDY_WORDS, SimulationParams
from fim.spec.fields import CONVERTED_KEYS, FIELD_MAP

SWEEP_SPEC_VERSION: Final = 2
"""Schema version of the stored `sweep_spec` mapping.

Version 2 adds the `zip` and `list` strategies (and `rows`); a version 1
spec is a grid and is still read."""

READABLE_SPEC_VERSIONS: Final = (1, 2)
"""Stored spec versions `SweepSpec.from_dict` accepts."""

SIZE_CONFIRMATION_THRESHOLD: Final = 100
"""Points at or above which a plan should ask before running."""

SEED_POLICIES: Final = ("spaced", "same")
"""`spaced`: independent points; `same`: every point uses the base seed."""

STRATEGIES: Final = ("grid", "zip", "list")
"""`grid`: every combination; `zip`: axes advance together; `list`: explicit rows."""

_SIGNIFICANT_DIGITS: Final = 12
"""Digits kept when expanding a range, so ids do not depend on float noise."""

AxisKind = Literal["int", "float", "choice"]
Scale = Literal["linear", "log"]
SeedPolicy = Literal["spaced", "same"]
CoordinateValue = int | float | str


_BOOLEAN_CHOICES: Final = ("true", "false")
"""The two words a switch takes in a spec, so every value is text."""

ApplyAxis = Callable[[dict[str, Any], CoordinateValue], None]
RequireBase = Callable[[Mapping[str, Any]], None]


def _apply_n(mapping: dict[str, Any], value: CoordinateValue) -> None:
    """Set the population size; an axis value counts individuals per deme."""
    mapping["N"] = int(value)


def _apply_migration_rate(mapping: dict[str, Any], value: CoordinateValue) -> None:
    """Set the migration rate, keeping a topology the base already names."""
    current = mapping["m"]
    mapping["m"] = {**current, "rate": value} if isinstance(current, Mapping) else value


def _apply_topology(mapping: dict[str, Any], value: CoordinateValue) -> None:
    """Re-express the migration setting on a topology, keeping its rate."""
    mapping["m"] = _with_topology(mapping["m"], str(value))


def _apply_migrants(mapping: dict[str, Any], value: CoordinateValue) -> None:
    """Set `m` so that `N * m` migrants per deme arrive each generation.

    `N` is individuals per deme, the base's or its own axis's, whichever the
    point has. This runs after every plain axis (see `_apply_axes`).
    """
    individuals = mapping["N"]
    _apply_migration_rate(mapping, _clean(float(value) / individuals))


def _require_migrants_base(base: Mapping[str, Any]) -> None:
    """Reject a base that cannot turn `N * m` into a migration rate."""
    _require_migration(base)
    population = base.get("N")
    if isinstance(population, bool) or not isinstance(population, int | float):
        raise ValueError(
            "cannot sweep Nm unless the base N is one number of individuals per deme"
        )


def _require_migration(base: Mapping[str, Any]) -> None:
    """Reject a base whose `m` cannot take a rate or a topology."""
    migration = base.get("m")
    if migration is None:
        raise ValueError("the base configuration needs an m to sweep m or topology")
    if isinstance(migration, Sequence) and not isinstance(migration, str | Mapping):
        raise ValueError("cannot sweep m or topology over a migration matrix")


def _require_single_mutation_rate(base: Mapping[str, Any]) -> None:
    """Reject a base that sets per-locus rates, which one `mu` would override."""
    if "mu_b" in base:
        raise ValueError("cannot sweep mu when the base configuration sets mu_b")


@dataclass(frozen=True, slots=True)
class AxisDefinition:
    """One parameter a sweep may vary, with the metadata its controls need.

    `apply` and `requires` are module-level functions, never lambdas, so a
    definition survives a process boundary.

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
        apply: Writes one value into a point's configuration mapping;
            `None` means a plain `mapping[key] = value`.
        requires: Raises `ValueError` when the base configuration cannot
            take this axis; `None` means any base can.
        generic: Whether the definition was synthesized from a configuration
            key rather than curated.
        derived: Whether the axis is worked out from other settings (it is
            applied after every plain axis, so it sees their values).
        convention: What the axis's numbers count, recorded in the stored
            spec so the sweep stays reproducible; empty when obvious.
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
    apply: ApplyAxis | None = None
    requires: RequireBase | None = None
    generic: bool = False
    derived: bool = False
    convention: str = ""

    @property
    def is_boolean(self) -> bool:
        """Return whether this is an on/off switch, written `true` or `false`."""
        return self.choices == _BOOLEAN_CHOICES

    def apply_to(self, mapping: dict[str, Any], value: CoordinateValue) -> None:
        """Write `value` into `mapping`, the configuration of one point."""
        if self.apply is not None:
            self.apply(mapping, value)
        elif self.is_boolean:
            mapping[self.key] = value == "true"
        else:
            mapping[self.key] = value

    def to_dict(self) -> dict[str, object]:
        """Return the description of this key as plain JSON-ready values."""
        return {
            "key": self.key,
            "label": self.label,
            "kind": self.kind,
            "unit": self.unit,
            "scale": self.scale,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "choices": list(self.choices),
            "display_domain": list(self.display_domain)
            if self.display_domain
            else None,
            "closed_form": self.closed_form,
            "generic": self.generic,
            "derived": self.derived,
            "convention": self.convention,
        }

    def check_base(self, base: Mapping[str, Any]) -> None:
        """Raise `ValueError` if `base` cannot take this axis."""
        if self.requires is not None:
            self.requires(base)


SWEEPABLE_KEYS: Final[dict[str, AxisDefinition]] = {
    "N": AxisDefinition(
        key="N",
        label="N (individuals per deme)",
        kind="int",
        unit="individuals",
        scale="log",
        minimum=1,
        display_domain=(5.0, 2500.0),
        closed_form=True,
        apply=_apply_n,
    ),
    "d": AxisDefinition(
        key="d",
        label="d (demes)",
        kind="int",
        unit="demes",
        scale="linear",
        minimum=2,
        display_domain=(2.0, 50.0),
        closed_form=True,
    ),
    "m": AxisDefinition(
        key="m",
        label="m (migration rate)",
        kind="float",
        unit="probability",
        scale="log",
        minimum=0.0,
        maximum=1.0,
        display_domain=(0.0001, 0.5),
        closed_form=True,
        apply=_apply_migration_rate,
        requires=_require_migration,
    ),
    "mu": AxisDefinition(
        key="mu",
        label="μ (mutation rate)",
        kind="float",
        unit="probability",
        scale="log",
        minimum=0.0,
        maximum=1.0,
        display_domain=(0.000001, 0.1),
        closed_form=True,
        requires=_require_single_mutation_rate,
    ),
    "Nm": AxisDefinition(
        key="Nm",
        label="N·m (migrants per deme per generation)",
        kind="float",
        unit="migrants per deme per generation",
        scale="log",
        minimum=0.0,
        display_domain=(0.01, 100.0),
        apply=_apply_migrants,
        requires=_require_migrants_base,
        derived=True,
        convention="N is individuals per deme",
    ),
    "topology": AxisDefinition(
        key="topology",
        label="migration topology",
        kind="choice",
        unit="topology",
        choices=("island", "ring", "linear", "torus"),
        apply=_apply_topology,
        requires=_require_migration,
    ),
    "deme_weighting": AxisDefinition(
        key="deme_weighting",
        label="deme weighting",
        kind="choice",
        unit="weighting",
        choices=("equal", "size"),
    ),
}
"""The curated parameters a sweep may vary, with labels, ranges and rules."""

# Configuration keys no axis can take, with the reason shown to the person who
# asked. Everything else that is one number, one word or one switch is open.
_UNSWEEPABLE_REASONS: Final[dict[str, str]] = {
    "seed": "seeds are set by the sweep's seed_policy",
    "expert": "it is a group of settings, not one value",
    "mu_b": "it is a list of per-locus rates, not one value",
    "loci": "it is a list of loci, not one value",
    "p_0": "it is a list of starting frequencies, not one value",
    "convergence_statistic": "it is a list of statistics, not one value",
}

# Keys whose default is the "derive it" sentinel (`None`), so the default
# cannot say what type the key takes.
_SENTINEL_DEFAULT_KINDS: Final[dict[str, AxisKind]] = {
    "convergence_burn_in": "int",
    "max_generations": "int",
    "statistic_precision": "float",
    "replicate_averaging_window": "int",
    "trajectory_thinning_start": "int",
    "max_concurrent_replicates": "int",
    "equilibrium_convergence_window": "int",
    "equilibrium_convergence_tolerance": "float",
    "equilibrium_max_generations": "int",
    "ploidy": "choice",
}

# The words a text key accepts. A key missing here accepts any word and each
# point is checked when the plan is built.
_GENERIC_CHOICES: Final[dict[str, tuple[str, ...]]] = {
    "migrant_sampling": ("continuous", "stochastic"),
    "mutation_model": ("infinite_alleles", "finite_alleles"),
    "locus_aggregation": ("ratio_of_means", "mean_of_ratios"),
    "convergence_combinator": ("any", "all"),
    "convergence_estimate": ("mean_of_values", "value_of_means", "auto"),
    "precision_method": ("interval", "planned_replicates"),
    "trajectory_retention": ("full", "thinned", "tapered"),
    "engine_backend": ("lineal", "auto", "generational", "generational-vector"),
    "jit": ("off", "numba"),
    "ploidy": tuple(PLOIDY_WORDS.values()),
}

_GENERIC_BOUNDS: Final[dict[str, tuple[float | None, float | None]]] = {
    "n_loci": (1, None),
    "locus_lengths": (1, None),
    "initial_allele_count": (1, None),
    "n_replicates": (1, None),
    "trajectory_keep_last": (1, None),
    "precision": (0.0, None),
    "confidence": (0.0, 1.0),
}

_LOG_SCALE_KEYS: Final = frozenset({"precision", "initial_concentration"})


def _generic_kind(key: str) -> AxisKind | None:
    """Return the axis kind a generic key takes, or `None` if it has none."""
    if key in _SENTINEL_DEFAULT_KINDS:
        return _SENTINEL_DEFAULT_KINDS[key]
    default = PARAMETER_DEFAULTS.get(key)
    if isinstance(default, bool | str):
        return "choice"
    if isinstance(default, int):
        return "int"
    if isinstance(default, float):
        return "float"
    return None


def _build_generic_keys() -> dict[str, AxisDefinition]:
    """Build a definition for every scalar configuration key not curated."""
    names = {entry.key for entry in FIELD_MAP} | set(CONVERTED_KEYS)
    found: dict[str, AxisDefinition] = {}
    for key in sorted(names - SWEEPABLE_KEYS.keys() - _UNSWEEPABLE_REASONS.keys()):
        kind = _generic_kind(key)
        if kind is None:
            continue
        default = PARAMETER_DEFAULTS.get(key)
        boolean = isinstance(default, bool)
        minimum, maximum = _GENERIC_BOUNDS.get(key, (None, None))
        found[key] = AxisDefinition(
            key=key,
            label=key,
            kind=kind,
            unit="",
            scale="log" if key in _LOG_SCALE_KEYS else "linear",
            minimum=minimum,
            maximum=maximum,
            choices=_BOOLEAN_CHOICES if boolean else _GENERIC_CHOICES.get(key, ()),
            generic=True,
        )
    return found


GENERIC_KEYS: Final[dict[str, AxisDefinition]] = _build_generic_keys()
"""Every other scalar configuration key a sweep may vary, by key.

Types come from the key's default (or `_SENTINEL_DEFAULT_KINDS`); each
generic entry has no label beyond its key and no display domain.
"""


def axis_definition(key: str) -> AxisDefinition:
    """Return the definition of a sweepable key, curated or generic.

    Raises:
        ValueError: The key cannot be swept; the message says why, or lists
            the curated keys when the key is unknown.
    """
    definition = SWEEPABLE_KEYS.get(key) or GENERIC_KEYS.get(key)
    if definition is not None:
        return definition
    reason = _UNSWEEPABLE_REASONS.get(key)
    if reason is not None:
        raise ValueError(f"cannot sweep {key!r}: {reason}")
    curated = ", ".join(sorted(SWEEPABLE_KEYS))
    raise ValueError(
        f"cannot sweep {key!r}; it is not a scalar configuration key "
        f"(the common ones are {curated}; `fim sweep keys` lists them all)"
    )


def sweepable_key_names() -> tuple[str, ...]:
    """Return every sweepable key: the curated ones, then the generic ones."""
    return (*SWEEPABLE_KEYS, *sorted(GENERIC_KEYS))


@dataclass(frozen=True, slots=True)
class SweepAxis:
    """One varied parameter and the exact values it takes, in order."""

    key: str
    values: tuple[CoordinateValue, ...]

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-serializable stored form.

        A derived axis also records what its numbers count (`convention`).
        """
        stored: dict[str, object] = {"key": self.key, "values": list(self.values)}
        definition = SWEEPABLE_KEYS.get(self.key)
        if definition is not None and definition.convention:
            stored["convention"] = definition.convention
        return stored


@dataclass(frozen=True, slots=True)
class SweepSpec:
    """What is held fixed (`base`) and what varies (`axes`).

    Attributes:
        base: A complete configuration mapping, exactly what
            `SimulationParams.from_mapping` accepts. Its `N` is
            individuals per deme, and it must say `ploidy`; `N` is
            overwritten when it is an axis.
        axes: The varied keys, first axis slowest in the enumeration.
        strategy: `grid` (every combination, first axis slowest), `zip`
            (the axes advance together and must have equal lengths) or
            `list` (the explicit `rows`; see `from_rows`).
        seed_policy: `spaced` gives point `i` the seed `base seed + i *
            n_replicates`, where `i` is the point's position in the
            strategy's order, so no two points share a replicate seed;
            `same` gives every point the base seed.
        rows: For `list`, one mapping of axis key to value per point, in
            order. Empty otherwise.
    """

    base: Mapping[str, Any]
    axes: tuple[SweepAxis, ...]
    strategy: str = "grid"
    seed_policy: SeedPolicy = "spaced"
    rows: tuple[Mapping[str, CoordinateValue], ...] = ()

    def __post_init__(self) -> None:
        """Validate the axes against the base and the sweepable-key table."""
        if self.strategy not in STRATEGIES:
            raise ValueError(f"sweep strategy must be one of {list(STRATEGIES)}")
        if self.seed_policy not in SEED_POLICIES:
            raise ValueError(f"sweep seed_policy must be one of {list(SEED_POLICIES)}")
        if not self.axes:
            raise ValueError("a sweep needs at least one axis")
        if self.strategy != "list" and self.rows:
            raise ValueError("only the list strategy takes rows")
        keys = [axis.key for axis in self.axes]
        if len(set(keys)) != len(keys):
            raise ValueError("a sweep axis key may appear only once")
        for axis in self.axes:
            axis_definition(axis.key)
            if not axis.values:
                raise ValueError(f"sweep axis {axis.key!r} has no values")
        if {"Nm", "m"} <= set(keys):
            raise ValueError("Nm sets m, so a sweep cannot vary both Nm and m")
        _check_base_supports_axes(self.base, set(keys))
        _check_strategy(self, keys)

    @classmethod
    def from_rows(
        cls,
        base: Mapping[str, Any],
        rows: Sequence[Mapping[str, object]],
        seed_policy: SeedPolicy = "spaced",
    ) -> SweepSpec:
        """Build a `list` spec from explicit rows, one mapping per point.

        Every row names the same keys. Each key becomes an axis holding that
        key's distinct values in order of first appearance, which is what
        the results views plot against.

        Raises:
            ValueError: There are no rows, a row names different keys from
                the first, or a value does not fit its key.
        """
        if not rows:
            raise ValueError("a list sweep needs at least one point")
        keys = list(rows[0])
        coerced: list[dict[str, CoordinateValue]] = []
        for row in rows:
            if set(row) != set(keys):
                raise ValueError("every point of a list sweep needs the same keys")
            coerced.append(
                {key: _coerce(axis_definition(key), row[key]) for key in keys}
            )
        # A repeated point is dropped here, keeping the first, so the seeds of
        # the points that remain follow the order the person gave.
        first: dict[tuple[CoordinateValue, ...], dict[str, CoordinateValue]] = {}
        for row in coerced:
            first.setdefault(tuple(row[key] for key in keys), row)
        coerced = list(first.values())
        axes = tuple(
            SweepAxis(key, tuple(dict.fromkeys(row[key] for row in coerced)))
            for key in keys
        )
        return cls(
            base=dict(base),
            axes=axes,
            strategy="list",
            seed_policy=seed_policy,
            rows=tuple(coerced),
        )

    @property
    def grid_size(self) -> int:
        """Return the number of positions the strategy enumerates."""
        if self.strategy == "list":
            return len(self.rows)
        if self.strategy == "zip":
            return len(self.axes[0].values)
        return math.prod(len(axis.values) for axis in self.axes)

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-serializable stored form (without the points)."""
        stored: dict[str, object] = {
            "spec_version": SWEEP_SPEC_VERSION,
            "strategy": self.strategy,
            "base": dict(self.base),
            "axes": [axis.to_dict() for axis in self.axes],
            "seed_policy": self.seed_policy,
        }
        if self.strategy == "list":
            stored["rows"] = [dict(row) for row in self.rows]
        return stored

    def digest(self, plan: SweepPlan | None = None) -> str:
        """Return a fingerprint of this experiment: the spec and its points.

        The SHA-256 of the canonical JSON (sorted keys, no spaces) of the
        stored form of the spec and the ordered run ids of its valid points.
        Two people who hold the same digest hold the same experiment: the
        same base, axes, seed policy and software-independent point ids.
        The order of keys in the base does not matter.

        Args:
            plan: The spec's enumerated plan; computed here if omitted.

        Returns:
            `"sha256:"` followed by 64 hexadecimal digits.
        """
        found = plan if plan is not None else enumerate_points(self)
        document = {
            "spec": self.to_dict(),
            "points": [point.run_id for point in found.points],
        }
        text = json.dumps(
            document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()

    def to_sweep_file(self, name: str | None = None) -> str:
        """Write this spec as a sweep file, the inverse of `spec_from_config`.

        The file holds the base configuration and a `sweep:` block with each
        axis as its exact list of values (a range is written out, so the file
        does not depend on how the values were first described), so reading
        it back gives an equal spec.

        Args:
            name: The sweep's name, written into the `sweep:` block.

        Returns:
            YAML text.
        """
        block: dict[str, Any] = {}
        if name is not None:
            block["name"] = name
        block["strategy"] = self.strategy
        block["seed_policy"] = self.seed_policy
        if self.strategy == "list":
            block["points"] = [dict(row) for row in self.rows]
        else:
            block["axes"] = {axis.key: list(axis.values) for axis in self.axes}
        document = {**dict(self.base), "sweep": block}
        return yaml.safe_dump(document, sort_keys=False, default_flow_style=None)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SweepSpec:
        """Rebuild a spec from its stored form (`to_dict`).

        Raises:
            ValueError: The mapping is malformed or its version is unknown.
        """
        version = value.get("spec_version")
        if version not in READABLE_SPEC_VERSIONS:
            raise ValueError(f"unsupported sweep spec_version: {version!r}")
        base = value.get("base")
        if not isinstance(base, Mapping):
            raise ValueError("sweep spec 'base' must be an object")
        if value.get("strategy") == "list":
            return cls.from_rows(
                base,
                _stored_rows(value.get("rows")),
                _seed_policy(value.get("seed_policy", "spaced")),
            )
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
    for index, coordinates in enumerate(_STRATEGY_POINTS[spec.strategy](spec)):
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


def _grid_points(spec: SweepSpec) -> Iterator[dict[str, CoordinateValue]]:
    """Yield every combination of the axes, first axis slowest."""
    keys = [axis.key for axis in spec.axes]
    for combination in itertools.product(*(axis.values for axis in spec.axes)):
        yield dict(zip(keys, combination, strict=True))


def _zip_points(spec: SweepSpec) -> Iterator[dict[str, CoordinateValue]]:
    """Yield the axes' values together, position by position."""
    keys = [axis.key for axis in spec.axes]
    for combination in zip(*(axis.values for axis in spec.axes), strict=True):
        yield dict(zip(keys, combination, strict=True))


def _list_points(spec: SweepSpec) -> Iterator[dict[str, CoordinateValue]]:
    """Yield the explicit rows in the order given."""
    for row in spec.rows:
        yield dict(row)


_STRATEGY_POINTS: Final[
    dict[str, Callable[[SweepSpec], Iterator[dict[str, CoordinateValue]]]]
] = {"grid": _grid_points, "zip": _zip_points, "list": _list_points}
"""Strategy name to the function that yields its coordinates in order."""


def derived_values(spec: SweepSpec, point: SweepPoint) -> dict[str, CoordinateValue]:
    """Return what a derived axis set at a point, for display.

    For an `Nm` axis that is the migration rate `m` it worked out.

    Returns:
        A mapping of setting to value; empty when the sweep has no derived
        axis.
    """
    shown: dict[str, CoordinateValue] = {}
    if any(axis.key == "Nm" for axis in spec.axes):
        migration = point.params["m"]
        shown["m"] = migration["rate"] if isinstance(migration, Mapping) else migration
    return shown


def point_run_name(study_name: str, point: SweepPoint) -> str:
    """Return the name a sweep point's run is given: Study name plus its values.

    For example `"Island size sweep N=50, m=0.01"`. The coordinates are
    what tells one point's run from its siblings in the same Study.

    Args:
        study_name: The sweep Study's name.
        point: The point the run computes.

    Returns:
        The run name.
    """
    coordinates = ", ".join(
        f"{key}={_format_coordinate(value)}" for key, value in point.coordinates.items()
    )
    return f"{study_name} sweep {coordinates}"


def point_run_description(
    study_name: str, spec: SweepSpec, point: SweepPoint, position: int, total: int
) -> str:
    """Return the description a sweep point's run is given.

    Says which sweep the run belongs to, where it sits in that sweep,
    and what every axis ranges over, so a run met on its own (in a
    Study it was copied into, say) still explains itself.

    Args:
        study_name: The sweep Study's name.
        spec: The sweep specification, for the axis ranges.
        point: The point the run computes.
        position: The point's 1-based place among the planned points.
        total: How many points are planned.

    Returns:
        One sentence, for example `'Point 3 of 8 of the sweep "Island
        size": N=50 of N=[5..2500] (4 values); m=0.01 of m=[0.01, 0.1].'`.
    """
    axes = "; ".join(
        f"{axis.key}={_format_coordinate(point.coordinates[axis.key])} of "
        f"{axis.key}={_format_axis_values(axis.values)}"
        for axis in spec.axes
    )
    return f'Point {position} of {total} of the sweep "{study_name}": {axes}.'


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
    sweepable = axis_definition(key)
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
    unknown = set(block) - {"name", "axes", "seed_policy", "strategy", "points"}
    if unknown:
        names = ", ".join(sorted(str(key) for key in unknown))
        raise ValueError(f"unknown sweep key(s): {names}")
    base = {key: value for key, value in config.items() if key != "sweep"}
    name = block.get("name")
    if name is not None and not isinstance(name, str):
        raise ValueError("sweep 'name' must be text")
    policy = _seed_policy(block.get("seed_policy", "spaced"))
    strategy = str(block.get("strategy", "list" if "points" in block else "grid"))
    if strategy == "list":
        if "axes" in block:
            raise ValueError("a list sweep takes 'points', not 'axes'")
        return SweepSpec.from_rows(
            base, _stored_rows(block.get("points")), policy
        ), name
    if "points" in block:
        raise ValueError("'points' belongs to the list strategy")
    raw_axes = block.get("axes")
    if not isinstance(raw_axes, Mapping) or not raw_axes:
        raise ValueError("sweep 'axes' must be a mapping of key to values or range")
    spec = SweepSpec(
        base=base,
        axes=tuple(expand_axis(str(key), value) for key, value in raw_axes.items()),
        strategy=strategy,
        seed_policy=policy,
    )
    return spec, name


def read_points_csv(path: Path) -> list[dict[str, object]]:
    """Read the points of a list sweep from a CSV file with a header row.

    Each cell becomes a whole number, a decimal number or text, in that
    order of preference, so `8` is an integer and `0.01` a float. Key names
    and values are then checked like any other list sweep.

    Raises:
        ValueError: The file has no header or no points.
    """
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"{path}: the points file needs a header row of keys")
        rows = [
            {key: _csv_value(text) for key, text in row.items() if key is not None}
            for row in reader
        ]
    if not rows:
        raise ValueError(f"{path}: the points file has no points")
    return rows


def resolve_points_file(config: Mapping[str, Any], directory: Path) -> dict[str, Any]:
    """Return `config` with a `sweep.points_file` replaced by its `points`.

    Args:
        config: A sweep file's contents.
        directory: What a relative `points_file` is relative to (the sweep
            file's folder).

    Raises:
        ValueError: The block also lists `points`, or the file is unreadable.
    """
    block = config.get("sweep")
    if not isinstance(block, Mapping) or "points_file" not in block:
        return dict(config)
    if "points" in block:
        raise ValueError("a sweep takes 'points' or 'points_file', not both")
    target = directory / str(block["points_file"])
    try:
        rows = read_points_csv(target)
    except OSError as error:
        raise ValueError(f"cannot read the points file {target}: {error}") from error
    replaced = {key: value for key, value in block.items() if key != "points_file"}
    return {**config, "sweep": {**replaced, "points": rows}}


def _csv_value(text: str) -> object:
    """Return one CSV cell as an int, a float or the text itself."""
    cell = text.strip()
    for convert in (int, float):
        try:
            return convert(cell)
        except ValueError:
            continue
    return cell


def apply_coordinates(
    base: Mapping[str, Any], coordinates: Mapping[str, CoordinateValue]
) -> dict[str, Any]:
    """Return `base` with each axis value applied, and no seed set.

    The same substitution `enumerate_points` performs for one point (an `N`
    value counts individuals, a `topology` value re-expresses `m`, and so
    on), exposed so a caller can evaluate something at arbitrary
    coordinates, such as the closed-form theory along an axis.

    Raises:
        ValueError: `base` cannot take one of the axes (for example an `m`
            axis with no `m` in the base).
    """
    _check_base_supports_axes(base, set(coordinates))
    mapping: dict[str, Any] = dict(base)
    _apply_axes(mapping, coordinates)
    return mapping


def _apply_axes(
    mapping: dict[str, Any], coordinates: Mapping[str, CoordinateValue]
) -> None:
    """Apply every axis value to `mapping`; derived axes last.

    A derived axis (`Nm`) is worked out from the other settings, so it must
    see the final `N` whatever order the axes were listed in.
    """
    ordered = sorted(
        coordinates.items(),
        key=lambda item: item[0] in SWEEPABLE_KEYS and SWEEPABLE_KEYS[item[0]].derived,
    )
    for key, value in ordered:
        _apply_axis(mapping, key, value)


def _apply_axis(mapping: dict[str, Any], key: str, value: CoordinateValue) -> None:
    """Apply one axis value to a point's configuration mapping in place."""
    definition = SWEEPABLE_KEYS.get(key) or GENERIC_KEYS.get(key)
    if definition is not None:
        definition.apply_to(mapping, value)


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
    for key in sorted(keys):
        definition = SWEEPABLE_KEYS.get(key) or GENERIC_KEYS.get(key)
        if definition is not None:
            definition.check_base(base)


def _coerce(sweepable: AxisDefinition, value: object) -> CoordinateValue:
    """Return `value` as this key's type, or raise a `ValueError` naming it."""
    key = sweepable.key
    if sweepable.kind == "choice":
        if sweepable.is_boolean and isinstance(value, bool):
            return "true" if value else "false"
        if not isinstance(value, str):
            raise ValueError(f"{key} must be text, not {value!r}")
        if sweepable.choices and value not in sweepable.choices:
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


def _check_bounds(sweepable: AxisDefinition, number: float) -> None:
    """Raise if `number` is outside the key's legal range."""
    if sweepable.minimum is not None and number < sweepable.minimum:
        raise ValueError(f"{sweepable.key} must be at least {sweepable.minimum:g}")
    if sweepable.maximum is not None and number > sweepable.maximum:
        raise ValueError(f"{sweepable.key} must be at most {sweepable.maximum:g}")


# An axis with at most this many values names them all in a run's
# description; a longer one is summarized as a range.
_AXIS_VALUES_LISTED: Final = 3


def _format_axis_values(values: Sequence[CoordinateValue]) -> str:
    """Return an axis's values compactly: all of them when few, else a range.

    Up to three values are listed (`[0.01, 0.1]`); more are shown as
    their first and last with a count (`[5..2500] (8 values)`).
    """
    if len(values) <= _AXIS_VALUES_LISTED:
        return "[" + ", ".join(_format_coordinate(value) for value in values) + "]"
    first = _format_coordinate(values[0])
    last = _format_coordinate(values[-1])
    return f"[{first}..{last}] ({len(values)} values)"


def _format_coordinate(value: CoordinateValue) -> str:
    """Return one coordinate value as short text (`0.01`, `50`, `ring`)."""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


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
    _apply_axes(mapping, coordinates)
    if spec.seed_policy == "spaced":
        replicates = _integer_setting(mapping, "n_replicates")
        mapping["seed"] = _integer_setting(spec.base, "seed") + index * replicates
    return mapping


def _range_values(
    sweepable: AxisDefinition, definition: Mapping[Any, Any]
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


def _check_strategy(spec: SweepSpec, keys: list[str]) -> None:
    """Check what the strategy needs of the axes and rows."""
    if spec.strategy == "zip":
        lengths = {len(axis.values) for axis in spec.axes}
        if len(lengths) > 1:
            detail = ", ".join(
                f"{axis.key} has {len(axis.values)}" for axis in spec.axes
            )
            raise ValueError(f"zip axes need equal lengths: {detail}")
    elif spec.strategy == "list":
        if not spec.rows:
            raise ValueError("a list sweep needs at least one point")
        if any(set(row) != set(keys) for row in spec.rows):
            raise ValueError("every point of a list sweep needs the same keys")


def _stored_rows(value: object) -> list[Mapping[str, object]]:
    """Return the rows of a list sweep, checking their shape."""
    if not isinstance(value, Sequence) or isinstance(value, str) or not value:
        raise ValueError("a list sweep needs 'points': a list of one mapping per point")
    if not all(isinstance(row, Mapping) and row for row in value):
        raise ValueError("each point of a list sweep must be a mapping of key to value")
    return list(value)


def _stored_axis(entry: object) -> SweepAxis:
    """Rebuild one stored axis, re-validating its values."""
    if not isinstance(entry, Mapping) or "key" not in entry or "values" not in entry:
        raise ValueError("each sweep axis needs a key and its values")
    key = str(entry["key"])
    sweepable = axis_definition(key)
    convention = entry.get("convention", sweepable.convention)
    if convention != sweepable.convention:
        raise ValueError(
            f"sweep axis {key!r} was stored with the convention {convention!r}, "
            f"which this version does not use ({sweepable.convention!r})"
        )
    values = entry["values"]
    if not isinstance(values, Sequence) or isinstance(values, str):
        raise ValueError(f"sweep axis {key!r} values must be a list")
    return SweepAxis(key, tuple(_coerce(sweepable, item) for item in values))
