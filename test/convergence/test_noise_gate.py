"""`ConvergenceMonitor`'s noise-adequacy gate: `_gated_stable`.

`trailing_window_stable` alone answers "has this stopped trending," which a
noisy-but-flat series can satisfy by chance long before its own trailing-
window mean is actually known to the configured tolerance — the real defect
these tests were written against (a botanist-reported worked example whose
"converged" `D` was a single noisy generation, nowhere near the model's own
expectation). These tests prove the gate actually delays a stop until the
window's own standard error is noise-adequate, that it still stops promptly
once the process is genuinely precise, and that a run capped without ever
reaching noise-adequacy reports that honestly.
"""

from __future__ import annotations

import itertools
import math
import random
from collections.abc import Sequence
from typing import Literal

import pytest

from fim.config.convergence import MINIMUM_NOISE_CHECK_WINDOW
from fim.convergence.criteria import TrailingWindowCriterion, trailing_window_stable
from fim.convergence.monitor import ConvergenceMonitor, ConvergenceOutcome
from fim.convergence.window_statistics import WindowStatistics, window_statistics


def _noisy_flat_series(
    *, mean: float, sigma: float, length: int, seed: int
) -> list[float]:
    """Independent Gaussian noise around a fixed mean -- no trend, real noise."""
    rng = random.Random(seed)
    return [mean + rng.gauss(0.0, sigma) for _ in range(length)]


def _ar1_series(
    *, phi: float, sigma: float, length: int, seed: int, mean: float = 0.4
) -> list[float]:
    """A stationary AR(1) series -- see `test_window_statistics.py`'s own twin.

    Correlated noise, not independent: real per-generation drift/mutation
    statistics behave this way (`fim.convergence.defaults`'s own relaxation
    time `tau` is exactly this process's own correlation length), and
    correlation is what genuinely fools the trend-only check -- two
    neighboring halves of a correlated series look artificially similar to
    each other even while the *window's own mean* is still poorly known.
    Independent noise (`_noisy_flat_series`, above) does not reproduce that
    asymmetry: for i.i.d. noise the two checks have comparable statistical
    power (the half-window-difference's own standard deviation is exactly
    twice the window-mean's own standard error), so it is used only for the
    two tests that do not depend on the asymmetry itself.
    """
    rng = random.Random(seed)
    stationary_sigma = sigma / math.sqrt(1.0 - phi * phi)
    value = mean + rng.gauss(0.0, stationary_sigma)
    series = []
    for _ in range(length):
        series.append(value)
        value = mean + phi * (value - mean) + rng.gauss(0.0, sigma)
    return series


def test_the_noise_gate_delays_a_stop_the_trend_check_alone_would_have_taken() -> None:
    """Correlated noise that fools the trend check does not fool the gate.

    `phi=0.85` positive correlation, `window=24`, `tolerance=0.03`: two
    neighboring 12-value halves of a correlated series land close together
    (`trailing_window_stable` alone -- the old, ungated behavior -- passes
    by generation 23, confirmed below, not assumed) long before the window's
    own correlation-corrected standard error has actually shrunk enough. The
    gated monitor must not stop that early, and must eventually stop once it
    genuinely has (`stats.noise_adequate`).
    """
    window, tolerance = 24, 0.03
    series = _ar1_series(phi=0.85, sigma=0.05, length=3000, seed=8)

    ungated_count = next(
        count
        for count in range(window, len(series) + 1)
        if trailing_window_stable(series[:count], window, tolerance)
    )
    ungated_stop = ungated_count - 1  # `record()`'s own 0-indexed generation
    assert ungated_stop < 30  # the premise: the old rule really is fooled early

    monitor = ConvergenceMonitor(
        TrailingWindowCriterion(window, tolerance), max_generations=len(series) - 1
    )
    gated_stop = None
    for generation, value in enumerate(series):
        if monitor.record(generation, value).stopped:
            gated_stop = generation
            break

    assert gated_stop is not None
    assert gated_stop > ungated_stop + 50
    stats = monitor.window_statistics("value")
    assert stats is not None
    assert stats.noise_adequate(tolerance)


def test_a_genuinely_precise_series_still_stops_promptly() -> None:
    """Tiny noise relative to tolerance costs (almost) no extra generations.

    Regression guard: the gate must not meaningfully delay the easy,
    already-well-served case (a multi-locus or multi-replicate mean whose
    own noise is already far below the requested tolerance).
    """
    window, tolerance = 30, 0.02
    series = _noisy_flat_series(mean=0.6, sigma=0.0005, length=200, seed=3)

    ungated_stop = (
        next(
            count
            for count in range(window, len(series) + 1)
            if trailing_window_stable(series[:count], window, tolerance)
        )
        - 1
    )
    monitor = ConvergenceMonitor(
        TrailingWindowCriterion(window, tolerance), max_generations=len(series) - 1
    )
    gated_stop = next(
        generation
        for generation, value in enumerate(series)
        if monitor.record(generation, value).stopped
    )

    assert gated_stop - ungated_stop <= 2


def test_a_run_that_never_reaches_noise_adequacy_is_honestly_capped() -> None:
    """Hitting the cap without a noise-adequate window reports `converged=False`.

    Persistent noise (`sigma` large relative to `tolerance`, a window too
    short to average enough of it away) never satisfies the gate — the run
    must report the cap, not a false convergence, and `window_statistics`
    must still be available so a caller can say how far off the estimate is.
    """
    window, tolerance = 20, 0.001
    series = _noisy_flat_series(mean=0.3, sigma=0.05, length=150, seed=5)

    monitor = ConvergenceMonitor(
        TrailingWindowCriterion(window, tolerance), max_generations=len(series) - 1
    )
    outcome = None
    for generation, value in enumerate(series):
        outcome = monitor.record(generation, value)
        if outcome.stopped:
            break

    assert outcome is not None
    assert outcome.stopped
    assert outcome.converged is False
    stats = monitor.window_statistics("value")
    assert stats is not None
    assert not stats.noise_adequate(tolerance)
    assert stats.standard_error > tolerance * 0.5


def test_window_statistics_is_none_before_any_check_has_run() -> None:
    """A fresh monitor, or one whose trend never stabilized, has nothing yet."""
    monitor = ConvergenceMonitor(TrailingWindowCriterion(20, 0.001), max_generations=5)
    for generation, value in enumerate([0.0, 1.0, 0.0, 1.0, 0.0, 1.0][:5]):
        monitor.record(generation, value)

    assert monitor.window_statistics("value") is None


@pytest.mark.parametrize("window", [2, MINIMUM_NOISE_CHECK_WINDOW - 1])
def test_a_window_shorter_than_the_noise_check_minimum_is_never_gated(
    window: int,
) -> None:
    """Below `MINIMUM_NOISE_CHECK_WINDOW`, the trend check alone decides.

    Matches the trend-only check's own original behavior for a window too
    short to estimate a lag-1 autocorrelation from at all
    (`fim.convergence.window_statistics`'s own docstring).
    """
    monitor = ConvergenceMonitor(
        TrailingWindowCriterion(window, 0.0), max_generations=10
    )
    outcome = None
    for generation in range(window):
        outcome = monitor.record(generation, 0.5)

    assert outcome is not None
    assert outcome.stopped
    assert outcome.converged
    assert monitor.window_statistics("value") is None


def test_a_criterion_without_a_window_or_tolerance_is_never_gated() -> None:
    """A non-`TrailingWindowCriterion`-shaped criterion passes through unchanged."""

    class _AlwaysStable:
        def is_stable(self, history: list[float]) -> bool:
            del history
            return True

    monitor = ConvergenceMonitor(_AlwaysStable(), max_generations=10)  # type: ignore[arg-type]
    outcome = monitor.record(0, 0.5)

    assert outcome.stopped
    assert outcome.converged
    assert monitor.window_statistics("value") is None


def test_the_evidence_window_grows_past_a_flickering_trend_check() -> None:
    """A flickering (but genuinely stationary) trend check must not reset growth.

    The first version of this gate reset its accumulated evidence every
    time the fast trend check next read `False` for even one generation --
    confirmed directly to never grow past the base `window` at all on a
    real 200,000-generation engine run, because real noisy data flickers
    the trend check more often than the doubling interval allows. This
    seeds one such flickering series and checks the window really did grow
    beyond its own base length by the time the run stopped.
    """
    window, tolerance = 24, 0.03
    series = _ar1_series(phi=0.7, sigma=0.05, length=3000, seed=1)

    monitor = ConvergenceMonitor(
        TrailingWindowCriterion(window, tolerance), max_generations=len(series) - 1
    )
    for generation, value in enumerate(series):
        if monitor.record(generation, value).stopped:
            break

    stats = monitor.window_statistics("value")
    assert stats is not None
    assert stats.window > window
    assert stats.noise_adequate(tolerance)


def _several_statistic_histories(length: int) -> dict[str, list[float]]:
    """Four watched statistics whose noise gates settle at different times.

    `slow` (strongly correlated, `phi=0.85`) needs a long evidence window,
    `medium` a shorter one, while `flat` (constant) and `precise` (tiny
    independent noise) are noise-adequate at their very first check — a
    mix in which, before the order-dependence fix, whichever statistic was
    listed first decided which of the others ever had their gates
    consulted at all.
    """
    return {
        "slow": _ar1_series(phi=0.85, sigma=0.05, length=length, seed=8),
        "medium": _ar1_series(phi=0.6, sigma=0.04, length=length, seed=3, mean=0.6),
        "flat": [0.5] * length,
        "precise": _noisy_flat_series(mean=0.6, sigma=0.0005, length=length, seed=3),
    }


def _run_several(
    histories: dict[str, list[float]],
    statistics: tuple[str, ...],
    combinator: Literal["any", "all"],
    *,
    window: int = 24,
    tolerance: float = 0.03,
) -> ConvergenceMonitor:
    """Drive a several-statistic monitor over `histories` until it stops."""
    length = len(next(iter(histories.values())))
    monitor = ConvergenceMonitor(
        TrailingWindowCriterion(window, tolerance),
        max_generations=length - 1,
        statistics=statistics,
        combinator=combinator,
    )
    for generation in range(length):
        values = {name: histories[name][generation] for name in statistics}
        if monitor.record(generation, values).stopped:
            break
    return monitor


@pytest.mark.parametrize("combinator", ["any", "all"])
def test_the_stop_decision_does_not_depend_on_statistic_order(
    combinator: Literal["any", "all"],
) -> None:
    """Every ordering of the same statistics stops identically.

    `record` once handed a generator to `all`/`any`, which short-circuit:
    a statistic listed after the one that decided a round was never
    judged that round, so its evidence window was anchored late (or never)
    and its noise checks fell on a different schedule. Measured on exactly
    these histories, the old code stopped `"all"` at generation 456 in one
    order and 129 in another. Every permutation must now agree on the
    stop generation and on every statistic's `window_statistics` (whether
    available at all, and its exact value when it is).
    """
    histories = _several_statistic_histories(3000)
    names = tuple(histories)

    def summary(
        order: tuple[str, ...],
    ) -> tuple[ConvergenceOutcome, dict[str, WindowStatistics | None]]:
        """Return one ordering's outcome and every statistic's window check."""
        monitor = _run_several(histories, order, combinator)
        return (
            monitor.outcome(),
            {name: monitor.window_statistics(name) for name in names},
        )

    reference = summary(names)
    assert reference[0].converged
    for order in itertools.permutations(names):
        assert summary(order) == reference, order


def test_all_anchors_a_statistic_that_is_not_deciding_the_outcome() -> None:
    """Under `"all"`, a statistic waited on by others is still gated each round.

    `slow` is listed first and is still far from noise-adequate at
    generation 23, so it alone decides that `"all"` is not yet satisfied.
    `precise` is trend-stable from its first full window, and must have its
    own evidence window anchored and checked right then — not deferred
    until `slow` happens to read `True`, as the short-circuiting `all()`
    once did (leaving `precise` with no `window_statistics` at all here).
    """
    window, tolerance = 24, 0.03
    histories = _several_statistic_histories(window)
    statistics = ("slow", "precise")
    monitor = ConvergenceMonitor(
        TrailingWindowCriterion(window, tolerance),
        max_generations=1000,
        statistics=statistics,
        combinator="all",
    )
    for generation in range(window):
        monitor.record(
            generation, {name: histories[name][generation] for name in statistics}
        )

    assert not monitor.should_stop()
    slow = monitor.window_statistics("slow")
    assert slow is None or not slow.noise_adequate(tolerance)
    precise = monitor.window_statistics("precise")
    assert precise is not None
    assert precise.window == window
    assert precise.noise_adequate(tolerance)


def test_an_already_adequate_statistic_is_not_rechecked_while_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once noise-adequate, a statistic's cached verdict is reused, not recomputed.

    Under `"all"`, `precise` is noise-adequate at its first check (generation
    23), while `drifting` keeps falling for 200 generations before it
    levels off — so `"all"` cannot fire for a long while after `precise` has
    already passed. `_gated_stable`'s own docstring promises that a
    statistic in that position stops growing and keeps returning its one
    cached verdict; the `O(window)` `window_statistics` computation must
    therefore run exactly once for `precise` over the whole run (it was
    once recomputed every generation, over an ever-growing window), and
    that cached `True` must still be what lets the run converge once
    `drifting` settles.
    """
    window, tolerance = 24, 0.03
    length = 600
    precise_series = _noisy_flat_series(mean=0.6, sigma=0.0005, length=length, seed=3)
    # Strictly negative throughout, so a computation over `drifting`'s
    # values can never be mistaken for one over `precise`'s (all near 0.6).
    drifting_series = [
        -0.01 - 0.01 * min(generation, 200) for generation in range(length)
    ]

    precise_windows: list[int] = []

    def counting_window_statistics(values: Sequence[float]) -> WindowStatistics:
        """Record each computation over `precise`'s values, then delegate."""
        if min(values) > 0.0:
            precise_windows.append(len(values))
        return window_statistics(values)

    monkeypatch.setattr(
        "fim.convergence.monitor.window_statistics", counting_window_statistics
    )
    monitor = ConvergenceMonitor(
        TrailingWindowCriterion(window, tolerance),
        max_generations=length - 1,
        statistics=("precise", "drifting"),
        combinator="all",
    )
    stop = None
    for generation in range(length):
        values = {
            "precise": precise_series[generation],
            "drifting": drifting_series[generation],
        }
        if monitor.record(generation, values).stopped:
            stop = generation
            break

    assert precise_windows == [window]
    assert stop is not None
    assert stop > 200  # `drifting`, not `precise`, decided when "all" fired
    assert monitor.outcome().converged
    precise = monitor.window_statistics("precise")
    assert precise is not None
    assert precise.window == window  # the cached window stopped growing
    assert precise.noise_adequate(tolerance)
