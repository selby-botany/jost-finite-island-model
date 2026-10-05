"""Read the bundled examples catalog for the Examples dialog and presets.

Every `doc/examples/<id>/` directory is one example.
`dev/bin/build-examples-catalog` writes them, with the class tree from
`doc/examples/classes.yaml`, into `webui/examples/catalog.json` at commit
time (design doc
`20261005-claude-opus-5-5-read-only-examples-and-classes-design.md`
§4.1, `selby/restricted`). `packaging/fim.spec` bundles the whole
`webui/` tree into every packaged executable, so the catalog exists both
in a development checkout and inside a frozen application, while
`doc/examples/` itself is not bundled.

An example's configuration may carry labels (`name`, `description`,
`class`) and internal attributes (`_read_only` and any other key that
starts with `_`). `split_configuration` separates them from the model
keys, so a loaded example reaches the Configure form as an ordinary,
editable configuration (design §1, "Consequence").

`list_presets` and `get_preset` are the older, flat view of the same
catalog, kept as thin aliases until their callers (the File menu's
"Load example…" picker) move to the Examples dialog (design §5).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The bundled catalog, relative to the `webui/` directory.
CATALOG_RELATIVE_PATH = Path("examples") / "catalog.json"

# Configuration keys that label a run rather than configure the model
# (design §1). Never part of the run ID; never sent to the form.
LABEL_KEYS = ("name", "description", "class")

# A top-level configuration key starting with this is an internal
# attribute (`_read_only`), which the form always drops.
INTERNAL_KEY_PREFIX = "_"

# A top-level `_key:` line in YAML text, with any indented lines that
# belong to it. Only the plain block style the examples use is handled.
_INTERNAL_YAML_KEY = re.compile(r"^_[^\s:]*:.*\n(?:[ \t]+.*\n|[ \t]*\n)*", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class ExampleClass:
    """One class in the examples tree (design §2).

    Args:
        class_id: Lowercase kebab-case ID, unique across the tree.
        title: Display title.
        description: One line about the class; empty when none is given.
        children: Child classes; the tree is at most two levels deep.
    """

    class_id: str
    title: str
    description: str
    children: tuple[ExampleClass, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return the class as the JSON shape the bridge sends the page."""
        return {
            "id": self.class_id,
            "title": self.title,
            "description": self.description,
            "children": [child.to_dict() for child in self.children],
        }


@dataclass(frozen=True, slots=True)
class Example:
    """One bundled example.

    Args:
        example_id: The example directory's name.
        name: Display name: the `name` label, or the README's heading.
        description: The `description` label, or the README's first
            paragraph.
        class_id: The `class` label, or `"unclassified"`.
        readme: The full README Markdown.
        readme_excerpt: A short plain-text excerpt of the README.
        yaml_text: The configuration's YAML text, unmodified, or `None`
            for an example reproduced by a script instead.
        outputs: The example's bundled output files, relative to
            `webui/examples/<example_id>/`.
    """

    example_id: str
    name: str
    description: str
    class_id: str
    readme: str
    readme_excerpt: str
    yaml_text: str | None
    outputs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Catalog:
    """The bundled class tree and examples, in display order."""

    classes: tuple[ExampleClass, ...] = ()
    examples: tuple[Example, ...] = ()


@dataclass(frozen=True, slots=True)
class Preset:
    """One example with a configuration, in the older flat preset view.

    Args:
        preset_id: The example's ID.
        title: The example's display name.
        yaml_text: The example's configuration, ready for
            `yaml.safe_load`.
    """

    preset_id: str
    title: str
    yaml_text: str


def _example_class(raw: Mapping[str, Any]) -> ExampleClass:
    """Build one `ExampleClass` from its catalog mapping."""
    return ExampleClass(
        class_id=str(raw["id"]),
        title=str(raw["title"]),
        description=str(raw.get("description") or ""),
        children=tuple(_example_class(child) for child in raw.get("children", [])),
    )


def _example(raw: Mapping[str, Any]) -> Example:
    """Build one `Example` from its catalog mapping."""
    yaml_text = raw.get("config_yaml")
    return Example(
        example_id=str(raw["id"]),
        name=str(raw["name"]),
        description=str(raw.get("description") or ""),
        class_id=str(raw["class"]),
        readme=str(raw.get("readme") or ""),
        readme_excerpt=str(raw.get("readme_excerpt") or ""),
        yaml_text=None if yaml_text is None else str(yaml_text),
        outputs=tuple(str(path) for path in raw.get("outputs", [])),
    )


def get_example(webui_directory: Path, example_id: str) -> Example | None:
    """Return one example by ID, or `None` if the catalog has no such example.

    Args:
        webui_directory: Same as `load_catalog`.
        example_id: An `Example.example_id`.
    """
    for example in load_catalog(webui_directory).examples:
        if example.example_id == example_id:
            return example
    return None


def get_preset(webui_directory: Path, preset_id: str) -> Preset | None:
    """Return one preset by ID, or `None` if no such preset exists.

    Args:
        webui_directory: Same as `list_presets`.
        preset_id: A `Preset.preset_id` from a prior `list_presets` call.
    """
    for preset in list_presets(webui_directory):
        if preset.preset_id == preset_id:
            return preset
    return None


def list_presets(webui_directory: Path) -> list[Preset]:
    """Return every example that has a configuration, as a flat preset list.

    Args:
        webui_directory: `fim.gui.app._webui_directory()`'s return value.

    Returns:
        One `Preset` per example with a `config.yaml`, in catalog order.
        An example reproduced by a script has no configuration to load
        and is left out. Empty when the catalog is missing or unreadable.
    """
    return [
        Preset(
            preset_id=example.example_id,
            title=example.name,
            yaml_text=example.yaml_text,
        )
        for example in load_catalog(webui_directory).examples
        if example.yaml_text is not None
    ]


def load_catalog(webui_directory: Path) -> Catalog:
    """Return the bundled examples catalog.

    Args:
        webui_directory: `fim.gui.app._webui_directory()`'s return value,
            the directory holding `index.html` and `examples/`, frozen
            or not.

    Returns:
        The catalog, or an empty `Catalog` if `examples/catalog.json` is
        missing or malformed. Callers treat that as "no examples
        available" (a stale or hand-modified install), not a reason to
        fail the Configure screen.
    """
    try:
        data = json.loads(
            (webui_directory / CATALOG_RELATIVE_PATH).read_text(encoding="utf-8")
        )
        return Catalog(
            classes=tuple(_example_class(raw) for raw in data["classes"]),
            examples=tuple(_example(raw) for raw in data["examples"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return Catalog()


def split_configuration(
    configuration: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Separate a configuration's model keys from its labels.

    Internal attributes (any top-level key starting with `_`) are
    dropped, so a run started from a loaded example is an ordinary,
    editable run (design §1).

    Args:
        configuration: A parsed `config.yaml` mapping.

    Returns:
        `(model, labels)`: the configuration without labels or internal
        attributes, and the labels that were present (a subset of
        `LABEL_KEYS`).
    """
    model: dict[str, Any] = {}
    labels: dict[str, Any] = {}
    for key, value in configuration.items():
        if key in LABEL_KEYS:
            labels[key] = value
        elif not str(key).startswith(INTERNAL_KEY_PREFIX):
            model[key] = value
    return model, labels


def strip_internal_yaml_keys(yaml_text: str) -> str:
    """Return `yaml_text` without its top-level `_` keys.

    The "View YAML" text is meant to be copied into a new configuration
    file; an internal attribute such as `_read_only: true` would make
    the copy's run read-only, with the shipped example's run ID. Labels
    stay, since `fim run` records them as the new run's name.

    Args:
        yaml_text: A configuration in plain block-style YAML.

    Returns:
        The same text with each top-level `_key:` entry, and any
        indented lines belonging to it, removed.
    """
    text = yaml_text if yaml_text.endswith("\n") else yaml_text + "\n"
    stripped = _INTERNAL_YAML_KEY.sub("", text)
    return stripped if yaml_text.endswith("\n") else stripped.removesuffix("\n")
