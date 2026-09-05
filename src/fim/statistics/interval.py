"""Confidence intervals for across-replicate sample means.

A **confidence interval** is a range around a measured average that
expresses how much uncertainty is left after averaging only a limited
number of independent measurements — the same idea a poll reports as
"52% ± 3%" rather than a single bare number, where the "± 3%" is exactly
this kind of interval. See `fim.engine.reports_summary`'s own docstring
for the fuller explanation of what problem this solves in this project
specifically: each independently seeded replicate run contributes one
scalar draw (its own final ``D``, ``G_ST``, and so on), and this module
is what turns a handful of those draws into a mean plus a defensible
range around it.

The confidence interval of the mean of several such draws is a standard
Student's-t interval on the sample mean, exactly as for any other
independent, identically distributed sample — no bootstrap or other
resampling scheme is needed on top of draws that are already independent
by construction. "Student's-t" is the standard statistical method for
exactly this situation: a small number of independent measurements whose
own true variability is not known in advance and must itself be
estimated from the measurements at hand (as opposed to the simpler,
better-known bell-curve method, which assumes that variability is
already known) — the correction it applies matters most for a handful of
replicates and fades away as more are added, which is why the "degrees
of freedom" (one less than the number of values being averaged — see
`confidence_interval`'s own docstring) appears throughout this file.

The critical value (how many standard errors wide the interval needs to
be, for the requested confidence level and sample size) comes from a
standard published Student's-t table (linear interpolation in ``1/df``
between listed degrees of freedom, the conventional way to read an
unlisted row off a printed table) rather than an inverse regularized-
incomplete-beta computation (a more general but numerically delicate way
of computing the identical values from first principles): every tabled
degrees of freedom below 120 matches a printed statistics table exactly,
needs no dependency beyond the standard library, and avoids hand-rolling
a numerically delicate special function in the one area of this project
under direct outside statistical review. Above the table's tail, the
exact standard-normal quantile is used (a t-distribution's
``degrees_of_freedom -> infinity`` limit — with enough replicates, the
small-sample correction becomes negligible and the ordinary bell-curve
answer is exact), computed by the standard library's `statistics.
NormalDist`, not another approximate table row.
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
    """A sample mean with a two-sided confidence interval.

    Fields:
        mean: The plain average of every value supplied.
        half_width: How far the interval extends on either side of
            `mean` — `low`/`high` are just `mean` minus/plus this same
            number, kept as its own field since it is often useful on
            its own (the "± 3%" half of a "52% ± 3%"-style report).
        low, high: The interval's own two ends — the range this
            project's own convention is that the true underlying
            average plausibly falls within, at the requested
            `confidence` level.
        sample_count: How many values went into this interval — the
            same number `confidence_interval`'s own `values` argument
            had. Carried along here so a reader of the *result* alone
            can judge how much weight to put on the interval (a narrow
            interval from only 2 replicates is a much shakier basis for
            confidence than an equally narrow one from 40) without
            needing to separately track down how many replicates
            actually ran.
        confidence: The confidence level actually used (see
            `student_t_critical_value`) — kept alongside the numbers it
            produced so the result is self-describing, the same reason
            `fim.engine.RunResult` carries its own `params` alongside
            its own outcome.
    """

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

    See this module's own docstring, above, for what a confidence
    interval is and why the Student's-t method is the right tool for
    a handful of independent replicate draws specifically.

    Args:
        values: Independent, identically distributed observations (each
            replicate's own scalar outcome). At least two are required —
            a confidence interval is fundamentally a statement about how
            much a value *varies* across repeated measurements, which is
            not a meaningful question to ask of a single measurement on
            its own.
        confidence: Two-tailed confidence level; see
            `student_t_critical_value` for supported values.

    Returns:
        The sample mean and its confidence interval.

    Raises:
        ValueError: If fewer than two values are supplied, or any value
            is not finite.
    """
    sample_count = len(values)
    if sample_count < _MINIMUM_SAMPLE_COUNT:
        raise ValueError("confidence_interval requires at least two values")
    # No per-value finiteness check existed here at all before this
    # project's own multi-model engine review, 2026-09-04 (`FIM-07`/
    # finding Kimi-FIM-07): a `nan` silently produced a `nan` mean and
    # interval for a direct caller of this function (the convergence-
    # criterion path this project's own engine actually uses was already
    # fail-safe against this, since `nan <= tolerance` is always `False`
    # — but that is `fim.convergence.criteria`'s own accident, not a
    # contract this function itself ever made). Matches `fim.convergence.
    # monitor.ConvergenceMonitor.record`'s own identical rule.
    for value in values:
        if not math.isfinite(value):
            raise ValueError("confidence_interval requires finite values")
    mean = math.fsum(values) / sample_count
    # "Bessel-corrected" means dividing by (sample_count - 1) rather than
    # sample_count itself when estimating how spread out the values
    # are -- the standard statistical correction for the fact that the
    # values' own mean was itself estimated from this same limited
    # sample, which otherwise makes the raw spread a slight
    # underestimate of the sample's true underlying variability.
    # "Standard error of the mean" is then how much that spread
    # translates into uncertainty specifically about the *mean* (as
    # opposed to uncertainty about any one individual value) -- it
    # shrinks as more replicates are added, which is the whole reason
    # running more replicates narrows a reported interval.
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

    "Two-tailed" means the reported interval accounts for the true
    average being either higher *or* lower than the sample's own mean —
    the ordinary, default way to build a confidence interval, as opposed
    to a "one-tailed" interval that only bounds one direction. This
    function is a lookup, not a computation from first principles: it
    reads (or interpolates between) the entries of `_T_TABLE`, above —
    the same fixed numbers found in the "Student's-t table" printed in
    the back of most introductory statistics textbooks — falling back to
    the exact large-sample (normal-distribution) answer once
    `degrees_of_freedom` is larger than anything the table lists (see
    `_normal_quantile`, below).

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
    """Interpolate an untabled row in ``1/df``, the standard table convention.

    `_T_TABLE`, above, only lists selected degrees of freedom (1
    through 10, then wider gaps: 12, 15, 20, and so on) — exactly
    matching a real printed statistics table, which does the same for
    space. For a degrees-of-freedom value that falls between two listed
    rows, this estimates the missing value by interpolating (blending
    linearly) between its two neighbors — done in terms of `1/df` rather
    than `df` itself specifically because the critical value changes
    much more steeply at low degrees of freedom than at high ones, and
    interpolating in `1/df` tracks that curve far more accurately than a
    naive straight-line blend of the raw numbers would; this is the same
    convention a person reading a printed table by hand is taught to use
    for a row the table itself omits.
    """
    lower = max(df for df in _TABLE_DEGREES_OF_FREEDOM if df < degrees_of_freedom)
    upper = min(df for df in _TABLE_DEGREES_OF_FREEDOM if df > degrees_of_freedom)
    lower_value = _T_TABLE[lower][confidence]
    upper_value = _T_TABLE[upper][confidence]
    lower_x, upper_x, x = 1.0 / lower, 1.0 / upper, 1.0 / degrees_of_freedom
    weight = (x - lower_x) / (upper_x - lower_x)
    return lower_value + weight * (upper_value - lower_value)


def _normal_quantile(confidence: float) -> float:
    """Return the exact two-tailed standard-normal quantile (df -> infinity).

    As the number of replicates grows, the Student's-t distribution
    itself gets closer and closer to the ordinary bell curve ("the
    standard normal distribution") — the small-sample correction
    described in this module's own docstring, above, was only ever
    needed because a *small* number of replicates leaves genuine doubt
    about how spread out the underlying values really are, and that
    doubt shrinks toward zero as more replicates are added. This
    function is what `student_t_critical_value` falls back to once
    `degrees_of_freedom` runs past the end of `_T_TABLE`: rather than
    guessing at (or interpolating past the edge of) a table built for
    small samples, it computes the exact bell-curve answer directly,
    using the standard library's own `statistics.NormalDist` — the same
    number a t-table would converge to if it kept going forever.
    `(1.0 + confidence) / 2.0` is the standard conversion from a
    two-tailed confidence level (e.g. 95% in the middle) to the matching
    one-sided cutoff `inv_cdf` expects (e.g. the 97.5th percentile,
    which leaves exactly 2.5% in each of the two excluded tails).
    """
    return NormalDist().inv_cdf((1.0 + confidence) / 2.0)
