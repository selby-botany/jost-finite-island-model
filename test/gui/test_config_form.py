"""Unit tests for all six tabs' marshaling (no Tk).

`test_config_form_round_trips_starter_config` is this package's own
named regression test — every tab now exists, so `starter_form_values()`'s
output round-trips through `form_values_to_payload` back into an
equivalent `SimulationParams`.
"""

from __future__ import annotations

import json
import math
from dataclasses import fields, replace

import pytest
import yaml

from fim.cli import STARTER_CONFIG
from fim.config.expert import ExpertSettings
from fim.gui import config_form
from fim.gui.config_form import (
    DEFAULT_RUN_SETTING_FIELD_NAMES,
    EXPERT_FIELDS,
    form_values_to_payload,
    params_to_form_values,
    run_setting_differences,
    run_setting_error,
    starter_form_values,
    validate_run_settings,
)
from fim.model.locus import LocusSpec
from fim.model.params import PLOIDY_WORDS, SimulationParams


def _starter() -> dict[str, str]:
    """The starter form with a ploidy chosen, i.e. a submittable form.

    A fresh form deliberately has no ploidy (the botanist must choose);
    diploid pairs the starter's 225 individuals with its historical 450
    gene copies, so every payload assertion below keeps its old numbers.
    """
    return {**config_form.starter_form_values(), "ploidy": "2"}


def test_starter_form_values_reflects_the_cli_starter_config() -> None:
    """`starter_form_values` matches `fim.cli.STARTER_CONFIG`'s own values."""
    values = config_form.starter_form_values()

    # Individuals, with no ploidy chosen: 225 individuals is the 450 gene
    # copies `fim init` writes, at its recorded diploid ploidy.
    assert values["N"] == "225"
    assert values["ploidy"] == ""
    assert values["d"] == "20"
    assert values["seed"] == "20260814"
    assert values["deme_weighting"] == "equal"
    assert values["max_generations"] == "auto"
    assert values["convergence_burn_in"] == "auto"
    assert values["migrant_sampling"] == "continuous"
    assert values["m_mode"] == "scalar"
    assert values["m_rate"] == "0.001"


def test_starter_form_values_with_no_overrides_is_unchanged() -> None:
    """`overrides=None` (the default) matches the pre-`overrides` behavior."""
    assert config_form.starter_form_values() == config_form.starter_form_values(None)
    assert config_form.starter_form_values() == config_form.starter_form_values({})


def test_starter_form_values_applies_a_valid_overlay() -> None:
    """A valid overlay replaces just its own keys; every other value is untouched."""
    values = config_form.starter_form_values(
        overrides={"engine_backend": "generational", "n_replicates": "16"}
    )

    assert values["engine_backend"] == "generational"
    assert values["n_replicates"] == "16"
    # Untouched by the overlay -- still the true starter value.
    assert values["N"] == "225"
    assert values["d"] == "20"


def test_starter_form_values_rejects_an_invalid_overlay() -> None:
    """An overlay that does not validate raises, like any other bad submission."""
    with pytest.raises(ValueError, match="n_replicates"):
        config_form.starter_form_values(overrides={"n_replicates": "not a number"})


def test_default_run_setting_field_names_excludes_scientific_per_run_fields() -> None:
    """Execution/backend defaults are covered; per-run scientific choices are not.

    A real, reported design decision: `convergence_statistic`/
    `convergence_combinator` are experimental, per-run choices with no
    sensible system-wide default -- a fresh configuration already gets
    a sensible single-statistic default -- and stay Configure-only,
    unlike `engine_backend`/`n_replicates`/`max_generations`/the
    convergence-loop *timing* fields (statistic-agnostic) and the
    expert-level backend-tuning fields, which this tuple does cover.
    """
    names = set(config_form.DEFAULT_RUN_SETTING_FIELD_NAMES)

    assert "engine_backend" in names
    assert "n_replicates" in names
    assert "max_generations" in names
    assert "convergence_burn_in" in names
    assert "precision" in names
    assert "confidence" in names
    assert "jit" in names
    assert "auto_vector_min_d" in names
    assert "auto_vector_max_capacity" in names
    assert "max_concurrent_replicates" in names
    assert "convergence_combinator" not in names
    for name in config_form.CONVERGENCE_STATISTIC_NAMES:
        assert f"cs_{name}" not in names
    assert "track_expensive_statistics" not in names
    assert "sigma_band_enabled" not in names
    assert "sigma_band_multiplier" not in names
    assert "sigma_band_window" not in names


def test_all_fields_covers_every_tabs_plain_fields() -> None:
    """`all_fields()` names every tab's plain fields; composites are excluded."""
    names = {field.name for field in config_form.all_fields()}

    assert names == {
        "ploidy",
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
        "convergence_burn_in",
        "convergence_estimate",
        "statistic_precision",
        "precision",
        "track_expensive_statistics",
        "n_replicates",
        "stop_batch_early",
        "precision_method",
        "replicate_averaging_window",
        "trajectory_retention",
        "trajectory_stride",
        "trajectory_thinning_start",
        "replicate_minimum",
        "confidence",
        "engine_backend",
        "max_concurrent_replicates",
        "jit",
        "auto_vector_min_d",
        "auto_vector_max_capacity",
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

    payload = config_form.form_values_to_payload(_starter())
    restored = SimulationParams.from_mapping(payload)

    assert restored == starter_params


def test_form_values_to_payload_parses_every_plain_field_kind() -> None:
    """Int, choice, and int_list fields all coerce to the right Python type."""
    values = dict(_starter())
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

    # 450 individuals, diploid: 900 gene copies.
    # The form's N is individuals, like the configuration's; the word is the
    # configuration's spelling of ploidy.
    assert payload["N"] == 450
    assert payload["ploidy"] == "diploid"
    assert payload["d"] == 20
    assert payload["seed"] == 7
    assert payload["deme_weighting"] == "equal"
    assert payload["max_generations"] == 1000
    assert payload["migrant_sampling"] == "stochastic"
    assert payload["m"] == 0.01


def test_form_values_to_payload_coerces_a_bool_field_from_true_false_text() -> None:
    """A "bool" field coerces the literal "true"/"false" text a checkbox writes.

    `track_expensive_statistics` is this form's first plain "bool"
    `FormField`, so it needs no dedicated
    `*_to_payload` function of its own; the generic `all_fields()`
    dispatch loop in `form_values_to_payload` handles it directly.
    """
    checked = dict(_starter())
    checked["track_expensive_statistics"] = "true"
    unchecked = dict(_starter())
    unchecked["track_expensive_statistics"] = "false"

    checked_payload = config_form.form_values_to_payload(checked)
    unchecked_payload = config_form.form_values_to_payload(unchecked)

    assert checked_payload["track_expensive_statistics"] is True
    assert unchecked_payload["track_expensive_statistics"] is False


def test_params_to_form_values_renders_track_expensive_statistics_as_text() -> None:
    """`params_to_form_values` renders the field back as literal "true"/"false"."""
    enabled = SimulationParams(
        gene_copies=10, m=0.1, mu=0.0, d=2, seed=1, track_expensive_statistics=True
    )
    disabled = SimulationParams(
        gene_copies=10, m=0.1, mu=0.0, d=2, seed=1, track_expensive_statistics=False
    )

    enabled_values = config_form.params_to_form_values(enabled)
    disabled_values = config_form.params_to_form_values(disabled)

    assert enabled_values["track_expensive_statistics"] == "true"
    assert disabled_values["track_expensive_statistics"] == "false"


def test_form_values_to_payload_accepts_a_per_deme_n_list() -> None:
    """A comma-separated `N` becomes a list — the O(d) cardinality-rule case."""
    values = dict(_starter())
    values.update({"N": "200, 300, 150", "d": "3"})

    payload = config_form.form_values_to_payload(values)

    # Individuals per deme, times the starter's diploid ploidy.
    assert payload["N"] == [200, 300, 150]


def test_form_values_to_payload_rejects_a_non_integer_n_item() -> None:
    """A bad per-deme N entry names its own index, matching `_parse_population_size`."""
    values = dict(_starter())
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
    values = dict(_starter())
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


def test_m_to_payload_torus_carries_rows_and_columns_as_integers() -> None:
    """A torus needs its grid shape; the other topologies never send one."""
    payload = config_form.m_to_payload(
        {
            "m_mode": "topology",
            "m_rate": "",
            "m_topology": "torus",
            "m_topology_rate": "0.2",
            "m_topology_rows": "4",
            "m_topology_columns": "5",
        }
    )

    assert payload == {"topology": "torus", "rate": 0.2, "rows": 4, "columns": 5}


def test_m_to_payload_torus_rejects_a_missing_or_non_integer_side() -> None:
    """Blank or fractional rows/columns fail with the field's own name."""
    base = {
        "m_mode": "topology",
        "m_rate": "",
        "m_topology": "torus",
        "m_topology_rate": "0.2",
        "m_topology_rows": "4",
        "m_topology_columns": "5",
    }
    with pytest.raises(ValueError, match=r"m\.rows must be an integer"):
        config_form.m_to_payload({**base, "m_topology_rows": "4.5"})
    # A form saved before the torus existed has no columns key at all.
    without_columns = {k: v for k, v in base.items() if k != "m_topology_columns"}
    with pytest.raises(ValueError, match=r"m\.columns must be an integer"):
        config_form.m_to_payload(without_columns)


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
        "gene_copies": 20,
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
    values = dict(_starter())
    values["locus_lengths"] = "50, 8000"

    payload = config_form.form_values_to_payload(values)

    assert payload["locus_lengths"] == [50, 8000]
    assert payload["n_loci"] == 2


def test_form_values_to_payload_derives_n_loci_one_from_a_bare_length() -> None:
    """A single, comma-free `locus_lengths` value means `n_loci == 1`."""
    values = dict(_starter())
    values["locus_lengths"] = "200"

    payload = config_form.form_values_to_payload(values)

    assert payload["locus_lengths"] == 200
    assert payload["n_loci"] == 1


def test_form_values_to_payload_parses_stop_batch_early_as_a_bool() -> None:
    """The "stop the batch early" checkbox submits a real `bool`."""
    for text, expected in (("true", True), ("false", False)):
        values = dict(_starter())
        values["stop_batch_early"] = text

        payload = config_form.form_values_to_payload(values)

        assert payload["stop_batch_early"] is expected


def test_form_values_to_payload_parses_a_set_precision() -> None:
    """A non-empty `precision` field parses as a float."""
    values = dict(_starter())
    values["precision"] = "0.05"

    payload = config_form.form_values_to_payload(values)

    assert payload["precision"] == 0.05


def test_form_values_to_payload_converts_confidence_to_a_float() -> None:
    """`confidence`'s "float_choice" kind submits a float, not a string."""
    values = dict(_starter())
    values["confidence"] = "0.99"

    payload = config_form.form_values_to_payload(values)

    assert payload["confidence"] == pytest.approx(0.99)
    assert isinstance(payload["confidence"], float)


def test_form_values_to_payload_treats_max_concurrent_replicates_empty_as_unset() -> (
    None
):
    """An empty `max_concurrent_replicates` field submits `None`, not an error.

    `20260914-claude-sonnet-5-non-lineal-
    batch-execution-design.md` (`selby/restricted`), §5.5.
    """
    values = dict(_starter())
    values["max_concurrent_replicates"] = ""

    payload = config_form.form_values_to_payload(values)

    assert payload["max_concurrent_replicates"] is None


def test_form_values_to_payload_parses_a_set_max_concurrent_replicates() -> None:
    """A non-empty `max_concurrent_replicates` field parses as an int, not a float."""
    values = dict(_starter())
    values["max_concurrent_replicates"] = "4"

    payload = config_form.form_values_to_payload(values)

    assert payload["max_concurrent_replicates"] == 4
    assert isinstance(payload["max_concurrent_replicates"], int)


def test_form_values_to_payload_rejects_a_non_integer_max_concurrent_replicates() -> (
    None
):
    """`"optional_int"` rejects `"3.5"` — `int("3.5")` itself already would.

    `SimulationParams.max_concurrent_replicates` must be a whole number,
    so this field's own kind must reject a fractional value at the form
    layer rather than silently truncating or deferring to a less clear
    error further down the validation chain.
    """
    values = dict(_starter())
    values["max_concurrent_replicates"] = "3.5"

    with pytest.raises(
        ValueError, match="max_concurrent_replicates must be an integer"
    ):
        config_form.form_values_to_payload(values)


def test_params_to_form_values_round_trips_max_concurrent_replicates() -> None:
    """A real `max_concurrent_replicates` value survives params -> form -> payload."""
    params = replace(
        SimulationParams.from_mapping(yaml.safe_load(STARTER_CONFIG)),
        n_replicates=5,
        max_concurrent_replicates=3,
    )

    values = config_form.params_to_form_values(params)

    assert values["max_concurrent_replicates"] == "3"
    payload = config_form.form_values_to_payload(values)
    assert payload["max_concurrent_replicates"] == 3
    restored = SimulationParams.from_mapping(payload)
    assert restored.max_concurrent_replicates == 3


def test_starter_form_values_leaves_max_concurrent_replicates_unset() -> None:
    """The starter config never sets `max_concurrent_replicates` — a fresh form
    shows it blank, matching `SimulationParams`'s own `None` default."""
    assert _starter()["max_concurrent_replicates"] == ""


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
    absence, exactly like an unset optional field's omission
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
    values = dict(_starter())
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
    values = dict(_starter())
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
    values = dict(_starter())
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
    values = dict(_starter())
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
    values = dict(_starter())
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
    values = dict(_starter())
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
    values = dict(_starter())
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
    values = dict(_starter())
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
            "equilibrium_convergence_tolerance must be finite and greater than 0",
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
    """Per-locus rates no single `mu_b` produces have no form representation."""
    params = _params(
        mu=(0.001, 0.05),
        loci=(LocusSpec(1, 50), LocusSpec(2, 8000)),
    )

    with pytest.raises(ValueError, match="different mu for each locus"):
        config_form.mu_from_params(params)


def test_mu_from_params_rejects_unequal_rates_on_equal_length_loci() -> None:
    """Equal-length loci share any `mu_b`'s rate, so unequal rates are not one."""
    params = _params(
        mu=(0.001, 0.002),
        loci=(LocusSpec(1, 100), LocusSpec(2, 100)),
    )

    with pytest.raises(ValueError, match=r"no single per-base rate \(mu_b\)"):
        config_form.mu_from_params(params)


@pytest.mark.parametrize("mu_b", [0.00002, 1e-7, 0.0123, 0.5])
def test_mu_from_params_recovers_mu_b_from_unequal_loci_exactly(mu_b: float) -> None:
    """A `mu_b` expanded over unequal loci renders as that `mu_b`, round-trip exact."""
    config: dict[str, object] = {
        "N": 20,
        "ploidy": "haploid",
        "d": 2,
        "m": 0.1,
        "seed": 7,
        "loci": [
            {"locus_id": 1, "length": 50},
            {"locus_id": 2, "length": 500},
            {"locus_id": 3, "length": 7},
        ],
    }
    params = SimulationParams.from_mapping({**config, "mu_b": mu_b})
    assert isinstance(params.mu, tuple)

    values = config_form.mu_from_params(params)

    assert values == {"mu_mode": "mu_b", "mu_value": "", "mu_b_value": str(mu_b)}
    restored = SimulationParams.from_mapping(
        {**config, **config_form.mu_to_payload(values)}
    )
    assert restored.mu == params.mu


def test_per_base_mutation_rate_accepts_a_rate_within_tolerance_only() -> None:
    """Rates a hair off a `mu_b`'s expansion are accepted; a real difference is not."""
    lengths = [50, 500]
    exact = [1.0 - (1.0 - 2e-5) ** length for length in lengths]
    nudged = [exact[0], exact[1] * (1.0 + 1e-12)]
    different = [exact[0], exact[1] * 1.001]

    assert config_form.per_base_mutation_rate(exact, lengths) == 2e-5
    recovered = config_form.per_base_mutation_rate(nudged, lengths)
    assert recovered is not None
    assert math.isclose(recovered, 2e-5, rel_tol=1e-9)
    assert config_form.per_base_mutation_rate(different, lengths) is None


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

    `finite_alleles` and a short locus because a finite-alleles table is
    `4 ** length` columns wide, so `"generational-vector"` refuses the
    starter's 200-base locus (`build_engine_backend`), not for any reason
    to do with the form itself — the starter config's own locus is replaced
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


def test_starter_form_values_seeds_the_recommended_auto_engine_backend() -> None:
    """A fresh form's own real, functional default now matches what it visually shows.

    A real, previously-shipped inconsistency, found investigating GUI/
    CLI parity (design doc `20260911-claude-sonnet-5-gui-engine-
    backend-selector-design.md`, `selby/restricted`): `CHANGELOG.md`'s
    own entry for this control claims "a brand-new form defaulting to
    `auto`," but that was only ever true of `index.html`'s own static
    markup, for the fraction of a second before `loadInitialForm`
    applies `starter_form_values()` over it — `STARTER_CONFIG` named no
    `engine_backend` at all, so that overwrite silently reverted every
    real fresh form back to `PARAMETER_DEFAULTS`'s own `"lineal"`, the
    one backend this project's own recorded benchmarks never found
    fastest. `STARTER_CONFIG` now pins `engine_backend: auto` explicitly
    — the identical fix already applied to `n_replicates` for the
    identical reason (a field this form cares about, left to an
    implicit library default that can silently drift under it) — so a
    fresh form's own real, functional starting value now actually is
    what the page has always visually claimed.
    """
    assert _starter()["engine_backend"] == "auto"


def test_payload_to_yaml_text_orders_engine_backend_last() -> None:
    """`engine_backend` is emitted, and emitted in `configuration.md`'s own order.

    Its documented section ("Engine backend and JIT") follows "Analysis
    and execution", whose last key is `migrant_sampling` — so
    `_YAML_KEY_ORDER` places it after that rather than leaving
    `payload_to_yaml_text`'s own defensive "unknown key" fallback to
    append it in whatever order the payload dict happened to build.
    """
    text = config_form.payload_to_yaml_text(
        config_form.form_values_to_payload({**_starter(), "engine_backend": "auto"})
    )
    keys = [
        line.split(":", 1)[0] for line in text.splitlines() if not line.startswith(" ")
    ]

    assert "engine_backend: auto" in text
    assert keys.index("engine_backend") > keys.index("migrant_sampling")


def test_form_values_to_payload_turns_individuals_into_gene_copies() -> None:
    """The payload carries individuals and the ploidy word; from_mapping multiplies."""
    for ploidy in (1, 2, 3, 4):
        values = {**_starter(), "ploidy": str(ploidy), "N": "100"}

        payload = config_form.form_values_to_payload(values)

        assert payload["N"] == 100
        assert payload["ploidy"] == PLOIDY_WORDS[ploidy]
        params = SimulationParams.from_mapping(payload)
        assert params.ploidy == ploidy
        assert params.gene_copies == 100 * ploidy


def test_form_values_to_payload_refuses_a_blank_ploidy() -> None:
    """A blank ploidy is refused, never guessed, and names the field."""
    values = {**config_form.starter_form_values()}

    with pytest.raises(ValueError, match="ploidy must be chosen") as error:
        config_form.form_values_to_payload(values)

    assert config_form.field_for_error(str(error.value)) == "ploidy"


def test_params_to_form_values_divides_gene_copies_back_into_individuals() -> None:
    """Round trip: a diploid run's 450 gene copies reopen as 225 individuals."""
    params = SimulationParams.from_mapping(yaml.safe_load(STARTER_CONFIG))

    values = config_form.params_to_form_values(params)

    assert values["ploidy"] == "2"
    assert values["N"] == "225"
    per_deme = SimulationParams.from_mapping(
        {
            **yaml.safe_load(STARTER_CONFIG),
            "d": 3,
            "N": [100, 200, 60],
            "ploidy": "diploid",
        }
    )
    assert config_form.params_to_form_values(per_deme)["N"] == "100,200,60"


def test_a_config_with_no_ploidy_cannot_be_loaded_into_the_form() -> None:
    """Ploidy is required, so a ploidy-less file fails before it reaches the form."""
    config = {**yaml.safe_load(STARTER_CONFIG)}
    del config["ploidy"]

    with pytest.raises(ValueError, match="ploidy is required"):
        SimulationParams.from_mapping(config)


def test_starter_form_values_overlay_may_choose_the_ploidy() -> None:
    """A saved default ploidy arrives as an override and makes the starter valid."""
    values = config_form.starter_form_values(overrides={"ploidy": "3"})

    assert values["ploidy"] == "3"
    assert values["N"] == "225"
    payload = config_form.form_values_to_payload(values)
    assert payload["N"] == 225
    assert payload["ploidy"] == "triploid"


def test_a_derived_burn_in_and_cap_are_shown_as_auto_not_as_numbers() -> None:
    """A derived value is never frozen into the form as if it were typed."""
    params = SimulationParams.from_mapping(
        {"N": 100, "ploidy": "haploid", "d": 5, "m": 0.0001, "mu": 0.000001, "seed": 1}
    )

    values = config_form.params_to_form_values(params)

    assert values["convergence_burn_in"] == "auto"
    assert values["max_generations"] == "auto"


def test_an_explicit_burn_in_and_cap_are_shown_as_numbers() -> None:
    """Explicit values stay explicit in the form."""
    params = SimulationParams.from_mapping(
        {
            "N": 100,
            "ploidy": "haploid",
            "d": 5,
            "m": 0.0001,
            "mu": 0.000001,
            "seed": 1,
            "max_generations": 900,
            "convergence_burn_in": 60,
        }
    )

    values = config_form.params_to_form_values(params)

    assert values["max_generations"] == "900"
    assert values["convergence_burn_in"] == "60"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("auto", "auto"), ("AUTO", "auto"), ("", "auto"), ("  ", "auto"), ("250", 250)],
)
def test_the_form_accepts_auto_blank_or_a_whole_number(
    text: str, expected: object
) -> None:
    """Both derivable fields parse the same way."""
    values = {
        **config_form.starter_form_values(),
        "max_generations": text,
        "convergence_burn_in": text,
    }

    payload = config_form.form_values_to_payload({**values, "ploidy": "1"})

    assert payload["max_generations"] == expected
    assert payload["convergence_burn_in"] == expected


@pytest.mark.parametrize("text", ["many", "1.5", "12abc"])
def test_the_form_rejects_a_window_that_is_neither_auto_nor_whole(text: str) -> None:
    """Junk names the field and the accepted forms."""
    values = {
        **config_form.starter_form_values(),
        "max_generations": text,
        "ploidy": "1",
    }

    with pytest.raises(
        ValueError, match="max_generations must be a whole number or auto"
    ):
        config_form.form_values_to_payload(values)


def test_a_form_showing_auto_round_trips_through_the_params() -> None:
    """auto in the form derives on validation and shows as auto again."""
    values = {
        **config_form.starter_form_values(),
        "ploidy": "1",
    }
    params = SimulationParams.from_mapping(config_form.form_values_to_payload(values))

    assert config_form.params_to_form_values(params)["max_generations"] == "auto"


def test_validate_run_settings_accepts_every_engine_backend_the_form_offers() -> None:
    """Run defaults are judged on their own, not against the starter model.

    Whether a model can use a backend depends on the model
    (`generational-vector` refuses stochastic migrant counts, and once
    refused the starter's infinite alleles too); judging defaults against
    the starter once rejected that backend outright.
    """
    for backend in ("lineal", "auto", "generational", "generational-vector"):
        config_form.validate_run_settings({"engine_backend": backend, "jit": "off"})


def test_validate_run_settings_parses_text_as_a_submitted_form_would() -> None:
    """Blank or `auto` window and cap, and blank concurrency, are valid text."""
    config_form.validate_run_settings(
        {
            "max_generations": "",
            "max_concurrent_replicates": "",
            "confidence": "0.99",
            "max_workers": "not validated here",
        }
    )


def test_run_setting_error_names_the_problem_of_one_field_only() -> None:
    """One field's own check, worded as `SimulationParams` words it."""
    assert config_form.run_setting_error("n_replicates", "16") is None
    assert config_form.run_setting_error("n_replicates", "0") == (
        "n_replicates must be at least 1"
    )
    assert config_form.run_setting_error("jit", "fast") == (
        "jit must be 'off' or 'numba'"
    )
    # A field valid alone is valid here even if another field it could
    # contradict is not given.
    assert config_form.run_setting_error("max_generations", "500") is None


def test_every_run_setting_has_a_plain_language_label() -> None:
    """The differences notice can name every run setting in words."""
    assert set(config_form.RUN_SETTING_LABELS) == set(
        config_form.DEFAULT_RUN_SETTING_FIELD_NAMES
    )


def test_run_setting_differences_lists_only_fields_whose_values_differ() -> None:
    """Equal values spelled differently are not differences; real ones are listed."""
    settings = config_form.starter_form_values()
    run = {
        **settings,
        "engine_backend": "lineal",
        "n_replicates": "1",
        # The same values, spelled differently.
        "precision": "1e-2",
        "max_generations": "",
        "confidence": "0.950",
    }

    differences = config_form.run_setting_differences(run, settings)

    assert differences == [
        {
            "field": "engine_backend",
            "label": "Execution engine",
            "runValue": "lineal",
            "settingsValue": "auto",
            "runText": "lineal",
            "settingsText": "auto",
        },
        {
            "field": "n_replicates",
            "label": "Number of replicates",
            "runValue": "1",
            "settingsValue": "200",
            "runText": "1",
            "settingsText": "200",
        },
    ]


def test_run_setting_differences_shows_blank_values_as_words() -> None:
    """Blank concurrency reads "unlimited"; a blank derived cap reads "auto"."""
    settings = config_form.starter_form_values()
    run = {**settings, "max_concurrent_replicates": "4", "max_generations": "500"}

    differences = config_form.run_setting_differences(run, settings)

    shown = {entry["field"]: entry["settingsText"] for entry in differences}
    assert shown == {
        "max_concurrent_replicates": "unlimited",
        "max_generations": "auto",
    }


def test_run_setting_differences_skips_fields_missing_from_either_side() -> None:
    """Only fields both sides carry are compared."""
    assert (
        config_form.run_setting_differences(
            {"engine_backend": "lineal"}, {"n_replicates": "3"}
        )
        == []
    )


def test_every_expert_setting_has_a_settings_field_and_a_form_value() -> None:
    """Each setting is a run default `expert_<name>`, shown at its effective value."""
    names = {field.name for field in fields(ExpertSettings)}
    assert {field.name for field in EXPERT_FIELDS} == {f"expert_{n}" for n in names}
    for field in EXPERT_FIELDS:
        assert field.name in DEFAULT_RUN_SETTING_FIELD_NAMES
    starter = starter_form_values()
    defaults = ExpertSettings()
    for name in names:
        assert starter[f"expert_{name}"] == str(getattr(defaults, name))


def test_expert_form_values_round_trip_through_the_payload() -> None:
    """Changed settings reach `expert`; a default-valued form adds no manifest entry."""
    values = starter_form_values()
    values["ploidy"] = "1"
    plain = SimulationParams.from_mapping(form_values_to_payload(values))
    assert plain.expert.changes() == {}
    assert "expert" not in plain.to_dict()

    values["expert_batch_width"] = "4"
    values["expert_log_sync_seconds"] = "0.5"
    params = SimulationParams.from_mapping(form_values_to_payload(values))

    assert params.expert.batch_width == 4
    assert params.expert.log_sync_seconds == 0.5
    assert params.to_dict()["expert"] == {"batch_width": 4, "log_sync_seconds": 0.5}
    again = params_to_form_values(params)
    assert again["expert_batch_width"] == "4"
    assert again["expert_log_sync_seconds"] == "0.5"


def test_a_saved_form_without_expert_keys_still_validates() -> None:
    """A form saved before Expert Settings existed keeps working (defaults)."""
    values = {
        key: value
        for key, value in starter_form_values().items()
        if not key.startswith("expert_")
    }
    values["ploidy"] = "1"

    payload = form_values_to_payload(values)

    assert payload["expert"] == {}


def test_run_setting_validation_names_the_bad_expert_field() -> None:
    """A bad value or a contradicting pair is refused by name."""
    assert run_setting_error("expert_batch_width", "4") is None
    assert "batch_width" in (run_setting_error("expert_batch_width", "0") or "")
    assert "integer" in (run_setting_error("expert_batch_width", "2.5") or "")
    with pytest.raises(ValueError, match="cap_maximum"):
        validate_run_settings(
            {"expert_cap_minimum": "1000", "expert_cap_maximum": "10"}
        )
    with pytest.raises(ValueError, match="averaging_multiple_maximum"):
        validate_run_settings(
            {
                "expert_averaging_multiple_minimum": "10",
                "expert_averaging_multiple_maximum": "5",
            }
        )


def test_a_loaded_configuration_with_a_changed_expert_setting_differs() -> None:
    """The run-settings notice lists a changed expert setting with its label."""
    settings = starter_form_values()
    run = dict(settings, expert_batch_width="2")

    differences = run_setting_differences(run, settings)

    assert [d["field"] for d in differences] == ["expert_batch_width"]
    assert differences[0]["label"].startswith("Expert: ")
    assert differences[0]["runText"] == "2"


def test_the_new_convergence_fields_round_trip_through_the_payload() -> None:
    """Estimate, method, window and per-statistic precision survive the form."""
    values = starter_form_values()
    values["ploidy"] = "1"
    values["convergence_estimate"] = "auto"
    values["precision_method"] = "planned_replicates"
    values["replicate_averaging_window"] = "2500"
    values["statistic_precision"] = "D=0.02, A_CGD=0.5"
    values["cs_D"] = "true"
    values["cs_A_CGD"] = "true"

    params = SimulationParams.from_mapping(form_values_to_payload(values))

    assert params.convergence_estimate == "auto"
    assert params.precision_method == "planned_replicates"
    assert params.replicate_averaging_window == 2500
    assert params.statistic_precision == (("A_CGD", 0.5), ("D", 0.02))
    again = params_to_form_values(params)
    assert again["convergence_estimate"] == "auto"
    assert again["precision_method"] == "planned_replicates"
    assert again["replicate_averaging_window"] == "2500"
    assert again["statistic_precision"] == "A_CGD=0.5, D=0.02"


def test_the_defaults_of_the_new_fields_are_the_configuration_defaults() -> None:
    """A fresh form: mean of values, interval, auto window, no overrides."""
    starter = starter_form_values()

    assert starter["convergence_estimate"] == "mean_of_values"
    assert starter["precision_method"] == "interval"
    assert starter["replicate_averaging_window"] == "auto"
    assert starter["statistic_precision"] == ""


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("D", "NAME=number"),
        ("=0.5", "NAME=number"),
        ("D=wide", r"statistic_precision\[D\] must be a number"),
        ("D=0.1, D=0.2", "names D twice"),
    ],
)
def test_a_malformed_statistic_precision_is_refused_by_name(
    text: str, message: str
) -> None:
    """The text is NAME=number pairs; anything else says what is wrong."""
    values = starter_form_values()
    values["ploidy"] = "1"
    values["statistic_precision"] = text

    with pytest.raises(ValueError, match=message):
        form_values_to_payload(values)


def test_the_new_run_defaults_are_validated_on_their_own() -> None:
    """Settings refuses a bad estimate, method or window by name."""
    assert run_setting_error("convergence_estimate", "auto") is None
    assert "convergence_estimate" in (
        run_setting_error("convergence_estimate", "x") or ""
    )
    assert run_setting_error("precision_method", "planned_replicates") is None
    assert "precision_method" in (run_setting_error("precision_method", "x") or "")
    assert run_setting_error("replicate_averaging_window", "auto") is None
    assert run_setting_error("replicate_averaging_window", "800") is None
    assert "replicate_averaging_window" in (
        run_setting_error("replicate_averaging_window", "0") or ""
    )


def test_the_retention_fields_round_trip_through_the_payload() -> None:
    """Retention, stride and thinning start survive the form; derived stays auto."""
    values = starter_form_values()
    values["ploidy"] = "1"
    assert values["trajectory_retention"] == "full"
    assert values["trajectory_stride"] == "10"
    assert values["trajectory_thinning_start"] == "auto"

    values["trajectory_retention"] = "thinned"
    values["trajectory_stride"] = "25"
    params = SimulationParams.from_mapping(form_values_to_payload(values))
    again = params_to_form_values(params)
    assert params.trajectory_retention == "thinned"
    assert again["trajectory_stride"] == "25"
    # A derived start shows as `auto` again, never frozen into the form.
    assert again["trajectory_thinning_start"] == "auto"

    values["trajectory_thinning_start"] = "4321"
    typed = SimulationParams.from_mapping(form_values_to_payload(values))
    assert params_to_form_values(typed)["trajectory_thinning_start"] == "4321"


def test_the_retention_defaults_are_validated_on_their_own() -> None:
    """Settings refuses a bad retention, stride or start by name."""
    assert run_setting_error("trajectory_retention", "thinned") is None
    assert "trajectory_retention" in (
        run_setting_error("trajectory_retention", "x") or ""
    )
    assert run_setting_error("trajectory_stride", "5") is None
    assert "trajectory_stride" in (run_setting_error("trajectory_stride", "0") or "")
    assert run_setting_error("trajectory_thinning_start", "auto") is None
    assert "trajectory_thinning_start" in (
        run_setting_error("trajectory_thinning_start", "-1") or ""
    )
