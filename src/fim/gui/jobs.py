"""Track GUI jobs, worker reservations, and addressed event state.

`JobAdmission` atomically admits concurrent jobs within a shared worker
budget. Each active job owns its execution state and core reservation;
completed event snapshots remain available until the owning document
closes. The same lock protects event sequences and snapshots, but is
never held while waiting for a worker or delivering to the page.
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
from typing import IO, Any, Literal, Protocol

ViewLevel = Literal["full", "summary"]
JobKind = Literal["run", "saved-run", "sweep"]
JobPriority = Literal["normal", "lower", "lowest"]
MAX_RESERVED_PERCENT = 100


class WindowEvaluator(Protocol):
    """The one page operation a job's comparison target needs."""

    def evaluate_js(self, script: str) -> Any:
        """Evaluate one JavaScript expression in the owning page."""


def usable_core_count() -> int:
    """Return the process-visible CPU count, falling back to the machine count."""
    process_cpu_count = getattr(os, "process_cpu_count", None)
    count = process_cpu_count() if callable(process_cpu_count) else None
    return max(1, count or os.cpu_count() or 1)


def reserved_core_count(
    usable_cores: int,
    reserved_cores: int = 0,
    reserved_percent: int | None = None,
) -> int:
    """Resolve the configured whole-core or percentage reserve."""
    if usable_cores < 1:
        raise ValueError("usable_cores must be at least one")
    if isinstance(reserved_cores, bool) or not isinstance(reserved_cores, int):
        raise ValueError("reserved_cores must be an integer")
    if reserved_cores < 0:
        raise ValueError("reserved_cores cannot be negative")
    if reserved_percent is not None and (
        isinstance(reserved_percent, bool)
        or not isinstance(reserved_percent, int)
        or not 0 <= reserved_percent <= MAX_RESERVED_PERCENT
    ):
        raise ValueError("reserved_percent must be between zero and one hundred")
    if reserved_percent is not None:
        return (usable_cores * reserved_percent + MAX_RESERVED_PERCENT - 1) // (
            MAX_RESERVED_PERCENT
        )
    return reserved_cores


def worker_capacity(
    usable_cores: int,
    reserved_cores: int = 0,
    reserved_percent: int | None = None,
) -> int:
    """Return the worker budget after the user reserve, with one slot minimum."""
    reserved = reserved_core_count(usable_cores, reserved_cores, reserved_percent)
    return max(1, usable_cores - reserved)


@dataclass(slots=True)
class ChildJob:
    """One child process, its message channel, and its parent lifeline."""

    process: subprocess.Popen[bytes]
    messages: Listener
    lifeline: IO[bytes] | None
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


@dataclass(slots=True)
class JobRecord:
    """Execution state and worker reservation for one admitted job."""

    job_id: str
    kind: JobKind
    cores: int
    priority: JobPriority = "lower"
    request: dict[str, Any] = field(default_factory=dict)
    cancel_event: threading.Event | None = None
    output_reservation: Path | None = None
    active_object_id: str | None = None
    child: ChildJob | None = None
    supervisor: threading.Thread | None = None
    worker: threading.Thread | None = None
    drain: threading.Thread | None = None
    previous_version_run: Path | None = None
    comparison_target: tuple[Path, WindowEvaluator] | None = None
    study_id: str | None = None
    live_deme_pair: tuple[int, int] | None = None


def start_child_job(
    object_id: str,
    *,
    results: Path | None,
    pairwise_max_demes: int,
    priority: JobPriority = "lower",
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
    worker_command.extend(("--priority", priority))
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
    """One lock protects active jobs, reservations, and addressed event state.

    Never held across anything that waits on another thread or on the
    page. Admission checks the aggregate worker reservation and records
    one job atomically, so simultaneous starts cannot exceed the budget.
    The same lock guards event sequencing and snapshots; those critical
    sections are short and must not call into the page.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self._next_sequence: dict[str, int] = {}
        self._view_levels: dict[str, ViewLevel] = {}
        self._job_priorities: dict[str, JobPriority] = {}
        self._latest_events: dict[str, dict[str, Any]] = {}
        self._active_jobs: dict[str, JobRecord] = {}

    def admit(
        self,
        kind: JobKind,
        cores: int,
        capacity: int,
        *,
        object_id: str | None = None,
        priority: JobPriority = "lower",
    ) -> tuple[JobRecord | None, int]:
        """Atomically reserve worker capacity and create an active job.

        Returns the new record and remaining capacity, or `None` and the
        unchanged available capacity when this request does not fit.
        """
        if cores < 1:
            raise ValueError("a job must reserve at least one core")
        if priority not in ("normal", "lower", "lowest"):
            raise ValueError(f"unsupported job priority: {priority!r}")
        with self.lock:
            remaining = max(
                0, capacity - sum(job.cores for job in self._active_jobs.values())
            )
            if cores > remaining or (
                object_id is not None
                and any(
                    job.request.get("object_id") == object_id
                    for job in self._active_jobs.values()
                )
            ):
                return None, remaining
            job_id = self.new_job_id()
            record = JobRecord(job_id, kind, cores, priority=priority)
            if object_id is not None:
                record.request["object_id"] = object_id
            self._active_jobs[job_id] = record
            self._job_priorities[job_id] = priority
            return record, remaining - cores

    def active_job(self, job_id: str) -> JobRecord | None:
        """Return the active record for `job_id`, if it still reserves capacity."""
        with self.lock:
            return self._active_jobs.get(job_id)

    def active_jobs(self) -> tuple[JobRecord, ...]:
        """Return a stable snapshot of all jobs that still reserve capacity."""
        with self.lock:
            return tuple(self._active_jobs.values())

    def available_cores(self, capacity: int) -> int:
        """Return the unreserved worker capacity under the registry lock."""
        with self.lock:
            used = sum(job.cores for job in self._active_jobs.values())
            return max(0, capacity - used)

    def release(self, job_id: str) -> JobRecord | None:
        """Release one job's capacity while retaining its event snapshot."""
        with self.lock:
            return self._active_jobs.pop(job_id, None)

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
        self._job_priorities[job_id] = "lower"
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
                "priority": self._job_priorities[job_id],
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
            self._active_jobs.pop(job_id, None)
            self._next_sequence.pop(job_id, None)
            self._view_levels.pop(job_id, None)
            self._job_priorities.pop(job_id, None)
            self._latest_events.pop(job_id, None)
