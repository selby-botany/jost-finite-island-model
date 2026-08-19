"""Confidence intervals for across-replicate sample means.

Each independently seeded replicate run contributes one scalar draw (its
own final ``D``, ``G_ST``, and so on); the confidence interval of the mean
of several such draws is a standard Student's-t interval on the sample
mean, exactly as for any other independent, identically distributed
sample — no bootstrap or other resampling scheme is needed on top of
draws that are already independent by construction.

The critical value comes from a standard published Student's-t table
(linear interpolation in ``1/df`` between listed degrees of freedom, the
conventional way to read an unlisted row off a printed table) rather than
an inverse regularized-incomplete-beta computation: every tabled degrees
of freedom below 120 matches a printed statistics table exactly, needs no
dependency beyond the standard library, and avoids hand-rolling a
numerically delicate special function in the one area of this project
under direct outside statistical review. Above the table's tail, the
exact standard-normal quantile is used (a t-distribution's
``degrees_of_freedom -> infinity`` limit), computed by the standard
library's `statistics.NormalDist`, not another approximate table row.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from statistics import NormalDist
from typing import TypedDict

_SUPPORTED_CONFIDENCE_LEVELS = (0.90, 0.95, 0.99)
_MINIMUM_SAMPLE_COUNT = 2

# Two-tailed Student's-t critical values, by degrees of freedom, at each
# supported confidence level. Every entry matches a standard published
# t-table exactly; unlisted degrees of freedom are interpolated in
# ``1/df`` by `student_t_critical_value`.
_T_TABLE: dict[int, dict[float, float]] = {
    1: {0.90: 6.314, 0.95: 12.706, 0.99: 63.657},
    2: {0.90: 2.920, 0.95: 4.303, 0.99: 9.925},
    3: {0.90: 2.353, 0.95: 3.182, 0.99: 5.841},
    4: {0.90: 2.132, 0.95: 2.776, 0.99: 4.604},
    5: {0.90: 2.015, 0.95: 2.571, 0.99: 4.032},
    6: {0.90: 1.943, 0.95: 2.447, 0.99: 3.707},
    7: {0.90: 1.895, 0.95: 2.365, 0.99: 3.499},
    8: {0.90: 1.860, 0.95: 2.306, 0.99: 3.355},
    9: {0.90: 1.833, 0.95: 2.262, 0.99: 3.250},
    10: {0.90: 1.812, 0.95: 2.228, 0.99: 3.169},
    12: {0.90: 1.782, 0.95: 2.179, 0.99: 3.055},
    15: {0.90: 1.753, 0.95: 2.131, 0.99: 2.947},
    20: {0.90: 1.725, 0.95: 2.086, 0.99: 2.845},
    24: {0.90: 1.711, 0.95: 2.064, 0.99: 2.797},
    30: {0.90: 1.697, 0.95: 2.042, 0.99: 2.750},
    40: {0.90: 1.684, 0.95: 2.021, 0.99: 2.704},
    60: {0.90: 1.671, 0.95: 2.000, 0.99: 2.660},
    120: {0.90: 1.658, 0.95: 1.980, 0.99: 2.617},
}
_TABLE_DEGREES_OF_FREEDOM = sorted(_T_TABLE)
_MAXIMUM_TABLED_DEGREES_OF_FREEDOM = _TABLE_DEGREES_OF_FREEDOM[-1]


class ConfidenceInterval(TypedDict):
    """A sample mean with a two-sided confidence interval."""

    mean: float
    half_width: float
    low: float
    high: float
    sample_count: int
    confidence: float


def confidence_interval(
    values: Sequence[float],
    *,
    confidence: float = 0.95,
) -> ConfidenceInterval:
    """Return a sample mean's Student's-t confidence interval.

    Args:
        values: Independent, identically distributed observations (each
            replicate's own scalar outcome). At least two are required.
        confidence: Two-tailed confidence level; see
            `student_t_critical_value` for supported values.

    Returns:
        The sample mean and its confidence interval.

    Raises:
        ValueError: If fewer than two values are supplied.
    """
    sample_count = len(values)
    if sample_count < _MINIMUM_SAMPLE_COUNT:
        raise ValueError("confidence_interval requires at least two values")
    mean = math.fsum(values) / sample_count
    # Bessel-corrected sample variance, then the standard error of the mean.
    variance = math.fsum((value - mean) ** 2 for value in values) / (sample_count - 1)
    standard_error = math.sqrt(variance / sample_count)
    half_width = student_t_critical_value(sample_count - 1, confidence) * standard_error
    return {
        "mean": mean,
        "half_width": half_width,
        "low": mean - half_width,
        "high": mean + half_width,
        "sample_count": sample_count,
        "confidence": confidence,
    }


def student_t_critical_value(degrees_of_freedom: int, confidence: float) -> float:
    """Return the two-tailed Student's-t critical value.

    Args:
        degrees_of_freedom: Sample size minus one; must be at least 1.
        confidence: One of the three supported two-tailed confidence
            levels (``0.90``, ``0.95``, ``0.99``).

    Returns:
        The critical value ``t`` such that a sample mean's interval
        ``mean +/- t * standard_error`` covers ``confidence`` of the
        sampling distribution under normality.

    Raises:
        ValueError: If `degrees_of_freedom` is not a positive integer or
            `confidence` is not a supported level.
    """
    if degrees_of_freedom < 1:
        raise ValueError("degrees_of_freedom must be at least 1")
    if confidence not in _SUPPORTED_CONFIDENCE_LEVELS:
        raise ValueError("confidence must be 0.90, 0.95, or 0.99")
    if degrees_of_freedom in _T_TABLE:
        return _T_TABLE[degrees_of_freedom][confidence]
    if degrees_of_freedom > _MAXIMUM_TABLED_DEGREES_OF_FREEDOM:
        return _normal_quantile(confidence)
    return _interpolate(degrees_of_freedom, confidence)


def _interpolate(degrees_of_freedom: int, confidence: float) -> float:
    """Interpolate an untabled row in ``1/df``, the standard table convention."""
    lower = max(df for df in _TABLE_DEGREES_OF_FREEDOM if df < degrees_of_freedom)
    upper = min(df for df in _TABLE_DEGREES_OF_FREEDOM if df > degrees_of_freedom)
    lower_value = _T_TABLE[lower][confidence]
    upper_value = _T_TABLE[upper][confidence]
    lower_x, upper_x, x = 1.0 / lower, 1.0 / upper, 1.0 / degrees_of_freedom
    weight = (x - lower_x) / (upper_x - lower_x)
    return lower_value + weight * (upper_value - lower_value)


def _normal_quantile(confidence: float) -> float:
    """Return the exact two-tailed standard-normal quantile (df -> infinity)."""
    return NormalDist().inv_cdf((1.0 + confidence) / 2.0)
