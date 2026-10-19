"""Tests of `fim.persistence.taper` and of tapering a finished run.

The log is written with the real writer from synthetic frames, so what a
taper keeps and what a log written from only those frames would hold can be
compared byte for byte.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tlog_support import LAYOUT, same_frames, walk

from fim.persistence.frame import TrajectoryFrame
from fim.persistence.retention import TrajectoryRetention, tapered_generations
from fim.persistence.taper import taper_log
from fim.persistence.tlog import LogWriter, TlogError, iter_frames, recover

RUN_ID = "run-taper"
OPTIONS = {"key_every": 16, "block_generations": 32}


def _write(path: Path, frames: list[TrajectoryFrame], **options: object) -> None:
    """Write `frames` with the writer options of a run."""
    writer = LogWriter(path, RUN_ID, LAYOUT, **{**OPTIONS, **options})  # type: ignore[arg-type]
    for frame in frames:
        writer.submit(frame)
    writer.close()


def test_tapering_keeps_exactly_the_chosen_generations(tmp_path: Path) -> None:
    frames = walk(120, change=0.1, seed=1)
    path = tmp_path / "t.tlog"
    _write(path, frames)
    kept = tapered_generations(119, 10, 30, keep_burn_in=False)

    result = taper_log(path, kept, options=OPTIONS)

    held = list(iter_frames(path))
    assert [f.generation for f in held] == kept
    same_frames(held, [frames[g] for g in kept])
    assert (result.frames_before, result.frames_after) == (120, len(kept))
    assert result.bytes_after < result.bytes_before


def test_a_tapered_log_equals_a_log_written_from_only_the_kept_frames(
    tmp_path: Path,
) -> None:
    frames = walk(150, change=0.05, seed=2)
    kept = tapered_generations(149, 12, 0, keep_burn_in=True)
    tapered = tmp_path / "tapered.tlog"
    direct = tmp_path / "direct.tlog"
    _write(tapered, frames)
    taper_log(tapered, kept, options=OPTIONS)
    _write(direct, [frames[g] for g in kept])

    assert tapered.read_bytes() == direct.read_bytes()


def test_tapering_twice_changes_nothing(tmp_path: Path) -> None:
    frames = walk(100, change=0.1, seed=3)
    path = tmp_path / "t.tlog"
    _write(path, frames)
    kept = tapered_generations(99, 8, 20)
    taper_log(path, kept, options=OPTIONS)
    once = path.read_bytes()

    taper_log(path, kept, options=OPTIONS)

    assert path.read_bytes() == once


def test_a_rolling_taper_equals_one_taper_at_the_end(tmp_path: Path) -> None:
    """Tapering as the run goes, then again at the end, equals tapering once."""
    frames = walk(200, change=0.08, seed=4)
    one_shot = tmp_path / "one.tlog"
    rolling = tmp_path / "rolling.tlog"
    _write(one_shot, frames)
    taper_log(
        one_shot, tapered_generations(199, 10, 0, keep_burn_in=True), options=OPTIONS
    )
    _write(rolling, frames[:121])
    taper_log(
        rolling, tapered_generations(120, 10, 0, keep_burn_in=True), options=OPTIONS
    )
    # The run goes on: the log now holds the early taper plus the new frames.
    held = list(iter_frames(rolling))
    later = rolling.with_name("later.tlog")
    _write(later, [*held, *frames[121:]])
    taper_log(
        later, tapered_generations(199, 10, 0, keep_burn_in=True), options=OPTIONS
    )

    assert later.read_bytes() == one_shot.read_bytes()


def test_a_generation_the_log_lacks_is_refused_and_the_original_is_kept(
    tmp_path: Path,
) -> None:
    frames = walk(40, change=0.1, seed=5)
    path = tmp_path / "t.tlog"
    _write(path, frames)
    before = path.read_bytes()

    with pytest.raises(ValueError, match="does not hold generation 99"):
        taper_log(path, [0, 10, 99], options=OPTIONS)

    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.taper"))


def test_a_damaged_log_is_not_replaced(tmp_path: Path) -> None:
    path = tmp_path / "t.tlog"
    path.write_bytes(b"not a trajectory log")

    with pytest.raises(TlogError):
        taper_log(path, [0], options=OPTIONS)

    assert path.read_bytes() == b"not a trajectory log"


def test_a_tapered_log_still_recovers_with_every_block_intact(tmp_path: Path) -> None:
    frames = walk(90, change=0.2, seed=6)
    path = tmp_path / "t.tlog"
    _write(path, frames)
    taper_log(path, tapered_generations(89, 9, 0), options=OPTIONS)

    scan = recover(path, truncate=False)

    assert scan.header.run_id == RUN_ID
    assert scan.blocks[-1].last_generation == 89


def test_the_retention_object_agrees_with_the_taper(tmp_path: Path) -> None:
    retention = TrajectoryRetention(tapered=True, keep_last=7, burn_in=15)
    frames = walk(80, change=0.1, seed=7)
    path = tmp_path / "t.tlog"
    _write(path, frames)

    taper_log(path, retention.kept_generations(79), options=OPTIONS)

    assert len(list(iter_frames(path))) == retention.count_through(79)
