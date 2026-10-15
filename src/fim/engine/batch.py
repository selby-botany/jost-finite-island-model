"""The generation-first batch driver and its adaptive stop."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from fim.convergence.batch_window import BatchWindowPlanner
from fim.engine.lanes import (
    Advancer,
    ReplicaLane,
    build_replica_lane,
    finalize_replica_lane,
)
from fim.engine.monitors import (
    batch_window_planner,
    replicate_monitor,
    replicate_stopping_values,
    window_noise,
)
from fim.engine.records import Clock, RunResult
from fim.model.params import SimulationParams
from fim.persistence.store import TrajectoryStore

logger = logging.getLogger(__name__)


def _require_lane_result(lane: ReplicaLane) -> RunResult:
    """Return a finalized lane's own result, or raise if it has none.

    Unreachable in practice: `run_batch`'s own loop always sets `result`
    in the same step it marks a lane inactive (see `run_batch`, below),
    so a lane reached here with `active=False` and `result=None` would
    mean that invariant broke — guarded explicitly rather than assumed,
    the same discipline `run_one`'s own "stopped convergence monitor has
    no reason" guard already uses.
    """
    if lane.result is None:
        raise RuntimeError(f"replicate {lane.run_id} has no result")
    return lane.result


def run_batch(
    params: SimulationParams,
    store: TrajectoryStore,
    run_id: str | None,
    clock: Clock,
    advancer: Advancer,
) -> tuple[RunResult, ...]:
    """Run every replicate `params` describes, generation-first.

    The generation-first counterpart to `LinealBackend`'s own
    replica-first dispatch: every currently-active lane's own generation
    *g* is advanced before any of them moves on to generation *g + 1*
    (via `advancer.advance`), rather than one replica's entire trajectory
    running to completion before the next starts.

    An adaptive early stop (`SimulationParams.
    stop_batch_early`; see `replicate_monitor`) is judged on an
    *accepted prefix*, in replicate order, exactly as `LinealBackend`'s
    own sequential loop judges it: replicate *i* is fed to the cross-
    replica monitor only once it and every lower-numbered replicate have
    finished, so the monitor sees replicates 1, 2, 3, ... in that order
    regardless of which lane happened to converge first. The stop fires
    at the same replicate, on the same values, as `LinealBackend` for
    the same seed, so both backends keep the same replicates and report
    the same summary. Feeding the monitor in finishing order instead
    (this function's own behavior through 2026-10-06) kept whichever
    replicates converged soonest; convergence time correlates with the
    statistics being estimated (in the adaptive worked example, the
    replicates that converge earliest have lower `D`), so that order
    biased the batch's own estimate rather than merely reordering it.

    Waiting for a slow, low-numbered replicate costs only time, never
    correctness: every lane keeps advancing, generation by generation,
    while the prefix waits. A lane that finishes ahead of the prefix is
    finalized immediately (its `RunResult` held until the prefix reaches
    it, the same memory every kept replicate already uses) and its
    window slot (`max_concurrent_replicates`) is reused at once. Once the
    stop fires, every lane past the stopping replicate — still running,
    or finished but not yet admitted — is discarded, exactly as
    `LinealBackend` never runs, and `run_batch_parallel` discards,
    replicates past its own stopping point.

    Every lane owns its own, never-shared `np.random.Generator` and its
    own `AlleleRegistry` — nothing here ever reads or writes another
    lane's state, which is what makes the traversal order above safe:
    reordering *when* one lane's generation is computed relative to
    another's never changes that lane's own result, and the accepted-
    prefix rule above means it never changes *which* lanes are kept.

    An adaptive stop's own discarded lanes have their own rows discarded
    from `store` before returning, not left behind as orphaned data no
    returned `RunResult` accounts for (this project's own multi-model
    engine review, 2026-09-04, `FIM-49`/finding M-01/finding P2-1 case
    1). `build_replica_lane` already wrote each lane's own generation
    zero eagerly, before this loop ever runs, so even a lane the
    generation-first loop below never advances past that point still
    has rows to discard, not merely lanes that got partway through.

    At most `params.max_concurrent_replicates` lanes are ever alive at
    once — `None` (the default) means every requested replicate, exactly
    every prior release's own behavior, since `min(window_size,
    n_replicates)` below then equals `n_replicates` itself and every
    lane is built up front as before. A smaller window instead builds
    lanes lazily: only the window's own worth exist up front, and a
    lane is constructed for replicate index *i* only once some earlier
    lane's own slot frees up, immediately before advancing past that
    point — never merely reserved. This is `FIM-45`/`P-04`/`P2-7`'s own
    fix (a replicate `run_batch` never gets to before an adaptive stop
    now never pays for that replicate's own setup either, on any
    `Advancer`) and, for `VectorizedAdvancer` specifically,
    `FIM-48`'s own fix too: that advancer's cached `VectorBlock`
    (`ReplicaLane.vectorized_state`, released immediately once a lane
    finalizes — see `finalize_replica_lane`) is held by every
    concurrently active lane at once, so bounding how many lanes are
    ever active at once directly bounds this backend's own steady-state
    memory, independent of `n_replicates` itself. A replacement lane is
    built only *after* confirming the cross-replica monitor has not
    just decided to stop — building one first and discarding it a line
    later would reintroduce exactly the wasted setup this fix exists to
    remove.
    """
    window_size = params.max_concurrent_replicates or params.n_replicates
    next_unbuilt_index = min(window_size, params.n_replicates)
    # The first wave the averaging windows are matched from is a property of
    # the configuration (`batch_window_planner`), not of this machine.
    planner = batch_window_planner(params)
    # Lanes are only ever built in ascending replica order and appended,
    # so `lanes[i].replica_index == i` throughout: the accepted prefix
    # below is a plain slice.
    lanes = [
        build_replica_lane(params, replica_index, run_id, store, clock, planner)
        for replica_index in range(next_unbuilt_index)
    ]
    cross_monitor = replicate_monitor(params)
    admitted_count = 0
    while any(lane.active for lane in lanes):
        active_lanes = [
            lane
            for lane in lanes
            if lane.active and not waiting_for_window(lane, planner)
        ]
        newly_stopped = advancer.advance(active_lanes, store)
        for lane in sorted(newly_stopped, key=lambda lane: lane.replica_index):
            lane.active = False
            lane.result = finalize_replica_lane(lane, clock, store)
            if planner is not None:
                matched = planner.record(
                    lane.replica_index,
                    window_noise(lane.result.report, params.convergence_statistics),
                )
                if matched is not None:
                    # The first wave is measured: every replicate that was
                    # waiting for its window now knows it.
                    for waiting in lanes:
                        if waiting.active and waiting.monitor.awaiting_window:
                            waiting.monitor.set_averaging_window(matched)

        # Admit the accepted prefix: every finished lane whose lower-
        # numbered lanes have all finished too, in replica order — the
        # order `LinealBackend` feeds the same monitor.
        if cross_monitor is not None:
            while (
                admitted_count < len(lanes) and lanes[admitted_count].result is not None
            ):
                admitted = _require_lane_result(lanes[admitted_count])
                admitted_count += 1
                outcome = cross_monitor.record(
                    admitted_count,
                    replicate_stopping_values(admitted, params.convergence_statistics),
                )
                if outcome.stopped:
                    logger.info(
                        "adaptive replicate stop triggered after %d of %d replicate(s)",
                        admitted_count,
                        params.n_replicates,
                    )
                    return _finish_adaptive_stop(lanes, admitted_count, store)

        # Refill freed window slots only once the stop is ruled out, so
        # no lane is ever built just to be discarded.
        for _ in newly_stopped:
            if next_unbuilt_index < params.n_replicates:
                lanes.append(
                    build_replica_lane(
                        params, next_unbuilt_index, run_id, store, clock, planner
                    )
                )
                next_unbuilt_index += 1
    return tuple(_require_lane_result(lane) for lane in lanes)


def _finish_adaptive_stop(
    lanes: Sequence[ReplicaLane],
    accepted_count: int,
    store: TrajectoryStore,
) -> tuple[RunResult, ...]:
    """Keep the accepted prefix of an adaptively stopped batch; discard the rest.

    Split out of `run_batch` so its loop reads as one sequence of steps.
    Every lane past the stopping replicate is discarded, whether it is
    still running or already finished ahead of the prefix: its rows
    leave `store` (`FIM-49`), its `result` is dropped, and any
    `VectorizedAdvancer` cache it still holds is released.

    Args:
        lanes: Every lane `run_batch` built, in replica order.
        accepted_count: How many leading lanes the stop accepted; the
            stopping replicate is `lanes[accepted_count - 1]`.
        store: The batch's shared trajectory store.

    Returns:
        The accepted lanes' results, in replica order.
    """
    accepted = lanes[:accepted_count]
    for discarded in lanes[accepted_count:]:
        store.discard(discarded.run_id)
        # Its ancestral phase (an equilibrium-split founding) goes too.
        if discarded.equilibrium_store is not None:
            discarded.equilibrium_store.discard(discarded.run_id)
        discarded.active = False
        discarded.result = None
        discarded.vectorized_state = None
    logger.debug(
        "adaptive stop kept replicate(s) 1..%d, discarded %d later lane(s)",
        accepted_count,
        len(lanes) - accepted_count,
    )
    return tuple(_require_lane_result(lane) for lane in accepted)


def waiting_for_window(lane: ReplicaLane, planner: BatchWindowPlanner | None) -> bool:
    """Return whether a replicate must pause until its window is known.

    A replicate past the first wave learns its averaging window only when the
    wave has been measured. It may average until the shortest window it could
    be given, `BatchWindowPlanner.earliest_window`; beyond that it would risk
    overshooting its window, which would make its result depend on how far it
    had run when the wave ended, and so on the backend. It pauses there.

    Args:
        lane: A running lane.
        planner: The batch's planner, or `None`.

    Returns:
        `True` when the lane is waiting for its window and has reached the
        earliest generation it may stop at.
    """
    if planner is None or not lane.monitor.awaiting_window:
        return False
    reached = lane.monitor.window_end_generation or 0
    return reached >= lane.monitor.burn_in_generation + planner.earliest_window
