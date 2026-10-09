"""Tests of the log writer's thread, back-pressure, sync policy and faults.

The writer's promises are about *what reaches the file and when*, and none
of them may depend on timing, so every test drives the writer through an
injected clock, an injected sync function or a fault hook, and waits on
barriers (`flush`) rather than on time. The central check is that the
background writer produces exactly the bytes of the inline one.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from fim.persistence import tlog
from fim.persistence.frame import FrameLayout, TrajectoryFrame
from fim.persistence.tlog import (
    InjectedFaultError,
    LogWriter,
    SyncMode,
    iter_frames,
    recover,
    sync_file,
)

RUN_ID = "run-writer"
LAYOUT = FrameLayout(locus_ids=(2, 5), deme_sizes=(100, 50, 20))
BACKSTOP_SECONDS = 120.0


def _frames(count: int, seed: int = 11) -> list[TrajectoryFrame]:
    """`count` random frames of `LAYOUT`, generations 0..count-1."""
    rng = np.random.default_rng(seed)
    frames = []
    for generation in range(count):
        nal: list[int] = []
        ids: list[int] = []
        fr: list[float] = []
        for size in LAYOUT.deme_sizes:
            for _locus in LAYOUT.locus_ids:
                n = int(rng.integers(1, 4))
                cuts = np.sort(rng.choice(np.arange(1, size), n - 1, replace=False))
                counts = np.diff(np.concatenate(([0], cuts, [size])))
                nal.append(n)
                ids.extend(np.sort(rng.choice(5000, n, replace=False)).tolist())
                fr.extend((counts / float(size)).tolist())
        frames.append(
            TrajectoryFrame(
                generation=generation,
                counts=np.asarray(nal, dtype=np.int32),
                allele_ids=np.asarray(ids, dtype=np.int64),
                frequencies=np.asarray(fr, dtype=np.float64),
            )
        )
    return frames


def _same(got: list[TrajectoryFrame], want: list[TrajectoryFrame]) -> None:
    """Require two frame lists to be equal, with exact frequency bits."""
    assert [f.generation for f in got] == [f.generation for f in want]
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


def _write_with(path: Path, frames: list[TrajectoryFrame], **options: object) -> None:
    """Write `frames` through a writer built with `options`, then close it."""
    writer = LogWriter(path, RUN_ID, LAYOUT, **options)  # type: ignore[arg-type]
    for frame in frames:
        writer.submit(frame)
    writer.close()


@pytest.mark.parametrize(
    "options",
    [
        {"block_generations": 4, "queue_depth": 4},
        {"block_generations": 1, "queue_depth": 1},
        {"block_generations": 7, "queue_depth": 2, "buffer_bytes": 4096},
    ],
)
def test_the_background_writer_writes_the_bytes_of_the_inline_writer(
    tmp_path: Path, options: dict[str, int]
) -> None:
    """Thread or no thread, pool of one or many: the file is the same."""
    frames = _frames(40)
    _write_with(tmp_path / "inline.tlog", frames, **options)
    _write_with(tmp_path / "thread.tlog", frames, background=True, **options)
    assert (tmp_path / "inline.tlog").read_bytes() == (
        tmp_path / "thread.tlog"
    ).read_bytes()
    _same(list(iter_frames(tmp_path / "thread.tlog")), frames)


def test_back_pressure_with_every_buffer_in_use_still_writes_every_frame(
    tmp_path: Path,
) -> None:
    """Gate the thread until the producer has used every buffer, then release.

    One generation per block and a queue of one means the producer needs a
    fourth buffer while the thread is stuck on the first write: it must wait
    for the pool rather than allocate without bound, and nothing is lost.
    """
    frames = _frames(30)
    holder: dict[str, LogWriter] = {}
    release = threading.Event()
    gated = threading.Event()

    def hook(point: str, size: int = 0) -> int | None:
        """Hold the first write until the pool is exhausted, then let it go."""
        del size
        if point == "before_write" and not gated.is_set():
            gated.set()
            writer = holder["writer"]
            deadline = time.monotonic() + BACKSTOP_SECONDS
            while not (
                writer._buffers_made == writer._buffer_limit
                and writer._free is not None
                and writer._free.empty()
            ):
                assert time.monotonic() < deadline, (
                    "the producer never exhausted the pool"
                )
                assert release.wait(0.001) is False
            assert writer._buffers_made == writer._buffer_limit
        return None

    writer = LogWriter(
        tmp_path / "t.tlog",
        RUN_ID,
        LAYOUT,
        block_generations=1,
        queue_depth=1,
        background=True,
        fault=hook,
    )
    holder["writer"] = writer
    for frame in frames:
        writer.submit(frame)
    writer.close()
    assert writer._buffers_made == writer._buffer_limit == 3
    _same(list(iter_frames(tmp_path / "t.tlog")), frames)


def test_flush_is_a_barrier_after_which_everything_is_committed(
    tmp_path: Path,
) -> None:
    """After `flush` the file holds every submitted generation, readable now."""
    frames = _frames(25)
    writer = LogWriter(
        tmp_path / "t.tlog", RUN_ID, LAYOUT, block_generations=1000, background=True
    )
    for frame in frames[:10]:
        writer.submit(frame)
    assert writer.committed_generation == -1  # nothing sealed yet
    writer.flush()
    generation, position, crc = writer.snapshot()
    assert generation == 9
    scan = recover(tmp_path / "t.tlog", truncate=False)
    assert (scan.last_generation, scan.valid_end, scan.chain_crc) == (
        generation,
        position,
        crc,
    )
    _same(list(iter_frames(tmp_path / "t.tlog")), frames[:10])
    for frame in frames[10:]:
        writer.submit(frame)
    writer.close()
    _same(list(iter_frames(tmp_path / "t.tlog")), frames)


def test_an_error_in_the_writer_thread_surfaces_on_the_next_submit(
    tmp_path: Path,
) -> None:
    """A failed write is raised to the producer, not swallowed in the thread."""
    calls = 0

    def hook(point: str, size: int = 0) -> int | None:
        """Fail the second write."""
        del size
        nonlocal calls
        if point == "before_write":
            calls += 1
            if calls == 3:  # the header is not hooked; this is the third block
                raise InjectedFaultError("disk full")
        return None

    writer = LogWriter(
        tmp_path / "t.tlog",
        RUN_ID,
        LAYOUT,
        block_generations=1,
        background=True,
        fault=hook,
    )
    frames = _frames(50)
    with pytest.raises(InjectedFaultError, match="disk full"):
        for frame in frames:
            writer.submit(frame)
        writer.flush()
    with pytest.raises(InjectedFaultError):
        writer.close()
    writer.close()
    # The first block was committed before the failure.
    assert recover(tmp_path / "t.tlog").last_generation >= 0


@pytest.mark.parametrize("keep", [0, 1, 7, 100])
def test_a_write_cut_short_leaves_a_torn_block_that_recovery_removes(
    tmp_path: Path, keep: int
) -> None:
    """A crash in the middle of `write(2)`: whole earlier blocks survive."""
    frames = _frames(12)
    seen = {"blocks": 0}

    def hook(point: str, size: int = 0) -> int | None:
        """Cut the third block short after `keep` bytes."""
        if point == "before_write":
            seen["blocks"] += 1
            if seen["blocks"] == 3:  # the header is not hooked; this is block 3
                return min(keep, size - 1)
        return None

    writer = LogWriter(
        tmp_path / "t.tlog", RUN_ID, LAYOUT, block_generations=3, fault=hook
    )
    with pytest.raises(InjectedFaultError, match="cut short"):
        for frame in frames:
            writer.submit(frame)
        writer.close()
    writer.close()
    result = recover(tmp_path / "t.tlog")
    assert len(result.blocks) == 2
    _same(list(iter_frames(tmp_path / "t.tlog")), frames[:6])


def test_sync_runs_when_its_period_has_passed_and_at_flush_and_close(
    tmp_path: Path,
) -> None:
    """The exact order of writes and syncs under an injected clock."""
    clock = _FakeClock()
    events: list[str] = []

    def hook(point: str, size: int = 0) -> int | None:
        """Record each block write."""
        del size
        if point == "after_write":
            events.append("write")
        return None

    def fake_sync(fd: int, mode: SyncMode) -> None:
        """Record each durability call and the mode it was asked for."""
        del fd
        events.append(f"sync:{mode}")

    writer = LogWriter(
        tmp_path / "t.tlog",
        RUN_ID,
        LAYOUT,
        block_generations=1,
        clock=clock,
        sync="full",
        sync_seconds=2.0,
        sync_function=fake_sync,
        fault=hook,
    )
    frames = _frames(8)
    for frame in frames[:6]:
        writer.submit(frame)
        clock.now += 1.0
    # One generation per block, and a block is written as soon as it is full,
    # so generation i is written when the clock reads i. The sync period is
    # 2 s, so the first sync follows the write at clock 2.0, the next the
    # write at clock 4.0 (4.0 - 2.0 >= 2.0).
    assert events == [
        "write",  # generation 0, clock 0.0
        "write",  # generation 1, clock 1.0: 1.0 - 0.0 < 2.0
        "write",  # generation 2, clock 2.0: 2.0 - 0.0 >= 2.0
        "sync:full",
        "write",  # generation 3, clock 3.0
        "write",  # generation 4, clock 4.0: 4.0 - 2.0 >= 2.0
        "sync:full",
        "write",  # generation 5, clock 5.0
    ]
    # A flush that asks for durability syncs what is not yet durable.
    writer.flush(sync=True)
    assert events[-1] == "sync:full"
    assert writer.synced_generation == 5
    writer.submit(frames[6])
    assert events[-1] == "write"
    writer.close()
    assert events[-2:] == ["write", "sync:full"]
    assert writer.stats.syncs == 4


def test_sync_none_never_syncs(tmp_path: Path) -> None:
    """The default policy makes no durability call at all."""
    calls: list[str] = []
    writer = LogWriter(
        tmp_path / "t.tlog",
        RUN_ID,
        LAYOUT,
        block_generations=1,
        sync="none",
        sync_function=lambda _fd, _mode: calls.append("sync"),
    )
    for frame in _frames(5):
        writer.submit(frame)
    writer.flush(sync=True)
    writer.close()
    assert calls == []
    assert writer.stats.syncs == 0


def test_a_background_writer_syncs_on_its_own_thread(tmp_path: Path) -> None:
    """The group commit happens off the producing thread."""
    threads: list[str] = []
    writer = LogWriter(
        tmp_path / "t.tlog",
        RUN_ID,
        LAYOUT,
        block_generations=2,
        background=True,
        sync="fsync",
        sync_function=lambda _fd, _mode: threads.append(
            threading.current_thread().name
        ),
    )
    for frame in _frames(6):
        writer.submit(frame)
    writer.flush(sync=True)
    assert threads
    assert set(threads) == {"fim-tlog-writer"}
    writer.close()
    assert threading.current_thread().name not in threads


def test_a_failed_sync_surfaces_like_a_failed_write(tmp_path: Path) -> None:
    """An error at a named sync point stops the writer and is raised."""

    def hook(point: str, size: int = 0) -> int | None:
        """Fail before the first sync."""
        del size
        if point == "before_sync":
            raise InjectedFaultError("sync failed")
        return None

    writer = LogWriter(
        tmp_path / "t.tlog",
        RUN_ID,
        LAYOUT,
        background=True,
        sync="fsync",
        sync_function=lambda _fd, _mode: None,
        fault=hook,
    )
    writer.submit(_frames(1)[0])
    with pytest.raises(InjectedFaultError, match="sync failed"):
        writer.flush(sync=True)
    with pytest.raises(InjectedFaultError):
        writer.close()


def test_sync_file_modes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`fsync`, `full` and `auto` call what they say; `full` needs the platform."""
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(os, "fsync", lambda fd: calls.append(("fsync", fd)))
    fd = os.open(tmp_path / "x", os.O_WRONLY | os.O_CREAT)
    try:
        sync_file(fd, "none")
        assert calls == []
        sync_file(fd, "fsync")
        assert calls == [("fsync", fd)]
        monkeypatch.setattr(tlog, "fcntl", None)
        sync_file(fd, "auto")
        assert calls[-1] == ("fsync", fd)
        with pytest.raises(OSError, match="F_FULLFSYNC"):
            sync_file(fd, "full")

        class FakeFcntl:
            """A platform that has `F_FULLFSYNC`."""

            F_FULLFSYNC = 51

            @staticmethod
            def fcntl(descriptor: int, command: int) -> int:
                """Record the call."""
                calls.append(("fcntl", command))
                return descriptor

        monkeypatch.setattr(tlog, "fcntl", FakeFcntl)
        sync_file(fd, "full")
        sync_file(fd, "auto")
        assert calls[-2:] == [("fcntl", 51), ("fcntl", 51)]
    finally:
        os.close(fd)


@pytest.mark.parametrize(
    "options",
    [
        {"queue_depth": 0},
        {"sync_seconds": 0.0},
        {"sync": "sometimes"},
    ],
)
def test_writer_options_are_validated(
    tmp_path: Path, options: dict[str, object]
) -> None:
    """Nonsense options are refused before the file exists."""
    with pytest.raises(ValueError, match=r"queue_depth|sync"):
        LogWriter(tmp_path / "t.tlog", RUN_ID, LAYOUT, **options)  # type: ignore[arg-type]
    assert not (tmp_path / "t.tlog").exists()


def test_a_process_that_dies_loses_only_what_was_never_written(
    tmp_path: Path,
) -> None:
    """Kill the writer process after a flush: everything flushed survives.

    The child flushes after generation 59 (a barrier), writes 40 more
    generations and then exits without closing anything (`os._exit`), which
    stops it as abruptly as a crash does. The parent recovers the file: it
    must hold every flushed generation and nothing but a prefix of the rest.
    """
    frames = _frames(100)
    np.savez(
        tmp_path / "frames.npz",
        counts=np.stack([f.counts for f in frames]),
        ids=np.concatenate([f.allele_ids for f in frames]),
        freqs=np.concatenate([f.frequencies for f in frames]),
        entries=np.array([f.rows for f in frames]),
    )
    program = (
        "import sys, numpy as np\n"
        "from fim.persistence.frame import FrameLayout, TrajectoryFrame\n"
        "from fim.persistence.tlog import LogWriter\n"
        "data = np.load(sys.argv[1])\n"
        "layout = FrameLayout(locus_ids=(2, 5), deme_sizes=(100, 50, 20))\n"
        "writer = LogWriter(sys.argv[2], 'run-writer', layout,\n"
        "                   block_generations=8, background=True)\n"
        "offsets = np.concatenate(([0], np.cumsum(data['entries'])))\n"
        "for g in range(100):\n"
        "    a, b = int(offsets[g]), int(offsets[g + 1])\n"
        "    writer.submit(TrajectoryFrame(g, data['counts'][g],\n"
        "        data['ids'][a:b], data['freqs'][a:b]))\n"
        "    if g == 59:\n"
        "        writer.flush()\n"
        "import os\n"
        "os._exit(0)\n"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            program,
            str(tmp_path / "frames.npz"),
            str(tmp_path / "t.tlog"),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=BACKSTOP_SECONDS,
    )
    assert result.returncode == 0, result.stderr
    scan = recover(tmp_path / "t.tlog")
    assert scan.last_generation >= 59
    recovered = list(iter_frames(tmp_path / "t.tlog"))
    _same(recovered, frames[: len(recovered)])
    assert len(recovered) >= 60


def test_the_byte_path_never_reads_the_wall_clock() -> None:
    """Time decides when to seal or sync, never what a byte says."""
    package = Path(__file__).resolve().parents[2] / "src" / "fim" / "persistence"
    for name in ("tlog.py", "tlog_codec.py"):
        source = (package / name).read_text(encoding="utf-8")
        assert "time.time(" not in source
        assert "datetime" not in source
    assert "F_FULLFSYNC" in (package / "tlog.py").read_text(encoding="utf-8")
