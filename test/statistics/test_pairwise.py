"""Tests for the vectorized all-pairs Nei identity matrices."""

from __future__ import annotations

import itertools
import random
import unittest

import numpy as np

from fim.statistics import (
    NEI_DENOMINATORS,
    NEI_LOCUS_RULES,
    locus_frequency_matrix,
    nei_pair_identity,
    pairwise_nei_identities,
    upper_triangle,
)


def _random_tables(
    seed: int, deme_count: int, locus_count: int
) -> list[list[dict[int, float]]]:
    """Return seeded random frequency tables (one per locus)."""
    generator = random.Random(seed)
    tables: list[list[dict[int, float]]] = []
    for _ in range(locus_count):
        table: list[dict[int, float]] = []
        for _ in range(deme_count):
            alleles = generator.sample(range(8), generator.randint(1, 5))
            counts = [generator.randint(1, 20) for _ in alleles]
            total = sum(counts)
            table.append({a: c / total for a, c in zip(alleles, counts, strict=True)})
        tables.append(table)
    return tables


class PairwiseNeiTests(unittest.TestCase):
    """Matrices agree with the scalar pair functions, element by element."""

    def test_every_element_matches_nei_pair_identity(self) -> None:
        """All four matrices equal the pure-Python pair function per pair."""
        tables = _random_tables(seed=7, deme_count=6, locus_count=3)
        family = pairwise_nei_identities(tables)
        self.assertEqual(
            set(family), set(itertools.product(NEI_DENOMINATORS, NEI_LOCUS_RULES))
        )
        for (denominator, locus_rule), matrix in family.items():
            for first, second in itertools.combinations(range(6), 2):
                expected = nei_pair_identity(
                    [table[first] for table in tables],
                    [table[second] for table in tables],
                    denominator=denominator,
                    locus_rule=locus_rule,
                )
                self.assertAlmostEqual(matrix[first, second], expected, places=12)

    def test_symmetric_unit_diagonal_and_bounded(self) -> None:
        """Every matrix is symmetric, has a diagonal of 1 and stays in [0, 1]."""
        family = pairwise_nei_identities(_random_tables(3, 9, 2))
        for matrix in family.values():
            np.testing.assert_allclose(matrix, matrix.T, atol=1e-15)
            np.testing.assert_array_equal(np.diagonal(matrix), np.ones(9))
            self.assertTrue(np.all((matrix >= 0.0) & (matrix <= 1.0)))

    def test_no_shared_allele_gives_zero_identity(self) -> None:
        """Disjoint demes: 0 in every form, including the locus mean."""
        tables = [[{0: 1.0}, {1: 1.0}], [{0: 1.0}, {0: 1.0}]]
        family = pairwise_nei_identities(tables)
        self.assertEqual(family[("geometric", "locus_mean")][0, 1], 0.0)
        self.assertGreater(family[("geometric", "pooled")][0, 1], 0.0)

    def test_upper_triangle_order_and_length(self) -> None:
        """Row-major strict upper triangle: [0,1], [0,2], [1,2]."""
        matrix = np.array([[1.0, 0.1, 0.2], [0.1, 1.0, 0.3], [0.2, 0.3, 1.0]])
        self.assertEqual(upper_triangle(matrix), [0.1, 0.2, 0.3])

    def test_frequency_matrix_and_shape_errors(self) -> None:
        """Absent alleles are zero; empty or ragged input is rejected."""
        matrix = locus_frequency_matrix([{0: 0.5, 3: 0.5}, {3: 1.0}])
        np.testing.assert_array_equal(matrix, [[0.5, 0.5], [0.0, 1.0]])
        with self.assertRaises(ValueError):
            pairwise_nei_identities([])
        with self.assertRaises(ValueError):
            pairwise_nei_identities([[{0: 1.0}], [{0: 1.0}, {0: 1.0}]])

    def test_large_deme_count_shape(self) -> None:
        """A 300-deme run produces full-size matrices (the capture path's scale)."""
        family = pairwise_nei_identities(_random_tables(11, 300, 2))
        for matrix in family.values():
            self.assertEqual(matrix.shape, (300, 300))
            self.assertEqual(len(upper_triangle(matrix)), 300 * 299 // 2)
