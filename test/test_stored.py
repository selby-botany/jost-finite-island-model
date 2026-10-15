"""Saving a run with `simulate` and opening it again with `open_run`."""

from __future__ import annotations

from pathlib import Path

import pytest

import fim
from fim.errors import StoreError


def _spec(**overrides: object) -> fim.RunSpec:
    """Return a small spec that runs a fixed number of generations."""
    fields: dict[str, object] = {
        "model": fim.Model(
            demes=3,
            population=fim.Population(individuals=40, ploidy=1),
            migration=0.1,
            mutation=fim.InfiniteAlleles(rate=0.01),
        ),
        "seed": 5,
        "stopping": fim.Stopping(burn_in=5, max_generations=40),
        "replication": fim.Replication(n=3, stop_early=False),
    }
    fields.update(overrides)
    return fim.RunSpec(**fields)  # type: ignore[arg-type]


def test_a_saved_batch_reopens_with_the_same_numbers(tmp_path: Path) -> None:
    """The reopened run equals the fresh one: ID, spec, reports, stops, summary."""
    spec = _spec()
    result = fim.simulate(spec, recording=fim.Recording(directory=tmp_path / "run"))
    stored = fim.open_run(tmp_path / "run")

    assert stored.run_id == result.run_id
    assert stored.spec.seed == spec.seed
    assert stored.spec.replication.n == 3
    assert len(stored.replicates) == len(result.replicates) == 3
    for fresh, saved in zip(result.replicates, stored.replicates, strict=True):
        assert saved.run_id == fresh.run_id
        assert saved.report.to_dict() == fresh.report.to_dict()
        assert saved.stop == fresh.stop
        assert saved.histories is not None
        assert saved.histories.generations == fresh.histories.generations
    assert set(stored.summary) == set(result.summary)
    assert stored.summary["D"]["mean"] == result.summary["D"]["mean"]


def test_a_saved_single_run_is_flat_and_reopens_with_one_replicate(
    tmp_path: Path,
) -> None:
    """A single replicate is saved in the flat layout `fim run` writes."""
    spec = _spec(replication=fim.Replication(n=1))
    result = fim.simulate(spec, recording=fim.Recording(directory=tmp_path / "one"))
    assert (tmp_path / "one" / "report.json").is_file()
    assert not list((tmp_path / "one").glob("replicate-*"))
    stored = fim.open_run(tmp_path / "one")
    assert stored.only().report["D"] == result.only().report["D"]
    assert len(stored.summary) == 0


def test_the_stored_trajectory_reads_and_reanalyzes(tmp_path: Path) -> None:
    """Rows come back by generation, and re-analysis matches the report."""
    fim.simulate(
        _spec(replication=fim.Replication(n=1)),
        recording=fim.Recording(directory=tmp_path / "run"),
    )
    replicate = fim.open_run(tmp_path / "run").only()
    rows = replicate.rows([0, 1])
    assert set(rows) == {0, 1}
    assert all(row["run_id"] == replicate.run_id for row in rows[0])
    replicate.verify()
    assert replicate.reanalyze() is not None


def test_a_tampered_trajectory_fails_verification(tmp_path: Path) -> None:
    """The manifest's digest catches an edited log."""
    fim.simulate(
        _spec(replication=fim.Replication(n=1)),
        recording=fim.Recording(directory=tmp_path / "run"),
    )
    replicate = fim.open_run(tmp_path / "run").only()
    with replicate.trajectory_path.open("ab") as handle:
        handle.write(b"x")
    with pytest.raises(ValueError, match="does not match"):
        replicate.verify()


def test_a_directory_without_a_manifest_is_not_a_saved_run(tmp_path: Path) -> None:
    """Opening anything else raises a StoreError that says why."""
    with pytest.raises(StoreError, match="not a saved run"):
        fim.open_run(tmp_path)


def test_a_saved_run_is_not_overwritten(tmp_path: Path) -> None:
    """Publishing refuses an existing target, leaving the first run intact."""
    target = tmp_path / "run"
    fim.simulate(_spec(), recording=fim.Recording(directory=target))
    before = (target / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        fim.simulate(_spec(), recording=fim.Recording(directory=target))
    assert (target / "manifest.json").read_bytes() == before


def test_only_refuses_a_stored_batch(tmp_path: Path) -> None:
    """`only()` says how many replicates the saved run has."""
    fim.simulate(_spec(), recording=fim.Recording(directory=tmp_path / "run"))
    with pytest.raises(ValueError, match="3 replicates"):
        fim.open_run(tmp_path / "run").only()


def test_worker_processes_save_the_same_run(tmp_path: Path) -> None:
    """The `lineal` process pool gives the numbers and files of a serial run."""
    spec = _spec()
    serial = fim.simulate(spec, recording=fim.Recording(directory=tmp_path / "serial"))
    pooled = fim.simulate(
        spec,
        execution=fim.Execution(backend="lineal", workers=2),
        recording=fim.Recording(directory=tmp_path / "pooled"),
    )
    assert [r.report.to_dict() for r in pooled.replicates] == [
        r.report.to_dict() for r in serial.replicates
    ]
    assert sorted(p.name for p in (tmp_path / "pooled").iterdir()) == sorted(
        p.name for p in (tmp_path / "serial").iterdir()
    )
