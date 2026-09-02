"""Unit tests for `fim.gui.recent_runs`.

No display, no Tk import — this module builds no widgets, so none of
these tests carry the `gui` marker. Every fixture manifest is a real
one, written by a real `cli.main(["run", ...])` call — the same
guarantee `test/cli/test_cli.py` gives every other manifest-reading
test in this project — rather than a hand-constructed
`RunManifest`/`BatchManifest`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from fim import cli, paths
from fim.gui import recent_runs


def _write_run(tmp_path: Path, name: str, **overrides: object) -> Path:
    """Write a tiny deterministic config, run it under `name`, return its directory."""
    config: dict[str, object] = {
        "N": 20,
        "d": 2,
        "m": 0.1,
        "mu": 0.01,
        "seed": 1,
        "loci": [{"locus_id": 1, "length": 200}],
        "convergence_window": 4,
        "convergence_tolerance": 1.0,
        "max_generations": 5,
        "n_replicates": 1,
        "replicate_tolerance": None,
    }
    config.update(overrides)
    config_path = tmp_path / f"{name}.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    output_directory = tmp_path / "results" / name
    arguments = ["run", str(config_path), "-o", str(output_directory), "--quiet"]
    if config.get("n_replicates", 1) != 1:
        arguments.append("--sequential")
    assert cli.main(arguments) == 0
    return output_directory


def test_recent_runs_lists_manifests_newest_first(tmp_path: Path) -> None:
    """Design doc's own named test: results come back newest `ended_at` first."""
    first = _write_run(tmp_path, "first", seed=1)
    second = _write_run(tmp_path, "second", seed=2)

    # Force an unambiguous ordering independent of how fast the two runs
    # above actually completed (both can legitimately finish within the
    # same wall-clock millisecond on a fast machine) — this test is about
    # `list_recent_runs`'s *sort*, not about real elapsed time, so the
    # manifests' own `ended_at` fields are rewritten directly rather than
    # relied upon to already differ.
    _rewrite_ended_at(first / "manifest.json", "2026-01-01T00:00:00Z")
    _rewrite_ended_at(second / "manifest.json", "2026-01-02T00:00:00Z")

    runs = recent_runs.list_recent_runs(tmp_path / "results")

    assert [run.directory for run in runs] == [second, first]


def test_recent_runs_labels_batch_manifests_as_batch(tmp_path: Path) -> None:
    """A batch manifest is listed, labeled distinctly, not offered for direct opening.

    Not offered to Screen 3 directly (`doc/fim-gui-design.md` §9) —
    `is_batch` is the flag Screen 6 uses to refuse opening it the same
    way a scalar run is opened.
    """
    scalar = _write_run(tmp_path, "scalar", seed=1)
    batch = _write_run(tmp_path, "batch", seed=2, n_replicates=3)
    _rewrite_ended_at(scalar / "manifest.json", "2026-01-01T00:00:00Z")
    _rewrite_ended_at(batch / "manifest.json", "2026-01-02T00:00:00Z")

    runs = recent_runs.list_recent_runs(tmp_path / "results")

    assert [run.directory for run in runs] == [batch, scalar]
    assert runs[0].is_batch is True
    assert runs[0].label == "batch (3/3)"
    assert runs[1].is_batch is False
    assert runs[1].label == "statistic converged"


def test_recent_runs_skips_an_unparseable_manifest(tmp_path: Path) -> None:
    """A malformed manifest.json is skipped, not fatal to the whole scan."""
    valid = _write_run(tmp_path, "valid")
    broken = tmp_path / "results" / "broken"
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text(
        json.dumps({"not": "a valid manifest"}), encoding="utf-8"
    )

    runs = recent_runs.list_recent_runs(tmp_path / "results")

    assert [run.directory for run in runs] == [valid]


def test_recent_runs_returns_empty_for_a_missing_directory(tmp_path: Path) -> None:
    """A `results_directory` that does not exist yet is not an error."""
    assert recent_runs.list_recent_runs(tmp_path / "no-such-directory") == []


def test_recent_runs_returns_empty_for_an_empty_directory(tmp_path: Path) -> None:
    """An existing but empty `results_directory` returns no runs."""
    empty = tmp_path / "results"
    empty.mkdir()

    assert recent_runs.list_recent_runs(empty) == []


def test_recent_runs_defaults_to_paths_results_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting `results_directory` resolves through `fim.paths.results_directory`."""
    monkeypatch.setattr(paths, "project_root", lambda: tmp_path)
    _write_run(tmp_path, "only")

    runs = recent_runs.list_recent_runs()

    assert len(runs) == 1
    assert runs[0].run_id


def _rewrite_ended_at(manifest_path: Path, ended_at: str) -> None:
    """Patch one manifest's `ended_at` field in place, for deterministic ordering."""
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["ended_at"] = ended_at
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
