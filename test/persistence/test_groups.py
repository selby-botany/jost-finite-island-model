"""Unit tests for `fim.persistence.groups` (Study and Experiment manifests)."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fim.persistence.groups import (
    ExperimentManifest,
    StudyManifest,
    add_run_to_study,
    add_study_to_experiment,
    copy_experiment,
    copy_study,
    create_experiment,
    create_study,
    delete_experiment,
    delete_study,
    experiment_manifest_path,
    get_experiment,
    get_study,
    list_experiments,
    list_studies,
    read_experiment_manifest,
    read_study_manifest,
    resolve_run_directory,
    study_manifest_path,
    write_experiment_manifest,
    write_study_manifest,
)


def _study(**overrides: object) -> StudyManifest:
    """Build one minimal, otherwise-valid `StudyManifest` for validation tests."""
    fields: dict[str, object] = {
        "schema_version": 1,
        "study_id": "study-aaaaaaaa",
        "name": "Ring-topology migration sweep",
        "description": "How D responds to m.",
        "created_at": "2026-09-15T09:12:00.000000Z",
        "updated_at": "2026-09-17T11:40:22.500000Z",
        "run_directories": ("run-a", "run-b"),
        "sweep_spec": None,
    }
    fields.update(overrides)
    return StudyManifest(**fields)  # type: ignore[arg-type]


def _experiment(**overrides: object) -> ExperimentManifest:
    """Build one minimal, otherwise-valid `ExperimentManifest` for validation tests."""
    fields: dict[str, object] = {
        "schema_version": 1,
        "experiment_id": "experiment-aaaaaaaa",
        "name": "Migration topology and differentiation",
        "description": "Characterizing topology effects.",
        "created_at": "2026-09-10T08:00:00.000000Z",
        "updated_at": "2026-09-17T11:40:22.500000Z",
        "study_ids": ("study-aaaaaaaa",),
    }
    fields.update(overrides)
    return ExperimentManifest(**fields)  # type: ignore[arg-type]


def _run_directory(results: Path, name: str) -> Path:
    """Create a minimal fake run directory: just enough for `manifest.json` to exist."""
    directory = results / name
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text("{}", encoding="utf-8")
    return directory


# -- StudyManifest / ExperimentManifest dataclasses --------------------------


def test_study_to_dict_from_dict_round_trips() -> None:
    """A written-then-read Study manifest is field-for-field identical."""
    study = _study()

    assert StudyManifest.from_dict(study.to_dict()) == study


def test_study_run_count_matches_run_directories() -> None:
    """`run_count` is derived, never independently settable."""
    assert _study(run_directories=()).run_count == 0
    assert _study(run_directories=("a", "b", "c")).run_count == 3


def test_study_from_dict_rejects_a_missing_required_field() -> None:
    """A payload missing `name` is a clear error."""
    payload = _study().to_dict()
    del payload["name"]

    with pytest.raises(ValueError, match="study manifest is missing: name"):
        StudyManifest.from_dict(payload)


def test_study_name_must_not_be_blank() -> None:
    """A whitespace-only Study name is rejected."""
    with pytest.raises(ValueError, match="name must not be blank"):
        _study(name="   ")


def test_study_sweep_spec_must_be_an_object_or_null() -> None:
    """A non-object `sweep_spec` is rejected, not silently coerced."""
    payload = _study().to_dict()
    payload["sweep_spec"] = "not an object"

    with pytest.raises(ValueError, match="sweep_spec"):
        StudyManifest.from_dict(payload)


def test_experiment_to_dict_from_dict_round_trips() -> None:
    """A written-then-read Experiment manifest is field-for-field identical."""
    experiment = _experiment()

    assert ExperimentManifest.from_dict(experiment.to_dict()) == experiment


def test_experiment_study_count_matches_study_ids() -> None:
    """`study_count` is derived, never independently settable."""
    assert _experiment(study_ids=()).study_count == 0
    assert _experiment(study_ids=("a", "b")).study_count == 2


def test_write_study_manifest_round_trips_through_disk(tmp_path: Path) -> None:
    """`write_study_manifest`/`read_study_manifest` round-trip through a real file."""
    path = study_manifest_path("study-aaaaaaaa", results=tmp_path)
    study = _study()

    write_study_manifest(path, study)

    assert read_study_manifest(path) == study


def test_write_experiment_manifest_round_trips_through_disk(tmp_path: Path) -> None:
    """`write_experiment_manifest`/`read_experiment_manifest` round-trip."""
    path = experiment_manifest_path("experiment-aaaaaaaa", results=tmp_path)
    experiment = _experiment()

    write_experiment_manifest(path, experiment)

    assert read_experiment_manifest(path) == experiment


# -- Study CRUD ---------------------------------------------------------------


def test_create_study_writes_an_empty_study(tmp_path: Path) -> None:
    """A freshly created Study has no members yet."""
    study = create_study("Ring sweep", "A description.", results=tmp_path)

    assert study.name == "Ring sweep"
    assert study.description == "A description."
    assert study.run_directories == ()
    assert study.study_id.startswith("study-")
    assert get_study(study.study_id, results=tmp_path) == study


def test_create_study_rejects_a_blank_name(tmp_path: Path) -> None:
    """A whitespace-only name is rejected before anything is written."""
    with pytest.raises(ValueError, match="must not be blank"):
        create_study("   ", results=tmp_path)


def test_get_study_raises_for_an_unknown_id(tmp_path: Path) -> None:
    """Looking up a Study that was never created is a clear error."""
    with pytest.raises(ValueError, match="no such study: study-ffffffff"):
        get_study("study-ffffffff", results=tmp_path)


def test_list_studies_returns_every_study_oldest_first(tmp_path: Path) -> None:
    """Listing order is creation order, for stable per-parent display ordinals."""
    clock_a = lambda: datetime(2026, 9, 15, 9, 0, 0, tzinfo=UTC)  # noqa: E731
    clock_b = lambda: datetime(2026, 9, 16, 9, 0, 0, tzinfo=UTC)  # noqa: E731
    first = create_study("First", results=tmp_path, clock=clock_a)
    second = create_study("Second", results=tmp_path, clock=clock_b)

    assert [study.study_id for study in list_studies(results=tmp_path)] == [
        first.study_id,
        second.study_id,
    ]


def test_list_studies_returns_empty_list_when_no_index_exists(tmp_path: Path) -> None:
    """A `results/` tree with no Study ever created lists as empty, not an error."""
    assert list_studies(results=tmp_path) == []


def test_list_studies_skips_an_unreadable_manifest(tmp_path: Path) -> None:
    """One malformed Study file does not hide the rest of the listing."""
    good = create_study("Good", results=tmp_path)
    bad_path = study_manifest_path("study-deadbeef", results=tmp_path)
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.write_text("not json", encoding="utf-8")

    assert [study.study_id for study in list_studies(results=tmp_path)] == [
        good.study_id
    ]


def test_add_run_to_study_appends_a_reference(tmp_path: Path) -> None:
    """Adding a run records its directory name, without moving anything."""
    study = create_study("Ring sweep", results=tmp_path)
    run_directory = _run_directory(tmp_path, "run-a")

    updated = add_run_to_study(study.study_id, run_directory, results=tmp_path)

    assert updated.run_directories == ("run-a",)
    assert run_directory.is_dir()  # never moved


def test_add_run_to_study_is_idempotent(tmp_path: Path) -> None:
    """Adding the same run twice is a no-op, not a duplicate entry."""
    study = create_study("Ring sweep", results=tmp_path)
    run_directory = _run_directory(tmp_path, "run-a")
    add_run_to_study(study.study_id, run_directory, results=tmp_path)

    updated = add_run_to_study(study.study_id, run_directory, results=tmp_path)

    assert updated.run_directories == ("run-a",)


def test_add_run_to_study_stores_an_absolute_path_outside_results(
    tmp_path: Path,
) -> None:
    """A run published outside `results/` is stored as an absolute path."""
    results = tmp_path / "results"
    results.mkdir()
    study = create_study("Ring sweep", results=results)
    elsewhere = tmp_path / "elsewhere" / "run-x"
    elsewhere.mkdir(parents=True)
    (elsewhere / "manifest.json").write_text("{}", encoding="utf-8")

    updated = add_run_to_study(study.study_id, elsewhere, results=results)

    assert updated.run_directories == (str(elsewhere.resolve()),)


def test_add_run_to_study_raises_for_an_unknown_study(tmp_path: Path) -> None:
    """Adding a run to a nonexistent Study is a clear error."""
    run_directory = _run_directory(tmp_path, "run-a")

    with pytest.raises(ValueError, match="no such study"):
        add_run_to_study("study-ffffffff", run_directory, results=tmp_path)


def test_delete_study_removes_its_member_runs_by_default(tmp_path: Path) -> None:
    """Deleting a Study deletes every Run it references (confirmed product decision)."""
    study = create_study("Ring sweep", results=tmp_path)
    run_directory = _run_directory(tmp_path, "run-a")
    add_run_to_study(study.study_id, run_directory, results=tmp_path)

    delete_study(study.study_id, results=tmp_path)

    assert not run_directory.exists()
    with pytest.raises(ValueError, match="no such study"):
        get_study(study.study_id, results=tmp_path)


def test_delete_study_can_keep_its_runs(tmp_path: Path) -> None:
    """`delete_runs=False` removes only the grouping, never the Run data."""
    study = create_study("Ring sweep", results=tmp_path)
    run_directory = _run_directory(tmp_path, "run-a")
    add_run_to_study(study.study_id, run_directory, results=tmp_path)

    delete_study(study.study_id, results=tmp_path, delete_runs=False)

    assert run_directory.is_dir()


def test_delete_study_tolerates_an_already_missing_run_directory(
    tmp_path: Path,
) -> None:
    """A run directory deleted out-of-band does not make Study deletion fail."""
    study = create_study("Ring sweep", results=tmp_path)
    run_directory = _run_directory(tmp_path, "run-a")
    add_run_to_study(study.study_id, run_directory, results=tmp_path)
    shutil.rmtree(run_directory)

    delete_study(study.study_id, results=tmp_path)  # must not raise


def test_copy_study_creates_an_independent_study_with_the_same_runs(
    tmp_path: Path,
) -> None:
    """Copying a Study shares its run references without duplicating any data."""
    study = create_study("Ring sweep", "Original.", results=tmp_path)
    run_directory = _run_directory(tmp_path, "run-a")
    add_run_to_study(study.study_id, run_directory, results=tmp_path)

    copied = copy_study(study.study_id, name="Ring sweep copy", results=tmp_path)

    assert copied.study_id != study.study_id
    assert copied.name == "Ring sweep copy"
    assert copied.description == "Original."
    assert copied.run_directories == ("run-a",)
    # Diverging the copy must never affect the source.
    other_run = _run_directory(tmp_path, "run-b")
    add_run_to_study(copied.study_id, other_run, results=tmp_path)
    assert get_study(study.study_id, results=tmp_path).run_directories == ("run-a",)


# -- Experiment CRUD ------------------------------------------------------


def test_create_experiment_writes_an_empty_experiment(tmp_path: Path) -> None:
    """A freshly created Experiment has no member Studies yet."""
    experiment = create_experiment("Topology", "A description.", results=tmp_path)

    assert experiment.name == "Topology"
    assert experiment.study_ids == ()
    assert experiment.experiment_id.startswith("experiment-")


def test_add_study_to_experiment_appends_and_is_idempotent(tmp_path: Path) -> None:
    """Adding a Study to an Experiment behaves like `add_run_to_study` one level up."""
    experiment = create_experiment("Topology", results=tmp_path)
    study = create_study("Ring sweep", results=tmp_path)

    add_study_to_experiment(experiment.experiment_id, study.study_id, results=tmp_path)
    updated = add_study_to_experiment(
        experiment.experiment_id, study.study_id, results=tmp_path
    )

    assert updated.study_ids == (study.study_id,)


def test_add_study_to_experiment_raises_for_an_unknown_study(tmp_path: Path) -> None:
    """An Experiment cannot reference a Study that does not exist."""
    experiment = create_experiment("Topology", results=tmp_path)

    with pytest.raises(ValueError, match="no such study"):
        add_study_to_experiment(
            experiment.experiment_id, "study-ffffffff", results=tmp_path
        )


def test_list_experiments_returns_every_experiment_oldest_first(
    tmp_path: Path,
) -> None:
    """Listing order matches `list_studies`'s own oldest-first convention."""
    clock_a = lambda: datetime(2026, 9, 10, 8, 0, 0, tzinfo=UTC)  # noqa: E731
    clock_b = lambda: datetime(2026, 9, 11, 8, 0, 0, tzinfo=UTC)  # noqa: E731
    first = create_experiment("First", results=tmp_path, clock=clock_a)
    second = create_experiment("Second", results=tmp_path, clock=clock_b)

    assert [
        experiment.experiment_id for experiment in list_experiments(results=tmp_path)
    ] == [first.experiment_id, second.experiment_id]


def test_delete_experiment_cascades_to_studies_and_their_runs(tmp_path: Path) -> None:
    """Deleting an Experiment deletes its Studies, and transitively their Runs."""
    experiment = create_experiment("Topology", results=tmp_path)
    study = create_study("Ring sweep", results=tmp_path)
    run_directory = _run_directory(tmp_path, "run-a")
    add_run_to_study(study.study_id, run_directory, results=tmp_path)
    add_study_to_experiment(experiment.experiment_id, study.study_id, results=tmp_path)

    delete_experiment(experiment.experiment_id, results=tmp_path)

    assert not run_directory.exists()
    with pytest.raises(ValueError, match="no such study"):
        get_study(study.study_id, results=tmp_path)
    with pytest.raises(ValueError, match="no such experiment"):
        get_experiment(experiment.experiment_id, results=tmp_path)


def test_delete_experiment_can_keep_its_studies(tmp_path: Path) -> None:
    """`delete_studies=False` removes only the Experiment, never its Studies."""
    experiment = create_experiment("Topology", results=tmp_path)
    study = create_study("Ring sweep", results=tmp_path)
    add_study_to_experiment(experiment.experiment_id, study.study_id, results=tmp_path)

    delete_experiment(experiment.experiment_id, results=tmp_path, delete_studies=False)

    assert get_study(study.study_id, results=tmp_path) == study


def test_copy_experiment_creates_an_independent_experiment_with_the_same_studies(
    tmp_path: Path,
) -> None:
    """Copying an Experiment shares its study references without duplicating them."""
    experiment = create_experiment("Topology", "Original.", results=tmp_path)
    study = create_study("Ring sweep", results=tmp_path)
    add_study_to_experiment(experiment.experiment_id, study.study_id, results=tmp_path)

    copied = copy_experiment(
        experiment.experiment_id, name="Topology copy", results=tmp_path
    )

    assert copied.experiment_id != experiment.experiment_id
    assert copied.study_ids == (study.study_id,)


# -- resolve_run_directory ------------------------------------------------


def test_resolve_run_directory_accepts_an_existing_directory(tmp_path: Path) -> None:
    """A real run directory (relative to `results`) resolves to itself."""
    run_directory = _run_directory(tmp_path, "run-a")

    assert resolve_run_directory("run-a", results=tmp_path) == run_directory.resolve()


def test_resolve_run_directory_accepts_an_absolute_directory_path(
    tmp_path: Path,
) -> None:
    """An absolute directory path resolves directly, bypassing `results` entirely."""
    run_directory = _run_directory(tmp_path, "run-a")

    assert (
        resolve_run_directory(str(run_directory), results=tmp_path)
        == run_directory.resolve()
    )


def test_resolve_run_directory_accepts_a_manifest_path(tmp_path: Path) -> None:
    """A path directly to `manifest.json` resolves to its parent directory."""
    run_directory = _run_directory(tmp_path, "run-a")

    resolved = resolve_run_directory(
        str(run_directory / "manifest.json"), results=tmp_path
    )

    assert resolved == run_directory.resolve()


def test_resolve_run_directory_raises_when_nothing_matches(tmp_path: Path) -> None:
    """A reference matching no directory, manifest path, or run_id is a clear error."""
    with pytest.raises(ValueError, match="could not identify a run"):
        resolve_run_directory("nope", results=tmp_path)
