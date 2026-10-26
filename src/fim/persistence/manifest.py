"""Represent and verify run and batch manifests.

A run manifest records parameters, run identity, stop status, and digests of
persisted artifacts. A batch manifest describes a replicate batch. Readers
and writers validate these versioned JSON records; integrity checks verify
artifact bytes against the recorded digests.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, TypedDict

from fim.model.params import SimulationParams
from fim.paths import write_text_atomically

logger = logging.getLogger(__name__)

# Bumped whenever RunManifest's on-disk shape changes incompatibly. Recorded
# in every manifest so a future reader can tell which contract wrote it,
# rather than guessing from which fields happen to be present.
#
# 2 (from 1): adds `initial_condition_mode`/`equilibrium_generation_count`/
# `equilibrium_final_heterozygosity` (`20260907-claude-sonnet-5-
# equilibrium-split-design.md`, decision 5) — every one `None` on a
# manifest written before this schema version, the same backward-
# compatible shape `engine_backend`/`jit` already established.
#
CURRENT_SCHEMA_VERSION = 3

# Bumped whenever BatchManifest's on-disk shape changes incompatibly —
# tracked independently of CURRENT_SCHEMA_VERSION, since a batch manifest
# describes a whole replicate batch, a distinct document type from one run.
CURRENT_BATCH_SCHEMA_VERSION = 1

# Read in fixed-size chunks so hashing a large trajectory never requires
# holding the whole file in memory at once.
_HASH_CHUNK_BYTES = 1 << 20


class ArtifactDigest(TypedDict):
    """One durable run-output file's content identity.

    A "digest" (or "hash") is a short fingerprint computed from a
    file's exact bytes such that changing even one byte of the file
    changes the fingerprint completely and unpredictably — so two
    files with the same digest can be trusted to have identical
    content, and a file whose current digest no longer matches a
    previously recorded one has definitely been altered since.
    """

    sha256: str
    bytes: int


def hash_file(path: Path | str) -> ArtifactDigest:
    """Return one file's exact size and lowercase hex-encoded SHA-256 digest.

    "SHA-256" is a specific, standard, cryptographically strong hashing
    algorithm — recomputing it is the only way to check a digest, there
    is no shortcut, which is exactly the property that makes tampering
    with a file without changing its digest infeasible in practice.
    Reading the file in fixed-size chunks (`_HASH_CHUNK_BYTES`, above)
    rather than all at once means hashing even a very large trajectory
    file never requires holding the whole thing in memory simultaneously.

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

    `artifacts` is `None` for an in-memory result. Persisted manifests
    contain digests for completed sibling artifacts; these digests
    support integrity checks when files are read later.

    `engine_backend` and `jit` record the resolved engine settings, or
    `None` if the manifest predates these fields. They are stored as
    strings so manifests can be read without importing the engine and
    remain readable if an engine option is renamed. For
    `engine_backend="auto"`, the manifest records the selected backend,
    not `"auto"`.

    `initial_condition_mode` records the resolved initialization
    strategy. `equilibrium_generation_count` and
    `equilibrium_final_heterozygosity` are populated only for
    `"equilibrium_split"`. These fields are `None` for older manifests
    and for other initial-condition modes.

    `auto_derived` and `relaxation_time` record whether convergence
    settings were derived from the model's relaxation time.
    `parameters` contains the resolved integer settings; `auto_derived`
    is empty when both settings were supplied explicitly. `None` in
    these fields indicates a manifest written before they were added.
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
    engine_backend: str | None = None
    jit: str | None = None
    initial_condition_mode: str | None = None
    equilibrium_generation_count: int | None = None
    equilibrium_final_heterozygosity: float | None = None
    auto_derived: tuple[str, ...] | None = None
    relaxation_time: float | None = None

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
        _validate_run_manifest_optional_numeric_fields(self)

    def params(self) -> SimulationParams:
        """Reconstruct the exact validated simulation parameters.

        `self.parameters` is a plain, loosely typed mapping (the
        manifest's own JSON-safe form of the run's configuration); this
        rebuilds it into the fully validated `SimulationParams` the
        rest of the project actually works with, exactly as if the
        original config file were parsed again — the whole point of
        recording `parameters` in the manifest in the first place is so
        this reconstruction is possible without needing the original
        config file to still exist.
        """
        return SimulationParams.from_mapping(self.parameters)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable manifest mapping.

        The inverse of `from_dict`, below: turns this dataclass into
        the exact nested plain-``dict`` shape `write_manifest` actually
        writes to `manifest.json`, and that `from_dict` reads back.
        """
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
            "engine_backend": self.engine_backend,
            "jit": self.jit,
            "initial_condition_mode": self.initial_condition_mode,
            "equilibrium_generation_count": self.equilibrium_generation_count,
            "equilibrium_final_heterozygosity": self.equilibrium_final_heterozygosity,
            "auto_derived": (
                list(self.auto_derived) if self.auto_derived is not None else None
            ),
            "relaxation_time": self.relaxation_time,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RunManifest:
        """Validate and reconstruct a manifest mapping.

        The counterpart to `to_dict`, above, and to `read_manifest`,
        below: given a loosely typed JSON object exactly like the one
        `to_dict` produces (or a hand-edited or externally produced one
        with the same shape), checks every required field is present
        and well-formed and reconstructs the equivalent `RunManifest`.
        """
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
            _raise_missing_manifest_fields(value, missing)
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
            engine_backend=_optional_string(value, "engine_backend"),
            jit=_optional_string(value, "jit"),
            initial_condition_mode=_optional_string(value, "initial_condition_mode"),
            equilibrium_generation_count=_optional_int(
                value, "equilibrium_generation_count"
            ),
            equilibrium_final_heterozygosity=_optional_float(
                value, "equilibrium_final_heterozygosity"
            ),
            auto_derived=_optional_string_tuple(value, "auto_derived"),
            relaxation_time=_optional_float(value, "relaxation_time"),
        )


def read_manifest(path: Path | str) -> RunManifest:
    """Read and validate one manifest JSON file.

    Reads the raw JSON from disk and hands it to `RunManifest.
    from_dict`, above, which does the actual field-by-field validation
    — this function's own job is only the file I/O and the top-level
    "is this even a JSON object" check.
    """
    manifest_path = Path(path)
    logger.debug("reading manifest: %s", manifest_path)
    with manifest_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("manifest root must be an object")
    return RunManifest.from_dict(payload)


def write_manifest(path: Path | str, manifest: RunManifest) -> None:
    """Write a manifest atomically and deterministically, replacing any prior file.

    "Deterministically" here means the same fixed formatting
    `fim.persistence.report.write_report` uses (sorted keys, a single
    trailing newline, `NaN`/`Infinity` rejected outright) — see that
    function's own docstring for why this matters. This is the one
    function that actually creates `manifest.json` on disk; everywhere
    else in the project works with the in-memory `RunManifest` object.

    Atomic (`paths.write_text_atomically`), not an in-place truncate
    and rewrite: `fim.sweep_compact.compact_run`/`_compact_replicate`
    call this on an already-published, live run directory, so a crash
    mid-write used to leave a truncated `manifest.json` behind instead
    of either the old manifest or the new one (this project's own
    2026-10-25 project review, finding P-4).
    """
    text = (
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    write_text_atomically(Path(path), text, prefix=".manifest.")
    logger.debug("wrote manifest: %s", path)


def verify_trajectory_integrity(
    trajectory_path: Path, manifest: RunManifest, *, artifact: str = "trajectory"
) -> None:
    """Raise `ValueError` if a file does not match its recorded digest.

    The manifest's SHA-256 digest and byte count are compared with the
    current file. A missing digest or a mismatch prevents analysis from
    proceeding with an unverified artifact.

    Args:
        trajectory_path: The trajectory file about to be read.
        manifest: Its companion manifest.
        artifact: The manifest key of the file's digest: `"trajectory"` (the
            default) or `"equilibrium_trajectory"` for an equilibrium-split
            run's ancestral phase.

    Raises:
        ValueError: If the manifest has no digest for `artifact`, or the
            file does not match its recorded digest.
    """
    if manifest.artifacts is None or artifact not in manifest.artifacts:
        raise ValueError(
            f"manifest for {manifest.run_id!r} has no recorded {artifact} "
            "digest to verify against (written by a version of fim "
            "predating this integrity check)"
        )
    expected = manifest.artifacts[artifact]
    actual = hash_file(trajectory_path)
    if actual != expected:
        logger.warning(
            "trajectory integrity check failed for %s: expected sha256 %s "
            "(%d bytes), found %s (%d bytes)",
            manifest.run_id,
            expected["sha256"],
            expected["bytes"],
            actual["sha256"],
            actual["bytes"],
        )
        raise ValueError(
            f"trajectory does not match its manifest: expected sha256 "
            f"{expected['sha256']} ({expected['bytes']} bytes), found "
            f"{actual['sha256']} ({actual['bytes']} bytes) — the file may "
            "have been edited, truncated, or replaced since the run "
            "completed"
        )
    logger.debug("trajectory integrity check passed for %s", manifest.run_id)


@dataclass(frozen=True, slots=True)
class BatchManifest:
    """Capture everything needed to identify and verify a replicate batch.

    `replicate_run_ids` lists the published `replicate-NNN/`
    subdirectories. Persisted artifact digests cover `summary.json` and
    each replicate's `manifest.json`.
    """

    schema_version: int
    run_id: str
    replicate_run_ids: tuple[str, ...]
    parameters: Mapping[str, object]
    started_at: str
    ended_at: str
    software_version: str
    artifacts: Mapping[str, ArtifactDigest] | None = None

    def __post_init__(self) -> None:
        """Validate required batch-manifest identity and digest fields."""
        if self.schema_version < 1:
            raise ValueError("batch manifest schema_version must be at least 1")
        if not self.run_id:
            raise ValueError("batch manifest run_id must not be empty")
        if not self.replicate_run_ids:
            raise ValueError("batch manifest replicate_run_ids must not be empty")
        if not self.started_at or not self.ended_at:
            raise ValueError("batch manifest timestamps must not be empty")
        if not self.software_version:
            raise ValueError("batch manifest software_version must not be empty")
        if self.artifacts is not None:
            for name, digest in self.artifacts.items():
                _validate_artifact_digest(name, digest)

    @property
    def replicate_count(self) -> int:
        """Return the number of published replicates.

        Every replicate in a batch shares the exact same
        `SimulationParams` except its own seed
        (`fim.engine.identity.replicate_seed`); this is simply
        how many of them actually completed and were published.
        """
        return len(self.replicate_run_ids)

    def params(self) -> SimulationParams:
        """Reconstruct the exact validated simulation parameters.

        The batch-level counterpart to `RunManifest.params`, above; see
        that method's own docstring for what this reconstructs and why.
        """
        return SimulationParams.from_mapping(self.parameters)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable batch manifest mapping.

        The batch-level counterpart to `RunManifest.to_dict`, above.
        """
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "replicate_run_ids": list(self.replicate_run_ids),
            "replicate_count": self.replicate_count,
            "parameters": dict(self.parameters),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "software_version": self.software_version,
            "artifacts": (
                {name: dict(digest) for name, digest in self.artifacts.items()}
                if self.artifacts is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> BatchManifest:
        """Validate and reconstruct a batch manifest mapping.

        The batch-level counterpart to `RunManifest.from_dict`, above.
        """
        required = {
            "schema_version",
            "run_id",
            "replicate_run_ids",
            "parameters",
            "started_at",
            "ended_at",
            "software_version",
        }
        missing = required - set(value)
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"batch manifest is missing: {names}")
        parameters = value["parameters"]
        if not isinstance(parameters, Mapping):
            raise ValueError("batch manifest parameters must be an object")
        replicate_run_ids = _required_replicate_run_ids(value)
        raw_count = value.get("replicate_count")
        if raw_count is not None and raw_count != len(replicate_run_ids):
            raise ValueError(
                "batch manifest replicate_count does not match replicate_run_ids"
            )
        return cls(
            schema_version=_required_int(value, "schema_version", minimum=1),
            run_id=_required_string(value, "run_id"),
            replicate_run_ids=replicate_run_ids,
            parameters=dict(parameters),
            started_at=_required_string(value, "started_at"),
            ended_at=_required_string(value, "ended_at"),
            software_version=_required_string(value, "software_version"),
            artifacts=_optional_artifacts(value.get("artifacts")),
        )


def read_batch_manifest(path: Path | str) -> BatchManifest:
    """Read and validate one batch manifest JSON file.

    The batch-level counterpart to `read_manifest`, above.
    """
    manifest_path = Path(path)
    logger.debug("reading batch manifest: %s", manifest_path)
    with manifest_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("batch manifest root must be an object")
    return BatchManifest.from_dict(payload)


def write_batch_manifest(path: Path | str, manifest: BatchManifest) -> None:
    """Write a batch manifest atomically and deterministically.

    Replaces any prior file. The batch-level counterpart to
    `write_manifest`, above, including
    its own reason for writing atomically rather than in place.
    """
    text = (
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    write_text_atomically(Path(path), text, prefix=".batch-manifest.")
    logger.debug("wrote batch manifest: %s", path)


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


def _raise_missing_manifest_fields(
    value: Mapping[str, Any],
    missing: set[str],
) -> NoReturn:
    """Raise a manifest-missing-fields error, naming cause and remedy for
    the one case fim 1.0.0's own released output hits.

    A manifest missing only `schema_version`, with a recognizable
    `software_version` present, is not corrupt — it predates the
    manifest schema-version contract this project added after 1.0.0,
    the only version ever released. Naming that explicitly, rather
    than only the JSON key that happens to be absent, points at the
    actual cause: there is no automated migration for a pre-schema-
    version manifest, so re-running the same configuration with the
    current `fim` is the way to get a manifest this version can read.

    Args:
        value: The manifest mapping being validated.
        missing: The required field names not present in `value`.

    Raises:
        ValueError: Always.
    """
    software_version = value.get("software_version")
    if missing == {"schema_version"} and isinstance(software_version, str):
        raise ValueError(
            f"manifest has no schema_version — it was written by fim "
            f"{software_version}, before this project's manifest "
            "schema-version contract existed (1.0.0, the only version "
            "released so far, wrote no schema_version field at all). "
            "There is no automated migration for a pre-schema-version "
            "manifest; re-run the same configuration with the current "
            "fim to get a manifest this version can read."
        )
    names = ", ".join(sorted(missing))
    raise ValueError(f"manifest is missing: {names}")


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


def _required_replicate_run_ids(value: Mapping[str, Any]) -> tuple[str, ...]:
    """Read the batch manifest's required nonempty list of replicate run IDs."""
    raw_value = value.get("replicate_run_ids")
    if not isinstance(raw_value, list) or not raw_value:
        raise ValueError(
            "batch manifest field 'replicate_run_ids' must be a nonempty list"
        )
    if not all(isinstance(item, str) and item for item in raw_value):
        raise ValueError(
            "batch manifest field 'replicate_run_ids' must contain only "
            "nonempty strings"
        )
    return tuple(raw_value)


def _optional_string(value: Mapping[str, Any], key: str) -> str | None:
    """Read one optional nonempty string field, or `None` when absent/null.

    Used for `engine_backend`/`jit` — both `None` in any manifest
    written before those fields existed, so missing/`null` is a normal,
    valid case here, not an error the way an empty non-`None` string
    would be.
    """
    raw_value = value.get(key)
    if raw_value is None:
        return None
    if not isinstance(raw_value, str) or not raw_value:
        raise ValueError(f"manifest field {key!r} must be a nonempty string or null")
    return raw_value


def _optional_string_tuple(
    value: Mapping[str, Any], key: str
) -> tuple[str, ...] | None:
    """Read one optional list-of-nonempty-strings field, or `None` when absent/null.

    Used for `auto_derived` -- unlike `_optional_string` above, an empty
    list is a normal, valid value here (a run whose own `convergence_
    window`/`max_generations` were both given explicitly), not an
    error; only a missing key or an explicit `null` means "a manifest
    written before this field existed."
    """
    raw_value = value.get(key)
    if raw_value is None:
        return None
    if not isinstance(raw_value, list) or not all(
        isinstance(item, str) and item for item in raw_value
    ):
        raise ValueError(
            f"manifest field {key!r} must be a list of nonempty strings or null"
        )
    return tuple(raw_value)


def _optional_int(
    value: Mapping[str, Any], key: str, *, minimum: int = 0
) -> int | None:
    """Read one optional integer field, or `None` when absent/null.

    Used for `equilibrium_generation_count` — `None` in any manifest
    written before that field existed, or whenever the run did not use
    equilibrium-split at all (`RunManifest`'s own docstring).
    """
    raw_value = value.get(key)
    if raw_value is None:
        return None
    if isinstance(raw_value, bool) or not isinstance(raw_value, int):
        raise ValueError(f"manifest field {key!r} must be an integer or null")
    if raw_value < minimum:
        raise ValueError(f"manifest field {key!r} must be at least {minimum}")
    return raw_value


def _optional_float(value: Mapping[str, Any], key: str) -> float | None:
    """Read one optional finite float field, or `None` when absent/null.

    Used for `equilibrium_final_heterozygosity` — the same "absent
    before this field existed, or not applicable to this run's own
    initial-condition mode" cases `_optional_int` documents.
    """
    raw_value = value.get(key)
    if raw_value is None:
        return None
    if isinstance(raw_value, bool) or not isinstance(raw_value, int | float):
        raise ValueError(f"manifest field {key!r} must be a number or null")
    if not math.isfinite(raw_value):
        raise ValueError(f"manifest field {key!r} must be finite")
    return float(raw_value)


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


def _validate_run_manifest_optional_numeric_fields(manifest: RunManifest) -> None:
    """Validate `RunManifest`'s own optional numeric fields.

    Extracted out of `RunManifest.__post_init__` (which calls this once,
    last) purely to keep that method's own branch count low -- every
    check here would otherwise live directly in `__post_init__` itself;
    splitting them out changes nothing about what is validated or when.
    """
    if manifest.equilibrium_generation_count is not None and (
        manifest.equilibrium_generation_count < 0
    ):
        raise ValueError("manifest equilibrium_generation_count must be non-negative")
    if manifest.equilibrium_final_heterozygosity is not None and not (
        0.0 <= manifest.equilibrium_final_heterozygosity < 1.0
    ):
        raise ValueError("manifest equilibrium_final_heterozygosity must be in [0, 1)")
    if manifest.relaxation_time is not None and not math.isfinite(
        manifest.relaxation_time
    ):
        raise ValueError("manifest relaxation_time must be finite")


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
