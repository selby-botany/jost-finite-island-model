"""Replayable run-manifest representation and JSON I/O."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fim.model.params import SimulationParams


@dataclass(frozen=True, slots=True)
class RunManifest:
    """Capture everything needed to identify and replay a run."""

    run_id: str
    parameters: Mapping[str, object]
    started_at: str
    ended_at: str
    converged: bool
    convergence_statistic: str
    stop_reason: str
    generation: int
    software_version: str

    def __post_init__(self) -> None:
        """Validate required manifest identity and terminal fields."""
        if not self.run_id:
            raise ValueError("manifest run_id must not be empty")
        if not self.started_at or not self.ended_at:
            raise ValueError("manifest timestamps must not be empty")
        if self.generation < 0:
            raise ValueError("manifest generation must be non-negative")
        if not self.software_version:
            raise ValueError("manifest software_version must not be empty")

    def params(self) -> SimulationParams:
        """Reconstruct the exact validated simulation parameters."""
        return SimulationParams.from_mapping(self.parameters)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable manifest mapping."""
        return {
            "run_id": self.run_id,
            "parameters": dict(self.parameters),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "convergence": {
                "converged": self.converged,
                "statistic": self.convergence_statistic,
                "reason": self.stop_reason,
                "generation": self.generation,
            },
            "software_version": self.software_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RunManifest:
        """Validate and reconstruct a manifest mapping."""
        required = {
            "run_id",
            "parameters",
            "started_at",
            "ended_at",
            "convergence",
            "software_version",
        }
        missing = required - set(value)
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"manifest is missing: {names}")
        parameters = value["parameters"]
        convergence = value["convergence"]
        if not isinstance(parameters, Mapping):
            raise ValueError("manifest parameters must be an object")
        if not isinstance(convergence, Mapping):
            raise ValueError("manifest convergence must be an object")
        return cls(
            run_id=_required_string(value, "run_id"),
            parameters=dict(parameters),
            started_at=_required_string(value, "started_at"),
            ended_at=_required_string(value, "ended_at"),
            converged=_required_bool(convergence, "converged"),
            convergence_statistic=_required_string(convergence, "statistic"),
            stop_reason=_required_string(convergence, "reason"),
            generation=_required_int(convergence, "generation"),
            software_version=_required_string(value, "software_version"),
        )


def read_manifest(path: Path | str) -> RunManifest:
    """Read and validate one manifest JSON file."""
    manifest_path = Path(path)
    with manifest_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("manifest root must be an object")
    return RunManifest.from_dict(payload)


def write_manifest(path: Path | str, manifest: RunManifest) -> None:
    """Write a manifest deterministically, replacing any prior file."""
    manifest_path = Path(path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(
            manifest.to_dict(),
            handle,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        handle.write("\n")


def _required_bool(value: Mapping[str, Any], key: str) -> bool:
    """Read one required Boolean field."""
    raw_value = value.get(key)
    if not isinstance(raw_value, bool):
        raise ValueError(f"manifest field {key!r} must be a Boolean")
    return raw_value


def _required_int(value: Mapping[str, Any], key: str) -> int:
    """Read one required non-negative integer field."""
    raw_value = value.get(key)
    if isinstance(raw_value, bool) or not isinstance(raw_value, int):
        raise ValueError(f"manifest field {key!r} must be an integer")
    if raw_value < 0:
        raise ValueError(f"manifest field {key!r} must be non-negative")
    return raw_value


def _required_string(value: Mapping[str, Any], key: str) -> str:
    """Read one required nonempty string field."""
    raw_value = value.get(key)
    if not isinstance(raw_value, str) or not raw_value:
        raise ValueError(f"manifest field {key!r} must be a nonempty string")
    return raw_value
