"""Tests of the trajectory retention rule (design 6.13).

Pure functions of the settings: the kept generations never depend on time,
disk space or order, so the rule is checked against a brute-force listing.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from fim.persistence.retention import TrajectoryRetention, tapered_generations


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


# --- tapering ---------------------------------------------------------------


def _taper(
    final: int, keep_last: int, burn_in: int = 0, *, keep: bool = False
) -> list[int]:
    return tapered_generations(final, keep_last, burn_in, keep_burn_in=keep)


def _brute_force(final: int, keep_last: int, burn_in: int, *, keep: bool) -> list[int]:
    """The rule as the module docstring states it, one generation at a time."""
    kept = []
    for generation in range(final + 1):
        age = final - generation
        if not keep and 0 < generation < burn_in:
            continue
        stride = 1 if age < keep_last else 1 << int(math.log2(age // keep_last))
        if generation in (0, burn_in, final) or generation % stride == 0:
            kept.append(generation)
    return kept


def test_the_tapered_rule_keeps_the_end_whole_and_doubles_the_stride_going_back() -> (
    None
):
    assert _taper(30, 5) == [
        0,
        4,
        8,
        12,
        14,
        16,
        18,
        20,
        21,
        22,
        23,
        24,
        25,
        26,
        27,
        28,
        29,
        30,
    ]


def test_burn_in_frames_are_dropped_unless_asked_for() -> None:
    dropped = _taper(30, 5, burn_in=12)
    kept = _taper(30, 5, burn_in=12, keep=True)

    assert dropped[:3] == [0, 12, 14]
    assert kept[:4] == [0, 4, 8, 12]
    assert set(dropped) <= set(kept)


@pytest.mark.parametrize("burn_in", [0, 9, 40])
@pytest.mark.parametrize("keep", [False, True])
@pytest.mark.parametrize(
    ("final", "keep_last"), [(0, 1), (7, 3), (64, 8), (200, 10), (999, 50)]
)
def test_the_tapered_rule_matches_a_brute_force_listing(
    final: int, keep_last: int, burn_in: int, *, keep: bool
) -> None:
    assert _taper(final, keep_last, burn_in, keep=keep) == _brute_force(
        final, keep_last, burn_in, keep=keep
    )


@settings(max_examples=60, deadline=None)
@given(
    shorter=st.integers(min_value=0, max_value=600),
    extra=st.integers(min_value=0, max_value=900),
    keep_last=st.integers(min_value=1, max_value=40),
)
def test_tapering_is_path_independent(shorter: int, extra: int, keep_last: int) -> None:
    """What the final taper keeps was never lost by an earlier taper.

    Tapering when the run had reached `shorter` and then letting it go on
    leaves a log that holds everything the final rule needs, so tapering
    while the run goes equals tapering once at the end.
    """
    final = shorter + extra
    early = set(_taper(shorter, keep_last, keep=True))
    held = early | set(range(shorter + 1, final + 1))

    assert set(_taper(final, keep_last, keep=True)) <= held


@given(
    final=st.integers(min_value=0, max_value=3000),
    keep_last=st.integers(min_value=1, max_value=60),
)
def test_the_frame_count_is_bounded_by_the_logarithm_of_the_length(
    final: int, keep_last: int
) -> None:
    count = len(_taper(final, keep_last, keep=True))

    assert count <= keep_last * (2 + math.log2(final / keep_last + 1)) + 3


def test_two_replicates_of_equal_length_keep_the_same_generations() -> None:
    assert _taper(5000, 100, 400) == _taper(5000, 100, 400)


def test_a_long_run_keeps_about_twelve_thousand_frames() -> None:
    assert 12_000 <= len(_taper(2_600_000, 1000, keep=True)) <= 12_500


def test_the_retention_object_counts_and_lists_the_tapered_set() -> None:
    retention = TrajectoryRetention(tapered=True, keep_last=5, burn_in=12)

    assert retention.kept_generations(30) == _taper(30, 5, 12)
    assert retention.count_through(30) == len(_taper(30, 5, 12))
    assert retention.skips_generations and not retention.thinned
    assert retention.keeps(3)  # every frame is written; the end cuts it down


@pytest.mark.parametrize(("final", "keep_last"), [(-1, 5), (5, 0)])
def test_the_rule_refuses_nonsense(final: int, keep_last: int) -> None:
    with pytest.raises(ValueError, match="keep_last"):
        tapered_generations(final, keep_last)
