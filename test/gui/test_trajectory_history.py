"""Unit tests for `fim.gui.trajectory_history`.

No display, no `gui` marker: `sampled_statistic_history` returns plain
data (generation numbers and statistic values), not anything rendered —
the identical "no window needed" shape `test_animation.py`'s own module
docstring already establishes for `fim.gui.animation`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from fim import cli
from fim.gui.trajectory_history import sampled_statistic_history


def _write_run(tmp_path: Path, **overrides: object) -> Path:
    """Write a small, real completed run and return its output directory.

    Mirrors `test_reanalyze.py`'s own identically-shaped helper.
    """
    config: dict[str, object] = {
        "N": 20,
        "d": 2,
        "m": 0.1,
        "mu": 0.01,
        "seed": 20260814,
        "loci": [{"locus_id": 1, "length": 200}],
        "convergence_window": 4,
        "convergence_tolerance": 1.0,
        "max_generations": 10,
        "n_replicates": 1,
        "replicate_tolerance": None,
    }
    config.update(overrides)
    config_path = tmp_path / "run.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    output_directory = tmp_path / "output"
    assert (
        cli.main(["run", str(config_path), "-o", str(output_directory), "--quiet"]) == 0
    )
    return output_directory


def test_final_sample_matches_the_live_report(tmp_path: Path) -> None:
    """The last sampled generation's own statistics match the run's own report.json.

    Mirrors `test_reanalyze_trajectory_matches_the_live_report`'s own
    proof for `reanalyze_trajectory`, one generation at a time — the
    same underlying mechanism, applied to the run's own final entry.
    """
    output = _write_run(tmp_path)

    history = sampled_statistic_history(output / "trajectory.jsonl")

    live = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert history.generations[-1] == live["generation"]
    for name in ("D", "G_ST", "E_ST", "K_ST", "H_S", "H_T"):
        assert history.histories[name][-1] == pytest.approx(live[name])


def test_generations_and_every_history_share_one_length(tmp_path: Path) -> None:
    """`generations` and each statistic's own history line up one to one."""
    output = _write_run(tmp_path)

    history = sampled_statistic_history(output / "trajectory.jsonl")

    assert len(history.generations) > 1
    assert history.generations == sorted(history.generations)
    for values in history.histories.values():
        assert len(values) == len(history.generations)


def test_generation_zero_is_always_the_first_sample(tmp_path: Path) -> None:
    """The starting population is always included, not just the final one."""
    output = _write_run(tmp_path)

    history = sampled_statistic_history(output / "trajectory.jsonl")

    assert history.generations[0] == 0


def test_max_samples_bounds_how_many_generations_are_recomputed(
    tmp_path: Path,
) -> None:
    """A long run's own sample never exceeds `max_samples`, matching animation.

    A tight `convergence_tolerance` (`test_animation.py`'s own `_write_
    run` uses the identical value, for the identical reason) keeps this
    run from settling early, so it persists every generation up to
    `max_generations` — a small `max_samples` here genuinely exercises
    the cap rather than coincidentally sampling every generation anyway
    (this module's own default fixture settings above converge almost
    immediately, exactly the opposite of what this one test needs).
    """
    output = _write_run(tmp_path, convergence_tolerance=1e-6, max_generations=20)
    live = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert live["generation"] == 20, "fixture assumption: this run reaches the cap"

    history = sampled_statistic_history(output / "trajectory.jsonl", max_samples=5)

    assert len(history.generations) <= 5
    assert history.generations[0] == 0
    assert history.generations[-1] == 20


def test_rejects_a_tampered_trajectory(tmp_path: Path) -> None:
    """A trajectory edited after the run completed fails the digest check.

    Identical failure mode to `reanalyze_trajectory`'s own equivalent
    test — the same `verify_trajectory_integrity` call underneath.
    """
    output = _write_run(tmp_path)
    trajectory = output / "trajectory.jsonl"
    corrupted = trajectory.read_text(encoding="utf-8").replace(
        '"run_id":"run-', '"run_id":"other-'
    )
    trajectory.write_text(corrupted, encoding="utf-8")

    with pytest.raises(ValueError, match="does not match its manifest"):
        sampled_statistic_history(trajectory)
