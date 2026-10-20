"""The one lock that admits a run, a saved run, or a sweep -- and its jobId/sequence.

Before this module existed, `Api._claim_run` (plain and saved runs) and
`Api.start_sweep`/`resume_sweep` (sweeps) each decided whether they could
start under a *different* lock: a dedicated lock for the former, and no
lock at all for the latter, which only ever checked `_busy_message()`
and set `_sweep_cancel_event` as two separate, unsynchronized steps. A
run's own claim and a sweep's own admission could therefore interleave:
each could see the other's slot as free for one instant, and both could
start (`20261019-claude-opus-5-5-fim-gui-tabs-design-review.md`'s own
note on this exact gap, left for "the tabs registry's job" to close --
commit A4 of that design's Release A schedule).

`JobAdmission` is that one lock, shared by every admission decision.
`fim.gui.app.Api` keeps its own existing, observable fields --
`_run_in_flight`, `_active_object_id`, `_sweep_cancel_event` -- as the
state those decisions are made from; nothing here duplicates or
replaces them, and no bridge method's return shape changes. Only the
synchronization moves here, so `Api._claim_run`/`_claim_sweep` (and
their own release counterparts) can never both see a free slot for the
same instant.

Commit A5 of that same schedule ("addressed job events and controls")
adds a `jobId` and one sequence per job to every addressed push. Commit
A7 adds the active job's view level and latest event snapshot, so a page
can switch between full and summary rendering and recover after a gap.
`Api._job_id` is carried as a plain argument into each drain/push
function; background threads never read a later claim's id from `Api`.

This Release A registry still admits only one active job. Its retained
snapshot is discarded when that job's claim is released; multi-job
records and per-document retention belong to later commits.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Literal

ViewLevel = Literal["full", "summary"]


class JobAdmission:
    """One lock, held only long enough to decide and record one claim.

    Never held across anything that waits on another thread or on the
    page -- every caller takes it, makes its decision, and releases it
    again within the same few lines (`Api._claim_run`/`_claim_sweep`,
    and their own release counterparts). The same lock also guards
    `_next_sequence`, below: a short enough critical section (one dict
    read and one write) that sharing it costs nothing worth a second
    lock, and `JobAdmission`'s own docstring already expects this
    module to grow into the fuller registry that will need one lock
    for all of it anyway.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self._next_sequence: dict[str, int] = {}
        self._view_levels: dict[str, ViewLevel] = {}
        self._latest_events: dict[str, dict[str, Any]] = {}

    def new_job_id(self) -> str:
        """Mint a fresh id for one claim, never reused.

        Called from inside `_claim_run`/`_claim_sweep`, under `self.
        lock`, the instant a claim succeeds -- not by the page (unlike
        the eventual `start_job(jobId, request)` of design doc §7.2,
        where the page mints it before this registry exists at all);
        nothing about this id's own shape is a promise to any later
        commit, including A6's "`jobId` is the lifecycle's own `command_
        key` for a Run-object job" -- that constraint is this module's
        own later problem to satisfy, not a reason to constrain this id
        now.
        """
        job_id = uuid.uuid4().hex
        self._view_levels[job_id] = "full"
        return job_id

    def view_level(self, job_id: str) -> ViewLevel:
        """Return the job's current rendering level.

        A released job has no retained view state; callers that outlive a
        claim receive the full level rather than failing during shutdown.
        """
        with self.lock:
            return self._view_levels.get(job_id, "full")

    def set_view_level(self, job_id: str, level: ViewLevel) -> bool:
        """Set a claimed job's rendering level; return false if it is unknown."""
        with self.lock:
            if job_id not in self._view_levels:
                return False
            self._view_levels[job_id] = level
            return True

    def record_event(self, job_id: str, event_type: str, payload: Any) -> int:
        """Assign an event sequence and retain its payload for snapshots."""
        with self.lock:
            sequence = self._next_sequence.get(job_id, 0)
            self._next_sequence[job_id] = sequence + 1
            self._latest_events[job_id] = {
                "sequence": sequence,
                "type": event_type,
                "payload": payload,
            }
            return sequence

    def snapshot(self, job_id: str) -> dict[str, Any] | None:
        """Return the current view level and latest event for a claimed job."""
        with self.lock:
            if job_id not in self._view_levels:
                return None
            assigned = self._next_sequence.get(job_id, 0)
            event = self._latest_events.get(job_id)
            return {
                "viewLevel": self._view_levels[job_id],
                "sequence": assigned - 1,
                "snapshot": None if event is None else dict(event),
            }

    def next_sequence(self, job_id: str) -> int:
        """Return `job_id`'s next sequence number, starting at 0.

        Every call for the same `job_id` returns one more than the
        last, including sequences assigned through `record_event` --
        `forget` drops the bookkeeping once a job is done with it. A
        stray late call for an already-forgotten id simply starts again
        at 0, indistinguishable on the wire from a fresh job's first
        event, since `forget` is only called after release and a `job_id`
        is never reused across different claims.
        """
        with self.lock:
            sequence = self._next_sequence.get(job_id, 0)
            self._next_sequence[job_id] = sequence + 1
            return sequence

    def latest_sequence(self, job_id: str) -> int | None:
        """Return the most recently assigned sequence for `job_id`, or `None`.

        `None` means `job_id` has never had a sequence assigned through
        `next_sequence` or `record_event` (including: never claimed, or
        claimed but not yet pushed anything) -- `Api.get_job_snapshot`'s
        own "nothing yet" case, distinct from a real sequence of `0`.
        """
        with self.lock:
            assigned = self._next_sequence.get(job_id)
            return None if assigned is None else assigned - 1

    def forget(self, job_id: str) -> None:
        """Drop `job_id`'s sequence, view, and snapshot when its claim is released.

        Keeps this dict from growing for the life of the process, one
        entry per job ever claimed -- harmless at any one point, but
        needless to keep once nothing can call `next_sequence` for
        `job_id` again (`Api._release_run_claim`/`_release_sweep_claim`,
        the only callers, clear `Api._job_id` in the same breath).
        """
        with self.lock:
            self._next_sequence.pop(job_id, None)
            self._view_levels.pop(job_id, None)
            self._latest_events.pop(job_id, None)
