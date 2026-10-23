"""Tests for execution ownership and fenced commits (`run_ownership`).

Two opens of the same lock file contend even within one process, so most
ownership races are exercised in-process; one test uses a real second
process to prove that a dead owner is detected without any timeout.
"""

from __future__ import annotations

import itertools
import json
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import psutil
import pytest
import yaml

from fim.examples import classes
from fim.persistence import run_ownership
from fim.persistence.object_lock import exclusive_lock
from fim.persistence.run_lifecycle import (
    AttemptMismatchError,
    CheckpointReference,
    CommandKeyReuseError,
    IllegalTransitionError,
    LifecycleError,
    RevisionConflictError,
    RunEvent,
    RunPhase,
    RunState,
)
from fim.persistence.run_objects import (
    CONFIG_FILE_NAME,
    RunObject,
    create_run_object,
    delete_run_object,
    read_run_object,
)
from fim.persistence.run_ownership import (
    KILLED_MESSAGE,
    AttemptOwner,
    ConfigurationDriftError,
    ExecutionLease,
    OwnershipConflictError,
    ResumeUnavailableError,
    abandon_run_object,
    attempt_lock_path,
    attempt_owner_path,
    kill_run_object,
    read_attempt_owner,
    reconcile_run_object,
    reconcile_run_objects,
    record_worker_failure,
    request_cancel,
    request_pause,
    resume_run_object,
    start_run_object,
)

_CLASSES_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "classes.yaml"
AT = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
BACKSTOP_SECONDS = 120.0
CHECKPOINT = CheckpointReference(
    checkpoint_id="ckpt-000010", digest="0" * 64, committed_generation=10
)

_VALID: dict[str, object] = {
    "N": 20,
    "ploidy": "haploid",
    "d": 2,
    "m": 0.1,
    "mu": 0.01,
    "seed": 20261005,
    "precision": 1.0,
    "max_generations": 10,
    "n_replicates": 1,
    "stop_batch_early": False,
}


@pytest.fixture(autouse=True)
def _fixture_class_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check every `class` against the test fixture, never the shipped file."""
    monkeypatch.setattr(classes, "default_classes_path", lambda: _CLASSES_FIXTURE)


def _clock() -> datetime:
    """Return the fixed test time."""
    return AT


def _attempts() -> Callable[[], str]:
    """Return a deterministic attempt ID factory."""
    counter = itertools.count(1)
    return lambda: f"att-{next(counter):032x}"


def _planned(tmp_path: Path, **updates: object) -> RunObject:
    """Create a planned object in `tmp_path`."""
    text = yaml.safe_dump({**_VALID, **updates}, sort_keys=False)
    return create_run_object(
        text, results=tmp_path, clock=_clock, id_factory=lambda: "obj-" + "1" * 32
    )


def _start(tmp_path: Path, run_object: RunObject, **kwargs: object) -> ExecutionLease:
    """Start `run_object` and return its lease."""
    result = start_run_object(
        run_object.object_id,
        expected_revision=run_object.record.revision,
        results=tmp_path,
        clock=_clock,
        attempt_factory=_attempts(),
        **kwargs,  # type: ignore[arg-type]
    )
    assert result.lease is not None
    return result.lease


def test_start_opens_an_attempt_and_holds_its_lock(tmp_path: Path) -> None:
    """Starting records the attempt and holds the attempt lock."""
    planned = _planned(tmp_path)
    lease = _start(tmp_path, planned)
    stored = read_run_object(planned.object_id, results=tmp_path).record
    assert stored.state is RunState.RUNNING
    assert stored.active_attempt_id == lease.attempt_id
    assert stored.first_started_at is not None
    lease.release()


def test_worker_exit_is_recorded_as_failed_after_ownership_ends(
    tmp_path: Path,
) -> None:
    """A dead child is failed, not merely marked as an ownerless run."""
    planned = _planned(tmp_path)
    lease = _start(tmp_path, planned)
    lease.release()

    failed = record_worker_failure(
        planned.object_id, "worker process exited with code 7", results=tmp_path
    )

    assert failed.record.state is RunState.FAILED
    assert failed.record.last_error is not None
    assert failed.record.last_error.category == "worker"
    assert failed.record.last_error.message == "worker process exited with code 7"
    assert failed.record.last_error.attempt_id == lease.attempt_id


def test_worker_failure_cannot_be_recorded_while_owner_holds_lock(
    tmp_path: Path,
) -> None:
    """A supervisor cannot fail an attempt that still has a live owner."""
    planned = _planned(tmp_path)
    lease = _start(tmp_path, planned)

    with pytest.raises(OwnershipConflictError):
        record_worker_failure(planned.object_id, "unexpected exit", results=tmp_path)

    lease.release()


def test_a_draft_cannot_start(tmp_path: Path) -> None:
    """Starting a draft fails and leaves no owner behind."""
    draft = create_run_object(
        "N: [\n", results=tmp_path, clock=_clock, id_factory=lambda: "obj-" + "2" * 32
    )
    with pytest.raises(IllegalTransitionError):
        start_run_object(draft.object_id, expected_revision=1, results=tmp_path)
    with exclusive_lock(attempt_lock_path(draft.object_id, tmp_path), blocking=False):
        pass


def test_start_refuses_a_configuration_edited_behind_the_record(
    tmp_path: Path,
) -> None:
    """A `config.yaml` that no longer matches the digest cannot start."""
    planned = _planned(tmp_path)
    (planned.directory / CONFIG_FILE_NAME).write_text(
        yaml.safe_dump({**_VALID, "mu": 0.5}), encoding="utf-8"
    )
    with pytest.raises(ConfigurationDriftError):
        start_run_object(planned.object_id, expected_revision=1, results=tmp_path)
    assert read_run_object(planned.object_id, results=tmp_path).record.state is (
        RunState.PLANNED
    )


def test_second_start_finds_the_run_already_running(tmp_path: Path) -> None:
    """Two starts from the same revision yield exactly one owner."""
    planned = _planned(tmp_path)
    lease = _start(tmp_path, planned)
    with pytest.raises(RevisionConflictError):
        start_run_object(planned.object_id, expected_revision=1, results=tmp_path)
    lease.release()


def test_a_held_attempt_lock_refuses_a_new_owner(tmp_path: Path) -> None:
    """A live attempt lock refuses another owner regardless of the record."""
    planned = _planned(tmp_path)
    with (
        exclusive_lock(attempt_lock_path(planned.object_id, tmp_path)),
        pytest.raises(OwnershipConflictError),
    ):
        start_run_object(planned.object_id, expected_revision=1, results=tmp_path)


def test_progress_and_completion_commit_through_the_lease(tmp_path: Path) -> None:
    """Progress advances revision without history; completion releases."""
    planned = _planned(tmp_path)
    with _start(tmp_path, planned) as lease:
        before = lease.current().record
        after = lease.commit_progress(
            phase=RunPhase.SIMULATING, checkpoint=CHECKPOINT
        ).record
        assert after.revision == before.revision + 1
        assert after.transition_history == before.transition_history
        assert after.committed_generation == 10
        done = lease.finish(RunEvent.COMPLETE, phase=RunPhase.FINALIZING)
    assert done.record.state is RunState.COMPLETED
    assert not lease.is_held
    with exclusive_lock(attempt_lock_path(planned.object_id, tmp_path), blocking=False):
        pass


def test_an_escaping_exception_records_failure(tmp_path: Path) -> None:
    """Leaving the lease by an exception records `failed` with the error."""
    planned = _planned(tmp_path)
    with pytest.raises(RuntimeError), _start(tmp_path, planned):
        raise RuntimeError("engine exploded")
    record = read_run_object(planned.object_id, results=tmp_path).record
    assert record.state is RunState.FAILED
    assert record.last_error is not None
    assert "engine exploded" in record.last_error.message


def test_pause_is_requested_then_acknowledged_by_the_owner(tmp_path: Path) -> None:
    """The controller requests; the owner checkpoints and acknowledges."""
    planned = _planned(tmp_path)
    lease = _start(tmp_path, planned)
    running = lease.current().record
    request_pause(
        planned.object_id, expected_revision=running.revision, results=tmp_path
    )
    assert lease.current().record.state is RunState.PAUSING
    lease.commit_progress(checkpoint=CHECKPOINT)
    paused = lease.finish(RunEvent.COMMIT_PAUSE)
    assert paused.record.state is RunState.PAUSED
    assert paused.record.active_attempt_id is None


def test_resume_needs_a_committed_checkpoint(tmp_path: Path) -> None:
    """A run that stopped before any checkpoint cannot resume."""
    planned = _planned(tmp_path)
    lease = _start(tmp_path, planned)
    lease.release()
    interrupted = reconcile_run_object(planned.object_id, results=tmp_path)
    with pytest.raises(ResumeUnavailableError):
        resume_run_object(
            planned.object_id,
            expected_revision=interrupted.record.revision,
            results=tmp_path,
        )
    assert read_run_object(planned.object_id, results=tmp_path).record.state is (
        RunState.INTERRUPTED
    )


def test_stale_attempt_is_fenced_after_recovery_and_resume(tmp_path: Path) -> None:
    """After owner loss and resume, the old attempt cannot publish."""
    planned = _planned(tmp_path)
    stale = _start(tmp_path, planned)
    stale.commit_progress(checkpoint=CHECKPOINT)
    # Simulate a lost owner whose process is somehow still alive.
    stale_lock = stale._lock
    stale._lock = None
    assert stale_lock is not None
    stale_lock.close()
    interrupted = reconcile_run_object(planned.object_id, results=tmp_path)
    assert interrupted.record.state is RunState.INTERRUPTED
    resumed = resume_run_object(
        planned.object_id,
        expected_revision=interrupted.record.revision,
        results=tmp_path,
        attempt_factory=lambda: "att-" + "f" * 32,
    )
    assert resumed.lease is not None
    stale._lock = stale_lock  # pretend it still holds a lease
    with pytest.raises(AttemptMismatchError):
        stale.commit_progress(committed_generation=20)
    with pytest.raises(AttemptMismatchError):
        stale.finish(RunEvent.COMPLETE)
    record = read_run_object(planned.object_id, results=tmp_path).record
    assert record.state is RunState.RUNNING
    assert record.active_attempt_id == "att-" + "f" * 32
    resumed.lease.release()


def test_reconcile_leaves_a_live_owner_alone(tmp_path: Path) -> None:
    """A held attempt lock proves life; reconciliation changes nothing."""
    planned = _planned(tmp_path)
    lease = _start(tmp_path, planned)
    assert reconcile_run_objects(tmp_path) == []
    lease.release()
    changed = reconcile_run_objects(tmp_path, clock=_clock)
    assert [item.object_id for item in changed] == [planned.object_id]


def _receipt(monkeypatch: pytest.MonkeyPatch, problem: str | None) -> list[str]:
    """Make every receipt check return `problem`; return the checked run IDs."""
    checked: list[str] = []

    def check(_directory: Path, *, kind: object, run_id: str) -> str | None:
        checked.append(run_id)
        return problem

    monkeypatch.setattr(run_ownership, "receipt_problem", check)
    return checked


def _stranded_in_finalization(tmp_path: Path) -> RunObject:
    """Start a run, reach finalization, and lose the owner."""
    planned = _planned(tmp_path)
    lease = _start(tmp_path, planned)
    lease.commit_progress(phase=RunPhase.FINALIZING, checkpoint=CHECKPOINT)
    lease.release()
    return planned


def test_reconcile_recovers_a_verified_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lost owner whose receipt verifies is completed, not interrupted."""
    checked = _receipt(monkeypatch, None)
    planned = _stranded_in_finalization(tmp_path)
    run_id = planned.record.scientific_run_id

    changed = reconcile_run_objects(tmp_path, clock=_clock)

    assert [item.object_id for item in changed] == [planned.object_id]
    record = changed[0].record
    assert record.state is RunState.COMPLETED
    assert record.transition_history[-1].event is RunEvent.RECOVER_COMPLETION
    assert record.active_attempt_id is None
    assert checked == [run_id]


def test_reconcile_interrupts_when_the_receipt_does_not_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unverified receipt changes nothing about owner loss."""
    _receipt(monkeypatch, "artifact report.json is missing")
    planned = _stranded_in_finalization(tmp_path)

    record = reconcile_run_object(planned.object_id, results=tmp_path).record

    assert record.state is RunState.INTERRUPTED
    assert reconcile_run_objects(tmp_path) == []


def test_reconcile_checks_no_receipt_before_finalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run lost while simulating cannot have finished."""
    checked = _receipt(monkeypatch, None)
    planned = _planned(tmp_path)
    lease = _start(tmp_path, planned)
    lease.commit_progress(phase=RunPhase.SIMULATING, checkpoint=CHECKPOINT)
    lease.release()

    record = reconcile_run_object(planned.object_id, results=tmp_path).record

    assert record.state is RunState.INTERRUPTED
    assert checked == []


@pytest.mark.parametrize("stop", ["interrupt", "fail"])
def test_reconcile_recovers_a_stopped_finalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stop: str
) -> None:
    """An interrupted or failed finalization whose receipt verifies completes."""
    _receipt(monkeypatch, "not yet")
    planned = _planned(tmp_path)
    if stop == "fail":
        with pytest.raises(OSError), _start(tmp_path, planned) as lease:
            lease.commit_progress(phase=RunPhase.FINALIZING, checkpoint=CHECKPOINT)
            raise OSError("disk full")
    else:
        lease = _start(tmp_path, planned)
        lease.commit_progress(phase=RunPhase.FINALIZING, checkpoint=CHECKPOINT)
        lease.release()
        reconcile_run_object(planned.object_id, results=tmp_path)
    stopped = read_run_object(planned.object_id, results=tmp_path).record
    assert stopped.state in {RunState.INTERRUPTED, RunState.FAILED}
    _receipt(monkeypatch, None)

    record = reconcile_run_object(planned.object_id, results=tmp_path).record

    assert record.state is RunState.COMPLETED
    assert record.last_error is None


def test_reconcile_leaves_a_live_finalization_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker still publishing is never completed behind its back."""
    checked = _receipt(monkeypatch, None)
    planned = _planned(tmp_path)
    lease = _start(tmp_path, planned)
    lease.commit_progress(phase=RunPhase.FINALIZING, checkpoint=CHECKPOINT)

    assert reconcile_run_objects(tmp_path) == []

    assert checked == []
    assert lease.finish(RunEvent.COMPLETE).record.state is RunState.COMPLETED


def test_the_owner_can_request_its_own_pause(tmp_path: Path) -> None:
    """`request_pause` on the lease pauses a running run and nothing else."""
    planned = _planned(tmp_path)
    lease = _start(tmp_path, planned)
    pausing = lease.request_pause().record
    assert pausing.state is RunState.PAUSING
    assert lease.request_pause().record.revision == pausing.revision
    lease.commit_progress(checkpoint=CHECKPOINT)
    assert lease.finish(RunEvent.COMMIT_PAUSE).record.state is RunState.PAUSED


def test_a_stale_owner_cannot_request_a_pause(tmp_path: Path) -> None:
    """A pause request from a fenced attempt is refused."""
    planned = _planned(tmp_path)
    stale = _start(tmp_path, planned)
    stale.commit_progress(checkpoint=CHECKPOINT)
    stale_lock = stale._lock
    stale._lock = None
    assert stale_lock is not None
    stale_lock.close()
    interrupted = reconcile_run_object(planned.object_id, results=tmp_path)
    resumed = resume_run_object(
        planned.object_id,
        expected_revision=interrupted.record.revision,
        results=tmp_path,
        attempt_factory=lambda: "att-" + "f" * 32,
    )
    assert resumed.lease is not None
    stale._lock = stale_lock
    with pytest.raises(AttemptMismatchError):
        stale.request_pause()
    assert read_run_object(planned.object_id, results=tmp_path).record.state is (
        RunState.RUNNING
    )
    resumed.lease.release()


def test_cancel_wins_until_the_owner_completes(tmp_path: Path) -> None:
    """A committed completion stands; a later cancel request is illegal."""
    planned = _planned(tmp_path)
    lease = _start(tmp_path, planned)
    lease.finish(RunEvent.COMPLETE)
    record = read_run_object(planned.object_id, results=tmp_path).record
    with pytest.raises(IllegalTransitionError):
        request_cancel(
            planned.object_id, expected_revision=record.revision, results=tmp_path
        )


def test_cancel_request_is_acknowledged_as_cancelled(tmp_path: Path) -> None:
    """Cancelling then the owner's acknowledgment yields `cancelled`."""
    planned = _planned(tmp_path)
    lease = _start(tmp_path, planned)
    request_cancel(
        planned.object_id,
        expected_revision=lease.current().record.revision,
        results=tmp_path,
    )
    cancelled = lease.finish(RunEvent.COMMIT_CANCEL)
    assert cancelled.record.state is RunState.CANCELLED


def test_active_run_cannot_be_deleted(tmp_path: Path) -> None:
    """Deletion is refused while an attempt owns execution."""
    planned = _planned(tmp_path)
    lease = _start(tmp_path, planned)
    with pytest.raises(IllegalTransitionError):
        delete_run_object(
            planned.object_id,
            expected_revision=lease.current().record.revision,
            results=tmp_path,
        )
    lease.release()


def test_repeated_command_key_returns_the_original_outcome(tmp_path: Path) -> None:
    """A retried request with the same key changes nothing and says so."""
    planned = _planned(tmp_path)
    first = abandon_run_object(
        planned.object_id, expected_revision=1, results=tmp_path, command_key="k-1"
    )
    again = abandon_run_object(
        planned.object_id, expected_revision=1, results=tmp_path, command_key="k-1"
    )
    assert not first.replayed
    assert again.replayed
    assert again.run_object.record == first.run_object.record


def test_repeated_start_key_replays_without_a_second_attempt(tmp_path: Path) -> None:
    """A retried start returns the original outcome and no new lease."""
    planned = _planned(tmp_path)
    lease = _start(tmp_path, planned, command_key="start-1")
    retry = start_run_object(
        planned.object_id,
        expected_revision=1,
        results=tmp_path,
        command_key="start-1",
    )
    assert retry.replayed
    assert retry.lease is None
    assert retry.run_object.record.active_attempt_id == lease.attempt_id
    lease.release()


def test_reusing_a_key_for_another_command_is_refused(tmp_path: Path) -> None:
    """One key names one command; reusing it for another is an error."""
    planned = _planned(tmp_path)
    lease = _start(tmp_path, planned, command_key="k-2")
    with pytest.raises(CommandKeyReuseError):
        request_pause(
            planned.object_id,
            expected_revision=lease.current().record.revision,
            results=tmp_path,
            command_key="k-2",
        )
    lease.release()


_OWNER_PROGRAM = """
import sys
from pathlib import Path
from fim.persistence.run_ownership import start_run_object

result = start_run_object(
    sys.argv[1], expected_revision=1, results=Path(sys.argv[2])
)
print("owning", flush=True)
sys.stdin.read()
"""


def test_a_dead_owner_process_is_detected_without_a_timeout(tmp_path: Path) -> None:
    """A second process owns the run; once it dies, recovery interrupts it."""
    planned = _planned(tmp_path)
    owner = subprocess.Popen(
        [sys.executable, "-c", _OWNER_PROGRAM, planned.object_id, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert owner.stdout is not None
        assert owner.stdout.readline().strip() == "owning", owner.stderr
        alive = reconcile_run_object(planned.object_id, results=tmp_path)
        assert alive.record.state is RunState.RUNNING
        with pytest.raises(IllegalTransitionError):
            start_run_object(
                planned.object_id,
                expected_revision=alive.record.revision,
                results=tmp_path,
            )
    finally:
        owner.kill()
        owner.wait(timeout=BACKSTOP_SECONDS)
    lost = reconcile_run_object(planned.object_id, results=tmp_path)
    assert lost.record.state is RunState.INTERRUPTED
    assert lost.record.active_attempt_id is None


def test_start_records_this_process_as_the_attempt_owner(tmp_path: Path) -> None:
    """The owner record names the attempt, this PID, and its start time."""
    planned = _planned(tmp_path)
    lease = _start(tmp_path, planned)
    owner = read_attempt_owner(planned.object_id, tmp_path)
    assert owner is not None
    assert owner.attempt_id == lease.attempt_id
    assert owner.pid == os.getpid()
    assert owner.started_at == psutil.Process().create_time()
    lease.release()


def test_kill_stops_a_live_owner_process_and_records_failed(tmp_path: Path) -> None:
    """A real owner in its own session is killed, then recorded as failed."""
    planned = _planned(tmp_path)
    owner = subprocess.Popen(
        [sys.executable, "-c", _OWNER_PROGRAM, planned.object_id, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        assert owner.stdout is not None
        assert owner.stdout.readline().strip() == "owning", owner.stderr
        recorded = read_attempt_owner(planned.object_id, tmp_path)
        assert recorded is not None
        assert (recorded.pid, recorded.process_group) == (owner.pid, owner.pid)
        killed = kill_run_object(
            planned.object_id, results=tmp_path, timeout=BACKSTOP_SECONDS
        )
        assert owner.wait(timeout=BACKSTOP_SECONDS) == -signal.SIGKILL
    finally:
        owner.kill()
        owner.wait(timeout=BACKSTOP_SECONDS)
    assert killed.record.state is RunState.FAILED
    error = killed.record.last_error
    assert error is not None
    assert (error.category, error.message) == ("killed", KILLED_MESSAGE)
    owner_record = attempt_owner_path(planned.object_id, tmp_path)
    assert not owner_record.exists()
    attempt_lock = attempt_lock_path(planned.object_id, tmp_path)
    assert attempt_lock.exists()
    delete_run_object(
        planned.object_id, expected_revision=killed.record.revision, results=tmp_path
    )
    assert not attempt_lock.exists()


def test_kill_refuses_a_run_that_is_not_executing(tmp_path: Path) -> None:
    """Only a running, pausing, or cancelling run can be killed."""
    planned = _planned(tmp_path)
    with pytest.raises(LifecycleError, match="not executing"):
        kill_run_object(planned.object_id, results=tmp_path)


def test_kill_records_failed_when_the_owner_is_already_gone(tmp_path: Path) -> None:
    """A run whose owner released its lock is failed without signaling."""
    planned = _planned(tmp_path)
    _start(tmp_path, planned).release()
    killed = kill_run_object(planned.object_id, results=tmp_path, clock=_clock)
    assert killed.record.state is RunState.FAILED


def test_kill_refuses_a_live_owner_it_cannot_identify(tmp_path: Path) -> None:
    """Without a matching owner record, nothing is signaled or recorded."""
    planned = _planned(tmp_path)
    lease = _start(tmp_path, planned)
    attempt_owner_path(planned.object_id, tmp_path).unlink()
    with pytest.raises(OwnershipConflictError, match="did not record its process"):
        kill_run_object(planned.object_id, results=tmp_path)
    stored = read_run_object(planned.object_id, results=tmp_path).record
    assert stored.state is RunState.RUNNING
    lease.release()


def test_kill_spares_a_later_process_given_the_same_pid(tmp_path: Path) -> None:
    """A start time that does not match means a reused PID: no signal sent.

    The owner here is this test process with an earlier start time, so a
    signal would end the test run; the lock stays held, so the wait times
    out and nothing is recorded.
    """
    planned = _planned(tmp_path)
    lease = _start(tmp_path, planned)
    real = AttemptOwner.of_this_process(lease.attempt_id)
    assert real.started_at is not None
    reused = AttemptOwner(lease.attempt_id, real.pid, None, real.started_at - 3600)
    attempt_owner_path(planned.object_id, tmp_path).write_text(
        json.dumps(reused.to_mapping()), encoding="utf-8"
    )
    with pytest.raises(OwnershipConflictError, match="still holds"):
        kill_run_object(planned.object_id, results=tmp_path, timeout=0)
    stored = read_run_object(planned.object_id, results=tmp_path).record
    assert stored.state is RunState.RUNNING
    lease.release()


@pytest.mark.parametrize(
    "mapping",
    [
        None,
        [],
        {"attemptId": 1, "pid": 1, "processGroup": None, "startedAt": None},
        {"attemptId": "a", "pid": "1", "processGroup": None, "startedAt": None},
        {"attemptId": "a", "pid": 1, "processGroup": "1", "startedAt": None},
        {"attemptId": "a", "pid": 1, "processGroup": None, "startedAt": "x"},
    ],
)
def test_a_malformed_owner_record_reads_as_none(mapping: object) -> None:
    """Anything but the written shape is treated as no owner record."""
    assert AttemptOwner.from_mapping(mapping) is None


def test_an_owner_record_round_trips() -> None:
    """The JSON mapping reads back as the same owner."""
    owner = AttemptOwner("att-1", 42, 42, 1.5)
    assert AttemptOwner.from_mapping(owner.to_mapping()) == owner


@pytest.mark.parametrize("checkpoint", [False, True])
def test_cancelled_run_reopens_only_with_a_checkpoint(
    tmp_path: Path, checkpoint: bool
) -> None:
    """Resurrection opens a fenced attempt without altering checkpoint identity."""
    planned = _planned(tmp_path)
    lease = _start(tmp_path, planned)
    if checkpoint:
        lease.commit_progress(checkpoint=CHECKPOINT)
    current = lease.current()
    request_cancel(
        planned.object_id, expected_revision=current.record.revision, results=tmp_path
    )
    cancelled = lease.finish(RunEvent.COMMIT_CANCEL)
    if not checkpoint:
        with pytest.raises(ResumeUnavailableError):
            resume_run_object(
                planned.object_id,
                expected_revision=cancelled.record.revision,
                results=tmp_path,
            )
        assert read_run_object(planned.object_id, results=tmp_path) == cancelled
        return
    opened = resume_run_object(
        planned.object_id,
        expected_revision=cancelled.record.revision,
        results=tmp_path,
    )
    assert opened.lease is not None
    with opened.lease as resurrected:
        assert resurrected.current().record.state is RunState.RUNNING
        assert resurrected.current().record.latest_checkpoint == CHECKPOINT
        with pytest.raises(AttemptMismatchError):
            lease.commit_progress(committed_generation=11)
