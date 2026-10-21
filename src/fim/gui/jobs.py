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
A7 adds each job's view level and latest event snapshot, so a page
can switch between full and summary rendering and recover after a gap.
`Api._job_id` is carried as a plain argument into each drain/push
function; background threads never read a later claim's id from `Api`.

This Release A registry still admits only one active job. Completed
snapshots remain available until the owning document closes; later
commits add the document lifecycle that removes them.
"""

from __future__ import annotations

import contextlib
import os
import queue
import secrets
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from multiprocessing.connection import Client, Connection, Listener
from pathlib import Path
from typing import Any, BinaryIO, Literal

ViewLevel = Literal["full", "summary"]


@dataclass(slots=True)
class ChildJob:
    """One child process, its message channel, and its parent lifeline."""

    process: subprocess.Popen[bytes]
    messages: Listener
    lifeline: BinaryIO | None
    _lifeline_lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )
    _lifeline_closed: bool = field(default=False, init=False, repr=False)

    def is_alive(self) -> bool:
        """Return whether the worker process is still running."""
        return self.process.poll() is None

    def join(self, timeout: float | None = None) -> None:
        """Wait up to `timeout` seconds for the worker process."""
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.process.wait(timeout)

    def send_lifeline(self, payload: bytes) -> bool:
        """Send a control message unless the parent pipe has been closed."""
        with self._lifeline_lock:
            if self._lifeline_closed or self.lifeline is None:
                return False
            self.lifeline.write(payload)
            self.lifeline.flush()
            return True

    def close_lifeline(self) -> None:
        """Tell the worker's lifeline watcher that the GUI is closing."""
        with self._lifeline_lock:
            if not self._lifeline_closed:
                if self.lifeline is not None:
                    self.lifeline.close()
                self._lifeline_closed = True


def start_child_job(
    object_id: str,
    *,
    results: Path | None,
    pairwise_max_demes: int,
    command: Sequence[str] | None = None,
) -> ChildJob:
    """Start a worker executable and return its authenticated message channel."""
    authkey = secrets.token_bytes(32)
    messages = Listener(("127.0.0.1", 0), authkey=authkey)
    host, port = messages.address
    environment = os.environ.copy()
    environment.update(
        {
            "FIM_GUI_JOB_HOST": host,
            "FIM_GUI_JOB_PORT": str(port),
            "FIM_GUI_JOB_AUTHKEY": authkey.hex(),
        }
    )
    worker_command = (
        list(command)
        if command is not None
        else [sys.executable, "-m", "fim.launcher", "--gui-job", object_id]
    )
    if results is not None:
        worker_command.extend(("--results-directory", str(results)))
    worker_command.extend(("--pairwise-max-demes", str(pairwise_max_demes)))
    try:
        process = subprocess.Popen(
            worker_command,
            stdin=subprocess.PIPE,
            env=environment,
        )
    except BaseException:
        messages.close()
        raise
    return ChildJob(process, messages, process.stdin)


def connect_message_channel() -> Connection:
    """Connect a GUI child worker to its parent's authenticated listener."""
    host = os.environ["FIM_GUI_JOB_HOST"]
    port = int(os.environ["FIM_GUI_JOB_PORT"])
    authkey = bytes.fromhex(os.environ["FIM_GUI_JOB_AUTHKEY"])
    return Client((host, port), authkey=authkey)


def _accept_child_connection(
    child: ChildJob,
    accepted: queue.Queue[Connection | OSError],
    destination: queue.Queue[Any],
    poll_interval: float,
) -> Connection | None:
    """Return the worker's connection, or `None` if it exits before connecting."""
    while True:
        try:
            candidate = accepted.get(timeout=poll_interval)
        except queue.Empty:
            if child.process.poll() is None:
                continue
            child.messages.close()
            return None
        if isinstance(candidate, OSError):
            if child.process.poll() is None:
                destination.put(("error", str(candidate)))
            return None
        return candidate


def _drain_child_connection(
    connection: Connection,
    destination: queue.Queue[Any],
    on_message: Callable[[Any], Any] | None,
) -> Any:
    """Forward ordinary child messages and retain its final terminal message."""
    terminal_message: Any = None
    while True:
        try:
            message = connection.recv()
        except EOFError:
            break
        if message[0] in {"done", "paused", "cancelled", "error"}:
            terminal_message = message
            continue
        if on_message is not None:
            message = on_message(message)
            if message is None:
                continue
        destination.put(message)
    return terminal_message


def forward_child_messages(
    child: ChildJob,
    destination: queue.Queue[Any],
    *,
    on_message: Callable[[Any], Any] | None = None,
    on_exit: Callable[[int | None], Any] | None = None,
    poll_interval: float = 0.1,
) -> None:
    """Forward child messages until it exits, then report an unexpected exit."""
    accepted: queue.Queue[Connection | OSError] = queue.Queue(maxsize=1)

    def accept_messages() -> None:
        """Accept the child's connection without blocking process supervision."""
        try:
            accepted.put(child.messages.accept())
        except OSError as error:
            accepted.put(error)

    accept_thread = threading.Thread(
        target=accept_messages,
        name=f"fim-gui-message-accept-{child.process.pid}",
        daemon=True,
    )
    accept_thread.start()
    try:
        connection = _accept_child_connection(
            child, accepted, destination, poll_interval
        )
        terminal_message = None
        if connection is not None:
            terminal_message = _drain_child_connection(
                connection, destination, on_message
            )
            connection.close()
        child.process.wait()
        if terminal_message is not None:
            destination.put(terminal_message)
        elif on_exit is not None:
            destination.put(on_exit(child.process.returncode))
    finally:
        child.close_lifeline()
        child.messages.close()


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

        A closed job has no retained view state; callers that outlive a
        document receive the full level rather than failing during shutdown.
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
        """Return the current view level and latest event for a retained job."""
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
        """Drop `job_id`'s sequence, view, and snapshot when its document closes.

        Keeps this dict from growing for the life of the process, one
        entry per job ever claimed. The owning document calls this after
        it closes; claim release alone must not discard a finished job's
        snapshot, which can still be needed to resynchronize its page.
        """
        with self.lock:
            self._next_sequence.pop(job_id, None)
            self._view_levels.pop(job_id, None)
            self._latest_events.pop(job_id, None)
