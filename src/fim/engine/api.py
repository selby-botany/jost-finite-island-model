"""The engine entry point: `simulate_params`."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace

from fim.engine.backends import build_engine_backend, resolve_auto_engine_backend
from fim.engine.records import (
    Clock,
    SimulationOutput,
    utc_now,
)
from fim.model.params import SimulationParams
from fim.persistence.store import TrajectoryStore

logger = logging.getLogger(__name__)


def simulate_params(
    params: SimulationParams,
    *,
    store: TrajectoryStore | None = None,
    run_id: str | None = None,
    clock: Clock | None = None,
    max_workers: int | None = None,
    store_factory: Callable[[str], TrajectoryStore] | None = None,
) -> SimulationOutput:
    """Run the finite island model until convergence or the hard cap.

    The engine's one entry point. Given a validated configuration
    (`params`), it simulates one population's generations one at a time
    (migration, then drift, then mutation, following
    `fim.model.operators.step`) until the watched statistic(s) settle or the
    hard generation cap is reached, then returns everything about the
    finished run. If `SimulationParams.n_replicates` is more than one, it
    does that once per independently seeded replicate, running every
    requested replicate or (with `SimulationParams.stop_batch_early` set)
    stopping once enough have run to pin down the answer. Which engine runs
    it is `params.engine_backend` (with `params.jit` and the `auto`
    thresholds): there is no second place to say so.

    Most callers want `fim.simulate`, which takes a `RunSpec`, always returns
    a `Result`, and can save the run. This function returns the engine's own
    `RunResult` (one, or a tuple for a batch) and is for code that already
    holds `SimulationParams`.

    Args:
        params: The validated run configuration.
        store: Where to write each generation's state as the run proceeds
            (default: in memory only). One store is shared across a
            sequential batch, each replicate's rows distinguished by its
            `run_id`; it must be `None` when `max_workers` is set, since two
            processes cannot share one Python object. Mutually exclusive with
            `store_factory`.
        run_id: This run's identifier (default: derived from the parameters,
            see `deterministic_run_id`).
        clock: Source of "now" for manifest timestamps; tests supply a fixed
            one. Under `max_workers` it crosses a process boundary, so it
            must be picklable (a module-level function, not a closure).
        max_workers: Run a `lineal` batch's replicates across this many
            processes instead of one after another. `None` runs them one at
            a time. The adaptive stop is still judged in replicate order, so
            a batch can overshoot by at most `max_workers - 1` replicates,
            whose results are discarded. Meaningful only for `lineal`; any
            other backend refuses it (see `max_concurrent_replicates` for the
            generational equivalent).
        store_factory: Builds one fresh store per replicate from its run ID
            (required, not just useful, with `max_workers`, and picklable in
            the same sense as `clock`). With neither this nor `store`, a
            private in-memory store is used.

    Returns:
        One result, or one independently seeded result per replicate. With
        `stop_batch_early` on (the default) replicates stop accumulating once
        every watched statistic's across-replicate interval is within
        `precision`, so the tuple can be shorter than `n_replicates`.

    Raises:
        ValueError: `store` and `store_factory` are both given, `max_workers`
            is combined with a `store` or with a backend other than
            `"lineal"`, or the backend cannot run (for example it needs
            `numba`; see `build_engine_backend`).
    """
    engine_backend = params.engine_backend
    jit = params.jit
    auto_vector_min_d = params.auto_vector_min_d
    auto_vector_max_capacity = params.auto_vector_max_capacity
    # `max_workers` sizes a `ProcessPoolExecutor` that only `LinealBackend`
    # ever builds; no other backend has a process pool for it to mean
    # anything about.
    if engine_backend != "lineal" and max_workers is not None:
        raise ValueError(
            "max_workers is lineal-backend-only; it has no effect under "
            f"engine_backend={engine_backend!r} — see max_concurrent_"
            "replicates for the generational/generational-vector "
            "equivalent concurrency control"
        )
    run_clock = clock if clock is not None else utc_now
    if params.convergence_burn_in >= params.max_generations:
        # Honest, not refused: such a run ends at its cap without ever
        # averaging, and says so (capped, with no evidence window).
        logger.warning(
            "max_generations (%d) is not above the burn-in (%d generations), so "
            "the run will stop before averaging starts; raise max_generations "
            "or set a shorter convergence_burn_in",
            params.max_generations,
            params.convergence_burn_in,
        )
    # The *resolved* backend choice — never the literal string "auto" —
    # computed once, up front, for stamping every result's own manifest
    # afterward (`RunManifest.engine_backend`'s own docstring; design doc
    # §7.4), rather than recomputed a second time after `backend.run`
    # already returned.
    resolved_engine_backend = (
        resolve_auto_engine_backend(
            params, auto_vector_min_d, auto_vector_max_capacity, jit
        )
        if engine_backend == "auto"
        else engine_backend
    )
    # `simulate_params()` itself only validates its own public signature and picks a
    # backend (`build_engine_backend`); every actual dispatch decision
    # (scalar vs. sequential batch vs. process-parallel batch, or the
    # generation-first driving loop) lives in that backend's own `run`.
    backend = build_engine_backend(
        engine_backend,
        jit=jit,
        max_workers=max_workers,
        store_factory=store_factory,
        params=params,
        auto_vector_min_d=auto_vector_min_d,
        auto_vector_max_capacity=auto_vector_max_capacity,
    )
    output = backend.run(params, store, run_id, run_clock)
    # Stamp every result's own manifest with the resolved backend
    # choice computed above, so a runtime-data-dependent "auto" decision
    # is not lost to the persisted record.
    if isinstance(output, tuple):
        return tuple(
            replace(
                result,
                manifest=replace(
                    result.manifest,
                    engine_backend=resolved_engine_backend,
                    jit=jit,
                ),
            )
            for result in output
        )
    return replace(
        output,
        manifest=replace(
            output.manifest, engine_backend=resolved_engine_backend, jit=jit
        ),
    )
