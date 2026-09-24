"""Tests for `fim.reproducibility`: comparing two runs of one configuration."""

from __future__ import annotations

import json
from pathlib import Path

from fim.reproducibility import compare_runs


def _run(
    directory: Path,
    *,
    version: str,
    trajectory: str = "aaa",
    report: dict[str, object] | None = None,
    scatter: str = "s1",
) -> Path:
    """Write a minimal scalar run: a manifest with digests, and a report."""
    directory.mkdir(parents=True)
    report = report if report is not None else {"D": 0.25, "generation": 10}
    (directory / "report.json").write_text(json.dumps(report), encoding="utf-8")
    manifest = {
        "software_version": version,
        "artifacts": {
            "trajectory": {"sha256": trajectory},
            "report": {"sha256": json.dumps(report, sort_keys=True)},
            "scatter": {"sha256": scatter},
        },
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory


def test_matching_data_artifacts_are_identical_whatever_the_scatter_bytes(
    tmp_path: Path,
) -> None:
    old = _run(tmp_path / "old", version="1.2.0", scatter="s1")
    new = _run(tmp_path / "new", version="1.3.0", scatter="s2")

    comparison = compare_runs(old, new)

    assert comparison.identical is True
    assert comparison.differences == ()
    assert (comparison.old_version, comparison.new_version) == ("1.2.0", "1.3.0")


def test_a_changed_trajectory_and_statistic_is_named(tmp_path: Path) -> None:
    old = _run(tmp_path / "old", version="1.2.0")
    new = _run(
        tmp_path / "new",
        version="1.3.0",
        trajectory="bbb",
        report={"D": 0.26, "generation": 10},
    )

    comparison = compare_runs(old, new)

    assert comparison.identical is False
    labels = [item.label for item in comparison.differences]
    assert labels[:2] == ["report", "trajectory"]
    assert "D" in labels
    assert "generation" not in labels
    statistic = next(item for item in comparison.differences if item.label == "D")
    assert (statistic.old, statistic.new) == (0.25, 0.26)


def test_a_missing_artifact_counts_as_a_difference(tmp_path: Path) -> None:
    old = _run(tmp_path / "old", version="1.2.0")
    new = _run(tmp_path / "new", version="1.3.0")
    manifest = json.loads((new / "manifest.json").read_text(encoding="utf-8"))
    del manifest["artifacts"]["trajectory"]
    (new / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    comparison = compare_runs(old, new)

    assert comparison.identical is False
    assert comparison.differences[0].label == "trajectory"


def test_a_batch_summary_compares_its_means(tmp_path: Path) -> None:
    def batch(directory: Path, mean: float, digest: str) -> Path:
        directory.mkdir()
        (directory / "summary.json").write_text(
            json.dumps({"D": {"mean": mean, "low": 0.0, "high": 1.0}}), encoding="utf-8"
        )
        (directory / "manifest.json").write_text(
            json.dumps(
                {
                    "software_version": "x",
                    "artifacts": {"summary": {"sha256": digest}},
                }
            ),
            encoding="utf-8",
        )
        return directory

    comparison = compare_runs(
        batch(tmp_path / "a", 0.1, "d1"), batch(tmp_path / "b", 0.2, "d2")
    )

    assert comparison.identical is False
    assert [(d.label, d.old, d.new) for d in comparison.differences] == [
        ("summary", "d1", "d2"),
        ("D", 0.1, 0.2),
    ]


def test_to_dict_is_json_serializable(tmp_path: Path) -> None:
    old = _run(tmp_path / "old", version="1.2.0")
    new = _run(tmp_path / "new", version="1.3.0", trajectory="bbb")

    payload = compare_runs(old, new).to_dict()

    assert json.loads(json.dumps(payload))["identical"] is False
