"""Durable draft and planned Run objects under `results/objects/`.

A Run object is a saved Run intention with a stable `object_id`. Its
directory holds:

- `config.yaml`: the configuration document exactly as the user saved it,
  valid or not, so reopening a draft returns precisely what was entered.
- `run.json`: the lifecycle record (`fim.persistence.run_lifecycle`), the
  single authority for the object's state.

Every save validates the document. A valid document makes the object
`planned`, with the effective configuration's digest and scientific
`run_id`; an invalid one makes it `draft`, with the validation issues. Saving
is therefore always possible while the object has not started, and the
object moves between `draft` and `planned` as the user edits.

`objects/` is a namespace beside the legacy result directories, never a
replacement for them. Legacy discovery scans `results/*/manifest.json`, so
it never sees an object, and objects never move or rewrite a legacy result.

Every change happens under a cross-process per-object lock
(`fim.persistence.object_lock`) and an expected-revision check, so two
editors cannot silently overwrite each other.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import yaml

from fim.model.params import SimulationParams
from fim.paths import atomic_directory, results_directory, write_text_atomically
from fim.persistence.object_lock import exclusive_lock
from fim.persistence.run_lifecycle import (
    ACTIVE_STATES,
    PRE_EXECUTION_STATES,
    RUN_LIFECYCLE_FILE_NAME,
    Capabilities,
    Configuration,
    IllegalTransitionError,
    LifecycleError,
    LifecycleRecordError,
    RevisionConflictError,
    RunEvent,
    RunKind,
    RunLifecycleRecord,
    ValidationIssue,
    apply_event,
    capabilities,
    is_object_id,
    new_record,
)
from fim.persistence.run_metadata import RunLabels

logger = logging.getLogger(__name__)

OBJECTS_DIRECTORY_NAME: Final = "objects"
CONFIG_FILE_NAME: Final = "config.yaml"
_LOCKS_DIRECTORY_NAME: Final = ".locks"

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


class RunObjectNotFoundError(LifecycleError):
    """No Run object exists with the requested ID."""


@dataclass(frozen=True, slots=True)
class RunObject:
    """One Run object as read from disk.

    Args:
        directory: The object's directory.
        record: Its lifecycle record.
        config_text: The configuration document exactly as saved.
        labels: Labels read from a parseable configuration; empty otherwise.
    """

    directory: Path
    record: RunLifecycleRecord
    config_text: str
    labels: RunLabels

    @property
    def object_id(self) -> str:
        """Return the object's stable identity."""
        return self.record.object_id

    def capabilities(self, **kwargs: bool) -> Capabilities:
        """Return the lifecycle's allowed actions for this object.

        Args:
            **kwargs: Passed to `fim.persistence.run_lifecycle.capabilities`.
        """
        return capabilities(self.record, **kwargs)


@dataclass(frozen=True, slots=True)
class ObjectDiagnostic:
    """A directory under `objects/` that is not a readable Run object.

    Args:
        directory: The unreadable directory.
        message: Why it could not be read.
    """

    directory: Path
    message: str


@dataclass(frozen=True, slots=True)
class ObjectListing:
    """Result of scanning `objects/`: readable objects and diagnostics."""

    objects: tuple[RunObject, ...]
    diagnostics: tuple[ObjectDiagnostic, ...]


def clone_run_object(
    source_object_id: str,
    *,
    results: Path | None = None,
    clock: Clock | None = None,
    id_factory: IdFactory | None = None,
) -> RunObject:
    """Create a new object from another object's saved configuration.

    The clone copies only the configuration document. It starts as `draft`
    or `planned` by that document's validity and records the source as
    provenance; it never copies execution history, checkpoints, or
    ownership.

    Args:
        source_object_id: Object to copy the configuration from.
        results: Results directory (default: `results_directory()`).
        clock: Injectable current-time source.
        id_factory: Injectable object ID source.

    Returns:
        The new object.
    """
    source = read_run_object(source_object_id, results=results)
    return create_run_object(
        source.config_text,
        results=results,
        clock=clock,
        id_factory=id_factory,
        source_object_id=source.object_id,
    )


def configuration_digest(params: SimulationParams) -> str:
    """Return the SHA-256 of the canonical effective configuration."""
    canonical = json.dumps(
        params.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def create_run_object(
    config_text: str,
    *,
    results: Path | None = None,
    clock: Clock | None = None,
    id_factory: IdFactory | None = None,
    source_object_id: str | None = None,
) -> RunObject:
    """Save a new Run object from a configuration document of any validity.

    The object directory is built in a hidden sibling and published with one
    rename, so a crash never leaves a half-created object.

    Args:
        config_text: The configuration document as entered.
        results: Results directory (default: `results_directory()`).
        clock: Injectable current-time source.
        id_factory: Injectable object ID source.
        source_object_id: Object this one was cloned from, if any.

    Returns:
        The new object, `planned` if the document is valid, else `draft`.
    """
    object_id = (id_factory or new_object_id)()
    if not is_object_id(object_id):
        raise ValueError(f"invalid object_id: {object_id!r}")
    configuration, labels = validate_configuration_text(config_text)
    record = new_record(
        object_id,
        configuration,
        at=_now(clock),
        kind=_kind_for(config_text),
        source_object_id=source_object_id,
    )
    directory = object_directory(object_id, results)
    with object_locked(object_id, results), atomic_directory(directory) as working:
        _write_config(working, config_text)
        write_run_record(working, record)
    logger.debug("created run object %s (%s)", object_id, record.state.value)
    return RunObject(directory, record, config_text, labels)


def delete_run_object(
    object_id: str, *, expected_revision: int, results: Path | None = None
) -> None:
    """Delete a Run object that has no active execution.

    The directory is first renamed to a hidden name, so it disappears from
    discovery in one step even if removing its files is interrupted.

    Args:
        object_id: Object to delete.
        expected_revision: Lifecycle revision the caller read.
        results: Results directory (default: `results_directory()`).

    Raises:
        RevisionConflictError: The object changed since it was read.
        IllegalTransitionError: The object has an active attempt.
    """
    with object_locked(object_id, results):
        current = read_run_object_unlocked(object_id, results)
        record = current.record
        if record.revision != expected_revision:
            raise RevisionConflictError(expected_revision, record.revision)
        if record.state in ACTIVE_STATES:
            raise IllegalTransitionError(
                record.state, RunEvent.ABANDON, record.revision
            )
        doomed = current.directory.with_name(f".deleted-{object_id}-{uuid.uuid4().hex}")
        current.directory.rename(doomed)
    shutil.rmtree(doomed)
    logger.debug("deleted run object %s", object_id)


def list_run_objects(results: Path | None = None) -> ObjectListing:
    """Return every readable Run object and a diagnostic for each other one.

    Hidden entries (in-progress creation, deletion, locks) are skipped. A
    directory whose name is an object ID but whose record cannot be read
    yields a diagnostic, never a fabricated object.

    Args:
        results: Results directory (default: `results_directory()`).

    Returns:
        Objects sorted by object ID, and diagnostics sorted by path.
    """
    root = objects_directory(results)
    if not root.is_dir():
        return ObjectListing((), ())
    found: list[RunObject] = []
    diagnostics: list[ObjectDiagnostic] = []
    for entry in sorted(root.iterdir()):
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        if not is_object_id(entry.name):
            diagnostics.append(
                ObjectDiagnostic(entry, "directory name is not a run object ID")
            )
            continue
        try:
            found.append(read_run_object_unlocked(entry.name, results))
        except (LifecycleError, OSError, UnicodeDecodeError) as error:
            diagnostics.append(ObjectDiagnostic(entry, str(error)))
    return ObjectListing(tuple(found), tuple(diagnostics))


def new_object_id() -> str:
    """Return a new random object ID."""
    return f"obj-{uuid.uuid4().hex}"


def object_directory(object_id: str, results: Path | None = None) -> Path:
    """Return the directory of the object `object_id`.

    Raises:
        ValueError: `object_id` is not a well-formed object ID, so it cannot
            be trusted as a path component.
    """
    if not is_object_id(object_id):
        raise ValueError(f"invalid object_id: {object_id!r}")
    return objects_directory(results) / object_id


@contextmanager
def object_locked(object_id: str, results: Path | None) -> Iterator[None]:
    """Hold the object's cross-process bookkeeping lock for the block.

    Every read-modify-write of `run.json` happens under this lock; it is
    held only for the bookkeeping step, never for execution.

    Args:
        object_id: Object whose lock to take.
        results: Results directory (default: `results_directory()`).

    Yields:
        Nothing; the lock is released when the block exits.
    """
    with exclusive_lock(_lock_path(object_id, results)):
        yield


def objects_directory(results: Path | None = None) -> Path:
    """Return the `objects/` namespace inside the results directory."""
    root = results if results is not None else results_directory()
    return root / OBJECTS_DIRECTORY_NAME


def read_run_object(object_id: str, *, results: Path | None = None) -> RunObject:
    """Read one Run object.

    Raises:
        RunObjectNotFoundError: No such object exists.
        LifecycleRecordError: Its record is malformed or unsupported.
    """
    with object_locked(object_id, results):
        return read_run_object_unlocked(object_id, results)


def read_run_object_unlocked(object_id: str, results: Path | None) -> RunObject:
    """Read one object without taking its lock.

    Args:
        object_id: Object to read.
        results: Results directory (default: `results_directory()`).

    Returns:
        The object as stored. The caller holds `object_locked` or tolerates
        a concurrent change.

    Raises:
        RunObjectNotFoundError: No such object exists.
        LifecycleRecordError: `run.json` or `config.yaml` is unreadable.
    """
    directory = object_directory(object_id, results)
    record_path = directory / RUN_LIFECYCLE_FILE_NAME
    if not record_path.is_file():
        raise RunObjectNotFoundError(f"no run object {object_id!r}")
    try:
        document: Any = json.loads(record_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise LifecycleRecordError(f"run.json is not valid JSON: {error}") from error
    record = RunLifecycleRecord.from_dict(document)
    if record.object_id != object_id:
        raise LifecycleRecordError(
            f"run.json names object {record.object_id!r}, not {object_id!r}"
        )
    config_path = directory / CONFIG_FILE_NAME
    if not config_path.is_file():
        raise LifecycleRecordError("config.yaml is missing")
    config_text = config_path.read_text(encoding="utf-8")
    labels = RunLabels()
    if record.state in PRE_EXECUTION_STATES:
        _, labels = validate_configuration_text(config_text)
    else:
        try:
            document = yaml.safe_load(config_text)
            if isinstance(document, Mapping):
                labels = RunLabels.from_config(document)
        except (yaml.YAMLError, ValueError):
            labels = RunLabels()
    return RunObject(directory, record, config_text, labels)


def save_run_object(
    object_id: str,
    config_text: str,
    *,
    expected_revision: int,
    results: Path | None = None,
    clock: Clock | None = None,
) -> RunObject:
    """Save an edited configuration; reclassify the object by its validity.

    Args:
        object_id: Object to update.
        config_text: The edited configuration document, valid or not.
        expected_revision: Lifecycle revision the caller read.
        results: Results directory (default: `results_directory()`).
        clock: Injectable current-time source.

    Returns:
        The updated object: `planned` if the document is valid, else `draft`.

    Raises:
        RevisionConflictError: The object changed since it was read.
        IllegalTransitionError: Execution has started, so the configuration
            is fixed.
    """
    configuration, labels = validate_configuration_text(config_text)
    event = RunEvent.SAVE_VALID if configuration.is_valid else RunEvent.SAVE_INVALID
    with object_locked(object_id, results):
        current = read_run_object_unlocked(object_id, results)
        record = apply_event(
            current.record,
            event,
            at=_now(clock),
            expected_revision=expected_revision,
            configuration=configuration,
        )
        record = replace(record, kind=_kind_for(config_text))
        # Configuration first: a crash between the two writes leaves a
        # record whose digest disagrees with the text, which start refuses.
        _write_config(current.directory, config_text)
        write_run_record(current.directory, record)
    logger.debug("saved run object %s (%s)", object_id, record.state.value)
    return RunObject(current.directory, record, config_text, labels)


def validate_configuration_text(
    config_text: str,
) -> tuple[Configuration, RunLabels]:
    """Validate one configuration document without raising for bad input.

    The same checks `fim run` applies: the document must be a YAML mapping,
    its labels must be valid, and `SimulationParams.from_mapping` must accept
    it. Every failure becomes a `ValidationIssue`.

    Args:
        config_text: The configuration document.

    Returns:
        The validation outcome and the labels (empty unless they parsed).
    """
    try:
        document = yaml.safe_load(config_text)
    except yaml.YAMLError as error:
        return Configuration.invalid((_yaml_issue(error),)), RunLabels()
    if document is None:
        issue = ValidationIssue("the configuration is empty")
        return Configuration.invalid((issue,)), RunLabels()
    if not isinstance(document, Mapping):
        issue = ValidationIssue("the configuration must be a mapping of settings")
        return Configuration.invalid((issue,)), RunLabels()
    issues: list[ValidationIssue] = []
    try:
        labels = RunLabels.from_config(document)
    except ValueError as error:
        labels = RunLabels()
        issues.append(ValidationIssue(str(error)))
    try:
        params = SimulationParams.from_mapping(document)
    except (ValueError, TypeError) as error:
        issues.append(ValidationIssue(str(error)))
    if issues:
        return Configuration.invalid(issues), labels
    return (
        Configuration.valid(configuration_digest(params), _scientific_run_id(params)),
        labels,
    )


def write_run_record(directory: Path, record: RunLifecycleRecord) -> None:
    """Write `run.json` deterministically and atomically."""
    text = json.dumps(record.to_dict(), indent=2, sort_keys=True, allow_nan=False)
    write_text_atomically(
        directory / RUN_LIFECYCLE_FILE_NAME, text + "\n", prefix=".run."
    )


def _kind_for(config_text: str) -> RunKind:
    """Return batch when a parseable document asks for several replicates."""
    try:
        document = yaml.safe_load(config_text)
    except yaml.YAMLError:
        return RunKind.SCALAR
    replicates = document.get("n_replicates") if isinstance(document, dict) else None
    if isinstance(replicates, int) and not isinstance(replicates, bool):
        return RunKind.BATCH if replicates > 1 else RunKind.SCALAR
    return RunKind.SCALAR


def _lock_path(object_id: str, results: Path | None) -> Path:
    """Return the lock file guarding the object's bookkeeping."""
    directory = object_directory(object_id, results)
    return directory.parent / _LOCKS_DIRECTORY_NAME / f"{object_id}.lock"


def _now(clock: Clock | None) -> datetime:
    """Return the current time from `clock`, or the real UTC time."""
    return clock() if clock is not None else datetime.now(UTC)


def _scientific_run_id(params: SimulationParams) -> str:
    """Return the run ID `fim run` would derive for `params`."""
    from fim.engine import deterministic_run_id  # noqa: PLC0415

    return deterministic_run_id(params)


def _write_config(directory: Path, config_text: str) -> None:
    """Write the configuration document exactly as given."""
    write_text_atomically(directory / CONFIG_FILE_NAME, config_text, prefix=".config.")


def _yaml_issue(error: yaml.YAMLError) -> ValidationIssue:
    """Return a validation issue locating a YAML syntax error."""
    mark = getattr(error, "problem_mark", None)
    location = None
    if mark is not None:
        location = f"line {mark.line + 1}, column {mark.column + 1}"
    problem = getattr(error, "problem", None) or "the configuration is not valid YAML"
    return ValidationIssue(f"YAML syntax error: {problem}", location)
