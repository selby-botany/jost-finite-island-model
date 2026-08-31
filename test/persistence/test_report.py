"""Unit tests for deterministic JSON report/summary writing."""

from __future__ import annotations

from pathlib import Path

from fim.persistence.report import write_report


def test_write_report_matches_previous_cli_json_formatting(tmp_path: Path) -> None:
    """`write_report` reproduces `cli.py`'s pre-extraction JSON formatting.

    Regression proof for Milestone G0 (`doc/fim-gui-design.md` §12):
    sorted keys, two-space indent, trailing newline, no
    `NaN`/`Infinity`.
    """
    target = tmp_path / "nested" / "report.json"

    write_report(target, {"b": 1, "a": 2.5, "c": [1, 2, 3]})

    assert target.read_text(encoding="utf-8") == (
        '{\n  "a": 2.5,\n  "b": 1,\n  "c": [\n    1,\n    2,\n    3\n  ]\n}\n'
    )


def test_write_report_creates_parent_directories(tmp_path: Path) -> None:
    """A missing parent directory is created, matching every prior writer."""
    target = tmp_path / "a" / "b" / "c" / "summary.json"

    write_report(target, {"D": {"mean": 0.5}})

    assert target.is_file()
