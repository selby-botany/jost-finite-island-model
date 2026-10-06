"""Unit tests for `fim.persistence.run_metadata`."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from fim.persistence.run_metadata import (
    RunLabels,
    RunMetadata,
    read_run_metadata,
    replace_run_metadata,
    run_metadata_path,
    write_run_labels,
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


_CLASSES_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "classes.yaml"


def _fixed_clock() -> datetime:
    """Return one fixed instant, so timestamps are a function of the test."""
    return datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)


def test_run_labels_from_config_reads_all_three_labels() -> None:
    """`name`, `description`, and `class` come back stripped, others ignored."""
    config = {
        "N": 20,
        "seed": 1,
        "name": "  Ring of four ",
        "description": "Four demes.\n",
        "class": "migration",
    }

    labels = RunLabels.from_config(config, classes_path=_CLASSES_FIXTURE)

    assert labels == RunLabels(
        name="Ring of four", description="Four demes.", run_class="migration"
    )
    assert not labels.is_empty


def test_run_labels_from_config_without_labels_is_empty() -> None:
    """A configuration without labels (or with `null` ones) has none."""
    assert RunLabels.from_config({"N": 20}).is_empty
    assert RunLabels.from_config(
        {"name": None, "description": None, "class": None}
    ).is_empty


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"name": ""}, "name must not be blank"),
        ({"description": "   "}, "description must not be blank"),
        ({"name": 7}, "name must be text"),
        ({"class": "galaxy"}, "unknown class 'galaxy'"),
        ({"class": "Not Kebab"}, "kebab-case"),
    ],
)
def test_run_labels_from_config_rejects_bad_labels(
    config: dict[str, object], message: str
) -> None:
    """Blank text, non-text, and an unknown or malformed class are rejected."""
    with pytest.raises(ValueError, match=message):
        RunLabels.from_config(config, classes_path=_CLASSES_FIXTURE)


def test_class_round_trips_under_the_json_key_class() -> None:
    """`run_class` is written as `class`, and read back from it."""
    metadata = _metadata(run_class="literature-distances")

    payload = metadata.to_dict()

    assert payload["class"] == "literature-distances"
    assert RunMetadata.from_dict(payload) == metadata


def test_to_dict_omits_class_when_unset() -> None:
    """A sidecar without a class keeps the shape earlier versions wrote."""
    assert "class" not in _metadata().to_dict()


def test_from_dict_reads_a_sidecar_written_before_classes_existed() -> None:
    """An old sidecar, without a `class` key, still reads, with no class."""
    payload = {
        "schema_version": 1,
        "name": "Old run",
        "description": None,
        "created_at": "2026-09-17T14:03:11.204112Z",
        "updated_at": "2026-09-17T14:03:11.204112Z",
    }

    assert RunMetadata.from_dict(payload).run_class is None


def test_blank_class_is_rejected() -> None:
    """A class, when present, must not be blank."""
    with pytest.raises(ValueError, match="class must not be blank"):
        _metadata(run_class=" ")


def test_replace_run_metadata_keeps_the_class_unless_one_is_passed(
    tmp_path: Path,
) -> None:
    """Renaming a run keeps its class; passing `run_class` changes or clears it."""
    replace_run_metadata(
        tmp_path,
        name="One",
        description=None,
        run_class="migration",
        clock=_fixed_clock,
    )

    renamed = replace_run_metadata(
        tmp_path, name="Two", description=None, clock=_fixed_clock
    )
    assert renamed.run_class == "migration"

    reclassed = replace_run_metadata(
        tmp_path,
        name="Two",
        description=None,
        run_class="literature",
        clock=_fixed_clock,
    )
    assert reclassed.run_class == "literature"

    cleared = replace_run_metadata(
        tmp_path, name="Two", description=None, run_class=None, clock=_fixed_clock
    )
    assert cleared.run_class is None
    assert read_run_metadata(run_metadata_path(tmp_path)).run_class is None


def test_write_run_labels_creates_a_sidecar_from_the_labels(tmp_path: Path) -> None:
    """A new run's labels become its `metadata.json`."""
    labels = RunLabels(name="Ring", description="Four demes.", run_class="migration")

    written = write_run_labels(tmp_path, labels, clock=_fixed_clock)

    assert written is not None
    assert read_run_metadata(run_metadata_path(tmp_path)) == written
    assert written.run_class == "migration"
    assert written.created_at == written.updated_at == "2026-10-05T12:00:00Z"


@pytest.mark.parametrize("prior", ["valid", "unreadable"])
def test_write_run_labels_never_overwrites_an_existing_sidecar(
    tmp_path: Path, prior: str
) -> None:
    """Any existing `metadata.json`, readable or not, is kept byte for byte."""
    path = run_metadata_path(tmp_path)
    if prior == "valid":
        write_run_metadata(path, _metadata(name="Renamed later"))
    else:
        path.write_text("not json", encoding="utf-8")
    before = path.read_bytes()

    written = write_run_labels(tmp_path, RunLabels(name="From config"))

    assert written is None
    assert path.read_bytes() == before


def test_write_run_labels_writes_nothing_for_empty_labels(tmp_path: Path) -> None:
    """A configuration without labels leaves the run without a sidecar."""
    assert write_run_labels(tmp_path, RunLabels()) is None
    assert not run_metadata_path(tmp_path).exists()


def test_write_run_labels_works_for_a_read_only_run(tmp_path: Path) -> None:
    """Creating a sidecar is not an edit, so a read-only run gets its labels."""
    (tmp_path / "manifest.json").write_text(
        '{"parameters": {"_read_only": true}}', encoding="utf-8"
    )

    written = write_run_labels(tmp_path, RunLabels(name="Example"))

    assert written is not None
    assert written.name == "Example"
