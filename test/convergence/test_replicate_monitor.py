"""Tests of `ConvergenceMonitor`, the replicate batch's across-replicate monitor.

The monitor remembers one value per completed replicate and asks a
`ConfidenceIntervalCriterion` whether the interval is tight enough.
"""

from __future__ import annotations

from typing import Any

import pytest

from fim.convergence.criteria import ConfidenceIntervalCriterion
from fim.convergence.monitor import ConvergenceMonitor, StopReason


def _monitor(**changes: Any) -> ConvergenceMonitor:
    """A monitor whose criterion is met by any three identical values."""
    settings: dict[str, Any] = {"max_generations": 10}
    settings.update(changes)
    return ConvergenceMonitor(
        ConfidenceIntervalCriterion(minimum_count=3, tolerance=0.0),
        **settings,
    )


def test_it_distinguishes_convergence_from_the_cap() -> None:
    """A tight sample is converged; one that never tightens hits the cap."""
    tight = _monitor()
    for index in range(3):
        outcome = tight.record(index, 0.5)
    assert outcome.converged
    assert outcome.reason is StopReason.STATISTIC_CONVERGED

    loose = _monitor(max_generations=4)
    for index, value in enumerate([0.0, 1.0, 0.0, 1.0, 0.0]):
        outcome = loose.record(index, value)
    assert outcome.stopped
    assert not outcome.converged
    assert outcome.reason is StopReason.MAX_GENERATIONS
    assert outcome.generation == 4


def test_several_statistics_need_a_mapping_and_accept_a_partial_one() -> None:
    """A bare float needs one statistic; a mapping may omit one this round."""
    monitor = _monitor(statistics=("D", "G_ST"))
    with pytest.raises(ValueError, match="requires a mapping"):
        monitor.record(0, 0.5)
    monitor.record(0, {"D": 0.5})
    monitor.record(1, {})
    assert monitor.histories == {"D": (0.5,), "G_ST": ()}
    with pytest.raises(ValueError, match="unconfigured statistic"):
        monitor.record(2, {"D": 0.5, "G_ST": 0.5, "E_ST": 0.5})


def test_all_needs_every_statistic_and_any_needs_one() -> None:
    """`all` waits for the loose statistic; `any` stops on the tight one."""
    rows = [{"tight": 0.5, "loose": value} for value in (0.0, 1.0, 0.0, 1.0, 0.0, 1.0)]

    def stop(combinator: str) -> tuple[bool, int | None]:
        monitor = _monitor(
            statistics=("tight", "loose"),
            combinator=combinator,
            max_generations=5,
        )
        for index, row in enumerate(rows):
            outcome = monitor.record(index, row)
            if outcome.stopped:
                return outcome.converged, outcome.generation
        return False, None

    assert stop("any") == (True, 2)
    assert stop("all") == (False, 5)


def test_stable_statistics_names_those_that_passed_on_the_last_round() -> None:
    """Under `any`, only the passing statistic is named."""
    monitor = _monitor(statistics=("tight", "loose"), combinator="any")
    for index, value in enumerate([0.0, 1.0, 0.0]):
        monitor.record(index, {"tight": 0.5, "loose": value})
    assert monitor.stable_statistics() == ("tight",)


def test_extra_statistics_are_recorded_but_never_gate_stopping() -> None:
    """A display-only statistic that never settles does not hold the batch."""
    monitor = _monitor(statistics=("D",), extra_statistics=("H_S",))
    for index, value in enumerate([0.0, 0.5, 1.0]):
        monitor.record(index, {"D": 0.5, "H_S": value})
    assert monitor.should_stop()
    assert monitor.outcome().converged
    assert monitor.histories == {"D": (0.5, 0.5, 0.5), "H_S": (0.0, 0.5, 1.0)}
    assert monitor.history == (0.5, 0.5, 0.5)
    assert monitor.generations == (0, 1, 2)
    assert monitor.reason() is StopReason.STATISTIC_CONVERGED


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"max_generations": 0}, "max_generations"),
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
