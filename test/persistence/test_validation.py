"""Validation tests for trajectory stores and run manifests."""

from __future__ import annotations

from pathlib import Path

import pytest

from fim.persistence.jsonl_store import JSONLTrajectoryStore
from fim.persistence.manifest import RunManifest, read_manifest
from fim.persistence.store import InMemoryTrajectoryStore, normalize_row


def _row(**updates: object) -> dict[str, object]:
    """Return one valid trajectory row."""
    row: dict[str, object] = {
        "run_id": "run-a",
        "generation": 0,
        "deme": 1,
        "locus_id": 1,
        "allele_id": 0,
        "frequency": 1.0,
    }
    row.update(updates)
    return row


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"run_id": ""}, "run_id must be"),
        ({"generation": -1}, "generation must be"),
        ({"generation": True}, "generation must be"),
        ({"deme": 0}, "deme must be"),
        ({"locus_id": 0}, "locus_id must be"),
        ({"allele_id": -1}, "allele_id must be"),
        ({"frequency": 0.0}, "frequency must be"),
        ({"frequency": 1.1}, "frequency must be"),
        ({"frequency": float("nan")}, "frequency must be"),
    ],
)
def test_normalize_row_rejects_invalid_values(
    updates: dict[str, object],
    message: str,
) -> None:
    """The public row schema rejects invalid bounds and primitive types."""
    with pytest.raises(ValueError, match=message):
        normalize_row(_row(**updates))


def test_normalize_row_reports_missing_extra_and_context_mismatches() -> None:
    """Schema and generation context errors identify their exact cause."""
    row = _row()
    del row["deme"]
    with pytest.raises(ValueError, match="missing: deme"):
        normalize_row(row)
    with pytest.raises(ValueError, match="unknown fields: extra"):
        normalize_row({**_row(), "extra": 1})
    with pytest.raises(ValueError, match="does not match"):
        normalize_row(_row(run_id="other"), run_id="run-a")
    with pytest.raises(ValueError, match="generation.*does not match"):
        normalize_row(_row(generation=2), generation=1)


def test_stores_reject_empty_generations_and_filter_run_ids(tmp_path: Path) -> None:
    """Both storage backends enforce nonempty appends and run filtering."""
    memory = InMemoryTrajectoryStore()
    jsonl = JSONLTrajectoryStore(tmp_path / "nested" / "trajectory.jsonl")
    for store in (memory, jsonl):
        with pytest.raises(ValueError, match="at least one row"):
            store.write_generation("run-a", 0, [])
        store.write_generation("run-a", 0, [_row()])
        store.write_generation("run-b", 0, [_row(run_id="run-b")])
        assert list(store.read("run-a")) == [_row()]
        assert list(store.read("run-b")) == [_row(run_id="run-b")]


def test_jsonl_store_reports_missing_and_corrupt_complete_lines(tmp_path: Path) -> None:
    """Unreadable files and malformed complete lines are distinct failures."""
    missing = JSONLTrajectoryStore(tmp_path / "missing.jsonl")
    with pytest.raises(FileNotFoundError, match="does not exist"):
        list(missing.read("run-a"))

    path = tmp_path / "corrupt.jsonl"
    path.write_text("{not-json}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        list(JSONLTrajectoryStore(path).read("run-a"))

    path.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not an object"):
        list(JSONLTrajectoryStore(path).read("run-a"))


def _manifest() -> RunManifest:
    """Return a valid minimal manifest."""
    params = {
        "N": 20,
        "d": 2,
        "m": 0.1,
        "mu": 0.001,
        "seed": 7,
    }
    return RunManifest(
        run_id="run-a",
        parameters=params,
        started_at="start",
        ended_at="end",
        converged=True,
        convergence_statistic="D",
        stop_reason="statistic converged",
        generation=2,
        software_version="1.0.0",
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("run_id", "", "run_id"),
        ("started_at", "", "timestamps"),
        ("software_version", "", "software_version"),
        ("generation", -1, "generation"),
    ],
)
def test_manifest_constructor_validates_identity_fields(
    field: str,
    value: object,
    message: str,
) -> None:
    """Manifest identity and terminal metadata cannot be empty or negative."""
    manifest = _manifest()
    updates = {
        "run_id": manifest.run_id,
        "parameters": manifest.parameters,
        "started_at": manifest.started_at,
        "ended_at": manifest.ended_at,
        "converged": manifest.converged,
        "convergence_statistic": manifest.convergence_statistic,
        "stop_reason": manifest.stop_reason,
        "generation": manifest.generation,
        "software_version": manifest.software_version,
    }
    updates[field] = value
    with pytest.raises(ValueError, match=message):
        RunManifest(**updates)  # type: ignore[arg-type]


def test_manifest_mapping_validation_reports_missing_and_wrong_nested_types() -> None:
    """Manifest JSON validation identifies missing and malformed structures."""
    value = _manifest().to_dict()
    for field in ("run_id", "parameters", "started_at", "ended_at", "convergence"):
        missing = dict(value)
        del missing[field]
        with pytest.raises(ValueError, match=field):
            RunManifest.from_dict(missing)
    with pytest.raises(ValueError, match="parameters must be"):
        RunManifest.from_dict({**value, "parameters": []})
    with pytest.raises(ValueError, match="convergence must be"):
        RunManifest.from_dict({**value, "convergence": []})


def test_manifest_nested_fields_have_strict_types(tmp_path: Path) -> None:
    """Nested manifest fields reject wrong primitive types and negative values."""
    value = _manifest().to_dict()
    convergence = value["convergence"]
    assert isinstance(convergence, dict)
    for field, bad, message in (
        ("converged", 1, "Boolean"),
        ("statistic", "", "nonempty"),
        ("reason", 1, "nonempty"),
        ("generation", -1, "non-negative"),
    ):
        invalid = {**value, "convergence": {**convergence, field: bad}}
        with pytest.raises(ValueError, match=message):
            RunManifest.from_dict(invalid)

    path = tmp_path / "manifest.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="root must be"):
        read_manifest(path)
