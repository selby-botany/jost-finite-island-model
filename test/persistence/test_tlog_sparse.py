"""Tests of the log's sparse mode: keyframes and deltas.

In sparse mode a full keyframe opens a block every `key_every`
generations, and the generations between are stored as the (deme, locus)
pairs that changed. These tests check, over long random walks with quiet
and busy stretches, that the sparse log decodes to exactly the frames that
were written, that its blocks and keyframes sit where the rules say, that it
is much smaller than the dense log on a quiet run, and that export, sharding
and recovery all work on it. Seeded; nothing depends on timing.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from fim.persistence.frame import FrameLayout, TrajectoryFrame
from fim.persistence.tlog import (
    FLAG_SPARSE,
    LogWriter,
    iter_frames,
    recover,
    scan_blocks,
)
from fim.persistence.tlog_export import derive_jsonl, plan_shards

RUN_ID = "run-sparse"
LAYOUT = FrameLayout(locus_ids=(3, 1, 8, 9), deme_sizes=(100, 64, 25))
PAIRS = LAYOUT.pairs


def _fresh_pair(
    rng: np.random.Generator, size: int, wide: bool
) -> tuple[list[int], list[float]]:
    """Alleles (ids, frequencies) of one pair; `wide` allows many alleles."""
    n = int(rng.integers(1, 30 if wide else 4))
    n = min(n, size)
    cuts: np.ndarray = (
        np.sort(rng.choice(np.arange(1, size), n - 1, replace=False))
        if n > 1
        else np.zeros(0, dtype=np.int64)
    )
    counts = np.diff(np.concatenate(([0], cuts, [size])))
    ids = np.sort(rng.choice(2**34, n, replace=False)).tolist()
    return ids, [c / float(size) for c in counts.tolist()]


def _walk(
    count: int, *, change: float, seed: int, step: int = 1, wide: bool = False
) -> list[TrajectoryFrame]:
    """Frames where each generation changes about `change` of the pairs."""
    rng = np.random.default_rng(seed)
    sizes = [size for size in LAYOUT.deme_sizes for _ in LAYOUT.locus_ids]
    pairs = [_fresh_pair(rng, size, wide) for size in sizes]
    frames = []
    for index in range(count):
        for p in range(PAIRS):
            if index == 0 or rng.random() < change:
                pairs[p] = _fresh_pair(rng, sizes[p], wide)
        frames.append(
            TrajectoryFrame(
                generation=index * step,
                counts=np.array([len(ids) for ids, _ in pairs], dtype=np.int32),
                allele_ids=np.array(
                    [i for ids, _ in pairs for i in ids], dtype=np.int64
                ),
                frequencies=np.array([f for _, freq in pairs for f in freq]),
            )
        )
    return frames


def _write(path: Path, frames: list[TrajectoryFrame], **options: object) -> LogWriter:
    """Write `frames` through a sparse writer and close it."""
    options.setdefault("mode", "sparse")
    writer = LogWriter(path, RUN_ID, LAYOUT, **options)  # type: ignore[arg-type]
    for frame in frames:
        writer.submit(frame)
    writer.close()
    return writer


def _same(got: list[TrajectoryFrame], want: list[TrajectoryFrame]) -> None:
    """Require two frame lists to be equal, with exact frequency bits."""
    assert [f.generation for f in got] == [f.generation for f in want]
    for left, right in zip(got, want, strict=True):
        assert np.array_equal(left.counts, right.counts)
        assert np.array_equal(left.allele_ids, right.allele_ids)
        assert left.frequencies.tobytes() == right.frequencies.tobytes()


@pytest.mark.parametrize("change", [0.0, 0.03, 0.5, 1.0])
def test_a_sparse_log_decodes_to_exactly_the_frames_written(
    tmp_path: Path, change: float
) -> None:
    """From a frozen run to one that changes everything every generation."""
    frames = _walk(120, change=change, seed=int(change * 100) + 1)
    path = tmp_path / "t.tlog"
    _write(path, frames, key_every=16, block_generations=64)
    _same(list(iter_frames(path)), frames)


def test_pairs_that_gain_many_alleles_widen_the_encoder_state(tmp_path: Path) -> None:
    """More alleles than the initial width in a changed pair still round-trips."""
    frames = _walk(60, change=0.4, seed=3, wide=True)
    assert max(int(f.counts.max()) for f in frames) > 16
    path = tmp_path / "t.tlog"
    _write(path, frames, key_every=8)
    _same(list(iter_frames(path)), frames)


def test_the_header_says_sparse_and_keyframes_open_blocks(tmp_path: Path) -> None:
    """Every `key_every`-th generation opens a block with a full record."""
    frames = _walk(40, change=0.05, seed=5)
    path = tmp_path / "t.tlog"
    _write(path, frames, key_every=10, block_generations=1000)
    scan = recover(path, truncate=False)
    assert scan.header.flags & FLAG_SPARSE
    assert scan.header.key_every == 10
    assert [b.first_generation for b in scan.blocks] == [0, 10, 20, 30]
    assert all(b.is_key for b in scan.blocks)
    assert [b.n_records for b in scan.blocks] == [10, 10, 10, 10]


def test_blocks_sealed_between_keyframes_start_with_a_delta(tmp_path: Path) -> None:
    """A block cut by its generation count mid-interval is not a keyframe block."""
    frames = _walk(30, change=0.05, seed=6)
    path = tmp_path / "t.tlog"
    _write(path, frames, key_every=12, block_generations=5)
    scan = recover(path, truncate=False)
    keys = [b.first_generation for b in scan.blocks if b.is_key]
    assert keys == [0, 12, 24]
    assert not all(b.is_key for b in scan.blocks)
    _same(list(iter_frames(path)), frames)


def test_a_quiet_sparse_run_is_far_smaller_than_the_dense_log(tmp_path: Path) -> None:
    """The point of the mode: most generations change almost nothing."""
    frames = _walk(300, change=0.01, seed=7)
    _write(tmp_path / "sparse.tlog", frames, key_every=64)
    _write(tmp_path / "dense.tlog", frames, mode="dense")
    sparse = (tmp_path / "sparse.tlog").stat().st_size
    dense = (tmp_path / "dense.tlog").stat().st_size
    assert sparse * 4 < dense


def test_thinned_generations_keep_their_numbers_in_sparse_mode(tmp_path: Path) -> None:
    """Generation gaps survive the deltas and the keyframe blocks."""
    frames = _walk(50, change=0.1, seed=8, step=13)
    path = tmp_path / "t.tlog"
    _write(path, frames, key_every=7, block_generations=20)
    _same(list(iter_frames(path)), frames)
    assert [f.generation for f in iter_frames(path)] == [13 * i for i in range(50)]


def test_export_of_a_sparse_log_equals_export_of_the_dense_log(tmp_path: Path) -> None:
    """The canonical JSONL does not depend on how the log was stored."""
    frames = _walk(80, change=0.1, seed=9)
    _write(tmp_path / "sparse.tlog", frames, key_every=9, block_generations=1000)
    _write(tmp_path / "dense.tlog", frames, mode="dense")
    sparse = derive_jsonl(tmp_path / "sparse.tlog", tmp_path / "sparse.jsonl")
    dense = derive_jsonl(tmp_path / "dense.tlog", tmp_path / "dense.jsonl")
    assert (tmp_path / "sparse.jsonl").read_bytes() == (
        tmp_path / "dense.jsonl"
    ).read_bytes()
    assert sparse.sha256 == dense.sha256


def test_sharded_export_of_a_sparse_log_starts_shards_at_keyframes(
    tmp_path: Path,
) -> None:
    """Shards begin at keyframe blocks only, and sharded equals single-process."""
    frames = _walk(100, change=0.1, seed=10)
    path = tmp_path / "t.tlog"
    _write(path, frames, key_every=10, block_generations=4)
    scan = recover(path, truncate=False)
    shards = plan_shards(scan, 2)
    assert all(scan.blocks[first].is_key for first, _stop in shards)
    assert len(shards) > 1
    single = derive_jsonl(path, tmp_path / "single.jsonl")
    sharded = derive_jsonl(path, tmp_path / "sharded.jsonl", workers=2, shard_blocks=2)
    assert (tmp_path / "single.jsonl").read_bytes() == (
        tmp_path / "sharded.jsonl"
    ).read_bytes()
    assert sharded.sha256 == single.sha256


@pytest.mark.parametrize("block", [0, 1, 3, 5])
def test_a_flipped_bit_in_a_sparse_log_ends_the_prefix_at_that_block(
    tmp_path: Path, block: int
) -> None:
    """Recovery works block by block; deltas never reach across a bad block."""
    frames = _walk(60, change=0.1, seed=11)
    path = tmp_path / "t.tlog"
    _write(path, frames, key_every=12, block_generations=8)
    data = bytearray(path.read_bytes())
    scan = scan_blocks(bytes(data))
    assert len(scan.blocks) > block
    info = scan.blocks[block]
    data[info.offset + 32 + 6] ^= 0x04
    path.write_bytes(bytes(data))
    result = recover(path)
    assert len(result.blocks) == block
    survivors = sum(b.n_records for b in result.blocks)
    _same(list(iter_frames(path)), frames[:survivors])


def test_writer_option_validation(tmp_path: Path) -> None:
    """A bad mode or key interval is refused before the file exists."""
    with pytest.raises(ValueError, match="mode"):
        LogWriter(tmp_path / "a.tlog", RUN_ID, LAYOUT, mode="lumpy")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="key_every"):
        LogWriter(tmp_path / "b.tlog", RUN_ID, LAYOUT, key_every=0)
    assert not list(tmp_path.glob("*.tlog"))


def test_the_background_sparse_writer_writes_the_inline_writers_bytes(
    tmp_path: Path,
) -> None:
    """Thread or not, a sparse log is the same file."""
    frames = _walk(90, change=0.08, seed=12)
    _write(tmp_path / "inline.tlog", frames, key_every=11, block_generations=6)
    _write(
        tmp_path / "thread.tlog",
        frames,
        key_every=11,
        block_generations=6,
        background=True,
        queue_depth=1,
    )
    assert (tmp_path / "inline.tlog").read_bytes() == (
        tmp_path / "thread.tlog"
    ).read_bytes()
