"""Tests for one-generation update operators."""

from collections.abc import Callable

import numpy as np
import pytest

from fim.model.allele import MINTED_ID_START, AlleleId, AlleleRegistry
from fim.model.locus import LocusSpec
from fim.model.operators import drift, migrate, mutate, step
from fim.model.params import SimulationParams
from fim.model.state import ModelState


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
