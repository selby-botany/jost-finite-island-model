"""Resumable execution sessions for one scalar replicate.

A session is the stepwise form of the blocking `fim.engine.fim` call for a
single replicate: `ScalarSession.create` builds generation zero,
`ScalarSession.advance` runs a bounded number of generations, and
`ScalarSession.finalize` produces the same `fim.engine.RunResult` the
blocking call returns. Between `advance` calls the session sits at a safe
point, where nothing is half-written, so a caller can observe a pause or
cancel request and capture a checkpoint.

The session reuses the generation-first lane machinery
(`fim.engine.ReplicaLane`, `_build_replica_lane`, `SequentialAdvancer`,
`_finalize_replica_lane`) that the golden-parity tests already prove is
bit-identical to the lineal reference loop for the same seed. Only the
scalar backends (``lineal`` and ``generational``) are supported; the dense
vector backend gains checkpoint support separately.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from fim.engine import (
    Clock,
    JitOption,
    ReplicaLane,
    RunResult,
    SequentialAdvancer,
    _build_replica_lane,
    _finalize_replica_lane,
    _resolve_auto_engine_backend,
    _utc_now,
)
from fim.model.params import SimulationParams
from fim.persistence.store import (
    InMemoryTrajectoryStore,
    TrajectoryStore,
    close_store,
)

ScalarBackend = Literal["lineal", "generational"]
SCALAR_BACKENDS: tuple[ScalarBackend, ...] = ("lineal", "generational")


class UnsupportedSessionError(ValueError):
    """Raised when a configuration cannot run as a resumable scalar session."""


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
        engine_backend: ScalarBackend,
        jit: JitOption,
    ) -> None:
        """Wrap an already-built lane; use `create` instead of calling this.

        Args:
            lane: The replicate's working state, at a safe point.
            store: Where generations are persisted.
            clock: Timestamp source for the manifest.
            engine_backend: The resolved scalar backend recorded in the
                manifest.
            jit: The JIT option the drift step uses and the manifest records.
        """
        self._lane = lane
        self._store = store
        self._clock = clock
        self._engine_backend: ScalarBackend = engine_backend
        self._jit: JitOption = jit
        self._advancer = SequentialAdvancer(jit=jit == "numba")
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
                replicate or resolves to a non-scalar backend.
        """
        engine_backend = resolve_scalar_backend(params)
        if params.n_replicates != 1:
            raise UnsupportedSessionError(
                "a resumable session runs exactly one replicate; "
                f"n_replicates is {params.n_replicates}"
            )
        if engine_backend == "lineal" and params.jit != "off":
            raise UnsupportedSessionError(
                "jit is not offered on the lineal backend; set jit to 'off'"
            )
        run_clock = clock if clock is not None else _utc_now
        trajectory_store = store if store is not None else InMemoryTrajectoryStore()
        try:
            lane = _build_replica_lane(params, 0, run_id, trajectory_store, run_clock)
        except BaseException:
            close_store(trajectory_store)
            raise
        return cls(lane, trajectory_store, run_clock, engine_backend, params.jit)

    @property
    def generation(self) -> int:
        """The last generation computed, persisted and recorded."""
        return self._lane.state.generation

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
            result = _finalize_replica_lane(self._lane, self._clock, self._store)
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


def resolve_scalar_backend(params: SimulationParams) -> ScalarBackend:
    """Resolve `params.engine_backend` to a scalar backend, or refuse.

    ``auto`` is resolved exactly as `fim.engine.fim` resolves it, so a
    session never runs a configuration on a different backend than the
    blocking call would.

    Args:
        params: The configuration to run.

    Returns:
        ``"lineal"`` or ``"generational"``.

    Raises:
        UnsupportedSessionError: If the configuration resolves to the dense
            vector backend.
    """
    backend = params.engine_backend
    if backend == "auto":
        backend = _resolve_auto_engine_backend(
            params,
            params.auto_vector_min_d,
            params.auto_vector_max_capacity,
            params.jit,
        )
    if backend == "lineal":
        return "lineal"
    if backend == "generational":
        return "generational"
    raise UnsupportedSessionError(
        f"engine backend {backend!r} does not support resumable sessions yet; "
        "choose 'lineal' or 'generational'"
    )
