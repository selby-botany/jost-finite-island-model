"""Compare sweeps: the same statistic across sweeps, joined by coordinates.

The botanist's questions are comparative: ring against island topology,
`N` = 100 against `N` = 1,000, a sweep against its recomputed twin. A
comparison lines up two or more sweeps on the axis values they share, reports
the statistic of each at every shared coordinate, and, for exactly two
sweeps, the difference with an interval.

One definition of "comparable" serves the command line, the app and the
export: two sweeps compare on the axis keys both vary, at the values both
have. Nothing is stored; a comparison is computed when it is asked for.

**The interval of a difference.** For two sweeps with replicate means `m1`
and `m2`, standard deviations `s1` and `s2`, and `n1` and `n2` replicates,

    difference = m1 - m2
    standard error = sqrt(s1^2 / n1 + s2^2 / n2)
    degrees of freedom = standard error^4 /
        ((s1^2 / n1)^2 / (n1 - 1) + (s2^2 / n2)^2 / (n2 - 1))

(Welch's unequal-variance method with the Welch-Satterthwaite degrees of
freedom, rounded down to a whole number so the interval errs wide). The
interval is `difference` plus or minus the Student's-t critical value for
those degrees of freedom times the standard error. It needs at least two
replicates on each side; with fewer, the difference is reported without an
interval.

**Seeds (botanist question Q7).** Welch's method assumes the two sweeps'
replicates are independent. They are when no replicate seed is shared, which
is the default: a sweep gives point `i` the seed `base seed + i *
n_replicates`, so two sweeps with different base seeds share none. Two
sweeps built from the same base seed and the same grid *do* share seeds
point for point (common random numbers), as do sweeps built with
`seed_policy: same`. The comparison does not switch method silently: it
looks at the seeds the compared runs actually used and reports
`pairing` as `independent`, `shared-seeds` or `partly-shared-seeds`, along
with each sweep's seed policy, so a reader can judge the interval.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any, Literal

from fim.statistics.interval import student_t_critical_value
from fim.sweep import CoordinateValue
from fim.sweep_table import HealthClass, ResultTable, StatisticCell, TablePoint

Pairing = Literal["independent", "shared-seeds", "partly-shared-seeds", "unknown"]
"""Whether the compared runs used the same seeds: none shared, all shared at
every shared coordinate, some shared, or too few shared coordinates to say."""

_COSMETIC_KEYS = frozenset({"name", "description", "class", "_read_only"})
"""Configuration keys that name a sweep without changing what it simulates."""

_DEFAULT_CONFIDENCE = 0.95
_TWO = 2


@dataclass(frozen=True, slots=True)
class SweepRef:
    """A sweep to compare.

    Attributes:
        study_id: The sweep Study's id.
        label: What to call it in the comparison; defaults to the Study's name.
    """

    study_id: str
    label: str = ""


@dataclass(frozen=True, slots=True)
class DifferenceCell:
    """The difference of two sweeps' means at one coordinate.

    Attributes:
        difference: First sweep's mean minus the second's.
        low: Lower end of the interval, or `None` without one.
        high: Upper end of the interval, or `None` without one.
        half_width: Half the interval's width, or `None`.
        confidence: The interval's confidence level, or `None`.
        degrees_of_freedom: Welch-Satterthwaite degrees of freedom (rounded
            down) behind the interval, or `None`.
    """

    difference: float
    low: float | None
    high: float | None
    half_width: float | None
    confidence: float | None
    degrees_of_freedom: int | None


@dataclass(frozen=True, slots=True)
class ComparedPoint:
    """One shared coordinate and what each sweep has there.

    Attributes:
        coordinates: The compared axis values (shared keys only).
        cells: Each sweep's statistic by label; `None` where that sweep has no
            finished point.
        seeds: Each sweep's seed at the coordinate by label; `None` where it
            has none.
        health: How far to trust each sweep's point by label (`None` where it
            has none or its health is unknown); a flagged point stays flagged
            in a comparison.
        difference: First minus second, for exactly two sweeps whose cells
            are both present; otherwise `None`.
    """

    coordinates: Mapping[str, CoordinateValue]
    cells: Mapping[str, StatisticCell | None]
    seeds: Mapping[str, int | None]
    health: Mapping[str, HealthClass | None]
    difference: DifferenceCell | None


@dataclass(frozen=True, slots=True)
class Comparison:
    """Sweeps lined up on their shared coordinates.

    Attributes:
        statistic: The statistic compared.
        on: The axis keys the sweeps were joined on.
        labels: The sweeps' labels, in the order given.
        points: Every shared coordinate, in the first sweep's grid order.
        base_differences: Each base configuration key that differs between
            the sweeps (and is not one of their axes), with each sweep's
            value by label: the difference being investigated.
        pairing: Whether the compared runs shared seeds (see the module
            docstring).
        seed_policies: Each sweep's seed policy by label.
    """

    statistic: str
    on: tuple[str, ...]
    labels: tuple[str, ...]
    points: tuple[ComparedPoint, ...]
    base_differences: Mapping[str, Mapping[str, object]]
    pairing: Pairing
    seed_policies: Mapping[str, str]


def welch_difference(first: StatisticCell, second: StatisticCell) -> DifferenceCell:
    """Return the difference of two cells' means with a Welch interval.

    Args:
        first: The first sweep's cell.
        second: The second sweep's cell.

    Returns:
        `first.mean - second.mean`; with the interval of the module
        docstring when both cells have at least two replicates and a spread,
        and without one otherwise.
    """
    difference = first.mean - second.mean
    spreads = (first.sample_std, second.sample_std)
    counts = (first.sample_count, second.sample_count)
    if any(s is None for s in spreads) or any(n < _TWO for n in counts):
        return DifferenceCell(difference, None, None, None, None, None)
    assert first.sample_std is not None and second.sample_std is not None
    variance_1 = first.sample_std**2 / first.sample_count
    variance_2 = second.sample_std**2 / second.sample_count
    squared_error = variance_1 + variance_2
    if squared_error == 0:
        return DifferenceCell(difference, difference, difference, 0.0, None, None)
    degrees = squared_error**2 / (
        variance_1**2 / (first.sample_count - 1)
        + variance_2**2 / (second.sample_count - 1)
    )
    whole = max(1, math.floor(degrees))
    confidence = first.confidence or second.confidence or _DEFAULT_CONFIDENCE
    half_width = student_t_critical_value(whole, confidence) * math.sqrt(squared_error)
    return DifferenceCell(
        difference,
        difference - half_width,
        difference + half_width,
        half_width,
        confidence,
        whole,
    )


def compare_sweeps(
    refs: Sequence[SweepRef],
    *,
    statistic: str,
    on: Sequence[str] | None = None,
    results: Path | None = None,
) -> Comparison:
    """Line up sweeps on their shared axes and compare one statistic.

    Args:
        refs: Two or more sweeps. A ref with no label is called by its
            Study's name.
        statistic: A statistic's catalog key.
        on: The axis keys to join on, all of which every sweep must vary;
            by default every axis they all share.
        results: Optional results-directory override.

    Returns:
        The comparison. For exactly two sweeps each shared coordinate with a
        finished point on both sides has a `difference`.

    Raises:
        ValueError: Fewer than two sweeps, repeated labels, no shared axis
            key (or `on` names one that is not shared), a shared axis with no
            common value, a sweep that also varies an axis outside `on`
            (its points could not be told apart), sweeps of different
            ploidy, or a statistic none of the sweeps has.
    """
    if len(refs) < _TWO:
        raise ValueError("comparing needs at least two sweeps")
    tables = [ResultTable.read(ref.study_id, results=results) for ref in refs]
    labels = [ref.label or table.name for ref, table in zip(refs, tables, strict=True)]
    if len(set(labels)) != len(labels):
        raise ValueError(
            "the sweeps need different labels; they are all called "
            f"{', '.join(repr(label) for label in labels)}"
        )
    keys = _joined_keys(tables, labels, on)
    for label, table in zip(labels, tables, strict=True):
        extra = [key for key in table.axis_keys if key not in keys]
        if extra:
            raise ValueError(
                f"sweep {label!r} also varies {extra[0]!r}, which the other "
                "sweeps do not; compare on all of its axes, or pick sweeps "
                "that vary the same ones"
            )
    ploidies = {table.spec.base.get("ploidy") for table in tables}
    if len(ploidies) > 1:
        raise ValueError(
            "the sweeps have different ploidy, so their N does not mean the same"
        )
    if not any(
        statistic in point.row.statistics
        for table in tables
        for point in table.points
        if point.row is not None
    ):
        raise ValueError(
            f"no finished point of these sweeps has statistic {statistic!r}"
        )
    values = _common_values(tables, labels, keys)
    index = [_by_coordinates(table, keys) for table in tables]
    points = tuple(
        _compared_point(
            statistic, dict(zip(keys, combination, strict=True)), labels, index
        )
        for combination in product(*(values[key] for key in keys))
    )
    return Comparison(
        statistic=statistic,
        on=tuple(keys),
        labels=tuple(labels),
        points=points,
        base_differences=_base_differences(tables, labels),
        pairing=_pairing(points),
        seed_policies={
            label: table.spec.seed_policy
            for label, table in zip(labels, tables, strict=True)
        },
    )


def _joined_keys(
    tables: Sequence[ResultTable], labels: Sequence[str], on: Sequence[str] | None
) -> list[str]:
    """Return the axis keys to join on, checking each is varied by every sweep."""
    shared = [
        key
        for key in tables[0].axis_keys
        if all(key in table.axis_keys for table in tables[1:])
    ]
    if not shared:
        raise ValueError(
            "these sweeps share no axis key, so there is nothing to line up: "
            + "; ".join(
                f"{label!r} varies {', '.join(table.axis_keys)}"
                for label, table in zip(labels, tables, strict=True)
            )
        )
    if on is None:
        return shared
    unknown = [key for key in on if key not in shared]
    if unknown or not on:
        raise ValueError(
            f"cannot join on {unknown[0] if unknown else 'nothing'!r}; the axes "
            f"every sweep varies are {', '.join(shared)}"
        )
    return list(on)


def _common_values(
    tables: Sequence[ResultTable], labels: Sequence[str], keys: Sequence[str]
) -> dict[str, list[CoordinateValue]]:
    """Return, for each joined key, the values every sweep has (first sweep's order)."""
    common: dict[str, list[CoordinateValue]] = {}
    for key in keys:
        per_sweep = [
            next(axis.values for axis in table.spec.axes if axis.key == key)
            for table in tables
        ]
        kept = [v for v in per_sweep[0] if all(v in other for other in per_sweep[1:])]
        if not kept:
            raise ValueError(
                f"axis {key!r} has no value in common: "
                + "; ".join(
                    f"{label!r} has {', '.join(str(v) for v in values)}"
                    for label, values in zip(labels, per_sweep, strict=True)
                )
            )
        common[key] = kept
    return common


def _by_coordinates(
    table: ResultTable, keys: Sequence[str]
) -> dict[tuple[CoordinateValue, ...], TablePoint]:
    """Index a table's points by their values on the joined keys."""
    return {tuple(p.coordinates[key] for key in keys): p for p in table.points}


def _compared_point(
    statistic: str,
    coordinates: Mapping[str, CoordinateValue],
    labels: Sequence[str],
    index: Sequence[Mapping[tuple[CoordinateValue, ...], TablePoint]],
) -> ComparedPoint:
    """Collect each sweep's cell and seed at one coordinate."""
    key = tuple(coordinates.values())
    cells: dict[str, StatisticCell | None] = {}
    seeds: dict[str, int | None] = {}
    health: dict[str, HealthClass | None] = {}
    for label, lookup in zip(labels, index, strict=True):
        point = lookup.get(key)
        row = point.row if point is not None else None
        cells[label] = row.statistics.get(statistic) if row is not None else None
        seeds[label] = row.seed if row is not None else None
        health[label] = (
            row.health.classify() if row is not None and row.health else None
        )
    difference = None
    if len(labels) == _TWO:
        first, second = (cells[label] for label in labels)
        if first is not None and second is not None:
            difference = welch_difference(first, second)
    return ComparedPoint(coordinates, cells, seeds, health, difference)


def _pairing(points: Sequence[ComparedPoint]) -> Pairing:
    """Say whether the compared runs shared seeds, from the seeds they used."""
    compared = [
        [seed for seed in point.seeds.values() if seed is not None] for point in points
    ]
    compared = [seeds for seeds in compared if len(seeds) >= _TWO]
    if not compared:
        return "unknown"
    sharing = [len(set(seeds)) < len(seeds) for seeds in compared]
    if all(sharing):
        return "shared-seeds"
    return "partly-shared-seeds" if any(sharing) else "independent"


def _base_differences(
    tables: Sequence[ResultTable], labels: Sequence[str]
) -> dict[str, dict[str, object]]:
    """Return the base configuration keys the sweeps disagree on.

    Keys that are an axis of any of the sweeps (their base value is replaced
    at every point) and keys that only name a sweep are left out.
    """
    swept = {key for table in tables for key in table.axis_keys}
    keys = sorted(
        {key for table in tables for key in table.spec.base} - swept - _COSMETIC_KEYS
    )
    differences: dict[str, dict[str, object]] = {}
    for key in keys:
        values: list[Any] = [table.spec.base.get(key) for table in tables]
        if any(value != values[0] for value in values[1:]):
            differences[key] = dict(zip(labels, values, strict=True))
    return differences
