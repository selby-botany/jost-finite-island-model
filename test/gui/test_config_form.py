"""Unit tests for all six tabs' marshaling (no Tk).

`test_config_form_round_trips_starter_config` is this package's own
named regression test — every tab now exists, so `starter_form_values()`'s
output round-trips through `form_values_to_payload` back into an
equivalent `SimulationParams`.
"""

from __future__ import annotations

import pytest
import yaml

from fim.cli import STARTER_CONFIG
from fim.gui import config_form
from fim.model.locus import LocusSpec
from fim.model.params import SimulationParams


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


def test_all_fields_covers_every_tabs_plain_fields() -> None:
    """`all_fields()` names every tab's plain fields; composites are excluded."""
    names = {field.name for field in config_form.all_fields()}

    assert names == {
        "N",
        "d",
        "seed",
        "deme_weighting",
        "max_generations",
        "migrant_sampling",
        "mutation_model",
        "locus_lengths",
        "initial_allele_count",
        "initial_concentration",
        "convergence_combinator",
        "convergence_window",
        "convergence_tolerance",
        "n_replicates",
        "replicate_tolerance",
        "replicate_minimum",
        "replicate_confidence",
    }
    # `m`, `mu`/`mu_b`, `convergence_statistic`, and `p_0` are composite
    # (mode selectors or read-only summaries) — never plain `FormField`s.
    assert "m" not in names
    assert "mu" not in names
    assert "convergence_statistic" not in names
    assert "p_0" not in names


def test_config_form_round_trips_starter_config() -> None:
    """`starter_form_values()` round-trips into an equivalent `SimulationParams`.

    Reachable only once every
    tab exists — `form_values_to_payload` now covers every key
    `SimulationParams.from_mapping` requires.
    """
    starter_params = SimulationParams.from_mapping(yaml.safe_load(STARTER_CONFIG))

    payload = config_form.form_values_to_payload(config_form.starter_form_values())
    restored = SimulationParams.from_mapping(payload)

    assert restored == starter_params


def test_form_values_to_payload_parses_every_plain_field_kind() -> None:
    """Int, choice, and int_list fields all coerce to the right Python type."""
    values = dict(config_form.starter_form_values())
    values.update(
        {
            "N": "450",
            "d": "20",
            "seed": "7",
            "deme_weighting": "equal",
            "max_generations": "1000",
            "migrant_sampling": "stochastic",
            "m_mode": "scalar",
            "m_rate": "0.01",
        }
    )

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
    values = dict(config_form.starter_form_values())
    values.update({"N": "200, 300, 150", "d": "3"})

    payload = config_form.form_values_to_payload(values)

    assert payload["N"] == [200, 300, 150]


def test_form_values_to_payload_rejects_a_non_integer_n_item() -> None:
    """A bad per-deme N entry names its own index, matching `_parse_population_size`."""
    values = dict(config_form.starter_form_values())
    values.update({"N": "200, oops", "d": "2"})

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
    """`m`/`m.topology`/`m.rate` and unknown-key errors have no inline widget.

    `m` has no single `FormField` of its own (the scalar/topology
    selector is composite), so its own and its sub-fields' messages are
    deliberately unmatched here — `tab_for_error` (below) still routes
    them to Migration for the banner/tab-dot, but there is no per-field
    widget to show them beside.
    """
    assert config_form.field_for_error("m must be a number") is None
    assert config_form.field_for_error("m.topology must be 'ring' or 'linear'") is None
    assert config_form.field_for_error("unknown configuration key(s): bogus") is None


def _params(**overrides: object) -> SimulationParams:
    """Build one minimal, otherwise-valid `SimulationParams` for these tests."""
    fields: dict[str, object] = {
        "N": 20,
        "m": 0.1,
        "mu": 0.001,
        "d": 2,
        "seed": 7,
    }
    fields.update(overrides)
    return SimulationParams(**fields)  # type: ignore[arg-type]


def test_form_values_to_payload_accepts_a_per_locus_length_list() -> None:
    """A comma-separated `locus_lengths` derives `n_loci` from its own item count.

    The cardinality rule's O(loci) case (`doc/fim-gui-design.md` §6.1),
    this package's own named test's counterpart for `locus_lengths` rather than a
    per-locus `mu` list — this form has no such widget (G11 scopes
    `mu`/`mu_b` to shared scalars only; see `mu_from_params`'s own
    per-locus rejection below).
    """
    values = dict(config_form.starter_form_values())
    values["locus_lengths"] = "50, 8000"

    payload = config_form.form_values_to_payload(values)

    assert payload["locus_lengths"] == [50, 8000]
    assert payload["n_loci"] == 2


def test_form_values_to_payload_derives_n_loci_one_from_a_bare_length() -> None:
    """A single, comma-free `locus_lengths` value means `n_loci == 1`."""
    values = dict(config_form.starter_form_values())
    values["locus_lengths"] = "200"

    payload = config_form.form_values_to_payload(values)

    assert payload["locus_lengths"] == 200
    assert payload["n_loci"] == 1


def test_form_values_to_payload_treats_replicate_tolerance_empty_as_unset() -> None:
    """An empty `replicate_tolerance` field submits `None`, not an error."""
    values = dict(config_form.starter_form_values())
    values["replicate_tolerance"] = ""

    payload = config_form.form_values_to_payload(values)

    assert payload["replicate_tolerance"] is None


def test_form_values_to_payload_parses_a_set_replicate_tolerance() -> None:
    """A non-empty `replicate_tolerance` field parses as a float."""
    values = dict(config_form.starter_form_values())
    values["replicate_tolerance"] = "0.05"

    payload = config_form.form_values_to_payload(values)

    assert payload["replicate_tolerance"] == 0.05


def test_form_values_to_payload_converts_replicate_confidence_to_a_float() -> None:
    """`replicate_confidence`'s "float_choice" kind submits a float, not a string."""
    values = dict(config_form.starter_form_values())
    values["replicate_confidence"] = "0.99"

    payload = config_form.form_values_to_payload(values)

    assert payload["replicate_confidence"] == pytest.approx(0.99)
    assert isinstance(payload["replicate_confidence"], float)


def test_mu_to_payload_mu_mode_returns_a_bare_mu_key() -> None:
    """`mu` mode submits `{"mu": ...}` only."""
    payload = config_form.mu_to_payload(
        {"mu_mode": "mu", "mu_value": "0.001", "mu_b_value": ""}
    )

    assert payload == {"mu": 0.001}


def test_mu_to_payload_mu_b_mode_returns_a_bare_mu_b_key() -> None:
    """`mu_b` mode submits `{"mu_b": ...}` only — exclusive with `mu`."""
    payload = config_form.mu_to_payload(
        {"mu_mode": "mu_b", "mu_value": "", "mu_b_value": "0.00003"}
    )

    assert payload == {"mu_b": 0.00003}


def test_mu_to_payload_rejects_an_unknown_mode() -> None:
    """An unrecognized mode is a clear programming error, not a silent default."""
    with pytest.raises(ValueError, match="unknown mu selector mode"):
        config_form.mu_to_payload(
            {"mu_mode": "bogus", "mu_value": "", "mu_b_value": ""}
        )


def test_mu_from_params_scalar_mu_renders_mu_mode() -> None:
    """A scalar `params.mu` always renders as `mu_mode="mu"`."""
    values = config_form.mu_from_params(_params(mu=0.002))

    assert values == {"mu_mode": "mu", "mu_value": "0.002", "mu_b_value": ""}


def test_mu_from_params_rejects_a_genuinely_per_locus_mu() -> None:
    """A per-locus `mu` (unequal rates across loci) has no form representation."""
    params = _params(
        mu=(0.001, 0.05),
        loci=(LocusSpec(1, 50), LocusSpec(2, 8000)),
    )

    with pytest.raises(ValueError, match="per-locus mu"):
        config_form.mu_from_params(params)


def test_m_from_params_matrix_shows_the_loaded_badge() -> None:
    """A matrix-shaped `m` renders `m_mode="loaded"` with a size summary."""
    params = _params(d=3, m=((0.9, 0.05, 0.05), (0.05, 0.9, 0.05), (0.05, 0.05, 0.9)))

    values = config_form.m_from_params(params)

    assert values["m_mode"] == "loaded"
    assert "3" in values["m_loaded_summary"]
    assert "loaded from file" in values["m_loaded_summary"]


def test_m_to_payload_rejects_loaded_mode() -> None:
    """ "Loaded" mode has no editable payload — the screen must splice it in itself."""
    with pytest.raises(ValueError, match="cannot be edited here"):
        config_form.m_to_payload(
            {
                "m_mode": "loaded",
                "m_rate": "",
                "m_topology": "ring",
                "m_topology_rate": "",
            }
        )


def test_convergence_statistic_to_payload_returns_a_bare_string_for_one_checked() -> (
    None
):
    """Exactly one checked statistic submits as a bare string, matching `to_dict()`."""
    values = {f"cs_{name}": "false" for name in config_form.CONVERGENCE_STATISTIC_NAMES}
    values["cs_G_ST"] = "true"

    assert config_form.convergence_statistic_to_payload(values) == "G_ST"


def test_convergence_statistic_to_payload_returns_a_list_for_several_checked() -> None:
    """Two or more checked statistics submit as a list, in canonical order."""
    values = {f"cs_{name}": "false" for name in config_form.CONVERGENCE_STATISTIC_NAMES}
    values["cs_H_T"] = "true"
    values["cs_D"] = "true"

    assert config_form.convergence_statistic_to_payload(values) == ["D", "H_T"]


def test_convergence_statistic_from_params_checks_only_the_watched_names() -> None:
    """`convergence_statistic_from_params` checks exactly the watched statistics."""
    values = config_form.convergence_statistic_from_params(
        _params(convergence_statistic=("D", "K_ST"))
    )

    assert values["cs_D"] == "true"
    assert values["cs_K_ST"] == "true"
    assert values["cs_G_ST"] == "false"
    assert values["cs_E_ST"] == "false"
    assert values["cs_H_S"] == "false"
    assert values["cs_H_T"] == "false"


def test_p0_summary_from_params_is_empty_when_not_loaded() -> None:
    """No `p_0` means an empty summary — nothing to badge."""
    assert config_form.p0_summary_from_params(_params()) == ""


def test_p0_summary_from_params_describes_a_loaded_p0() -> None:
    """A loaded `p_0` renders a non-empty, informative summary."""
    params = _params(d=2, initial_frequencies=(({0: 1.0},), ({0: 1.0},)))

    summary = config_form.p0_summary_from_params(params)

    assert summary != ""
    assert "loaded from file" in summary


def test_params_to_form_values_rejects_custom_locus_ids() -> None:
    """A `loci` list with non-default-position IDs has no form representation."""
    params = _params(loci=(LocusSpec(locus_id=5, length=200),))

    with pytest.raises(ValueError, match="custom locus IDs"):
        config_form.params_to_form_values(params)


def test_params_to_form_values_includes_every_composite_fields_keys() -> None:
    """A round-tripped params object populates every composite's own keys too."""
    values = config_form.params_to_form_values(_params())

    for key in ("m_mode", "mu_mode", "p0_summary"):
        assert key in values
    for name in config_form.CONVERGENCE_STATISTIC_NAMES:
        assert f"cs_{name}" in values


@pytest.mark.parametrize(
    ("name", "expected_tab"),
    [
        ("N", "population"),
        ("d", "population"),
        ("locus_lengths", "mutation"),
        ("initial_allele_count", "initial_conditions"),
        ("convergence_window", "convergence"),
        ("n_replicates", "batch"),
        ("m", "migration"),
        ("mu", "mutation"),
        ("mu_b", "mutation"),
        ("convergence_statistic", "convergence"),
    ],
)
def test_tab_for_field_finds_every_plain_and_composite_field(
    name: str, expected_tab: str
) -> None:
    """Every plain `FormField` and every composite field resolves to its own tab."""
    assert config_form.tab_for_field(name) == expected_tab


def test_tab_for_field_returns_none_for_an_unknown_name() -> None:
    """A name this form exposes nowhere at all resolves to no tab."""
    assert config_form.tab_for_field("bogus") is None


@pytest.mark.parametrize(
    ("message", "expected_tab"),
    [
        ("d must be an integer", "population"),
        ("N[0] must be an integer", "population"),
        ("mu must be a number", "mutation"),
        ("mu_b must be a number", "mutation"),
        ("m must be a number", "migration"),
        ("m.topology must be 'ring' or 'linear'", "migration"),
        ("m.rate must be a number", "migration"),
        ("convergence_statistic must not be empty", "convergence"),
        ("replicate_minimum cannot exceed n_replicates", "batch"),
    ],
)
def test_tab_for_error_routes_plain_and_composite_messages(
    message: str, expected_tab: str
) -> None:
    """`tab_for_error` finds the right tab for both plain and composite messages.

    Regression proof that `mu`/`mu_b`/`m.topology`/`m.rate` messages
    are not misrouted to Migration merely because they also start with
    the single letter `m` — longer, more specific prefixes are checked
    first.
    """
    assert config_form.tab_for_error(message) == expected_tab


def test_tab_for_error_returns_none_for_an_unknown_key_message() -> None:
    """A message naming no field this form exposes resolves to no tab."""
    assert config_form.tab_for_error("unknown configuration key(s): bogus") is None
