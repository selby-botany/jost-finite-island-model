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
        identical: Whether every data artifact matches bit for bit.
        differences: Artifacts that differ, then the reported statistics
            that changed (at most `_MAXIMUM_DIFFERENCES`).
        old_version: The earlier run's software version.
        new_version: The recomputed run's software version.
    """

    identical: bool
    differences: tuple[Difference, ...]
    old_version: str
    new_version: str

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
