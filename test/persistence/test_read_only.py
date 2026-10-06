"""Read-only Studies, Experiments, and Runs (`fim.persistence.groups`).

Read-only examples design (2026-10-05), section 3: every edit to a
read-only item is refused with `ReadOnlyError`, viewing and copying stay
allowed, and only the seeding write path (`write_read_only_study`/
`write_read_only_experiment`) bypasses the checks.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fim.persistence.groups import (
    ExperimentManifest,
    ReadOnlyError,
    StudyManifest,
    add_run_to_study,
    add_study_to_experiment,
    clear_study_runs,
    copy_experiment,
    copy_study,
    create_experiment,
    create_study,
    delete_experiment,
    delete_runs,
    delete_study,
    experiment_manifest_path,
    get_experiment,
    get_study,
    is_run_read_only,
    prune_missing_studies,
    read_study_manifest,
    remove_run_references,
    study_manifest_path,
    supersede_run,
    update_experiment_details,
    update_study_details,
    write_read_only_experiment,
    write_read_only_study,
)
from fim.persistence.run_metadata import (
    read_run_metadata,
    replace_run_metadata,
    run_metadata_path,
)


def _clock(day: int) -> datetime:
    """Return a fixed instant on one day of October 2026."""
    return datetime(2026, 10, day, 12, 0, 0, tzinfo=UTC)


def _make_run(directory: Path, *, read_only: bool) -> Path:
    """Create a minimal run directory whose manifest marks it read-only or not.

    `is_run_read_only` reads only `manifest.json`'s `parameters`, so a
    full manifest is not needed here.
    """
    directory.mkdir(parents=True)
    parameters: dict[str, object] = {"N": 20, "seed": 1}
    if read_only:
        parameters["_read_only"] = True
    (directory / "manifest.json").write_text(
        json.dumps({"parameters": parameters}), encoding="utf-8"
    )
    return directory


def _seed(results: Path) -> tuple[StudyManifest, ExperimentManifest, Path]:
    """Seed one read-only example run, Study, and Experiment under `results`."""
    run = _make_run(results / "examples" / "ring", read_only=True)
    study = write_read_only_study(
        "study-examples-migration",
        name="Migration structure",
        run_directories=[run],
        results=results,
        clock=lambda: _clock(1),
    )
    experiment = write_read_only_experiment(
        "experiment-examples",
        name="Examples",
        study_ids=[study.study_id],
        results=results,
        clock=lambda: _clock(1),
    )
    return study, experiment, run


# Run read-only detection.


def test_is_run_read_only_reads_the_manifest_parameters(tmp_path: Path) -> None:
    """Only a JSON `true` under `parameters._read_only` makes a run read-only."""
    assert is_run_read_only(_make_run(tmp_path / "ro", read_only=True))
    assert not is_run_read_only(_make_run(tmp_path / "rw", read_only=False))
    assert not is_run_read_only(tmp_path / "missing")

    malformed = tmp_path / "malformed"
    malformed.mkdir()
    (malformed / "manifest.json").write_text("not json", encoding="utf-8")
    assert not is_run_read_only(malformed)

    stringly = tmp_path / "stringly"
    stringly.mkdir()
    (stringly / "manifest.json").write_text(
        json.dumps({"parameters": {"_read_only": "true"}}), encoding="utf-8"
    )
    assert not is_run_read_only(stringly)


def test_read_only_error_is_a_value_error() -> None:
    """Existing `except ValueError` handlers report it without changes."""
    assert issubclass(ReadOnlyError, ValueError)


# Manifest shape.


def test_read_only_is_written_only_when_true(tmp_path: Path) -> None:
    """An editable manifest keeps its JSON shape; a read-only one adds the key."""
    editable = create_study("Mine", results=tmp_path, clock=lambda: _clock(1))
    study, experiment, _ = _seed(tmp_path)

    editable_payload = json.loads(
        study_manifest_path(editable.study_id, results=tmp_path).read_text("utf-8")
    )
    study_payload = json.loads(
        study_manifest_path(study.study_id, results=tmp_path).read_text("utf-8")
    )
    experiment_payload = json.loads(
        experiment_manifest_path(experiment.experiment_id, results=tmp_path).read_text(
            "utf-8"
        )
    )
    assert "read_only" not in editable_payload
    assert study_payload["read_only"] is True
    assert experiment_payload["read_only"] is True


def test_old_manifests_without_read_only_still_load() -> None:
    """A manifest written before `read_only` existed reads back editable."""
    study_payload = {
        "schema_version": 1,
        "study_id": "study-aaaaaaaa",
        "name": "Old study",
        "description": None,
        "created_at": "2026-09-15T09:12:00Z",
        "updated_at": "2026-09-15T09:12:00Z",
        "run_directories": ["run-a"],
        "run_count": 1,
        "sweep_spec": None,
    }
    experiment_payload = {
        "schema_version": 1,
        "experiment_id": "experiment-aaaaaaaa",
        "name": "Old experiment",
        "description": None,
        "created_at": "2026-09-15T09:12:00Z",
        "updated_at": "2026-09-15T09:12:00Z",
        "study_ids": ["study-aaaaaaaa"],
        "study_count": 1,
    }

    assert StudyManifest.from_dict(study_payload).read_only is False
    assert ExperimentManifest.from_dict(experiment_payload).read_only is False


def test_a_non_boolean_read_only_is_rejected() -> None:
    """`read_only` must be a real boolean when present."""
    study = StudyManifest(
        schema_version=1,
        study_id="study-aaaaaaaa",
        name="Study",
        description=None,
        created_at="2026-09-15T09:12:00Z",
        updated_at="2026-09-15T09:12:00Z",
        run_directories=(),
    )
    payload = {**study.to_dict(), "read_only": "yes"}

    with pytest.raises(ValueError, match="'read_only' must be a boolean"):
        StudyManifest.from_dict(payload)


# Read-only Studies.


def test_a_read_only_study_cannot_be_renamed_or_described(tmp_path: Path) -> None:
    """`update_study_details` refuses, and the manifest is unchanged."""
    study, _, _ = _seed(tmp_path)
    path = study_manifest_path(study.study_id, results=tmp_path)
    before = path.read_bytes()

    with pytest.raises(ReadOnlyError, match="read-only"):
        update_study_details(
            study.study_id,
            name="Renamed",
            description=None,
            documentation=None,
            results=tmp_path,
        )

    assert path.read_bytes() == before


@pytest.mark.parametrize("delete_runs_too", [True, False])
def test_a_read_only_study_cannot_be_deleted(
    tmp_path: Path, delete_runs_too: bool
) -> None:
    """`delete_study` refuses, with or without its Runs, and deletes nothing."""
    study, _, run = _seed(tmp_path)

    with pytest.raises(ReadOnlyError):
        delete_study(study.study_id, results=tmp_path, delete_runs=delete_runs_too)

    assert get_study(study.study_id, results=tmp_path) == study
    assert run.is_dir()


def test_a_read_only_study_cannot_be_emptied(tmp_path: Path) -> None:
    """`clear_study_runs` refuses, and every Run stays."""
    study, _, run = _seed(tmp_path)

    with pytest.raises(ReadOnlyError):
        clear_study_runs(study.study_id, results=tmp_path)

    assert get_study(study.study_id, results=tmp_path).run_directories == (
        "examples/ring",
    )
    assert run.is_dir()


def test_a_read_only_study_cannot_gain_runs(tmp_path: Path) -> None:
    """`add_run_to_study` refuses a read-only Study."""
    study, _, _ = _seed(tmp_path)
    other = _make_run(tmp_path / "mine", read_only=False)

    with pytest.raises(ReadOnlyError):
        add_run_to_study(study.study_id, other, results=tmp_path)

    assert get_study(study.study_id, results=tmp_path) == study


def test_remove_run_references_leaves_a_read_only_study_alone(tmp_path: Path) -> None:
    """A dangling link in a read-only Study is left for re-seeding to repair."""
    study, _, run = _seed(tmp_path)

    remove_run_references([run], results=tmp_path)

    assert get_study(study.study_id, results=tmp_path) == study


def test_copying_a_read_only_study_gives_an_editable_copy(tmp_path: Path) -> None:
    """Copying stays allowed, and the copy can be edited."""
    study, experiment, _ = _seed(tmp_path)

    study_copy = copy_study(study.study_id, name="My copy", results=tmp_path)
    experiment_copy = copy_experiment(
        experiment.experiment_id, name="My examples", results=tmp_path
    )

    assert not study_copy.read_only
    assert study_copy.run_directories == study.run_directories
    assert not experiment_copy.read_only
    update_study_details(
        study_copy.study_id,
        name="Renamed copy",
        description=None,
        documentation=None,
        results=tmp_path,
    )


# Read-only Experiments.


def test_a_read_only_experiment_cannot_be_renamed_or_described(
    tmp_path: Path,
) -> None:
    """`update_experiment_details` refuses, and the manifest is unchanged."""
    _, experiment, _ = _seed(tmp_path)

    with pytest.raises(ReadOnlyError, match="experiment 'Examples'"):
        update_experiment_details(
            experiment.experiment_id,
            name="Renamed",
            description="New.",
            documentation=None,
            results=tmp_path,
        )

    assert get_experiment(experiment.experiment_id, results=tmp_path) == experiment


@pytest.mark.parametrize("delete_studies_too", [True, False])
def test_a_read_only_experiment_cannot_be_deleted(
    tmp_path: Path, delete_studies_too: bool
) -> None:
    """`delete_experiment` refuses, and its Studies and Runs stay."""
    study, experiment, run = _seed(tmp_path)

    with pytest.raises(ReadOnlyError):
        delete_experiment(
            experiment.experiment_id,
            results=tmp_path,
            delete_studies=delete_studies_too,
        )

    assert get_experiment(experiment.experiment_id, results=tmp_path) == experiment
    assert get_study(study.study_id, results=tmp_path) == study
    assert run.is_dir()


def test_a_read_only_experiment_cannot_gain_studies(tmp_path: Path) -> None:
    """`add_study_to_experiment` refuses a read-only Experiment."""
    _, experiment, _ = _seed(tmp_path)
    mine = create_study("Mine", results=tmp_path)

    with pytest.raises(ReadOnlyError):
        add_study_to_experiment(
            experiment.experiment_id, mine.study_id, results=tmp_path
        )

    assert get_experiment(experiment.experiment_id, results=tmp_path) == experiment


def test_prune_missing_studies_leaves_a_read_only_experiment_alone(
    tmp_path: Path,
) -> None:
    """A dangling Study id in a read-only Experiment is left for re-seeding."""
    study, experiment, _ = _seed(tmp_path)
    study_manifest_path(study.study_id, results=tmp_path).unlink()

    assert prune_missing_studies(results=tmp_path) == 0
    assert get_experiment(experiment.experiment_id, results=tmp_path) == experiment


# Read-only Runs.


def test_a_read_only_run_cannot_be_renamed_described_or_reclassed(
    tmp_path: Path,
) -> None:
    """`replace_run_metadata` refuses a read-only run and writes nothing."""
    run = _make_run(tmp_path / "ro", read_only=True)

    with pytest.raises(ReadOnlyError, match="cannot be renamed"):
        replace_run_metadata(run, name="Renamed", description=None)
    with pytest.raises(ReadOnlyError):
        replace_run_metadata(run, name=None, description=None, run_class="migration")

    assert not run_metadata_path(run).exists()


def test_an_editable_run_can_still_be_renamed(tmp_path: Path) -> None:
    """Control: the check refuses only read-only runs."""
    run = _make_run(tmp_path / "rw", read_only=False)

    replace_run_metadata(run, name="Renamed", description=None)

    assert read_run_metadata(run_metadata_path(run)).name == "Renamed"


def test_delete_runs_refuses_a_read_only_run_and_deletes_nothing(
    tmp_path: Path,
) -> None:
    """A selection holding a read-only run is refused whole."""
    read_only = _make_run(tmp_path / "ro", read_only=True)
    editable = _make_run(tmp_path / "rw", read_only=False)

    with pytest.raises(ReadOnlyError, match="ro; nothing was deleted"):
        delete_runs([editable, read_only], results=tmp_path)

    assert read_only.is_dir()
    assert editable.is_dir()


def test_delete_runs_deletes_editable_runs_and_unlinks_them(tmp_path: Path) -> None:
    """Control: ordinary runs are deleted, unlinked, and a gone one is tolerated."""
    first = _make_run(tmp_path / "one", read_only=False)
    second = _make_run(tmp_path / "two", read_only=False)
    study = create_study("Mine", results=tmp_path)
    add_run_to_study(study.study_id, first, results=tmp_path)
    add_run_to_study(study.study_id, second, results=tmp_path)

    deleted = delete_runs([first, tmp_path / "already-gone"], results=tmp_path)

    assert deleted == 1
    assert not first.exists()
    assert second.is_dir()
    assert get_study(study.study_id, results=tmp_path).run_directories == ("two",)


def test_supersede_run_refuses_to_replace_a_read_only_run(tmp_path: Path) -> None:
    """A read-only run is never deleted, even as an exact duplicate."""
    old = _make_run(tmp_path / "old", read_only=True)
    new = _make_run(tmp_path / "new", read_only=False)
    study = create_study("Mine", results=tmp_path)
    add_run_to_study(study.study_id, old, results=tmp_path)

    with pytest.raises(ReadOnlyError):
        supersede_run(old, new, results=tmp_path)

    assert old.is_dir()
    assert get_study(study.study_id, results=tmp_path).run_directories == ("old",)


def test_deleting_an_editable_study_keeps_its_read_only_runs(tmp_path: Path) -> None:
    """A user's Study holding an example can be deleted; the example survives."""
    example = _make_run(tmp_path / "example", read_only=True)
    mine = _make_run(tmp_path / "mine", read_only=False)
    study = create_study("Mine", results=tmp_path)
    add_run_to_study(study.study_id, example, results=tmp_path)
    add_run_to_study(study.study_id, mine, results=tmp_path)

    delete_study(study.study_id, results=tmp_path)

    assert example.is_dir()
    assert not mine.exists()


def test_emptying_an_editable_study_keeps_its_read_only_runs_linked(
    tmp_path: Path,
) -> None:
    """`clear_study_runs` deletes ordinary runs; a read-only one stays, linked."""
    example = _make_run(tmp_path / "example", read_only=True)
    mine = _make_run(tmp_path / "mine", read_only=False)
    study = create_study("Mine", results=tmp_path)
    add_run_to_study(study.study_id, example, results=tmp_path)
    add_run_to_study(study.study_id, mine, results=tmp_path)

    before = clear_study_runs(study.study_id, results=tmp_path)

    assert before.run_count == 2
    assert example.is_dir()
    assert not mine.exists()
    assert get_study(study.study_id, results=tmp_path).run_directories == ("example",)


def test_deleting_an_editable_experiment_keeps_its_read_only_studies(
    tmp_path: Path,
) -> None:
    """A user's Experiment holding an example Study can be deleted; the Study stays."""
    study, _, run = _seed(tmp_path)
    experiment = create_experiment("Mine", results=tmp_path)
    add_study_to_experiment(experiment.experiment_id, study.study_id, results=tmp_path)

    delete_experiment(experiment.experiment_id, results=tmp_path)

    assert get_study(study.study_id, results=tmp_path) == study
    assert run.is_dir()
    with pytest.raises(ValueError, match="no such experiment"):
        get_experiment(experiment.experiment_id, results=tmp_path)


def test_deleting_an_editable_study_leaves_read_only_experiments_untouched(
    tmp_path: Path,
) -> None:
    """Detaching a deleted Study never edits a read-only Experiment."""
    _, experiment, _ = _seed(tmp_path)
    mine = create_study("Mine", results=tmp_path)

    delete_study(mine.study_id, results=tmp_path)

    assert get_experiment(experiment.experiment_id, results=tmp_path) == experiment


# The seeding write path.


def test_write_read_only_study_creates_a_fixed_id_read_only_study(
    tmp_path: Path,
) -> None:
    """The seeding path writes a read-only Study at exactly the id given."""
    study, _, _ = _seed(tmp_path)

    assert study.study_id == "study-examples-migration"
    assert study.read_only
    assert study.run_directories == ("examples/ring",)
    assert (
        read_study_manifest(study_manifest_path(study.study_id, results=tmp_path))
        == study
    )


def test_write_read_only_study_bypasses_the_read_only_check(tmp_path: Path) -> None:
    """Re-seeding replaces a read-only Study, keeping `created_at`."""
    study, _, run = _seed(tmp_path)
    second = _make_run(tmp_path / "examples" / "stepping-stone", read_only=True)

    replaced = write_read_only_study(
        study.study_id,
        name="Migration structure, revised",
        run_directories=[run, second],
        description="Hubs and rings.",
        results=tmp_path,
        clock=lambda: _clock(2),
    )

    assert replaced.name == "Migration structure, revised"
    assert replaced.run_directories == ("examples/ring", "examples/stepping-stone")
    assert replaced.created_at == study.created_at
    assert replaced.updated_at == "2026-10-02T12:00:00Z"
    assert replaced.read_only


def test_write_read_only_study_is_idempotent(tmp_path: Path) -> None:
    """Seeding the same content again writes nothing and keeps `updated_at`."""
    study, _, run = _seed(tmp_path)
    path = study_manifest_path(study.study_id, results=tmp_path)
    before = path.read_bytes()

    again = write_read_only_study(
        study.study_id,
        name="Migration structure",
        run_directories=["examples/ring", run],
        results=tmp_path,
        clock=lambda: _clock(3),
    )

    assert again == study
    assert path.read_bytes() == before


def test_write_read_only_study_replaces_an_editable_study_at_its_id(
    tmp_path: Path,
) -> None:
    """A fixed seeding id is app-owned, so an editable manifest there is replaced."""
    run = _make_run(tmp_path / "examples" / "ring", read_only=True)
    write_read_only_study(
        "study-examples-x", name="X", run_directories=[], results=tmp_path
    )
    path = study_manifest_path("study-examples-x", results=tmp_path)
    payload = json.loads(path.read_text("utf-8"))
    del payload["read_only"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    rewritten = write_read_only_study(
        "study-examples-x", name="X", run_directories=[run], results=tmp_path
    )

    assert rewritten.read_only


@pytest.mark.parametrize("study_id", ["examples-migration", "study-", "experiment-x"])
def test_write_read_only_study_requires_the_study_prefix(
    tmp_path: Path, study_id: str
) -> None:
    """A seeded Study id must look like every other Study id."""
    with pytest.raises(ValueError, match="must start with 'study-'"):
        write_read_only_study(
            study_id, name="Name", run_directories=[], results=tmp_path
        )


def test_write_read_only_experiment_bypasses_and_is_idempotent(
    tmp_path: Path,
) -> None:
    """Re-seeding the Experiment replaces it when changed, and not otherwise."""
    study, experiment, _ = _seed(tmp_path)
    other = write_read_only_study(
        "study-examples-literature",
        name="Literature comparisons",
        run_directories=[],
        results=tmp_path,
        clock=lambda: _clock(1),
    )

    same = write_read_only_experiment(
        experiment.experiment_id,
        name="Examples",
        study_ids=[study.study_id],
        results=tmp_path,
        clock=lambda: _clock(4),
    )
    changed = write_read_only_experiment(
        experiment.experiment_id,
        name="Examples",
        study_ids=[study.study_id, other.study_id],
        results=tmp_path,
        clock=lambda: _clock(5),
    )

    assert same == experiment
    assert changed.study_ids == (study.study_id, other.study_id)
    assert changed.created_at == experiment.created_at
    assert changed.updated_at == "2026-10-05T12:00:00Z"
    assert changed.read_only


def test_write_read_only_experiment_requires_existing_studies(tmp_path: Path) -> None:
    """Studies are seeded first; an unknown member Study is an error."""
    with pytest.raises(ValueError, match="no such study"):
        write_read_only_experiment(
            "experiment-examples",
            name="Examples",
            study_ids=["study-examples-missing"],
            results=tmp_path,
        )


def test_write_read_only_experiment_requires_the_experiment_prefix(
    tmp_path: Path,
) -> None:
    """A seeded Experiment id must look like every other Experiment id."""
    with pytest.raises(ValueError, match="must start with 'experiment-'"):
        write_read_only_experiment(
            "examples", name="Examples", study_ids=[], results=tmp_path
        )
