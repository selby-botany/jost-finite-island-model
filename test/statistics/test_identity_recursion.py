"""Tests for the closed-form identity recursion (`fim.statistics`)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from fim.statistics import (
    IDENTITY_STATISTIC_NAMES,
    equilibrium_d,
    g_st,
    h_s,
    h_t,
    identities_from_heterozygosities,
    identities_to_statistics,
    identity_recursion,
    jost_d,
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

    An independent implementation of the recursion: migrate as `M J M^T`,
    mutate by the exact second moment, drift on the diagonal only.

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
    survival = (1.0 - mu) ** 2 + mu * (1.0 - mu) / population_size
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
