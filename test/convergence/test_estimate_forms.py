"""Tests of the two expected-value forms of `D` and `G_ST` (design 6.11).

Every test is a pure function of its commit: fixed series, fixed seeds, and
expected numbers computed in the test from the same formulas.
"""

from __future__ import annotations

import math
import random
from dataclasses import replace

import numpy as np
import pytest

from fim.convergence.monitor import BurnInMonitor
from fim.convergence.window_statistics import (
    IdentityStatistic,
    degenerate_share,
    geyer_window_statistics,
    select_form,
    value_of_means_statistics,
)
from fim.engine import _identity_statistics
from fim.model.params import SimulationParams

DEMES = 4


@pytest.fixture
def identity() -> dict[str, IdentityStatistic]:
    """`D` and `G_ST` for a four-deme model, as the engine builds them."""
    params = SimulationParams.from_mapping(
        {"N": 20, "ploidy": "haploid", "d": DEMES, "m": 0.1, "mu": 0.01, "seed": 1}
    )
    return _identity_statistics(params)


def _heterozygosities(
    length: int, *, seed: int, floor: float = 0.2
) -> tuple[list[float], list[float]]:
    """Seeded, positively correlated `H_S`/`H_T` series with `H_S <= H_T`."""
    rng = random.Random(seed)
    within, total = 0.4, 0.6
    h_s, h_t = [], []
    for _ in range(length):
        within = max(floor, min(0.8, 0.4 + 0.9 * (within - 0.4) + rng.gauss(0, 0.03)))
        total = max(within, min(0.95, 0.6 + 0.9 * (total - 0.6) + rng.gauss(0, 0.03)))
        h_s.append(within)
        h_t.append(total)
    return h_s, h_t


def _monitor(identity: dict[str, IdentityStatistic], **changes: object):
    """A monitor watching `D` with `G_ST`, `H_S` and `H_T` recorded alongside."""
    settings: dict[str, object] = {
        "max_generations": 100_000,
        "precision": 0.0,
        "burn_in": 1,
        "first_check": 5,
        "statistics": ("D",),
        "extra_statistics": ("G_ST", "H_S", "H_T"),
        "minimum_effective_sample_size": 10.0,
        "identity_statistics": identity,
    }
    settings.update(changes)
    return BurnInMonitor(**settings)  # type: ignore[arg-type]


def _record(
    monitor: BurnInMonitor,
    identity: dict[str, IdentityStatistic],
    h_s: list[float],
    h_t: list[float],
    *,
    stop_at: int | None = None,
) -> None:
    """Record each generation's `D`, `G_ST` (when defined), `H_S` and `H_T`."""
    for generation, (within, total) in enumerate(zip(h_s, h_t, strict=True)):
        values = {"H_S": within, "H_T": total}
        d_value = identity["D"].function(within, total)
        assert d_value is not None
        values["D"] = d_value
        g_value = identity["G_ST"].function(within, total)
        if g_value is not None:
            values["G_ST"] = g_value
        monitor.record(generation, values)
        if stop_at is not None and generation >= stop_at:
            return


def test_form_one_is_the_mean_of_the_values_and_form_two_is_the_value_of_the_means(
    identity: dict[str, IdentityStatistic],
) -> None:
    """The two forms differ exactly as `mean f` and `f(mean)` do."""
    h_s, h_t = _heterozygosities(400, seed=11)
    monitor = _monitor(identity)
    _record(monitor, identity, h_s, h_t)

    forms = monitor.estimate_forms("D")
    assert forms is not None and forms.mean_of_values and forms.value_of_means
    window_s, window_t = h_s[1:], h_t[1:]
    d_values = [
        identity["D"].function(s, t) for s, t in zip(window_s, window_t, strict=True)
    ]
    assert forms.mean_of_values.mean == pytest.approx(
        math.fsum(v for v in d_values if v is not None) / len(window_s), abs=1e-15
    )
    expected = identity["D"].function(
        math.fsum(window_s) / len(window_s), math.fsum(window_t) / len(window_t)
    )
    assert forms.value_of_means.mean == pytest.approx(expected, abs=1e-15)
    assert forms.mean_of_values.mean != forms.value_of_means.mean
    assert forms.undefined_generations == 0


def test_the_selected_form_is_the_default_headline(
    identity: dict[str, IdentityStatistic],
) -> None:
    """With no choice made the headline is the mean of values; the other is carried."""
    h_s, h_t = _heterozygosities(300, seed=12)
    default = _monitor(identity)
    other = _monitor(identity, estimate="value_of_means")
    _record(default, identity, h_s, h_t)
    _record(other, identity, h_s, h_t)

    forms = default.estimate_forms("D")
    assert forms is not None and forms.selected == "mean_of_values"
    assert default.evidence_statistics("D") == forms.mean_of_values
    chosen = other.estimate_forms("D")
    assert chosen is not None and chosen.selected == "value_of_means"
    assert other.evidence_statistics("D") == chosen.value_of_means
    assert chosen.value_of_means == forms.value_of_means


def test_a_statistic_that_is_not_an_identity_function_has_one_form(
    identity: dict[str, IdentityStatistic],
) -> None:
    """`H_S` is recorded but is no function of the identities: form one only."""
    h_s, h_t = _heterozygosities(200, seed=13)
    monitor = _monitor(identity, estimate="value_of_means")
    _record(monitor, identity, h_s, h_t)

    forms = monitor.estimate_forms("H_S")
    assert forms is not None
    assert forms.value_of_means is None
    assert forms.selected == "mean_of_values"


def test_an_undefined_generation_is_dropped_from_form_one_and_counted(
    identity: dict[str, IdentityStatistic],
) -> None:
    """`G_ST` is undefined where `H_T` is zero; form two stays defined."""
    h_s, h_t = _heterozygosities(200, seed=14)
    h_s[50] = h_t[50] = 0.0
    h_s[60] = h_t[60] = 0.0
    monitor = _monitor(identity)
    _record(monitor, identity, h_s, h_t)

    forms = monitor.estimate_forms("G_ST")
    assert forms is not None and forms.mean_of_values and forms.value_of_means
    assert forms.undefined_generations == 2
    assert forms.mean_of_values.window == len(h_s) - 1 - 2
    assert forms.value_of_means.window == len(h_s) - 1


def test_auto_uses_the_value_of_means_exactly_when_the_rule_says_so(
    identity: dict[str, IdentityStatistic],
) -> None:
    """Auto: form two if any generation is undefined or too many are degenerate."""
    clean_s, clean_t = _heterozygosities(200, seed=15)
    undefined_s, undefined_t = list(clean_s), list(clean_t)
    undefined_s[70] = undefined_t[70] = 0.0
    wobble = [0.001 + 0.0001 * (step % 5) for step in range(150)]
    tiny_s = wobble + clean_s[150:]
    tiny_t = [1.5 * value for value in wobble] + clean_t[150:]
    cases = {
        "clean": (clean_s, clean_t, "mean_of_values"),
        "undefined": (undefined_s, undefined_t, "value_of_means"),
        "tiny": (tiny_s, tiny_t, "value_of_means"),
    }
    for label, (h_s, h_t, expected) in cases.items():
        monitor = _monitor(identity, estimate="auto")
        _record(monitor, identity, h_s, h_t)
        forms = monitor.estimate_forms("G_ST")
        assert forms is not None and forms.selected == expected, label


def test_select_form_applies_the_two_thresholds_exactly() -> None:
    """The degenerate share must exceed the fraction; any undefined one is enough."""
    kwargs = {"auto_fraction": 0.01}
    assert select_form("auto", undefined_generations=0, degenerate=0.01, **kwargs) == (
        "mean_of_values"
    )
    assert select_form("auto", undefined_generations=0, degenerate=0.011, **kwargs) == (
        "value_of_means"
    )
    assert select_form("auto", undefined_generations=1, degenerate=0.0, **kwargs) == (
        "value_of_means"
    )
    assert select_form(
        "value_of_means", undefined_generations=0, degenerate=0.0, **kwargs
    ) == ("value_of_means")
    with pytest.raises(ValueError, match="unknown estimate choice"):
        select_form("sometimes", undefined_generations=0, degenerate=0.0, **kwargs)


def test_degenerate_share_counts_denominators_below_the_threshold(
    identity: dict[str, IdentityStatistic],
) -> None:
    """`G_ST` divides by `H_T`; `D` by `1 - H_S`."""
    h_s = np.array([0.1, 0.5, 0.995, 0.3])
    h_t = np.array([0.2, 0.005, 0.999, 0.4])
    assert degenerate_share(h_s, h_t, identity["G_ST"], 0.01) == 0.25
    assert degenerate_share(h_s, h_t, identity["D"], 0.01) == 0.25
    assert degenerate_share(h_s[:0], h_t[:0], identity["D"], 0.01) == 0.0


@pytest.mark.parametrize("name", ["D", "G_ST"])
def test_the_gradients_match_finite_differences_of_the_formulas(
    identity: dict[str, IdentityStatistic], name: str
) -> None:
    """The delta method's slopes are the derivatives of the engine's own formula."""
    statistic = identity[name]
    step = 1e-6
    for within, total in [(0.3, 0.5), (0.5, 0.7), (0.1, 0.3), (0.45, 0.46)]:
        slope_s, slope_t = statistic.gradient(within, total)
        up = statistic.function(within + step, total)
        down = statistic.function(within - step, total)
        assert up is not None and down is not None
        assert slope_s == pytest.approx((up - down) / (2 * step), rel=1e-4, abs=1e-6)
        up = statistic.function(within, total + step)
        down = statistic.function(within, total - step)
        assert up is not None and down is not None
        assert slope_t == pytest.approx((up - down) / (2 * step), rel=1e-4, abs=1e-6)


def test_the_delta_method_error_of_a_flat_denominator_is_the_scaled_geyer_error(
    identity: dict[str, IdentityStatistic],
) -> None:
    """With `H_T` constant, `G_ST`'s error is `H_S`'s Geyer error times `1 / H_T`."""
    h_s, _ = _heterozygosities(500, seed=16)
    total = 0.7
    window_s = np.array(h_s)
    window_t = np.full_like(window_s, total)

    stats = value_of_means_statistics(window_s, window_t, identity["G_ST"])

    assert stats is not None
    assert stats.mean == pytest.approx(1.0 - float(np.mean(window_s)) / total)
    assert stats.standard_error == pytest.approx(
        geyer_window_statistics(window_s).standard_error / total, rel=1e-12
    )


def test_value_of_means_is_none_for_a_short_window_and_for_an_undefined_mean(
    identity: dict[str, IdentityStatistic],
) -> None:
    """No estimate is invented where the function has no value."""
    short = np.array([0.4, 0.5])
    assert value_of_means_statistics(short, short, identity["D"]) is None
    zeros = np.zeros(10)
    assert value_of_means_statistics(zeros, zeros, identity["G_ST"]) is None
    assert value_of_means_statistics(zeros, zeros[:5], identity["D"]) is None


def test_a_monitor_with_identity_statistics_needs_the_heterozygosities(
    identity: dict[str, IdentityStatistic],
) -> None:
    """The forms are computed from `H_S` and `H_T`, so both must be recorded."""
    with pytest.raises(ValueError, match="H_S and H_T"):
        _monitor(identity, extra_statistics=("G_ST",))
    with pytest.raises(ValueError, match="not recorded"):
        _monitor(replace(identity["D"]) and {"X": identity["D"]})
    with pytest.raises(ValueError, match="unknown estimate"):
        _monitor(identity, estimate="sometimes")
