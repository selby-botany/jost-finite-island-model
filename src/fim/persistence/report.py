"""Deterministic JSON writing for run-level report and summary artifacts.

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

    Args:
        path: Destination file path. Parent directories are created.
        value: JSON-serializable mapping.
    """
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(dict(value), handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
