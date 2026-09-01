"""Pure migration, mutation, drift, and generation-pipeline operators.

This module implements the three biological processes every generation
of the simulation actually goes through, plus `step`, which chains all
three together in the standard order:

- `migrate` — a fraction of each deme's gene copies are replaced by a
  weighted average of every other deme's own allele frequencies (the
  "migrant pool"), modeling individuals moving between sub-populations.
- `mutate` — a small, randomly chosen number of gene copies switch to
  a different, new-or-existing allele, modeling a real mutation event.
- `drift` — the full population of `N` gene copies is re-sampled from
  the current frequencies, the same way flipping a weighted coin `N`
  times only approximately reproduces the coin's own true weighting;
  this is what makes a finite population's allele frequencies wander
  randomly from one generation to the next, purely from chance, even
  with no migration or mutation happening at all.

Every function here is "pure" in the sense that none of them mutate
their `ModelState` argument in place — each one returns a brand-new
state representing the *result* of applying that one process, leaving
the state it was given untouched (see `fim.model.state.ModelState`'s
own docstring for why that immutability matters). `step`, at the
bottom of this file, is what `fim.engine`'s run loop actually calls
once per generation: it runs migration, then mutation, then drift, in
that fixed order, which is the standard order these three processes
are applied in a Wright-Fisher-style simulation.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence

import numpy as np

from fim.model.allele import AlleleId, AlleleRegistry, FiniteAlleleRegistry
from fim.model.params import (
    Migration,
    MutationRate,
    PopulationSize,
    SimulationParams,
)
from fim.model.state import FrequencyMap, ModelState

_JIT_MULTINOMIAL_VIA_BINOMIAL: (
    Callable[[np.random.Generator, int, np.ndarray], np.ndarray] | None
) = None


def _multinomial_via_binomial(
    rng: np.random.Generator, n: int, probabilities: np.ndarray
) -> np.ndarray:
    """Draw one multinomial sample via sequential conditional-binomial draws.

    The standard identity: a multinomial draw of size `n` over
    categories `p_1 .. p_k` decomposes into `count_1 ~ Binomial(n,
    p_1)`, then `count_2 ~ Binomial(n - count_1, p_2 / (1 - p_1))`, and
    so on, the final category absorbing whatever remains. This is not
    merely *statistically equivalent* to `numpy.random.Generator.
    multinomial` — confirmed directly, across several thousand
    randomized seed/parameter combinations (`test/model/
    test_operators.py`), it reproduces `Generator.multinomial`'s own
    output bit-for-bit, because NumPy's own internal implementation
    already uses this identical decomposition. That bit-identity is
    exactly what makes `_jit_multinomial_via_binomial`, below, a safe
    drop-in replacement for `rng.multinomial(n, probabilities)`: the
    only reason this function exists at all is that `Generator.
    multinomial` itself is something Numba's `@jit` cannot compile,
    where `Generator.binomial` with scalar arguments is — not because
    the sampling algorithm itself needed to change.

    Args:
        rng: The run's explicitly threaded random generator.
        n: Total count to distribute across categories.
        probabilities: Row-stochastic (summing to 1) category weights.

    Returns:
        Integer counts, one per category, summing to `n`.
    """
    category_count = probabilities.shape[0]
    counts = np.empty(category_count, dtype=np.int64)
    remaining_n = n
    remaining_p = 1.0
    for index in range(category_count - 1):
        target_p = probabilities[index] / remaining_p if remaining_p > 0.0 else 0.0
        if target_p < 0.0:
            target_p = 0.0
        elif target_p > 1.0:
            target_p = 1.0
        drawn = rng.binomial(remaining_n, target_p)
        counts[index] = drawn
        remaining_n -= drawn
        remaining_p -= probabilities[index]
    counts[category_count - 1] = remaining_n
    return counts


def _jit_multinomial_via_binomial(
    rng: np.random.Generator, n: int, probabilities: np.ndarray
) -> np.ndarray:
    """`_multinomial_via_binomial`, JIT-compiled with `nogil=True`.

    `numba` is an optional dependency (``pip install fim[jit]``),
    imported here and nowhere else in this module — importing `fim.
    model.operators` never requires it, and only a caller that
    explicitly passes `jit=True` (`drift`, below, threaded down from
    `fim()`'s own `jit="numba"` via `step`) ever pays its import/
    compilation cost or needs it installed at all. Compiled once, on
    first call, and cached at module level — every call after the first
    reuses the already-compiled function rather than recompiling.

    Raises:
        ImportError: If `numba` is not installed. A caller should not
            reach this function at all unless something upstream already
            decided `jit=True` was wanted — `fim.engine.
            build_engine_backend` is the one place that decision is
            actually made.
    """
    global _JIT_MULTINOMIAL_VIA_BINOMIAL  # noqa: PLW0603
    if _JIT_MULTINOMIAL_VIA_BINOMIAL is None:
        import numba  # noqa: PLC0415 -- lazy, optional-dependency import

        _JIT_MULTINOMIAL_VIA_BINOMIAL = numba.jit(nogil=True)(_multinomial_via_binomial)
    return _JIT_MULTINOMIAL_VIA_BINOMIAL(rng, n, probabilities)


def drift(
    state: ModelState,
    population_size: PopulationSize,
    rng: np.random.Generator,
    *,
    jit: bool = False,
) -> ModelState:
    """Resample ``N`` gene copies per deme and locus.

    "Genetic drift" is the random change in allele frequencies from one
    generation to the next that happens purely because a real
    population is finite — even with no selection, migration, or
    mutation at all, a fair coin flipped 10 times does not always come
    up exactly 5 heads, and a deme's `N` gene copies are exactly that
    kind of finite, random draw from the previous generation's own
    frequencies. This function is what actually performs that draw: for
    every deme and locus, it treats the current frequency map as the
    probabilities of a `numpy.random.Generator.multinomial` draw of
    size `N` (multinomial being the many-outcomes generalization of the
    familiar two-outcome binomial coin flip), then converts the drawn
    integer counts back into frequencies — which, unlike migration's or
    mutation's smooth, continuous frequency changes, always land
    exactly on the ``1 / N`` grid (a frequency of, say, `3/50`, never
    `3.2/50`), since they came from literally counting whole gene
    copies.

    Args:
        state: Post-migration and post-mutation state.
        population_size: Shared or per-deme gene-copy count.
        rng: The run's explicitly threaded random generator.
        jit: When `True`, draw each deme/locus's own counts via
            `_jit_multinomial_via_binomial` (a Numba-JIT-compiled,
            `nogil=True` conditional-binomial decomposition) instead of
            `rng.multinomial` directly — bit-identical output either
            way (see that function's own docstring for why), but able
            to run with the GIL released, which is what lets
            `fim.engine.ThreadedAdvancer` see real multi-thread
            speedup rather than none at all. `False` (the default) is
            every prior release's own behavior, unchanged, and needs
            `numba` installed only when `True`.

    Returns:
        The next generation with frequencies on a ``1 / N`` grid.
    """
    sizes = _population_sizes(population_size, state.deme_count)
    demes: list[tuple[Mapping[AlleleId, float], ...]] = []
    for deme, size in zip(state.frequencies, sizes, strict=True):
        locus_maps: list[Mapping[AlleleId, float]] = []
        for frequency_map in deme:
            allele_ids = tuple(frequency_map)
            probabilities = np.fromiter(
                frequency_map.values(),
                dtype=np.float64,
                count=len(allele_ids),
            )
            probabilities /= probabilities.sum()
            counts = (
                _jit_multinomial_via_binomial(rng, size, probabilities)
                if jit
                else rng.multinomial(size, probabilities)
            )
            locus_maps.append(
                {
                    allele_id: int(count) / size
                    for allele_id, count in zip(
                        allele_ids,
                        counts,
                        strict=True,
                    )
                    if count
                }
            )
        demes.append(tuple(locus_maps))
    result = ModelState(
        loci=state.loci,
        frequencies=tuple(demes),
        generation=state.generation + 1,
    )
    result.validate_support(sizes)
    return result


def migrate(
    state: ModelState,
    m: Migration,
    population_size: PopulationSize | None = None,
    *,
    rng: np.random.Generator | None = None,
) -> ModelState:
    """Blend each deme with the current all-other-deme migrant pool.

    Every deme keeps a ``1 - rate`` share of its own current
    frequencies and mixes in a ``rate`` share of the "migrant pool" —
    a weighted average of every *other* deme's own frequencies, the
    weighting coming either from a flat symmetric rate (`m` as a plain
    number, applied identically between every pair of demes) or from a
    full custom weight matrix (`m` as a matrix, letting some pairs of
    demes exchange more migrants than others — see `fim.model.topology`
    for building one). This is the process that keeps demes from
    drifting apart in isolation: without any migration at all, each
    deme's own genetic drift (see `drift`, above) is independent, so
    over time they diverge; migration is the counteracting force that
    homogenizes them, and the balance between the two is exactly what
    the differentiation measures in `fim.statistics.differentiation`
    are designed to quantify.

    Args:
        state: Current generation.
        m: Symmetric migration rate or a complete row-stochastic matrix.
        population_size: Optional gene-copy counts used to size-weight pools.
        rng: Optional random generator selecting the opt-in stochastic
            migrant-count model (``SimulationParams.migrant_sampling ==
            "stochastic"``). ``None`` (the default) applies the migration
            rate as an exact fraction, unchanged from every prior release.
            When given, each deme's migrant *count* is instead drawn from
            ``Binomial(size, rate)`` each generation — mean ``size * rate``,
            matching the deterministic case in expectation, but varying
            generation to generation — while migrant *composition* stays
            the existing deterministic, weighted pool average. Requires
            ``population_size``.

    Returns:
        A state at the same generation containing post-migration frequencies.

    Raises:
        ValueError: If ``rng`` is given without ``population_size``.
    """
    if rng is not None and population_size is None:
        raise ValueError("migrate() requires population_size when rng is given")
    matrix_sizes: tuple[int, ...] | None = (
        _population_sizes(population_size, state.deme_count)
        if population_size is not None
        else None
    )
    if isinstance(m, int | float):
        symmetric_sizes = (
            matrix_sizes if matrix_sizes is not None else (1,) * state.deme_count
        )
        demes = _migrate_symmetric(state, float(m), symmetric_sizes, rng=rng)
    else:
        demes = _migrate_matrix(state, m, sizes=matrix_sizes, rng=rng)
    return ModelState(
        loci=state.loci,
        frequencies=demes,
        generation=state.generation,
    )


def mutate(
    state: ModelState,
    mu: MutationRate,
    population_size: PopulationSize,
    registry: AlleleRegistry,
    rng: np.random.Generator,
    *,
    finite_alleles: FiniteAlleleRegistry | None = None,
) -> ModelState:
    """Replace a binomially sampled number of copies with new alleles.

    A "mutation event" is one gene copy switching to a different
    allele than it currently carries — biologically, a copying error
    when a cell divides. This function decides *how many* such events
    happen this generation in each deme/locus (drawn from a Binomial
    distribution, `Binomial(N, mu)` — the standard way of modeling "each
    of `N` independent gene copies has its own small, fixed probability
    `mu` of mutating this generation"), and then decides *which* new
    allele each mutating copy becomes: under the default infinite-
    alleles model, always a fresh, never-before-seen identity (see
    `fim.model.allele.AlleleRegistry`); under the opt-in finite-alleles
    (K-allele) model, possibly a state that already exists elsewhere in
    the run (see `fim.model.allele.FiniteAlleleSpace`).

    Existing allele mass is reduced proportionally, avoiding an extra drift
    sample in the mutation stage.

    Args:
        state: Post-migration state.
        mu: Per-copy mutation probability — shared by every locus, or one
            rate per locus (`SimulationParams.mutation_rates`; typically
            derived from a per-base rate and each locus's own length via
            `SimulationParams.from_mapping`'s `mu_b`).
        population_size: Shared or per-deme gene-copy count.
        registry: Global mutant-allele allocator for the run — used under
            the default infinite-alleles model, where every mutation event
            receives a fresh global identity.
        rng: The run's explicitly threaded random generator.
        finite_alleles: Optional per-locus finite-allele-space registry
            selecting the opt-in finite-alleles (K-allele) model instead
            (`SimulationParams.mutation_model == "finite_alleles"`). A
            mutation event's target then depends on its *source* allele —
            never itself, but possibly a state already present elsewhere
            in the run — so mutating copies are first attributed back to
            the existing allele each one came from, sampled proportionally
            to that allele's current share, exactly like the proportional
            mass reduction below already assumes.

    Returns:
        A post-mutation state at the same generation.
    """
    if isinstance(mu, float) and mu == 0.0:
        return state
    mutation_rates = mu if isinstance(mu, tuple) else (mu,) * state.locus_count
    sizes = _population_sizes(population_size, state.deme_count)
    demes: list[tuple[Mapping[AlleleId, float], ...]] = []
    for deme, size in zip(state.frequencies, sizes, strict=True):
        locus_maps: list[Mapping[AlleleId, float]] = []
        for frequency_map, locus, rate in zip(
            deme, state.loci, mutation_rates, strict=True
        ):
            event_count = int(rng.binomial(size, rate))
            if event_count == 0:
                locus_maps.append(dict(frequency_map))
                continue
            # Reduce every existing allele's mass by the same factor so the
            # continuous post-migration frequencies are preserved rather than
            # rounded onto the 1 / N grid. Rounding here would deterministically
            # erase sub-grid migrant mass and undo migration, biasing the run
            # toward spurious differentiation; drift is the sole operator that
            # realizes N discrete gene copies. Valid under either mutation
            # model: which existing copies mutate doesn't change how much
            # mass leaves the surviving distribution, only where it goes.
            retained_mass = 1.0 - event_count / size
            mutated: dict[AlleleId, float] = {
                allele_id: frequency * retained_mass
                for allele_id, frequency in frequency_map.items()
            }
            event_frequency = 1.0 / size
            if finite_alleles is None:
                for _event in range(event_count):
                    mutated[registry.next_id()] = event_frequency
            else:
                # Attribute the event_count mutating copies back to the
                # existing alleles they actually came from, proportionally
                # to current share — needed here, unlike above, because a
                # K-allele target excludes its own source, so the source's
                # identity is no longer irrelevant to the outcome. A target
                # can coincide with another event's target, or with mass
                # already retained above, so contributions accumulate
                # rather than overwrite.
                allele_ids = tuple(frequency_map)
                probabilities = np.fromiter(
                    frequency_map.values(),
                    dtype=np.float64,
                    count=len(allele_ids),
                )
                probabilities /= probabilities.sum()
                source_counts = rng.multinomial(event_count, probabilities)
                for source_id, source_count in zip(
                    allele_ids, source_counts, strict=True
                ):
                    for _event in range(int(source_count)):
                        target = finite_alleles.mutate_target(
                            locus.locus_id, source_id, rng
                        )
                        mutated[target] = mutated.get(target, 0.0) + event_frequency
            locus_maps.append(_normalize(mutated))
        demes.append(tuple(locus_maps))
    return ModelState(
        loci=state.loci,
        frequencies=tuple(demes),
        generation=state.generation,
    )


def step(
    state: ModelState,
    params: SimulationParams,
    registry: AlleleRegistry,
    rng: np.random.Generator,
    *,
    finite_alleles: FiniteAlleleRegistry | None = None,
    jit: bool = False,
) -> ModelState:
    """Advance one generation in migration, mutation, then drift order.

    This is the one function `fim.engine`'s run loop actually calls,
    once per generation: it chains `migrate`, `mutate`, and `drift`,
    above, in that fixed order — a real population experiences all
    three of these forces continuously and simultaneously, but a
    discrete-generation simulation has to apply them in *some* order
    each tick, and migration-then-mutation-then-drift is the
    conventional choice this project follows.

    Args:
        state: Current model state.
        params: Validated run parameters.
        registry: Global mutant-allele allocator.
        rng: The run's explicitly threaded random generator.
        finite_alleles: Optional per-locus finite-allele-space registry,
            built once per run by the caller and threaded through every
            generation — required when
            ``params.mutation_model == "finite_alleles"``, unused
            otherwise.
        jit: Passed through to `drift`'s own `jit` argument — see its
            docstring. Only `drift`'s own multinomial draw is
            JIT-compiled today; `migrate`'s and `mutate`'s own RNG calls
            are unaffected by this flag, a deliberate, documented scope
            boundary (drift's is the highest-frequency RNG call in this
            module — one per deme per locus, every generation — not an
            oversight of the other two).

    Returns:
        The next generation.
    """
    # Only the opt-in "stochastic" mode passes rng into migrate(); the
    # default "continuous" mode passes None, so migrate() consumes zero
    # rng draws and every existing reproducible run is bit-for-bit
    # unaffected by this feature's existence.
    migration_rng = rng if params.migrant_sampling == "stochastic" else None
    migrated = migrate(state, params.m, params.N, rng=migration_rng)
    mutated = mutate(
        migrated, params.mu, params.N, registry, rng, finite_alleles=finite_alleles
    )
    return drift(mutated, params.N, rng, jit=jit)


def _allele_union(frequency_maps: Sequence[FrequencyMap]) -> tuple[AlleleId, ...]:
    """Return all present IDs in deterministic first-observed order."""
    observed: dict[AlleleId, None] = {}
    for frequency_map in frequency_maps:
        for allele_id in frequency_map:
            observed.setdefault(allele_id, None)
    return tuple(observed)


def _blend(
    local: Mapping[AlleleId, float],
    pool: Mapping[AlleleId, float],
    rate: float,
    size: int,
    rng: np.random.Generator,
) -> Mapping[AlleleId, float]:
    """Blend one deme/locus using a binomially sampled migrant count.

    ``rate`` is applied as ``Binomial(size, rate) / size`` instead of
    exactly, so the migrant *count* varies generation to generation with
    mean ``size * rate`` while the migrant *composition* stays exactly
    ``pool`` — the same deterministic weighted average the continuous path
    always used. Drift, not this step, remains the pipeline's only operator
    that resamples every gene copy; this only randomizes how many of a
    deme's ``size`` gene copies are attributed to the migrant pool this
    generation, versus how many stayed local.

    Args:
        local: This deme's own pre-migration frequency map.
        pool: The deterministic migrant-pool frequency map.
        rate: The scalar or per-row migration rate — the binomial draw's
            success probability.
        size: This deme's gene-copy count, ``N_i``.
        rng: The run's explicitly threaded random generator.

    Returns:
        A normalized post-migration frequency map.
    """
    migrant_count = int(rng.binomial(size, rate))
    migrant_fraction = migrant_count / size
    blended = {
        allele_id: (1.0 - migrant_fraction) * local.get(allele_id, 0.0)
        + migrant_fraction * pool.get(allele_id, 0.0)
        for allele_id in _allele_union((local, pool))
    }
    return _normalize(blended)


def _migrate_matrix(
    state: ModelState,
    matrix: tuple[tuple[float, ...], ...],
    *,
    sizes: tuple[int, ...] | None = None,
    rng: np.random.Generator | None = None,
) -> tuple[tuple[Mapping[AlleleId, float], ...], ...]:
    """Apply a complete source-weight matrix."""
    result: list[tuple[Mapping[AlleleId, float], ...]] = []
    for destination, weights in enumerate(matrix):
        locus_maps: list[Mapping[AlleleId, float]] = []
        for locus_index in range(state.locus_count):
            sources = tuple(
                state.frequency_map(source, locus_index)
                for source in range(state.deme_count)
            )
            if rng is None:
                allele_ids = _allele_union(sources)
                blended = {
                    allele_id: math.fsum(
                        weight * source.get(allele_id, 0.0)
                        for weight, source in zip(weights, sources, strict=True)
                    )
                    for allele_id in allele_ids
                }
                locus_maps.append(_normalize(blended))
                continue
            # Stochastic path: the row's own diagonal entry is this
            # destination's self-retention weight; everything else is the
            # migrant pool, exactly as in the deterministic case above, but
            # the count drawn from that pool this generation is now random
            # rather than exactly ``migrant_weight * size``.
            migrant_weight = 1.0 - weights[destination]
            local = sources[destination]
            if migrant_weight <= 0.0:
                locus_maps.append(dict(local))
                continue
            if sizes is None:
                raise ValueError("migrate() requires population_size when rng is given")
            pool = _row_pool(weights, sources, destination, migrant_weight)
            locus_maps.append(
                _blend(local, pool, migrant_weight, sizes[destination], rng)
            )
        result.append(tuple(locus_maps))
    return tuple(result)


def _migrate_symmetric(
    state: ModelState,
    rate: float,
    sizes: tuple[int, ...],
    *,
    rng: np.random.Generator | None = None,
) -> tuple[tuple[Mapping[AlleleId, float], ...], ...]:
    """Apply scalar all-other-deme migration in ``O(d * A)`` time.

    The all-other-deme migrant pool for a destination equals the global
    size-weighted allele mass with the destination's own contribution removed:
    ``pool(a) = (sum_k size_k f_k(a) - size_dest f_dest(a))
    / (sum_k size_k - size_dest)``. Precomputing the per-locus global mass once
    turns the naive ``O(d^2 * A)`` recomputation (each destination re-summing
    every other deme) into a single pass plus one subtraction per destination,
    which matters at large ``d`` such as the 100-deme Dear-Nolan scenario.

    With ``rng is None`` (the default), ``rate`` is applied to every deme
    as an exact fraction — the original, still-default behavior, computed
    exactly as before. With an ``rng``, each destination's migrant count is
    instead drawn from ``Binomial(size, rate)`` (see ``_blend``); the two
    branches are kept fully separate below so the default path's
    floating-point arithmetic is untouched by the new one.
    """
    if rate == 0.0:
        return tuple(
            tuple(dict(frequency_map) for frequency_map in deme)
            for deme in state.frequencies
        )
    total_size = float(sum(sizes))
    global_mass: list[dict[AlleleId, float]] = []
    for locus_index in range(state.locus_count):
        mass: dict[AlleleId, float] = {}
        for deme_index in range(state.deme_count):
            size = sizes[deme_index]
            frequency_map = state.frequency_map(deme_index, locus_index)
            for allele_id, frequency in frequency_map.items():
                mass[allele_id] = mass.get(allele_id, 0.0) + size * frequency
        global_mass.append(mass)
    result: list[tuple[Mapping[AlleleId, float], ...]] = []
    for destination in range(state.deme_count):
        destination_size = sizes[destination]
        other_weight = total_size - destination_size
        locus_maps: list[Mapping[AlleleId, float]] = []
        for locus_index in range(state.locus_count):
            local = state.frequency_map(destination, locus_index)
            if rng is None:
                blended: dict[AlleleId, float] = {}
                for allele_id, total in global_mass[locus_index].items():
                    local_frequency = local.get(allele_id, 0.0)
                    pool_frequency = (
                        total - destination_size * local_frequency
                    ) / other_weight
                    blended[allele_id] = (
                        1.0 - rate
                    ) * local_frequency + rate * pool_frequency
                locus_maps.append(_normalize(blended))
            else:
                pool = {
                    allele_id: (total - destination_size * local.get(allele_id, 0.0))
                    / other_weight
                    for allele_id, total in global_mass[locus_index].items()
                }
                locus_maps.append(_blend(local, pool, rate, destination_size, rng))
        result.append(tuple(locus_maps))
    return tuple(result)


def _normalize(
    frequency_map: Mapping[AlleleId, float],
) -> dict[AlleleId, float]:
    """Normalize positive floating-point mass to exactly one."""
    positive = {
        allele_id: frequency
        for allele_id, frequency in frequency_map.items()
        if frequency > 0.0
    }
    total = math.fsum(positive.values())
    if total <= 0.0:
        raise ValueError("operator produced no positive allele mass")
    return {allele_id: frequency / total for allele_id, frequency in positive.items()}


def _population_sizes(
    value: PopulationSize,
    deme_count: int,
) -> tuple[int, ...]:
    """Expand and validate a population-size argument."""
    sizes = (value,) * deme_count if isinstance(value, int) else tuple(value)
    if len(sizes) != deme_count:
        raise ValueError("N must contain one gene-copy count per deme")
    if any(isinstance(size, bool) or size < 1 for size in sizes):
        raise ValueError("all N values must be positive integers")
    return sizes


def _row_pool(
    weights: tuple[float, ...],
    sources: tuple[Mapping[AlleleId, float], ...],
    destination: int,
    migrant_weight: float,
) -> Mapping[AlleleId, float]:
    """Return one migration-matrix row's non-self weighted-average pool.

    Args:
        weights: The destination's full source-weight row.
        sources: Every deme's current frequency map for one locus.
        destination: The destination's index within ``weights``/``sources``.
        migrant_weight: ``1 - weights[destination]``, the row's total
            non-self weight, used to renormalize the excluded-self average.

    Returns:
        The migrant pool's frequency map; mass sums to 1.
    """
    others = tuple(
        (weight, source)
        for index, (weight, source) in enumerate(zip(weights, sources, strict=True))
        if index != destination
    )
    allele_ids = _allele_union(tuple(source for _weight, source in others))
    return {
        allele_id: math.fsum(
            weight * source.get(allele_id, 0.0) for weight, source in others
        )
        / migrant_weight
        for allele_id in allele_ids
    }
