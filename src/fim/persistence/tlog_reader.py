"""Random access to a binary trajectory log.

`LogReader` answers "what did generation G look like?" without reading the
whole file. It scans the block headers once (verifying the chained
checksums), then rebuilds any generation by starting at the nearest keyframe
block at or before it and applying the records forward: at most `key_every`
records for a sparse log, one for a dense one. The scrubber, the history
graphs, the animation and re-analysis all read through it.

Following a run in progress is cheap: a process-wide cache keeps the last
scan of each log, and a new reader (or `refresh`) resumes scanning at the
end of the committed prefix, so each poll costs only the blocks written since
the last. Only committed blocks are ever visible, so a reader never sees half
a generation.

A reader holds the file open and mapped; `close` (or a `with` block)
releases both.
"""

from __future__ import annotations

import bisect
import mmap
import os
import threading
from collections import OrderedDict
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from types import TracebackType
from typing import Final

import numpy as np

from fim.persistence import tlog_codec as codec
from fim.persistence.frame import FrameLayout, TrajectoryFrame, frame_to_rows
from fim.persistence.store import TrajectoryRow
from fim.persistence.tlog import (
    BLOCK_HEADER_SIZE,
    BlockInfo,
    ScanResult,
    TlogError,
    prefix_still_holds,
    scan_blocks,
)

_CACHE_ENTRIES: Final = 8
_CACHE: OrderedDict[str, ScanResult] = OrderedDict()
_CACHE_LOCK = threading.Lock()


def _cached_scan(path: str) -> ScanResult | None:
    """Return the last scan recorded for `path`, if any."""
    with _CACHE_LOCK:
        scan = _CACHE.get(path)
        if scan is not None:
            _CACHE.move_to_end(path)
        return scan


def _remember_scan(path: str, scan: ScanResult) -> None:
    """Record `scan` as the latest for `path`, evicting the oldest entries."""
    with _CACHE_LOCK:
        _CACHE[path] = scan
        _CACHE.move_to_end(path)
        while len(_CACHE) > _CACHE_ENTRIES:
            _CACHE.popitem(last=False)


def forget_cached_scans() -> None:
    """Drop every cached scan (for tests, and after files are replaced)."""
    with _CACHE_LOCK:
        _CACHE.clear()


class LogReader:
    """Read-only, memory-mapped access to the committed generations of a log.

    Args:
        path: The log file.
        use_cache: Whether to resume from the process-wide cache of scans.

    Raises:
        FileNotFoundError: If the file does not exist.
        TlogError: If it is not a usable log.
    """

    def __init__(self, path: Path | str, *, use_cache: bool = True) -> None:
        """Open and map the file and scan its committed blocks."""
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(f"trajectory does not exist: {self.path}")
        self._key = os.path.realpath(self.path)
        self._use_cache = use_cache
        self._handle = self.path.open("rb")
        self._map: mmap.mmap | None = None
        self._buffer: np.ndarray | None = None
        self._scan: ScanResult | None = None
        self._first: list[int] = []
        self._key_blocks: list[int] = []
        try:
            self._remap()
            self._rescan()
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> LogReader:
        """Return this reader, for use as a context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release the file and its mapping."""
        self.close()

    @property
    def scan(self) -> ScanResult:
        """The scan of the committed prefix, as of the last refresh."""
        assert self._scan is not None
        return self._scan

    @property
    def run_id(self) -> str:
        """The run the log holds."""
        return self.scan.header.run_id

    @property
    def layout(self) -> FrameLayout:
        """The frame layout of the log."""
        return self.scan.header.layout

    @property
    def blocks(self) -> list[BlockInfo]:
        """The committed blocks."""
        return self.scan.blocks

    @property
    def generation_count(self) -> int:
        """Generations (records) committed."""
        return self.scan.generations

    @property
    def rows(self) -> int:
        """Trajectory rows committed."""
        return self.scan.rows

    @property
    def last_generation(self) -> int:
        """The last committed generation, or `-1` for none."""
        return self.scan.last_generation

    def refresh(self) -> int:
        """Pick up blocks committed since the last scan.

        Returns:
            The number of generations committed now.
        """
        self._remap()
        self._rescan()
        return self.generation_count

    def close(self) -> None:
        """Release the mapping and the file; safe to call twice."""
        self._buffer = None
        if self._map is not None:
            self._map.close()
            self._map = None
        self._handle.close()

    def generation_numbers(self) -> list[int]:
        """Return every recorded generation number, in order.

        A block whose records are consecutive (the usual case) is expanded
        without looking at it; a block with gaps (a thinned run) has its
        record headers read, which costs a few operations per record.
        """
        numbers: list[int] = []
        buffer = self._require_buffer()
        for block in self.blocks:
            span = block.last_generation - block.first_generation + 1
            if span == block.n_records:
                numbers.extend(range(block.first_generation, block.last_generation + 1))
                continue
            deltas = np.zeros(block.n_records, np.int64)
            codec.record_deltas(
                buffer, block.offset + BLOCK_HEADER_SIZE, block.n_records, deltas
            )
            current = block.first_generation
            numbers.append(current)
            for delta in deltas[1:].tolist():
                current += int(delta)
                numbers.append(current)
        return numbers

    def frame_at(self, generation: int) -> TrajectoryFrame:
        """Rebuild one generation as a frame.

        Starts at the nearest keyframe block at or before the generation and
        applies the records forward.

        Args:
            generation: A recorded generation number.

        Returns:
            The frame.

        Raises:
            KeyError: If the generation was not recorded (never reached, or
                thinned away).
        """
        for frame in self.frames([generation]):
            return frame
        raise KeyError(generation)

    def frames(
        self,
        generations: Iterable[int] | None = None,
        *,
        skip_missing: bool = False,
    ) -> Iterator[TrajectoryFrame]:
        """Yield frames for the given generations (default: all), in order.

        A single forward pass: the decoder state carries across the targets,
        and jumps to a nearer keyframe block when a target is far ahead, so
        asking for a few spread-out generations costs a few short replays.

        Args:
            generations: The generations wanted; duplicates are ignored and
                the order given does not matter.
            skip_missing: Leave out a generation that was not recorded
                instead of raising.

        Yields:
            The frames of the recorded generations among those asked for,
            ascending.

        Raises:
            KeyError: If a requested generation was not recorded and
                `skip_missing` is false.
        """
        if generations is None:
            yield from self._all_frames()
            return
        wanted = sorted(set(generations))
        buffer = self._require_buffer()
        layout = self.layout
        sizes = np.asarray(layout.deme_sizes, dtype=np.int64)
        state = codec.DecodedState(layout.pairs)
        block_index = -1  # index of the block `position` is in
        position = 0
        record_in_block = 0
        generation = -1
        for target in wanted:
            index = self._block_containing(target)
            if index is None:
                if skip_missing:
                    continue
                raise KeyError(target)
            key = self._key_block_at_or_before(index)
            need_jump = (
                block_index < 0
                or key > block_index
                or (index == block_index and target <= generation)
                or index < block_index
            )
            if need_jump:
                block_index = key
                position = self.blocks[key].offset + BLOCK_HEADER_SIZE
                record_in_block = 0
                generation = self.blocks[key].first_generation
            # An earlier skipped target may have overshot onto this one: the
            # state then already is this generation.
            found = (not need_jump) and generation == target
            while not found:
                block = self.blocks[block_index]
                if record_in_block >= block.n_records:
                    block_index += 1
                    if block_index >= len(self.blocks):
                        break
                    block = self.blocks[block_index]
                    position = block.offset + BLOCK_HEADER_SIZE
                    record_in_block = 0
                position, _rows, _kind, gen_delta = codec.decode_record(
                    buffer, position, layout.demes, layout.loci, sizes, state
                )
                generation = (
                    block.first_generation
                    if record_in_block == 0
                    else generation + gen_delta
                )
                record_in_block += 1
                if generation == target:
                    found = True
                    break
                if generation > target:
                    break
            if not found:
                if skip_missing:
                    block_index = min(block_index, len(self.blocks) - 1)
                    continue
                raise KeyError(target)
            counts, ids, freq = state.frame_arrays()
            yield TrajectoryFrame(
                generation=generation, counts=counts, allele_ids=ids, frequencies=freq
            )

    def rows_at(self, generation: int) -> list[TrajectoryRow]:
        """Return one generation's trajectory rows.

        Raises:
            KeyError: If the generation was not recorded.
        """
        return frame_to_rows(
            self.frame_at(generation), self.layout, self.run_id, validate=False
        )

    def _all_frames(self) -> Iterator[TrajectoryFrame]:
        """Yield every committed generation, decoding each record once."""
        buffer = self._require_buffer()
        layout = self.layout
        sizes = np.asarray(layout.deme_sizes, dtype=np.int64)
        state = codec.DecodedState(layout.pairs)
        for block in self.blocks:
            position = block.offset + BLOCK_HEADER_SIZE
            generation = block.first_generation
            for index in range(block.n_records):
                position, _rows, _kind, gen_delta = codec.decode_record(
                    buffer, position, layout.demes, layout.loci, sizes, state
                )
                if index:
                    generation += gen_delta
                counts, ids, freq = state.frame_arrays()
                yield TrajectoryFrame(
                    generation=generation,
                    counts=counts,
                    allele_ids=ids,
                    frequencies=freq,
                )

    def _block_containing(self, generation: int) -> int | None:
        """Return the index of the block whose range holds `generation`."""
        index = bisect.bisect_right(self._first, generation) - 1
        if index < 0:
            return None
        if generation > self.blocks[index].last_generation:
            return None
        return index

    def _key_block_at_or_before(self, index: int) -> int:
        """Return the nearest keyframe block at or before block `index`."""
        position = bisect.bisect_right(self._key_blocks, index) - 1
        if position < 0:
            raise TlogError("the log has no keyframe before this block")
        return self._key_blocks[position]

    def _require_buffer(self) -> np.ndarray:
        """Return the mapped bytes, or raise if the reader was closed."""
        if self._buffer is None:
            raise ValueError("the log reader is closed")
        return self._buffer

    def _remap(self) -> None:
        """(Re)map the file at its current size."""
        self._buffer = None
        if self._map is not None:
            self._map.close()
            self._map = None
        if os.fstat(self._handle.fileno()).st_size == 0:
            raise TlogError("trajectory log is empty")
        self._map = mmap.mmap(self._handle.fileno(), 0, access=mmap.ACCESS_READ)
        self._buffer = np.frombuffer(self._map, dtype=np.uint8)

    def _rescan(self) -> None:
        """Scan the committed blocks, resuming from the best earlier scan."""
        assert self._map is not None
        resume: ScanResult | None = None
        for candidate in (
            self._scan,
            _cached_scan(self._key) if self._use_cache else None,
        ):
            if (
                candidate is not None
                and (resume is None or candidate.valid_end > resume.valid_end)
                and prefix_still_holds(self._map, candidate)
            ):
                resume = candidate
        self._scan = scan_blocks(self._map, resume=resume)
        if self._use_cache:
            _remember_scan(self._key, self._scan)
        self._first = [block.first_generation for block in self._scan.blocks]
        self._key_blocks = [i for i, b in enumerate(self._scan.blocks) if b.is_key]


def read_rows(
    path: Path | str, run_id: str, generations: Sequence[int] | None = None
) -> dict[int, list[TrajectoryRow]]:
    """Return a log's rows grouped by generation.

    Args:
        path: The log file.
        run_id: The run the rows must belong to (another run has none here).
        generations: Only these generations; default every committed one. A
            generation the log does not hold is simply absent from the result.

    Returns:
        `{generation: rows}`; a log of another run gives `{}`.

    Raises:
        FileNotFoundError: If the file does not exist.
        TlogError: If the file is not a usable log.
    """
    with LogReader(path) as reader:
        if reader.run_id != run_id:
            return {}
        layout = reader.layout
        return {
            frame.generation: frame_to_rows(frame, layout, run_id, validate=False)
            for frame in reader.frames(generations, skip_missing=True)
        }
