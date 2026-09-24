"""Tests for `fim.sweep`: axes, enumeration, validation and seed policy."""

from __future__ import annotations

from typing import Any

import pytest

from fim.model.params import SimulationParams
from fim.sweep import (
    SIZE_CONFIRMATION_THRESHOLD,
    SweepAxis,
    SweepSpec,
    enumerate_points,
    expand_axis,
    spec_from_config,
    work_estimate,
)

_BASE: dict[str, Any] = {
    "N": 40,
    "ploidy": 2,
    "d": 4,
    "m": 0.01,
    "mu": 0.001,
    "seed": 1000,
    "n_replicates": 3,
    "max_generations": 50,
}


def _spec(*axes: SweepAxis, **overrides: Any) -> SweepSpec:
    """Build a spec over `_BASE` with the given axes."""
    return SweepSpec(base={**_BASE, **overrides}, axes=axes)


def test_a_list_axis_keeps_its_values_and_rejects_a_repeat() -> None:
    assert expand_axis("d", [4, 8, 16]).values == (4, 8, 16)
    with pytest.raises(ValueError, match="more than once"):
        expand_axis("d", [4, 4])


def test_a_linear_range_includes_both_endpoints_exactly() -> None:
    axis = expand_axis("m", {"start": 0.0, "stop": 0.3, "count": 4, "scale": "linear"})

    assert axis.values == (0.0, 0.1, 0.2, 0.3)


def test_a_log_range_is_geometric_and_platform_stable() -> None:
    axis = expand_axis("m", {"start": 0.0001, "stop": 0.1, "count": 4, "scale": "log"})

    assert axis.values == (0.0001, 0.001, 0.01, 0.1)


def test_a_range_defaults_to_the_keys_own_scale() -> None:
    log_default = expand_axis("m", {"start": 0.001, "stop": 0.1, "count": 3})
    linear_default = expand_axis("d", {"start": 2, "stop": 6, "count": 3})

    assert log_default.values == (0.001, 0.01, 0.1)
    assert linear_default.values == (2, 4, 6)


def test_an_integer_range_rounds_and_removes_repeats() -> None:
    axis = expand_axis("d", {"start": 2, "stop": 5, "count": 8, "scale": "log"})

    assert axis.values == tuple(sorted(set(axis.values)))
    assert axis.values[0] == 2
    assert axis.values[-1] == 5


def test_a_count_of_one_gives_the_start() -> None:
    assert expand_axis("m", {"start": 0.01, "stop": 0.5, "count": 1}).values == (0.01,)


@pytest.mark.parametrize(
    ("key", "definition", "message"),
    [
        ("bogus", [1], "cannot sweep"),
        ("m", [2.0], "at most"),
        ("d", [1], "at least"),
        ("d", [3.5], "whole numbers"),
        ("m", ["x"], "numbers"),
        ("topology", ["cube"], "one of"),
        ("topology", {"start": 1, "stop": 2, "count": 2}, "not a range"),
        ("m", {"start": 0.0, "stop": 0.1, "count": 3, "scale": "log"}, "above 0"),
        ("m", {"start": 0.1, "stop": 0.2}, "needs count"),
        ("m", {"start": 0.1, "stop": 0.2, "count": 0}, "at least 1"),
        ("m", {"start": 0.1, "stop": 0.2, "count": 2, "step": 1}, "unknown range"),
        ("m", 0.1, "list of values"),
        ("m", [], "no values"),
    ],
)
def test_a_bad_axis_definition_is_named(
    key: str, definition: Any, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        expand_axis(key, definition)


def test_enumeration_is_first_axis_slowest_and_indexed_by_grid_position() -> None:
    spec = _spec(expand_axis("m", [0.01, 0.1]), expand_axis("d", [4, 6, 8]))

    plan = enumerate_points(spec)

    assert plan.grid_size == 6
    assert [tuple(p.coordinates.values()) for p in plan.points] == [
        (0.01, 4),
        (0.01, 6),
        (0.01, 8),
        (0.1, 4),
        (0.1, 6),
        (0.1, 8),
    ]
    assert [p.index for p in plan.points] == [0, 1, 2, 3, 4, 5]


def test_every_point_is_a_valid_configuration_with_its_deterministic_id() -> None:
    plan = enumerate_points(_spec(expand_axis("d", [4, 6])))

    for point in plan.points:
        params = SimulationParams.from_mapping(point.params)
        assert params.d == point.coordinates["d"]
        assert point.run_id.startswith("run-")
    assert plan.points[0].run_id != plan.points[1].run_id


def test_enumeration_is_deterministic() -> None:
    spec = _spec(expand_axis("m", [0.01, 0.1]), expand_axis("d", [4, 6]))

    first = [p.run_id for p in enumerate_points(spec).points]
    second = [p.run_id for p in enumerate_points(spec).points]

    assert first == second


def test_an_n_axis_counts_individuals_and_multiplies_by_ploidy() -> None:
    plan = enumerate_points(_spec(expand_axis("N", [10, 25]), ploidy=3, N=30))

    assert [p.params["N"] for p in plan.points] == [30, 75]
    assert [SimulationParams.from_mapping(p.params).ploidy for p in plan.points] == [
        3,
        3,
    ]


def test_an_n_axis_needs_a_ploidy() -> None:
    base = {key: value for key, value in _BASE.items() if key != "ploidy"}

    with pytest.raises(ValueError, match="ploidy"):
        SweepSpec(base=base, axes=(expand_axis("N", [10]),))


def test_spaced_seeds_never_share_a_replicate_seed() -> None:
    plan = enumerate_points(_spec(expand_axis("d", [4, 6, 8, 10])))

    used = [
        seed
        for point in plan.points
        for seed in range(point.params["seed"], point.params["seed"] + 3)
    ]

    assert len(used) == len(set(used))
    assert [p.params["seed"] for p in plan.points] == [1000, 1003, 1006, 1009]


def test_the_same_seed_policy_gives_every_point_the_base_seed() -> None:
    spec = SweepSpec(base=_BASE, axes=(expand_axis("d", [4, 6]),), seed_policy="same")

    assert {p.params["seed"] for p in enumerate_points(spec).points} == {1000}


def test_an_invalid_point_is_reported_with_its_reason_and_position() -> None:
    base = {
        **_BASE,
        "d": 12,
        "m": {"topology": "torus", "rate": 0.01, "rows": 3, "columns": 4},
    }
    spec = SweepSpec(base=base, axes=(expand_axis("d", [12, 10]),))

    plan = enumerate_points(spec)

    assert [p.coordinates["d"] for p in plan.points] == [12]
    assert [(p.index, p.coordinates["d"]) for p in plan.invalid] == [(1, 10)]
    assert "rows" in plan.invalid[0].reason or "columns" in plan.invalid[0].reason
    assert plan.grid_size == 2


def test_points_resolving_to_one_configuration_collapse() -> None:
    spec = SweepSpec(
        base=_BASE,
        axes=(SweepAxis("deme_weighting", ("equal", "equal")),),
        seed_policy="same",
    )

    plan = enumerate_points(spec)

    assert len(plan.points) == 1
    assert plan.collapsed == 1


def test_a_topology_axis_keeps_the_migration_rate() -> None:
    plan = enumerate_points(
        _spec(expand_axis("topology", ["island", "ring", "linear"]))
    )

    rates = []
    for point in plan.points:
        migration = point.params["m"]
        rates.append(migration["rate"] if isinstance(migration, dict) else migration)
    assert rates == [0.01, 0.01, 0.01]
    assert point.params["m"] == {"topology": "linear", "rate": 0.01}


def test_an_m_axis_reaches_into_a_topology_mapping() -> None:
    base = {**_BASE, "m": {"topology": "ring", "rate": 0.5}}
    plan = enumerate_points(
        SweepSpec(base=base, axes=(expand_axis("m", [0.01, 0.02]),))
    )

    assert [p.params["m"] for p in plan.points] == [
        {"topology": "ring", "rate": 0.01},
        {"topology": "ring", "rate": 0.02},
    ]


def test_mu_cannot_be_swept_over_mu_b() -> None:
    base = {key: value for key, value in _BASE.items() if key != "mu"} | {"mu_b": 1e-5}

    with pytest.raises(ValueError, match="mu_b"):
        SweepSpec(base=base, axes=(expand_axis("mu", [0.001]),))


def test_an_axis_key_may_appear_only_once_and_a_sweep_needs_an_axis() -> None:
    with pytest.raises(ValueError, match="only once"):
        _spec(expand_axis("d", [4]), expand_axis("d", [6]))
    with pytest.raises(ValueError, match="at least one axis"):
        SweepSpec(base=_BASE, axes=())


def test_the_stored_form_round_trips() -> None:
    spec = _spec(expand_axis("m", [0.01, 0.1]), expand_axis("d", [4, 6]))

    rebuilt = SweepSpec.from_dict(spec.to_dict())

    assert rebuilt == spec
    assert [p.run_id for p in enumerate_points(rebuilt).points] == [
        p.run_id for p in enumerate_points(spec).points
    ]


def test_an_unknown_spec_version_is_refused() -> None:
    stored = {**_spec(expand_axis("d", [4])).to_dict(), "spec_version": 99}

    with pytest.raises(ValueError, match="spec_version"):
        SweepSpec.from_dict(stored)


def test_a_sweep_file_splits_into_a_base_and_a_spec() -> None:
    config = {
        **_BASE,
        "sweep": {
            "name": "Migration and demes",
            "axes": {
                "m": {"start": 0.001, "stop": 0.1, "count": 3},
                "d": [4, 8],
            },
            "seed_policy": "same",
        },
    }

    spec, name = spec_from_config(config)

    assert name == "Migration and demes"
    assert "sweep" not in spec.base
    assert [axis.key for axis in spec.axes] == ["m", "d"]
    assert spec.axes[0].values == (0.001, 0.01, 0.1)
    assert spec.seed_policy == "same"


@pytest.mark.parametrize(
    ("block", "message"),
    [
        (None, "needs a 'sweep:' block"),
        ({"axes": {}}, "mapping"),
        ({"axes": {"d": [4]}, "colour": 1}, "unknown sweep key"),
        ({"axes": {"d": [4]}, "seed_policy": "random"}, "seed_policy"),
        ({"axes": {"d": [4]}, "name": 3}, "name"),
    ],
)
def test_a_malformed_sweep_block_is_named(block: Any, message: str) -> None:
    config = dict(_BASE) if block is None else {**_BASE, "sweep": block}

    with pytest.raises(ValueError, match=message):
        spec_from_config(config)


def test_the_work_estimate_is_points_times_replicates_times_generations() -> None:
    spec = _spec(expand_axis("d", [4, 6]))

    estimate = work_estimate(spec, enumerate_points(spec))

    assert estimate == {
        "points": 2,
        "replicates": 3,
        "max_generations": 50,
        "replicate_generations": 300,
    }


def test_a_large_plan_asks_for_confirmation() -> None:
    small = enumerate_points(_spec(expand_axis("d", [4, 6])))
    large = enumerate_points(
        _spec(
            expand_axis(
                "m", {"start": 0.001, "stop": 0.1, "count": SIZE_CONFIRMATION_THRESHOLD}
            )
        )
    )

    assert small.needs_confirmation is False
    assert large.needs_confirmation is True
