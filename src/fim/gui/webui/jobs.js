"use strict";

/* Addressed job events (`20261019-claude-opus-5-5-fim-gui-tabs-design.md`
 * §7.3): `fim.onJobEvent({jobId, sequence, type, payload})`, one such push
 * per real event, numbered by one sequence per job that never resets while
 * that job is claimed (`fim.gui.jobs.JobAdmission`/`fim.gui.app._push_job_
 * event`). This script only tracks that sequence -- de-duplicating,
 * detecting a gap, and resyncing from a snapshot after one -- it does not
 * yet drive any screen (that starts at A9, once a document exists to
 * receive it). The existing named pushes (`fim.onRunProgress`,
 * `fim.onBatchDone`, `fim.onSweepEvent`, ...) are untouched and keep
 * driving the page exactly as before; `onJobEvent` is a second, additive
 * channel, not a replacement.
 *
 * Progress events are coalesced before delivery (`fim.gui.publisher`'s
 * own module docstring), so a jump in a job's own sequence is routine for
 * that type alone and is never treated as a gap; a jump before a
 * `"status"` or `"terminal"` event -- neither of which the publisher ever
 * coalesces or drops -- means a push was lost, so this asks for a
 * snapshot to resynchronize (design doc §7.3's "asks for a snapshot after
 * a gap").
 */

// The latest sequence applied for each `jobId` seen so far -- `undefined`
// (via `Map.get`, checked with `.has`) for a `jobId` never seen before,
// distinct from a real sequence of `0`.
const lastJobEventSequence = new Map();

// Test-only introspection: how many events have actually been applied
// (never a duplicate, stale, or gap-triggering one) per `jobId`, and the
// most recently applied one's own payload. Never a full history -- a
// long run can push thousands of progress ticks, and nothing here needs
// to remember more than the latest.
const appliedJobEventCount = new Map();
const latestAppliedJobEventPayload = new Map();

window.fim = window.fim || {};
window.fim.jobs = window.fim.jobs || {};

/**
 * Ask the one job named for its own current sequence, after a gap.
 * A thin, separately-reassignable wrapper around the real bridge call
 * (`window.fim.jobs.requestSnapshot`, not this function directly) so a
 * test can stub it with no real job in flight -- the same "no real run"
 * shape `test/gui/test_scrubber_frame_cap.py`'s own module docstring
 * already uses for a one-off bridge call.
 *
 * @param {string} jobId
 */
function requestJobSnapshot(jobId) {
    window.pywebview.api.get_job_snapshot(jobId).then((snapshot) => {
        if (snapshot && snapshot.ok && typeof snapshot.sequence === "number") {
            lastJobEventSequence.set(jobId, snapshot.sequence);
        }
    });
}

window.fim.jobs.requestSnapshot = requestJobSnapshot;

/**
 * Receive one addressed job push, in the order the publisher attempted it.
 *
 * A duplicate or stale sequence (`<=` whatever was last applied for this
 * `jobId`) is silently ignored -- it was already applied, or is older
 * than what was. A `jobId` never seen before is always accepted, however
 * large its first sequence number is: there is nothing yet to call a gap
 * relative to, and progress may legitimately arrive naming a `jobId`
 * this script has not yet attached to anything else (design doc §7.3:
 * "progress may arrive before the start reply; it is routed by jobId").
 * A gap ahead of a `"status"` or `"terminal"` event (never true for
 * `"progress"`, which the publisher coalesces by design, so a skipped
 * sequence there is routine, not a loss) asks for a snapshot instead of
 * applying the event that revealed it.
 *
 * @param {{jobId: string, sequence: number, type: string, payload: *}} event
 */
window.fim.onJobEvent = function onJobEvent(event) {
    const { jobId, sequence, type, payload } = event;
    const last = lastJobEventSequence.has(jobId)
        ? lastJobEventSequence.get(jobId)
        : null;
    if (last !== null && sequence <= last) {
        return;
    }
    if (type !== "progress" && last !== null && sequence > last + 1) {
        window.fim.jobs.requestSnapshot(jobId);
        return;
    }
    lastJobEventSequence.set(jobId, sequence);
    appliedJobEventCount.set(jobId, (appliedJobEventCount.get(jobId) || 0) + 1);
    latestAppliedJobEventPayload.set(jobId, payload);
};

// Test-only introspection -- no production caller reads these.
window.fim.jobs.lastSequenceFor = function lastSequenceFor(jobId) {
    return lastJobEventSequence.has(jobId) ? lastJobEventSequence.get(jobId) : null;
};
window.fim.jobs.appliedCountFor = function appliedCountFor(jobId) {
    return appliedJobEventCount.get(jobId) || 0;
};
window.fim.jobs.latestAppliedFor = function latestAppliedFor(jobId) {
    return latestAppliedJobEventPayload.has(jobId)
        ? latestAppliedJobEventPayload.get(jobId)
        : null;
};
