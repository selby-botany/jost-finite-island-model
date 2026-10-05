"""Unit tests for `fim.examples.classes`, the run-class tree reader.

Kept beside the run-metadata tests because a run's `class` label is
checked against this tree before it is written to `metadata.json`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
import yaml

from fim.examples import classes
from fim.examples.classes import (
    ClassTree,
    RunClass,
    is_valid_class_id,
    parse_class_tree,
    read_class_tree,
    validate_run_class,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "classes.yaml"


def _write(tmp_path: Path, payload: Any) -> Path:
    """Write `payload` as YAML to a temporary class file and return its path."""
    path = tmp_path / "classes.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def test_fixture_reads_as_the_documented_tree() -> None:
    """The design's example tree reads back whole, children included."""
    tree = read_class_tree(FIXTURE)

    assert [entry.class_id for entry in tree.classes] == [
        "getting-started",
        "migration",
        "mutation-and-loci",
        "replicates-and-engines",
        "literature",
    ]
    literature = tree.get("literature")
    assert literature is not None
    assert literature.children == (
        RunClass(class_id="literature-distances", title="Genetic distances"),
    )
    assert tree.get("getting-started") == RunClass(
        class_id="getting-started",
        title="Getting started",
        description="One-screen runs that show the core statistics settling.",
    )


def test_order_in_the_file_is_preserved() -> None:
    """Display order is file order, not sorted order, children after parents."""
    payload = {
        "classes": [
            {"id": "zeta", "title": "Zeta"},
            {
                "id": "alpha",
                "title": "Alpha",
                "children": [
                    {"id": "alpha-two", "title": "Two"},
                    {"id": "alpha-one", "title": "One"},
                ],
            },
            {"id": "mu", "title": "Mu"},
        ]
    }

    tree = parse_class_tree(payload)

    assert tree.ids() == ("zeta", "alpha", "alpha-two", "alpha-one", "mu")
    assert [entry["id"] for entry in tree.to_list()] == ["zeta", "alpha", "mu"]


def test_lookup_finds_parents_and_children() -> None:
    """`in`, `get`, and `parent_of` see every level of the tree."""
    tree = read_class_tree(FIXTURE)

    assert "literature-distances" in tree
    assert "migration" in tree
    assert "nonexistent" not in tree
    assert 3 not in tree
    assert tree.get("nonexistent") is None
    parent = tree.parent_of("literature-distances")
    assert parent is not None
    assert parent.class_id == "literature"
    assert tree.parent_of("literature") is None


def test_to_list_round_trips_through_the_parser() -> None:
    """The bridge-ready list parses back to an identical tree."""
    tree = read_class_tree(FIXTURE)

    assert parse_class_tree({"classes": tree.to_list()}) == tree


@pytest.mark.parametrize(
    "children",
    [
        [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}],
        [{"id": "parent", "title": "Parent again"}],
    ],
    ids=["between-top-level-classes", "child-repeats-its-parent"],
)
def test_duplicate_ids_are_rejected_across_the_whole_tree(
    children: list[dict[str, str]],
) -> None:
    """An ID may appear once, whether at the top level or as a child."""
    payload = {
        "classes": [
            {"id": "a", "title": "A"},
            {"id": "parent", "title": "Parent", "children": children},
        ]
    }

    with pytest.raises(ValueError, match="used more than once"):
        parse_class_tree(payload)


def test_a_grandchild_is_rejected_as_too_deep() -> None:
    """The tree holds top-level classes and one level of children only."""
    payload = {
        "classes": [
            {
                "id": "top",
                "title": "Top",
                "children": [
                    {
                        "id": "middle",
                        "title": "Middle",
                        "children": [{"id": "bottom", "title": "Bottom"}],
                    }
                ],
            }
        ]
    }

    with pytest.raises(ValueError, match="at most 2 levels deep"):
        parse_class_tree(payload)


@pytest.mark.parametrize(
    "class_id",
    [
        "Getting-started",
        "getting_started",
        "getting started",
        "-leading",
        "trailing-",
        "double--hyphen",
        "",
        7,
        None,
    ],
)
def test_a_badly_formed_id_is_rejected(class_id: object) -> None:
    """IDs are lowercase kebab-case strings, nothing else."""
    with pytest.raises(ValueError, match="kebab-case"):
        parse_class_tree({"classes": [{"id": class_id, "title": "Title"}]})


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "root must be a mapping"),
        ({"classes": []}, "nonempty list"),
        ({"classes": "getting-started"}, "nonempty list"),
        ({"classes": [{"id": "a", "title": "A"}], "extra": 1}, "unknown class file"),
        ({"classes": ["a"]}, r"classes\[0\] must be a mapping"),
        ({"classes": [{"id": "a"}]}, "nonblank title"),
        ({"classes": [{"id": "a", "title": "  "}]}, "nonblank title"),
        ({"classes": [{"id": "a", "title": "A", "description": ""}]}, "description"),
        ({"classes": [{"id": "a", "title": "A", "colour": "red"}]}, "unknown"),
        ({"classes": [{"id": "a", "title": "A", "children": []}]}, "nonempty list"),
    ],
)
def test_malformed_files_are_rejected_with_a_named_reason(
    payload: object, message: str
) -> None:
    """Every rule in the module docstring has its own clear error."""
    with pytest.raises(ValueError, match=message):
        parse_class_tree(payload)


def test_invalid_yaml_is_a_value_error(tmp_path: Path) -> None:
    """A file that is not YAML at all reports as a `ValueError`, not a crash."""
    path = tmp_path / "classes.yaml"
    path.write_text("classes: [unclosed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="not valid YAML"):
        read_class_tree(path)


def test_is_valid_class_id_accepts_kebab_case() -> None:
    """Single words, digits, and hyphen-joined words are all well formed."""
    assert is_valid_class_id("literature")
    assert is_valid_class_id("literature-distances")
    assert is_valid_class_id("part-6")


def test_validate_run_class_accepts_a_known_class() -> None:
    """A class listed anywhere in the file is accepted and returned unchanged."""
    assert validate_run_class("literature-distances", path=FIXTURE) == (
        "literature-distances"
    )


def test_validate_run_class_rejects_an_unknown_class() -> None:
    """A well-formed ID the file does not list is rejected, naming the choices."""
    with pytest.raises(ValueError, match=r"unknown class 'galaxy'.*getting-started"):
        validate_run_class("galaxy", path=FIXTURE)


def test_validate_run_class_without_a_class_file_checks_format_only(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """No class file (a packaged build) accepts any well-formed ID, with a warning."""
    missing = tmp_path / "absent.yaml"

    with caplog.at_level(logging.WARNING, logger="fim.examples.classes"):
        assert validate_run_class("anything-goes", path=missing) == "anything-goes"

    assert "accepted without checking" in caplog.text
    with pytest.raises(ValueError, match="kebab-case"):
        validate_run_class("Not Kebab", path=missing)


def test_validate_run_class_reports_a_malformed_class_file(tmp_path: Path) -> None:
    """A broken class file is an error, not silently treated as absent."""
    path = _write(tmp_path, {"classes": [{"id": "a", "title": "A"}] * 2})

    with pytest.raises(ValueError, match="used more than once"):
        validate_run_class("a", path=path)


def test_load_default_class_tree_is_none_without_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default loader returns `None` where no class file exists."""
    monkeypatch.setattr(
        classes, "default_classes_path", lambda: tmp_path / "absent.yaml"
    )

    assert classes.load_default_class_tree() is None


def test_load_default_class_tree_reads_the_default_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default loader reads whatever `default_classes_path` names."""
    monkeypatch.setattr(classes, "default_classes_path", lambda: FIXTURE)

    tree = classes.load_default_class_tree()

    assert isinstance(tree, ClassTree)
    assert "migration" in tree


def test_default_classes_path_points_into_the_checkout_examples() -> None:
    """The default path is `doc/examples/classes.yaml` under the checkout root."""
    path = classes.default_classes_path()

    assert path.parts[-3:] == ("doc", "examples", "classes.yaml")
    assert (path.parents[2] / "pyproject.toml").is_file()
