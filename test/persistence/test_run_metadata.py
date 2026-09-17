"""Unit tests for `fim.persistence.run_metadata`."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from fim.persistence.run_metadata import (
    RunMetadata,
    read_run_metadata,
    replace_run_metadata,
    run_metadata_path,
    write_run_metadata,
)


def _metadata(**overrides: object) -> RunMetadata:
    """Build one minimal, otherwise-valid `RunMetadata` for validation tests."""
    fields: dict[str, object] = {
        "schema_version": 1,
        "name": "Baseline before topology sweep",
        "description": "Sanity check.",
        "created_at": "2026-09-17T14:03:11.204112Z",
        "updated_at": "2026-09-17T14:03:11.204112Z",
    }
    fields.update(overrides)
    return RunMetadata(**fields)  # type: ignore[arg-type]


def test_to_dict_from_dict_round_trips() -> None:
    """A written-then-read metadata object is field-for-field identical."""
    metadata = _metadata()

    assert RunMetadata.from_dict(metadata.to_dict()) == metadata


def test_from_dict_accepts_name_and_description_independently_unset() -> None:
    """A user may set only one of `name`/`description`, or neither."""
    only_description = _metadata(name=None)
    only_name = _metadata(description=None)

    assert RunMetadata.from_dict(only_description.to_dict()).name is None
    assert RunMetadata.from_dict(only_name.to_dict()).description is None


def test_from_dict_rejects_a_missing_required_field() -> None:
    """A payload missing `created_at` is a clear error, not a crash."""
    payload = _metadata().to_dict()
    del payload["created_at"]

    with pytest.raises(ValueError, match="run metadata is missing: created_at"):
        RunMetadata.from_dict(payload)


def test_schema_version_below_one_is_rejected() -> None:
    """`schema_version` must be at least 1."""
    with pytest.raises(ValueError, match="schema_version"):
        _metadata(schema_version=0)


def test_blank_name_is_rejected() -> None:
    """A whitespace-only name is treated the same as a missing one, not stored blank."""
    with pytest.raises(ValueError, match="name must not be blank"):
        _metadata(name="   ")


def test_blank_description_is_rejected() -> None:
    """A whitespace-only description is rejected the same way a blank name is."""
    with pytest.raises(ValueError, match="description must not be blank"):
        _metadata(description="   ")


def test_write_then_read_round_trips_through_disk(tmp_path: Path) -> None:
    """`write_run_metadata`/`read_run_metadata` round-trip through a real file."""
    path = run_metadata_path(tmp_path)
    metadata = _metadata()

    write_run_metadata(path, metadata)

    assert read_run_metadata(path) == metadata


def test_read_run_metadata_rejects_a_non_object_root(tmp_path: Path) -> None:
    """A metadata file whose JSON root is not an object is a clear error."""
    path = run_metadata_path(tmp_path)
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="root must be an object"):
        read_run_metadata(path)


def test_replace_run_metadata_creates_a_fresh_file(tmp_path: Path) -> None:
    """Creating metadata for a run with none yet sets `created_at == updated_at`."""
    clock = lambda: datetime(2026, 9, 17, 12, 0, 0, tzinfo=UTC)  # noqa: E731

    metadata = replace_run_metadata(
        tmp_path, name="Run one", description=None, clock=clock
    )

    assert metadata.name == "Run one"
    assert metadata.created_at == metadata.updated_at == "2026-09-17T12:00:00Z"


def test_replace_run_metadata_preserves_created_at_on_a_second_call(
    tmp_path: Path,
) -> None:
    """Renaming an already-named run keeps its original `created_at`."""
    first_clock = lambda: datetime(2026, 9, 17, 12, 0, 0, tzinfo=UTC)  # noqa: E731
    second_clock = lambda: datetime(2026, 9, 18, 9, 30, 0, tzinfo=UTC)  # noqa: E731
    replace_run_metadata(tmp_path, name="Run one", description=None, clock=first_clock)

    renamed = replace_run_metadata(
        tmp_path,
        name="Run one, renamed",
        description="Now with a description",
        clock=second_clock,
    )

    assert renamed.name == "Run one, renamed"
    assert renamed.description == "Now with a description"
    assert renamed.created_at == "2026-09-17T12:00:00Z"
    assert renamed.updated_at == "2026-09-18T09:30:00Z"


def test_replace_run_metadata_ignores_an_unreadable_prior_file(tmp_path: Path) -> None:
    """A corrupt existing `metadata.json` never blocks writing a fresh one."""
    path = run_metadata_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json", encoding="utf-8")
    clock = lambda: datetime(2026, 9, 17, 12, 0, 0, tzinfo=UTC)  # noqa: E731

    metadata = replace_run_metadata(
        tmp_path, name="Recovered", description=None, clock=clock
    )

    assert metadata.name == "Recovered"
    assert metadata.created_at == "2026-09-17T12:00:00Z"
