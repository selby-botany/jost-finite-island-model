"""Verify a Run object's completion receipt before recovering completion.

A worker writes a completed run's artifacts and then, last, its manifest:
for a scalar run the run manifest, for a batch the batch manifest. That
manifest is the run's *completion receipt*: it names the scientific run ID
and records the size and SHA-256 digest of every other artifact. A worker
that stops after publishing the receipt but before committing `completed`
leaves a run whose output is whole. Reconciliation completes such a run
(`recover-completion`) only when the receipt verifies here; otherwise the
run is interrupted and a resume finalizes it again.

Verification is strict: the receipt must parse, name the run's own ID, and
match every file it lists. A batch receipt is checked recursively, each
replicate's own receipt included. File sizes are compared before any file
is hashed, so a truncated or missing artifact is rejected cheaply.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Final

from fim.persistence.binary_store import (
    EQUILIBRIUM_LOG_FILENAME,
    TRAJECTORY_LOG_FILENAME,
)
from fim.persistence.manifest import (
    ArtifactDigest,
    hash_file,
    read_batch_manifest,
    read_manifest,
)
from fim.persistence.run_lifecycle import RunKind

RECEIPT_FILENAME: Final = "manifest.json"
"""The completion receipt, written after every artifact it lists."""

BATCH_SUMMARY_KEY: Final = "summary"
"""The batch receipt's digest key for `summary.json`."""

BATCH_SUMMARY_FILENAME: Final = "summary.json"
"""A completed batch's cross-replicate summary."""

ARTIFACT_FILENAMES: Final = {
    "trajectory": TRAJECTORY_LOG_FILENAME,
    "equilibrium_trajectory": EQUILIBRIUM_LOG_FILENAME,
    "report": "report.json",
    "scatter": "scatter.png",
    "convergence": "convergence.jsonl",
    "pairwise": "pairwise.json",
}
"""Each scalar artifact key a receipt may list, and the file it names.

The same names `fim.cli._run_artifact_targets` and
`fim.gui.runner.run_artifact_targets` use, less the receipt itself; a test
keeps the three in agreement."""


def receipt_problem(directory: Path, *, kind: RunKind, run_id: str) -> str | None:
    """Return why a run's completion receipt does not verify, or `None`.

    Args:
        directory: The Run object's directory.
        kind: Whether the run is one replicate or a batch.
        run_id: The scientific run ID the receipt must name.

    Returns:
        `None` when the receipt and every artifact it lists verify;
        otherwise a short description of the first problem found.
    """
    if kind is RunKind.BATCH:
        return _batch_problem(directory, run_id)
    return _scalar_problem(directory, run_id)


def _batch_problem(directory: Path, run_id: str) -> str | None:
    """Verify a batch receipt, its summary, and every replicate receipt."""
    path = directory / RECEIPT_FILENAME
    try:
        manifest = read_batch_manifest(path)
    except (OSError, ValueError) as error:
        return _unreadable_receipt(path, error)
    if manifest.run_id != run_id:
        return f"receipt {path} names run {manifest.run_id!r}, not {run_id!r}"
    replicates: dict[str, str] = {}
    for replicate_run_id in manifest.replicate_run_ids:
        name = _replicate_directory_name(run_id, replicate_run_id)
        if name is None:
            return f"receipt {path} names a foreign replicate {replicate_run_id!r}"
        replicates[name] = replicate_run_id
    artifacts = manifest.artifacts or {}
    if set(artifacts) != {BATCH_SUMMARY_KEY, *replicates}:
        return f"receipt {path} lists unexpected artifacts {sorted(artifacts)}"
    return next(
        (
            problem
            for problem in _batch_file_problems(directory, artifacts, replicates)
            if problem is not None
        ),
        None,
    )


def _batch_file_problems(
    directory: Path,
    artifacts: Mapping[str, ArtifactDigest],
    replicates: Mapping[str, str],
) -> Iterator[str | None]:
    """Check a batch's files lazily, so verification stops at the first problem.

    Args:
        directory: The batch object's directory.
        artifacts: The batch receipt's digests.
        replicates: Each replicate directory name and its run ID.

    Yields:
        Each check's problem, or `None` when that check passed.
    """
    for key, digest in sorted(artifacts.items()):
        relative = (
            BATCH_SUMMARY_FILENAME
            if key == BATCH_SUMMARY_KEY
            else f"{key}/{RECEIPT_FILENAME}"
        )
        yield _digest_problem(directory / relative, digest)
    for name, replicate_run_id in sorted(replicates.items()):
        yield _scalar_problem(directory / name, replicate_run_id)


def _digest_problem(path: Path, digest: ArtifactDigest) -> str | None:
    """Compare one file with its recorded digest, size first."""
    try:
        if path.stat().st_size != digest["bytes"]:
            return f"artifact {path} does not have its recorded size"
        if hash_file(path) != digest:
            return f"artifact {path} does not match its recorded digest"
    except FileNotFoundError:
        return f"artifact {path} is missing"
    except OSError as error:
        return f"artifact {path} is unreadable: {error}"
    return None


def _replicate_directory_name(run_id: str, replicate_run_id: str) -> str | None:
    """Return `replicate-NNN` for `<run_id>-rNNN`, or `None` if foreign."""
    prefix = f"{run_id}-r"
    suffix = replicate_run_id.removeprefix(prefix)
    if suffix == replicate_run_id or not suffix.isdigit():
        return None
    return f"replicate-{int(suffix):03}"


def _scalar_problem(directory: Path, run_id: str) -> str | None:
    """Verify one replicate's receipt and every artifact it lists."""
    path = directory / RECEIPT_FILENAME
    try:
        manifest = read_manifest(path)
    except (OSError, ValueError) as error:
        return _unreadable_receipt(path, error)
    if manifest.run_id != run_id:
        return f"receipt {path} names run {manifest.run_id!r}, not {run_id!r}"
    artifacts = manifest.artifacts
    if not artifacts or "trajectory" not in artifacts:
        return f"receipt {path} does not list the trajectory"
    for key, digest in sorted(artifacts.items()):
        filename = ARTIFACT_FILENAMES.get(key)
        if filename is None:
            return f"receipt {path} lists an unknown artifact {key!r}"
        problem = _digest_problem(directory / filename, digest)
        if problem is not None:
            return problem
    return None


def _unreadable_receipt(path: Path, error: OSError | ValueError) -> str:
    """Describe a receipt that could not be read."""
    if isinstance(error, FileNotFoundError):
        return f"no receipt {path} was published"
    return f"unreadable receipt {path}: {error}"
