"""Focused tests for pairwise genetic distance and identity (Nei 1972)."""

from __future__ import annotations

import math
import unittest

from hypothesis import given
from hypothesis import strategies as st

from fim.statistics import (
    cross_identity,
    identity,
    nei_d,
    nei_d_prime,
    nei_founder_identity,
    nei_geometric_distance,
    nei_geometric_identity,
    nei_identity,
    nei_mean_distance,
    nei_standard_distance,
)


@st.composite
def random_locus_profile(
    draw: st.DrawFn,
    locus_count: int,
    max_alleles: int = 5,
) -> list[dict[int, float]]:
    """Generate a valid multi-locus profile for one population."""
    profile: list[dict[int, float]] = []
    for _ in range(locus_count):
        k = draw(st.integers(min_value=1, max_value=max_alleles))
        counts = [draw(st.integers(min_value=1, max_value=20)) for _ in range(k)]
        total = sum(counts)
        profile.append(
            {allele_id: count / total for allele_id, count in enumerate(counts)}
        )
    return profile


class GeneticDistanceTests(unittest.TestCase):
    """Verify formulas, bounds, literature regression, and input validation."""

    def test_claim_1_identity_endpoint(self) -> None:
        """Claim 1: Identical frequencies at every locus imply D = 0 and I = 1."""
        # Single locus cases
        monomorphic = {0: 1.0}
        polymorphic = {0: 0.2, 1: 0.5, 2: 0.3}

        for locus in [monomorphic, polymorphic]:
            self.assertAlmostEqual(cross_identity(locus, locus), identity(locus))
            self.assertEqual(nei_identity(locus, locus), 1.0)
            self.assertEqual(nei_standard_distance(locus, locus), 0.0)
            self.assertEqual(nei_d(locus, locus), 0.0)
            self.assertEqual(nei_geometric_identity(locus, locus), 1.0)
            self.assertEqual(nei_geometric_distance(locus, locus), 0.0)
            self.assertEqual(nei_mean_distance(locus, locus), 0.0)
            self.assertEqual(nei_d_prime(locus, locus), 0.0)

        # Multi-locus cases (L = 3)
        loci_x = [
            {0: 0.6, 1: 0.4},
            {0: 1.0},
            {0: 0.1, 1: 0.2, 2: 0.7},
        ]
        loci_y = [dict(locus) for locus in loci_x]

        self.assertEqual(nei_identity(loci_x, loci_y), 1.0)
        self.assertEqual(nei_standard_distance(loci_x, loci_y), 0.0)
        self.assertEqual(nei_d(loci_x, loci_y), 0.0)
        self.assertEqual(nei_geometric_identity(loci_x, loci_y), 1.0)
        self.assertEqual(nei_geometric_distance(loci_x, loci_y), 0.0)
        self.assertEqual(nei_mean_distance(loci_x, loci_y), 0.0)
        self.assertEqual(nei_d_prime(loci_x, loci_y), 0.0)

    def test_claim_2_disjoint_support_endpoint(self) -> None:
        """Claim 2: No shared alleles at any locus implies I = 0 and D = +inf."""
        # Single locus completely disjoint
        pop_x = {0: 1.0}
        pop_y = {1: 1.0}

        self.assertEqual(cross_identity(pop_x, pop_y), 0.0)
        self.assertEqual(nei_identity(pop_x, pop_y), 0.0)
        self.assertEqual(nei_standard_distance(pop_x, pop_y), math.inf)
        self.assertEqual(nei_d(pop_x, pop_y), math.inf)
        self.assertEqual(nei_geometric_identity(pop_x, pop_y), 0.0)
        self.assertEqual(nei_geometric_distance(pop_x, pop_y), math.inf)
        self.assertEqual(nei_mean_distance(pop_x, pop_y), math.inf)
        self.assertEqual(nei_d_prime(pop_x, pop_y), math.inf)

        # Multi-locus: all loci disjoint
        multi_x = [{0: 0.5, 1: 0.5}, {0: 1.0}]
        multi_y = [{2: 0.3, 3: 0.7}, {1: 1.0}]

        self.assertEqual(nei_identity(multi_x, multi_y), 0.0)
        self.assertEqual(nei_standard_distance(multi_x, multi_y), math.inf)
        self.assertEqual(nei_geometric_identity(multi_x, multi_y), 0.0)
        self.assertEqual(nei_geometric_distance(multi_x, multi_y), math.inf)
        self.assertEqual(nei_mean_distance(multi_x, multi_y), math.inf)

        # Multi-locus: 1 shared locus, 1 disjoint locus
        # D is finite because J_XY > 0, but D' is infinite because I_2 = 0
        part_x = [{0: 0.5, 1: 0.5}, {0: 1.0}]
        part_y = [{0: 0.5, 1: 0.5}, {1: 1.0}]

        i_standard = nei_identity(part_x, part_y)
        d_standard = nei_standard_distance(part_x, part_y)
        self.assertGreater(i_standard, 0.0)
        self.assertLess(i_standard, 1.0)
        self.assertTrue(math.isfinite(d_standard))
        self.assertGreater(d_standard, 0.0)

        # D' requires every locus to share alleles; one disjoint locus yields +inf
        self.assertEqual(nei_geometric_identity(part_x, part_y), 0.0)
        self.assertEqual(nei_geometric_distance(part_x, part_y), math.inf)
        self.assertEqual(nei_mean_distance(part_x, part_y), math.inf)

    def test_claim_3_non_negativity(self) -> None:
        """Claim 3: D >= 0 and I in [0, 1] unconditionally (Cauchy-Schwarz)."""
        profiles = [
            (
                [{0: 0.9, 1: 0.1}, {0: 0.3, 1: 0.7}],
                [{0: 0.1, 1: 0.9}, {0: 0.8, 1: 0.2}],
            ),
            (
                [{0: 0.5, 1: 0.5}],
                [{0: 0.25, 1: 0.75}],
            ),
            (
                [{0: 1.0}, {0: 0.5, 1: 0.5}],
                [{0: 1.0}, {0: 0.5, 1: 0.5}],
            ),
        ]

        for prof_x, prof_y in profiles:
            ident = nei_identity(prof_x, prof_y)
            dist = nei_standard_distance(prof_x, prof_y)
            self.assertGreaterEqual(ident, 0.0)
            self.assertLessEqual(ident, 1.0)
            self.assertGreaterEqual(dist, 0.0)

            geom_ident = nei_geometric_identity(prof_x, prof_y)
            geom_dist = nei_geometric_distance(prof_x, prof_y)
            self.assertGreaterEqual(geom_ident, 0.0)
            self.assertLessEqual(geom_ident, 1.0)
            self.assertGreaterEqual(geom_dist, 0.0)

            mean_dist = nei_mean_distance(prof_x, prof_y)
            self.assertGreaterEqual(mean_dist, 0.0)

    def test_claim_4_d_prime_internal_identity(self) -> None:
        """Claim 4: Geometric-mean D' equals arithmetic-mean D' (Eq. 4 vs Eq. 4')."""
        # Test configurations with 2, 3, and 5 loci
        configurations = [
            (
                [{0: 0.8, 1: 0.2}, {0: 0.3, 1: 0.7}],
                [{0: 0.5, 1: 0.5}, {0: 0.4, 1: 0.6}],
            ),
            (
                [{0: 0.6, 1: 0.3, 2: 0.1}, {0: 0.9, 1: 0.1}, {0: 0.4, 1: 0.6}],
                [{0: 0.2, 1: 0.7, 2: 0.1}, {0: 0.3, 1: 0.7}, {0: 0.5, 1: 0.5}],
            ),
            (
                [
                    {0: 0.2, 1: 0.8},
                    {0: 0.5, 1: 0.5},
                    {0: 0.7, 1: 0.3},
                    {0: 0.1, 1: 0.9},
                    {0: 0.4, 1: 0.6},
                ],
                [
                    {0: 0.4, 1: 0.6},
                    {0: 0.6, 1: 0.4},
                    {0: 0.3, 1: 0.7},
                    {0: 0.2, 1: 0.8},
                    {0: 0.5, 1: 0.5},
                ],
            ),
        ]

        for loci_x, loci_y in configurations:
            d_geom = nei_geometric_distance(loci_x, loci_y)
            d_mean = nei_mean_distance(loci_x, loci_y)
            self.assertTrue(math.isfinite(d_geom))
            self.assertTrue(math.isfinite(d_mean))
            self.assertAlmostEqual(d_geom, d_mean, places=12)
            self.assertAlmostEqual(d_geom, nei_d_prime(loci_x, loci_y), places=12)

    def test_claim_5_founder_effect_closed_form(self) -> None:
        """Claim 5: Founder identity I_0 matches Nei (1972) Eq. 9 exact numbers."""
        # Case 1: J_z = 0.8, N_x = inf, N_y = 50
        # Eq. 9: 0.8 / sqrt(0.8 * (0.8 + 0.2 / 100)) = 0.8 / sqrt(0.6416) ≈ 0.99875...
        i0_case1 = nei_founder_identity(0.8, math.inf, 50)
        expected_case1 = 0.8 / math.sqrt(0.8 * (0.8 + 0.2 / 100.0))
        self.assertAlmostEqual(i0_case1, expected_case1, places=12)
        self.assertAlmostEqual(i0_case1, 0.99875, places=5)
        # Paper prints 0.9987 (truncated to 4 decimals)
        self.assertAlmostEqual(i0_case1, 0.9987, delta=0.0001)
        self.assertEqual(math.floor(i0_case1 * 10000) / 10000, 0.9987)

        # Case 2: J_z = 0.8, N_x = inf, N_y = 5
        # Eq. 9: 0.8 / sqrt(0.8 * (0.8 + 0.2 / 10)) = 0.8 / sqrt(0.656) ≈ 0.987729...
        i0_case2 = nei_founder_identity(0.8, math.inf, 5)
        expected_case2 = 0.8 / math.sqrt(0.8 * (0.8 + 0.2 / 10.0))
        self.assertAlmostEqual(i0_case2, expected_case2, places=12)
        self.assertAlmostEqual(i0_case2, 0.98773, places=5)
        # Paper prints 0.9877 (truncated or rounded)
        self.assertAlmostEqual(i0_case2, 0.9877, delta=0.0001)

        # Symmetry: N_x and N_y can be swapped
        self.assertEqual(
            nei_founder_identity(0.8, 50, math.inf),
            nei_founder_identity(0.8, math.inf, 50),
        )
        self.assertEqual(
            nei_founder_identity(0.7, 20, 40),
            nei_founder_identity(0.7, 40, 20),
        )

        # Boundary: infinite founder sizes yield complete identity
        self.assertEqual(nei_founder_identity(0.8, math.inf, math.inf), 1.0)

        # Boundary: monomorphic parental population (J_z = 1) yields complete identity
        self.assertEqual(nei_founder_identity(1.0, 5, 5), 1.0)

        # Monotonicity: smaller founder size strictly reduces expected identity
        self.assertLess(
            nei_founder_identity(0.8, math.inf, 5),
            nei_founder_identity(0.8, math.inf, 50),
        )

    def test_claim_6_table_1_regression(self) -> None:
        """Claim 6: Table 1 regression check matches Nei (1972) printed values."""
        # Highlighted pairs in design doc §1.4:
        # I = 0.9823 -> D ≈ 0.0179 (paper prints 0.0178)
        # I = 0.8221 -> D ≈ 0.1959 (paper prints 0.1959, exact match)
        d_09823 = -math.log(0.9823)
        self.assertAlmostEqual(d_09823, 0.0179, places=4)
        self.assertAlmostEqual(d_09823, 0.0178, delta=0.0001)

        d_08221 = -math.log(0.8221)
        self.assertAlmostEqual(d_08221, 0.1959, places=4)

        # Full 15-pair matrix from Nei (1972) Table 1 (house mouse, 6 populations).
        # Format: (pop_i, pop_j, printed_I, printed_D)
        table_1_pairs = [
            (1, 2, 0.9823, 0.0178),
            (1, 3, 0.9749, 0.0256),
            (1, 4, 0.9792, 0.0210),
            (1, 5, 0.8236, 0.1941),
            (1, 6, 0.8221, 0.1959),
            (2, 3, 0.9854, 0.0147),
            (2, 4, 0.9906, 0.0094),
            (2, 5, 0.8436, 0.1701),
            (2, 6, 0.8425, 0.1713),
            (3, 4, 0.9943, 0.0057),
            (3, 5, 0.8264, 0.1906),
            (3, 6, 0.8303, 0.1859),
            (4, 5, 0.8748, 0.1337),
            (4, 6, 0.8787, 0.1292),
            (5, 6, 0.9982, 0.0018),
        ]

        for p_i, p_j, printed_i, printed_d in table_1_pairs:
            recomputed_d = -math.log(printed_i)
            # Recomputed D matches printed D within 0.0002
            self.assertAlmostEqual(
                recomputed_d,
                printed_d,
                delta=0.0002,
                msg=(
                    f"Mismatch for population pair ({p_i}, {p_j}): "
                    f"recomputed {recomputed_d} vs printed {printed_d}"
                ),
            )

    def test_non_metric_triangle_inequality_violation(self) -> None:
        """Nei's D is non-metric: triangle inequality fails (Discussion, p. 290)."""
        pop_a = {0: 0.9, 1: 0.1}
        pop_b = {0: 0.5, 1: 0.5}
        pop_c = {1: 0.1, 2: 0.9}

        d_ab = nei_standard_distance(pop_a, pop_b)
        d_bc = nei_standard_distance(pop_b, pop_c)
        d_ac = nei_standard_distance(pop_a, pop_c)

        # All three pairwise distances are finite
        self.assertTrue(math.isfinite(d_ab))
        self.assertTrue(math.isfinite(d_bc))
        self.assertTrue(math.isfinite(d_ac))

        # Triangle inequality fails: D(A, C) > D(A, B) + D(B, C)
        self.assertGreater(d_ac, d_ab + d_bc)

    def test_symmetry(self) -> None:
        """Pairwise genetic distance and identity are strictly symmetric."""
        pop_x = [{0: 0.7, 1: 0.3}, {0: 0.2, 1: 0.8}]
        pop_y = [{0: 0.4, 1: 0.6}, {0: 0.5, 1: 0.5}]

        self.assertEqual(
            cross_identity(pop_x[0], pop_y[0]),
            cross_identity(pop_y[0], pop_x[0]),
        )
        self.assertEqual(nei_identity(pop_x, pop_y), nei_identity(pop_y, pop_x))
        self.assertEqual(
            nei_standard_distance(pop_x, pop_y),
            nei_standard_distance(pop_y, pop_x),
        )
        self.assertEqual(
            nei_geometric_identity(pop_x, pop_y),
            nei_geometric_identity(pop_y, pop_x),
        )
        self.assertEqual(
            nei_geometric_distance(pop_x, pop_y),
            nei_geometric_distance(pop_y, pop_x),
        )
        self.assertEqual(
            nei_mean_distance(pop_x, pop_y),
            nei_mean_distance(pop_y, pop_x),
        )

    def test_input_validation(self) -> None:
        """Inputs are strictly validated for type, domain, and locus alignment."""
        # Non-mapping
        with self.assertRaises(TypeError):
            cross_identity([0.5, 0.5], {0: 1.0})  # type: ignore[arg-type]

        # Non-integer allele ID
        with self.assertRaises(TypeError):
            cross_identity({"a": 1.0}, {0: 1.0})

        # Frequencies not summing to 1
        with self.assertRaises(ValueError):
            cross_identity({0: 0.5}, {0: 1.0})

        # Negative frequency
        with self.assertRaises(ValueError):
            cross_identity({0: -0.1, 1: 1.1}, {0: 1.0})

        # Locus count mismatch
        with self.assertRaises(ValueError):
            nei_identity([{0: 1.0}, {0: 1.0}], [{0: 1.0}])

        # Empty loci sequence
        with self.assertRaises(ValueError):
            nei_identity([], [])

        # Founder identity validation: j_z outside (0, 1]
        with self.assertRaises(ValueError):
            nei_founder_identity(0.0, 50, 50)
        with self.assertRaises(ValueError):
            nei_founder_identity(-0.5, 50, 50)
        with self.assertRaises(ValueError):
            nei_founder_identity(1.1, 50, 50)

        # Founder identity validation: non-positive population sizes
        with self.assertRaises(ValueError):
            nei_founder_identity(0.8, 0, 50)
        with self.assertRaises(ValueError):
            nei_founder_identity(0.8, -10, 50)
        with self.assertRaises(ValueError):
            nei_founder_identity(0.8, 50, 0)

        # Founder identity validation: type errors
        with self.assertRaises(TypeError):
            nei_founder_identity("0.8", 50, 50)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            nei_founder_identity(0.8, "50", 50)  # type: ignore[arg-type]


@given(random_locus_profile(locus_count=3), random_locus_profile(locus_count=3))
def test_hypothesis_genetic_distance_properties(
    profile_x: list[dict[int, float]],
    profile_y: list[dict[int, float]],
) -> None:
    """Property test: bounds, symmetry, and Cauchy-Schwarz for random profiles."""
    ident = nei_identity(profile_x, profile_y)
    dist = nei_standard_distance(profile_x, profile_y)

    # Bounded identity and non-negative distance
    assert 0.0 <= ident <= 1.0
    assert dist >= 0.0

    # Symmetry
    assert math.isclose(ident, nei_identity(profile_y, profile_x), rel_tol=1e-12)
    assert math.isclose(
        dist, nei_standard_distance(profile_y, profile_x), rel_tol=1e-12
    )

    # Geometric and arithmetic distance equivalence when shared
    geom_ident = nei_geometric_identity(profile_x, profile_y)
    if geom_ident > 0.0:
        d_geom = nei_geometric_distance(profile_x, profile_y)
        d_mean = nei_mean_distance(profile_x, profile_y)
        assert math.isclose(d_geom, d_mean, rel_tol=1e-9, abs_tol=1e-9)
