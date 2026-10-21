"use strict";

/* Addressed job events (`20261019-claude-opus-5-5-fim-gui-tabs-design.md`
 * §7.3): `fim.onJobEvent({jobId, sequence, type, payload})`, one such push
 * per real event, numbered by one sequence per job that never resets while
 * that job is claimed (`fim.gui.jobs.JobAdmission`/`fim.gui.app._push_job_
 * event`). This script tracks the sequence, de-duplicates events, and
 * retains the latest snapshot after a gap or view change.
 * `run-view-running.js` consumes accepted progress events to update the
 * attached document's live model (A11). Existing named pushes
 * (`fim.onRunProgress`, `fim.onBatchDone`, `fim.onSweepEvent`, ...) remain
 * the single-document rendering path; with multiple documents, live
 * progress uses the addressed channel to avoid changing the selected
 * document by mistake.
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
const latestJobSnapshot = new Map();
const jobViewLevel = new Map();
const jobPriority = new Map();
const closedJobIds = new Set();
const closingJobDocuments = new Map();

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
    return window.pywebview.api.get_job_snapshot(jobId).then((snapshot) => {
        applyJobSnapshot(jobId, snapshot);
        return snapshot;
    });
}

function applyJobSnapshot(jobId, snapshot) {
    if (
        closedJobIds.has(jobId) ||
        !snapshot ||
        !snapshot.ok ||
        snapshot.jobId !== jobId ||
        !Number.isInteger(snapshot.sequence)
    ) {
        return false;
    }
    const last = lastJobEventSequence.has(jobId)
        ? lastJobEventSequence.get(jobId)
        : -1;
    if (snapshot.sequence < last) {
        return false;
    }
    lastJobEventSequence.set(jobId, snapshot.sequence);
    if (snapshot.viewLevel === "full" || snapshot.viewLevel === "summary") {
        jobViewLevel.set(jobId, snapshot.viewLevel);
    }
    if (["normal", "lower", "lowest"].includes(snapshot.priority)) {
        jobPriority.set(jobId, snapshot.priority);
        const tabId = window.fim.workspace?.documentForJob(jobId);
        const state = tabId ? window.fim.workspace.get(tabId) : null;
        if (state) {
            state.effectivePriority = snapshot.priority;
            window.fim.workspace.notifyChange();
        }
    }
    if (snapshot.snapshot) {
        latestJobSnapshot.set(jobId, snapshot.snapshot);
        latestAppliedJobEventPayload.set(
            jobId,
            snapshot.snapshot.payload
        );
    }
    return true;
}

window.fim.jobs.requestSnapshot = requestJobSnapshot;
window.fim.jobs.setView = function setView(jobId, level) {
    return window.pywebview.api.set_job_view(jobId, level).then((snapshot) => {
        applyJobSnapshot(jobId, snapshot);
        return snapshot;
    });
};
window.fim.jobs.closeDocument = function closeDocument(jobId) {
    const pending = closingJobDocuments.get(jobId);
    if (pending) {
        return pending;
    }
    if (closedJobIds.has(jobId)) {
        return Promise.resolve({ ok: true });
    }
    closedJobIds.add(jobId);
    const request = window.pywebview.api.close_job_document(jobId).then((result) => {
        if (!result || !result.ok) {
            throw new Error(
                result?.message || "could not release the closed job document"
            );
        }
        lastJobEventSequence.delete(jobId);
        appliedJobEventCount.delete(jobId);
        latestAppliedJobEventPayload.delete(jobId);
        latestJobSnapshot.delete(jobId);
        jobViewLevel.delete(jobId);
        jobPriority.delete(jobId);
        return result;
    }).catch((error) => {
        closedJobIds.delete(jobId);
        throw error;
    }).finally(() => {
        closingJobDocuments.delete(jobId);
    });
    closingJobDocuments.set(jobId, request);
    return request;
};
window.fim.jobs.isDocumentClosed = function isDocumentClosed(jobId) {
    return closedJobIds.has(jobId);
};

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
 * A gap ahead of a `"status"` or `"terminal"` event asks for a snapshot
 * instead of applying the event that revealed it. A gap before
 * `"annotation"` or `"finished"` can be caused by coalesced progress,
 * so those ordered follow-up events request a snapshot and still apply.
 * Returns whether the event passed sequence checks and was applied, so
 * document-owned consumers do not process stale progress.
 *
 * @param {{jobId: string, sequence: number, type: string, payload: *}} event
 * @returns {boolean}
 */
window.fim.onJobEvent = function onJobEvent(event) {
    const { jobId, sequence, type, payload } = event;
    if (closedJobIds.has(jobId)) {
        return false;
    }
    const last = lastJobEventSequence.has(jobId)
        ? lastJobEventSequence.get(jobId)
        : null;
    if (last !== null && sequence <= last) {
        return false;
    }
    if (type !== "progress" && last !== null && sequence > last + 1) {
        window.fim.jobs.requestSnapshot(jobId);
        if (type !== "annotation" && type !== "finished") {
            return false;
        }
    }
    lastJobEventSequence.set(jobId, sequence);
    appliedJobEventCount.set(jobId, (appliedJobEventCount.get(jobId) || 0) + 1);
    latestAppliedJobEventPayload.set(jobId, payload);
    latestJobSnapshot.set(jobId, { sequence, type, payload });
    const tabId = window.fim.workspace?.documentForJob(jobId);
    const state = tabId === null ? null : window.fim.workspace.get(tabId);
    if (state) {
        state.jobState = type === "finished" ? "finished" : type === "terminal"
            ? "terminal"
            : "running";
        window.fim.workspace.notifyChange();
        if (type === "finished" && state.closeAfterStart) {
            state.closeAfterStart = false;
            window.fim.requestCloseDocument(tabId);
        }
    }
    if (
        type === "annotation" &&
        typeof window.fim.onJobAnnotation === "function"
    ) {
        window.fim.onJobAnnotation(jobId, payload);
    }
    return true;
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
window.fim.jobs.snapshotFor = function snapshotFor(jobId) {
    return latestJobSnapshot.has(jobId) ? latestJobSnapshot.get(jobId) : null;
};
window.fim.jobs.viewLevelFor = function viewLevelFor(jobId) {
    return jobViewLevel.has(jobId) ? jobViewLevel.get(jobId) : null;
};
window.fim.jobs.priorityFor = function priorityFor(jobId) {
    return jobPriority.has(jobId) ? jobPriority.get(jobId) : null;
};
