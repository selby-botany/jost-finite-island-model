"""Unit tests for the Population/Migration tab marshaling (no Tk).

Full round-tripping through `SimulationParams.from_mapping` needs every
tab's fields (`mu`, `loci`, ...), which this milestone's later commits
add — see `test_config_form_round_trips_starter_config` once the
remaining four tabs exist. This file covers exactly what this commit
builds: `all_fields()`'s two tabs, the `m` scalar/topology selector,
and `field_for_error`'s routing for both.
"""

from __future__ import annotations

import pytest

from fim.gui import config_form


def test_starter_form_values_reflects_the_cli_starter_config() -> None:
    """`starter_form_values` matches `fim.cli.STARTER_CONFIG`'s own values."""
    values = config_form.starter_form_values()

    assert values["N"] == "450"
    assert values["d"] == "20"
    assert values["seed"] == "20260814"
    assert values["deme_weighting"] == "size"
    assert values["max_generations"] == "10000"
    assert values["migrant_sampling"] == "continuous"
    assert values["m_mode"] == "scalar"
    assert values["m_rate"] == "0.001"


def test_all_fields_covers_population_and_migration_tabs() -> None:
    """`all_fields()` names exactly this commit's two tabs' plain fields."""
    names = {field.name for field in config_form.all_fields()}

    assert names == {
        "N",
        "d",
        "seed",
        "deme_weighting",
        "max_generations",
        "migrant_sampling",
    }


def test_form_values_to_payload_parses_every_plain_field_kind() -> None:
    """Int, choice, and int_list fields all coerce to the right Python type."""
    values = {
        "N": "450",
        "d": "20",
        "seed": "7",
        "deme_weighting": "equal",
        "max_generations": "1000",
        "migrant_sampling": "stochastic",
        "m_mode": "scalar",
        "m_rate": "0.01",
        "m_topology": "ring",
        "m_topology_rate": "0.1",
    }

    payload = config_form.form_values_to_payload(values)

    assert payload["N"] == 450
    assert payload["d"] == 20
    assert payload["seed"] == 7
    assert payload["deme_weighting"] == "equal"
    assert payload["max_generations"] == 1000
    assert payload["migrant_sampling"] == "stochastic"
    assert payload["m"] == 0.01


def test_form_values_to_payload_accepts_a_per_deme_n_list() -> None:
    """A comma-separated `N` becomes a list — the O(d) cardinality-rule case."""
    values = {
        "N": "200, 300, 150",
        "d": "3",
        "seed": "7",
        "deme_weighting": "size",
        "max_generations": "1000",
        "migrant_sampling": "continuous",
        "m_mode": "scalar",
        "m_rate": "0.01",
        "m_topology": "ring",
        "m_topology_rate": "0.1",
    }

    payload = config_form.form_values_to_payload(values)

    assert payload["N"] == [200, 300, 150]


def test_form_values_to_payload_rejects_a_non_integer_n_item() -> None:
    """A bad per-deme N entry names its own index, matching `_parse_population_size`."""
    values = {
        "N": "200, oops",
        "d": "2",
        "seed": "7",
        "deme_weighting": "size",
        "max_generations": "1000",
        "migrant_sampling": "continuous",
        "m_mode": "scalar",
        "m_rate": "0.01",
        "m_topology": "ring",
        "m_topology_rate": "0.1",
    }

    with pytest.raises(ValueError, match=r"N\[1\] must be an integer"):
        config_form.form_values_to_payload(values)


def test_m_to_payload_scalar_mode_returns_a_bare_float() -> None:
    """Scalar mode's payload is a bare float, `_parse_migration`'s first shape."""
    payload = config_form.m_to_payload(
        {
            "m_mode": "scalar",
            "m_rate": "0.05",
            "m_topology": "ring",
            "m_topology_rate": "",
        }
    )

    assert payload == 0.05


def test_m_to_payload_topology_mode_returns_a_topology_mapping() -> None:
    """Topology mode's payload matches `_migration_from_topology`'s expected shape."""
    payload = config_form.m_to_payload(
        {
            "m_mode": "topology",
            "m_rate": "",
            "m_topology": "linear",
            "m_topology_rate": "0.2",
        }
    )

    assert payload == {"topology": "linear", "rate": 0.2}


def test_m_to_payload_rejects_an_unknown_mode() -> None:
    """An unrecognized mode is a clear programming error, not a silent default."""
    with pytest.raises(ValueError, match="unknown m selector mode"):
        config_form.m_to_payload(
            {
                "m_mode": "bogus",
                "m_rate": "",
                "m_topology": "ring",
                "m_topology_rate": "",
            }
        )


def test_field_for_error_matches_a_bare_n_message() -> None:
    """An `N`-shape error (not a list-item error) still routes to `N`."""
    assert (
        config_form.field_for_error("N must be an integer or a list of integers") == "N"
    )


def test_field_for_error_matches_an_n_list_item_message() -> None:
    """A per-item `N[i]` error also routes to the one `N` field."""
    assert config_form.field_for_error("N[0] must be an integer") == "N"


def test_field_for_error_matches_a_plain_field_message() -> None:
    """A plain field's own error routes to it by its exact name prefix."""
    assert config_form.field_for_error("d must be an integer") == "d"


def test_field_for_error_returns_none_for_an_unmatched_message() -> None:
    """`m`/`m.topology`/`m.rate` and unknown-key errors fall through to the banner.

    `m` has no single `FormField` of its own (the scalar/topology
    selector is composite), so its own and its sub-fields' messages are
    deliberately unmatched here — the banner is where they show until a
    later commit's tab-flagging work (§7.3's fifth bullet) exists.
    """
    assert config_form.field_for_error("m must be a number") is None
    assert config_form.field_for_error("m.topology must be 'ring' or 'linear'") is None
    assert config_form.field_for_error("unknown configuration key(s): bogus") is None
