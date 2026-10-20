"""Addressed job events (`fim.onJobEvent`): sequencing, dedup, gaps, snapshots.

Commit A5 of `20261019-claude-opus-5-5-fim-gui-tabs-design.md`'s own
Release A schedule adds a second, additive push channel alongside every
existing named one (`fim.onRunProgress`, `fim.onBatchDone`, `fim.
onSweepEvent`, ...): `fim.onJobEvent({jobId, sequence, type, payload})`,
one sequence per job, covering every event that job ever pushes
(`fim.gui.jobs.JobAdmission.next_sequence`, `fim.gui.app._push_job_
event`). `webui/jobs.js` is the page side -- it tracks that sequence
alone; nothing yet reads it to drive a screen (that starts at A9, once a
document exists to receive it).

Three groups of tests:

- Backend, no window: `JobAdmission`'s own sequence bookkeeping, and
  `Api._claim_run`/`_claim_sweep` minting a job id, `Api.get_job_snapshot`
  answering from it.
- Backend, a fake window: a real `_drain_run_messages` call proves the
  legacy push and its addressed companion both land, in order.
- The real page, no real run: synthetic `window.fim.onJobEvent(...)`
  calls drive `webui/jobs.js` directly, the same "no real run" shape
  `test_scrubber_frame_cap.py`'s own module docstring already uses.
"""

from __future__ import annotations

import json
import queue
from pathlib import Path
from typing import Any

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
    assert snapshot == {"ok": True, "jobId": job_id, "sequence": -1}


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
        "sequence": 2,
    }


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
    """A job this process once ran, but no longer has claimed, is "no such job".

    Nothing is retained about a finished job past its own claim's
    release in this commit (`Api.get_job_snapshot`'s own docstring) --
    a fuller, persistent snapshot is a later commit's job.
    """
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
                window.fim.onJobEvent(
                    {jobId: "job-a", sequence: 0, type: "progress", payload: "first"});
                window.fim.onJobEvent(
                    {jobId: "job-a", sequence: 1, type: "status", payload: "second"});
                // Duplicate and stale: both already applied.
                window.fim.onJobEvent(
                    {jobId: "job-a", sequence: 0, type: "progress", payload: "dup-0"});
                window.fim.onJobEvent(
                    {jobId: "job-a", sequence: 1, type: "status", payload: "dup-1"});
                """
            )
            outcome.put(window.evaluate_js(_read_job_state("job-a")))
        finally:
            window.destroy()

    webview.start(_drive)
    state = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert state is not None
    assert state == {"lastSequence": 1, "appliedCount": 2, "latest": "second"}


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
                        {ok: true, jobId: jobId, sequence: 42});
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
            outcome.put(window.evaluate_js(_read_job_state("job-a")))
        finally:
            window.destroy()

    webview.start(_drive)
    state = outcome.get(timeout=_OUTCOME_TIMEOUT_SECONDS)

    assert state is not None
    assert state["lastSequence"] == 42
