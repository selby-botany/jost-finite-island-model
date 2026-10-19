"""Tests of `fim.persistence.recording`: what a run kept, and whether it is enough."""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from fim.persistence.recording import (
    Recording,
    comparable_artifacts,
    recording_of_run,
    run_satisfies,
)
from fim.persistence.retention import TrajectoryRetention, tapered_generations

_RECORDINGS = [
    Recording(),
    Recording(level="thinned", stride=5, thinning_start=20),
    Recording(level="thinned", stride=10, thinning_start=20),
    Recording(level="thinned", stride=10, thinning_start=40),
    Recording(level="tapered", keep_last=10),
    Recording(level="tapered", keep_last=40),
    Recording(level="tapered", keep_last=40, keep_burn_in=True),
    Recording(level="statistics"),
    Recording(history="compact"),
    Recording(level="tapered", keep_last=40, history="compact"),
    Recording(level="statistics", history="compact"),
]


def _frames(recording: Recording, final: int, burn_in: int) -> set[int]:
    """The generations a run with this recording holds, by the rules themselves."""
    if recording.level == "statistics":
        return set()
    if recording.level == "full":
        return set(range(final + 1))
    if recording.level == "thinned":
        rule = TrajectoryRetention(
            thinned=True,
            start=recording.thinning_start,
            stride=recording.stride,
            burn_in=burn_in,
        )
        return set(rule.kept_generations(final))
    return set(
        tapered_generations(
            final, recording.keep_last, burn_in, keep_burn_in=recording.keep_burn_in
        )
    )


@pytest.mark.parametrize(
    ("have", "want"), list(itertools.product(_RECORDINGS, repeat=2))
)
def test_a_run_that_satisfies_a_request_holds_every_frame_it_asks_for(
    have: Recording, want: Recording
) -> None:
    if not have.satisfies(want):
        return
    assert have.history == "exact" or want.history == "compact"
    for final, burn_in in [(30, 12), (200, 60), (999, 0), (4000, 700)]:
        assert _frames(have, final, burn_in) >= _frames(want, final, burn_in)


def test_the_ladder_in_words() -> None:
    full, statistics = Recording(), Recording(level="statistics")
    big = Recording(level="tapered", keep_last=40)
    small = Recording(level="tapered", keep_last=10)

    assert all(
        full.satisfies(other) for other in _RECORDINGS if other.history == "exact"
    )
    assert statistics.satisfies(statistics)
    assert not statistics.satisfies(full) and not statistics.satisfies(small)
    assert big.satisfies(small) and not small.satisfies(big)
    assert big.satisfies(statistics)
    assert not big.satisfies(full)
    assert not Recording(level="thinned").satisfies(small)
    assert not small.satisfies(Recording(level="thinned"))


def test_an_exact_history_serves_a_compact_request_but_not_the_reverse() -> None:
    exact, compact = Recording(), Recording(history="compact")

    assert exact.satisfies(compact) and exact.satisfies(exact)
    assert compact.satisfies(compact) and not compact.satisfies(exact)


def test_burn_in_frames_kept_serve_a_request_that_drops_them_not_the_reverse() -> None:
    kept = Recording(level="tapered", keep_last=20, keep_burn_in=True)
    dropped = Recording(level="tapered", keep_last=20)

    assert kept.satisfies(dropped) and not dropped.satisfies(kept)


def test_a_statistics_run_is_missing_the_trajectory_and_the_full_history() -> None:
    assert Recording(level="statistics").absent_artifacts == {
        "trajectory",
        "equilibrium_trajectory",
        "convergence",
    }
    assert Recording().absent_artifacts == frozenset()


def test_what_two_runs_of_one_seed_must_agree_on() -> None:
    full, tapered = Recording(), Recording(level="tapered")

    assert comparable_artifacts(full, full) >= {"report", "trajectory", "convergence"}
    assert comparable_artifacts(full, tapered) == {
        "report",
        "pairwise",
        "summary",
        "convergence",
    }
    assert comparable_artifacts(full, Recording(history="compact")) == {
        "report",
        "pairwise",
        "summary",
        "trajectory",
    }
    assert comparable_artifacts(full, Recording(level="statistics")) == {
        "report",
        "pairwise",
        "summary",
    }


def test_a_recording_is_read_from_parameters_with_defaults() -> None:
    assert Recording.from_parameters({}) == Recording()
    read = Recording.from_parameters(
        {
            "trajectory_retention": "tapered",
            "trajectory_keep_last": 25,
            "trajectory_keep_burn_in": True,
            "history_format": "compact",
        }
    )
    assert read == Recording(
        level="tapered", keep_last=25, keep_burn_in=True, history="compact"
    )


def test_a_run_directory_reports_what_its_manifest_says(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(
        json.dumps({"parameters": {"trajectory_retention": "statistics"}})
    )

    assert recording_of_run(tmp_path) == Recording(level="statistics")
    assert not run_satisfies(tmp_path, {})
    assert run_satisfies(tmp_path, {"trajectory_retention": "statistics"})
    assert recording_of_run(tmp_path / "missing") == Recording()
    (tmp_path / "manifest.json").write_text("not json")
    assert recording_of_run(tmp_path) == Recording()
