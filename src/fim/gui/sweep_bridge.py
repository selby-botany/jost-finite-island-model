"""JSON-shaped payloads for the sweep bridge calls (`fim.gui.app.Api`).

Pure functions turning a page request into a `SweepSpec` and turning a
plan, a Study or its results back into plain dictionaries the page can
draw. Nothing here touches a window or starts a run; `Api` owns that.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from fim.persistence.groups import StudyManifest
from fim.persistence.recording import run_satisfies
from fim.sweep import (
    SIZE_CONFIRMATION_THRESHOLD,
    SweepPlan,
    SweepSpec,
    axis_definition,
    expand_axis,
    sweepable_key_names,
)
from fim.sweep_compare import ComparedPoint, SweepRef, compare_sweeps
from fim.sweep_cost import static_cost
from fim.sweep_run import (
    completed_run_ids,
    completed_runs,
    sweep_point_results,
    sweep_point_statuses,
    sweep_spec_of,
    trajectory_cache_path,
)
from fim.sweep_table import PointHealth, ResultTable
from fim.sweep_trajectory import TrajectoryCache, table_trajectories


def sweepable_keys_payload() -> list[dict[str, Any]]:
    """Describe every sweepable key for the page's axis controls."""
    return [
        {
            "key": key.key,
            "label": key.label,
            "kind": key.kind,
            "unit": key.unit,
            "scale": key.scale,
            "minimum": key.minimum,
            "maximum": key.maximum,
            "choices": list(key.choices),
            "displayDomain": list(key.display_domain) if key.display_domain else None,
            "closedForm": key.closed_form,
            "generic": key.generic,
        }
        for key in (axis_definition(name) for name in sweepable_key_names())
    ]


def spec_from_request(base: Mapping[str, Any], request: Mapping[str, Any]) -> SweepSpec:
    """Build a `SweepSpec` from the page's request and a validated base mapping.

    `request["axes"]` is a list of `{"key", "values"}` or `{"key",
    "range": {"start", "stop", "count", "scale"}}` entries, in axis order;
    `request["strategy"]` (`grid` or `zip`) and `request["seedPolicy"]` are
    optional.

    Raises:
        ValueError: An axis is malformed, or the spec is not valid.
    """
    raw_axes = request.get("axes")
    if not isinstance(raw_axes, list) or not raw_axes:
        raise ValueError("choose at least one axis to sweep")
    axes = []
    for entry in raw_axes:
        if not isinstance(entry, Mapping) or "key" not in entry:
            raise ValueError("each sweep axis needs a key")
        definition = entry["values"] if "values" in entry else entry.get("range")
        axes.append(expand_axis(str(entry["key"]), definition))
    policy = request.get("seedPolicy", "spaced")
    if policy not in {"spaced", "same"}:
        raise ValueError("seed policy must be 'spaced' or 'same'")
    strategy = request.get("strategy", "grid")
    if strategy not in {"grid", "zip"}:
        raise ValueError("strategy must be 'grid' or 'zip'")
    return SweepSpec(
        base=dict(base), axes=tuple(axes), strategy=strategy, seed_policy=policy
    )


def trajectories_payload(
    study_id: str,
    statistic: str,
    *,
    by: str | None,
    held: Mapping[str, Any],
    results: Path | None = None,
) -> dict[str, Any]:
    """Group a sweep's point trajectories into curves for the overlay chart.

    Undefined samples (no replicate had a value) are sent as `null`, since a
    page cannot read `NaN` from JSON.
    """
    try:
        table = ResultTable.read(study_id, results=results)
        curves = table_trajectories(
            table,
            by=by,
            held=held,
            cache=TrajectoryCache(trajectory_cache_path(study_id, results=results)),
        )
    except ValueError as error:
        return {"ok": False, "message": str(error)}

    def clean(values: Sequence[float]) -> list[float | None]:
        return [None if math.isnan(value) else value for value in values]

    series = []
    for curve in curves:
        points = []
        for entry in curve.entries:
            found = entry.trajectory.statistics.get(statistic)
            if found is None:
                continue
            row = entry.point.row
            health = None if row is None or row.health is None else row.health
            points.append(
                {
                    "index": entry.point.index,
                    "runId": entry.point.run_id,
                    "directory": str(row.directory) if row is not None else "",
                    "nReplicates": entry.trajectory.replicates,
                    "burnIn": entry.trajectory.burn_in,
                    "confidence": entry.trajectory.confidence,
                    "generations": list(entry.trajectory.generations),
                    "mean": clean(found.mean),
                    "low": clean(found.low),
                    "high": clean(found.high),
                    "std": clean(found.std),
                    "count": list(found.count),
                    "quorum": [
                        entry.trajectory.has_quorum(position, statistic)
                        for position in range(len(found.count))
                    ],
                    "health": "healthy"
                    if health is None
                    else health.classify(table.policy),
                    "healthReason": ""
                    if health is None
                    else health.describe(table.policy),
                }
            )
        series.append({"byValue": curve.by_value, "points": points})
    return {
        "ok": True,
        "by": by,
        "statistic": statistic,
        "series": series,
    }


def existing_run_ids(results: Path | None = None) -> set[str]:
    """Return the id of every run a sweep would reuse, so a plan can mark them.

    Counts finished run directories and completed Run objects made by this
    software version, as `run_sweep` does.
    """
    return completed_run_ids(results=results)


def reusable_run_ids(plan: SweepPlan, results: Path | None = None) -> set[str]:
    """Return the planned points whose existing run holds what the plan asks.

    A run made by this version counts only if it kept at least as much as the
    point's configuration asks it to (a tapered run does not stand in for full
    frames), the rule `run_sweep` applies.
    """
    runs = completed_runs(results=results)
    return {
        point.run_id
        for point in plan.points
        if point.run_id in runs and run_satisfies(runs[point.run_id], point.params)
    }


def plan_payload(
    spec: SweepSpec, plan: SweepPlan, *, results: Path | None = None
) -> dict[str, Any]:
    """Describe a plan for the page, marking points whose run already exists."""
    existing = reusable_run_ids(plan, results)
    points = [
        {
            "index": point.index,
            "coordinates": dict(point.coordinates),
            "runId": point.run_id,
            "exists": point.run_id in existing,
        }
        for point in plan.points
    ]
    reused = sum(1 for point in points if point["exists"])
    return {
        "ok": True,
        "axes": [axis.to_dict() for axis in spec.axes],
        "strategy": spec.strategy,
        "gridSize": plan.grid_size,
        "points": points,
        "invalid": [
            {
                "index": invalid.index,
                "coordinates": dict(invalid.coordinates),
                "reason": invalid.reason,
            }
            for invalid in plan.invalid
        ],
        "collapsed": plan.collapsed,
        "newCount": len(points) - reused,
        "reusedCount": reused,
        "cost": static_cost(spec, plan).to_dict(),
        "needsConfirmation": plan.needs_confirmation,
        "confirmationThreshold": SIZE_CONFIRMATION_THRESHOLD,
    }


def status_payload(
    study: StudyManifest, *, results: Path | None = None
) -> dict[str, Any]:
    """Describe a sweep Study and each planned point's derived state.

    `description`/`documentation` are the Study's own, for the sweep
    screen's name tooltip (`webui/screens/details.js`).
    """
    spec = sweep_spec_of(study)
    return {
        "ok": True,
        "studyId": study.study_id,
        "name": study.name,
        "description": study.description,
        "documentation": study.documentation,
        "axes": [axis.to_dict() for axis in spec.axes],
        "points": [
            {
                "index": status.index,
                "coordinates": dict(status.coordinates),
                "runId": status.run_id,
                "state": status.state,
                "reason": status.reason,
                "objectId": status.object_id,
            }
            for status in sweep_point_statuses(study, results=results)
        ],
    }


def _health_payload(health: PointHealth | None) -> dict[str, Any] | None:
    """Describe a point's health for the page, or `None` if it is not known.

    The class and the reason are worked out here, in one place, so the
    chart, the table and the tooltips all say the same thing.
    """
    if health is None:
        return None
    return {
        "class": health.classify(),
        "reason": health.describe(),
        "replicates": health.replicates,
        "converged": health.converged,
        "hitCap": health.hit_cap,
        "other": health.other,
    }


def series_payload(
    study_id: str,
    x: str,
    statistic: str,
    *,
    by: str | None,
    held: Mapping[str, Any],
    results: Path | None = None,
) -> dict[str, Any]:
    """Group a sweep's results into response curves for the page.

    The grouping is `ResultTable.series`; this only renames fields to the
    page's own style. A request the table refuses comes back as a message.
    """
    try:
        table = ResultTable.read(study_id, results=results)
        curves = table.series(x, statistic, by=by, held=held)
    except ValueError as error:
        return {"ok": False, "message": str(error)}
    return {
        "ok": True,
        "x": x,
        "by": by,
        "statistic": statistic,
        "series": [
            {
                "byValue": curve.by_value,
                "points": [
                    {
                        "x": point.x,
                        "index": point.point_index,
                        "runId": point.run_id,
                        "directory": str(point.directory),
                        "mean": point.mean,
                        "ciLow": point.ci_low,
                        "ciHigh": point.ci_high,
                        "sdLow": point.sd_low,
                        "sdHigh": point.sd_high,
                        "sampleCount": point.sample_count,
                        "nReplicates": point.replicates,
                        "health": point.health or "healthy",
                        "healthReason": point.health_reason,
                    }
                    for point in curve.points
                ],
            }
            for curve in curves
        ],
    }


def comparison_payload(
    study_ids: Sequence[str],
    statistic: str,
    *,
    on: Sequence[str] | None,
    labels: Sequence[str] | None,
    results: Path | None = None,
) -> dict[str, Any]:
    """Compare sweeps for the page: the join, each sweep's cells, the difference.

    Args:
        study_ids: Two or more sweep Studies.
        statistic: A statistic's catalog key, or empty for the first of
            `D`, `G_ST` that the sweeps have (else the first they have).
        on: The axes to join on, or `None` for every shared axis.
        labels: A label for each Study, or `None` for the Study names (with
            the Study id added where two names are alike).
        results: Optional results-directory override.

    Returns:
        `{"ok": True, ...}` with the comparison, or `{"ok": False, "message"}`
        for a comparison that cannot be made.
    """
    try:
        tables = [ResultTable.read(i, results=results) for i in study_ids]
        available = sorted({n for table in tables for n in table.statistic_names()})
        chosen = statistic or next(
            (name for name in ("D", "G_ST") if name in available),
            available[0] if available else "",
        )
        names = list(labels) if labels else _distinct_labels(tables)
        comparison = compare_sweeps(
            [SweepRef(i, label) for i, label in zip(study_ids, names, strict=True)],
            statistic=chosen,
            on=on,
            results=results,
        )
    except ValueError as error:
        return {"ok": False, "message": str(error)}
    return {
        "ok": True,
        "statistic": comparison.statistic,
        "available": available,
        "on": list(comparison.on),
        "labels": list(comparison.labels),
        "studyIds": list(study_ids),
        "pairing": comparison.pairing,
        "seedPolicies": dict(comparison.seed_policies),
        "baseDifferences": {
            key: dict(values) for key, values in comparison.base_differences.items()
        },
        "axes": {key: _common(tables, key) for key in comparison.on},
        "points": [_compared_point_payload(point) for point in comparison.points],
    }


def _distinct_labels(tables: Sequence[ResultTable]) -> list[str]:
    """Name each sweep by its Study's name, adding the id where names repeat."""
    names = [table.name for table in tables]
    return [
        f"{table.name} ({table.study_id})"
        if names.count(table.name) > 1
        else table.name
        for table in tables
    ]


def _common(tables: Sequence[ResultTable], key: str) -> list[Any]:
    """Return an axis's values that every sweep has, in the first sweep's order."""
    per_sweep = [
        next(axis.values for axis in table.spec.axes if axis.key == key)
        for table in tables
    ]
    return [v for v in per_sweep[0] if all(v in other for other in per_sweep[1:])]


def _offset(mean: float, spread: float | None, sign: int) -> float | None:
    """Return `mean` plus or minus `spread`, or `None` without a spread."""
    return None if spread is None else mean + sign * spread


def _compared_point_payload(point: ComparedPoint) -> dict[str, Any]:
    """Describe one shared coordinate for the page."""
    difference = point.difference
    return {
        "coordinates": dict(point.coordinates),
        "seeds": dict(point.seeds),
        "cells": {
            label: None
            if cell is None
            else {
                "mean": cell.mean,
                "low": cell.low,
                "high": cell.high,
                "sdLow": None
                if cell.sample_std is None
                else cell.mean - cell.sample_std,
                "sdHigh": None
                if cell.sample_std is None
                else cell.mean + cell.sample_std,
                "sampleCount": cell.sample_count,
                "health": point.health.get(label) or "healthy",
            }
            for label, cell in point.cells.items()
        },
        "difference": None
        if difference is None
        else {
            "difference": difference.difference,
            "low": difference.low,
            "high": difference.high,
            "confidence": difference.confidence,
            "degreesOfFreedom": difference.degrees_of_freedom,
        },
    }


def results_payload(
    study: StudyManifest, *, results: Path | None = None
) -> dict[str, Any]:
    """Describe a sweep Study's finished points and their statistics."""
    payload = status_payload(study, results=results)
    payload["results"] = [
        {
            "index": result.index,
            "coordinates": dict(result.coordinates),
            "runId": result.run_id,
            "directory": str(result.directory),
            "nReplicates": result.n_replicates,
            "statistics": {
                name: {"mean": cell.mean, "low": cell.low, "high": cell.high}
                for name, cell in result.statistics.items()
            },
            "health": _health_payload(result.health),
        }
        for result in sweep_point_results(study, results=results)
    ]
    return payload
