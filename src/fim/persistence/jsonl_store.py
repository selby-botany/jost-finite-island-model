"""Human-readable incremental JSON Lines trajectory storage.

"JSON Lines" (the ``.jsonl`` extension) is a simple file format where
each line of the file is its own complete, independent JSON object —
unlike a single big JSON array, a new line can be appended to the end
of the file at any time without rewriting anything already there, and
a reader can process the file one line at a time without first loading
the whole thing into memory. That is exactly what a running simulation
needs: `write_generation`, below, appends one generation's own rows to
the file the moment that generation finishes, so the trajectory
survives on disk even if the run is later interrupted, and a very long
run's trajectory file never needs to be held entirely in memory at
once, either to write it or to read it back.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

from fim.persistence.store import TrajectoryRow, normalize_row


class JSONLTrajectoryStore:
    """Append and read validated trajectory rows in JSON Lines format.

    This is the real, file-backed implementation of the
    `fim.persistence.store.TrajectoryStore` protocol — the one actually
    used by `fim.engine` for a real run (as opposed to
    `fim.persistence.store.InMemoryTrajectoryStore`, a lighter-weight
    stand-in used by library calls and unit tests that never need a
    file on disk at all).
    """

    def __init__(self, path: Path | str) -> None:
        """Bind the store to one trajectory file.

        Args:
            path: JSON Lines file path. Its parent is created on first write.
        """
        self.path = Path(path)

    def write_generation(
        self,
        run_id: str,
        generation: int,
        rows: Iterable[Mapping[str, Any]],
    ) -> None:
        """Append and flush all rows for one generation.

        Every row is validated (via `fim.persistence.store.
        normalize_row`) before anything is written, so a malformed row
        is rejected up front rather than partially written to disk.
        ``handle.flush()`` hands this generation's bytes from Python's
        own internal buffer to the operating system right away, rather
        than leaving them sitting in memory until the file is
        eventually closed — this generation is written to disk as soon
        as this call returns, instead of remaining vulnerable to being
        lost entirely if the process is interrupted or crashes before
        the file handle would otherwise have been closed.
        """
        normalized_rows = [
            normalize_row(row, run_id=run_id, generation=generation) for row in rows
        ]
        if not normalized_rows:
            raise ValueError("a generation must contain at least one row")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            for row in normalized_rows:
                handle.write(
                    json.dumps(
                        row,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                )
                handle.write("\n")
            handle.flush()

    def read(self, run_id: str) -> Iterator[TrajectoryRow]:
        """Yield complete rows matching ``run_id``, oldest first.

        A generator (built via the inner `iterate` function, below,
        rather than returning a plain list) so a large trajectory file
        is streamed one row at a time instead of being fully loaded
        into memory before the caller sees any of it.

        A final partial line from an interrupted append is ignored. Any malformed
        complete line is reported as corruption.

        This tolerance is a deliberate scope boundary, not a completeness
        guarantee: this method alone cannot tell an interrupted-append
        trailing partial line from a trajectory that is simply short a
        generation for some other reason, since it has no manifest to
        compare against. Detecting that a trajectory doesn't have as
        many generations as it claims to is a manifest-level guarantee —
        `fim.persistence.manifest.verify_trajectory_integrity`'s SHA-256
        digest check, and `fim.reanalyze.reanalyze_trajectory`'s own
        generation-count cross-check against `RunManifest.
        generation_count` — not one this store makes on its own.
        """
        if not self.path.is_file():
            raise FileNotFoundError(f"trajectory does not exist: {self.path}")

        def iterate() -> Iterator[TrajectoryRow]:
            with self.path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError as error:
                        if not line.endswith("\n"):
                            return
                        raise ValueError(
                            f"invalid JSON on trajectory line {line_number}"
                        ) from error
                    if not isinstance(payload, dict):
                        raise ValueError(
                            f"trajectory line {line_number} is not an object"
                        )
                    row = normalize_row(payload)
                    if row["run_id"] == run_id:
                        yield row

        return iterate()
