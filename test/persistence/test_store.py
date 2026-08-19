"""Tests for incremental trajectory and manifest persistence."""

from pathlib import Path

from fim.model.allele import AlleleId
from fim.model.locus import LocusSpec
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.persistence.jsonl_store import JSONLTrajectoryStore
from fim.persistence.manifest import RunManifest, read_manifest, write_manifest
from fim.persistence.store import InMemoryTrajectoryStore


def _state(generation: int) -> ModelState:
    """Return one sparse state for persistence tests."""
    return ModelState(
        loci=(LocusSpec(1, 200),),
        frequencies=(
            ({AlleleId(0): 0.25, AlleleId(1): 0.75},),
            ({AlleleId(0): 1.0},),
        ),
        generation=generation,
    )


def test_in_memory_store_round_trips_rows() -> None:
    """The protocol contract preserves every public-schema field."""
    store = InMemoryTrajectoryStore()
    rows = _state(0).to_rows("run-a")

    store.write_generation("run-a", 0, rows)

    assert list(store.read("run-a")) == rows


def test_jsonl_store_appends_generations_and_ignores_partial_tail(
    tmp_path: Path,
) -> None:
    """Every complete flushed row remains readable after interruption."""
    path = tmp_path / "trajectory.jsonl"
    store = JSONLTrajectoryStore(path)
    for generation in range(2):
        state = _state(generation)
        store.write_generation("run-a", generation, state.to_rows("run-a"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"run_id":"run-a"')

    rows = list(store.read("run-a"))

    assert {row["generation"] for row in rows} == {0, 1}
    assert len(rows) == 6


def test_manifest_round_trip_reconstructs_parameters(tmp_path: Path) -> None:
    """A saved manifest contains a lossless replay configuration."""
    params = SimulationParams(
        N=20,
        m=0.1,
        mu=0.001,
        d=2,
        seed=7,
        loci=(LocusSpec(1, 200),),
    )
    manifest = RunManifest(
        schema_version=1,
        run_id="run-a",
        parameters=params.to_dict(),
        started_at="2026-08-14T20:00:00Z",
        ended_at="2026-08-14T20:00:01Z",
        converged=True,
        convergence_statistic="D",
        stop_reason="statistic converged",
        generation=4,
        generation_count=5,
        software_version="1.0.0",
    )
    path = tmp_path / "manifest.json"

    write_manifest(path, manifest)
    restored = read_manifest(path)

    assert restored == manifest
    assert restored.params() == params


def test_manifest_round_trip_reconstructs_several_convergence_statistics(
    tmp_path: Path,
) -> None:
    """A manifest watching several statistics is a lossless replay too."""
    params = SimulationParams(
        N=20,
        m=0.1,
        mu=0.001,
        d=2,
        seed=7,
        loci=(LocusSpec(1, 200),),
        convergence_statistic=("D", "G_ST"),
        convergence_combinator="any",
    )
    manifest = RunManifest(
        schema_version=1,
        run_id="run-a",
        parameters=params.to_dict(),
        started_at="2026-08-14T20:00:00Z",
        ended_at="2026-08-14T20:00:01Z",
        converged=True,
        convergence_statistic=("D", "G_ST"),
        stop_reason="statistic converged",
        generation=4,
        generation_count=5,
        software_version="1.0.0",
    )
    path = tmp_path / "manifest.json"

    write_manifest(path, manifest)
    restored = read_manifest(path)

    assert restored == manifest
    assert restored.convergence_statistic == ("D", "G_ST")
    assert restored.params() == params
