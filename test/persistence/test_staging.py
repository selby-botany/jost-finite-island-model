"""Tests for `fim.persistence.staging`: orphan staging reclamation.

Builders that died are modelled by staging directories whose locks no
one holds; live builders by locks this process holds. Ages come from an
injected clock, so no test depends on the wall clock.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fim.paths import STAGING_LOCK_FILENAME
from fim.persistence.object_lock import exclusive_lock
from fim.persistence.run_checkpoint import CHECKPOINTS_DIRECTORY_NAME
from fim.persistence.run_objects import objects_directory
from fim.persistence.run_ownership import attempt_lock_path
from fim.persistence.staging import (
    CHECKPOINT_STAGING_PREFIX,
    STAGING_GRACE_SECONDS,
    StagingKind,
    find_orphan_staging,
    reclaim_orphan_staging,
)

_OBJECT_ID = "obj-" + "a" * 32
_LOCK_TIME = 1_000_000.0
_LATER = _LOCK_TIME + STAGING_GRACE_SECONDS + 1


def _later() -> float:
    return _LATER


def _output_staging(parent: Path, *, locked: bool = True) -> Path:
    """Return a dead builder's output staging, as `atomic_directory` makes."""
    staging = parent / ".run.abcd1234"
    staging.mkdir(parents=True)
    (staging / "report.json").write_text("{}", encoding="utf-8")
    if locked:
        lock = staging / STAGING_LOCK_FILENAME
        lock.touch()
        os.utime(lock, (_LOCK_TIME, _LOCK_TIME))
    return staging


def _checkpoint_staging(results: Path) -> Path:
    """Return a dead attempt's checkpoint staging inside its object."""
    staging = (
        objects_directory(results)
        / _OBJECT_ID
        / CHECKPOINTS_DIRECTORY_NAME
        / f"{CHECKPOINT_STAGING_PREFIX}0001"
    )
    staging.mkdir(parents=True)
    return staging


def test_an_unheld_old_owner_lock_is_reclaimed(tmp_path: Path) -> None:
    """Both the results directory and `objects/` are scanned."""
    top = _output_staging(tmp_path)
    nested = _output_staging(objects_directory(tmp_path))

    orphans = reclaim_orphan_staging(tmp_path, now=_later)

    assert [(o.path, o.kind, o.reclaimable) for o in orphans] == [
        (top, StagingKind.OUTPUT, True),
        (nested, StagingKind.OUTPUT, True),
    ]
    assert not top.exists()
    assert not nested.exists()


def test_a_new_owner_lock_is_within_the_grace_period(tmp_path: Path) -> None:
    """A lock not yet old enough to judge is not listed at all."""
    _output_staging(tmp_path)

    assert find_orphan_staging(tmp_path, now=lambda: _LOCK_TIME + 1) == []


def test_a_held_owner_lock_marks_a_live_builder(tmp_path: Path) -> None:
    """A builder still holding its lock is neither listed nor removed."""
    staging = _output_staging(tmp_path)

    with exclusive_lock(staging / STAGING_LOCK_FILENAME):
        assert reclaim_orphan_staging(tmp_path, now=_later) == []
    assert staging.exists()


def test_staging_without_an_owner_lock_is_reported_and_kept(
    tmp_path: Path,
) -> None:
    """Nothing proves such a builder is gone, so only a person removes it."""
    staging = _output_staging(tmp_path, locked=False)

    (orphan,) = reclaim_orphan_staging(tmp_path, now=_later)

    assert orphan.path == staging
    assert not orphan.reclaimable
    assert "remove it by hand" in orphan.reason
    assert staging.exists()


def test_an_owner_lock_deleted_before_removal_keeps_the_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A builder that deletes its lock on the way to publishing wins.

    The removal must neither recreate the deleted lock nor remove the
    staging that is about to be renamed into place.
    """
    staging = _output_staging(tmp_path)
    orphans = find_orphan_staging(tmp_path, now=_later)
    (staging / STAGING_LOCK_FILENAME).unlink()

    from fim.persistence import staging as module  # noqa: PLC0415

    monkeypatch.setattr(module, "find_orphan_staging", lambda *_, **__: orphans)
    reclaim_orphan_staging(tmp_path, now=_later)

    assert (staging / "report.json").exists()
    assert not (staging / STAGING_LOCK_FILENAME).exists()


def test_checkpoint_staging_is_reclaimed_when_no_attempt_runs(
    tmp_path: Path,
) -> None:
    """The free attempt lock proves no attempt can still publish it."""
    staging = _checkpoint_staging(tmp_path)

    (orphan,) = reclaim_orphan_staging(tmp_path)

    assert orphan.kind is StagingKind.CHECKPOINT
    assert orphan.reclaimable
    assert not staging.exists()


def test_checkpoint_staging_of_an_executing_object_is_kept(
    tmp_path: Path,
) -> None:
    """An attempt holding its lock may be publishing that checkpoint."""
    staging = _checkpoint_staging(tmp_path)

    with exclusive_lock(attempt_lock_path(_OBJECT_ID, tmp_path)):
        assert reclaim_orphan_staging(tmp_path) == []
    assert staging.exists()


def test_a_dry_run_removes_nothing(tmp_path: Path) -> None:
    """Every reclaimable orphan is reported and left in place."""
    output = _output_staging(tmp_path)
    checkpoint = _checkpoint_staging(tmp_path)

    orphans = reclaim_orphan_staging(tmp_path, dry_run=True, now=_later)

    assert {orphan.path for orphan in orphans} == {output, checkpoint}
    assert output.exists()
    assert checkpoint.exists()


def test_unrelated_directories_are_ignored(tmp_path: Path) -> None:
    """Published runs, ordinary hidden folders, and non-objects stay."""
    (tmp_path / "run-1").mkdir()
    (tmp_path / ".hidden").mkdir()
    stray = (
        objects_directory(tmp_path)
        / "not-an-object"
        / CHECKPOINTS_DIRECTORY_NAME
        / f"{CHECKPOINT_STAGING_PREFIX}0001"
    )
    stray.mkdir(parents=True)

    assert find_orphan_staging(tmp_path, now=_later) == []


def test_a_missing_results_directory_has_no_orphans(tmp_path: Path) -> None:
    """Scanning a results directory not yet created finds nothing."""
    assert find_orphan_staging(tmp_path / "absent", now=_later) == []
