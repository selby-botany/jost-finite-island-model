"""`fim run` leaves no trajectory file open (the kept-open handle leak check).

`JSONLTrajectoryStore` keeps its append handle open between generations,
so every owner must release it before `fim.paths.atomic_directory` renames
(or, on failure, removes) the run directory; an open handle blocks both on
Windows. These tests run the real command in this process and assert,
through `conftest.tracked_jsonl_stores`, that no store it built is still
open afterwards.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from conftest import assert_none_open

from fim import cli
from fim.persistence.jsonl_store import (
    EQUILIBRIUM_TRAJECTORY_FILENAME,
    JSONLTrajectoryStore,
)


def _write_config(path: Path, **updates: object) -> None:
    """Write a tiny deterministic YAML configuration."""
    config: dict[str, object] = {
        "N": 20,
        "ploidy": "haploid",
        "d": 2,
        "m": 0.1,
        "mu": 0.01,
        "seed": 20260814,
        "loci": [{"locus_id": 1, "length": 200}],
        "initial_allele_count": 2,
        "initial_concentration": 1.0,
        "deme_weighting": "size",
        "convergence_statistic": "D",
        "convergence_window": 4,
        "convergence_tolerance": 1.0,
        "max_generations": 10,
        "n_replicates": 1,
        "replicate_tolerance": None,
    }
    config.update(updates)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def test_scalar_run_closes_its_trajectory_and_ancestral_handles(
    tmp_path: Path, tracked_jsonl_stores: list[JSONLTrajectoryStore]
) -> None:
    """A scalar `fim run` closes both stores of an equilibrium-split run."""
    config = tmp_path / "run.yaml"
    output = tmp_path / "output"
    _write_config(
        config,
        equilibrium_convergence_window=2,
        equilibrium_convergence_tolerance=0.5,
        equilibrium_max_generations=200,
    )

    status = cli.main(["run", str(config), "--output", str(output), "--quiet"])

    assert status == 0
    # The main store and its ancestral-phase companion, both checked.
    assert len(tracked_jsonl_stores) == 2
    assert_none_open(tracked_jsonl_stores)
    assert (output / "trajectory.jsonl").read_text().strip()
    assert (output / EQUILIBRIUM_TRAJECTORY_FILENAME).read_text().strip()


@pytest.mark.parametrize(
    ("backend", "flags"),
    [
        ("lineal", ["--sequential"]),
        ("generational", []),
    ],
)
def test_batch_run_closes_every_replicates_handles(
    tmp_path: Path,
    tracked_jsonl_stores: list[JSONLTrajectoryStore],
    backend: str,
    flags: list[str],
) -> None:
    """A `fim run` batch closes every replicate's handles on each engine path.

    A `--workers` batch builds its stores in worker processes, which this
    process cannot see; those close in `fim.engine._run_one`, which the
    engine-level tests in `test/persistence/test_jsonl_lifecycle.py` cover.
    """
    config = tmp_path / "run.yaml"
    output = tmp_path / "output"
    _write_config(config, n_replicates=3, engine_backend=backend)

    status = cli.main(["run", str(config), "-o", str(output), "--quiet", *flags])

    assert status == 0
    assert len(tracked_jsonl_stores) == 3
    assert_none_open(tracked_jsonl_stores)
    for replicate in ("replicate-001", "replicate-002", "replicate-003"):
        assert (output / replicate / "trajectory.jsonl").read_text().strip()
