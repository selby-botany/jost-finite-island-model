"""Unit tests for `fim.gui.presets` (no display, no `gui` marker).

`load_catalog`/`list_presets`/`get_preset` only read the bundled
`webui/examples/catalog.json` from disk (written by
`dev/bin/build-examples-catalog`) — none of the pywebview machinery this
package's other tests need.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from fim.gui.presets import (
    Preset,
    get_example,
    get_preset,
    list_presets,
    load_catalog,
    split_configuration,
    strip_internal_yaml_keys,
)

_ROOT = Path(__file__).resolve().parents[2]
_REAL_WEBUI_DIRECTORY = _ROOT / "src" / "fim" / "gui" / "webui"

# The examples that have a configuration, in catalog order — a snapshot,
# not a re-derivation: adding, removing, or reordering an example (or
# assigning classes, which regroups them) is meant to need this list
# updated right alongside it, so a human notices.
_EXPECTED_PRESET_IDS = (
    "unequal-island-sizes-with-a-migration-hub",
    "stepping-stone-spatial-migration",
    "literature-distance-statistics-from-an-explicit-founder-split",
    "equilibrium-split-founding",
    "stochastic-migrant-counts",
    "finite-length-alleles-the-k-allele-model",
    "wright-takahata-finite-deme-correction",
    "kimura-weiss-isolation-by-distance",
    "per-base-mutation-rate-across-unequal-locus-lengths",
    "several-convergence-statistics",
    "within-run-sigma-band",
    "an-adaptive-replicate-batch-with-a-confidence-interval",
    "a-large-d-batch-under-generational-vector",
    "a-long-locus-batch-under-the-generational-engine",
    "dear-nolan-low",
    "golden-part-vi",
)

_REAL_PRESETS = list_presets(_REAL_WEBUI_DIRECTORY)


def _write_catalog(webui: Path, catalog: dict[str, Any]) -> None:
    """Write a fixture catalog where `load_catalog` reads it."""
    (webui / "examples").mkdir(parents=True)
    (webui / "examples" / "catalog.json").write_text(
        json.dumps(catalog), encoding="utf-8"
    )


def test_list_presets_returns_every_example_with_a_configuration() -> None:
    """Every bundled example with a `config.yaml` is a preset, in catalog order."""
    assert tuple(preset.preset_id for preset in _REAL_PRESETS) == _EXPECTED_PRESET_IDS
    for preset in _REAL_PRESETS:
        assert preset.yaml_text.strip() != ""
        assert preset.title != ""


def test_presets_use_the_canonical_example_configs() -> None:
    """Each preset's text is its `doc/examples/<id>/config.yaml`, unmodified."""
    examples_directory = _ROOT / "doc" / "examples"
    configs = {
        path.parent.name: path.read_text(encoding="utf-8")
        for path in examples_directory.glob("*/config.yaml")
    }

    assert {preset.preset_id: preset.yaml_text for preset in _REAL_PRESETS} == configs


def test_catalog_includes_the_script_reproduced_example_without_yaml() -> None:
    """`dear-nolan-high` has no `config.yaml`: listed, but not as a preset."""
    example = get_example(_REAL_WEBUI_DIRECTORY, "dear-nolan-high")

    assert example is not None
    assert example.yaml_text is None
    assert example.readme.startswith("# ")
    assert get_preset(_REAL_WEBUI_DIRECTORY, "dear-nolan-high") is None


def test_every_catalog_example_names_a_class_in_the_tree() -> None:
    """The bundled class tree covers every example's class."""
    catalog = load_catalog(_REAL_WEBUI_DIRECTORY)
    class_ids = {
        example_class.class_id
        for parent in catalog.classes
        for example_class in (parent, *parent.children)
    }

    assert catalog.examples
    assert {example.class_id for example in catalog.examples} <= class_ids


def test_get_preset_returns_the_matching_preset() -> None:
    """`get_preset` finds one preset by its own id."""
    preset = get_preset(_REAL_WEBUI_DIRECTORY, "stepping-stone-spatial-migration")

    assert preset is not None
    assert preset.title == "Stepping-stone (spatial) migration"
    assert "topology: ring" in preset.yaml_text


def test_get_preset_returns_none_for_an_unknown_id() -> None:
    """An id naming no real preset is `None`, not a raised exception."""
    assert get_preset(_REAL_WEBUI_DIRECTORY, "not-a-real-preset") is None


def test_list_presets_returns_empty_for_a_directory_with_no_catalog(
    tmp_path: Path,
) -> None:
    """A missing `examples/catalog.json` (a stale install) is `[]`, not a crash."""
    assert list_presets(tmp_path) == []
    assert load_catalog(tmp_path).examples == ()


def test_malformed_catalog_reads_as_empty(tmp_path: Path) -> None:
    """A catalog missing its keys is treated as no examples at all."""
    _write_catalog(tmp_path, {"examples": [{"id": "x"}]})

    assert load_catalog(tmp_path).examples == ()


def test_load_catalog_reads_classes_and_examples(tmp_path: Path) -> None:
    """A fixture catalog round-trips into `ExampleClass`/`Example` values."""
    _write_catalog(
        tmp_path,
        {
            "classes": [
                {
                    "id": "parent",
                    "title": "Parent",
                    "description": "",
                    "children": [
                        {
                            "id": "kid",
                            "title": "Kid",
                            "description": "K.",
                            "children": [],
                        }
                    ],
                }
            ],
            "examples": [
                {
                    "id": "one",
                    "name": "One",
                    "description": "First.",
                    "class": "kid",
                    "readme": "# One\n",
                    "readme_excerpt": "Excerpt.",
                    "config_yaml": "N: 1\n",
                    "outputs": ["report.json"],
                }
            ],
        },
    )

    catalog = load_catalog(tmp_path)

    assert catalog.classes[0].to_dict() == {
        "id": "parent",
        "title": "Parent",
        "description": "",
        "children": [
            {"id": "kid", "title": "Kid", "description": "K.", "children": []}
        ],
    }
    (example,) = catalog.examples
    assert (example.example_id, example.class_id, example.outputs) == (
        "one",
        "kid",
        ("report.json",),
    )
    assert list_presets(tmp_path) == [Preset("one", "One", "N: 1\n")]


def test_split_configuration_separates_labels_and_drops_internal_keys() -> None:
    """Labels come out; `_` keys vanish; every model key stays."""
    model, labels = split_configuration(
        {
            "name": "A name",
            "description": "A description.",
            "class": "migration",
            "_read_only": True,
            "_future": 1,
            "N": 10,
            "d": 2,
        }
    )

    assert model == {"N": 10, "d": 2}
    assert labels == {
        "name": "A name",
        "description": "A description.",
        "class": "migration",
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("_read_only: true\nN: 10\n", "N: 10\n"),
        ("N: 10\n_read_only: true\nd: 2\n", "N: 10\nd: 2\n"),
        ("N: 10\n_nested:\n  a: 1\n  b: 2\nd: 2\n", "N: 10\nd: 2\n"),
        ("name: Kept\nN: 10\n_read_only: true", "name: Kept\nN: 10"),
        ("loci:\n  - _not_top_level: 1\n", "loci:\n  - _not_top_level: 1\n"),
    ],
)
def test_strip_internal_yaml_keys(text: str, expected: str) -> None:
    """Only top-level `_` keys (and their indented blocks) are removed."""
    stripped = strip_internal_yaml_keys(text)

    assert stripped == expected
    assert not any(str(key).startswith("_") for key in yaml.safe_load(stripped))
