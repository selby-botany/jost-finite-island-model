"""Human-readable incremental JSON Lines trajectory storage."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

from fim.persistence.store import TrajectoryRow, normalize_row


class JSONLTrajectoryStore:
    """Append and read validated trajectory rows in JSON Lines format."""

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
        """Append and flush all rows for one generation."""
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
        """Yield complete rows matching ``run_id``.

        A final partial line from an interrupted append is ignored. Any malformed
        complete line is reported as corruption.
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
