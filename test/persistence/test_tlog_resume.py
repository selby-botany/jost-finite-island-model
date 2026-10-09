"""Checkpoint and resume: a killed run continues from a committed position.

A checkpoint records where the committed log ended (byte offset, chained
checksum, last generation, counts, a copy of the header). Resuming checks the
file against it with one comparison, cuts everything after it, and continues
with a keyframe. The central claim is the stage's exit criterion: a run that
is killed and resumed leaves exactly the log of the uninterrupted run, byte
for byte when the checkpoint falls on a block and keyframe boundary, and with
identical frames and exported JSONL in every case. Everything is seeded and
the kills are real process exits at injected points, never timers.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from tlog_support import LAYOUT, same_frames, walk

from fim.persistence.binary_store import BinaryLogStore
from fim.persistence.frame import FrameLayout, TrajectoryFrame, frame_to_rows
from fim.persistence.tlog import (
    LogPosition,
    LogWriter,
    TlogError,
    iter_frames,
    recover,
)
from fim.persistence.tlog_export import derive_jsonl

RUN_ID = "run-resume"
BACKSTOP_SECONDS = 120.0
OPTIONS: dict[str, Any] = {
    "mode": "sparse",
    "key_every": 16,
    "block_generations": 8,
}


def _write(path: Path, frames: list[TrajectoryFrame], **options: Any) -> None:
    """Write `frames` to a new log and close it."""
    merged = {**OPTIONS, **options}
    writer = LogWriter(path, RUN_ID, LAYOUT, **merged)
    for frame in frames:
        writer.submit(frame)
    writer.close()


def _interrupted_then_resumed(
    path: Path,
    frames: list[TrajectoryFrame],
    checkpoint_after: int,
    extra: int,
    **options: Any,
) -> LogPosition:
    """Write, checkpoint, write more, stop; then resume from the checkpoint.

    The generations written after the checkpoint belong to a state the
    checkpoint does not describe: resuming discards them, and the caller's
    stream continues from the checkpoint.
    """
    merged = {**OPTIONS, **options}
    first = LogWriter(path, RUN_ID, LAYOUT, **merged)
    for frame in frames[:checkpoint_after]:
        first.submit(frame)
    position = first.checkpoint()
    for frame in frames[checkpoint_after : checkpoint_after + extra]:
        first.submit(frame)
    first.flush()
    first.close()
    second = LogWriter(path, RUN_ID, LAYOUT, resume=position, **merged)
    for frame in frames[checkpoint_after:]:
        second.submit(frame)
    second.close()
    return position


@pytest.mark.parametrize("background", [False, True])
@pytest.mark.parametrize("mode", ["sparse", "dense"])
def test_resuming_at_a_block_and_keyframe_boundary_gives_the_uninterrupted_bytes(
    tmp_path: Path, mode: str, background: bool
) -> None:
    """Kill-and-resume equals the uninterrupted run, byte for byte."""
    frames = walk(80, change=0.1, seed=41)
    _write(tmp_path / "whole.tlog", frames, mode=mode, background=background)
    # 32 generations is a multiple of the block size (8) and key interval (16).
    _interrupted_then_resumed(
        tmp_path / "resumed.tlog",
        frames,
        checkpoint_after=32,
        extra=21,
        mode=mode,
        background=background,
    )
    assert (tmp_path / "resumed.tlog").read_bytes() == (
        tmp_path / "whole.tlog"
    ).read_bytes()


@pytest.mark.parametrize("checkpoint_after", [1, 5, 13, 33, 61])
def test_resuming_anywhere_keeps_every_frame_and_the_exported_jsonl(
    tmp_path: Path, checkpoint_after: int
) -> None:
    """Off a boundary the bytes may differ (a short block, an extra keyframe),
    but the generations, and so the canonical export, are the same."""
    frames = walk(70, change=0.15, seed=42)
    _write(tmp_path / "whole.tlog", frames)
    _interrupted_then_resumed(
        tmp_path / "resumed.tlog", frames, checkpoint_after=checkpoint_after, extra=9
    )
    same_frames(list(iter_frames(tmp_path / "resumed.tlog")), frames)
    whole = derive_jsonl(tmp_path / "whole.tlog")
    resumed = derive_jsonl(tmp_path / "resumed.tlog")
    assert (resumed.sha256, resumed.size, resumed.rows) == (
        whole.sha256,
        whole.size,
        whole.rows,
    )


def test_the_first_record_after_a_resume_is_a_keyframe(tmp_path: Path) -> None:
    """The previous frame is not in memory, so the log restarts from a keyframe."""
    frames = walk(40, change=0.05, seed=43)
    _interrupted_then_resumed(
        tmp_path / "t.tlog", frames, checkpoint_after=11, extra=6, block_generations=4
    )
    blocks = recover(tmp_path / "t.tlog", truncate=False).blocks
    resumed_block = next(b for b in blocks if b.first_generation == 11)
    assert resumed_block.is_key


def test_resuming_without_a_position_keeps_the_committed_prefix_and_drops_a_torn_tail(
    tmp_path: Path,
) -> None:
    """After a crash with no checkpoint: everything intact is kept."""
    frames = walk(50, change=0.1, seed=44)
    path = tmp_path / "t.tlog"
    _write(path, frames[:30])
    committed = path.stat().st_size
    with path.open("ab") as handle:
        handle.write(b"FTB1" + b"\x07" * 90)  # a torn, uncommittable block
    writer = LogWriter(path, RUN_ID, LAYOUT, resume=True, **OPTIONS)
    assert writer.file_position == committed
    assert writer.committed_generation == 29
    for frame in frames[30:]:
        writer.submit(frame)
    writer.close()
    same_frames(list(iter_frames(path)), frames)


def test_a_thinned_stream_resumes_with_its_gaps(tmp_path: Path) -> None:
    """Generation numbers need only increase; the gaps survive a resume."""
    frames = walk(40, change=0.1, seed=45, step=9)
    _interrupted_then_resumed(tmp_path / "t.tlog", frames, checkpoint_after=16, extra=5)
    got = list(iter_frames(tmp_path / "t.tlog"))
    assert [f.generation for f in got] == [9 * i for i in range(40)]
    same_frames(got, frames)


def test_a_resumed_writer_refuses_a_generation_that_does_not_follow(
    tmp_path: Path,
) -> None:
    """Resuming at generation 15 means the next frame must be later than 15."""
    frames = walk(20, change=0.1, seed=46)
    path = tmp_path / "t.tlog"
    _write(path, frames[:16])
    writer = LogWriter(path, RUN_ID, LAYOUT, resume=True, **OPTIONS)
    with pytest.raises(ValueError, match="does not follow"):
        writer.submit(frames[15])
    writer.submit(frames[16])
    writer.close()


def test_a_position_that_does_not_match_the_file_is_refused(tmp_path: Path) -> None:
    """Checksum, offset, generation, a shorter file, another log: all refused."""
    frames = walk(40, change=0.1, seed=47)
    path = tmp_path / "t.tlog"
    writer = LogWriter(path, RUN_ID, LAYOUT, **OPTIONS)
    for frame in frames[:24]:
        writer.submit(frame)
    position = writer.checkpoint()
    for frame in frames[24:]:
        writer.submit(frame)
    writer.close()
    full = path.read_bytes()

    def attempt(candidate: LogPosition) -> None:
        """Try to resume from `candidate`."""
        LogWriter(path, RUN_ID, LAYOUT, resume=candidate, **OPTIONS).close()

    with pytest.raises(ValueError, match="checksum"):
        attempt(replace(position, chain_crc=position.chain_crc ^ 1))
    with pytest.raises(ValueError, match="checksum"):
        attempt(replace(position, generation=position.generation - 1))
    with pytest.raises(ValueError, match="no committed block ends"):
        attempt(replace(position, offset=position.offset + 1))
    path.write_bytes(full[: position.offset - 5])
    with pytest.raises(ValueError, match="shorter than the checkpoint"):
        attempt(position)
    # A different log with the same header cannot satisfy the checkpoint.
    other = walk(40, change=0.9, seed=48)
    path.unlink()
    _write(path, other)
    with pytest.raises(ValueError, match=r"checksum|no committed block"):
        attempt(position)


def test_resume_refuses_another_run_layout_or_mode(tmp_path: Path) -> None:
    """A file that is not this run's log is never continued."""
    frames = walk(12, change=0.1, seed=49)
    path = tmp_path / "t.tlog"
    _write(path, frames)
    with pytest.raises(TlogError, match="another run"):
        LogWriter(path, "other-run", LAYOUT, resume=True, **OPTIONS).close()
    with pytest.raises(TlogError, match="another run or layout"):
        LogWriter(
            path,
            RUN_ID,
            FrameLayout(locus_ids=(1,), deme_sizes=(10,)),
            resume=True,
            **OPTIONS,
        ).close()
    with pytest.raises(TlogError, match="another mode"):
        LogWriter(
            path, RUN_ID, LAYOUT, resume=True, **{**OPTIONS, "mode": "dense"}
        ).close()
    with pytest.raises(FileNotFoundError):
        LogWriter(tmp_path / "none.tlog", RUN_ID, LAYOUT, resume=True)
    (tmp_path / "empty.tlog").write_bytes(b"")
    with pytest.raises(TlogError, match="empty"):
        LogWriter(tmp_path / "empty.tlog", RUN_ID, LAYOUT, resume=True)


def test_a_damaged_header_is_restored_from_the_checkpoints_copy(
    tmp_path: Path,
) -> None:
    """The header is copied into every checkpoint so a bad one is not fatal."""
    frames = walk(40, change=0.1, seed=50)
    path = tmp_path / "t.tlog"
    writer = LogWriter(path, RUN_ID, LAYOUT, **OPTIONS)
    for frame in frames[:24]:
        writer.submit(frame)
    position = writer.checkpoint()
    writer.close()
    damaged = bytearray(path.read_bytes())
    damaged[12] ^= 0xFF
    path.write_bytes(bytes(damaged))
    with pytest.raises(TlogError):
        recover(path, truncate=False)
    with pytest.raises(TlogError):
        LogWriter(path, RUN_ID, LAYOUT, resume=True, **OPTIONS).close()
    resumed = LogWriter(path, RUN_ID, LAYOUT, resume=position, **OPTIONS)
    for frame in frames[24:]:
        resumed.submit(frame)
    resumed.close()
    same_frames(list(iter_frames(path)), frames)


def test_a_checkpoint_position_is_a_plain_record_of_the_committed_end(
    tmp_path: Path,
) -> None:
    """The position names the committed end and matches a fresh scan of it."""
    frames = walk(30, change=0.1, seed=51)
    writer = LogWriter(tmp_path / "t.tlog", RUN_ID, LAYOUT, background=True, **OPTIONS)
    for frame in frames[:19]:
        writer.submit(frame)
    position = writer.checkpoint()
    scan = recover(tmp_path / "t.tlog", truncate=False)
    assert position.generation == scan.last_generation == 18
    assert position.offset == scan.valid_end
    assert position.chain_crc == scan.chain_crc
    assert (position.generations, position.rows) == (scan.generations, scan.rows)
    assert position.header == (tmp_path / "t.tlog").read_bytes()[: len(position.header)]
    assert writer.position() == position
    writer.close()


def test_the_store_checkpoints_and_resumes(tmp_path: Path) -> None:
    """The same, through `BinaryLogStore`, reading rows back across the resume."""
    frames = walk(60, change=0.1, seed=52)
    path = tmp_path / "t.tlog"
    store = BinaryLogStore(path, mode="sparse", key_every=16, block_generations=8)
    store.begin_run(RUN_ID, LAYOUT)
    with pytest.raises(ValueError, match="nothing has been written"):
        store.checkpoint()
    for frame in frames[:32]:
        store.write_frame(RUN_ID, frame)
    position = store.checkpoint()
    for frame in frames[32:45]:
        store.write_frame(RUN_ID, frame)
    store.close()

    resumed = BinaryLogStore(path, mode="sparse", key_every=16, block_generations=8)
    returned = resumed.resume(RUN_ID, LAYOUT, position)
    assert returned.generation == position.generation == 31
    for frame in frames[32:]:
        resumed.write_frame(RUN_ID, frame)
    with pytest.raises(ValueError, match="before any write"):
        resumed.resume(RUN_ID, LAYOUT)
    resumed.close()
    rows = list(BinaryLogStore(path).read(RUN_ID))
    assert rows == [
        row for frame in frames for row in frame_to_rows(frame, LAYOUT, RUN_ID)
    ]


KILL_PROGRAM = """
import json, os, sys
import numpy as np
from fim.persistence.frame import FrameLayout, TrajectoryFrame
from fim.persistence.tlog import LogWriter

data = np.load(sys.argv[1])
layout = FrameLayout(locus_ids=(3, 1, 8, 9), deme_sizes=(100, 64, 25))
writer = LogWriter(sys.argv[2], 'run-resume', layout, mode='sparse', key_every=16,
                   block_generations=8, background=True, sync='fsync')
offsets = np.concatenate(([0], np.cumsum(data['entries'])))
for g in range(len(data['entries'])):
    a, b = int(offsets[g]), int(offsets[g + 1])
    writer.submit(TrajectoryFrame(
        g, data['counts'][g], data['ids'][a:b], data['freqs'][a:b]))
    if g == 31:
        p = writer.checkpoint()
        with open(sys.argv[3], 'w') as fh:
            json.dump({'generation': p.generation, 'offset': p.offset,
                       'chain_crc': p.chain_crc, 'generations': p.generations,
                       'rows': p.rows, 'header': p.header.hex()}, fh)
    if g == 52:
        writer.flush()
os._exit(0)
"""


def test_a_process_killed_after_a_checkpoint_resumes_to_the_uninterrupted_log(
    tmp_path: Path,
) -> None:
    """A real abrupt process exit, then resume: the log equals the whole run.

    The child checkpoints at generation 31 (a block and keyframe boundary),
    commits more blocks, and exits without closing anything. The parent
    resumes from the checkpoint, writes the rest, and compares with a log
    written without interruption: byte for byte.
    """
    frames = walk(80, change=0.1, seed=53)
    np.savez(
        tmp_path / "frames.npz",
        counts=np.stack([f.counts for f in frames]),
        ids=np.concatenate([f.allele_ids for f in frames]),
        freqs=np.concatenate([f.frequencies for f in frames]),
        entries=np.array([f.rows for f in frames]),
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            KILL_PROGRAM,
            str(tmp_path / "frames.npz"),
            str(tmp_path / "killed.tlog"),
            str(tmp_path / "position.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=BACKSTOP_SECONDS,
    )
    assert result.returncode == 0, result.stderr
    killed = recover(tmp_path / "killed.tlog", truncate=False)
    assert killed.last_generation >= 52  # more than the checkpoint was committed
    raw = json.loads((tmp_path / "position.json").read_text())
    position = LogPosition(**{**raw, "header": bytes.fromhex(raw["header"])})
    assert position.generation == 31
    writer = LogWriter(
        tmp_path / "killed.tlog",
        RUN_ID,
        LAYOUT,
        resume=position,
        mode="sparse",
        key_every=16,
        block_generations=8,
    )
    for frame in frames[32:]:
        writer.submit(frame)
    writer.close()
    _write(tmp_path / "whole.tlog", frames)
    assert (tmp_path / "killed.tlog").read_bytes() == (
        tmp_path / "whole.tlog"
    ).read_bytes()
    assert hashlib.sha256((tmp_path / "killed.tlog").read_bytes()).hexdigest() == (
        hashlib.sha256((tmp_path / "whole.tlog").read_bytes()).hexdigest()
    )
