"""Tests for `fim.convergence.defaults`: derived window and cap."""

from __future__ import annotations

import math

import pytest

from fim.config.convergence import (
    ABSOLUTE_MAX_GENERATIONS,
    CAP_RELAXATION_MULTIPLE,
    MINIMUM_MAX_GENERATIONS,
    MINIMUM_WINDOW,
    WINDOW_RELAXATION_MULTIPLE,
)
from fim.config.limits import MAXIMUM_RECURSION_DEMES
from fim.convergence.defaults import (
    derive_convergence_defaults,
    describe_derived_convergence,
    island_relaxation_time,
    panmictic_equilibration,
    recursion_relaxation_time,
    relaxation_time,
)


def _island(d: int, m: float) -> list[list[float]]:
    """Return the symmetric island migration matrix."""
    return [
        [1.0 - m if row == column else m / (d - 1) for column in range(d)]
        for row in range(d)
    ]


def _ring(d: int, m: float) -> list[list[float]]:
    """Return a nearest-neighbor ring migration matrix."""
    matrix = [[0.0] * d for _ in range(d)]
    for i in range(d):
        matrix[i][i] += 1.0 - m
        matrix[i][(i + 1) % d] += m / 2.0
        matrix[i][(i - 1) % d] += m / 2.0
    return matrix


# (d, N, m, mu): the closed form must track the recursion's own eigenvalue.
ISLAND_CASES = [
    (5, 100, 1e-4, 1e-6),
    (20, 100, 1e-3, 1e-6),
    (4, 100, 0.01, 0.005),
    (24, 2000, 0.01, 0.001),
]


@pytest.mark.parametrize(("d", "n", "m", "mu"), ISLAND_CASES)
def test_closed_form_matches_the_recursion_eigenvalue(
    d: int, n: int, m: float, mu: float
) -> None:
    """The island closed form is within 5% of the recursion's slowest mode."""
    truth = recursion_relaxation_time(
        deme_sizes=[n] * d, migration=_island(d, m), mutation=mu
    )
    closed = island_relaxation_time(
        total_size=d * n, deme_count=d, migration=m, mutation=mu
    )
    assert closed == pytest.approx(truth, rel=0.05)


def test_dear_nolan_low_relaxation_time_is_about_twenty_thousand() -> None:
    """The source scenario relaxes over about 20,000 generations."""
    tau = relaxation_time(deme_sizes=[100] * 5, migration=1e-4, mutation_rates=[1e-6])
    assert tau == pytest.approx(19_700, rel=0.01)


def test_derived_defaults_are_multiples_of_the_relaxation_time() -> None:
    """Window and cap are the documented multiples of `tau`."""
    derived = derive_convergence_defaults(
        deme_sizes=[100] * 5, migration=1e-4, mutation_rates=[1e-6]
    )
    assert derived.window == math.ceil(
        WINDOW_RELAXATION_MULTIPLE * derived.relaxation_time
    )
    assert derived.max_generations == math.ceil(
        CAP_RELAXATION_MULTIPLE * derived.relaxation_time
    )


def test_fast_models_keep_the_historical_floors() -> None:
    """A quickly relaxing model never gets a window or cap below their own floors."""
    derived = derive_convergence_defaults(
        deme_sizes=[10] * 3, migration=0.5, mutation_rates=[0.1]
    )
    assert derived.window == MINIMUM_WINDOW
    assert derived.max_generations == MINIMUM_MAX_GENERATIONS


def test_window_never_exceeds_the_cap_when_the_cap_is_clamped() -> None:
    """A nearly isolated system stays finite and keeps window <= cap."""
    derived = derive_convergence_defaults(
        deme_sizes=[100] * 3, migration=1e-12, mutation_rates=[1e-12]
    )
    assert derived.max_generations == ABSOLUTE_MAX_GENERATIONS
    assert derived.window <= derived.max_generations


@pytest.mark.parametrize(("smaller", "larger"), [(1e-3, 1e-4), (1e-4, 1e-5)])
def test_lower_migration_never_shortens_the_window(
    smaller: float, larger: float
) -> None:
    """Monotonicity: less migration means a longer (or equal) window."""
    fast = derive_convergence_defaults(
        deme_sizes=[100] * 5, migration=smaller, mutation_rates=[1e-6]
    )
    slow = derive_convergence_defaults(
        deme_sizes=[100] * 5, migration=larger, mutation_rates=[1e-6]
    )
    assert slow.window >= fast.window


def test_mutation_dominates_at_high_migration() -> None:
    """With strong migration and mutation, tau is about 1 / (2 mu)."""
    tau = relaxation_time(
        deme_sizes=[2000] * 100, migration=0.01, mutation_rates=[0.001]
    )
    assert tau == pytest.approx(499, rel=0.01)


def test_ring_is_slower_than_the_island_model_at_equal_rate() -> None:
    """A ring's local migration lengthens the relaxation time."""
    island = relaxation_time(
        deme_sizes=[100] * 10, migration=_island(10, 0.01), mutation_rates=[1e-6]
    )
    ring = relaxation_time(
        deme_sizes=[100] * 10, migration=_ring(10, 0.01), mutation_rates=[1e-6]
    )
    assert ring > island


def test_explicit_island_matrix_agrees_with_the_scalar_form() -> None:
    """A scalar `m` and its equivalent matrix give the same `tau`."""
    scalar = relaxation_time(
        deme_sizes=[100] * 5, migration=1e-3, mutation_rates=[1e-6]
    )
    matrix = relaxation_time(
        deme_sizes=[100] * 5, migration=_island(5, 1e-3), mutation_rates=[1e-6]
    )
    assert scalar == pytest.approx(matrix, rel=0.05)


def test_asymmetric_matrix_has_a_finite_relaxation_time() -> None:
    """A one-way chain of demes still relaxes."""
    matrix = [[0.9, 0.1, 0.0], [0.0, 0.9, 0.1], [0.1, 0.0, 0.9]]
    tau = relaxation_time(deme_sizes=[50] * 3, migration=matrix, mutation_rates=[1e-5])
    assert 0.0 < tau < math.inf


def test_unequal_sizes_use_the_recursion_route() -> None:
    """Unequal deme sizes with a scalar `m` still produce a finite `tau`."""
    tau = relaxation_time(
        deme_sizes=[50, 100, 200], migration=1e-3, mutation_rates=[1e-6]
    )
    assert tau > 0.0


def test_the_slowest_locus_sets_the_relaxation_time() -> None:
    """The smallest per-locus rate decides, whatever the other loci do."""
    slowest = relaxation_time(
        deme_sizes=[100] * 5, migration=1e-3, mutation_rates=[1e-5]
    )
    assert slowest == relaxation_time(
        deme_sizes=[100] * 5, migration=1e-3, mutation_rates=[1e-3, 1e-5]
    )
    assert slowest == relaxation_time(
        deme_sizes=[100] * 5, migration=1e-3, mutation_rates=[1e-5, 1e-3, 1e-4]
    )
    assert slowest > relaxation_time(
        deme_sizes=[100] * 5, migration=1e-3, mutation_rates=[1e-3]
    )


def test_the_slowest_locus_also_sets_the_matrix_route() -> None:
    """Unequal deme sizes take the eigenvalue route; the rule is the same."""
    sizes = [60, 100, 140]
    assert relaxation_time(
        deme_sizes=sizes, migration=0.05, mutation_rates=[1e-3, 1e-5]
    ) == relaxation_time(deme_sizes=sizes, migration=0.05, mutation_rates=[1e-5])


def test_no_migration_and_no_mutation_has_no_relaxation_time() -> None:
    """Nothing to wait for: reject rather than guess."""
    with pytest.raises(ValueError, match="no relaxation time"):
        derive_convergence_defaults(
            deme_sizes=[100] * 3, migration=0.0, mutation_rates=[0.0]
        )


def test_no_migration_with_mutation_uses_the_mutation_rate() -> None:
    """Isolated demes still lose identity through mutation."""
    tau = relaxation_time(deme_sizes=[100] * 3, migration=0.0, mutation_rates=[1e-3])
    assert tau == pytest.approx(500.0)


def test_large_explicit_matrix_is_rejected_with_guidance() -> None:
    """Above the eigenvalue route's size limit, `auto` is not available."""
    d = MAXIMUM_RECURSION_DEMES + 1
    with pytest.raises(ValueError, match="explicitly"):
        relaxation_time(
            deme_sizes=[100] * d, migration=_island(d, 0.01), mutation_rates=[1e-6]
        )


def test_large_scalar_island_uses_the_closed_form() -> None:
    """A scalar `m` at any `d` never needs the eigenvalue route."""
    tau = relaxation_time(deme_sizes=[100] * 200, migration=0.01, mutation_rates=[1e-6])
    assert tau > 0.0


def test_panmictic_burn_in_for_the_equilibrium_split_example() -> None:
    """600 gene copies at `mu` 0.001: tau about 273, burn-in about 4.6 tau.

    The bundled equilibrium-split example (3 demes of 200, haploid). Its
    `tau` is exactly the one-deme recursion's (both are the textbook
    `1 / (1 - (1 - 1/N)(1 - mu)^2)`), and its expected heterozygosity is
    close to `theta / (1 + theta)`, `theta = 2 N mu = 1.2`.
    """
    result = panmictic_equilibration(
        total_size=600, mutation_rates=[0.001], tolerance=0.01
    )
    one_deme = recursion_relaxation_time(
        deme_sizes=[600], migration=[[1.0]], mutation=0.001
    )

    assert result.relaxation_time == pytest.approx(one_deme, rel=1e-12)
    assert result.relaxation_time == pytest.approx(273.0, abs=0.5)
    assert result.generations == math.ceil(
        math.log(0.01) / math.log(1.0 - 1.0 / result.relaxation_time)
    )
    assert result.generations == pytest.approx(
        result.relaxation_time * math.log(100.0), rel=0.01
    )
    assert result.expected_heterozygosity == pytest.approx(1.2 / 2.2, rel=0.01)


def test_panmictic_burn_in_bounds_the_identity_recursion_from_any_start() -> None:
    """After the burn-in, the expected identity is within tolerance of F*.

    Iterates the textbook expected recursion (drift, then each copy
    mutates: `F' = 1/N + (1 - 1/N)(1 - mu)^2 F`) from both extremes
    (every copy identical, and none) and checks the departure at the
    burn-in.
    """
    size, rate, tolerance = 40, 0.02, 0.01
    result = panmictic_equilibration(
        total_size=size, mutation_rates=[rate], tolerance=tolerance
    )
    survival = 1.0 - 1.0 / size
    keep = (1.0 - rate) ** 2
    equilibrium = 1.0 - result.expected_heterozygosity
    for start in (0.0, 1.0):
        identity = start
        for _ in range(result.generations):
            identity = 1.0 / size + survival * keep * identity
        assert abs(identity - equilibrium) <= tolerance


def test_panmictic_burn_in_follows_the_slowest_locus_and_rejects_bad_input() -> None:
    """The smallest rate sets the burn-in; a zero tolerance is refused."""
    mixed = panmictic_equilibration(
        total_size=100, mutation_rates=[0.05, 0.001], tolerance=0.01
    )
    slow = panmictic_equilibration(
        total_size=100, mutation_rates=[0.001], tolerance=0.01
    )

    assert mixed.generations == slow.generations
    assert (
        panmictic_equilibration(
            total_size=100, mutation_rates=[0.01], tolerance=1.0
        ).generations
        == 0
    )
    with pytest.raises(ValueError, match="greater than 0"):
        panmictic_equilibration(total_size=100, mutation_rates=[0.01], tolerance=0.0)


def test_derived_convergence_sentence_names_window_cap_and_time() -> None:
    """The shared sentence carries all three numbers, with thousands separators."""
    text = describe_derived_convergence(
        window=59_078, max_generations=295_390, relaxation_time=19_693.4
    )

    assert text == (
        "Convergence: window 59,078 generations, cap 295,390 (derived; this "
        "model needs about 19,693 generations to forget its starting state)"
    )
