"""Find and reclaim staging directories that a killed process left behind.

Two writers build output in a hidden staging directory and publish it with
one atomic rename:

- `fim.paths.atomic_directory` stages a run's output folder, or a new Run
  object's directory, as `.<name>.<random>` beside the target. It holds an
  owner lock, `.fim-staging.lock`, inside the staging directory while it
  builds and removes it just before the rename.
- `fim.persistence.run_checkpoint.publish_checkpoint` stages a checkpoint
  as `checkpoints/.staging-<id>` inside its object directory, only while
  the object's attempt lock is held.

A process killed by SIGKILL (or power loss) runs no cleanup, so its staging
directory stays. A staging directory is reclaimed only when its builder is
provably gone:

- output staging: its owner lock exists, nobody holds it, and it is older
  than a grace period, which covers the instant between creating the lock
  file and locking it;
- checkpoint staging: the object's attempt lock can be taken, and is held
  while the directory is removed, so no attempt can start meanwhile.

Output staging with no owner lock predates the lock (or failed in the
instant before the rename). It is reported but never removed automatically,
because nothing proves its builder is gone.

Only the results directory itself and its `objects/` directory are scanned
for output staging; a run published elsewhere with `fim run --output` is
not.
"""

from __future__ import annotations

import logging
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from fim.paths import STAGING_LOCK_FILENAME, results_directory
from fim.persistence.object_lock import (
    LockUnavailableError,
    exclusive_lock,
    is_lock_held,
)
from fim.persistence.run_checkpoint import CHECKPOINTS_DIRECTORY_NAME
from fim.persistence.run_lifecycle import is_object_id
from fim.persistence.run_objects import objects_directory
from fim.persistence.run_ownership import attempt_lock_path

logger = logging.getLogger(__name__)

STAGING_GRACE_SECONDS: Final = 60.0
"""Minimum age of an unheld owner lock before its staging is reclaimed."""

CHECKPOINT_STAGING_PREFIX: Final = ".staging-"
"""Prefix of a checkpoint's staging directory (`run_checkpoint`)."""

# `tempfile.mkdtemp(prefix=".<name>.")` appends eight characters.
_OUTPUT_STAGING: Final = re.compile(r"^\.(?P<target>.+)\.[a-z0-9_]{8}$")


class StagingKind(StrEnum):
    """Which writer created a staging directory."""

    OUTPUT = "output"
    CHECKPOINT = "checkpoint"


@dataclass(frozen=True, slots=True)
class OrphanStaging:
    """A staging directory no live writer is building.

    Attributes:
        path: The staging directory.
        kind: Which writer created it.
        reclaimable: Whether its builder is provably gone.
        reason: Why it is, or is not, reclaimable.
    """

    path: Path
    kind: StagingKind
    reclaimable: bool
    reason: str


def find_orphan_staging(
    results: Path | None = None,
    *,
    now: Callable[[], float] = time.time,
    grace_seconds: float = STAGING_GRACE_SECONDS,
) -> list[OrphanStaging]:
    """Return every staging directory no live writer is building.

    Args:
        results: Results directory (default: `results_directory()`).
        now: Current time in seconds since the epoch, for the grace period.
        grace_seconds: Minimum age of an unheld owner lock.

    Returns:
        The orphans, sorted by path. A staging directory whose writer is
        alive, or whose owner lock is too new to judge, is not listed.
    """
    root = results if results is not None else results_directory()
    found: list[OrphanStaging] = []
    for parent in (root, objects_directory(root)):
        found.extend(_output_orphans(parent, now(), grace_seconds))
    found.extend(_checkpoint_orphans(root))
    return sorted(found, key=lambda orphan: orphan.path)


def reclaim_orphan_staging(
    results: Path | None = None,
    *,
    dry_run: bool = False,
    now: Callable[[], float] = time.time,
    grace_seconds: float = STAGING_GRACE_SECONDS,
) -> list[OrphanStaging]:
    """Remove every reclaimable orphan staging directory.

    Args:
        results: Results directory (default: `results_directory()`).
        dry_run: Report what would be removed without removing it.
        now: Current time in seconds since the epoch, for the grace period.
        grace_seconds: Minimum age of an unheld owner lock.

    Returns:
        Every orphan found, reclaimable or not, sorted by path. Without
        `dry_run`, each reclaimable one has been removed, unless a writer
        claimed it meanwhile.
    """
    root = results if results is not None else results_directory()
    orphans = find_orphan_staging(root, now=now, grace_seconds=grace_seconds)
    if not dry_run:
        for orphan in orphans:
            if orphan.reclaimable:
                _remove(orphan, root)
    return orphans


def _checkpoint_orphans(root: Path) -> list[OrphanStaging]:
    """Return checkpoint staging in objects with no executing attempt."""
    found: list[OrphanStaging] = []
    parent = objects_directory(root)
    if not parent.is_dir():
        return found
    for directory in sorted(parent.iterdir()):
        if not is_object_id(directory.name):
            continue
        checkpoints = directory / CHECKPOINTS_DIRECTORY_NAME
        if not checkpoints.is_dir():
            continue
        staging = sorted(
            entry
            for entry in checkpoints.iterdir()
            if entry.is_dir() and entry.name.startswith(CHECKPOINT_STAGING_PREFIX)
        )
        if not staging or is_lock_held(attempt_lock_path(directory.name, root)):
            continue
        found.extend(
            OrphanStaging(
                entry,
                StagingKind.CHECKPOINT,
                reclaimable=True,
                reason="no attempt is executing its object",
            )
            for entry in staging
        )
    return found


def _output_orphans(
    parent: Path, now: float, grace_seconds: float
) -> list[OrphanStaging]:
    """Return output staging in `parent` whose builder is not alive."""
    found: list[OrphanStaging] = []
    if not parent.is_dir():
        return found
    for entry in sorted(parent.iterdir()):
        if not entry.is_dir() or not _OUTPUT_STAGING.match(entry.name):
            continue
        lock = entry / STAGING_LOCK_FILENAME
        try:
            age = now - lock.stat().st_mtime
        except FileNotFoundError:
            found.append(
                OrphanStaging(
                    entry,
                    StagingKind.OUTPUT,
                    reclaimable=False,
                    reason="no owner lock proves its builder is gone; "
                    "remove it by hand if no fim process is running",
                )
            )
            continue
        if age < grace_seconds or is_lock_held(lock):
            continue
        found.append(
            OrphanStaging(
                entry,
                StagingKind.OUTPUT,
                reclaimable=True,
                reason="its builder no longer holds the owner lock",
            )
        )
    return found


def _remove(orphan: OrphanStaging, root: Path) -> None:
    """Remove one reclaimable orphan, rechecking under the lock that guards it."""
    if orphan.kind is StagingKind.CHECKPOINT:
        object_id = orphan.path.parent.parent.name
        guard = attempt_lock_path(object_id, root)
    else:
        guard = orphan.path / STAGING_LOCK_FILENAME
    # An output owner lock is never recreated: a missing one means its
    # builder deleted it on the way to publishing. Its builder may also
    # delete it after this opens it, so its presence is checked again
    # once locked; a lock taken on a deleted file proves nothing.
    output = orphan.kind is StagingKind.OUTPUT
    removed = False
    try:
        with exclusive_lock(guard, blocking=False, create=not output):
            if not output or guard.exists():
                shutil.rmtree(orphan.path, ignore_errors=True)
                removed = True
    except (LockUnavailableError, FileNotFoundError):
        pass
    if not removed:
        logger.info("staging %s was claimed meanwhile; kept", orphan.path)
        return
    if output:
        # The owner lock was open during removal, which Windows refuses.
        shutil.rmtree(orphan.path, ignore_errors=True)
    logger.info("reclaimed orphan staging %s", orphan.path)
