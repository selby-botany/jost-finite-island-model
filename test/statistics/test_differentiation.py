"""Focused tests for pure finite-island differentiation statistics."""

from __future__ import annotations

import json
import math
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import cast
from unittest.mock import patch

from fim.statistics import (
    d_m,
    differentiation,
    differentiation_q,
    e_st,
    equilibrium_d,
    equilibrium_g_st,
    g_st,
    g_st_log,
    h_s,
    h_st,
    h_t,
    heterozygosity,
    hill_number,
    identity,
    identity_recovery_equilibrium,
    identity_recovery_half_life,
    identity_recovery_rate,
    identity_recovery_trajectory,
    jost_d,
    k_st,
    r_st,
    statistics_report,
    total_hill_number,
    within_hill_number,
)
from fim.statistics.differentiation import FrequencyTable

DATA_DIRECTORY = Path(__file__).parents[1] / "data" / "statistics"


def _fixture(name: str) -> dict[str, object]:
    """Load one hand-checked golden frequency-table fixture."""
    with (DATA_DIRECTORY / name).open(encoding="utf-8") as fixture_file:
        payload = json.load(fixture_file)
    assert isinstance(payload, dict)
    return payload


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
                # A TypedDict's keys are literals to mypy, but this test walks
                # whatever statistic names each fixture's `expected` object
                # happens to list; re-view the same TypedDict (still a plain
                # dict at runtime) as an ordinary string-keyed mapping so a
                # dynamic key is allowed.
                report_values = cast("Mapping[str, float | None]", report)
                expected = fixture["expected"]
                assert isinstance(expected, dict)
                for name, value in expected.items():
                    self.assertAlmostEqual(report_values[name], value, places=12)

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
        self.assertIsNone(g_st_log(table))

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

    def test_statistics_report_matches_every_independently_validated_function(
        self,
    ) -> None:
        """The single-validation fast path agrees with each public function.

        `statistics_report` no longer computes `H_S`/`H_T`/`G_ST`/`D`/
        `E_ST`/`K_ST` by calling the public `h_s`/`h_t`/`g_st`/`jost_d`/
        `e_st`/`k_st` functions (`20260903-claude-sonnet-5-fim-vg-
        performance-campaign-design.md` §6.1 item 3) — each of those,
        called directly here instead, independently re-validates the
        same table from scratch and (for `g_st`/`jost_d`) recomputes
        `H_S`/`H_T` from scratch too, so agreement here is a genuine
        cross-check of the refactored fast path's own math, not
        circular. `test_golden_statistics`, above, already proves this
        for seven hand-picked fixtures; this proves it for tables that
        exercise ragged widths and private alleles the fixtures may not.
        """
        tables: list[list[dict[int, float]]] = [
            [{0: 0.5, 1: 0.5}, {0: 0.25, 2: 0.75}],
            [{0: 1.0}, {1: 0.5, 2: 0.5}, {0: 0.2, 1: 0.3, 2: 0.5}],
            [{0: 0.1, 1: 0.9}, {0: 0.9, 1: 0.1}, {1: 1.0}, {0: 0.5, 2: 0.5}],
        ]
        for table in tables:
            with self.subTest(table=table):
                report = statistics_report(table)
                report_values = cast("dict[str, float | None]", dict(report))
                expected = {
                    "H_S": h_s(table),
                    "H_T": h_t(table),
                    "H_ST": h_st(table),
                    "G_ST": g_st(table),
                    "D": jost_d(table),
                    "E_ST": e_st(table),
                    "K_ST": k_st(table),
                }
                for name, expected_value in expected.items():
                    observed_value = report_values[name]
                    assert observed_value is not None
                    assert expected_value is not None
                    self.assertAlmostEqual(observed_value, expected_value, places=12)

    def test_statistics_report_validates_the_table_exactly_once(self) -> None:
        """The redundant re-validation this stage removed stays removed.

        Direct mechanism-level proof, not just a matching-output proof:
        before this fix, one `statistics_report` call validated the same
        table roughly eleven times over — once itself, once each inside
        `h_s`/`h_t`/`g_st`/`jost_d`/`e_st`/`k_st`, and a further two
        inside `g_st`'s/`jost_d`'s own internal `h_s`/`h_t` calls
        (measured directly by profiling `_convergence_values_
        vectorized`'s own reference-scale hot path, not assumed from
        reading the code alone). Counts real calls across a table with
        several demes, not just that the final numbers happen to match.
        """
        table = [{0: 0.5, 1: 0.5}, {0: 0.25, 2: 0.75}, {0: 0.4, 1: 0.6}]
        original = differentiation._validate_table
        call_count = 0

        def counting_validate_table(
            frequency_table: FrequencyTable,
        ) -> tuple[dict[int, float], ...]:
            nonlocal call_count
            call_count += 1
            return original(frequency_table)

        with patch.object(differentiation, "_validate_table", counting_validate_table):
            differentiation.statistics_report(table)

        self.assertEqual(call_count, 1)

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
                values.extend(
                    value
                    for value in (g_st(table), g_st_log(table))
                    if value is not None
                )
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
                hill_number({0: 1.0}, order)  # type: ignore[arg-type]

    def test_table_and_deme_validation_reports_bad_inputs(self) -> None:
        """Public statistics reject malformed mappings and frequencies."""
        cases: tuple[tuple[object, str], ...] = (
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
        for function in (g_st, jost_d, e_st, k_st, d_m, r_st, g_st_log):
            with (
                self.subTest(function=function.__name__),
                self.assertRaisesRegex(ValueError, "at least two"),
            ):
                function(one)
        with self.assertRaisesRegex(ValueError, "only defined"):
            differentiation_q([{0: 1.0}, {0: 1.0}], 2, [1, 1])

    def test_two_deme_two_allele_special_case_matches_the_guide(self) -> None:
        """Part VII's "two demes, two alleles" table, by hand from the guide.

        `doc/jost-differentiation-measures.md` Part VII walks through this
        exact four-row table as the one regime where G_ST and D "largely
        evaporate" into agreement; unlike Part IV's own worked examples
        (`test_golden_statistics`, above), no existing fixture covers it.
        The G_ST/D values below are the guide's own printed numbers; E_ST
        and K_ST are not printed there but are asserted anyway since they
        are cheap to also pin down exactly at this small a table.
        """
        cases = (
            ("both demes fixed, different alleles", [{0: 1.0}, {1: 1.0}], 1.0, 1.0),
            (
                "identical frequencies, not both fixed",
                [{0: 0.5, 1: 0.5}, {0: 0.5, 1: 0.5}],
                0.0,
                0.0,
            ),
            (
                "one deme 50/50, the other fixed",
                [{0: 0.5, 1: 0.5}, {0: 1.0}],
                1 / 3,
                1 / 3,
            ),
            ("both demes fixed for the same allele", [{0: 1.0}, {0: 1.0}], None, 0.0),
        )
        for description, table, expected_g_st, expected_d in cases:
            with self.subTest(description=description):
                report = statistics_report(table)
                actual_g_st = report["G_ST"]
                if expected_g_st is None or actual_g_st is None:
                    self.assertIsNone(actual_g_st)
                    self.assertIsNone(expected_g_st)
                else:
                    self.assertAlmostEqual(actual_g_st, expected_g_st, places=6)
                self.assertAlmostEqual(report["D"], expected_d, places=6)

    def test_d_m_matches_its_defining_algebra_and_is_never_negative(self) -> None:
        """Nei's D_m (Eq. 10) equals d/(d-1) * (H_T - H_S) and stays >= 0.

        Unlike every other between-deme measure in this module, `D_m` is
        not rescaled to `[0, 1]` (see its own docstring) — but the first
        table below (both demes fixed for different alleles, `H_S = 0`,
        `H_T = 0.5`) still lands on exactly `1.0`: `d_m`'s own docstring
        explains why `H_S = 0` caps `H_T` at `(d-1)/d`, making `D_m`
        reach exactly `1` there for any `d`, not the looser `d/(d-1)`
        ceiling `H_T <= 1` alone would suggest.
        """
        tables = (
            [{0: 1.0}, {1: 1.0}],
            [{0: 0.5, 1: 0.5}, {0: 0.5, 1: 0.5}],
            [{0: 0.1, 1: 0.9}, {0: 0.9, 1: 0.1}],
        )
        for table in tables:
            with self.subTest(table=table):
                deme_count = len(table)
                expected = (deme_count / (deme_count - 1)) * (h_t(table) - h_s(table))
                self.assertAlmostEqual(d_m(table), expected)
                self.assertGreaterEqual(d_m(table), 0.0)
        self.assertAlmostEqual(d_m([{0: 1.0}, {1: 1.0}]), 1.0)

    def test_r_st_is_d_m_over_h_s_and_none_at_zero_within_deme_diversity(self) -> None:
        """Nei's R_ST (Eq. 11) is D_m/H_S, undefined when H_S is zero."""
        table = [{0: 0.1, 1: 0.9}, {0: 0.9, 1: 0.1}]
        actual_r_st = r_st(table)
        assert actual_r_st is not None
        self.assertAlmostEqual(actual_r_st, d_m(table) / h_s(table))
        self.assertIsNone(r_st([{0: 1.0}, {1: 1.0}]))

    def test_g_st_log_matches_a_hand_worked_value(self) -> None:
        """The log-based large-differentiation G_ST estimator, by hand.

        Design-doc worked case: `H_S = 0.18`, `H_T = 0.5`, so
        `J_S = 0.82`, `J_T = 0.5`, giving
        `ln(0.82/0.5) / -ln(0.5) ~= 0.713696` — larger than the ordinary
        linear `g_st` (`0.64`) at this same table, as Nei's own paper
        anticipates for the log form away from the zero-differentiation
        endpoint.
        """
        table = [{0: 0.9, 1: 0.1}, {0: 0.1, 1: 0.9}]
        self.assertAlmostEqual(h_s(table), 0.18)
        self.assertAlmostEqual(h_t(table), 0.5)
        actual_g_st = g_st(table)
        assert actual_g_st is not None
        self.assertAlmostEqual(actual_g_st, 0.64)
        actual_g_st_log = g_st_log(table)
        assert actual_g_st_log is not None
        self.assertAlmostEqual(actual_g_st_log, 0.713696, places=6)
        self.assertGreater(actual_g_st_log, actual_g_st)

    def test_g_st_log_agrees_with_g_st_at_the_complete_fixation_endpoint(self) -> None:
        """Both G_ST estimators equal exactly 1 when demes fix apart.

        The one case where the two necessarily agree — `H_S = 0` (no
        diversity within either deme) and each deme fixed for a
        different allele — checked exactly, not approximately, since
        both forms reduce to closed integers here.
        """
        table = [{0: 1.0}, {1: 1.0}]
        self.assertEqual(g_st(table), 1.0)
        self.assertEqual(g_st_log(table), 1.0)

    def test_duplicate_canonical_allele_id_is_rejected(self) -> None:
        """Two distinct dict keys that normalize to the same id collide.

        `_validate_deme` canonicalizes every allele id through
        `operator.index` before checking for duplicates, so triggering the
        duplicate check needs two dict keys that are genuinely distinct
        Python objects (different identity, not `==` to each other, so both
        survive as separate entries in the same dict) yet resolve to the
        same integer identity through `__index__` — an ordinary `int` key
        can never collide with itself this way, which is why this path has
        no other test.
        """

        class _AlwaysIndexesToOne:
            def __index__(self) -> int:
                return 1

        with self.assertRaisesRegex(ValueError, "duplicate allele ID"):
            h_s([{1: 0.5, _AlwaysIndexesToOne(): 0.5}])

    def test_within_hill_number_order_one_is_the_alpha_entropy_exponential(
        self,
    ) -> None:
        """q=1 within-deme ("alpha") Hill diversity is exp of mean entropy.

        `_hill`'s own docstring: order=0 and order=2 are both limiting cases
        computed via their own closed forms, handled separately from the
        general power-sum formula; `within_hill_number` mirrors that same
        three-way split, but only its order 0 and order 2 branches were
        previously exercised (`test_weighted_heterozygosity_and_hill_numbers_
        use_weights`, `test_differentiation_endpoints_and_hill_partition`).
        Chosen so the expected value has a clean closed form: with equal
        deme weights, one deme fixed (entropy 0) and one deme an even
        two-allele split (entropy ln 2), the weighted mean entropy is
        (ln 2)/2, and exp((ln 2)/2) = sqrt(2) exactly.
        """
        table = [{0: 0.5, 1: 0.5}, {0: 1.0}]
        self.assertAlmostEqual(within_hill_number(table, 1), math.sqrt(2))

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

    def test_identity_recovery_trajectory_at_zero_generations_is_the_start(
        self,
    ) -> None:
        """No elapsed time means no change: f_0[t=0] == f0_initial exactly."""
        self.assertEqual(
            identity_recovery_trajectory(0.42, 100, 0.05, 0),
            0.42,
        )

    def test_identity_recovery_half_life_lands_on_the_exact_midpoint(self) -> None:
        """Whitlock (1992)'s t_1/2 is where the trajectory is exactly halfway.

        Checked both directions -- starting above the equilibrium (the
        trajectory decreases) and starting below it (the trajectory
        increases) -- since `identity_recovery_rate` is the same either
        way and the paper's own derivation makes no directional
        assumption.
        """
        population_size = 100
        m = 0.05
        equilibrium = identity_recovery_equilibrium(population_size, m)
        half_life = identity_recovery_half_life(population_size, m)
        for f0_initial in (0.8, 0.01):
            with self.subTest(f0_initial=f0_initial):
                midpoint = f0_initial + (equilibrium - f0_initial) / 2.0
                self.assertAlmostEqual(
                    identity_recovery_trajectory(
                        f0_initial, population_size, m, half_life
                    ),
                    midpoint,
                )

    def test_identity_recovery_rate_is_bounded_in_the_unit_interval(self) -> None:
        """L = (1-m)^2 * (1 - 1/N) stays in [0, 1) for every valid input."""
        for population_size, m in ((1, 0.0), (2, 0.0), (100, 0.05), (1000, 1.0)):
            with self.subTest(population_size=population_size, m=m):
                rate = identity_recovery_rate(population_size, m)
                self.assertGreaterEqual(rate, 0.0)
                self.assertLess(rate, 1.0)

    def test_identity_recovery_half_life_is_zero_at_full_replacement(self) -> None:
        """At m=1, L=0 and half-life is the correct limit, 0, not a crash."""
        self.assertEqual(identity_recovery_half_life(100, 1.0), 0.0)

    def test_identity_recovery_functions_validate_their_inputs(self) -> None:
        """The identity-recovery family rejects the same malformed inputs
        as the rest of this module's equilibrium-formula family."""
        with self.assertRaisesRegex(ValueError, "positive gene-copy"):
            identity_recovery_rate(0, 0.05)
        with self.assertRaisesRegex(ValueError, "m must"):
            identity_recovery_rate(100, 1.5)
        with self.assertRaisesRegex(ValueError, "positive gene-copy"):
            identity_recovery_equilibrium(0, 0.05)
        with self.assertRaisesRegex(ValueError, "m must"):
            identity_recovery_half_life(100, -0.1)
        with self.assertRaisesRegex(ValueError, "f0_initial must"):
            identity_recovery_trajectory(1.5, 100, 0.05, 10)
        with self.assertRaisesRegex(ValueError, "generations must"):
            identity_recovery_trajectory(0.5, 100, 0.05, -1)
        with self.assertRaisesRegex(ValueError, "generations must"):
            identity_recovery_trajectory(0.5, 100, 0.05, math.inf)
