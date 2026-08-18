"""Pure migration, mutation, drift, and generation-pipeline operators."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import numpy as np

from fim.model.allele import AlleleId, AlleleRegistry
from fim.model.params import Migration, PopulationSize, SimulationParams
from fim.model.state import FrequencyMap, ModelState


def drift(
    state: ModelState,
    population_size: PopulationSize,
    rng: np.random.Generator,
) -> ModelState:
    """Resample ``N`` gene copies per deme and locus.

    Args:
        state: Post-migration and post-mutation state.
        population_size: Shared or per-deme gene-copy count.
        rng: The run's explicitly threaded random generator.

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
            counts = rng.multinomial(size, probabilities)
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
    mu: float,
    population_size: PopulationSize,
    registry: AlleleRegistry,
    rng: np.random.Generator,
) -> ModelState:
    """Replace a binomially sampled number of copies with novel alleles.

    Existing allele mass is reduced proportionally, avoiding an extra drift
    sample in the mutation stage. Every mutation event receives a fresh global
    identity and contributes exactly ``1 / N`` frequency.

    Args:
        state: Post-migration state.
        mu: Per-copy mutation probability.
        population_size: Shared or per-deme gene-copy count.
        registry: Global mutant-allele allocator for the run.
        rng: The run's explicitly threaded random generator.

    Returns:
        A post-mutation state at the same generation.
    """
    if mu == 0.0:
        return state
    sizes = _population_sizes(population_size, state.deme_count)
    demes: list[tuple[Mapping[AlleleId, float], ...]] = []
    for deme, size in zip(state.frequencies, sizes, strict=True):
        locus_maps: list[Mapping[AlleleId, float]] = []
        for frequency_map in deme:
            event_count = int(rng.binomial(size, mu))
            if event_count == 0:
                locus_maps.append(dict(frequency_map))
                continue
            # Reduce every existing allele's mass by the same factor so the
            # continuous post-migration frequencies are preserved rather than
            # rounded onto the 1 / N grid. Rounding here would deterministically
            # erase sub-grid migrant mass and undo migration, biasing the run
            # toward spurious differentiation; drift is the sole operator that
            # realizes N discrete gene copies.
            retained_mass = 1.0 - event_count / size
            mutated: dict[AlleleId, float] = {
                allele_id: frequency * retained_mass
                for allele_id, frequency in frequency_map.items()
            }
            event_frequency = 1.0 / size
            for _event in range(event_count):
                mutated[registry.next_id()] = event_frequency
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
) -> ModelState:
    """Advance one generation in migration, mutation, then drift order.

    Args:
        state: Current model state.
        params: Validated run parameters.
        registry: Global mutant-allele allocator.
        rng: The run's explicitly threaded random generator.

    Returns:
        The next generation.
    """
    # Only the opt-in "stochastic" mode passes rng into migrate(); the
    # default "continuous" mode passes None, so migrate() consumes zero
    # rng draws and every existing reproducible run is bit-for-bit
    # unaffected by this feature's existence.
    migration_rng = rng if params.migrant_sampling == "stochastic" else None
    migrated = migrate(state, params.m, params.N, rng=migration_rng)
    mutated = mutate(migrated, params.mu, params.N, registry, rng)
    return drift(mutated, params.N, rng)


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
