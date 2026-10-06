"""Seed the bundled worked examples into the results folder as read-only runs.

Read-only examples design (`20261005-claude-opus-5-5-read-only-examples-
and-classes-design.md`, `selby/restricted`), section 4.2. The desktop app
carries every worked example in `webui/examples/` (`dev/bin/build-
examples-catalog` writes it): a `catalog.json` naming each example's
labels, class, and configuration, and a copy of each example's saved
output files. `seed_examples` turns that bundle into ordinary results the
Home card can list:

- **Run directories.** An example with a saved result (a `manifest.json`
  in the bundle) gets `results/examples/<id>/`, holding its
  `config.yaml`, its saved output files, and a `metadata.json` with its
  name, description, and class. Every file there is app-owned: a newer
  bundle replaces a file whose content differs, and removes one it no
  longer ships.
- **Studies.** One read-only Study per class that has at least one
  seeded example, with the fixed id `study-examples-<class-id>` and the
  class title as its name; a child class is its own Study, named
  "Parent — Child".
- **Experiment.** One read-only Experiment, `experiment-examples`, named
  "Examples", holding those Studies in class order.

An example with no saved result yet (its outputs were never committed)
gets **no** run directory and appears in no Study. Home lists a Study's
members only when each has a readable `manifest.json` (`fim.gui.app.
_recent_run_at_directory`), and `groups.is_run_read_only` reads that
same manifest, so a directory without one would be invisible in the tree
yet editable and deletable on disk: the least surprising choice is to
seed nothing until there is something to show. The Examples dialog still
lists such an example, and can load it into Configure.

Seeding is idempotent: a second call with the same bundle writes nothing.
It never touches anything outside its own items: the `results/examples/`
directories it creates (a directory there holding any file seeding did
not write is left alone), the `study-examples-*` Studies, and
`experiment-examples`. The Study and Experiment manifests are written
through `groups.write_read_only_study`/`write_read_only_experiment`, the
one write path that may create read-only groupings.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from fim import paths
from fim.persistence import groups
from fim.persistence.run_metadata import (
    CURRENT_RUN_METADATA_SCHEMA_VERSION,
    RunMetadata,
    read_run_metadata,
    run_metadata_path,
    write_run_metadata,
)

logger = logging.getLogger(__name__)

CATALOG_FILE_NAME: Final = "catalog.json"
CONFIG_FILE_NAME: Final = "config.yaml"

EXAMPLES_DIRECTORY_NAME: Final = "examples"
EXAMPLES_EXPERIMENT_ID: Final = "experiment-examples"
EXAMPLES_EXPERIMENT_NAME: Final = "Examples"
EXAMPLES_EXPERIMENT_DESCRIPTION: Final = (
    "The worked examples shipped with fim, with their saved results. "
    "Read-only: load one into Configure to run an editable copy."
)
EXAMPLES_STUDY_PREFIX: Final = "study-examples-"

# The catalog's own class for an example without a `class` label
# (`dev/bin/build-examples-catalog`'s `UNCLASSIFIED_CLASS`). It is a
# display bucket, not a class from `doc/examples/classes.yaml`, so it is
# never written as a run's class label.
UNCLASSIFIED_CLASS_ID: Final = "unclassified"

# Separates a parent class's title from a child's in the child's Study name.
CHILD_STUDY_SEPARATOR: Final = " — "

# The only files seeding writes into an example's run directory, and so
# the only ones it may replace or remove: the top-level outputs and
# configuration, and each batch replicate's outputs one level down.
_OWNED_TOP_LEVEL_FILES: Final = frozenset(
    {"config.yaml", "manifest.json", "report.json", "summary.json", "metadata.json"}
)
_OWNED_REPLICATE_FILES: Final = frozenset({"manifest.json", "report.json"})

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    """Return the current UTC time, injectable for deterministic tests."""
    return datetime.now(UTC)


def _format_timestamp(value: datetime) -> str:
    """Return a UTC ISO-8601 timestamp, as `fim.persistence.run_metadata` does."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class BundledClass:
    """One class in the bundle's tree.

    Args:
        class_id: The class ID.
        title: Display title.
        description: One line about the class, or `None`.
        children: Child classes, in display order.
    """

    class_id: str
    title: str
    description: str | None = None
    children: tuple[BundledClass, ...] = ()


@dataclass(frozen=True, slots=True)
class BundledExample:
    """One example in the bundle.

    Args:
        example_id: The example's ID (its `doc/examples/` directory name).
        name: Display name.
        description: One-line description, or `None`.
        class_id: The catalog class, `UNCLASSIFIED_CLASS_ID` when unlabeled.
        config_yaml: The configuration text, or `None` for an example
            reproduced by a script instead.
        outputs: The saved output files, relative to the example's own
            bundle directory.
    """

    example_id: str
    name: str
    description: str | None
    class_id: str
    config_yaml: str | None
    outputs: tuple[str, ...] = ()

    @property
    def has_saved_result(self) -> bool:
        """Return whether the bundle carries this example's saved run."""
        return "manifest.json" in self.outputs


@dataclass(frozen=True, slots=True)
class ExamplesBundle:
    """A parsed examples bundle.

    Args:
        directory: The bundle directory (`webui/examples/`).
        classes: The class tree, in display order.
        examples: Every example, in display order.
    """

    directory: Path
    classes: tuple[BundledClass, ...]
    examples: tuple[BundledExample, ...]


@dataclass(frozen=True, slots=True)
class SeedReport:
    """What one `seed_examples` call found and did.

    Args:
        seeded: The examples that have a run directory, in display order.
        without_saved_result: The examples with no saved result yet,
            which have none.
        written: Every file this call created or replaced (run files,
            run metadata, and Study and Experiment manifests).
        removed: Every file or directory this call removed.
    """

    seeded: tuple[str, ...] = ()
    without_saved_result: tuple[str, ...] = ()
    written: tuple[Path, ...] = field(default=())
    removed: tuple[Path, ...] = field(default=())

    @property
    def changed(self) -> bool:
        """Return whether anything on disk changed."""
        return bool(self.written or self.removed)


def example_run_directory(example_id: str, *, results: Path | None = None) -> Path:
    """Return where a seeded example's run lives.

    Args:
        example_id: The example's ID.
        results: Optional results-directory override.

    Returns:
        `results / "examples" / example_id`.
    """
    root = results if results is not None else paths.results_directory()
    return root / EXAMPLES_DIRECTORY_NAME / example_id


def example_study_id(class_id: str) -> str:
    """Return the fixed id of the read-only Study for one class."""
    return f"{EXAMPLES_STUDY_PREFIX}{class_id}"


def read_bundle(bundle: Path) -> ExamplesBundle | None:
    """Read an examples bundle's `catalog.json`.

    Args:
        bundle: The bundle directory.

    Returns:
        The parsed bundle, or `None` when the directory holds no catalog
        (nothing to seed).

    Raises:
        OSError: The catalog exists but cannot be read.
        ValueError: The catalog is not valid JSON or lacks a required key.
    """
    catalog_path = bundle / CATALOG_FILE_NAME
    if not catalog_path.is_file():
        logger.debug("no examples catalog at %s", catalog_path)
        return None
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{catalog_path}: catalog root must be an object")
    try:
        classes = tuple(_bundled_class(raw) for raw in payload["classes"])
        examples = tuple(_bundled_example(raw) for raw in payload["examples"])
    except (KeyError, TypeError) as error:
        raise ValueError(f"{catalog_path}: malformed catalog: {error}") from error
    return ExamplesBundle(directory=bundle, classes=classes, examples=examples)


def seed_examples(
    results: Path | None = None, *, bundle: Path, clock: Clock = _utc_now
) -> SeedReport:
    """Make the results folder hold the bundle's examples as read-only items.

    See this module's docstring for what is written and why. Idempotent,
    and confined to its own items.

    Args:
        results: Optional results-directory override.
        bundle: The examples bundle directory (`webui/examples/`).
        clock: Injectable current-time source, for deterministic tests.

    Returns:
        What was seeded, skipped, written, and removed. A bundle with no
        catalog seeds nothing and changes nothing.

    Raises:
        OSError: A file cannot be read or written.
        ValueError: The catalog is malformed.
    """
    root = results if results is not None else paths.results_directory()
    parsed = read_bundle(bundle)
    if parsed is None:
        return SeedReport()

    written: list[Path] = []
    removed: list[Path] = []

    # Run directories, one per example with a saved result.
    seeded = [example for example in parsed.examples if example.has_saved_result]
    for example in seeded:
        written.extend(_seed_run_directory(parsed, example, root, clock))
    removed.extend(_remove_stale_run_directories({e.example_id for e in seeded}, root))

    # One read-only Study per class with seeded members, in class order.
    study_ids = []
    for study_id, name, description, members in _class_studies(parsed, seeded):
        path = groups.study_manifest_path(study_id, results=root)
        before = _read_bytes(path)
        groups.write_read_only_study(
            study_id,
            name=name,
            description=description,
            run_directories=[
                example_run_directory(member.example_id, results=root)
                for member in members
            ],
            results=root,
            clock=clock,
        )
        if _read_bytes(path) != before:
            written.append(path)
        study_ids.append(study_id)
    removed.extend(_remove_stale_studies(set(study_ids), root))

    # The Examples Experiment, holding those Studies.
    removed.extend(_write_experiment(study_ids, root, clock, written))

    return SeedReport(
        seeded=tuple(example.example_id for example in seeded),
        without_saved_result=tuple(
            example.example_id
            for example in parsed.examples
            if not example.has_saved_result
        ),
        written=tuple(written),
        removed=tuple(removed),
    )


def _bundled_class(raw: Mapping[str, Any]) -> BundledClass:
    """Build one class (and its children) from its catalog mapping."""
    description = raw.get("description") or None
    return BundledClass(
        class_id=str(raw["id"]),
        title=str(raw["title"]),
        description=str(description) if description is not None else None,
        children=tuple(_bundled_class(child) for child in raw.get("children") or []),
    )


def _bundled_example(raw: Mapping[str, Any]) -> BundledExample:
    """Build one example from its catalog mapping."""
    description = raw.get("description") or None
    config_yaml = raw.get("config_yaml")
    return BundledExample(
        example_id=str(raw["id"]),
        name=str(raw["name"]),
        description=str(description) if description is not None else None,
        class_id=str(raw["class"]),
        config_yaml=str(config_yaml) if config_yaml is not None else None,
        outputs=tuple(str(path) for path in raw.get("outputs") or []),
    )


def _class_studies(
    bundle: ExamplesBundle, seeded: Sequence[BundledExample]
) -> list[tuple[str, str, str | None, list[BundledExample]]]:
    """Return `(study_id, name, description, members)` per class with members.

    Classes are visited in display order, each parent before its
    children; a child's Study is named "Parent — Child".
    """
    studies = []
    for parent in bundle.classes:
        entries = [(parent, parent.title)] + [
            (child, f"{parent.title}{CHILD_STUDY_SEPARATOR}{child.title}")
            for child in parent.children
        ]
        for entry, name in entries:
            members = [e for e in seeded if e.class_id == entry.class_id]
            if members:
                studies.append(
                    (example_study_id(entry.class_id), name, entry.description, members)
                )
    return studies


def _is_owned(relative: Path) -> bool:
    """Return whether a path inside an example's run directory is seeding's own."""
    parts = relative.parts
    if len(parts) == 1:
        return parts[0] in _OWNED_TOP_LEVEL_FILES
    return len(parts) == 2 and parts[1] in _OWNED_REPLICATE_FILES  # noqa: PLR2004


def _read_bytes(path: Path) -> bytes | None:
    """Return a file's bytes, or `None` when it does not exist."""
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _remove_stale_run_directories(keep: set[str], results: Path) -> list[Path]:
    """Remove example run directories the bundle no longer seeds.

    Only a directory holding nothing but seeding's own files is removed;
    anything else in it (a file a user put there) keeps it, and it is
    logged rather than touched.
    """
    examples_root = results / EXAMPLES_DIRECTORY_NAME
    if not examples_root.is_dir():
        return []
    removed = []
    for directory in sorted(path for path in examples_root.iterdir() if path.is_dir()):
        if directory.name in keep:
            continue
        files = [path for path in directory.rglob("*") if path.is_file()]
        if all(_is_owned(path.relative_to(directory)) for path in files):
            shutil.rmtree(directory)
            removed.append(directory)
        else:
            logger.info("keeping %s: it holds files seeding did not write", directory)
    return removed


def _remove_stale_studies(keep: set[str], results: Path) -> list[Path]:
    """Remove read-only example Studies for classes that no longer have one."""
    directory = paths.studies_directory(results)
    if not directory.is_dir():
        return []
    removed = []
    for path in sorted(directory.glob(f"{EXAMPLES_STUDY_PREFIX}*.json")):
        if path.stem in keep:
            continue
        try:
            stale = groups.read_study_manifest(path)
        except (OSError, ValueError):
            continue
        if stale.read_only:
            path.unlink()
            removed.append(path)
    return removed


def _seed_run_directory(
    bundle: ExamplesBundle, example: BundledExample, results: Path, clock: Clock
) -> list[Path]:
    """Write one example's run directory, returning every file it changed.

    The configuration and the output files are written only when their
    bytes differ from the bundle's; an owned file the bundle no longer
    ships is removed (and returned too). The metadata is rewritten only
    when its name, description, or class differs.
    """
    directory = example_run_directory(example.example_id, results=results)
    wanted: dict[Path, bytes] = {}
    if example.config_yaml is not None:
        wanted[Path(CONFIG_FILE_NAME)] = example.config_yaml.encode("utf-8")
    source = bundle.directory / example.example_id
    for output in example.outputs:
        wanted[Path(output)] = (source / output).read_bytes()

    changed = []
    for wanted_path, content in wanted.items():
        target = directory / wanted_path
        if _read_bytes(target) != content:
            _write_bytes_atomically(target, content)
            changed.append(target)

    # An owned file the bundle no longer ships (a batch example that
    # became a single run, say) would otherwise contradict the rest.
    if directory.is_dir():
        for path in sorted(directory.rglob("*")):
            relative = path.relative_to(directory)
            if (
                path.is_file()
                and _is_owned(relative)
                and relative not in wanted
                and relative != Path(run_metadata_path(directory).name)
            ):
                path.unlink()
                changed.append(path)

    if _seed_metadata(directory, example, clock):
        changed.append(run_metadata_path(directory))
    return changed


def _seed_metadata(directory: Path, example: BundledExample, clock: Clock) -> bool:
    """Write the example's name, description, and class; return whether it changed.

    Written directly (`write_run_metadata`), not through
    `replace_run_metadata`, which refuses a read-only run: seeding is the
    one writer allowed to label one. An existing sidecar with the same
    labels is left as it is, so its timestamps do not move.
    """
    run_class = None if example.class_id == UNCLASSIFIED_CLASS_ID else example.class_id
    path = run_metadata_path(directory)
    prior: RunMetadata | None = None
    if path.is_file():
        try:
            prior = read_run_metadata(path)
        except (OSError, ValueError) as error:
            logger.debug("replacing unreadable example metadata %s: %s", path, error)
    if prior is not None and (prior.name, prior.description, prior.run_class) == (
        example.name,
        example.description,
        run_class,
    ):
        return False
    now = _format_timestamp(clock())
    write_run_metadata(
        path,
        RunMetadata(
            schema_version=CURRENT_RUN_METADATA_SCHEMA_VERSION,
            name=example.name,
            description=example.description,
            created_at=prior.created_at if prior is not None else now,
            updated_at=now,
            run_class=run_class,
        ),
    )
    return True


def _write_bytes_atomically(path: Path, content: bytes) -> None:
    """Write `content` to `path` so a reader sees the old file or the new one.

    The byte-for-byte counterpart of `fim.paths.write_text_atomically`:
    seeding compares bytes with the bundle, so a text-mode write that
    translated line endings would make every later call rewrite the file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".example-")
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as temp_file:
            temp_file.write(content)
        temp_path.chmod(paths.default_file_mode())
        paths.replace_with_retry(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _write_experiment(
    study_ids: Sequence[str], results: Path, clock: Clock, written: list[Path]
) -> list[Path]:
    """Write the Examples Experiment, or remove it when there is nothing in it.

    Appends the manifest's path to `written` when it changed; returns
    the paths removed (the manifest itself, when no Study is left).
    """
    path = groups.experiment_manifest_path(EXAMPLES_EXPERIMENT_ID, results=results)
    if not study_ids:
        try:
            stale = groups.read_experiment_manifest(path)
        except (OSError, ValueError):
            return []
        if stale.read_only:
            path.unlink()
            return [path]
        return []
    before = _read_bytes(path)
    groups.write_read_only_experiment(
        EXAMPLES_EXPERIMENT_ID,
        name=EXAMPLES_EXPERIMENT_NAME,
        description=EXAMPLES_EXPERIMENT_DESCRIPTION,
        study_ids=study_ids,
        results=results,
        clock=clock,
    )
    if _read_bytes(path) != before:
        written.append(path)
    return []
