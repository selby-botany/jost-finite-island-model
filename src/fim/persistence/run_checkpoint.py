"""Publish and verify immutable checkpoints for resumable Run objects.

Each checkpoint contains versioned JSON metadata and a NumPy archive. The
lifecycle record points to the authoritative checkpoint only after the staged
directory has been flushed and published. Digests verify both files, and
unreferenced checkpoint directories are not authoritative.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import uuid
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np
import numpy.typing as npt

from fim.paths import default_directory_mode, default_file_mode
from fim.persistence.run_lifecycle import (
    AttemptMismatchError,
    CheckpointReference,
    LifecycleError,
    RunPhase,
)
from fim.persistence.run_ownership import ExecutionLease

CHECKPOINT_SCHEMA_VERSION: Final = 1
CHECKPOINTS_DIRECTORY_NAME: Final = "checkpoints"
CHECKPOINT_FILE_NAME: Final = "checkpoint.json"
ARRAYS_FILE_NAME: Final = "arrays.npz"
_STAGING_PREFIX: Final = ".staging-"
# A fixed timestamp keeps archive bytes a pure function of their contents.
_ZIP_DATE_TIME: Final = (1980, 1, 1, 0, 0, 0)

Arrays = Mapping[str, npt.NDArray[Any]]
FaultHook = Callable[[str], None]


class CheckpointError(LifecycleError):
    """A checkpoint is missing, corrupt, or does not match its reference."""


class UnsupportedCheckpointError(CheckpointError):
    """A checkpoint uses a schema version this program cannot execute."""


@dataclass(frozen=True, slots=True)
class LoadedCheckpoint:
    """A verified checkpoint read back from disk.

    Args:
        reference: The reference it was verified against.
        payload: The engine's JSON metadata.
        arrays: The engine's arrays, by name.
    """

    reference: CheckpointReference
    payload: dict[str, Any]
    arrays: dict[str, npt.NDArray[Any]]


def checkpoints_directory(object_directory: Path) -> Path:
    """Return the directory holding an object's checkpoints.

    Args:
        object_directory: The Run object's directory.

    Returns:
        `object_directory / "checkpoints"`.
    """
    return object_directory / CHECKPOINTS_DIRECTORY_NAME


def collect_checkpoint_garbage(
    object_directory: Path, keep: CheckpointReference | None
) -> tuple[str, ...]:
    """Remove every checkpoint directory the lifecycle pointer does not name.

    Call only while owning execution (or holding the object exclusively), so
    no concurrent publication can be mid-flight.

    Args:
        object_directory: The Run object's directory.
        keep: The referenced checkpoint to keep, or `None` to remove all.

    Returns:
        The names removed, sorted.
    """
    directory = checkpoints_directory(object_directory)
    if not directory.is_dir():
        return ()
    keep_name = None if keep is None else keep.checkpoint_id
    removed = sorted(
        entry.name
        for entry in directory.iterdir()
        if entry.name != keep_name and entry.is_dir()
    )
    for name in removed:
        shutil.rmtree(directory / name)
    return tuple(removed)


def commit_checkpoint(
    lease: ExecutionLease,
    *,
    payload: Mapping[str, Any],
    arrays: Arrays,
    committed_generation: int,
    phase: RunPhase | None = None,
    checkpoint_id: str | None = None,
    fault: FaultHook | None = None,
) -> CheckpointReference:
    """Publish a checkpoint and make it the object's latest, then prune.

    Args:
        lease: The owning attempt's lease.
        payload: Engine metadata (JSON-safe; integers above 2**53 must be
            encoded as strings by the caller).
        arrays: Engine arrays (no object arrays).
        committed_generation: Last generation the checkpoint fully contains.
        phase: Execution phase to record with the pointer, if it changed.
        checkpoint_id: Explicit directory name (default: generated).
        fault: Test hook called with `"staged"`, `"published"` and
            `"committed"` after each step; raising simulates a crash there.

    Returns:
        The reference now recorded in `run.json`.

    Raises:
        AttemptMismatchError: The lease no longer owns execution; the newly
            published directory is removed and the pointer is unchanged.
    """
    directory = lease.current().directory
    reference = publish_checkpoint(
        directory,
        payload=payload,
        arrays=arrays,
        committed_generation=committed_generation,
        checkpoint_id=checkpoint_id,
        fault=fault,
    )
    try:
        lease.commit_progress(phase=phase, checkpoint=reference)
    except AttemptMismatchError:
        shutil.rmtree(
            checkpoints_directory(directory) / reference.checkpoint_id,
            ignore_errors=True,
        )
        raise
    _fire(fault, "committed")
    # The previous checkpoint is kept until the new pointer is durable.
    collect_checkpoint_garbage(directory, reference)
    return reference


def load_checkpoint(
    object_directory: Path, reference: CheckpointReference
) -> LoadedCheckpoint:
    """Read and verify the checkpoint a lifecycle record points at.

    Args:
        object_directory: The Run object's directory.
        reference: The record's `latest_checkpoint`.

    Returns:
        The verified payload and arrays.

    Raises:
        CheckpointError: Files are missing or do not match the reference.
        UnsupportedCheckpointError: The schema version is not executable.
    """
    directory = checkpoints_directory(object_directory) / reference.checkpoint_id
    try:
        document_bytes = (directory / CHECKPOINT_FILE_NAME).read_bytes()
        archive_bytes = (directory / ARRAYS_FILE_NAME).read_bytes()
    except OSError as error:
        raise CheckpointError(
            f"checkpoint {reference.checkpoint_id!r} is unreadable: {error}"
        ) from error
    if _sha256(document_bytes) != reference.digest:
        raise CheckpointError(
            f"checkpoint {reference.checkpoint_id!r} does not match its digest"
        )
    document = _parse_document(document_bytes, reference)
    if _sha256(archive_bytes) != document["arrays_sha256"]:
        raise CheckpointError(
            f"checkpoint {reference.checkpoint_id!r} arrays do not match"
        )
    arrays = _read_arrays(archive_bytes)
    if sorted(arrays) != document["array_names"]:
        raise CheckpointError(
            f"checkpoint {reference.checkpoint_id!r} array names do not match"
        )
    return LoadedCheckpoint(
        reference=reference, payload=dict(document["payload"]), arrays=arrays
    )


def new_checkpoint_id(committed_generation: int) -> str:
    """Return a fresh checkpoint directory name that sorts by generation.

    Args:
        committed_generation: The checkpoint's committed generation.

    Returns:
        `ckpt-<generation, 12 digits>-<12 random hex digits>`.
    """
    return f"ckpt-{committed_generation:012d}-{uuid.uuid4().hex[:12]}"


def publish_checkpoint(
    object_directory: Path,
    *,
    payload: Mapping[str, Any],
    arrays: Arrays,
    committed_generation: int,
    checkpoint_id: str | None = None,
    fault: FaultHook | None = None,
) -> CheckpointReference:
    """Write a checkpoint durably and publish its directory by atomic rename.

    This does not update `run.json`; until a lease commits the returned
    reference the new directory is unreferenced garbage.

    Args:
        object_directory: The Run object's directory.
        payload: Engine metadata (JSON-safe).
        arrays: Engine arrays (no object arrays).
        committed_generation: Last generation the checkpoint fully contains.
        checkpoint_id: Explicit directory name (default: generated).
        fault: Test hook; see `commit_checkpoint`.

    Returns:
        The reference to commit.

    Raises:
        ValueError: The payload is not JSON-safe or an array is an object
            array, or `committed_generation` is negative.
        FileExistsError: `checkpoint_id` is already published.
    """
    if committed_generation < 0:
        raise ValueError("committed_generation must be non-negative")
    name = checkpoint_id or new_checkpoint_id(committed_generation)
    if name.startswith(".") or "/" in name or os.sep in name:
        raise ValueError(f"invalid checkpoint id {name!r}")
    archive_bytes = _write_arrays(arrays)
    document_bytes = _document_bytes(
        payload,
        committed_generation=committed_generation,
        arrays_sha256=_sha256(archive_bytes),
        array_names=sorted(arrays),
    )
    parent = checkpoints_directory(object_directory)
    parent.mkdir(mode=default_directory_mode(), parents=True, exist_ok=True)
    target = parent / name
    if target.exists():
        raise FileExistsError(f"checkpoint {name!r} already exists")
    staging = parent / f"{_STAGING_PREFIX}{name}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(mode=default_directory_mode())
    try:
        _write_durably(staging / ARRAYS_FILE_NAME, archive_bytes)
        _write_durably(staging / CHECKPOINT_FILE_NAME, document_bytes)
        _fsync_directory(staging)
        _fire(fault, "staged")
        staging.rename(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    _fsync_directory(parent)
    _fire(fault, "published")
    return CheckpointReference(
        checkpoint_id=name,
        digest=_sha256(document_bytes),
        committed_generation=committed_generation,
    )


def _document_bytes(
    payload: Mapping[str, Any],
    *,
    committed_generation: int,
    arrays_sha256: str,
    array_names: list[str],
) -> bytes:
    """Return the canonical bytes of `checkpoint.json`."""
    document = {
        "array_names": array_names,
        "arrays_sha256": arrays_sha256,
        "committed_generation": committed_generation,
        "payload": dict(payload),
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
    }
    try:
        text = json.dumps(
            document, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"checkpoint payload is not JSON-safe: {error}") from error
    return (text + "\n").encode("utf-8")


def _fire(fault: FaultHook | None, point: str) -> None:
    """Call the test fault hook, if any, at a named point."""
    if fault is not None:
        fault(point)


def _fsync_directory(directory: Path) -> None:
    """Flush a directory entry to disk where the platform allows it."""
    with contextlib.suppress(OSError):
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _parse_document(
    document_bytes: bytes, reference: CheckpointReference
) -> dict[str, Any]:
    """Parse and structurally validate `checkpoint.json`."""
    label = f"checkpoint {reference.checkpoint_id!r}"
    try:
        document = json.loads(document_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CheckpointError(f"{label} is not valid JSON") from error
    if not isinstance(document, dict):
        raise CheckpointError(f"{label} is not a JSON object")
    version = document.get("schema_version")
    if version != CHECKPOINT_SCHEMA_VERSION:
        raise UnsupportedCheckpointError(
            f"{label} has schema version {version!r}; "
            f"this program executes version {CHECKPOINT_SCHEMA_VERSION}"
        )
    names = document.get("array_names")
    if not (
        isinstance(document.get("payload"), dict)
        and isinstance(document.get("arrays_sha256"), str)
        and isinstance(names, list)
        and all(isinstance(name, str) for name in names)
    ):
        raise CheckpointError(f"{label} is malformed")
    if document.get("committed_generation") != reference.committed_generation:
        raise CheckpointError(f"{label} generation does not match its reference")
    return document


def _read_arrays(archive_bytes: bytes) -> dict[str, npt.NDArray[Any]]:
    """Read every array from an archive written by `_write_arrays`."""
    try:
        with np.load(io.BytesIO(archive_bytes), allow_pickle=False) as archive:
            return {name: archive[name] for name in archive.files}
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        raise CheckpointError(f"checkpoint arrays are unreadable: {error}") from error


def _sha256(data: bytes) -> str:
    """Return the SHA-256 hex digest of `data`."""
    return hashlib.sha256(data).hexdigest()


def _write_arrays(arrays: Arrays) -> bytes:
    """Return deterministic `.npz` bytes for `arrays`, sorted by name."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(arrays):
            values = np.asarray(arrays[name])
            if values.dtype.hasobject:
                raise ValueError(f"checkpoint array {name!r} holds Python objects")
            entry = zipfile.ZipInfo(f"{name}.npy", date_time=_ZIP_DATE_TIME)
            entry.external_attr = 0o644 << 16
            with archive.open(entry, "w", force_zip64=True) as stream:
                np.lib.format.write_array(stream, values, allow_pickle=False)
    return buffer.getvalue()


def _write_durably(path: Path, data: bytes) -> None:
    """Write `data` to a new file and flush it to disk."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(default_file_mode())
