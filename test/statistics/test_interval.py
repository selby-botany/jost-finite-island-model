"""Focused tests for across-replicate confidence intervals."""

from __future__ import annotations

import math
import unittest
from statistics import NormalDist

from fim.statistics import confidence_interval, student_t_critical_value


class StudentTCriticalValueTests(unittest.TestCase):
    """Verify table lookups, interpolation, and the normal-quantile tail."""

    def test_tabled_degrees_of_freedom_match_published_values_exactly(self) -> None:
        """Every listed row returns its exact standard t-table entry."""
        self.assertEqual(student_t_critical_value(1, 0.95), 12.706)
        self.assertEqual(student_t_critical_value(10, 0.90), 1.812)
        self.assertEqual(student_t_critical_value(30, 0.95), 2.042)
        self.assertEqual(student_t_critical_value(120, 0.99), 2.617)

    def test_untabled_degrees_of_freedom_interpolate_between_neighbors(self) -> None:
        """A gap row (df=11, between the table's 10 and 12) is bracketed."""
        below = student_t_critical_value(10, 0.95)
        above = student_t_critical_value(12, 0.95)
        between = student_t_critical_value(11, 0.95)
        self.assertLess(above, between)
        self.assertLess(between, below)

    def test_interpolation_matches_the_documented_one_over_df_formula(self) -> None:
        """The interpolated value matches direct 1/df linear interpolation."""
        lower_value, upper_value = 1.812, 1.782
        lower_x, upper_x, x = 1.0 / 10, 1.0 / 12, 1.0 / 11
        weight = (x - lower_x) / (upper_x - lower_x)
        expected = lower_value + weight * (upper_value - lower_value)
        self.assertAlmostEqual(student_t_critical_value(11, 0.90), expected, places=12)

    def test_degrees_of_freedom_beyond_the_table_use_the_exact_normal_quantile(
        self,
    ) -> None:
        """Past the table's tail, the exact df -> infinity limit is used."""
        expected = NormalDist().inv_cdf((1.0 + 0.95) / 2.0)
        self.assertEqual(student_t_critical_value(121, 0.95), expected)
        self.assertEqual(student_t_critical_value(10_000, 0.95), expected)

    def test_normal_quantile_matches_well_known_z_values(self) -> None:
        """The df -> infinity limit reproduces the familiar z critical values."""
        huge = 1_000_000
        self.assertAlmostEqual(student_t_critical_value(huge, 0.90), 1.645, places=3)
        self.assertAlmostEqual(student_t_critical_value(huge, 0.95), 1.960, places=3)
        self.assertAlmostEqual(student_t_critical_value(huge, 0.99), 2.576, places=3)

    def test_invalid_degrees_of_freedom_and_confidence_are_rejected(self) -> None:
        """Non-positive df and unsupported confidence levels raise."""
        with self.assertRaises(ValueError):
            student_t_critical_value(0, 0.95)
        with self.assertRaises(ValueError):
            student_t_critical_value(-1, 0.95)
        with self.assertRaises(ValueError):
            student_t_critical_value(10, 0.80)


class ConfidenceIntervalTests(unittest.TestCase):
    """Verify the sample-mean confidence interval and its edge cases."""

    def test_matches_a_hand_computed_interval(self) -> None:
        """A tiny sample's interval matches hand-computed values exactly."""
        interval = confidence_interval([1.0, 2.0, 3.0], confidence=0.95)
        expected_half_width = student_t_critical_value(2, 0.95) * math.sqrt(1.0 / 3.0)
        self.assertEqual(interval["mean"], 2.0)
        self.assertEqual(interval["sample_count"], 3)
        self.assertEqual(interval["confidence"], 0.95)
        self.assertAlmostEqual(interval["half_width"], expected_half_width, places=12)
        self.assertAlmostEqual(interval["low"], 2.0 - expected_half_width, places=12)
        self.assertAlmostEqual(interval["high"], 2.0 + expected_half_width, places=12)

    def test_identical_values_produce_a_zero_width_interval(self) -> None:
        """No variance in the sample means a certain, zero-width interval."""
        interval = confidence_interval([0.5, 0.5, 0.5, 0.5], confidence=0.95)
        self.assertEqual(interval["half_width"], 0.0)
        self.assertEqual(interval["low"], interval["high"])

    def test_more_replicates_at_the_same_spread_tightens_the_interval(self) -> None:
        """Doubling a repeated pattern's replicate count shrinks the interval."""
        small = confidence_interval([1.0, 2.0, 3.0, 4.0])
        large = confidence_interval([1.0, 2.0, 3.0, 4.0] * 10)
        self.assertLess(large["half_width"], small["half_width"])

    def test_default_confidence_is_ninety_five_percent(self) -> None:
        """Omitting `confidence` matches the documented 95% default."""
        with_default = confidence_interval([1.0, 2.0, 3.0])
        explicit = confidence_interval([1.0, 2.0, 3.0], confidence=0.95)
        self.assertEqual(with_default, explicit)

    def test_fewer_than_two_values_is_rejected(self) -> None:
        """A confidence interval needs at least two observations."""
        with self.assertRaises(ValueError):
            confidence_interval([])
        with self.assertRaises(ValueError):
            confidence_interval([1.0])


if __name__ == "__main__":
    unittest.main()
