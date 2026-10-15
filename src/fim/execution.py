"""Resumable execution sessions for one scalar replicate.

A session is the stepwise form of the blocking `fim.engine.fim` call for a
single replicate: `ScalarSession.create` builds generation zero,
`ScalarSession.advance` runs a bounded number of generations, and
`ScalarSession.finalize` produces the same `fim.engine.RunResult` the
blocking call returns. Between `advance` calls the session sits at a safe
point, where nothing is half-written, so a caller can observe a pause or
cancel request and capture a checkpoint.

The session reuses the generation-first lane machinery
(`fim.engine.ReplicaLane`, `build_replica_lane`, `SequentialAdvancer`,
`VectorizedAdvancer`, `finalize_replica_lane`) that the golden-parity
tests already prove is bit-identical to the lineal reference loop for the
same seed. Every single-replicate backend is supported: ``lineal`` and
``generational`` step a `ModelState`; ``generational-vector`` steps a
dense `fim.model.vector_block.VectorBlock`, which a checkpoint then holds
in place of the population and registries.

At a safe point `ScalarSession.capture` exports everything the run needs
to continue exactly (population, registries, random state, convergence
monitor, the committed position of the trajectory log and of the
ancestral-phase log, and the equilibration outcome);
`ScalarSession.restore` rebuilds a session from it, cutting the logs back
to those positions, so the resumed run produces the same generations,
report and manifest as one that never stopped. `ScalarSession.checkpoint`
publishes a capture under an execution lease.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Final, Literal

import numpy as np
import numpy.typing as npt

from fim.engine import (
    Advancer,
    Clock,
    JitOption,
    ReplicaLane,
    RunResult,
    SequentialAdvancer,
    VectorizedAdvancer,
    build_engine_backend,
)
from fim.engine.backends import numba_is_available, resolve_auto_engine_backend
from fim.engine.lanes import (
    build_replica_lane,
    equilibrium_frame_layout,
    finalize_replica_lane,
    frame_layout,
)
from fim.engine.monitors import burn_in_monitor
from fim.engine.records import utc_now
from fim.errors import StoreError, UnsupportedError
from fim.model.allele import AlleleRegistry
from fim.model.initial import EquilibrationOutcome
from fim.model.operators import expand_population_sizes
from fim.model.params import SimulationParams
from fim.model.vector_block import VectorBlock, VectorMigration
from fim.persistence import tlog
from fim.persistence.binary_store import BinaryLogStore
from fim.persistence.retention import TrajectoryRetention
from fim.persistence.run_checkpoint import (
    FaultHook,
    LoadedCheckpoint,
    commit_checkpoint,
)
from fim.persistence.run_lifecycle import CheckpointReference, RunPhase
from fim.persistence.run_objects import configuration_digest
from fim.persistence.run_ownership import ExecutionLease
from fim.persistence.scalar_snapshot import (
    SnapshotError,
    export_population,
    export_registries,
    export_rng,
    import_population,
    import_registries,
    import_rng,
)
from fim.persistence.store import (
    InMemoryTrajectoryStore,
    TrajectoryStore,
    close_store,
    equilibrium_store_for,
)

SessionBackend = Literal["lineal", "generational", "generational-vector"]
SESSION_BACKENDS: tuple[SessionBackend, ...] = (
    "lineal",
    "generational",
    "generational-vector",
)

SESSION_CHECKPOINT_KIND: Final = "scalar-session"
"""The `kind` a session's checkpoint payload carries.

"Scalar" names one replicate lane, as opposed to a batch of them; the kind
covers every backend a session runs, the vector backend included.
"""

SESSION_CHECKPOINT_VERSION: Final = 2
"""Version of the session payload layout this build writes.

Version 2 adds the `vector` block of the generational-vector backend; with
it, `population` and `registries` are `null` (the block holds them).
Version 1 payloads never have a block.
"""

READABLE_SESSION_CHECKPOINT_VERSIONS: Final = (1, 2)
"""Payload versions this build restores."""

EQUILIBRATION_HISTORY: Final = "equilibration.history"
"""Array holding the ancestral phase's `H_S` history, when there was one."""

VECTOR_PREFIX: Final = "vector."
"""Prefix of the vector block's arrays inside a checkpoint."""


class UnsupportedSessionError(UnsupportedError):
    """Raised when a configuration cannot run as a resumable scalar session."""


class SessionRestoreError(StoreError, ValueError):
    """Raised when a checkpoint cannot continue the given configuration."""


@dataclass(frozen=True, slots=True)
class SessionCapture:
    """Everything a checkpoint of a scalar session holds.

    Args:
        payload: JSON-safe metadata; integers that may exceed 2**53 are
            decimal strings.
        arrays: The population, registry, monitor and equilibration arrays.
        committed_generation: The generation the capture describes.
        phase: `RunPhase.FINALIZING` once the run has stopped, otherwise
            `RunPhase.SIMULATING`.
    """

    payload: dict[str, Any]
    arrays: dict[str, npt.NDArray[Any]]
    committed_generation: int
    phase: RunPhase


class ScalarSession:
    """One scalar replicate, advanced in bounded steps between safe points.

    Build one with `create`. The session owns its trajectory store: it is
    closed by `finalize` or `close`, and the session is a context manager
    that closes it on exit.
    """

    def __init__(
        self,
        lane: ReplicaLane,
        store: TrajectoryStore,
        clock: Clock,
        engine_backend: SessionBackend,
        jit: JitOption,
    ) -> None:
        """Wrap an already-built lane; use `create` instead of calling this.

        Args:
            lane: The replicate's working state, at a safe point.
            store: Where generations are persisted.
            clock: Timestamp source for the manifest.
            engine_backend: The resolved backend recorded in the manifest.
            jit: The JIT option the drift step uses and the manifest records.
        """
        self._lane = lane
        self._store = store
        self._clock = clock
        self._engine_backend: SessionBackend = engine_backend
        self._jit: JitOption = jit
        self._advancer: Advancer = (
            VectorizedAdvancer()
            if engine_backend == "generational-vector"
            else SequentialAdvancer(jit=jit == "numba")
        )
        self._result: RunResult | None = None
        self._closed = False

    def __enter__(self) -> ScalarSession:
        """Return this session for a `with` block."""
        return self

    def __exit__(self, *_: object) -> None:
        """Close the session's store, whether or not it finished."""
        self.close()

    @classmethod
    def create(
        cls,
        params: SimulationParams,
        store: TrajectoryStore | None = None,
        *,
        run_id: str | None = None,
        clock: Clock | None = None,
    ) -> ScalarSession:
        """Build generation zero of a single replicate and persist it.

        Args:
            params: A validated single-replicate configuration.
            store: Where to persist generations; in memory when omitted.
            run_id: The scientific run identifier; derived from `params`
                when omitted, exactly as `fim.engine.fim` derives it.
            clock: Timestamp source; the current UTC time when omitted.

        Returns:
            A session at the generation-zero safe point.

        Raises:
            UnsupportedSessionError: If `params` asks for more than one
                replicate or names a backend configuration `fim.engine.fim`
                itself refuses.
        """
        engine_backend = check_session_support(params)
        run_clock = clock if clock is not None else utc_now
        trajectory_store = store if store is not None else InMemoryTrajectoryStore()
        try:
            lane = build_replica_lane(params, 0, run_id, trajectory_store, run_clock)
        except BaseException:
            close_store(trajectory_store)
            raise
        return cls(lane, trajectory_store, run_clock, engine_backend, params.jit)

    @classmethod
    def restore(
        cls,
        params: SimulationParams,
        checkpoint: LoadedCheckpoint,
        store: BinaryLogStore,
        *,
        clock: Clock | None = None,
    ) -> ScalarSession:
        """Rebuild a session at the safe point a checkpoint describes.

        The trajectory log (and the ancestral-phase log, if any) is cut back
        to the position the checkpoint recorded, so generations written
        after it, which belong to a state the checkpoint does not describe,
        are dropped and written again identically.

        The backend and JIT option recorded in the checkpoint are used as
        they are: ``auto`` is never resolved again on resume.

        Args:
            params: The configuration the run was started with.
            checkpoint: A verified checkpoint (`load_checkpoint`).
            store: The run's trajectory log, not yet written to.
            clock: Timestamp source; the current UTC time when omitted.

        Returns:
            A session at the checkpoint's generation.

        Raises:
            SessionRestoreError: If the checkpoint is not a scalar session's,
                was taken under another configuration, or is inconsistent.
        """
        payload = checkpoint.payload
        backend, jit = check_payload_identity(payload, params)
        try:
            lane = restore_lane(params, payload, checkpoint.arrays, store)
        except (ValueError, KeyError, TypeError) as error:
            close_store(store)
            raise SessionRestoreError(
                f"checkpoint {checkpoint.reference.checkpoint_id} cannot be "
                f"restored: {error}"
            ) from error
        except BaseException:
            close_store(store)
            raise
        if _lane_generation(lane) != checkpoint.reference.committed_generation:
            close_store(store)
            raise SessionRestoreError(
                f"checkpoint {checkpoint.reference.checkpoint_id} holds generation "
                f"{_lane_generation(lane)}, not the committed generation "
                f"{checkpoint.reference.committed_generation}"
            )
        run_clock = clock if clock is not None else utc_now
        return cls(lane, store, run_clock, backend, jit)

    @property
    def generation(self) -> int:
        """The last generation computed, persisted and recorded."""
        return _lane_generation(self._lane)

    @property
    def lane(self) -> ReplicaLane:
        """The working state; read-only use only (snapshots, diagnostics)."""
        return self._lane

    @property
    def run_id(self) -> str:
        """The scientific run identifier."""
        return self._lane.run_id

    @property
    def stopped(self) -> bool:
        """Whether the convergence monitor has stopped the run."""
        return self._lane.monitor.should_stop()

    @property
    def store(self) -> TrajectoryStore:
        """The trajectory store generations are persisted to."""
        return self._store

    def advance(self, generations: int | None = None) -> bool:
        """Run up to `generations` more generations, stopping early on a stop.

        Each generation is stepped, recorded with the monitor and persisted
        when retained, exactly as the blocking loop does; on return the
        session is again at a safe point.

        Args:
            generations: The most generations to run; until the monitor
                stops when omitted.

        Returns:
            Whether the run has stopped.

        Raises:
            ValueError: If `generations` is negative or the session is
                finalized or closed.
        """
        if generations is not None and generations < 0:
            raise ValueError(f"generations must be non-negative; got {generations}")
        self._require_open()
        remaining = generations
        while not self.stopped and (remaining is None or remaining > 0):
            self._advancer.advance([self._lane], self._store)
            if remaining is not None:
                remaining -= 1
        return self.stopped

    def capture(self) -> SessionCapture:
        """Export the session's exact state at the current safe point.

        Commits and syncs the trajectory log (and the ancestral-phase log)
        first, so the positions recorded name data already on disk.

        Returns:
            The checkpoint payload and arrays.

        Raises:
            UnsupportedSessionError: If the trajectory store is not a binary
                log, whose committed position a checkpoint can record.
            ValueError: If the session is finalized or closed.
        """
        self._require_open()
        lane = self._lane
        state, arrays = export_lane(lane, self._store)
        payload: dict[str, Any] = {
            "configuration_digest": configuration_digest(lane.params),
            "engine_backend": self._engine_backend,
            "jit": self._jit,
            "kind": SESSION_CHECKPOINT_KIND,
            "version": SESSION_CHECKPOINT_VERSION,
        } | state
        return SessionCapture(
            payload=payload,
            arrays=arrays,
            committed_generation=self.generation,
            phase=RunPhase.FINALIZING if self.stopped else RunPhase.SIMULATING,
        )

    def checkpoint(
        self, lease: ExecutionLease, *, fault: FaultHook | None = None
    ) -> CheckpointReference:
        """Capture the session and publish it as the object's latest checkpoint.

        Args:
            lease: The owning attempt's execution lease.
            fault: Test hook passed to `commit_checkpoint`.

        Returns:
            The reference now recorded in the object's `run.json`.

        Raises:
            AttemptMismatchError: If the lease no longer owns execution.
            UnsupportedSessionError: If the store cannot be checkpointed.
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
        """Release the store's open files; idempotent."""
        if not self._closed:
            self._closed = True
            close_store(self._store)

    def finalize(self) -> RunResult:
        """Build the run's result once it has stopped, and close the store.

        Returns:
            The same result the blocking `fim.engine.fim` call returns for
            this configuration.

        Raises:
            ValueError: If the run has not stopped yet or the session was
                closed without finishing.
        """
        if self._result is not None:
            return self._result
        self._require_open()
        if not self.stopped:
            raise ValueError(
                f"run {self.run_id} has not stopped at generation {self.generation}"
            )
        try:
            result = finalize_replica_lane(self._lane, self._clock, self._store)
        finally:
            self.close()
        self._result = replace(
            result,
            manifest=replace(
                result.manifest, engine_backend=self._engine_backend, jit=self._jit
            ),
        )
        return self._result

    def _require_open(self) -> None:
        """Raise if the session can no longer advance."""
        if self._result is not None:
            raise ValueError(f"run {self.run_id} is already finalized")
        if self._closed:
            raise ValueError(f"run {self.run_id}'s session is closed")


def check_session_support(params: SimulationParams) -> SessionBackend:
    """Refuse a configuration a resumable session cannot run.

    Callers check this before committing a start, so an unsupported
    configuration is reported without recording a failed attempt.

    Args:
        params: The configuration to run.

    Returns:
        The resolved single-lane backend.

    Raises:
        UnsupportedSessionError: If `params` asks for more than one
            replicate, or the resolved backend cannot be built for it (JIT
            on the lineal backend, no numba for the vector backend, or a
            locus over the vector capacity ceiling).
    """
    engine_backend = resolve_session_backend(params)
    if params.n_replicates != 1:
        raise UnsupportedSessionError(
            "a resumable session runs exactly one replicate; "
            f"n_replicates is {params.n_replicates}"
        )
    check_backend_buildable(params, engine_backend)
    return engine_backend


def check_backend_buildable(
    params: SimulationParams, engine_backend: SessionBackend
) -> None:
    """Refuse a backend the blocking call itself would refuse for `params`.

    Args:
        params: The configuration to run.
        engine_backend: The resolved backend.

    Raises:
        UnsupportedSessionError: If the backend cannot be built for `params`.
    """
    # The blocking call's own backend checks (JIT availability per backend,
    # numba for the vector backend, the vector capacity ceiling), so a
    # session refuses exactly what `fim.engine.fim` refuses.
    try:
        build_engine_backend(
            engine_backend,
            jit=params.jit,
            params=params,
            auto_vector_min_d=params.auto_vector_min_d,
            auto_vector_max_capacity=params.auto_vector_max_capacity,
        )
    except ValueError as error:
        raise UnsupportedSessionError(str(error)) from error


def export_lane(
    lane: ReplicaLane, store: TrajectoryStore
) -> tuple[dict[str, Any], dict[str, npt.NDArray[Any]]]:
    """Export one lane's exact state at a safe point.

    Commits and syncs the lane's trajectory log (and its ancestral-phase
    log) first, so the positions recorded name data already on disk.

    Args:
        lane: The lane, between two generations.
        store: The lane's own trajectory log.

    Returns:
        The lane's JSON-safe metadata (`run_id`, `started_at`,
        `generation`, `rng`, `monitor`, `population`, `registries`,
        `vector`, `trajectory`, `equilibration`) and its arrays.

    Raises:
        UnsupportedSessionError: If a log is not a binary log, whose
            committed position a checkpoint can record.
    """
    if not isinstance(store, BinaryLogStore):
        raise UnsupportedSessionError(
            "only a binary trajectory log can be checkpointed; got "
            f"{type(store).__name__}"
        )
    monitor, monitor_arrays = lane.monitor.export_state()
    arrays: dict[str, npt.NDArray[Any]] = dict(monitor_arrays)
    payload: dict[str, Any] = {
        "equilibration": None,
        "generation": _lane_generation(lane),
        "monitor": monitor,
        "population": None,
        "registries": None,
        "rng": export_rng(lane.rng),
        "run_id": lane.run_id,
        "started_at": lane.started_at,
        "trajectory": _position_to_json(store.checkpoint()),
        "vector": None,
    }
    block = lane.vectorized_state
    if block is None:
        population, population_arrays = export_population(lane.state)
        registries, registry_arrays = export_registries(
            lane.registry, lane.finite_alleles
        )
        payload["population"] = population
        payload["registries"] = registries
        arrays |= population_arrays | registry_arrays
    else:
        # Once the vector backend has built its block, the block is the
        # population and the registries: `lane.state` and the lane's
        # registries are left at generation zero until the run stops.
        vector, vector_arrays = block.export_state()
        payload["vector"] = vector
        arrays |= {
            f"{VECTOR_PREFIX}{name}": array for name, array in vector_arrays.items()
        }
    outcome = lane.equilibration_outcome
    if outcome is not None:
        ancestral = lane.equilibrium_store
        if not isinstance(ancestral, BinaryLogStore):
            raise UnsupportedSessionError(
                "the ancestral-phase trajectory is not a binary log"
            )
        payload["equilibration"] = {
            "final_heterozygosity": outcome.final_heterozygosity,
            "generation_count": outcome.generation_count,
            "trajectory": _position_to_json(ancestral.checkpoint()),
        }
        arrays[EQUILIBRATION_HISTORY] = np.asarray(outcome.history, dtype=np.float64)
    return payload, arrays


def resolve_session_backend(params: SimulationParams) -> SessionBackend:
    """Resolve `params.engine_backend` to a concrete backend.

    ``auto`` is resolved exactly as `fim.engine.fim` resolves it, so a
    session never runs a configuration on a different backend than the
    blocking call would.

    Args:
        params: The configuration to run.

    Returns:
        ``"lineal"``, ``"generational"`` or ``"generational-vector"``.

    Raises:
        UnsupportedSessionError: If the backend is not one a session runs.
    """
    backend = params.engine_backend
    if backend == "auto":
        backend = resolve_auto_engine_backend(
            params,
            params.auto_vector_min_d,
            params.auto_vector_max_capacity,
            params.jit,
        )
    for known in SESSION_BACKENDS:
        if backend == known:
            return known
    raise UnsupportedSessionError(
        f"engine backend {backend!r} does not support resumable sessions"
    )


def check_payload_identity(
    payload: Mapping[str, Any],
    params: SimulationParams,
    *,
    kind: str = SESSION_CHECKPOINT_KIND,
    versions: tuple[int, ...] = READABLE_SESSION_CHECKPOINT_VERSIONS,
) -> tuple[SessionBackend, JitOption]:
    """Check a payload is a session's of `kind`, taken under `params`.

    Args:
        payload: The checkpoint's metadata.
        params: The configuration the caller means to continue.
        kind: The payload kind expected.
        versions: The payload versions this build reads for `kind`.

    Returns:
        The backend and JIT option the run was started with.

    Raises:
        SessionRestoreError: If the kind, version, configuration, backend or
            JIT option does not match.
    """
    if payload.get("kind") != kind:
        raise SessionRestoreError(
            f"checkpoint kind {payload.get('kind')!r} is not {kind!r}"
        )
    if payload.get("version") not in versions:
        raise SessionRestoreError(
            f"session checkpoint version {payload.get('version')!r} is "
            f"not supported; this build reads versions "
            f"{', '.join(map(str, versions))}"
        )
    if payload.get("configuration_digest") != configuration_digest(params):
        raise SessionRestoreError(
            "the checkpoint was taken under another configuration; a run "
            "continues only with the configuration it was started with"
        )
    backend = payload.get("engine_backend")
    if backend not in SESSION_BACKENDS:
        raise SessionRestoreError(
            f"checkpoint backend {backend!r} is not one a session can resume"
        )
    if backend == "generational-vector" and not numba_is_available():
        raise SessionRestoreError(
            "the run was started on the generational-vector backend, which "
            "needs the optional numba dependency; install it with "
            "`pip install fim[jit]` to resume the run"
        )
    if payload.get("vector") is not None and backend != "generational-vector":
        raise SessionRestoreError(
            f"checkpoint holds a vector block but names backend {backend!r}"
        )
    jit = payload.get("jit")
    if jit != params.jit:
        raise SessionRestoreError(
            f"checkpoint JIT option {jit!r} differs from the configuration's "
            f"{params.jit!r}"
        )
    return backend, params.jit


def _position_from_json(value: object) -> tlog.LogPosition:
    """Rebuild a log position written by `_position_to_json`.

    Raises:
        SnapshotError: If the value is malformed.
    """
    if not isinstance(value, Mapping):
        raise SnapshotError("a log position must be a mapping")
    fields = ("generation", "offset", "chain_crc", "generations", "rows")
    numbers: dict[str, int] = {}
    for name in fields:
        number = value.get(name)
        if not isinstance(number, int) or isinstance(number, bool):
            raise SnapshotError(f"log position field {name!r} must be an integer")
        numbers[name] = number
    header = value.get("header")
    if not isinstance(header, str):
        raise SnapshotError("log position header must be base64 text")
    try:
        header_bytes = base64.b64decode(header.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as error:
        raise SnapshotError("log position header is not valid base64") from error
    return tlog.LogPosition(header=header_bytes, **numbers)


def _position_to_json(position: tlog.LogPosition) -> dict[str, Any]:
    """Return a log position as JSON-safe metadata (the header in base64)."""
    return {
        "chain_crc": position.chain_crc,
        "generation": position.generation,
        "generations": position.generations,
        "header": base64.b64encode(position.header).decode("ascii"),
        "offset": position.offset,
        "rows": position.rows,
    }


def _restore_equilibration(
    params: SimulationParams,
    payload: Mapping[str, Any],
    arrays: Mapping[str, npt.NDArray[Any]],
    store: BinaryLogStore,
    run_id: str,
) -> tuple[EquilibrationOutcome | None, TrajectoryStore | None]:
    """Rebuild the ancestral phase's outcome and reopen its log, if it ran.

    Raises:
        SnapshotError: If the recorded outcome is malformed.
    """
    recorded = payload.get("equilibration")
    if recorded is None:
        return None, None
    if not isinstance(recorded, Mapping):
        raise SnapshotError("equilibration must be a mapping")
    generation_count = recorded.get("generation_count")
    final = recorded.get("final_heterozygosity")
    if not isinstance(generation_count, int) or not isinstance(final, float):
        raise SnapshotError("equilibration outcome is malformed")
    history = arrays.get(EQUILIBRATION_HISTORY)
    if history is None or history.dtype != np.float64 or history.ndim != 1:
        raise SnapshotError(f"array {EQUILIBRATION_HISTORY!r} is missing or malformed")
    outcome = EquilibrationOutcome(
        generation_count=generation_count,
        final_heterozygosity=final,
        history=tuple(float(value) for value in history),
    )
    ancestral = equilibrium_store_for(store, run_id)
    if not isinstance(ancestral, BinaryLogStore):
        raise SnapshotError("the ancestral-phase store is not a binary log")
    ancestral.resume(
        run_id,
        equilibrium_frame_layout(params),
        _position_from_json(recorded.get("trajectory")),
    )
    return outcome, ancestral


def restore_lane(
    params: SimulationParams,
    payload: Mapping[str, Any],
    arrays: Mapping[str, npt.NDArray[Any]],
    store: BinaryLogStore,
    *,
    replica_index: int = 0,
    awaiting_window: bool = False,
) -> ReplicaLane:
    """Rebuild one replicate's lane from the state `export_lane` wrote.

    The trajectory log (and the ancestral-phase log, if any) is cut back to
    the recorded positions.

    Args:
        params: The lane's own configuration (a batch replicate's, for a
            lane of a batch).
        payload: The lane's metadata.
        arrays: The lane's arrays, without any batch prefix.
        store: The lane's own trajectory log, not yet written to.
        replica_index: The lane's zero-based index in its batch.
        awaiting_window: Whether the lane was built waiting for the
            batch's averaging window (its monitor is in window mode).

    Returns:
        The lane, at the recorded generation.

    Raises:
        SnapshotError: If any part of the state is malformed or inconsistent.
        ValueError: If a log does not match its recorded position.
    """
    run_id = payload["run_id"]
    started_at = payload["started_at"]
    if not isinstance(run_id, str) or not isinstance(started_at, str):
        raise SnapshotError("run_id and started_at must be strings")
    rng = import_rng(payload["rng"])
    monitor = burn_in_monitor(params, awaiting_window=awaiting_window)
    try:
        monitor.restore_state(payload["monitor"], _select(arrays, "monitor."))
    except (ValueError, RuntimeError) as error:
        raise SnapshotError(f"monitor state: {error}") from error
    block: VectorBlock | None = None
    if payload.get("vector") is None:
        state = import_population(payload["population"], _select(arrays, "population."))
        registry, finite_alleles = import_registries(
            payload["registries"], _select(arrays, "finite."), state
        )
    else:
        block = _restore_block(params, payload["vector"], arrays)
        # The block is authoritative; the lane's `state` and registries are
        # only read again once the run stops, when the advancer rebuilds
        # them from the block, so they are rebuilt from it here too.
        state = block.to_model_state()
        registry = AlleleRegistry(start=block.next_id)
        finite_alleles = None
    if state.generation != payload["generation"]:
        raise SnapshotError(
            f"population is at generation {state.generation}, not "
            f"{payload['generation']!r}"
        )
    if not monitor.generations or monitor.generations[-1] != state.generation:
        raise SnapshotError("the monitor's last generation is not the population's")
    store.resume(
        run_id, frame_layout(params), _position_from_json(payload["trajectory"])
    )
    outcome, ancestral = _restore_equilibration(params, payload, arrays, store, run_id)
    return ReplicaLane(
        replica_index=replica_index,
        run_id=run_id,
        params=params,
        state=state,
        rng=rng,
        registry=registry,
        finite_alleles=finite_alleles,
        monitor=monitor,
        started_at=started_at,
        vectorized_state=block,
        equilibration_outcome=outcome,
        equilibrium_store=ancestral,
        retention=TrajectoryRetention.from_params(params),
    )


def _restore_block(
    params: SimulationParams,
    metadata: object,
    arrays: Mapping[str, npt.NDArray[Any]],
) -> VectorBlock:
    """Rebuild the vector backend's block from a checkpoint.

    The configuration-derived parts are built from `params` exactly as
    `fim.engine.VectorizedAdvancer` builds them on a lane's first tick.

    Raises:
        SnapshotError: If the block is malformed or does not fit `params`.
    """
    if not isinstance(metadata, Mapping):
        raise SnapshotError("vector must be a mapping")
    deme_count = len(params.population_sizes)
    block_arrays = {
        name.removeprefix(VECTOR_PREFIX): array
        for name, array in _select(arrays, VECTOR_PREFIX).items()
    }
    try:
        return VectorBlock.from_state(
            metadata,
            block_arrays,
            loci=params.loci,
            sizes=expand_population_sizes(params.gene_copies, deme_count),
            mutation_rates=params.mutation_rates,
            migration=VectorMigration.from_parameter(
                params.m,
                deme_count,
                stochastic=params.migrant_sampling == "stochastic",
            ),
            mutation_model=params.mutation_model,
        )
    except ValueError as error:
        raise SnapshotError(f"vector block: {error}") from error


def _lane_generation(lane: ReplicaLane) -> int:
    """Return the lane's current generation, wherever it is held.

    Under the vector backend `lane.state` stays at generation zero until
    the run stops; the block holds the current generation.
    """
    block = lane.vectorized_state
    return block.generation if block is not None else lane.state.generation


def _select(
    arrays: Mapping[str, npt.NDArray[Any]], prefix: str
) -> dict[str, npt.NDArray[Any]]:
    """Return the arrays whose names start with `prefix`."""
    return {name: array for name, array in arrays.items() if name.startswith(prefix)}
