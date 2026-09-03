"""Tests for validated and replayable simulation parameters."""

import pytest

from fim.model.locus import LocusSpec
from fim.model.params import PARAMETER_DEFAULTS, SimulationParams


def _valid_config() -> dict[str, object]:
    """Return the smallest complete config mapping."""
    return {
        "N": 20,
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
    assert params.convergence_statistic == PARAMETER_DEFAULTS["convergence_statistic"]
    assert params.convergence_statistics == ("D",)
    assert params.convergence_combinator == PARAMETER_DEFAULTS["convergence_combinator"]
    assert params.convergence_window == PARAMETER_DEFAULTS["convergence_window"]
    assert params.convergence_tolerance == PARAMETER_DEFAULTS["convergence_tolerance"]
    assert params.max_generations == PARAMETER_DEFAULTS["max_generations"]
    assert params.n_replicates == PARAMETER_DEFAULTS["n_replicates"]
    assert params.n_replicates == 200
    assert params.replicate_tolerance == PARAMETER_DEFAULTS["replicate_tolerance"]
    assert params.replicate_tolerance == 0.01
    assert params.replicate_minimum == PARAMETER_DEFAULTS["replicate_minimum"]
    assert params.replicate_confidence == PARAMETER_DEFAULTS["replicate_confidence"]
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
        ("convergence_window", 1, "convergence_window"),
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
        N=(10, 20),
        m=((0.9, 0.1), (0.2, 0.8)),
        mu=0.001,
        d=2,
        seed=7,
        loci=(LocusSpec(1, 200),),
    )

    assert params.population_sizes == (10, 20)


def test_initial_allele_count_is_bounded_by_the_smallest_deme_n() -> None:
    """Unequal per-deme N constrains founding alleles by the smallest deme."""
    config = {**_valid_config(), "N": [5, 50], "initial_allele_count": 6}

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


def test_mapping_round_trip_is_lossless() -> None:
    """Manifest serialization reconstructs equal parameters."""
    original = SimulationParams.from_mapping(
        {
            **_valid_config(),
            "loci": [{"locus_id": 3, "length": 500}],
            "convergence_window": 6,
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
        ({"convergence_tolerance": -1.0}, "non-negative"),
        ({"convergence_tolerance": float("nan")}, "finite"),
        ({"max_generations": 0}, "max_generations"),
        (
            # Default convergence_window is 50; capping max_generations to 5
            # leaves room for only 6 possible records (generation 0 plus 5
            # steps), so the window could never fill before the cap stops
            # the run.
            {"max_generations": 5},
            "convergence_window cannot exceed max_generations",
        ),
        ({"n_replicates": 0}, "n_replicates"),
        ({"replicate_tolerance": -1.0}, "non-negative"),
        ({"replicate_tolerance": float("nan")}, "finite"),
        ({"replicate_minimum": 1}, "replicate_minimum"),
        ({"replicate_confidence": 0.80}, "replicate_confidence"),
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
    `replicate_tolerance` stopped defaulting to `None`: the same
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
            "replicate_tolerance": 0.0,
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
            "replicate_tolerance": 0.0,
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


def test_replicate_tolerance_round_trips_unconditionally() -> None:
    """`replicate_tolerance` always round-trips exactly, `None` included.

    An absent `replicate_tolerance` key means "use the default"
    (`DEFAULT_REPLICATE_TOLERANCE`, `0.01`) now, not "disabled" — that
    default is a real, non-`None` number. `to_dict()` always includes
    `replicate_tolerance` unconditionally (`null` for `None`), unlike
    `initial_frequencies` (still omitted when `None`, since `None` is
    still *that* field's own default): omitting a `None`
    `replicate_tolerance` the same way would silently turn an explicit
    "disabled" into "use the default" the next time the dict round-trips
    through `from_mapping` — a real bug this project's own test suite
    caught directly (several tests build a batch config via
    `{**tiny_params.to_dict(), "n_replicates": N}`, which depends on
    `to_dict()` preserving `tiny_params`'s own explicit `replicate_
    tolerance=None` losslessly).
    """
    default_params = SimulationParams.from_mapping(_valid_config())
    assert (
        default_params.replicate_tolerance == PARAMETER_DEFAULTS["replicate_tolerance"]
    )
    assert default_params.to_dict()["replicate_tolerance"] == 0.01
    assert SimulationParams.from_mapping(default_params.to_dict()) == default_params

    tightened = SimulationParams.from_mapping(
        {**_valid_config(), "replicate_tolerance": 0.02}
    )
    assert tightened.replicate_tolerance == 0.02
    assert tightened.to_dict()["replicate_tolerance"] == 0.02
    assert SimulationParams.from_mapping(tightened.to_dict()) == tightened

    disabled = SimulationParams.from_mapping(
        {**_valid_config(), "replicate_tolerance": None}
    )
    assert disabled.replicate_tolerance is None
    assert disabled.to_dict()["replicate_tolerance"] is None
    assert SimulationParams.from_mapping(disabled.to_dict()) == disabled


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
        ({"topology": "square", "rate": 0.1}, "must be 'ring' or 'linear'"),
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
        ("convergence_window", 1.5, "must be an integer"),
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
        "initial_allele_count": 1,
        "p_0": [
            [{"0": 1 / 3, "1": 1 / 3, "2": 1 / 3}],
            [{"0": 1.0}],
        ],
    }
    with pytest.raises(ValueError, match="support"):
        SimulationParams.from_mapping(config)
