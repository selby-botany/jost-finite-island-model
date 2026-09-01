"""Tests for one-generation update operators."""

from collections.abc import Callable

import numpy as np
import pytest

from fim.model.allele import (
    MINTED_ID_START,
    AlleleId,
    AlleleRegistry,
    FiniteAlleleRegistry,
    FiniteAlleleSpace,
)
from fim.model.initial import generate_initial_state
from fim.model.locus import LocusSpec
from fim.model.operators import (
    _jit_multinomial_via_binomial,
    _multinomial_via_binomial,
    drift,
    migrate,
    mutate,
    step,
)
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.model.topology import dense_matrix_from_neighbors, stepping_stone_neighbors


def _state() -> ModelState:
    """Return a two-deme biallelic state."""
    return ModelState(
        loci=(LocusSpec(1, 100),),
        frequencies=(
            ({AlleleId(0): 0.8, AlleleId(1): 0.2},),
            ({AlleleId(0): 0.2, AlleleId(1): 0.8},),
        ),
    )


def test_migrate_zero_is_identity() -> None:
    """No migration leaves all frequencies unchanged."""
    assert migrate(_state(), 0.0) == _state()


def test_migrate_one_replaces_each_deme_with_other_pool() -> None:
    """At m=1, two demes exchange their complete frequency vectors."""
    migrated = migrate(_state(), 1.0)

    expected_deme_zero = _state().frequency_map(1, 0)
    expected_deme_one = _state().frequency_map(0, 0)
    for allele_id, expected in expected_deme_zero.items():
        assert migrated.frequency_map(0, 0)[allele_id] == pytest.approx(expected)
    for allele_id, expected in expected_deme_one.items():
        assert migrated.frequency_map(1, 0)[allele_id] == pytest.approx(expected)


def test_migration_conserves_equal_deme_mean() -> None:
    """Symmetric all-to-all migration preserves metapopulation frequency."""
    migrated = migrate(_state(), 0.35)

    original_mean = (
        sum(_state().frequency_map(deme, 0).get(AlleleId(0), 0.0) for deme in range(2))
        / 2
    )
    migrated_mean = (
        sum(migrated.frequency_map(deme, 0).get(AlleleId(0), 0.0) for deme in range(2))
        / 2
    )
    assert migrated_mean == pytest.approx(original_mean)


def _extract_frequencies(state: ModelState) -> list[list[dict[int, float]]]:
    """Return normalized frequencies as plain ``int``-keyed dictionaries."""
    return [
        [
            {
                int(allele): freq
                for allele, freq in state.frequency_map(deme, locus).items()
            }
            for locus in range(state.locus_count)
        ]
        for deme in range(state.deme_count)
    ]


def _reference_symmetric_migration(
    frequencies: list[list[dict[int, float]]],
    sizes: tuple[int, ...],
    rate: float,
) -> list[list[dict[int, float]]]:
    """Compute scalar migration with the naive per-destination definition.

    This independent oracle re-sums every other deme for each destination
    (``O(d^2 * A)``) so that the optimized ``O(d * A)`` implementation can be
    validated against the exact definition it replaces.
    """
    deme_count = len(frequencies)
    locus_count = len(frequencies[0])
    result: list[list[dict[int, float]]] = []
    for destination in range(deme_count):
        others = [k for k in range(deme_count) if k != destination]
        other_weight = sum(sizes[k] for k in others)
        destination_maps: list[dict[int, float]] = []
        for locus in range(locus_count):
            local = frequencies[destination][locus]
            alleles: set[int] = set()
            for k in range(deme_count):
                alleles |= set(frequencies[k][locus])
            blended: dict[int, float] = {}
            for allele in alleles:
                pool = (
                    sum(
                        sizes[k] * frequencies[k][locus].get(allele, 0.0)
                        for k in others
                    )
                    / other_weight
                )
                blended[allele] = (1.0 - rate) * local.get(allele, 0.0) + rate * pool
            positive = {a: v for a, v in blended.items() if v > 0.0}
            total = sum(positive.values())
            destination_maps.append({a: v / total for a, v in positive.items()})
        result.append(destination_maps)
    return result


def _build_state(frequencies: list[list[dict[int, float]]]) -> ModelState:
    """Build a ``ModelState`` from raw ``int``-keyed frequency dictionaries."""
    locus_count = len(frequencies[0])
    return ModelState(
        loci=tuple(LocusSpec(index + 1, 100) for index in range(locus_count)),
        frequencies=tuple(
            tuple(
                {AlleleId(allele): freq for allele, freq in locus.items()}
                for locus in deme
            )
            for deme in frequencies
        ),
    )


_MIGRATION_EQUIVALENCE_CASES = {
    "equal_sizes_shared_biallelic": (
        [
            [{0: 0.7, 1: 0.3}],
            [{0: 0.2, 1: 0.8}],
            [{0: 0.5, 1: 0.5}],
        ],
        None,
        0.1,
    ),
    "uniform_size_multilocus": (
        [
            [{0: 0.6, 1: 0.4}, {2: 0.9, 3: 0.1}],
            [{0: 0.1, 1: 0.9}, {2: 0.3, 3: 0.7}],
            [{0: 0.5, 1: 0.5}, {2: 0.5, 3: 0.5}],
            [{0: 0.8, 1: 0.2}, {2: 0.2, 3: 0.8}],
        ],
        100,
        0.25,
    ),
    "unequal_sizes": (
        [
            [{0: 0.9, 1: 0.1}],
            [{0: 0.4, 1: 0.6}],
            [{0: 0.2, 1: 0.8}],
        ],
        (10, 250, 3000),
        0.3,
    ),
    "zero_rate_identity": (
        [
            [{0: 0.7, 1: 0.3}],
            [{0: 0.2, 1: 0.8}],
        ],
        (5, 9),
        0.0,
    ),
    "sparse_alleles": (
        [
            [{0: 1.0}],
            [{1: 0.5, 2: 0.5}],
            [{0: 0.25, 3: 0.75}],
            [{2: 0.4, 3: 0.6}],
        ],
        (100, 50, 200, 75),
        0.2,
    ),
    "private_alleles": (
        [
            [{0: 0.6, 10: 0.4}],
            [{0: 0.6, 11: 0.4}],
            [{0: 0.6, 12: 0.4}],
            [{0: 0.6, 13: 0.4}],
        ],
        (40, 60, 80, 20),
        0.15,
    ),
    "full_replacement_private": (
        [
            [{0: 0.5, 20: 0.5}],
            [{1: 0.5, 21: 0.5}],
            [{2: 0.5, 22: 0.5}],
        ],
        None,
        1.0,
    ),
}


@pytest.mark.parametrize(
    ("frequencies", "population_size", "rate"),
    list(_MIGRATION_EQUIVALENCE_CASES.values()),
    ids=list(_MIGRATION_EQUIVALENCE_CASES),
)
def test_symmetric_migration_matches_naive_reference(
    frequencies: list[list[dict[int, float]]],
    population_size: int | tuple[int, ...] | None,
    rate: float,
) -> None:
    """The O(d*A) migration reproduces the naive definition it replaces."""
    state = _build_state(frequencies)
    normalized = _extract_frequencies(state)
    deme_count = state.deme_count
    if population_size is None:
        sizes: tuple[int, ...] = (1,) * deme_count
    elif isinstance(population_size, int):
        sizes = (population_size,) * deme_count
    else:
        sizes = population_size

    migrated = migrate(state, rate, population_size)
    expected = _reference_symmetric_migration(normalized, sizes, rate)

    for deme in range(deme_count):
        for locus in range(state.locus_count):
            observed_map = migrated.frequency_map(deme, locus)
            expected_map = expected[deme][locus]
            assert {int(a) for a in observed_map} == set(expected_map)
            for allele, value in expected_map.items():
                assert observed_map[AlleleId(allele)] == pytest.approx(value, abs=1e-11)


def test_mutation_zero_is_identity(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """A zero mutation probability consumes no identities or randomness."""
    state = _state()

    assert mutate(state, 0.0, 100, AlleleRegistry(), rng(3)) is state


def _two_locus_state() -> ModelState:
    """Return a one-deme, two-locus state, each locus biallelic."""
    return ModelState(
        loci=(LocusSpec(1, 100), LocusSpec(2, 100)),
        frequencies=(
            (
                {AlleleId(0): 0.8, AlleleId(1): 0.2},
                {AlleleId(0): 0.8, AlleleId(1): 0.2},
            ),
        ),
    )


def test_mutation_per_locus_rate_only_mutates_the_configured_locus(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """A per-locus mutation-rate tuple applies each rate to its own locus.

    Locus 1's rate is exactly 0; locus 2's is not. Only locus 2 may show a
    mutant-range identity afterward — proving the rate is picked up by
    locus, not shared or averaged across them.
    """
    mutated = mutate(
        _two_locus_state(), (0.0, 0.3), 100, AlleleRegistry(), rng(20260822)
    )

    assert mutated.frequency_map(0, 0) == _two_locus_state().frequency_map(0, 0)
    assert any(
        int(allele_id) >= MINTED_ID_START for allele_id in mutated.frequency_map(0, 1)
    )


def test_mutation_uniform_per_locus_tuple_matches_scalar_broadcast(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """A per-locus tuple of equal rates behaves identically to the scalar.

    `SimulationParams` already collapses a uniform list to a scalar for
    storage, but `mutate()` itself must also treat the two forms as the
    same rate applied per locus, independent of that collapsing.
    """
    state = _two_locus_state()

    via_scalar = mutate(state, 0.3, 100, AlleleRegistry(), rng(11))
    via_tuple = mutate(state, (0.3, 0.3), 100, AlleleRegistry(), rng(11))

    assert via_scalar == via_tuple


def test_mutation_events_receive_fresh_ids(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Every observed mutation has a unique mutant-range identity."""
    mutated = mutate(_state(), 0.1, 100, AlleleRegistry(), rng(17))
    mutant_ids = {
        int(allele_id)
        for deme in mutated.frequencies
        for locus in deme
        for allele_id in locus
        if int(allele_id) >= MINTED_ID_START
    }

    assert mutant_ids
    assert len(mutant_ids) == len(set(mutant_ids))


def test_mutation_scales_existing_mass_and_preserves_sub_grid_allele(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Mutation reduces existing mass proportionally, not by grid rounding.

    Regression guard for the mutation operator: existing allele frequencies
    must be scaled by a single retained-mass factor so their ratios are
    preserved and a rare sub-``1/N`` migrant survives. The earlier
    implementation rounded continuous frequencies onto the ``1/N`` grid before
    resampling, which deterministically dropped alleles below ``0.5/N`` (here
    the ``0.004`` allele) and undid migration, biasing runs toward spurious
    differentiation.
    """
    population_size = 100
    common = AlleleId(0)
    sub_grid_migrant = AlleleId(1)
    # 0.004 < 1 / 100: below the grid the old apportionment could represent.
    state = ModelState(
        loci=(LocusSpec(1, 100),),
        frequencies=(({common: 0.996, sub_grid_migrant: 0.004},),),
    )

    mutated = mutate(state, 0.05, population_size, AlleleRegistry(), rng(7))
    result = mutated.frequency_map(0, 0)

    # The rare migrant is retained, not rounded away.
    assert result.get(sub_grid_migrant, 0.0) > 0.0
    # Proportional scaling preserves the exact pre-existing frequency ratio.
    assert result[common] / result[sub_grid_migrant] == pytest.approx(0.996 / 0.004)
    # Events still mint fresh identities and total mass stays normalized.
    assert any(int(allele_id) >= MINTED_ID_START for allele_id in result)
    assert sum(result.values()) == pytest.approx(1.0)


def test_mutation_finite_alleles_respects_locus_specific_capacity(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Each locus's mutation targets stay within that locus's own capacity.

    Two loci share one state but have different capacities (4 and 4**10);
    every observed target must respect its own locus's bound, not the
    other's — proving `mutate()` looks up the registry by `locus_id`
    rather than sharing one space across loci.
    """
    population_size = 200
    state = ModelState(
        loci=(LocusSpec(1, 1), LocusSpec(2, 10)),
        frequencies=(
            (
                {AlleleId(0): 1.0},
                {AlleleId(0): 1.0},
            ),
        ),
    )
    finite_alleles = FiniteAlleleRegistry(
        {
            1: FiniteAlleleSpace(4, [AlleleId(0)]),
            2: FiniteAlleleSpace(4**10, [AlleleId(0)]),
        }
    )

    mutated = mutate(
        state,
        0.3,
        population_size,
        AlleleRegistry(),
        rng(20260821),
        finite_alleles=finite_alleles,
    )

    assert all(int(a) < 4 for a in mutated.frequency_map(0, 0))
    assert all(int(a) < 4**10 for a in mutated.frequency_map(0, 1))
    assert any(int(a) >= 4 for a in mutated.frequency_map(0, 1))


def test_mutation_finite_alleles_accumulates_recurrent_targets() -> None:
    """Two events landing on the same target sum mass instead of overwriting.

    Capacity 2 leaves exactly one possible target for any mutating copy —
    deterministic in shape, so no statistical tolerance is needed: only
    alleles 0 and 1 can ever exist, and their mass must still sum to 1
    afterward. A flat assignment instead of accumulation would silently
    drop earlier events' contributions and this would fail.
    """
    population_size = 100
    state = ModelState(
        loci=(LocusSpec(1, 1),),
        frequencies=(({AlleleId(0): 0.5, AlleleId(1): 0.5},),),
    )
    finite_alleles = FiniteAlleleRegistry(
        {1: FiniteAlleleSpace(2, [AlleleId(0), AlleleId(1)])}
    )

    mutated = mutate(
        state,
        0.3,
        population_size,
        AlleleRegistry(),
        np.random.default_rng(5),
        finite_alleles=finite_alleles,
    )
    result = mutated.frequency_map(0, 0)

    assert set(result) == {AlleleId(0), AlleleId(1)}
    assert sum(result.values()) == pytest.approx(1.0)


def test_mutation_finite_alleles_none_matches_infinite_alleles_default(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Omitting `finite_alleles` is identical to passing `None` explicitly.

    The opt-in contract: every call written before this feature existed
    (including every other test in this file) keeps meaning exactly what
    it always meant.
    """
    state = _state()

    omitted = mutate(state, 0.1, 100, AlleleRegistry(), rng(17))
    explicit_none = mutate(
        state, 0.1, 100, AlleleRegistry(), rng(17), finite_alleles=None
    )

    assert omitted == explicit_none


@pytest.mark.statistical
def test_mutation_finite_alleles_recurrence_rate_matches_theory(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """A fixed-seed sample recurrence rate through `mutate()` matches theory.

    End-to-end version of `FiniteAlleleSpace`'s own recurrence-rate test:
    drives the same probability through `mutate()`'s source-attribution and
    accumulation logic, not just the space's `mutate_target` in isolation.
    ``population_size=1, mu=1.0`` makes each call exactly one mutation
    event with a certain, deterministic source — the only randomness left
    is that one event's target — so each trial is one clean Bernoulli
    observation, exactly like the space-level test.
    """
    capacity = 100
    minted_count = 50
    trials = 20_000
    expected_probability = (minted_count - 1) / (capacity - 1)
    generator = rng(20260821)
    state = ModelState(
        loci=(LocusSpec(1, 1),),
        frequencies=(({AlleleId(0): 1.0},),),
    )

    def one_target() -> AlleleId:
        """Mutate the single gene copy once and return its new identity."""
        finite_alleles = FiniteAlleleRegistry(
            {
                1: FiniteAlleleSpace(
                    capacity,
                    [AlleleId(identity) for identity in range(minted_count)],
                )
            }
        )
        mutated = mutate(
            state, 1.0, 1, AlleleRegistry(), generator, finite_alleles=finite_alleles
        )
        (target,) = mutated.frequency_map(0, 0)
        return target

    outcomes = np.asarray(
        [int(one_target()) < minted_count for _trial in range(trials)]
    )
    observed_rate = outcomes.mean()
    standard_error = np.sqrt(
        expected_probability * (1.0 - expected_probability) / trials
    )

    assert observed_rate == pytest.approx(
        expected_probability, abs=5.0 * standard_error
    )


def test_drift_preserves_invariants_and_is_seeded(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Multinomial drift returns reproducible N-bounded frequency maps."""
    first = drift(_state(), 20, rng(99))
    second = drift(_state(), 20, rng(99))

    assert first == second
    assert all(
        total == pytest.approx(1.0) for total in first.total_frequency().values()
    )
    assert all(support <= 20 for deme in first.support_sizes() for support in deme)


def test_step_matches_explicit_operator_order(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """The public pipeline is drift(mutate(migrate(state)))."""
    params = SimulationParams(
        N=20,
        m=0.2,
        mu=0.05,
        d=2,
        seed=6,
        loci=_state().loci,
    )
    expected_rng = rng(6)
    expected = drift(
        mutate(
            migrate(_state(), params.m, params.N),
            params.mu,
            params.N,
            AlleleRegistry(),
            expected_rng,
        ),
        params.N,
        expected_rng,
    )

    assert step(_state(), params, AlleleRegistry(), rng(6)) == expected


def test_matrix_migration_applies_source_weights() -> None:
    """A complete migration matrix blends source demes row by row."""
    migrated = migrate(
        _state(),
        ((0.75, 0.25), (0.25, 0.75)),
    )
    assert migrated.frequency_map(0, 0)[AlleleId(0)] == pytest.approx(0.65)
    assert migrated.frequency_map(1, 0)[AlleleId(0)] == pytest.approx(0.35)


def _three_deme_state() -> ModelState:
    """Return a three-deme biallelic state with distinct frequencies."""
    return ModelState(
        loci=(LocusSpec(1, 100),),
        frequencies=(
            ({AlleleId(0): 0.8, AlleleId(1): 0.2},),
            ({AlleleId(0): 0.2, AlleleId(1): 0.8},),
            ({AlleleId(0): 0.5, AlleleId(1): 0.5},),
        ),
    )


def test_asymmetric_migration_matrix_applies_per_row_directional_weights() -> None:
    """Rows need not agree: each deme's inflow mix is independently configurable.

    Deme 0 retains almost all of its own frequency and takes a small pull from
    deme 2; deme 1 blends evenly with deme 0; deme 2 does not migrate at all.
    A symmetric matrix (equal off-diagonal entries, as in the row-by-row test
    above) could never produce this — the whole point of a general matrix
    (design §9's "asymmetric migration" row) is a source mix that differs by
    destination.
    """
    matrix = ((0.95, 0.0, 0.05), (0.5, 0.5, 0.0), (0.0, 0.0, 1.0))

    migrated = migrate(_three_deme_state(), matrix)

    assert migrated.frequency_map(0, 0)[AlleleId(0)] == pytest.approx(0.785)
    assert migrated.frequency_map(1, 0)[AlleleId(0)] == pytest.approx(0.5)
    assert migrated.frequency_map(2, 0)[AlleleId(0)] == pytest.approx(0.5)


def test_matrix_migration_ignores_population_size() -> None:
    """A full matrix's rows are the authoritative weights, not N-derived ones.

    Unlike the scalar path, which auto-computes a size-weighted migrant pool
    from ``population_size``, a full matrix already states each destination's
    exact source weights — ``population_size`` has nothing left to contribute
    and must not silently change the result.
    """
    matrix = ((0.95, 0.0, 0.05), (0.5, 0.5, 0.0), (0.0, 0.0, 1.0))
    state = _three_deme_state()

    without_sizes = migrate(state, matrix)
    with_wildly_unequal_sizes = migrate(state, matrix, (5, 5_000, 20))

    assert without_sizes == with_wildly_unequal_sizes


def test_symmetric_scalar_migration_is_the_matrix_special_case() -> None:
    """Scalar migration for equal-size demes equals its explicit matrix form.

    Confirms design §9's claim directly: at equal deme size, the scalar
    all-other-demes formula and a fully written-out symmetric matrix
    (``1 - m`` on the diagonal, ``m / (d - 1)`` off it) are the same
    operator, not merely similar ones.
    """
    state = _three_deme_state()
    rate = 0.3
    equal_sizes = (10, 10, 10)
    equivalent_matrix = tuple(
        tuple(1.0 - rate if row == column else rate / 2.0 for column in range(3))
        for row in range(3)
    )

    via_scalar = migrate(state, rate, equal_sizes)
    via_matrix = migrate(state, equivalent_matrix)

    for deme in range(3):
        scalar_map = via_scalar.frequency_map(deme, 0)
        matrix_map = via_matrix.frequency_map(deme, 0)
        assert set(scalar_map) == set(matrix_map)
        for allele_id, value in scalar_map.items():
            assert matrix_map[allele_id] == pytest.approx(value)


def _single_private_allele_state(d: int) -> ModelState:
    """Return a state where deme 0 alone is fixed for a private allele."""
    frequencies: list[tuple[dict[AlleleId, float]]] = [({AlleleId(99): 1.0},)]
    frequencies.extend(({AlleleId(0): 1.0},) for _ in range(d - 1))
    return ModelState(loci=(LocusSpec(1, 100),), frequencies=tuple(frequencies))


def test_ring_stepping_stone_migration_reaches_only_direct_neighbors() -> None:
    """A ring topology is genuinely spatial: one hop reaches only neighbors.

    Proves the actual claim behind "stepping-stone" — not merely that the
    generated matrix looks sparse, but that migrating through it leaves an
    allele private to deme 0 completely absent from every non-neighboring
    deme after a single generation, while both ring neighbors (including
    the wraparound one) pick up exactly the expected trace of it.
    """
    d = 6
    matrix = dense_matrix_from_neighbors(
        stepping_stone_neighbors(d, topology="ring", rate=0.3), d
    )

    migrated = migrate(_single_private_allele_state(d), matrix)

    assert migrated.frequency_map(0, 0)[AlleleId(99)] == pytest.approx(0.7)
    assert migrated.frequency_map(1, 0)[AlleleId(99)] == pytest.approx(0.15)
    assert migrated.frequency_map(5, 0)[AlleleId(99)] == pytest.approx(0.15)
    for deme in (2, 3, 4):
        assert AlleleId(99) not in migrated.frequency_map(deme, 0)


def test_linear_stepping_stone_migration_does_not_wrap_around() -> None:
    """A linear chain is the ring's non-wrapping special case, not a variant math.

    Identical setup and rate to the ring test above; the only difference is
    that deme 5 — the ring's wraparound neighbor of deme 0 — picks up
    nothing at all here, because a bounded chain has no edge connecting the
    two ends.
    """
    d = 6
    matrix = dense_matrix_from_neighbors(
        stepping_stone_neighbors(d, topology="linear", rate=0.3), d
    )

    migrated = migrate(_single_private_allele_state(d), matrix)

    assert migrated.frequency_map(0, 0)[AlleleId(99)] == pytest.approx(0.7)
    assert migrated.frequency_map(1, 0)[AlleleId(99)] == pytest.approx(0.15)
    for deme in (2, 3, 4, 5):
        assert AlleleId(99) not in migrated.frequency_map(deme, 0)


def test_symmetric_migration_uses_population_size_weights() -> None:
    """Unequal demes contribute migrants in proportion to their copy counts."""
    state = ModelState(
        loci=_state().loci,
        frequencies=(
            ({AlleleId(0): 1.0},),
            ({AlleleId(1): 1.0},),
        ),
    )
    migrated = migrate(state, 1.0, (10, 30))
    assert migrated.frequency_map(0, 0) == {AlleleId(1): 1.0}
    assert migrated.frequency_map(1, 0) == {AlleleId(0): 1.0}


def test_migrate_stochastic_requires_population_size(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """rng without population_size is rejected rather than silently ignored."""
    with pytest.raises(ValueError, match="population_size"):
        migrate(_state(), 0.3, rng=rng(1))


@pytest.mark.statistical
def test_migrate_stochastic_scalar_migrant_count_matches_binomial_theory(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """A fixed-seed sample mean and variance fall in pre-derived bands.

    Deme 0 starts fixed for a private allele and deme 1 fixed for another;
    with only two demes, deme 0's entire migrant pool is deme 1, so the
    post-migration frequency of deme 1's allele in deme 0 *is* the random
    migrant fraction ``K / size`` directly, with ``K ~ Binomial(size, rate)``
    by construction.
    """
    size = 100
    rate = 0.3
    replicates = 10_000
    state = ModelState(
        loci=(LocusSpec(1, 100),),
        frequencies=(
            ({AlleleId(0): 1.0},),
            ({AlleleId(1): 1.0},),
        ),
    )
    generator = rng(20260818)
    observed = np.asarray(
        [
            migrate(state, rate, size, rng=generator)
            .frequency_map(0, 0)
            .get(AlleleId(1), 0.0)
            for _ in range(replicates)
        ]
    )
    expected_mean = rate
    expected_variance = rate * (1.0 - rate) / size
    mean_standard_error = np.sqrt(expected_variance / replicates)
    variance_standard_error = expected_variance * np.sqrt(2.0 / (replicates - 1))

    assert np.mean(observed) == pytest.approx(
        expected_mean, abs=5.0 * mean_standard_error
    )
    assert np.var(observed, ddof=1) == pytest.approx(
        expected_variance, abs=5.0 * variance_standard_error
    )


def test_migrate_stochastic_preserves_pool_composition(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Randomizing the migrant count never changes migrant composition.

    Deme 0 carries only allele 0; the migrant pool (demes 1 and 2, equal
    size) carries only alleles 1 and 2, in an exact 0.5/0.5 split. Because
    allele 0 never appears in the pool and alleles 1/2 never appear
    locally, whatever count a stochastic draw lands on, alleles 1 and 2
    must still arrive in that same 0.5/0.5 ratio — only the *total*
    migrant mass is random here, never its internal mixture (`migrate`'s
    docstring, `_blend`).
    """
    state = ModelState(
        loci=(LocusSpec(1, 100),),
        frequencies=(
            ({AlleleId(0): 1.0},),
            ({AlleleId(1): 0.6, AlleleId(2): 0.4},),
            ({AlleleId(1): 0.4, AlleleId(2): 0.6},),
        ),
    )
    rate = 0.4
    sizes = (100, 100, 100)

    migrated = migrate(state, rate, sizes, rng=rng(7))

    migrant_one = migrated.frequency_map(0, 0).get(AlleleId(1), 0.0)
    migrant_two = migrated.frequency_map(0, 0).get(AlleleId(2), 0.0)
    assert migrant_one + migrant_two > 0.0
    assert migrant_one / (migrant_one + migrant_two) == pytest.approx(0.5, abs=1e-9)


@pytest.mark.statistical
def test_migrate_stochastic_matrix_migrant_count_matches_binomial_theory(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """The matrix path's stochastic branch obeys the same binomial theory.

    Mirrors the scalar-path statistical test above, but through
    ``_migrate_matrix``'s independent stochastic branch: deme 0's row gives
    a 0.3 non-self weight entirely to deme 1, so the same direct
    migrant-fraction argument applies.
    """
    size = 100
    matrix = ((0.7, 0.3), (0.3, 0.7))
    replicates = 10_000
    state = ModelState(
        loci=(LocusSpec(1, 100),),
        frequencies=(
            ({AlleleId(0): 1.0},),
            ({AlleleId(1): 1.0},),
        ),
    )
    generator = rng(20260818)
    observed = np.asarray(
        [
            migrate(state, matrix, size, rng=generator)
            .frequency_map(0, 0)
            .get(AlleleId(1), 0.0)
            for _ in range(replicates)
        ]
    )
    expected_mean = 0.3
    expected_variance = 0.3 * 0.7 / size
    mean_standard_error = np.sqrt(expected_variance / replicates)
    variance_standard_error = expected_variance * np.sqrt(2.0 / (replicates - 1))

    assert np.mean(observed) == pytest.approx(
        expected_mean, abs=5.0 * mean_standard_error
    )
    assert np.var(observed, ddof=1) == pytest.approx(
        expected_variance, abs=5.0 * variance_standard_error
    )


def test_migrate_stochastic_matrix_self_weight_one_matches_continuous(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """A row with no outgoing weight never samples and never drifts from it.

    Deme 2's row is ``(0.0, 0.0, 1.0)`` — full self-retention. The
    stochastic and continuous paths must agree exactly for that deme,
    since there is nothing random left to draw once the migrant weight
    is zero.
    """
    matrix = ((0.95, 0.0, 0.05), (0.5, 0.5, 0.0), (0.0, 0.0, 1.0))
    state = _three_deme_state()

    continuous = migrate(state, matrix)
    stochastic = migrate(state, matrix, (100, 100, 100), rng=rng(3))

    assert stochastic.frequency_map(2, 0) == continuous.frequency_map(2, 0)


def test_mutation_can_have_no_events_without_changing_maps(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """A seeded zero-event mutation pass preserves the existing maps."""
    state = _state()
    mutated = mutate(state, 1e-12, 100, AlleleRegistry(), rng(1))
    assert mutated == state


@pytest.mark.parametrize(
    ("operation", "population_size", "message"),
    [
        (drift, 0, "positive"),
        (drift, (20,), "one gene-copy"),
        (mutate, (20,), "one gene-copy"),
    ],
)
def test_operators_validate_population_size_shape(
    operation: object,
    population_size: object,
    message: str,
) -> None:
    """Operator entry points reject invalid shared and per-deme sizes."""
    if operation is drift:
        with pytest.raises(ValueError, match=message):
            drift(_state(), population_size, np.random.default_rng(1))  # type: ignore[arg-type]
    else:
        with pytest.raises(ValueError, match=message):
            mutate(
                _state(),
                0.1,
                population_size,  # type: ignore[arg-type]
                AlleleRegistry(),
                np.random.default_rng(1),
            )


@pytest.mark.statistical
def test_drift_variance_matches_binomial_theory(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """A fixed-seed sample variance falls in a pre-derived five-sigma band."""
    population_size = 100
    probability = 0.3
    replicates = 10_000
    state = ModelState(
        loci=(LocusSpec(1, 100),),
        frequencies=(({AlleleId(0): probability, AlleleId(1): 1.0 - probability},),),
    )
    generator = rng(20260814)
    observed = np.asarray(
        [
            drift(state, population_size, generator)
            .frequency_map(0, 0)
            .get(AlleleId(0), 0.0)
            for _ in range(replicates)
        ]
    )
    expected_variance = probability * (1.0 - probability) / population_size
    variance_standard_error = expected_variance * np.sqrt(2.0 / (replicates - 1))

    assert np.var(observed, ddof=1) == pytest.approx(
        expected_variance,
        abs=5.0 * variance_standard_error,
    )


@pytest.mark.statistical
def test_drift_variance_matches_binomial_theory_per_deme_when_n_is_unequal(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Each deme resamples at its own N, not a shared or averaged value."""
    sizes = (50, 400)
    probability = 0.3
    replicates = 10_000
    state = ModelState(
        loci=(LocusSpec(1, 100),),
        frequencies=(
            ({AlleleId(0): probability, AlleleId(1): 1.0 - probability},),
            ({AlleleId(0): probability, AlleleId(1): 1.0 - probability},),
        ),
    )
    generator = rng(20260814)
    observed = np.asarray(
        [
            [
                drift(state, sizes, generator)
                .frequency_map(deme, 0)
                .get(
                    AlleleId(0),
                    0.0,
                )
                for deme in range(len(sizes))
            ]
            for _ in range(replicates)
        ]
    )
    for deme, size in enumerate(sizes):
        expected_variance = probability * (1.0 - probability) / size
        variance_standard_error = expected_variance * np.sqrt(2.0 / (replicates - 1))
        assert np.var(observed[:, deme], ddof=1) == pytest.approx(
            expected_variance,
            abs=5.0 * variance_standard_error,
        )


# `_multinomial_via_binomial`/`_jit_multinomial_via_binomial` (JIT
# feasibility, `20260901-claude-sonnet-5-fim-engine-backend-factory-
# design.md` §5.3): the whole point of this decomposition is that it
# reproduces `numpy.random.Generator.multinomial`'s own output
# bit-for-bit, not merely the same distribution — these tests check
# exact equality, across many randomized parameter combinations with a
# fixed master seed (reproducible per commit, not left to real entropy),
# deliberately including edge cases a hand-picked example could miss:
# `n == 0`, a single category, and a zero-probability category.


def test_multinomial_via_binomial_matches_generator_multinomial_exactly() -> None:
    """The plain (unjitted) decomposition matches `rng.multinomial` bit-for-bit."""
    controller = np.random.default_rng(20260901)
    mismatches = 0
    for trial in range(1000):
        seed = int(controller.integers(0, 2**31))
        category_count = int(controller.integers(2, 12))
        raw = controller.random(category_count)
        if trial % 7 == 0:
            raw[0] = 0.0  # exercise a zero-probability category
        probabilities = raw / raw.sum()
        n = int(controller.integers(0, 2000))

        direct = np.random.Generator(np.random.PCG64(seed)).multinomial(
            n, probabilities
        )
        decomposed = _multinomial_via_binomial(
            np.random.Generator(np.random.PCG64(seed)), n, probabilities
        )
        if not (direct == decomposed).all():
            mismatches += 1

    assert mismatches == 0


def test_multinomial_via_binomial_handles_a_single_category() -> None:
    """A single category has no conditional draw to make — `n` goes there whole."""
    result = _multinomial_via_binomial(
        np.random.Generator(np.random.PCG64(1)), 42, np.array([1.0])
    )
    assert result.tolist() == [42]


def test_multinomial_via_binomial_handles_n_zero() -> None:
    """Zero individuals to distribute is a legal, all-zero draw."""
    result = _multinomial_via_binomial(
        np.random.Generator(np.random.PCG64(1)), 0, np.array([0.3, 0.7])
    )
    assert result.tolist() == [0, 0]


def test_jit_multinomial_via_binomial_matches_plain_decomposition() -> None:
    """The Numba-JIT-compiled path is exactly the same function, compiled."""
    pytest.importorskip("numba")
    controller = np.random.default_rng(20260902)
    for _ in range(200):
        seed = int(controller.integers(0, 2**31))
        category_count = int(controller.integers(2, 8))
        raw = controller.random(category_count)
        probabilities = raw / raw.sum()
        n = int(controller.integers(0, 500))

        plain = _multinomial_via_binomial(
            np.random.Generator(np.random.PCG64(seed)), n, probabilities
        )
        jitted = _jit_multinomial_via_binomial(
            np.random.Generator(np.random.PCG64(seed)), n, probabilities
        )
        assert plain.tolist() == list(jitted)


def test_drift_with_jit_matches_drift_without_jit_bit_for_bit(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """`jit=True` changes nothing about `drift`'s own output, for the same seed."""
    pytest.importorskip("numba")
    unjitted = drift(_state(), 20, rng(20260901), jit=False)
    jitted = drift(_state(), 20, rng(20260901), jit=True)

    assert unjitted == jitted


def test_step_with_jit_matches_step_without_jit_bit_for_bit(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """`jit=True` changes nothing about `step`'s own output either, end to end."""
    pytest.importorskip("numba")
    params = SimulationParams(N=20, m=0.2, mu=0.05, d=2, seed=6, loci=_state().loci)

    unjitted = step(_state(), params, AlleleRegistry(), rng(6), jit=False)
    jitted = step(_state(), params, AlleleRegistry(), rng(6), jit=True)

    assert unjitted == jitted


def test_drift_with_jit_matches_without_jit_across_many_generations_and_demes(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """The batched, ragged (deme, locus) flat-buffer path stays bit-identical.

    `_state()`'s own fixture (2 demes, 1 locus) barely exercises
    `_build_flat_drift_buffers`'s own ragged, varying-category-count
    layout — this test uses many demes and loci, run across enough
    generations that some (deme, locus) pairs lose alleles to drift
    entirely (shrinking their own category count generation to
    generation, changing every later `offsets` slice's own width), which
    is exactly the shape most likely to expose an indexing bug in the
    flat-buffer-plus-offsets packing/unpacking if one existed.
    """
    pytest.importorskip("numba")
    params = SimulationParams(
        N=25,
        m=0.15,
        mu=0.02,
        d=12,
        seed=20260901,
        loci=(LocusSpec(1, 50), LocusSpec(2, 30), LocusSpec(3, 80)),
    )
    state = generate_initial_state(params, rng(20260901))

    unjitted_rng = rng(7)
    jitted_rng = rng(7)
    for _ in range(40):
        unjitted_state = drift(state, params.N, unjitted_rng, jit=False)
        jitted_state = drift(state, params.N, jitted_rng, jit=True)
        assert unjitted_state == jitted_state
        state = unjitted_state
