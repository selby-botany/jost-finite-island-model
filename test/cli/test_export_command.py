"""`fim export` and `export_trajectory`: the explicit JSON Lines export.

The export is the one producer of `trajectory.jsonl`, so its guarantees are
tested end to end on real runs made by `fim run`: the file is the canonical
bytes (equal to the plain `json.dumps` text of the same run's rows), the log is
checked against its manifest first, a full disk and an existing file are
refused with clear messages, nothing partial is left behind, and a receipt
records both SHA-256 digests.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml
from tlog_support import canonical_jsonl

from fim import cli
from fim.engine import fim
from fim.model.params import SimulationParams
from fim.persistence.store import InMemoryTrajectoryStore
from fim.persistence.tlog_export import RECEIPT_SCHEMA, export_trajectory, resolve_log


def _config(path: Path, **updates: object) -> None:
    """Write a tiny deterministic configuration."""
    config: dict[str, object] = {
        "N": 30,
        "ploidy": "haploid",
        "d": 3,
        "m": 0.1,
        "mu": 0.02,
        "seed": 20261009,
        "loci": [{"locus_id": 1, "length": 200}, {"locus_id": 2, "length": 200}],
        "initial_allele_count": 3,
        "convergence_window": 4,
        "convergence_tolerance": 1e-12,
        "max_generations": 30,
        "n_replicates": 1,
        "replicate_tolerance": None,
    }
    config.update(updates)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _run(tmp_path: Path, **updates: object) -> Path:
    """Run `fim run` and return the run directory."""
    config = tmp_path / "run.yaml"
    _config(config, **updates)
    output = tmp_path / "run"
    assert cli.main(["run", str(config), "-o", str(output), "--quiet"]) == 0
    return output


def _reference_jsonl(tmp_path: Path, **updates: object) -> bytes:
    """The plain `json.dumps` text of the rows of the same configuration."""
    config = tmp_path / "ref.yaml"
    _config(config, **updates)
    params = SimulationParams.from_mapping(yaml.safe_load(config.read_text()))
    store = InMemoryTrajectoryStore()
    result = fim(
        params.gene_copies,
        params.m,
        params.mu,
        params.d,
        params=params,
        store=store,
        run_id=json.loads((tmp_path / "run" / "manifest.json").read_text())["run_id"],
    )
    assert not isinstance(result, tuple)
    return canonical_jsonl(store.read(result.run_id))


def test_export_writes_the_canonical_file_and_a_receipt(tmp_path: Path) -> None:
    """The exported file is the canonical text; the receipt says so."""
    run = _run(tmp_path)
    receipt = export_trajectory(run)
    reference = _reference_jsonl(tmp_path)
    assert receipt.output == run / "trajectory.jsonl"
    assert receipt.output.read_bytes() == reference
    assert receipt.sha256 == hashlib.sha256(reference).hexdigest()
    assert receipt.size == len(reference)
    document = json.loads(receipt.receipt.read_text(encoding="utf-8"))
    assert document["schema"] == RECEIPT_SCHEMA
    assert document["jsonl"]["sha256"] == receipt.sha256
    assert document["source"]["file"] == "trajectory.tlog"
    assert (
        document["source"]["sha256"]
        == json.loads((run / "manifest.json").read_text())["artifacts"]["trajectory"][
            "sha256"
        ]
    )
    assert document["jsonl"]["rows"] == document["source"]["rows"] == receipt.rows
    assert not list(run.glob("*.partial"))


def test_the_command_exports_a_run_directory_or_a_log_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`fim export` takes either, prints the digest and honors -o and --workers."""
    run = _run(tmp_path)
    assert cli.main(["export", str(run)]) == 0
    printed = capsys.readouterr().out
    assert "Exported" in printed
    assert (
        hashlib.sha256((run / "trajectory.jsonl").read_bytes()).hexdigest() in printed
    )
    out = tmp_path / "elsewhere" / "t.jsonl"
    assert (
        cli.main(
            ["export", str(run / "trajectory.tlog"), "-o", str(out), "--workers", "1"]
        )
        == 0
    )
    assert out.read_bytes() == (run / "trajectory.jsonl").read_bytes()
    assert out.with_name("t.jsonl.export.json").is_file()


def test_the_export_of_an_equilibrium_split_run_covers_both_logs(
    tmp_path: Path,
) -> None:
    """The ancestral log exports to its own file, checked against its own digest."""
    run = _run(
        tmp_path,
        equilibrium_convergence_window=2,
        equilibrium_convergence_tolerance=0.5,
        equilibrium_max_generations=200,
    )
    main = export_trajectory(run)
    ancestral = export_trajectory(run / "equilibrium_trajectory.tlog")
    assert ancestral.output.name == "equilibrium_trajectory.jsonl"
    assert main.rows > 0
    assert ancestral.rows > 0
    assert ancestral.sha256 == hashlib.sha256(ancestral.output.read_bytes()).hexdigest()


def test_an_existing_output_is_refused_unless_forced(tmp_path: Path) -> None:
    """Nothing is overwritten by accident."""
    run = _run(tmp_path)
    export_trajectory(run)
    with pytest.raises(FileExistsError, match="already exists"):
        export_trajectory(run)
    assert cli.main(["export", str(run)]) == 2
    assert cli.main(["export", str(run), "--force"]) == 0
    export_trajectory(run, overwrite=True)


def test_a_log_that_no_longer_matches_its_manifest_is_not_exported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A tampered log fails the manifest digest before anything is written."""
    run = _run(tmp_path)
    log = run / "trajectory.tlog"
    damaged = bytearray(log.read_bytes())
    damaged[len(damaged) // 2] ^= 0x01
    log.write_bytes(bytes(damaged))
    with pytest.raises(ValueError, match="does not match its manifest"):
        export_trajectory(run)
    assert cli.main(["export", str(run)]) == 2
    assert "does not match its manifest" in capsys.readouterr().err
    assert not (run / "trajectory.jsonl").exists()


def test_a_full_disk_is_reported_before_anything_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact size is known first, so a too-small disk is refused up front."""
    run = _run(tmp_path)
    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda _path: shutil._ntuple_diskusage(10**9, 10**9 - 5, 5),
    )
    with pytest.raises(OSError, match="not enough free space"):
        export_trajectory(run)
    assert not (run / "trajectory.jsonl").exists()
    assert not list(run.glob("*.partial"))


def test_a_missing_log_is_a_clear_error(tmp_path: Path) -> None:
    """A directory with no log, and a path that does not exist, are refused."""
    with pytest.raises(FileNotFoundError):
        resolve_log(tmp_path)
    assert cli.main(["export", str(tmp_path / "nope")]) == 2


def test_a_log_without_a_manifest_still_exports(tmp_path: Path) -> None:
    """The manifest check applies when there is a manifest, not otherwise."""
    run = _run(tmp_path)
    loose = tmp_path / "loose"
    loose.mkdir()
    shutil.copy(run / "trajectory.tlog", loose / "trajectory.tlog")
    receipt = export_trajectory(loose)
    assert receipt.output.read_bytes() == (_reference_jsonl(tmp_path))
