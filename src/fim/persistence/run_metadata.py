"""Optional name/description sidecar for a run, attachable at any time.

A completed run's own `manifest.json` is a fixed, digest-verified
receipt (`fim.persistence.manifest`) — rewriting it to add a name would
either break `verify_trajectory_integrity`'s own checksum or require
recomputing it for no scientific reason. `RunMetadata` instead lives in
its own small sidecar file, `metadata.json`, beside `manifest.json`:
attaching, renaming, or clearing a run's name/description never touches
the manifest at all.

A run with no `metadata.json` is not incomplete — it is exactly what
every run produced before this file existed already is (`20260917-
claude-sonnet-5-run-study-experiment-hierarchy-design.md`, `selby/
restricted`, §3.1/§3.4): callers read it the same graceful-degradation
way `fim.gui.app._read_json_object` already reads `report.json`/
`summary.json` — missing or malformed means "no name set," never an
error for the run itself.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Bumped whenever RunMetadata's on-disk shape changes incompatibly —
# tracked independently of `fim.persistence.manifest.CURRENT_SCHEMA_
# VERSION`, since this is a distinct document type from a run manifest.
CURRENT_RUN_METADATA_SCHEMA_VERSION = 1

RUN_METADATA_FILE_NAME = "metadata.json"

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    """Return the current UTC time, injectable for deterministic tests."""
    return datetime.now(UTC)


def _format_timestamp(value: datetime) -> str:
    """Return an unambiguous UTC ISO-8601 timestamp, matching `RunManifest`.

    A separate, tiny copy of `fim.cli._format_timestamp`'s own logic —
    that function's own docstring explains why every timestamp in this
    project is a small local copy rather than a shared import.
    """
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class RunMetadata:
    """A run's optional, user-attached name and description.

    `name`/`description` are independently optional — a user may set
    only one of the two (e.g. a description with no short name yet).
    `created_at` never changes once written; `updated_at` moves forward
    every time `replace_run_metadata` is called again for the same run.
    """

    schema_version: int
    name: str | None
    description: str | None
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        """Validate schema version, timestamps, and non-blank text fields."""
        if self.schema_version < 1:
            raise ValueError("run metadata schema_version must be at least 1")
        if self.name is not None and not self.name.strip():
            raise ValueError("run metadata name must not be blank")
        if self.description is not None and not self.description.strip():
            raise ValueError("run metadata description must not be blank")
        if not self.created_at or not self.updated_at:
            raise ValueError("run metadata timestamps must not be empty")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable run metadata mapping."""
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RunMetadata:
        """Validate and reconstruct run metadata from a parsed JSON mapping."""
        required = {"schema_version", "created_at", "updated_at"}
        missing = required - set(value)
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"run metadata is missing: {names}")
        return cls(
            schema_version=_required_int(value, "schema_version"),
            name=_optional_string(value, "name"),
            description=_optional_string(value, "description"),
            created_at=_required_string(value, "created_at"),
            updated_at=_required_string(value, "updated_at"),
        )


def run_metadata_path(run_directory: Path | str) -> Path:
    """Return where a run's own metadata sidecar lives.

    Args:
        run_directory: The run's own output directory.

    Returns:
        `run_directory / "metadata.json"`.
    """
    return Path(run_directory) / RUN_METADATA_FILE_NAME


def read_run_metadata(path: Path | str) -> RunMetadata:
    """Read and validate one run metadata JSON file.

    Args:
        path: The `metadata.json` file to read.

    Returns:
        The parsed, validated run metadata.

    Raises:
        OSError: The file cannot be read.
        ValueError: The file's content is not a valid run metadata object.
    """
    metadata_path = Path(path)
    logger.debug("reading run metadata: %s", metadata_path)
    with metadata_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("run metadata root must be an object")
    return RunMetadata.from_dict(payload)


def write_run_metadata(path: Path | str, metadata: RunMetadata) -> None:
    """Atomically write `metadata` to `path`, creating parent directories as needed.

    Same mkstemp-then-`os.replace` idiom as `fim.gui.preferences.
    save_preferences` — a concurrent reader always sees either the
    previous complete file or the new one, never a torn write. Unlike
    `fim.persistence.manifest.write_manifest`, this file is never built
    inside a `fim.paths.atomic_directory` block (it is written beside an
    already-published run, potentially long after the fact), so it needs
    its own atomicity here rather than inheriting a caller's.
    """
    metadata_path = Path(path)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(metadata.to_dict(), indent=2, sort_keys=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=metadata_path.parent, prefix=".metadata-"
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temp_file:
            temp_file.write(payload)
        temp_path.replace(metadata_path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    logger.debug("wrote run metadata: %s", metadata_path)


def replace_run_metadata(
    run_directory: Path | str,
    *,
    name: str | None,
    description: str | None,
    clock: Clock = _utc_now,
) -> RunMetadata:
    """Create or overwrite a run's own metadata, preserving `created_at`.

    The single entry point for both "attach a name at creation" (`fim
    run --name/--description`) and "rename after the fact" (a GUI
    action on an already-completed run) — whichever `metadata.json`
    already exists (if any) only ever contributes its own `created_at`;
    `name`/`description` are always replaced wholesale with the values
    given here, never merged field-by-field, so clearing a field is as
    simple as passing `None` for it.

    Args:
        run_directory: The run's own output directory.
        name: The new short name, or `None` to leave/set it unset.
        description: The new longer description, or `None` to leave/set
            it unset.
        clock: Injectable current-time source, for deterministic tests.

    Returns:
        The metadata just written.
    """
    path = run_metadata_path(run_directory)
    created_at: str | None = None
    if path.is_file():
        try:
            created_at = read_run_metadata(path).created_at
        except (OSError, ValueError) as error:
            logger.debug("ignoring unreadable prior run metadata %s: %s", path, error)
    now = _format_timestamp(clock())
    metadata = RunMetadata(
        schema_version=CURRENT_RUN_METADATA_SCHEMA_VERSION,
        name=name,
        description=description,
        created_at=created_at if created_at is not None else now,
        updated_at=now,
    )
    write_run_metadata(path, metadata)
    return metadata


def _required_int(value: Mapping[str, Any], key: str, *, minimum: int = 1) -> int:
    """Read one required integer field bounded below by `minimum`."""
    raw_value = value.get(key)
    if isinstance(raw_value, bool) or not isinstance(raw_value, int):
        raise ValueError(f"run metadata field {key!r} must be an integer")
    if raw_value < minimum:
        raise ValueError(f"run metadata field {key!r} must be at least {minimum}")
    return raw_value


def _required_string(value: Mapping[str, Any], key: str) -> str:
    """Read one required nonempty string field."""
    raw_value = value.get(key)
    if not isinstance(raw_value, str) or not raw_value:
        raise ValueError(f"run metadata field {key!r} must be a nonempty string")
    return raw_value


def _optional_string(value: Mapping[str, Any], key: str) -> str | None:
    """Read one optional nonempty string field, or `None` when absent/null."""
    raw_value = value.get(key)
    if raw_value is None:
        return None
    if not isinstance(raw_value, str) or not raw_value:
        raise ValueError(
            f"run metadata field {key!r} must be a nonempty string or null"
        )
    return raw_value
