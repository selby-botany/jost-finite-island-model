"""Versioned Run lifecycle records, the transition table, and capabilities.

A Run object moves through durable lifecycle states that are separate from
its scientific outcome. This module is the single definition of those
states, the events that move between them, and the record stored as an
object's `run.json`. It does no I/O: `fim.persistence.run_objects` reads and
writes records, and every frontend (CLI, GUI, sweeps) asks this module, never
its own copy of the rules, whether an action is legal.

Two states hold an editable configuration that has never run:

- `draft`: the saved configuration is invalid or incomplete. It may be
  edited, cloned, or deleted, but never started.
- `planned`: the saved configuration is valid and complete, so it may start.

Saving reclassifies the object: a save whose configuration validates moves
it to `planned`, and a save that does not moves it to `draft`. The user
never picks between the two directly.

The remaining states follow the Run lifecycle design
(`20261006-gpt-6.1-sol-run-lifecycle-and-checkpoint-design.md`,
`selby/restricted`, section 4). `completed` and `cancelled` are terminal.
Delete is removal of the object, not a state. One event, `recover-completion`,
is added to that design: a worker that published its completion receipt
but stopped before committing `completed` is completed by reconciliation
once the receipt verifies. A release that predates the event cannot read a
record whose history contains it. Phase, scientific outcome,
execution kind, and protection are attributes of the record, never extra
state values.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

from fim.errors import FimError

CURRENT_RUN_LIFECYCLE_SCHEMA_VERSION: Final = 1
"""Version of the `run.json` shape; bumped on an incompatible change."""

RUN_LIFECYCLE_FILE_NAME: Final = "run.json"

_OBJECT_ID_PATTERN: Final = re.compile(r"^obj-[0-9a-f]{32}$")
_ATTEMPT_ID_PATTERN: Final = re.compile(r"^att-[0-9a-f]{32}$")
_COMMAND_KEY_PATTERN: Final = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class RunState(StrEnum):
    """Durable lifecycle state of one Run object."""

    DRAFT = "draft"
    PLANNED = "planned"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class RunEvent(StrEnum):
    """An event that may move a Run object to another lifecycle state."""

    SAVE_VALID = "save-valid"
    SAVE_INVALID = "save-invalid"
    START = "start"
    REQUEST_PAUSE = "request-pause"
    COMMIT_PAUSE = "commit-pause"
    RESUME = "resume"
    COMPLETE = "complete"
    OWNER_LOST = "owner-lost"
    FAIL = "fail"
    REQUEST_CANCEL = "request-cancel"
    COMMIT_CANCEL = "commit-cancel"
    ABANDON = "abandon"
    RECOVER_COMPLETION = "recover-completion"


class RunKind(StrEnum):
    """Whether a Run object executes one replicate or a replicate batch."""

    SCALAR = "scalar"
    BATCH = "batch"


class RunPhase(StrEnum):
    """Execution phase within `running`; never a lifecycle state."""

    INITIALIZING = "initializing"
    SIMULATING = "simulating"
    SIGMA_BAND = "sigma_band"
    FINALIZING = "finalizing"


PRE_EXECUTION_STATES: Final = frozenset({RunState.DRAFT, RunState.PLANNED})
"""States whose configuration is still editable; nothing has executed."""

ACTIVE_STATES: Final = frozenset(
    {RunState.RUNNING, RunState.PAUSING, RunState.CANCELLING}
)
"""States in which one attempt owns execution."""

TERMINAL_STATES: Final = frozenset({RunState.COMPLETED, RunState.CANCELLED})
"""States no event may leave."""

RESUMABLE_STATES: Final = frozenset(
    {RunState.PAUSED, RunState.INTERRUPTED, RunState.FAILED}
)
"""States from which a verified checkpoint may continue execution."""

_S = RunState
_E = RunEvent

TRANSITIONS: Final[Mapping[tuple[RunState, RunEvent], RunState]] = {
    # Saving reclassifies a never-executed object by its validation result.
    (_S.DRAFT, _E.SAVE_VALID): _S.PLANNED,
    (_S.DRAFT, _E.SAVE_INVALID): _S.DRAFT,
    (_S.PLANNED, _E.SAVE_VALID): _S.PLANNED,
    (_S.PLANNED, _E.SAVE_INVALID): _S.DRAFT,
    (_S.PLANNED, _E.START): _S.RUNNING,
    (_S.RUNNING, _E.REQUEST_PAUSE): _S.PAUSING,
    (_S.PAUSING, _E.COMMIT_PAUSE): _S.PAUSED,
    (_S.RUNNING, _E.COMPLETE): _S.COMPLETED,
    (_S.PAUSING, _E.COMPLETE): _S.COMPLETED,
    # Completion wins when it commits before cancellation takes effect.
    (_S.CANCELLING, _E.COMPLETE): _S.COMPLETED,
    (_S.PAUSED, _E.RESUME): _S.RUNNING,
    (_S.INTERRUPTED, _E.RESUME): _S.RUNNING,
    (_S.FAILED, _E.RESUME): _S.RUNNING,
    (_S.RUNNING, _E.OWNER_LOST): _S.INTERRUPTED,
    (_S.PAUSING, _E.OWNER_LOST): _S.INTERRUPTED,
    (_S.CANCELLING, _E.OWNER_LOST): _S.INTERRUPTED,
    (_S.RUNNING, _E.FAIL): _S.FAILED,
    (_S.PAUSING, _E.FAIL): _S.FAILED,
    (_S.CANCELLING, _E.FAIL): _S.FAILED,
    # A stop request supersedes a pending pause request.
    (_S.RUNNING, _E.REQUEST_CANCEL): _S.CANCELLING,
    (_S.PAUSING, _E.REQUEST_CANCEL): _S.CANCELLING,
    (_S.CANCELLING, _E.COMMIT_CANCEL): _S.CANCELLED,
    (_S.PLANNED, _E.ABANDON): _S.CANCELLED,
    (_S.PAUSED, _E.ABANDON): _S.CANCELLED,
    (_S.INTERRUPTED, _E.ABANDON): _S.CANCELLED,
    (_S.FAILED, _E.ABANDON): _S.CANCELLED,
    # A worker that published a verified completion receipt but lost
    # ownership before committing completion; see `apply_event`.
    (_S.RUNNING, _E.RECOVER_COMPLETION): _S.COMPLETED,
    (_S.PAUSING, _E.RECOVER_COMPLETION): _S.COMPLETED,
    (_S.CANCELLING, _E.RECOVER_COMPLETION): _S.COMPLETED,
    (_S.INTERRUPTED, _E.RECOVER_COMPLETION): _S.COMPLETED,
    (_S.FAILED, _E.RECOVER_COMPLETION): _S.COMPLETED,
}
"""Every legal `(state, event)` pair and the state it leads to."""

_ATTEMPT_OPENING_EVENTS: Final = frozenset({RunEvent.START, RunEvent.RESUME})
_ATTEMPT_CLOSING_STATES: Final = frozenset(
    {
        RunState.PAUSED,
        RunState.INTERRUPTED,
        RunState.FAILED,
        RunState.CANCELLED,
        RunState.COMPLETED,
    }
)
_SAVE_EVENTS: Final = frozenset({RunEvent.SAVE_VALID, RunEvent.SAVE_INVALID})


class LifecycleError(FimError):
    """Base class for every lifecycle rule or record violation."""


class IllegalTransitionError(LifecycleError):
    """An event is not legal in the object's current state.

    Attributes:
        state: The object's current state.
        event: The rejected event.
        revision: The object's current lifecycle revision.
    """

    def __init__(self, state: RunState, event: RunEvent, revision: int) -> None:
        """Record the rejected request and describe it."""
        self.state = state
        self.event = event
        self.revision = revision
        super().__init__(
            f"cannot apply {event.value!r} to a run in state "
            f"{state.value!r} (revision {revision})"
        )


class RevisionConflictError(LifecycleError):
    """A request named a lifecycle revision other than the current one.

    Attributes:
        expected: The revision the caller based its request on.
        actual: The object's current revision.
    """

    def __init__(self, expected: int, actual: int) -> None:
        """Record both revisions and describe the conflict."""
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"run changed since it was read: expected revision {expected}, "
            f"found {actual}"
        )


class AttemptMismatchError(LifecycleError):
    """An event came from an attempt that no longer owns execution."""


class LifecycleRecordError(LifecycleError):
    """A stored lifecycle record is malformed or has an unknown schema."""


class CommandKeyReuseError(LifecycleError):
    """An idempotency key was reused for a different command.

    Attributes:
        command_key: The reused key.
        original: The transition the key first produced.
    """

    def __init__(self, command_key: str, original: TransitionRecord) -> None:
        """Record the key and its original transition, and describe them."""
        self.command_key = command_key
        self.original = original
        super().__init__(
            f"command key {command_key!r} already applied "
            f"{original.event.value!r} at revision {original.revision}"
        )


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One reason a saved configuration cannot run.

    Args:
        message: Human-readable description of the problem.
        location: Configuration key or document location, or `None` when
            the problem concerns the document as a whole.
    """

    message: str
    location: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form of this issue."""
        return {"location": self.location, "message": self.message}

    @classmethod
    def from_dict(cls, value: object) -> ValidationIssue:
        """Parse one stored issue, rejecting a malformed value."""
        mapping = _require_mapping("validation issue", value)
        return cls(
            message=_require_str("validation issue message", mapping.get("message")),
            location=_optional_str(
                "validation issue location", mapping.get("location")
            ),
        )


@dataclass(frozen=True, slots=True)
class ErrorInfo:
    """The most recent execution error recorded for a Run object.

    Args:
        category: Short machine-readable category, such as `engine` or
            `storage`.
        message: Human-readable description.
        phase: Execution phase when the error happened, if known.
        attempt_id: Attempt that reported the error, if any.
    """

    category: str
    message: str
    phase: RunPhase | None = None
    attempt_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form of this error."""
        return {
            "attempt_id": self.attempt_id,
            "category": self.category,
            "message": self.message,
            "phase": None if self.phase is None else self.phase.value,
        }

    @classmethod
    def from_dict(cls, value: object) -> ErrorInfo:
        """Parse one stored error, rejecting a malformed value."""
        mapping = _require_mapping("last error", value)
        phase = _optional_str("last error phase", mapping.get("phase"))
        return cls(
            category=_require_str("last error category", mapping.get("category")),
            message=_require_str("last error message", mapping.get("message")),
            phase=None if phase is None else _parse_enum(RunPhase, phase, "phase"),
            attempt_id=_optional_str(
                "last error attempt_id", mapping.get("attempt_id")
            ),
        )


@dataclass(frozen=True, slots=True)
class CheckpointReference:
    """Pointer from a lifecycle record to its latest committed checkpoint.

    Args:
        checkpoint_id: Directory name under the object's `checkpoints/`.
        digest: SHA-256 hex digest of the checkpoint's metadata document.
        committed_generation: Last generation the checkpoint fully contains.
    """

    checkpoint_id: str
    digest: str
    committed_generation: int

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form of this reference."""
        return {
            "checkpoint_id": self.checkpoint_id,
            "committed_generation": self.committed_generation,
            "digest": self.digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> CheckpointReference:
        """Parse one stored reference, rejecting a malformed value."""
        mapping = _require_mapping("latest checkpoint", value)
        return cls(
            checkpoint_id=_require_str("checkpoint_id", mapping.get("checkpoint_id")),
            digest=_require_str("checkpoint digest", mapping.get("digest")),
            committed_generation=_require_int(
                "committed_generation", mapping.get("committed_generation"), 0
            ),
        )


@dataclass(frozen=True, slots=True)
class TransitionRecord:
    """One lifecycle transition retained in the object's history.

    Args:
        event: The event applied.
        from_state: State before the event.
        to_state: State after the event.
        at: UTC timestamp of the transition.
        revision: Lifecycle revision the transition produced.
        attempt_id: Attempt that caused the transition, if any.
        command_key: Idempotency key of the request that caused the
            transition, if the caller supplied one.
    """

    event: RunEvent
    from_state: RunState
    to_state: RunState
    at: str
    revision: int
    attempt_id: str | None = None
    command_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form of this transition."""
        return {
            "at": self.at,
            "attempt_id": self.attempt_id,
            "command_key": self.command_key,
            "event": self.event.value,
            "from_state": self.from_state.value,
            "revision": self.revision,
            "to_state": self.to_state.value,
        }

    @classmethod
    def from_dict(cls, value: object) -> TransitionRecord:
        """Parse one stored transition, rejecting a malformed value."""
        mapping = _require_mapping("transition", value)
        return cls(
            event=_parse_enum(RunEvent, mapping.get("event"), "event"),
            from_state=_parse_enum(RunState, mapping.get("from_state"), "from_state"),
            to_state=_parse_enum(RunState, mapping.get("to_state"), "to_state"),
            at=_require_str("transition at", mapping.get("at")),
            revision=_require_int("transition revision", mapping.get("revision"), 1),
            attempt_id=_optional_str(
                "transition attempt_id", mapping.get("attempt_id")
            ),
            command_key=_optional_str(
                "transition command_key", mapping.get("command_key")
            ),
        )


@dataclass(frozen=True, slots=True)
class RunLifecycleRecord:
    """The mutable lifecycle authority for one Run object (`run.json`).

    A draft has validation issues and no configuration digest or scientific
    run ID; a planned or later object has a digest and run ID and no issues.
    `__post_init__` enforces those and the other cross-field invariants, so a
    record that exists is always internally consistent.

    Args:
        object_id: Stable identity of the persisted Run object.
        kind: Scalar or batch execution.
        state: Current lifecycle state.
        revision: Monotonic lifecycle revision, starting at 1.
        configuration_revision: Number of configuration saves, starting at 1.
        configuration_digest: SHA-256 of the canonical effective
            configuration, or `None` for a draft.
        scientific_run_id: Content-derived scientific run ID, or `None` for a
            draft.
        validation_issues: Why a draft cannot run; empty otherwise.
        created_at: UTC creation timestamp.
        updated_at: UTC timestamp of the latest change.
        first_started_at: UTC timestamp of the first start, if any.
        completed_at: UTC timestamp of completion, if completed.
        phase: Current or last execution phase, if execution began.
        committed_generation: Last durably committed generation, if any.
        active_attempt_id: Attempt that owns execution in an active state.
        latest_checkpoint: Latest committed checkpoint, if any.
        last_error: Most recent execution error, if any.
        source_object_id: Object this one was cloned or retried from.
        transition_history: Every transition applied, oldest first.
        schema_version: `run.json` schema version.
    """

    object_id: str
    kind: RunKind
    state: RunState
    revision: int
    configuration_revision: int
    configuration_digest: str | None
    scientific_run_id: str | None
    validation_issues: tuple[ValidationIssue, ...]
    created_at: str
    updated_at: str
    first_started_at: str | None = None
    completed_at: str | None = None
    phase: RunPhase | None = None
    committed_generation: int | None = None
    active_attempt_id: str | None = None
    latest_checkpoint: CheckpointReference | None = None
    last_error: ErrorInfo | None = None
    source_object_id: str | None = None
    transition_history: tuple[TransitionRecord, ...] = field(default=())
    schema_version: int = CURRENT_RUN_LIFECYCLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        """Reject a record whose fields contradict each other."""
        if not is_object_id(self.object_id):
            raise LifecycleRecordError(f"invalid object_id: {self.object_id!r}")
        if self.revision < 1 or self.configuration_revision < 1:
            raise LifecycleRecordError("revisions start at 1")
        configured = self.configuration_digest is not None
        if configured != (self.scientific_run_id is not None):
            raise LifecycleRecordError(
                "configuration_digest and scientific_run_id must both be set "
                "or both be absent"
            )
        if self.state is RunState.DRAFT:
            if configured or not self.validation_issues:
                raise LifecycleRecordError(
                    "a draft has validation issues and no effective configuration"
                )
        elif not configured or self.validation_issues:
            raise LifecycleRecordError(
                f"a {self.state.value} run has a valid effective configuration"
            )
        if (self.active_attempt_id is not None) != (self.state in ACTIVE_STATES):
            raise LifecycleRecordError(
                "exactly the active states have an active attempt"
            )
        if self.active_attempt_id is not None and not is_attempt_id(
            self.active_attempt_id
        ):
            raise LifecycleRecordError(
                f"invalid attempt_id: {self.active_attempt_id!r}"
            )
        if (self.completed_at is not None) != (self.state is RunState.COMPLETED):
            raise LifecycleRecordError("only a completed run has completed_at")
        if self.state in PRE_EXECUTION_STATES and (
            self.first_started_at is not None
            or self.phase is not None
            or self.committed_generation is not None
            or self.latest_checkpoint is not None
        ):
            raise LifecycleRecordError(
                "a run that never started has no execution progress"
            )

    @property
    def is_draft(self) -> bool:
        """Return whether the saved configuration cannot run yet."""
        return self.state is RunState.DRAFT

    @property
    def is_terminal(self) -> bool:
        """Return whether no event may leave the current state."""
        return self.state in TERMINAL_STATES

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form stored as `run.json`."""
        return {
            "active_attempt_id": self.active_attempt_id,
            "committed_generation": self.committed_generation,
            "completed_at": self.completed_at,
            "configuration_digest": self.configuration_digest,
            "configuration_revision": self.configuration_revision,
            "created_at": self.created_at,
            "first_started_at": self.first_started_at,
            "kind": self.kind.value,
            "last_error": None
            if self.last_error is None
            else self.last_error.to_dict(),
            "latest_checkpoint": (
                None
                if self.latest_checkpoint is None
                else self.latest_checkpoint.to_dict()
            ),
            "object_id": self.object_id,
            "phase": None if self.phase is None else self.phase.value,
            "revision": self.revision,
            "schema_version": self.schema_version,
            "scientific_run_id": self.scientific_run_id,
            "source_object_id": self.source_object_id,
            "state": self.state.value,
            "transition_history": [
                transition.to_dict() for transition in self.transition_history
            ],
            "updated_at": self.updated_at,
            "validation_issues": [issue.to_dict() for issue in self.validation_issues],
        }

    @classmethod
    def from_dict(cls, value: object) -> RunLifecycleRecord:
        """Parse a stored `run.json` document.

        Raises:
            LifecycleRecordError: The document is malformed, inconsistent, or
                has a schema version this release does not understand.
        """
        mapping = _require_mapping("run.json", value)
        version = mapping.get("schema_version")
        if version != CURRENT_RUN_LIFECYCLE_SCHEMA_VERSION:
            raise LifecycleRecordError(
                f"unsupported run.json schema_version: {version!r}"
            )
        phase = _optional_str("phase", mapping.get("phase"))
        checkpoint = mapping.get("latest_checkpoint")
        error = mapping.get("last_error")
        return cls(
            object_id=_require_str("object_id", mapping.get("object_id")),
            kind=_parse_enum(RunKind, mapping.get("kind"), "kind"),
            state=_parse_enum(RunState, mapping.get("state"), "state"),
            revision=_require_int("revision", mapping.get("revision"), 1),
            configuration_revision=_require_int(
                "configuration_revision", mapping.get("configuration_revision"), 1
            ),
            configuration_digest=_optional_str(
                "configuration_digest", mapping.get("configuration_digest")
            ),
            scientific_run_id=_optional_str(
                "scientific_run_id", mapping.get("scientific_run_id")
            ),
            validation_issues=tuple(
                ValidationIssue.from_dict(issue)
                for issue in _require_list(
                    "validation_issues", mapping.get("validation_issues")
                )
            ),
            created_at=_require_str("created_at", mapping.get("created_at")),
            updated_at=_require_str("updated_at", mapping.get("updated_at")),
            first_started_at=_optional_str(
                "first_started_at", mapping.get("first_started_at")
            ),
            completed_at=_optional_str("completed_at", mapping.get("completed_at")),
            phase=None if phase is None else _parse_enum(RunPhase, phase, "phase"),
            committed_generation=_optional_int(
                "committed_generation", mapping.get("committed_generation")
            ),
            active_attempt_id=_optional_str(
                "active_attempt_id", mapping.get("active_attempt_id")
            ),
            latest_checkpoint=(
                None
                if checkpoint is None
                else CheckpointReference.from_dict(checkpoint)
            ),
            last_error=None if error is None else ErrorInfo.from_dict(error),
            source_object_id=_optional_str(
                "source_object_id", mapping.get("source_object_id")
            ),
            transition_history=tuple(
                TransitionRecord.from_dict(item)
                for item in _require_list(
                    "transition_history", mapping.get("transition_history")
                )
            ),
            schema_version=CURRENT_RUN_LIFECYCLE_SCHEMA_VERSION,
        )


@dataclass(frozen=True, slots=True)
class Configuration:
    """Validation outcome of one saved configuration.

    Exactly one of the two forms holds: a valid configuration has a digest
    and scientific run ID; an invalid one has at least one issue.

    Args:
        digest: SHA-256 of the canonical effective configuration, or `None`.
        scientific_run_id: Content-derived run ID, or `None`.
        issues: Why the configuration cannot run; empty when valid.
    """

    digest: str | None
    scientific_run_id: str | None
    issues: tuple[ValidationIssue, ...] = ()

    def __post_init__(self) -> None:
        """Reject a configuration that is both or neither valid and invalid."""
        valid = self.digest is not None and self.scientific_run_id is not None
        partial = (self.digest is None) != (self.scientific_run_id is None)
        if partial or valid == bool(self.issues):
            raise ValueError(
                "a configuration is valid (digest and run ID) or invalid "
                "(issues), never both or neither"
            )

    @property
    def is_valid(self) -> bool:
        """Return whether the configuration may run."""
        return not self.issues

    @classmethod
    def valid(cls, digest: str, scientific_run_id: str) -> Configuration:
        """Return a valid configuration outcome."""
        return cls(digest=digest, scientific_run_id=scientific_run_id)

    @classmethod
    def invalid(cls, issues: Sequence[ValidationIssue]) -> Configuration:
        """Return an invalid configuration outcome."""
        return cls(digest=None, scientific_run_id=None, issues=tuple(issues))


@dataclass(frozen=True, slots=True)
class Capabilities:
    """Actions the lifecycle allows for one Run object, with reasons.

    Frontends render these; they never recompute them. A `False` capability
    has an entry in `reasons` explaining why.
    """

    can_edit: bool
    can_start: bool
    can_pause: bool
    can_resume: bool
    can_cancel: bool
    can_abandon: bool
    can_delete: bool
    reasons: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form sent to frontends."""
        return {
            "can_abandon": self.can_abandon,
            "can_cancel": self.can_cancel,
            "can_delete": self.can_delete,
            "can_edit": self.can_edit,
            "can_pause": self.can_pause,
            "can_resume": self.can_resume,
            "can_start": self.can_start,
            "reasons": dict(sorted(self.reasons.items())),
        }


def apply_event(
    record: RunLifecycleRecord,
    event: RunEvent,
    *,
    at: datetime,
    expected_revision: int | None = None,
    attempt_id: str | None = None,
    configuration: Configuration | None = None,
    phase: RunPhase | None = None,
    error: ErrorInfo | None = None,
    command_key: str | None = None,
) -> RunLifecycleRecord:
    """Return the record that results from applying one event.

    Args:
        record: The current record.
        event: The event to apply.
        at: Time of the transition; converted to UTC.
        expected_revision: Revision the caller read, or `None` to skip the
            check (only for an internal step already under the object lock).
        attempt_id: For `start`/`resume`, the new attempt; for an event raised
            by a worker, the attempt raising it.
        configuration: For a save event, the newly validated configuration.
        phase: Execution phase to record, if it changed.
        error: For `fail`, the error to record.
        command_key: Idempotency key of the request, recorded in the
            transition so a retried request can find its original outcome
            (see `find_command`).

    Returns:
        A new record with the next state, revision + 1, and the transition
        appended to its history.

    Raises:
        RevisionConflictError: `expected_revision` is stale.
        IllegalTransitionError: The event is not legal in the current state,
            or `recover-completion` is applied outside the finalizing phase.
        AttemptMismatchError: A worker event names an attempt that does not
            own execution, or a start/resume omits its new attempt.
        CommandKeyReuseError: `command_key` already produced a transition.
        ValueError: An argument required by the event is missing or wrong.
    """
    if command_key is not None:
        _check_command_key(command_key)
        original = find_command(record, command_key)
        if original is not None:
            raise CommandKeyReuseError(command_key, original)
    if expected_revision is not None and expected_revision != record.revision:
        raise RevisionConflictError(expected_revision, record.revision)
    target = TRANSITIONS.get((record.state, event))
    if target is None:
        raise IllegalTransitionError(record.state, event, record.revision)
    # Only a run that reached finalization can have published a receipt;
    # the caller verifies the receipt itself (`fim.persistence.run_receipt`).
    if event is RunEvent.RECOVER_COMPLETION and record.phase is not RunPhase.FINALIZING:
        raise IllegalTransitionError(record.state, event, record.revision)
    timestamp = format_timestamp(at)
    changes = {
        **_configuration_changes(record, event, configuration),
        **_ownership_changes(record, event, target, attempt_id, timestamp),
        **_outcome_changes(event, target, error, timestamp),
    }
    if phase is not None:
        changes["phase"] = phase

    revision = record.revision + 1
    transition = TransitionRecord(
        event=event,
        from_state=record.state,
        to_state=target,
        at=timestamp,
        revision=revision,
        attempt_id=attempt_id,
        command_key=command_key,
    )
    return replace(
        record,
        state=target,
        revision=revision,
        updated_at=timestamp,
        transition_history=(*record.transition_history, transition),
        **changes,
    )


def capabilities(
    record: RunLifecycleRecord,
    *,
    read_only: bool = False,
    pause_supported: bool = False,
    resume_verified: bool = False,
) -> Capabilities:
    """Return the actions allowed for one Run object, with reasons.

    Args:
        record: The object's lifecycle record.
        read_only: Whether the object is a protected example.
        pause_supported: Whether the active attempt's engine phase can pause.
        resume_verified: Whether a checkpoint was verified as compatible.

    Returns:
        The allowed actions; every disallowed one has a reason.
    """
    state = record.state
    reasons: dict[str, str] = {}

    def allow(name: str, ok: bool, reason: str) -> bool:
        """Record `reason` for `name` when the action is not allowed."""
        if not ok:
            reasons[name] = reason
        return ok

    if read_only:
        protected = "this run is a read-only example"
        for name in (
            "can_edit",
            "can_start",
            "can_pause",
            "can_resume",
            "can_cancel",
            "can_abandon",
            "can_delete",
        ):
            reasons[name] = protected
        return Capabilities(False, False, False, False, False, False, False, reasons)

    can_edit = allow(
        "can_edit",
        state in PRE_EXECUTION_STATES,
        "the configuration is fixed once execution starts",
    )
    if state is RunState.DRAFT:
        start_reason = "the configuration is incomplete or invalid: " + "; ".join(
            issue.message for issue in record.validation_issues
        )
    else:
        start_reason = f"a {state.value} run cannot start"
    can_start = allow("can_start", state is RunState.PLANNED, start_reason)
    can_pause = allow(
        "can_pause",
        state is RunState.RUNNING and pause_supported,
        (
            "pause is not available in the current phase"
            if state is RunState.RUNNING
            else f"a {state.value} run is not running"
        ),
    )
    can_resume = allow(
        "can_resume",
        state in RESUMABLE_STATES and resume_verified,
        (
            "no verified compatible checkpoint is available"
            if state in RESUMABLE_STATES
            else f"a {state.value} run cannot resume"
        ),
    )
    can_cancel = allow(
        "can_cancel",
        state in {RunState.RUNNING, RunState.PAUSING},
        f"a {state.value} run has no execution to cancel",
    )
    can_abandon = allow(
        "can_abandon",
        (state, RunEvent.ABANDON) in TRANSITIONS,
        (
            "delete a draft instead of abandoning it"
            if state is RunState.DRAFT
            else f"a {state.value} run cannot be abandoned"
        ),
    )
    can_delete = allow(
        "can_delete",
        state not in ACTIVE_STATES,
        "pause or cancel the run and wait for it to stop before deleting it",
    )
    return Capabilities(
        can_edit=can_edit,
        can_start=can_start,
        can_pause=can_pause,
        can_resume=can_resume,
        can_cancel=can_cancel,
        can_abandon=can_abandon,
        can_delete=can_delete,
        reasons=reasons,
    )


def _configuration_changes(
    record: RunLifecycleRecord,
    event: RunEvent,
    configuration: Configuration | None,
) -> dict[str, Any]:
    """Return a save event's configuration fields; check they match it.

    Raises:
        ValueError: A save lacks its configuration, the configuration's
            validity disagrees with the event, or a non-save event passes one.
    """
    if event not in _SAVE_EVENTS:
        if configuration is not None:
            raise ValueError("only a save event may change the configuration")
        return {}
    if configuration is None:
        raise ValueError("a save event requires its validated configuration")
    if configuration.is_valid != (event is RunEvent.SAVE_VALID):
        raise ValueError(f"{event.value!r} does not match the configuration")
    return {
        "configuration_digest": configuration.digest,
        "configuration_revision": record.configuration_revision + 1,
        "scientific_run_id": configuration.scientific_run_id,
        "validation_issues": configuration.issues,
    }


def _outcome_changes(
    event: RunEvent,
    target: RunState,
    error: ErrorInfo | None,
    timestamp: str,
) -> dict[str, Any]:
    """Return the error and completion fields an event records.

    Raises:
        ValueError: `fail` lacks its error, or another event passes one.
    """
    changes: dict[str, Any] = {}
    if event is RunEvent.FAIL:
        if error is None:
            raise ValueError("a fail event requires its error")
        changes["last_error"] = error
    elif error is not None:
        raise ValueError("only a fail event records an error")
    if target is RunState.COMPLETED:
        changes["completed_at"] = timestamp
    if event is RunEvent.RECOVER_COMPLETION:
        changes["last_error"] = None
    return changes


def _ownership_changes(
    record: RunLifecycleRecord,
    event: RunEvent,
    target: RunState,
    attempt_id: str | None,
    timestamp: str,
) -> dict[str, Any]:
    """Return the attempt fields an event changes; fence stale attempts.

    Start and resume open a new attempt. Any other event that names an
    attempt while one owns execution must name that same attempt.

    Raises:
        AttemptMismatchError: The new attempt ID is missing or malformed, or
            a worker event names an attempt that does not own execution.
    """
    changes: dict[str, Any] = {}
    if event in _ATTEMPT_OPENING_EVENTS:
        if attempt_id is None or not is_attempt_id(attempt_id):
            raise AttemptMismatchError(f"{event.value!r} requires a new attempt_id")
        changes["active_attempt_id"] = attempt_id
        changes["last_error"] = None
        if record.first_started_at is None:
            changes["first_started_at"] = timestamp
    elif (
        record.state in ACTIVE_STATES
        and attempt_id is not None
        and attempt_id != record.active_attempt_id
    ):
        raise AttemptMismatchError(
            f"attempt {attempt_id!r} does not own run {record.object_id!r}"
        )
    if target in _ATTEMPT_CLOSING_STATES:
        changes["active_attempt_id"] = None
    return changes


def find_command(
    record: RunLifecycleRecord, command_key: str
) -> TransitionRecord | None:
    """Return the transition an idempotency key produced, if any.

    Args:
        record: The record whose history to search.
        command_key: The idempotency key of a request.

    Returns:
        The transition recorded with `command_key`, or `None`.
    """
    for transition in record.transition_history:
        if transition.command_key == command_key:
            return transition
    return None


def format_timestamp(value: datetime) -> str:
    """Return an unambiguous UTC ISO-8601 timestamp, matching `RunManifest`.

    Raises:
        ValueError: `value` has no time zone, so its instant is ambiguous.
    """
    if value.tzinfo is None:
        raise ValueError("lifecycle timestamps require a time zone")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def is_attempt_id(value: str) -> bool:
    """Return whether `value` is a well-formed attempt ID."""
    return bool(_ATTEMPT_ID_PATTERN.fullmatch(value))


def is_object_id(value: str) -> bool:
    """Return whether `value` is a well-formed object ID.

    The fixed form also guarantees an object ID is a safe directory name.
    """
    return bool(_OBJECT_ID_PATTERN.fullmatch(value))


def new_record(
    object_id: str,
    configuration: Configuration,
    *,
    at: datetime,
    kind: RunKind = RunKind.SCALAR,
    source_object_id: str | None = None,
) -> RunLifecycleRecord:
    """Return the record for a newly saved object: `planned` or `draft`.

    Args:
        object_id: The new object's identity.
        configuration: The first save's validation outcome.
        at: Creation time.
        kind: Scalar or batch execution.
        source_object_id: Object this one was cloned from, if any.
    """
    timestamp = format_timestamp(at)
    return RunLifecycleRecord(
        object_id=object_id,
        kind=kind,
        state=RunState.PLANNED if configuration.is_valid else RunState.DRAFT,
        revision=1,
        configuration_revision=1,
        configuration_digest=configuration.digest,
        scientific_run_id=configuration.scientific_run_id,
        validation_issues=configuration.issues,
        created_at=timestamp,
        updated_at=timestamp,
        source_object_id=source_object_id,
    )


def record_progress(
    record: RunLifecycleRecord,
    *,
    at: datetime,
    attempt_id: str,
    phase: RunPhase | None = None,
    committed_generation: int | None = None,
    checkpoint: CheckpointReference | None = None,
) -> RunLifecycleRecord:
    """Return the record after the owning attempt commits progress.

    Progress is not a transition: the state is unchanged and no history
    entry is added, but the revision advances so readers see the change.

    Args:
        record: The current record; it must be in an active state.
        at: Time of the commit; converted to UTC.
        attempt_id: The attempt committing; it must own execution.
        phase: Execution phase to record, if it changed.
        committed_generation: Last durably committed generation, if it
            advanced; it never moves backward.
        checkpoint: Newly published checkpoint, if any. Its generation is
            the committed generation unless one is given explicitly.

    Returns:
        A new record with the progress applied and revision + 1.

    Raises:
        AttemptMismatchError: `attempt_id` does not own execution, including
            when the run is no longer executing at all.
        ValueError: Progress moves backward or disagrees with the checkpoint.
    """
    if attempt_id != record.active_attempt_id:
        raise AttemptMismatchError(
            f"attempt {attempt_id!r} does not own run {record.object_id!r}"
        )
    generation = committed_generation
    if checkpoint is not None:
        if generation is None:
            generation = checkpoint.committed_generation
        elif generation != checkpoint.committed_generation:
            raise ValueError(
                "committed_generation disagrees with the checkpoint generation"
            )
    previous = record.committed_generation
    if generation is not None and previous is not None and generation < previous:
        raise ValueError(
            f"committed_generation moved backward: {previous} -> {generation}"
        )
    return replace(
        record,
        revision=record.revision + 1,
        updated_at=format_timestamp(at),
        phase=record.phase if phase is None else phase,
        committed_generation=previous if generation is None else generation,
        latest_checkpoint=(
            record.latest_checkpoint if checkpoint is None else checkpoint
        ),
    )


def _check_command_key(command_key: str) -> None:
    """Reject an idempotency key that is empty, long, or unprintable."""
    if not _COMMAND_KEY_PATTERN.fullmatch(command_key):
        raise ValueError(f"invalid command_key: {command_key!r}")


def _optional_int(name: str, value: object) -> int | None:
    """Return `value` as a non-negative int or `None`; reject anything else."""
    return None if value is None else _require_int(name, value, 0)


def _optional_str(name: str, value: object) -> str | None:
    """Return `value` as a string or `None`; reject anything else."""
    return None if value is None else _require_str(name, value)


def _parse_enum[T: StrEnum](enum: type[T], value: object, name: str) -> T:
    """Return `value` as a member of `enum`, or raise `LifecycleRecordError`."""
    try:
        return enum(_require_str(name, value))
    except ValueError as error:
        raise LifecycleRecordError(f"unknown {name}: {value!r}") from error


def _require_int(name: str, value: object, minimum: int) -> int:
    """Return `value` as an int no smaller than `minimum`."""
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise LifecycleRecordError(f"{name} must be an integer >= {minimum}")
    return value


def _require_list(name: str, value: object) -> list[object]:
    """Return `value` as a list, or raise `LifecycleRecordError`."""
    if not isinstance(value, list):
        raise LifecycleRecordError(f"{name} must be a list")
    return value


def _require_mapping(name: str, value: object) -> Mapping[str, object]:
    """Return `value` as a mapping, or raise `LifecycleRecordError`."""
    if not isinstance(value, Mapping):
        raise LifecycleRecordError(f"{name} must be an object")
    return value


def _require_str(name: str, value: object) -> str:
    """Return `value` as a non-empty string, or raise `LifecycleRecordError`."""
    if not isinstance(value, str) or not value:
        raise LifecycleRecordError(f"{name} must be a non-empty string")
    return value
