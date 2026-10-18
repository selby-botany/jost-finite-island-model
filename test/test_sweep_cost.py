"""Tests for `fim.sweep_cost`: the static cost bands of a sweep.

Every expected number is derived here from the resolved configuration of the
point itself (its burn-in, cap, and replicate settings), so the tests check
the plumbing and the arithmetic, never an engine's speed.
"""

from __future__ import annotations

from typing import Any

import pytest

from fim.model.params import SimulationParams
from fim.sweep import SweepPlan, SweepSpec, enumerate_points, expand_axis
from fim.sweep_cost import Band, point_cost, static_cost
from fim.sweep_run import resolve_concurrency

_BASE: dict[str, Any] = {
    "N": 40,
    "ploidy": "diploid",
    "d": 4,
    "m": 0.01,
    "mu": 0.001,
    "seed": 1000,
    "n_replicates": 3,
    "max_generations": 50,
}


def _spec(*axes: Any, **overrides: Any) -> SweepSpec:
    """A spec over `_BASE` with the given axes and base overrides."""
    base = {**_BASE, **overrides}
    base = {key: value for key, value in base.items() if value is not None}
    return SweepSpec(base=base, axes=axes)


def _params(point: Any) -> SimulationParams:
    """The resolved configuration of a planned point."""
    return SimulationParams.from_mapping(point.params)


def test_a_band_holds_its_ends_in_order_and_does_arithmetic_end_to_end() -> None:
    band = Band(2.0, 5.0)

    assert (band + Band(1.0, 1.5)) == Band(3.0, 6.5)
    assert (band * Band(3.0, 4.0)) == Band(6.0, 20.0)
    assert band.scaled(2.0) == Band(4.0, 10.0)
    assert band.to_dict() == {"low": 2.0, "high": 5.0, "calibrated": None}
    with pytest.raises(ValueError, match="above its high"):
        Band(5.0, 2.0)


def test_the_generation_band_is_the_derived_burn_in_up_to_the_cap() -> None:
    spec = _spec(expand_axis("d", [4, 6]), max_generations=None)
    plan = enumerate_points(spec)

    cost = static_cost(spec, plan)

    assert len(cost.points) == 2
    for point, found in zip(plan.points, cost.points, strict=True):
        params = _params(point)
        assert found.run_id == point.run_id
        assert found.generations == Band(
            params.convergence_burn_in, params.max_generations
        )
        assert found.generations.low < found.generations.high


def test_a_cap_below_the_burn_in_makes_the_cap_the_whole_band() -> None:
    spec = _spec(expand_axis("d", [4]), max_generations=50)
    (plan_point,) = enumerate_points(spec).points
    params = _params(plan_point)
    assert params.convergence_burn_in > 50  # the fixture's derived burn-in

    found = point_cost(plan_point.run_id, params)

    assert found.generations == Band(50, 50)


def test_the_cap_is_derived_for_each_point_from_its_own_migration() -> None:
    spec = _spec(expand_axis("m", [0.01, 0.00001]), max_generations=None, mu=1e-6)

    cost = static_cost(spec, enumerate_points(spec))

    fast, slow = cost.points
    assert fast.generations.high < slow.generations.high
    assert cost.generation_band() == Band(
        min(fast.generations.low, slow.generations.low), slow.generations.high
    )


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"n_replicates": 200}, Band(10, 200)),  # adaptive: the batch minimum up
        ({"n_replicates": 200, "replicate_minimum": 30}, Band(30, 200)),
        ({"n_replicates": 5, "replicate_minimum": 30}, Band(5, 5)),  # clamped down
        ({"n_replicates": 200, "stop_batch_early": False}, Band(200, 200)),
        ({"n_replicates": 1}, Band(1, 1)),  # a single run
    ],
)
def test_the_replicate_band_follows_the_batch_settings(
    overrides: dict[str, Any], expected: Band
) -> None:
    spec = _spec(expand_axis("d", [4]), **overrides)
    (plan_point,) = enumerate_points(spec).points

    found = point_cost(plan_point.run_id, _params(plan_point))

    assert found.replicates == expected


def test_a_points_work_is_its_replicates_times_its_generations() -> None:
    spec = _spec(expand_axis("d", [4]), n_replicates=20, max_generations=None)
    (plan_point,) = enumerate_points(spec).points
    params = _params(plan_point)

    found = point_cost(plan_point.run_id, params)

    assert found.work == Band(
        10 * params.convergence_burn_in, 20 * params.max_generations
    )


def test_sweep_work_adds_up_over_points_and_scales_with_replicates() -> None:
    few = static_cost(*_spec_and_plan(expand_axis("d", [4, 6]), n_replicates=20))
    more_points = static_cost(
        *_spec_and_plan(expand_axis("d", [4, 6, 8, 10]), n_replicates=20)
    )
    double = static_cost(*_spec_and_plan(expand_axis("d", [4, 6]), n_replicates=40))

    assert few.work == few.points[0].work + few.points[1].work
    assert more_points.work.high > few.work.high
    assert double.work.high == pytest.approx(2 * few.work.high)


def _spec_and_plan(*axes: Any, **overrides: Any) -> tuple[SweepSpec, Any]:
    """A spec and its plan, for `static_cost(*...)`."""
    spec = _spec(*axes, **overrides)
    return spec, enumerate_points(spec)


def test_an_empty_plan_costs_nothing() -> None:
    spec = _spec(expand_axis("d", [4]))
    plan = SweepPlan((), (), 0, 1)

    cost = static_cost(spec, plan)

    assert plan.points == ()
    assert (cost.work, cost.generation_band(), cost.replicate_band()) == (
        Band(0, 0),
        Band(0, 0),
        Band(0, 0),
    )


def test_the_points_at_once_come_from_the_concurrency_rule() -> None:
    spec, plan = _spec_and_plan(expand_axis("d", [4, 6, 8, 10]))
    chosen = resolve_concurrency(
        points=len(plan.points),
        params=_params(plan.points[0]),
        cores=2,
    )

    cost = static_cost(spec, plan, points_at_once=chosen.points_at_once)

    assert cost.points_at_once == chosen.points_at_once
    assert static_cost(spec, plan).points_at_once is None


def test_the_cost_serializes_for_the_page() -> None:
    spec, plan = _spec_and_plan(expand_axis("d", [4, 6]))

    payload = static_cost(spec, plan, points_at_once=2).to_dict()

    assert payload["points"] == 2
    assert payload["pointsAtOnce"] == 2
    assert set(payload["work"]) == {"low", "high", "calibrated"}  # type: ignore[call-overload]
    assert payload["replicates"] == {"low": 3.0, "high": 3.0, "calibrated": None}
