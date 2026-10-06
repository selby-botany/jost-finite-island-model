"""The seeded Examples experiment through `fim.gui.app.Api`'s bridge.

Read-only examples design (`20261005-claude-opus-5-5-read-only-examples-
and-classes-design.md`, `selby/restricted`), section 4.2: Home seeds the
bundled examples whenever it finds the Examples experiment missing
(`Api.ensure_examples`). Window-free, like `test_app_api.py`.

The bundle here is a fixture built from one real, tiny `fim run` of a
configuration carrying `_read_only: true`, laid out exactly as
`dev/bin/build-examples-catalog` lays out `webui/examples/`, so the
tests do not change whenever the shipped examples do.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from fim import cli, paths
from fim.examples import seed
from fim.gui import app as app_module
from fim.gui.app import Api
from fim.persistence import groups

_CONFIG: dict[str, Any] = {
    "name": "Shipped example",
    "description": "A tiny example run.",
    "_read_only": True,
    "N": 20,
    "ploidy": "haploid",
    "d": 2,
    "m": 0.1,
    "mu": 0.01,
    "seed": 5,
    "loci": [{"locus_id": 1, "length": 200}],
    "convergence_window": 4,
    "convergence_tolerance": 1.0,
    "max_generations": 10,
    "n_replicates": 1,
    "replicate_tolerance": None,
}


def build_bundle(root: Path) -> Path:
    """Write a fixture examples bundle under `root` and return its directory.

    Two examples in one class: `tiny-example`, with a saved result from a
    real run (its `manifest.json` and `report.json`, never its
    trajectory), and `not-run-yet`, with a configuration only.
    """
    config_text = yaml.safe_dump(_CONFIG, sort_keys=False)
    config_path = root / "tiny-example.yaml"
    config_path.write_text(config_text, encoding="utf-8")
    run = root / "tiny-run"
    assert cli.main(["run", str(config_path), "-o", str(run), "--quiet"]) == 0
    bundle = root / "bundle"
    (bundle / "tiny-example").mkdir(parents=True)
    for name in ("manifest.json", "report.json"):
        (bundle / "tiny-example" / name).write_bytes((run / name).read_bytes())
    catalog = {
        "schema_version": 1,
        "generator": "dev/bin/build-examples-catalog",
        "classes": [
            {
                "id": "getting-started",
                "title": "Getting started",
                "description": "One-screen runs.",
                "children": [],
            }
        ],
        "examples": [
            {
                "id": "tiny-example",
                "name": "Shipped example",
                "description": "A tiny example run.",
                "class": "getting-started",
                "readme": "# Shipped example\n",
                "readme_excerpt": "A tiny example run.",
                "config_yaml": config_text,
                "outputs": ["manifest.json", "report.json"],
            },
            {
                "id": "not-run-yet",
                "name": "Not run yet",
                "description": "No saved result.",
                "class": "getting-started",
                "readme": "# Not run yet\n",
                "readme_excerpt": "",
                "config_yaml": config_text.replace("seed: 5", "seed: 6"),
                "outputs": [],
            },
        ],
    }
    (bundle / "catalog.json").write_text(json.dumps(catalog, indent=2), "utf-8")
    return bundle


@pytest.fixture
def results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point `paths.results_directory()` at an isolated directory."""
    root = tmp_path / "results"
    root.mkdir()
    monkeypatch.setattr(paths, "results_directory", lambda: root)
    return root


@pytest.fixture
def bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build the fixture bundle and make it the app's bundled examples."""
    built = build_bundle(tmp_path)
    monkeypatch.setattr(app_module, "_examples_bundle_directory", lambda: built)
    return built


def test_ensure_examples_seeds_a_missing_examples_experiment(
    results: Path, bundle: Path
) -> None:
    """Missing: seeded, and reported as changed so Home refetches."""
    api = Api()

    result = api.ensure_examples()

    assert result == {"ok": True, "changed": True}
    # The fixture's own `fim run` also filed its run in the default
    # Experiment, which is listed too.
    experiments = {row["experimentId"]: row for row in api.list_experiments()}
    assert experiments["experiment-examples"]["readOnly"] is True
    assert experiments["experiment-examples"]["name"] == "Examples"
    summary = api.get_study_run_summary("study-examples-getting-started")
    (row,) = summary["runs"]
    assert row["name"] == "Shipped example"
    assert row["readOnly"] is True
    assert row["directory"] == str(results / "examples" / "tiny-example")


def test_ensure_examples_does_nothing_while_the_experiment_exists(
    results: Path, bundle: Path
) -> None:
    """Present: no seeding at all, even if the bundle has changed since."""
    api = Api()
    api.ensure_examples()
    report = results / "examples" / "tiny-example" / "report.json"
    before = report.read_bytes()
    (bundle / "tiny-example" / "report.json").write_text("{}\n", "utf-8")

    result = api.ensure_examples()

    assert result == {"ok": True, "changed": False}
    assert report.read_bytes() == before


def test_ensure_examples_reports_a_damaged_bundle(
    results: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed catalog is a refusal with a message, never an exception."""
    damaged = tmp_path / "damaged"
    damaged.mkdir()
    (damaged / "catalog.json").write_text("{not json", "utf-8")
    monkeypatch.setattr(app_module, "_examples_bundle_directory", lambda: damaged)

    result = Api().ensure_examples()

    assert result["ok"] is False
    assert "could not be seeded" in result["message"]
    assert groups.list_experiments(results=results) == []


def test_app_start_seeds_the_examples_before_the_window_opens(
    results: Path, bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`main` seeds once the results location is final."""
    seeded: list[Path] = []
    monkeypatch.setattr(
        app_module,
        "_seed_bundled_examples",
        lambda: seeded.append(paths.results_directory()),
    )

    def _stop(*_args: object, **_kwargs: object) -> None:
        raise SystemExit(0)

    monkeypatch.setattr(app_module, "create_window", _stop)

    with pytest.raises(SystemExit):
        app_module.main([])

    assert seeded == [results]


def test_the_seeded_study_holds_only_examples_with_a_saved_result(
    results: Path, bundle: Path
) -> None:
    """`not-run-yet` has no saved result, so no run directory and no row."""
    Api().ensure_examples()

    study = groups.get_study("study-examples-getting-started", results=results)

    assert study.run_directories == ("examples/tiny-example",)
    assert not seed.example_run_directory("not-run-yet", results=results).exists()


def test_open_run_shows_a_seeded_example_from_its_saved_report(
    results: Path, bundle: Path
) -> None:
    """No trajectory, a saved report: the report-only payload (design §4.3)."""
    api = Api()
    api.ensure_examples()
    directory = seed.example_run_directory("tiny-example", results=results)
    saved_report = json.loads((directory / "report.json").read_text("utf-8"))

    result = api.open_run({"trajectoryPath": str(directory / "trajectory.jsonl")})

    assert result["ok"] is True
    assert result["reportOnly"] is True
    assert result["readOnly"] is True
    assert result["report"] == saved_report
    assert result["panels"] is None
    assert result["trajectoryPath"] is None
    assert result["convergenceGenerations"] is None
    assert result["outputDirectory"] == str(directory)
    assert result["directoryName"] == "tiny-example"
    assert result["statistics"]["D"] == app_module.format_report_statistic(
        saved_report, "D", api.get_significant_digits()
    )
    assert result["configSummary"]["N"].startswith("20")


def test_open_run_without_a_trajectory_or_a_report_still_fails(
    results: Path, bundle: Path
) -> None:
    """Only a saved report enables the report-only path."""
    api = Api()
    api.ensure_examples()
    directory = seed.example_run_directory("tiny-example", results=results)
    (directory / "report.json").unlink()

    result = api.open_run({"trajectoryPath": str(directory / "trajectory.jsonl")})

    assert result["ok"] is False


def test_open_batch_shows_a_batch_from_its_saved_summary(
    tmp_path: Path, results: Path
) -> None:
    """A batch without replicate trajectories opens from `summary.json`."""
    config = {**_CONFIG, "n_replicates": 2, "seed": 9}
    config_path = tmp_path / "batch.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), "utf-8")
    source = tmp_path / "batch-run"
    assert cli.main(["run", str(config_path), "-o", str(source), "--quiet"]) == 0
    saved = results / "saved-batch"
    saved.mkdir()
    for path in source.rglob("*.json"):
        if path.name in {"manifest.json", "report.json", "summary.json"}:
            target = saved / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    summary = json.loads((saved / "summary.json").read_text("utf-8"))

    result = Api().open_batch(str(saved))

    assert result["ok"] is True
    assert result["reportOnly"] is True
    assert result["readOnly"] is True
    assert len(result["replicates"]) == 2
    assert set(result["summary"]) == {
        name for name, value in summary.items() if isinstance(value, dict)
    }
    assert result["pooledConvergenceHistories"] == {}


def test_load_run_configuration_gives_an_editable_copy_of_a_seeded_example(
    results: Path, bundle: Path
) -> None:
    """ "Run it": labels to the boxes, `_` keys dropped, Settings synced."""
    api = Api()
    api.ensure_examples()
    directory = seed.example_run_directory("tiny-example", results=results)

    result = api.load_run_configuration(str(directory))

    assert result["ok"] is True
    assert (result["name"], result["description"]) == (
        "Shipped example",
        "A tiny example run.",
    )
    assert not any(key.startswith("_") for key in result["values"])
    assert api.validate_form(result["values"])["ok"] is True


def test_load_run_configuration_falls_back_to_the_manifest(
    results: Path, bundle: Path
) -> None:
    """Without `config.yaml`, the manifest's parameters and the metadata serve."""
    api = Api()
    api.ensure_examples()
    directory = seed.example_run_directory("tiny-example", results=results)
    (directory / "config.yaml").unlink()

    result = api.load_run_configuration(str(directory))

    assert result["ok"] is True
    assert result["name"] == "Shipped example"
    assert api.validate_form(result["values"])["ok"] is True


def test_load_run_configuration_refuses_a_directory_that_is_not_a_run(
    tmp_path: Path, results: Path
) -> None:
    """Nothing to load: a message, not an exception."""
    assert Api().load_run_configuration(str(tmp_path / "nowhere"))["ok"] is False
