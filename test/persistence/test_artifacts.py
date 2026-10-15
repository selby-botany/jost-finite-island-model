"""The shared writer of a saved run's files."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from vector_support import loci, make_params

from fim.engine import RunResult, simulate_params
from fim.persistence.artifacts import (
    convergence_history_rows,
    run_artifact_targets,
    write_run_artifacts,
)
from fim.persistence.binary_store import BinaryLogStore
from fim.persistence.manifest import read_manifest, verify_trajectory_integrity


def _run(directory: Path) -> RunResult:
    """Run a tiny configuration, streaming its trajectory into `directory`."""
    params = make_params(40, loci=loci(1), d=3, mu=0.02, m=0.1)
    with BinaryLogStore(run_artifact_targets(directory)["trajectory"]) as store:
        output = simulate_params(
            params=params,
            store=store,
            run_id="run",
            clock=lambda: datetime(2026, 10, 15, tzinfo=UTC),
        )
    assert isinstance(output, RunResult)
    return output


def test_targets_name_every_documented_file(tmp_path: Path) -> None:
    """The filenames are fixed, and live in the given directory."""
    targets = run_artifact_targets(tmp_path)
    assert {key: path.name for key, path in targets.items()} == {
        "trajectory": "trajectory.tlog",
        "equilibrium_trajectory": "equilibrium_trajectory.tlog",
        "manifest": "manifest.json",
        "report": "report.json",
        "scatter": "scatter.png",
        "convergence": "convergence.jsonl",
        "pairwise": "pairwise.json",
    }
    assert all(path.parent == tmp_path for path in targets.values())


def test_writing_a_run_produces_a_verifiable_manifest_last(tmp_path: Path) -> None:
    """Every sibling file exists and is digested in the manifest."""
    result = _run(tmp_path)
    targets = write_run_artifacts(result, tmp_path)
    for name in (
        "trajectory",
        "report",
        "scatter",
        "convergence",
        "pairwise",
        "manifest",
    ):
        assert targets[name].is_file(), name
    manifest = read_manifest(targets["manifest"])
    assert manifest.artifacts is not None
    assert set(manifest.artifacts) == {
        "trajectory",
        "report",
        "scatter",
        "convergence",
        "pairwise",
    }
    assert json.loads(targets["report"].read_text(encoding="utf-8")) == result.report
    verify_trajectory_integrity(targets["trajectory"], manifest)


def test_convergence_rows_are_one_per_generation(tmp_path: Path) -> None:
    """Each row carries its generation and every fully aligned statistic."""
    result = _run(tmp_path)
    rows = convergence_history_rows(result)
    assert [row["generation"] for row in rows] == list(result.convergence_generations)
    assert all("D" in row for row in rows)
