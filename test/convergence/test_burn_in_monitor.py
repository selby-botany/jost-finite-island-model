"""Tests of `BurnInMonitor`, the burn-in-then-average rule of a single run.

Every test is a pure function of its commit: fixed inputs, fixed seeds, and
exact expected generations worked out from the schedule (burn-in, first check,
doubling) or computed in the test with the Geyer estimator on the same data.
"""

from __future__ import annotations

import math
import random
from statistics import NormalDist

import pytest

from fim.convergence.monitor import BurnInMonitor, StopReason
from fim.convergence.window_statistics import geyer_window_statistics


def _monitor(**changes: object) -> BurnInMonitor:
    """A monitor with small, exact settings: burn-in 10, first check 20."""
    settings: dict[str, object] = {
        "max_generations": 10_000,
        "precision": 0.01,
        "burn_in": 10,
        "first_check": 20,
        "minimum_effective_sample_size": 10.0,
    }
    settings.update(changes)
    return BurnInMonitor(**settings)  # type: ignore[arg-type]


def _feed(monitor: BurnInMonitor, series: list[float]) -> int | None:
    """Record `series` from generation 0 until the monitor stops.

    Returns:
        The generation it stopped at, or `None` if the series ran out first.
    """
    for generation, value in enumerate(series):
        if monitor.record(generation, value).stopped:
            return generation
    return None


def _noise(length: int, *, phi: float, seed: int, level: float = 0.5) -> list[float]:
    """A seeded AR(1) series around `level` with unit-variance noise scaled small."""
    rng = random.Random(seed)
    scale = 0.05
    sigma = scale * math.sqrt(1.0 - phi * phi)
    value = level + rng.gauss(0.0, scale)
    out = []
    for _ in range(length):
        out.append(value)
        value = level + phi * (value - level) + rng.gauss(0.0, sigma)
    return out


def test_it_never_stops_inside_the_burn_in_even_for_a_constant_series() -> None:
    """A flat series is exactly known, yet nothing can stop before the first check."""
    monitor = _monitor()
    for generation in range(30):
        outcome = monitor.record(generation, 0.4)
        # The first check is at burn_in + first_check = 30.
        assert not outcome.stopped, generation
    assert monitor.record(30, 0.4).stopped


def test_a_constant_series_stops_at_the_first_check_when_the_floor_holds() -> None:
    """Window `[10, 30]` has 21 values, so ESS is 21, above the floor of 10."""
    monitor = _monitor()
    assert _feed(monitor, [0.4] * 100) == 30
    outcome = monitor.outcome()
    assert outcome.converged
    assert outcome.reason is StopReason.STATISTIC_CONVERGED
    assert monitor.window_start_generation == 10


def test_generation_zero_is_never_in_an_evidence_window() -> None:
    """A start-state outlier at generation 0 does not touch the window mean."""
    monitor = _monitor()
    _feed(monitor, [99.0] + [0.4] * 100)
    stats = monitor.evidence_statistics("value")
    assert stats is not None
    assert stats.mean == 0.4
    assert stats.window == 31 - 10


def test_a_floor_above_the_first_window_waits_for_the_doubling_check() -> None:
    """Floor 30: 21 values at generation 30 fail; the next check is at 10 + 2 * 21.

    The schedule after a check at `t` with start `s` is
    `next = s + ceil(growth * (t + 1 - s))`: here `10 + 42 = 52`, whose window
    `[10, 52]` has 43 values, at least the floor of 30.
    """
    monitor = _monitor(minimum_effective_sample_size=30.0)
    assert _feed(monitor, [0.4] * 200) == 52


def test_a_low_standard_error_with_too_few_effective_values_does_not_stop() -> None:
    """A strongly autocorrelated series with a small SE still waits for its ESS."""
    series = _noise(60_000, phi=0.99, seed=2026)
    monitor = _monitor(
        precision=100.0, minimum_effective_sample_size=50.0, max_generations=100_000
    )
    # With a huge precision the standard error is never the obstacle, so the
    # stop generation is set by the first check whose ESS reaches 50.
    stop = _feed(monitor, series)
    assert stop is not None
    # Recompute independently: walk the same schedule and take the first
    # check whose Geyer ESS meets the floor.
    start, check = 10, 30
    expected = None
    while check < len(series):
        window = series[start : check + 1]
        if geyer_window_statistics(window).effective_sample_size >= 50.0:
            expected = check
            break
        check = start + math.ceil(2.0 * (check + 1 - start))
    assert stop == expected
    assert stop > 30


def test_the_precision_target_is_precision_over_the_normal_quantile() -> None:
    """`SE <= precision / z`: 0.01 / 1.96 at 95%, wider at 99%."""
    z95 = NormalDist().inv_cdf(0.975)
    z99 = NormalDist().inv_cdf(0.995)
    assert _monitor().target_standard_error == pytest.approx(0.01 / z95)
    assert _monitor(confidence=0.99).target_standard_error == pytest.approx(0.01 / z99)
    assert _monitor(confidence=0.90).target_standard_error == pytest.approx(
        0.01 / NormalDist().inv_cdf(0.95)
    )


def test_a_noisy_series_stops_at_the_first_check_meeting_both_conditions() -> None:
    """The stop is the first scheduled check with SE and ESS both satisfied."""
    series = _noise(20_000, phi=0.8, seed=11)
    monitor = _monitor(precision=0.01, minimum_effective_sample_size=50.0)
    stop = _feed(monitor, series)
    assert stop is not None
    target = 0.01 / NormalDist().inv_cdf(0.975)
    start, check, expected = 10, 30, None
    while check < len(series):
        stats = geyer_window_statistics(series[start : check + 1])
        if stats.standard_error <= target and stats.effective_sample_size >= 50.0:
            expected = check
            break
        check = start + math.ceil(2.0 * (check + 1 - start))
    assert stop == expected
    final = monitor.evidence_statistics("value")
    assert final is not None
    assert final.standard_error <= target


def test_a_run_that_never_reaches_the_precision_is_capped_honestly() -> None:
    """At the cap the run is reported as capped, with the window it averaged."""
    series = _noise(400, phi=0.9, seed=5)
    monitor = _monitor(max_generations=300, precision=1e-6)
    stop = _feed(monitor, series)
    assert stop == 300
    outcome = monitor.outcome()
    assert not outcome.converged
    assert outcome.reason is StopReason.MAX_GENERATIONS
    assert monitor.stable_statistics() == ()
    stats = monitor.evidence_statistics("value")
    assert stats is not None
    assert stats.window == 300 + 1 - 10


def test_a_cap_inside_the_burn_in_has_no_evidence_window() -> None:
    """A run capped before its burn-in ends reports no window at all."""
    monitor = _monitor(burn_in=50, max_generations=20)
    assert _feed(monitor, [0.4] * 100) == 20
    assert not monitor.outcome().converged
    assert monitor.evidence_statistics("value") is None


def test_every_watched_statistic_is_judged_at_every_check() -> None:
    """The stop does not depend on the order the statistics are listed in."""
    rng = random.Random(3)
    rows = [
        {"D": 0.5 + 0.2 * rng.gauss(0, 1), "G": 0.3 + 0.001 * rng.gauss(0, 1)}
        for _ in range(3000)
    ]

    def stop(order: tuple[str, ...]) -> int | None:
        monitor = _monitor(statistics=order, precision=0.05)
        for generation, row in enumerate(rows):
            if monitor.record(generation, row).stopped:
                return generation
        return None

    forward, backward = stop(("D", "G")), stop(("G", "D"))
    assert forward is not None
    assert forward == backward


def test_all_waits_for_the_noisier_statistic_and_any_does_not() -> None:
    """`all` needs both statistics to pass; `any` stops when one does."""
    rng = random.Random(4)
    rows = [{"flat": 0.5, "noisy": 0.5 + 0.2 * rng.gauss(0, 1)} for _ in range(5000)]

    def stop(combinator: str) -> int | None:
        monitor = _monitor(
            statistics=("flat", "noisy"),
            combinator=combinator,
            precision=0.05,
        )
        for generation, row in enumerate(rows):
            if monitor.record(generation, row).stopped:
                return generation
        return None

    any_stop, all_stop = stop("any"), stop("all")
    assert any_stop == 30
    assert all_stop is not None
    assert all_stop > any_stop


def test_stable_statistics_names_those_that_passed_at_the_stopping_check() -> None:
    """Under `any` only the passing statistics are named; under `all` every one."""
    rng = random.Random(4)
    rows = [{"flat": 0.5, "noisy": 0.5 + 0.2 * rng.gauss(0, 1)} for _ in range(5000)]
    monitor = _monitor(statistics=("flat", "noisy"), combinator="any", precision=0.05)
    for generation, row in enumerate(rows):
        if monitor.record(generation, row).stopped:
            break
    assert monitor.stable_statistics() == ("flat",)


def test_the_fractional_burn_in_discards_the_first_tenth_at_each_check() -> None:
    """With no burn-in the window starts at `floor(0.1 t)` at the stopping check."""
    monitor = _monitor(burn_in=None, first_check=50)
    assert _feed(monitor, [0.4] * 500) == 50
    assert monitor.window_start_generation == 5
    stats = monitor.evidence_statistics("value")
    assert stats is not None
    assert stats.window == 50 + 1 - 5


def test_extra_statistics_are_recorded_but_never_decide() -> None:
    """A display-only statistic that never settles does not hold the run."""
    rng = random.Random(8)
    monitor = _monitor(statistics=("D",), extra_statistics=("H_S",))
    for generation in range(100):
        outcome = monitor.record(generation, {"D": 0.5, "H_S": rng.random()})
        if outcome.stopped:
            break
    assert monitor.outcome().converged
    assert monitor.outcome().generation == 30
    assert len(monitor.histories["H_S"]) == 31
    assert monitor.evidence_statistics("H_S") is not None


def test_a_statistic_undefined_in_some_generations_keeps_its_own_window() -> None:
    """Gaps shorten a statistic's history, and its window follows generations."""
    monitor = _monitor(statistics=("D",), extra_statistics=("G",))
    for generation in range(31):
        values = {"D": 0.5}
        if generation % 2 == 0:
            values["G"] = 0.3
        monitor.record(generation, values)
    generations = monitor.value_generations["G"]
    assert generations[0] == 0
    stats = monitor.evidence_statistics("G")
    assert stats is not None
    assert stats.window == len([g for g in generations if g >= 10])


def test_a_statistic_with_too_few_defined_values_is_not_judged() -> None:
    """Fewer than three defined values in the window cannot pass."""
    monitor = _monitor(statistics=("D", "G"))
    for generation in range(100):
        values = {"D": 0.5}
        if generation in (0, 60):
            values["G"] = 0.3
        if monitor.record(generation, values).stopped:
            break
    assert not monitor.outcome().converged


def test_the_first_check_is_not_before_the_first_window_value_floor() -> None:
    """`first_check` must give the estimator at least three values."""
    with pytest.raises(ValueError, match="first_check"):
        _monitor(first_check=2)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"max_generations": 0}, "max_generations"),
        ({"precision": -1.0}, "precision"),
        ({"precision": float("nan")}, "precision"),
        ({"confidence": 0.8}, "confidence"),
        ({"burn_in": 0}, "burn_in"),
        ({"growth": 1.0}, "growth"),
        ({"fractional_burn_in": 0.0}, "fractional_burn_in"),
        ({"fractional_burn_in": 1.0}, "fractional_burn_in"),
        ({"minimum_effective_sample_size": 0.0}, "minimum_effective_sample_size"),
        ({"statistics": ()}, "statistics must not be empty"),
        ({"statistics": ("D", "D")}, "must not repeat a name"),
        ({"statistics": ("D",), "extra_statistics": ("D",)}, "must not repeat a name"),
        ({"combinator": "either"}, "combinator must be"),
    ],
)
def test_the_constructor_validates_its_arguments(
    changes: dict[str, object], message: str
) -> None:
    """Each invalid argument is refused by name."""
    with pytest.raises(ValueError, match=message):
        _monitor(**changes)


@pytest.mark.parametrize(
    "generation",
    [True, False, 1.5, "1", None],
    ids=["True", "False", "1.5", "'1'", "None"],
)
def test_record_rejects_a_non_integer_generation(generation: object) -> None:
    """A non-integer generation, a `bool` included, raises `ValueError`."""
    with pytest.raises(ValueError, match="non-negative integer"):
        _monitor().record(generation, 0.5)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [True, "0.5", None], ids=["True", "'0.5'", "None"])
def test_record_rejects_a_non_numeric_value(value: object) -> None:
    """A non-numeric value raises `ValueError`, not a raw `TypeError`."""
    with pytest.raises(ValueError, match="real number"):
        _monitor().record(0, value)  # type: ignore[arg-type]


def test_record_rejects_non_finite_negative_and_unordered_input() -> None:
    """Infinity, a negative generation and a repeated generation are refused."""
    monitor = _monitor()
    with pytest.raises(ValueError, match="finite"):
        monitor.record(0, math.inf)
    with pytest.raises(ValueError, match="non-negative"):
        monitor.record(-1, 0.5)
    monitor.record(0, 0.5)
    with pytest.raises(ValueError, match="increasing order"):
        monitor.record(0, 0.5)


def test_several_statistics_need_a_mapping_and_accept_a_partial_one() -> None:
    """A bare float needs one watched statistic; a mapping may omit a statistic."""
    monitor = _monitor(statistics=("D", "G_ST"))
    with pytest.raises(ValueError, match="requires a mapping"):
        monitor.record(0, 0.5)
    monitor.record(0, {"D": 0.5})
    monitor.record(1, {})
    assert monitor.histories == {"D": (0.5,), "G_ST": ()}
    with pytest.raises(ValueError, match="unconfigured statistic"):
        monitor.record(2, {"D": 0.5, "G_ST": 0.5, "E_ST": 0.5})


def test_it_refuses_a_record_after_it_stopped() -> None:
    """A stopped monitor is finished."""
    monitor = _monitor()
    assert _feed(monitor, [0.4] * 100) == 30
    with pytest.raises(RuntimeError, match="stopped"):
        monitor.record(31, 0.4)
    assert monitor.should_stop()
    assert monitor.reason() is StopReason.STATISTIC_CONVERGED


def test_the_recorded_series_are_exposed_in_order() -> None:
    """`generations`, `history` and `histories` mirror what was recorded."""
    monitor = _monitor()
    for generation in range(5):
        monitor.record(generation, generation / 10)
    assert monitor.generations == (0, 1, 2, 3, 4)
    assert monitor.history == (0.0, 0.1, 0.2, 0.3, 0.4)
    assert monitor.histories == {"value": monitor.history}


def test_the_window_end_burn_in_and_geweke_z_are_exposed_for_the_report() -> None:
    """A step series gives the exact `z`; a short window has none."""
    monitor = _monitor(precision=0.0, max_generations=399)
    for generation in range(400):
        step = 0.0 if generation < 100 else 1.0
        monitor.record(generation, step + (generation % 3) * 0.001)
        if monitor.should_stop():
            break

    assert monitor.window_end_generation == 399
    assert monitor.burn_in_generation == 10
    z = monitor.evidence_geweke_z("value")
    assert z is not None and z < -3.0
    short = _monitor()
    for generation in range(15):
        short.record(generation, 0.5)
    assert short.evidence_geweke_z("value") is None


def test_a_fractional_burn_in_reports_the_window_start_as_its_burn_in() -> None:
    """With no relaxation time the burn-in is wherever the window started."""
    monitor = _monitor(burn_in=None, precision=0.0, max_generations=200)
    for generation in range(201):
        monitor.record(generation, 0.5 + (generation % 5) * 0.01)
        if monitor.should_stop():
            break

    assert monitor.window_start_generation == 20
    assert monitor.burn_in_generation == 20


def test_a_window_mode_monitor_stops_exactly_when_its_window_is_averaged() -> None:
    """Burn-in 10 and a window of 25: stop at generation 35, with no checks."""
    monitor = _monitor(averaging_window=25, precision=0.0)

    stopped = _feed(monitor, [0.5] * 100)

    assert stopped == 35
    outcome = monitor.outcome()
    assert outcome.converged is True
    assert outcome.reason is StopReason.AVERAGING_COMPLETE
    assert monitor.stable_statistics() == ("value",)


def test_a_window_mode_monitor_waits_for_a_window_it_does_not_have_yet() -> None:
    """Awaiting a window, the monitor never stops; the window then ends it."""
    monitor = _monitor(awaiting_window=True)
    initially_awaiting = monitor.awaiting_window
    assert initially_awaiting is True
    for generation in range(60):
        monitor.record(generation, 0.5)
    assert not monitor.should_stop()

    monitor.set_averaging_window(70)
    assert monitor.awaiting_window is False
    assert not monitor.should_stop()
    for generation in range(60, 90):
        monitor.record(generation, 0.5)
        if monitor.should_stop():
            break

    assert monitor.outcome().generation == 80


def test_a_waiting_monitor_stops_at_once_when_the_window_ends_where_it_paused() -> None:
    """A replicate paused at burn-in + 40 and given a window of 40 is done."""
    monitor = _monitor(awaiting_window=True)
    for generation in range(51):
        monitor.record(generation, 0.5)
    assert not monitor.should_stop()

    monitor.set_averaging_window(40)

    assert monitor.should_stop()
    assert monitor.outcome().generation == 50
    assert monitor.outcome().reason is StopReason.AVERAGING_COMPLETE


def test_a_window_mode_monitor_that_reaches_the_cap_first_is_capped() -> None:
    """A window longer than the room under the cap ends as a capped run."""
    monitor = _monitor(averaging_window=500, max_generations=100, precision=0.0)

    stopped = _feed(monitor, [0.5] * 200)

    assert stopped == 100
    assert monitor.outcome().reason is StopReason.MAX_GENERATIONS
    assert monitor.outcome().converged is False


def test_window_mode_needs_a_burn_in_and_a_positive_window() -> None:
    """The fractional burn-in has no fixed start to average from."""
    with pytest.raises(ValueError, match="needs a burn-in"):
        _monitor(burn_in=None, averaging_window=10)
    with pytest.raises(ValueError, match="at least 1"):
        _monitor(averaging_window=0)
    with pytest.raises(RuntimeError, match="window-mode"):
        _monitor().set_averaging_window(10)


def _noisy_monitor(**changes: object) -> tuple[BurnInMonitor, list[float]]:
    """A monitor on a seeded AR(1) series that is too noisy to stop quickly."""
    series = _noise(3000, phi=0.5, seed=4)
    monitor = _monitor(precision=0.001, minimum_effective_sample_size=10.0, **changes)
    return monitor, series


def test_the_projection_is_the_window_start_plus_the_scaled_window() -> None:
    """`start + L * (SE / target)**2` from the window as it stands (design 6.10)."""
    monitor, series = _noisy_monitor()
    for generation, value in enumerate(series[:200]):
        monitor.record(generation, value)

    # Generations 0 to 199 are recorded and the burn-in is 10, so the window
    # holds series[10:200]: 190 values.
    stats = geyer_window_statistics(series[10:200])
    target = monitor.target_standard_error
    scaled = 190 * (stats.standard_error / target) ** 2
    expected = 10 + math.ceil(max(scaled, 10.0 * stats.tau_int))
    assert monitor.projected_generations_for("value") == expected
    assert expected > 200
    # The projection kept from the latest check is for a shorter window, so
    # it is a different (earlier-data) number, but of the same order.
    assert monitor.projected_generations is not None
    assert monitor.projected_generations > 200


def test_a_met_precision_projects_the_window_end_and_zero_precision_none() -> None:
    """Nothing is projected for a statistic that already meets the target."""
    monitor = _monitor(precision=0.5)
    for generation in range(25):
        monitor.record(generation, 0.5 + (generation % 2) * 0.001)
    assert monitor.projected_generations_for("value") == 24

    exact = _monitor(precision=0.0)
    for generation in range(25):
        exact.record(generation, 0.5 + (generation % 3) * 0.01)
    assert exact.projected_generations_for("value") is None
    assert exact.projected_generations is None


def test_a_projection_beyond_the_cap_is_logged_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The cap warning appears once, naming the projected generations."""
    monitor, series = _noisy_monitor(max_generations=500)
    with caplog.at_level("WARNING", logger="fim.convergence.monitor"):
        for generation, value in enumerate(series[:400]):
            monitor.record(generation, value)

    warnings = [r for r in caplog.records if "reach its cap" in r.getMessage()]
    assert len(warnings) == 1
    assert str(monitor.projected_generations) in warnings[0].getMessage() or (
        "generations needed" in warnings[0].getMessage()
    )
    assert monitor.projected_generations is not None
    assert monitor.projected_generations > 500


def test_no_cap_warning_when_the_projection_fits_under_the_cap(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A run that will finish in time says nothing."""
    monitor = _monitor(precision=0.02, max_generations=100_000)
    with caplog.at_level("WARNING", logger="fim.convergence.monitor"):
        for generation, value in enumerate(_noise(2000, phi=0.3, seed=2)):
            if monitor.record(generation, value).stopped:
                break
    assert not [r for r in caplog.records if "reach its cap" in r.getMessage()]


def test_any_projects_the_soonest_statistic_and_all_the_latest() -> None:
    """Under `any` one statistic is enough; under `all` the slowest decides."""
    quiet = _noise(400, phi=0.2, seed=8)
    noisy = _noise(400, phi=0.6, seed=9)
    projections = {}
    for combinator in ("all", "any"):
        monitor = BurnInMonitor(
            max_generations=100_000,
            precision=0.002,
            burn_in=10,
            first_check=20,
            statistics=("quiet", "noisy"),
            combinator=combinator,
            minimum_effective_sample_size=10.0,
        )
        for generation in range(300):
            monitor.record(
                generation,
                {
                    "quiet": quiet[generation] * 0.1 + 0.5,
                    "noisy": noisy[generation] + 0.5,
                },
            )
            if monitor.should_stop():
                break
        projections[combinator] = (
            monitor.projected_generations_for("quiet"),
            monitor.projected_generations_for("noisy"),
            monitor.projected_generations,
        )
    quiet_all, noisy_all, overall_all = projections["all"]
    overall_any = projections["any"][2]
    assert quiet_all is not None and noisy_all is not None
    assert noisy_all > quiet_all
    assert overall_all is not None and overall_any is not None
    assert overall_any <= overall_all


def test_a_statistic_precision_override_replaces_the_run_precision() -> None:
    """A statistic given its own plus-or-minus is judged against that, alone."""
    series = _noise(600, phi=0.4, seed=21)
    loose = BurnInMonitor(
        max_generations=100_000,
        precision=0.0005,
        burn_in=10,
        first_check=20,
        statistics=("value",),
        minimum_effective_sample_size=10.0,
        statistic_precision={"value": 0.5},
    )
    strict = _monitor(precision=0.0005)

    assert _feed(loose, series) == 30
    assert _feed(strict, series) is None
    assert loose.target_standard_error_for("value", 0.5) == pytest.approx(
        0.5 / NormalDist().inv_cdf(0.975)
    )


def test_a_relative_statistic_targets_precision_times_max_of_one_and_its_mean() -> None:
    """`precision * max(1, |mean|)`: absolute below one, relative above."""
    monitor = BurnInMonitor(
        max_generations=1000,
        precision=0.02,
        burn_in=1,
        first_check=5,
        statistics=("count",),
        relative_statistics=("count",),
    )
    z = NormalDist().inv_cdf(0.975)

    assert monitor.target_standard_error_for("count", 0.3) == pytest.approx(0.02 / z)
    assert monitor.target_standard_error_for("count", 40.0) == pytest.approx(
        0.02 * 40.0 / z
    )
    assert monitor.target_standard_error_for("count", -40.0) == pytest.approx(
        0.02 * 40.0 / z
    )


def test_a_relative_statistic_stops_where_an_absolute_one_would_not() -> None:
    """A noisy count of about 40 passes at 2% relative but not at 0.02 absolute."""
    rng = random.Random(5)
    series = [40.0 + rng.gauss(0.0, 0.5) for _ in range(2000)]
    common = {
        "max_generations": 100_000,
        "precision": 0.02,
        "burn_in": 10,
        "first_check": 20,
        "statistics": ("count",),
        "minimum_effective_sample_size": 10.0,
    }
    relative = BurnInMonitor(relative_statistics=("count",), **common)  # type: ignore[arg-type]
    absolute = BurnInMonitor(**common)  # type: ignore[arg-type]

    relative_stop = None
    for generation, value in enumerate(series):
        relative.record(generation, value)
        if relative.should_stop():
            relative_stop = generation
            break
    absolute_stop = None
    for generation, value in enumerate(series):
        absolute.record(generation, value)
        if absolute.should_stop():
            absolute_stop = generation
            break

    assert relative_stop is not None
    assert absolute_stop is None or absolute_stop > relative_stop


def test_an_override_for_an_unwatched_statistic_is_refused() -> None:
    """The override must name a watched statistic and be non-negative."""
    with pytest.raises(ValueError, match="unwatched"):
        _monitor(statistic_precision={"other": 0.1})
    with pytest.raises(ValueError, match="non-negative"):
        _monitor(statistic_precision={"value": -0.1})
