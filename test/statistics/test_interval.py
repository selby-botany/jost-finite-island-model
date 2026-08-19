"""Focused tests for across-replicate confidence intervals."""

from __future__ import annotations

import math
import unittest
from statistics import NormalDist

from fim.statistics import confidence_interval, student_t_critical_value

# Untabled degrees of freedom spanning the whole interpolated range between
# `interval.py`'s printed table rows (10-12, 12-15, ..., 60-120), used by
# `test_interpolated_critical_values_match_an_independent_quadrature_oracle`
# below.
_UNTABLED_DEGREES_OF_FREEDOM = (11, 13, 18, 25, 35, 45, 55, 70, 90, 110)

# The interpolation-error bound the oracle test checks against, in tail
# *probability* space. Empirically, both the interpolated rows and the
# table's own exactly-tabled rows (e.g. df=10, an exact entry, not an
# interpolated one) show quadrature-vs-table tail-probability discrepancies
# up to ~4e-5 -- consistent with a standard printed t-table's own
# three-decimal-place rounding, not a defect in either the interpolation or
# the quadrature. 1e-4 keeps roughly 3x headroom above every value observed
# while still catching a genuinely wrong interpolation (a swapped row or an
# inverted 1/df weight moves the tail probability by orders of magnitude
# more than this).
_INTERPOLATION_ERROR_TOLERANCE = 1e-4


def _t_log_normalizer(degrees_of_freedom: int) -> float:
    """Return the log of the Student's-t density's normalizing constant.

    Via `math.lgamma` rather than `math.gamma` directly, so degrees of
    freedom in the table's range (up to 120) never risk a `Gamma`
    overflow.
    """
    df = degrees_of_freedom
    return (
        math.lgamma((df + 1) / 2.0)
        - 0.5 * math.log(df * math.pi)
        - math.lgamma(df / 2.0)
    )


def _t_density(x: float, degrees_of_freedom: int) -> float:
    """Return the Student's-t probability density at `x`."""
    df = degrees_of_freedom
    kernel = math.exp(math.log(1.0 + x * x / df) * (-(df + 1) / 2.0))
    return math.exp(_t_log_normalizer(df)) * kernel


def _reference_tail_probability(
    critical_value: float, degrees_of_freedom: int, *, steps: int = 20_000
) -> float:
    """Return ``P(T > critical_value)`` by direct numerical quadrature.

    This is deliberately independent of `interval._T_TABLE` and its 1/df
    interpolation: it integrates the closed-form Student's-t density
    (`_t_density`) directly, via composite Simpson's rule, and shares no
    code path with the production critical-value lookup it is checking.
    Used only as a test-time oracle -- pure standard library, no runtime
    dependency added to the package itself.

    The semi-infinite integral ``integral(t_density, critical_value, inf)``
    is mapped onto the finite interval ``v in (0, 1]`` via ``x =
    critical_value / v`` (so ``v=1`` is ``x=critical_value`` and ``v -> 0``
    is ``x -> infinity``), the standard substitution for a polynomially
    decaying tail -- the Student's-t density is heavier-tailed than
    exponential, so a fixed finite upper cutoff on `x` would either
    truncate real mass (too small) or waste quadrature resolution on a
    negligible region (too large).

    Args:
        critical_value: The value to compute the upper-tail probability
            beyond, i.e. `t` in `P(T > t)`.
        degrees_of_freedom: The distribution's degrees of freedom.
        steps: Simpson's-rule interval count; must be even.

    Returns:
        The upper-tail probability `P(T > critical_value)`.
    """

    def integrand(v: float) -> float:
        # At v == 0 (x -> infinity), the substitution's Jacobian and the
        # density's polynomial decay cancel to a finite limit rather than
        # diverging or vanishing to an indeterminate 0/0 -- evaluated
        # directly rather than approached, since the closed-form density
        # stays numerically well-behaved for every finite `x` (no
        # overflow risk: `x` only ever appears raised to a negative
        # power). For degrees_of_freedom > 1 the limit is exactly zero;
        # for degrees_of_freedom == 1 (the Cauchy distribution, whose
        # x**-2 tail exactly cancels the substitution's v**-2 Jacobian)
        # it is the nonzero constant `normalizer / critical_value`.
        if v <= 0.0:
            if degrees_of_freedom > 1:
                return 0.0
            return math.exp(_t_log_normalizer(1)) / critical_value
        x = critical_value / v
        return _t_density(x, degrees_of_freedom) * critical_value / (v * v)

    step_size = 1.0 / steps
    total = integrand(0.0) + integrand(1.0)
    for i in range(1, steps):
        weight = 4 if i % 2 == 1 else 2
        total += weight * integrand(i * step_size)
    return total * step_size / 3.0


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

    def test_interpolated_critical_values_match_an_independent_quadrature_oracle(
        self,
    ) -> None:
        """1/df interpolation is checked against numerical integration, not just itself.

        R22 (`doc/dev/20260818-claude-opus-5-project-review-rollup.md`,
        not committed): interpolating a printed table in 1/df is a
        defensible dependency-free choice, but nothing previously bounded
        its actual error against an independent reference -- every prior
        test in this class either reads a tabled row back or recomputes
        the same 1/df formula the production code already uses, so a
        wrong interpolation weight or a transposed table row would have
        passed unnoticed as long as it was internally self-consistent.

        For each untabled degrees-of-freedom value, the interpolated
        critical value is fed to `_reference_tail_probability` -- a
        numerical integration of the closed-form Student's-t density
        that shares no code, table, or formula with
        `interval._interpolate` -- and the resulting tail probability
        must land within `_INTERPOLATION_ERROR_TOLERANCE` of the target
        tail probability the critical value is supposed to produce.
        """
        for degrees_of_freedom in _UNTABLED_DEGREES_OF_FREEDOM:
            for confidence in (0.90, 0.95, 0.99):
                critical_value = student_t_critical_value(
                    degrees_of_freedom, confidence
                )
                target_tail_probability = (1.0 - confidence) / 2.0
                reference_tail_probability = _reference_tail_probability(
                    critical_value, degrees_of_freedom
                )
                self.assertAlmostEqual(
                    reference_tail_probability,
                    target_tail_probability,
                    delta=_INTERPOLATION_ERROR_TOLERANCE,
                    msg=(
                        f"df={degrees_of_freedom} confidence={confidence}: "
                        f"interpolated critical value {critical_value} implies "
                        f"tail probability {reference_tail_probability}, too far "
                        f"from the target {target_tail_probability}"
                    ),
                )

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
