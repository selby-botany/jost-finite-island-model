"""Deterministic JSON writing for run-level report and summary artifacts.

A run's own results — its final statistics, or a batch's across-
replicate summary — need to end up in a `.json` file a human can open
directly or another program can read back reliably; `write_report`,
below, is the one function that actually writes such a file, in a
fixed, reproducible format (see its own docstring for exactly what
"deterministic" means here and why it matters).

Extracted from `fim.cli`'s private `_write_json` (design doc
`20260819-claude-sonnet-5-graphical-interface.md` §3.7), parallel to
`fim.persistence.manifest.write_manifest`: one shared, deterministic JSON
writer for both `report.json` (a `fim.engine.FinalReport`, plus the CLI's
`fim stats` re-analysis reports and `fim.gui`'s own run reports) and a
batch's `summary.json` (`fim.engine.replicate_summary`'s across-replicate
confidence intervals) — every caller needing byte-identical, sorted-key,
newline-terminated JSON, not just report.json specifically.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path


def write_report(path: Path | str, value: Mapping[str, object]) -> None:
    """Write one JSON report artifact deterministically.

    "Deterministically" means the exact same bytes are written every
    time for the exact same `value`: keys are always sorted
    alphabetically (``sort_keys=True``), the file always ends with
    exactly one trailing newline, and the same 2-space indentation is
    always used — so two runs with identical results produce byte-
    identical `report.json`/`summary.json` files, letting a plain
    ``diff`` (or a version-control system's own diff view) show a real
    change in results, never a spurious change caused only by dict key
    ordering or formatting differing between two writes. ``allow_nan=
    False`` additionally rejects ``NaN``/``Infinity`` outright rather
    than writing them as invalid JSON that most other tools cannot
    parse back — a real bug in the reported values should be caught
    here, not silently written to a file that then fails to load
    somewhere else.

    Args:
        path: Destination file path. Parent directories are created.
        value: JSON-serializable mapping.
    """
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(dict(value), handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
