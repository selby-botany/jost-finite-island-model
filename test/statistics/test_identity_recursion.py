"""Tests for the closed-form identity recursion (`fim.statistics`)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from fim.config.limits import MAXIMUM_RECURSION_DEMES
from fim.statistics import (
    IDENTITY_STATISTIC_NAMES,
    equilibrium_d,
    g_st,
    h_s,
    h_t,
    identities_from_heterozygosities,
    identities_to_statistics,
    identity_matrix_from_frequencies,
    identity_recursion,
    jost_d,
    matrix_identity_trajectory,
)

# (gene copies per deme, migration, mutation, demes): fast mixing, slow
# mixing, two demes, and the starter scale, so both eigenvalue orderings
# and the two-deme special case are covered.
CONFIGURATIONS = [
    (60, 0.05, 0.01, 5),
    (450, 0.01, 0.005, 4),
    (100, 0.1, 0.01, 2),
    (225, 0.001, 3e-7, 20),
]


def _brute_force_identities(
    population_size: int,
    m: float,
    mu: float,
    d: int,
    steps: int,
    start: np.ndarray,
) -> tuple[float, float]:
    """Iterate the full `d` by `d` identity matrix, no symmetry assumed.

    An independent implementation of the textbook recursion: migrate as
    `M J M^T`, drift on the diagonal only (`1/N + (1 - 1/N) J_ii`), then
    each copy mutates (`(1 - mu)^2` on every pair of distinct copies).

    Args:
        population_size: Gene copies per deme.
        m: Symmetric migration rate.
        mu: Per-copy mutation probability.
        d: Number of demes.
        steps: Generations to iterate.
        start: Starting `d` by `d` identity matrix.

    Returns:
        Mean diagonal and mean off-diagonal entry after `steps`.
    """
    migration = np.full((d, d), m / (d - 1))
    np.fill_diagonal(migration, 1.0 - m)
    survival = (1.0 - mu) ** 2
    identities = start.copy()
    diagonal = np.diag_indices(d)
    for _ in range(steps):
        identities = migration @ identities @ migration.T * survival
        identities[diagonal] = (
            1.0 / population_size + (1.0 - 1.0 / population_size) * identities[diagonal]
        )
    within = float(np.mean(np.diag(identities)))
    between = float((identities.sum() - np.trace(identities)) / (d * (d - 1)))
    return within, between


def _asymmetric_start(d: int, seed: int) -> np.ndarray:
    """Return a symmetric, deliberately uneven identity matrix."""
    values = np.random.default_rng(seed).random((d, d))
    return (values + values.T) / 2.0


@pytest.mark.parametrize(("size", "m", "mu", "d"), CONFIGURATIONS)
@pytest.mark.parametrize("steps", [0, 1, 7, 200])
def test_closed_form_matches_full_matrix_iteration(
    size: int, m: float, mu: float, d: int, steps: int
) -> None:
    """The closed form equals the full matrix, even from an uneven start."""
    start = _asymmetric_start(d, seed=size + d)
    within0 = float(np.mean(np.diag(start)))
    between0 = float((start.sum() - np.trace(start)) / (d * (d - 1)))

    expected = _brute_force_identities(size, m, mu, d, steps, start)
    actual = identity_recursion(size, m, mu, d).identities_after(
        steps, within0, between0
    )

    assert actual == pytest.approx(expected, rel=1e-9, abs=1e-12)


@pytest.mark.parametrize(("size", "m", "mu", "d"), CONFIGURATIONS)
def test_fixed_point_is_where_the_trajectory_ends(
    size: int, m: float, mu: float, d: int
) -> None:
    """A very long run reaches the recursion's own fixed point."""
    recursion = identity_recursion(size, m, mu, d)

    within, between = recursion.identities_after(50_000_000, 1.0, 1.0)

    assert (within, between) == pytest.approx(recursion.fixed_point, rel=1e-9)


@pytest.mark.parametrize(("size", "m", "mu", "d"), CONFIGURATIONS)
def test_equilibrium_d_agrees_with_the_published_approximation(
    size: int, m: float, mu: float, d: int
) -> None:
    """The exact fixed-point `D` is within 15% of `equilibrium_d`."""
    recursion = identity_recursion(size, m, mu, d)

    settled = identities_to_statistics(*recursion.fixed_point, d)["D"]

    assert settled == pytest.approx(equilibrium_d(m, mu, d), rel=0.15)


def test_identical_founding_demes_start_with_no_differentiation() -> None:
    """`within == between == 1` is `D = G_ST = 0` at generation zero."""
    statistics = identity_recursion(100, 0.05, 0.001, 4).statistics_after(0, 1.0, 1.0)

    assert statistics["D"] == pytest.approx(0.0, abs=1e-12)
    assert statistics["G_ST"] == pytest.approx(0.0, abs=1e-12)


def test_statistics_match_the_frequency_table_definitions() -> None:
    """`identities_to_statistics` matches the frequency-table definitions."""
    rng = np.random.default_rng(20260926)
    d = 4
    weights = rng.dirichlet(np.ones(5), size=d)
    table = [{allele: float(p) for allele, p in enumerate(row)} for row in weights]
    gram = weights @ weights.T
    within = float(np.mean(np.diag(gram)))
    between = float((gram.sum() - np.trace(gram)) / (d * (d - 1)))

    actual = identities_to_statistics(within, between, d)

    assert actual["H_S"] == pytest.approx(h_s(table))
    assert actual["H_T"] == pytest.approx(h_t(table))
    assert actual["G_ST"] == pytest.approx(g_st(table))
    assert actual["D"] == pytest.approx(jost_d(table))
    assert set(actual) == set(IDENTITY_STATISTIC_NAMES)


def test_heterozygosities_round_trip_through_identities() -> None:
    """`identities_from_heterozygosities` inverts `H_S`/`H_T`."""
    within, between = 0.62, 0.31
    statistics = identities_to_statistics(within, between, 5)

    recovered = identities_from_heterozygosities(
        statistics["H_S"], statistics["H_T"], 5
    )

    assert recovered == pytest.approx((within, between))


def test_no_diversity_gives_zero_rather_than_nan() -> None:
    """A fixed population (everyone identical) has finite, zero differentiation."""
    statistics = identities_to_statistics(1.0, 1.0, 3)

    assert all(math.isfinite(value) for value in statistics.values())
    assert statistics["G_ST"] == 0.0


@pytest.mark.parametrize(
    ("size", "m", "mu", "d"),
    [(100, 0.1, 0.01, 1), (0, 0.1, 0.01, 4), (100, 1.5, 0.01, 4), (100, 0.1, -1, 4)],
)
def test_invalid_inputs_are_refused(size: int, m: float, mu: float, d: int) -> None:
    """Out-of-range inputs raise `ValueError`."""
    with pytest.raises(ValueError, match="identity recursion"):
        identity_recursion(size, m, mu, d)


def test_no_migration_and_no_mutation_has_no_fixed_point() -> None:
    """Fully isolated, mutation-free demes never settle, so there is no closed form."""
    with pytest.raises(ValueError, match="no closed-form trajectory"):
        identity_recursion(100, 0.0, 0.0, 4)


HUB_SIZES = (200, 200, 200, 800)
HUB_MATRIX = (
    (0.95, 0.02, 0.02, 0.01),
    (0.02, 0.95, 0.02, 0.01),
    (0.02, 0.02, 0.95, 0.01),
    (0.01, 0.01, 0.01, 0.97),
)
# Directed ring: not reversible, so the operator has complex eigenvalues.
DIRECTED_RING = tuple(
    tuple(0.8 if i == j else 0.2 if j == (i + 1) % 5 else 0.0 for j in range(5))
    for i in range(5)
)


def _brute_force_statistics(
    sizes: tuple[int, ...],
    matrix: tuple[tuple[float, ...], ...],
    mu: float,
    start: np.ndarray,
    steps: int,
) -> dict[str, float]:
    """Iterate the full identity matrix one generation at a time."""
    d = len(sizes)
    migration = np.asarray(matrix)
    inverse = 1.0 / np.asarray(sizes, dtype=float)
    survival = (1.0 - mu) ** 2
    identities = start.copy()
    diagonal = np.diag_indices(d)
    for _ in range(steps):
        migrated = migration @ identities @ migration.T
        identities = survival * migrated
        identities[diagonal] = inverse + (1.0 - inverse) * survival * migrated[diagonal]
    return identities_to_statistics(
        float(np.mean(np.diag(identities))),
        float((identities.sum() - np.trace(identities)) / (d * (d - 1))),
        d,
    )


@pytest.mark.parametrize(
    ("sizes", "matrix", "mu"),
    [
        (HUB_SIZES, HUB_MATRIX, 0.001),
        (HUB_SIZES, HUB_MATRIX, 0.0),
        ((150,) * 5, DIRECTED_RING, 0.002),
    ],
)
def test_matrix_trajectory_matches_step_by_step_iteration(
    sizes: tuple[int, ...], matrix: tuple[tuple[float, ...], ...], mu: float
) -> None:
    """Unequal sizes, a hub, and a non-reversible ring all match iteration."""
    d = len(sizes)
    start = _asymmetric_start(d, seed=3)
    steps = [0, 1, 5, 40, 300]

    actual = matrix_identity_trajectory(
        deme_sizes=sizes,
        migration=matrix,
        mutation=mu,
        initial_identities=start,
        generations=steps,
    )

    for index, step in enumerate(steps):
        expected = _brute_force_statistics(sizes, matrix, mu, start, step)
        for name in IDENTITY_STATISTIC_NAMES:
            assert actual[name][index] == pytest.approx(
                expected[name], rel=1e-8, abs=1e-10
            ), (name, step)


def test_matrix_trajectory_agrees_with_the_two_variable_form_for_an_island() -> None:
    """Equal sizes and symmetric migration give the same curve either way."""
    size, m, mu, d = 60, 0.05, 0.01, 5
    migration = np.full((d, d), m / (d - 1))
    np.fill_diagonal(migration, 1.0 - m)
    start = _asymmetric_start(d, seed=11)
    within = float(np.mean(np.diag(start)))
    between = float((start.sum() - np.trace(start)) / (d * (d - 1)))
    steps = [0, 3, 50, 900]

    matrix_form = matrix_identity_trajectory(
        deme_sizes=(size,) * d,
        migration=migration,
        mutation=mu,
        initial_identities=start,
        generations=steps,
    )

    recursion = identity_recursion(size, m, mu, d)
    for index, step in enumerate(steps):
        two_variable = recursion.statistics_after(step, within, between)
        for name in IDENTITY_STATISTIC_NAMES:
            assert matrix_form[name][index] == pytest.approx(
                two_variable[name], rel=1e-3, abs=1e-6
            )


def test_identity_matrix_from_frequencies_matches_the_definition() -> None:
    """Diagonal is `sum x^2`, off-diagonal `sum x y`, averaged over loci."""
    frequencies = [
        [{0: 0.5, 1: 0.5}, {0: 1.0}],
        [{0: 1.0}, {0: 0.25, 2: 0.75}],
    ]

    matrix = identity_matrix_from_frequencies(frequencies)

    assert matrix[0, 0] == pytest.approx((0.5 + 1.0) / 2)
    assert matrix[1, 1] == pytest.approx((1.0 + 0.625) / 2)
    assert matrix[0, 1] == pytest.approx((0.5 + 0.25) / 2)
    assert matrix[0, 1] == matrix[1, 0]


def test_matrix_trajectory_refuses_too_many_demes() -> None:
    """More demes than the eigenproblem limit is refused, not slow."""
    d = MAXIMUM_RECURSION_DEMES + 1
    with pytest.raises(ValueError, match="between 2 and"):
        matrix_identity_trajectory(
            deme_sizes=(10,) * d,
            migration=np.eye(d),
            mutation=0.01,
            initial_identities=np.eye(d),
            generations=[0],
        )


def test_matrix_trajectory_without_migration_or_mutation_has_no_fixed_point() -> None:
    """Isolated, mutation-free demes never settle."""
    with pytest.raises(ValueError, match="no closed-form trajectory"):
        matrix_identity_trajectory(
            deme_sizes=(10, 10),
            migration=np.eye(2),
            mutation=0.0,
            initial_identities=np.eye(2),
            generations=[0],
        )
