"""The binary trajectory log: file format, block writer, scanning and recovery.

A log holds one run's trajectory as a header followed by *blocks*. All
integers are little-endian.

```text
file header   "FIMTLOG1" | u16 version | u16 flags | u32 demes | u32 loci
              | u32 key_every | u16 run_id length | run_id (UTF-8)
              | varint deme_sizes[demes] | varint locus_ids[loci] | u32 crc32
block         "FTB1" | u32 payload_len | u64 first_generation
              | u64 last_generation | u32 n_records | u32 rows
              | payload (n_records records, `fim.persistence.tlog_codec`)
              | u32 chain_crc
```

`chain_crc` is the CRC-32 of the block header and payload seeded with the
previous block's `chain_crc` (the first block is seeded with the CRC of the
file header's body). A **block is the unit of commit**: it counts only when
its magic, bounds and chained checksum all hold, and because each checksum
covers its predecessor, a damaged or missing block invalidates everything
after it, so a block that happens to look valid after a hole (pages can
reach a disk out of order after a power failure) is never accepted. The
longest valid prefix of blocks is exactly what was committed.

The block header carries the first and last generation and the row count, so
a reader (or a checkpoint) learns what a block holds without decoding it. A
*keyframe* (a full record) always opens a block, so a reader can start
decoding at any keyframe block.

This module has no dependency on Numba: the record codec compiles itself
when Numba is installed and runs as plain Python otherwise.
"""

from __future__ import annotations

import logging
import mmap
import os
import queue
import struct
import threading
import time
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Final, Literal, Protocol

import numpy as np

from fim.persistence import tlog_codec as codec
from fim.persistence.frame import FrameLayout, TrajectoryFrame

logger = logging.getLogger(__name__)

try:  # `fcntl` does not exist on Windows.
    import fcntl
except ImportError:  # pragma: no cover - exercised only on Windows
    fcntl = None  # type: ignore[assignment]

FILE_MAGIC: Final = b"FIMTLOG1"
BLOCK_MAGIC: Final = b"FTB1"
FORMAT_VERSION: Final = 1

FLAG_SPARSE: Final = 1
"""Header flag: delta records may follow keyframes (the log is "sparse")."""

_FIXED_HEADER: Final = struct.Struct("<HHIIIH")
_BLOCK_HEADER: Final = struct.Struct("<4sIQQII")
BLOCK_HEADER_SIZE: Final = _BLOCK_HEADER.size
"""Bytes of a block's header (32)."""
_CRC: Final = struct.Struct("<I")
CRC_SIZE: Final = _CRC.size

DEFAULT_BLOCK_GENERATIONS: Final = 512
"""Generations after which the open block is sealed and written."""

DEFAULT_BLOCK_SECONDS: Final[float | None] = None
"""Seconds after which the open block is sealed and written; `None` for never.

Off by default because sealing on time makes the file's bytes depend on how
fast the machine ran (the block boundaries move), while sealing on count and
size alone makes the file a pure function of the run: the same configuration
always writes the same bytes, so a digest in a manifest can be compared
between runs. An interactive caller that wants its live view never more than
a moment stale opts in with a number of seconds; when such a writer closes it
rewrites the log with the canonical block boundaries (see
`LogWriter.canonical_on_close`), so the finished file is the same either way."""

DEFAULT_KEY_EVERY: Final = 256
"""Generations between keyframes in sparse mode."""

DEFAULT_BUFFER_BYTES: Final = 4 * 1024 * 1024
"""Bytes of one block buffer."""

DEFAULT_QUEUE_DEPTH: Final = 4
"""Sealed blocks that may wait for the writer thread."""

DEFAULT_SYNC_SECONDS: Final = 2.0
"""Seconds between group-commit syncs."""

_POLL_SECONDS: Final = 0.05

_VARINT_MORE: Final = 128
_VARINT_PAYLOAD: Final = 127
_VARINT_MAX_SHIFT: Final = 63

MIN_BUFFER_BYTES: Final = 4096
"""The smallest block buffer a writer accepts."""

_RECORD_BOUND_BASE: Final = 64
_RECORD_BOUND_PER_PAIR: Final = 12
_RECORD_BOUND_PER_ENTRY: Final = 32
_BLOCK_SLACK: Final = 8


class TlogError(ValueError):
    """The file is not a usable trajectory log (bad magic, header or version)."""


@dataclass(frozen=True, slots=True)
class LogHeader:
    """The parsed file header of a log.

    Args:
        version: The format version.
        flags: Header flags (`FLAG_SPARSE`).
        run_id: The one run the log holds.
        layout: The frame layout (locus identifiers, gene copies per deme).
        key_every: Generations between keyframes in sparse mode.
        header_len: Bytes of the header including its checksum.
        crc: The CRC-32 of the header's body (the seed of the block chain).
    """

    version: int
    flags: int
    run_id: str
    layout: FrameLayout
    key_every: int
    header_len: int
    crc: int


@dataclass(frozen=True, slots=True)
class BlockInfo:
    """One committed block, as found by `scan_blocks`.

    Args:
        offset: Where the block starts in the file.
        payload_len: Bytes of the block's records.
        first_generation: Generation of the block's first record.
        last_generation: Generation of the block's last record.
        n_records: Records (generations) in the block.
        rows: Trajectory rows in the block's generations.
        end: Offset just after the block's checksum.
        chain_crc: The chained checksum after this block.
        is_key: Whether the block opens with a full record.
    """

    offset: int
    payload_len: int
    first_generation: int
    last_generation: int
    n_records: int
    rows: int
    end: int
    chain_crc: int
    is_key: bool


@dataclass(frozen=True, slots=True)
class ScanResult:
    """What `scan_blocks` and `recover` found.

    Args:
        header: The file header.
        blocks: The committed blocks, in order.
        valid_end: Offset just after the last committed block (the header's
            end for a log with none).
        file_size: Bytes in the file when it was scanned.
        reason: Why scanning stopped (`"end of file"` for a clean log).
    """

    header: LogHeader
    blocks: list[BlockInfo]
    valid_end: int
    file_size: int
    reason: str
    chain_crc: int = field(default=0)

    @property
    def generations(self) -> int:
        """Records (generations) in the committed blocks."""
        return sum(block.n_records for block in self.blocks)

    @property
    def rows(self) -> int:
        """Trajectory rows in the committed blocks."""
        return sum(block.rows for block in self.blocks)

    @property
    def last_generation(self) -> int:
        """The last committed generation, or `-1` when none is committed."""
        return self.blocks[-1].last_generation if self.blocks else -1


@dataclass(frozen=True, slots=True)
class LogPosition:
    """A committed position in a log: what a checkpoint records about it.

    It names the end of a committed block by its byte offset, the chained
    checksum there and the last generation inside, so resuming can check
    that the file really holds that block (one 32-bit comparison; no hash of
    the file) and cut off everything after it. The header is copied in too:
    a damaged header makes a log unusable, so resuming can restore it.

    Args:
        generation: The last committed generation (`-1` for none).
        offset: Bytes of the file up to the end of the last committed block.
        chain_crc: The chained checksum at that offset.
        generations: Generations committed so far.
        rows: Rows committed so far.
        header: The file header's bytes.
    """

    generation: int
    offset: int
    chain_crc: int
    generations: int
    rows: int
    header: bytes


def _varints(values: list[int] | tuple[int, ...]) -> bytes:
    """Encode non-negative integers as base-128 varints."""
    out = bytearray()
    for value in values:
        remaining = value
        while remaining >= _VARINT_MORE:
            out.append((remaining & _VARINT_PAYLOAD) | _VARINT_MORE)
            remaining >>= 7
        out.append(remaining)
    return bytes(out)


def build_header(
    run_id: str, layout: FrameLayout, key_every: int, flags: int = 0
) -> bytes:
    """Return the file header of a log.

    Args:
        run_id: The run the log holds.
        layout: Its frame layout.
        key_every: Generations between keyframes in sparse mode.
        flags: Header flags.

    Returns:
        The header bytes including the trailing CRC-32.
    """
    rid = run_id.encode("utf-8")
    body = (
        FILE_MAGIC
        + _FIXED_HEADER.pack(
            FORMAT_VERSION, flags, layout.demes, layout.loci, key_every, len(rid)
        )
        + rid
        + _varints(layout.deme_sizes)
        + _varints(layout.locus_ids)
    )
    return body + _CRC.pack(zlib.crc32(body))


def parse_header(data: bytes | mmap.mmap | memoryview) -> LogHeader:
    """Parse and verify a log's file header.

    Args:
        data: The start of the file.

    Returns:
        The header.

    Raises:
        TlogError: If the magic, version, length or checksum is wrong.
    """
    fixed_end = len(FILE_MAGIC) + _FIXED_HEADER.size
    if len(data) < fixed_end or bytes(data[: len(FILE_MAGIC)]) != FILE_MAGIC:
        raise TlogError("not a trajectory log (bad magic)")
    version, flags, demes, loci, key_every, rid_len = _FIXED_HEADER.unpack_from(
        data, len(FILE_MAGIC)
    )
    if version != FORMAT_VERSION:
        raise TlogError(f"unsupported trajectory log version {version}")
    pos = fixed_end
    if pos + rid_len > len(data):
        raise TlogError("trajectory log header is truncated")
    try:
        run_id = bytes(data[pos : pos + rid_len]).decode("utf-8")
    except UnicodeDecodeError as error:
        raise TlogError("trajectory log run id is not UTF-8") from error
    pos += rid_len

    def varints(pos: int, count: int) -> tuple[list[int], int]:
        values: list[int] = []
        for _ in range(count):
            value = shift = 0
            while True:
                if pos >= len(data) or shift > _VARINT_MAX_SHIFT:
                    raise TlogError("trajectory log header is truncated")
                byte = data[pos]
                pos += 1
                value |= (byte & _VARINT_PAYLOAD) << shift
                if byte < _VARINT_MORE:
                    break
                shift += 7
            values.append(value)
        return values, pos

    sizes, pos = varints(pos, demes)
    locus_ids, pos = varints(pos, loci)
    if pos + CRC_SIZE > len(data):
        raise TlogError("trajectory log header is truncated")
    (stored,) = _CRC.unpack_from(data, pos)
    crc = zlib.crc32(bytes(data[:pos]))
    if stored != crc:
        raise TlogError("trajectory log header checksum mismatch")
    return LogHeader(
        version=version,
        flags=flags,
        run_id=run_id,
        layout=FrameLayout(locus_ids=tuple(locus_ids), deme_sizes=tuple(sizes)),
        key_every=key_every,
        header_len=pos + CRC_SIZE,
        crc=crc,
    )


def scan_blocks(
    data: bytes | mmap.mmap | memoryview,
    header: LogHeader | None = None,
    *,
    resume: ScanResult | None = None,
) -> ScanResult:
    """Walk the chained blocks of a log and return the committed prefix.

    Stops at the first block that is torn, zero-filled, out of chain or
    corrupt; everything before it is committed, everything from it on is not.

    Args:
        data: The whole file.
        header: Its parsed header, if already known.
        resume: An earlier scan of the same file (a prefix of it). Scanning
            continues from where that one stopped instead of from the start,
            so following a log that is still being written costs only the
            new blocks. The caller must have checked that the earlier prefix
            still holds (`prefix_still_holds`).

    Returns:
        The committed blocks and why scanning stopped.
    """
    header = header or (resume.header if resume is not None else parse_header(data))
    size = len(data)
    view = memoryview(data)
    pos = resume.valid_end if resume is not None else header.header_len
    chain = resume.chain_crc if resume is not None else header.crc
    blocks: list[BlockInfo] = list(resume.blocks) if resume is not None else []
    reason = "end of file"
    while pos < size:
        if pos + BLOCK_HEADER_SIZE + CRC_SIZE > size:
            reason = "block header extends past the end of the file (torn)"
            break
        magic, payload_len, first, last, n_records, rows = _BLOCK_HEADER.unpack_from(
            data, pos
        )
        if magic != BLOCK_MAGIC:
            reason = "no block magic (torn or zero tail)"
            break
        end = pos + BLOCK_HEADER_SIZE + payload_len
        if end + CRC_SIZE > size:
            reason = "block extends past the end of the file (torn)"
            break
        crc = zlib.crc32(view[pos:end], chain)
        (stored,) = _CRC.unpack_from(data, end)
        if crc != stored:
            reason = "checksum mismatch"
            break
        is_key = (
            payload_len > 2 * codec.PAD4
            and data[pos + BLOCK_HEADER_SIZE + 2 * codec.PAD4] == codec.KIND_FULL
        )
        blocks.append(
            BlockInfo(
                offset=pos,
                payload_len=payload_len,
                first_generation=first,
                last_generation=last,
                n_records=n_records,
                rows=rows,
                end=end + CRC_SIZE,
                chain_crc=crc,
                is_key=bool(is_key),
            )
        )
        chain = crc
        pos = end + CRC_SIZE
    # `pos` is where the first bad block (or the end of the file) begins,
    # which is exactly the end of the committed prefix.
    return ScanResult(
        header=header,
        blocks=blocks,
        valid_end=pos,
        file_size=size,
        reason=reason,
        chain_crc=chain,
    )


def prefix_still_holds(data: bytes | mmap.mmap | memoryview, scan: ScanResult) -> bool:
    """Whether an earlier scan's committed prefix is still the start of this file.

    A cheap check, not a verification: the file must be at least as long as
    the prefix, its header must be the same, and the chain checksum stored at
    the end of the prefix must still be the one the scan recorded. A log that
    is only ever appended to (a run in progress) always passes; a file that
    was replaced fails with overwhelming probability. Whole-file integrity
    is the manifest digest's job.

    Args:
        data: The file as it is now.
        scan: The earlier scan.

    Returns:
        `True` if scanning may resume from `scan.valid_end`.
    """
    if len(data) < scan.valid_end:
        return False
    try:
        if parse_header(data).crc != scan.header.crc:
            return False
    except TlogError:
        return False
    if not scan.blocks:
        return True
    (stored,) = _CRC.unpack_from(data, scan.valid_end - CRC_SIZE)
    return bool(stored == scan.chain_crc)


def recover(path: Path | str, *, truncate: bool = True) -> ScanResult:
    """Scan a log file and, by default, cut off everything after the committed prefix.

    Args:
        path: The log file.
        truncate: Whether to truncate a torn or corrupt tail. A reader that
            does not own the file passes `False`.

    Returns:
        What was found.

    Raises:
        TlogError: If the file is empty or its header is unusable.
    """
    with Path(path).open("r+b" if truncate else "rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        if size == 0:
            raise TlogError("trajectory log is empty")
        with mmap.mmap(
            handle.fileno(),
            0,
            access=mmap.ACCESS_READ,
        ) as data:
            result = scan_blocks(data)
        if truncate and result.valid_end < size:
            handle.truncate(result.valid_end)
    return result


def _record_bound(pairs: int, entries: int) -> int:
    """Return an upper bound on the bytes one encoded record can take."""
    return (
        _RECORD_BOUND_BASE
        + _RECORD_BOUND_PER_PAIR * pairs
        + _RECORD_BOUND_PER_ENTRY * entries
    )


class InjectedFaultError(OSError):
    """A failure a `FaultHook` asked for; it stands in for a crash or a full disk."""


class FaultHook(Protocol):
    """Named points in the write path where a test may inject a failure.

    The writer calls the hook as `hook(point, size)` at each point:
    `"before_write"` (`size` is the bytes about to be written),
    `"after_write"`, `"before_sync"` and `"after_sync"`. The hook may raise
    (`InjectedFaultError`) to stop the writer there. At `"before_write"` it may
    instead return a byte count smaller than `size`: only that many bytes
    are written and then `InjectedFaultError` is raised, which leaves a torn
    block exactly as a crash in the middle of `write(2)` would.
    """

    def __call__(self, point: str, size: int = 0) -> int | None:
        """Observe `point`; optionally shorten a write or raise."""
        ...


LogMode = Literal["dense", "sparse"]
"""`"dense"` writes every generation in full; `"sparse"` writes a full
keyframe every `key_every` generations and, between them, only the (deme,
locus) pairs that changed."""

SyncMode = Literal["none", "fsync", "full", "auto"]
"""How the file is made durable: not at all, `fsync`, `F_FULLFSYNC`, or the
strongest call the platform has (`"full"` on macOS, `fsync` elsewhere)."""


def sync_file(fd: int, mode: SyncMode) -> None:
    """Make the data written to `fd` durable.

    `fsync` on macOS only hands data to the drive, which may keep it in its
    cache; `F_FULLFSYNC` asks the drive to flush, and costs milliseconds.
    Elsewhere `fsync` is durable.

    Args:
        fd: An open file descriptor.
        mode: `"none"` does nothing, `"fsync"` calls `os.fsync`, `"full"`
            calls `F_FULLFSYNC`, `"auto"` picks `"full"` where the platform
            has it and `"fsync"` otherwise.

    Raises:
        OSError: If `"full"` is asked for and the platform lacks it, or the
            call fails.
    """
    if mode == "none":
        return
    full = getattr(fcntl, "F_FULLFSYNC", None) if fcntl is not None else None
    if mode == "auto":
        mode = "full" if full is not None else "fsync"
    if mode == "full":
        if full is None or fcntl is None:
            raise OSError("F_FULLFSYNC is not available on this platform")
        fcntl.fcntl(fd, full)
    else:
        os.fsync(fd)


@dataclass(slots=True)
class WriterStats:
    """Counters a writer keeps, for tests and for tuning.

    Args:
        blocks: Blocks committed.
        generations: Generations committed.
        rows: Rows committed.
        bytes: File bytes written, header included.
        syncs: Durability calls made.
        stall_seconds: Time the producer waited for a free buffer.
        timed_seals: Blocks sealed because `block_seconds` had passed, the only
            kind of seal that depends on how fast the machine ran.
    """

    blocks: int = 0
    generations: int = 0
    rows: int = 0
    bytes: int = 0
    syncs: int = 0
    stall_seconds: float = 0.0
    timed_seals: int = 0


@dataclass(slots=True)
class _Sealed:
    """A sealed block waiting to be checksummed and written."""

    buffer: np.ndarray
    length: int
    last_generation: int
    records: int
    rows: int


_INITIAL_WIDTH: Final = 16
_STOP: Final = object()
_SYNC: Final = object()


class LogWriter:
    """Encode frames into blocks and append them to a log file.

    One writer owns one log file for one run. `submit` encodes a frame into
    the open block; the block is sealed when it holds `block_generations`
    generations, when `block_seconds` (if set) have passed on the injected
    `clock`, or
    when the next record would not fit. A sealed block is checksummed and
    written with `write(2)`; once that returns it is *committed*, and a
    process killed afterwards loses at most the open block and the blocks
    still queued.

    With `background=True` the checksum, the write and the sync run on a
    writer thread, so the producing thread only encodes. That thread calls
    nothing but `zlib.crc32`, `os.write` and the sync call, all of which
    release the GIL, so it never competes with a compiled kernel for it. A
    pool of block buffers bounds memory: when every buffer is queued or being
    written the producer waits (back-pressure), and the wait is counted in
    `stats.stall_seconds`. An exception in the thread is raised by the next
    `submit` or by `close`.

    Durability is a group commit: with `sync` other than `"none"` the writer
    thread syncs the file when `sync_seconds` have passed since the last
    sync, when `flush(sync=True)` is called (a checkpoint or a pause), and
    when the writer closes.

    Args:
        path: The log file; it must not exist.
        run_id: The run the log holds.
        layout: Its frame layout.
        block_generations: Generations per block, at most.
        block_seconds: Seconds an open block may wait before being sealed, or
            `None` (the default) to seal on count and size only, which keeps
            the file's bytes a pure function of the frames written.
        buffer_bytes: Size of a block buffer.
        key_every: Generations between keyframes in sparse mode.
        mode: `"dense"` (every record full) or `"sparse"` (keyframes and
            deltas). A keyframe always opens a block, so a reader can
            start decoding there.
        clock: A monotonic clock returning seconds (tests inject one). It
            only decides *when* to seal or sync; no time enters any byte.
        background: Whether a writer thread does the checksum, write and sync.
        queue_depth: Sealed blocks that may wait for the thread, and one
            less than the number of buffers.
        canonical_on_close: When a block was sealed on `block_seconds`, the
            block boundaries (and so the file's bytes) depend on timing. With
            this set (the default) `close` then rewrites the log, in the same
            directory and from the committed frames, with the boundaries a
            writer without `block_seconds` would have chosen, and replaces
            the file. The finished log is then a pure function of the run
            whatever the timing was. Positions from `checkpoint` refer to
            the file before that rewrite. A rewrite that fails (a reader
            holding the file open on Windows, a full disk) leaves the valid
            original and logs a warning.
        sync: Durability policy, see `sync_file`.
        sync_seconds: Seconds between group-commit syncs.
        sync_function: Replaces `sync_file` (tests inject one).
        fault: A `FaultHook` for tests.
        resume: `None` creates a new log (the file must not exist). `True`
            reopens an existing log of the same run and layout, drops any torn
            or corrupt tail and continues after the last committed block. A
            `LogPosition` does the same but cuts back to that checkpointed
            position, which it first checks against the file. After either,
            the first record written is a keyframe (the previous frame is
            not in memory), so the log stays decodable from any keyframe.

    Raises:
        FileExistsError: If `path` already exists and `resume` is `None`.
        FileNotFoundError: If `resume` is set and the file does not exist.
        TlogError: If a resumed file is not a log of this run and layout.
        ValueError: If a checkpoint position does not match the file.
    """

    def __init__(
        self,
        path: Path | str,
        run_id: str,
        layout: FrameLayout,
        *,
        block_generations: int = DEFAULT_BLOCK_GENERATIONS,
        block_seconds: float | None = DEFAULT_BLOCK_SECONDS,
        buffer_bytes: int = DEFAULT_BUFFER_BYTES,
        key_every: int = DEFAULT_KEY_EVERY,
        mode: LogMode = "dense",
        clock: Callable[[], float] = time.monotonic,
        background: bool = False,
        queue_depth: int = DEFAULT_QUEUE_DEPTH,
        canonical_on_close: bool = True,
        sync: SyncMode = "none",
        sync_seconds: float = DEFAULT_SYNC_SECONDS,
        sync_function: Callable[[int, SyncMode], None] = sync_file,
        fault: FaultHook | None = None,
        resume: LogPosition | bool | None = None,
    ) -> None:
        """Create the file (or reopen it to continue) and start the thread if asked."""
        self._validate_options(
            block_generations=block_generations,
            block_seconds=block_seconds,
            buffer_bytes=buffer_bytes,
            queue_depth=queue_depth,
            sync=sync,
            sync_seconds=sync_seconds,
            mode=mode,
            key_every=key_every,
        )
        self.path = Path(path)
        self.run_id = run_id
        self.layout = layout
        self.block_generations = block_generations
        self.block_seconds = block_seconds
        self.buffer_bytes = buffer_bytes
        self.key_every = key_every
        self.mode = mode
        self.background = background
        self.canonical_on_close = canonical_on_close
        self.sync = sync
        self.sync_seconds = sync_seconds
        self.stats = WriterStats()
        self._clock = clock
        self._sync_function = sync_function
        self._fault = fault
        self._sizes = np.asarray(layout.deme_sizes, dtype=np.int64)
        self._tmp8 = np.zeros(1, np.float64)
        self._header = build_header(
            run_id, layout, key_every, FLAG_SPARSE if mode == "sparse" else 0
        )
        self._closed = False
        self._state_lock = threading.Lock()
        self._error: BaseException | None = None
        resumed: ScanResult | None = None
        if resume is None or resume is False:
            self._fd = os.open(
                self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY, 0o644
            )
            try:
                self._write_all(self._header)
            except BaseException:
                os.close(self._fd)
                raise
        else:
            resumed = self._open_for_resume(None if resume is True else resume)
        self._init_state(clock)
        if resumed is not None:
            self._adopt_scan(resumed)
        self._init_buffers(queue_depth)
        if background:
            self._start_thread()

    @staticmethod
    def _validate_options(
        *,
        block_generations: int,
        block_seconds: float | None,
        buffer_bytes: int,
        queue_depth: int,
        sync: str,
        sync_seconds: float,
        mode: str,
        key_every: int,
    ) -> None:
        """Refuse nonsense options before any file is touched.

        Raises:
            ValueError: For the first bad option, naming it.
        """
        if block_generations < 1:
            raise ValueError("block_generations must be at least 1")
        if block_seconds is not None and block_seconds <= 0:
            raise ValueError("block_seconds must be positive")
        if buffer_bytes < MIN_BUFFER_BYTES:
            raise ValueError(f"buffer_bytes must be at least {MIN_BUFFER_BYTES}")
        if queue_depth < 1:
            raise ValueError("queue_depth must be at least 1")
        if sync_seconds <= 0:
            raise ValueError("sync_seconds must be positive")
        if sync not in ("none", "fsync", "full", "auto"):
            raise ValueError(f"unknown sync mode {sync!r}")
        if mode not in ("dense", "sparse"):
            raise ValueError(f"unknown log mode {mode!r}")
        if key_every < 1:
            raise ValueError("key_every must be at least 1")

    def _open_for_resume(self, position: LogPosition | None) -> ScanResult:
        """Reopen an existing log for appending after its committed prefix.

        Checks that the file is this run's log, restores a damaged header from
        the checkpoint when one is given, verifies the checkpoint against the
        file, and cuts off everything after the resume point.

        Returns:
            The scan of what remains.
        """
        if not self.path.is_file():
            raise FileNotFoundError(f"trajectory log does not exist: {self.path}")
        with self.path.open("r+b") as handle:
            if os.fstat(handle.fileno()).st_size == 0:
                raise TlogError("trajectory log is empty")
            self._restore_damaged_header(handle, position)
            with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
                header = parse_header(data)
                self._check_same_log(header)
                scan = scan_blocks(data, header)
            if position is not None:
                scan = self._cut_back_to(scan, position)
            if scan.valid_end < os.fstat(handle.fileno()).st_size:
                handle.truncate(scan.valid_end)
        self._fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | _O_BINARY)
        return scan

    def _restore_damaged_header(
        self, handle: BinaryIO, position: LogPosition | None
    ) -> None:
        """Rewrite a header that no longer parses from the checkpoint's copy.

        Raises:
            TlogError: If the header is damaged and there is no copy to restore.
        """
        with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
            try:
                parse_header(data)
            except TlogError:
                if position is None or len(data) < len(position.header):
                    raise
            else:
                return
        handle.seek(0)
        handle.write(position.header if position is not None else b"")
        handle.flush()

    def _check_same_log(self, header: LogHeader) -> None:
        """Require the file's header to be this writer's run, layout and mode.

        Raises:
            TlogError: If it is not.
        """
        if header.run_id != self.run_id or header.layout != self.layout:
            raise TlogError(
                f"{self.path.name} holds another run or layout, cannot continue it"
            )
        if bool(header.flags & FLAG_SPARSE) != (self.mode == "sparse"):
            raise TlogError(f"{self.path.name} was written in another mode")
        self.key_every = header.key_every

    def _cut_back_to(self, scan: ScanResult, position: LogPosition) -> ScanResult:
        """Return `scan` limited to the blocks up to a checked checkpoint."""
        cut = self._checkpoint_offset(scan, position)
        kept = [block for block in scan.blocks if block.end <= cut]
        return ScanResult(
            header=scan.header,
            blocks=kept,
            valid_end=cut,
            file_size=scan.file_size,
            reason=scan.reason,
            chain_crc=kept[-1].chain_crc if kept else scan.header.crc,
        )

    @staticmethod
    def _checkpoint_offset(scan: ScanResult, position: LogPosition) -> int:
        """Return the offset a checkpoint names after checking it against the scan.

        Raises:
            ValueError: If no committed block ends there with that checksum
                and last generation (the file is shorter, or another log).
        """
        if position.generation < 0:
            if position.offset != scan.header.header_len:
                raise ValueError("checkpoint does not match the log (offset)")
            return position.offset
        for block in scan.blocks:
            if block.end == position.offset:
                if (
                    block.chain_crc != position.chain_crc
                    or block.last_generation != position.generation
                ):
                    raise ValueError("checkpoint does not match the log (checksum)")
                return position.offset
        raise ValueError(
            "checkpoint does not match the log: no committed block ends at "
            f"byte {position.offset} (the file may be shorter than the checkpoint)"
        )

    def _adopt_scan(self, scan: ScanResult) -> None:
        """Continue from a scanned committed prefix instead of an empty log."""
        self.file_position = scan.valid_end
        self.chain_crc = scan.chain_crc
        self.committed_generation = scan.last_generation
        self.generations_written = scan.generations
        self.rows_written = scan.rows
        self.blocks_written = len(scan.blocks)
        self._latest = scan.last_generation
        self.stats.bytes = scan.valid_end

    def _init_state(self, clock: Callable[[], float]) -> None:
        """Set the commit counters and the open-block bookkeeping."""
        self.file_position = len(self._header)
        self.stats.bytes = len(self._header)
        self.chain_crc = zlib.crc32(self._header[:-CRC_SIZE])
        self.committed_generation = -1
        self.generations_written = 0
        self.rows_written = 0
        self.blocks_written = 0
        self._last_sync = clock()
        self._synced_generation = -1
        self._buffer: np.ndarray | None = None
        self._spare: np.ndarray | None = None
        self._latest = -1
        self._limit = 0
        self._pos = 0
        self._records = 0
        self._rows = 0
        self._first_generation = 0
        self._last_generation = 0
        self._opened_at = 0.0
        self._since_key = 0
        self._previous_count = np.zeros(self.layout.pairs, np.int32)
        self._previous_ids = np.zeros((self.layout.pairs, _INITIAL_WIDTH), np.int64)
        self._previous_freq = np.zeros((self.layout.pairs, _INITIAL_WIDTH), np.float64)

    def _init_buffers(self, queue_depth: int) -> None:
        """Set up the buffer pool and queues used by a background writer."""
        self._work: queue.Queue[object] | None = None
        self._free: queue.Queue[np.ndarray] | None = None
        self._buffers_made = 0
        self._buffer_limit = queue_depth + 2
        self._thread: threading.Thread | None = None
        self._queue_depth = queue_depth

    def _start_thread(self) -> None:
        """Start the writer thread and its queues."""
        self._work = queue.Queue(maxsize=self._queue_depth)
        self._free = queue.Queue()
        self._thread = threading.Thread(
            target=self._run, name="fim-tlog-writer", daemon=True
        )
        self._thread.start()

    @property
    def synced_generation(self) -> int:
        """The last generation known to have been made durable (`-1` for none)."""
        return self._synced_generation

    def submit(self, frame: TrajectoryFrame) -> None:
        """Encode one generation into the open block, sealing it when due.

        Args:
            frame: The generation; its counts must match the layout.

        Raises:
            ValueError: If generations do not strictly increase or the
                frame does not fit the layout.
            RuntimeError: If the writer is closed.
            BaseException: Whatever the writer thread raised earlier.
        """
        if self._closed:
            raise RuntimeError("the log writer is closed")
        self._raise_error()
        if frame.counts.shape != (self.layout.pairs,):
            raise ValueError("frame does not fit the log's layout")
        if frame.generation <= self._latest:
            raise ValueError(
                f"generation {frame.generation} does not follow {self._latest}"
            )
        entries = int(frame.allele_ids.shape[0])
        bound = _record_bound(self.layout.pairs, entries)
        # In sparse mode every `key_every`-th record (and the first) is a full
        # keyframe, which always opens a block.
        keyframe = self.mode == "dense" or self._since_key == 0
        keyframe = keyframe or self._since_key >= self.key_every
        if self._buffer is not None:
            regular = self._pos + bound + _BLOCK_SLACK > self._limit or (
                keyframe and self.mode == "sparse" and self._records > 0
            )
            timed = (
                not regular
                and self.block_seconds is not None
                and self._clock() - self._opened_at >= self.block_seconds
            )
            if regular or timed:
                self.stats.timed_seals += timed
                self._seal()
        if self._buffer is None:
            self._begin(frame.generation, bound)
        assert self._buffer is not None
        gen_delta = (
            0 if self._records == 0 else frame.generation - self._last_generation
        )
        if keyframe:
            self._pos, rows = codec.enc_full(
                self._buffer,
                self._pos,
                gen_delta,
                frame.counts,
                frame.allele_ids,
                frame.frequencies,
                self.layout.demes,
                self.layout.loci,
                self._sizes,
                self._tmp8,
            )
            if self.mode == "sparse":
                self._ensure_state_width(int(frame.counts.max()))
                codec.load_state_from_csr(
                    frame.counts,
                    frame.allele_ids,
                    frame.frequencies,
                    self._previous_count,
                    self._previous_ids,
                    self._previous_freq,
                )
                self._since_key = 0
        else:
            self._ensure_state_width(int(frame.counts.max()))
            self._pos, rows, _changed = codec.enc_delta(
                self._buffer,
                self._pos,
                gen_delta,
                frame.counts,
                frame.allele_ids,
                frame.frequencies,
                self.layout.demes,
                self.layout.loci,
                self._sizes,
                self._previous_count,
                self._previous_ids,
                self._previous_freq,
                self._tmp8,
            )
        self._since_key += 1
        self._records += 1
        self._rows += rows
        self._last_generation = frame.generation
        self._latest = frame.generation
        # A block that has reached its generation count is sealed at once, so
        # it becomes readable now rather than when the next frame arrives.
        if self._records >= self.block_generations:
            self._seal()

    def flush(self, *, sync: bool = False) -> None:
        """Seal the open block and wait until everything submitted is written.

        A barrier: when this returns every generation submitted so far is
        committed (and durable when `sync` is true and the sync mode is not
        `"none"`). A checkpoint or a pause calls it with `sync=True`.

        Args:
            sync: Also make the data durable now.

        Raises:
            BaseException: Whatever the writer thread raised.
        """
        self._raise_error()
        if self._buffer is not None and self._records:
            self._seal()
        if self._work is not None:
            if sync:
                self._work.put(_SYNC)
            self._work.join()
            self._raise_error()
        elif sync:
            self._sync_now()

    def close(self) -> None:
        """Write the open block, sync, stop the thread and close the file.

        Safe to call twice. The file descriptor is closed even when an
        earlier write failed; the failure is then raised from here.

        Raises:
            BaseException: Whatever the writer thread raised.
        """
        if self._closed:
            return
        try:
            try:
                if self._buffer is not None and self._records:
                    self._seal()
            finally:
                if self._work is not None:
                    self._work.put(_STOP)
                    assert self._thread is not None
                    self._thread.join()
            self._raise_error()
            if self.sync != "none":
                self._sync_now()
        finally:
            self._closed = True
            os.close(self._fd)
        if self.canonical_on_close and self.stats.timed_seals:
            self._rewrite_canonically()

    def _rewrite_canonically(self) -> None:
        """Replace the closed log with the same frames in canonical blocks.

        Called once, from `close`, when timing moved a block boundary. The
        rewrite goes to a sibling file that replaces the log atomically, so a
        failure at any point leaves the original, complete, log in place.
        """
        rewritten = self.path.with_name(self.path.name + ".canonical")
        rewritten.unlink(missing_ok=True)
        try:
            writer = LogWriter(
                rewritten,
                self.run_id,
                self.layout,
                block_generations=self.block_generations,
                block_seconds=None,
                buffer_bytes=self.buffer_bytes,
                key_every=self.key_every,
                mode=self.mode,
                background=False,
                sync=self.sync,
                clock=self._clock,
            )
            try:
                for frame in iter_frames(self.path):
                    writer.submit(frame)
            finally:
                writer.close()
            rewritten.replace(self.path)
        except (OSError, TlogError):
            logger.warning(
                "could not rewrite %s with canonical blocks; keeping the original",
                self.path,
                exc_info=True,
            )
            rewritten.unlink(missing_ok=True)

    def snapshot(self) -> tuple[int, int, int]:
        """Return `(committed generation, committed bytes, chain checksum)` together.

        Read under the lock the writer thread holds while it updates them,
        so the three always describe the same block.
        """
        with self._state_lock:
            return self.committed_generation, self.file_position, self.chain_crc

    def _ensure_state_width(self, needed: int) -> None:
        """Widen the previous-frame state so every pair of `needed` alleles fits."""
        width = self._previous_ids.shape[1]
        if needed <= width:
            return
        new_width = max(needed, 2 * width)
        ids = np.zeros((self.layout.pairs, new_width), np.int64)
        freq = np.zeros((self.layout.pairs, new_width), np.float64)
        ids[:, :width] = self._previous_ids
        freq[:, :width] = self._previous_freq
        self._previous_ids, self._previous_freq = ids, freq

    def checkpoint(self) -> LogPosition:
        """Commit and sync everything written so far and return its position.

        The barrier a checkpoint or a pause needs: when this returns, every
        submitted generation is in a committed block and durable (when the
        sync mode is not `"none"`), and the returned position names exactly
        that. Resuming from it later cuts the file back to this point.

        Returns:
            The committed position.

        Raises:
            BaseException: Whatever the writer thread raised.
        """
        self.flush(sync=True)
        return self.position()

    def position(self) -> LogPosition:
        """Return the committed position now, without waiting for anything.

        Whatever the writer thread has committed so far; `checkpoint` is the
        barrier that makes it everything submitted.
        """
        with self._state_lock:
            return LogPosition(
                generation=self.committed_generation,
                offset=self.file_position,
                chain_crc=self.chain_crc,
                generations=self.generations_written,
                rows=self.rows_written,
                header=self._header,
            )

    def _raise_error(self) -> None:
        """Raise the exception the writer thread stored, if any."""
        error = self._error
        if error is not None:
            raise error

    def _begin(self, generation: int, bound: int) -> None:
        """Open a new block whose first record is `generation`."""
        needed = BLOCK_HEADER_SIZE + bound + _BLOCK_SLACK + CRC_SIZE
        buffer = self._take_buffer(needed)
        self._buffer = buffer
        self._limit = len(buffer) - CRC_SIZE
        self._pos = BLOCK_HEADER_SIZE
        self._records = 0
        self._rows = 0
        self._first_generation = generation
        self._opened_at = self._clock()

    def _take_buffer(self, needed: int) -> np.ndarray:
        """Return a buffer of at least `needed` bytes, waiting if all are in use."""
        if self._free is None:
            spare, self._spare = self._spare, None
            if spare is not None and len(spare) >= needed:
                return spare
            return self._new_buffer(needed)
        try:
            buffer = self._free.get_nowait()
        except queue.Empty:
            if self._buffers_made < self._buffer_limit:
                return self._new_buffer(needed)
            started = self._clock()
            while True:
                self._raise_error()
                try:
                    buffer = self._free.get(timeout=_POLL_SECONDS)
                    break
                except queue.Empty:
                    continue
            self.stats.stall_seconds += self._clock() - started
        if len(buffer) < needed:
            return self._new_buffer(needed)
        return buffer

    def _new_buffer(self, needed: int) -> np.ndarray:
        """Allocate a block buffer big enough for `needed` bytes."""
        self._buffers_made += 1
        size = max(self.buffer_bytes, 2 * needed if needed > self.buffer_bytes else 0)
        return np.zeros(size, np.uint8)

    def _seal(self) -> None:
        """Close the open block and commit it (queue it when in the background)."""
        buffer = self._buffer
        assert buffer is not None
        _BLOCK_HEADER.pack_into(
            buffer,
            0,
            BLOCK_MAGIC,
            self._pos - BLOCK_HEADER_SIZE,
            self._first_generation,
            self._last_generation,
            self._records,
            self._rows,
        )
        sealed = _Sealed(
            buffer=buffer,
            length=self._pos,
            last_generation=self._last_generation,
            records=self._records,
            rows=self._rows,
        )
        self._buffer = None
        self._records = 0
        if self._work is None:
            self._commit(sealed)
        else:
            self._raise_error()
            self._work.put(sealed)

    def _commit(self, sealed: _Sealed) -> None:
        """Checksum and write one sealed block, then sync if it is time."""
        buffer = sealed.buffer
        crc = zlib.crc32(memoryview(buffer)[: sealed.length], self.chain_crc)
        _CRC.pack_into(buffer, sealed.length, crc)
        total = sealed.length + CRC_SIZE
        self._write_block(memoryview(buffer)[:total])
        with self._state_lock:
            self.chain_crc = crc
            self.file_position += total
            self.committed_generation = sealed.last_generation
            self.generations_written += sealed.records
            self.rows_written += sealed.rows
            self.blocks_written += 1
        self.stats.blocks += 1
        self.stats.generations += sealed.records
        self.stats.rows += sealed.rows
        self.stats.bytes += total
        if self._free is not None:
            self._free.put(buffer)
        else:
            self._spare = buffer
        if self.sync != "none" and self._clock() - self._last_sync >= self.sync_seconds:
            self._sync_now()

    def _write_block(self, data: memoryview) -> None:
        """Write one sealed block, honoring the fault hook."""
        size = len(data)
        if self._fault is not None:
            allowed = self._fault("before_write", size)
            if allowed is not None and allowed < size:
                self._write_all(data[:allowed])
                raise InjectedFaultError(
                    f"write cut short after {allowed} of {size} bytes"
                )
        self._write_all(data)
        if self._fault is not None:
            self._fault("after_write", size)

    def _sync_now(self) -> None:
        """Make everything committed so far durable; honor the fault hook.

        Does nothing when the sync mode is `"none"` or when everything
        committed is already durable (a periodic sync may have just run).
        """
        if self.sync == "none" or self._synced_generation == self.committed_generation:
            return
        if self._fault is not None:
            self._fault("before_sync", 0)
        self._sync_function(self._fd, self.sync)
        self._last_sync = self._clock()
        self._synced_generation = self.committed_generation
        self.stats.syncs += 1
        if self._fault is not None:
            self._fault("after_sync", 0)

    def _write_all(self, data: bytes | memoryview) -> None:
        """Write every byte of `data`, retrying short writes."""
        view = memoryview(data)
        done = 0
        while done < len(view):
            done += os.write(self._fd, view[done:])

    def _run(self) -> None:
        """Writer thread: commit queued blocks and honor sync requests."""
        assert self._work is not None
        failed = False
        while True:
            item = self._work.get()
            try:
                if item is _STOP:
                    return
                if failed:
                    continue
                if item is _SYNC:
                    self._sync_now()
                elif isinstance(item, _Sealed):
                    self._commit(item)
            except BaseException as error:  # re-raised on the producer
                self._error = error
                failed = True
            finally:
                self._work.task_done()


_O_BINARY: Final = getattr(os, "O_BINARY", 0)
"""`O_BINARY` on Windows (no newline translation); `0` elsewhere."""


def iter_frames(
    path: Path | str, *, scan: ScanResult | None = None
) -> Iterator[TrajectoryFrame]:
    """Yield every committed generation of a log as a frame, in order.

    Reads the file through a read-only memory map, applying each record to a
    decoded state (a delta needs the generation before it). Frames are
    independent copies.

    Args:
        path: The log file.
        scan: A scan of the same file, to avoid scanning twice.

    Yields:
        One `TrajectoryFrame` per committed generation.

    Raises:
        TlogError: If the file is not a usable log.
    """
    with Path(path).open("rb") as handle:
        if os.fstat(handle.fileno()).st_size == 0:
            raise TlogError("trajectory log is empty")
        with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
            result = scan or scan_blocks(data)
            layout = result.header.layout
            sizes = np.asarray(layout.deme_sizes, dtype=np.int64)
            state = codec.DecodedState(layout.pairs)
            buffer = np.frombuffer(data, dtype=np.uint8)
            try:
                for block in result.blocks:
                    position = block.offset + BLOCK_HEADER_SIZE
                    generation = block.first_generation
                    for index in range(block.n_records):
                        position, _rows, _kind, gen_delta = codec.decode_record(
                            buffer,
                            position,
                            layout.demes,
                            layout.loci,
                            sizes,
                            state,
                        )
                        if index:
                            generation += gen_delta
                        counts, ids, frequencies = state.frame_arrays()
                        yield TrajectoryFrame(
                            generation=generation,
                            counts=counts,
                            allele_ids=ids,
                            frequencies=frequencies,
                        )
            finally:
                del buffer
