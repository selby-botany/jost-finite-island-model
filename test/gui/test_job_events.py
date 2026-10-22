"""Addressed job events (`fim.onJobEvent`): sequencing, dedup, gaps, snapshots.

Commits A5 and A7 of `20261019-claude-opus-5-5-fim-gui-tabs-design.md`'s
Release A schedule add an addressed push channel alongside each existing
named one (`fim.onRunProgress`, `fim.onBatchDone`, `fim.onSweepEvent`,
...): `fim.onJobEvent({jobId, sequence, type, payload})`, one sequence
per job. The job registry retains a finished job's view level and latest event
snapshot until its document closes; `webui/jobs.js` adopts snapshots
after a gap or view change, but does not yet render document state
(that starts at A9).

Three groups of tests:

- Backend, no window: `JobAdmission`'s sequence and snapshot bookkeeping,
  and `Api`'s claim, view-change, and snapshot behavior.
- Backend, a fake window: a real `_drain_run_messages` call proves the
  legacy push and its addressed companion both land, in order.
- The real page, no real run: synthetic `window.fim.onJobEvent(...)`
  calls drive `webui/jobs.js` directly, the same "no real run" shape
  `test_scrubber_frame_cap.py`'s own module docstring already uses.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import webview
from conftest import COMPLETION_BACKSTOP_SECONDS

from fim.gui import app as app_module
from fim.gui import jobs as jobs_module
from fim.gui.app import Api, create_window
from fim.gui.publisher import Publisher

from .conftest import wait_for_run_view_ready

# Not a module-level `pytestmark`: only the "real page" group below opens
# a real `pywebview` window and needs `gui`; the backend groups need no
# display and run fine in the default, parallel non-GUI suite -- marking
# the whole module would silently exclude them from it too.
_OUTCOME_TIMEOUT_SECONDS = 20.0


class _FakeWindow:
    """Records every `evaluate_js` call, in order."""

    def __init__(self) -> None:
        self.scripts: list[str] = []

    def evaluate_js(self, script: str) -> None:
        self.scripts.append(script)


# -- Backend, no window -----------------------------------------------------


def test_next_sequence_starts_at_zero_and_counts_up() -> None:
    """One job's own sequence: 0, 1, 2, never resetting on its own."""
    admission = jobs_module.JobAdmission()
    job_id = admission.new_job_id()
    assert [admission.next_sequence(job_id) for _ in range(3)] == [0, 1, 2]


def test_two_jobs_have_independent_sequences() -> None:
    """A second job's own counter starts at 0 regardless of the first's."""
    admission = jobs_module.JobAdmission()
    first = admission.new_job_id()
    second = admission.new_job_id()
    assert admission.next_sequence(first) == 0
    assert admission.next_sequence(first) == 1
    assert admission.next_sequence(second) == 0
    assert admission.next_sequence(first) == 2


@pytest.mark.parametrize(
    ("usable_cores", "reserved_cores", "expected"),
    [(1, 0, 1), (8, 0, 8), (8, 3, 5), (2, 9, 1)],
)
def test_worker_capacity_respects_reserve_and_one_slot_minimum(
    usable_cores: int, reserved_cores: int, expected: int
) -> None:
    """Injected CPU counts make reserve arithmetic deterministic."""
    assert jobs_module.worker_capacity(usable_cores, reserved_cores) == expected


def test_worker_capacity_rejects_invalid_inputs() -> None:
    """Invalid budgets fail explicitly instead of producing hidden capacity."""
    with pytest.raises(ValueError, match="usable_cores"):
        jobs_module.worker_capacity(0, 0)
    with pytest.raises(ValueError, match="reserved_cores"):
        jobs_module.worker_capacity(2, -1)


@pytest.mark.parametrize(
    ("usable_cores", "reserved_percent", "expected"),
    [(3, 34, 2), (8, 25, 2), (8, 0, 0), (8, 100, 8)],
)
def test_percentage_reserve_rounds_up_to_whole_cores(
    usable_cores: int, reserved_percent: int, expected: int
) -> None:
    """A fractional core reserve always rounds upward."""
    assert (
        jobs_module.reserved_core_count(usable_cores, reserved_percent=reserved_percent)
        == expected
    )


@pytest.mark.parametrize(
    ("total_bytes", "mode", "value", "expected"),
    [
        (16 * jobs_module.GIB_BYTES, "auto", 0, 2 * jobs_module.GIB_BYTES),
        (40 * jobs_module.GIB_BYTES, "auto", 0, 4 * jobs_module.GIB_BYTES),
        (9 * jobs_module.GIB_BYTES, "gib", 3, 3 * jobs_module.GIB_BYTES),
        (1001, "percent", 10, 101),
    ],
)
def test_memory_reserve_modes_and_rounding(
    total_bytes: int,
    mode: jobs_module.MemoryReserveMode,
    value: int,
    expected: int,
) -> None:
    """Automatic, GiB, and percentage reserves resolve to whole bytes."""
    assert jobs_module.memory_reserve_bytes(total_bytes, mode, value) == expected


def test_memory_estimate_uses_child_and_vector_cost_per_worker_slot() -> None:
    """Batch slots account for child overhead and each known vector block."""
    assert jobs_module.estimate_job_memory_bytes(3, 100, 25) == 375


def test_child_memory_estimate_uses_largest_rss_or_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Measured workers use the largest RSS; unmeasurable workers use fallback."""
    admission = jobs_module.JobAdmission()
    first, _ = admission.admit("run", 1, 3)
    second, _ = admission.admit("saved-run", 1, 3)
    assert first is not None
    assert second is not None

    for record, pid in ((first, 1), (second, 2)):
        record.child = cast(
            jobs_module.ChildJob,
            SimpleNamespace(process=SimpleNamespace(pid=pid, poll=lambda: None)),
        )

    rss_by_pid = {1: 512, 2: 1024}
    monkeypatch.setattr(
        jobs_module.psutil,
        "Process",
        lambda pid: SimpleNamespace(
            memory_info=lambda: SimpleNamespace(rss=rss_by_pid[pid])
        ),
    )
    assert jobs_module.child_memory_estimate_bytes(admission.active_jobs()) == 1024
    assert (
        jobs_module.child_memory_estimate_bytes(())
        == jobs_module.DEFAULT_CHILD_MEMORY_ESTIMATE_BYTES
    )


def test_invalid_memory_reserves_are_rejected() -> None:
    """Invalid memory settings cannot silently become a zero reserve."""
    with pytest.raises(ValueError, match="at least one"):
        jobs_module.memory_reserve_bytes(0)
    with pytest.raises(ValueError, match="percentage"):
        jobs_module.memory_reserve_bytes(10, "percent", 101)
    with pytest.raises(ValueError, match="mode"):
        jobs_module.memory_reserve_bytes(10, "invalid")  # type: ignore[arg-type]


def test_pause_job_pauses_a_saved_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A memory warning's Pause action reaches the durable Run lifecycle."""
    api = Api()
    record, _ = api._jobs.admit("run", 1, 1)
    assert record is not None
    record.active_object_id = "run-object"
    paused: list[str] = []
    monkeypatch.setattr(app_module.object_bridge, "pause", paused.append)

    assert api.pause_job(record.job_id) == {"ok": True}
    assert paused == ["run-object"]


def test_pause_job_stops_a_sweep_at_a_resumable_point() -> None:
    """A sweep Pause request sets its cooperative checkpoint event."""
    api = Api()
    record, _ = api._jobs.admit("sweep", 1, 1)
    assert record is not None
    record.cancel_event = threading.Event()

    assert api.pause_job(record.job_id) == {"ok": True}
    assert record.cancel_event.is_set()


def test_admission_retains_effective_priority_in_job_snapshots() -> None:
    """Changing preferences cannot change the priority of an admitted job."""
    admission = jobs_module.JobAdmission()
    record, _remaining = admission.admit("run", cores=1, capacity=2, priority="lowest")

    assert record is not None
    assert record.priority == "lowest"
    assert admission.snapshot(record.job_id) == {
        "viewLevel": "full",
        "priority": "lowest",
        "sequence": -1,
        "snapshot": None,
    }
    admission.release(record.job_id)
    retained = admission.snapshot(record.job_id)
    assert retained is not None
    assert retained["priority"] == "lowest"


def test_admission_never_reserves_more_than_available_capacity() -> None:
    """A refused reservation leaves the existing job and remaining budget intact."""
    admission = jobs_module.JobAdmission()
    first, remaining = admission.admit("run", cores=3, capacity=5)

    assert first is not None
    assert remaining == 2
    refused, unchanged = admission.admit("sweep", cores=3, capacity=5)
    assert refused is None
    assert unchanged == 2
    assert admission.available_cores(5) == 2
    assert admission.active_jobs() == (first,)

    released = admission.release(first.job_id)

    assert released is first
    assert admission.available_cores(5) == 5


def test_concurrent_admission_cannot_overbook_worker_capacity() -> None:
    """Simultaneous requests compete atomically for the final worker slot."""
    admission = jobs_module.JobAdmission()
    start = threading.Barrier(3)
    results: queue.Queue[tuple[jobs_module.JobRecord | None, int]] = queue.Queue()

    def reserve() -> None:
        """Attempt to reserve the only slot after both threads are ready."""
        start.wait(timeout=2)
        results.put(admission.admit("run", cores=1, capacity=1))

    threads = [threading.Thread(target=reserve) for _ in range(2)]
    for thread in threads:
        thread.start()
    start.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()

    reservations = [results.get_nowait() for _ in threads]
    admitted = [record for record, _remaining in reservations if record]
    refused = [remaining for record, remaining in reservations if record is None]

    assert len(admitted) == 1
    assert refused == [0]
    assert admission.active_jobs() == tuple(admitted)
    assert admission.available_cores(1) == 0


def test_latest_sequence_is_none_before_any_push() -> None:
    """A claimed job that has not pushed anything yet has no latest sequence."""
    admission = jobs_module.JobAdmission()
    job_id = admission.new_job_id()
    assert admission.latest_sequence(job_id) is None
    admission.next_sequence(job_id)
    assert admission.latest_sequence(job_id) == 0
    admission.next_sequence(job_id)
    assert admission.latest_sequence(job_id) == 1


def test_forget_drops_the_bookkeeping() -> None:
    """Forgetting a job's own id lets a later, unrelated use of it start fresh."""
    admission = jobs_module.JobAdmission()
    job_id = admission.new_job_id()
    admission.next_sequence(job_id)
    admission.next_sequence(job_id)
    admission.forget(job_id)
    assert admission.latest_sequence(job_id) is None
    assert admission.next_sequence(job_id) == 0


def test_claim_run_mints_a_job_id_and_release_clears_it(tmp_path: Path) -> None:
    """`_claim_run` mints `Api._job_id`; `_release_run_claim` clears it."""
    api = Api(preferences_path=tmp_path / "preferences.json")
    refusal = api._claim_run()
    assert refusal is None
    job_id = api._job_id
    assert job_id is not None
    api._release_run_claim()
    assert api._job_id is None
    # The id is forgotten, not merely cleared: a stray post-release read
    # starts fresh rather than continuing the finished job's own count.
    assert api._jobs.latest_sequence(job_id) is None


def test_claim_sweep_mints_a_job_id_and_release_clears_it(tmp_path: Path) -> None:
    """`_claim_sweep` mints `Api._job_id`; `_release_sweep_claim` clears it."""
    api = Api(preferences_path=tmp_path / "preferences.json")
    busy = api._claim_sweep()
    assert busy is None
    job_id = api._job_id
    assert job_id is not None
    assert api._sweep_cancel_event is not None
    api._release_sweep_claim()
    assert api._job_id is None
    assert api._sweep_cancel_event is None


def test_a_finished_job_snapshot_survives_claim_release(tmp_path: Path) -> None:
    """Finished jobs remain available for page resynchronization."""
    api = Api(preferences_path=tmp_path / "preferences.json")
    assert api._claim_run() is None
    job_id = api._job_id
    assert job_id is not None
    api._jobs.record_event(job_id, "finished", {})

    api._release_run_claim(retain_snapshot=True)

    assert api._job_id is None
    assert api.get_job_snapshot(job_id) == {
        "ok": True,
        "jobId": job_id,
        "viewLevel": "full",
        "priority": "lower",
        "sequence": 0,
        "snapshot": {
            "sequence": 0,
            "type": "finished",
            "payload": {},
        },
    }


def test_closing_a_finished_job_document_forgets_its_snapshot(
    tmp_path: Path,
) -> None:
    """Closing a document drops the finished job's retained event state."""
    api = Api(preferences_path=tmp_path / "preferences.json")
    assert api._claim_run() is None
    job_id = api._job_id
    assert job_id is not None
    api._jobs.record_event(job_id, "finished", {})
    api._release_run_claim(retain_snapshot=True)

    assert api.close_job_document(job_id) == {"ok": True}
    assert api.get_job_snapshot(job_id) == {"ok": False, "message": "no such job"}


def test_a_run_claim_and_a_sweep_claim_each_mint_their_own_job_id(
    tmp_path: Path,
) -> None:
    """A run's own job id and a later sweep's own are never the same value."""
    api = Api(preferences_path=tmp_path / "preferences.json")
    assert api._claim_run() is None
    run_job_id = api._job_id
    api._release_run_claim()
    assert api._claim_sweep() is None
    sweep_job_id = api._job_id
    api._release_sweep_claim()
    assert run_job_id != sweep_job_id


def test_get_job_snapshot_before_any_push(tmp_path: Path) -> None:
    """A snapshot taken before the job has pushed anything reports sequence -1."""
    api = Api(preferences_path=tmp_path / "preferences.json")
    assert api._claim_run() is None
    job_id = api._job_id
    assert job_id is not None
    snapshot = api.get_job_snapshot(job_id)
    assert snapshot == {
        "ok": True,
        "jobId": job_id,
        "viewLevel": "full",
        "priority": "lower",
        "sequence": -1,
        "snapshot": None,
    }


def test_get_job_snapshot_reports_the_latest_assigned_sequence(
    tmp_path: Path,
) -> None:
    """A snapshot reflects however many pushes `next_sequence` has already given out."""
    api = Api(preferences_path=tmp_path / "preferences.json")
    assert api._claim_run() is None
    job_id = api._job_id
    assert job_id is not None
    api._jobs.next_sequence(job_id)
    api._jobs.next_sequence(job_id)
    api._jobs.next_sequence(job_id)
    assert api.get_job_snapshot(job_id) == {
        "ok": True,
        "jobId": job_id,
        "viewLevel": "full",
        "priority": "lower",
        "sequence": 2,
        "snapshot": None,
    }


def test_set_job_view_returns_latest_event_snapshot(tmp_path: Path) -> None:
    """Changing the active job's view level returns its current event snapshot."""
    api = Api(preferences_path=tmp_path / "preferences.json")
    assert api._claim_run() is None
    job_id = api._job_id
    assert job_id is not None
    api._jobs.record_event(job_id, "progress", {"generation": 12})

    snapshot = api.set_job_view(job_id, "summary")

    assert snapshot == {
        "ok": True,
        "jobId": job_id,
        "viewLevel": "summary",
        "priority": "lower",
        "sequence": 0,
        "snapshot": {
            "sequence": 0,
            "type": "progress",
            "payload": {"generation": 12},
        },
    }


def test_set_job_view_refuses_invalid_level(tmp_path: Path) -> None:
    """A bad view level is rejected without changing the active job."""
    api = Api(preferences_path=tmp_path / "preferences.json")
    assert api._claim_run() is None
    job_id = api._job_id
    assert job_id is not None

    assert api.set_job_view(job_id, "compact") == {
        "ok": False,
        "message": "view level must be full or summary",
    }
    assert api.get_job_snapshot(job_id)["viewLevel"] == "full"


def test_get_job_snapshot_refuses_an_unknown_job_id(tmp_path: Path) -> None:
    """A `jobId` nobody has claimed is refused, not reported as empty."""
    api = Api(preferences_path=tmp_path / "preferences.json")
    assert api.get_job_snapshot("no-such-job") == {
        "ok": False,
        "message": "no such job",
    }


def test_get_job_snapshot_refuses_a_job_whose_claim_has_been_released(
    tmp_path: Path,
) -> None:
    """A released claim's view and snapshot are not active job state."""
    api = Api(preferences_path=tmp_path / "preferences.json")
    assert api._claim_run() is None
    job_id = api._job_id
    assert job_id is not None
    api._release_run_claim()
    assert api.get_job_snapshot(job_id) == {"ok": False, "message": "no such job"}


# -- Backend, a fake window --------------------------------------------------


def test_the_addressed_push_rides_alongside_the_legacy_push_in_order() -> None:
    """A real drain's legacy push and its `onJobEvent` companion both land.

    Proves A5's own "legacy pushes still reach the single document": the
    addressed channel is additive, never a replacement, and is delivered
    right after the push it accompanies, in the one publisher's own
    delivery order (`fim.gui.publisher`'s own module docstring).
    """
    message_queue: queue.Queue[Any] = queue.Queue()
    message_queue.put(("cancelled", 7))
    window = _FakeWindow()
    publisher = Publisher()
    admission = jobs_module.JobAdmission()
    job_id = admission.new_job_id()

    app_module._drain_run_messages(
        window,
        message_queue,
        max_generations=10,
        deme_count=1,
        output_directory=Path("/unused"),
        publisher=publisher,
        job_admission=admission,
        job_id=job_id,
    )
    assert publisher.stop(COMPLETION_BACKSTOP_SECONDS) is True

    assert len(window.scripts) == 2
    assert window.scripts[0].startswith("fim.onRunCancelled(")
    assert window.scripts[1].startswith("fim.onJobEvent(")
    envelope = json.loads(window.scripts[1][len("fim.onJobEvent(") : -1])
    assert envelope == {
        "jobId": job_id,
        "sequence": 0,
        "type": "terminal",
        "payload": {"kind": "cancelled", "value": 7},
    }


# -- The real page, no real run ----------------------------------------------

_READ_JOB_STATE = """({
    lastSequence: window.fim.jobs.lastSequenceFor(%(job_id)s),
    appliedCount: window.fim.jobs.appliedCountFor(%(job_id)s),
    latest: window.fim.jobs.latestAppliedFor(%(job_id)s)
})"""


def _read_job_state(job_id: str) -> str:
    """A snippet reading `window.fim.jobs`'s own test introspection for `job_id`."""
    quoted = f'"{job_id}"'
    return _READ_JOB_STATE % {"job_id": quoted}


@pytest.mark.gui
def test_one_sequence_per_job_and_jobs_never_cross() -> None:
    """Two jobs' own sequences are tracked independently, never conflated."""
    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            wait_for_run_view_ready(window)
            window.evaluate_js(
                """
                window.fim.onJobEvent(
                    {jobId: "job-a", sequence: 0, type: "progress", payload: 1});
                window.fim.onJobEvent(
                    {jobId: "job-a", sequence: 1, type: "progress", payload: 2});
                window.fim.onJobEvent(
                    {jobId: "job-b", sequence: 0, type: "status", payload: "b"});
                window.fim.onJobEvent(
                    {jobId: "job-a", sequence: 2, type: "status", payload: 3});
                """
            )
            outcome.put(
                {
                    "a": window.evaluate_js(_read_job_state("job-a")),
                    "b": window.evaluate_js(_read_job_state("job-b")),
                }
            )
        finally:
            window.destroy()

    webview.start(_drive)
    state = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert state is not None
    assert state["a"] == {"lastSequence": 2, "appliedCount": 3, "latest": 3}
    assert state["b"] == {"lastSequence": 0, "appliedCount": 1, "latest": "b"}


@pytest.mark.gui
def test_duplicate_and_stale_events_are_ignored() -> None:
    """A repeated or older sequence never re-applies, never regresses tracking."""
    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            wait_for_run_view_ready(window)
            window.evaluate_js(
                """
                const applied = [];
                applied.push(window.fim.onJobEvent(
                    {jobId: "job-a", sequence: 0, type: "progress", payload: "first"}));
                applied.push(window.fim.onJobEvent(
                    {jobId: "job-a", sequence: 1, type: "status", payload: "second"}));
                // Duplicate and stale: both already applied.
                applied.push(window.fim.onJobEvent(
                    {jobId: "job-a", sequence: 0, type: "progress", payload: "dup-0"}));
                applied.push(window.fim.onJobEvent(
                    {jobId: "job-a", sequence: 1, type: "status", payload: "dup-1"}));
                window.__fimAppliedEvents = applied;
                """
            )
            outcome.put(
                {
                    "job": window.evaluate_js(_read_job_state("job-a")),
                    "applied": window.evaluate_js("window.__fimAppliedEvents"),
                }
            )
        finally:
            window.destroy()

    webview.start(_drive)
    state = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert state is not None
    assert state["job"] == {
        "lastSequence": 1,
        "appliedCount": 2,
        "latest": "second",
    }
    assert state["applied"] == [True, True, False, False]


@pytest.mark.gui
def test_a_jobs_first_ever_event_is_never_treated_as_a_gap() -> None:
    """Progress may arrive before anything else names a job; it is still applied.

    Design doc §7.3: "progress may arrive before the start reply; it is
    routed by jobId." A never-before-seen `jobId`'s first event is
    accepted however large its own sequence is -- there is nothing yet
    to call a gap relative to -- and is attributed to exactly that job,
    leaving any other, already-tracked job untouched.
    """
    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            wait_for_run_view_ready(window)
            window.evaluate_js(
                """
                window.fim.onJobEvent(
                    {jobId: "job-y", sequence: 2, type: "status", payload: "y"});
                window.fim.onJobEvent(
                    {jobId: "job-x", sequence: 7, type: "progress",
                     payload: "x-first"});
                """
            )
            outcome.put(
                {
                    "x": window.evaluate_js(_read_job_state("job-x")),
                    "y": window.evaluate_js(_read_job_state("job-y")),
                }
            )
        finally:
            window.destroy()

    webview.start(_drive)
    state = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert state is not None
    assert state["x"] == {"lastSequence": 7, "appliedCount": 1, "latest": "x-first"}
    assert state["y"] == {"lastSequence": 2, "appliedCount": 1, "latest": "y"}


@pytest.mark.gui
def test_a_progress_gap_never_requests_a_snapshot() -> None:
    """A skipped progress sequence is routine (coalescing), not a loss."""
    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            wait_for_run_view_ready(window)
            window.evaluate_js(
                """
                window.__snapshotRequests = [];
                window.fim.jobs.requestSnapshot = function (jobId) {
                    window.__snapshotRequests.push(jobId);
                };
                window.fim.onJobEvent(
                    {jobId: "job-a", sequence: 0, type: "status", payload: "s0"});
                window.fim.onJobEvent(
                    {jobId: "job-a", sequence: 5, type: "progress", payload: "p5"});
                """
            )
            outcome.put(
                {
                    "requests": window.evaluate_js("window.__snapshotRequests"),
                    "state": window.evaluate_js(_read_job_state("job-a")),
                }
            )
        finally:
            window.destroy()

    webview.start(_drive)
    state = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert state is not None
    assert state["requests"] == []
    assert state["state"] == {"lastSequence": 5, "appliedCount": 2, "latest": "p5"}


@pytest.mark.gui
def test_a_gap_before_a_status_or_terminal_event_requests_a_snapshot() -> None:
    """A skipped `status`/`terminal` sequence means a push was lost: resync.

    The gap-revealing event itself is not applied -- `lastSequenceFor`
    stays at whatever it was before the gap was seen.
    """
    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            wait_for_run_view_ready(window)
            window.evaluate_js(
                """
                window.__snapshotRequests = [];
                window.fim.jobs.requestSnapshot = function (jobId) {
                    window.__snapshotRequests.push(jobId);
                };
                window.fim.onJobEvent(
                    {jobId: "job-a", sequence: 0, type: "status", payload: "s0"});
                // A gap: sequence 3 skips 1 and 2, and this is a
                // "terminal" event, never coalesced away.
                window.fim.onJobEvent(
                    {jobId: "job-a", sequence: 3, type: "terminal", payload: "t3"});
                """
            )
            outcome.put(
                {
                    "requests": window.evaluate_js("window.__snapshotRequests"),
                    "state": window.evaluate_js(_read_job_state("job-a")),
                }
            )
        finally:
            window.destroy()

    webview.start(_drive)
    state = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert state is not None
    assert state["requests"] == ["job-a"]
    # The gap-revealing event was not applied: still at sequence 0.
    assert state["state"] == {"lastSequence": 0, "appliedCount": 1, "latest": "s0"}


@pytest.mark.gui
def test_a_snapshot_resyncs_the_tracked_sequence() -> None:
    """Once a snapshot resolves, its own reported sequence is adopted.

    `window.pywebview.api.get_job_snapshot` is stubbed here with no real
    job in flight -- the same "no real run" shape this module's own
    docstring describes.
    """
    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            wait_for_run_view_ready(window)
            window.evaluate_js(
                """
                window.pywebview.api.get_job_snapshot = function (jobId) {
                    return Promise.resolve(
                        {
                            ok: true,
                            jobId: jobId,
                            sequence: 42,
                            viewLevel: "summary",
                            snapshot: {
                                sequence: 42,
                                type: "progress",
                                payload: {generation: 42}
                            }
                        });
                };
                window.fim.onJobEvent(
                    {jobId: "job-a", sequence: 0, type: "status", payload: "s0"});
                window.fim.onJobEvent(
                    {jobId: "job-a", sequence: 9, type: "terminal", payload: "t9"});
                """
            )
            # A separate round trip: the snapshot's own `.then(...)`
            # resolves on the microtask queue, which drains between
            # `evaluate_js` calls, not within the one above.
            outcome.put(
                {
                    "state": window.evaluate_js(_read_job_state("job-a")),
                    "snapshot": window.evaluate_js(
                        'window.fim.jobs.snapshotFor("job-a")'
                    ),
                    "viewLevel": window.evaluate_js(
                        'window.fim.jobs.viewLevelFor("job-a")'
                    ),
                }
            )
        finally:
            window.destroy()

    webview.start(_drive)
    state = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert state is not None
    assert state["state"]["lastSequence"] == 42
    assert state["viewLevel"] == "summary"
    assert state["snapshot"] == {
        "sequence": 42,
        "type": "progress",
        "payload": {"generation": 42},
    }


@pytest.mark.gui
def test_a_late_snapshot_does_not_overwrite_a_newer_event() -> None:
    """A delayed snapshot cannot move event tracking backward."""
    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            wait_for_run_view_ready(window)
            window.evaluate_js(
                """
                window.pywebview.api.get_job_snapshot = function () {
                    return new Promise((resolve) => {
                        window.__resolveJobSnapshot = resolve;
                    });
                };
                window.fim.jobs.requestSnapshot("job-a");
                window.fim.onJobEvent(
                    {jobId: "job-a", sequence: 2, type: "progress", payload: "new"});
                window.__resolveJobSnapshot({
                    ok: true,
                    jobId: "job-a",
                    sequence: 1,
                    viewLevel: "summary",
                    snapshot: {
                        sequence: 1,
                        type: "progress",
                        payload: "stale"
                    }
                });
                """
            )
            outcome.put(
                {
                    "state": window.evaluate_js(_read_job_state("job-a")),
                    "snapshot": window.evaluate_js(
                        'window.fim.jobs.snapshotFor("job-a")'
                    ),
                }
            )
        finally:
            window.destroy()

    webview.start(_drive)
    state = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert state is not None
    assert state["state"] == {
        "lastSequence": 2,
        "appliedCount": 1,
        "latest": "new",
    }
    assert state["snapshot"] == {
        "sequence": 2,
        "type": "progress",
        "payload": "new",
    }


@pytest.mark.gui
def test_an_annotation_for_a_closed_document_is_dropped() -> None:
    """A late annotation cannot reach a document that has closed."""
    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            wait_for_run_view_ready(window)
            window.evaluate_js(
                """
                window.__annotationCount = 0;
                window.pywebview.api.close_job_document = function () {
                    return Promise.resolve({ok: true});
                };
                window.fim.onJobAnnotation = function () {
                    window.__annotationCount += 1;
                };
                window.fim.jobs.closeDocument("job-closed");
                window.fim.onJobEvent({
                    jobId: "job-closed",
                    sequence: 0,
                    type: "annotation",
                    payload: {identical: true}
                });
                """
            )
            outcome.put(
                window.evaluate_js(
                    """({
                        annotationCount: window.__annotationCount,
                        lastSequence:
                            window.fim.jobs.lastSequenceFor("job-closed"),
                        isClosed:
                            window.fim.jobs.isDocumentClosed("job-closed")
                    })"""
                )
            )
        finally:
            window.destroy()

    webview.start(_drive)
    state = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert state == {
        "annotationCount": 0,
        "lastSequence": None,
        "isClosed": True,
    }


@pytest.mark.gui
def test_an_annotation_after_coalesced_progress_is_applied() -> None:
    """Progress coalescing does not suppress the following annotation."""
    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            wait_for_run_view_ready(window)
            window.evaluate_js(
                """
                window.__annotationCount = 0;
                window.__snapshotRequests = 0;
                window.pywebview.api.get_job_snapshot = function () {
                    window.__snapshotRequests += 1;
                    return Promise.resolve({
                        ok: true,
                        jobId: "job-annotated",
                        sequence: 2,
                        viewLevel: "full",
                        snapshot: {
                            sequence: 2,
                            type: "annotation",
                            payload: {identical: true}
                        }
                    });
                };
                window.fim.onJobAnnotation = function () {
                    window.__annotationCount += 1;
                };
                window.fim.onJobEvent({
                    jobId: "job-annotated",
                    sequence: 0,
                    type: "progress",
                    payload: {}
                });
                window.fim.onJobEvent({
                    jobId: "job-annotated",
                    sequence: 2,
                    type: "annotation",
                    payload: {identical: true}
                });
                """
            )
            outcome.put(
                window.evaluate_js(
                    """({
                        annotationCount: window.__annotationCount,
                        snapshotRequests: window.__snapshotRequests,
                        lastSequence:
                            window.fim.jobs.lastSequenceFor("job-annotated")
                    })"""
                )
            )
        finally:
            window.destroy()

    webview.start(_drive)
    state = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert state == {
        "annotationCount": 1,
        "snapshotRequests": 1,
        "lastSequence": 2,
    }


@pytest.mark.gui
def test_memory_warning_names_jobs_and_pause_button_refreshes_the_banner() -> None:
    """The page names each pressured job and wires Pause to the API."""
    window = create_window(hidden=True)
    outcome: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def _drive() -> None:
        try:
            wait_for_run_view_ready(window)
            window.evaluate_js(
                """
                window.__fimMemoryWarningReady = false;
                (async () => {
                    await window.fim.jobs.refreshMemoryWarning();
                    window.__fimMemoryStatus = {
                        ok: true,
                        warning: true,
                        availableBytes: 1024 ** 3,
                        reservedBytes: 2 * 1024 ** 3,
                        jobs: [
                            {jobId: "run-1", name: "Long run", kind: "run"},
                            {jobId: "sweep-2", name: "Migration sweep",
                             kind: "sweep"}
                        ]
                    };
                    window.__fimPausedJobs = [];
                    window.pywebview.api.get_memory_status =
                        () => Promise.resolve(window.__fimMemoryStatus);
                    window.pywebview.api.pause_job = (jobId) => {
                        window.__fimPausedJobs.push(jobId);
                        window.__fimMemoryStatus = {
                            ok: true, warning: false, jobs: []
                        };
                        return Promise.resolve({ok: true});
                    };
                    await window.fim.jobs.refreshMemoryWarning();
                    window.__fimMemoryWarningReady = true;
                })();
                """
            )
            deadline = time.monotonic() + _OUTCOME_TIMEOUT_SECONDS
            initial: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                initial = window.evaluate_js(
                    """window.__fimMemoryWarningReady ? ({
                        hidden:
                            document.getElementById('memory-warning-banner').hidden,
                        jobs: Array.from(
                            document.querySelectorAll('.memory-warning-job')
                        ).map((item) => ({
                            name: item.querySelector('span').textContent,
                            label: item.querySelector('button')
                                .getAttribute('aria-label'),
                            text: item.querySelector('button').textContent
                        }))
                    }) : null"""
                )
                if initial is not None:
                    break
                time.sleep(0.05)
            if initial is None:
                raise TimeoutError("memory warning did not render")
            window.evaluate_js(
                "document.querySelector('.memory-warning-job button').click();"
            )
            deadline = time.monotonic() + _OUTCOME_TIMEOUT_SECONDS
            after: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                after = window.evaluate_js(
                    """({
                        paused: window.__fimPausedJobs,
                        hidden:
                            document.getElementById('memory-warning-banner').hidden
                    })"""
                )
                if after["paused"] and after["hidden"]:
                    break
                time.sleep(0.05)
            outcome.put({"initial": initial, "after": after})
        finally:
            window.destroy()

    webview.start(_drive)
    state = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert state["initial"] == {
        "hidden": False,
        "jobs": [
            {"name": "Long run", "label": "Pause Long run", "text": "Pause"},
            {
                "name": "Migration sweep",
                "label": "Pause Migration sweep",
                "text": "Pause",
            },
        ],
    }
    assert state["after"] == {"paused": ["run-1"], "hidden": True}
