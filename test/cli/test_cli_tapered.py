"""A tapered run end to end: the log is cut down when the replicate finishes.

The run is the CLI's own, so the whole path is exercised: the engine writes
every frame, `write_run_artifacts` tapers the log before it takes the
digests, and the readers (`fim stats`, integrity, the run id) see a run that
is whole but has gaps in its generations.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from conftest import FAST_EXPERT_SETTINGS

from fim import cli
from fim.model.params import SimulationParams
from fim.persistence.manifest import read_manifest
from fim.persistence.retention import tapered_generations
from fim.persistence.tlog import iter_frames

_BASE: dict[str, object] = {
    "N": 20,
    "ploidy": "haploid",
    "d": 2,
    "m": 0.1,
    "mu": 0.01,
    "seed": 20260814,
    "loci": [{"locus_id": 1, "length": 200}],
    "convergence_statistic": "D",
    "precision": 1.0,
    "convergence_burn_in": 10,
    "expert": dict(FAST_EXPERT_SETTINGS),
    "max_generations": 80,
    "n_replicates": 1,
    "stop_batch_early": False,
}
_TAPERED: dict[str, object] = {
    "trajectory_retention": "tapered",
    "trajectory_keep_last": 6,
}


def _run(tmp_path: Path, name: str, **extra: object) -> Path:
    config = tmp_path / f"{name}.yaml"
    config.write_text(yaml.safe_dump({**_BASE, **extra}), encoding="utf-8")
    output = tmp_path / name
    assert cli.main(["run", str(config), "--output", str(output), "--quiet"]) == 0
    return output


def test_a_tapered_run_keeps_the_frames_the_rule_names(tmp_path: Path) -> None:
    output = _run(tmp_path, "tapered", **_TAPERED)

    manifest = read_manifest(output / "manifest.json")
    held = [frame.generation for frame in iter_frames(output / "trajectory.tlog")]
    final = manifest.generation
    assert held == tapered_generations(final, 6, 10)
    assert held[0] == 0 and 10 in held and held[-1] == final
    assert len(held) < final + 1


def test_a_tapered_runs_log_is_smaller_and_its_digest_is_recorded(
    tmp_path: Path,
) -> None:
    full = _run(tmp_path, "full")
    tapered = _run(tmp_path, "tapered", **_TAPERED)

    assert (tapered / "trajectory.tlog").stat().st_size < (
        full / "trajectory.tlog"
    ).stat().st_size
    from fim.persistence.manifest import hash_file  # noqa: PLC0415

    recorded = read_manifest(tapered / "manifest.json").artifacts
    assert recorded is not None
    assert recorded["trajectory"] == hash_file(tapered / "trajectory.tlog")


def test_tapering_does_not_change_the_run_id_or_the_statistics(
    tmp_path: Path,
) -> None:
    full = _run(tmp_path, "full")
    tapered = _run(tmp_path, "tapered", **_TAPERED)

    assert (
        read_manifest(full / "manifest.json").run_id
        == read_manifest(tapered / "manifest.json").run_id
    )
    assert (full / "report.json").read_bytes() == (tapered / "report.json").read_bytes()
    assert (full / "convergence.jsonl").read_bytes() == (
        tapered / "convergence.jsonl"
    ).read_bytes()
    parameters = read_manifest(tapered / "manifest.json").parameters
    assert parameters["trajectory_retention"] == "tapered"
    assert (
        "trajectory_retention" not in read_manifest(full / "manifest.json").parameters
    )


def test_stats_reads_the_final_generation_and_names_a_dropped_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = _run(tmp_path, "tapered", **_TAPERED)
    final = read_manifest(output / "manifest.json").generation
    dropped = next(
        g for g in range(1, final) if g not in tapered_generations(final, 6, 10)
    )

    assert cli.main(["stats", str(output / "trajectory.tlog")]) == 0
    capsys.readouterr()
    status = cli.main(
        ["stats", str(output / "trajectory.tlog"), "--generation", str(dropped)]
    )

    assert status != 0
    assert "tapered" in capsys.readouterr().err


def test_the_settings_are_validated_and_do_not_change_identity() -> None:
    plain = SimulationParams.from_mapping({**_BASE})
    tapered = SimulationParams.from_mapping({**_BASE, **_TAPERED})

    assert tapered.trajectory_retention == "tapered"
    assert tapered.identity_dict() == plain.identity_dict()
    with pytest.raises(ValueError, match="trajectory_keep_last"):
        SimulationParams.from_mapping({**_BASE, "trajectory_keep_last": 0})
    with pytest.raises(ValueError, match="trajectory_retention"):
        SimulationParams.from_mapping({**_BASE, "trajectory_retention": "sparse"})
    with pytest.raises(ValueError, match="trajectory_keep_burn_in"):
        SimulationParams.from_mapping({**_BASE, "trajectory_keep_burn_in": "yes"})
