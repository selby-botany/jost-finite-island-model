"""The one lock that admits a run, a saved run, or a sweep.

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

A fuller job registry -- one record per running job, addressed by its
own `jobId`, independent of whether it is a run, a saved run, or a
sweep -- is a later commit's own job (`20261019-claude-opus-5-5-fim-gui-
tabs-design.md` §7.1, commit A5 onward); this module only closes the one
admission race A4 promises, in the file that work will eventually grow
into.
"""

from __future__ import annotations

import threading


class JobAdmission:
    """One lock, held only long enough to decide and record one claim.

    Never held across anything that waits on another thread or on the
    page -- every caller takes it, makes its decision, and releases it
    again within the same few lines (`Api._claim_run`/`_claim_sweep`,
    and their own release counterparts).
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
