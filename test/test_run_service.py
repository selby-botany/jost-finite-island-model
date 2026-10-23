"""Tests for the lifecycle service (`fim.run_service`).

Pause, cancel, and failure are requested from the progress observer at a
chosen generation, so every scenario stops at the same safe point on every
run; no test depends on timing.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

from fim import run_service
from fim.engine import RunResult
from fim.examples import classes
from fim.execution import ScalarSession, UnsupportedSessionError
from fim.model.params import SimulationParams
from fim.persistence.artifacts import write_run_artifacts
from fim.persistence.binary_store import BinaryLogStore
from fim.persistence.manifest import read_manifest, verify_trajectory_integrity
from fim.persistence.object_lock import is_lock_held
from fim.persistence.run_checkpoint import UnsupportedCheckpointError
from fim.persistence.run_lifecycle import (
    CheckpointReference,
    IllegalTransitionError,
    LifecycleError,
    RunPhase,
    RunState,
)
from fim.persistence.run_objects import (
    RunObject,
    create_run_object,
    read_run_object,
)
from fim.persistence.run_ownership import (
    attempt_lock_path,
    reconcile_run_object,
    request_cancel,
    request_pause,
)
from fim.persistence.tlog import iter_frames
from fim.run_service import (
    CheckpointCadence,
    DraftRunError,
    Session,
    cadence_for_params,
    cancel_run_object,
    completion_was_recovered,
    diagnose_run_object,
    execute_run_object,
    load_params,
)

_CLASSES_FIXTURE = (
    Path(__file__).resolve().parent / "persistence" / "fixtures" / "classes.yaml"
)
AT = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
OBJECT_ID = "obj-" + "1" * 32

_CONFIG: dict[str, Any] = {
    "N": 20,
    "ploidy": "haploid",
    "d": 2,
    "m": 0.1,
    "mu": 0.05,
    "seed": 20261011,
    "loci": [{"locus_id": 1, "length": 200}],
    "precision": 0.0,
    "max_generations": 30,
    "n_replicates": 1,
    "stop_batch_early": False,
    "engine_backend": "lineal",
}

# Requests are checked every 5 generations; time never triggers a checkpoint.
_CADENCE = CheckpointCadence(generations=10, seconds=1e9, control_generations=5)

Observer = Callable[[Session], None]


@pytest.fixture(autouse=True)
def _fixture_class_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check every `class` against the test fixture, never the shipped file."""
    monkeypatch.setattr(classes, "default_classes_path", lambda: _CLASSES_FIXTURE)


@pytest.fixture
def checkpointed(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[int]]:
    """Record the generation of every checkpoint a session publishes."""
    generations: list[int] = []
    original = ScalarSession.checkpoint

    def spy(self: ScalarSession, lease: Any, **kwargs: Any) -> CheckpointReference:
        generations.append(self.generation)
        return original(self, lease, **kwargs)

    monkeypatch.setattr(ScalarSession, "checkpoint", spy)
    yield generations


class _Writer:
    """An artifact writer that remembers what it was asked to write."""

    def __init__(self) -> None:
        """Start with nothing written."""
        self.results: list[RunResult] = []

    def __call__(self, result: RunResult, directory: Path) -> None:
        """Remember `result` and write the standard artifacts."""
        self.results.append(result)
        write_run_artifacts(result, directory)


def _attempts() -> Callable[[], str]:
    """Return a deterministic attempt ID factory."""
    counter = itertools.count(1)
    return lambda: f"att-{next(counter):032x}"


def _clock() -> datetime:
    """Return the fixed test time."""
    return AT


def _create(tmp_path: Path, **changes: Any) -> RunObject:
    """Save a Run object from `_CONFIG` with `changes` applied."""
    text = yaml.safe_dump({**_CONFIG, **changes}, sort_keys=False)
    return create_run_object(
        text, results=tmp_path, clock=_clock, id_factory=lambda: OBJECT_ID
    )


def _execute(
    tmp_path: Path,
    writer: _Writer,
    observer: Observer | None = None,
    cadence: CheckpointCadence = _CADENCE,
    monotonic: Callable[[], float] | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> RunObject:
    """Execute the test object with deterministic stores and identities."""
    return execute_run_object(
        OBJECT_ID,
        write_artifacts=writer,
        results=tmp_path,
        clock=_clock,
        cadence=cadence,
        monotonic=monotonic if monotonic is not None else (lambda: 0.0),
        store_factory=_store,
        attempt_factory=_attempts(),
        observer=observer,
        stop_requested=stop_requested,
    )


def _at(generation: int, action: Callable[[], object]) -> Observer:
    """Return an observer that performs `action` once, at `generation`."""
    done: list[bool] = []

    def observe(session: Session) -> None:
        if session.generation >= generation and not done:
            done.append(True)
            action()

    return observe


def _revision(tmp_path: Path) -> int:
    """Return the stored lifecycle revision of the test object."""
    return read_run_object(OBJECT_ID, results=tmp_path).record.revision


def _store(path: Path, _params: SimulationParams) -> BinaryLogStore:
    """Return a log whose block boundaries never depend on time."""
    return BinaryLogStore(
        path,
        background=False,
        block_seconds=None,
        block_generations=4,
        key_every=4,
    )


def _whole(tmp_path: Path) -> RunResult:
    """Run `_CONFIG` without interruption into `tmp_path/whole.tlog`."""
    params = SimulationParams.from_mapping(_CONFIG)
    with ScalarSession.create(
        params, _store(tmp_path / "whole.tlog", params), clock=_clock
    ) as session:
        session.advance()
        return session.finalize()


def _assert_matches_whole(tmp_path: Path, result: RunResult) -> None:
    """Assert `result` and its log equal the uninterrupted run."""
    whole = _whole(tmp_path)
    assert result.report == whole.report
    assert result.final_state == whole.final_state
    assert result.convergence_histories == whole.convergence_histories
    assert result.manifest == whole.manifest
    resumed = list(iter_frames(tmp_path / "objects" / OBJECT_ID / "trajectory.tlog"))
    expected = list(iter_frames(tmp_path / "whole.tlog"))
    assert [frame.generation for frame in resumed] == [
        frame.generation for frame in expected
    ]
    for one, other in zip(resumed, expected, strict=True):
        np.testing.assert_array_equal(one.counts, other.counts)
        np.testing.assert_array_equal(one.allele_ids, other.allele_ids)


def test_a_planned_run_executes_to_completion(
    tmp_path: Path, checkpointed: list[int]
) -> None:
    """Start, checkpoint on cadence, finalize, and record completion."""
    _create(tmp_path)
    writer = _Writer()

    finished = _execute(tmp_path, writer)

    record = finished.record
    assert record.state is RunState.COMPLETED
    assert record.phase is RunPhase.FINALIZING
    assert record.active_attempt_id is None
    assert record.committed_generation == 30
    assert checkpointed == [0, 10, 20, 30]
    (result,) = writer.results
    _assert_matches_whole(tmp_path, result)
    directory = finished.directory
    manifest = read_manifest(directory / "manifest.json")
    verify_trajectory_integrity(directory / "trajectory.tlog", manifest)
    assert not is_lock_held(attempt_lock_path(OBJECT_ID, tmp_path))


@pytest.mark.parametrize("cancel", [False, True])
def test_pause_then_resume_finishes_as_the_uninterrupted_run(
    tmp_path: Path, checkpointed: list[int], cancel: bool
) -> None:
    """Paused and cancelled runs continue exactly from their checkpoint."""
    _create(tmp_path)
    writer = _Writer()
    pause = _at(
        15,
        lambda: (request_cancel if cancel else request_pause)(
            OBJECT_ID, expected_revision=_revision(tmp_path), results=tmp_path
        ),
    )

    paused = _execute(tmp_path, writer, observer=pause)

    record = paused.record
    assert record.state is (RunState.CANCELLED if cancel else RunState.PAUSED)
    assert record.active_attempt_id is None
    assert record.committed_generation == 15
    assert record.latest_checkpoint is not None
    assert record.latest_checkpoint.committed_generation == (10 if cancel else 15)
    assert not is_lock_held(attempt_lock_path(OBJECT_ID, tmp_path))
    assert not writer.results

    resumed = _execute(tmp_path, writer)

    assert resumed.record.state is RunState.COMPLETED
    assert checkpointed == ([0, 10, 20, 30] if cancel else [0, 10, 15, 25, 30])
    _assert_matches_whole(tmp_path, writer.results[0])
    if cancel:
        whole_results = tmp_path / "uninterrupted"
        _create(whole_results)
        whole = _execute(whole_results, _Writer())
        assert (resumed.directory / "report.json").read_bytes() == (
            whole.directory / "report.json"
        ).read_bytes()


def test_a_pause_requested_as_the_run_stops_completes_it(
    tmp_path: Path, checkpointed: list[int]
) -> None:
    """A run that has stopped completes; no generation is added for a pause."""
    _create(tmp_path)
    writer = _Writer()
    pause = _at(
        30,
        lambda: request_pause(
            OBJECT_ID, expected_revision=_revision(tmp_path), results=tmp_path
        ),
    )

    finished = _execute(tmp_path, writer, observer=pause)

    assert finished.record.state is RunState.COMPLETED
    assert writer.results[0].report["generation"] == 30
    assert checkpointed == [0, 10, 20, 30]


def test_cancel_keeps_the_committed_partial_trajectory(tmp_path: Path) -> None:
    """Cancel stops at a safe point and leaves the frames written so far."""
    _create(tmp_path)
    writer = _Writer()
    cancel = _at(
        10,
        lambda: request_cancel(
            OBJECT_ID, expected_revision=_revision(tmp_path), results=tmp_path
        ),
    )

    cancelled = _execute(tmp_path, writer, observer=cancel)

    assert cancelled.record.state is RunState.CANCELLED
    assert cancelled.record.committed_generation == 10
    assert not writer.results
    frames = list(iter_frames(cancelled.directory / "trajectory.tlog"))
    assert [frame.generation for frame in frames] == list(range(11))
    assert not (cancelled.directory / "manifest.json").exists()


def test_a_draft_is_refused_with_its_validation_issues(tmp_path: Path) -> None:
    """A draft never opens an attempt and reports why it cannot start."""
    _create(tmp_path, N="twenty")

    with pytest.raises(DraftRunError) as raised:
        _execute(tmp_path, _Writer())

    assert raised.value.issues
    assert "draft" in str(raised.value)
    assert read_run_object(OBJECT_ID, results=tmp_path).record.state is (RunState.DRAFT)


def test_an_unsupported_configuration_is_refused_before_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A configuration no session can build is reported, not failed."""

    def refuse(_params: SimulationParams) -> None:
        raise UnsupportedSessionError("refused for the test")

    monkeypatch.setattr(run_service, "check_session_support", refuse)
    _create(tmp_path)

    with pytest.raises(UnsupportedSessionError):
        _execute(tmp_path, _Writer())

    record = read_run_object(OBJECT_ID, results=tmp_path).record
    assert record.state is RunState.PLANNED
    assert record.revision == 1


def test_a_completed_run_cannot_execute_again(tmp_path: Path) -> None:
    """Completion is terminal."""
    _create(tmp_path)
    _execute(tmp_path, _Writer())

    with pytest.raises(IllegalTransitionError):
        _execute(tmp_path, _Writer())


def test_a_lost_owner_is_reconciled_and_resumed_exactly(tmp_path: Path) -> None:
    """An attempt that dies without recording anything resumes from its checkpoint."""
    _create(tmp_path)
    writer = _Writer()

    def die() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _execute(tmp_path, writer, observer=_at(15, die))

    record = read_run_object(OBJECT_ID, results=tmp_path).record
    assert record.state is RunState.RUNNING
    assert record.committed_generation == 10

    finished = _execute(tmp_path, writer)

    assert finished.record.state is RunState.COMPLETED
    states = [entry.to_state for entry in finished.record.transition_history]
    assert RunState.INTERRUPTED in states
    _assert_matches_whole(tmp_path, writer.results[0])


def test_a_failure_is_recorded_and_resumable(tmp_path: Path) -> None:
    """An engine error records `failed`; resuming finishes the same run."""
    _create(tmp_path)
    writer = _Writer()

    def fail() -> None:
        raise RuntimeError("disk on fire")

    with pytest.raises(RuntimeError, match="disk on fire"):
        _execute(tmp_path, writer, observer=_at(15, fail))

    record = read_run_object(OBJECT_ID, results=tmp_path).record
    assert record.state is RunState.FAILED
    assert record.last_error is not None
    assert "disk on fire" in record.last_error.message

    finished = _execute(tmp_path, writer)

    assert finished.record.state is RunState.COMPLETED
    _assert_matches_whole(tmp_path, writer.results[0])


def test_elapsed_time_triggers_checkpoints(
    tmp_path: Path, checkpointed: list[int]
) -> None:
    """Every safe point is a checkpoint when the time interval has passed."""
    _create(tmp_path)
    ticks = itertools.count()
    cadence = CheckpointCadence(generations=1000, seconds=1.0, control_generations=5)

    _execute(tmp_path, _Writer(), cadence=cadence, monotonic=lambda: next(ticks))

    assert checkpointed == [0, 5, 10, 15, 20, 25, 30]


def test_cancel_abandons_a_run_without_a_worker(tmp_path: Path) -> None:
    """A planned run has no worker to ask, so it is cancelled at once."""
    created = _create(tmp_path)

    outcome = cancel_run_object(
        OBJECT_ID, expected_revision=created.record.revision, results=tmp_path
    )

    assert outcome.run_object.record.state is RunState.CANCELLED


def test_cancel_asks_an_executing_worker(tmp_path: Path) -> None:
    """A running run is asked to stop and is `cancelling` until it does."""
    _create(tmp_path)
    states: list[RunState] = []

    def cancel() -> None:
        outcome = cancel_run_object(
            OBJECT_ID, expected_revision=_revision(tmp_path), results=tmp_path
        )
        states.append(outcome.run_object.record.state)

    finished = _execute(tmp_path, _Writer(), observer=_at(5, cancel))

    assert states == [RunState.CANCELLING]
    assert finished.record.state is RunState.CANCELLED


@pytest.mark.parametrize(
    "changes",
    [
        {"generations": 0},
        {"control_generations": 0},
        {"seconds": 0.0},
    ],
)
def test_a_cadence_that_never_progresses_is_rejected(changes: dict[str, Any]) -> None:
    """Zero or negative intervals are refused."""
    with pytest.raises(ValueError, match="positive"):
        CheckpointCadence(**changes)


class _CrashingWriter(_Writer):
    """A writer that stops the process (as a kill would) around its work."""

    def __init__(self, *, before: bool, corrupt_receipt: bool = False) -> None:
        """Crash `before` writing anything, or after the receipt is written."""
        super().__init__()
        self.before = before
        self.corrupt_receipt = corrupt_receipt

    def __call__(self, result: RunResult, directory: Path) -> None:
        """Write (unless crashing first), then raise `KeyboardInterrupt`."""
        if not self.before:
            super().__call__(result, directory)
            if self.corrupt_receipt:
                (directory / "report.json").write_text("{}", encoding="utf-8")
        raise KeyboardInterrupt


def test_a_completion_receipt_left_by_a_killed_worker_is_recovered(
    tmp_path: Path,
) -> None:
    """A verified receipt completes the run without running it again."""
    _create(tmp_path)
    crashing = _CrashingWriter(before=False)
    with pytest.raises(KeyboardInterrupt):
        _execute(tmp_path, crashing)
    stranded = read_run_object(OBJECT_ID, results=tmp_path).record
    assert stranded.state is RunState.RUNNING
    assert stranded.phase is RunPhase.FINALIZING

    writer = _Writer()
    recovered = _execute(tmp_path, writer)

    assert recovered.record.state is RunState.COMPLETED
    assert completion_was_recovered(recovered.record)
    assert recovered.record.active_attempt_id is None
    assert not writer.results
    assert diagnose_run_object(recovered) == ()
    _assert_matches_whole(tmp_path, crashing.results[0])


def test_a_failure_after_the_receipt_is_recovered_as_completion(
    tmp_path: Path,
) -> None:
    """An error raised once every artifact was written loses nothing."""
    _create(tmp_path)

    class _FailingAfter(_Writer):
        def __call__(self, result: RunResult, directory: Path) -> None:
            super().__call__(result, directory)
            raise OSError("disk went away after the receipt")

    with pytest.raises(OSError, match="after the receipt"):
        _execute(tmp_path, _FailingAfter())
    assert read_run_object(OBJECT_ID, results=tmp_path).record.state is (
        RunState.FAILED
    )

    recovered = _execute(tmp_path, _Writer())

    assert completion_was_recovered(recovered.record)
    assert recovered.record.last_error is None


def test_a_finalization_killed_before_the_receipt_is_redone(tmp_path: Path) -> None:
    """No receipt means interrupted; resuming finalizes from the checkpoint."""
    _create(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        _execute(tmp_path, _CrashingWriter(before=True))
    stranded = read_run_object(OBJECT_ID, results=tmp_path)
    (problem,) = diagnose_run_object(stranded)
    assert problem == (
        f"finalization did not finish: no receipt "
        f"{stranded.directory / 'manifest.json'} was published"
    )

    writer = _Writer()
    finished = _execute(tmp_path, writer)

    assert finished.record.state is RunState.COMPLETED
    assert not completion_was_recovered(finished.record)
    states = [entry.to_state for entry in finished.record.transition_history]
    assert RunState.INTERRUPTED in states
    _assert_matches_whole(tmp_path, writer.results[0])


def test_a_receipt_that_no_longer_verifies_is_replaced(tmp_path: Path) -> None:
    """A stale receipt is removed before any artifact is rewritten."""
    _create(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        _execute(tmp_path, _CrashingWriter(before=False, corrupt_receipt=True))
    manifest = tmp_path / "objects" / OBJECT_ID / "manifest.json"
    assert manifest.exists()
    seen: list[bool] = []

    class _Checking(_Writer):
        def __call__(self, result: RunResult, directory: Path) -> None:
            seen.append((directory / "manifest.json").exists())
            super().__call__(result, directory)

    finished = _execute(tmp_path, _Checking())

    assert seen == [False]
    assert finished.record.state is RunState.COMPLETED
    assert not completion_was_recovered(finished.record)
    verify_trajectory_integrity(
        finished.directory / "trajectory.tlog", read_manifest(manifest)
    )


def test_a_stop_request_pauses_at_the_next_safe_point(
    tmp_path: Path, checkpointed: list[int]
) -> None:
    """SIGTERM's flag pauses the run with a checkpoint; resume is exact."""
    _create(tmp_path)
    flag: list[bool] = []

    paused = _execute(
        tmp_path,
        _Writer(),
        observer=_at(15, lambda: flag.append(True)),
        stop_requested=lambda: bool(flag),
    )

    assert paused.record.state is RunState.PAUSED
    assert paused.record.committed_generation == 15
    assert checkpointed == [0, 10, 15]
    writer = _Writer()
    _execute(tmp_path, writer)
    _assert_matches_whole(tmp_path, writer.results[0])


def test_a_stop_request_as_the_run_stops_completes_it(tmp_path: Path) -> None:
    """A run that has already stopped completes instead of pausing."""
    _create(tmp_path)
    flag: list[bool] = []

    finished = _execute(
        tmp_path,
        _Writer(),
        observer=_at(30, lambda: flag.append(True)),
        stop_requested=lambda: bool(flag),
    )

    assert finished.record.state is RunState.COMPLETED


def test_diagnosis_reports_a_corrupt_checkpoint(tmp_path: Path) -> None:
    """A damaged checkpoint is named before a resume would trip on it."""
    _create(tmp_path)

    def interrupt() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _execute(tmp_path, _Writer(), observer=_at(15, interrupt))
    stopped = reconcile_run_object(OBJECT_ID, results=tmp_path)
    assert stopped.record.state is RunState.INTERRUPTED
    assert diagnose_run_object(stopped) == ()
    reference = stopped.record.latest_checkpoint
    assert reference is not None
    checkpoint = stopped.directory / "checkpoints" / reference.checkpoint_id
    for member in checkpoint.iterdir():
        member.write_bytes(b"damaged")

    (problem,) = diagnose_run_object(stopped)

    assert problem.startswith("the checkpoint is corrupt: ")


def _pause_at_generation_15(tmp_path: Path, writer: _Writer) -> RunObject:
    """Pause the test object at generation 15, leaving one checkpoint."""
    pause = _at(
        15,
        lambda: request_pause(
            OBJECT_ID, expected_revision=_revision(tmp_path), results=tmp_path
        ),
    )
    return _execute(tmp_path, writer, observer=pause)


def test_diagnosis_reports_an_unsupported_checkpoint_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A checkpoint from an incompatible release names cloning as the fix."""
    _create(tmp_path)
    paused = _pause_at_generation_15(tmp_path, _Writer())

    def refuse(_directory: Path, _reference: CheckpointReference) -> None:
        raise UnsupportedCheckpointError("schema version 2")

    monkeypatch.setattr(run_service, "load_checkpoint", refuse)

    assert diagnose_run_object(paused) == (
        "the checkpoint needs a different release: schema version 2; "
        "clone it to run the same configuration from generation 0",
    )


def test_resuming_an_unsupported_checkpoint_version_suggests_cloning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resuming against an incompatible checkpoint names cloning as the fix.

    Before this, the raw `UnsupportedCheckpointError` reached the caller
    unchanged: true, but useless to someone who does not already know that
    cloning is the way out of a format upgrade with no migration path.
    """
    writer = _Writer()
    _create(tmp_path)
    _pause_at_generation_15(tmp_path, writer)

    def refuse(_directory: Path, _reference: CheckpointReference) -> None:
        raise UnsupportedCheckpointError("schema version 2")

    monkeypatch.setattr(run_service, "load_checkpoint", refuse)

    with pytest.raises(
        LifecycleError,
        match=f"^{OBJECT_ID}'s checkpoint needs a different release: "
        r"schema version 2; clone it to run the same configuration "
        "from generation 0$",
    ):
        _execute(tmp_path, writer)


def test_diagnosis_reports_an_unsupported_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A paused run this release cannot continue says so."""
    _create(tmp_path)
    flag = [True]
    _execute(
        tmp_path,
        _Writer(),
        observer=_at(5, lambda: None),
        stop_requested=lambda: bool(flag),
    )

    def refuse(_params: SimulationParams) -> None:
        raise UnsupportedSessionError("refused for the test")

    monkeypatch.setattr(run_service, "check_session_support", refuse)
    paused = read_run_object(OBJECT_ID, results=tmp_path)

    assert diagnose_run_object(paused) == (
        "this release cannot run the configuration: refused for the test",
    )


def test_cadence_for_params_defaults() -> None:
    """Default simulation parameters produce default checkpoint cadence."""
    params = SimulationParams.from_mapping(_CONFIG)
    cadence = cadence_for_params(params)

    assert cadence.generations == 1000
    assert cadence.seconds == 60.0
    assert cadence.control_generations == 50


def test_cadence_for_params_respects_expert_settings() -> None:
    """Expert settings configure the checkpoint cadence."""
    params = SimulationParams.from_mapping(
        {
            **_CONFIG,
            "expert": {
                "checkpoint_generations": 250,
                "checkpoint_seconds": 15.0,
            },
        }
    )
    cadence = cadence_for_params(params)

    assert cadence.generations == 250
    assert cadence.seconds == 15.0
    assert cadence.control_generations == 50


def test_cadence_for_params_accepts_explicit_overrides() -> None:
    """Caller overrides take precedence over expert settings."""
    params = SimulationParams.from_mapping(
        {
            **_CONFIG,
            "expert": {
                "checkpoint_generations": 250,
                "checkpoint_seconds": 15.0,
            },
        }
    )
    cadence = cadence_for_params(
        params, generations=100, seconds=5.0, control_generations=10
    )

    assert cadence.generations == 100
    assert cadence.seconds == 5.0
    assert cadence.control_generations == 10


def test_load_params_reads_configuration_with_expert_settings(
    tmp_path: Path,
) -> None:
    """`load_params` recovers full SimulationParams including expert settings."""
    _create(
        tmp_path,
        expert={"checkpoint_generations": 300, "checkpoint_seconds": 20.0},
    )
    run_obj = read_run_object(OBJECT_ID, results=tmp_path)
    params = load_params(run_obj)

    assert params.expert.checkpoint_generations == 300
    assert params.expert.checkpoint_seconds == 20.0


def test_diagnosis_reports_damaged_published_results(tmp_path: Path) -> None:
    """A completed run whose artifacts changed is flagged, not repaired."""
    _create(tmp_path)
    finished = _execute(tmp_path, _Writer())
    (finished.directory / "report.json").write_text("{}", encoding="utf-8")

    (problem,) = diagnose_run_object(finished)

    assert problem.startswith("the published results are damaged: ")
    assert "report.json" in problem
