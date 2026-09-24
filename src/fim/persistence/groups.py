"""Study and Experiment: named groupings of Runs, and of Studies.

Full design: `20260917-claude-sonnet-5-run-study-experiment-hierarchy-
design.md` (`selby/restricted`). Three layers exist after this module:
a **Run** (unchanged — one scalar run or replicate batch, `fim.
persistence.manifest`), a **Study** (a named list of Run directories
pursuing one research question), and an **Experiment** (a named list of
Study ids pursuing a longer-running research goal). A Study/Experiment
never duplicates or moves anything it references — `StudyManifest.
run_directories` names existing run directories exactly the way
`fim.persistence.manifest.BatchManifest.replicate_run_ids` already
names existing replicate directories one level down, and
`ExperimentManifest.study_ids` does the same one level further up.

`StudyManifest`/`ExperimentManifest` are deliberately two separate,
structurally parallel dataclasses (a "twin," like `fim.persistence.
manifest`'s own `RunManifest`/`BatchManifest`) rather than one generic
type parameterized over the member field name — this project's own
established convention prefers a small explicit copy over a shared
generic (`fim.persistence.manifest.read_manifest`/`read_batch_manifest`
are byte-identical copy-paste-adapts of each other for the same reason).

Deleting a Study deletes every Run it references; deleting an
Experiment deletes every Study it references (and, transitively, every
Run those Studies reference) — a deliberate, explicit product decision
overriding this design document's own original, more conservative
leaning ("deleting an organizational grouping should never be a
data-destroying operation"), confirmed directly by the project owner
when this module was implemented. `delete_study`/`delete_experiment`
both accept an escape hatch (`delete_runs=False`/`delete_studies=False`)
for a caller that genuinely only wants the grouping gone.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from fim import paths
from fim.persistence.manifest import read_batch_manifest, read_manifest

logger = logging.getLogger(__name__)

# Bumped whenever StudyManifest's on-disk shape changes incompatibly —
# tracked independently of every other schema-version constant in this
# project, since a Study is its own distinct document type.
CURRENT_STUDY_SCHEMA_VERSION = 1

# Bumped whenever ExperimentManifest's on-disk shape changes incompatibly.
CURRENT_EXPERIMENT_SCHEMA_VERSION = 1

_STUDY_ID_PREFIX = "study-"
_EXPERIMENT_ID_PREFIX = "experiment-"
_ID_RANDOM_HEX_DIGITS = 8

# The always-present default Study/Experiment (`20260918-claude-
# sonnet-5-home-tree-reorg-design.md`, `selby/restricted`, §1) --
# fixed, well-known ids rather than `generate_study_id`/`generate_
# experiment_id`'s own random ones, so code resolving "no Study/
# Experiment chosen" never needs a name-matching heuristic, and a
# botanist renaming either never breaks that resolution. `"default"`
# is not valid hex, so these can never collide with a randomly
# generated id sharing the same prefix.
DEFAULT_STUDY_ID: Final = "study-default"
DEFAULT_EXPERIMENT_ID: Final = "experiment-default"
DEFAULT_STUDY_NAME: Final = "Default study"
DEFAULT_EXPERIMENT_NAME: Final = "Default experiment"

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    """Return the current UTC time, injectable for deterministic tests."""
    return datetime.now(UTC)


def _format_timestamp(value: datetime) -> str:
    """Return an unambiguous UTC ISO-8601 timestamp, matching `RunManifest`.

    A separate, tiny copy of `fim.cli._format_timestamp`'s own logic —
    that function's own docstring explains why every timestamp in this
    project is a small local copy rather than a shared import.
    """
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def generate_study_id() -> str:
    """Return a short, opaque, randomly generated Study id.

    Unlike `fim.engine.deterministic_run_id`, this is not content-
    derived: a Study has no fixed "configuration" to hash, only a
    membership list expected to grow over its own lifetime
    (`StudyManifest`'s own docstring).
    """
    return f"{_STUDY_ID_PREFIX}{secrets.token_hex(_ID_RANDOM_HEX_DIGITS // 2)}"


def generate_experiment_id() -> str:
    """Return a short, opaque, randomly generated Experiment id."""
    return f"{_EXPERIMENT_ID_PREFIX}{secrets.token_hex(_ID_RANDOM_HEX_DIGITS // 2)}"


def _generate_unique_id(
    generator: Callable[[], str], exists: Callable[[str], bool]
) -> str:
    """Retry `generator` until it returns an id `exists` says is free.

    The same "bounded collision-retry" shape `fim.paths.
    default_output_directory` already uses for its own timestamped
    directory names — astronomically unlikely to loop even once at
    `_ID_RANDOM_HEX_DIGITS` of randomness, but never assumed uncondit-
    ionally collision-free.
    """
    for _ in range(1000):
        candidate = generator()
        if not exists(candidate):
            return candidate
    raise RuntimeError("could not generate a unique id after 1000 attempts")


@dataclass(frozen=True, slots=True)
class StudyManifest:
    """A named, described set of Run directories pursuing one research question.

    `run_directories` entries are either a bare run-directory name
    (the common case: a run living directly under `results/`) or a
    path — relative to `results/` if the run is nested further inside
    it, otherwise absolute — for a run published somewhere else
    entirely (`fim run -o SOME/OTHER/PATH`). A directory listed here
    that no longer exists (deleted results) is simply skipped by any
    reader that resolves it, never fatal (`fim.gui.app._read_json_
    object`'s own "one missing thing does not hide everything else"
    precedent).

    `sweep_spec` is `None` for a manually assembled Study; a future
    `SweepSpec`-shaped tool may populate it purely for provenance —
    never required for reading a Study back, since `run_directories`
    alone is sufficient to show its contents (design doc §3.2/§8).
    """

    schema_version: int
    study_id: str
    name: str
    description: str | None
    created_at: str
    updated_at: str
    run_directories: tuple[str, ...]
    sweep_spec: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        """Validate schema version, identity, name, and timestamps."""
        if self.schema_version < 1:
            raise ValueError("study manifest schema_version must be at least 1")
        if not self.study_id:
            raise ValueError("study manifest study_id must not be empty")
        if not self.name.strip():
            raise ValueError("study manifest name must not be blank")
        if self.description is not None and not self.description.strip():
            raise ValueError("study manifest description must not be blank")
        if not self.created_at or not self.updated_at:
            raise ValueError("study manifest timestamps must not be empty")

    @property
    def run_count(self) -> int:
        """Return how many run directories this Study currently references."""
        return len(self.run_directories)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable study manifest mapping."""
        return {
            "schema_version": self.schema_version,
            "study_id": self.study_id,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "run_directories": list(self.run_directories),
            "run_count": self.run_count,
            "sweep_spec": (
                dict(self.sweep_spec) if self.sweep_spec is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StudyManifest:
        """Validate and reconstruct a study manifest from a parsed JSON mapping."""
        required = {
            "schema_version",
            "study_id",
            "name",
            "created_at",
            "updated_at",
            "run_directories",
        }
        missing = required - set(value)
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"study manifest is missing: {names}")
        sweep_spec = value.get("sweep_spec")
        if sweep_spec is not None and not isinstance(sweep_spec, Mapping):
            raise ValueError(
                "study manifest field 'sweep_spec' must be an object or null"
            )
        return cls(
            schema_version=_required_int(
                value, "schema_version", label="study manifest"
            ),
            study_id=_required_string(value, "study_id", label="study manifest"),
            name=_required_string(value, "name", label="study manifest"),
            description=_optional_string(value, "description", label="study manifest"),
            created_at=_required_string(value, "created_at", label="study manifest"),
            updated_at=_required_string(value, "updated_at", label="study manifest"),
            run_directories=_string_tuple(
                value, "run_directories", label="study manifest"
            ),
            sweep_spec=dict(sweep_spec) if sweep_spec is not None else None,
        )


@dataclass(frozen=True, slots=True)
class ExperimentManifest:
    """A named, described set of Study ids pursuing a common research goal.

    Structurally identical in shape to `StudyManifest`, one level up
    (`study_ids` in place of `run_directories`, no `sweep_spec`) — an
    Experiment is otherwise a thin container with no results of its own
    beyond what its Studies already show (design doc §1.3).
    """

    schema_version: int
    experiment_id: str
    name: str
    description: str | None
    created_at: str
    updated_at: str
    study_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        """Validate schema version, identity, name, and timestamps."""
        if self.schema_version < 1:
            raise ValueError("experiment manifest schema_version must be at least 1")
        if not self.experiment_id:
            raise ValueError("experiment manifest experiment_id must not be empty")
        if not self.name.strip():
            raise ValueError("experiment manifest name must not be blank")
        if self.description is not None and not self.description.strip():
            raise ValueError("experiment manifest description must not be blank")
        if not self.created_at or not self.updated_at:
            raise ValueError("experiment manifest timestamps must not be empty")

    @property
    def study_count(self) -> int:
        """Return how many studies this Experiment currently references."""
        return len(self.study_ids)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable experiment manifest mapping."""
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "study_ids": list(self.study_ids),
            "study_count": self.study_count,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ExperimentManifest:
        """Validate and reconstruct an experiment manifest from a parsed mapping."""
        required = {
            "schema_version",
            "experiment_id",
            "name",
            "created_at",
            "updated_at",
            "study_ids",
        }
        missing = required - set(value)
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"experiment manifest is missing: {names}")
        return cls(
            schema_version=_required_int(
                value, "schema_version", label="experiment manifest"
            ),
            experiment_id=_required_string(
                value, "experiment_id", label="experiment manifest"
            ),
            name=_required_string(value, "name", label="experiment manifest"),
            description=_optional_string(
                value, "description", label="experiment manifest"
            ),
            created_at=_required_string(
                value, "created_at", label="experiment manifest"
            ),
            updated_at=_required_string(
                value, "updated_at", label="experiment manifest"
            ),
            study_ids=_string_tuple(value, "study_ids", label="experiment manifest"),
        )


def study_manifest_path(study_id: str, *, results: Path | None = None) -> Path:
    """Return where one Study's own manifest file lives.

    Args:
        study_id: The Study's own id.
        results: Optional results-directory override (default:
            `fim.paths.results_directory()`).

    Returns:
        `fim.paths.studies_directory(results) / f"{study_id}.json"`.
    """
    root = results if results is not None else paths.results_directory()
    return paths.studies_directory(root) / f"{study_id}.json"


def experiment_manifest_path(
    experiment_id: str, *, results: Path | None = None
) -> Path:
    """Return where one Experiment's own manifest file lives."""
    root = results if results is not None else paths.results_directory()
    return paths.experiments_directory(root) / f"{experiment_id}.json"


def read_study_manifest(path: Path | str) -> StudyManifest:
    """Read and validate one Study manifest JSON file."""
    manifest_path = Path(path)
    with manifest_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("study manifest root must be an object")
    return StudyManifest.from_dict(payload)


def write_study_manifest(path: Path | str, manifest: StudyManifest) -> None:
    """Atomically write `manifest` to `path`, creating parent directories as needed.

    Same mkstemp-then-`os.replace` idiom as `fim.gui.preferences.
    save_preferences` — a Study's own index file is a standalone
    document beside already-published, otherwise-untouched run
    directories, not something built inside a `fim.paths.
    atomic_directory` block the way `manifest.json` itself is, so it
    needs its own atomicity here.
    """
    _write_json_atomically(Path(path), manifest.to_dict(), prefix=".study-")
    logger.debug("wrote study manifest: %s", path)


def read_experiment_manifest(path: Path | str) -> ExperimentManifest:
    """Read and validate one Experiment manifest JSON file."""
    manifest_path = Path(path)
    with manifest_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("experiment manifest root must be an object")
    return ExperimentManifest.from_dict(payload)


def write_experiment_manifest(path: Path | str, manifest: ExperimentManifest) -> None:
    """Atomically write `manifest` to `path`, creating parent directories as needed."""
    _write_json_atomically(Path(path), manifest.to_dict(), prefix=".experiment-")
    logger.debug("wrote experiment manifest: %s", path)


def _write_json_atomically(path: Path, payload: object, *, prefix: str) -> None:
    """Shared mkstemp-then-replace body for the two write functions above."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True)
    descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=prefix)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temp_file:
            temp_file.write(text)
        paths.replace_with_retry(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def create_study(
    name: str,
    description: str | None = None,
    *,
    results: Path | None = None,
    clock: Clock = _utc_now,
    sweep_spec: Mapping[str, object] | None = None,
) -> StudyManifest:
    """Create a new, empty Study and write its manifest.

    Args:
        name: Short human name; must not be blank.
        description: Optional longer description.
        results: Optional results-directory override.
        clock: Injectable current-time source, for deterministic tests.
        sweep_spec: The stored sweep specification and plan (`fim.
            sweep_run.create_sweep_study`), or `None` for a Study
            assembled by hand.

    Returns:
        The newly created, empty Study.
    """
    root = results if results is not None else paths.results_directory()
    stripped_name = name.strip()
    if not stripped_name:
        raise ValueError("study name must not be blank")
    now = _format_timestamp(clock())
    study_id = _generate_unique_id(
        generate_study_id,
        lambda candidate: study_manifest_path(candidate, results=root).exists(),
    )
    manifest = StudyManifest(
        schema_version=CURRENT_STUDY_SCHEMA_VERSION,
        study_id=study_id,
        name=stripped_name,
        description=description,
        created_at=now,
        updated_at=now,
        run_directories=(),
        sweep_spec=dict(sweep_spec) if sweep_spec is not None else None,
    )
    write_study_manifest(study_manifest_path(study_id, results=root), manifest)
    return manifest


def get_study(study_id: str, *, results: Path | None = None) -> StudyManifest:
    """Read one existing Study by id.

    Raises:
        ValueError: No Study with this id exists.
    """
    root = results if results is not None else paths.results_directory()
    path = study_manifest_path(study_id, results=root)
    if not path.is_file():
        raise ValueError(f"no such study: {study_id}")
    return read_study_manifest(path)


def study_run_directories(
    study: StudyManifest, *, results: Path | None = None
) -> list[Path]:
    """Resolve every Run directory `study` references that still exists.

    A directory listed in `study.run_directories` that no longer exists
    (deleted out of band) is silently skipped, never fatal — the same
    "one missing thing does not hide everything else" precedent this
    module's own docstring describes for `run_directories` itself. The
    GUI's `Api.list_studies`/`Api.get_study_run_summary` (`fim.gui.app`)
    are this function's own two callers.
    """
    root = results if results is not None else paths.results_directory()
    resolved = (
        _resolve_stored_run_reference(entry, results=root)
        for entry in study.run_directories
    )
    return [directory for directory in resolved if directory.is_dir()]


def list_studies(*, results: Path | None = None) -> list[StudyManifest]:
    """Return every Study under `results`, oldest first.

    Oldest-first (not newest-first, unlike `fim.gui.recent_runs.
    list_recent_runs`) so a per-parent display ordinal computed from
    this order ("Study 1," "Study 2," ...) stays stable as new Studies
    are added (design doc §4).

    A manifest file that fails to parse is skipped, logged, never fatal
    to the rest of the listing.
    """
    root = results if results is not None else paths.results_directory()
    directory = paths.studies_directory(root)
    if not directory.is_dir():
        return []
    found: list[StudyManifest] = []
    for manifest_path in sorted(directory.glob("*.json")):
        try:
            found.append(read_study_manifest(manifest_path))
        except (OSError, ValueError) as error:
            logger.debug(
                "skipping unreadable study manifest %s: %s", manifest_path, error
            )
    found.sort(key=lambda study: study.created_at)
    return found


def add_run_to_study(
    study_id: str,
    run_directory: Path | str,
    *,
    results: Path | None = None,
    clock: Clock = _utc_now,
) -> StudyManifest:
    """Add one Run directory to an existing Study; idempotent.

    Adding a directory already present is a no-op that returns the
    Study unchanged (design doc §5) — `updated_at` only moves forward
    on a genuine membership change.

    Raises:
        ValueError: No Study with this id exists.
    """
    root = results if results is not None else paths.results_directory()
    manifest = get_study(study_id, results=root)
    reference = _run_reference_string(Path(run_directory), results=root)
    if reference in manifest.run_directories:
        return manifest
    updated = replace(
        manifest,
        run_directories=(*manifest.run_directories, reference),
        updated_at=_format_timestamp(clock()),
    )
    write_study_manifest(study_manifest_path(study_id, results=root), updated)
    return updated


def delete_study(
    study_id: str,
    *,
    results: Path | None = None,
    delete_runs: bool = True,
) -> StudyManifest:
    """Delete a Study's own manifest and, by default, every Run it references.

    Args:
        study_id: The Study to delete.
        results: Optional results-directory override.
        delete_runs: When true (the default — a deliberate, explicit
            product decision; see this module's own docstring), every
            referenced Run directory is removed too. When false, only
            the `StudyManifest` itself is removed and its Runs become
            unattached again ("Unsorted").

    Returns:
        The manifest as it existed immediately before deletion.

    Raises:
        ValueError: No Study with this id exists.
    """
    root = results if results is not None else paths.results_directory()
    manifest = get_study(study_id, results=root)
    if delete_runs:
        _delete_unshared_runs(manifest, results=root)
    study_manifest_path(study_id, results=root).unlink(missing_ok=True)
    # An Experiment must never keep naming a Study that no longer exists:
    # its own count and its expanded list would both claim a Study that
    # is not there.
    _detach_study_from_experiments(study_id, results=root)
    return manifest


def clear_study_runs(
    study_id: str,
    *,
    results: Path | None = None,
    clock: Clock = _utc_now,
) -> StudyManifest:
    """Delete every Run a Study references and keep the (now empty) Study.

    The counterpart to `delete_study` for emptying a Study without
    removing the Study itself: selecting a Study row for deletion removes
    the Study too, and there was no other way to remove all of its Runs
    at once. A directory already gone is tolerated, as in `delete_study`.

    Returns:
        The Study as it was before its Runs were removed (so a caller can
        report how many there were).

    Raises:
        ValueError: No Study with this id exists.
    """
    root = results if results is not None else paths.results_directory()
    manifest = get_study(study_id, results=root)
    _delete_unshared_runs(manifest, results=root)
    write_study_manifest(
        study_manifest_path(study_id, results=root),
        replace(manifest, run_directories=(), updated_at=_format_timestamp(clock())),
    )
    return manifest


def shared_run_directories(study_id: str, *, results: Path | None = None) -> list[Path]:
    """Return the Study's Runs that another Study also references.

    A Run is a link from a Study to a directory (a computed configuration),
    and one Run may be linked from several Studies. Deleting a Study, or
    emptying it, removes its links and deletes only the Runs nothing else
    links to, so it can never delete a Run out from under another Study.
    """
    root = results if results is not None else paths.results_directory()
    manifest = get_study(study_id, results=root)
    others = _directories_referenced_by_others(study_id, results=root)
    return [
        directory
        for entry in manifest.run_directories
        if (directory := _resolve_stored_run_reference(entry, results=root)).resolve()
        in others
    ]


def remove_run_references(
    directories: Sequence[Path | str], *, results: Path | None = None
) -> None:
    """Drop every link, in every Study, to the given (deleted) Run directories.

    Called after a Run is deleted itself, so no Study keeps counting a Run
    that is gone.
    """
    root = results if results is not None else paths.results_directory()
    gone = {Path(directory).resolve() for directory in directories}
    for study in list_studies(results=root):
        kept = tuple(
            entry
            for entry in study.run_directories
            if _resolve_stored_run_reference(entry, results=root).resolve() not in gone
        )
        if kept != study.run_directories:
            write_study_manifest(
                study_manifest_path(study.study_id, results=root),
                replace(study, run_directories=kept),
            )


def _directories_referenced_by_others(study_id: str, *, results: Path) -> set[Path]:
    """Return the resolved Run directories any Study other than `study_id` links."""
    return {
        _resolve_stored_run_reference(entry, results=results).resolve()
        for other in list_studies(results=results)
        if other.study_id != study_id
        for entry in other.run_directories
    }


def _delete_unshared_runs(manifest: StudyManifest, *, results: Path) -> None:
    """Delete the Study's Run directories that no other Study links."""
    others = _directories_referenced_by_others(manifest.study_id, results=results)
    for entry in manifest.run_directories:
        directory = _resolve_stored_run_reference(entry, results=results)
        if directory.resolve() not in others:
            shutil.rmtree(directory, ignore_errors=True)


def prune_missing_studies(*, results: Path | None = None) -> int:
    """Remove from every Experiment any Study id that has no manifest.

    Repairs the state an older `delete_study` left behind (it removed the
    Study but not its listing in an Experiment), so an Experiment claimed
    Studies it could not show.

    Returns:
        How many dangling Study ids were removed across all Experiments.
    """
    root = results if results is not None else paths.results_directory()
    removed = 0
    for experiment in list_experiments(results=root):
        kept = tuple(
            study_id
            for study_id in experiment.study_ids
            if study_manifest_path(study_id, results=root).is_file()
        )
        if kept != experiment.study_ids:
            removed += len(experiment.study_ids) - len(kept)
            write_experiment_manifest(
                experiment_manifest_path(experiment.experiment_id, results=root),
                replace(experiment, study_ids=kept),
            )
    return removed


def _detach_study_from_experiments(
    study_id: str, *, results: Path, clock: Clock = _utc_now
) -> None:
    """Drop `study_id` from every Experiment that lists it."""
    for experiment in list_experiments(results=results):
        if study_id in experiment.study_ids:
            write_experiment_manifest(
                experiment_manifest_path(experiment.experiment_id, results=results),
                replace(
                    experiment,
                    study_ids=tuple(
                        entry for entry in experiment.study_ids if entry != study_id
                    ),
                    updated_at=_format_timestamp(clock()),
                ),
            )


def copy_study(
    study_id: str,
    *,
    name: str,
    results: Path | None = None,
    clock: Clock = _utc_now,
) -> StudyManifest:
    """Copy a Study's own run list into a new, independent Study.

    The lower-complexity alternative to letting one Run belong to more
    than one Study at once (design doc §10, resolved in favor of this):
    since membership is reference-based (`run_directories` names, never
    duplicates, a run's own directory), copying a Study's member list
    costs nothing and creates two genuinely independent groupings a
    botanist can then diverge — add different runs to each — without
    either affecting the other or the underlying Run data at all.

    Raises:
        ValueError: No Study with this id exists, or `name` is blank.
    """
    root = results if results is not None else paths.results_directory()
    source = get_study(study_id, results=root)
    stripped_name = name.strip()
    if not stripped_name:
        raise ValueError("study name must not be blank")
    now = _format_timestamp(clock())
    new_id = _generate_unique_id(
        generate_study_id,
        lambda candidate: study_manifest_path(candidate, results=root).exists(),
    )
    copied = StudyManifest(
        schema_version=CURRENT_STUDY_SCHEMA_VERSION,
        study_id=new_id,
        name=stripped_name,
        description=source.description,
        created_at=now,
        updated_at=now,
        run_directories=source.run_directories,
        sweep_spec=source.sweep_spec,
    )
    write_study_manifest(study_manifest_path(new_id, results=root), copied)
    return copied


def create_experiment(
    name: str,
    description: str | None = None,
    *,
    results: Path | None = None,
    clock: Clock = _utc_now,
) -> ExperimentManifest:
    """Create a new, empty Experiment and write its manifest."""
    root = results if results is not None else paths.results_directory()
    stripped_name = name.strip()
    if not stripped_name:
        raise ValueError("experiment name must not be blank")
    now = _format_timestamp(clock())
    experiment_id = _generate_unique_id(
        generate_experiment_id,
        lambda candidate: experiment_manifest_path(candidate, results=root).exists(),
    )
    manifest = ExperimentManifest(
        schema_version=CURRENT_EXPERIMENT_SCHEMA_VERSION,
        experiment_id=experiment_id,
        name=stripped_name,
        description=description,
        created_at=now,
        updated_at=now,
        study_ids=(),
    )
    write_experiment_manifest(
        experiment_manifest_path(experiment_id, results=root), manifest
    )
    return manifest


def get_experiment(
    experiment_id: str, *, results: Path | None = None
) -> ExperimentManifest:
    """Read one existing Experiment by id.

    Raises:
        ValueError: No Experiment with this id exists.
    """
    root = results if results is not None else paths.results_directory()
    path = experiment_manifest_path(experiment_id, results=root)
    if not path.is_file():
        raise ValueError(f"no such experiment: {experiment_id}")
    return read_experiment_manifest(path)


def list_experiments(*, results: Path | None = None) -> list[ExperimentManifest]:
    """Return every Experiment under `results`, oldest first (see `list_studies`)."""
    root = results if results is not None else paths.results_directory()
    directory = paths.experiments_directory(root)
    if not directory.is_dir():
        return []
    found: list[ExperimentManifest] = []
    for manifest_path in sorted(directory.glob("*.json")):
        try:
            found.append(read_experiment_manifest(manifest_path))
        except (OSError, ValueError) as error:
            logger.debug(
                "skipping unreadable experiment manifest %s: %s", manifest_path, error
            )
    found.sort(key=lambda experiment: experiment.created_at)
    return found


def add_study_to_experiment(
    experiment_id: str,
    study_id: str,
    *,
    results: Path | None = None,
    clock: Clock = _utc_now,
) -> ExperimentManifest:
    """Add one Study to an existing Experiment; idempotent.

    Raises:
        ValueError: No Experiment or Study with the given id exists.
    """
    root = results if results is not None else paths.results_directory()
    get_study(study_id, results=root)  # raises ValueError if unknown
    manifest = get_experiment(experiment_id, results=root)
    if study_id in manifest.study_ids:
        return manifest
    updated = replace(
        manifest,
        study_ids=(*manifest.study_ids, study_id),
        updated_at=_format_timestamp(clock()),
    )
    write_experiment_manifest(
        experiment_manifest_path(experiment_id, results=root), updated
    )
    return updated


def ensure_default_experiment(
    *, results: Path | None = None, clock: Clock = _utc_now
) -> ExperimentManifest:
    """Return the always-present default Experiment, creating it on first use.

    `20260918-claude-sonnet-5-home-tree-reorg-design.md` (`selby/
    restricted`), §1: called lazily, only from the one place that
    actually needs to resolve "no Experiment chosen" into a real one
    (`ensure_default_study`, below) — never eagerly at app launch, so a
    checkout that never runs anything never gains an empty manifest
    file it did not ask for.

    Returns:
        The existing default Experiment, unchanged, if one is already
        on disk; otherwise a newly created, empty one at `DEFAULT_
        EXPERIMENT_ID`.
    """
    root = results if results is not None else paths.results_directory()
    try:
        return get_experiment(DEFAULT_EXPERIMENT_ID, results=root)
    except ValueError:
        pass
    now = _format_timestamp(clock())
    manifest = ExperimentManifest(
        schema_version=CURRENT_EXPERIMENT_SCHEMA_VERSION,
        experiment_id=DEFAULT_EXPERIMENT_ID,
        name=DEFAULT_EXPERIMENT_NAME,
        description=None,
        created_at=now,
        updated_at=now,
        study_ids=(),
    )
    write_experiment_manifest(
        experiment_manifest_path(DEFAULT_EXPERIMENT_ID, results=root), manifest
    )
    return manifest


def ensure_default_study(
    *, results: Path | None = None, clock: Clock = _utc_now
) -> StudyManifest:
    """Return the always-present default Study, creating it on first use.

    `20260918-claude-sonnet-5-home-tree-reorg-design.md` (`selby/
    restricted`), §1/§2 — the destination `Api.start_run`'s own
    `study_id=None` resolves to (§2 of that same document), so that no
    run is ever left without a Study from the moment it publishes.
    Always ensures the default Experiment exists and references this
    Study too (`ensure_default_experiment`, `add_study_to_experiment`
    — both idempotent, so this costs nothing extra once either already
    holds), not only on this Study's own first creation: a Study whose
    manifest survived some earlier partial failure but whose own link
    to the default Experiment did not is repaired the next time
    anything asks for it, rather than staying silently orphaned.

    Returns:
        The existing default Study, unchanged, if one is already on
        disk; otherwise a newly created, empty one at `DEFAULT_STUDY_
        ID`, nested inside the default Experiment either way.
    """
    root = results if results is not None else paths.results_directory()
    ensure_default_experiment(results=root, clock=clock)
    try:
        manifest = get_study(DEFAULT_STUDY_ID, results=root)
    except ValueError:
        now = _format_timestamp(clock())
        manifest = StudyManifest(
            schema_version=CURRENT_STUDY_SCHEMA_VERSION,
            study_id=DEFAULT_STUDY_ID,
            name=DEFAULT_STUDY_NAME,
            description=None,
            created_at=now,
            updated_at=now,
            run_directories=(),
            sweep_spec=None,
        )
        write_study_manifest(
            study_manifest_path(DEFAULT_STUDY_ID, results=root), manifest
        )
    add_study_to_experiment(
        DEFAULT_EXPERIMENT_ID, DEFAULT_STUDY_ID, results=root, clock=clock
    )
    return manifest


def delete_experiment(
    experiment_id: str,
    *,
    results: Path | None = None,
    delete_studies: bool = True,
) -> ExperimentManifest:
    """Delete an Experiment's own manifest and, by default, every Study it references.

    Cascades transitively: deleting a member Study (`delete_study`,
    `delete_runs=True`) also deletes every Run that Study references —
    the same explicit product decision `delete_study`'s own docstring
    describes, one level up.

    Args:
        experiment_id: The Experiment to delete.
        results: Optional results-directory override.
        delete_studies: When true (the default), every referenced Study
            (and, transitively, every Run it references) is removed
            too. When false, only the `ExperimentManifest` itself is
            removed and its Studies become unattached again.

    Returns:
        The manifest as it existed immediately before deletion.

    Raises:
        ValueError: No Experiment with this id exists.
    """
    root = results if results is not None else paths.results_directory()
    manifest = get_experiment(experiment_id, results=root)
    if delete_studies:
        for study_id in manifest.study_ids:
            try:
                delete_study(study_id, results=root, delete_runs=True)
            except ValueError as error:
                logger.debug(
                    "study %s already gone while deleting experiment %s: %s",
                    study_id,
                    experiment_id,
                    error,
                )
    experiment_manifest_path(experiment_id, results=root).unlink(missing_ok=True)
    return manifest


def copy_experiment(
    experiment_id: str,
    *,
    name: str,
    results: Path | None = None,
    clock: Clock = _utc_now,
) -> ExperimentManifest:
    """Copy an Experiment's own study list into a new, independent Experiment.

    The Experiment-level counterpart to `copy_study`, above — copies
    only the `study_ids` reference list, never the Studies themselves.

    Raises:
        ValueError: No Experiment with this id exists, or `name` is blank.
    """
    root = results if results is not None else paths.results_directory()
    source = get_experiment(experiment_id, results=root)
    stripped_name = name.strip()
    if not stripped_name:
        raise ValueError("experiment name must not be blank")
    now = _format_timestamp(clock())
    new_id = _generate_unique_id(
        generate_experiment_id,
        lambda candidate: experiment_manifest_path(candidate, results=root).exists(),
    )
    copied = ExperimentManifest(
        schema_version=CURRENT_EXPERIMENT_SCHEMA_VERSION,
        experiment_id=new_id,
        name=stripped_name,
        description=source.description,
        created_at=now,
        updated_at=now,
        study_ids=source.study_ids,
    )
    write_experiment_manifest(experiment_manifest_path(new_id, results=root), copied)
    return copied


def resolve_run_directory(reference: str, *, results: Path | None = None) -> Path:
    """Resolve a botanist-supplied Run reference to exactly one directory.

    Accepts, tried in this order (design doc §10, resolved): a Run
    **directory** (as an existing path, or a bare name relative to
    `results`), a **path to a `manifest.json`** file, or a bare
    **`run_id`** — searched across every run under `results`, since a
    `run_id` is a reproducibility fingerprint of a configuration, not a
    directory-unique key (`fim.engine.deterministic_run_id`'s own
    docstring: running the identical configuration twice yields the
    same `run_id` both times). A `run_id` search is only attempted once
    the first two forms have failed, since a directory/manifest path is
    always unambiguous when it resolves at all.

    Args:
        reference: What the botanist typed.
        results: Optional results-directory override.

    Returns:
        The one, resolved, absolute Run directory.

    Raises:
        ValueError: `reference` does not uniquely identify a Run —
            either nothing matches, or (for a bare `run_id`) more than
            one directory shares it.
    """
    root = results if results is not None else paths.results_directory()
    candidate = Path(reference)
    if candidate.name == "manifest.json" and candidate.is_file():
        return candidate.parent.resolve()
    if candidate.is_dir() and (candidate / "manifest.json").is_file():
        return candidate.resolve()
    by_name = root / reference
    if by_name.is_dir() and (by_name / "manifest.json").is_file():
        return by_name.resolve()
    matches = _run_directories_with_id(reference, root)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        listed = ", ".join(str(path) for path in matches)
        raise ValueError(
            f"run_id {reference!r} matches more than one run directory "
            f"({listed}); specify a run directory instead"
        )
    raise ValueError(
        f"could not identify a run from {reference!r}: it is not an "
        "existing run directory, not a path to a manifest.json, and no "
        f"run under {root} has this run_id"
    )


def find_run_directories(
    run_id: str,
    *,
    software_version: str | None = None,
    results: Path | None = None,
) -> list[Path]:
    """Return every run directory directly under `results` with this `run_id`.

    The lookup that recognizes a configuration already computed: a run's id
    is a hash of its whole configuration, seed included. `software_version`,
    when given, also has to match the version recorded in the run's
    manifest, because the guarantee that the same configuration gives the
    same result holds within one software version: a run made by another
    version is not reused (`find_stale_run_directories` finds it). Reads
    manifests, so it is linear in the number of runs.
    """
    root = results if results is not None else paths.results_directory()
    return [
        directory
        for directory, identity in _run_identities(root)
        if identity[0] == run_id
        and (software_version is None or identity[1] == software_version)
    ]


def find_stale_run_directories(
    run_id: str, software_version: str, *, results: Path | None = None
) -> list[Path]:
    """Return runs with this `run_id` that another software version made."""
    root = results if results is not None else paths.results_directory()
    return [
        directory
        for directory, identity in _run_identities(root)
        if identity[0] == run_id and identity[1] != software_version
    ]


def run_id_of(run_directory: Path) -> str | None:
    """Return the `run_id` recorded in `run_directory`'s manifest, if readable."""
    identity = run_identity_of(run_directory)
    return identity[0] if identity is not None else None


def run_identity_of(run_directory: Path) -> tuple[str, str] | None:
    """Return `(run_id, software_version)` from a run's manifest, if readable."""
    return _manifest_identity(run_directory / "manifest.json")


def supersede_run(
    old: Path | str, new: Path | str, *, results: Path | None = None
) -> None:
    """Replace every link to `old` with a link to `new`, then delete `old`.

    For a run recomputed under a newer software version whose result matched
    the old one bit for bit: the old directory is an exact duplicate, so
    every Study that held it now holds the new one.
    """
    root = results if results is not None else paths.results_directory()
    old_path = Path(old).resolve()
    new_reference = _run_reference_string(Path(new), results=root)
    for study in list_studies(results=root):
        entries = [
            new_reference
            if _resolve_stored_run_reference(entry, results=root).resolve() == old_path
            else entry
            for entry in study.run_directories
        ]
        deduped = tuple(dict.fromkeys(entries))
        if deduped != study.run_directories:
            write_study_manifest(
                study_manifest_path(study.study_id, results=root),
                replace(study, run_directories=deduped),
            )
    shutil.rmtree(old_path, ignore_errors=True)


def _run_directories_with_id(run_id: str, results: Path) -> list[Path]:
    """Return every run directory under `results` whose manifest has `run_id`."""
    return [
        directory
        for directory, identity in _run_identities(results)
        if identity[0] == run_id
    ]


def _run_identities(results: Path) -> list[tuple[Path, tuple[str, str]]]:
    """Return `(directory, (run_id, software_version))` for every run under `results`.

    Tries the scalar shape then the batch shape for each candidate, like
    `fim.gui.recent_runs._recent_run_from_file` — a local copy rather than
    an import from `fim.gui`, since this module lives in `fim.persistence`
    and must not depend on the GUI package.
    """
    if not results.is_dir():
        return []
    found = []
    for manifest_path in sorted(results.glob("*/manifest.json")):
        identity = _manifest_identity(manifest_path)
        if identity is not None:
            found.append((manifest_path.parent.resolve(), identity))
    return found


def _manifest_identity(manifest_path: Path) -> tuple[str, str] | None:
    """Return one manifest's `(run_id, software_version)`, scalar or batch."""
    try:
        scalar = read_manifest(manifest_path)
        return scalar.run_id, scalar.software_version
    except (OSError, ValueError, KeyError):
        pass
    try:
        batch = read_batch_manifest(manifest_path)
        return batch.run_id, batch.software_version
    except (OSError, ValueError, KeyError):
        return None


def _run_reference_string(run_directory: Path, *, results: Path) -> str:
    """Return how a Run directory should be stored in `run_directories`.

    A bare name when the run lives directly under `results` (the common
    case), a path relative to `results` when nested further inside it,
    or an absolute path as a last resort for a run published entirely
    outside `results` (`fim run -o SOME/OTHER/PATH`).
    """
    resolved = run_directory.resolve()
    try:
        relative = resolved.relative_to(results.resolve())
    except ValueError:
        return str(resolved)
    return str(relative)


def _resolve_stored_run_reference(entry: str, *, results: Path) -> Path:
    """Invert `_run_reference_string`: turn a stored entry back into a path."""
    candidate = Path(entry)
    return candidate if candidate.is_absolute() else results / candidate


def _required_int(
    value: Mapping[str, Any], key: str, *, label: str, minimum: int = 1
) -> int:
    """Read one required integer field bounded below by `minimum`."""
    raw_value = value.get(key)
    if isinstance(raw_value, bool) or not isinstance(raw_value, int):
        raise ValueError(f"{label} field {key!r} must be an integer")
    if raw_value < minimum:
        raise ValueError(f"{label} field {key!r} must be at least {minimum}")
    return raw_value


def _required_string(value: Mapping[str, Any], key: str, *, label: str) -> str:
    """Read one required nonempty string field."""
    raw_value = value.get(key)
    if not isinstance(raw_value, str) or not raw_value:
        raise ValueError(f"{label} field {key!r} must be a nonempty string")
    return raw_value


def _optional_string(value: Mapping[str, Any], key: str, *, label: str) -> str | None:
    """Read one optional nonempty string field, or `None` when absent/null."""
    raw_value = value.get(key)
    if raw_value is None:
        return None
    if not isinstance(raw_value, str) or not raw_value:
        raise ValueError(f"{label} field {key!r} must be a nonempty string or null")
    return raw_value


def _string_tuple(value: Mapping[str, Any], key: str, *, label: str) -> tuple[str, ...]:
    """Read one required list-of-strings field; an empty list is valid."""
    raw_value = value.get(key)
    if not isinstance(raw_value, list):
        raise ValueError(f"{label} field {key!r} must be a list")
    if not all(isinstance(item, str) and item for item in raw_value):
        raise ValueError(f"{label} field {key!r} must contain only nonempty strings")
    return tuple(raw_value)
