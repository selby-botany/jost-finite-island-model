"""The lineal (reference) backend and its single-replicate loop."""

from __future__ import annotations

import logging
import multiprocessing
import pickle
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ProcessPoolExecutor

import numpy as np

from fim import __version__
from fim.convergence.monitor import ConvergenceMonitor
from fim.engine.identity import (
    derive_replicate_run_id,
    deterministic_run_id,
    params_for_replicate,
)
from fim.engine.lanes import (
    build_finite_allele_spaces,
    frame_layout,
    generate_initial_state_with_outcome,
    initial_condition_mode,
    persist_state,
)
from fim.engine.monitors import (
    batch_window_planner,
    burn_in_monitor,
    replicate_monitor,
    replicate_stopping_values,
    window_noise,
)
from fim.engine.records import (
    Clock,
    RunResult,
    SimulationOutput,
    format_timestamp,
)
from fim.engine.report import report_for_state, window_statistics_payload
from fim.engine.tracking import convergence_values
from fim.model.allele import MINTED_ID_START, AlleleRegistry, FiniteAlleleRegistry
from fim.model.operators import step
from fim.model.params import SimulationParams
from fim.persistence.manifest import CURRENT_SCHEMA_VERSION, RunManifest
from fim.persistence.retention import TrajectoryRetention
from fim.persistence.store import (
    InMemoryTrajectoryStore,
    TrajectoryStore,
    begin_store_run,
    close_store,
    equilibrium_store_for,
)

logger = logging.getLogger(__name__)


class LinealBackend:
    """Today's engine: one replica's whole trajectory, start to finish,
    computed before any cross-replicate bookkeeping happens — either one
    at a time, or `max_workers` at a time across real OS processes. See
    `simulate_params()`'s own docstring, below, for the full behavior; this class's
    own `run` is that same behavior, unchanged, moved behind
    `EngineBackend`'s shared contract rather than living directly inside
    `simulate_params()`.
    """

    def __init__(
        self,
        *,
        max_workers: int | None = None,
        store_factory: Callable[[str], TrajectoryStore] | None = None,
    ) -> None:
        """Configure this backend's own concurrency and per-replicate storage.

        Args:
            max_workers: See `simulate_params()`'s own docstring.
            store_factory: See `simulate_params()`'s own docstring.
        """
        self._max_workers = max_workers
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
        # Validated here, unconditionally, rather than only inside the
        # `n_replicates > 1` branch below: a single-replicate run
        # (`n_replicates == 1`) used to skip straight past every one of
        # these checks via its own early return, so `max_workers=0`,
        # `max_workers` alongside a real `store`, or a picklability
        # failure all silently succeeded there instead of raising
        # (this project's own multi-model engine review, 2026-09-04,
        # `FIM-05`/finding Kimi-FIM-05).
        if self._max_workers is not None:
            if self._max_workers < 1:
                raise ValueError("max_workers must be at least 1")
            if store is not None:
                raise ValueError(
                    "max_workers requires store=None; a single store instance "
                    "cannot be shared across worker processes — pass "
                    "store_factory instead"
                )
            require_picklable("clock", clock)
            if self._store_factory is not None:
                require_picklable("store_factory", self._store_factory)
        # From here down, this is a three-way dispatch, in order of
        # increasing complexity: a single run (`n_replicates == 1`, the
        # common case) goes straight to `run_one`, without spinning up
        # a worker-process pool for exactly one replicate even if
        # `max_workers` is set — every check above still ran, and a
        # given `store_factory` still builds this run's own store
        # (`FIM-05`; previously ignored here, silently falling back to
        # a fresh `InMemoryTrajectoryStore()` instead); a multi-
        # replicate batch with `max_workers` set hands off to the
        # parallel-worker-process path (`run_batch_parallel`);
        # everything else is a multi-replicate batch run the plain way,
        # one replicate after another, in the loop at the bottom of
        # this method.
        if params.n_replicates == 1:
            run_identifier = run_id or deterministic_run_id(params)
            trajectory_store = (
                self._store_factory(run_identifier)
                if self._store_factory is not None
                else (store if store is not None else InMemoryTrajectoryStore())
            )
            logger.debug("LinealBackend: dispatching a single scalar run")
            return run_one(params, trajectory_store, run_identifier, clock)

        monitor = replicate_monitor(params)
        if self._max_workers is not None:
            logger.debug(
                "LinealBackend: dispatching a %d-replicate batch across up to "
                "%d worker(s)",
                params.n_replicates,
                self._max_workers,
            )
            return run_batch_parallel(
                params,
                self._max_workers,
                self._store_factory,
                run_id,
                clock,
                monitor,
            )

        trajectory_store = store if store is not None else InMemoryTrajectoryStore()
        logger.debug(
            "LinealBackend: dispatching a %d-replicate sequential batch",
            params.n_replicates,
        )
        # Replicate i is an independent scalar run with seed + i, preserving the
        # scalar trajectory for the first result and deterministic batch ordering.
        results: list[RunResult] = []
        planner = batch_window_planner(params)
        for replicate_index in range(params.n_replicates):
            replicate_params = params_for_replicate(
                params,
                replicate_index,
                planner.window_for(replicate_index) if planner else None,
            )
            replicate_run_id = derive_replicate_run_id(params, replicate_index, run_id)
            # A supplied `store_factory` gives every replicate its own fresh
            # store, exactly like the parallel path's workers; otherwise every
            # replicate reuses the one shared `trajectory_store`, unchanged
            # from every prior release.
            replicate_store = (
                self._store_factory(replicate_run_id)
                if self._store_factory is not None
                else trajectory_store
            )
            result = run_one(
                replicate_params,
                replicate_store,
                replicate_run_id,
                clock,
            )
            results.append(result)
            if planner is not None:
                planner.record(
                    replicate_index,
                    window_noise(result.report, params.convergence_statistics),
                )
            if monitor is not None:
                outcome = monitor.record(
                    replicate_index + 1,
                    replicate_stopping_values(result, params.convergence_statistics),
                )
                if outcome.stopped:
                    logger.info(
                        "adaptive replicate stop triggered after %d of %d replicate(s)",
                        replicate_index + 1,
                        params.n_replicates,
                    )
                    break
        return tuple(results)


def require_picklable(name: str, value: object) -> None:
    """Reject an unpicklable `max_workers` argument before any worker spawns.

    "Pickling" is Python's own built-in way of converting an object into
    a stream of bytes that can be sent somewhere else and turned back
    into an equivalent object later — the mechanism `ProcessPoolExecutor`
    uses under the hood to hand `clock` and `store_factory` (see `fim`'s
    own docstring) across the boundary from this process into each
    separate worker process it starts. Most ordinary Python values (and
    ordinary, named functions) can be pickled without any trouble; a
    closure (a function defined inside another function, capturing some
    of that outer function's own local state) or a `lambda` generally
    cannot, since there is no way to describe "the specific place in the
    code where this was defined, plus whatever local variables it
    captured" as a self-contained stream of bytes. Without this check,
    handing in an unpicklable value would fail anyway — but only later,
    silently, deep inside worker-process spawn machinery, surfacing as
    raw pickling error noise with no indication of which of `fim`'s own
    arguments was actually at fault. Checking here instead fails
    immediately at the call site, before a single worker process is
    even started, naming the exact offending argument by name.

    Args:
        name: The public keyword argument's name, for the error message.
        value: The callable to verify.

    Raises:
        ValueError: If `value` cannot be pickled.
    """
    try:
        pickle.dumps(value)
    except (AttributeError, pickle.PicklingError, TypeError) as error:
        raise ValueError(
            f"{name} must be picklable to cross a max_workers process "
            "boundary — a module-level function, or functools.partial "
            f"over one, never a closure or lambda: {error}"
        ) from error


def run_batch_parallel(
    params: SimulationParams,
    max_workers: int,
    store_factory: Callable[[str], TrajectoryStore] | None,
    run_id: str | None,
    clock: Clock,
    monitor: ConvergenceMonitor | None,
) -> tuple[RunResult, ...]:
    """Run replicates in parallel worker-process batches of `max_workers`.

    This is `fim`'s own `max_workers` path — see that function's
    docstring for why real, separate operating-system processes are used
    at all here rather than something lighter-weight. This function's
    own job is just the bookkeeping around that: hand out replicates
    `max_workers` at a time to a pool of worker processes
    (`ProcessPoolExecutor`, from Python's own standard library), collect
    each one's result as it finishes, and — if an adaptive replicate
    stop is configured (`monitor`; see this module's own docstring,
    above) — decide after each whole batch of workers finishes whether
    enough replicates have now run to stop early.

    Replicates run in these fixed-size batches, rather than all being
    submitted to the worker pool at once, specifically so that adaptive
    stopping decision stays meaningful: the decision is always applied
    strictly in ascending replicate order (replicate 1's result seen
    before replicate 2's, and so on), the exact same order the
    sequential (non-parallel) loop already uses, so parallelizing the
    *computation* never reorders which replicate's own values actually
    fed the stopping decision. The one real trade-off for that
    guarantee: a batch can overshoot the exact minimal replicate count
    by up to ``max_workers - 1`` extra replicates, since the whole batch
    a stopping replicate happened to fall in has already been started by
    the time the decision is made.

    That overshoot's own compute is unavoidable — every future in the
    current batch was submitted to a pool sized exactly `max_workers`,
    so by the time a stop is decided, the rest have almost always
    already started running, and `Future.cancel()` cannot interrupt a
    worker process once it has (this project's own multi-model engine
    review, 2026-09-04, `FIM-50`/finding P2-1 case 2, confirms the
    compute cost is already documented and accepted). What is not
    accepted: `store_factory`, when given, makes each overshoot
    replicate's own worker write a real, persistent trajectory — those
    artifacts used to be left behind with no `RunResult` in this
    function's own return value to account for them, discoverable only
    by a reader of the store noticing a `run_id` that does not belong to
    any of the results actually returned. Now: `cancel()` is still tried
    first (a genuine, if rare, win for a future that has not started
    yet), then each overshoot future is waited on and, if `store_
    factory` is real, its own artifacts are discarded — never left
    orphaned regardless of whether the worker itself succeeded, raised,
    or was actually cancelled in time.
    """
    results: list[RunResult] = []
    replicate_index = 0
    planner = batch_window_planner(params)
    with ProcessPoolExecutor(
        max_workers=max_workers,
        mp_context=multiprocessing.get_context("spawn"),
    ) as executor:
        while replicate_index < params.n_replicates:
            batch_end = min(replicate_index + max_workers, params.n_replicates)
            # A replicate whose window is not known yet cannot start: the first
            # wave runs alone, then everything after it is matched to it.
            while (
                planner is not None
                and batch_end - 1 > replicate_index
                and planner.window_for(batch_end - 1) is None
            ):
                batch_end -= 1
            logger.debug(
                "submitting worker batch: replicates %d..%d of %d",
                replicate_index + 1,
                batch_end,
                params.n_replicates,
            )
            futures = {}
            batch_run_ids = {}
            for index in range(replicate_index, batch_end):
                replicate_params = params_for_replicate(
                    params, index, planner.window_for(index) if planner else None
                )
                replicate_run_id = derive_replicate_run_id(params, index, run_id)
                batch_run_ids[index] = replicate_run_id
                futures[index] = executor.submit(
                    _run_replicate_worker,
                    replicate_params,
                    replicate_run_id,
                    store_factory,
                    clock,
                )
            for index in range(replicate_index, batch_end):
                result = futures[index].result()
                results.append(result)
                if planner is not None:
                    planner.record(
                        index,
                        window_noise(result.report, params.convergence_statistics),
                    )
                if monitor is not None:
                    outcome = monitor.record(
                        index + 1,
                        replicate_stopping_values(
                            result, params.convergence_statistics
                        ),
                    )
                    if outcome.stopped:
                        logger.info(
                            "adaptive replicate stop triggered after %d of %d "
                            "replicate(s) (worker batch overshoot up to %d)",
                            index + 1,
                            params.n_replicates,
                            max_workers - 1,
                        )
                        _discard_overshoot_replicates(
                            futures,
                            batch_run_ids,
                            range(index + 1, batch_end),
                            store_factory,
                        )
                        return tuple(results)
            replicate_index = batch_end
    return tuple(results)


def _discard_overshoot_replicates(
    futures: Mapping[int, Future[RunResult]],
    run_ids: Mapping[int, str],
    overshoot_indices: range,
    store_factory: Callable[[str], TrajectoryStore] | None,
) -> None:
    """Cancel where possible; wait for and discard the rest, artifacts included.

    Split out of `run_batch_parallel` so that function's own adaptive-
    stop branch reads as one clear sequence of steps, not one more
    layer of nested loops (`FIM-50`; see that function's own docstring
    for the full reasoning this exists for).

    Args:
        futures: Every future submitted for the current batch, keyed by
            replicate index — including the ones already collected;
            only `overshoot_indices` are actually touched.
        run_ids: Each `overshoot_indices` entry's own `replicate_run_id`,
            computed once at submission time (never recomputed here, to
            rule out any risk of it drifting from what the worker was
            actually given).
        overshoot_indices: The batch indices strictly after the one that
            triggered the adaptive stop — never empty when this is worth
            calling, but a `range` of length zero is a safe no-op either
            way.
        store_factory: `None` means no real, persistent store exists for
            any replicate in this batch at all (every `RunResult` only
            carries its own in-memory store, already covered by simply
            never keeping a reference to the discarded `RunResult`) —
            this function does nothing in that case beyond waiting on
            the futures.
    """
    for index in overshoot_indices:
        future = futures[index]
        if future.cancel():
            # Successfully cancelled before the worker process ever
            # started — nothing ran, so nothing was ever written for
            # this replicate to discard.
            continue
        try:
            future.result()
        except BaseException:
            # An overshoot worker's own failure is not this function's
            # to raise; it was never
            # going into `results` either way, and its own store
            # artifacts (if any were written before it failed) still
            # need discarding below.
            logger.warning(
                "overshoot replicate %s raised after an adaptive stop; "
                "discarding its own store artifacts, if any",
                run_ids[index],
                exc_info=True,
            )
        if store_factory is not None:
            overshoot_store = store_factory(run_ids[index])
            overshoot_store.discard(run_ids[index])
            # An equilibrium-split replicate's ancestral phase too; a
            # no-op for any other run, which never wrote one.
            equilibrium_store_for(overshoot_store, run_ids[index]).discard(
                run_ids[index]
            )


def _run_replicate_worker(
    params: SimulationParams,
    run_id: str,
    store_factory: Callable[[str], TrajectoryStore] | None,
    clock: Clock,
) -> RunResult:
    """Run one replicate inside a worker process.

    This is the actual function each `ProcessPoolExecutor` worker
    process runs, submitted once per replicate by `run_batch_parallel`
    above. It has to be defined here, at module level, rather than as a
    closure inside `run_batch_parallel` itself, for the same "must be
    picklable to cross a process boundary" reason explained in
    `require_picklable`'s own docstring — `ProcessPoolExecutor` sends
    this very function, by reference, to each worker process it starts,
    and only a plain, named, module-level function can be sent that way.

    The pool explicitly uses multiprocessing's `"spawn"` context so a
    batch cannot fork a process that already owns test-runner or GUI
    threads. A spawned worker starts with a freshly imported, unconfigured
    `fim` logger, so this line normally goes nowhere (`fim.__init__`'s own
    `NullHandler`) until something reconfigures logging inside that worker.
    """
    logger.debug("worker process starting replicate %s", run_id)
    store = (
        store_factory(run_id)
        if store_factory is not None
        else InMemoryTrajectoryStore()
    )
    return run_one(params, store, run_id, clock)


def run_one(
    params: SimulationParams,
    store: TrajectoryStore,
    run_id: str,
    clock: Clock,
) -> RunResult:
    """Execute one scalar replicate, then release the store's open files.

    Runs `_simulate_one` and closes `store` (and its ancestral-phase
    companion) afterwards, whether the run finished or raised — a
    cancelled GUI run, for one, must not leave a file open inside a
    directory `fim.paths.atomic_directory` is about to remove, which
    Windows refuses. Closing never loses data and never makes the store
    unusable: the returned `RunResult.store` still reads, and a later
    write re-opens its file.
    """
    try:
        return _simulate_one(params, store, run_id, clock)
    finally:
        close_store(store)


def _simulate_one(
    params: SimulationParams,
    store: TrajectoryStore,
    run_id: str,
    clock: Clock,
) -> RunResult:
    """Execute one scalar replicate.

    This is the actual generation-by-generation simulation loop this
    whole module exists to run — everything above this function in the
    file (`fim`, the batch-parallel machinery) exists only to call this,
    once per replicate. In outline, it:

    1. Builds a starting population (generation zero) from `params`
       (`generate_initial_state`) and a fresh, independent random-number
       stream seeded from `params.seed` — the single source of all this
       replicate's own randomness, so the same seed always reproduces
       the exact same sequence of migration/mutation/drift outcomes.
    2. Sets up the bookkeeping every subsequent generation will need:
       an `AlleleRegistry` to hand out a fresh, never-before-used
       identity to each new mutation under the default "infinite
       alleles" model (see `build_finite_allele_spaces`'s own docstring
       for the alternative "finite alleles" model), and a
       `ConvergenceMonitor` to watch whichever statistic(s)
       `params` says to watch for the run settling down (see this
       module's own docstring, above, for what "convergence" means
       here).
    3. Repeats, once per generation: advance the population by one
       generation (`fim.model.operators.step` — migration, then
       drift, then mutation), persist that generation's own state to
       `store` (so it can be replayed, plotted, or animated later), and
       feed that generation's statistics to the convergence monitor —
       until the monitor says to stop, either because the watched
       statistic(s) have settled down or because `params.
       max_generations` (the hard cap) has been reached.
    4. Summarizes the final generation into a `FinalReport`
       (`report_for_state`) and packages everything — final state,
       report, the full per-generation history of the watched
       statistic(s), and a `RunManifest` recording when this run
       started and ended — into the `RunResult` this function returns.

    Called directly by `simulate_params()` for the ordinary, sequential case, and
    (wrapped by `_run_replicate_worker`) once per replicate inside each
    `ProcessPoolExecutor` worker process for the parallel-batch case —
    this function itself has no idea which of the two is calling it, and
    does not need to.
    """
    logger.info(
        "replicate %s starting (N=%s, d=%s, m=%s, mu=%s, seed=%s, max_generations=%s)",
        run_id,
        params.gene_copies,
        params.d,
        params.m,
        params.mu,
        params.seed,
        params.max_generations,
    )
    started_at = format_timestamp(clock())
    # `PCG64` is the specific, high-quality pseudo-random-number
    # algorithm NumPy recommends as its modern default; seeding it here,
    # once, from `params.seed`, is what makes every downstream random
    # choice in this replicate (the initial population, every
    # generation's own migration/mutation/drift outcomes) fully
    # reproducible from that one seed alone.
    rng = np.random.Generator(np.random.PCG64(params.seed))
    monitor = burn_in_monitor(params)

    # Generation zero: the starting population before any migration,
    # mutation, or drift has happened. Every allele already present here
    # was assigned an id by `generate_initial_state`, independently of
    # the `AlleleRegistry` below (which only hands out ids for *new*
    # mutations from generation one onward) — so the registry's own
    # numbering has to start strictly after the highest id already in
    # use, or a freshly minted mutation could collide with (and be
    # mistaken for) an allele that was already present at the start.
    state, equilibration_outcome, equilibrium_store = (
        generate_initial_state_with_outcome(params, rng, store, run_id)
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
    # `finite_alleles` stays `None` under the default "infinite alleles"
    # mutation model (every new mutation gets a brand-new id from
    # `registry` above, with no fixed ceiling on how many distinct
    # alleles can ever exist) — `fim.model.operators.step` reads this
    # `None` as "no finite-alleles bookkeeping in effect" and falls back
    # to that default behavior on its own.
    finite_alleles = (
        FiniteAlleleRegistry(build_finite_allele_spaces(state, params))
        if params.mutation_model == "finite_alleles"
        else None
    )
    # Generation zero is itself persisted and fed to the convergence
    # monitor before the loop below ever runs a single generation, so a
    # run that (unusually) already satisfies its own convergence
    # criterion at the very start still gets a correctly recorded
    # generation-zero observation, and a later replay of the persisted
    # trajectory always has a real starting frame to show, not a gap.
    begin_store_run(store, run_id, frame_layout(params))
    persist_state(store, run_id, state)
    monitor.record(
        state.generation,
        convergence_values(state, params),
    )
    # The main loop: advance one generation, persist it, check whether
    # the population's statistics have settled down or the hard
    # generation cap has been reached — `monitor.should_stop()` is the
    # single place either of those two conditions is actually decided.
    # `isEnabledFor` guards the actual log call, not just its formatting:
    # `convergence_values` below is computed unconditionally either way
    # (the monitor needs it regardless of logging), but skipping the
    # `logger.debug` call itself when DEBUG is disabled avoids the small,
    # otherwise-per-generation cost of formatting a message nobody reads
    # — see `doc/fim-logging-design.md` §9 for why this loop, specifically
    # (up to `max_generations`, by default 10000), is where that discipline
    # matters most in this whole codebase.
    debug_enabled = logger.isEnabledFor(logging.DEBUG)
    retention = TrajectoryRetention.from_params(params)
    while not monitor.should_stop():
        state = step(state, params, registry, rng, finite_alleles=finite_alleles)
        values = convergence_values(state, params)
        monitor.record(state.generation, values)
        # The stop generation is only known after `record`, so it is the one
        # generation thinning cannot decide in advance; it is always written.
        if retention.keeps(state.generation) or monitor.should_stop():
            persist_state(store, run_id, state)
        if debug_enabled:
            logger.debug(
                "replicate %s generation %d: %s", run_id, state.generation, values
            )

    outcome = monitor.outcome()
    if outcome.reason is None:
        # Unreachable in practice: `monitor.should_stop()` returning
        # `True` (the only way to exit the `while` loop above) is
        # `ConvergenceMonitor`'s own guarantee that a stop reason has
        # already been recorded. Guarded explicitly anyway, rather than
        # assumed, so a future bug in that guarantee surfaces here, at
        # the one place a missing reason would otherwise silently
        # propagate into a malformed `FinalReport`, instead of failing
        # confusingly somewhere far downstream.
        raise RuntimeError("stopped convergence monitor has no reason")
    report = report_for_state(
        state,
        params,
        run_id=run_id,
        converged=outcome.converged,
        reason=outcome.reason.value,
        window_statistics=window_statistics_payload(monitor),
        converged_statistics=monitor.stable_statistics(),
    )
    ended_at = format_timestamp(clock())
    logger.info(
        "replicate %s finished: %s at generation %d (converged=%s)",
        run_id,
        outcome.reason.value,
        state.generation,
        outcome.converged,
    )
    # The manifest is this run's own permanent record — not the
    # scientific results themselves (that is `report`, above), but the
    # surrounding bookkeeping proving *when* and *how* those results
    # were produced: which exact software version ran, when it started
    # and finished. Built here with `artifacts` left at its own default
    # of `None` (see `RunManifest`'s own docstring) — this function
    # never writes anything to disk itself, so it has no on-disk file
    # digests to report yet; a caller that does persist this run's own
    # files (`fim.persistence.artifacts.write_run_artifacts`) fills those in afterward,
    # once every file is completely written, so a later reader can
    # confirm none of them were altered since.
    manifest = RunManifest(
        schema_version=CURRENT_SCHEMA_VERSION,
        run_id=run_id,
        parameters=params.to_dict(),
        started_at=started_at,
        ended_at=ended_at,
        converged=outcome.converged,
        convergence_statistic=params.convergence_statistic,
        stop_reason=outcome.reason.value,
        generation=state.generation,
        generation_count=len(monitor.generations),
        software_version=__version__,
        initial_condition_mode=initial_condition_mode(params),
        equilibrium_generation_count=(
            equilibration_outcome.generation_count
            if equilibration_outcome is not None
            else None
        ),
        equilibrium_final_heterozygosity=(
            equilibration_outcome.final_heterozygosity
            if equilibration_outcome is not None
            else None
        ),
        auto_derived=tuple(sorted(params.auto_derived)),
        relaxation_time=params.relaxation_time,
    )
    return RunResult(
        run_id=run_id,
        params=params,
        final_state=state,
        report=report,
        convergence_generations=monitor.generations,
        convergence_history=monitor.history,
        convergence_histories=monitor.histories,
        manifest=manifest,
        store=store,
        equilibrium_store=equilibrium_store,
    )
