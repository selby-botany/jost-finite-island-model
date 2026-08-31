"""Unit tests for `fim.gui.animation`.

No display, no Tk import, no Matplotlib import at all — `pre_render_frames`
returns plain coordinate data (`doc/fim-gui-design.md` §8), not rendered
`Figure` objects, so nothing here carries the `gui` marker.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from fim import cli
from fim.gui import animation
from fim.persistence.manifest import read_manifest


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
    }
    config.update(overrides)
    config_path = tmp_path / "run.yaml"
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
