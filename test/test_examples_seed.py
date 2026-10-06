"""Seeding the bundled worked examples into the results folder.

Read-only examples design (`20261005-claude-opus-5-5-read-only-examples-
and-classes-design.md`, `selby/restricted`), section 4.2:
`fim.examples.seed.seed_examples` writes each example with a saved
result as a read-only run under `results/examples/<id>/`, one read-only
Study per class, and the read-only Examples Experiment. These tests drive
it against small fixture bundles shaped like `dev/bin/build-examples-
catalog`'s output, with a fixed clock, so every run of them sees the same
files and the same timestamps.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from fim.examples import seed
from fim.persistence import groups
from fim.persistence.run_metadata import read_run_metadata, run_metadata_path

_FIXED_CLOCK = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
_LATER_CLOCK = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

_CLASSES: list[dict[str, Any]] = [
    {
        "id": "getting-started",
        "title": "Getting started",
        "description": "One-screen runs.",
        "children": [],
    },
    {
        "id": "literature",
        "title": "Literature comparisons",
        "description": "",
        "children": [
            {
                "id": "literature-distances",
                "title": "Genetic distances",
                "description": "",
                "children": [],
            }
        ],
    },
    {
        "id": "unclassified",
        "title": "Unclassified",
        "description": "Examples not yet assigned to a class.",
        "children": [],
    },
]


def _manifest(example_id: str) -> bytes:
    """A small manifest whose parameters mark the run read-only."""
    payload = {"run_id": example_id, "parameters": {"N": 10, "_read_only": True}}
    return (json.dumps(payload, indent=2) + "\n").encode("utf-8")


def _entry(
    example_id: str,
    class_id: str,
    outputs: Mapping[str, bytes] | None = None,
    *,
    name: str | None = None,
    description: str = "What it shows.",
) -> tuple[dict[str, Any], Mapping[str, bytes]]:
    """One catalog entry and its output files (a scalar run by default)."""
    files = (
        outputs
        if outputs is not None
        else {"manifest.json": _manifest(example_id), "report.json": b'{"D": 0.1}\n'}
    )
    return (
        {
            "id": example_id,
            "name": name or example_id.replace("-", " ").capitalize(),
            "description": description,
            "class": class_id,
            "readme": "# Example\n",
            "readme_excerpt": "",
            "config_yaml": f"name: {example_id}\n_read_only: true\nN: 10\n",
            "outputs": sorted(files),
        },
        files,
    )


def _write_bundle(
    bundle: Path,
    entries: list[tuple[dict[str, Any], Mapping[str, bytes]]],
    classes: list[dict[str, Any]] | None = None,
) -> Path:
    """Write a bundle directory: `catalog.json` plus each example's outputs."""
    if bundle.exists():
        for path in sorted(bundle.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
    bundle.mkdir(parents=True, exist_ok=True)
    catalog = {
        "schema_version": 1,
        "generator": "dev/bin/build-examples-catalog",
        "classes": classes if classes is not None else _CLASSES,
        "examples": [entry for entry, _ in entries],
    }
    (bundle / "catalog.json").write_text(json.dumps(catalog, indent=2), "utf-8")
    for entry, files in entries:
        for relative, content in files.items():
            target = bundle / entry["id"] / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    return bundle


def _standard_entries() -> list[tuple[dict[str, Any], Mapping[str, bytes]]]:
    """Four examples: two classes with saved runs, a child class, no output."""
    batch_outputs = {
        "manifest.json": _manifest("batch-example"),
        "summary.json": b'{"D": {"mean": 0.1}}\n',
        "replicate-001/manifest.json": _manifest("batch-example-1"),
        "replicate-001/report.json": b'{"D": 0.1}\n',
    }
    return [
        _entry("first-steps", "getting-started", name="First steps"),
        _entry("batch-example", "getting-started", batch_outputs),
        _entry("nei-distance", "literature-distances"),
        _entry("not-run-yet", "literature", {}),
        _entry("loose-example", "unclassified"),
    ]


@pytest.fixture
def results(tmp_path: Path) -> Path:
    """An empty results directory."""
    root = tmp_path / "results"
    root.mkdir()
    return root


def _seed(results: Path, bundle: Path, clock: datetime = _FIXED_CLOCK) -> Any:
    """Seed with a fixed clock."""
    return seed.seed_examples(results, bundle=bundle, clock=lambda: clock)


def _snapshot(root: Path) -> dict[str, bytes]:
    """Every file under `root`, by relative path."""
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_seeding_writes_runs_studies_and_the_examples_experiment(
    tmp_path: Path, results: Path
) -> None:
    """Every part of section 4.2, from one standard bundle."""
    bundle = _write_bundle(tmp_path / "bundle", _standard_entries())

    report = _seed(results, bundle)

    # Run directories: only examples with a saved result.
    assert report.seeded == (
        "first-steps",
        "batch-example",
        "nei-distance",
        "loose-example",
    )
    assert report.without_saved_result == ("not-run-yet",)
    examples = results / "examples"
    assert sorted(path.name for path in examples.iterdir()) == [
        "batch-example",
        "first-steps",
        "loose-example",
        "nei-distance",
    ]
    first = examples / "first-steps"
    assert (first / "config.yaml").read_text("utf-8").startswith("name: first-steps\n")
    assert (first / "manifest.json").read_bytes() == _manifest("first-steps")
    assert groups.is_run_read_only(first)
    assert (examples / "batch-example" / "replicate-001" / "report.json").is_file()
    metadata = read_run_metadata(run_metadata_path(first))
    assert (metadata.name, metadata.description, metadata.run_class) == (
        "First steps",
        "What it shows.",
        "getting-started",
    )
    assert metadata.created_at == "2026-10-05T12:00:00Z"
    # The catalog's "unclassified" bucket is not a real class label.
    loose = read_run_metadata(run_metadata_path(examples / "loose-example"))
    assert loose.run_class is None

    # Studies: one per class with seeded members; a child is "Parent — Child".
    studies = {study.study_id: study for study in groups.list_studies(results=results)}
    assert set(studies) == {
        "study-examples-getting-started",
        "study-examples-literature-distances",
        "study-examples-unclassified",
    }
    getting_started = studies["study-examples-getting-started"]
    assert getting_started.read_only is True
    assert getting_started.name == "Getting started"
    assert getting_started.description == "One-screen runs."
    assert getting_started.run_directories == (
        "examples/first-steps",
        "examples/batch-example",
    )
    assert studies["study-examples-literature-distances"].name == (
        "Literature comparisons — Genetic distances"
    )

    # The Experiment holds them, in class order.
    experiment = groups.get_experiment("experiment-examples", results=results)
    assert experiment.read_only is True
    assert experiment.name == "Examples"
    assert experiment.study_ids == (
        "study-examples-getting-started",
        "study-examples-literature-distances",
        "study-examples-unclassified",
    )


def test_seeding_twice_changes_nothing(tmp_path: Path, results: Path) -> None:
    """Idempotent: the same bundle again writes no file, not even a timestamp."""
    bundle = _write_bundle(tmp_path / "bundle", _standard_entries())
    _seed(results, bundle)
    before = _snapshot(results)
    mtimes = {path: path.stat().st_mtime_ns for path in results.rglob("*")}

    report = _seed(results, bundle, _LATER_CLOCK)

    assert report.changed is False
    assert report.written == ()
    assert report.removed == ()
    assert _snapshot(results) == before
    assert {path: path.stat().st_mtime_ns for path in results.rglob("*")} == mtimes


def test_a_changed_bundle_file_replaces_only_that_file(
    tmp_path: Path, results: Path
) -> None:
    """A newer bundle rewrites the files whose content differs, nothing else."""
    entries = _standard_entries()
    bundle = _write_bundle(tmp_path / "bundle", entries)
    _seed(results, bundle)
    before = _snapshot(results)
    (bundle / "first-steps" / "report.json").write_bytes(b'{"D": 0.2}\n')

    report = _seed(results, bundle, _LATER_CLOCK)

    target = results / "examples" / "first-steps" / "report.json"
    assert report.written == (target,)
    assert target.read_bytes() == b'{"D": 0.2}\n'
    after = _snapshot(results)
    changed = {key for key in after if after[key] != before.get(key)}
    assert changed == {"examples/first-steps/report.json"}


@pytest.mark.parametrize("remove_part", [False, True])
def test_reseeding_keeps_decoded_data_until_an_archive_changes(
    tmp_path: Path, results: Path, remove_part: bool
) -> None:
    """Cached raw data survives identical seeding, not changed or removed parts."""
    files = {
        "manifest.json": _manifest("archived"),
        "report.json": b'{"D": 0.1}\n',
        "trajectory.jsonl.gz.part-0001": b"first",
        "trajectory.jsonl.gz.part-0002": b"second",
    }
    bundle = _write_bundle(
        tmp_path / "bundle", [_entry("archived", "getting-started", files)]
    )
    _seed(results, bundle)
    run = results / "examples" / "archived"
    restored = run / "trajectory.jsonl"
    restored.write_bytes(b"decoded")
    assert not _seed(results, bundle).changed
    assert restored.read_bytes() == b"decoded"
    (run / "notes.txt").write_bytes(b"keep")
    if remove_part:
        del files["trajectory.jsonl.gz.part-0002"]
    else:
        files["trajectory.jsonl.gz.part-0001"] = b"new first"
    _write_bundle(bundle, [_entry("archived", "getting-started", files)])
    _seed(results, bundle)
    assert not restored.exists()
    assert (run / "notes.txt").read_bytes() == b"keep"


def test_changed_labels_rewrite_the_metadata_and_keep_its_creation_time(
    tmp_path: Path, results: Path
) -> None:
    """A renamed example gets its new name; `created_at` survives."""
    bundle = _write_bundle(tmp_path / "bundle", _standard_entries())
    _seed(results, bundle)
    renamed = [_entry("first-steps", "getting-started", name="Better name")]
    _write_bundle(bundle, renamed)

    _seed(results, bundle, _LATER_CLOCK)

    metadata = read_run_metadata(
        run_metadata_path(results / "examples" / "first-steps")
    )
    assert metadata.name == "Better name"
    assert metadata.created_at == "2026-10-05T12:00:00Z"
    assert metadata.updated_at == "2026-10-06T12:00:00Z"


def test_an_owned_file_the_bundle_stops_shipping_is_removed(
    tmp_path: Path, results: Path
) -> None:
    """A batch example that became a single run loses its batch files."""
    bundle = _write_bundle(tmp_path / "bundle", _standard_entries())
    _seed(results, bundle)
    run = results / "examples" / "batch-example"
    (run / "notes.txt").write_text("mine", "utf-8")
    scalar = _entry("batch-example", "getting-started")
    _write_bundle(bundle, [scalar])

    _seed(results, bundle, _LATER_CLOCK)

    assert sorted(
        str(path.relative_to(run)) for path in run.rglob("*") if path.is_file()
    ) == ["config.yaml", "manifest.json", "metadata.json", "notes.txt", "report.json"]


def test_seeding_never_touches_a_users_own_runs_studies_or_experiments(
    tmp_path: Path, results: Path
) -> None:
    """Everything outside the examples' own items is byte-for-byte unchanged."""
    own_run = results / "run-mine"
    own_run.mkdir()
    (own_run / "manifest.json").write_text('{"parameters": {"N": 5}}', "utf-8")
    study = groups.create_study("Mine", results=results, clock=lambda: _FIXED_CLOCK)
    groups.add_run_to_study(study.study_id, own_run, results=results)
    groups.ensure_default_study(results=results, clock=lambda: _FIXED_CLOCK)
    before = _snapshot(results)
    bundle = _write_bundle(tmp_path / "bundle", _standard_entries())

    _seed(results, bundle)
    _write_bundle(bundle, [_entry("first-steps", "getting-started")])
    _seed(results, bundle, _LATER_CLOCK)

    after = _snapshot(results)
    examples_items = ("examples/", ".fim/studies/study-examples-")
    assert {
        key: value
        for key, value in after.items()
        if not key.startswith(examples_items)
        and not key.endswith("experiment-examples.json")
    } == before


def test_an_example_dropped_from_the_bundle_is_removed_with_its_study(
    tmp_path: Path, results: Path
) -> None:
    """Stale example items go; a directory holding a user's file stays."""
    bundle = _write_bundle(tmp_path / "bundle", _standard_entries())
    _seed(results, bundle)
    kept_by_user = results / "examples" / "nei-distance"
    (kept_by_user / "my-notes.txt").write_text("keep", "utf-8")
    _write_bundle(bundle, [_entry("first-steps", "getting-started")])

    report = _seed(results, bundle, _LATER_CLOCK)

    assert not (results / "examples" / "batch-example").exists()
    assert not (results / "examples" / "loose-example").exists()
    assert (kept_by_user / "my-notes.txt").is_file()
    assert [study.study_id for study in groups.list_studies(results=results)] == [
        "study-examples-getting-started"
    ]
    experiment = groups.get_experiment("experiment-examples", results=results)
    assert experiment.study_ids == ("study-examples-getting-started",)
    assert results / "examples" / "batch-example" in report.removed


def test_reseeding_restores_a_read_only_flag_an_older_version_dropped(
    tmp_path: Path, results: Path
) -> None:
    """An older fim that rewrote a manifest drops `read_only`; seeding repairs it."""
    bundle = _write_bundle(tmp_path / "bundle", _standard_entries())
    _seed(results, bundle)
    path = groups.study_manifest_path("study-examples-getting-started", results=results)
    payload = json.loads(path.read_text("utf-8"))
    del payload["read_only"]
    path.write_text(json.dumps(payload), "utf-8")

    report = _seed(results, bundle, _LATER_CLOCK)

    assert path in report.written
    assert groups.read_study_manifest(path).read_only is True


def test_a_bundle_without_a_catalog_seeds_nothing(
    tmp_path: Path, results: Path
) -> None:
    """No `catalog.json` (an unbundled build): nothing is created."""
    empty = tmp_path / "empty"
    empty.mkdir()

    report = _seed(results, empty)

    assert report == seed.SeedReport()
    assert list(results.iterdir()) == []


def test_a_bundle_with_no_saved_results_removes_a_stale_examples_experiment(
    tmp_path: Path, results: Path
) -> None:
    """With nothing to show, the Examples Experiment is not kept around empty."""
    bundle = _write_bundle(tmp_path / "bundle", _standard_entries())
    _seed(results, bundle)
    _write_bundle(bundle, [_entry("not-run-yet", "literature", {})])

    report = _seed(results, bundle, _LATER_CLOCK)

    assert report.seeded == ()
    assert groups.list_experiments(results=results) == []
    assert groups.list_studies(results=results) == []
    assert not any((results / "examples").iterdir())


def test_a_malformed_catalog_is_reported(tmp_path: Path, results: Path) -> None:
    """A catalog missing its keys raises `ValueError` naming the file."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "catalog.json").write_text('{"classes": []}', "utf-8")

    with pytest.raises(ValueError, match="malformed catalog"):
        _seed(results, bundle)


def test_the_committed_bundle_seeds_cleanly(results: Path) -> None:
    """The app's own `webui/examples/` bundle reads and seeds without error."""
    bundle = Path(seed.__file__).resolve().parents[1] / "gui" / "webui" / "examples"

    report = _seed(results, bundle)

    parsed = seed.read_bundle(bundle)
    assert parsed is not None
    assert set(report.seeded) == {
        example.example_id for example in parsed.examples if example.has_saved_result
    }
    for example_id in report.seeded:
        assert (
            seed.example_run_directory(example_id, results=results) / "manifest.json"
        ).is_file()
