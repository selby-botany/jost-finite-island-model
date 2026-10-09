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

import mmap
import os
import struct
import time
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import numpy as np

from fim.persistence import tlog_codec as codec
from fim.persistence.frame import FrameLayout, TrajectoryFrame

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

DEFAULT_BLOCK_SECONDS: Final = 2.0
"""Seconds after which the open block is sealed and written, whichever first."""

DEFAULT_KEY_EVERY: Final = 256
"""Generations between keyframes in sparse mode."""

DEFAULT_BUFFER_BYTES: Final = 4 * 1024 * 1024
"""Bytes of one block buffer."""

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
    data: bytes | mmap.mmap | memoryview, header: LogHeader | None = None
) -> ScanResult:
    """Walk the chained blocks of a log and return the committed prefix.

    Stops at the first block that is torn, zero-filled, out of chain or
    corrupt; everything before it is committed, everything from it on is not.

    Args:
        data: The whole file.
        header: Its parsed header, if already known.

    Returns:
        The committed blocks and why scanning stopped.
    """
    header = header or parse_header(data)
    size = len(data)
    view = memoryview(data)
    pos = header.header_len
    chain = header.crc
    blocks: list[BlockInfo] = []
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


class LogWriter:
    """Encode frames into blocks and append them to a log file.

    One writer owns one log file for one run. `submit` encodes a frame into
    the open block; the block is sealed (checksummed and written with
    `write(2)`) when it holds `block_generations` generations, when
    `block_seconds` have passed on the injected `clock`, or when the next
    record would not fit. A sealed block is committed as soon as `write(2)`
    returns: a process killed afterwards loses at most the open block.

    Args:
        path: The log file; it must not exist.
        run_id: The run the log holds.
        layout: Its frame layout.
        block_generations: Generations per block, at most.
        block_seconds: Seconds an open block may wait before being sealed.
        buffer_bytes: Size of a block buffer.
        key_every: Generations between keyframes in sparse mode.
        clock: A monotonic clock returning seconds (tests inject one).

    Raises:
        FileExistsError: If `path` already exists.
    """

    def __init__(
        self,
        path: Path | str,
        run_id: str,
        layout: FrameLayout,
        *,
        block_generations: int = DEFAULT_BLOCK_GENERATIONS,
        block_seconds: float = DEFAULT_BLOCK_SECONDS,
        buffer_bytes: int = DEFAULT_BUFFER_BYTES,
        key_every: int = DEFAULT_KEY_EVERY,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create the file and write its header."""
        if block_generations < 1:
            raise ValueError("block_generations must be at least 1")
        if block_seconds <= 0:
            raise ValueError("block_seconds must be positive")
        if buffer_bytes < MIN_BUFFER_BYTES:
            raise ValueError(f"buffer_bytes must be at least {MIN_BUFFER_BYTES}")
        self.path = Path(path)
        self.run_id = run_id
        self.layout = layout
        self.block_generations = block_generations
        self.block_seconds = block_seconds
        self.buffer_bytes = buffer_bytes
        self.key_every = key_every
        self._clock = clock
        self._sizes = np.asarray(layout.deme_sizes, dtype=np.int64)
        self._tmp8 = np.zeros(1, np.float64)
        self._header = build_header(run_id, layout, key_every)
        self._fd = os.open(
            self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY, 0o644
        )
        self._closed = False
        try:
            self._write_all(self._header)
        except BaseException:
            os.close(self._fd)
            raise
        self.file_position = len(self._header)
        self.chain_crc = zlib.crc32(self._header[:-CRC_SIZE])
        self.committed_generation = -1
        self.generations_written = 0
        self.rows_written = 0
        self.blocks_written = 0
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

    def submit(self, frame: TrajectoryFrame) -> None:
        """Encode one generation into the open block, sealing it when due.

        Args:
            frame: The generation; its counts must match the layout.

        Raises:
            ValueError: If generations do not strictly increase or the
                frame does not fit the layout.
            RuntimeError: If the writer is closed.
        """
        if self._closed:
            raise RuntimeError("the log writer is closed")
        if frame.counts.shape != (self.layout.pairs,):
            raise ValueError("frame does not fit the log's layout")
        if frame.generation <= self._latest:
            raise ValueError(
                f"generation {frame.generation} does not follow {self._latest}"
            )
        entries = int(frame.allele_ids.shape[0])
        bound = _record_bound(self.layout.pairs, entries)
        if self._buffer is not None and (
            self._pos + bound + _BLOCK_SLACK > self._limit
            or self._records >= self.block_generations
            or self._clock() - self._opened_at >= self.block_seconds
        ):
            self._seal()
        if self._buffer is None:
            self._begin(frame.generation, bound)
        assert self._buffer is not None
        gen_delta = (
            0 if self._records == 0 else frame.generation - self._last_generation
        )
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
        self._records += 1
        self._rows += rows
        self._last_generation = frame.generation
        self._latest = frame.generation

    def flush(self) -> None:
        """Seal and write the open block, if there is one (a commit point)."""
        if self._buffer is not None and self._records:
            self._seal()

    def close(self) -> None:
        """Write the open block and close the file; safe to call twice."""
        if self._closed:
            return
        try:
            self.flush()
        finally:
            self._closed = True
            os.close(self._fd)

    def _begin(self, generation: int, bound: int) -> None:
        """Open a new block whose first record is `generation`."""
        needed = BLOCK_HEADER_SIZE + bound + _BLOCK_SLACK + CRC_SIZE
        spare, self._spare = self._spare, None
        if spare is not None and len(spare) >= needed:
            buffer = spare
        else:
            size = max(
                self.buffer_bytes, 2 * needed if needed > self.buffer_bytes else 0
            )
            buffer = np.zeros(size, np.uint8)
        self._buffer = buffer
        self._limit = len(buffer) - CRC_SIZE
        self._pos = BLOCK_HEADER_SIZE
        self._records = 0
        self._rows = 0
        self._first_generation = generation
        self._opened_at = self._clock()

    def _seal(self) -> None:
        """Checksum the open block and write it; it is committed on return."""
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
        crc = zlib.crc32(memoryview(buffer)[: self._pos], self.chain_crc)
        _CRC.pack_into(buffer, self._pos, crc)
        total = self._pos + CRC_SIZE
        self._write_all(memoryview(buffer)[:total])
        self.chain_crc = crc
        self.file_position += total
        self.committed_generation = self._last_generation
        self.generations_written += self._records
        self.rows_written += self._rows
        self.blocks_written += 1
        # The write was synchronous, so the buffer can serve the next block.
        self._spare = buffer
        self._buffer = None
        self._records = 0

    def _write_all(self, data: bytes | memoryview) -> None:
        """Write every byte of `data`, retrying short writes."""
        view = memoryview(data)
        done = 0
        while done < len(view):
            done += os.write(self._fd, view[done:])


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
                        while True:
                            end, _rows, _kind, gen_delta = codec.apply_record(
                                buffer,
                                position,
                                layout.demes,
                                layout.loci,
                                sizes,
                                state.pn,
                                state.pid,
                                state.pc,
                                state.pf,
                            )
                            if end >= 0:
                                break
                            state.grow()
                        position = int(end)
                        if index:
                            generation += int(gen_delta)
                        counts, ids, frequencies = state.frame_arrays()
                        yield TrajectoryFrame(
                            generation=generation,
                            counts=counts,
                            allele_ids=ids,
                            frequencies=frequencies,
                        )
            finally:
                del buffer
