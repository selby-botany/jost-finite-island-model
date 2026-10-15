"""The `fim()` entry point."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace

from fim.engine.backends import build_engine_backend, resolve_auto_engine_backend
from fim.engine.records import (
    Clock,
    EngineBackendChoice,
    JitOption,
    SimulationOutput,
    utc_now,
)
from fim.model.params import Migration, MutationRate, PopulationSize, SimulationParams
from fim.persistence.store import TrajectoryStore

logger = logging.getLogger(__name__)


def fim(
    gene_copies: PopulationSize,
    m: Migration,
    mu: MutationRate,
    d: int,
    *,
    params: SimulationParams,
    store: TrajectoryStore | None = None,
    run_id: str | None = None,
    clock: Clock | None = None,
    max_workers: int | None = None,
    store_factory: Callable[[str], TrajectoryStore] | None = None,
    engine_backend: EngineBackendChoice | None = None,
    jit: JitOption | None = None,
    auto_vector_min_d: int | None = None,
    auto_vector_max_capacity: int | None = None,
) -> SimulationOutput:
    """Run the finite island model until convergence or the hard cap.

    This is the one function everything else in the `fim` package
    (the command line, the desktop app, every test) ultimately calls to
    actually run a simulation. Given a validated configuration
    (`params`), it simulates one population's generations one at a time
    — migration, then drift, then mutation, each generation, following
    `fim.model.operators.step` — until either its watched statistic(s)
    settle down (convergence) or the hard generation cap is reached,
    then returns everything about the finished run. If
    `SimulationParams.n_replicates` is more than one, it does that whole
    thing repeatedly, once per independently seeded replicate, either
    running every requested replicate or (with `SimulationParams.
    stop_batch_early` set) stopping early once enough replicates have
    run to pin down the answer confidently — see this module's own
    docstring, above, for what "convergence" and "replicate" mean here
    and why both kinds of stopping exist. Everything below this point is
    about the mechanical details of calling this function — which
    arguments exist and why, not what the simulation itself does.

    `N`, `m`, `mu`, and `d` (the four arguments almost every configuration
    is described by — the same shorthand a genetics paper would use) are
    each also already present, in identical form, inside `params`
    itself; both must be given, and must agree, because in practice most
    callers already have a `SimulationParams` object in hand (built once,
    validated once, and passed around everywhere) but a signature built
    entirely out of `params.whatever` is much harder to read at a glance
    than one that names the handful of values that actually matter
    scientifically. `_validate_public_signature` checks the two never
    silently disagree.

    Args:
        gene_copies: Gene-copy count, repeated from ``params`` for the public
            signature.
        m: Migration rate or matrix, repeated from ``params``.
        mu: Mutation probability, repeated from ``params``.
        d: Deme count, repeated from ``params``.
        params: Full validated run configuration and open parameter bag —
            everything about how to run the simulation that is not
            already covered by `gene_copies`/`m`/`mu`/`d` above (how many
            generations to allow, when to consider it converged, how
            many replicates to run, and so on). See `fim.model.params.
            SimulationParams`.
        store: Where to write each generation's own simulated state as
            the run proceeds — a `TrajectoryStore` (see `fim.
            persistence.store`); the default writes to memory only
            (nothing touches disk unless a caller supplies a real,
            file-backed store). One store is shared across every
            replicate in a *sequential* batch (each replicate's own rows
            distinguished by its own `run_id`); it must be left `None`
            whenever `max_workers` is set below, since two separate OS
            processes cannot safely write through the very same Python
            object — use `store_factory` instead for that case. Mutually
            exclusive with `store_factory`.
        run_id: This run's own identifier, if the caller wants a
            specific one; left unset, one is computed automatically from
            the run's own parameters (see `deterministic_run_id`) so the
            same configuration always gets the same id.
        clock: Where "now" comes from, for the timestamps recorded in a
            run's own manifest — almost never supplied by a normal
            caller (the real wall clock is the default); tests supply a
            fake one to get a fixed, reproducible timestamp instead of
            "whenever the test happened to run." Under `max_workers`
            (below), this value has to be sent across a process
            boundary to reach each worker, which in Python means it must
            be "picklable" — capable of being serialized to bytes and
            reconstructed on the other side. An ordinary named function
            defined at module level is picklable; a closure or a
            `lambda` is not (Python's own pickling mechanism cannot
            reconstruct one), so only the former is accepted here when
            `max_workers` is set.
        max_workers: Opt-in replicate-batch parallelism, for running
            several replicates' worth of generations at the same time
            instead of one after another. ``None`` (the default) runs
            every replicate strictly one at a time, exactly as every
            prior release of this project did. A number here instead
            runs replicates in batches of up to that many at once, using
            real, separate operating-system processes rather than
            threads — a deliberate choice, since each generation's own
            simulated state is ordinary Python objects (nested
            dictionaries of allele frequencies), and CPython (the
            standard Python interpreter) only ever lets one thread run
            Python code at a time regardless of how many exist (a
            well-known limitation called the Global Interpreter Lock, or
            GIL) — separate *processes*, each with its own interpreter,
            are the only way to get real, simultaneous computation for
            work shaped like this. An adaptive `stop_batch_early`
            stop (see this module's own docstring, above) is still
            checked strictly in ascending replicate order after each
            whole batch completes, so a batch can overshoot the exact
            minimal replicate count by at most ``max_workers - 1``
            replicates — a small, bounded, and deliberate trade-off for
            the parallelism, not an approximation of the stopping rule
            itself.
        store_factory: A function that builds one fresh trajectory store
            per replicate, given that replicate's own `run_id` — used
            instead of one shared `store` whenever every replicate needs
            its own independent place to write (any batch of more than
            one replicate can use this; it is required, not just
            useful, once `max_workers` is set, since a worker process
            cannot share the parent process's own `store` object at
            all). Whenever `max_workers` is set, this function must
            itself be picklable in the same sense `clock` above is — an
            ordinary module-level function, or `functools.partial` built
            from one, never a closure or a `lambda`. Left unset
            (``None``, the default), every replicate falls back to
            sharing whatever `store` was given (or a private, in-memory
            store if `store` was not given either). `lineal`-only —
            see `engine_backend` below.
        engine_backend: Which engine implementation actually runs this
            batch. Left unset (``None``, the default), falls back to
            `params.engine_backend` — a real `SimulationParams` field
            (config-file/CLI exposed, see `doc/configuration.md`), so a
            caller who only builds `params` and never touches this
            argument still gets whatever `params` itself asked for.
            Passing this argument explicitly overrides `params` for this
            one call, the same relationship `gene_copies`/`m`/`mu`/`d` have to
            `params`, without the "must agree" requirement those four
            enforce — this one is a pure override, not a redundant
            cross-check. ``"lineal"`` (both `params`'s own default and
            this project's original, single-engine behavior) means every
            caller that sets neither sees no change at all.
            ``"generational"`` reorders *when* each replicate's
            generations are computed (every still-active replicate's own
            generation advances together, fanned out across a real
            thread pool, rather than one replicate's whole trajectory
            finishing before the next starts) without changing what is
            computed: for the same seed, its own trajectory is
            bit-identical to ``"lineal"``'s, regardless of thread
            interleaving. That includes an adaptive batch
            (`stop_batch_early` set): every backend judges the
            adaptive stop on replicates in replicate order, admitting
            replicate *i* only once replicates 1 to *i* have all
            finished (`run_batch`'s own docstring), so ``"generational"``
            keeps the same replicates as ``"lineal"`` and reports the
            same summary, never the subset that happened to converge
            first. ``max_workers``/``store_factory`` above are
            meaningful only for ``"lineal"``; passing either alongside a
            different `engine_backend` is a `ValueError`, not a silent
            no-op — ``"generational"``'s own thread count is a separate,
            not-yet-publicly-reachable knob (see `ThreadedAdvancer`'s own
            docstring).
            ``"generational-vector"`` is a real, working third choice —
            array-native, both mutation models and both migrant sampling
            modes.
            It runs `fim.model.vector_block.VectorBlock`, one compiled
            kernel call per generation over every locus in
            `operators.step`'s own stage and draw order, and is
            **identical** to ``"lineal"`` and ``"generational"`` for the
            same seed on the same platform: every trajectory row (allele
            identity and frequency bit), the report, the final state and
            the manifest apart from `engine_backend`. "Same platform"
            carries the caveat `jit="numba"` already has: the compiled
            `lgamma`/`log`/`log1p`/`exp` are verified to match CPython
            only on the development platform, so across machines results
            agree statistically, not bit for bit. A memory ceiling
            (`FIM_VECTOR_MEMORY_CEILING_BYTES`, 2 GiB per replicate by
            default) fails a table too large for the machine with a
            message naming the remedies.
            ``"auto"`` picks between ``"generational"`` and
            ``"generational-vector"`` on `params`'s own behalf, using
            `auto_vector_min_d` (below) — never ``"lineal"`` (see
            `build_engine_backend`'s own docstring for why only this one
            axis is automated so far).
        jit: Whether the chosen backend should JIT-compile its own
            operators — a change in *how* the result is computed, not
            *what* is computed (bit-identical to ``"off"`` for the same
            seed). Left unset (``None``, the default), falls back to
            `params.jit`, the same relationship `engine_backend` above
            has to `params.engine_backend`. Meaningful only under
            ``engine_backend="generational"`` today (JIT-compiles
            `drift`'s own multinomial draw — needs the optional `numba`
            dependency, ``pip install fim[jit]``); ``"generational-
            vector"`` has no separate toggle for this — its own mutate
            step always requires `numba` internally regardless of this
            argument, so only ``"off"`` (the default) is accepted there,
            and passing ``"numba"`` is a `ValueError`; ``"lineal"`` never
            accepts anything but ``"off"``, permanently — see
            `build_engine_backend`'s own docstring.
        auto_vector_min_d: The deme-count cutover ``engine_backend=
            "auto"`` uses to decide between ``"generational"`` and
            ``"generational-vector"``; ignored under every other
            `engine_backend` value. Left unset (``None``, the default),
            falls back to `params.auto_vector_min_d` — see
            `DEFAULT_AUTO_VECTOR_MIN_D`'s own docstring
            (`fim.model.params`) for the full benchmark finding behind
            that field's own default and its known cross-environment
            caveats before trusting it on meaningfully different
            hardware.
        auto_vector_max_capacity: The per-locus capacity ceiling
            ``engine_backend="auto"`` uses alongside `auto_vector_min_d`
            under ``mutation_model="finite_alleles"`` — ignored under
            infinite alleles and under every other `engine_backend`
            value. Left
            unset (``None``, the default), falls back to `params.
            auto_vector_max_capacity` — see `DEFAULT_AUTO_VECTOR_MAX_
            CAPACITY`'s own docstring (`fim.model.params`) for the
            measured finding behind that field's own default.

    Returns:
        One result, or one independently seeded result per replicate.
        With `SimulationParams.stop_batch_early` off, exactly
        `n_replicates` replicates run. With it on (the default),
        replicates stop accumulating as soon as every watched
        statistic's across-replicate confidence interval tightens to at
        most `precision` (see
        `replicate_summary`), or `n_replicates` is reached, whichever
        comes first — so the returned tuple can be shorter than
        `n_replicates`.

    Raises:
        ValueError: If the named arguments disagree with ``params``,
            `store` and `store_factory` are both given, `max_workers` is
            combined with a non-``None`` `store`, `max_workers` is given
            alongside a non-``"lineal"`` `engine_backend` (there is no
            process pool anywhere else for it to size — see
            `build_engine_backend`'s own `max_workers` Args entry), or
            `jit` is anything but ``"off"`` under `engine_backend="lineal"`
            (or under `"generational-vector"`, including when `"auto"`
            resolves to it — see `build_engine_backend`'s own docstring).
    """
    _validate_public_signature(gene_copies, m, mu, d, params)
    # An explicit argument always wins; otherwise fall back to `params`'s
    # own field of the same name — real `SimulationParams` fields (see
    # `doc/configuration.md`), not orphaned duplicates of these
    # arguments' own former hardcoded defaults. `params` itself already
    # validated all three together (`SimulationParams.__post_init__`
    # via `_validate_engine_backend`), so nothing further to check here
    # beyond what `build_engine_backend` already does for a directly
    # constructed value.
    if engine_backend is None:
        engine_backend = params.engine_backend
    if jit is None:
        jit = params.jit
    if auto_vector_min_d is None:
        auto_vector_min_d = params.auto_vector_min_d
    if auto_vector_max_capacity is None:
        auto_vector_max_capacity = params.auto_vector_max_capacity
    # `store_factory` alone needs no rejection here any more
    # (`20260914-claude-sonnet-5-non-lineal-batch-execution-design.md`,
    # `selby/restricted`, §5.3): every backend now gives each replicate
    # its own store when one is supplied (`GenerationalBackend` via
    # `ReplicateFanoutStore`, `LinealBackend` directly). `max_workers`
    # stays lineal-only — it sizes a `ProcessPoolExecutor` that only
    # `LinealBackend` ever builds; no other backend has a process pool
    # for this argument to mean anything about.
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
    # `fim()` itself only validates its own public signature and picks a
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


def _validate_public_signature(
    gene_copies: PopulationSize,
    m: Migration,
    mu: MutationRate,
    d: int,
    params: SimulationParams,
) -> None:
    """Reject disagreement between named arguments and the parameter bag.

    `fim()` accepts `gene_copies`/`m`/`mu`/`d` both as their own named arguments
    and, redundantly, already inside `params` (see `fim`'s own docstring
    for why) — this function is what actually enforces that the two
    copies agree, rather than silently trusting whichever one a caller
    happened to update if they ever changed one without the other.
    """
    mismatches: list[str] = []
    if gene_copies != params.gene_copies:
        mismatches.append("gene_copies")
    if m != params.m:
        mismatches.append("m")
    if mu != params.mu:
        mismatches.append("mu")
    if d != params.d:
        mismatches.append("d")
    if mismatches:
        raise ValueError(
            "named fim arguments disagree with params: " + ", ".join(mismatches)
        )
