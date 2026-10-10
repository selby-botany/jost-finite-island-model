"""Compare archived reports without requiring platform-specific FFT rounding."""

from __future__ import annotations

import math
from typing import Any


def archived_report_equal(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    """Require exact report data except rounded evidence-window floats.

    Args:
        expected: The archived report, with caller-specific exclusions applied.
        actual: The fresh report, with the same exclusions applied.

    Returns:
        Whether all fields agree, allowing only numerical rounding in
        `window_statistics` (relative `1e-12`, absolute `1e-14`).
        Keys, types, integers, booleans, and non-window fields remain exact.
    """
    return (
        expected.keys() == actual.keys()
        and all(
            expected[key] == actual[key]
            for key in expected
            if key != "window_statistics"
        )
        and _window_equal(
            expected.get("window_statistics"), actual.get("window_statistics")
        )
    )


def _window_equal(expected: Any, actual: Any) -> bool:
    """Compare window data recursively, permitting only finite float rounding."""
    if type(expected) is not type(actual):
        return False
    if isinstance(expected, dict):
        return expected.keys() == actual.keys() and all(
            _window_equal(value, actual[key]) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(expected) == len(actual) and all(
            _window_equal(left, right)
            for left, right in zip(expected, actual, strict=True)
        )
    if isinstance(expected, float):
        return (
            math.isfinite(expected)
            and math.isfinite(actual)
            and math.isclose(expected, actual, rel_tol=1e-12, abs_tol=1e-14)
        )
    return bool(expected == actual)
