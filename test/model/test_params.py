"""Tests for validated and replayable simulation parameters."""

import math
from pathlib import Path

import pytest

from fim.config.expert import ExpertSettings
from fim.engine import deterministic_run_id
from fim.model.locus import LocusSpec
from fim.model.params import (
    _CONFIG_KEYS,
    _INTERNAL_KEYS,
    _LABEL_KEYS,
    PARAMETER_DEFAULTS,
    SimulationParams,
    describe_population,
    validate_execution_settings,
)


def _valid_config() -> dict[str, object]:
    """Return the smallest complete config mapping."""
    return {
        "N": 20,
        "ploidy": "haploid",
        "d": 2,
        "m": 0.1,
        "mu": 0.001,
        "seed": 7,
    }


def _valid_config_without_mu() -> dict[str, object]:
    """Return the smallest complete config mapping, minus `mu`.

    For tests exercising `mu_b`, mutually exclusive with `mu`.
    """
    return {key: value for key, value in _valid_config().items() if key != "mu"}


def test_scalar_parameters_construct_with_documented_defaults() -> None:
    """The public P-bag defaults remain synchronized with the design."""
    params = SimulationParams.from_mapping(_valid_config())

    assert params.initial_allele_count == PARAMETER_DEFAULTS["initial_allele_count"]
    assert params.initial_concentration == PARAMETER_DEFAULTS["initial_concentration"]
    assert params.deme_weighting == PARAMETER_DEFAULTS["deme_weighting"]
    # Equal weighting, matching how `D` and `K_ST` already weight demes,
    # so `E_ST` follows one convention unless `size` is asked for.
    assert params.deme_weighting == "equal"
    assert params.convergence_statistic == PARAMETER_DEFAULTS["convergence_statistic"]
    assert params.convergence_statistics == ("D",)
    assert params.convergence_combinator == PARAMETER_DEFAULTS["convergence_combinator"]
    # Unset means "derive it": the defaults table holds None, and the
    # constructed params hold the derived integers (see the derivation tests
    # at the end of this file).
    assert PARAMETER_DEFAULTS["convergence_burn_in"] is None
    assert PARAMETER_DEFAULTS["max_generations"] is None
    assert params.auto_derived == {"convergence_burn_in", "max_generations"}
    assert params.precision == PARAMETER_DEFAULTS["precision"]
    assert params.n_replicates == PARAMETER_DEFAULTS["n_replicates"]
    assert params.n_replicates == 200
    assert params.stop_batch_early is PARAMETER_DEFAULTS["stop_batch_early"]
    assert params.stop_batch_early is True
    assert params.precision == 0.01
    assert params.batch_precision == 0.01
    assert params.replicate_minimum == PARAMETER_DEFAULTS["replicate_minimum"]
    assert params.confidence == PARAMETER_DEFAULTS["confidence"]
    assert params.migrant_sampling == PARAMETER_DEFAULTS["migrant_sampling"]
    assert params.migrant_sampling == "continuous"
    assert params.mutation_model == PARAMETER_DEFAULTS["mutation_model"]
    assert params.mutation_model == "infinite_alleles"
    assert params.engine_backend == PARAMETER_DEFAULTS["engine_backend"]
    assert params.engine_backend == "lineal"
    assert params.jit == PARAMETER_DEFAULTS["jit"]
    assert params.jit == "off"
    assert params.auto_vector_min_d == PARAMETER_DEFAULTS["auto_vector_min_d"]
    assert (
        params.auto_vector_max_capacity
        == PARAMETER_DEFAULTS["auto_vector_max_capacity"]
    )


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("m", -0.1, "m must be between"),
        ("m", 1.1, "m must be between"),
        ("mu", -0.1, "mu must be between"),
        ("d", 1, "d must be at least 2"),
        ("N", 0, "N must be at least 1"),
        ("deme_weighting", "wrong", "deme_weighting"),
        ("convergence_burn_in", 0, "convergence_burn_in"),
        ("auto_vector_max_capacity", 0, "auto_vector_max_capacity"),
    ],
)
def test_invalid_values_name_the_offending_field(
    key: str,
    value: object,
    message: str,
) -> None:
    """Validation failures identify the incorrect field."""
    config = _valid_config()
    config[key] = value

    with pytest.raises(ValueError, match=message):
        SimulationParams.from_mapping(config)


def test_unknown_key_is_rejected_by_name() -> None:
    """The open bag is documented rather than silently accepting typos."""
    config = _valid_config()
    config["sead"] = 9

    with pytest.raises(ValueError, match="sead"):
        SimulationParams.from_mapping(config)


def test_array_n_and_matrix_m_are_shape_validated() -> None:
    """Future unequal-size and migration-matrix data shapes are accepted."""
    params = SimulationParams(
        gene_copies=(10, 20),
        m=((0.9, 0.1), (0.2, 0.8)),
        mu=0.001,
        d=2,
        seed=7,
        loci=(LocusSpec(1, 200),),
    )

    assert params.population_sizes == (10, 20)


def test_direct_construction_rejects_bool_and_string_matrix_entries() -> None:
    """A direct `SimulationParams(...)` entry is as strict as `from_mapping`'s own.

    Regression test for FIM-08: `_normalize_migration`/`_normalize_
    mutation_rate` used to coerce each matrix/list entry with a raw
    `float(item)`, which silently accepts a `bool` (`True`/`False`
    coerce to `1.0`/`0.0`) or a numeric string (`"0.5"`) — neither of
    which `SimulationParams.from_mapping`'s own `_parse_migration`/
    `_parse_mutation_rate` would ever accept, a real inconsistency
    between the two construction routes this project's own multi-model
    engine review, 2026-09-04, found.
    """
    with pytest.raises(ValueError, match="m\\[0\\] must be a number"):
        SimulationParams(
            gene_copies=10,
            m=((True, 0.0), (0.0, 1.0)),
            mu=0.001,
            d=2,
            seed=7,
            loci=(LocusSpec(1, 200),),
        )
    with pytest.raises(ValueError, match="mu\\[0\\] must be a number"):
        SimulationParams(
            gene_copies=10,
            m=0.1,
            mu=("0.5",),  # type: ignore[arg-type]
            d=2,
            seed=7,
            loci=(LocusSpec(1, 200),),
        )


def test_initial_allele_count_is_bounded_by_the_smallest_deme_n() -> None:
    """Unequal per-deme N constrains founding alleles by the smallest deme."""
    config = {
        **_valid_config(),
        "N": [5, 50],
        "ploidy": "haploid",
        "initial_allele_count": 6,
    }

    with pytest.raises(ValueError, match="cannot exceed the smallest deme N"):
        SimulationParams.from_mapping(config)

    accepted = SimulationParams.from_mapping({**config, "initial_allele_count": 5})
    assert accepted.population_sizes == (5, 50)


def test_auto_vector_max_capacity_is_a_real_field_and_round_trips() -> None:
    """`auto_vector_max_capacity` is caller-configurable, not a hidden constant.

    `20260903-claude-sonnet-5-fim-vg-performance-campaign-design.md`
    §6.1 item 2 — a non-default value specifically, not just the
    default `test_mapping_round_trip_is_lossless` (above) already
    exercises for every field at once, so a wiring mistake that
    happened to leave this field permanently pinned to its own default
    could not hide behind that test alone.
    """
    params = SimulationParams.from_mapping(
        {**_valid_config(), "auto_vector_max_capacity": 256}
    )

    assert params.auto_vector_max_capacity == 256
    assert SimulationParams.from_mapping(params.to_dict()) == params


def test_max_concurrent_replicates_defaults_to_none_and_round_trips() -> None:
    """`max_concurrent_replicates` is a real, optional field, `None` by default.

    `dev/doc/apps/selby/jost-finite-island-model/20260904-claude-
    sonnet-5-fim-engine-review-remediations.md`, `FIM-45`/`FIM-48`.
    Omitted from `to_dict()` when `None`, like `initial_frequencies` —
    this field's own default is already `None`, so an absent key and an
    explicit `None` mean the same thing to `from_mapping`, unlike the
    always-present fields (`test_precision_and_stop_batch_early_round_trip`).
    """
    default_params = SimulationParams.from_mapping(_valid_config())
    assert default_params.max_concurrent_replicates is None
    assert "max_concurrent_replicates" not in default_params.to_dict()
    assert SimulationParams.from_mapping(default_params.to_dict()) == default_params

    bounded_params = SimulationParams.from_mapping(
        {**_valid_config(), "n_replicates": 10, "max_concurrent_replicates": 3}
    )
    assert bounded_params.max_concurrent_replicates == 3
    assert bounded_params.to_dict()["max_concurrent_replicates"] == 3
    assert SimulationParams.from_mapping(bounded_params.to_dict()) == bounded_params


def test_max_concurrent_replicates_above_n_replicates_is_clamped_not_rejected() -> None:
    """A window wider than the whole batch is silently capped, not rejected.

    Mirrors `test_replicate_minimum_above_n_replicates_is_clamped_not_
    rejected`'s own reasoning: a window sized for a different, larger
    `n_replicates` (or just generously chosen) behaves exactly like
    `None` would, not like a config error.
    """
    params = SimulationParams.from_mapping(
        {**_valid_config(), "n_replicates": 3, "max_concurrent_replicates": 100}
    )
    assert params.max_concurrent_replicates == 3

    unaffected = SimulationParams.from_mapping(
        {**_valid_config(), "n_replicates": 10, "max_concurrent_replicates": 3}
    )
    assert unaffected.max_concurrent_replicates == 3


def test_max_concurrent_replicates_rejects_non_positive_values() -> None:
    """Zero or negative windows are config errors, not silently clamped."""
    with pytest.raises(
        ValueError, match="max_concurrent_replicates must be at least 1"
    ):
        SimulationParams.from_mapping(
            {**_valid_config(), "max_concurrent_replicates": 0}
        )


def test_mapping_round_trip_is_lossless() -> None:
    """Manifest serialization reconstructs equal parameters."""
    original = SimulationParams.from_mapping(
        {
            **_valid_config(),
            "loci": [{"locus_id": 3, "length": 500}],
        }
    )

    assert SimulationParams.from_mapping(original.to_dict()) == original


def test_convergence_statistic_accepts_several_names_and_round_trips() -> None:
    """Several statistics normalize to a tuple, round-trip, and stay ordered.

    Design §9: "several statistics needed to agree before stopping" lands as
    a list here; a single statistic is that list's one-element special case
    and keeps producing the same bare string as before this extension.
    """
    params = SimulationParams.from_mapping(
        {
            **_valid_config(),
            "convergence_statistic": ["G_ST", "D"],
            "convergence_combinator": "any",
        }
    )

    assert params.convergence_statistic == ("G_ST", "D")
    assert params.convergence_statistics == ("G_ST", "D")
    assert params.convergence_combinator == "any"
    assert params.to_dict()["convergence_statistic"] == ["G_ST", "D"]
    assert params.to_dict()["convergence_combinator"] == "any"
    assert SimulationParams.from_mapping(params.to_dict()) == params

    single = SimulationParams.from_mapping(
        {**_valid_config(), "convergence_statistic": ["D"]}
    )
    assert single.convergence_statistic == "D"
    assert single.convergence_statistics == ("D",)


def test_convergence_statistic_accepts_h_st() -> None:
    """`H_ST` is a real, watchable convergence statistic, not report-only.

    Regression test for `FIM-51`: `fim.engine.FinalReport`/
    `replicate_summary` already report `H_ST` alongside every other
    differentiation measure, but `_CONVERGENCE_STATISTICS` did not
    include it, so a run could not actually watch it for convergence —
    the one statistic reportable but not watchable, with no principled
    reason behind the gap.
    """
    params = SimulationParams.from_mapping(
        {**_valid_config(), "convergence_statistic": "H_ST"}
    )

    assert params.convergence_statistic == "H_ST"
    assert params.convergence_statistics == ("H_ST",)


def test_convergence_statistic_accepts_the_expensive_bonus_measurements() -> None:
    """`A_CGD`/`Delta`/`MI` are watchable, exactly like `E_ST`/`K_ST`.

    They joined `_CONVERGENCE_STATISTICS` on a real, reported request,
    reversing this same session's own earlier choice to exclude them as
    display-only "bonus" measurements with no convergence claim
    attached — same real, generation-scale compute cost either way
    (`fim.engine._EXPENSIVE_OPT_IN_STATISTICS`'s own docstring), so
    watching one of them for convergence, not merely opting into its
    display, is a choice this project leaves to the caller.
    """
    for statistic in ("A_CGD", "Delta", "MI"):
        params = SimulationParams.from_mapping(
            {**_valid_config(), "convergence_statistic": statistic}
        )

        assert params.convergence_statistic == statistic
        assert params.convergence_statistics == (statistic,)


def test_migrant_sampling_defaults_to_continuous_and_round_trips() -> None:
    """The opt-in stochastic migrant-count model stays off unless requested.

    Omitting the key entirely and configuring it explicitly as
    "continuous" must be indistinguishable — the whole point of an opt-in
    feature is that a config written before it existed keeps meaning
    exactly what it always meant.
    """
    default = SimulationParams.from_mapping(_valid_config())
    explicit_continuous = SimulationParams.from_mapping(
        {**_valid_config(), "migrant_sampling": "continuous"}
    )
    stochastic = SimulationParams.from_mapping(
        {**_valid_config(), "migrant_sampling": "stochastic"}
    )

    assert default == explicit_continuous
    assert default.migrant_sampling == "continuous"
    assert stochastic.migrant_sampling == "stochastic"
    assert default.to_dict()["migrant_sampling"] == "continuous"
    assert stochastic.to_dict()["migrant_sampling"] == "stochastic"
    assert SimulationParams.from_mapping(stochastic.to_dict()) == stochastic


def test_mutation_model_defaults_to_infinite_alleles_and_round_trips() -> None:
    """The opt-in finite-alleles model stays off unless requested.

    Omitting the key entirely and configuring it explicitly as
    "infinite_alleles" must be indistinguishable — the whole point of an
    opt-in feature is that a config written before it existed keeps
    meaning exactly what it always meant.
    """
    default = SimulationParams.from_mapping(_valid_config())
    explicit_infinite = SimulationParams.from_mapping(
        {**_valid_config(), "mutation_model": "infinite_alleles"}
    )
    finite = SimulationParams.from_mapping(
        {
            **_valid_config(),
            "mutation_model": "finite_alleles",
            "loci": [{"locus_id": 1, "length": 1}],
        }
    )

    assert default == explicit_infinite
    assert default.mutation_model == "infinite_alleles"
    assert finite.mutation_model == "finite_alleles"
    assert default.to_dict()["mutation_model"] == "infinite_alleles"
    assert finite.to_dict()["mutation_model"] == "finite_alleles"
    assert SimulationParams.from_mapping(finite.to_dict()) == finite


def test_finite_alleles_capacity_check_covers_every_locus_not_just_the_first() -> None:
    """A violation on the second locus is caught, not just the first's.

    Locus 1 (length 2, capacity 16) has ample headroom for
    `initial_allele_count=5`; locus 2 (length 1, capacity 4) does not. A
    validator that only checked `loci[0]` would miss this.
    """
    with pytest.raises(ValueError, match=r"locus 2.*exceeds the finite_alleles"):
        SimulationParams.from_mapping(
            {
                **_valid_config(),
                "mutation_model": "finite_alleles",
                "loci": [{"locus_id": 1, "length": 2}, {"locus_id": 2, "length": 1}],
                "initial_allele_count": 5,
            }
        )

    valid = SimulationParams.from_mapping(
        {
            **_valid_config(),
            "mutation_model": "finite_alleles",
            "loci": [{"locus_id": 1, "length": 2}, {"locus_id": 2, "length": 1}],
            "initial_allele_count": 4,
        }
    )
    assert valid.mutation_model == "finite_alleles"


def test_mu_accepts_an_explicit_per_locus_list() -> None:
    """`mu` generalizes to a per-locus list, mirroring `N`'s per-deme form.

    Two genuinely different rates, not a uniform list — proving each
    locus keeps its own configured value, not a shared or averaged one.
    """
    params = SimulationParams.from_mapping(
        {
            **_valid_config(),
            "loci": [{"locus_id": 1, "length": 10}, {"locus_id": 2, "length": 20}],
            "mu": [0.001, 0.01],
        }
    )

    assert params.mu == (0.001, 0.01)
    assert params.mutation_rates == (0.001, 0.01)
    assert params.to_dict()["mu"] == [0.001, 0.01]
    assert SimulationParams.from_mapping(params.to_dict()) == params


def test_mu_list_of_equal_values_collapses_to_scalar() -> None:
    """A per-locus list that happens to be uniform serializes as a scalar.

    Mirrors `N`'s own collapse behavior: the extra generality costs
    nothing in the common case where it is not actually being used.
    """
    params = SimulationParams.from_mapping(
        {
            **_valid_config(),
            "loci": [{"locus_id": 1, "length": 10}, {"locus_id": 2, "length": 20}],
            "mu": [0.002, 0.002],
        }
    )

    assert params.mu == 0.002
    assert params.mutation_rates == (0.002, 0.002)
    assert params.to_dict()["mu"] == 0.002


def test_mu_b_derives_each_locus_own_rate_from_length() -> None:
    """`mu_b` (per-base rate) expands to the exact per-locus Eq. 5 relation.

    ``mu = 1 - (1 - mu_b) ** length`` — checked against the exact formula,
    not the linear ``mu_b * length`` approximation the differentiation-
    measures guide only uses for small ``mu_b * length``.
    """
    mu_b = 0.0001
    lengths = (10, 1_000)
    params = SimulationParams.from_mapping(
        {
            **_valid_config_without_mu(),
            "loci": [
                {"locus_id": 1, "length": lengths[0]},
                {"locus_id": 2, "length": lengths[1]},
            ],
            "mu_b": mu_b,
        }
    )
    expected = tuple(1.0 - (1.0 - mu_b) ** length for length in lengths)
    assert params.mutation_rates == pytest.approx(expected)
    # mu_b itself is sugar: to_dict() always emits the resolved per-locus
    # mu, matching every other config-shorthand in this codebase (n_loci,
    # the migration sparse map, the stepping-stone topology mapping).
    assert "mu_b" not in params.to_dict()
    assert SimulationParams.from_mapping(params.to_dict()) == params


@pytest.mark.parametrize(
    ("mu_b", "message"),
    [(-0.1, "mu_b must be between"), (1.1, "mu_b must be between")],
)
def test_mu_b_is_validated_as_a_probability(mu_b: float, message: str) -> None:
    """`mu_b` itself is bounds-checked, same as any other probability."""
    with pytest.raises(ValueError, match=message):
        SimulationParams.from_mapping({**_valid_config_without_mu(), "mu_b": mu_b})


def test_mu_and_mu_b_are_mutually_exclusive_and_one_is_required() -> None:
    """Exactly one of `mu`/`mu_b` must be given — never both, never neither."""
    with pytest.raises(ValueError, match="mu cannot be combined with mu_b"):
        SimulationParams.from_mapping(
            {**_valid_config(), "mu_b": 0.0001}  # _valid_config() already has mu
        )
    with pytest.raises(ValueError, match="mu or mu_b"):
        SimulationParams.from_mapping(_valid_config_without_mu())


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"loci": []}, "loci must not be empty"),
        (
            {"loci": [{"locus_id": 1, "length": 10}, {"locus_id": 1, "length": 20}]},
            "locus IDs must be unique",
        ),
        ({"initial_allele_count": 21}, "cannot exceed"),
        ({"initial_concentration": 0.0}, "greater than 0"),
        ({"initial_concentration": float("inf")}, "finite"),
        ({"convergence_statistic": "unknown"}, "must be one of"),
        ({"convergence_statistic": []}, "must not be empty"),
        ({"convergence_statistic": ["D", "unknown"]}, "must be one of"),
        ({"convergence_statistic": ["D", "D"]}, "must not repeat"),
        ({"convergence_combinator": "either"}, "convergence_combinator"),
        ({"precision": -1.0}, "non-negative"),
        ({"precision": float("nan")}, "finite"),
        ({"max_generations": 0}, "max_generations"),
        ({"n_replicates": 0}, "n_replicates"),
        ({"precision": -1.0}, "non-negative"),
        ({"precision": float("nan")}, "finite"),
        ({"stop_batch_early": "yes"}, "stop_batch_early"),
        ({"replicate_minimum": 1}, "replicate_minimum"),
        ({"confidence": 0.80}, "confidence"),
        ({"migrant_sampling": "binomial"}, "migrant_sampling"),
        ({"mutation_model": "stepwise"}, "mutation_model"),
        ({"mu": [0.001, 0.002]}, "one rate per locus"),
        (
            {
                "loci": [{"locus_id": 1, "length": 10}, {"locus_id": 2, "length": 20}],
                "mu": [0.001, 0.01, 0.1],
            },
            "one rate per locus",
        ),
        (
            {
                "loci": [{"locus_id": 1, "length": 10}, {"locus_id": 2, "length": 20}],
                "mu": [1.1, 0.01],
            },
            r"mu\[0\] must be between",
        ),
        (
            {
                "mutation_model": "finite_alleles",
                "loci": [{"locus_id": 1, "length": 1}],
                "initial_allele_count": 5,
            },
            "exceeds the finite_alleles capacity",
        ),
        (
            {
                "mutation_model": "finite_alleles",
                "loci": [{"locus_id": 1, "length": 1}],
                "p_0": [[{"0": 0.5, "9": 0.5}], [{"0": 0.5, "9": 0.5}]],
            },
            "exceeds the finite_alleles capacity",
        ),
    ],
)
def test_post_init_validation_covers_all_scalar_contracts(
    updates: dict[str, object],
    message: str,
) -> None:
    """Every scalar validation rule rejects its documented invalid input."""
    with pytest.raises(ValueError, match=message):
        SimulationParams.from_mapping({**_valid_config(), **updates})


def test_replicate_minimum_above_n_replicates_is_clamped_not_rejected() -> None:
    """An unreachable replicate_minimum is silently capped at n_replicates.

    Previously rejected outright (`ValueError`) — changed once
    batches started stopping early by default: the same
    combination now arises from nothing more deliberate than setting a
    small `n_replicates` without separately thinking about `replicate_
    minimum` at all (this project's own CI found every GUI batch test
    hitting exactly this, `jost-finite-island-model` run 33656031751).
    The engine-flavored regression test for this same behavior lives in
    `test/engine/test_engine.py::
    test_replicate_minimum_above_n_replicates_runs_to_completion`.
    """
    params = SimulationParams.from_mapping(
        {
            **_valid_config(),
            "n_replicates": 3,
            "replicate_minimum": 100,
            "precision": 0.0,
        }
    )
    assert params.n_replicates == 3
    assert params.replicate_minimum == 3

    # A replicate_minimum already <= n_replicates is left exactly as given.
    unaffected = SimulationParams.from_mapping(
        {
            **_valid_config(),
            "n_replicates": 10,
            "replicate_minimum": 3,
            "precision": 0.0,
        }
    )
    assert unaffected.replicate_minimum == 3

    # n_replicates=1 is untouched regardless — adaptive stopping is
    # already inert there, and replicate_minimum's own floor (>= 2)
    # would otherwise make a naive `min(replicate_minimum, n_replicates)`
    # produce an out-of-range value.
    scalar = SimulationParams.from_mapping(
        {**_valid_config(), "n_replicates": 1, "replicate_minimum": 100}
    )
    assert scalar.replicate_minimum == 100


def test_precision_and_stop_batch_early_round_trip() -> None:
    """`precision`, `stop_batch_early` and `confidence` round-trip exactly.

    `batch_precision` is `precision` while the batch may stop early and
    `None` once `stop_batch_early` is off: the one value the batch's
    stopping rule reads.
    """
    default_params = SimulationParams.from_mapping(_valid_config())
    assert default_params.to_dict()["precision"] == 0.01
    assert default_params.to_dict()["stop_batch_early"] is True
    assert default_params.to_dict()["confidence"] == 0.95
    assert SimulationParams.from_mapping(default_params.to_dict()) == default_params

    tightened = SimulationParams.from_mapping({**_valid_config(), "precision": 0.02})
    assert tightened.precision == 0.02
    assert tightened.batch_precision == 0.02
    assert tightened.to_dict()["precision"] == 0.02
    assert SimulationParams.from_mapping(tightened.to_dict()) == tightened

    disabled = SimulationParams.from_mapping(
        {**_valid_config(), "stop_batch_early": False}
    )
    assert disabled.batch_precision is None
    assert disabled.precision == 0.01
    assert disabled.to_dict()["stop_batch_early"] is False
    assert SimulationParams.from_mapping(disabled.to_dict()) == disabled


@pytest.mark.parametrize(
    "old_key", ["convergence_tolerance", "replicate_tolerance", "replicate_confidence"]
)
def test_the_merged_settings_old_names_are_refused(old_key: str) -> None:
    """`precision` and `confidence` replaced three settings; no alias is kept."""
    with pytest.raises(ValueError, match=old_key):
        SimulationParams.from_mapping({**_valid_config(), old_key: 0.05})


def test_required_and_conflicting_configuration_keys_are_named() -> None:
    """Missing required fields and incompatible locus forms fail clearly."""
    with pytest.raises(ValueError, match=r"missing required.*N"):
        SimulationParams.from_mapping({"d": 2, "m": 0.1, "mu": 0.1, "seed": 1})
    with pytest.raises(ValueError, match="loci cannot be combined"):
        SimulationParams.from_mapping(
            {
                **_valid_config(),
                "loci": [{"length": 100}],
                "n_loci": 1,
            }
        )


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({**_valid_config(), "loci": "not-a-list"}, "loci must be a list"),
        ({**_valid_config(), "loci": [1]}, "loci\\[0\\] must be a mapping"),
        (
            {**_valid_config(), "loci": [{"length": 100, "extra": 1}]},
            "unknown loci\\[0\\]",
        ),
        ({**_valid_config(), "loci": [{}]}, "missing 'length'"),
        (
            {**_valid_config(), "n_loci": 2, "locus_lengths": [100]},
            "exactly n_loci",
        ),
        ({**_valid_config(), "n_loci": 0}, "loci must not be empty"),
        ({**_valid_config(), "locus_lengths": "wide"}, "locus_lengths must"),
    ],
)
def test_locus_configuration_shapes_are_rejected(
    config: dict[str, object],
    message: str,
) -> None:
    """Both compact and expanded locus configuration forms are validated."""
    with pytest.raises(ValueError, match=message):
        SimulationParams.from_mapping(config)


def test_locus_lengths_scalar_expands_and_explicit_ids_round_trip() -> None:
    """Scalar lengths expand in order while explicit IDs remain stable."""
    params = SimulationParams.from_mapping(
        {
            **_valid_config(),
            "n_loci": 2,
            "locus_lengths": 300,
        }
    )
    assert params.loci == (LocusSpec(1, 300), LocusSpec(2, 300))
    explicit = SimulationParams.from_mapping(
        {
            **_valid_config(),
            "loci": [{"locus_id": 9, "length": 400}],
        }
    )
    assert explicit.loci == (LocusSpec(9, 400),)


def test_locus_lengths_accept_a_genuinely_distinct_value_per_locus() -> None:
    """Per-locus length varies freely; nothing forces loci to share one value."""
    via_locus_lengths = SimulationParams.from_mapping(
        {
            **_valid_config(),
            "n_loci": 3,
            "locus_lengths": [80, 4_000, 15],
        }
    )
    assert via_locus_lengths.loci == (
        LocusSpec(1, 80),
        LocusSpec(2, 4_000),
        LocusSpec(3, 15),
    )

    via_loci = SimulationParams.from_mapping(
        {
            **_valid_config(),
            "loci": [
                {"locus_id": 1, "length": 80},
                {"locus_id": 2, "length": 4_000},
            ],
        }
    )
    assert via_loci.loci == (LocusSpec(1, 80), LocusSpec(2, 4_000))
    assert via_loci.to_dict()["loci"] == [
        {"locus_id": 1, "length": 80},
        {"locus_id": 2, "length": 4_000},
    ]


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("N", True, "N must be"),
        ("N", "20", "N must be"),
        ("N", [20], "exactly d"),
        ("m", True, "m must be"),
        ("m", "0.1", "m must be"),
        ("m", [[1.0]], "shape"),
        ("m", [[0.5, 0.5], [0.2, 0.7]], "row 1"),
        ("mu", True, "mu must be"),
        ("mu", float("inf"), "mu must be finite"),
        ("seed", 1.5, "seed must be"),
        ("seed", -1, "seed must be at least 0"),
        ("d", True, "d must be"),
    ],
)
def test_scalar_and_matrix_parsers_reject_wrong_types_and_shapes(
    key: str,
    value: object,
    message: str,
) -> None:
    """Configuration parsers reject booleans, non-numbers, and bad matrices."""
    with pytest.raises(ValueError, match=message):
        SimulationParams.from_mapping({**_valid_config(), key: value})


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("not-a-list", "N must be an integer"),
        ([True, 20], "N\\[0\\] must be"),
        (["0.1"], "N\\[0\\] must be"),
    ],
)
def test_population_size_parser_is_strict(value: object, message: str) -> None:
    """Population sizes do not coerce strings or booleans."""
    with pytest.raises(ValueError, match=message):
        SimulationParams.from_mapping({**_valid_config(), "N": value})


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("not-a-matrix", "m must be a number"),
        ([0.2, 0.8], "m\\[0\\] must be a list"),
        ([[True, 0.0], [0.0, 1.0]], "m\\[0\\]\\[0\\] must be"),
    ],
)
def test_migration_parser_is_strict(value: object, message: str) -> None:
    """Migration accepts scalar or nested lists, but no ambiguous values."""
    with pytest.raises(ValueError, match=message):
        SimulationParams.from_mapping({**_valid_config(), "m": value})


def test_migration_accepts_ring_and_linear_topology_sugar() -> None:
    """A compact {topology, rate} mapping expands to the full dense matrix."""
    ring = SimulationParams.from_mapping(
        {**_valid_config(), "d": 6, "m": {"topology": "ring", "rate": 0.3}}
    )
    linear = SimulationParams.from_mapping(
        {**_valid_config(), "d": 6, "m": {"topology": "linear", "rate": 0.3}}
    )

    assert ring.m == (
        (0.7, 0.15, 0.0, 0.0, 0.0, 0.15),
        (0.15, 0.7, 0.15, 0.0, 0.0, 0.0),
        (0.0, 0.15, 0.7, 0.15, 0.0, 0.0),
        (0.0, 0.0, 0.15, 0.7, 0.15, 0.0),
        (0.0, 0.0, 0.0, 0.15, 0.7, 0.15),
        (0.15, 0.0, 0.0, 0.0, 0.15, 0.7),
    )
    assert linear.m == (
        (0.7, 0.3, 0.0, 0.0, 0.0, 0.0),
        (0.15, 0.7, 0.15, 0.0, 0.0, 0.0),
        (0.0, 0.15, 0.7, 0.15, 0.0, 0.0),
        (0.0, 0.0, 0.15, 0.7, 0.15, 0.0),
        (0.0, 0.0, 0.0, 0.15, 0.7, 0.15),
        (0.0, 0.0, 0.0, 0.0, 0.3, 0.7),
    )
    assert SimulationParams.from_mapping(ring.to_dict()) == ring


def test_migration_accepts_a_hand_authored_sparse_neighbor_map() -> None:
    """A sparse {deme: {neighbor: weight}} map expands the same way by hand.

    JSON object keys are always strings, so numeric-string keys must parse
    identically to native integer keys (mirroring how ``p_0``'s allele keys
    are already coerced).
    """
    via_int_keys = SimulationParams.from_mapping(
        {
            **_valid_config(),
            "d": 3,
            "m": {1: {2: 0.2}, 2: {1: 0.2, 3: 0.2}, 3: {2: 0.2}},
        }
    )
    via_string_keys = SimulationParams.from_mapping(
        {
            **_valid_config(),
            "d": 3,
            "m": {"1": {"2": 0.2}, "2": {"1": 0.2, "3": 0.2}, "3": {"2": 0.2}},
        }
    )

    expected = (
        (0.8, 0.2, 0.0),
        (0.2, 0.6, 0.2),
        (0.0, 0.2, 0.8),
    )
    assert via_int_keys.m == expected
    assert via_string_keys.m == expected


@pytest.mark.parametrize(
    ("m", "message"),
    [
        ({"topology": "ring"}, "is missing rate"),
        ({"rate": 0.1}, "is missing topology"),
        (
            {"topology": "square", "rate": 0.1},
            "must be 'ring', 'linear', or 'torus'",
        ),
        ({"topology": "ring", "rate": 0.1, "extra": 1}, "unknown m topology"),
        ({1: {2: 0.6, 3: 0.6}}, "sum to more than 1"),
        ({1: {1: 0.1}}, "cannot list itself"),
        ({1: {9: 0.1}}, "outside 1"),
        ({9: {1: 0.1}}, "outside 1"),
        ({1: "not-a-mapping"}, "must be a mapping of neighbor to weight"),
        ({"not-a-number": {2: 0.1}}, "deme identifiers must be integers"),
        ({2.9: {1: 0.1}}, "deme identifiers must be integers"),
        ({1: {2.9: 0.1}}, "deme identifiers must be integers"),
    ],
)
def test_migration_topology_and_sparse_map_are_validated(
    m: dict[object, object],
    message: str,
) -> None:
    """Every documented sparse-map and topology-sugar rule is enforced."""
    with pytest.raises(ValueError, match=message):
        SimulationParams.from_mapping({**_valid_config(), "d": 3, "m": m})


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("deme_weighting", "bad", "deme_weighting must be"),
        ("deme_weighting", False, "nonempty"),
        ("convergence_statistic", "", "nonempty"),
        ("convergence_statistic", 1, "string or a list of strings"),
        ("convergence_burn_in", 1.5, "must be an integer"),
        ("max_generations", True, "must be an integer"),
    ],
)
def test_optional_scalar_parsers_are_strict(
    key: str,
    value: object,
    message: str,
) -> None:
    """Optional configuration values retain their declared primitive types."""
    with pytest.raises(ValueError, match=message):
        SimulationParams.from_mapping({**_valid_config(), key: value})


def test_explicit_initial_frequencies_are_normalized_and_serialized() -> None:
    """Zero entries disappear while positive frequencies remain immutable."""
    params = SimulationParams.from_mapping(
        {
            **_valid_config(),
            "p_0": [[{"0": 1.0, "1": 0.0}], [{"0": 0.5, "1": 0.5}]],
        }
    )
    assert params.initial_frequencies is not None
    assert params.initial_frequencies[0][0] == {0: 1.0}
    assert "p_0" in params.to_dict()


@pytest.mark.parametrize(
    ("p_0", "message"),
    [
        ("bad", "p_0 must be a list"),
        (["bad", []], "p_0\\[0\\] must be a list"),
        ([[1], []], "p_0\\[0\\]\\[0\\] must be a mapping"),
        ([[{"bad": 1.0}], [{"0": 1.0}]], "allele ID"),
        ([[{1.9: 0.5, 0: 0.5}], [{0: 1.0}]], "allele ID.*must be an integer"),
        ([[{-3: 1.0}], [{0: 1.0}]], "allele ID.*must be a non-negative integer"),
        ([[{"0": -0.1}], [{"0": 1.0}]], "must be finite"),
        ([[{"0": True}], [{"0": 1.0}]], "must be a number"),
    ],
)
def test_explicit_frequency_parser_names_malformed_inputs(
    p_0: object,
    message: str,
) -> None:
    """Malformed nested frequency tables produce precise errors."""
    with pytest.raises(ValueError, match=message):
        SimulationParams.from_mapping({**_valid_config(), "p_0": p_0})


@pytest.mark.parametrize(
    ("p_0", "message"),
    [
        ([[{"0": 0.5}], [{"0": 1.0}]], "sum to 1"),
        ([[{"0": 1.0}], [{"0": 0.5}]], "sum to 1"),
        ([[{}], [{"0": 1.0}]], "sum to 1"),
        ([[{"0": 1.0}], [{"0": 1.0}], [{"0": 1.0}]], "exactly d"),
        ([[{"0": 1.0}, {"0": 0.0}], [{"0": 1.0}]], "exactly 1 loci"),
    ],
)
def test_explicit_frequency_shape_and_probability_validation(
    p_0: object,
    message: str,
) -> None:
    """Frequency tables must match demes, loci, and available support."""
    with pytest.raises(ValueError, match=message):
        SimulationParams.from_mapping({**_valid_config(), "p_0": p_0})


def test_explicit_frequency_support_cannot_exceed_deme_size() -> None:
    """Explicit support is bounded by the configured gene-copy count."""
    config = {
        **_valid_config(),
        "N": 2,
        "ploidy": "haploid",
        "initial_allele_count": 1,
        "p_0": [
            [{"0": 1 / 3, "1": 1 / 3, "2": 1 / 3}],
            [{"0": 1.0}],
        ],
    }
    with pytest.raises(ValueError, match="support"):
        SimulationParams.from_mapping(config)


def _equilibrium_config(**changes: object) -> dict[str, object]:
    """Return a valid config with all three equilibrium_* fields set."""
    return {
        **_valid_config(),
        "equilibrium_convergence_window": 2,
        "equilibrium_convergence_tolerance": 0.01,
        "equilibrium_max_generations": 100,
        **changes,
    }


def test_equilibrium_split_fields_default_to_none_and_round_trip() -> None:
    """All three fields are `None` by default, omitted from `to_dict()`.

    Matches `max_concurrent_replicates`'s own round-trip contract
    (`test_max_concurrent_replicates_defaults_to_none_and_round_trips`):
    an absent key and an explicit `None` mean the same thing here, so
    omitting them keeps `from_mapping(to_dict())` lossless without
    needing an always-present workaround.
    """
    default_params = SimulationParams.from_mapping(_valid_config())
    assert default_params.equilibrium_convergence_window is None
    assert default_params.equilibrium_convergence_tolerance is None
    assert default_params.equilibrium_max_generations is None
    assert "equilibrium_convergence_window" not in default_params.to_dict()
    assert SimulationParams.from_mapping(default_params.to_dict()) == default_params

    configured = SimulationParams.from_mapping(_equilibrium_config())
    assert configured.equilibrium_convergence_window == 2
    assert configured.equilibrium_convergence_tolerance == 0.01
    assert configured.equilibrium_max_generations == 100
    assert configured.to_dict()["equilibrium_convergence_window"] == 2
    assert SimulationParams.from_mapping(configured.to_dict()) == configured


@pytest.mark.parametrize(
    "omit",
    [
        "equilibrium_convergence_window",
        "equilibrium_convergence_tolerance",
        "equilibrium_max_generations",
    ],
)
def test_equilibrium_split_fields_must_be_set_together(omit: str) -> None:
    """Setting only one or two of the three fields is rejected, not guessed at."""
    config = _equilibrium_config()
    del config[omit]
    with pytest.raises(ValueError, match="must be set together, or not at all"):
        SimulationParams.from_mapping(config)


def test_equilibrium_split_fields_reject_an_explicit_p_0() -> None:
    """A run cannot both fix an explicit p_0 and derive one from equilibrium-split."""
    config = _equilibrium_config(p_0=[[{"0": 1.0}], [{"0": 1.0}]])
    with pytest.raises(ValueError, match="cannot be combined with an explicit p_0"):
        SimulationParams.from_mapping(config)


def test_equilibrium_convergence_window_rejects_below_two() -> None:
    """`equilibrium_convergence_window` keeps its historical minimum of 2."""
    config = _equilibrium_config(equilibrium_convergence_window=1)
    with pytest.raises(
        ValueError, match="equilibrium_convergence_window must be at least 2"
    ):
        SimulationParams.from_mapping(config)


@pytest.mark.parametrize("tolerance", [-0.1, 0.0])
def test_equilibrium_convergence_tolerance_rejects_zero_and_negative(
    tolerance: float,
) -> None:
    """The tolerance sets the ancestral burn-in; zero would make it endless."""
    config = _equilibrium_config(equilibrium_convergence_tolerance=tolerance)
    with pytest.raises(
        ValueError,
        match="equilibrium_convergence_tolerance must be finite and greater than 0",
    ):
        SimulationParams.from_mapping(config)


def test_equilibrium_max_generations_rejects_non_positive() -> None:
    config = _equilibrium_config(equilibrium_max_generations=0)
    with pytest.raises(
        ValueError, match="equilibrium_max_generations must be at least 1"
    ):
        SimulationParams.from_mapping(config)


def test_equilibrium_convergence_window_cannot_exceed_max_generations() -> None:
    """The ancestral phase runs at least the window, so it must fit the cap."""
    config = _equilibrium_config(
        equilibrium_convergence_window=6, equilibrium_max_generations=5
    )
    with pytest.raises(
        ValueError, match="equilibrium_convergence_window cannot exceed"
    ):
        SimulationParams.from_mapping(config)
    assert (
        SimulationParams.from_mapping(
            _equilibrium_config(
                equilibrium_convergence_window=5, equilibrium_max_generations=5
            )
        ).equilibrium_convergence_window
        == 5
    )


def _sigma_band_config(**changes: object) -> dict[str, object]:
    """Return a valid config with both sigma_band_* fields set."""
    return {
        **_valid_config(),
        "sigma_band_multiplier": 2.0,
        "sigma_band_window": 100,
        **changes,
    }


def test_sigma_band_fields_default_to_none_and_round_trip() -> None:
    """Both fields are `None` by default, omitted from `to_dict()`.

    Matches `equilibrium_*`'s own round-trip contract
    (`test_equilibrium_split_fields_default_to_none_and_round_trip`): an
    absent key and an explicit `None` mean the same thing here, so
    omitting them keeps `from_mapping(to_dict())` lossless.
    """
    default_params = SimulationParams.from_mapping(_valid_config())
    assert default_params.sigma_band_multiplier is None
    assert default_params.sigma_band_window is None
    assert "sigma_band_multiplier" not in default_params.to_dict()
    assert SimulationParams.from_mapping(default_params.to_dict()) == default_params

    configured = SimulationParams.from_mapping(_sigma_band_config())
    assert configured.sigma_band_multiplier == 2.0
    assert configured.sigma_band_window == 100
    assert configured.to_dict()["sigma_band_multiplier"] == 2.0
    assert SimulationParams.from_mapping(configured.to_dict()) == configured


@pytest.mark.parametrize("omit", ["sigma_band_multiplier", "sigma_band_window"])
def test_sigma_band_fields_must_be_set_together(omit: str) -> None:
    """Setting only one of the two fields is rejected, not guessed at."""
    config = _sigma_band_config()
    del config[omit]
    with pytest.raises(ValueError, match="must be set together, or not at all"):
        SimulationParams.from_mapping(config)


@pytest.mark.parametrize("multiplier", [1.0, 2.5, 4.0, 0.0, -2.0])
def test_sigma_band_multiplier_rejects_anything_but_two_or_three(
    multiplier: float,
) -> None:
    """The multiplier is a closed set, not merely a suggestion."""
    config = _sigma_band_config(sigma_band_multiplier=multiplier)
    with pytest.raises(ValueError, match="sigma_band_multiplier must be"):
        SimulationParams.from_mapping(config)


def test_sigma_band_multiplier_accepts_three() -> None:
    """3.0 is the other half of the closed set, not merely 2.0 alone."""
    params = SimulationParams.from_mapping(
        _sigma_band_config(sigma_band_multiplier=3.0)
    )
    assert params.sigma_band_multiplier == 3.0


def test_sigma_band_window_rejects_below_two() -> None:
    """`sigma_band_window` needs at least two points to show a spread."""
    config = _sigma_band_config(sigma_band_window=1)
    with pytest.raises(ValueError, match="sigma_band_window must be at least 2"):
        SimulationParams.from_mapping(config)


def test_sigma_band_fields_do_not_conflict_with_equilibrium_split() -> None:
    """Unlike equilibrium_*, the sigma band is never mutually exclusive."""
    config = {
        **_sigma_band_config(),
        "equilibrium_convergence_window": 2,
        "equilibrium_convergence_tolerance": 0.01,
        "equilibrium_max_generations": 100,
    }
    params = SimulationParams.from_mapping(config)
    assert params.sigma_band_multiplier == 2.0
    assert params.equilibrium_convergence_window == 2


def test_sigma_band_fields_do_not_conflict_with_explicit_p_0() -> None:
    """The sigma band is also never mutually exclusive with an explicit p_0."""
    config = _sigma_band_config(p_0=[[{"0": 1.0}], [{"0": 1.0}]])
    params = SimulationParams.from_mapping(config)
    assert params.sigma_band_multiplier == 2.0
    assert params.initial_frequencies is not None


def test_every_accepted_config_key_appears_in_configuration_md() -> None:
    """Every key `from_mapping` accepts is documented for a user.

    A static-analysis check in the same spirit as `test/test_mypy_
    scope.py`: it reads `doc/configuration.md` off disk and starts no
    simulation, so it runs in milliseconds. `_CONFIG_KEYS` is the
    authority on what a config file may contain — an unrecognized key is
    rejected by name (`from_mapping`'s own strictness) — and
    `doc/configuration.md` is the only place a user finds out which keys
    those are. A key the parser accepts but the reference never mentions
    is, from outside, indistinguishable from one that does not exist.

    Regression test. `equilibrium_convergence_window`,
    `equilibrium_convergence_tolerance`, and
    `equilibrium_max_generations` shipped accepted, defaulted, and
    documented in `SimulationParams`'s own docstring, but appeared
    nowhere in `doc/configuration.md` at all, and nothing caught it for
    an entire release cycle (2026-09-12 API-compatibility-policy design,
    Approach E). Presence anywhere in the document is all this asserts,
    deliberately: a key does not need a heading of its own to be
    properly covered — `mu_b`, `n_loci`, and `locus_lengths` are each
    documented inside their parent key's own section, and the three
    `equilibrium_*` keys share one section because they must be set
    together.
    """
    documentation = (
        Path(__file__).resolve().parents[2] / "doc" / "configuration.md"
    ).read_text(encoding="utf-8")

    undocumented = sorted(key for key in _CONFIG_KEYS if key not in documentation)
    # Label keys are accepted too, so they need documenting just the same.
    # `name` and `class` are ordinary English words, so only their
    # code-formatted spelling counts as documenting them.
    undocumented += sorted(
        key
        for key in _LABEL_KEYS | _INTERNAL_KEYS
        if f"### `{key}`" not in documentation
    )

    assert not undocumented, (
        f"configuration keys accepted by SimulationParams.from_mapping but "
        f"absent from doc/configuration.md: {', '.join(undocumented)}"
    )


def test_migration_accepts_a_torus_topology_and_rejects_a_mismatched_shape() -> None:
    """`m: {topology: torus, rate, rows, columns}` expands to the dense matrix."""
    config = {
        **_valid_config(),
        "d": 12,
        "N": 100,
        "ploidy": "haploid",
        "m": {"topology": "torus", "rate": 0.4, "rows": 3, "columns": 4},
    }
    params = SimulationParams.from_mapping(config)

    assert isinstance(params.m, tuple)
    assert params.m[0][1] == pytest.approx(0.1)
    assert params.m[0][8] == pytest.approx(0.1)
    assert params.m[0][0] == pytest.approx(0.6)

    with pytest.raises(ValueError, match="has 12 demes, but d is 6"):
        SimulationParams.from_mapping({**config, "d": 6})
    with pytest.raises(ValueError, match=r"m\.rows must be an integer"):
        SimulationParams.from_mapping(
            {**config, "m": {**config["m"], "rows": 3.0}}  # type: ignore[dict-item]
        )


def test_ploidy_is_required_and_the_message_says_how_to_fix_the_file() -> None:
    """A configuration with no ploidy is refused, naming the fix."""
    config = {key: value for key, value in _valid_config().items() if key != "ploidy"}

    with pytest.raises(ValueError, match="ploidy is required") as error:
        SimulationParams.from_mapping(config)

    assert "N: 225 with ploidy: diploid" in str(error.value)


@pytest.mark.parametrize("old_spelling", [1, 2, 3, 4, "2", 2.0, True])
def test_an_integer_ploidy_is_refused_so_an_old_file_cannot_be_misread(
    old_spelling: object,
) -> None:
    """Files written when N meant gene copies say `ploidy: 2`; they fail loudly.

    Read under the new rule, `N: 450` with `ploidy: 2` would silently be 900
    gene copies, so only the words are accepted.
    """
    with pytest.raises(ValueError, match="ploidy must be a word") as error:
        SimulationParams.from_mapping(
            {**_valid_config(), "N": 450, "ploidy": old_spelling}
        )

    assert "N: 225 with ploidy: diploid" in str(error.value)


@pytest.mark.parametrize("word", ["pentaploid", "", "dip", "diploids"])
def test_only_the_four_ploidy_words_are_accepted(word: str) -> None:
    """Anything else is refused with the same guidance."""
    with pytest.raises(ValueError, match="ploidy must be a word"):
        SimulationParams.from_mapping({**_valid_config(), "ploidy": word})


@pytest.mark.parametrize(
    ("word", "ploidy"),
    [
        ("haploid", 1),
        ("diploid", 2),
        ("triploid", 3),
        ("tetraploid", 4),
        ("Diploid", 2),
        (" TETRAPLOID ", 4),
    ],
)
def test_ploidy_words_parse_case_insensitively(word: str, ploidy: int) -> None:
    """Spelling is forgiving about case and surrounding space."""
    params = SimulationParams.from_mapping({**_valid_config(), "ploidy": word})

    assert params.ploidy == ploidy


def test_n_counts_individuals_and_the_params_hold_gene_copies() -> None:
    """225 diploid individuals per deme is 450 gene copies inside."""
    params = SimulationParams.from_mapping(
        {**_valid_config(), "N": 225, "ploidy": "diploid"}
    )

    assert params.gene_copies == 450
    assert params.individuals == 225
    assert params.ploidy == 2


def test_a_per_deme_list_of_individuals_is_multiplied_by_the_ploidy() -> None:
    """Each deme's count is converted, in order."""
    params = SimulationParams.from_mapping(
        {**_valid_config(), "d": 3, "N": [200, 300, 150], "ploidy": "triploid"}
    )

    assert params.gene_copies == (600, 900, 450)
    assert params.individuals == (200, 300, 150)


def test_to_dict_writes_individuals_and_the_ploidy_word() -> None:
    """The record and the configuration say the same thing."""
    params = SimulationParams.from_mapping(
        {**_valid_config(), "N": 225, "ploidy": "diploid"}
    )

    assert params.to_dict()["N"] == 225
    assert params.to_dict()["ploidy"] == "diploid"


@pytest.mark.parametrize("ploidy", ["haploid", "diploid", "triploid", "tetraploid"])
def test_from_mapping_of_to_dict_is_the_identity(ploidy: str) -> None:
    """The round trip holds for every ploidy, scalar and per-deme N."""
    for n in (40, [40, 60]):
        params = SimulationParams.from_mapping(
            {**_valid_config(), "N": n, "ploidy": ploidy}
        )

        assert SimulationParams.from_mapping(params.to_dict()) == params


def test_the_same_population_in_either_unit_differs_only_in_the_run_id() -> None:
    """450 haploid and 225 diploid are one process with two records."""
    haploid = SimulationParams.from_mapping(
        {**_valid_config(), "N": 450, "ploidy": "haploid"}
    )
    diploid = SimulationParams.from_mapping(
        {**_valid_config(), "N": 225, "ploidy": "diploid"}
    )

    assert haploid.gene_copies == diploid.gene_copies == 450
    assert deterministic_run_id(diploid) != deterministic_run_id(haploid)


def test_a_directly_built_params_defaults_to_haploid() -> None:
    """Direct construction names gene copies; ploidy defaults to haploid."""
    params = SimulationParams(gene_copies=20, m=0.1, mu=0.001, d=2, seed=7)

    assert params.ploidy == 1
    assert params.individuals == 20
    assert params.to_dict()["ploidy"] == "haploid"


@pytest.mark.parametrize("ploidy", [0, 5, -1])
def test_a_directly_built_ploidy_outside_one_to_four_is_rejected(ploidy: int) -> None:
    """Only haploid through tetraploid exist."""
    with pytest.raises(ValueError, match="ploidy must be 1, 2, 3, or 4"):
        SimulationParams(gene_copies=60, m=0.1, mu=0.001, d=2, seed=7, ploidy=ploidy)


def test_a_directly_built_gene_copy_count_must_divide_by_the_ploidy() -> None:
    """A deme's gene copies must be a whole number of individuals."""
    with pytest.raises(ValueError, match=r"gene_copies\[1\] is 41"):
        SimulationParams(gene_copies=(40, 41), m=0.1, mu=0.001, d=2, seed=7, ploidy=2)


def test_describe_population_reads_the_way_a_botanist_does() -> None:
    """One formatter for every surface that shows the population size."""
    diploid = SimulationParams.from_mapping(
        {**_valid_config(), "N": 225, "ploidy": "diploid"}
    )
    unequal = SimulationParams.from_mapping(
        {**_valid_config(), "d": 3, "N": [200, 300, 150], "ploidy": "diploid"}
    )
    direct = SimulationParams(gene_copies=20, m=0.1, mu=0.001, d=2, seed=7)

    assert describe_population(diploid) == "225 diploid individuals per deme"
    assert describe_population(unequal) == "200, 300, 150 diploid individuals per deme"
    assert describe_population(direct) == "20 haploid individuals per deme"


def test_unset_burn_in_and_cap_are_derived_from_the_model() -> None:
    """Unset burn-in and cap take the derived values, recorded as derived."""
    params = SimulationParams.from_mapping(
        {"N": 100, "ploidy": "haploid", "d": 5, "m": 0.0001, "mu": 0.000001, "seed": 1}
    )
    tau = params.relaxation_time
    assert params.auto_derived == {"convergence_burn_in", "max_generations"}
    assert tau == pytest.approx(19_700, rel=0.01)
    assert tau is not None
    assert params.convergence_burn_in == math.ceil(math.log(200.0) * tau)
    assert params.max_generations == params.convergence_burn_in + math.ceil(15 * tau)


@pytest.mark.parametrize("auto", [None, "auto", "AUTO", " auto "])
def test_auto_spellings_all_mean_derive(auto: object) -> None:
    """`null` and `auto` (any case) request derivation."""
    config = {
        **_valid_config(),
        "convergence_burn_in": auto,
        "max_generations": auto,
    }
    params = SimulationParams.from_mapping(config)
    assert params.auto_derived == {"convergence_burn_in", "max_generations"}


@pytest.mark.parametrize("bad", [0, -5, "many", 1.5, True])
def test_a_bad_burn_in_or_cap_is_rejected_not_read_as_auto(bad: object) -> None:
    """A bare zero (the internal sentinel) and other junk are errors."""
    with pytest.raises(ValueError, match="convergence_burn_in"):
        SimulationParams.from_mapping({**_valid_config(), "convergence_burn_in": bad})
    with pytest.raises(ValueError, match="max_generations"):
        SimulationParams.from_mapping({**_valid_config(), "max_generations": bad})


def test_explicit_values_win_and_are_not_recorded_as_derived() -> None:
    """Both explicit: nothing derived, but the relaxation time is still known."""
    params = SimulationParams.from_mapping(
        {**_valid_config(), "convergence_burn_in": 60, "max_generations": 900}
    )
    assert (params.convergence_burn_in, params.max_generations) == (60, 900)
    assert params.auto_derived == frozenset()
    derived = SimulationParams.from_mapping(_valid_config())
    assert params.relaxation_time == derived.relaxation_time
    assert params.relaxation_time is not None


def test_a_model_without_a_relaxation_time_runs_with_explicit_values() -> None:
    """No migration and no mutation: explicit values work and `tau` stays unset."""
    params = SimulationParams.from_mapping(
        {
            **_valid_config(),
            "m": 0.0,
            "mu": 0.0,
            "convergence_burn_in": 60,
            "max_generations": 900,
        }
    )
    assert params.relaxation_time is None


def test_the_relaxation_time_follows_the_slowest_locus() -> None:
    """Per-locus mutation rates `[1e-3, 1e-5]` give the `1e-5` locus's `tau`."""
    config = _valid_config()
    mixed = SimulationParams.from_mapping(
        {
            **config,
            "loci": [{"locus_id": 1, "length": 10}, {"locus_id": 2, "length": 20}],
            "mu": [1e-3, 1e-5],
        }
    )
    slow = SimulationParams.from_mapping({**config, "mu": 1e-5})
    assert mixed.relaxation_time == slow.relaxation_time
    assert mixed.convergence_burn_in == slow.convergence_burn_in


def test_a_derived_burn_in_follows_the_precision() -> None:
    """A tighter precision derives a longer burn-in (`k = ln(2 / precision)`)."""
    loose = SimulationParams.from_mapping({**_valid_config(), "precision": 0.1})
    tight = SimulationParams.from_mapping({**_valid_config(), "precision": 0.0001})
    tau = loose.relaxation_time
    assert tau is not None
    assert tau == tight.relaxation_time
    assert loose.convergence_burn_in == math.ceil(5.0 * tau)
    assert tight.convergence_burn_in == math.ceil(math.log(20_000.0) * tau)


def test_a_derived_cap_includes_an_explicit_burn_in() -> None:
    """A long explicit burn-in raises the derived cap with it."""
    params = SimulationParams.from_mapping(
        {**_valid_config(), "convergence_burn_in": 500_000}
    )
    assert params.relaxation_time is not None
    assert params.max_generations == 500_000 + math.ceil(15 * params.relaxation_time)
    assert params.auto_derived == {"max_generations"}


def test_derived_values_round_trip_as_concrete_integers() -> None:
    """The round trip yields an equal, fully explicit configuration."""
    original = SimulationParams.from_mapping(_valid_config())
    again = SimulationParams.from_mapping(original.to_dict())
    assert again == original
    assert again.auto_derived == frozenset()
    assert original.to_dict()["convergence_burn_in"] == original.convergence_burn_in


def test_no_migration_and_no_mutation_needs_an_explicit_cap() -> None:
    """Nothing to wait for: a derived cap is refused, an explicit one works.

    The burn-in then falls back to the first tenth of the run: `auto` stays
    `auto` (zero inside) and round-trips as such.
    """
    config = {"N": 20, "ploidy": "haploid", "d": 2, "m": 0.0, "mu": 0.0, "seed": 1}
    with pytest.raises(ValueError, match="cannot derive max_generations"):
        SimulationParams.from_mapping(config)
    params = SimulationParams.from_mapping({**config, "max_generations": 500})
    assert params.convergence_burn_in == 0
    assert params.auto_derived == {"convergence_burn_in"}
    assert params.to_dict()["convergence_burn_in"] == "auto"
    assert SimulationParams.from_mapping(params.to_dict()) == params


def test_a_large_explicit_matrix_needs_an_explicit_cap() -> None:
    """An explicit matrix beyond the eigenvalue route's size is refused."""
    d = 30
    matrix = [
        [0.99 if row == column else 0.01 / (d - 1) for column in range(d)]
        for row in range(d)
    ]
    config = {"N": 20, "ploidy": "haploid", "d": d, "m": matrix, "mu": 0.001, "seed": 1}
    with pytest.raises(ValueError, match="explicit"):
        SimulationParams.from_mapping(config)
    params = SimulationParams.from_mapping({**config, "max_generations": 600})
    assert params.relaxation_time is None
    assert params.convergence_burn_in == 0


@pytest.mark.parametrize("model", ["infinite_alleles", "finite_alleles"])
def test_generational_vector_accepts_either_mutation_model(model: str) -> None:
    """Backend V runs both mutation models, so neither is rejected at load."""
    params = SimulationParams.from_mapping(
        {
            "N": 20,
            "ploidy": "haploid",
            "d": 3,
            "m": 0.1,
            "mu": 0.001,
            "seed": 1,
            "mutation_model": model,
            "engine_backend": "generational-vector",
        }
    )
    assert params.engine_backend == "generational-vector"
    assert params.mutation_model == model


def test_generational_vector_accepts_stochastic_migrant_sampling() -> None:
    """Backend V draws stochastic migrant counts, so the combination is valid."""
    params = SimulationParams.from_mapping(
        {
            "N": 20,
            "ploidy": "haploid",
            "d": 3,
            "m": 0.1,
            "mu": 0.001,
            "seed": 1,
            "migrant_sampling": "stochastic",
            "engine_backend": "generational-vector",
        }
    )
    assert params.migrant_sampling == "stochastic"
    assert params.engine_backend == "generational-vector"


def test_validate_execution_settings_accepts_a_vector_backend_without_a_model() -> None:
    """`generational-vector` is a valid execution default on its own.

    Whether a particular model can use it (a finite-alleles locus short
    enough to allocate) is decided when the backend is built.
    """
    validate_execution_settings(
        {
            "engine_backend": "generational-vector",
            "jit": "off",
            "n_replicates": 16,
            "max_generations": 100,
            "precision": 0.02,
            "confidence": 0.95,
            "auto_vector_min_d": 2,
            "auto_vector_max_capacity": 4096,
            "max_concurrent_replicates": None,
        }
    )
    validate_execution_settings({"max_generations": "auto"})


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({"n_replicates": 0}, "n_replicates must be at least 1"),
        ({"max_generations": 0}, "max_generations must be at least 1"),
        ({"precision": -0.1}, "precision must be non-negative"),
        ({"confidence": 0.5}, "confidence must be 0.90"),
        ({"engine_backend": "fast"}, "engine_backend must be"),
        ({"jit": "yes"}, "jit must be 'off' or 'numba'"),
        ({"auto_vector_min_d": 0}, "auto_vector_min_d must be at least 1"),
        ({"max_concurrent_replicates": 0}, "max_concurrent_replicates must be"),
        ({"engine_backend": "lineal", "jit": "numba"}, "only accepts jit='off'"),
    ],
)
def test_validate_execution_settings_rejects_with_simulation_params_wording(
    settings: dict[str, object], message: str
) -> None:
    """Each check uses the message `SimulationParams` itself raises."""
    with pytest.raises(ValueError, match=message):
        validate_execution_settings(settings)


def test_validate_execution_settings_matches_simulation_params_on_the_same_values() -> (
    None
):
    """A value `validate_execution_settings` refuses, `SimulationParams` refuses too."""
    with pytest.raises(ValueError, match="confidence") as from_params:
        SimulationParams.from_mapping({**_valid_config(), "confidence": 0.5})
    with pytest.raises(ValueError, match="confidence") as from_settings:
        validate_execution_settings({"confidence": 0.5})
    assert str(from_params.value) == str(from_settings.value)


def test_expert_settings_default_to_the_policy_constants_and_are_not_written() -> None:
    """With no `expert:` mapping every setting is its default and none is emitted."""
    params = SimulationParams.from_mapping(_valid_config())
    assert params.expert == ExpertSettings()
    assert "expert" not in params.to_dict()


def test_expert_settings_round_trip_and_only_changes_are_written() -> None:
    """A changed Expert Setting is copied into the parameters and round-trips."""
    params = SimulationParams.from_mapping(
        {
            **_valid_config(),
            "expert": {"minimum_effective_sample_size": 20, "first_check_minimum": 5},
        }
    )
    assert params.expert.minimum_effective_sample_size == 20.0
    assert params.expert.first_check_minimum == 5
    assert params.to_dict()["expert"] == {
        "minimum_effective_sample_size": 20.0,
        "first_check_minimum": 5,
    }
    assert SimulationParams.from_mapping(params.to_dict()) == params


def test_expert_settings_change_the_run_id_but_defaults_do_not() -> None:
    """A run that changes an Expert Setting is a different run."""
    base = SimulationParams.from_mapping(_valid_config())
    same = SimulationParams.from_mapping({**_valid_config(), "expert": {}})
    other = SimulationParams.from_mapping(
        {**_valid_config(), "expert": {"check_growth": 1.5}}
    )
    assert deterministic_run_id(same) == deterministic_run_id(base)
    assert deterministic_run_id(other) != deterministic_run_id(base)


@pytest.mark.parametrize(
    ("expert", "message"),
    [
        ({"no_such_setting": 1}, "unknown expert setting"),
        ({"burn_in_minimum_relaxation_times": 0.5}, "at least 1"),
        ({"first_check_relaxation_times": 0}, "greater than 0"),
        ({"first_check_minimum": 2}, "at least 3"),
        ({"first_check_minimum": 5.5}, "whole number"),
        ({"minimum_effective_sample_size": 9}, "at least 10"),
        ({"check_growth": 1.0}, "greater than 1"),
        ({"fractional_burn_in": 1.0}, "below 1"),
        ({"cap_minimum": 100, "cap_maximum": 50}, "cap_maximum"),
        ({"start_drift_alert_z": -1}, "greater than 0"),
        ({"estimate_auto_denominator": 0}, "greater than 0"),
        ({"estimate_auto_fraction": 1.0}, "below 1"),
        ({"check_growth": "fast"}, "check_growth"),
        ("everything", "mapping"),
    ],
)
def test_an_invalid_expert_setting_is_refused_by_name(
    expert: object, message: str
) -> None:
    """Unknown names and out-of-range values are rejected with a clear message."""
    with pytest.raises(ValueError, match=message):
        SimulationParams.from_mapping({**_valid_config(), "expert": expert})


def test_expert_settings_change_the_derived_burn_in_and_cap() -> None:
    """The burn-in floor and cap multiple reach the derivation."""
    base = SimulationParams.from_mapping(_valid_config())
    changed = SimulationParams.from_mapping(
        {
            **_valid_config(),
            "expert": {
                "burn_in_minimum_relaxation_times": 40,
                "cap_relaxation_multiple": 100,
                "cap_minimum": 1000,
            },
        }
    )
    tau = base.relaxation_time
    assert tau is not None
    assert changed.convergence_burn_in == math.ceil(40 * tau)
    assert changed.max_generations == changed.convergence_burn_in + math.ceil(100 * tau)


def test_convergence_estimate_defaults_to_mean_of_values_and_round_trips() -> None:
    """The setting defaults to the mean of values and survives `to_dict`."""
    default = SimulationParams.from_mapping(_valid_config())
    assert default.convergence_estimate == "mean_of_values"
    assert default.to_dict()["convergence_estimate"] == "mean_of_values"
    for choice in ("mean_of_values", "value_of_means", "auto"):
        params = SimulationParams.from_mapping(
            {**_valid_config(), "convergence_estimate": choice}
        )
        assert params.convergence_estimate == choice
        again = SimulationParams.from_mapping(params.to_dict())
        assert again.convergence_estimate == choice


def test_convergence_estimate_rejects_an_unknown_form_and_changes_the_run_id() -> None:
    """Only the three documented words are accepted, and the choice is identity."""
    with pytest.raises(ValueError, match="convergence_estimate must be"):
        SimulationParams.from_mapping(
            {**_valid_config(), "convergence_estimate": "median"}
        )
    base = SimulationParams.from_mapping(_valid_config())
    other = SimulationParams.from_mapping(
        {**_valid_config(), "convergence_estimate": "value_of_means"}
    )
    assert deterministic_run_id(other) != deterministic_run_id(base)
