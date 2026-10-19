"""Reading a run's per-generation statistic history, whatever its format.

Each replicate writes one row per generation (`convergence.jsonl`):
`{"generation": N, "D": ..., "H_S": ..., ...}`. This module is the one place
that knows how to find those files in a run directory and stream their rows,
so a reader that only wants the history (the sweep trajectory overlay, the
Results card, re-analysis) does not need to know how it was stored.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Final

HISTORY_FILENAME: Final = "convergence.jsonl"
"""The statistic history of one replicate or one scalar run."""

HistoryRow = tuple[int, dict[str, float | None]]


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
    """Return where a replicate's statistic history lives (it may not exist)."""
    return replicate_directory / HISTORY_FILENAME


def iter_history_rows(path: Path) -> Iterator[HistoryRow]:
    """Stream a history file's rows in generation order, one at a time.

    Args:
        path: A history file (see `history_path`).

    Yields:
        `(generation, {statistic: value})` for each row; a statistic that is
        undefined at a generation is `None`.

    Raises:
        ValueError: A row is not an object with an integer `generation`.
        OSError: The file cannot be read.
    """
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            text = raw.strip()
            if not text:
                continue
            row = json.loads(text)
            generation = row.get("generation") if isinstance(row, dict) else None
            if not isinstance(generation, int) or isinstance(generation, bool):
                raise ValueError(
                    f"{path.name}: a row must be an object with an integer 'generation'"
                )
            yield (
                generation,
                {name: value for name, value in row.items() if name != "generation"},
            )
