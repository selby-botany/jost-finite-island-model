"""Regression tests for portable, structurally strict example archives."""

from __future__ import annotations

import copy
import math
from typing import Any

import pytest
from example_support import archived_report_equal


def _report() -> dict[str, Any]:
    """Return a minimal report with nested evidence-window diagnostics."""
    return {
        "D": 0.3,
        "window_statistics": {
            "D": {
                "mean": 0.3,
                "standard_error": 0.008,
                "geweke_z": 0.0,
                "window": 40,
                "noise_adequate": True,
                "mean_of_values": {"mean": 0.3, "standard_error": 0.008},
            }
        },
    }


def test_archive_accepts_only_window_rounding() -> None:
    """One-ULP and near-zero FFT rounding preserve archive agreement."""
    expected = _report()
    actual = copy.deepcopy(expected)
    window = actual["window_statistics"]["D"]
    window["mean"] = math.nextafter(0.3, math.inf)
    window["mean_of_values"]["standard_error"] = math.nextafter(0.008, math.inf)
    window["geweke_z"] = 1e-15
    assert expected != actual
    assert archived_report_equal(expected, actual)
    actual["D"] = math.nextafter(0.3, math.inf)
    assert not archived_report_equal(expected, actual)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mean", 0.300000000001),
        ("standard_error", 0.008000000001),
        ("geweke_z", 2e-14),
        ("window", 41),
        ("window", 40.0),
        ("noise_adequate", False),
        ("noise_adequate", 1),
        ("mean", None),
        ("mean", math.inf),
        ("mean", math.nan),
    ],
)
def test_archive_rejects_changes_beyond_rounding(field: str, value: Any) -> None:
    """Numerical drift, type changes, and discrete decisions still fail."""
    expected = _report()
    actual = copy.deepcopy(expected)
    actual["window_statistics"]["D"][field] = value
    assert not archived_report_equal(expected, actual)


@pytest.mark.parametrize("level", ["report", "statistic", "window", "form"])
def test_archive_requires_identical_keys(level: str) -> None:
    """Missing data cannot masquerade as matching nulls or rounded values."""
    expected = _report()
    actual = copy.deepcopy(expected)
    if level == "report":
        actual["extra"] = None
    elif level == "statistic":
        actual["window_statistics"]["G_ST"] = {}
    elif level == "window":
        del actual["window_statistics"]["D"]["standard_error"]
    else:
        del actual["window_statistics"]["D"]["mean_of_values"]["mean"]
    assert not archived_report_equal(expected, actual)


def test_reports_without_windows_still_require_exact_agreement() -> None:
    """Short runs are not granted statistical or numerical tolerance."""
    assert archived_report_equal({"D": 0.3}, {"D": 0.3})
    assert not archived_report_equal({"D": 0.3}, {"D": 0.30000000000001})


def test_window_lists_preserve_length_and_values() -> None:
    """A sequence may round floats, but cannot lose or change entries."""
    expected = {"window_statistics": {"values": [0.3, 0.4]}}
    rounded = {"window_statistics": {"values": [math.nextafter(0.3, math.inf), 0.4]}}
    assert archived_report_equal(expected, rounded)
    assert not archived_report_equal(expected, {"window_statistics": {"values": [0.3]}})
    assert not archived_report_equal(
        expected, {"window_statistics": {"values": [0.3, 0.5]}}
    )
