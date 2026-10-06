"""Complete example artifacts survive lossless archiving and automatic opening."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from fim import cli
from fim import paths as paths_module
from fim.examples.artifacts import (
    RUN_OUTPUT_NAMES,
    copy_outputs,
    materialize_outputs,
    output_files,
)
from fim.gui.app import Api
from fim.persistence import groups
from fim.persistence.manifest import hash_file


@pytest.fixture(autouse=True)
def _isolated_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, log_isolation: None
) -> None:
    """Keep fixture runs out of the user's results index and log."""
    monkeypatch.setattr(paths_module, "results_directory", lambda: tmp_path / "results")


def _saved_run(root: Path, *, replicates: int = 1) -> Path:
    """Produce real artifacts through the same CLI used by the generator."""
    config = root / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "N": 20,
                "d": 4,
                "ploidy": "haploid",
                "m": 0.1,
                "mu": 0.01,
                "seed": 42,
                "loci": [{"locus_id": 1, "length": 5}],
                "mutation_model": "finite_alleles",
                "convergence_window": 11,
                "max_generations": 10,
                "n_replicates": replicates,
                "replicate_tolerance": None,
            }
        ),
        encoding="utf-8",
    )
    output = root / "run"
    assert cli.main(["run", str(config), "--output", str(output), "--quiet"]) == 0
    return output


@pytest.mark.parametrize("replicates", [1, 2])
def test_full_outputs_round_trip_and_open_with_graphs(
    tmp_path: Path, replicates: int
) -> None:
    """Scalar and batch opens reconstruct every byte, with frames and histories."""
    original = _saved_run(tmp_path, replicates=replicates)
    target = tmp_path / "example"
    copied = copy_outputs(original, target, part_bytes=200)
    assert any("trajectory.jsonl.gz.part-0002" in name for name in copied)
    assert all(path.stat().st_size <= 200 for path in target.rglob("*.gz.part-*"))
    api = Api()
    opened = (
        api.open_batch(str(target))
        if replicates > 1
        else api.open_run({"trajectoryPath": str(target / "trajectory.jsonl")})
    )
    assert opened["ok"], opened
    assert not opened.get("reportOnly", False)
    assert opened["panels"]
    frames = (
        api.get_batch_animation_frames(str(target))
        if replicates > 1
        else api.get_animation_frames(str(target))
    )
    assert frames["ok"], frames
    assert len(frames["frames"]) > 1
    for path in output_files(original):
        restored = target / path.relative_to(original)
        assert restored.read_bytes() == path.read_bytes()
    assert materialize_outputs(target) == []


def test_archive_is_deterministic_and_rejects_missing_or_corrupt_parts(
    tmp_path: Path,
) -> None:
    """Fixed gzip headers, bounded parts, and explicit corruption failures."""
    original = _saved_run(tmp_path)
    first, second = tmp_path / "first", tmp_path / "second"
    copy_outputs(original, first, part_bytes=200)
    copy_outputs(original, second, part_bytes=200)
    assert {
        path.relative_to(first): path.read_bytes() for path in output_files(first)
    } == {path.relative_to(second): path.read_bytes() for path in output_files(second)}
    (first / "trajectory.jsonl.gz.part-0001").unlink()
    with pytest.raises(ValueError, match="incomplete example archive"):
        materialize_outputs(first)
    part = second / "trajectory.jsonl.gz.part-0001"
    part.write_bytes(b"not gzip")
    with pytest.raises(OSError):
        materialize_outputs(second)
    assert not (second / "trajectory.jsonl").exists()
    assert not list(second.glob(".trajectory.jsonl-*"))


def test_archive_digest_is_verified_before_publishing(tmp_path: Path) -> None:
    """A valid gzip stream with wrong contents cannot become a completed run."""
    original = _saved_run(tmp_path)
    target = tmp_path / "example"
    copy_outputs(original, target)
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["artifacts"]["trajectory"] = hash_file(original / "report.json")
    manifest_path.write_text(json.dumps(manifest), "utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        materialize_outputs(target)
    assert not (target / "trajectory.jsonl").exists()


def test_truncated_gzip_does_not_publish_a_partial_trajectory(tmp_path: Path) -> None:
    """A missing gzip footer is an explicit failure and leaves no raw output."""
    original = _saved_run(tmp_path)
    target = tmp_path / "example"
    copy_outputs(original, target)
    part = target / "trajectory.jsonl.gz.part-0001"
    part.write_bytes(part.read_bytes()[:-8])
    with pytest.raises(ValueError, match="corrupt example archive"):
        materialize_outputs(target)
    assert not (target / "trajectory.jsonl").exists()
    assert not list(target.glob(".trajectory.jsonl-*"))


def test_artifact_inventory_matches_the_run_producer(tmp_path: Path) -> None:
    """Adding a CLI artifact requires adding it to the example inventory too."""
    assert {path.name for path in cli._run_artifact_targets(tmp_path).values()} == (
        set(RUN_OUTPUT_NAMES)
    )


def test_opening_a_study_restores_its_archived_members(tmp_path: Path) -> None:
    """Opening a Study directly must not require opening its example runs first."""
    original = _saved_run(tmp_path)
    target = tmp_path / "example"
    copy_outputs(original, target)
    study = groups.create_study("Archived examples")
    groups.add_run_to_study(study.study_id, target)
    opened = Api().open_study(study.study_id)
    assert opened["ok"], opened
    assert opened["panels"]
    assert (target / "trajectory.jsonl").read_bytes() == (
        original / "trajectory.jsonl"
    ).read_bytes()
