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

    Each active lane advances one generation per call to
    `advancer.advance`. Adaptive stopping is evaluated on the accepted
    replicate prefix: the cross-replicate monitor receives results in
    ascending replicate order, regardless of lane completion order.
    This preserves the stopping decision and accepted replicate set
    relative to `LinealBackend` for the same seed. Lanes continue
    advancing while a lower-numbered replicate is unfinished; completed
    results are held until they join the prefix. Lanes beyond a stopping
    point are discarded, including their stored trajectory rows.

    At most `params.max_concurrent_replicates` lanes are active
    simultaneously. When set, additional lanes are constructed as slots
    become available; no new lane is built after the adaptive monitor
    stops. Limiting active lanes bounds per-lane state and vectorized
    buffers independently of the total replicate count.
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
