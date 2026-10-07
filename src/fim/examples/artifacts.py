"""Lossless, bounded-size storage of complete worked-example run artifacts."""

from __future__ import annotations

import gzip
import io
import json
import shutil
import tempfile
import zlib
from collections.abc import Buffer, Sequence
from pathlib import Path
from typing import BinaryIO, Final

from fim.persistence.manifest import hash_file

RUN_OUTPUT_NAMES: Final = (
    "manifest.json",
    "report.json",
    "trajectory.jsonl",
    "equilibrium_trajectory.jsonl",
    "convergence.jsonl",
    "sigma_band_trajectory.jsonl",
    "pairwise.json",
    "scatter.png",
)
TOP_LEVEL_OUTPUT_NAMES: Final = (*RUN_OUTPUT_NAMES, "summary.json")
PART_BYTES: Final = 50 * 1024 * 1024
_COPY_BYTES: Final = 1024 * 1024
_PART_DIGITS: Final = 4


def archive_target(name: str) -> str | None:
    """Return the raw artifact name for a recognized gzip part, or None."""
    for artifact in RUN_OUTPUT_NAMES:
        prefix = f"{artifact}.gz.part-"
        suffix = name.removeprefix(prefix)
        if (
            artifact.endswith(".jsonl")
            and name.startswith(prefix)
            and len(suffix) == _PART_DIGITS
            and suffix.isascii()
            and suffix.isdigit()
            and int(suffix) > 0
        ):
            return artifact
    return None


def output_files(directory: Path, *, batch: bool = True) -> list[Path]:
    """List recognized raw artifacts and archive parts, including replicates."""
    names = TOP_LEVEL_OUTPUT_NAMES if batch else RUN_OUTPUT_NAMES
    files = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and (path.name in names or archive_target(path.name))
    )
    if batch:
        for replicate in sorted(directory.glob("replicate-*")):
            if replicate.is_dir():
                files.extend(output_files(replicate, batch=False))
    return files


class _PartWriter(io.RawIOBase):
    """Split one gzip stream into files no larger than the requested limit."""

    def __init__(self, target: Path, part_bytes: int) -> None:
        super().__init__()
        self.target = target
        self.part_bytes = part_bytes
        self.paths: list[Path] = []
        self.stream: BinaryIO | None = None
        self.written = 0

    def write(self, data: Buffer) -> int:
        """Write compressed bytes, rotating files at the exact size limit."""
        remaining = memoryview(data).cast("B")
        length = len(remaining)
        while remaining:
            if self.stream is None or self.written == self.part_bytes:
                self._close_part()
                if len(self.paths) >= 10**_PART_DIGITS - 1:
                    raise ValueError("example archive exceeds its part-number limit")
                path = self.target.with_name(
                    f"{self.target.name}.gz.part-{len(self.paths) + 1:04d}"
                )
                self.stream = path.open("wb")
                self.paths.append(path)
                self.written = 0
            count = min(len(remaining), self.part_bytes - self.written)
            self.stream.write(remaining[:count])
            self.written += count
            remaining = remaining[count:]
        return length

    def close(self) -> None:
        """Close the current part, including when compression fails."""
        self._close_part()
        super().close()

    def _close_part(self) -> None:
        """Close a part without marking the entire gzip stream closed."""
        if self.stream is not None:
            self.stream.close()
            self.stream = None


class _PartReader(io.RawIOBase):
    """Read ordered parts as one stream without joining them in memory."""

    def __init__(self, parts: Sequence[Path]) -> None:
        super().__init__()
        self.parts = iter(parts)
        self.stream: io.BufferedReader | None = None

    def readable(self) -> bool:
        """Tell BufferedReader that this stream supports reads."""
        return True

    def readinto(self, buffer: Buffer) -> int:
        """Read from the current part, advancing only at its end."""
        while True:
            if self.stream is None:
                part = next(self.parts, None)
                if part is None:
                    return 0
                self.stream = part.open("rb")
            count = self.stream.readinto(buffer)
            if count:
                return count
            self.stream.close()
            self.stream = None

    def close(self) -> None:
        """Close the current part if decoding stops before the end."""
        if self.stream is not None:
            self.stream.close()
        super().close()


def copy_outputs(
    source: Path, target: Path, *, part_bytes: int = PART_BYTES
) -> list[str]:
    """Copy all results, compressing JSONL losslessly into Git-sized parts."""
    if part_bytes <= 0:
        raise ValueError("archive part size must be positive")
    copied: list[str] = []
    for path in output_files(source):
        relative = path.relative_to(source)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".jsonl":
            writer = _PartWriter(destination, part_bytes)
            try:
                with (
                    path.open("rb") as original,
                    gzip.GzipFile(
                        filename="", mode="wb", fileobj=writer, mtime=0
                    ) as compressed,
                ):
                    shutil.copyfileobj(original, compressed, _COPY_BYTES)
            finally:
                writer.close()
            copied.extend(part.relative_to(target).as_posix() for part in writer.paths)
        else:
            shutil.copyfile(path, destination)
            copied.append(relative.as_posix())
    return sorted(copied)


def materialize_outputs(directory: Path) -> list[Path]:
    """Restore archived JSONL files atomically, checking original manifest hashes.

    Existing raw files are left for the normal run-integrity checks. Seeding
    removes them when an archive changes. This avoids decoding on every open.
    Missing, reordered, corrupt, or truncated parts fail explicitly.
    """
    restored: list[Path] = []
    archives: dict[Path, list[Path]] = {}
    for path in output_files(directory):
        name = archive_target(path.name)
        if name is not None:
            archives.setdefault(path.with_name(name), []).append(path)
    for target, parts in archives.items():
        if target.exists():
            continue
        expected_parts = [
            target.with_name(f"{target.name}.gz.part-{index:04d}")
            for index in range(1, len(parts) + 1)
        ]
        if parts != expected_parts:
            raise ValueError(f"incomplete example archive: {target.name}")
        manifest = json.loads(
            (target.parent / "manifest.json").read_text(encoding="utf-8")
        )
        key = target.stem
        expected = manifest.get("artifacts", {}).get(key)
        if expected is None:
            raise ValueError(f"example manifest has no digest for {target.name}")
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=target.parent, prefix=f".{target.name}-", delete=False
            ) as decoded:
                temporary = Path(decoded.name)
                with (
                    _PartReader(parts) as reader,
                    io.BufferedReader(reader) as stream,
                    gzip.GzipFile(fileobj=stream, mode="rb") as compressed,
                ):
                    shutil.copyfileobj(compressed, decoded, _COPY_BYTES)
            if hash_file(temporary) != expected:
                raise ValueError(f"example archive digest mismatch: {target.name}")
            temporary.replace(target)
            restored.append(target)
        except (EOFError, zlib.error) as error:
            raise ValueError(f"corrupt example archive: {target.name}") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return restored
