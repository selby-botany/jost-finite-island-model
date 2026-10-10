"""Tests of the trajectory retention rule (design 6.13).

Pure functions of the settings: the kept generations never depend on time,
disk space or order, so the rule is checked against a brute-force listing.
"""

from __future__ import annotations

import pytest

from fim.persistence.retention import TrajectoryRetention


def _kept(retention: TrajectoryRetention, final: int) -> list[int]:
    """The generations a run that stopped at `final` writes, by brute force."""
    return [
        generation
        for generation in range(final + 1)
        if retention.keeps(generation) or generation == final
    ]


def test_full_retention_keeps_every_generation() -> None:
    """The default rule skips nothing."""
    retention = TrajectoryRetention()

    assert all(retention.keeps(g) for g in range(500))
    assert retention.count_through(499) == 500


def test_thinning_keeps_the_head_the_stride_the_burn_in_and_the_end() -> None:
    """Generation 0, everything before the start, every stride-th, burn-in, final."""
    retention = TrajectoryRetention(thinned=True, start=20, stride=5, burn_in=33)

    assert _kept(retention, 62) == [
        *range(20),
        20,
        25,
        30,
        33,
        35,
        40,
        45,
        50,
        55,
        60,
        62,
    ]


@pytest.mark.parametrize("start", [1, 7, 20])
@pytest.mark.parametrize("stride", [1, 3, 10])
@pytest.mark.parametrize("burn_in", [0, 5, 21, 40])
@pytest.mark.parametrize("final", [0, 3, 20, 21, 39, 40, 41, 100])
def test_the_count_matches_a_brute_force_listing(
    start: int, stride: int, burn_in: int, final: int
) -> None:
    """`count_through` is exact, so a reader can check a thinned file's length."""
    retention = TrajectoryRetention(
        thinned=True, start=start, stride=stride, burn_in=burn_in
    )

    assert retention.count_through(final) == len(set(_kept(retention, final)))


def test_a_run_shorter_than_the_start_is_not_thinned() -> None:
    """Thinning starts at `start`; before it every generation is kept."""
    retention = TrajectoryRetention(thinned=True, start=100, stride=10)

    assert retention.count_through(99) == 100
    assert _kept(retention, 99) == list(range(100))
