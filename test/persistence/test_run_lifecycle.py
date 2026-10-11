"""Tests for `fim.persistence.run_lifecycle`: states, transitions, records.

Every `(state, event)` pair is checked against the transition table, so a
change to the table is a deliberate, visible test change. Timestamps are
fixed; nothing here reads the clock.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from fim.persistence.run_lifecycle import (
    ACTIVE_STATES,
    CURRENT_RUN_LIFECYCLE_SCHEMA_VERSION,
    TERMINAL_STATES,
    TRANSITIONS,
    AttemptMismatchError,
    CheckpointReference,
    CommandKeyReuseError,
    Configuration,
    ErrorInfo,
    IllegalTransitionError,
    LifecycleRecordError,
    RevisionConflictError,
    RunEvent,
    RunKind,
    RunLifecycleRecord,
    RunPhase,
    RunState,
    ValidationIssue,
    apply_event,
    capabilities,
    find_command,
    format_timestamp,
    is_object_id,
    new_record,
    record_progress,
)

AT = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
OBJECT_ID = "obj-" + "0" * 32
ATTEMPT_A = "att-" + "a" * 32
ATTEMPT_B = "att-" + "b" * 32
VALID = Configuration.valid("d" * 64, "run-0123456789abcdef")
INVALID = Configuration.invalid((ValidationIssue("N must be positive", "N"),))

_EXPECTED: dict[tuple[RunState, RunEvent], RunState] = {
    (RunState.DRAFT, RunEvent.SAVE_VALID): RunState.PLANNED,
    (RunState.DRAFT, RunEvent.SAVE_INVALID): RunState.DRAFT,
    (RunState.PLANNED, RunEvent.SAVE_VALID): RunState.PLANNED,
    (RunState.PLANNED, RunEvent.SAVE_INVALID): RunState.DRAFT,
    (RunState.PLANNED, RunEvent.START): RunState.RUNNING,
    (RunState.PLANNED, RunEvent.ABANDON): RunState.CANCELLED,
    (RunState.RUNNING, RunEvent.REQUEST_PAUSE): RunState.PAUSING,
    (RunState.RUNNING, RunEvent.COMPLETE): RunState.COMPLETED,
    (RunState.RUNNING, RunEvent.OWNER_LOST): RunState.INTERRUPTED,
    (RunState.RUNNING, RunEvent.FAIL): RunState.FAILED,
    (RunState.RUNNING, RunEvent.REQUEST_CANCEL): RunState.CANCELLING,
    (RunState.PAUSING, RunEvent.COMMIT_PAUSE): RunState.PAUSED,
    (RunState.PAUSING, RunEvent.COMPLETE): RunState.COMPLETED,
    (RunState.PAUSING, RunEvent.OWNER_LOST): RunState.INTERRUPTED,
    (RunState.PAUSING, RunEvent.FAIL): RunState.FAILED,
    (RunState.PAUSING, RunEvent.REQUEST_CANCEL): RunState.CANCELLING,
    (RunState.PAUSED, RunEvent.RESUME): RunState.RUNNING,
    (RunState.PAUSED, RunEvent.ABANDON): RunState.CANCELLED,
    (RunState.INTERRUPTED, RunEvent.RESUME): RunState.RUNNING,
    (RunState.INTERRUPTED, RunEvent.ABANDON): RunState.CANCELLED,
    (RunState.FAILED, RunEvent.RESUME): RunState.RUNNING,
    (RunState.FAILED, RunEvent.ABANDON): RunState.CANCELLED,
    (RunState.CANCELLING, RunEvent.COMMIT_CANCEL): RunState.CANCELLED,
    (RunState.CANCELLING, RunEvent.COMPLETE): RunState.COMPLETED,
    (RunState.CANCELLING, RunEvent.OWNER_LOST): RunState.INTERRUPTED,
    (RunState.CANCELLING, RunEvent.FAIL): RunState.FAILED,
}


def _record_in(state: RunState) -> RunLifecycleRecord:
    """Return a minimal consistent record in `state`."""
    configuration = INVALID if state is RunState.DRAFT else VALID
    record = new_record(OBJECT_ID, configuration, at=AT)
    if state in {RunState.DRAFT, RunState.PLANNED}:
        return record
    started = format_timestamp(AT)
    return replace(
        record,
        state=state,
        first_started_at=started,
        phase=RunPhase.SIMULATING,
        active_attempt_id=ATTEMPT_A if state in ACTIVE_STATES else None,
        completed_at=started if state is RunState.COMPLETED else None,
    )


def _apply(record: RunLifecycleRecord, event: RunEvent) -> RunLifecycleRecord:
    """Apply `event` with the arguments it needs and no others."""
    configuration = {RunEvent.SAVE_VALID: VALID, RunEvent.SAVE_INVALID: INVALID}
    opens = event in {RunEvent.START, RunEvent.RESUME}
    return apply_event(
        record,
        event,
        at=AT,
        expected_revision=record.revision,
        attempt_id=ATTEMPT_B if opens else None,
        configuration=configuration.get(event),
        error=(
            ErrorInfo("engine", "boom", RunPhase.SIMULATING)
            if event is RunEvent.FAIL
            else None
        ),
    )


def test_transition_table_matches_the_design() -> None:
    """The table holds exactly the designed transitions, draft included."""
    assert dict(TRANSITIONS) == _EXPECTED


@pytest.mark.parametrize(
    ("state", "event"), list(itertools.product(RunState, RunEvent))
)
def test_every_state_event_pair(state: RunState, event: RunEvent) -> None:
    """Legal pairs reach their target; every other pair is rejected."""
    record = _record_in(state)
    target = _EXPECTED.get((state, event))
    if target is None:
        with pytest.raises(IllegalTransitionError) as raised:
            _apply(record, event)
        assert raised.value.state is state
        assert raised.value.event is event
        assert raised.value.revision == record.revision
        return
    result = _apply(record, event)
    assert result.state is target
    assert result.revision == record.revision + 1
    assert result.transition_history[-1].from_state is state
    assert result.transition_history[-1].to_state is target
    assert (result.active_attempt_id is not None) == (target in ACTIVE_STATES)


def test_terminal_states_have_no_exits() -> None:
    """No event leaves `completed` or `cancelled`."""
    for state, event in TRANSITIONS:
        assert state not in TERMINAL_STATES, (state, event)


def test_invalid_first_save_creates_a_draft_with_its_issues() -> None:
    """An invalid configuration persists as a draft, not as an error."""
    record = new_record(OBJECT_ID, INVALID, at=AT)
    assert record.state is RunState.DRAFT
    assert record.is_draft
    assert record.validation_issues == INVALID.issues
    assert record.configuration_digest is None
    assert record.scientific_run_id is None


def test_draft_and_planned_reclassify_on_each_save() -> None:
    """Saves move back and forth by validity; the object ID never changes."""
    record = new_record(OBJECT_ID, INVALID, at=AT)
    record = _apply(record, RunEvent.SAVE_VALID)
    assert record.state is RunState.PLANNED
    assert record.validation_issues == ()
    assert record.scientific_run_id == VALID.scientific_run_id
    record = _apply(record, RunEvent.SAVE_INVALID)
    assert record.state is RunState.DRAFT
    assert record.scientific_run_id is None
    assert record.configuration_revision == 3
    assert record.object_id == OBJECT_ID


def test_save_event_must_match_its_configuration() -> None:
    """A valid configuration cannot be saved as invalid, or vice versa."""
    record = new_record(OBJECT_ID, VALID, at=AT)
    with pytest.raises(ValueError, match="does not match"):
        apply_event(record, RunEvent.SAVE_INVALID, at=AT, configuration=VALID)
    with pytest.raises(ValueError, match="requires"):
        apply_event(record, RunEvent.SAVE_VALID, at=AT)


def test_draft_cannot_start() -> None:
    """Start on a draft is an illegal transition naming the draft state."""
    with pytest.raises(IllegalTransitionError, match="'draft'"):
        _apply(new_record(OBJECT_ID, INVALID, at=AT), RunEvent.START)


def test_configuration_is_fixed_once_execution_starts() -> None:
    """A started run accepts no save of either kind."""
    for state in RunState:
        if state in {RunState.DRAFT, RunState.PLANNED}:
            continue
        for event in (RunEvent.SAVE_VALID, RunEvent.SAVE_INVALID):
            with pytest.raises(IllegalTransitionError):
                _apply(_record_in(state), event)


def test_stale_revision_is_rejected() -> None:
    """A request based on an older revision fails before any change."""
    record = new_record(OBJECT_ID, VALID, at=AT)
    with pytest.raises(RevisionConflictError) as raised:
        apply_event(
            record, RunEvent.START, at=AT, expected_revision=0, attempt_id=ATTEMPT_A
        )
    assert (raised.value.expected, raised.value.actual) == (0, 1)


def test_start_requires_a_well_formed_attempt() -> None:
    """Start without a valid new attempt ID is rejected."""
    record = new_record(OBJECT_ID, VALID, at=AT)
    with pytest.raises(AttemptMismatchError):
        apply_event(record, RunEvent.START, at=AT)
    with pytest.raises(AttemptMismatchError):
        apply_event(record, RunEvent.START, at=AT, attempt_id="att-x")


def test_stale_attempt_cannot_report() -> None:
    """A worker event from a superseded attempt is fenced out."""
    record = _record_in(RunState.RUNNING)
    with pytest.raises(AttemptMismatchError):
        apply_event(record, RunEvent.COMPLETE, at=AT, attempt_id=ATTEMPT_B)
    done = apply_event(record, RunEvent.COMPLETE, at=AT, attempt_id=ATTEMPT_A)
    assert done.state is RunState.COMPLETED
    assert done.completed_at == format_timestamp(AT)
    assert done.active_attempt_id is None


def test_completion_wins_over_a_pending_cancel() -> None:
    """Completion committed during cancelling yields completed."""
    record = _apply(_record_in(RunState.RUNNING), RunEvent.REQUEST_CANCEL)
    assert record.state is RunState.CANCELLING
    assert _apply(record, RunEvent.COMPLETE).state is RunState.COMPLETED
    with pytest.raises(IllegalTransitionError):
        _apply(_apply(record, RunEvent.COMPLETE), RunEvent.COMMIT_CANCEL)


def test_resume_opens_a_new_attempt_and_clears_the_error() -> None:
    """Resume from failed records the new attempt and keeps first start."""
    failed = _apply(_record_in(RunState.RUNNING), RunEvent.FAIL)
    assert failed.last_error is not None
    resumed = _apply(failed, RunEvent.RESUME)
    assert resumed.active_attempt_id == ATTEMPT_B
    assert resumed.last_error is None
    assert resumed.first_started_at == failed.first_started_at


def test_record_round_trips_through_json() -> None:
    """A record with every field set survives JSON exactly."""
    record = replace(
        _apply(_record_in(RunState.RUNNING), RunEvent.REQUEST_PAUSE),
        committed_generation=42,
        latest_checkpoint=CheckpointReference("ckpt-000042", "e" * 64, 42),
        kind=RunKind.BATCH,
        source_object_id="obj-" + "1" * 32,
    )
    text = json.dumps(record.to_dict(), sort_keys=True)
    assert RunLifecycleRecord.from_dict(json.loads(text)) == record


def test_draft_round_trips_with_its_issues() -> None:
    """A draft's validation issues are preserved exactly."""
    record = new_record(OBJECT_ID, INVALID, at=AT)
    assert RunLifecycleRecord.from_dict(record.to_dict()) == record


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("schema_version", 99, "schema_version"),
        ("state", "archived", "unknown state"),
        ("revision", 0, "revision"),
        ("revision", True, "revision"),
        ("object_id", "../etc", "object_id"),
        ("validation_issues", "nope", "validation_issues"),
        ("scientific_run_id", None, "both"),
    ],
)
def test_malformed_record_is_rejected(key: str, value: object, message: str) -> None:
    """Every malformed or inconsistent document raises a record error."""
    document = new_record(OBJECT_ID, VALID, at=AT).to_dict()
    document[key] = value
    with pytest.raises(LifecycleRecordError, match=message):
        RunLifecycleRecord.from_dict(document)


def test_inconsistent_records_cannot_be_built() -> None:
    """Cross-field invariants hold for every constructed record."""
    planned = new_record(OBJECT_ID, VALID, at=AT)
    with pytest.raises(LifecycleRecordError, match="active attempt"):
        replace(planned, state=RunState.RUNNING)
    with pytest.raises(LifecycleRecordError, match="draft"):
        replace(planned, state=RunState.DRAFT)
    with pytest.raises(LifecycleRecordError, match="never started"):
        replace(planned, committed_generation=3)


def test_configuration_is_exactly_one_form() -> None:
    """A configuration outcome is valid or invalid, never both or neither."""
    with pytest.raises(ValueError, match="never both"):
        Configuration(digest=None, scientific_run_id=None)
    with pytest.raises(ValueError, match="never both"):
        Configuration("d", "run-x", (ValidationIssue("bad"),))
    with pytest.raises(ValueError, match="never both"):
        Configuration("d", None)


def test_capabilities_for_draft_explain_why_start_is_blocked() -> None:
    """A draft is editable and deletable, never startable or abandonable."""
    result = capabilities(new_record(OBJECT_ID, INVALID, at=AT))
    assert result.can_edit and result.can_delete
    assert not result.can_start and not result.can_abandon
    assert "N must be positive" in result.reasons["can_start"]
    assert "delete" in result.reasons["can_abandon"]


def test_capabilities_by_state() -> None:
    """Each state allows exactly its designed actions."""
    planned = capabilities(_record_in(RunState.PLANNED))
    assert planned.can_start and planned.can_edit and planned.can_abandon
    running = capabilities(_record_in(RunState.RUNNING), pause_supported=True)
    assert running.can_pause and running.can_cancel
    assert not running.can_delete and not running.can_edit
    unsupported = capabilities(_record_in(RunState.RUNNING))
    assert not unsupported.can_pause
    assert "phase" in unsupported.reasons["can_pause"]
    paused = capabilities(_record_in(RunState.PAUSED))
    assert not paused.can_resume and "checkpoint" in paused.reasons["can_resume"]
    assert capabilities(_record_in(RunState.PAUSED), resume_verified=True).can_resume
    completed = capabilities(_record_in(RunState.COMPLETED))
    assert completed.can_delete and not completed.can_resume


def test_read_only_objects_allow_nothing() -> None:
    """A protected example refuses every action, with a reason for each."""
    result = capabilities(_record_in(RunState.COMPLETED), read_only=True)
    document = result.to_dict()
    assert not any(value for key, value in document.items() if key != "reasons")
    assert len(document["reasons"]) == 7


def test_timestamps_are_utc_and_require_a_zone() -> None:
    """Timestamps normalize to UTC `Z` form; naive times are refused."""
    eastern = datetime(2026, 10, 11, 8, 0, tzinfo=timezone(timedelta(hours=-4)))
    assert format_timestamp(eastern) == "2026-10-11T12:00:00Z"
    with pytest.raises(ValueError, match="time zone"):
        format_timestamp(AT.replace(tzinfo=None))


def test_object_ids_are_safe_directory_names() -> None:
    """Only the fixed `obj-<32 hex>` form is an object ID."""
    assert is_object_id(OBJECT_ID)
    for bad in ("obj-", "obj-" + "G" * 32, "../obj", OBJECT_ID + "/x"):
        assert not is_object_id(bad)


def test_schema_version_is_one() -> None:
    """The first lifecycle schema is version 1."""
    assert CURRENT_RUN_LIFECYCLE_SCHEMA_VERSION == 1


def test_command_key_is_recorded_and_found() -> None:
    """A keyed transition can be found again by its key."""
    record = apply_event(
        _record_in(RunState.PLANNED), RunEvent.ABANDON, at=AT, command_key="k-1"
    )
    original = find_command(record, "k-1")
    assert original is not None
    assert original.event is RunEvent.ABANDON
    assert find_command(record, "k-2") is None
    assert RunLifecycleRecord.from_dict(record.to_dict()) == record


def test_command_key_cannot_apply_twice() -> None:
    """A key that already produced a transition is refused."""
    record = apply_event(
        _record_in(RunState.RUNNING),
        RunEvent.REQUEST_PAUSE,
        at=AT,
        command_key="k-1",
    )
    with pytest.raises(CommandKeyReuseError):
        apply_event(record, RunEvent.REQUEST_CANCEL, at=AT, command_key="k-1")


@pytest.mark.parametrize("key", ["", "has space", "x" * 129, "slash/no"])
def test_malformed_command_key_is_refused(key: str) -> None:
    """Keys are short printable tokens."""
    with pytest.raises(ValueError, match="command_key"):
        apply_event(
            _record_in(RunState.PLANNED), RunEvent.ABANDON, at=AT, command_key=key
        )


def test_progress_advances_revision_without_a_transition() -> None:
    """Progress bumps the revision and keeps the state and history."""
    record = _record_in(RunState.RUNNING)
    checkpoint = CheckpointReference("ckpt-5", "e" * 64, 5)
    updated = record_progress(
        record, at=AT, attempt_id=ATTEMPT_A, checkpoint=checkpoint
    )
    assert updated.state is RunState.RUNNING
    assert updated.revision == record.revision + 1
    assert updated.transition_history == record.transition_history
    assert updated.committed_generation == 5
    assert updated.latest_checkpoint == checkpoint


def test_progress_from_a_stale_or_absent_attempt_is_fenced() -> None:
    """Only the owning attempt of an active run may commit progress."""
    with pytest.raises(AttemptMismatchError):
        record_progress(_record_in(RunState.RUNNING), at=AT, attempt_id=ATTEMPT_B)
    with pytest.raises(AttemptMismatchError):
        record_progress(_record_in(RunState.PAUSED), at=AT, attempt_id=ATTEMPT_A)


def test_progress_never_moves_backward() -> None:
    """Committed generation is monotonic and agrees with the checkpoint."""
    record = record_progress(
        _record_in(RunState.RUNNING),
        at=AT,
        attempt_id=ATTEMPT_A,
        committed_generation=8,
    )
    with pytest.raises(ValueError, match="backward"):
        record_progress(record, at=AT, attempt_id=ATTEMPT_A, committed_generation=7)
    with pytest.raises(ValueError, match="disagrees"):
        record_progress(
            record,
            at=AT,
            attempt_id=ATTEMPT_A,
            committed_generation=9,
            checkpoint=CheckpointReference("ckpt-10", "e" * 64, 10),
        )
