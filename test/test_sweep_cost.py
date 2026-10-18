"""Tests for `fim.sweep_cost`: the static cost bands of a sweep.

Every expected number is derived here from the resolved configuration of the
point itself (its burn-in, cap, and replicate settings), so the tests check
the plumbing and the arithmetic, never an engine's speed.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import pytest

from fim.model.params import SimulationParams
from fim.sweep import SweepPlan, SweepSpec, enumerate_points, expand_axis
from fim.sweep_cost import (
    Band,
    EngineProbe,
    ShapeClass,
    Throughput,
    ThroughputCache,
    band_text,
    format_bytes,
    format_duration,
    nearest_class,
    point_cost,
    probed_cost,
    static_cost,
)
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


# --- time and disk from a probe -------------------------------------------

FIGURES = Throughput(
    seconds_per_replicate_generation=0.5,
    trace_bytes_per_generation=100.0,
    statistic_bytes_per_generation=10.0,
    pairwise_bytes_per_pair=7.0,
)


class _FakeProbe:
    """A probe that answers from a table and records what it was asked."""

    def __init__(self, answers: dict[int, Throughput] | None = None) -> None:
        self.answers = answers or {}
        self.asked: list[tuple[int, int]] = []

    def measure(self, params: SimulationParams, generations: int) -> Throughput:
        self.asked.append((params.d, generations))
        return self.answers.get(params.d, FIGURES)


def test_seconds_and_bytes_follow_exactly_from_the_probe_figures() -> None:
    spec, plan = _spec_and_plan(expand_axis("d", [4]), n_replicates=20)
    (point,) = plan.points
    static = static_cost(spec, plan).points[0]
    pairs = 4 * 3 // 2

    cost = probed_cost(spec, plan, _FakeProbe())

    (found,) = cost.points
    assert found.seconds == static.work.scaled(0.5)
    assert found.trace_bytes == static.work.scaled(100.0)
    assert found.statistic_bytes == static.work.scaled(10.0) + static.replicates.scaled(
        pairs * 7.0
    )
    assert found.probe == "measured"
    assert found.run_id == point.run_id
    assert cost.disk_bytes == found.trace_bytes + found.statistic_bytes
    assert cost.statistics_bytes == found.statistic_bytes
    assert cost.classes_measured == 1


def test_a_lineal_batchs_workers_share_a_points_time() -> None:
    spec, plan = _spec_and_plan(expand_axis("d", [4]), n_replicates=20)
    alone = probed_cost(spec, plan, _FakeProbe()).points[0]

    shared = probed_cost(spec, plan, _FakeProbe(), workers_per_point=4).points[0]

    assert alone.seconds is not None and shared.seconds is not None
    assert shared.seconds == alone.seconds.scaled(0.25)
    assert shared.trace_bytes == alone.trace_bytes  # disk does not shrink


def test_deme_pair_statistics_are_left_out_above_the_pairwise_limit() -> None:
    spec, plan = _spec_and_plan(expand_axis("d", [4]), n_replicates=20)
    kept = probed_cost(spec, plan, _FakeProbe()).points[0].statistic_bytes
    left_out = (
        probed_cost(spec, plan, _FakeProbe(), pairwise_max_demes=3)
        .points[0]
        .statistic_bytes
    )

    assert kept is not None and left_out is not None
    assert left_out.high < kept.high
    assert left_out == static_cost(spec, plan).points[0].work.scaled(10.0)


def test_wall_time_is_bracketed_by_the_longest_point_and_grahams_bound() -> None:
    spec, plan = _spec_and_plan(expand_axis("d", [4, 6, 8, 10]), n_replicates=20)
    cost = probed_cost(spec, plan, _FakeProbe(), points_at_once=2)

    seconds = [p.seconds for p in cost.points]
    assert all(band is not None for band in seconds)
    total_low = sum(b.low for b in seconds if b)
    total_high = sum(b.high for b in seconds if b)
    longest_low = max(b.low for b in seconds if b)
    longest_high = max(b.high for b in seconds if b)
    assert cost.wall_seconds is not None
    assert cost.wall_seconds.low == pytest.approx(max(total_low / 2, longest_low))
    assert cost.wall_seconds.high == pytest.approx(total_high / 2 + 0.5 * longest_high)
    one_at_a_time = probed_cost(spec, plan, _FakeProbe())
    assert one_at_a_time.wall_seconds is not None
    assert one_at_a_time.wall_seconds.high == pytest.approx(total_high)


def test_only_the_biggest_classes_are_measured_and_the_rest_take_the_nearest() -> None:
    spec, plan = _spec_and_plan(expand_axis("d", [4, 8, 16]), n_replicates=20)
    figures = {
        4: Throughput(1.0, 1.0, 1.0, 1.0),
        8: Throughput(2.0, 2.0, 2.0, 2.0),
        16: Throughput(3.0, 3.0, 3.0, 3.0),
    }
    probe = _FakeProbe(figures)
    static = static_cost(spec, plan)
    by_d = {
        _params(point).d: cost
        for point, cost in zip(plan.points, static.points, strict=True)
    }
    biggest = sorted(by_d, key=lambda d: -by_d[d].work.high)[:2]
    left = next(d for d in by_d if d not in biggest)

    cost = probed_cost(spec, plan, probe, max_classes=2)

    assert sorted(d for d, _ in probe.asked) == sorted(biggest)
    assert cost.classes_measured == 2
    priced = {
        _params(point).d: found
        for point, found in zip(plan.points, cost.points, strict=True)
    }
    assert {d: p.probe for d, p in priced.items()} == {
        **dict.fromkeys(biggest, "measured"),
        left: "nearest",
    }
    shapes = {
        d: ShapeClass.of(_params(point))
        for point, d in ((p, _params(p).d) for p in plan.points)
    }
    borrowed = nearest_class(shapes[left], [shapes[d] for d in biggest])
    source = next(d for d, shape in shapes.items() if shape == borrowed)
    assert priced[left].seconds == by_d[left].work.scaled(
        figures[source].seconds_per_replicate_generation
    )


def test_each_probe_burst_uses_the_requested_generations() -> None:
    spec, plan = _spec_and_plan(expand_axis("d", [4]))
    probe = _FakeProbe()

    probed_cost(spec, plan, probe, generations=123)

    assert probe.asked == [(4, 123)]


def test_the_cache_answers_a_repeat_without_measuring(tmp_path: Path) -> None:
    spec, plan = _spec_and_plan(expand_axis("d", [4, 8]), n_replicates=20)
    path = tmp_path / "throughput.json"
    first = _FakeProbe()
    probed_cost(spec, plan, first, cache=ThroughputCache(path))
    second = _FakeProbe()

    again = probed_cost(spec, plan, second, cache=ThroughputCache(path))

    assert len(first.asked) == 2
    assert second.asked == []
    assert again.classes_measured == 2
    assert path.is_file()


@pytest.mark.parametrize("content", ["not json", "[1]", '{"k": 5}', ""])
def test_a_damaged_cache_is_ignored(tmp_path: Path, content: str) -> None:
    path = tmp_path / "throughput.json"
    path.write_text(content, encoding="utf-8")
    spec, plan = _spec_and_plan(expand_axis("d", [4]))
    probe = _FakeProbe()

    probed_cost(spec, plan, probe, cache=ThroughputCache(path))

    assert len(probe.asked) == 1


def test_a_cache_in_an_unwritable_place_is_not_an_error(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("a file where a folder should be", encoding="utf-8")
    spec, plan = _spec_and_plan(expand_axis("d", [4]))

    cost = probed_cost(
        spec, plan, _FakeProbe(), cache=ThroughputCache(blocked / "t.json")
    )

    assert cost.classes_measured == 1


def test_a_cache_entry_is_keyed_by_version_machine_and_shape() -> None:
    shape = ShapeClass(4, "lineal", 1, 200, 6)

    key = ThroughputCache.key_for(shape)

    assert key.endswith(shape.key)
    assert "|" in key and key.count("|") >= 4


def test_the_nearest_class_prefers_the_engine_then_the_closer_shape() -> None:
    target = ShapeClass(8, "lineal", 1, 200, 7)
    lineal_far = ShapeClass(64, "lineal", 1, 200, 12)
    vector_close = ShapeClass(8, "generational-vector", 1, 200, 7)
    lineal_near = ShapeClass(16, "lineal", 1, 200, 8)

    assert nearest_class(target, [vector_close, lineal_far]) == lineal_far
    assert nearest_class(target, [vector_close, lineal_far, lineal_near]) == lineal_near
    assert nearest_class(target, [vector_close]) == vector_close
    with pytest.raises(ValueError, match="no measured class"):
        nearest_class(target, [])


def test_the_nearest_class_does_not_depend_on_the_order_found() -> None:
    target = ShapeClass(8, "lineal", 1, 200, 7)
    equally_far = [
        ShapeClass(4, "lineal", 1, 200, 7),
        ShapeClass(16, "lineal", 1, 200, 7),
        ShapeClass(8, "lineal", 1, 200, 6),
        ShapeClass(8, "lineal", 1, 200, 8),
    ]
    answers = set()
    for seed in range(8):
        shuffled = list(equally_far)
        random.Random(seed).shuffle(shuffled)
        answers.add(nearest_class(target, shuffled))

    assert len(answers) == 1


def test_a_shape_class_groups_by_deme_count_engine_loci_and_size() -> None:
    a = _params(enumerate_points(_spec(expand_axis("d", [4]))).points[0])
    b = _params(enumerate_points(_spec(expand_axis("d", [4]), N=41)).points[0])
    c = _params(enumerate_points(_spec(expand_axis("d", [8]))).points[0])

    assert ShapeClass.of(a) == ShapeClass.of(b)  # 80 and 82 gene copies: one bucket
    assert ShapeClass.of(a) != ShapeClass.of(c)
    assert ShapeClass.of(a).key.startswith("d4|")


def test_the_engine_probe_cancels_fixed_costs_by_timing_two_bursts(
    tmp_path: Path,
) -> None:
    # A fake clock: the short burst takes 1 s and the long one 3 s.
    ticks = iter([0.0, 1.0, 10.0, 13.0])
    params = _params(
        enumerate_points(
            _spec(expand_axis("d", [2]), N=16, n_replicates=1, max_generations=None)
        ).points[0]
    )
    probe = EngineProbe(timer=lambda: next(ticks), scratch=tmp_path)

    figures = probe.measure(params, 30)

    # Each burst runs to its cap (30 and 60 generations), so the 2 s of
    # extra time is spread over 30 extra generations.
    assert figures.seconds_per_replicate_generation == pytest.approx(2.0 / 30)
    assert figures.trace_bytes_per_generation > 0
    assert figures.statistic_bytes_per_generation >= 0
    assert list(tmp_path.iterdir()) == []  # the scratch directories are gone


def test_a_burst_shorter_than_the_burn_in_does_not_warn(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A derived burn-in is thousands of generations; a burst is 30."""
    params = _params(
        enumerate_points(
            _spec(expand_axis("d", [2]), N=16, n_replicates=1, max_generations=None)
        ).points[0]
    )
    assert params.convergence_burn_in > 60

    with caplog.at_level("WARNING"):
        EngineProbe(scratch=tmp_path).measure(params, 30)

    assert [r.getMessage() for r in caplog.records if r.levelno >= 30] == []


def test_timing_noise_that_hides_the_difference_falls_back_to_the_long_burst(
    tmp_path: Path,
) -> None:
    # The long burst "took" less than the short one: noise, so the honest
    # upper figure is the long burst's total over its generations.
    ticks = iter([0.0, 2.0, 10.0, 11.0])
    params = _params(
        enumerate_points(
            _spec(expand_axis("d", [2]), N=16, n_replicates=1, max_generations=None)
        ).points[0]
    )

    figures = EngineProbe(timer=lambda: next(ticks), scratch=tmp_path).measure(
        params, 30
    )

    assert figures.seconds_per_replicate_generation == pytest.approx(1.0 / 60)


@pytest.mark.slow
def test_the_real_probe_returns_positive_figures_and_leaves_nothing(
    tmp_path: Path,
) -> None:
    params = _params(
        enumerate_points(
            _spec(expand_axis("d", [4]), N=40, n_replicates=1, max_generations=None)
        ).points[0]
    )

    figures = EngineProbe(scratch=tmp_path).measure(params, 100)

    assert figures.seconds_per_replicate_generation > 0
    assert figures.trace_bytes_per_generation > 0
    assert figures.pairwise_bytes_per_pair > 0
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (0, "0 s"),
        (40, "40 s"),
        (59.4, "59 s"),
        (60, "1 min"),
        (719, "12 min"),
        (3600, "1 h"),
        (11400, "3 h 10 min"),
        (86400, "1 d"),
        (2 * 86400 + 4 * 3600, "2 d 4 h"),
        (-5, "0 s"),
    ],
)
def test_durations_read_the_way_a_person_says_them(seconds: float, text: str) -> None:
    assert format_duration(seconds) == text


@pytest.mark.parametrize(
    ("size", "text"),
    [
        (0, "0 B"),
        (820, "820 B"),
        (4_200_000, "4.2 MB"),
        (18_000_000_000, "18 GB"),
        (140_000_000_000, "140 GB"),
        (2_500_000_000_000, "2.5 TB"),
    ],
)
def test_sizes_are_in_decimal_units(size: float, text: str) -> None:
    assert format_bytes(size) == text


def test_a_band_reads_as_a_range_unless_both_ends_read_the_same() -> None:
    assert band_text(Band(11400, 75600), format_duration) == "3 h 10 min to 21 h"
    assert band_text(Band(10, 11), format_duration) == "10 s to 11 s"
    assert band_text(Band(3600.0, 3601.0), format_duration) == "1 h"
