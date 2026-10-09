"""Derive the canonical `trajectory.jsonl` from a binary trajectory log.

The log is what a run stores; the JSON Lines file is something a person (or
a spreadsheet, or `jq`) asks for. This module turns the one into the other.
Every line is byte for byte what

```python
json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\\n"
```

gives for a trajectory row, that is

```text
{"allele_id":A,"deme":D,"frequency":F,"generation":G,"locus_id":L,"run_id":"R"}
```

so the file is canonical: the same log always yields the same bytes and the
same SHA-256.

How it stays exact and fast:

- Integers are formatted here, digit by digit (exact by construction).
- A float's text is never produced by a reimplementation. For a count-coded
  frequency `c / size` the text comes from a table built once per deme size
  with Python's own `repr`; any other frequency (a raw pair, or a size too
  large to tabulate) is formatted by a Python path that calls
  `float.__repr__`, the very function `json` calls.
- A *size pass* computes the exact byte length of a shard without copying a
  byte, so shards of the log (each starting at a keyframe block) can be
  formatted by separate processes and written to their final offsets in one
  pre-sized file with `pwrite`; no concatenation is needed.
- The SHA-256 is of the whole file. A single-process derivation hashes the
  bytes as they are written; a sharded one hashes the finished file in one
  sequential pass.

The compiled kernels (`format_pairs`, `size_pairs`) come from the same
optional-Numba decorator as the codec, so without Numba the module runs, only
more slowly.
"""

from __future__ import annotations

import hashlib
import json.encoder
import mmap
import os
import shutil
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from multiprocessing import get_context
from pathlib import Path
from typing import Final

import numpy as np

from fim.persistence import tlog_codec as codec
from fim.persistence.tlog import (
    BLOCK_HEADER_SIZE,
    BlockInfo,
    ScanResult,
    scan_blocks,
)

LUT_MAX_SIZE: Final = 1 << 20
"""Largest deme size whose float text is tabulated (others use Python)."""

_CHUNK_BYTES: Final = 16 * 1024 * 1024
_MAX_FLOAT_TEXT: Final = 25
_MAX_INT_TEXT: Final = 20
_GENERATION_FIELD: Final = ',"generation":'
_DEFAULT_WORKERS: Final = 4
_MIN_BLOCKS_PER_SHARD: Final = 1
_SHARD_TARGET_BLOCKS: Final = 64


@codec.kernel
def _copy(out, pos, src, off, n):
    """Copy `n` bytes of `src` from `off` into `out` at `pos`."""
    for i in range(n):
        out[pos + i] = src[off + i]
    return pos + n


@codec.kernel
def _decimal(out, pos, v):
    """Write the non-negative integer `v` in decimal; return the new position."""
    if v < 10:
        out[pos] = np.uint8(48 + v)
        return pos + 1
    n = 0
    t = v
    while t > 0:
        n += 1
        t //= 10
    end = pos + n
    q = end
    while v > 0:
        q -= 1
        out[q] = np.uint8(48 + v % 10)
        v //= 10
    return end


@codec.kernel
def format_pairs(
    out,
    opos,
    p_start,
    loci,
    demes,
    pn,
    pid,
    pc,
    consts,
    head_len,
    deme_off,
    deme_len,
    suf_off,
    suf_len,
    gen_bytes,
    lut_arena,
    lut_off,
    lut_len,
    lut_base,
    lut_size,
):
    """Format pairs `p_start..` into `out`, stopping at the first one needing Python.

    Returns:
        `(status, pair, new position)`. Status 0 means every pair was
        done; status 1 means pair `pair` has a raw or untabulated frequency
        and nothing of it was written.
    """
    pairs = loci * demes
    ngen = gen_bytes.shape[0]
    for p in range(p_start, pairs):
        n = pn[p]
        d = p // loci
        lcl = p - d * loci
        base = lut_base[d]
        lsz = lut_size[d]
        for k in range(n):
            c = pc[p, k]
            if c < 0 or c > lsz:
                return 1, p, opos
        for k in range(n):
            opos = _copy(out, opos, consts, 0, head_len)
            opos = _decimal(out, opos, pid[p, k])
            opos = _copy(out, opos, consts, deme_off[d], deme_len[d])
            idx = base + pc[p, k]
            opos = _copy(out, opos, lut_arena, lut_off[idx], lut_len[idx])
            opos = _copy(out, opos, gen_bytes, 0, ngen)
            opos = _copy(out, opos, consts, suf_off[lcl], suf_len[lcl])
    return 0, pairs, opos


@codec.kernel
def size_pairs(
    p_start,
    loci,
    demes,
    pn,
    pid,
    pc,
    head_len,
    deme_len,
    suf_len,
    gen_len,
    lut_len,
    lut_base,
    lut_size,
):
    """Byte count of pairs `p_start..` (same walk as `format_pairs`, no copying).

    Returns:
        `(status, pair, bytes)` with the same stop rule as `format_pairs`.
    """
    pairs = loci * demes
    total = 0
    for p in range(p_start, pairs):
        n = pn[p]
        d = p // loci
        lcl = p - d * loci
        base = lut_base[d]
        lsz = lut_size[d]
        for k in range(n):
            c = pc[p, k]
            if c < 0 or c > lsz:
                return 1, p, total
        for k in range(n):
            v = pid[p, k]
            nd = 1
            while v >= 10:
                nd += 1
                v //= 10
            total += (
                head_len
                + nd
                + deme_len[d]
                + lut_len[base + pc[p, k]]
                + gen_len
                + suf_len[lcl]
            )
    return 0, pairs, total


class JsonlFormatter:
    """The constant tables that turn decoded generations into canonical lines.

    Built once per log (it depends on the run id, the locus identifiers and
    the deme sizes only).

    Args:
        run_id: The run the rows belong to.
        locus_ids: Locus identifiers in layout order.
        deme_sizes: Gene copies per deme (`0` where unknown).
    """

    def __init__(
        self, run_id: str, locus_ids: tuple[int, ...], deme_sizes: tuple[int, ...]
    ) -> None:
        """Build the constant fragments and the float text tables."""
        self.run_id = run_id
        self.locus_ids = tuple(int(x) for x in locus_ids)
        self.sizes = tuple(int(s) for s in deme_sizes)
        self.loci = len(self.locus_ids)
        self.demes = len(self.sizes)
        encoded_run_id = json.encoder.encode_basestring_ascii(run_id)
        fragments: list[bytes] = []
        cursor = 0

        def add(text: bytes) -> tuple[int, int]:
            """Append a fragment; return its offset and length."""
            nonlocal cursor
            fragments.append(text)
            place = (cursor, len(text))
            cursor += len(text)
            return place

        head = add(b'{"allele_id":')
        self.head_len = head[1]
        deme_parts = [
            add(f',"deme":{d + 1},"frequency":'.encode("ascii"))
            for d in range(self.demes)
        ]
        locus_parts = [
            add(f',"locus_id":{lid},"run_id":{encoded_run_id}}}\n'.encode("ascii"))
            for lid in self.locus_ids
        ]
        self.consts = np.frombuffer(b"".join(fragments), dtype=np.uint8).copy()
        self.deme_off = np.array([x[0] for x in deme_parts], np.int64)
        self.deme_len = np.array([x[1] for x in deme_parts], np.int64)
        self.suf_off = np.array([x[0] for x in locus_parts], np.int64)
        self.suf_len = np.array([x[1] for x in locus_parts], np.int64)
        self._build_float_tables()
        self._row_bound = (
            self.head_len
            + _MAX_INT_TEXT
            + int(self.deme_len.max())
            + _MAX_FLOAT_TEXT
            + len(_GENERATION_FIELD)
            + _MAX_INT_TEXT
            + int(self.suf_len.max())
        )

    def _build_float_tables(self) -> None:
        """Build one `repr(c / size)` table per distinct deme size."""
        tables: dict[int, int] = {}
        chunks: list[bytes] = []
        offsets: list[int] = []
        lengths: list[int] = []
        base = np.zeros(self.demes, np.int64)
        limit = np.zeros(self.demes, np.int64)
        cursor = 0
        total = 0
        for d, size in enumerate(self.sizes):
            if size <= 0 or size > LUT_MAX_SIZE:
                base[d] = 0
                limit[d] = -1  # nothing tabulated: every count goes to Python
                continue
            if size not in tables:
                tables[size] = total
                for c in range(size + 1):
                    text = repr(c / size).encode("ascii") if c > 0 else b""
                    chunks.append(text)
                    offsets.append(cursor)
                    lengths.append(len(text))
                    cursor += len(text)
                total += size + 1
            base[d] = tables[size]
            limit[d] = size
        self.lut_arena = np.frombuffer(b"".join(chunks) or b"\0", dtype=np.uint8).copy()
        self.lut_off = np.array(offsets or [0], np.int64)
        self.lut_len = np.array(lengths or [0], np.int64)
        self.lut_base = base
        self.lut_size = limit

    @staticmethod
    def generation_bytes(generation: int) -> np.ndarray:
        """Return the per-generation constant text `,"generation":G`."""
        return np.frombuffer(
            f"{_GENERATION_FIELD}{generation}".encode("ascii"), dtype=np.uint8
        )

    def row_bound(self, rows: int) -> int:
        """Return an upper bound on the bytes of a generation of `rows` rows."""
        return rows * self._row_bound + 64

    def format_generation(
        self, out: np.ndarray, opos: int, generation: int, state: codec.DecodedState
    ) -> int:
        """Append one generation's lines to `out` and return the new offset."""
        gen = self.generation_bytes(generation)
        pair = 0
        while True:
            status, pair, opos = format_pairs(
                out,
                opos,
                pair,
                self.loci,
                self.demes,
                state.pn,
                state.pid,
                state.pc,
                self.consts,
                self.head_len,
                self.deme_off,
                self.deme_len,
                self.suf_off,
                self.suf_len,
                gen,
                self.lut_arena,
                self.lut_off,
                self.lut_len,
                self.lut_base,
                self.lut_size,
            )
            if status == 0:
                return int(opos)
            opos = self._python_pair(out, int(opos), pair, generation, state)
            pair += 1

    def size_generation(self, generation: int, state: codec.DecodedState) -> int:
        """Return the exact bytes `format_generation` would write."""
        gen_len = len(self.generation_bytes(generation))
        pair = 0
        total = 0
        scratch = np.zeros(1 << 16, np.uint8)
        while True:
            status, pair, nbytes = size_pairs(
                pair,
                self.loci,
                self.demes,
                state.pn,
                state.pid,
                state.pc,
                self.head_len,
                self.deme_len,
                self.suf_len,
                gen_len,
                self.lut_len,
                self.lut_base,
                self.lut_size,
            )
            total += int(nbytes)
            if status == 0:
                return total
            if int(state.pn[pair]) * self._row_bound > len(scratch):
                scratch = np.zeros(int(state.pn[pair]) * self._row_bound, np.uint8)
            total += self._python_pair(scratch, 0, pair, generation, state)
            pair += 1

    def _python_pair(
        self,
        out: np.ndarray,
        opos: int,
        pair: int,
        generation: int,
        state: codec.DecodedState,
    ) -> int:
        """Format one pair with Python's own float text (the slow path)."""
        deme, locus = divmod(pair, self.loci)
        encoded_run_id = json.encoder.encode_basestring_ascii(self.run_id)
        locus_id = self.locus_ids[locus]
        text = "".join(
            f'{{"allele_id":{int(state.pid[pair, k])},"deme":{deme + 1},'
            f'"frequency":{float.__repr__(float(state.pf[pair, k]))},'
            f'"generation":{generation},"locus_id":{locus_id},'
            f'"run_id":{encoded_run_id}}}\n'
            for k in range(int(state.pn[pair]))
        )
        data = text.encode("ascii")
        out[opos : opos + len(data)] = np.frombuffer(data, dtype=np.uint8)
        return opos + len(data)


def _formatter_for(scan: ScanResult) -> JsonlFormatter:
    """Build the formatter of a scanned log."""
    header = scan.header
    return JsonlFormatter(
        header.run_id, header.layout.locus_ids, header.layout.deme_sizes
    )


def _apply(
    buffer: np.ndarray,
    position: int,
    scan: ScanResult,
    sizes: np.ndarray,
    state: codec.DecodedState,
) -> tuple[int, int, int]:
    """Apply one record to `state`, widening it as needed.

    Returns:
        `(end position, rows, generation delta)`.
    """
    layout = scan.header.layout
    while True:
        end, rows, _kind, gen_delta = codec.apply_record(
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
            return int(end), int(rows), int(gen_delta)
        state.grow()


def _walk(
    data: mmap.mmap,
    scan: ScanResult,
    blocks: list[BlockInfo],
    visit: Callable[[int, int, codec.DecodedState], None],
) -> None:
    """Decode `blocks` (the first must be a keyframe block) and call `visit`.

    Args:
        data: The mapped log.
        scan: Its scan.
        blocks: A run of consecutive committed blocks.
        visit: Called as `visit(generation, rows, state)` per generation.
    """
    layout = scan.header.layout
    sizes = np.asarray(layout.deme_sizes, dtype=np.int64)
    state = codec.DecodedState(layout.pairs)
    buffer = np.frombuffer(data, dtype=np.uint8)
    try:
        for block in blocks:
            position = block.offset + BLOCK_HEADER_SIZE
            generation = block.first_generation
            for index in range(block.n_records):
                position, rows, gen_delta = _apply(buffer, position, scan, sizes, state)
                if index:
                    generation += gen_delta
                visit(generation, rows, state)
    finally:
        del buffer


def plan_shards(scan: ScanResult, blocks_per_shard: int) -> list[tuple[int, int]]:
    """Split a log's blocks into shards that each start at a keyframe block.

    Args:
        scan: The scanned log.
        blocks_per_shard: About how many blocks a shard should hold.

    Returns:
        `(first, stop)` block index pairs covering every block.
    """
    keys = [i for i, block in enumerate(scan.blocks) if block.is_key]
    if not scan.blocks:
        return []
    if not keys or keys[0] != 0:
        raise ValueError("the first block of a log must open with a keyframe")
    step = max(1, blocks_per_shard)
    shards: list[tuple[int, int]] = []
    for start in range(0, len(keys), step):
        stop = keys[start + step] if start + step < len(keys) else len(scan.blocks)
        shards.append((keys[start], stop))
    return shards


def _open_log(path: Path) -> tuple[mmap.mmap, ScanResult]:
    """Map a log read-only and scan it."""
    handle = path.open("rb")
    try:
        data = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
    finally:
        handle.close()
    return data, scan_blocks(data)


def _shard_bytes(path: str, first: int, stop: int) -> int:
    """Worker: the exact JSONL byte length of blocks `first..stop` of a log."""
    data, scan = _open_log(Path(path))
    try:
        fmt = _formatter_for(scan)
        total = 0

        def visit(generation: int, rows: int, state: codec.DecodedState) -> None:
            """Add one generation's byte count."""
            nonlocal total
            del rows
            total += fmt.size_generation(generation, state)

        _walk(data, scan, scan.blocks[first:stop], visit)
        return total
    finally:
        data.close()


class _Emitter:
    """Format generations into a chunk buffer and hand full chunks to a sink."""

    def __init__(
        self, formatter: JsonlFormatter, sink: Callable[[memoryview], None]
    ) -> None:
        """Start with an empty chunk."""
        self.formatter = formatter
        self.sink = sink
        self.buffer = np.zeros(_CHUNK_BYTES + (4 << 20), np.uint8)
        self.position = 0
        self.bytes = 0
        self.generations = 0
        self.rows = 0

    def visit(self, generation: int, rows: int, state: codec.DecodedState) -> None:
        """Format one generation, flushing the chunk when it is full."""
        bound = self.formatter.row_bound(rows)
        if self.position + bound > len(self.buffer):
            self.flush()
            if bound > len(self.buffer):
                self.buffer = np.zeros(bound * 2, np.uint8)
        self.position = self.formatter.format_generation(
            self.buffer, self.position, generation, state
        )
        if self.position > _CHUNK_BYTES:
            self.flush()
        self.generations += 1
        self.rows += rows

    def flush(self) -> None:
        """Hand the chunk to the sink and start a new one."""
        if self.position:
            self.sink(memoryview(self.buffer)[: self.position])
            self.bytes += self.position
            self.position = 0


def _emit(
    data: mmap.mmap,
    scan: ScanResult,
    blocks: list[BlockInfo],
    sink: Callable[[memoryview], None],
) -> tuple[int, int, int]:
    """Format `blocks` through `sink`; return `(bytes, generations, rows)`."""
    emitter = _Emitter(_formatter_for(scan), sink)
    _walk(data, scan, blocks, emitter.visit)
    emitter.flush()
    return emitter.bytes, emitter.generations, emitter.rows


def _shard_write(path: str, first: int, stop: int, out_path: str, offset: int) -> int:
    """Worker: format blocks `first..stop` and `pwrite` them at `offset`."""
    data, scan = _open_log(Path(path))
    fd = os.open(out_path, os.O_WRONLY | getattr(os, "O_BINARY", 0))
    try:
        cursor = offset

        def sink(view: memoryview) -> None:
            """Write one chunk at the shard's running offset."""
            nonlocal cursor
            done = 0
            while done < len(view):
                done += _pwrite(fd, view[done:], cursor + done)
            cursor += len(view)

        written, _gens, _rows = _emit(data, scan, scan.blocks[first:stop], sink)
        return written
    finally:
        os.close(fd)
        data.close()


def _pwrite(fd: int, view: memoryview, offset: int) -> int:
    """Write at an offset; fall back to seek and write where `pwrite` is missing."""
    if hasattr(os, "pwrite"):
        return os.pwrite(fd, view, offset)
    os.lseek(fd, offset, os.SEEK_SET)  # pragma: no cover - Windows
    return os.write(fd, view)  # pragma: no cover - Windows


@dataclass(frozen=True, slots=True)
class DerivedJsonl:
    """What deriving a trajectory produced.

    Args:
        path: The JSON Lines file, or `None` when it was only counted.
        sha256: The SHA-256 of the file's bytes, as hex.
        size: The file's byte count.
        generations: Generations written.
        rows: Rows (lines) written.
        run_id: The run the log holds.
    """

    path: Path | None
    sha256: str
    size: int
    generations: int
    rows: int
    run_id: str


def derive_jsonl(
    log_path: Path | str,
    out_path: Path | str | None = None,
    *,
    workers: int = 1,
    shard_blocks: int = _SHARD_TARGET_BLOCKS,
) -> DerivedJsonl:
    """Derive the canonical JSON Lines trajectory from a log.

    Args:
        log_path: The binary log.
        out_path: Where to write the JSON Lines file; `None` only computes
            its digest, size and counts (nothing is stored).
        workers: Processes to format with. `1` formats in this process and
            hashes as it writes; more split the log at keyframe blocks, size
            each shard, write each to its final offset, then hash the file.
        shard_blocks: About how many blocks one shard of a sharded
            derivation holds.

    Returns:
        The digest, size and counts of the derived file.

    Raises:
        FileNotFoundError: If the log does not exist.
        TlogError: If the log is unusable.
    """
    log = Path(log_path)
    if not log.is_file():
        raise FileNotFoundError(f"trajectory log does not exist: {log}")
    data, scan = _open_log(log)
    try:
        run_id = scan.header.run_id
        generations = scan.generations
        rows = scan.rows
        shards = plan_shards(scan, shard_blocks)
        if out_path is not None and workers > 1 and len(shards) > 1:
            digest, size = _derive_sharded(log, Path(out_path), shards, workers)
        else:
            digest, size = _derive_streaming(data, scan, out_path)
    finally:
        data.close()
    return DerivedJsonl(
        path=Path(out_path) if out_path is not None else None,
        sha256=digest,
        size=size,
        generations=generations,
        rows=rows,
        run_id=run_id,
    )


def _derive_streaming(
    data: mmap.mmap, scan: ScanResult, out_path: Path | str | None
) -> tuple[str, int]:
    """Format every block in this process, hashing as the bytes go out."""
    sha = hashlib.sha256()
    handle = None
    try:
        if out_path is not None:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            handle = Path(out_path).open("wb")  # noqa: SIM115 - closed in finally

        def sink(view: memoryview) -> None:
            """Hash a chunk and write it when there is a file."""
            sha.update(view)
            if handle is not None:
                handle.write(view)

        size, _gens, _rows = _emit(data, scan, scan.blocks, sink)
    finally:
        if handle is not None:
            handle.close()
    return sha.hexdigest(), size


def _derive_sharded(
    log: Path, out_path: Path, shards: list[tuple[int, int]], workers: int
) -> tuple[str, int]:
    """Size every shard in parallel, then write each to its offset, then hash."""
    context = get_context("spawn")
    with ProcessPoolExecutor(max_workers=workers, mp_context=context) as pool:
        size_futures = [pool.submit(_shard_bytes, str(log), a, b) for a, b in shards]
        sizes = [future.result() for future in size_futures]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        total = sum(sizes)
        with out_path.open("wb") as handle:
            handle.truncate(total)
        offsets = [sum(sizes[:i]) for i in range(len(sizes))]
        write_futures = [
            pool.submit(_shard_write, str(log), a, b, str(out_path), offset)
            for (a, b), offset in zip(shards, offsets, strict=True)
        ]
        written = [future.result() for future in write_futures]
    if written != sizes:
        raise RuntimeError("a shard wrote a different number of bytes than it sized")
    sha = hashlib.sha256()
    with out_path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_BYTES):
            sha.update(chunk)
    return sha.hexdigest(), total


def free_space_needed(log_path: Path | str) -> int:
    """Return the bytes of free space deriving this log's JSON Lines will need.

    Computed exactly by the size pass (about sixteen times faster than
    formatting), without writing anything.

    Args:
        log_path: The binary log.

    Returns:
        The size in bytes of the file `derive_jsonl` would write.

    Raises:
        FileNotFoundError: If the log does not exist.
        TlogError: If the log is unusable.
    """
    log = Path(log_path)
    if not log.is_file():
        raise FileNotFoundError(f"trajectory log does not exist: {log}")
    data, scan = _open_log(log)
    try:
        count = len(scan.blocks)
    finally:
        data.close()
    return _shard_bytes(str(log), 0, count) if count else 0


def check_free_space(log_path: Path | str, directory: Path | str) -> tuple[int, int]:
    """Return `(bytes needed, bytes free)` for deriving into `directory`.

    Args:
        log_path: The binary log.
        directory: Where the file would be written (it must exist).

    Returns:
        The exact size of the JSON Lines file and the free bytes there.
    """
    needed = free_space_needed(log_path)
    return needed, shutil.disk_usage(directory).free
