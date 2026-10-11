"""Compare archived reports without requiring platform-specific math rounding."""

from __future__ import annotations

import math
from typing import Any

_ROUNDED_ARCHIVE_FIELDS = frozenset({"window_statistics", "E_ST", "MI"})


def archived_report_equal(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    """Require exact data except rounded window and Shannon-entropy floats.

    Args:
        expected: The archived report, with caller-specific exclusions applied.
        actual: The fresh report, with the same exclusions applied.

    Returns:
        Whether all fields agree, allowing only numerical rounding in
        `window_statistics`, `E_ST`, and `MI` (relative `1e-12`, absolute
        `1e-14`). FFT/BLAS and the system logarithm can differ in their last
        bits across platforms. Keys, types, integers, booleans, and all other
        report fields remain exact; local parity never uses this comparator.
    """
    return expected.keys() == actual.keys() and all(
        _rounded_equal(expected[key], actual[key])
        if key in _ROUNDED_ARCHIVE_FIELDS
        else expected[key] == actual[key]
        for key in expected
    )


def _rounded_equal(expected: Any, actual: Any) -> bool:
    """Compare selected data recursively, permitting only finite float rounding."""
    if type(expected) is not type(actual):
        return False
    if isinstance(expected, dict):
        return expected.keys() == actual.keys() and all(
            _rounded_equal(value, actual[key]) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(expected) == len(actual) and all(
            _rounded_equal(left, right)
            for left, right in zip(expected, actual, strict=True)
        )
    if isinstance(expected, float):
        return (
            math.isfinite(expected)
            and math.isfinite(actual)
            and math.isclose(expected, actual, rel_tol=1e-12, abs_tol=1e-14)
        )
    return bool(expected == actual)
