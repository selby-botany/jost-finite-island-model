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

    `fim()` (below) validates its own public arguments, then hands off to
    exactly one backend's own `run` — today, always `LinealBackend`, the
    only implementation. This split exists ahead of a second backend
    actually landing because the engine is already planned to grow more
    than one way of running the same simulation: a thread-based,
    generation-first scheduler and a vectorized, array-based one are both
    designed but not yet built. Naming today's existing behavior as an
    explicit `EngineBackend` now means a future caller picks among
    backends through an ordinary keyword argument once those exist,
    rather than `fim()` growing an ever-larger hardcoded dispatch of its
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
            store_factory: See `fim()`'s own docstring. Unlike
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
        """Run `params`'s own replicate(s); see `fim()`'s own docstring."""
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
    """Resolve `engine_backend="auto"` to a concrete, non-`"auto"` choice.

    Shared by `build_engine_backend` (to know which backend to actually
    construct) and `fim()` (to know what to record in the run's own
    manifest as provenance, `RunManifest.engine_backend`) — kept in one
    place so the two can never silently drift apart on what `"auto"`
    actually picked for a given `params`. Never resolves to `"lineal"`
    — see `build_engine_backend`'s own `engine_backend` Args entry for
    why only the generational-vs-vector axis is automated so far.

    `"generational-vector"` is chosen when all of these hold:

    - `jit` is `"off"`: Backend V has no `jit` toggle, only Backend G
      offers one, and a caller who asked for it gets it (the output is the
      same either way);
    - `numba` is importable. Backend V's output is identical to
      Backend G's, so a missing `numba` costs only speed: `"auto"` falls
      back to `"generational"` instead of raising, as an explicit
      `"generational-vector"` choice does;
    - `params.d >= auto_vector_min_d`;
    - under `mutation_model="finite_alleles"` only, the largest locus
      capacity (`4 ** length`) is at most `auto_vector_max_capacity`: a
      finite-alleles table is `capacity` columns wide however few states
      are in use. Under infinite alleles the table is as wide as the
      alleles alive at once, so there is no such ceiling (the table's own
      memory ceiling, `fim.model.vector_block`, guards the rest).

    Otherwise `"generational"`. Because V reproduces G bit for bit, the
    choice changes how fast a run finishes and nothing in its output but
    the manifest's `engine_backend` field.
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
    """Return whether the optional `numba` dependency can actually be imported.

    `importlib.util.find_spec` locates the module without importing it —
    cheap enough to call on every `"generational-vector"` construction,
    and it never pays numba's own (non-trivial) import cost just to
    answer "is it there at all."

    `build_engine_backend` calls this before constructing a
    `VectorizedAdvancer` — `fim.model.vectorized`'s own mutate step
    imports `numba` unconditionally on first use (its own module
    docstring), with no pure-Python fallback, so a numba-less install
    used to fail on the *first `advance()` call*, after generation zero
    had already been built and persisted to the trajectory store — a
    real, previously unguarded gap (this project's own multi-model
    engine review, 2026-09-04, `FIM-01`/finding C-03/finding P1-3,
    independently found by three of four reviewers). Checking here
    instead turns that into a named `ValueError` at config time, before
    anything has been written.

    A module-level function, not inlined into `build_engine_backend`,
    specifically so a test can monkeypatch it directly (`fim.engine.
    numba_is_available`) to exercise the numba-less path without
    actually uninstalling numba from the test environment.
    """
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
    """Return the configured backend `fim()` should run against.

    One function, not a class hierarchy: Python's own first-class
    functions make a `ConcreteFactory` subclass per backend unnecessary
    ceremony here — a plain function that reads a choice and returns an
    object satisfying `EngineBackend`'s shape does the same job with no
    class tree to maintain.

    Args:
        engine_backend: Which backend to build. `"generational"` builds
            `GenerationalBackend(ThreadedAdvancer())` — real thread-based
            parallelism, not merely the structural reshuffle
            `SequentialAdvancer` alone would give (still directly
            available by constructing `GenerationalBackend()` without
            going through this factory, if a caller wants zero new
            thread-safety surface). `"generational-vector"` builds
            `GenerationalBackend(VectorizedAdvancer())` — array-native,
            one compiled kernel call per generation over every locus
            (`fim.model.vector_block`); runs both mutation models,
            both migrant sampling modes — needs the optional `numba`
            dependency unconditionally (see `jit`, below, for why that is
            not the same thing as `jit="numba"`).
            `"auto"` picks between `"generational"` and
            `"generational-vector"` using `params`/`auto_vector_min_d`
            (below) — never `"lineal"`, since no data yet characterizes
            where that boundary sits (see `auto_vector_min_d`'s own
            entry for why only the L-vs-V axis is automated so far).
            `resolve_auto_engine_backend` lists the conditions: V for
            continuous migration when `numba` is importable and `d` is
            large enough (and, for finite alleles, the capacity small
            enough), otherwise G. The output is identical either way.
        jit: Whether the chosen backend should JIT-compile its own
            operators. Under `"generational"`, `"numba"` JIT-compiles
            `drift`'s own multinomial draw (`fim.model.operators.drift`'s
            own docstring) — bit-identical output to `"off"`, for the
            same seed, *on the same machine*: that claim (and every
            "generational-vector" trajectory's own reliance on `numba`,
            unconditionally, further below) rests on `numba`'s own
            compiled math matching CPython's own `libm` bit for bit,
            verified only on this project's own development platform so
            far, not across the different `libm` implementations a
            different operating system or CPU architecture can ship
            (this project's own multi-model engine review, 2026-09-04,
            `FIM-22`/finding Kimi-FIM-22) — a portability caveat to keep
            in mind before treating a `jit="numba"`/"generational-
            vector" result as reproducible across machines the way
            every `jit="off"` result already is, not a known, confirmed
            divergence. Not yet shown to be a standalone wall-clock win,
            though: it fixes a real, separately measured regression an
            earlier, per-pair-call version had, but `drift`'s own
            dominant cost at this project's reference scale is
            marshaling to and from `ModelState`'s sparse representation,
            not the compiled draw itself (`drift`'s own docstring has
            the full measurement). `migrate`/`mutate`'s own RNG calls
            are not JIT-compiled at all. Needs the optional `numba`
            dependency installed (``pip install fim[jit]``) — not
            imported at all unless `jit="numba"` is actually requested.
            `"generational-vector"` has no separate toggle for this yet
            — its own mutate step always requires `numba` internally,
            regardless of this argument, so only `jit="off"` (the
            default) is accepted there; passing `jit="numba"` raises
            `ValueError`, not a silent no-op. `"lineal"` never accepts
            anything but `"off"` — a permanent restriction, not a
            temporary gap: `LinealBackend` stays the untouched golden
            reference every other backend's own parity tests are checked
            against (see its own docstring).
        max_workers: `LinealBackend`-only; ignored by every other
            backend, since there is no process pool anywhere else for it
            to size — `ThreadedAdvancer`'s own thread count is a
            separate, not-yet-publicly-reachable knob (see its own
            docstring for why this name is not reused for it there);
            `run_batch`'s own `max_concurrent_replicates` is the
            `"generational"`/`"generational-vector"` equivalent
            concurrency/memory-bounding control, a real `SimulationParams`
            field rather than a `build_engine_backend` argument.
        store_factory: Every backend accepts this the same way now — one
            real, independent store per replicate, keyed by that
            replicate's own run id. `LinealBackend` uses it directly (a
            worker process needs its own store; nothing else could be
            shared across a process boundary). `GenerationalBackend`
            wraps it in a `ReplicateFanoutStore` instead
            (`GenerationalBackend`'s own docstring) — no process pool
            involved, but the same one-store-per-replicate outcome.
        params: The run this backend is actually being built for —
            required, and only actually read, when `engine_backend ==
            "auto"`, to look at `params.d`/`params.mutation_model`/
            `params.migrant_sampling` and decide. Every other choice
            ignores it entirely (a caller building a `"lineal"`/
            `"generational"`/`"generational-vector"` backend directly
            never needs to pass a real `SimulationParams` here at all).
        auto_vector_min_d: The deme-count cutover `"auto"` uses to
            decide between `"generational"` and `"generational-vector"`
            — irrelevant, and unused, under every other `engine_backend`
            value. Defaults to `DEFAULT_AUTO_VECTOR_MIN_D` (`2`), this
            project's own best current estimate from a real benchmark
            sweep (see that constant's own docstring for the full
            finding, including its cross-environment caveats) —
            deliberately a parameter, not a hardcoded constant, because
            a follow-on check (the same sweep, run inside a Docker
            container instead of natively) found the threshold does
            *not* port cleanly across platforms: the qualitative
            direction held, but neither the exact crossover point nor
            the performance magnitude did. A deployment on
            meaningfully different hardware should re-run that same
            characterization and pass its own measured value here
            rather than trust this default blindly.
        auto_vector_max_capacity: The per-locus capacity ceiling
            `"auto"` uses alongside `auto_vector_min_d` — the largest
            capacity across every locus in `params.loci` must be at
            most this value, in addition to `d >= auto_vector_min_d`,
            for `"auto"` to pick `"generational-vector"` — under
            `mutation_model="finite_alleles"` only (an infinite-alleles
            table is as wide as the alleles alive at once, not
            `4 ** length`); irrelevant, and unused, under every other
            `engine_backend` value.
            Defaults to `DEFAULT_AUTO_VECTOR_MAX_CAPACITY` (`4096`) —
            see that constant's own docstring for the measured finding
            behind it and the same cross-environment/cross-fix
            staleness caveats `auto_vector_min_d` already carries.

    Raises:
        ValueError: If `engine_backend == "lineal"` and `jit != "off"`,
            `engine_backend == "generational-vector"` (or `"auto"`
            resolves to it) and `jit != "off"`, `engine_backend ==
            "generational-vector"` (or `"auto"` resolves to it) and the
            optional `numba` dependency is not installed, `engine_backend
            == "generational-vector"` and `params` uses `mutation_model ==
            "finite_alleles"` and names a locus whose capacity exceeds
            `auto_vector_max_capacity` (checked only when `params` is
            given; `"auto"` already refuses an over-capacity config on its
            own, before ever reaching this branch),
            `engine_backend == "auto"` and `params` is `None`, or
            `engine_backend` names something unrecognized.
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
        # `params` is given: `fim()` itself always passes one, so this
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
