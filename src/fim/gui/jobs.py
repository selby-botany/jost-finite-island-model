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
adds the one other piece of state every job push now needs: a `jobId`,
minted the instant a claim succeeds, and a sequence number, one per
`jobId`, that every one of that job's own pushes (`fim.gui.app._push_
job_event`) counts up from zero. `Api._job_id` (set by `_claim_run`/
`_claim_sweep`, cleared by their own release counterparts) is the one
currently-claimed job's id, carried as a plain argument into whichever
drain/push function needs to tag its own pushes with it -- exactly how
`output_directory` already is -- never read back live from `Api` by a
background thread, which could by then be tagging a *different* job's
pushes with a stale id.

A fuller job registry -- one record per running job, independent of
whether it is a run, a saved run, or a sweep, holding enough to answer
`list_jobs`/`set_job_view`/`get_job_snapshot` in full -- is a later
commit's own job (`20261019-claude-opus-5-5-fim-gui-tabs-design.md`
§7.1-§7.3, commit A7 onward); this module only mints the id and counts
the sequence, in the file that work will eventually grow into.
"""

from __future__ import annotations

import threading
import uuid


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
        return uuid.uuid4().hex

    def next_sequence(self, job_id: str) -> int:
        """Return `job_id`'s next sequence number, starting at 0.

        Every call for the same `job_id` returns one more than the
        last, forever (not reset by anything here) -- `forget` only
        drops the bookkeeping once a job is done with it; a stray,
        late call for an already-forgotten id simply starts again at 0,
        indistinguishable on the wire from a fresh job's own first
        event, since the two can never be confused (`forget` is only
        ever called once a job's own claim is released, and a `job_id`
        is never reused across two different claims).
        """
        with self.lock:
            sequence = self._next_sequence.get(job_id, 0)
            self._next_sequence[job_id] = sequence + 1
            return sequence

    def latest_sequence(self, job_id: str) -> int | None:
        """Return the most recently assigned sequence for `job_id`, or `None`.

        `None` means `job_id` has never had `next_sequence` called for
        it at all (including: never claimed, or claimed but not yet
        pushed anything) -- `Api.get_job_snapshot`'s own "nothing yet"
        case, distinct from a real sequence of `0`.
        """
        with self.lock:
            assigned = self._next_sequence.get(job_id)
            return None if assigned is None else assigned - 1

    def forget(self, job_id: str) -> None:
        """Drop `job_id`'s own sequence bookkeeping once its claim is released.

        Keeps this dict from growing for the life of the process, one
        entry per job ever claimed -- harmless at any one point, but
        needless to keep once nothing can call `next_sequence` for
        `job_id` again (`Api._release_run_claim`/`_release_sweep_claim`,
        the only callers, clear `Api._job_id` in the same breath).
        """
        with self.lock:
            self._next_sequence.pop(job_id, None)
