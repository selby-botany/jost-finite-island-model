"""Tests for `fim.persistence.run_receipt`: completion receipt checks.

Every receipt here comes from a real finished run written by the CLI's own
artifact writer, then damaged one way at a time. Batch receipts are tested
with batch objects in `test/test_run_service_batch.py`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from fim.examples import classes
from fim.execution import ScalarSession
from fim.model.params import SimulationParams
from fim.persistence.artifacts import run_artifact_targets, write_run_artifacts
from fim.persistence.binary_store import TRAJECTORY_LOG_FILENAME, BinaryLogStore
from fim.persistence.run_lifecycle import RunKind
from fim.persistence.run_receipt import (
    ARTIFACT_FILENAMES,
    RECEIPT_FILENAME,
    receipt_problem,
)

_CLASSES_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "classes.yaml"
AT = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)

_CONFIG: dict[str, Any] = {
    "N": 20,
    "ploidy": "haploid",
    "d": 2,
    "m": 0.1,
    "mu": 0.05,
    "seed": 20261011,
    "loci": [{"locus_id": 1, "length": 200}],
    "precision": 0.0,
    "max_generations": 12,
    "n_replicates": 1,
    "stop_batch_early": False,
    "engine_backend": "lineal",
}


@pytest.fixture(autouse=True)
def _fixture_class_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check every `class` against the test fixture, never the shipped file."""
    monkeypatch.setattr(classes, "default_classes_path", lambda: _CLASSES_FIXTURE)


@pytest.fixture
def published(tmp_path: Path) -> tuple[Path, str]:
    """Return a directory holding a finished scalar run, and its run ID."""
    params = SimulationParams.from_mapping(_CONFIG)
    store = BinaryLogStore(
        tmp_path / TRAJECTORY_LOG_FILENAME,
        background=False,
        block_seconds=None,
        block_generations=4,
        key_every=4,
    )
    with ScalarSession.create(params, store, clock=lambda: AT) as session:
        session.advance()
        result = session.finalize()
    write_run_artifacts(result, tmp_path)
    return tmp_path, result.run_id


def _problem(directory: Path, run_id: str) -> str | None:
    """Check a scalar receipt."""
    return receipt_problem(directory, kind=RunKind.SCALAR, run_id=run_id)


def _rewrite_artifacts(directory: Path, change: Any) -> None:
    """Apply `change` to the receipt's `artifacts` mapping in place."""
    path = directory / RECEIPT_FILENAME
    document = json.loads(path.read_text(encoding="utf-8"))
    change(document["artifacts"])
    path.write_text(json.dumps(document), encoding="utf-8")


def test_a_whole_run_verifies(published: tuple[Path, str]) -> None:
    """The writer's own receipt matches every file it lists."""
    directory, run_id = published
    assert _problem(directory, run_id) is None


def test_a_missing_receipt_was_never_published(published: tuple[Path, str]) -> None:
    """No receipt means finalization did not finish."""
    directory, run_id = published
    (directory / RECEIPT_FILENAME).unlink()
    assert _problem(directory, run_id) == (
        f"no receipt {directory / RECEIPT_FILENAME} was published"
    )


def test_an_unparsable_receipt_is_rejected(published: tuple[Path, str]) -> None:
    """A torn receipt cannot vouch for anything."""
    directory, run_id = published
    (directory / RECEIPT_FILENAME).write_text("{", encoding="utf-8")
    problem = _problem(directory, run_id)
    assert problem is not None
    assert problem.startswith("unreadable receipt ")


def test_a_receipt_for_another_run_is_rejected(published: tuple[Path, str]) -> None:
    """A receipt left from a different configuration does not count."""
    directory, run_id = published
    problem = _problem(directory, "run-ffffffffffffffff")
    assert problem is not None
    assert f"names run {run_id!r}" in problem


def test_a_truncated_artifact_fails_the_size_check(
    published: tuple[Path, str],
) -> None:
    """A short file is caught before it is hashed."""
    directory, run_id = published
    report = directory / "report.json"
    report.write_bytes(report.read_bytes()[:-1])
    assert _problem(directory, run_id) == (
        f"artifact {report} does not have its recorded size"
    )


def test_a_same_size_change_fails_the_digest(published: tuple[Path, str]) -> None:
    """Content is checked, not only length."""
    directory, run_id = published
    trajectory = directory / TRAJECTORY_LOG_FILENAME
    data = bytearray(trajectory.read_bytes())
    data[-1] ^= 0xFF
    trajectory.write_bytes(bytes(data))
    assert _problem(directory, run_id) == (
        f"artifact {trajectory} does not match its recorded digest"
    )


def test_a_missing_artifact_is_reported(published: tuple[Path, str]) -> None:
    """A listed file that is gone fails verification."""
    directory, run_id = published
    scatter = directory / "scatter.png"
    scatter.unlink()
    assert _problem(directory, run_id) == f"artifact {scatter} is missing"


def test_a_receipt_without_the_trajectory_is_rejected(
    published: tuple[Path, str],
) -> None:
    """The trajectory is the one artifact every completed run has."""
    directory, run_id = published
    _rewrite_artifacts(directory, lambda artifacts: artifacts.pop("trajectory"))
    problem = _problem(directory, run_id)
    assert problem is not None
    assert problem.endswith("does not list the trajectory")


def test_an_unknown_artifact_key_is_rejected(published: tuple[Path, str]) -> None:
    """A receipt from a newer writer is not trusted blindly."""
    directory, run_id = published
    _rewrite_artifacts(
        directory, lambda artifacts: artifacts.update(zzz=artifacts["report"])
    )
    problem = _problem(directory, run_id)
    assert problem is not None
    assert problem.endswith("lists an unknown artifact 'zzz'")


def test_artifact_filenames_match_both_writers(tmp_path: Path) -> None:
    """The receipt keys agree with the CLI's and the GUI's artifact names."""
    expected = {key: tmp_path / name for key, name in ARTIFACT_FILENAMES.items()}
    expected["manifest"] = tmp_path / RECEIPT_FILENAME
    # `history.json` exists only in a statistics-only run, so it is a receipt
    # key but not among the artifacts every run can have.
    del expected["history"]
    assert run_artifact_targets(tmp_path) == expected
