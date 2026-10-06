"""Read-only examples through `fim.gui.app.Api`'s bridge methods.

Read-only examples design (`20261005-claude-opus-5-5-read-only-examples-
and-classes-design.md`, `selby/restricted`), section 3: every bridge
method that edits a Study, an Experiment, or a Run reports a refusal as
`{"ok": False, "message": ...}` rather than raising, and deleting is all
or nothing with respect to read-only items. Window-free, like
`test_app_api.py`: these are plain Python calls.

A read-only Run here is a directory whose `manifest.json` parameters
carry `_read_only: true`, which is all `groups.is_run_read_only` reads;
read-only Studies and Experiments are written through the seeding
writers (`groups.write_read_only_study`/`write_read_only_experiment`).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from fim import paths
from fim.gui import app as app_module
from fim.gui.app import Api
from fim.persistence import groups
from fim.persistence.run_metadata import read_run_metadata, run_metadata_path

_FIXED_CLOCK = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


@pytest.fixture
def results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point `paths.results_directory()` at an isolated directory."""
    root = tmp_path / "results"
    root.mkdir()
    monkeypatch.setattr(paths, "results_directory", lambda: root)
    return root


def _run(results: Path, name: str, *, read_only: bool = False) -> Path:
    """Write a minimal run directory: just enough manifest to be read-only or not."""
    directory = results / name
    directory.mkdir(parents=True)
    parameters: dict[str, Any] = {"N": 10}
    if read_only:
        parameters["_read_only"] = True
    (directory / "manifest.json").write_text(
        json.dumps({"parameters": parameters}), encoding="utf-8"
    )
    return directory


def _read_only_study(results: Path, study_id: str, runs: list[Path]) -> str:
    """Write a read-only Study holding `runs`."""
    groups.write_read_only_study(
        study_id,
        name=f"Examples {study_id}",
        run_directories=runs,
        results=results,
        clock=lambda: _FIXED_CLOCK,
    )
    return study_id


def _read_only_experiment(results: Path, study_ids: list[str]) -> str:
    """Write the read-only Experiment holding `study_ids`."""
    groups.write_read_only_experiment(
        "experiment-examples",
        name="Examples",
        study_ids=study_ids,
        results=results,
        clock=lambda: _FIXED_CLOCK,
    )
    return "experiment-examples"


def test_delete_runs_is_all_or_nothing_when_one_run_is_read_only(results: Path) -> None:
    """One read-only run in the selection means nothing is deleted."""
    ordinary = _run(results, "run-ordinary")
    example = _run(results, "run-example", read_only=True)

    result = Api().delete_runs([str(ordinary), str(example)])

    assert result["ok"] is False
    assert "run-example" in result["message"]
    assert "nothing was deleted" in result["message"]
    assert ordinary.is_dir()
    assert example.is_dir()


def test_delete_runs_without_read_only_runs_still_deletes(results: Path) -> None:
    """The ordinary path is unchanged: every existing directory goes."""
    first = _run(results, "run-a")
    second = _run(results, "run-b")

    result = Api().delete_runs([str(first), str(second), str(results / "gone")])

    assert result == {"ok": True, "deletedCount": 2}
    assert not first.exists()
    assert not second.exists()


@pytest.mark.parametrize("kind", ["run", "study", "experiment"])
def test_delete_selected_refuses_a_selection_holding_a_read_only_item(
    results: Path, kind: str
) -> None:
    """A read-only item anywhere in the selection: nothing at all is deleted."""
    api = Api()
    example = _run(results, "run-example", read_only=True)
    study_id = _read_only_study(results, "study-examples-a", [example])
    experiment_id = _read_only_experiment(results, [study_id])
    ordinary_study = api.create_study("Mine")["studyId"]
    ordinary_run = _run(results, "run-mine")
    groups.add_run_to_study(ordinary_study, ordinary_run)
    read_only_item = {
        "run": {"kind": "run", "directory": str(example)},
        "study": {"kind": "study", "studyId": study_id},
        "experiment": {"kind": "experiment", "experimentId": experiment_id},
    }[kind]

    result = api.delete_selected(
        [
            {"kind": "study", "studyId": ordinary_study},
            {"kind": "run", "directory": str(ordinary_run)},
            read_only_item,
        ]
    )

    assert result["ok"] is False
    assert "nothing was deleted" in result["message"]
    assert ordinary_run.is_dir()
    assert groups.get_study(ordinary_study).run_count == 1
    assert example.is_dir()


def test_delete_selected_skips_a_read_only_run_its_selected_study_implies(
    results: Path,
) -> None:
    """Home's cascade sends a selected Study's example run too; it is kept."""
    api = Api()
    study_id = api.create_study("Mine")["studyId"]
    own = _run(results, "run-own")
    example = _run(results, "run-example", read_only=True)
    groups.add_run_to_study(study_id, own)
    groups.add_run_to_study(study_id, example)

    result = api.delete_selected(
        [
            {"kind": "study", "studyId": study_id},
            {"kind": "run", "directory": str(own)},
            {"kind": "run", "directory": str(example)},
        ]
    )

    assert result == {
        "ok": True,
        "deletedRunCount": 0,
        "deletedStudyCount": 1,
        "deletedExperimentCount": 0,
    }
    assert not own.exists()
    assert example.is_dir()


def test_every_listing_and_details_payload_says_what_is_read_only(
    results: Path,
) -> None:
    """`readOnly` rides on studies, experiments, details, and run context."""
    api = Api()
    example = _run(results, "run-example", read_only=True)
    study_id = _read_only_study(results, "study-examples-a", [example])
    _read_only_experiment(results, [study_id])
    own_study = api.create_study("Mine")["studyId"]

    studies = {row["studyId"]: row["readOnly"] for row in api.list_studies()}
    experiments = {
        row["experimentId"]: row["readOnly"] for row in api.list_experiments()
    }
    context = api.get_run_context(str(example))

    assert studies == {study_id: True, own_study: False}
    assert experiments == {"experiment-examples": True}
    assert api.get_details("run", str(example))["details"]["readOnly"] is True
    assert api.get_details("study", study_id)["details"]["readOnly"] is True
    assert api.get_details("study", own_study)["details"]["readOnly"] is False
    assert context["run"]["readOnly"] is True
    assert context["study"]["readOnly"] is True
    assert context["experiment"]["readOnly"] is True


def test_delete_study_counts_a_read_only_member_as_kept(results: Path) -> None:
    """An editable Study's example member survives and is reported as kept."""
    api = Api()
    study_id = api.create_study("Mine")["studyId"]
    own = _run(results, "run-own")
    example = _run(results, "run-example", read_only=True)
    groups.add_run_to_study(study_id, own)
    groups.add_run_to_study(study_id, example)

    result = api.delete_study(study_id)

    assert result == {
        "ok": True,
        "deletedRunCount": 1,
        "keptRunCount": 1,
        "readOnlyRunCount": 1,
    }
    assert not own.exists()
    assert example.is_dir()


def test_delete_study_runs_counts_a_read_only_member_as_kept(results: Path) -> None:
    """Emptying a Study keeps its example member, in the Study and on disk."""
    api = Api()
    study_id = api.create_study("Mine")["studyId"]
    own = _run(results, "run-own")
    example = _run(results, "run-example", read_only=True)
    groups.add_run_to_study(study_id, own)
    groups.add_run_to_study(study_id, example)

    result = api.delete_study_runs(study_id)

    assert result == {
        "ok": True,
        "deletedRunCount": 1,
        "keptRunCount": 1,
        "readOnlyRunCount": 1,
    }
    assert groups.study_run_directories(groups.get_study(study_id)) == [example]


def test_a_run_both_shared_and_read_only_is_counted_once(results: Path) -> None:
    """Kept is a set of runs, not a sum of reasons."""
    api = Api()
    first = api.create_study("First")["studyId"]
    second = api.create_study("Second")["studyId"]
    example = _run(results, "run-example", read_only=True)
    groups.add_run_to_study(first, example)
    groups.add_run_to_study(second, example)

    result = api.delete_study(first)

    assert result["keptRunCount"] == 1
    assert result["readOnlyRunCount"] == 1
    assert result["deletedRunCount"] == 0


def test_delete_experiment_does_not_count_a_kept_read_only_study(results: Path) -> None:
    """An editable Experiment holding an example Study keeps and skips it."""
    api = Api()
    example_study = _read_only_study(
        results, "study-examples-a", [_run(results, "run-example", read_only=True)]
    )
    experiment_id = api.create_experiment("Mine")["experimentId"]
    own_study = api.create_study("Own", experiment_id=experiment_id)["studyId"]
    api.add_study_to_experiment(experiment_id, example_study)

    result = api.delete_experiment(experiment_id)

    assert result == {"ok": True, "deletedStudyCount": 1}
    assert groups.get_study(example_study).read_only is True
    with pytest.raises(ValueError, match="no such study"):
        groups.get_study(own_study)


@pytest.mark.parametrize(
    ("method", "arguments"),
    [
        ("delete_study", ("study-examples-a",)),
        ("delete_study_runs", ("study-examples-a",)),
        ("delete_experiment", ("experiment-examples",)),
        ("update_study_details", ("study-examples-a", "New", "", "")),
        ("update_experiment_details", ("experiment-examples", "New", "", "")),
        ("add_study_to_experiment", ("experiment-examples", "study-mine")),
        ("add_run_to_study", ("study-examples-a", "RUN-MINE")),
    ],
)
def test_every_group_edit_refuses_a_read_only_item_with_a_message(
    results: Path, method: str, arguments: tuple[str, ...]
) -> None:
    """Each guarded bridge method answers `ok: False` with the refusal."""
    example = _run(results, "run-example", read_only=True)
    study_id = _read_only_study(results, "study-examples-a", [example])
    _read_only_experiment(results, [study_id])
    groups.write_study_manifest(
        groups.study_manifest_path("study-mine", results=results),
        groups.StudyManifest(
            schema_version=1,
            study_id="study-mine",
            name="Mine",
            description=None,
            created_at="2026-10-05T12:00:00Z",
            updated_at="2026-10-05T12:00:00Z",
            run_directories=(),
        ),
    )
    mine = _run(results, "run-mine")
    resolved = tuple(str(mine) if value == "RUN-MINE" else value for value in arguments)
    before_study = groups.get_study(study_id)
    before_experiment = groups.get_experiment("experiment-examples")

    result = getattr(Api(), method)(*resolved)

    assert result["ok"] is False
    assert "read-only" in result["message"]
    assert groups.get_study(study_id) == before_study
    assert groups.get_experiment("experiment-examples") == before_experiment
    assert example.is_dir()


def test_update_run_details_refuses_a_read_only_run(results: Path) -> None:
    """Renaming an example run is refused and its metadata is untouched."""
    example = _run(results, "run-example", read_only=True)

    result = Api().update_run_details(str(example), "Renamed", "")

    assert result["ok"] is False
    assert "read-only" in result["message"]
    assert not run_metadata_path(example).exists()


def test_create_study_inside_a_read_only_experiment_leaves_nothing_behind(
    results: Path,
) -> None:
    """The new Study is removed again when the Experiment refuses it."""
    study_id = _read_only_study(results, "study-examples-a", [])
    _read_only_experiment(results, [study_id])

    result = Api().create_study("Mine", experiment_id="experiment-examples")

    assert result["ok"] is False
    assert "read-only" in result["message"]
    assert [study.study_id for study in groups.list_studies(results=results)] == [
        study_id
    ]


def test_copying_a_read_only_study_makes_an_editable_one(results: Path) -> None:
    """Copy stays allowed, and the copy can be renamed."""
    api = Api()
    example = _run(results, "run-example", read_only=True)
    study_id = _read_only_study(results, "study-examples-a", [example])

    copied = api.copy_study(study_id, "My copy")

    assert copied["ok"] is True
    assert groups.get_study(copied["studyId"]).read_only is False
    assert api.update_study_details(copied["studyId"], "Renamed", "", "")["ok"]


class _RecordingWindow:
    """Collects every script a bridge helper pushes to the page."""

    def __init__(self) -> None:
        self.scripts: list[str] = []

    def evaluate_js(self, script: str) -> None:
        self.scripts.append(script)


def test_a_matching_recompute_of_a_read_only_run_keeps_it_and_tells_the_page(
    results: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_check_reproducibility` no longer raises for a read-only previous run."""
    previous = _run(results, "run-example", read_only=True)
    recomputed = _run(results, "run-new")

    class _Identical:
        identical = True

        def to_dict(self) -> dict[str, object]:
            return {"identical": True}

    monkeypatch.setattr(app_module, "compare_runs", lambda *_: _Identical())
    window = _RecordingWindow()

    app_module._check_reproducibility(window, recomputed, previous)

    assert previous.is_dir()
    assert window.scripts == ['fim.onReproducibilityChecked({"identical": true})']


def test_a_finished_run_aimed_at_a_read_only_study_is_filed_in_the_default_one(
    results: Path,
) -> None:
    """A read-only Study cannot take a new run; the default Study does."""
    study_id = _read_only_study(results, "study-examples-a", [])
    output = _run(results, "run-new")

    app_module._attach_finished_run_to_study(study_id, output)

    assert groups.get_study(study_id).run_directories == ()
    default = groups.get_study(groups.DEFAULT_STUDY_ID)
    assert default.run_directories == ("run-new",)


def test_a_read_only_runs_metadata_is_not_rewritten_by_run_details(
    results: Path,
) -> None:
    """Configure's run name on a reused example run is refused, best effort."""
    example = _run(results, "run-example", read_only=True)
    groups.write_read_only_study(
        "study-examples-a", name="A", run_directories=[example], results=results
    )
    run_metadata_path(example).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "Shipped",
                "description": None,
                "created_at": "2026-10-05T12:00:00Z",
                "updated_at": "2026-10-05T12:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    app_module._attach_finished_run_to_study(
        None, example, app_module.RunDetails(name="Mine", description=None)
    )

    assert read_run_metadata(run_metadata_path(example)).name == "Shipped"
