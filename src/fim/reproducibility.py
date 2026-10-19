"""Compare two runs of one configuration, bit for bit.

The simulator guarantees that one configuration and seed give the same
result every time, within one software version. A run made under another
version is recomputed rather than reused, and this module says whether the
recomputed run matches the earlier one, and if not, which reported values
changed, so a break in that guarantee is never silent.

The comparison uses the SHA-256 digests every run's manifest already records
for its data artifacts (a single run's trajectory and report, a batch's
summary and replicates). The scatter image is left out: it is a rendering
whose bytes can change with the plotting library without any result changing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from fim.persistence.recording import (
    Recording,
    comparable_artifacts,
    recording_of_run,
)

_IGNORED_ARTIFACTS: Final = frozenset({"scatter"})
_MAXIMUM_DIFFERENCES: Final = 20


@dataclass(frozen=True, slots=True)
class Difference:
    """One thing that differs between two runs of the same configuration.

    Attributes:
        label: What differs: an artifact name (`trajectory`), or a reported
            statistic (`D`).
        old: The earlier run's value, or its digest for an artifact.
        new: The recomputed run's value, or its digest for an artifact.
    """

    label: str
    old: object
    new: object


@dataclass(frozen=True, slots=True)
class Comparison:
    """The outcome of comparing an earlier run with its recomputation.

    Attributes:
        identical: Whether every compared artifact matches bit for bit.
        differences: Artifacts that differ, then the reported statistics
            that changed (at most `_MAXIMUM_DIFFERENCES`).
        old_version: The earlier run's software version.
        new_version: The recomputed run's software version.
        compared_on: When the two runs kept different things (one tapered,
            one full), the artifacts that were compared; empty when every
            data artifact was.
        not_compared: Artifacts left out because the runs kept them
            differently.
    """

    identical: bool
    differences: tuple[Difference, ...]
    old_version: str
    new_version: str
    compared_on: tuple[str, ...] = ()
    not_compared: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable form (for the page and a sidecar file)."""
        return {
            "identical": self.identical,
            "oldVersion": self.old_version,
            "newVersion": self.new_version,
            "differences": [
                {"label": item.label, "old": item.old, "new": item.new}
                for item in self.differences
            ],
            "comparedOn": list(self.compared_on),
            "notCompared": list(self.not_compared),
        }


def compare_runs(old: Path, new: Path) -> Comparison:
    """Compare two run directories of the same configuration.

    Args:
        old: The earlier run (another software version).
        new: The recomputed run.

    Returns:
        Whether they match, and what differs if not.

    Raises:
        OSError: A manifest cannot be read.
        ValueError: A manifest is not valid JSON.
    """
    old_manifest = _read_json(old / "manifest.json")
    new_manifest = _read_json(new / "manifest.json")
    older, newer = recording_of_run(old), recording_of_run(new)
    if older != newer:
        return _compare_partially(old, new, older, newer, old_manifest, new_manifest)
    old_artifacts = _data_artifacts(old_manifest)
    new_artifacts = _data_artifacts(new_manifest)
    differences: list[Difference] = [
        Difference(name, old_artifacts.get(name), new_artifacts.get(name))
        for name in sorted(old_artifacts.keys() | new_artifacts.keys())
        if old_artifacts.get(name) != new_artifacts.get(name)
    ]
    if differences:
        differences.extend(_statistic_differences(old, new))
    return Comparison(
        identical=not differences,
        differences=tuple(differences[:_MAXIMUM_DIFFERENCES]),
        old_version=str(old_manifest.get("software_version", "")),
        new_version=str(new_manifest.get("software_version", "")),
    )


def _compare_partially(
    old: Path,
    new: Path,
    older: Recording,
    newer: Recording,
    old_manifest: dict[str, Any],
    new_manifest: dict[str, Any],
) -> Comparison:
    """Compare two runs that kept different things, on what both kept alike.

    The report, the pairwise statistics and a batch's summary do not depend on
    what was kept, so they must match bit for bit. The trajectory log and the
    statistic history are compared only when both runs wrote them the same
    way; otherwise they are listed as not compared. A batch is compared
    replicate by replicate.
    """
    allowed = comparable_artifacts(older, newer)
    differences: list[Difference] = []
    compared: set[str] = set()
    skipped: set[str] = set()
    pairs = [("", old_manifest, new_manifest)]
    if _is_batch(old_manifest) and _is_batch(new_manifest):
        shared = set(_replicates(old_manifest)) & set(_replicates(new_manifest))
        pairs.extend(
            (
                f"{name}/",
                _read_json(old / name / "manifest.json"),
                _read_json(new / name / "manifest.json"),
            )
            for name in sorted(shared)
        )
    for prefix, one_manifest, other_manifest in pairs:
        left, right = _data_artifacts(one_manifest), _data_artifacts(other_manifest)
        if prefix == "" and _is_batch(old_manifest):
            left = {k: v for k, v in left.items() if k == "summary"}
            right = {k: v for k, v in right.items() if k == "summary"}
        for name in sorted(left.keys() | right.keys()):
            if name not in allowed or name not in left or name not in right:
                skipped.add(name)
                continue
            compared.add(name)
            if left[name] != right[name]:
                differences.append(
                    Difference(f"{prefix}{name}", left[name], right[name])
                )
    if differences:
        differences.extend(_statistic_differences(old, new))
    return Comparison(
        identical=not differences,
        differences=tuple(differences[:_MAXIMUM_DIFFERENCES]),
        old_version=str(old_manifest.get("software_version", "")),
        new_version=str(new_manifest.get("software_version", "")),
        compared_on=tuple(sorted(compared)),
        not_compared=tuple(sorted(skipped - compared)),
    )


def _is_batch(manifest: dict[str, Any]) -> bool:
    """Return whether a manifest is a batch's (it lists its replicates)."""
    return "replicate_run_ids" in manifest


def _replicates(manifest: dict[str, Any]) -> list[str]:
    """Return a batch manifest's replicate folder names."""
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        return []
    return [name for name in artifacts if name.startswith("replicate-")]


def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object from `path`."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} does not hold a JSON object")
    return payload


def _data_artifacts(manifest: dict[str, Any]) -> dict[str, str | None]:
    """Return `{artifact name: sha256}` for the artifacts that hold results."""
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        return {}
    return {
        name: entry.get("sha256") if isinstance(entry, dict) else None
        for name, entry in artifacts.items()
        if name not in _IGNORED_ARTIFACTS
    }


def _statistic_differences(old: Path, new: Path) -> list[Difference]:
    """List the reported statistics that changed (`report.json` or `summary.json`)."""
    for name in ("report.json", "summary.json"):
        try:
            old_report = _read_json(old / name)
            new_report = _read_json(new / name)
        except (OSError, ValueError):
            continue
        return [
            Difference(key, _value(old_report.get(key)), _value(new_report.get(key)))
            for key in sorted(old_report.keys() | new_report.keys())
            if _value(old_report.get(key)) != _value(new_report.get(key))
        ]
    return []


def _value(entry: object) -> object:
    """Reduce a report entry to what is compared: a summary's mean, or the value."""
    if isinstance(entry, dict) and "mean" in entry:
        return entry["mean"]
    return entry
