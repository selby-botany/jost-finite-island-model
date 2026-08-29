"""Deterministic validation against published finite-island scenarios."""

import pytest

from fim.statistics import equilibrium_d, equilibrium_g_st


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
