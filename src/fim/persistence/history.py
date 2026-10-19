"""A run's per-generation statistic history, in either of its two formats.

Each replicate keeps one row per generation: `{"generation": N, "D": ...,
"H_S": ..., ...}`. The **exact** format (`convergence.jsonl`) writes every
value at full precision as JSON lines, about 70 bytes a generation. The
**compact** format (`convergence.fhist`) rounds each value to a fixed-point
step and stores the differences between successive generations, packed to the
smallest integer width and compressed with zlib: on a real 355,000-generation
replicate it takes 0.3 MB where the JSON takes 25 MB.

Why rounding is safe: a run is asked for answers within `precision` (a
confidence-interval half width), so values stored to about 1% of `precision`
change no conclusion the history is used for. The step is a power of two
(`2**-fractional_bits`), so a decoded value is exactly representable and the
same file always decodes to the same numbers. `report.json` and `summary.json`
are computed inside the run and are never rounded.

This module is the one place that finds a run's replicate folders, finds a
history file, and reads or writes it, so a reader that only wants the history
does not need to know how it was stored.
"""

from __future__ import annotations

import json
import math
import struct
import zlib
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import numpy as np

HISTORY_FILENAME: Final = "convergence.jsonl"
"""The exact statistic history of one replicate or one scalar run."""

COMPACT_FILENAME: Final = "convergence.fhist"
"""The compact statistic history of one replicate or one scalar run."""

HISTORY_FILENAMES: Final = (HISTORY_FILENAME, COMPACT_FILENAME)
"""Every name a history file may have, exact first."""

_MAGIC: Final = b"FIMHIST1"
_LENGTH: Final = struct.Struct("<I")
_PRECISION_STEPS: Final = 100.0
"""A value is stored to within one hundredth of the run's precision."""
_GUARD_BITS: Final = 1
_MIN_BITS: Final = 1
_MAX_BITS: Final = 40
_LIMIT: Final = float(2**61)
"""Largest scaled magnitude a fixed-point value may have."""

HistoryRow = tuple[int, dict[str, float | None]]
HistoryColumns = tuple[list[int], dict[str, list[float]]]


class HistoryError(ValueError):
    """A compact history file is damaged, or a history cannot be compacted."""


def replicate_directories(run_directory: Path) -> list[Path]:
    """Return the directories that each hold one replicate's files.

    Args:
        run_directory: A run directory: a batch (which holds
            `replicate-NNN` folders) or a scalar run (which holds its own
            files).

    Returns:
        The replicate folders in order, or `[run_directory]` for a scalar
        run.
    """
    found = sorted(
        path
        for path in run_directory.glob("replicate-*")
        if (path / "manifest.json").is_file()
    )
    return found or [run_directory]


def history_path(replicate_directory: Path) -> Path:
    """Return where a replicate's statistic history lives.

    The compact file if there is one, else the exact path (which may not
    exist: a run kept without a history, or one that predates it).
    """
    compact = replicate_directory / COMPACT_FILENAME
    if compact.is_file():
        return compact
    return replicate_directory / HISTORY_FILENAME


def fractional_bits(precision: float) -> int:
    """Return the fixed-point fractional bits for a run's `precision`.

    The step `2**-bits` is at most `precision / 100`, with one guard bit, so a
    stored value is within about 0.5% of the precision of the value computed.

    Args:
        precision: The run's smallest precision (a confidence half width).

    Returns:
        The number of fractional bits, between 1 and 40.
    """
    if not precision > 0 or not math.isfinite(precision):
        return _MAX_BITS
    bits = math.ceil(math.log2(_PRECISION_STEPS / precision)) + _GUARD_BITS
    return max(_MIN_BITS, min(_MAX_BITS, bits))


def iter_history_rows(path: Path) -> Iterator[HistoryRow]:
    """Stream a history file's rows in generation order.

    Args:
        path: A history file in either format (see `history_path`).

    Yields:
        `(generation, {statistic: value})` for each row; a statistic that is
        undefined at a generation is `None` (exact) or `nan` (compact).

    Raises:
        ValueError: A row is malformed, or a compact file is damaged.
        OSError: The file cannot be read.
    """
    if path.suffix == Path(COMPACT_FILENAME).suffix:
        generations, columns = read_compact_columns(path)
        names = list(columns)
        for index, generation in enumerate(generations):
            yield generation, {name: columns[name][index] for name in names}
        return
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            text = raw.strip()
            if not text:
                continue
            row = json.loads(text)
            found = row.get("generation") if isinstance(row, dict) else None
            if not isinstance(found, int) or isinstance(found, bool):
                raise ValueError(
                    f"{path.name}: a row must be an object with an integer 'generation'"
                )
            yield (
                found,
                {name: value for name, value in row.items() if name != "generation"},
            )


def read_history_columns(path: Path) -> HistoryColumns:
    """Read a whole history as a generation list and one value list per statistic.

    Args:
        path: A history file in either format.

    Returns:
        `(generations, {statistic: values})`, each list as long as
        `generations`.

    Raises:
        ValueError: The file is malformed or damaged, or the statistics do
            not share one set of rows (an exact file whose rows differ).
    """
    if path.suffix == Path(COMPACT_FILENAME).suffix:
        return read_compact_columns(path)
    generations: list[int] = []
    columns: dict[str, list[float]] = {}
    for generation, values in iter_history_rows(path):
        generations.append(generation)
        for name, value in values.items():
            columns.setdefault(name, []).append(value)  # type: ignore[arg-type]
    return generations, columns


def sample_history(path: Path, grid: Sequence[int]) -> dict[str, list[float | None]]:
    """Return a history's values at the generations in `grid`.

    A compact file is decoded as arrays and indexed; an exact file is
    streamed once with memory the size of the grid. A generation the file
    lacks, or a statistic undefined there, is `None`.

    Args:
        path: A history file in either format.
        grid: Ascending generations to sample.
    """
    if path.suffix == Path(COMPACT_FILENAME).suffix:
        return _sample_compact(path, grid)
    samples: dict[str, list[float | None]] = {}
    position = 0
    for generation, values in iter_history_rows(path):
        while position < len(grid) and grid[position] < generation:
            position += 1
        if position >= len(grid):
            break
        if grid[position] != generation:
            continue
        for name, value in values.items():
            column = samples.setdefault(name, [None] * len(grid))
            column[position] = _finite(value)
    return samples


def write_compact_history(
    path: Path,
    generations: Sequence[int],
    columns: Mapping[str, Sequence[float | None]],
    *,
    bits: int,
) -> None:
    """Write a compact history.

    Each value becomes `round(value * 2**bits)`; the differences between
    successive generations are zigzag-encoded, packed to the narrowest
    unsigned width that holds them, and compressed. A value that is not a
    finite number is recorded as missing.

    Args:
        path: The file to write (replaced atomically).
        generations: The generation of each row, ascending.
        columns: One sequence of values per statistic, each as long as
            `generations`.
        bits: Fixed-point fractional bits (see `fractional_bits`).

    Raises:
        HistoryError: A column has the wrong length, or a value is too large
            to store at this precision (the caller falls back to the exact
            format).
    """
    count = len(generations)
    contiguous = (
        list(generations) == list(range(generations[0], generations[0] + count))
        if count
        else True
    )
    header: dict[str, Any] = {
        "rows": count,
        "bits": bits,
        "contiguous": contiguous,
        "first": generations[0] if count else 0,
        "columns": [],
    }
    blobs: list[bytes] = []
    if not contiguous:
        listed: dict[str, Any] = {}
        blobs.append(_pack(np.asarray(generations, dtype=np.int64), listed, None))
        header["generations_width"] = listed["width"]
    described: list[dict[str, Any]] = []
    for name, values in columns.items():
        if len(values) != count:
            raise HistoryError(
                f"statistic {name!r} has {len(values)} values, not {count}"
            )
        scaled = np.asarray(
            [math.nan if v is None else v for v in values], dtype=np.float64
        )
        missing = ~np.isfinite(scaled)
        scaled = np.where(missing, 0.0, scaled) * float(2**bits)
        if count and float(np.abs(scaled).max()) >= _LIMIT:
            raise HistoryError(
                f"statistic {name!r} is too large to store at {bits} fractional bits"
            )
        entry: dict[str, Any] = {"name": name}
        blob = _pack(np.rint(scaled).astype(np.int64), entry, np.flatnonzero(missing))
        described.append(entry)
        blobs.append(blob)
    header["columns"] = described
    encoded = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(_MAGIC)
        handle.write(_LENGTH.pack(len(encoded)))
        handle.write(encoded)
        for blob in blobs:
            handle.write(_LENGTH.pack(len(blob)))
            handle.write(blob)
    temporary.replace(path)


def read_compact_columns(path: Path) -> HistoryColumns:
    """Read a compact history as generations and one float list per statistic.

    Raises:
        HistoryError: The file is not a compact history, or is damaged.
        OSError: The file cannot be read.
    """
    header, blobs = _read_blobs(path)
    count = int(header["rows"])
    scale = 2.0 ** -int(header["bits"])
    cursor = 0
    if header["contiguous"]:
        first = int(header["first"])
        generations = list(range(first, first + count))
    else:
        generations = _unpack(
            blobs[cursor], count, {"width": header["generations_width"]}
        ).tolist()
        cursor += 1
    columns: dict[str, list[float]] = {}
    for entry in header["columns"]:
        quantized = _unpack(blobs[cursor], count, entry)
        cursor += 1
        values = quantized.astype(np.float64) * scale
        for index in entry.get("missing", []):
            values[index] = math.nan
        columns[str(entry["name"])] = values.tolist()
    return generations, columns


def _sample_compact(path: Path, grid: Sequence[int]) -> dict[str, list[float | None]]:
    """Index a decoded compact history at the generations in `grid`."""
    generations, columns = read_compact_columns(path)
    positions = np.searchsorted(np.asarray(generations), np.asarray(grid))
    found = {
        slot
        for slot, (position, wanted) in enumerate(zip(positions, grid, strict=True))
        if position < len(generations) and generations[position] == wanted
    }
    samples: dict[str, list[float | None]] = {}
    for name, values in columns.items():
        samples[name] = [
            _finite(values[int(positions[slot])]) if slot in found else None
            for slot in range(len(grid))
        ]
    return samples


def _finite(value: object) -> float | None:
    """Return `value` as a float if it is a finite number, else `None`."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _pack(
    quantized: np.ndarray, entry: dict[str, Any] | None, missing: np.ndarray | None
) -> bytes:
    """Delta-code, zigzag, narrow and compress one column of integers."""
    steps = np.diff(quantized, prepend=0)
    zigzag = ((steps << 1) ^ (steps >> 63)).astype(np.uint64)
    top = int(zigzag.max()) if zigzag.size else 0
    width = 1 if top < 2**8 else 2 if top < 2**16 else 4 if top < 2**32 else 8
    packed = zigzag.astype(f"<u{width}").tobytes()
    if entry is not None:
        entry["width"] = width
        if missing is not None and missing.size:
            entry["missing"] = missing.tolist()
    return zlib.compress(packed, 6)


def _unpack(blob: bytes, count: int, entry: Mapping[str, Any] | None) -> np.ndarray:
    """Invert `_pack`: decompress, widen, un-zigzag and sum the differences."""
    width = int(entry["width"]) if entry is not None else 8
    try:
        raw = zlib.decompress(blob)
    except zlib.error as error:
        raise HistoryError(f"compact history column is damaged: {error}") from error
    if len(raw) != count * width:
        raise HistoryError("compact history column has the wrong length")
    zigzag = np.frombuffer(raw, dtype=f"<u{width}").astype(np.uint64)
    steps = (zigzag >> np.uint64(1)).astype(np.int64) ^ -(zigzag & np.uint64(1)).astype(
        np.int64
    )
    return np.cumsum(steps, dtype=np.int64)


def _read_blobs(path: Path) -> tuple[dict[str, Any], list[bytes]]:
    """Read a compact file's header and its column blobs."""
    data = path.read_bytes()
    if not data.startswith(_MAGIC):
        raise HistoryError(f"{path.name} is not a compact history")
    position = len(_MAGIC)
    try:
        (length,) = _LENGTH.unpack_from(data, position)
        position += _LENGTH.size
        header = json.loads(data[position : position + length])
        position += length
        blobs = []
        while position < len(data):
            (size,) = _LENGTH.unpack_from(data, position)
            position += _LENGTH.size
            blobs.append(data[position : position + size])
            if len(blobs[-1]) != size:
                raise HistoryError(f"{path.name} is truncated")
            position += size
    except (struct.error, ValueError) as error:
        raise HistoryError(f"{path.name} is damaged: {error}") from error
    expected = len(header["columns"]) + (0 if header["contiguous"] else 1)
    if len(blobs) != expected:
        raise HistoryError(f"{path.name} is missing columns")
    return header, blobs
