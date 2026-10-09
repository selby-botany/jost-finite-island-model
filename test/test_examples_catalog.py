"""The bundled examples catalog: its generator, and the committed bundle's freshness.

`dev/bin/build-examples-catalog` turns `doc/examples/` into
`src/fim/gui/webui/examples/` (design doc `20261005-claude-opus-5-5-
read-only-examples-and-classes-design.md` §4.1, `selby/restricted`).
These tests load the script as a module and drive it against small
fixture trees, both before examples carry labels and a `classes.yaml`
and after.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "dev" / "bin" / "build-examples-catalog"


def _load_generator() -> ModuleType:
    """Import the extension-less generator script as a module."""
    loader = importlib.machinery.SourceFileLoader(
        "build_examples_catalog", str(GENERATOR)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


catalog_generator = _load_generator()

_README = """# {title}

## What this demonstrates

{first} It has `code` and a [link](https://example.invalid).

- A list item that is not prose.

Second paragraph with &mu; = 0.001.
"""

_CONFIG = "N: 10\nploidy: haploid\nd: 2\nm: 0.1\nmu: 0.001\nseed: 1\n"


def _example(
    root: Path,
    example_id: str,
    *,
    title: str = "An example",
    first: str = "The first paragraph.",
    config: str | None = _CONFIG,
) -> Path:
    """Write one fixture example directory and return it."""
    directory = root / example_id
    directory.mkdir(parents=True)
    (directory / "README.md").write_text(
        _README.format(title=title, first=first), encoding="utf-8"
    )
    if config is not None:
        (directory / "config.yaml").write_text(config, encoding="utf-8")
    return directory


def _usage(root: Path, *example_ids: str) -> Path:
    """Write a fixture usage guide whose markers give `example_ids`' order."""
    usage = root / "usage.md"
    usage.write_text(
        "".join(
            f"<!-- worked-example-config: examples/{example_id}/config.yaml -->\n"
            for example_id in example_ids
        ),
        encoding="utf-8",
    )
    return usage


def _build(examples: Path, usage: Path) -> dict[str, Any]:
    """Return the catalog the generator builds for the fixture tree."""
    catalog, _outputs = catalog_generator.build_catalog(examples, usage)
    assert isinstance(catalog, dict)
    return catalog


def test_committed_bundle_is_current() -> None:
    """The committed bundle matches a fresh build; run the generator if this fails.

    The pre-commit hook rebuilds it whenever an example, the usage
    guide, or the generator is staged, so a failure here means a commit
    bypassed the hook.
    """
    files = catalog_generator.bundle_files(
        catalog_generator.EXAMPLES_DIR, catalog_generator.USAGE_PATH
    )

    assert catalog_generator.stale_paths(catalog_generator.BUNDLE_DIR, files) == []


def test_committed_catalog_lists_every_example_directory() -> None:
    """Every `doc/examples/<id>/` with a README is in the committed catalog."""
    catalog = json.loads(
        (catalog_generator.BUNDLE_DIR / "catalog.json").read_text(encoding="utf-8")
    )
    expected = sorted(
        path.parent.name for path in (ROOT / "doc" / "examples").glob("*/README.md")
    )

    assert sorted(example["id"] for example in catalog["examples"]) == expected


def test_unlabelled_examples_derive_name_and_description_from_the_readme(
    tmp_path: Path,
) -> None:
    """Without labels: README heading, first prose paragraph, `unclassified`."""
    examples = tmp_path / "examples"
    _example(examples, "alpha", title="Alpha *example*", first="Alpha first.")
    usage = _usage(tmp_path, "alpha")

    catalog = _build(examples, usage)

    assert catalog["classes"] == [catalog_generator.UNCLASSIFIED_CLASS]
    (entry,) = catalog["examples"]
    assert entry["id"] == "alpha"
    assert entry["name"] == "Alpha example"
    assert entry["description"] == "Alpha first. It has code and a link."
    assert entry["class"] == "unclassified"
    assert entry["config_yaml"] == _CONFIG
    assert entry["readme"].startswith("# Alpha *example*")
    # The description is not repeated in the excerpt; the list item is
    # skipped and the entity decoded.
    assert entry["readme_excerpt"] == "Second paragraph with μ = 0.001."


def test_order_follows_the_usage_guide_then_the_id(tmp_path: Path) -> None:
    """Examples the guide marks come first, in its order; the rest by ID."""
    examples = tmp_path / "examples"
    for example_id in ("delta", "alpha", "charlie", "bravo"):
        _example(examples, example_id)
    usage = _usage(tmp_path, "charlie", "alpha")

    catalog = _build(examples, usage)

    assert [entry["id"] for entry in catalog["examples"]] == [
        "charlie",
        "alpha",
        "bravo",
        "delta",
    ]


def test_directory_without_readme_is_not_an_example(tmp_path: Path) -> None:
    """An empty or scratch directory under `doc/examples/` is ignored."""
    examples = tmp_path / "examples"
    _example(examples, "alpha")
    (examples / "scratch").mkdir()

    catalog = _build(examples, _usage(tmp_path))

    assert [entry["id"] for entry in catalog["examples"]] == ["alpha"]


def test_example_without_a_configuration_is_listed_with_null_yaml(
    tmp_path: Path,
) -> None:
    """A script-reproduced example (no `config.yaml`) still gets an entry."""
    examples = tmp_path / "examples"
    _example(examples, "scripted", config=None)

    catalog = _build(examples, _usage(tmp_path))

    assert catalog["examples"][0]["config_yaml"] is None


def test_all_output_files_and_compressed_trajectories_are_bundled(
    tmp_path: Path,
) -> None:
    """Every result artifact survives bundling, including compressed JSONL parts."""
    examples = tmp_path / "examples"
    directory = _example(examples, "batch")
    (directory / "summary.json").write_text("{}\n", encoding="utf-8")
    (directory / "trajectory.tlog").write_bytes(b"log")
    (directory / "convergence.jsonl").write_text("{}\n", encoding="utf-8")
    (directory / "convergence.jsonl.gz.part-0001").write_bytes(b"archive")
    (directory / "pairwise.json").write_text("{}\n", encoding="utf-8")
    (directory / "scatter.png").write_bytes(b"image")
    replicate = directory / "replicate-001"
    replicate.mkdir()
    (replicate / "manifest.json").write_text('{"r": 1}\n', encoding="utf-8")
    (replicate / "report.json").write_text('{"r": 2}\n', encoding="utf-8")
    (replicate / "convergence.jsonl").write_text("{}\n", encoding="utf-8")

    catalog, outputs = catalog_generator.build_catalog(examples, _usage(tmp_path))

    assert catalog["examples"][0]["outputs"] == [
        "convergence.jsonl",
        "convergence.jsonl.gz.part-0001",
        "pairwise.json",
        "replicate-001/convergence.jsonl",
        "replicate-001/manifest.json",
        "replicate-001/report.json",
        "scatter.png",
        "summary.json",
        "trajectory.tlog",
    ]
    assert outputs == {
        "batch/summary.json": b"{}\n",
        "batch/replicate-001/manifest.json": b'{"r": 1}\n',
        "batch/replicate-001/report.json": b'{"r": 2}\n',
        "batch/replicate-001/convergence.jsonl": b"{}\n",
        "batch/pairwise.json": b"{}\n",
        "batch/scatter.png": b"image",
        "batch/trajectory.tlog": b"log",
        "batch/convergence.jsonl": b"{}\n",
        "batch/convergence.jsonl.gz.part-0001": b"archive",
    }


def test_labels_and_class_tree_drive_names_and_order(tmp_path: Path) -> None:
    """With labels and `classes.yaml`: label text, class order, unclassified last."""
    examples = tmp_path / "examples"
    examples.mkdir()
    (examples / "classes.yaml").write_text(
        "classes:\n"
        "  - id: second\n"
        "    title: Second class\n"
        "  - id: first\n"
        "    title: First class\n"
        "    description: The first.\n"
        "    children:\n"
        "      - id: first-child\n"
        "        title: Child class\n",
        encoding="utf-8",
    )
    labelled = (
        "name: Labelled name\n"
        "description: Labelled description.\n"
        "class: {class_id}\n"
        "_read_only: true\n" + _CONFIG
    )
    _example(examples, "a-child", config=labelled.format(class_id="first-child"))
    _example(examples, "b-second", config=labelled.format(class_id="second"))
    _example(examples, "c-first", config=labelled.format(class_id="first"))
    _example(examples, "d-loose")

    catalog = _build(examples, _usage(tmp_path))

    assert [entry["id"] for entry in catalog["classes"]] == [
        "second",
        "first",
        "unclassified",
    ]
    assert catalog["classes"][1] == {
        "id": "first",
        "title": "First class",
        "description": "The first.",
        "children": [
            {
                "id": "first-child",
                "title": "Child class",
                "description": "",
                "children": [],
            }
        ],
    }
    assert [(entry["id"], entry["class"]) for entry in catalog["examples"]] == [
        ("b-second", "second"),
        ("c-first", "first"),
        ("a-child", "first-child"),
        ("d-loose", "unclassified"),
    ]
    labelled_entry = catalog["examples"][0]
    assert labelled_entry["name"] == "Labelled name"
    assert labelled_entry["description"] == "Labelled description."
    # The configuration text is shipped unmodified, labels included.
    assert labelled_entry["config_yaml"].startswith("name: Labelled name\n")


@pytest.mark.parametrize(
    ("classes_yaml", "config", "message"),
    [
        (None, "class: nowhere\n" + _CONFIG, "unknown class 'nowhere'"),
        (
            "classes:\n  - id: used\n    title: Used\n"
            "  - id: empty\n    title: Empty\n",
            "class: used\n" + _CONFIG,
            "class empty has no examples",
        ),
        (
            "classes:\n  - id: Bad_Id\n    title: Bad\n",
            _CONFIG,
            "kebab-case",
        ),
        (
            "classes:\n  - id: twice\n    title: A\n"
            "    children:\n      - id: twice\n        title: B\n",
            _CONFIG,
            "is used more than once",
        ),
        (
            "classes:\n  - id: top\n    title: Top\n    children:\n"
            "      - id: kid\n        title: Kid\n        children:\n"
            "          - id: grandkid\n            title: Grandkid\n",
            _CONFIG,
            "at most 2 levels deep",
        ),
        (None, "name: ''\n" + _CONFIG, "name must be non-empty text"),
    ],
)
def test_rule_violations_are_reported(
    tmp_path: Path, classes_yaml: str | None, config: str, message: str
) -> None:
    """Unknown or empty classes, bad IDs, deep trees and empty labels fail."""
    examples = tmp_path / "examples"
    examples.mkdir()
    if classes_yaml is not None:
        (examples / "classes.yaml").write_text(classes_yaml, encoding="utf-8")
    _example(examples, "only", config=config)

    with pytest.raises(catalog_generator.CatalogError, match=message):
        _build(examples, _usage(tmp_path))


def test_write_bundle_replaces_stale_files_and_reports_current(tmp_path: Path) -> None:
    """`write_bundle` leaves exactly the listed files; `stale_paths` then is empty."""
    bundle = tmp_path / "bundle"
    (bundle / "gone").mkdir(parents=True)
    (bundle / "gone" / "report.json").write_text("old\n", encoding="utf-8")
    (bundle / "catalog.json").write_text("old\n", encoding="utf-8")
    files = {"catalog.json": b"new\n", "kept/report.json": b"{}\n"}

    assert catalog_generator.stale_paths(bundle, files) == [
        "catalog.json",
        "gone/report.json",
        "kept/report.json",
    ]

    catalog_generator.write_bundle(bundle, files)

    assert catalog_generator.stale_paths(bundle, files) == []
    assert not (bundle / "gone").exists()
    assert (bundle / "catalog.json").read_bytes() == b"new\n"


def test_check_mode_reports_a_stale_bundle_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--check` exits 1 on a stale bundle and changes nothing; a refresh fixes it."""
    examples = tmp_path / "examples"
    _example(examples, "alpha")
    bundle = tmp_path / "bundle"
    monkeypatch.setattr(catalog_generator, "EXAMPLES_DIR", examples)
    monkeypatch.setattr(catalog_generator, "USAGE_PATH", _usage(tmp_path))
    monkeypatch.setattr(catalog_generator, "BUNDLE_DIR", bundle)

    assert catalog_generator.main(["--check"]) == 1
    assert not bundle.exists()
    assert "stale bundle file(s): catalog.json" in capsys.readouterr().err

    assert catalog_generator.main([]) == 0
    assert catalog_generator.main(["--check"]) == 0
