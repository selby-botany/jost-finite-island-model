"""Tests for durable draft and planned Run objects (`fim.persistence.run_objects`).

Object IDs and clocks are injected, so every test is a function of its
commit. Labels with a `class` are checked against the test fixture file.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from fim.engine import deterministic_run_id
from fim.examples import classes
from fim.model.params import SimulationParams
from fim.persistence.object_lock import (
    LockUnavailableError,
    exclusive_lock,
    is_lock_held,
)
from fim.persistence.run_lifecycle import (
    IllegalTransitionError,
    RevisionConflictError,
    RunEvent,
    RunKind,
    RunState,
    apply_event,
)
from fim.persistence.run_objects import (
    CONFIG_FILE_NAME,
    RunObjectNotFoundError,
    clone_run_object,
    configuration_digest,
    create_run_object,
    delete_run_object,
    list_run_objects,
    object_directory,
    objects_directory,
    read_run_object,
    save_run_object,
    validate_configuration_text,
)

_CLASSES_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "classes.yaml"
AT = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
ATTEMPT = "att-" + "a" * 32

_VALID: dict[str, object] = {
    "N": 20,
    "ploidy": "haploid",
    "d": 2,
    "m": 0.1,
    "mu": 0.01,
    "seed": 20261005,
    "precision": 1.0,
    "max_generations": 10,
    "n_replicates": 1,
    "stop_batch_early": False,
}


@pytest.fixture(autouse=True)
def _fixture_class_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check every `class` against the test fixture, never the shipped file."""
    monkeypatch.setattr(classes, "default_classes_path", lambda: _CLASSES_FIXTURE)


def _ids() -> Callable[[], str]:
    """Return a deterministic object ID factory."""
    counter = itertools.count(1)
    return lambda: f"obj-{next(counter):032x}"


def _clock() -> datetime:
    """Return the fixed test time."""
    return AT


def _text(**updates: object) -> str:
    """Return a configuration document with `updates` applied."""
    config = {**_VALID, **updates}
    return yaml.safe_dump(config, sort_keys=False)


def test_valid_configuration_creates_a_planned_object(tmp_path: Path) -> None:
    """A valid document is `planned` with its digest and scientific run ID."""
    text = _text(name="Baseline")
    created = create_run_object(text, results=tmp_path, clock=_clock, id_factory=_ids())
    params = SimulationParams.from_mapping(yaml.safe_load(text))
    record = created.record
    assert record.state is RunState.PLANNED
    assert record.scientific_run_id == deterministic_run_id(params)
    assert record.configuration_digest == configuration_digest(params)
    assert record.kind is RunKind.SCALAR
    assert created.labels.name == "Baseline"
    assert (created.directory / CONFIG_FILE_NAME).read_text(encoding="utf-8") == text


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("N: [unclosed\n", "YAML syntax error"),
        ("", "empty"),
        ("- just\n- a list\n", "mapping"),
        (_text(N=-5), "N"),
        (_text(**{"class": "no-such-class"}), "class"),
    ],
)
def test_invalid_configuration_is_saved_as_a_draft(
    tmp_path: Path, text: str, fragment: str
) -> None:
    """Any invalid document persists as a draft holding its exact text."""
    created = create_run_object(text, results=tmp_path, clock=_clock, id_factory=_ids())
    assert created.record.state is RunState.DRAFT
    assert created.record.scientific_run_id is None
    messages = " ".join(issue.message for issue in created.record.validation_issues)
    assert fragment in messages
    reopened = read_run_object(created.object_id, results=tmp_path)
    assert reopened.config_text == text
    assert reopened.record == created.record


def test_yaml_error_is_located(tmp_path: Path) -> None:
    """A YAML syntax error records its line and column."""
    configuration, _ = validate_configuration_text("a: 1\nb: [\n")
    assert configuration.issues[0].location is not None
    assert configuration.issues[0].location.startswith("line ")


def test_saves_move_between_draft_and_planned(tmp_path: Path) -> None:
    """Fixing promotes to planned; breaking demotes to draft; ID is stable."""
    created = create_run_object(
        _text(N=0), results=tmp_path, clock=_clock, id_factory=_ids()
    )
    assert created.record.state is RunState.DRAFT
    fixed = save_run_object(
        created.object_id,
        _text(),
        expected_revision=created.record.revision,
        results=tmp_path,
        clock=_clock,
    )
    assert fixed.record.state is RunState.PLANNED
    broken = save_run_object(
        created.object_id,
        "N: [\n",
        expected_revision=fixed.record.revision,
        results=tmp_path,
        clock=_clock,
    )
    assert broken.record.state is RunState.DRAFT
    assert broken.object_id == created.object_id
    assert broken.record.configuration_revision == 3
    assert read_run_object(created.object_id, results=tmp_path).config_text == (
        "N: [\n"
    )


def test_editing_parameters_changes_the_scientific_identity(tmp_path: Path) -> None:
    """A planned edit keeps the object ID and changes digest and run ID."""
    created = create_run_object(
        _text(), results=tmp_path, clock=_clock, id_factory=_ids()
    )
    edited = save_run_object(
        created.object_id,
        _text(mu=0.02),
        expected_revision=created.record.revision,
        results=tmp_path,
        clock=_clock,
    )
    assert edited.object_id == created.object_id
    assert edited.record.scientific_run_id != created.record.scientific_run_id
    assert edited.record.configuration_digest != created.record.configuration_digest


def test_labels_are_not_part_of_the_scientific_identity(tmp_path: Path) -> None:
    """Renaming a planned Run leaves its run ID unchanged."""
    created = create_run_object(
        _text(name="A"), results=tmp_path, clock=_clock, id_factory=_ids()
    )
    renamed = save_run_object(
        created.object_id,
        _text(name="B"),
        expected_revision=created.record.revision,
        results=tmp_path,
        clock=_clock,
    )
    assert renamed.labels.name == "B"
    assert renamed.record.scientific_run_id == created.record.scientific_run_id


def test_kind_follows_the_saved_replicate_count(tmp_path: Path) -> None:
    """Editing a scalar into a batch updates the object's kind."""
    created = create_run_object(
        _text(), results=tmp_path, clock=_clock, id_factory=_ids()
    )
    batch = save_run_object(
        created.object_id,
        _text(n_replicates=4),
        expected_revision=created.record.revision,
        results=tmp_path,
        clock=_clock,
    )
    assert batch.record.kind is RunKind.BATCH


def test_stale_save_is_rejected_and_changes_nothing(tmp_path: Path) -> None:
    """A save based on an old revision fails without writing anything."""
    created = create_run_object(
        _text(), results=tmp_path, clock=_clock, id_factory=_ids()
    )
    save_run_object(
        created.object_id,
        _text(mu=0.02),
        expected_revision=1,
        results=tmp_path,
        clock=_clock,
    )
    with pytest.raises(RevisionConflictError):
        save_run_object(
            created.object_id,
            _text(mu=0.03),
            expected_revision=1,
            results=tmp_path,
            clock=_clock,
        )
    assert (
        "mu: 0.02" in read_run_object(created.object_id, results=tmp_path).config_text
    )


def _start(tmp_path: Path, object_id: str) -> None:
    """Move an object to `running` by writing its record directly."""
    current = read_run_object(object_id, results=tmp_path)
    running = apply_event(current.record, RunEvent.START, at=AT, attempt_id=ATTEMPT)
    path = current.directory / "run.json"
    path.write_text(json.dumps(running.to_dict()), encoding="utf-8")


def test_started_object_refuses_saves_and_deletion(tmp_path: Path) -> None:
    """Configuration is fixed and deletion refused once execution starts."""
    created = create_run_object(
        _text(), results=tmp_path, clock=_clock, id_factory=_ids()
    )
    _start(tmp_path, created.object_id)
    with pytest.raises(IllegalTransitionError):
        save_run_object(
            created.object_id,
            _text(mu=0.5),
            expected_revision=2,
            results=tmp_path,
            clock=_clock,
        )
    with pytest.raises(IllegalTransitionError):
        delete_run_object(created.object_id, expected_revision=2, results=tmp_path)


def test_delete_removes_drafts_and_planned_objects(tmp_path: Path) -> None:
    """Deleting a draft removes it from disk and from discovery."""
    ids = _ids()
    draft = create_run_object("N: [\n", results=tmp_path, clock=_clock, id_factory=ids)
    planned = create_run_object(_text(), results=tmp_path, clock=_clock, id_factory=ids)
    delete_run_object(draft.object_id, expected_revision=1, results=tmp_path)
    assert not draft.directory.exists()
    listing = list_run_objects(tmp_path)
    assert [item.object_id for item in listing.objects] == [planned.object_id]
    with pytest.raises(RunObjectNotFoundError):
        read_run_object(draft.object_id, results=tmp_path)


def test_clone_copies_only_the_configuration(tmp_path: Path) -> None:
    """A clone of a draft is a new draft with provenance."""
    ids = _ids()
    source = create_run_object(
        _text(N=0), results=tmp_path, clock=_clock, id_factory=ids
    )
    clone = clone_run_object(
        source.object_id, results=tmp_path, clock=_clock, id_factory=ids
    )
    assert clone.object_id != source.object_id
    assert clone.record.state is RunState.DRAFT
    assert clone.record.source_object_id == source.object_id
    assert clone.config_text == source.config_text


def test_listing_reports_unreadable_objects_without_inventing_them(
    tmp_path: Path,
) -> None:
    """Corrupt, foreign, and hidden entries never become objects."""
    ids = _ids()
    good = create_run_object(_text(), results=tmp_path, clock=_clock, id_factory=ids)
    corrupt = create_run_object(_text(), results=tmp_path, clock=_clock, id_factory=ids)
    (corrupt.directory / "run.json").write_text("{", encoding="utf-8")
    root = objects_directory(tmp_path)
    (root / "not-an-object").mkdir()
    (root / ".obj-staging").mkdir()
    future = object_directory("obj-" + "f" * 32, tmp_path)
    future.mkdir()
    document = good.record.to_dict()
    document["schema_version"] = 99
    (future / "run.json").write_text(json.dumps(document), encoding="utf-8")
    listing = list_run_objects(tmp_path)
    assert [item.object_id for item in listing.objects] == [good.object_id]
    messages = {item.directory.name: item.message for item in listing.diagnostics}
    assert set(messages) == {corrupt.object_id, "not-an-object", future.name}
    assert "schema_version" in messages[future.name]


def test_objects_are_invisible_to_legacy_result_discovery(tmp_path: Path) -> None:
    """Legacy scans of `results/*/manifest.json` find no object."""
    create_run_object(_text(), results=tmp_path, clock=_clock, id_factory=_ids())
    assert list(tmp_path.glob("*/manifest.json")) == []


def test_object_ids_cannot_escape_the_namespace(tmp_path: Path) -> None:
    """A malformed object ID is refused before touching the file system."""
    with pytest.raises(ValueError, match="object_id"):
        read_run_object("../../etc", results=tmp_path)
    with pytest.raises(ValueError, match="object_id"):
        create_run_object(_text(), results=tmp_path, id_factory=lambda: "../x")


def test_lock_excludes_a_second_holder(tmp_path: Path) -> None:
    """A held lock refuses a non-blocking second holder until released."""
    path = tmp_path / "locks" / "a.lock"
    assert not is_lock_held(path)
    with exclusive_lock(path):
        assert is_lock_held(path)
        with (
            pytest.raises(LockUnavailableError),
            exclusive_lock(path, blocking=False),
        ):
            pass
    assert not is_lock_held(path)
