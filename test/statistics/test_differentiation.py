"""Focused tests for pure finite-island differentiation statistics."""

from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

from fim.statistics import (
    differentiation_q,
    e_st,
    equilibrium_d,
    equilibrium_g_st,
    g_st,
    h_s,
    h_st,
    h_t,
    heterozygosity,
    hill_number,
    identity,
    jost_d,
    k_st,
    statistics_report,
    total_hill_number,
    within_hill_number,
)

DATA_DIRECTORY = Path(__file__).parents[1] / "data" / "statistics"


def _fixture(name: str) -> dict[str, object]:
    """Load one hand-checked golden frequency-table fixture."""
    with (DATA_DIRECTORY / name).open(encoding="utf-8") as fixture_file:
        return json.load(fixture_file)


def _frequency_table(fixture: dict[str, object]) -> list[dict[int, float]]:
    """Convert JSON object keys into integer allele identifiers."""
    raw_table = fixture["frequency_table"]
    assert isinstance(raw_table, list)
    table: list[dict[int, float]] = []
    for deme in raw_table:
        assert isinstance(deme, dict)
        table.append(
            {int(allele_id): float(frequency) for allele_id, frequency in deme.items()}
        )
    return table


class DifferentiationStatisticsTests(unittest.TestCase):
    """Verify formulas, golden examples, bounds, and input validation."""

    def test_golden_statistics(self) -> None:
        """Golden tables match the worked examples in the differentiation guide."""
        for fixture_name in (
            "fixed_all_different.json",
            "fixed_five_five.json",
            "fixed_nine_one.json",
            "reversed_frequencies.json",
            "shared_all.json",
            "shared_and_private.json",
            "shared_none.json",
        ):
            with self.subTest(fixture_name=fixture_name):
                fixture = _fixture(fixture_name)
                report = statistics_report(_frequency_table(fixture))
                expected = fixture["expected"]
                self.assertIsInstance(expected, dict)
                for name, value in expected.items():
                    self.assertAlmostEqual(report[name], value, places=12)

    def test_single_deme_statistics_and_hill_orders(self) -> None:
        """H, J, and q=0,1,2 Hill numbers follow their defining equations."""
        deme = {0: 0.5, 1: 0.25, 2: 0.25}
        self.assertAlmostEqual(heterozygosity(deme), 0.625)
        self.assertAlmostEqual(identity(deme), 0.375)
        self.assertEqual(hill_number(deme, 0), 3.0)
        self.assertAlmostEqual(
            hill_number(deme, 1),
            math.exp(-0.5 * math.log(0.5) - 2 * 0.25 * math.log(0.25)),
        )
        self.assertAlmostEqual(hill_number(deme, 2), 1 / 0.375)

    def test_differentiation_endpoints_and_hill_partition(self) -> None:
        """q-family endpoints agree with D, E_ST, K_ST and H's partition."""
        table = [{0: 0.5, 1: 0.5}, {0: 0.25, 2: 0.75}]
        self.assertAlmostEqual(differentiation_q(table, 0), k_st(table))
        self.assertAlmostEqual(differentiation_q(table, 1), e_st(table))
        self.assertAlmostEqual(differentiation_q(table, 2), jost_d(table))
        self.assertAlmostEqual(
            h_t(table),
            h_s(table) + h_st(table) - h_s(table) * h_st(table),
        )
        self.assertAlmostEqual(
            total_hill_number(table, 2),
            1 / (1 - h_t(table)),
        )
        self.assertAlmostEqual(
            within_hill_number(table, 2),
            1 / (1 - h_s(table)),
        )

    def test_g_st_has_explicit_undefined_value_for_shared_fixation(self) -> None:
        """G_ST is None, rather than NaN, for total heterozygosity of zero."""
        table = [{9: 1.0}, {9: 1.0}]
        self.assertIsNone(g_st(table))
        self.assertIsNone(statistics_report(table)["G_ST"])

    def test_e_st_accepts_size_weights_but_d_is_always_equal_weighted(self) -> None:
        """Only E_ST accepts optional relative deme-size weights."""
        table = [{0: 1.0}, {1: 1.0}, {1: 1.0}]
        self.assertNotAlmostEqual(e_st(table), e_st(table, [10, 1, 1]))
        self.assertAlmostEqual(jost_d(table), 2 / 3)
        with self.assertRaisesRegex(ValueError, "only defined for Differentiation_q"):
            differentiation_q(table, 2, [10, 1, 1])

    def test_malformed_frequency_tables_and_weights_are_rejected(self) -> None:
        """Malformed frequency tables and weights produce specific errors."""
        cases: tuple[
            tuple[list[dict[object, float]], list[float] | None, str],
            ...,
        ] = (
            ([], None, "at least one deme"),
            ([{0: 0.8}], None, "sum to 1"),
            ([{True: 1.0}], None, "integer-like"),
            ([{0: 1.0}, {1: 1.0}], [1], "expected 2"),
            ([{0: 1.0}, {1: 1.0}], [1, 0], "strictly positive"),
        )
        for table, weights, message in cases:
            with (
                self.subTest(message=message),
                self.assertRaisesRegex((TypeError, ValueError), message),
            ):
                h_s(table, weights)

    def test_differentiation_statistics_are_bounded(self) -> None:
        """All defined scalar differentiation measures stay inside [0, 1]."""
        tables = (
            [{0: 1.0}, {1: 1.0}],
            [{0: 0.5, 1: 0.5}, {0: 0.5, 1: 0.5}],
            [{0: 0.1, 1: 0.9}, {0: 0.9, 1: 0.1}],
        )
        for table in tables:
            with self.subTest(table=table):
                values = [jost_d(table), e_st(table), k_st(table)]
                gst = g_st(table)
                if gst is not None:
                    values.append(gst)
                for value in values:
                    self.assertGreaterEqual(value, 0.0)
                    self.assertLessEqual(value, 1.0)

    def test_weighted_heterozygosity_and_hill_numbers_use_weights(self) -> None:
        """Optional weights change pooled and within-deme diversity as documented."""
        table = [{0: 1.0}, {1: 1.0}]
        self.assertAlmostEqual(h_s(table, [3, 1]), 0.0)
        self.assertAlmostEqual(h_t(table, [3, 1]), 0.375)
        self.assertAlmostEqual(
            within_hill_number([{0: 0.5, 1: 0.5}, {0: 1.0}], 0, [3, 1]),
            1.75,
        )
        self.assertGreater(total_hill_number(table, 1, [3, 1]), 1.0)

    def test_hill_orders_and_input_types_are_validated(self) -> None:
        """Hill APIs reject negative, nonnumeric, and nonfinite orders."""
        for order, error in (
            (-1, ValueError),
            (math.inf, ValueError),
            (True, TypeError),
            ("1", TypeError),
        ):
            with self.assertRaises(error):
                hill_number({0: 1.0}, order)

    def test_table_and_deme_validation_reports_bad_inputs(self) -> None:
        """Public statistics reject malformed mappings and frequencies."""
        cases = (
            (object(), "frequency table"),
            ([1], "deme 0"),
            ([{}], "at least one allele"),
            ([{"x": 1.0}], "integer-like"),
            ([{0: -0.1, 1: 1.1}], "finite and non-negative"),
            ([{0: 0.5}], "sum to 1"),
        )
        for table, message in cases:
            with (
                self.subTest(message=message),
                self.assertRaisesRegex((TypeError, ValueError), message),
            ):
                h_s(table)  # type: ignore[arg-type]

    def test_weight_validation_reports_all_contract_failures(self) -> None:
        """Deme weights must match, be numeric, positive, and summable."""
        table = [{0: 1.0}, {1: 1.0}]
        cases = (
            (object(), "sequence"),
            ([1], "expected 2"),
            ([True, 1], "real number"),
            ([0, 1], "strictly positive"),
            ([0.0, 0.0], "strictly positive"),
        )
        for weights, message in cases:
            with (
                self.subTest(message=message),
                self.assertRaisesRegex((TypeError, ValueError), message),
            ):
                h_t(table, weights)  # type: ignore[arg-type]

    def test_differentiation_requires_multiple_demes_and_rejects_weighted_non_q1(
        self,
    ) -> None:
        """Between-deme measures require two demes and equal weighting rules."""
        one = [{0: 1.0}]
        for function in (g_st, jost_d, e_st, k_st):
            with (
                self.subTest(function=function.__name__),
                self.assertRaisesRegex(ValueError, "at least two"),
            ):
                function(one)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "only defined"):
            differentiation_q([{0: 1.0}, {0: 1.0}], 2, [1, 1])

    def test_equilibrium_formulas_validate_inputs_and_zero_mutation(self) -> None:
        """Equilibrium helpers expose exact parameter and zero-mutation contracts."""
        self.assertAlmostEqual(equilibrium_d(0.1, 0.01, 3), 1 / 6)
        self.assertAlmostEqual(equilibrium_g_st(20, 0.1, 0.01, 3), 1 / 10.6)
        with self.assertRaisesRegex(ValueError, "mu greater"):
            equilibrium_d(0.1, 0.0, 3)
        cases = (
            (False, 0.1, 0.01, 3, "positive gene-copy"),
            (1, 0.1, 0.01, 1, "at least 2"),
            (1, -0.1, 0.01, 3, "m must"),
            (1, 0.1, math.inf, 3, "mu must"),
        )
        for population_size, m, mu, d, message in cases:
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(ValueError, message),
            ):
                equilibrium_g_st(population_size, m, mu, d)
