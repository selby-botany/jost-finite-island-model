"""Tests for operational stochastic-equilibrium detection."""

import pytest

from fim.convergence.criteria import TrailingWindowCriterion, trailing_window_stable
from fim.convergence.monitor import ConvergenceMonitor, StopReason


def test_constant_sequence_is_stable_when_window_fills() -> None:
    """A fixed statistic converges at the first full window."""
    assert trailing_window_stable([0.5, 0.5, 0.5, 0.5], 4, 0.0)


def test_linear_drift_is_not_stable() -> None:
    """A moving half-window mean does not converge."""
    assert not trailing_window_stable([0.0, 0.1, 0.2, 0.3], 4, 0.05)


@pytest.mark.parametrize(
    ("history", "tolerance", "expected"),
    [
        ([0.9, 1.1, 0.9, 1.1], 0.0, True),
        ([0.0, 0.0, 1.0, 1.0], 0.9, False),
    ],
)
def test_oscillation_uses_half_window_means(
    history: list[float],
    tolerance: float,
    expected: bool,
) -> None:
    """Oscillation is judged by the documented half-mean rule."""
    assert trailing_window_stable(history, 4, tolerance) is expected


def test_monitor_distinguishes_convergence_from_cap() -> None:
    """Terminal outcomes remain valid and explain why the run stopped."""
    converged = ConvergenceMonitor(
        TrailingWindowCriterion(4, 0.0),
        max_generations=10,
    )
    for generation in range(4):
        converged.record(generation, 0.5)

    capped = ConvergenceMonitor(
        TrailingWindowCriterion(4, 0.0),
        max_generations=3,
    )
    for generation, value in enumerate([0.0, 0.1, 0.2, 0.3]):
        capped.record(generation, value)

    assert converged.reason() is StopReason.STATISTIC_CONVERGED
    assert capped.reason() is StopReason.MAX_GENERATIONS
    assert converged.outcome().converged
    assert not capped.outcome().converged


def test_multi_statistic_monitor_requires_a_mapping_but_accepts_a_partial_one() -> None:
    """Watching several statistics rejects a bare float, not a partial mapping.

    Regression test for R5: a mapping that omits a configured statistic
    (its value is undefined this round) is now valid — that statistic
    simply does not advance this round — but a name outside the
    configured set is still almost certainly a typo and still raises.
    """
    monitor = ConvergenceMonitor(
        TrailingWindowCriterion(2, 0.0),
        max_generations=10,
        statistics=("D", "G_ST"),
    )

    with pytest.raises(ValueError, match="requires a mapping"):
        monitor.record(0, 0.5)

    monitor.record(0, {"D": 0.5})
    monitor.record(1, {})
    assert monitor.histories == {"D": (0.5,), "G_ST": ()}

    with pytest.raises(ValueError, match="unconfigured statistic"):
        monitor.record(2, {"D": 0.5, "G_ST": 0.5, "E_ST": 0.5})


def test_all_combinator_requires_every_statistic_stable() -> None:
    """The all combinator stops only once every statistic's history is stable."""
    monitor = ConvergenceMonitor(
        TrailingWindowCriterion(2, 0.0),
        max_generations=10,
        statistics=("D", "G_ST"),
        combinator="all",
    )

    # D is immediately stable at a constant 0.5; G_ST keeps drifting for two
    # more generations, so the combined monitor must not stop until it does.
    monitor.record(0, {"D": 0.5, "G_ST": 0.0})
    monitor.record(1, {"D": 0.5, "G_ST": 0.1})
    assert not monitor.should_stop()
    monitor.record(2, {"D": 0.5, "G_ST": 0.2})
    assert not monitor.should_stop()
    monitor.record(3, {"D": 0.5, "G_ST": 0.2})

    assert monitor.should_stop()
    assert monitor.outcome().converged
    assert monitor.histories == {
        "D": (0.5, 0.5, 0.5, 0.5),
        "G_ST": (0.0, 0.1, 0.2, 0.2),
    }


def test_any_combinator_stops_as_soon_as_one_statistic_is_stable() -> None:
    """The any combinator stops as soon as one statistic's history is stable."""
    monitor = ConvergenceMonitor(
        TrailingWindowCriterion(2, 0.0),
        max_generations=10,
        statistics=("D", "G_ST"),
        combinator="any",
    )

    # D is immediately stable; G_ST is still moving. "any" must stop on D
    # alone rather than waiting for G_ST, unlike the "all" case above.
    monitor.record(0, {"D": 0.5, "G_ST": 0.0})
    monitor.record(1, {"D": 0.5, "G_ST": 0.1})

    assert monitor.should_stop()
    assert monitor.outcome().converged
    assert monitor.history == (0.5, 0.5)
    assert monitor.histories == {"D": (0.5, 0.5), "G_ST": (0.0, 0.1)}


def test_monitor_constructor_validates_statistics_and_combinator() -> None:
    """Statistic names and the combinator are validated at construction."""
    with pytest.raises(ValueError, match="statistics must not be empty"):
        ConvergenceMonitor(
            TrailingWindowCriterion(2, 0.0), max_generations=10, statistics=()
        )
    with pytest.raises(ValueError, match="must not repeat a name"):
        ConvergenceMonitor(
            TrailingWindowCriterion(2, 0.0),
            max_generations=10,
            statistics=("D", "D"),
        )
    with pytest.raises(ValueError, match="combinator must be"):
        ConvergenceMonitor(
            TrailingWindowCriterion(2, 0.0),
            max_generations=10,
            combinator="either",  # type: ignore[arg-type]
        )
