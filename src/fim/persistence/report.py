"""Write deterministic JSON reports and JSON Lines rows.

The writers use stable key ordering, fixed indentation, and a trailing newline.
Non-finite values are rejected so output remains valid JSON.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from pathlib import Path

logger = logging.getLogger(__name__)


def write_report(path: Path | str, value: Mapping[str, object]) -> None:
    """Write a deterministic, indented JSON report.

    Keys are sorted, output uses two-space indentation and ends with one
    newline. Non-finite values are rejected.

    Args:
        path: Destination file path. Parent directories are created.
        value: JSON-serializable mapping.
    """
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(dict(value), handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    logger.debug("wrote report: %s", report_path)


def write_jsonl_rows(path: Path | str, rows: Iterable[Mapping[str, object]]) -> None:
    """Write a sequence of small JSON objects as one deterministic JSON Lines artifact.

    One compact JSON object per line (the same `sort_keys=True,
    separators=(",", ":")` convention as the exported trajectory text
    (`fim.persistence.tlog_export`), applied here to an artifact
    written once, all at once) — the exact same bytes for the exact same `rows`, for
    the identical "a plain `diff` shows a real change, never a
    formatting difference" reason `write_report` documents.

    Args:
        path: Destination file path. Parent directories are created.
        rows: JSON-serializable mappings, one per line, in order.
    """
    jsonl_path = Path(path)
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(
                json.dumps(
                    dict(row), sort_keys=True, separators=(",", ":"), allow_nan=False
                )
            )
            handle.write("\n")
    logger.debug("wrote JSON Lines artifact: %s", jsonl_path)
