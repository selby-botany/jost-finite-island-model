"""Backend protocol, the generational backend, and the factory."""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from typing import Literal, Protocol

from fim.config.defaults import (
    DEFAULT_AUTO_VECTOR_MAX_CAPACITY,
    DEFAULT_AUTO_VECTOR_MIN_D,
)
from fim.engine.batch import run_batch
from fim.engine.lanes import (
    Advancer,
    SequentialAdvancer,
    ThreadedAdvancer,
    VectorizedAdvancer,
)
from fim.engine.lineal import LinealBackend
from fim.engine.records import Clock, EngineBackendChoice, JitOption, SimulationOutput
from fim.model.locus import finite_allele_capacity
from fim.model.params import SimulationParams
from fim.persistence.store import (
    InMemoryTrajectoryStore,
    ReplicateFanoutStore,
    TrajectoryStore,
    close_store,
)


class EngineBackend(Protocol):
    """One way of actually running a batch of replicates to completion.

    `simulate_params()` (below) validates its own public arguments, then hands off to
    exactly one backend's own `run` — today, always `LinealBackend`, the
    only implementation. This split exists ahead of a second backend
    actually landing because the engine is already planned to grow more
    than one way of running the same simulation: a thread-based,
    generation-first scheduler and a vectorized, array-based one are both
    designed but not yet built. Naming today's existing behavior as an
    explicit `EngineBackend` now means a future caller picks among
    backends through an ordinary keyword argument once those exist,
    rather than `simulate_params()` growing an ever-larger hardcoded dispatch of its
    own.

    Every backend takes exactly the same four "what to run, where"
    arguments — anything about *how* a given backend actually computes
    (worker-process counts, thread pools, which array representation) is
    that backend's own constructor's business, not part of this shared
    contract.
    """

    def run(
        self,
        params: SimulationParams,
        store: TrajectoryStore | None,
        run_id: str | None,
        clock: Clock,
    ) -> SimulationOutput:
        """Run every replicate `params` describes; return the result(s)."""
        ...


class GenerationalBackend:
    """`run_batch`, driven by an injectable `Advancer`.

    Reorders *when* generations are computed (§4.2 of the design this
    implements) without changing *what* is computed: with the default
    `SequentialAdvancer`, every replicate's own trajectory is
    bit-identical to what `LinealBackend` produces for the same seed
    (proven by `run_batch`'s own docstring; checked directly by this
    project's own golden-parity tests). A future thread-pool-backed
    `Advancer` can be passed here to add real concurrency without this
    class itself changing at all.
    """

    def __init__(
        self,
        advancer: Advancer | None = None,
        *,
        store_factory: Callable[[str], TrajectoryStore] | None = None,
    ) -> None:
        """Configure which `Advancer` drives this backend's own batches.

        Args:
            advancer: Defaults to `SequentialAdvancer()` — no new
                concurrency, matching `LinealBackend`'s own trajectories
                exactly for the same seed.
            store_factory: See `simulate_params()`'s own docstring. Unlike
                `LinealBackend`, this backend never needs a process
                pool to use one — `run_batch` already writes every
                replicate's own rows through one shared `store`
                argument, keyed by that replicate's own `run_id`
                (`fim.persistence.store.TrajectoryStore`'s own
                contract), so wrapping `store_factory` in a
                `ReplicateFanoutStore` (below) and handing *that* to
                `run_batch` as its one `store` gives every replicate a
                real, independent file — the same outcome
                `LinealBackend`'s own `store_factory` path already
                produces — with no change to `run_batch`, `ReplicaLane`,
                or any `Advancer` implementation at all
                (`20260914-claude-sonnet-5-non-lineal-batch-execution-
                design.md`, `selby/restricted`, §5.1-§5.2).
        """
        self._advancer = advancer if advancer is not None else SequentialAdvancer()
        self._store_factory = store_factory

    def run(
        self,
        params: SimulationParams,
        store: TrajectoryStore | None,
        run_id: str | None,
        clock: Clock,
    ) -> SimulationOutput:
        """Run `params`'s own replicate(s); see `simulate_params()`'s own docstring."""
        if store is not None and self._store_factory is not None:
            raise ValueError("store and store_factory are mutually exclusive")
        trajectory_store = (
            ReplicateFanoutStore(self._store_factory)
            if self._store_factory is not None
            else (store if store is not None else InMemoryTrajectoryStore())
        )
        try:
            results = run_batch(params, trajectory_store, run_id, clock, self._advancer)
        finally:
            # Every replicate was already closed as it finished; this
            # catches the lanes of a batch that raised partway, and a
            # shared store, which has only the one handle to close.
            close_store(trajectory_store)
        if params.n_replicates == 1:
            return results[0]
        return results


def resolve_auto_engine_backend(
    params: SimulationParams,
    auto_vector_min_d: int,
    auto_vector_max_capacity: int,
    jit: JitOption = "off",
) -> Literal["generational", "generational-vector"]:
    """Resolve `"auto"` to `"generational"` or `"generational-vector"`.

    Select `"generational-vector"` when `jit` is `"off"`, Numba is
    available, `params.d` meets `auto_vector_min_d`, and the largest
    finite-alleles locus capacity (when applicable) does not exceed
    `auto_vector_max_capacity`. Otherwise select `"generational"`.
    This function does not select `"lineal"`. The generational backends
    produce the same results for the same seed on the development
    platform; cross-platform bitwise parity has not been established.
    The selected backend is recorded in the run manifest.
    """
    if jit != "off":
        return "generational"
    if not numba_is_available() or params.d < auto_vector_min_d:
        return "generational"
    if params.mutation_model == "finite_alleles":
        max_capacity = max(
            finite_allele_capacity(locus.length) for locus in params.loci
        )
        if max_capacity > auto_vector_max_capacity:
            return "generational"
    return "generational-vector"


def numba_is_available() -> bool:
    """Return whether Python can locate `numba` without importing it."""
    return importlib.util.find_spec("numba") is not None


def build_engine_backend(
    engine_backend: EngineBackendChoice,
    *,
    jit: JitOption = "off",
    max_workers: int | None = None,
    store_factory: Callable[[str], TrajectoryStore] | None = None,
    params: SimulationParams | None = None,
    auto_vector_min_d: int = DEFAULT_AUTO_VECTOR_MIN_D,
    auto_vector_max_capacity: int = DEFAULT_AUTO_VECTOR_MAX_CAPACITY,
) -> EngineBackend:
    """Return the configured backend `simulate_params()` should run against.

    Args:
        engine_backend: Backend to construct. `"lineal"` uses
            `LinealBackend`; `"generational"` uses
            `GenerationalBackend` with `ThreadedAdvancer`;
            `"generational-vector"` uses `GenerationalBackend` with
            `VectorizedAdvancer` and requires `numba`. `"auto"` selects
            between the generational backends and never selects
            `"lineal"`. The vector backend is selected when `numba` is
            importable, `jit` is `"off"`, `params.d` meets
            `auto_vector_min_d`, and, for finite alleles, the largest
            locus capacity does not exceed `auto_vector_max_capacity`.
            Otherwise, `"auto"` selects `"generational"`.
        jit: JIT option for the selected backend. `"generational"`
            accepts `"off"` or `"numba"`; `"lineal"` and
            `"generational-vector"` require `"off"`. The vector backend
            uses Numba internally regardless. The generational JIT path
            preserves results for the same seed on the development
            platform; cross-platform bitwise parity has not been
            established.
        max_workers: Worker-process count for `"lineal"`; ignored by
            other backends. Generational batch concurrency is configured
            through `SimulationParams.max_concurrent_replicates`.
        store_factory: Optional factory for a separate trajectory store
            per replicate.
        params: Run parameters. Required for `"auto"` and used to check
            finite-alleles capacity when `"generational-vector"` is
            selected explicitly, if provided.
        auto_vector_min_d: Minimum deme count for `"auto"` to select the
            vector backend. Defaults to `2`; benchmark crossover points
            may vary by platform.
        auto_vector_max_capacity: Maximum finite-alleles locus capacity
            for `"auto"` to select the vector backend. Defaults to
            `4096`.

    Raises:
        ValueError: If the backend name or JIT option is invalid, `"auto"`
            is used without `params`, the vector backend lacks `numba`,
            or an explicitly selected vector backend exceeds the
            finite-alleles capacity limit.
    """
    if engine_backend == "auto":
        if params is None:
            raise ValueError(
                "engine_backend='auto' needs params to decide with — pass "
                "the SimulationParams this backend will actually run"
            )
        # Resolve to a concrete choice, then fall through to the same
        # branches below — never invents a third code path, just picks
        # which existing one applies.
        engine_backend = resolve_auto_engine_backend(
            params, auto_vector_min_d, auto_vector_max_capacity, jit
        )
    if engine_backend == "lineal":
        if jit != "off":
            raise ValueError(
                "jit is not offered on the lineal backend — it stays the "
                "untouched golden reference, see LinealBackend's own "
                "docstring"
            )
        return LinealBackend(max_workers=max_workers, store_factory=store_factory)
    if engine_backend == "generational":
        return GenerationalBackend(
            ThreadedAdvancer(jit=jit), store_factory=store_factory
        )
    if engine_backend == "generational-vector":
        if jit != "off":
            raise ValueError(
                "generational-vector has no separate jit toggle yet — its "
                "own mutate step always requires numba internally "
                "(fim.model.vectorized), regardless of this argument; pass "
                "jit='off' (the default)"
            )
        if not numba_is_available():
            raise ValueError(
                "generational-vector needs the optional numba dependency "
                "(fim.model.vectorized's own mutate step imports it "
                "unconditionally) — install it with `pip install fim[jit]` "
                "before selecting this backend"
            )
        # `"auto"` already refuses this same backend when a locus's own
        # capacity exceeds `auto_vector_max_capacity`
        # (`resolve_auto_engine_backend`, above); explicit selection must
        # refuse it too, because a finite-alleles table is `capacity`
        # columns wide however few states are in use (the default locus
        # length, 200, implies capacity 4**200). Only checkable when
        # `params` is given: `simulate_params()` itself always passes one, so this
        # closes the reachable gap, while the params-less form (used by
        # several tests to build a backend with no real run in mind) gets
        # no capacity opinion. Infinite alleles has no capacity to speak
        # of (locus length means something else there), so it is skipped.
        if params is not None and params.mutation_model == "finite_alleles":
            max_capacity = max(
                finite_allele_capacity(locus.length) for locus in params.loci
            )
            if max_capacity > auto_vector_max_capacity:
                raise ValueError(
                    "generational-vector's own dense per-locus capacity "
                    f"would be {max_capacity} (the largest locus length "
                    "among params.loci), exceeding auto_vector_max_capacity="
                    f"{auto_vector_max_capacity} — the same ceiling "
                    "engine_backend='auto' itself already refuses to cross "
                    "for this exact backend. Reduce the offending locus "
                    "length, raise auto_vector_max_capacity only after "
                    "confirming the memory a (d, capacity) dense array at "
                    "that size actually needs is available, or choose a "
                    "different engine_backend"
                )
        return GenerationalBackend(VectorizedAdvancer(), store_factory=store_factory)
    raise ValueError(f"unknown engine backend: {engine_backend!r}")
