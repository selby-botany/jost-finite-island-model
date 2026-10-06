"""`fim run` and configuration labels; CLI reporting of read-only items.

Read-only examples design (2026-10-05), sections 1 and 3: `fim run`
writes a configuration's `name`, `description`, and `class` to the new
run's `metadata.json` unless one already exists, and every CLI command
that edits a read-only Study or Experiment reports `ReadOnlyError` as an
ordinary one-line error.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
import yaml

from fim import cli, paths
from fim.examples import classes
from fim.persistence.groups import (
    get_experiment,
    get_study,
    write_read_only_experiment,
    write_read_only_study,
)
from fim.persistence.run_metadata import RunLabels, run_metadata_path

_CLASSES_FIXTURE = (
    Path(__file__).resolve().parents[1] / "persistence" / "fixtures" / "classes.yaml"
)


@pytest.fixture(autouse=True)
def _fixture_class_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check every `class` against the test fixture, never the shipped file.

    The shipped `doc/examples/classes.yaml` belongs to the examples and
    may change; these tests must be a function of their own commit.
    """
    monkeypatch.setattr(classes, "default_classes_path", lambda: _CLASSES_FIXTURE)


def _write_config(path: Path, **updates: object) -> None:
    """Write a tiny, fast, deterministic scalar configuration."""
    config: dict[str, object] = {
        "N": 20,
        "ploidy": "haploid",
        "d": 2,
        "m": 0.1,
        "mu": 0.01,
        "seed": 20261005,
        "convergence_window": 4,
        "convergence_tolerance": 1.0,
        "max_generations": 10,
        "n_replicates": 1,
        "replicate_tolerance": None,
    }
    config.update(updates)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _read_metadata(directory: Path) -> dict[str, object]:
    """Return a run's `metadata.json` as a mapping."""
    payload = json.loads(run_metadata_path(directory).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _run(config: Path, output: Path, *extra: str) -> int:
    """Run `fim run` quietly on `config` into `output`."""
    return cli.main(["run", str(config), "--output", str(output), "--quiet", *extra])


def test_run_writes_the_configuration_labels_to_metadata(tmp_path: Path) -> None:
    """`name`, `description`, and `class` land in the new run's sidecar."""
    config = tmp_path / "run.yaml"
    output = tmp_path / "output"
    _write_config(
        config,
        name="Two islands",
        description="The smallest model.\n",
        **{"class": "getting-started"},
    )

    assert _run(config, output) == 0

    metadata = _read_metadata(output)
    assert metadata["name"] == "Two islands"
    assert metadata["description"] == "The smallest model."
    assert metadata["class"] == "getting-started"
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert not {"name", "description", "class"} & set(manifest["parameters"])


def test_run_flags_override_the_configuration_labels(tmp_path: Path) -> None:
    """`--name`/`--description` win, field by field; the class is kept."""
    config = tmp_path / "run.yaml"
    output = tmp_path / "output"
    _write_config(
        config, name="From config", description="Config text.", **{"class": "migration"}
    )

    assert _run(config, output, "--name", "From flag") == 0

    metadata = _read_metadata(output)
    assert metadata["name"] == "From flag"
    assert metadata["description"] == "Config text."
    assert metadata["class"] == "migration"


def test_run_rejects_an_unknown_class_before_running(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A typo in `class` is reported before any output exists."""
    config = tmp_path / "run.yaml"
    output = tmp_path / "output"
    _write_config(config, **{"class": "migraton"})

    assert _run(config, output) == 2

    assert "unknown class 'migraton'" in capsys.readouterr().err
    assert not output.exists()


def test_run_of_a_read_only_configuration_still_records_its_labels(
    tmp_path: Path,
) -> None:
    """Writing a new sidecar is creation, so a read-only run gets its labels."""
    config = tmp_path / "run.yaml"
    output = tmp_path / "output"
    _write_config(config, name="Shipped example", _read_only=True)

    assert _run(config, output) == 0

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["parameters"]["_read_only"] is True
    assert _read_metadata(output)["name"] == "Shipped example"


def _arguments(**overrides: object) -> argparse.Namespace:
    """Return the `fim run` arguments `_record_run_organization` reads."""
    values: dict[str, object] = {"name": None, "description": None, "study": None}
    values.update(overrides)
    return argparse.Namespace(**values)


def _existing_sidecar(directory: Path) -> bytes:
    """Give `directory` a `metadata.json` of its own and return its bytes."""
    directory.mkdir()
    path = run_metadata_path(directory)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "Renamed by the user",
                "description": None,
                "created_at": "2026-10-01T09:00:00Z",
                "updated_at": "2026-10-02T09:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    return path.read_bytes()


def test_configuration_labels_never_overwrite_an_existing_sidecar(
    tmp_path: Path,
) -> None:
    """An existing `metadata.json` wins over the configuration's labels."""
    output = tmp_path / "output"
    before = _existing_sidecar(output)
    labels = RunLabels(name="From config", run_class="migration")

    cli._record_run_organization(output, _arguments(), labels)

    assert run_metadata_path(output).read_bytes() == before


def test_explicit_flags_still_edit_an_existing_sidecar(tmp_path: Path) -> None:
    """With a sidecar present, `--name` is an edit, as it always was."""
    output = tmp_path / "output"
    _existing_sidecar(output)

    cli._record_run_organization(
        output, _arguments(name="From flag"), RunLabels(name="From config")
    )

    metadata = _read_metadata(output)
    assert metadata["name"] == "From flag"
    assert metadata["created_at"] == "2026-10-01T09:00:00Z"


def _seed_examples(results: Path) -> None:
    """Seed one read-only example run, Study, and Experiment under `results`."""
    run = results / "examples" / "ring"
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(
        json.dumps({"parameters": {"_read_only": True}}), encoding="utf-8"
    )
    write_read_only_study(
        "study-examples-migration",
        name="Migration structure",
        run_directories=[run],
        results=results,
    )
    write_read_only_experiment(
        "experiment-examples",
        name="Examples",
        study_ids=["study-examples-migration"],
        results=results,
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["study", "delete", "study-examples-migration"],
        ["study", "add-run", "study-examples-migration", "mine"],
        ["experiment", "delete", "experiment-examples"],
        ["experiment", "add-study", "experiment-examples", "STUDY"],
    ],
    ids=["study-delete", "study-add-run", "experiment-delete", "experiment-add-study"],
)
def test_cli_reports_a_read_only_refusal_as_a_one_line_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    """Every editing command refuses a read-only item cleanly, changing nothing."""
    results = tmp_path / "results"
    monkeypatch.setattr(paths, "results_directory", lambda: results)
    _seed_examples(results)
    mine = results / "mine"
    mine.mkdir()
    (mine / "manifest.json").write_text(json.dumps({"parameters": {}}), "utf-8")
    assert cli.main(["study", "create", "--name", "Mine"]) == 0
    capsys.readouterr()
    own_study = next(
        study_id
        for study_id in (
            path.stem for path in paths.studies_directory(results).glob("*")
        )
        if study_id != "study-examples-migration"
    )
    study_before = get_study("study-examples-migration", results=results)
    experiment_before = get_experiment("experiment-examples", results=results)

    status = cli.main([own_study if part == "STUDY" else part for part in argv])

    error = capsys.readouterr().err
    assert status == 2
    # The last line is the user-facing one; the logger's own record of
    # the failure may precede it on the same stream.
    assert error.splitlines()[-1].startswith("fim: error: ")
    assert "read-only" in error.splitlines()[-1]
    assert "Traceback" not in error
    assert get_study("study-examples-migration", results=results) == study_before
    assert get_experiment("experiment-examples", results=results) == experiment_before


def test_cli_copies_a_read_only_study_into_an_editable_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Copying stays allowed from the terminal too."""
    results = tmp_path / "results"
    monkeypatch.setattr(paths, "results_directory", lambda: results)
    _seed_examples(results)

    status = cli.main(["study", "copy", "study-examples-migration", "--name", "Mine"])

    assert status == 0
    copies = [
        path
        for path in paths.studies_directory(results).glob("*.json")
        if path.stem != "study-examples-migration"
    ]
    assert len(copies) == 1
    assert "read_only" not in json.loads(copies[0].read_text(encoding="utf-8"))
