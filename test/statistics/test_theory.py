"""Tests for `fim.statistics.theory`, the closed-form predictions.

The expected values come from the differentiation module's own formulas, so
these tests pin the plumbing (which inputs reach which formula, and when a
prediction is undefined), not the population genetics.
"""

from __future__ import annotations

from math import isclose

import pytest

from fim.statistics.differentiation import (
    equilibrium_d,
    equilibrium_g_st,
    equilibrium_heterozygosity_isolated,
)
from fim.statistics.theory import (
    THEORY_STATISTICS,
    equilibrium_numeric_predictions,
    theory_at,
    theory_for_configuration,
)

_BASE = {"N": 450, "ploidy": "haploid", "d": 20, "m": 0.001, "mu": 3e-05}


def test_every_predicted_statistic_is_in_the_numeric_predictions() -> None:
    predictions = equilibrium_numeric_predictions(450, 0.001, 3e-05, 20)

    assert set(THEORY_STATISTICS) <= set(predictions)


def test_a_configuration_gets_the_formulas_values() -> None:
    result = theory_for_configuration(_BASE)

    assert result["D"] == equilibrium_d(0.001, 3e-05, 20)
    assert result["G_ST"] == equilibrium_g_st(450, 0.001, 3e-05, 20)
    assert result["H_S"] == equilibrium_heterozygosity_isolated(450, 3e-05)
    assert set(result) == set(THEORY_STATISTICS)


@pytest.mark.parametrize(
    ("word", "copies"),
    [("haploid", 1), ("diploid", 2), ("triploid", 3), ("tetraploid", 4)],
)
def test_n_counts_individuals_and_the_formulas_see_gene_copies(
    word: str, copies: int
) -> None:
    result = theory_for_configuration({**_BASE, "N": 225, "ploidy": word})

    assert result["G_ST"] == equilibrium_g_st(225 * copies, 0.001, 3e-05, 20)
    assert result["H_S"] == equilibrium_heterozygosity_isolated(225 * copies, 3e-05)


def test_diploid_theory_differs_from_the_same_n_haploid() -> None:
    haploid = theory_for_configuration({**_BASE, "N": 225})
    diploid = theory_for_configuration({**_BASE, "N": 225, "ploidy": "diploid"})

    assert haploid["G_ST"] != diploid["G_ST"]
    assert haploid["D"] == diploid["D"]  # D has no N term


def test_integer_valued_floats_are_accepted() -> None:
    result = theory_for_configuration({**_BASE, "N": 450.0, "d": 20.0})

    assert result == theory_for_configuration(_BASE)


@pytest.mark.parametrize(
    "configuration",
    [
        {"N": 450, "d": 20, "m": 0.001},
        {**_BASE, "N": 0},
        {**_BASE, "d": 1},
        {**_BASE, "m": "high"},
        {**_BASE, "N": True},
        {**_BASE, "ploidy": "pentaploid"},
        {"N": 450, "d": 20, "m": 0.001, "mu": 3e-05},
    ],
)
def test_a_configuration_the_formulas_do_not_cover_is_all_undefined(
    configuration: dict[str, object],
) -> None:
    assert theory_for_configuration(configuration) == dict.fromkeys(THEORY_STATISTICS)


def test_zero_mutation_leaves_only_the_mutation_free_statistics_undefined() -> None:
    result = theory_for_configuration({**_BASE, "mu": 0.0})

    assert result["D"] is None
    assert set(result) == set(THEORY_STATISTICS)


def test_theory_at_applies_coordinates_to_the_base() -> None:
    at_coordinates = theory_at(_BASE, {"d": 10, "m": 0.01})

    assert at_coordinates == theory_for_configuration({**_BASE, "d": 10, "m": 0.01})
    assert isclose(at_coordinates["D"] or 0.0, equilibrium_d(0.01, 3e-05, 10))


def test_an_axis_the_base_cannot_take_is_all_undefined() -> None:
    without_migration = {"N": 450, "ploidy": "haploid", "d": 20, "mu": 3e-05}

    assert theory_at(without_migration, {"m": 0.01}) == dict.fromkeys(THEORY_STATISTICS)
