"""Validation tests for trajectory stores and run manifests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from fim.persistence.jsonl_store import JSONLTrajectoryStore
from fim.persistence.manifest import (
    BatchManifest,
    RunManifest,
    read_batch_manifest,
    read_manifest,
    write_batch_manifest,
    write_manifest,
)
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
        ({"frequency": True}, "frequency must be"),
        ({"frequency": "1"}, "frequency must be"),
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
        schema_version=1,
        run_id="run-a",
        parameters=params,
        started_at="start",
        ended_at="end",
        converged=True,
        convergence_statistic="D",
        stop_reason="statistic converged",
        generation=2,
        generation_count=3,
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
        "schema_version": manifest.schema_version,
        "run_id": manifest.run_id,
        "parameters": manifest.parameters,
        "started_at": manifest.started_at,
        "ended_at": manifest.ended_at,
        "converged": manifest.converged,
        "convergence_statistic": manifest.convergence_statistic,
        "stop_reason": manifest.stop_reason,
        "generation": manifest.generation,
        "generation_count": manifest.generation_count,
        "software_version": manifest.software_version,
    }
    updates[field] = value
    with pytest.raises(ValueError, match=message):
        RunManifest(**updates)  # type: ignore[arg-type]


def test_manifest_mapping_validation_reports_missing_and_wrong_nested_types() -> None:
    """Manifest JSON validation identifies missing and malformed structures."""
    value = _manifest().to_dict()
    for field in (
        "schema_version",
        "run_id",
        "parameters",
        "started_at",
        "ended_at",
        "convergence",
    ):
        missing = dict(value)
        del missing[field]
        with pytest.raises(ValueError, match=field):
            RunManifest.from_dict(missing)
    with pytest.raises(ValueError, match="parameters must be"):
        RunManifest.from_dict({**value, "parameters": []})
    with pytest.raises(ValueError, match="convergence must be"):
        RunManifest.from_dict({**value, "convergence": []})


def test_manifest_missing_schema_version_names_the_legacy_cause() -> None:
    """A pre-1.1.0 manifest missing only `schema_version` gets a specific,
    actionable error naming the cause and the remedy.

    Regression test for S9: `fim` 1.0.0, the only released version, wrote
    no `schema_version` field at all, so every manifest it produced hits
    exactly this case (missing `schema_version`, everything else
    present, `software_version` a recognizable string) — the generic
    "manifest is missing: schema_version" message named the absent JSON
    key but not why it was absent or what to do about it.
    """
    legacy = dict(_manifest().to_dict())
    del legacy["schema_version"]
    legacy["software_version"] = "1.0.0"

    with pytest.raises(ValueError, match="written by fim 1.0.0"):
        RunManifest.from_dict(legacy)
    with pytest.raises(ValueError, match="no automated migration"):
        RunManifest.from_dict(legacy)


def test_manifest_missing_several_fields_uses_the_generic_message() -> None:
    """More than just `schema_version` missing falls back to the generic,
    key-naming error rather than the legacy-specific one.
    """
    value = dict(_manifest().to_dict())
    del value["schema_version"]
    del value["run_id"]

    with pytest.raises(ValueError, match="manifest is missing: run_id, schema_version"):
        RunManifest.from_dict(value)


def test_manifest_nested_fields_have_strict_types(tmp_path: Path) -> None:
    """Nested manifest fields reject wrong primitive types and negative values."""
    value = _manifest().to_dict()
    convergence = value["convergence"]
    assert isinstance(convergence, dict)
    for field, bad, message in (
        ("converged", 1, "Boolean"),
        ("statistic", "", "nonempty"),
        ("statistic", [], "nonempty string or list"),
        ("statistic", [1, 2], "nonempty string or list"),
        ("statistic", ["D", ""], "nonempty string or list"),
        ("reason", 1, "nonempty"),
        ("generation", -1, "at least 0"),
        ("generation_count", 0, "at least 1"),
    ):
        invalid = {**value, "convergence": {**convergence, field: bad}}
        with pytest.raises(ValueError, match=message):
            RunManifest.from_dict(invalid)

    path = tmp_path / "manifest.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="root must be"):
        read_manifest(path)


def test_manifest_schema_version_is_validated() -> None:
    """`schema_version` rejects non-positive values from both construction paths."""
    with pytest.raises(ValueError, match="schema_version"):
        replace(_manifest(), schema_version=0)
    value = _manifest().to_dict()
    with pytest.raises(ValueError, match="schema_version"):
        RunManifest.from_dict({**value, "schema_version": 0})


def test_manifest_artifacts_default_to_none_and_round_trip_when_present(
    tmp_path: Path,
) -> None:
    """`artifacts` is `None` unless a run actually recorded on-disk digests."""
    manifest = _manifest()
    assert manifest.artifacts is None

    digested = replace(
        manifest,
        artifacts={
            "trajectory": {"sha256": "a" * 64, "bytes": 123},
            "report": {"sha256": "b" * 64, "bytes": 45},
        },
    )
    path = tmp_path / "manifest.json"
    write_manifest(path, digested)
    restored = read_manifest(path)

    assert restored == digested
    assert restored.artifacts == {
        "trajectory": {"sha256": "a" * 64, "bytes": 123},
        "report": {"sha256": "b" * 64, "bytes": 45},
    }


@pytest.mark.parametrize(
    ("digest", "message"),
    [
        ({"bytes": 1}, "sha256"),
        ({"sha256": "", "bytes": 1}, "sha256"),
        ({"sha256": "a" * 64}, "bytes"),
        ({"sha256": "a" * 64, "bytes": "1"}, "bytes"),
        ({"sha256": "a" * 64, "bytes": True}, "bytes"),
        ({"sha256": "a" * 64, "bytes": -1}, "non-negative"),
    ],
)
def test_manifest_artifact_digests_are_validated(
    digest: dict[str, object],
    message: str,
) -> None:
    """Every recorded artifact digest needs a real hash and a non-negative size."""
    value = _manifest().to_dict()
    with pytest.raises(ValueError, match=message):
        RunManifest.from_dict({**value, "artifacts": {"trajectory": digest}})


def _batch_manifest() -> BatchManifest:
    """Return a valid minimal batch manifest."""
    params = {
        "N": 20,
        "d": 2,
        "m": 0.1,
        "mu": 0.001,
        "seed": 7,
        "n_replicates": 2,
    }
    return BatchManifest(
        schema_version=1,
        run_id="batch-a",
        replicate_run_ids=("batch-a-r001", "batch-a-r002"),
        parameters=params,
        started_at="start",
        ended_at="end",
        software_version="1.0.0",
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("run_id", "", "run_id"),
        ("replicate_run_ids", (), "replicate_run_ids"),
        ("started_at", "", "timestamps"),
        ("software_version", "", "software_version"),
    ],
)
def test_batch_manifest_constructor_validates_identity_fields(
    field: str,
    value: object,
    message: str,
) -> None:
    """Batch manifest identity metadata cannot be empty."""
    manifest = _batch_manifest()
    updates = {
        "schema_version": manifest.schema_version,
        "run_id": manifest.run_id,
        "replicate_run_ids": manifest.replicate_run_ids,
        "parameters": manifest.parameters,
        "started_at": manifest.started_at,
        "ended_at": manifest.ended_at,
        "software_version": manifest.software_version,
    }
    updates[field] = value
    with pytest.raises(ValueError, match=message):
        BatchManifest(**updates)  # type: ignore[arg-type]


def test_batch_manifest_mapping_validation_reports_missing_and_wrong_nested_types() -> (
    None
):
    """Batch manifest JSON validation identifies missing and malformed fields."""
    value = _batch_manifest().to_dict()
    for field in (
        "schema_version",
        "run_id",
        "replicate_run_ids",
        "parameters",
        "started_at",
        "ended_at",
        "software_version",
    ):
        missing = dict(value)
        del missing[field]
        with pytest.raises(ValueError, match="missing"):
            BatchManifest.from_dict(missing)
    with pytest.raises(ValueError, match="parameters must be"):
        BatchManifest.from_dict({**value, "parameters": []})


@pytest.mark.parametrize(
    ("replicate_run_ids", "message"),
    [
        ([], "nonempty list"),
        ("batch-a-r001", "nonempty list"),
        ([""], "nonempty strings"),
        ([1], "nonempty strings"),
    ],
)
def test_batch_manifest_replicate_run_ids_are_validated(
    replicate_run_ids: object,
    message: str,
) -> None:
    """`replicate_run_ids` must be a nonempty list of nonempty strings."""
    value = _batch_manifest().to_dict()
    with pytest.raises(ValueError, match=message):
        BatchManifest.from_dict({**value, "replicate_run_ids": replicate_run_ids})


def test_batch_manifest_replicate_count_must_match_replicate_run_ids() -> None:
    """A hand-edited `replicate_count` disagreeing with the ID list is rejected."""
    value = _batch_manifest().to_dict()
    with pytest.raises(ValueError, match="replicate_count"):
        BatchManifest.from_dict({**value, "replicate_count": 99})


def test_batch_manifest_replicate_count_is_derived_not_required() -> None:
    """`replicate_count` need not be present on read; it is derived on write."""
    value = _batch_manifest().to_dict()
    del value["replicate_count"]

    restored = BatchManifest.from_dict(value)

    assert restored.replicate_count == 2


def test_batch_manifest_schema_version_is_validated() -> None:
    """`schema_version` rejects non-positive values from both construction paths."""
    with pytest.raises(ValueError, match="schema_version"):
        replace(_batch_manifest(), schema_version=0)
    value = _batch_manifest().to_dict()
    with pytest.raises(ValueError, match="schema_version"):
        BatchManifest.from_dict({**value, "schema_version": 0})


def test_batch_manifest_artifacts_default_to_none_and_round_trip_when_present(
    tmp_path: Path,
) -> None:
    """`artifacts` is `None` unless a batch actually recorded on-disk digests."""
    manifest = _batch_manifest()
    assert manifest.artifacts is None

    digested = replace(
        manifest,
        artifacts={
            "summary": {"sha256": "a" * 64, "bytes": 123},
            "replicate-001": {"sha256": "b" * 64, "bytes": 45},
            "replicate-002": {"sha256": "c" * 64, "bytes": 67},
        },
    )
    path = tmp_path / "manifest.json"
    write_batch_manifest(path, digested)
    restored = read_batch_manifest(path)

    assert restored == digested
    assert restored.artifacts == {
        "summary": {"sha256": "a" * 64, "bytes": 123},
        "replicate-001": {"sha256": "b" * 64, "bytes": 45},
        "replicate-002": {"sha256": "c" * 64, "bytes": 67},
    }


def test_read_batch_manifest_rejects_a_non_object_root(tmp_path: Path) -> None:
    """A batch manifest file must contain a JSON object at its root."""
    path = tmp_path / "manifest.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="root must be"):
        read_batch_manifest(path)
