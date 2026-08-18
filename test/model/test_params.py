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


def test_scalar_parameters_construct_with_documented_defaults() -> None:
    """The public P-bag defaults remain synchronized with the design."""
    params = SimulationParams.from_mapping(_valid_config())

    assert params.initial_allele_count == PARAMETER_DEFAULTS["initial_allele_count"]
    assert params.initial_concentration == PARAMETER_DEFAULTS["initial_concentration"]
    assert params.deme_weighting == PARAMETER_DEFAULTS["deme_weighting"]
    assert params.convergence_statistic == PARAMETER_DEFAULTS["convergence_statistic"]
    assert params.convergence_window == PARAMETER_DEFAULTS["convergence_window"]
    assert params.convergence_tolerance == PARAMETER_DEFAULTS["convergence_tolerance"]
    assert params.max_generations == PARAMETER_DEFAULTS["max_generations"]


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
        ({"convergence_tolerance": -1.0}, "non-negative"),
        ({"convergence_tolerance": float("nan")}, "finite"),
        ({"max_generations": 0}, "max_generations"),
        ({"n_replicates": 0}, "n_replicates"),
    ],
)
def test_post_init_validation_covers_all_scalar_contracts(
    updates: dict[str, object],
    message: str,
) -> None:
    """Every scalar validation rule rejects its documented invalid input."""
    with pytest.raises(ValueError, match=message):
        SimulationParams.from_mapping({**_valid_config(), **updates})


def test_required_and_conflicting_configuration_keys_are_named() -> None:
    """Missing required fields and incompatible locus forms fail clearly."""
    with pytest.raises(ValueError, match="missing required.*N"):
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


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("deme_weighting", "bad", "deme_weighting must be"),
        ("deme_weighting", False, "nonempty"),
        ("convergence_statistic", "", "nonempty"),
        ("convergence_statistic", 1, "nonempty"),
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
