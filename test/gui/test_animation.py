"""Unit tests for `fim.gui.animation`.

No display, no Tk import, no Matplotlib import at all — `pre_render_frames`
returns plain coordinate data (`doc/fim-gui-design.md` §8), not rendered
`Figure` objects, so nothing here carries the `gui` marker.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml

from fim import cli
from fim.gui import animation, batch_runner
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.persistence.manifest import read_batch_manifest, read_manifest
from fim.reanalyze import group_rows_by_generation
from fim.viz.scatter import frequency_points


def _write_run(tmp_path: Path, **overrides: object) -> Path:
    """Write a small config with several generations and return its output directory."""
    config: dict[str, object] = {
        "N": 20,
        "d": 2,
        "m": 0.1,
        "mu": 0.01,
        "seed": 1,
        "loci": [{"locus_id": 1, "length": 200}],
        "convergence_window": 8,
        "convergence_tolerance": 1e-6,
        "max_generations": 12,
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


def _write_batch_run(tmp_path: Path, **overrides: object) -> Path:
    """Write a small, staggered-stopping batch and return its output directory.

    `seed=42`/`convergence_tolerance=0.02` matches `test/engine/
    test_engine.py`'s own identical configuration, confirmed live to
    produce real, staggered stopping generations (`[3, 5, 6, 12, 15]`)
    rather than every replicate converging together.
    """
    config: dict[str, object] = {
        "N": 20,
        "d": 2,
        "m": 0.1,
        "mu": 0.01,
        "seed": 42,
        "loci": [{"locus_id": 1, "length": 200}],
        "convergence_window": 4,
        "convergence_tolerance": 0.02,
        "max_generations": 30,
        "n_replicates": 5,
        "replicate_tolerance": None,
    }
    config.update(overrides)
    config_path = tmp_path / "batch.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    output_directory = tmp_path / "output"
    assert (
        cli.main(["run", str(config_path), "-o", str(output_directory), "--quiet"]) == 0
    )
    return output_directory


def test_select_sample_generations_returns_everything_when_within_the_limit() -> None:
    """Fewer available generations than `max_frames` returns all of them, sorted."""
    assert animation.select_sample_generations([3, 1, 2], max_frames=10) == [1, 2, 3]


def test_select_sample_generations_deduplicates_and_sorts_input() -> None:
    """Out-of-order, duplicate-containing input still normalizes correctly."""
    assert animation.select_sample_generations([5, 5, 1, 3, 1], max_frames=10) == [
        1,
        3,
        5,
    ]


def test_select_sample_generations_caps_at_max_frames() -> None:
    """More available generations than `max_frames` never returns more than that."""
    available = list(range(1519))  # 1519 generations, matching the design mock

    sampled = animation.select_sample_generations(available, max_frames=100)

    assert len(sampled) <= 100
    assert sampled == sorted(set(sampled))
    assert sampled[0] == 0
    assert sampled[-1] == 1518


def test_select_sample_generations_always_includes_first_and_last() -> None:
    """First and last generation numbers are always present when max_frames >= 2."""
    for max_frames in (2, 3, 10, 50, 99, 100, 101):
        sampled = animation.select_sample_generations(range(1519), max_frames)
        assert sampled[0] == 0
        assert sampled[-1] == 1518


def test_select_sample_generations_single_generation_input() -> None:
    """A single available generation returns just that one, regardless of max_frames."""
    assert animation.select_sample_generations([7], max_frames=100) == [7]


def test_select_sample_generations_max_frames_one_returns_only_the_last() -> None:
    """`max_frames == 1` keeps only the run's terminal state."""
    assert animation.select_sample_generations([0, 1, 2, 3], max_frames=1) == [3]


def test_select_sample_generations_max_frames_zero_returns_nothing() -> None:
    """`max_frames <= 0` is a degenerate, but not erroring, request for no frames."""
    assert animation.select_sample_generations([0, 1, 2], max_frames=0) == []


def test_select_sample_generations_empty_input_returns_nothing() -> None:
    """No available generations at all returns an empty list, not an error."""
    assert animation.select_sample_generations([], max_frames=100) == []


def test_pre_render_frames_matches_select_sample_generations(tmp_path: Path) -> None:
    """The frame count and generation numbers match sampling alone would compute."""
    output = _write_run(tmp_path)
    manifest = read_manifest(output / "manifest.json")
    params = manifest.params()
    trajectory = output / "trajectory.jsonl"

    frames = animation.pre_render_frames(trajectory, params, manifest.run_id)

    expected_generations = animation.select_sample_generations(
        range(manifest.generation + 1)
    )
    assert [frame.generation for frame in frames] == expected_generations
    for frame in frames:
        # d=2 (the config above): one column per deme, at least one row
        # (one persisted locus/allele pair is always present).
        assert frame.points.shape[1] == 2
        assert frame.points.shape[0] >= 1


def test_pre_render_frames_are_sorted_ascending_by_generation(tmp_path: Path) -> None:
    """Frames come back in generation order regardless of trajectory row order."""
    output = _write_run(tmp_path)
    manifest = read_manifest(output / "manifest.json")
    params = manifest.params()
    trajectory = output / "trajectory.jsonl"

    frames = animation.pre_render_frames(trajectory, params, manifest.run_id)

    generations = [frame.generation for frame in frames]
    assert generations == sorted(generations)


def test_pre_render_batch_frames_carries_a_stopped_replicates_state_forward(
    tmp_path: Path,
) -> None:
    """A replicate's own final state still contributes to frames after it stops.

    Batch trajectory panel design `20260912-claude-sonnet-5-batch-
    trajectory-panel-design.md` (`selby/restricted`): the scatter's own
    counterpart to `fim.engine.pooled_convergence_histories`'s identical
    carry-forward choice for the trajectory band -- dropping a
    converged replicate from later frames would pool an ever-shrinking,
    systematically-biased subset (the stragglers), not a representative
    sample, the same real defect that function's own docstring
    describes.

    The earliest-stopping replicate is placed first in `replicates`
    (`pooled_frequency_points` concatenates row-wise, in the given
    order), so its own frozen final block always starts at offset 0 in
    every later frame's own pooled points -- letting this test compare
    that one block directly (an exact array match) rather than
    reasoning about the *whole* pooled array's own row count, which
    otherwise drifts on its own as mutation introduces new alleles
    across generations, unrelated to whether any replicate stopped.
    """
    output = _write_batch_run(tmp_path)
    manifest = read_batch_manifest(output / "manifest.json")
    params = manifest.params()
    replicate_paths = {
        replicate_run_id: (
            batch_runner.replicate_output_directory(
                output, manifest.run_id, replicate_run_id
            )
            / "trajectory.jsonl"
        )
        for replicate_run_id in manifest.replicate_run_ids
    }
    per_replicate_final = {
        replicate_run_id: _final_generation_and_points(
            trajectory_path, replicate_run_id, params
        )
        for replicate_run_id, trajectory_path in replicate_paths.items()
    }
    earliest_run_id = min(
        per_replicate_final, key=lambda run_id: per_replicate_final[run_id][0]
    )
    earliest_final_generation, earliest_final_points = per_replicate_final[
        earliest_run_id
    ]
    other_run_ids = [
        run_id for run_id in manifest.replicate_run_ids if run_id != earliest_run_id
    ]
    # Real precondition for the rest of this test to mean anything: if
    # every replicate stopped together, there is nothing for "carries
    # a value forward" to exercise.
    assert any(
        per_replicate_final[run_id][0] > earliest_final_generation
        for run_id in other_run_ids
    ), (
        "fixture no longer produces staggered stopping generations -- "
        "pick a different seed/tolerance so this test still exercises "
        "the carry-forward case it is named for"
    )
    replicates = [(earliest_run_id, replicate_paths[earliest_run_id])] + [
        (run_id, replicate_paths[run_id]) for run_id in other_run_ids
    ]

    frames = animation.pre_render_batch_frames(replicates, params)

    assert len(frames) > 1
    later_frames = [
        frame for frame in frames if frame.generation > earliest_final_generation
    ]
    assert later_frames, "no sampled frame falls after the earliest replicate stopped"
    for frame in later_frames:
        block = frame.points[: len(earliest_final_points)]
        assert (block == earliest_final_points).all(), (
            f"generation {frame.generation}: the earliest-stopping "
            f"replicate's own block changed after it stopped -- "
            f"carry-forward is not working"
        )


def _final_generation_and_points(
    trajectory_path: Path, run_id: str, params: SimulationParams
) -> tuple[int, np.ndarray]:
    """Return one replicate's own last recorded generation and its `frequency_points`.

    A small, direct (not `pre_render_batch_frames`-derived) computation
    of "what should this replicate's own contribution freeze to" --
    used as the independent oracle `test_pre_render_batch_frames_
    carries_a_stopped_replicates_state_forward` checks the real
    function's own output against.
    """
    grouped = group_rows_by_generation(trajectory_path, run_id)
    final_generation = max(grouped)
    state = ModelState.from_rows(grouped[final_generation], params.loci)
    return final_generation, frequency_points(state)


def test_pre_render_batch_frames_is_empty_for_no_replicates(tmp_path: Path) -> None:
    """No replicates at all is a degenerate, non-erroring input, not a crash."""
    output = _write_batch_run(tmp_path)
    manifest = read_batch_manifest(output / "manifest.json")
    params = manifest.params()

    assert animation.pre_render_batch_frames([], params) == []


def test_animation_module_never_imports_matplotlib() -> None:
    """Direct regression test: no rendering happens on this path.

    A static check of the module's own source, not a runtime
    `sys.modules` check — other test files in this session may have
    already imported `matplotlib` for unrelated reasons, which would
    make a runtime check pass regardless of whether `animation.py`
    itself ever does.
    """
    source = Path(animation.__file__).read_text(encoding="utf-8")
    assert "matplotlib" not in source
