"""Tests of the binary log file: header, blocks, chained checksums, recovery.

Every case writes real frames through `LogWriter` into a temporary file and
checks the bytes that come back, so the writer, the scan, the recovery and
the frame iterator are tested against one another. The corruption cases
follow the design's list (cut, zeroed page, bit flip, swapped blocks,
trailing garbage, zero tail): each must recover to a prefix of whole blocks
whose frames equal the frames that were written. Time-based sealing uses an
injected clock; nothing sleeps and nothing depends on the machine.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from fim.persistence.frame import FrameLayout, TrajectoryFrame
from fim.persistence.tlog import (
    BLOCK_HEADER_SIZE,
    LogWriter,
    TlogError,
    build_header,
    iter_frames,
    parse_header,
    recover,
    scan_blocks,
)

RUN_ID = "run-tlog"
LAYOUT = FrameLayout(locus_ids=(7, 3, 12), deme_sizes=(100, 64, 7))


def _frame(rng: np.random.Generator, generation: int) -> TrajectoryFrame:
    """A random frame of `LAYOUT`: mostly counted pairs, some raw ones."""
    nal: list[int] = []
    ids: list[int] = []
    fr: list[float] = []
    for size in LAYOUT.deme_sizes:
        for _locus in LAYOUT.locus_ids:
            n = int(rng.integers(1, 4))
            n = min(n, size)
            if rng.random() < 0.15:
                values = rng.random(n)
                values /= values.sum()
            else:
                cuts = np.sort(rng.choice(np.arange(1, size), n - 1, replace=False))
                counts = np.diff(np.concatenate(([0], cuts, [size])))
                values = counts / float(size)
            nal.append(n)
            ids.extend(np.sort(rng.choice(2**33, n, replace=False)).tolist())
            fr.extend(values.tolist())
    return TrajectoryFrame(
        generation=generation,
        counts=np.asarray(nal, dtype=np.int32),
        allele_ids=np.asarray(ids, dtype=np.int64),
        frequencies=np.asarray(fr, dtype=np.float64),
    )


def _frames(count: int, *, step: int = 1, seed: int = 4) -> list[TrajectoryFrame]:
    """`count` frames numbered 0, step, 2 * step, ..."""
    rng = np.random.default_rng(seed)
    return [_frame(rng, index * step) for index in range(count)]


def _write(path: Path, frames: list[TrajectoryFrame], **options: object) -> LogWriter:
    """Write `frames` through a writer, close it and return it."""
    writer = LogWriter(path, RUN_ID, LAYOUT, **options)  # type: ignore[arg-type]
    for frame in frames:
        writer.submit(frame)
    writer.close()
    return writer


def _same(got: list[TrajectoryFrame], want: list[TrajectoryFrame]) -> None:
    """Require two frame lists to be equal, with exact frequency bits."""
    assert [frame.generation for frame in got] == [frame.generation for frame in want]
    for left, right in zip(got, want, strict=True):
        assert np.array_equal(left.counts, right.counts)
        assert np.array_equal(left.allele_ids, right.allele_ids)
        assert left.frequencies.tobytes() == right.frequencies.tobytes()


class _FakeClock:
    """A clock the test advances by hand."""

    def __init__(self) -> None:
        """Start at zero."""
        self.now = 0.0

    def __call__(self) -> float:
        """Return the current fake time."""
        return self.now


def test_header_round_trips_run_id_layout_and_flags() -> None:
    """The header carries the run id (non-ASCII too), sizes (0 allowed), loci."""
    layout = FrameLayout(locus_ids=(1, 2**20, 5), deme_sizes=(0, 100, 2**21))
    data = build_header("run-é-☃", layout, 256, flags=1)
    header = parse_header(data)
    assert header.run_id == "run-é-☃"
    assert header.layout == layout
    assert (header.version, header.flags, header.key_every) == (1, 1, 256)
    assert header.header_len == len(data)


def test_every_truncation_of_the_header_is_refused() -> None:
    """A header cut at any byte (or flipped at any byte) never parses."""
    data = build_header(RUN_ID, LAYOUT, 256)
    for cut in range(len(data)):
        with pytest.raises(TlogError):
            parse_header(data[:cut])
    for index in range(len(data)):
        damaged = bytearray(data)
        damaged[index] ^= 0x01
        with pytest.raises(TlogError):
            parse_header(bytes(damaged))


def test_an_unsupported_version_is_refused() -> None:
    """A future version number is a clear error, not a misread."""
    data = bytearray(build_header(RUN_ID, LAYOUT, 256))
    struct.pack_into("<H", data, 8, 99)
    with pytest.raises(TlogError, match=r"version|checksum"):
        parse_header(bytes(data))


def test_blocks_hold_what_was_written(tmp_path: Path) -> None:
    """Block boundaries, counts, rows and generation ranges are all recorded."""
    frames = _frames(10)
    path = tmp_path / "t.tlog"
    writer = _write(path, frames, block_generations=4)
    result = recover(path)
    assert result.reason == "end of file"
    assert [b.n_records for b in result.blocks] == [4, 4, 2]
    assert [(b.first_generation, b.last_generation) for b in result.blocks] == [
        (0, 3),
        (4, 7),
        (8, 9),
    ]
    assert result.rows == sum(frame.rows for frame in frames)
    assert result.generations == 10
    assert result.last_generation == 9
    assert all(block.is_key for block in result.blocks)
    assert result.valid_end == result.file_size == path.stat().st_size
    assert writer.committed_generation == 9
    assert writer.file_position == result.file_size
    assert writer.chain_crc == result.chain_crc
    assert writer.blocks_written == 3
    _same(list(iter_frames(path)), frames)


def test_a_log_with_no_frames_has_a_header_and_no_blocks(tmp_path: Path) -> None:
    """Closing an unused writer leaves a valid, empty log."""
    path = tmp_path / "t.tlog"
    _write(path, [])
    result = recover(path)
    assert result.blocks == []
    assert result.last_generation == -1
    assert list(iter_frames(path)) == []


def test_thinned_generations_keep_their_numbers(tmp_path: Path) -> None:
    """Gaps between recorded generations survive through the record deltas."""
    frames = _frames(9, step=7)
    path = tmp_path / "t.tlog"
    _write(path, frames, block_generations=4)
    assert [frame.generation for frame in iter_frames(path)] == [
        7 * i for i in range(9)
    ]
    _same(list(iter_frames(path)), frames)
    assert recover(path).blocks[1].first_generation == 28


def test_a_block_is_sealed_when_its_time_is_up(tmp_path: Path) -> None:
    """With an injected clock the block boundary is exactly where time says."""
    clock = _FakeClock()
    frames = _frames(6)
    writer = LogWriter(
        tmp_path / "t.tlog",
        RUN_ID,
        LAYOUT,
        block_generations=1000,
        block_seconds=2.0,
        clock=clock,
    )
    for index, frame in enumerate(frames):
        writer.submit(frame)
        # The first block opens at 0.0; generation 2 finds 2.0 s elapsed and
        # seals it, so the blocks are [0, 1], [2, 3] and [4, 5].
        clock.now += 1.0
        if index == 1:
            assert writer.blocks_written == 0
    writer.close()
    result = recover(tmp_path / "t.tlog")
    assert [b.n_records for b in result.blocks] == [2, 2, 2]


def test_a_block_is_sealed_when_the_buffer_is_full(tmp_path: Path) -> None:
    """A small buffer makes many blocks; a frame larger than it still fits."""
    frames = _frames(80)
    path = tmp_path / "t.tlog"
    _write(path, frames, buffer_bytes=4096, block_generations=1000)
    result = recover(path)
    assert len(result.blocks) > 1
    _same(list(iter_frames(path)), frames)
    big = TrajectoryFrame(
        generation=0,
        counts=np.full(LAYOUT.pairs, 40, dtype=np.int32),
        allele_ids=np.arange(40 * LAYOUT.pairs, dtype=np.int64) * 3 + 1,
        frequencies=np.concatenate([np.full(40, 1.0 / 40.0)] * LAYOUT.pairs),
    )
    other = tmp_path / "big.tlog"
    _write(other, [big], buffer_bytes=4096)
    _same(list(iter_frames(other)), [big])


def test_writer_refusals(tmp_path: Path) -> None:
    """Bad options, a second file, wrong layout, repeated generations, closed."""
    path = tmp_path / "t.tlog"
    with pytest.raises(ValueError, match="block_generations"):
        LogWriter(path, RUN_ID, LAYOUT, block_generations=0)
    with pytest.raises(ValueError, match="block_seconds"):
        LogWriter(path, RUN_ID, LAYOUT, block_seconds=0.0)
    with pytest.raises(ValueError, match="buffer_bytes"):
        LogWriter(path, RUN_ID, LAYOUT, buffer_bytes=10)
    writer = LogWriter(path, RUN_ID, LAYOUT)
    with pytest.raises(FileExistsError):
        LogWriter(path, RUN_ID, LAYOUT)
    frames = _frames(3)
    writer.submit(frames[0])
    with pytest.raises(ValueError, match="does not follow"):
        writer.submit(frames[0])
    bad = TrajectoryFrame(
        generation=5,
        counts=np.zeros(2, dtype=np.int32),
        allele_ids=np.zeros(0, dtype=np.int64),
        frequencies=np.zeros(0),
    )
    with pytest.raises(ValueError, match="layout"):
        writer.submit(bad)
    writer.close()
    writer.close()
    with pytest.raises(RuntimeError, match="closed"):
        writer.submit(frames[1])


def test_an_empty_file_is_not_a_log(tmp_path: Path) -> None:
    """Scanning an empty or foreign file raises a clear error."""
    empty = tmp_path / "empty.tlog"
    empty.write_bytes(b"")
    with pytest.raises(TlogError, match="empty"):
        recover(empty)
    with pytest.raises(TlogError, match="empty"):
        list(iter_frames(empty))
    foreign = tmp_path / "foreign.tlog"
    foreign.write_bytes(b"not a log at all, just text")
    with pytest.raises(TlogError, match="magic"):
        recover(foreign)


def _written(tmp_path: Path, count: int = 12) -> tuple[Path, list[TrajectoryFrame]]:
    """A log of `count` frames in blocks of 3, and the frames."""
    frames = _frames(count)
    path = tmp_path / "t.tlog"
    _write(path, frames, block_generations=3)
    return path, frames


def _prefix(frames: list[TrajectoryFrame], blocks: int) -> list[TrajectoryFrame]:
    """The frames of the first `blocks` blocks of 3."""
    return frames[: 3 * blocks]


@pytest.mark.parametrize("cut_back", [1, 2, 4, 5, 40, 200])
def test_a_log_cut_inside_its_last_block_recovers_the_earlier_blocks(
    tmp_path: Path, cut_back: int
) -> None:
    """A torn tail loses only the open block; recovery truncates the file."""
    path, frames = _written(tmp_path)
    full = path.stat().st_size
    with path.open("r+b") as handle:
        handle.truncate(full - cut_back)
    result = recover(path)
    assert len(result.blocks) == 3
    assert "torn" in result.reason or "extends" in result.reason
    assert path.stat().st_size == result.valid_end < full - cut_back + 1
    _same(list(iter_frames(path)), _prefix(frames, 3))


def test_recovery_without_truncation_leaves_the_file_alone(tmp_path: Path) -> None:
    """A reader that does not own the file must not modify it."""
    path, frames = _written(tmp_path)
    with path.open("ab") as handle:
        handle.write(b"\x00" * 100)
    size = path.stat().st_size
    result = recover(path, truncate=False)
    assert path.stat().st_size == size
    assert result.valid_end < size
    assert len(result.blocks) == 4
    _same(list(iter_frames(path)), frames)


@pytest.mark.parametrize("tail", [b"\x00" * 5000, b"garbage", b"FTB1" + b"\x00" * 40])
def test_trailing_zeros_or_garbage_are_cut_off(tmp_path: Path, tail: bytes) -> None:
    """A pre-sized zero tail or random trailing bytes never count as a block."""
    path, frames = _written(tmp_path)
    clean = path.stat().st_size
    with path.open("ab") as handle:
        handle.write(tail)
    result = recover(path)
    assert path.stat().st_size == clean == result.valid_end
    _same(list(iter_frames(path)), frames)


@pytest.mark.parametrize("block", [0, 1, 2, 3])
def test_a_flipped_bit_in_any_block_ends_the_committed_prefix_there(
    tmp_path: Path, block: int
) -> None:
    """A bit flip in a block's payload invalidates that block and all later ones."""
    path, frames = _written(tmp_path)
    info = scan_blocks(path.read_bytes()).blocks[block]
    data = bytearray(path.read_bytes())
    data[info.offset + BLOCK_HEADER_SIZE + 5] ^= 0x10
    path.write_bytes(bytes(data))
    result = recover(path)
    assert len(result.blocks) == block
    assert result.reason == "checksum mismatch"
    _same(list(iter_frames(path)), _prefix(frames, block))


def test_a_zeroed_page_in_the_middle_ends_the_committed_prefix(tmp_path: Path) -> None:
    """A hole (zeroed bytes) in block 1 loses blocks 1 onward, never block 2."""
    path, frames = _written(tmp_path)
    info = scan_blocks(path.read_bytes()).blocks[1]
    data = bytearray(path.read_bytes())
    data[info.offset : info.offset + 64] = b"\x00" * 64
    path.write_bytes(bytes(data))
    result = recover(path)
    assert len(result.blocks) == 1
    _same(list(iter_frames(path)), _prefix(frames, 1))


def test_swapped_blocks_are_not_accepted(tmp_path: Path) -> None:
    """Each checksum covers its predecessor, so reordered blocks break the chain."""
    path, frames = _written(tmp_path)
    blocks = scan_blocks(path.read_bytes()).blocks
    data = path.read_bytes()
    first = data[blocks[1].offset : blocks[1].end]
    second = data[blocks[2].offset : blocks[2].end]
    swapped = data[: blocks[1].offset] + second + first + data[blocks[2].end :]
    path.write_bytes(swapped)
    result = recover(path)
    assert len(result.blocks) == 1
    _same(list(iter_frames(path)), _prefix(frames, 1))


def test_a_missing_block_breaks_the_chain_for_every_later_block(
    tmp_path: Path,
) -> None:
    """A valid-looking block after a hole is rejected (power-failure case)."""
    path, frames = _written(tmp_path)
    blocks = scan_blocks(path.read_bytes()).blocks
    data = path.read_bytes()
    spliced = data[: blocks[1].offset] + data[blocks[2].offset :]
    path.write_bytes(spliced)
    assert len(recover(path).blocks) == 1
    _same(list(iter_frames(path)), _prefix(frames, 1))


def test_recover_checks_the_header_first(tmp_path: Path) -> None:
    """A damaged header makes the whole log unusable rather than misread."""
    path, _frames_written = _written(tmp_path)
    data = bytearray(path.read_bytes())
    data[12] ^= 0x01
    path.write_bytes(bytes(data))
    with pytest.raises(TlogError):
        recover(path)
