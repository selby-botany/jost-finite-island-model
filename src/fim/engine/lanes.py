"""Replicate lanes, advancers, and lane construction and finalization."""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Protocol, cast

import numpy as np

from fim import __version__
from fim.convergence.batch_window import BatchWindowPlanner
from fim.convergence.monitor import BurnInMonitor, StopReason
from fim.engine.identity import (
    derive_replicate_run_id,
    deterministic_run_id,
    params_for_replicate,
)
from fim.engine.monitors import burn_in_monitor
from fim.engine.records import Clock, JitOption, RunResult, format_timestamp
from fim.engine.report import report_for_state, window_statistics_payload
from fim.engine.tracking import convergence_values, convergence_values_vectorized
from fim.model.allele import (
    MINTED_ID_START,
    AlleleRegistry,
    FiniteAlleleRegistry,
    FiniteAlleleSpace,
)
from fim.model.initial import (
    EquilibrationOutcome,
    EquilibriumSplitInitialCondition,
    generate_initial_state,
)
from fim.model.locus import finite_allele_capacity
from fim.model.operators import expand_population_sizes, step
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.model.vector_block import VectorBlock, VectorMigration
from fim.persistence.frame import FrameLayout, state_to_frame
from fim.persistence.manifest import CURRENT_SCHEMA_VERSION, RunManifest
from fim.persistence.retention import TrajectoryRetention
from fim.persistence.store import (
    FrameStore,
    TrajectoryStore,
    begin_store_run,
    close_run_store,
    equilibrium_store_for,
    store_wants_frames,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ReplicaLane:
    """One replica's own mutable working state, mid-batch.

    Exactly what `run_one`'s own local variables already are (`state`,
    `rng`, `registry`, `finite_alleles`, `monitor`) — a structural change,
    promoting one function's stack frame into an explicit, named,
    per-replica object so a batch can hold several side by side and
    advance them together one generation at a time, rather than running
    each replica's entire trajectory before starting the next (see
    `GenerationalBackend`, below). Nothing about what any one lane
    computes changes because of this.

    `vectorized_state` is a `VectorizedAdvancer`-only cache — the live
    `fim.model.vector_block.VectorBlock` this lane is actually stepping,
    carried forward generation to generation, with `state` (the
    `ModelState` below) left untouched mid-run rather than reconstructed
    from it every tick. The block is what carries everything a
    `ModelState` cannot: the identity of every column, the counter that
    mints new allele identities (infinite alleles), and each locus's
    minted-state bookkeeping (finite alleles — re-deriving it from `state`
    alone, every generation, would forget any allele minted and then
    driven extinct, the normal fate of a fresh low-frequency mutant).
    `convergence_values_vectorized` reads statistics straight from the
    block's arrays, so `state` only needs rebuilding once, when the lane
    actually stops. Unused by every other `Advancer`; stays `None` for the
    whole run under those. Released (set back to `None`) by
    `finalize_replica_lane` the instant a lane stops, not held for the
    rest of the batch's own run — see that function's own docstring
    (`FIM-48`).

    `equilibration_outcome` is set once, by `build_replica_lane`
    (`generate_initial_state_with_outcome`'s own return value), and
    only when this lane's own `params` configured equilibrium-split —
    `None` for every other initial-condition mode. `_finalize_replica_
    lane` reads it to populate this lane's own manifest fields.
    `equilibrium_store` is set alongside it: where `build_replica_lane`
    already streamed the ancestral phase's own trajectory (the
    `equilibrium_trajectory.tlog` artifact), handed on to
    `RunResult.equilibrium_store`.
    """

    replica_index: int
    run_id: str
    params: SimulationParams
    state: ModelState
    rng: np.random.Generator
    registry: AlleleRegistry
    finite_alleles: FiniteAlleleRegistry | None
    monitor: BurnInMonitor
    started_at: str
    active: bool = True
    result: RunResult | None = None
    vectorized_state: VectorBlock | None = None
    equilibration_outcome: EquilibrationOutcome | None = None
    equilibrium_store: TrajectoryStore | None = None
    retention: TrajectoryRetention = field(default_factory=TrajectoryRetention)


class Advancer(Protocol):
    """Steps every currently-active lane forward by exactly one generation.

    The single fan-out point `run_batch` (below) delegates to — never
    calls `step()` itself. `SequentialAdvancer` is currently the only
    implementation: a plain loop, visiting lanes one at a time, in order.
    A future thread-pool-backed advancer can implement this same
    protocol without `run_batch` itself ever changing.
    """

    def advance(
        self,
        active_lanes: Sequence[ReplicaLane],
        store: TrajectoryStore,
    ) -> list[ReplicaLane]:
        """Advance every lane in `active_lanes` by one generation.

        Returns whichever of them just stopped — including a lane whose
        monitor already reported stopped *before* this call (the
        generation-zero edge case, where convergence is already
        satisfied before any generation past zero ever runs), which is
        returned without being stepped at all.
        """
        ...


class SequentialAdvancer:
    """The default `Advancer`: a plain loop, one lane at a time.

    Always correct, with zero new thread-safety surface — a single
    thread visiting lanes one at a time in a `for` loop cannot race with
    itself. This is what makes `GenerationalBackend` bit-identical to
    `LinealBackend` for the same seed (proof: each lane's own generation
    sequence, RNG draws, and allele-id minting depend only on that
    lane's own prior state, never on any other lane or on wall-clock
    order — reordering *when* one lane's generation is computed relative
    to another's changes nothing about that lane's own result).
    """

    def __init__(self, *, jit: bool = False) -> None:
        """Configure whether this advancer's own lanes step with JIT.

        Args:
            jit: Passed through to `fim.model.operators.step`'s own
                `jit` argument on every call — see its docstring.
                `False` (the default) is every prior release's own
                behavior, unchanged, and needs no `numba` install at
                all; `True` still produces a bit-identical trajectory to
                `False`, for the same seed (`drift`'s own docstring),
                only with the GIL released during the drift step.
        """
        self._jit = jit

    def advance(
        self,
        active_lanes: Sequence[ReplicaLane],
        store: TrajectoryStore,
    ) -> list[ReplicaLane]:
        """Step every active lane by exactly one generation.

        Mirrors one potential iteration of `run_one`'s own `while not
        monitor.should_stop():` loop, per lane, in order.
        """
        debug_enabled = logger.isEnabledFor(logging.DEBUG)
        newly_stopped: list[ReplicaLane] = []
        for lane in active_lanes:
            if lane.monitor.should_stop():
                newly_stopped.append(lane)
                continue
            lane.state = step(
                lane.state,
                lane.params,
                lane.registry,
                lane.rng,
                finite_alleles=lane.finite_alleles,
                jit=self._jit,
            )
            values = convergence_values(lane.state, lane.params)
            lane.monitor.record(lane.state.generation, values)
            if (
                lane.retention.keeps(lane.state.generation)
                or lane.monitor.should_stop()
            ):
                persist_state(store, lane.run_id, lane.state)
            if debug_enabled:
                logger.debug(
                    "replicate %s generation %d: %s",
                    lane.run_id,
                    lane.state.generation,
                    values,
                )
            if lane.monitor.should_stop():
                newly_stopped.append(lane)
        return newly_stopped


def _partition_into_blocks(
    lanes: Sequence[ReplicaLane], block_count: int
) -> list[list[ReplicaLane]]:
    """Split `lanes` into up to `block_count` contiguous, near-equal blocks.

    Dynamic partitioning: computed fresh from whichever lanes happen to
    be active this tick, not a persistent assignment reused across
    ticks — see `ThreadedAdvancer`'s own docstring for why a static
    assignment (decided once, at batch start) is a documented, deferred
    optimization rather than this stage's own scope. Never returns an
    empty block: with fewer lanes than `block_count`, each lane gets its
    own block instead of an emptier partition.
    """
    if not lanes:
        return []
    actual_block_count = min(block_count, len(lanes))
    base_size, remainder = divmod(len(lanes), actual_block_count)
    blocks: list[list[ReplicaLane]] = []
    start = 0
    for block_index in range(actual_block_count):
        size = base_size + (1 if block_index < remainder else 0)
        blocks.append(list(lanes[start : start + size]))
        start += size
    return blocks


class ThreadedAdvancer:
    """Fans `SequentialAdvancer`'s own per-lane stepping out across threads.

    Partitions whichever lanes are active this tick into up to
    `max_workers` contiguous blocks (`_partition_into_blocks`) and steps
    each block — sequentially, in order, exactly what `SequentialAdvancer`
    already does — inside its own thread, via `ThreadPoolExecutor`.
    Blocks are disjoint (every lane belongs to exactly one), so no two
    threads ever touch the same lane's own state, RNG, or allele
    registry at once; the one thing genuinely shared across threads is
    `store`, made safe by the `threading.Lock` both
    `fim.persistence.store.InMemoryTrajectoryStore` and
    `fim.persistence.binary_store.BinaryLogStore` now hold around
    their own `write_generation`.

    Whether this delivers real wall-clock speedup over
    `SequentialAdvancer` depends on how much of each generation's own
    work happens inside NumPy/JIT calls that release the GIL versus
    pure-Python loop overhead — measured, not merely open, as of
    `20260901-claude-sonnet-5-fim-engine-backend-factory-design.md`
    §10 items 10a/10c: at this project's own reference scale, `drift`'s
    own `_inversion_binomial` primitive (needed to keep this backend
    bit-identical to `LinealBackend`, see below) is pure Python and
    holds the GIL for nearly all of `drift`'s own wall-clock time. That
    measurement predates §10 item 10e's own stages 1-3b (2026-09-03):
    `migrate`'s own scalar-rate, continuous-migration path and `mutate`'s
    own event-count draw, whole-generation minting, source attribution,
    and (below a per-locus capacity bound) target selection are now
    `nogil`-compiled too, under `jit=True` — the "unjitted and GIL-bound
    regardless of `jit`" claim this paragraph made through 2026-09-02 is
    no longer an accurate description of the code. Whether that
    translates into real wall-clock thread scaling is a genuinely
    different, re-measured-not-yet question — `20260903-claude-sonnet-
    5-fim-vg-performance-campaign-design.md` §6.3 item 1 schedules the
    re-sweep this claim now depends on; until that lands, treat this
    class's own real-world thread-scaling behavior as unmeasured against
    the current code, not as either the old regression or a new win.
    What this class does guarantee, unconditionally:
    identical results to `SequentialAdvancer` for the same seed, since
    the actual per-lane computation is the same code
    (`SequentialAdvancer.advance` itself, called once per block), only
    fanned out across threads rather than run in one.

    Block assignment is computed fresh every tick from whichever lanes
    happen to be active (`_partition_into_blocks`), not a persistent,
    static assignment decided once at batch start — worth revisiting as
    a later, separately-measured optimization once lane membership is
    treated as fixed-and-masked rather than genuinely shrinking, but not
    this class's own current scope.
    """

    def __init__(
        self, max_workers: int | None = None, *, jit: JitOption = "off"
    ) -> None:
        """Configure how many blocks/threads this advancer fans out across.

        Args:
            max_workers: Defaults to `os.cpu_count()` (or 1 if that
                cannot be determined) — the same default reasoning
                `concurrent.futures.ThreadPoolExecutor` itself uses.
                Deliberately not the same `max_workers` `simulate_params()`'s own
                public signature accepts — that one is `LinealBackend`-
                only (a process count); reusing the name for a thread
                count here was flagged, not settled, by the design this
                implements, so this constructor's own parameter is
                reachable only by building a `GenerationalBackend`
                directly, not yet through `simulate_params()` itself.
            jit: Passed through to each block's own `SequentialAdvancer`
                — see its docstring. ``"numba"`` JIT-compiles `drift`'s
                own multinomial draw (`fim.model.operators.drift`'s own
                docstring) — bit-identical, and no longer regresses
                wall-clock time the way an earlier, per-pair-call version
                measurably did, but is not yet shown to be a clear
                standalone win either: `drift`'s own dominant cost at
                this project's reference scale is marshaling to and from
                `ModelState`'s sparse representation, not the compiled
                draw itself, so this setting alone should not be assumed
                to speed up `ThreadedAdvancer`'s own thread fan-out in
                practice. `migrate`/`mutate`'s RNG calls stay unjitted
                and GIL-bound regardless of this setting either way.

        Raises:
            ValueError: If `max_workers` is given and is less than 1.
        """
        self._max_workers = (
            max_workers if max_workers is not None else (os.cpu_count() or 1)
        )
        if self._max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        self._jit = jit
        # Built lazily, on first `advance()` call, and reused for every
        # subsequent tick of this `ThreadedAdvancer`'s own lifetime,
        # rather than a fresh `ThreadPoolExecutor` (and its own OS
        # thread spin-up/tear-down) every single generation — a real,
        # measured cost, not a hypothetical one (design doc
        # `20260901-claude-sonnet-5-fim-engine-backend-factory-
        # design.md` S10 item 10a: a 150-generation run at a high
        # thread count pays 150 rounds of this if it is not cached).
        # Always built at `self._max_workers` size regardless of how
        # many lanes are active on the *first* tick — harmless, since
        # `advance()` never submits more than `len(blocks)` (already
        # capped at `self._max_workers`) tasks to it on any given tick;
        # an idle worker thread costs nothing beyond its own OS
        # resources, and Python's own `concurrent.futures` registers an
        # `atexit` hook that joins every outstanding executor's threads
        # before the process exits, so leaving this open for this
        # object's own lifetime (never explicitly shut down) is safe,
        # not a leak. `_sequential` is cached the same way — its own
        # `__init__` only stores a `bool`, but there is no reason to
        # reallocate one every tick either.
        self._executor: ThreadPoolExecutor | None = None
        self._sequential = SequentialAdvancer(jit=jit == "numba")

    def advance(
        self,
        active_lanes: Sequence[ReplicaLane],
        store: TrajectoryStore,
    ) -> list[ReplicaLane]:
        """Step every active lane by one generation, fanned out across threads."""
        blocks = _partition_into_blocks(active_lanes, self._max_workers)
        if not blocks:
            return []
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=self._max_workers)
        newly_stopped: list[ReplicaLane] = []
        futures = [
            self._executor.submit(self._sequential.advance, block, store)
            for block in blocks
        ]
        for future in futures:
            newly_stopped.extend(future.result())
        return newly_stopped


class VectorizedAdvancer:
    """Steps every currently-active lane forward by one generation, array-native.

    Backend V's `Advancer`. Each lane's generation-zero `ModelState` is
    converted exactly once, the first tick the lane is advanced, into a
    dense `fim.model.vector_block.VectorBlock` cached on the lane
    (`ReplicaLane.vectorized_state`); from then on every generation is one
    compiled kernel call over every locus, with no `ModelState` in
    between. Trajectory rows are written straight from the arrays,
    convergence statistics are read from them, and a real `ModelState` is
    rebuilt once per lane, when its monitor reports it has stopped, for
    the report and `RunResult` machinery that needs one.

    **Both mutation models** run on the block, in the same stage and draw
    order as `fim.model.operators.step`, with the arithmetic of the
    dictionary-based operators ported operation for operation. For the same
    seed on the same platform the trajectory rows, report and final state
    are therefore identical to Backend L's and G's, every allele identity
    and frequency bit. Under infinite alleles the lane's `AlleleRegistry`
    counter is brought up to the identities the block handed out when the
    lane stops; under finite alleles the K-allele target draws are made in
    the kernel, interleaved with the next pair's mutation counts exactly as
    `operators.mutate` does. The convergence statistics `D`, `G_ST`, `H_S`,
    `H_T` and `H_ST` are computed inside the kernel and equal
    `statistics_report`'s bits; the opt-in expensive statistics (`E_ST`,
    `K_ST`, `A_CGD`, `Delta`, `MI`) stay in Python.

    Both migrant sampling modes run in the kernel. Under
    `migrant_sampling="stochastic"` the kernel draws one binomial migrant
    count per destination deme per generation, shared by every locus and
    ahead of drift, exactly as `operators.migrate` does with a generator.
    A genuine caller-supplied `(d, d)` migration matrix is supported (one
    exact sum per cell in the continuous case).

    Args:
        memory_ceiling_bytes: Per-lane ceiling on the table, in bytes;
            `None` uses the 2 GiB default or the
            `FIM_VECTOR_MEMORY_CEILING_BYTES` environment variable
            (`fim.model.vector_block.resolve_memory_ceiling`). A lane
            whose table would exceed it raises
            `fim.model.vector_block.VectorMemoryCeilingError`, naming
            what to change.
    """

    def __init__(self, *, memory_ceiling_bytes: int | None = None) -> None:
        """Remember the per-lane memory ceiling for new tables.

        Args:
            memory_ceiling_bytes: See the class docstring.
        """
        self._memory_ceiling_bytes = memory_ceiling_bytes

    def advance(
        self,
        active_lanes: Sequence[ReplicaLane],
        store: TrajectoryStore,
    ) -> list[ReplicaLane]:
        """Step every active lane by one generation, array-native.

        Builds each lane's block on its first tick, steps it, writes the
        generation's rows, and records the convergence statistics. When a
        lane's monitor reports stopped, `lane.state` (stale until now) is
        rebuilt from the block and the lane's registry counter is brought
        up to the identities the block minted.

        Args:
            active_lanes: The lanes still running.
            store: Where each lane's rows for the new generation go.

        Returns:
            The lanes whose monitor reports stopped, including one that was
            already stopped before this call and is returned unstepped.
        """
        newly_stopped: list[ReplicaLane] = []
        for lane in active_lanes:
            if lane.monitor.should_stop():
                # A lane can be stopped from outside the loop below: a
                # replicate paused for its batch's averaging window stops
                # the moment `run_batch` hands it a window ending where it
                # paused. Its `lane.state` is still the stale generation
                # zero, so the report would describe the initial state.
                if lane.vectorized_state is not None:
                    lane.state = lane.vectorized_state.to_model_state()
                    lane.vectorized_state.sync_registry(lane.registry)
                newly_stopped.append(lane)
                continue
            block = lane.vectorized_state
            if block is None:
                # Only the very first tick: `lane.state` is still the real
                # generation-zero `ModelState`. Every later tick steps the
                # cached block; this conversion never runs again.
                block = VectorBlock.from_model_state(
                    lane.state,
                    sizes=expand_population_sizes(
                        lane.params.gene_copies, lane.state.deme_count
                    ),
                    mutation_rates=lane.params.mutation_rates,
                    migration=VectorMigration.from_parameter(
                        lane.params.m,
                        lane.state.deme_count,
                        stochastic=lane.params.migrant_sampling == "stochastic",
                    ),
                    mutation_model=lane.params.mutation_model,
                    next_id=lane.registry.next_value,
                    memory_ceiling_bytes=self._memory_ceiling_bytes,
                )
                lane.vectorized_state = block
            block.advance(lane.rng)
            lane.monitor.record(
                block.generation, convergence_values_vectorized(block, lane.params)
            )
            if lane.retention.keeps(block.generation) or lane.monitor.should_stop():
                _persist_block(store, lane.run_id, block)
            if lane.monitor.should_stop():
                # `lane.state` has been left stale, at generation zero,
                # this whole time: materialize the real final state now,
                # the one point something outside the array-native loop
                # needs it.
                lane.state = block.to_model_state()
                block.sync_registry(lane.registry)
                newly_stopped.append(lane)
        return newly_stopped


def frame_layout(params: SimulationParams) -> FrameLayout:
    """Return the frame layout of a run: its locus ids and gene copies per deme.

    Args:
        params: The run's parameters.

    Returns:
        The layout a `FrameStore` is told through `begin_store_run`.
    """
    return FrameLayout(
        locus_ids=tuple(locus.locus_id for locus in params.loci),
        deme_sizes=tuple(int(size) for size in params.population_sizes),
    )


def equilibrium_frame_layout(params: SimulationParams) -> FrameLayout:
    """Return the frame layout of a run's ancestral (equilibrium-split) phase.

    One panmictic ancestral population of every deme's gene copies together
    (`EquilibriumSplitInitialCondition`), so its frames have one deme of that
    size.

    Args:
        params: The run's parameters.

    Returns:
        The layout the ancestral-phase store is told through `begin_store_run`.
    """
    return FrameLayout(
        locus_ids=tuple(locus.locus_id for locus in params.loci),
        deme_sizes=(sum(int(size) for size in params.population_sizes),),
    )


def _persist_block(store: TrajectoryStore, run_id: str, block: VectorBlock) -> None:
    """Write Backend V's current generation to `store`.

    A store that prefers frames (`store_wants_frames`) gets the block's
    frame, built by one compiled call. Any other store gets the rows, as
    always, so a custom store is unaffected.

    Args:
        store: The run's store.
        run_id: The run being written.
        block: The array-native state, already advanced.
    """
    if store_wants_frames(store, run_id):
        cast("FrameStore", store).write_frame(run_id, block.frame())
    else:
        store.write_generation(
            run_id, block.generation, block.rows(run_id), validate=False
        )


def persist_state(store: TrajectoryStore, run_id: str, state: ModelState) -> None:
    """Write a `ModelState` generation (Backends L and G) to `store`.

    The rows or frame are built by this module, from a state whose own
    construction validated every value, so they are written unvalidated.

    Args:
        store: The run's store.
        run_id: The run being written.
        state: The generation.
    """
    if store_wants_frames(store, run_id):
        cast("FrameStore", store).write_frame(run_id, state_to_frame(state))
    else:
        store.write_generation(
            run_id, state.generation, state.to_rows(run_id), validate=False
        )


def build_replica_lane(
    params: SimulationParams,
    replica_index: int,
    run_id: str | None,
    store: TrajectoryStore,
    clock: Clock,
    planner: BatchWindowPlanner | None = None,
) -> ReplicaLane:
    """Build and seed one replica lane through generation zero.

    Mirrors `run_one`'s own setup exactly — seed derivation, registry
    start, finite-alleles construction, generation-zero persistence and
    convergence recording, in the same order — so a `ReplicaLane`'s
    subsequent generations, advanced one at a time by an `Advancer`, are
    bit-identical to what `run_one` alone would have produced for the
    same replica. `run_id`/`params` naming for a multi-replicate batch
    exactly matches `LinealBackend`'s own sequential-batch branch, so the
    two backends' own run ids agree for the same configuration.
    """
    window = planner.window_for(replica_index) if planner is not None else None
    if params.n_replicates == 1:
        lane_run_id = run_id or deterministic_run_id(params)
        lane_params = params
    else:
        lane_params = params_for_replicate(params, replica_index, window)
        lane_run_id = derive_replicate_run_id(params, replica_index, run_id)
    logger.info(
        "replicate %s starting (N=%s, d=%s, m=%s, mu=%s, seed=%s, max_generations=%s)",
        lane_run_id,
        lane_params.gene_copies,
        lane_params.d,
        lane_params.m,
        lane_params.mu,
        lane_params.seed,
        lane_params.max_generations,
    )
    started_at = format_timestamp(clock())
    rng = np.random.Generator(np.random.PCG64(lane_params.seed))
    # A replicate past the first wave of a batch may not know its window yet:
    # it averages from the burn-in and waits for the wave to be measured.
    monitor = burn_in_monitor(
        lane_params, awaiting_window=planner is not None and window is None
    )
    state, equilibration_outcome, equilibrium_store = (
        generate_initial_state_with_outcome(lane_params, rng, store, lane_run_id)
    )
    highest_initial_id = max(
        (
            int(allele_id)
            for deme in state.frequencies
            for locus in deme
            for allele_id in locus
        ),
        default=MINTED_ID_START - 1,
    )
    registry = AlleleRegistry(start=max(MINTED_ID_START, highest_initial_id + 1))
    finite_alleles = (
        FiniteAlleleRegistry(build_finite_allele_spaces(state, lane_params))
        if lane_params.mutation_model == "finite_alleles"
        else None
    )
    begin_store_run(store, lane_run_id, frame_layout(lane_params))
    persist_state(store, lane_run_id, state)
    monitor.record(state.generation, convergence_values(state, lane_params))
    return ReplicaLane(
        replica_index=replica_index,
        run_id=lane_run_id,
        params=lane_params,
        state=state,
        rng=rng,
        registry=registry,
        finite_alleles=finite_alleles,
        monitor=monitor,
        started_at=started_at,
        equilibration_outcome=equilibration_outcome,
        equilibrium_store=equilibrium_store,
        retention=TrajectoryRetention.from_params(lane_params),
    )


def finalize_replica_lane(
    lane: ReplicaLane,
    clock: Clock,
    store: TrajectoryStore,
) -> RunResult:
    """Build this lane's own `RunResult`, once its monitor has stopped.

    Mirrors the tail of `run_one` exactly — same report/manifest
    construction, from this lane's own final state and monitor history.

    Also releases `lane.vectorized_state` (a `VectorizedAdvancer`-only
    cache — see `ReplicaLane`'s own docstring) the moment it stops being
    needed: `VectorizedAdvancer.
    advance` already rebuilt `lane.state` (the `ModelState` the
    `RunResult` below actually reads) from `lane.vectorized_state`
    before ever calling this function, so the dense array itself is
    pure dead weight from here on — `run_batch`'s own `lanes` list still
    holds this `ReplicaLane` until the whole batch returns, so without
    this, a finished lane's own cached state would stay alive for the
    rest of the batch's run for nothing. `FIM-48` (this project's own
    multi-model engine review, 2026-09-04): the fix `run_batch`'s own
    bounded window bounds how many lanes hold this cache *concurrently*;
    this releases *each one's own copy* the instant its lane is done,
    independent of whether a window is configured at all. A no-op for
    every other `Advancer`, whose lanes never populate either field to
    begin with.
    """
    # This lane has written its last generation: release its trajectory
    # file now, so a batch holds open only the files of the lanes still
    # running (at most `max_concurrent_replicates`), never one per
    # replicate for the whole batch.
    close_run_store(store, lane.run_id)
    outcome = lane.monitor.outcome()
    lane.vectorized_state = None
    if outcome.reason is None:
        # Unreachable in practice — see `run_one`'s own identical guard
        # for why this is checked explicitly rather than assumed.
        raise RuntimeError("stopped convergence monitor has no reason")
    if (
        outcome.reason is StopReason.AVERAGING_COMPLETE
        and not lane.params.replicate_averaging_window
        and outcome.generation is not None
    ):
        # A replicate that waited for the batch's window averaged for however
        # long it took to learn it; record that as the fixed window which
        # would reproduce the replicate exactly.
        lane.params = replace(
            lane.params,
            replicate_averaging_window=outcome.generation
            - lane.monitor.burn_in_generation,
        )
    report = report_for_state(
        lane.state,
        lane.params,
        run_id=lane.run_id,
        converged=outcome.converged,
        reason=outcome.reason.value,
        window_statistics=window_statistics_payload(lane.monitor),
        converged_statistics=lane.monitor.stable_statistics(),
    )
    ended_at = format_timestamp(clock())
    logger.info(
        "replicate %s finished: %s at generation %d (converged=%s)",
        lane.run_id,
        outcome.reason.value,
        lane.state.generation,
        outcome.converged,
    )
    manifest = RunManifest(
        schema_version=CURRENT_SCHEMA_VERSION,
        run_id=lane.run_id,
        parameters=lane.params.to_dict(),
        started_at=lane.started_at,
        ended_at=ended_at,
        converged=outcome.converged,
        convergence_statistic=lane.params.convergence_statistic,
        stop_reason=outcome.reason.value,
        generation=lane.state.generation,
        generation_count=len(lane.monitor.generations),
        software_version=__version__,
        initial_condition_mode=initial_condition_mode(lane.params),
        equilibrium_generation_count=(
            lane.equilibration_outcome.generation_count
            if lane.equilibration_outcome is not None
            else None
        ),
        equilibrium_final_heterozygosity=(
            lane.equilibration_outcome.final_heterozygosity
            if lane.equilibration_outcome is not None
            else None
        ),
        # Copied from `lane.params` (`RunManifest`'s own docstring on
        # these two fields has the full account of why `parameters`
        # above cannot carry this) -- `tuple(sorted(...))` for a
        # deterministic manifest.json regardless of `frozenset`'s own
        # unordered iteration.
        auto_derived=tuple(sorted(lane.params.auto_derived)),
        relaxation_time=lane.params.relaxation_time,
    )
    return RunResult(
        run_id=lane.run_id,
        params=lane.params,
        final_state=lane.state,
        report=report,
        convergence_generations=lane.monitor.generations,
        convergence_history=lane.monitor.history,
        convergence_histories=lane.monitor.histories,
        manifest=manifest,
        store=store,
        equilibrium_store=lane.equilibrium_store,
    )


def generate_initial_state_with_outcome(
    params: SimulationParams,
    rng: np.random.Generator,
    store: TrajectoryStore,
    run_id: str,
) -> tuple[ModelState, EquilibrationOutcome | None, TrajectoryStore | None]:
    """Generate generation zero, capturing the equilibration outcome when relevant.

    `generate_initial_state` itself always discards `EquilibriumSplit
    InitialCondition`'s own richer `EquilibrationOutcome` (see that
    function's own docstring) — `build_replica_lane`/`run_one`, this
    function's own two callers, are the ones that actually need it, to
    persist it into this run's own manifest (`20260907-claude-sonnet-5-
    equilibrium-split-design.md`, decision 5). Every engine path —
    `LinealBackend` through `run_one`, `GenerationalBackend` through
    `build_replica_lane` — reaches the ancestral phase here
    (the ancestral phase runs on the dictionary-based operators under
    every backend; `generational-vector` then continues from its
    founding state), so this is also the one place its trajectory is
    persisted: each
    ancestral generation is written, as it is
    simulated, to `store`'s ancestral-phase companion
    (`fim.persistence.store.equilibrium_store_for`; beside a JSONL
    `trajectory.tlog`, that is `equilibrium_trajectory.tlog`), keyed
    by `run_id` and numbered by the ancestral phase's own counter. That
    companion is returned third, `None` when there was no ancestral
    phase. The dispatch condition below intentionally
    mirrors `generate_initial_state`'s own: `SimulationParams.
    __post_init__` guarantees all three `equilibrium_*` fields are set
    together or not at all, so checking all three here (rather than
    reusing that function's own boolean result) is what actually lets
    the fields be passed to `EquilibriumSplitInitialCondition` below
    without a further, unchecked `None` for type-checking purposes.
    """
    if (
        params.equilibrium_convergence_window is not None
        and params.equilibrium_convergence_tolerance is not None
        and params.equilibrium_max_generations is not None
    ):
        generator = EquilibriumSplitInitialCondition(
            convergence_window=params.equilibrium_convergence_window,
            convergence_tolerance=params.equilibrium_convergence_tolerance,
            max_generations=params.equilibrium_max_generations,
        )
        equilibrium_store = equilibrium_store_for(store, run_id)
        begin_store_run(equilibrium_store, run_id, equilibrium_frame_layout(params))

        def write_ancestral_generation(ancestral: ModelState) -> None:
            # Rows or frames built from a valid state need no re-validation
            # (`fim.persistence.store`'s top docstring).
            persist_state(equilibrium_store, run_id, ancestral)

        state, outcome = generator.generate_with_outcome(
            params, rng, on_generation=write_ancestral_generation
        )
        return state, outcome, equilibrium_store
    return generate_initial_state(params, rng), None, None


def initial_condition_mode(params: SimulationParams) -> str:
    """Return this run's own resolved initial-condition strategy name.

    `RunManifest.initial_condition_mode`'s own value — computed here
    rather than stored as a matching `SimulationParams` field, the same
    "record the resolved runtime choice, not a repeated copy of the
    dispatch rule" shape `engine_backend`'s own manifest field already
    uses for `engine_backend="auto"`.
    """
    if params.equilibrium_convergence_window is not None:
        return "equilibrium_split"
    if params.initial_frequencies is not None:
        return "explicit"
    return "dirichlet"


def build_finite_allele_spaces(
    state: ModelState,
    params: SimulationParams,
) -> dict[int, FiniteAlleleSpace]:
    """Construct one finite-allele state space per locus, seeded from generation zero.

    By default, a new mutation always creates a brand-new allele
    identity that has never existed before in the run (the "infinite
    alleles" model — a standard simplifying assumption in population
    genetics, reasonable when mutations are rare enough that the same
    exact mutation recurring twice by chance is negligible). This
    project also supports an opt-in alternative, the "finite alleles" (or
    "K-allele") model (`SimulationParams.mutation_model ==
    "finite_alleles"`, design §9): at each locus, only a fixed, finite
    set of allele identities can ever exist — every distinct DNA
    sequence of that locus's own length is one possible allele, so a
    locus `n` bases long admits `4**n` of them, one possibility per base
    for each of the four DNA letters (see `finite_allele_capacity`). A
    mutation instead picks uniformly among whichever of that fixed set
    is not the current allele, which lets the *same* allele recur
    independently at different points in the run, closer to how a real,
    physically limited genetic sequence actually behaves. This function
    builds the
    bookkeeping (`FiniteAlleleSpace`, one per locus) that tracks and
    enforces that fixed set for a single run, seeded from whichever
    alleles are already present in the population at generation zero.

    Args:
        state: The run's generated generation-zero state.
        params: Validated run parameters.

    Returns:
        One `FiniteAlleleSpace` per locus, keyed by `LocusSpec.locus_id`.
    """
    return {
        locus.locus_id: FiniteAlleleSpace(
            finite_allele_capacity(locus.length),
            (
                allele_id
                for deme in state.frequencies
                for allele_id in deme[locus_index]
            ),
        )
        for locus_index, locus in enumerate(params.loci)
    }
