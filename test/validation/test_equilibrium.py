"""Deterministic validation against published finite-island scenarios."""

import math

import pytest

from fim.statistics import (
    equilibrium_d,
    equilibrium_g_st,
    equilibrium_shannon_differentiation,
    equilibrium_shannon_entropy_isolated,
    equilibrium_shannon_entropy_isolated_smm,
    equilibrium_shannon_entropy_subpopulation,
    equilibrium_shannon_entropy_total,
)
from fim.statistics.differentiation import _EULER_GAMMA, _digamma


@pytest.mark.parametrize(
    ("population_size", "d", "m", "mu", "expected_g_st", "expected_d"),
    [
        (100, 5, 0.0001, 0.000001, 0.97, 0.04),
        (2000, 100, 0.01, 0.001, 0.02, 0.91),
    ],
)
def test_dear_nolan_scenarios_match_published_approximations(
    population_size: int,
    d: int,
    m: float,
    mu: float,
    expected_g_st: float,
    expected_d: float,
) -> None:
    """The gene-copy formulas reproduce both documented scenario values."""
    assert equilibrium_g_st(population_size, m, mu, d) == pytest.approx(
        expected_g_st,
        abs=0.015,
    )
    assert equilibrium_d(m, mu, d) == pytest.approx(expected_d, abs=0.015)


def test_part_iv_dynamic_example_matches_the_guide() -> None:
    """The Part IV "dynamic example" bottleneck scenario, both endpoints.

    `doc/jost-differentiation-measures.md` Part IV: a 100-deme population
    (`d = 100`) recovers from a bottleneck to `N = 10,000` diploid
    individuals each (20,000 gene copies) with `mu = 0.001` and zero
    migration. The guide's own equilibrium numbers: `D -> 1.00 "exactly"`
    (quoting Part VI's own worked sanity check, "with no migration
    (m = 0), D = 1") and `G_ST -> 1/41.4 ~= 0.0242`.
    """
    assert equilibrium_d(0.0, 0.001, 100) == 1.0
    assert equilibrium_g_st(20_000, 0.0, 0.001, 100) == pytest.approx(
        0.0242, abs=0.0005
    )


def test_gene_copy_convention_uses_two_n_not_four_n() -> None:
    """Haploid N=100 plugs in directly without a ploidy conversion."""
    observed = equilibrium_g_st(100, 0.0001, 0.000001, 5)
    diploid_individual_formula = 1.0 / (
        (5 / 4) ** 2 * 4 * 100 * 0.0001 + (5 / 4) * 4 * 100 * 0.000001 + 1
    )

    assert observed > diploid_individual_formula
    assert observed == pytest.approx(0.97, abs=0.01)


@pytest.mark.parametrize(
    ("x", "expected"),
    [
        (1.0, -_EULER_GAMMA),
        (2.0, 1.0 - _EULER_GAMMA),
        (3.0, 1.0 + 1.0 / 2.0 - _EULER_GAMMA),
        (5.0, 1.0 + 1.0 / 2.0 + 1.0 / 3.0 + 1.0 / 4.0 - _EULER_GAMMA),
        (0.5, -_EULER_GAMMA - 2.0 * math.log(2.0)),
        (1.5, 2.0 - _EULER_GAMMA - 2.0 * math.log(2.0)),
    ],
)
def test_digamma_matches_known_closed_forms(x: float, expected: float) -> None:
    """`_digamma` matches the textbook exact values at integers and halves.

    Integers: `psi(n) = H_{n-1} - gamma`, the `n-1`-th harmonic number
    minus the Euler-Mascheroni constant (the recurrence `_digamma`
    itself uses to shift small arguments, unrolled by hand here as an
    independent check rather than trusted circularly). Half-integers:
    `psi(1/2) = -gamma - 2*ln(2)` and `psi(3/2) = psi(1/2) + 2`, the two
    other closed forms every digamma reference table starts with.
    """
    assert _digamma(x) == pytest.approx(expected, abs=1e-8)


def test_digamma_rejects_non_positive_and_invalid_input() -> None:
    """`_digamma` is only defined here for a positive, finite `x`."""
    for bad in (0.0, -1.0, math.inf, math.nan):
        with pytest.raises(ValueError, match="positive"):
            _digamma(bad)


@pytest.mark.parametrize(
    ("population_size", "mu", "expected"),
    [
        # theta = 2*N*mu = 1 -> psi(2) + gamma = H_1 = 1.0 exactly.
        (1, 0.5, 1.0),
        # theta = 2*N*mu = 2 -> psi(3) + gamma = H_2 = 1.5 exactly.
        (1, 1.0, 1.5),
        (2, 0.5, 1.5),
    ],
)
def test_equilibrium_shannon_entropy_isolated_matches_harmonic_numbers(
    population_size: int, mu: float, expected: float
) -> None:
    """Chao et al. (2015) Eq. 2A at integer `theta`, an exact harmonic number.

    `psi(n) + gamma = H_{n-1}` for a positive integer `n` -- chosen
    `(population_size, mu)` pairs here land `theta = 2*population_size*mu`
    on exactly `0` or `1`, both cases where the equilibrium entropy has
    a clean closed form independent of any digamma-table lookup, unlike
    `test_digamma_matches_known_closed_forms`'s own values (which this
    test does not reuse, so the two together do not share a single
    point of failure).
    """
    assert equilibrium_shannon_entropy_isolated(population_size, mu) == pytest.approx(
        expected, abs=1e-8
    )


def test_equilibrium_shannon_entropy_is_increasing_in_mutation() -> None:
    """More mutation never lowers the equilibrium Shannon entropy (IAM or SMM).

    Same direction as `equilibrium_d`'s own migration-mutation-ratio
    result (Part VI): more mutation drives more standing diversity at
    equilibrium, in the same way a locus with a higher mutation rate
    supports more distinct alleles. Checked for both mutation models
    together since they share the same underlying `theta`.
    """
    population_size = 100
    low, high = (
        equilibrium_shannon_entropy_isolated(population_size, 0.0001),
        equilibrium_shannon_entropy_isolated(population_size, 0.01),
    )
    assert low < high

    low_smm, high_smm = (
        equilibrium_shannon_entropy_isolated_smm(population_size, 0.0001),
        equilibrium_shannon_entropy_isolated_smm(population_size, 0.01),
    )
    assert low_smm < high_smm


def test_equilibrium_shannon_entropy_smm_reduces_toward_iam_as_alpha_shrinks() -> None:
    """SMM entropy approaches the IAM value as `alpha` (and `theta`) shrink.

    Chao et al. (2015) state this "bridge" explicitly: SMM's own `alpha`
    parameter tends to 0 as `theta` does, at which point Eq. 5A reduces
    exactly to Eq. 2A. Checked here as a convergence trend (SMM and IAM
    values move closer together as `theta` shrinks) rather than an exact
    equality at any single point, since `alpha` is strictly positive for
    every `theta > 0`, however small.
    """
    population_size = 100
    gaps = [
        abs(
            equilibrium_shannon_entropy_isolated_smm(population_size, mu)
            - equilibrium_shannon_entropy_isolated(population_size, mu)
        )
        for mu in (0.01, 0.0001, 0.000001)
    ]
    assert gaps == sorted(gaps, reverse=True)
    assert gaps[-1] < 1e-4


def test_equilibrium_shannon_entropy_rejects_zero_mutation() -> None:
    """An isolated deme with `mu=0` never reaches a polymorphic equilibrium."""
    with pytest.raises(ValueError, match="mu greater than 0"):
        equilibrium_shannon_entropy_isolated(100, 0.0)
    with pytest.raises(ValueError, match="mu greater than 0"):
        equilibrium_shannon_entropy_isolated_smm(100, 0.0)
    with pytest.raises(ValueError, match="mu greater than 0"):
        equilibrium_shannon_entropy_total(100, 0.01, 0.0, 4)


def test_equilibrium_shannon_entropy_isolated_validates_its_inputs() -> None:
    """`_validate_isolated_equilibrium_inputs` rejects a bad `N` or `mu`.

    The isolated-population counterpart to `equilibrium_g_st`/
    `equilibrium_d`'s own input validation, checked the same way.
    """
    for bad_population_size in (0, -1, 1.5, True):
        with pytest.raises(ValueError, match="positive gene-copy count"):
            equilibrium_shannon_entropy_isolated(bad_population_size, 0.001)  # type: ignore[arg-type]
    for bad_mu in (-0.1, 1.1, math.inf, True):
        with pytest.raises(ValueError, match="mu must be between 0 and 1"):
            equilibrium_shannon_entropy_isolated(100, bad_mu)


def test_equilibrium_shannon_entropy_total_exceeds_isolated_with_more_demes() -> None:
    """Pooling more demes together can only add entropy, never remove it.

    The same "pooling can only add diversity" property Part V's
    replication principle already establishes for heterozygosity-based
    measures (`fim.statistics.differentiation`'s own module docstring),
    checked here on the total-population Shannon entropy instead: for
    fixed `N`, `m`, `mu`, more demes pooled together means strictly more
    standing diversity at equilibrium, never less.
    """
    values = [
        equilibrium_shannon_entropy_total(100, 0.01, 0.001, d) for d in (2, 4, 10, 50)
    ]
    assert values == sorted(values)


@pytest.mark.parametrize(
    ("population_size", "m", "mu", "d"),
    [
        (100, 0.01, 0.001, 4),
        (2000, 0.01, 0.001, 100),
        (100, 0.0001, 0.000001, 5),
        # d=2: Chao et al.'s own stated weak spot for Eq. 7D (see
        # `equilibrium_shannon_entropy_subpopulation`'s own docstring) --
        # included deliberately, not avoided, so the bounds property is
        # checked at the approximation's own least reliable point too.
        (5000, 0.15, 0.0022, 2),
    ],
)
def test_equilibrium_shannon_entropy_subpopulation_never_exceeds_total(
    population_size: int, m: float, mu: float, d: int
) -> None:
    """A subpopulation can never hold more diversity than the whole population.

    The same "pooling can only add diversity" direction as
    `test_equilibrium_shannon_entropy_total_exceeds_isolated_with_more_
    demes`, above, checked here between a single subpopulation and the
    total population it is part of rather than between demes counts.
    """
    total = equilibrium_shannon_entropy_total(population_size, m, mu, d)
    subpopulation = equilibrium_shannon_entropy_subpopulation(population_size, m, mu, d)
    assert subpopulation <= total


@pytest.mark.parametrize(
    ("population_size", "m", "mu", "d"),
    [
        (100, 0.01, 0.001, 4),
        (2000, 0.01, 0.001, 100),
        (100, 0.0001, 0.000001, 5),
    ],
)
def test_equilibrium_shannon_differentiation_is_bounded(
    population_size: int, m: float, mu: float, d: int
) -> None:
    """Shannon differentiation stays in `[0, 1]`, the same range `D`/`G_ST` share.

    Chao et al. (2015) state this directly: zero when every
    subpopulation is identical to the whole, one when subpopulations
    share no alleles at all. Checked over three scenarios spanning very
    different `Nm`/`Nmu` regimes, deliberately excluding `d=2` here (see
    `equilibrium_shannon_entropy_subpopulation`'s own docstring for why
    that specific case gets its own, separately-bounded check below
    rather than being folded into this tight one).
    """
    value = equilibrium_shannon_differentiation(population_size, m, mu, d)
    assert 0.0 <= value <= 1.0


def test_equilibrium_shannon_differentiation_at_two_demes_is_roughly_bounded() -> None:
    """At `d=2`, Eq. 7D's own approximation error can push slightly outside `[0, 1]`.

    Chao et al. (2015) state their own approximation formula (Eq. 7D,
    behind `equilibrium_shannon_entropy_subpopulation`) is unreliable
    specifically "for the special case of two subpopulations" -- their
    own words, not a caveat invented here. Checked with a deliberately
    loose bound rather than the tight `[0, 1]` the three-or-more-deme
    scenarios get, so a real regression (the approximation becoming
    *badly* wrong, not just imprecise at its own documented weak point)
    would still be caught.
    """
    value = equilibrium_shannon_differentiation(5000, 0.15, 0.0022, 2)
    assert -0.05 <= value <= 1.05


def test_equilibrium_shannon_differentiation_is_increasing_in_mutation() -> None:
    """More mutation drives more equilibrium Shannon differentiation.

    Part VI's own "Why this kills the standard inference" pattern
    (`equilibrium_d`'s own docstring), extended to the Shannon-entropy
    family: Fig. 1/Fig. 2 of Chao et al. (2015) show Shannon
    differentiation and Jost's `D` "always exhibit consistent
    patterns" -- both increasing in `mu`, both decreasing in `m` (the
    next test, below) -- stated in the paper as a qualitative figure,
    checked here as an executable property instead.
    """
    values = [
        equilibrium_shannon_differentiation(100, 0.01, mu, 4)
        for mu in (0.0001, 0.001, 0.01)
    ]
    assert values == sorted(values)


def test_equilibrium_shannon_differentiation_is_decreasing_in_migration() -> None:
    """More migration lowers equilibrium Shannon differentiation.

    The migration-direction half of the same Fig. 1/Fig. 2 pattern the
    mutation-direction test above checks.
    """
    values = [
        equilibrium_shannon_differentiation(100, m, 0.001, 4)
        for m in (0.001, 0.01, 0.1)
    ]
    assert values == sorted(values, reverse=True)


def test_equilibrium_shannon_entropy_subpopulation_rejects_zero_mutation() -> None:
    """A FIM subpopulation with `mu=0` never reaches a polymorphic equilibrium."""
    with pytest.raises(ValueError, match="mu greater than 0"):
        equilibrium_shannon_entropy_subpopulation(100, 0.01, 0.0, 4)
