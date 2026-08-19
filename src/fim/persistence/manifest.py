"""Replayable, verifiable run-manifest representation and JSON I/O."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

from fim.model.params import SimulationParams

# Bumped whenever RunManifest's on-disk shape changes incompatibly. Recorded
# in every manifest so a future reader can tell which contract wrote it,
# rather than guessing from which fields happen to be present.
CURRENT_SCHEMA_VERSION = 1

# Read in fixed-size chunks so hashing a large trajectory never requires
# holding the whole file in memory at once.
_HASH_CHUNK_BYTES = 1 << 20


class ArtifactDigest(TypedDict):
    """One durable run-output file's content identity."""

    sha256: str
    bytes: int


def hash_file(path: Path | str) -> ArtifactDigest:
    """Return one file's exact size and lowercase hex-encoded SHA-256 digest.

    Args:
        path: File to hash.

    Returns:
        The file's byte count and SHA-256 digest, together sufficient to
        detect any edit, truncation, or replacement of the file's content.
    """
    file_path = Path(path)
    digest = hashlib.sha256()
    total_bytes = 0
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
            total_bytes += len(chunk)
    return {"sha256": digest.hexdigest(), "bytes": total_bytes}


@dataclass(frozen=True, slots=True)
class RunManifest:
    """Capture everything needed to identify, verify, and replay a run.

    `artifacts` is `None` for a manifest as the engine constructs it —
    `fim.engine._run_one` returns a `RunResult` without ever writing to
    disk, so it cannot yet know a durable file's content digest. A
    manifest actually persisted to disk (`fim.cli._write_run_artifacts`)
    is written only once every other artifact (`trajectory.jsonl`,
    `report.json`, `scatter.png`) is fully flushed, with `artifacts`
    populated from their real on-disk digests — so `artifacts is not
    None` on a *read* manifest doubles as "every sibling artifact this
    manifest names existed, complete, at the moment this manifest was
    written." `fim stats` (`fim.cli._verify_trajectory_integrity`) uses
    that digest to refuse a trajectory that was edited, truncated, or
    replaced after the fact.
    """

    schema_version: int
    run_id: str
    parameters: Mapping[str, object]
    started_at: str
    ended_at: str
    converged: bool
    convergence_statistic: str | tuple[str, ...]
    stop_reason: str
    generation: int
    generation_count: int
    software_version: str
    artifacts: Mapping[str, ArtifactDigest] | None = None

    def __post_init__(self) -> None:
        """Validate required manifest identity, terminal, and digest fields."""
        if self.schema_version < 1:
            raise ValueError("manifest schema_version must be at least 1")
        if not self.run_id:
            raise ValueError("manifest run_id must not be empty")
        if not self.started_at or not self.ended_at:
            raise ValueError("manifest timestamps must not be empty")
        if self.generation < 0:
            raise ValueError("manifest generation must be non-negative")
        if self.generation_count < 1:
            raise ValueError("manifest generation_count must be at least 1")
        if not self.software_version:
            raise ValueError("manifest software_version must not be empty")
        if self.artifacts is not None:
            for name, digest in self.artifacts.items():
                _validate_artifact_digest(name, digest)

    def params(self) -> SimulationParams:
        """Reconstruct the exact validated simulation parameters."""
        return SimulationParams.from_mapping(self.parameters)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable manifest mapping."""
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "parameters": dict(self.parameters),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "convergence": {
                "converged": self.converged,
                "statistic": (
                    self.convergence_statistic
                    if isinstance(self.convergence_statistic, str)
                    else list(self.convergence_statistic)
                ),
                "reason": self.stop_reason,
                "generation": self.generation,
                "generation_count": self.generation_count,
            },
            "software_version": self.software_version,
            "artifacts": (
                {name: dict(digest) for name, digest in self.artifacts.items()}
                if self.artifacts is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RunManifest:
        """Validate and reconstruct a manifest mapping."""
        required = {
            "schema_version",
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
            schema_version=_required_int(value, "schema_version", minimum=1),
            run_id=_required_string(value, "run_id"),
            parameters=dict(parameters),
            started_at=_required_string(value, "started_at"),
            ended_at=_required_string(value, "ended_at"),
            converged=_required_bool(convergence, "converged"),
            convergence_statistic=_required_string_or_strings(convergence, "statistic"),
            stop_reason=_required_string(convergence, "reason"),
            generation=_required_int(convergence, "generation"),
            generation_count=_required_int(convergence, "generation_count", minimum=1),
            software_version=_required_string(value, "software_version"),
            artifacts=_optional_artifacts(value.get("artifacts")),
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


def _optional_artifacts(
    raw_value: Any,
) -> Mapping[str, ArtifactDigest] | None:
    """Parse the optional `artifacts` mapping, or `None` when absent/null."""
    if raw_value is None:
        return None
    if not isinstance(raw_value, Mapping):
        raise ValueError("manifest field 'artifacts' must be an object or null")
    digests: dict[str, ArtifactDigest] = {}
    for name, raw_digest in raw_value.items():
        if not isinstance(raw_digest, Mapping):
            raise ValueError(f"manifest artifact {name!r} must be an object")
        sha256 = raw_digest.get("sha256")
        digest_bytes = raw_digest.get("bytes")
        if not isinstance(sha256, str) or not sha256:
            raise ValueError(f"manifest artifact {name!r} sha256 must be a string")
        if isinstance(digest_bytes, bool) or not isinstance(digest_bytes, int):
            raise ValueError(f"manifest artifact {name!r} bytes must be an integer")
        if digest_bytes < 0:
            raise ValueError(f"manifest artifact {name!r} bytes must be non-negative")
        digests[name] = {"sha256": sha256, "bytes": digest_bytes}
    return digests


def _required_bool(value: Mapping[str, Any], key: str) -> bool:
    """Read one required Boolean field."""
    raw_value = value.get(key)
    if not isinstance(raw_value, bool):
        raise ValueError(f"manifest field {key!r} must be a Boolean")
    return raw_value


def _required_int(
    value: Mapping[str, Any],
    key: str,
    *,
    minimum: int = 0,
) -> int:
    """Read one required integer field bounded below by `minimum`."""
    raw_value = value.get(key)
    if isinstance(raw_value, bool) or not isinstance(raw_value, int):
        raise ValueError(f"manifest field {key!r} must be an integer")
    if raw_value < minimum:
        raise ValueError(f"manifest field {key!r} must be at least {minimum}")
    return raw_value


def _required_string(value: Mapping[str, Any], key: str) -> str:
    """Read one required nonempty string field."""
    raw_value = value.get(key)
    if not isinstance(raw_value, str) or not raw_value:
        raise ValueError(f"manifest field {key!r} must be a nonempty string")
    return raw_value


def _required_string_or_strings(
    value: Mapping[str, Any],
    key: str,
) -> str | tuple[str, ...]:
    """Read one required field as a nonempty string or a nonempty list of them."""
    raw_value = value.get(key)
    if isinstance(raw_value, str):
        if not raw_value:
            raise ValueError(f"manifest field {key!r} must be a nonempty string")
        return raw_value
    if isinstance(raw_value, list) and raw_value:
        strings = tuple(raw_value)
        if all(isinstance(item, str) and item for item in strings):
            return strings
    raise ValueError(
        f"manifest field {key!r} must be a nonempty string or list of strings"
    )


def _validate_artifact_digest(name: str, digest: ArtifactDigest) -> None:
    """Validate one already-typed artifact digest at manifest construction."""
    sha256 = digest.get("sha256")
    digest_bytes = digest.get("bytes")
    if not isinstance(sha256, str) or not sha256:
        raise ValueError(f"manifest artifact {name!r} sha256 must be a nonempty string")
    if isinstance(digest_bytes, bool) or not isinstance(digest_bytes, int):
        raise ValueError(f"manifest artifact {name!r} bytes must be an integer")
    if digest_bytes < 0:
        raise ValueError(f"manifest artifact {name!r} bytes must be non-negative")
