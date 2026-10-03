"""Tests for the statistic catalog, the one list every other list derives from."""

from __future__ import annotations

import itertools
import json
import unittest

from fim.statistics import NEI_DENOMINATORS, NEI_LOCUS_RULES
from fim.statistics.catalog import (
    CATALOG,
    Measure,
    Scope,
    catalog_payload,
    convergence_statistic_keys,
    default_shown_keys,
    history_keys,
    nei_key,
    pair_keys,
    report_keys,
    spec,
)

_ORIGINAL_TEN = (
    "D",
    "G_ST",
    "E_ST",
    "K_ST",
    "H_S",
    "H_T",
    "H_ST",
    "A_CGD",
    "Delta",
    "MI",
)


class CatalogTests(unittest.TestCase):
    """Structure, derived lists and the Nei family's coverage."""

    def test_keys_are_unique(self) -> None:
        """No key appears twice."""
        keys = [entry.key for entry in CATALOG]
        self.assertEqual(len(keys), len(set(keys)))

    def test_original_statistics_keep_their_order_and_defaults(self) -> None:
        """The ten long-standing statistics lead, shown, and stay eligible."""
        self.assertEqual(tuple(entry.key for entry in CATALOG[:10]), _ORIGINAL_TEN)
        self.assertEqual(convergence_statistic_keys(), _ORIGINAL_TEN)
        self.assertEqual(default_shown_keys(), _ORIGINAL_TEN)

    def test_history_policies_match_the_engine_split(self) -> None:
        """Five always tracked, five opt-in; everything new is not tracked."""
        self.assertEqual(history_keys("always"), ("D", "G_ST", "H_S", "H_T", "H_ST"))
        self.assertEqual(
            history_keys("opt_in"), ("E_ST", "K_ST", "A_CGD", "Delta", "MI")
        )

    def test_nei_family_is_complete_and_hidden_by_default(self) -> None:
        """Every measure x scope x denominator x rule exists, hidden, ineligible."""
        measures: tuple[Measure, ...] = ("distance", "identity")
        scopes: tuple[Scope, ...] = ("global", "pair")
        for measure, scope, denominator, locus_rule in itertools.product(
            measures,
            scopes,
            NEI_DENOMINATORS,
            NEI_LOCUS_RULES,
        ):
            entry = spec(nei_key(measure, scope, denominator, locus_rule))
            self.assertEqual(entry.scope, scope)
            self.assertEqual(entry.nei, (measure, denominator, locus_rule))
            self.assertFalse(entry.default_shown)
            self.assertFalse(entry.convergence_eligible)

    def test_scope_lists(self) -> None:
        """Report keys are the global ones; pair keys are the 8 pair Nei members."""
        self.assertEqual(len(pair_keys()), 8)
        self.assertTrue(set(report_keys()).isdisjoint(pair_keys()))
        self.assertEqual(
            set(report_keys()) | set(pair_keys()), {e.key for e in CATALOG}
        )

    def test_attribution_and_negative_note(self) -> None:
        """Arithmetic forms cite Jost; only the geometric all-demes forms warn."""
        arithmetic = spec("NEI_D_PAIR_ARITH")
        self.assertIn("Jost, L. (2026) private communication", arithmetic.description)
        self.assertIn("Nei (1972)", spec("NEI_D_PAIR_GEO").description)
        self.assertIn("negative", spec("NEI_D_ALL_GEO").description)
        self.assertNotIn("negative", spec("NEI_D_ALL_ARITH").description)
        self.assertEqual(spec("NEI_D_ALL_GEO").bounds, (None, None))
        self.assertEqual(spec("NEI_I_PAIR_GEO").bounds, (0.0, 1.0))

    def test_unknown_key_is_named(self) -> None:
        """A typo raises a `KeyError` naming the key."""
        with self.assertRaisesRegex(KeyError, "NOPE"):
            spec("NOPE")

    def test_payload_is_json_and_ordered(self) -> None:
        """The bridge payload round-trips through JSON in catalog order."""
        payload = json.loads(json.dumps(catalog_payload()))
        self.assertEqual([row["key"] for row in payload], [e.key for e in CATALOG])
