"""Unit tests for `fim.persistence.manifest.verify_trajectory_integrity`."""

from __future__ import annotations

from pathlib import Path

import pytest

from fim.model.locus import LocusSpec
from fim.model.params import SimulationParams
from fim.persistence.manifest import RunManifest, hash_file, verify_trajectory_integrity


def _manifest(**overrides: object) -> RunManifest:
    """Build one minimal, otherwise-valid manifest for integrity tests."""
    params = SimulationParams(
        N=20, m=0.1, mu=0.001, d=2, seed=7, loci=(LocusSpec(1, 200),)
    )
    fields: dict[str, object] = {
        "schema_version": 1,
        "run_id": "run-a",
        "parameters": params.to_dict(),
        "started_at": "2026-08-14T20:00:00Z",
        "ended_at": "2026-08-14T20:00:01Z",
        "converged": True,
        "convergence_statistic": "D",
        "stop_reason": "statistic converged",
        "generation": 4,
        "generation_count": 5,
        "software_version": "1.0.0",
        "artifacts": None,
    }
    fields.update(overrides)
    return RunManifest(**fields)  # type: ignore[arg-type]


def test_verify_trajectory_integrity_accepts_a_matching_digest(
    tmp_path: Path,
) -> None:
    """A trajectory whose digest matches its manifest passes silently.

    Regression proof for Milestone G0 (design doc
    `20260819-claude-sonnet-5-graphical-interface.md` §3.7, §3.8, §6.3):
    the relocated `verify_trajectory_integrity` reproduces
    `cli._verify_trajectory_integrity`'s exact prior behavior.
    """
    trajectory = tmp_path / "trajectory.jsonl"
    trajectory.write_text('{"generation": 0}\n', encoding="utf-8")
    manifest = _manifest(artifacts={"trajectory": hash_file(trajectory)})

    verify_trajectory_integrity(trajectory, manifest)


def test_verify_trajectory_integrity_rejects_a_truncated_file(
    tmp_path: Path,
) -> None:
    """A file shorter than its recorded digest is rejected, not silently accepted."""
    trajectory = tmp_path / "trajectory.jsonl"
    trajectory.write_text('{"generation": 0}\n{"generation": 1}\n', encoding="utf-8")
    manifest = _manifest(artifacts={"trajectory": hash_file(trajectory)})
    trajectory.write_text('{"generation": 0}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="does not match its manifest"):
        verify_trajectory_integrity(trajectory, manifest)


def test_verify_trajectory_integrity_rejects_an_edited_file(tmp_path: Path) -> None:
    """A same-length but edited file is rejected — length alone is not enough."""
    trajectory = tmp_path / "trajectory.jsonl"
    trajectory.write_text('{"generation": 0}\n', encoding="utf-8")
    manifest = _manifest(artifacts={"trajectory": hash_file(trajectory)})
    trajectory.write_text('{"generation": 9}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="does not match its manifest"):
        verify_trajectory_integrity(trajectory, manifest)


def test_verify_trajectory_integrity_rejects_a_manifest_with_no_digest(
    tmp_path: Path,
) -> None:
    """A manifest predating this check (no recorded digest) is a clear error."""
    trajectory = tmp_path / "trajectory.jsonl"
    trajectory.write_text('{"generation": 0}\n', encoding="utf-8")
    manifest = _manifest(artifacts=None)

    with pytest.raises(ValueError, match="no recorded trajectory"):
        verify_trajectory_integrity(trajectory, manifest)


def test_verify_trajectory_integrity_rejects_a_manifest_missing_the_trajectory_digest(
    tmp_path: Path,
) -> None:
    """`artifacts` populated but with no `trajectory` entry is rejected too."""
    trajectory = tmp_path / "trajectory.jsonl"
    trajectory.write_text('{"generation": 0}\n', encoding="utf-8")
    other = tmp_path / "report.json"
    other.write_text("{}", encoding="utf-8")
    manifest = _manifest(artifacts={"report": hash_file(other)})

    with pytest.raises(ValueError, match="no recorded trajectory"):
        verify_trajectory_integrity(trajectory, manifest)
