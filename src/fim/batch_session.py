"""Resumable execution sessions for a batch of replicates.

`BatchSession` is the stepwise form of `fim.engine.run_batch`: `create`
builds the first window of lanes, `advance` runs a bounded number of
*ticks* (one `run_batch` loop iteration each: every running lane advances
one generation, finished lanes are finalized, the accepted prefix is fed to
the adaptive stopping rule, and freed window slots are refilled), and
`finalize` returns the same results `fim.engine.fim` returns for the same
configuration. Between ticks the session sits at a safe point.

`capture` records, at a safe point, the coordinator (how many lanes were
built and admitted, where an adaptive stop fell) and one lane state per
built lane, in the layout `fim.execution.export_lane` writes, under the
array prefix ``lane.<index>.``. A finished lane's state is taken just
before it was finalized; restoring it finalizes it again, which is
deterministic, so its `RunResult` is never stored. The averaging-window
planner and the adaptive stopping monitor are rebuilt by replaying those
results in replicate order, exactly the order `run_batch` fed them, so
neither needs a serialized form.

An adaptive stop discards the lanes past the stopping replicate only in
`finalize`, after the caller has had the chance to checkpoint the stop:
an earlier checkpoint still names those lanes' logs, so deleting them at
the stop would leave a crash between the stop and the next checkpoint
unrecoverable.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Final

import numpy.typing as npt

from fim.convergence.batch_window import BatchWindowPlanner
from fim.convergence.monitor import ConvergenceMonitor
from fim.engine import (
    Advancer,
    Clock,
    JitOption,
    ReplicaLane,
    RunResult,
    SequentialAdvancer,
    VectorizedAdvancer,
)
from fim.engine.batch import waiting_for_window
from fim.engine.identity import derive_replicate_run_id, params_for_replicate
from fim.engine.lanes import build_replica_lane, finalize_replica_lane
from fim.engine.monitors import (
    batch_window_planner,
    replicate_monitor,
    replicate_stopping_values,
    window_noise,
)
from fim.engine.records import utc_now
from fim.execution import (
    SessionBackend,
    SessionCapture,
    SessionRestoreError,
    UnsupportedSessionError,
    check_backend_buildable,
    check_payload_identity,
    export_lane,
    resolve_session_backend,
    restore_lane,
)
from fim.model.params import SimulationParams
from fim.persistence.binary_store import BinaryLogStore
from fim.persistence.run_checkpoint import (
    FaultHook,
    LoadedCheckpoint,
    commit_checkpoint,
)
from fim.persistence.run_lifecycle import CheckpointReference, RunPhase
from fim.persistence.run_objects import configuration_digest
from fim.persistence.run_ownership import ExecutionLease
from fim.persistence.scalar_snapshot import SnapshotError
from fim.persistence.store import (
    ReplicateFanoutStore,
    TrajectoryStore,
    close_store,
    equilibrium_store_for,
)

BATCH_CHECKPOINT_KIND: Final = "batch-session"
"""The `kind` a batch session's checkpoint payload carries."""

BATCH_CHECKPOINT_VERSION: Final = 1
"""Version of the batch payload layout this build writes."""

READABLE_BATCH_CHECKPOINT_VERSIONS: Final = (1,)
"""Batch payload versions this build restores."""

MINIMUM_BATCH_REPLICATES: Final = 2
"""The fewest replicates a batch session runs; one runs as a scalar session."""

LANE_ACTIVE: Final = "active"
LANE_FINISHED: Final = "finished"

StoreFactory = Callable[[str], TrajectoryStore]


@dataclass(frozen=True, slots=True)
class _FinishedLane:
    """A finished lane's state as it was just before finalizing.

    Args:
        state: The lane metadata `export_lane` wrote.
        arrays: The lane's arrays, unprefixed.
        ended_at: The manifest's `ended_at`, so finalizing again on restore
            reproduces the manifest exactly.
    """

    state: dict[str, Any]
    arrays: dict[str, npt.NDArray[Any]]
    ended_at: str


class BatchSession:
    """A batch of replicates, advanced in bounded ticks between safe points.

    Build one with `create`. The session owns its fan-out trajectory store
    (one child store per replicate): `finalize` and `close` close it, and
    the session is a context manager that closes it on exit.
    """

    def __init__(
        self,
        params: SimulationParams,
        store: ReplicateFanoutStore,
        run_id: str | None,
        clock: Clock,
        engine_backend: SessionBackend,
    ) -> None:
        """Set up an empty session; use `create` or `restore` instead.

        Args:
            params: The batch configuration.
            store: The fan-out store every lane writes through.
            run_id: The batch's run identifier, or `None` to derive each
                replicate's from `params`.
            clock: Timestamp source for the manifests.
            engine_backend: The resolved backend recorded in the manifests.
        """
        self._params = params
        self._store = store
        self._run_id = run_id
        self._clock = clock
        self._engine_backend: SessionBackend = engine_backend
        self._jit: JitOption = params.jit
        self._advancer: Advancer = (
            VectorizedAdvancer()
            if engine_backend == "generational-vector"
            else SequentialAdvancer(jit=params.jit == "numba")
        )
        self._planner: BatchWindowPlanner | None = batch_window_planner(params)
        self._cross_monitor: ConvergenceMonitor | None = replicate_monitor(params)
        self._lanes: list[ReplicaLane] = []
        self._windows: list[int | None] = []
        self._finished: dict[int, _FinishedLane | None] = {}
        self._admitted_count = 0
        self._accepted_count: int | None = None
        self._ticks = 0
        self._discarded_run_ids: list[str] = []
        self._stale_from: int | None = None
        self._results: tuple[RunResult, ...] | None = None
        self._closed = False

    def __enter__(self) -> BatchSession:
        """Return this session for a `with` block."""
        return self

    def __exit__(self, *_: object) -> None:
        """Close the session's stores, whether or not it finished."""
        self.close()

    @classmethod
    def create(
        cls,
        params: SimulationParams,
        store_factory: StoreFactory,
        *,
        run_id: str | None = None,
        clock: Clock | None = None,
    ) -> BatchSession:
        """Build the first window of lanes, each through generation zero.

        Args:
            params: A validated configuration of two or more replicates.
            store_factory: Builds one replicate's own store from its run id.
            run_id: The batch's run identifier; replicate run ids derive
                from it exactly as `fim.engine.fim` derives them.
            clock: Timestamp source; the current UTC time when omitted.

        Returns:
            A session at the tick-zero safe point.

        Raises:
            UnsupportedSessionError: If `params` names one replicate or a
                backend configuration `fim.engine.fim` itself refuses.
        """
        engine_backend = check_batch_session_support(params)
        session = cls(
            params,
            ReplicateFanoutStore(store_factory),
            run_id,
            clock if clock is not None else utc_now,
            engine_backend,
        )
        try:
            window_size = params.max_concurrent_replicates or params.n_replicates
            for _ in range(min(window_size, params.n_replicates)):
                session._build_next_lane()
        except BaseException:
            session.close()
            raise
        return session

    @classmethod
    def restore(
        cls,
        params: SimulationParams,
        checkpoint: LoadedCheckpoint,
        store_factory: Callable[[str], BinaryLogStore],
        *,
        clock: Clock | None = None,
    ) -> BatchSession:
        """Rebuild a session at the safe point a checkpoint describes.

        Each built lane's log is cut back to its recorded position; finished
        lanes are finalized again; the window planner and the adaptive
        stopping monitor are rebuilt by replaying finished results in
        replicate order. A lane built after the checkpoint by the
        interrupted attempt has its leftover log discarded before it is
        built again.

        Args:
            params: The configuration the batch was started with.
            checkpoint: A verified checkpoint (`load_checkpoint`).
            store_factory: Builds one replicate's existing binary log.
            clock: Timestamp source; the current UTC time when omitted.

        Returns:
            A session at the checkpoint's tick.

        Raises:
            SessionRestoreError: If the checkpoint is not a batch session's,
                was taken under another configuration, or is inconsistent.
        """
        payload = checkpoint.payload
        backend, _ = check_payload_identity(
            payload,
            params,
            kind=BATCH_CHECKPOINT_KIND,
            versions=READABLE_BATCH_CHECKPOINT_VERSIONS,
        )
        run_id = payload.get("run_id")
        if run_id is not None and not isinstance(run_id, str):
            raise SessionRestoreError("the batch run_id must be a string or null")
        session = cls(
            params,
            ReplicateFanoutStore(store_factory),
            run_id,
            clock if clock is not None else utc_now,
            backend,
        )
        try:
            session._restore_coordinator(payload, checkpoint.arrays)
        except (ValueError, KeyError, TypeError) as error:
            session.close()
            raise SessionRestoreError(
                f"checkpoint {checkpoint.reference.checkpoint_id} cannot be "
                f"restored: {error}"
            ) from error
        except BaseException:
            session.close()
            raise
        if session._ticks != checkpoint.reference.committed_generation:
            session.close()
            raise SessionRestoreError(
                f"checkpoint {checkpoint.reference.checkpoint_id} holds tick "
                f"{session._ticks}, not the committed tick "
                f"{checkpoint.reference.committed_generation}"
            )
        return session

    @property
    def generation(self) -> int:
        """The tick count, which the lifecycle records as committed progress.

        Each tick advances every running lane by one generation, so the
        count is the generation the longest-running lane has reached.
        """
        return self._ticks

    @property
    def lanes(self) -> Sequence[ReplicaLane]:
        """Every lane built so far, in replicate order; read-only use only."""
        return tuple(self._lanes)

    @property
    def params(self) -> SimulationParams:
        """The batch's configuration."""
        return self._params

    @property
    def run_id(self) -> str | None:
        """The batch's run ID; replicate IDs are `<run_id>-rNNN`."""
        return self._run_id

    @property
    def stopped(self) -> bool:
        """Whether every lane finished or the adaptive stop fired."""
        return self._accepted_count is not None or not any(
            lane.active for lane in self._lanes
        )

    @property
    def store(self) -> ReplicateFanoutStore:
        """The fan-out store every lane writes through."""
        return self._store

    @property
    def ticks(self) -> int:
        """How many ticks have run; a checkpoint's committed generation."""
        return self._ticks

    def advance(self, ticks: int | None = None) -> bool:
        """Run up to `ticks` more ticks, stopping early once the batch stops.

        Args:
            ticks: The most ticks to run; until the batch stops when omitted.

        Returns:
            Whether the batch has stopped.

        Raises:
            ValueError: If `ticks` is negative or the session is finalized
                or closed.
        """
        if ticks is not None and ticks < 0:
            raise ValueError(f"ticks must be non-negative; got {ticks}")
        self._require_open()
        remaining = ticks
        while not self.stopped and (remaining is None or remaining > 0):
            self._tick()
            if remaining is not None:
                remaining -= 1
        return self.stopped

    def capture(self) -> SessionCapture:
        """Export the batch's exact state at the current safe point.

        Returns:
            The checkpoint payload and arrays; the committed generation is
            the tick count.

        Raises:
            UnsupportedSessionError: If a lane's store is not a binary log.
            ValueError: If the session is finalized or closed.
        """
        self._require_open()
        arrays: dict[str, npt.NDArray[Any]] = {}
        entries: list[dict[str, Any]] = []
        discarded = list(self._discarded_run_ids)
        for lane, window in zip(self._lanes, self._windows, strict=True):
            index = lane.replica_index
            if self._accepted_count is not None and index >= self._accepted_count:
                # Past an adaptive stop: recorded only so finalize (or a
                # restored attempt) deletes the replicate's logs.
                discarded.append(lane.run_id)
                continue
            entry: dict[str, Any] = {
                "ended_at": None,
                "index": index,
                "lane": None,
                "run_id": lane.run_id,
                "status": LANE_ACTIVE,
                "window": window,
            }
            if lane.result is not None:
                finished = self._finished.get(index)
                if finished is None:
                    raise UnsupportedSessionError(
                        f"replicate {lane.run_id} finished without a capturable "
                        "state; only binary trajectory logs can be checkpointed"
                    )
                entry["status"] = LANE_FINISHED
                entry["ended_at"] = finished.ended_at
                entry["lane"] = finished.state
                lane_arrays = finished.arrays
            else:
                entry["lane"], lane_arrays = export_lane(
                    lane, self._store.store_for(lane.run_id)
                )
            arrays |= {
                f"{_lane_prefix(index)}{name}": array
                for name, array in lane_arrays.items()
            }
            entries.append(entry)
        payload: dict[str, Any] = {
            "accepted_count": self._accepted_count,
            "admitted_count": self._admitted_count,
            "configuration_digest": configuration_digest(self._params),
            "discarded_run_ids": discarded,
            "engine_backend": self._engine_backend,
            "jit": self._jit,
            "kind": BATCH_CHECKPOINT_KIND,
            "lanes": entries,
            "run_id": self._run_id,
            "ticks": self._ticks,
            "version": BATCH_CHECKPOINT_VERSION,
        }
        return SessionCapture(
            payload=payload,
            arrays=arrays,
            committed_generation=self._ticks,
            phase=RunPhase.FINALIZING if self.stopped else RunPhase.SIMULATING,
        )

    def checkpoint(
        self, lease: ExecutionLease, *, fault: FaultHook | None = None
    ) -> CheckpointReference:
        """Capture the batch and publish it as the object's latest checkpoint.

        Args:
            lease: The owning attempt's execution lease.
            fault: Test hook passed to `commit_checkpoint`.

        Returns:
            The reference now recorded in the object's `run.json`.
        """
        capture = self.capture()
        return commit_checkpoint(
            lease,
            payload=capture.payload,
            arrays=capture.arrays,
            committed_generation=capture.committed_generation,
            phase=capture.phase,
            fault=fault,
        )

    def close(self) -> None:
        """Release every lane's open files; idempotent."""
        if not self._closed:
            self._closed = True
            close_store(self._store)

    def commit_logs(self) -> int:
        """Make every active lane's trajectory durable up to this tick.

        Used when a batch is cancelled: the partial logs are kept, and the
        tick count is what the lifecycle records as committed.

        Returns:
            The tick count.

        Raises:
            ValueError: If the session is finalized or closed.
        """
        self._require_open()
        for lane in self._lanes:
            store = self._store.store_for(lane.run_id)
            if lane.active and isinstance(store, BinaryLogStore):
                store.checkpoint()
        return self._ticks

    def finalize(self) -> tuple[RunResult, ...]:
        """Return the kept replicates' results once the batch has stopped.

        After an adaptive stop, every lane past the stopping replicate is
        discarded from the store first, as `fim.engine.run_batch` does.

        Returns:
            The same results `fim.engine.fim` returns for this configuration.

        Raises:
            ValueError: If the batch has not stopped, or the session was
                closed without finishing.
        """
        if self._results is not None:
            return self._results
        self._require_open()
        if not self.stopped:
            raise ValueError(f"the batch has not stopped after {self._ticks} tick(s)")
        try:
            kept = self._lanes
            if self._accepted_count is not None:
                kept = self._lanes[: self._accepted_count]
                self._discard_past_stop()
            results = tuple(_require_result(lane) for lane in kept)
        finally:
            self.close()
        self._results = tuple(
            replace(
                result,
                manifest=replace(
                    result.manifest,
                    engine_backend=self._engine_backend,
                    jit=self._jit,
                ),
            )
            for result in results
        )
        return self._results

    def _admit_prefix(self) -> None:
        """Feed the accepted prefix to the adaptive stopping rule, in order."""
        monitor = self._cross_monitor
        if monitor is None:
            return
        while (
            self._admitted_count < len(self._lanes)
            and self._lanes[self._admitted_count].result is not None
        ):
            admitted = _require_result(self._lanes[self._admitted_count])
            self._admitted_count += 1
            outcome = monitor.record(
                self._admitted_count,
                replicate_stopping_values(
                    admitted, self._params.convergence_statistics
                ),
            )
            if outcome.stopped:
                self._accepted_count = self._admitted_count
                return

    def _build_next_lane(self) -> None:
        """Build the next unbuilt replicate's lane through generation zero."""
        index = len(self._lanes)
        if self._stale_from is not None and index >= self._stale_from:
            # The interrupted attempt may have built this lane after the
            # checkpoint; its log would block a fresh one.
            _discard_run(
                self._store,
                derive_replicate_run_id(self._params, index, self._run_id),
            )
        window = self._planner.window_for(index) if self._planner is not None else None
        lane = build_replica_lane(
            self._params, index, self._run_id, self._store, self._clock, self._planner
        )
        self._lanes.append(lane)
        self._windows.append(window)

    def _discard_past_stop(self) -> None:
        """Discard every lane past the stopping replicate from the store."""
        accepted = self._accepted_count
        if accepted is None:
            return
        for lane in self._lanes[accepted:]:
            self._store.discard(lane.run_id)
            if lane.equilibrium_store is not None:
                lane.equilibrium_store.discard(lane.run_id)
            lane.active = False
            lane.result = None
            lane.vectorized_state = None
        for run_id in self._discarded_run_ids:
            _discard_run(self._store, run_id)

    def _finish_lane(self, lane: ReplicaLane) -> None:
        """Finalize a newly stopped lane, keeping its state first."""
        lane.active = False
        try:
            state, arrays = export_lane(lane, self._store.store_for(lane.run_id))
        except UnsupportedSessionError:
            state, arrays = None, None
        lane.result = finalize_replica_lane(lane, self._clock, self._store)
        self._finished[lane.replica_index] = (
            _FinishedLane(state, arrays, lane.result.manifest.ended_at)
            if state is not None and arrays is not None
            else None
        )

    def _record_window(self, lane: ReplicaLane) -> None:
        """Record a finished first-wave lane with the window planner."""
        planner = self._planner
        if planner is None:
            return
        matched = planner.record(
            lane.replica_index,
            window_noise(
                _require_result(lane).report, self._params.convergence_statistics
            ),
        )
        if matched is not None:
            # The first wave is measured: every replicate that was waiting
            # for its window now knows it.
            for waiting in self._lanes:
                if waiting.active and waiting.monitor.awaiting_window:
                    waiting.monitor.set_averaging_window(matched)

    def _require_open(self) -> None:
        """Raise if the session can no longer advance or capture."""
        if self._results is not None:
            raise ValueError("the batch is already finalized")
        if self._closed:
            raise ValueError("the batch session is closed")

    def _restore_coordinator(
        self, payload: Mapping[str, Any], arrays: Mapping[str, npt.NDArray[Any]]
    ) -> None:
        """Rebuild lanes, planner and stopping monitor from a checkpoint.

        Raises:
            SnapshotError: If the coordinator or a lane is inconsistent.
        """
        params = self._params
        ticks = _integer(payload, "ticks")
        admitted = _integer(payload, "admitted_count")
        accepted = payload.get("accepted_count")
        if accepted is not None and not isinstance(accepted, int):
            raise SnapshotError("accepted_count must be an integer or null")
        discarded = payload.get("discarded_run_ids")
        if not isinstance(discarded, list) or not all(
            isinstance(run_id, str) for run_id in discarded
        ):
            raise SnapshotError("discarded_run_ids must be a list of strings")
        entries = payload.get("lanes")
        if not isinstance(entries, list) or len(entries) > params.n_replicates:
            raise SnapshotError("lanes must be a list no longer than the batch")
        if accepted is None and discarded:
            raise SnapshotError("replicates are discarded before any stop")
        if accepted is not None and len(entries) != accepted:
            raise SnapshotError("a stopped batch lists exactly its accepted lanes")
        past_stop = {
            derive_replicate_run_id(params, index, self._run_id)
            for index in range(len(entries), params.n_replicates)
        }
        if not set(discarded) <= past_stop or len(set(discarded)) != len(discarded):
            raise SnapshotError("discarded_run_ids must name replicates past the stop")
        self._discarded_run_ids = list(discarded)
        for index, entry in enumerate(entries):
            if not isinstance(entry, Mapping) or entry.get("index") != index:
                raise SnapshotError(f"lane entry {index} is malformed")
            self._restore_entry(index, entry, arrays)
        self._ticks = ticks
        self._stale_from = len(entries)
        self._replay(admitted, accepted)

    def _restore_entry(
        self,
        index: int,
        entry: Mapping[str, Any],
        arrays: Mapping[str, npt.NDArray[Any]],
    ) -> None:
        """Rebuild one lane from its coordinator entry."""
        params = self._params
        window = entry.get("window")
        if window is not None and (not isinstance(window, int) or window < 1):
            raise SnapshotError(f"lane {index} window is malformed")
        if self._planner is None and window is not None:
            raise SnapshotError(f"lane {index} has a window but the batch has none")
        run_id = derive_replicate_run_id(params, index, self._run_id)
        if entry.get("run_id") != run_id:
            raise SnapshotError(f"lane {index} is not replicate {run_id!r}")
        status = entry.get("status")
        if status not in (LANE_ACTIVE, LANE_FINISHED):
            raise SnapshotError(f"lane {index} status {status!r} is unknown")
        state = entry.get("lane")
        if not isinstance(state, Mapping):
            raise SnapshotError(f"lane {index} has no state")
        child = self._store.store_for(run_id)
        if not isinstance(child, BinaryLogStore):
            raise SnapshotError(f"lane {index}'s store is not a binary log")
        prefix = _lane_prefix(index)
        lane_arrays = {
            name.removeprefix(prefix): array
            for name, array in arrays.items()
            if name.startswith(prefix)
        }
        lane = restore_lane(
            params_for_replicate(params, index, window),
            state,
            lane_arrays,
            child,
            replica_index=index,
            awaiting_window=self._planner is not None and window is None,
        )
        self._lanes.append(lane)
        self._windows.append(window)
        if status == LANE_FINISHED:
            ended_at = entry.get("ended_at")
            if not isinstance(ended_at, str):
                raise SnapshotError(f"lane {index} ended_at must be a string")
            if not lane.monitor.should_stop():
                raise SnapshotError(f"lane {index} is finished but not stopped")
            lane.active = False
            result = finalize_replica_lane(lane, self._clock, self._store)
            lane.result = replace(
                result, manifest=replace(result.manifest, ended_at=ended_at)
            )
            self._finished[index] = _FinishedLane(dict(state), lane_arrays, ended_at)

    def _replay(self, admitted: int, accepted: int | None) -> None:
        """Replay finished results into the window planner and stop rule.

        Raises:
            SnapshotError: If the replay disagrees with the recorded counts.
        """
        planner = self._planner
        if planner is not None:
            for lane in self._lanes:
                if lane.result is not None:
                    planner.record(
                        lane.replica_index,
                        window_noise(
                            lane.result.report, self._params.convergence_statistics
                        ),
                    )
            for lane, window in zip(self._lanes, self._windows, strict=True):
                # A lane built before the first wave was measured waits for
                # its window (`None`); every other lane was built with the
                # window the replayed planner gives it.
                expected = planner.window_for(lane.replica_index)
                waiting = window is None and lane.replica_index >= planner.first_wave
                if window != expected and not waiting:
                    raise SnapshotError(
                        f"lane {lane.replica_index} window {window!r} disagrees "
                        f"with the planner's {expected!r}"
                    )
        monitor = self._cross_monitor
        if monitor is None:
            if admitted != 0 or accepted is not None:
                raise SnapshotError("a batch without adaptive stop admits nothing")
            return
        if admitted > len(self._lanes) and accepted is None:
            raise SnapshotError("admitted_count exceeds the lanes built")
        for count in range(1, admitted + 1):
            if count > len(self._lanes) or self._lanes[count - 1].result is None:
                raise SnapshotError(f"admitted replicate {count} has not finished")
            outcome = monitor.record(
                count,
                replicate_stopping_values(
                    _require_result(self._lanes[count - 1]),
                    self._params.convergence_statistics,
                ),
            )
            if outcome.stopped != (count == accepted):
                raise SnapshotError(
                    "the adaptive stop replays differently from the checkpoint"
                )
        self._admitted_count = admitted
        self._accepted_count = accepted

    def _tick(self) -> None:
        """Run one `fim.engine.run_batch` loop iteration."""
        planner = self._planner
        running = [
            lane
            for lane in self._lanes
            if lane.active and not waiting_for_window(lane, planner)
        ]
        newly_stopped = self._advancer.advance(running, self._store)
        for lane in sorted(newly_stopped, key=lambda lane: lane.replica_index):
            self._finish_lane(lane)
            self._record_window(lane)
        self._admit_prefix()
        self._ticks += 1
        if self._accepted_count is not None:
            return
        # Refill freed window slots only once the stop is ruled out.
        for _ in newly_stopped:
            if len(self._lanes) < self._params.n_replicates:
                self._build_next_lane()


def check_batch_session_support(params: SimulationParams) -> SessionBackend:
    """Refuse a batch configuration a resumable batch session cannot run.

    Args:
        params: The configuration to run.

    Returns:
        The resolved backend.

    Raises:
        UnsupportedSessionError: If `params` names a single replicate (a
            `fim.execution.ScalarSession` runs that) or a backend the
            blocking call itself refuses.
    """
    engine_backend = resolve_session_backend(params)
    if params.n_replicates < MINIMUM_BATCH_REPLICATES:
        raise UnsupportedSessionError(
            "a batch session runs two or more replicates; "
            f"n_replicates is {params.n_replicates}"
        )
    check_backend_buildable(params, engine_backend)
    return engine_backend


def _discard_run(store: ReplicateFanoutStore, run_id: str) -> None:
    """Discard a replicate's logs (and its ancestral-phase log) by run id."""
    child = store.store_for(run_id)
    child.discard(run_id)
    equilibrium_store_for(child, run_id).discard(run_id)


def _integer(payload: Mapping[str, Any], name: str) -> int:
    """Return a non-negative integer field of the coordinator payload."""
    value = payload.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SnapshotError(f"{name} must be a non-negative integer")
    return value


def _lane_prefix(index: int) -> str:
    """Return the array-name prefix of lane `index` inside a checkpoint."""
    return f"lane.{index}."


def _require_result(lane: ReplicaLane) -> RunResult:
    """Return a finalized lane's result, or raise if it has none."""
    if lane.result is None:
        raise RuntimeError(f"replicate {lane.run_id} has no result")
    return lane.result
