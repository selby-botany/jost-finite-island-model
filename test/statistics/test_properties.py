"""Property-based tests for differentiation identities and bounds."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from fim.statistics import (
    e_st,
    g_st,
    h_s,
    h_st,
    h_t,
    jost_d,
    k_st,
    total_hill_number,
    within_hill_number,
)


@st.composite
def frequency_tables(
    draw: st.DrawFn,
) -> list[dict[int, float]]:
    """Generate valid, bounded per-deme frequency maps."""
    deme_count = draw(st.integers(min_value=2, max_value=5))
    allele_count = draw(st.integers(min_value=1, max_value=6))
    table: list[dict[int, float]] = []
    for _deme in range(deme_count):
        first = draw(st.integers(min_value=1, max_value=20))
        rest = draw(
            st.lists(
                st.integers(min_value=0, max_value=20),
                min_size=allele_count - 1,
                max_size=allele_count - 1,
            )
        )
        counts = [first, *rest]
        total = sum(counts)
        table.append(
            {
                allele_id: count / total
                for allele_id, count in enumerate(counts)
                if count
            }
        )
    return table


@given(frequency_tables())
def test_heterozygosity_partition_and_ceiling(
    table: list[dict[int, float]],
) -> None:
    """H_T, H_S, H_ST, and G_ST obey the Part V identities."""
    within = h_s(table)
    total = h_t(table)
    between = h_st(table)
    fixation = g_st(table)

    assert total >= within - 1e-12
    assert total == pytest.approx(within + between - within * between)
    if fixation is not None:
        assert fixation <= 1.0 - within + 1e-12


@given(frequency_tables())
def test_all_differentiation_measures_are_bounded(
    table: list[dict[int, float]],
) -> None:
    """Every defined differentiation measure remains in the unit interval."""
    values = [jost_d(table), e_st(table), k_st(table)]
    fixation = g_st(table)
    if fixation is not None:
        values.append(fixation)

    assert all(0.0 <= value <= 1.0 for value in values)


@given(frequency_tables())
def test_replication_principle_doubles_q2_beta_diversity(
    table: list[dict[int, float]],
) -> None:
    """An equally diverse disjoint copy doubles total/within Hill diversity."""
    allele_offset = 100
    duplicated = [
        *table,
        *[
            {allele + allele_offset: frequency for allele, frequency in deme.items()}
            for deme in table
        ],
    ]
    original_beta = total_hill_number(table, 2) / within_hill_number(table, 2)
    duplicated_beta = total_hill_number(
        duplicated,
        2,
    ) / within_hill_number(duplicated, 2)

    assert duplicated_beta == pytest.approx(2.0 * original_beta)


def test_jost_d_endpoints_are_exact() -> None:
    """D is zero for identical demes and one for disjoint demes."""
    assert jost_d([{0: 0.25, 1: 0.75}, {0: 0.25, 1: 0.75}]) == 0.0
    assert jost_d([{0: 0.5, 1: 0.5}, {2: 0.5, 3: 0.5}]) == 1.0
