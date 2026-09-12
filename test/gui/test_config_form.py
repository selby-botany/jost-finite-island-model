"""Unit tests for all six tabs' marshaling (no Tk).

`test_config_form_round_trips_starter_config` is this package's own
named regression test — every tab now exists, so `starter_form_values()`'s
output round-trips through `form_values_to_payload` back into an
equivalent `SimulationParams`.
"""

from __future__ import annotations

import json

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
        "initial_allele_count",
        "initial_concentration",
        "convergence_combinator",
        "convergence_window",
        "convergence_tolerance",
        "track_expensive_statistics",
        "n_replicates",
        "replicate_tolerance",
        "replicate_minimum",
        "replicate_confidence",
        "engine_backend",
    }
    # `m`, `mu`/`mu_b`, `loci`/`locus_lengths`, `convergence_statistic`,
    # and `p_0` are all composite mode selectors — never plain
    # `FormField`s.
    assert "m" not in names
    assert "locus_lengths" not in names
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


def test_form_values_to_payload_coerces_a_bool_field_from_true_false_text() -> None:
    """A "bool" field coerces the literal "true"/"false" text a checkbox writes.

    `track_expensive_statistics` is this form's first plain "bool"
    `FormField` — unlike `sigma_band_enabled`, it needs no dedicated
    `*_to_payload` function of its own; the generic `all_fields()`
    dispatch loop in `form_values_to_payload` handles it directly.
    """
    checked = dict(config_form.starter_form_values())
    checked["track_expensive_statistics"] = "true"
    unchecked = dict(config_form.starter_form_values())
    unchecked["track_expensive_statistics"] = "false"

    checked_payload = config_form.form_values_to_payload(checked)
    unchecked_payload = config_form.form_values_to_payload(unchecked)

    assert checked_payload["track_expensive_statistics"] is True
    assert unchecked_payload["track_expensive_statistics"] is False


def test_params_to_form_values_renders_track_expensive_statistics_as_text() -> None:
    """`params_to_form_values` renders the field back as literal "true"/"false"."""
    enabled = SimulationParams(
        N=10, m=0.1, mu=0.0, d=2, seed=1, track_expensive_statistics=True
    )
    disabled = SimulationParams(
        N=10, m=0.1, mu=0.0, d=2, seed=1, track_expensive_statistics=False
    )

    enabled_values = config_form.params_to_form_values(enabled)
    disabled_values = config_form.params_to_form_values(disabled)

    assert enabled_values["track_expensive_statistics"] == "true"
    assert disabled_values["track_expensive_statistics"] == "false"


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


def test_form_values_to_payload_raises_value_error_for_a_missing_field() -> None:
    """A missing key surfaces as `ValueError`, not a bare `KeyError`.

    Confirmed live against a real `preferences.json` predating
    `loci_mode`: `Api.get_initial_form` re-validates a saved form and
    relies on catching exactly `ValueError` to discard one that no
    longer matches the current field set, falling back to
    `starter_form_values()` (its own docstring). Before this test, a
    field simply absent from `values` -- the schema-drift shape the CLI
    starter config already hit once with `n_replicates` -- raised
    `KeyError` instead, which that `except ValueError` never catches:
    the bridge call surfaced to a real, already-launched app as a
    permanently blank Run-destination canvas with no error shown
    anywhere a double-clicked `.app` user could see, since pywebview
    only prints an uncaught bridge exception to a terminal nothing
    launched from Finder has.
    """
    values = dict(config_form.starter_form_values())
    del values["loci_mode"]

    with pytest.raises(ValueError, match=r"missing field.*loci_mode"):
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


def test_initial_conditions_to_payload_dirichlet_omits_equilibrium_fields() -> None:
    """Dirichlet mode's payload has none of the three equilibrium keys at all.

    Omitted, not set to `None` or an empty string — `SimulationParams.
    from_mapping`'s own equilibrium fields default to `None` by
    absence, exactly like `replicate_tolerance`'s own omission
    convention.
    """
    payload = config_form.initial_conditions_to_payload(
        {
            "initial_conditions_mode": "dirichlet",
            "equilibrium_convergence_window": "",
            "equilibrium_convergence_tolerance": "",
            "equilibrium_max_generations": "",
        }
    )

    assert payload == {}


def test_initial_conditions_to_payload_equilibrium_split_mode_parses_all_three() -> (
    None
):
    """Equilibrium-split mode submits all three fields, parsed to their own types."""
    payload = config_form.initial_conditions_to_payload(
        {
            "initial_conditions_mode": "equilibrium_split",
            "equilibrium_convergence_window": "50",
            "equilibrium_convergence_tolerance": "0.01",
            "equilibrium_max_generations": "10000",
        }
    )

    assert payload == {
        "equilibrium_convergence_window": 50,
        "equilibrium_convergence_tolerance": 0.01,
        "equilibrium_max_generations": 10000,
    }


def test_initial_conditions_to_payload_rejects_an_invalid_equilibrium_field() -> None:
    """A bad equilibrium field's own error names that field, like any other."""
    with pytest.raises(ValueError, match="equilibrium_max_generations must be"):
        config_form.initial_conditions_to_payload(
            {
                "initial_conditions_mode": "equilibrium_split",
                "equilibrium_convergence_window": "50",
                "equilibrium_convergence_tolerance": "0.01",
                "equilibrium_max_generations": "not-a-number",
            }
        )


def test_initial_conditions_from_params_dirichlet_is_all_empty() -> None:
    """An ordinary Dirichlet-mode configuration renders empty equilibrium fields."""
    values = config_form.initial_conditions_from_params(_params())

    assert values == {
        "initial_conditions_mode": "dirichlet",
        "equilibrium_convergence_window": "",
        "equilibrium_convergence_tolerance": "",
        "equilibrium_max_generations": "",
        "p0_json": "",
        "fixed_per_deme_choice": "all_same",
    }


def test_initial_conditions_from_params_equilibrium_split_round_trips() -> None:
    """An equilibrium-split configuration's three fields render back exactly."""
    params = _params(
        equilibrium_convergence_window=50,
        equilibrium_convergence_tolerance=0.01,
        equilibrium_max_generations=10000,
    )

    values = config_form.initial_conditions_from_params(params)

    assert values == {
        "initial_conditions_mode": "equilibrium_split",
        "equilibrium_convergence_window": "50",
        "equilibrium_convergence_tolerance": "0.01",
        "equilibrium_max_generations": "10000",
        "p0_json": "",
        "fixed_per_deme_choice": "all_same",
    }


def test_initial_conditions_to_payload_explicit_p0_mode_parses_p0_json() -> None:
    """Explicit-p0 mode's payload is `{"p_0": ...}`, parsed from the grid's own JSON."""
    payload = config_form.initial_conditions_to_payload(
        {
            "initial_conditions_mode": "explicit_p0",
            "p0_json": '[[{"0": 0.5, "1": 0.5}], [{"0": 1.0}]]',
        }
    )

    assert payload == {"p_0": [[{"0": 0.5, "1": 0.5}], [{"0": 1.0}]]}


def test_initial_conditions_to_payload_rejects_malformed_p0_json() -> None:
    """A syntactically invalid `p0_json` is a clear error, not a crash."""
    with pytest.raises(ValueError, match="valid JSON"):
        config_form.initial_conditions_to_payload(
            {"initial_conditions_mode": "explicit_p0", "p0_json": "{not valid"}
        )


@pytest.mark.parametrize(
    "malformed",
    ['"not-a-list"', "[1]", "[[1]]", '[[{"0": "not-a-number"}]]'],
)
def test_initial_conditions_to_payload_rejects_the_wrong_p0_shape(
    malformed: str,
) -> None:
    """Valid JSON that is not demes-of-loci-of-frequency-mappings is still rejected."""
    with pytest.raises(ValueError, match="p_0"):
        config_form.initial_conditions_to_payload(
            {"initial_conditions_mode": "explicit_p0", "p0_json": malformed}
        )


def test_initial_conditions_to_payload_rejects_an_unknown_mode() -> None:
    """An unrecognized mode is a clear programming error, not a silent default."""
    with pytest.raises(ValueError, match="unknown initial_conditions selector mode"):
        config_form.initial_conditions_to_payload({"initial_conditions_mode": "bogus"})


def test_initial_conditions_from_params_explicit_p0_round_trips() -> None:
    """An explicit `p_0` configuration renders back as a real, loadable grid.

    Submitting that grid's own values back reproduces the identical
    `p_0` — the same "loaded badge to real editor" upgrade `m_from_
    params`'s own `"matrix"` mode already made for a loaded migration
    matrix, and `loci_from_params`'s own `"custom"` mode for custom
    locus IDs.
    """
    params = _params(d=2, initial_frequencies=(({0: 1.0},), ({0: 0.5, 1: 0.5},)))

    values = config_form.initial_conditions_from_params(params)

    assert values["initial_conditions_mode"] == "explicit_p0"
    assert json.loads(values["p0_json"]) == [[{"0": 1.0}], [{"0": 0.5, "1": 0.5}]]

    payload = config_form.initial_conditions_to_payload(values)

    assert payload == {"p_0": [[{"0": 1.0}], [{"0": 0.5, "1": 0.5}]]}


def test_initial_conditions_to_payload_fixed_per_deme_all_different() -> None:
    """ "All different" fixes deme *i* for allele *i*, for every locus."""
    values = dict(config_form.starter_form_values())
    values.update(
        {
            "initial_conditions_mode": "fixed_per_deme",
            "fixed_per_deme_choice": "all_different",
            "d": "3",
            "loci_mode": "lengths",
            "locus_lengths": "200",
        }
    )

    payload = config_form.initial_conditions_to_payload(values)

    assert payload == {"p_0": [[{"0": 1.0}], [{"1": 1.0}], [{"2": 1.0}]]}


def test_initial_conditions_to_payload_fixed_per_deme_all_same() -> None:
    """ "All same" fixes every deme for allele 0 -- the no-differentiation baseline."""
    values = dict(config_form.starter_form_values())
    values.update(
        {
            "initial_conditions_mode": "fixed_per_deme",
            "fixed_per_deme_choice": "all_same",
            "d": "3",
            "loci_mode": "lengths",
            "locus_lengths": "200",
        }
    )

    payload = config_form.initial_conditions_to_payload(values)

    assert payload == {"p_0": [[{"0": 1.0}], [{"0": 1.0}], [{"0": 1.0}]]}


def test_initial_conditions_to_payload_fixed_per_deme_all_but_one() -> None:
    """ "All but one": every deme but the last is allele 0; the last is allele 1."""
    values = dict(config_form.starter_form_values())
    values.update(
        {
            "initial_conditions_mode": "fixed_per_deme",
            "fixed_per_deme_choice": "all_but_one",
            "d": "3",
            "loci_mode": "lengths",
            "locus_lengths": "200",
        }
    )

    payload = config_form.initial_conditions_to_payload(values)

    assert payload == {"p_0": [[{"0": 1.0}], [{"0": 1.0}], [{"1": 1.0}]]}


def test_initial_conditions_to_payload_fixed_per_deme_applies_to_every_locus() -> None:
    """Every locus gets the identical per-deme fixation pattern."""
    values = dict(config_form.starter_form_values())
    values.update(
        {
            "initial_conditions_mode": "fixed_per_deme",
            "fixed_per_deme_choice": "all_same",
            "d": "2",
            "loci_mode": "lengths",
            "locus_lengths": "200, 8000, 3",
        }
    )

    payload = config_form.initial_conditions_to_payload(values)

    assert payload == {
        "p_0": [
            [{"0": 1.0}, {"0": 1.0}, {"0": 1.0}],
            [{"0": 1.0}, {"0": 1.0}, {"0": 1.0}],
        ]
    }


def test_initial_conditions_to_payload_fixed_per_deme_rejects_an_unknown_choice() -> (
    None
):
    """An unrecognized sub-choice is a clear programming error, not a silent default."""
    values = dict(config_form.starter_form_values())
    values.update(
        {
            "initial_conditions_mode": "fixed_per_deme",
            "fixed_per_deme_choice": "bogus",
            "d": "2",
        }
    )

    with pytest.raises(ValueError, match="unknown fixed_per_deme selector choice"):
        config_form.initial_conditions_to_payload(values)


def test_form_values_to_payload_fixed_per_deme_round_trips() -> None:
    """A full form submission in fixed-per-deme mode builds a valid configuration."""
    values = dict(config_form.starter_form_values())
    values.update(
        {
            "initial_conditions_mode": "fixed_per_deme",
            "fixed_per_deme_choice": "all_different",
            "d": "3",
        }
    )

    payload = config_form.form_values_to_payload(values)
    params = SimulationParams.from_mapping(payload)

    assert params.initial_frequencies == (({0: 1.0},), ({1: 1.0},), ({2: 1.0},))


def test_form_values_to_payload_equilibrium_split_round_trips() -> None:
    """A full form submission in equilibrium-split mode builds a valid configuration."""
    values = dict(config_form.starter_form_values())
    values.update(
        config_form.initial_conditions_from_params(
            _params(
                equilibrium_convergence_window=50,
                equilibrium_convergence_tolerance=0.01,
                equilibrium_max_generations=10000,
            )
        )
    )

    payload = config_form.form_values_to_payload(values)
    params = SimulationParams.from_mapping(payload)

    assert params.equilibrium_convergence_window == 50
    assert params.equilibrium_convergence_tolerance == 0.01
    assert params.equilibrium_max_generations == 10000


def test_form_values_to_payload_explicit_p0_round_trips() -> None:
    """A full form submission in explicit-p0 mode builds a valid configuration.

    Matches the starter config's own `d=20`, single-locus shape
    (`fim.cli.STARTER_CONFIG`) so the grid's own deme/locus counts are
    accepted without also having to override `d`/`loci` in `values`.
    """
    values = dict(config_form.starter_form_values())
    values.update(
        config_form.initial_conditions_from_params(
            _params(d=20, initial_frequencies=tuple(({0: 1.0},) for _ in range(20)))
        )
    )

    payload = config_form.form_values_to_payload(values)
    params = SimulationParams.from_mapping(payload)

    assert params.initial_frequencies is not None
    assert len(params.initial_frequencies) == 20
    assert params.initial_frequencies[0] == ({0: 1.0},)


@pytest.mark.parametrize(
    ("message", "expected_field", "expected_tab"),
    [
        (
            "equilibrium_convergence_tolerance must be finite and non-negative",
            "equilibrium_convergence_tolerance",
            "initial_conditions",
        ),
        (
            "equilibrium_convergence_window, equilibrium_convergence_tolerance, "
            "and equilibrium_max_generations must be set together, or not at all",
            None,
            "initial_conditions",
        ),
        (
            "equilibrium-split fields cannot be combined with an explicit p_0",
            None,
            "initial_conditions",
        ),
        (
            "p_0 must contain exactly d demes",
            None,
            "initial_conditions",
        ),
        (
            "p_0 deme 1, locus 1 frequencies must sum to 1",
            None,
            "initial_conditions",
        ),
    ],
)
def test_equilibrium_split_errors_route_to_the_initial_conditions_tab(
    message: str, expected_field: str | None, expected_tab: str
) -> None:
    """Every equilibrium-split validation message reaches the right tab.

    A message naming one specific field (the tolerance range check)
    also highlights that field directly; the two "group" messages (all-
    or-none, and the `p_0` conflict) name no single field, so only the
    tab is located — the same distinction `m`/`mu_b`'s own composite
    errors already draw.
    """
    assert config_form.field_for_error(message) == expected_field
    assert config_form.tab_for_error(message) == expected_tab


def test_mu_from_params_rejects_a_genuinely_per_locus_mu() -> None:
    """A per-locus `mu` (unequal rates across loci) has no form representation."""
    params = _params(
        mu=(0.001, 0.05),
        loci=(LocusSpec(1, 50), LocusSpec(2, 8000)),
    )

    with pytest.raises(ValueError, match="per-locus mu"):
        config_form.mu_from_params(params)


def test_m_from_params_matrix_renders_matrix_mode_with_the_real_values() -> None:
    """A matrix-shaped `m` renders `m_mode="matrix"` with its own dense values."""
    matrix = ((0.9, 0.05, 0.05), (0.05, 0.9, 0.05), (0.05, 0.05, 0.9))
    params = _params(d=3, m=matrix)

    values = config_form.m_from_params(params)

    assert values["m_mode"] == "matrix"
    assert json.loads(values["m_matrix_json"]) == [list(row) for row in matrix]


def test_m_to_payload_matrix_mode_round_trips_through_m_from_params() -> None:
    """A matrix rendered by `m_from_params` submits back to the identical matrix."""
    matrix = ((0.9, 0.05, 0.05), (0.05, 0.9, 0.05), (0.05, 0.05, 0.9))
    values = config_form.m_from_params(_params(d=3, m=matrix))

    payload = config_form.m_to_payload(values)

    assert payload == [list(row) for row in matrix]


def test_m_to_payload_matrix_mode_rejects_malformed_json() -> None:
    """A syntactically invalid `m_matrix_json` is a clear error, not a crash."""
    with pytest.raises(ValueError, match="valid JSON"):
        config_form.m_to_payload(
            {
                "m_mode": "matrix",
                "m_rate": "",
                "m_topology": "ring",
                "m_topology_rate": "",
                "m_matrix_json": "{not valid json",
            }
        )


@pytest.mark.parametrize(
    "malformed",
    ["[]", "[[]]", '["not-a-number-row"]', '[[1, "x"]]', '"not-a-list"'],
)
def test_m_to_payload_matrix_mode_rejects_the_wrong_shape(malformed: str) -> None:
    """Valid JSON that is not a list of number rows is still rejected."""
    with pytest.raises(ValueError, match="nonempty"):
        config_form.m_to_payload(
            {
                "m_mode": "matrix",
                "m_rate": "",
                "m_topology": "ring",
                "m_topology_rate": "",
                "m_matrix_json": malformed,
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


def test_sigma_band_to_payload_omits_both_fields_when_disabled() -> None:
    """Unchecked toggle: neither field reaches the payload at all.

    `20260910-claude-sonnet-5-gui-sigma-band-design.md` (`selby/
    restricted`) approach A1 -- the identical "set together or not at
    all" contract `SimulationParams` itself already enforces for this
    pair.
    """
    values = {"sigma_band_enabled": "false"}

    assert config_form.sigma_band_to_payload(values) == {}


def test_sigma_band_to_payload_parses_both_fields_when_enabled() -> None:
    """Checked toggle: both fields parse to their declared types."""
    values = {
        "sigma_band_enabled": "true",
        "sigma_band_multiplier": "3.0",
        "sigma_band_window": "150",
    }

    assert config_form.sigma_band_to_payload(values) == {
        "sigma_band_multiplier": 3.0,
        "sigma_band_window": 150,
    }


def test_sigma_band_to_payload_rejects_a_non_integer_window() -> None:
    """A malformed window is a clear, field-named error, not a silent coercion."""
    values = {
        "sigma_band_enabled": "true",
        "sigma_band_multiplier": "2.0",
        "sigma_band_window": "not-a-number",
    }

    with pytest.raises(ValueError, match="sigma_band_window"):
        config_form.sigma_band_to_payload(values)


def test_sigma_band_from_params_disabled_seeds_suggested_defaults() -> None:
    """`sigma_band_multiplier is None` renders the toggle off, GUI defaults seeded."""
    values = config_form.sigma_band_from_params(_params())

    assert values["sigma_band_enabled"] == "false"
    assert values["sigma_band_multiplier"] == "2.0"
    assert values["sigma_band_window"] == "100"


def test_sigma_band_from_params_enabled_round_trips_the_real_values() -> None:
    """A real sigma-band configuration renders back enabled, with its own values."""
    values = config_form.sigma_band_from_params(
        _params(sigma_band_multiplier=3.0, sigma_band_window=250)
    )

    assert values["sigma_band_enabled"] == "true"
    assert values["sigma_band_multiplier"] == "3.0"
    assert values["sigma_band_window"] == "250"


def test_sigma_band_round_trips_through_form_values_to_payload_and_back() -> None:
    """`sigma_band_to_payload`/`from_params` agree, all the way around the loop."""
    payload = config_form.sigma_band_to_payload(
        {
            "sigma_band_enabled": "true",
            "sigma_band_multiplier": "2.0",
            "sigma_band_window": "75",
        }
    )
    params = _params(**payload)

    form_values = config_form.sigma_band_from_params(params)
    round_tripped = config_form.sigma_band_to_payload(form_values)

    assert round_tripped == payload


def test_field_for_error_locates_a_sigma_band_error() -> None:
    """A sigma-band validation error routes to the field, then the convergence tab."""
    field = config_form.field_for_error("sigma_band_window must be an integer")

    assert field == "sigma_band_window"
    assert config_form.tab_for_field(field) == "convergence"


def test_loci_from_params_sequential_ids_render_lengths_mode() -> None:
    """Default, sequential locus IDs render the simple comma-list mode."""
    params = _params(loci=(LocusSpec(1, 50), LocusSpec(2, 8000)))

    values = config_form.loci_from_params(params)

    assert values == {
        "loci_mode": "lengths",
        "locus_lengths": "50,8000",
        "loci_json": "",
    }


def test_loci_from_params_custom_ids_render_a_real_editable_grid() -> None:
    """Custom, non-default-position locus IDs render a real grid, not a rejection.

    `loci_to_payload` submitting that grid's own values back reproduces
    the identical `loci` list — the same "loaded badge to real editor"
    upgrade `m_from_params`'s own `"matrix"` mode already made for a
    loaded migration matrix.
    """
    params = _params(loci=(LocusSpec(locus_id=5, length=200),))

    values = config_form.loci_from_params(params)

    assert values["loci_mode"] == "custom"
    assert json.loads(values["loci_json"]) == [{"locus_id": 5, "length": 200}]

    payload = config_form.loci_to_payload(values)

    assert payload == {"loci": [{"locus_id": 5, "length": 200}]}


def test_loci_to_payload_lengths_mode_derives_n_loci() -> None:
    """Lengths mode's payload is `n_loci`/`locus_lengths`, matching the O(loci) rule."""
    payload = config_form.loci_to_payload(
        {"loci_mode": "lengths", "locus_lengths": "50, 8000, 3", "loci_json": ""}
    )

    assert payload == {"n_loci": 3, "locus_lengths": [50, 8000, 3]}


def test_loci_to_payload_rejects_malformed_json() -> None:
    """A syntactically invalid `loci_json` is a clear error, not a crash."""
    with pytest.raises(ValueError, match="valid JSON"):
        config_form.loci_to_payload(
            {"loci_mode": "custom", "locus_lengths": "", "loci_json": "{not valid"}
        )


@pytest.mark.parametrize(
    "malformed",
    ["[]", '[{"locus_id": 1}]', '[{"locus_id": "x", "length": 1}]', '"not-a-list"'],
)
def test_loci_to_payload_rejects_the_wrong_shape(malformed: str) -> None:
    """Valid JSON that is not a list of `{locus_id, length}` rows is still rejected."""
    with pytest.raises(ValueError, match="nonempty"):
        config_form.loci_to_payload(
            {"loci_mode": "custom", "locus_lengths": "", "loci_json": malformed}
        )


def test_loci_to_payload_rejects_an_unknown_mode() -> None:
    """An unrecognized mode is a clear programming error, not a silent default."""
    with pytest.raises(ValueError, match="unknown loci selector mode"):
        config_form.loci_to_payload(
            {"loci_mode": "bogus", "locus_lengths": "", "loci_json": ""}
        )


def test_params_to_form_values_includes_every_composite_fields_keys() -> None:
    """A round-tripped params object populates every composite's own keys too."""
    values = config_form.params_to_form_values(_params())

    for key in (
        "m_mode",
        "mu_mode",
        "p0_json",
        "fixed_per_deme_choice",
        "loci_mode",
        "loci_json",
    ):
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


@pytest.mark.parametrize(
    "backend",
    ["lineal", "auto", "generational", "generational-vector"],
)
def test_engine_backend_round_trips_every_legal_value(backend: str) -> None:
    """All four legal `engine_backend` values survive a full form round trip.

    The correctness case the GUI engine-backend selector design doc
    (`20260911-claude-sonnet-5-gui-engine-backend-selector-design.md`)
    rejected approach B1 over: a value the form cannot represent is not
    merely invisible, it is silently rewritten on the next save. The two
    de-emphasized values (`"generational"`/`"generational-vector"`) are
    the ones that matter here — a botanist rarely picks either, but a
    hand-edited YAML or a reopened manifest can genuinely hold one.

    `finite_alleles` and a short locus because `"generational-vector"`
    refuses any other mutation model outright
    (`_validate_engine_backend`), not for any reason to do with the form
    itself — the starter config's own single 200-base locus is replaced
    rather than supplemented, since `loci` and `locus_lengths` cannot
    both be given.
    """
    params = SimulationParams.from_mapping(
        {
            **yaml.safe_load(STARTER_CONFIG),
            "loci": [{"locus_id": 1, "length": 3}],
            "mutation_model": "finite_alleles",
            "engine_backend": backend,
        }
    )

    values = config_form.params_to_form_values(params)
    restored = SimulationParams.from_mapping(config_form.form_values_to_payload(values))

    assert values["engine_backend"] == backend
    assert restored.engine_backend == backend


def test_starter_form_values_still_seeds_the_lineal_engine_backend() -> None:
    """A fresh form keeps `SimulationParams`'s own default, unchanged by the selector.

    Adding the control changes nothing for a user who never touches it:
    `STARTER_CONFIG` names no `engine_backend`, so the starter form
    seeds `PARAMETER_DEFAULTS`'s own `"lineal"`. The page's own `auto`
    default selection (`index.html`) is only what an untouched
    `<select>` shows, and is overwritten the moment any real form —
    starter or saved — is applied over it.
    """
    assert config_form.starter_form_values()["engine_backend"] == "lineal"


def test_payload_to_yaml_text_orders_engine_backend_last() -> None:
    """`engine_backend` is emitted, and emitted in `configuration.md`'s own order.

    Its documented section ("Engine backend and JIT") follows "Analysis
    and execution", whose last key is `migrant_sampling` — so
    `_YAML_KEY_ORDER` places it after that rather than leaving
    `payload_to_yaml_text`'s own defensive "unknown key" fallback to
    append it in whatever order the payload dict happened to build.
    """
    text = config_form.payload_to_yaml_text(
        config_form.form_values_to_payload(
            {**config_form.starter_form_values(), "engine_backend": "auto"}
        )
    )
    keys = [
        line.split(":", 1)[0] for line in text.splitlines() if not line.startswith(" ")
    ]

    assert "engine_backend: auto" in text
    assert keys.index("engine_backend") > keys.index("migrant_sampling")
