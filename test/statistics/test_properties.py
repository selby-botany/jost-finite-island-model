"""Property-based tests for differentiation identities and bounds."""

import random

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from fim.statistics import (
    e_st,
    equilibrium_d,
    equilibrium_g_st,
    g_st,
    h_s,
    h_st,
    h_t,
    jost_d,
    k_st,
    total_hill_number,
    within_hill_number,
)
from fim.statistics.differentiation import statistics_report


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


@st.composite
def extreme_concentration_frequency_tables(
    draw: st.DrawFn,
) -> list[dict[int, float]]:
    """Generate near-fixation frequency tables from a tiny-concentration draw.

    `frequency_tables`, above, only ever draws integer counts up to `20`
    (frequencies no closer than `0.01` to `0`/`1`): the 2026-10-25 project
    review's finding S-1 traces `_bounded`'s absolute-tolerance bug to
    exactly the gap this strategy fills, frequencies from `1e-12` up to
    `1`, via the same `Gamma(concentration, 1)` construction
    `DirichletInitialCondition.generate` uses for
    `initial_concentration` (`doc/configuration.md`'s own "smaller values
    are more uneven", down to values the fixture above never reaches).
    `random.Random` rather than a Hypothesis float/integer strategy: a
    Dirichlet draw has no natural shrinkable encoding of its own, so a
    Hypothesis-drawn seed runs an ordinary gamma sampler deterministically
    instead.
    """
    deme_count = draw(st.integers(min_value=2, max_value=4))
    allele_count = draw(st.integers(min_value=2, max_value=4))
    concentration = draw(st.floats(min_value=1e-6, max_value=0.2, allow_nan=False))
    seed = draw(st.integers(min_value=0, max_value=2**32 - 1))
    rng = random.Random(seed)
    table: list[dict[int, float]] = []
    for _deme in range(deme_count):
        shares = [
            max(rng.gammavariate(concentration, 1.0), 1e-300)
            for _ in range(allele_count)
        ]
        total = sum(shares)
        table.append(
            {allele_id: share / total for allele_id, share in enumerate(shares)}
        )
    return table


@st.composite
def many_allele_frequency_tables(draw: st.DrawFn) -> list[dict[int, float]]:
    """Generate two disjoint demes with at least `10**4` alleles each.

    The 2026-10-25 project review's finding S-1 also reproduces through
    $D$'s `/ (1 - H_S)` denominator at a large allele count with no
    near-fixation frequency anywhere (`H_S` itself approaches `1` simply
    because there are many alleles, each individually rare) — a second,
    independent route to the same tiny-denominator regime, worth its own
    case alongside `extreme_concentration_frequency_tables`.
    """
    allele_count = draw(st.integers(min_value=10_000, max_value=15_000))
    seed = draw(st.integers(min_value=0, max_value=2**32 - 1))
    rng = random.Random(seed)
    table: list[dict[int, float]] = []
    for deme_index in range(2):
        counts = [rng.randint(1, 100) for _ in range(allele_count)]
        total = sum(counts)
        offset = deme_index * allele_count
        table.append(
            {
                allele_id + offset: count / total
                for allele_id, count in enumerate(counts)
            }
        )
    return table


@given(extreme_concentration_frequency_tables())
def test_statistics_report_tolerates_near_fixation_roundoff(
    table: list[dict[int, float]],
) -> None:
    """A tiny-concentration frequency table never raises `ArithmeticError`.

    Regression test for the 2026-10-25 project review's finding S-1:
    before `_bounded`'s conditioning-aware tolerance, a `G_ST`, `H_ST`,
    or `D` computed from a table this close to fixation could raise
    instead of returning a bounded statistic.
    """
    report = statistics_report(table)
    assert 0.0 <= report["H_S"] <= 1.0
    assert 0.0 <= report["H_T"] <= 1.0
    assert 0.0 <= report["H_ST"] <= 1.0
    assert 0.0 <= report["D"] <= 1.0
    if report["G_ST"] is not None:
        assert 0.0 <= report["G_ST"] <= 1.0


@settings(max_examples=15, deadline=None)
@given(many_allele_frequency_tables())
def test_statistics_report_tolerates_many_alleles_near_full_divergence(
    table: list[dict[int, float]],
) -> None:
    """Thousands of per-deme-private alleles never raise `ArithmeticError`.

    Regression test for the 2026-10-25 project review's finding S-1's $D$
    reproduction: two demes sharing no allele push `H_S` to within a few
    `ULP`s of `1`, so $D$'s `/ (1 - H_S)` denominator shrinks toward zero
    even though every frequency involved is an ordinary, non-extreme
    value. `max_examples` lowered and `deadline` disabled because each
    example samples and normalizes 20,000-30,000 per-allele counts, not
    because the property itself is expensive to check.
    """
    report = statistics_report(table)
    assert 0.0 <= report["H_ST"] <= 1.0
    assert 0.0 <= report["D"] <= 1.0


@given(frequency_tables())
def test_heterozygosity_partition_and_ceiling(
    table: list[dict[int, float]],
) -> None:
    """$H_T$, $H_S$, $H_{ST}$, and $G_{ST}$ obey the Part V identities."""
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


@given(frequency_tables(), st.data())
def test_differentiation_statistics_are_invariant_to_deme_order(
    table: list[dict[int, float]],
    data: st.DataObject,
) -> None:
    """Reordering demes changes none of the four equal-weighted measures.

    Every equal-weighted differentiation measure treats a frequency table as
    an unordered collection of demes — which physical deme happens to be
    listed first is bookkeeping, never a scientific input. Compared with
    `pytest.approx` rather than `==`: the *mathematical* sums involved are
    order-independent, but `_pooled` (`fim.statistics.differentiation`)
    accumulates each allele's pooled frequency with an ordinary running
    `+=` rather than `math.fsum`, so a different deme visiting order can
    legitimately land the pooled float a few ULPs away from the original —
    a real, if tiny, floating-point order-dependence, not a defect this
    test is trying to catch. Draws an arbitrary permutation via Hypothesis
    rather than only reversing the list, so shrinking can still find a
    *minimal* reordering that breaks the property, not just the one
    reordering a hand-written test thought to try.
    """
    permutation = data.draw(st.permutations(range(len(table))))
    reordered = [table[index] for index in permutation]

    assert jost_d(reordered) == pytest.approx(jost_d(table))
    assert e_st(reordered) == pytest.approx(e_st(table))
    assert k_st(reordered) == pytest.approx(k_st(table))
    assert g_st(reordered) == pytest.approx(g_st(table))


@given(frequency_tables(), st.data())
def test_differentiation_statistics_are_invariant_to_allele_relabeling(
    table: list[dict[int, float]],
    data: st.DataObject,
) -> None:
    """A single consistent relabeling of allele identities changes nothing.

    Allele IDs are bookkeeping labels only, never mathematically meaningful
    themselves (`fim.statistics.differentiation`'s own module docstring) —
    only whether two entries across demes share an identity or not. Shifting
    every allele id in the whole table by the same offset is one such
    relabeling: it preserves which demes share which allele while changing
    every id's actual numeric value, so every equal-weighted measure must
    come out identical.
    """
    offset = data.draw(st.integers(min_value=1, max_value=10_000))
    relabeled = [
        {allele_id + offset: frequency for allele_id, frequency in deme.items()}
        for deme in table
    ]

    assert jost_d(relabeled) == jost_d(table)
    assert e_st(relabeled) == e_st(table)
    assert k_st(relabeled) == k_st(table)
    assert g_st(relabeled) == g_st(table)


@given(
    d=st.integers(min_value=2, max_value=50),
    mu=st.floats(min_value=1e-6, max_value=1.0, allow_nan=False),
    m_values=st.lists(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        min_size=2,
        max_size=2,
    ),
)
def test_equilibrium_d_and_g_st_are_non_increasing_in_migration(
    d: int,
    mu: float,
    m_values: list[float],
) -> None:
    """More migration never raises equilibrium $D$ or $G_{ST}$ (fixed mu, d).

    Direct, exact-algebra check of Part VI's own "Equilibrium D" and
    "Equilibrium GST" sections: both closed forms are monotonic
    non-increasing functions of `m` alone (more migration homogenizes
    demes, so neither differentiation measure can increase) — checked
    here on the closed forms themselves, not a stochastic run of any
    kind, so this can never flake and costs nothing to run. Was
    previously untested: no monotonicity check of any kind existed
    anywhere in this suite before this test (test-manifest.yaml's own
    `M1`-`M5` panel proposed the same underlying claim, but as a chain
    of single-seeded engine runs compared with a fixed `1e-9`
    tolerance — a design this project's own determinism rule rules
    out, since it compares single stochastic realizations rather than
    expectations, and each of the five runs would need its own
    independent convergence horizon rather than a shared one).
    """
    m_lo, m_hi = sorted(m_values)
    population_size = 100

    assert equilibrium_d(m_lo, mu, d) >= equilibrium_d(m_hi, mu, d)
    assert equilibrium_g_st(population_size, m_lo, mu, d) >= equilibrium_g_st(
        population_size, m_hi, mu, d
    )


@given(
    d=st.integers(min_value=2, max_value=50),
    m=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    mu_values=st.lists(
        st.floats(min_value=1e-6, max_value=1.0, allow_nan=False),
        min_size=2,
        max_size=2,
    ),
)
def test_equilibrium_d_and_g_st_move_opposite_ways_in_mutation(
    d: int,
    m: float,
    mu_values: list[float],
) -> None:
    """$D$ rises and $G_{ST}$ falls together as mutation rate rises (fixed m, d).

    Part VI's own headline point ("Why this kills the standard
    inference"): allelic differentiation ($D$) and nearness-to-fixation
    ($G_{ST}$) respond to mutation rate in *opposite* directions — more
    mutation drives more allelic differentiation between demes ($D$
    rises toward 1) even as it moves every deme further from fixation
    ($G_{ST}$ falls). This test is the executable version of that
    section's own prose, not a restatement of it.
    """
    mu_lo, mu_hi = sorted(mu_values)
    population_size = 100

    assert equilibrium_d(m, mu_lo, d) <= equilibrium_d(m, mu_hi, d)
    assert equilibrium_g_st(population_size, m, mu_lo, d) >= equilibrium_g_st(
        population_size, m, mu_hi, d
    )


@given(
    population_size=st.integers(min_value=1, max_value=5000),
    d=st.integers(min_value=2, max_value=50),
    mu=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    m_values=st.lists(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        min_size=2,
        max_size=2,
    ),
)
def test_aoki_2023_claim_4_g_st_monotonicity_in_migration(
    population_size: int,
    d: int,
    mu: float,
    m_values: list[float],
) -> None:
    """$G_{ST}$ monotonically decreases with increasing migration rate.

    Aoki et al. (2023) §5.3 Claim 4: $G_{ST}$ responds in a strictly downward
    direction to higher gene flow (m). Tested as a pure property test across
    arbitrary population sizes, deme counts, and mutation rates against
    equilibrium_g_st.
    """
    m_lo, m_hi = sorted(m_values)
    g_st_lo = equilibrium_g_st(population_size, m_lo, mu, d)
    g_st_hi = equilibrium_g_st(population_size, m_hi, mu, d)
    assert g_st_lo >= g_st_hi
