"""Tests of `LogReader`: random access, listing, following a live log.

The reader must return, for any generation, exactly the frame the writer was
given, whatever the log's shape: dense or sparse, blocks cut between
keyframes, thinned generations. The strongest check is exhaustive: for every
generation of several logs, `frame_at` equals the frame written. The rest
cover the generation listing, subsets in any order, absent generations, the
resumable scan that makes following a run cheap, and the file errors.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from tlog_support import LAYOUT, same_frames, walk

from fim.engine import fim
from fim.model.params import SimulationParams
from fim.persistence import tlog, tlog_reader
from fim.persistence.binary_store import BinaryLogStore
from fim.persistence.frame import TrajectoryFrame, frame_to_rows
from fim.persistence.store import InMemoryTrajectoryStore
from fim.persistence.tlog import LogWriter, TlogError
from fim.persistence.tlog_reader import LogReader, forget_cached_scans, read_rows

RUN_ID = "run-reader"


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    """Start every test with no cached scans."""
    forget_cached_scans()


def _write(path: Path, frames: list[TrajectoryFrame], **options: object) -> None:
    """Write `frames` to a log and close it."""
    writer = LogWriter(path, RUN_ID, LAYOUT, **options)  # type: ignore[arg-type]
    for frame in frames:
        writer.submit(frame)
    writer.close()


SHAPES = {
    "dense": {"mode": "dense", "block_generations": 7},
    "sparse, blocks are keyframe intervals": {"mode": "sparse", "key_every": 9},
    "sparse, blocks cut between keyframes": {
        "mode": "sparse",
        "key_every": 13,
        "block_generations": 4,
    },
    "sparse, one huge block per interval": {
        "mode": "sparse",
        "key_every": 40,
        "block_generations": 1000,
    },
}


@pytest.mark.parametrize("shape", list(SHAPES))
@pytest.mark.parametrize("step", [1, 5])
def test_frame_at_equals_the_frame_written_for_every_generation(
    tmp_path: Path, shape: str, step: int
) -> None:
    """Exhaustive: each recorded generation rebuilds exactly, thinned or not."""
    frames = walk(90, change=0.08, seed=21, step=step)
    path = tmp_path / "t.tlog"
    _write(path, frames, **SHAPES[shape])
    with LogReader(path) as reader:
        for frame in frames:
            got = reader.frame_at(frame.generation)
            same_frames([got], [frame])
        assert reader.generation_count == 90
        assert reader.last_generation == frames[-1].generation
        assert reader.run_id == RUN_ID
        assert reader.layout == LAYOUT
        assert reader.rows == sum(frame.rows for frame in frames)


def test_a_subset_comes_back_ascending_whatever_order_it_was_asked_in(
    tmp_path: Path,
) -> None:
    """Targets are sorted and de-duplicated; far-apart ones jump keyframes."""
    frames = walk(120, change=0.05, seed=22)
    path = tmp_path / "t.tlog"
    _write(path, frames, mode="sparse", key_every=16, block_generations=5)
    wanted = [97, 3, 3, 60, 118, 0, 61]
    with LogReader(path) as reader:
        got = list(reader.frames(wanted))
    same_frames(got, [frames[g] for g in sorted(set(wanted))])


def test_all_frames_equal_the_sequential_decode(tmp_path: Path) -> None:
    """`frames()` with no argument walks everything once."""
    frames = walk(60, change=0.2, seed=23, step=3)
    path = tmp_path / "t.tlog"
    _write(path, frames, mode="sparse", key_every=8)
    with LogReader(path) as reader:
        same_frames(list(reader.frames()), frames)


@pytest.mark.parametrize("missing", [-1, 1, 2, 64, 10_000])
def test_an_unrecorded_generation_is_a_key_error(tmp_path: Path, missing: int) -> None:
    """Thinned-away, never reached and negative generations are not found."""
    frames = walk(20, change=0.1, seed=24, step=3)
    path = tmp_path / "t.tlog"
    _write(path, frames, mode="sparse", key_every=6)
    with LogReader(path) as reader:
        with pytest.raises(KeyError):
            reader.frame_at(missing)
        with pytest.raises(KeyError):
            list(reader.frames([0, missing]))


def test_generation_numbers_expand_contiguous_blocks_and_read_thinned_ones(
    tmp_path: Path,
) -> None:
    """The listing is exact for a plain run and for one with gaps."""
    plain = walk(50, change=0.1, seed=25)
    _write(tmp_path / "plain.tlog", plain, mode="sparse", key_every=10)
    with LogReader(tmp_path / "plain.tlog") as reader:
        assert reader.generation_numbers() == list(range(50))
    thinned = walk(50, change=0.1, seed=26, step=11)
    _write(tmp_path / "thin.tlog", thinned, mode="dense", block_generations=6)
    with LogReader(tmp_path / "thin.tlog") as reader:
        assert reader.generation_numbers() == [11 * i for i in range(50)]


def test_rows_at_and_read_rows_give_the_trajectory_rows(tmp_path: Path) -> None:
    """Rows come back in the engine's order with the log's run id."""
    frames = walk(30, change=0.3, seed=27)
    path = tmp_path / "t.tlog"
    _write(path, frames, mode="sparse", key_every=7)
    with LogReader(path) as reader:
        assert reader.rows_at(11) == frame_to_rows(frames[11], LAYOUT, RUN_ID)
    grouped = read_rows(path, RUN_ID, [2, 20])
    assert sorted(grouped) == [2, 20]
    assert grouped[20] == frame_to_rows(frames[20], LAYOUT, RUN_ID)
    assert read_rows(path, RUN_ID).keys() == set(range(30))
    assert read_rows(path, "another-run") == {}


def test_a_real_run_reads_back_by_generation_exactly_like_the_memory_store(
    tmp_path: Path,
) -> None:
    """Engine output, sparse log, random access: identical rows per generation."""
    params = SimulationParams.from_mapping(
        {
            "N": 30,
            "ploidy": "haploid",
            "m": 0.1,
            "mu": 0.02,
            "d": 3,
            "seed": 20261010,
            "loci": [{"locus_id": 4, "length": 200}, {"locus_id": 9, "length": 200}],
            "initial_allele_count": 3,
            "convergence_window": 4,
            "convergence_tolerance": 1e-12,
            "max_generations": 60,
            "n_replicates": 1,
            "replicate_tolerance": None,
        }
    )
    reference = InMemoryTrajectoryStore()
    store = BinaryLogStore(tmp_path / "t.tlog", key_every=16, block_generations=5)
    for sink in (reference, store):
        fim(
            params.gene_copies,
            params.m,
            params.mu,
            params.d,
            params=params,
            store=sink,
            run_id=RUN_ID,
        )
    store.close()
    expected: dict[int, list[object]] = {}
    for row in reference.read(RUN_ID):
        expected.setdefault(row["generation"], []).append(row)
    assert read_rows(tmp_path / "t.tlog", RUN_ID) == expected
    with LogReader(tmp_path / "t.tlog") as reader:
        for generation in (0, 1, 17, 33, 59, 60):
            assert reader.rows_at(generation) == expected[generation]


def test_a_reader_follows_a_log_that_is_still_being_written(tmp_path: Path) -> None:
    """Only committed blocks are visible, and `refresh` picks up new ones."""
    frames = walk(40, change=0.1, seed=28)
    path = tmp_path / "t.tlog"
    writer = LogWriter(path, RUN_ID, LAYOUT, mode="sparse", key_every=8)
    for frame in frames[:10]:
        writer.submit(frame)
    writer.flush()
    reader = LogReader(path)
    assert reader.generation_count == 10
    for frame in frames[10:25]:
        writer.submit(frame)
    assert reader.generation_count == 10  # nothing new until refreshed
    writer.flush()
    assert reader.refresh() == 25
    same_frames([reader.frame_at(24)], [frames[24]])
    with pytest.raises(KeyError):
        reader.frame_at(25)
    for frame in frames[25:]:
        writer.submit(frame)
    writer.close()
    assert reader.refresh() == 40
    same_frames([reader.frame_at(39)], [frames[39]])
    reader.close()


def test_a_refresh_scans_only_the_new_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Following a log resumes from the end of the committed prefix."""
    frames = walk(50, change=0.1, seed=29)
    path = tmp_path / "t.tlog"
    writer = LogWriter(path, RUN_ID, LAYOUT, mode="dense", block_generations=5)
    for frame in frames[:20]:
        writer.submit(frame)
    resumed: list[int] = []
    real_scan = tlog.scan_blocks

    def spy(data: object, header: object = None, *, resume: object = None) -> object:
        """Record how many blocks the scan did not have to redo."""
        resumed.append(len(resume.blocks) if resume is not None else -1)  # type: ignore[attr-defined]
        return real_scan(data, header, resume=resume)  # type: ignore[arg-type]

    monkeypatch.setattr(tlog_reader, "scan_blocks", spy)
    reader = LogReader(path)
    assert reader.generation_count == 20
    for frame in frames[20:35]:
        writer.submit(frame)
    reader.refresh()
    assert resumed == [-1, 4]  # the full first scan, then only the tail
    assert reader.generation_count == 35
    writer.close()
    reader.close()


def test_a_second_reader_resumes_from_the_cached_scan_until_the_file_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cache saves the rescan for an unchanged log, and is ignored for a new one."""
    frames = walk(30, change=0.1, seed=30)
    path = tmp_path / "t.tlog"
    _write(path, frames, mode="dense", block_generations=5)
    resumed: list[int] = []
    real_scan = tlog.scan_blocks

    def spy(data: object, header: object = None, *, resume: object = None) -> object:
        """Record whether a scan started from a cached prefix."""
        resumed.append(len(resume.blocks) if resume is not None else -1)  # type: ignore[attr-defined]
        return real_scan(data, header, resume=resume)  # type: ignore[arg-type]

    monkeypatch.setattr(tlog_reader, "scan_blocks", spy)
    with LogReader(path) as first:
        assert first.generation_count == 30
    with LogReader(path) as second:
        assert second.generation_count == 30
    assert resumed == [-1, 6]
    # Replace the file with a different log: the cache must not be trusted.
    other = walk(12, change=0.4, seed=31)
    path.unlink()
    _write(path, other, mode="dense", block_generations=5)
    with LogReader(path) as third:
        assert third.generation_count == 12
        same_frames([third.frame_at(11)], [other[11]])
    assert resumed[-1] == -1


def test_the_reader_sees_only_committed_blocks(tmp_path: Path) -> None:
    """Trailing garbage or a torn block is never part of the log."""
    frames = walk(24, change=0.1, seed=32)
    path = tmp_path / "t.tlog"
    _write(path, frames, mode="dense", block_generations=6)
    clean = path.stat().st_size
    with path.open("ab") as handle:
        handle.write(b"FTB1" + b"\x00" * 60)
    with LogReader(path) as reader:
        assert reader.generation_count == 24
        assert reader.scan.valid_end == clean
        same_frames(list(reader.frames()), frames)


def test_file_errors(tmp_path: Path) -> None:
    """Missing, empty and foreign files; a closed reader."""
    with pytest.raises(FileNotFoundError):
        LogReader(tmp_path / "nope.tlog")
    (tmp_path / "empty.tlog").write_bytes(b"")
    with pytest.raises(TlogError, match="empty"):
        LogReader(tmp_path / "empty.tlog")
    (tmp_path / "foreign.tlog").write_bytes(b"x" * 200)
    with pytest.raises(TlogError, match="magic"):
        LogReader(tmp_path / "foreign.tlog")
    frames = walk(5, change=0.1, seed=33)
    _write(tmp_path / "t.tlog", frames, mode="dense")
    reader = LogReader(tmp_path / "t.tlog")
    reader.close()
    reader.close()
    with pytest.raises(ValueError, match="closed"):
        reader.frame_at(0)
    assert np.array_equal(frames[0].counts, frames[0].counts)


def test_skipping_missing_generations_never_loses_a_later_one(tmp_path: Path) -> None:
    """A thinned log asked for absent and present generations in one pass.

    Asking for 2 overshoots to 3 while looking; 3 must still be found.
    """
    frames = walk(30, change=0.1, seed=34, step=3)
    path = tmp_path / "t.tlog"
    _write(path, frames, mode="sparse", key_every=7, block_generations=4)
    with LogReader(path) as reader:
        got = list(reader.frames([1, 2, 3, 4, 5, 6, 10_000, 87], skip_missing=True))
    assert [frame.generation for frame in got] == [3, 6, 87]
    same_frames(got, [frames[1], frames[2], frames[29]])
    grouped = read_rows(path, RUN_ID, [1, 2, 3, 4, 90, 87])
    assert sorted(grouped) == [3, 87]
