"""JSON-shaped payloads for the sweep bridge calls (`fim.gui.app.Api`).

Pure functions turning a page request into a `SweepSpec` and turning a
plan, a Study or its results back into plain dictionaries the page can
draw. Nothing here touches a window or starts a run; `Api` owns that.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fim import paths
from fim.persistence import groups
from fim.persistence.groups import StudyManifest
from fim.sweep import (
    SIZE_CONFIRMATION_THRESHOLD,
    SWEEPABLE_KEYS,
    SweepPlan,
    SweepSpec,
    expand_axis,
    work_estimate,
)
from fim.sweep_run import (
    sweep_point_results,
    sweep_point_statuses,
    sweep_spec_of,
)


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
        }
        for key in SWEEPABLE_KEYS.values()
    ]


def spec_from_request(base: Mapping[str, Any], request: Mapping[str, Any]) -> SweepSpec:
    """Build a `SweepSpec` from the page's request and a validated base mapping.

    `request["axes"]` is a list of `{"key", "values"}` or `{"key",
    "range": {"start", "stop", "count", "scale"}}` entries, in axis order;
    `request["seedPolicy"]` is optional.

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
    return SweepSpec(base=dict(base), axes=tuple(axes), seed_policy=policy)


def plan_payload(
    spec: SweepSpec, plan: SweepPlan, *, results: Path | None = None
) -> dict[str, Any]:
    """Describe a plan for the page, marking points whose run already exists."""
    existing = {
        run_id
        for run_id in (
            groups.run_id_of(directory.parent) for directory in _run_manifests(results)
        )
        if run_id is not None
    }
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
        "estimate": work_estimate(spec, plan),
        "needsConfirmation": plan.needs_confirmation,
        "confirmationThreshold": SIZE_CONFIRMATION_THRESHOLD,
    }


def status_payload(
    study: StudyManifest, *, results: Path | None = None
) -> dict[str, Any]:
    """Describe a sweep Study and each planned point's derived state."""
    spec = sweep_spec_of(study)
    return {
        "ok": True,
        "studyId": study.study_id,
        "name": study.name,
        "axes": [axis.to_dict() for axis in spec.axes],
        "points": [
            {
                "index": status.index,
                "coordinates": dict(status.coordinates),
                "runId": status.run_id,
                "state": status.state,
                "reason": status.reason,
            }
            for status in sweep_point_statuses(study, results=results)
        ],
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
                name: dict(stat) for name, stat in result.statistics.items()
            },
        }
        for result in sweep_point_results(study, results=results)
    ]
    return payload


def _run_manifests(results: Path | None) -> list[Path]:
    """Return every run manifest path directly under the results directory."""
    root = results if results is not None else paths.results_directory()
    return sorted(root.glob("*/manifest.json")) if root.is_dir() else []
