"""Read and validate the run-class tree, `doc/examples/classes.yaml`.

A **class** is a group ID for runs: the worked examples use classes to
build the Examples dialog's tree, and a user's own run may carry one
too, as the configuration label `class` (`doc/configuration.md`). This
module is the only reader of the class file (design doc
`20261005-claude-opus-5-5-read-only-examples-and-classes-design.md`,
`selby/restricted`, section 2).

The file holds one top-level key, `classes`, a list of entries:

```yaml
classes:
  - id: getting-started
    title: Getting started
    description: One-screen runs that show the core statistics settling.
  - id: literature
    title: Literature comparisons
    children:
      - id: literature-distances
        title: Genetic distances
```

Rules, all enforced by `parse_class_tree`:

- The tree is shallow: top-level classes plus at most one level of
  `children`. A child with `children` of its own is rejected.
- Order in the file is display order, and it is preserved.
- Every `id` is lowercase kebab-case (`getting-started`) and unique
  across the whole tree, parents and children together.
- `title` is required; `description` is optional. Any other key is
  rejected, so a typo is reported rather than ignored.

The file lives in the source checkout, beside the examples. A packaged
build carries no `doc/` directory, so `load_default_class_tree` returns
`None` there, and `validate_run_class` then checks only the ID's format
(see its docstring for why that is the safe choice).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, TypeGuard

import yaml

import fim

logger = logging.getLogger(__name__)

CLASSES_FILE_NAME: Final = "classes.yaml"

# Top-level classes plus one level of children.
MAX_CLASS_DEPTH: Final = 2

_CLASS_ID_PATTERN: Final = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_ROOT_KEYS: Final = frozenset({"classes"})
_ENTRY_KEYS: Final = frozenset({"id", "title", "description", "children"})


@dataclass(frozen=True, slots=True)
class RunClass:
    """One class in the tree: its ID, display text, and child classes.

    Args:
        class_id: Lowercase kebab-case ID, unique across the whole tree.
        title: Short display name.
        description: Optional one-line summary, or `None`.
        children: Child classes in display order; always empty for a
            child class, since the tree is at most two levels deep.
    """

    class_id: str
    title: str
    description: str | None = None
    children: tuple[RunClass, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable mapping in the file's own key names."""
        result: dict[str, object] = {"id": self.class_id, "title": self.title}
        if self.description is not None:
            result["description"] = self.description
        if self.children:
            result["children"] = [child.to_dict() for child in self.children]
        return result


@dataclass(frozen=True, slots=True)
class ClassTree:
    """The whole validated class tree, top-level classes in display order.

    Args:
        classes: The top-level classes, each with its own children.
    """

    classes: tuple[RunClass, ...]

    def __contains__(self, class_id: object) -> bool:
        """Return whether `class_id` names a class anywhere in the tree."""
        return self.get(class_id) is not None if isinstance(class_id, str) else False

    def __iter__(self) -> Iterator[RunClass]:
        """Yield every class, depth first: each parent, then its children."""
        for parent in self.classes:
            yield parent
            yield from parent.children

    def get(self, class_id: str) -> RunClass | None:
        """Return the class with this ID, or `None` when there is none.

        Args:
            class_id: The ID to look up.

        Returns:
            The matching class, parent or child, or `None`.
        """
        return next((entry for entry in self if entry.class_id == class_id), None)

    def ids(self) -> tuple[str, ...]:
        """Return every class ID in display order (see `__iter__`)."""
        return tuple(entry.class_id for entry in self)

    def parent_of(self, class_id: str) -> RunClass | None:
        """Return the parent of a child class, or `None` for a top-level one.

        Args:
            class_id: The ID of the class whose parent is wanted.

        Returns:
            The parent class, or `None` when `class_id` is a top-level
            class or names no class at all.
        """
        return next(
            (
                parent
                for parent in self.classes
                if any(child.class_id == class_id for child in parent.children)
            ),
            None,
        )

    def to_list(self) -> list[dict[str, object]]:
        """Return the tree as JSON-serializable data, in the file's own shape."""
        return [entry.to_dict() for entry in self.classes]


def default_classes_path() -> Path:
    """Return where the class file lives in a source checkout.

    Anchored on the `fim` package's own `__init__.py`, as
    `fim.paths.project_root` is, so the answer does not depend on the
    current directory: `src/fim/__init__.py` climbs two levels to the
    checkout root, then down to `doc/examples/classes.yaml`.

    Returns:
        The path, whether or not a file exists there.
    """
    source_root = Path(fim.__file__).resolve().parents[2]
    return source_root / "doc" / "examples" / CLASSES_FILE_NAME


def is_valid_class_id(text: object) -> TypeGuard[str]:
    """Return whether `text` is a lowercase kebab-case class ID.

    Args:
        text: The candidate value, of any type.

    Returns:
        `True` for a string such as `getting-started` or `literature`,
        `False` for anything else (upper case, spaces, underscores, a
        leading or trailing hyphen, a doubled hyphen, or a non-string).
    """
    return isinstance(text, str) and _CLASS_ID_PATTERN.fullmatch(text) is not None


def load_default_class_tree() -> ClassTree | None:
    """Read the checkout's class file, or return `None` when it is absent.

    Returns:
        The validated tree, or `None` when `default_classes_path` names
        no file (a packaged build carries no `doc/` directory).

    Raises:
        OSError: The file exists but cannot be read.
        ValueError: The file exists but is not a valid class tree.
    """
    path = default_classes_path()
    if not path.is_file():
        logger.debug("no class file at %s", path)
        return None
    return read_class_tree(path)


def parse_class_tree(payload: object) -> ClassTree:
    """Validate a parsed class file and build its tree.

    Args:
        payload: The parsed YAML document.

    Returns:
        The validated tree, in the file's own order.

    Raises:
        ValueError: The document breaks one of the rules in this
            module's docstring; the message names the offending entry.
    """
    # The root: exactly one key, a nonempty list.
    if not isinstance(payload, Mapping):
        raise ValueError("class file root must be a mapping with a 'classes' list")
    unknown = sorted(str(key) for key in set(payload) - _ROOT_KEYS)
    if unknown:
        raise ValueError(f"unknown class file key(s): {', '.join(unknown)}")
    entries = payload.get("classes")
    if not isinstance(entries, list) or not entries:
        raise ValueError("class file 'classes' must be a nonempty list")

    # Every entry, recursively, sharing one set of seen IDs so that a
    # child cannot reuse a parent's (or a cousin's) ID either.
    seen: set[str] = set()
    classes = tuple(
        _parse_entry(entry, depth=1, location=f"classes[{index}]", seen=seen)
        for index, entry in enumerate(entries)
    )
    return ClassTree(classes=classes)


def read_class_tree(path: Path | str) -> ClassTree:
    """Read and validate one class file.

    Args:
        path: The YAML file to read.

    Returns:
        The validated tree.

    Raises:
        OSError: The file cannot be read.
        ValueError: The file is not valid YAML or not a valid class tree.
    """
    class_path = Path(path)
    logger.debug("reading class file: %s", class_path)
    text = class_path.read_text(encoding="utf-8")
    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ValueError(
            f"class file {class_path} is not valid YAML: {error}"
        ) from error
    return parse_class_tree(payload)


def validate_run_class(class_id: object, *, path: Path | str | None = None) -> str:
    """Check a configuration's `class` label and return it.

    With a class file available, `class_id` must name one of its
    classes. Without one (a packaged build has no `doc/` directory),
    only the ID's format is checked and a warning is logged. That is
    the safe choice: a class is a label, never part of the run's ID or
    its results, so refusing to run a valid example configuration only
    because the file listing the classes was not shipped would block a
    correct run to protect nothing. A malformed ID is still rejected in
    both cases.

    Args:
        class_id: The value of the configuration's `class` key.
        path: The class file to check against (default:
            `default_classes_path`); a path naming no file means "no
            class file available".

    Returns:
        `class_id`, unchanged.

    Raises:
        ValueError: `class_id` is not a kebab-case string, or names no
            class in an available class file, or the class file itself
            is malformed.
    """
    # The format check applies whether or not the file is available.
    if not is_valid_class_id(class_id):
        raise ValueError(
            f"class must be a lowercase kebab-case class ID such as "
            f"'getting-started', not {class_id!r}"
        )

    # Membership needs the file; without it, accept with a warning.
    class_path = Path(path) if path is not None else default_classes_path()
    if not class_path.is_file():
        logger.warning(
            "class %r accepted without checking: no class file at %s",
            class_id,
            class_path,
        )
        return class_id
    tree = read_class_tree(class_path)
    if class_id not in tree:
        known = ", ".join(tree.ids())
        raise ValueError(f"unknown class {class_id!r}; known classes: {known}")
    return class_id


def _parse_entry(entry: Any, *, depth: int, location: str, seen: set[str]) -> RunClass:
    """Validate one class entry (and its children) and build it.

    Args:
        entry: The parsed entry.
        depth: 1 for a top-level class, 2 for a child.
        location: Where the entry sits, for error messages
            (`classes[4].children[0]`).
        seen: Every ID accepted so far, across the whole tree; updated.

    Returns:
        The validated class.

    Raises:
        ValueError: The entry breaks a rule in this module's docstring.
    """
    # Shape and keys.
    if not isinstance(entry, Mapping):
        raise ValueError(f"{location} must be a mapping")
    unknown = sorted(str(key) for key in set(entry) - _ENTRY_KEYS)
    if unknown:
        raise ValueError(f"unknown {location} key(s): {', '.join(unknown)}")

    # The ID: present, well formed, and unique across the tree.
    class_id = entry.get("id")
    if not is_valid_class_id(class_id):
        raise ValueError(
            f"{location} id must be lowercase kebab-case, such as "
            f"'getting-started', not {class_id!r}"
        )
    if class_id in seen:
        raise ValueError(f"{location} id {class_id!r} is used more than once")
    seen.add(class_id)

    # Display text.
    title = entry.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"{location} ({class_id}) needs a nonblank title")
    description = entry.get("description")
    if description is not None and (
        not isinstance(description, str) or not description.strip()
    ):
        raise ValueError(
            f"{location} ({class_id}) description must be nonblank text when given"
        )

    # Children: one level only, and a nonempty list when present.
    raw_children = entry.get("children")
    children: tuple[RunClass, ...] = ()
    if raw_children is not None:
        if depth >= MAX_CLASS_DEPTH:
            raise ValueError(
                f"{location} ({class_id}) has children, but the class tree "
                f"is at most {MAX_CLASS_DEPTH} levels deep"
            )
        if not isinstance(raw_children, list) or not raw_children:
            raise ValueError(
                f"{location} ({class_id}) children must be a nonempty list"
            )
        children = tuple(
            _parse_entry(
                child,
                depth=depth + 1,
                location=f"{location}.children[{index}]",
                seen=seen,
            )
            for index, child in enumerate(raw_children)
        )
    return RunClass(
        class_id=class_id,
        title=title.strip(),
        description=description.strip() if description is not None else None,
        children=children,
    )
