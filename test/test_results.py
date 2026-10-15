"""The result types a run returns, built from what the engine produces."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from vector_support import loci, make_params

from fim.engine import simulate_params
from fim.engine.identity import deterministic_run_id
from fim.model.params import SimulationParams
from fim.persistence.store import InMemoryTrajectoryStore
from fim.results import Report, Result, result_from_runs
from fim.statistics.catalog import report_keys

EXAMPLES = Path(__file__).resolve().parents[1] / "doc" / "examples"


def _clock() -> datetime:
    """Return a fixed manifest time."""
    return datetime(2026, 10, 15, tzinfo=UTC)


def _result(params: SimulationParams, **keywords: object) -> Result:
    """Run `params` on the engine and wrap the output as a `Result`."""
    store = InMemoryTrajectoryStore()
    output = simulate_params(
        params=params,
        store=store,
        run_id=deterministic_run_id(params),
        clock=_clock,
        **keywords,  # type: ignore[arg-type]
    )
    return result_from_runs(output, run_id=deterministic_run_id(params), params=params)


def _small(**overrides: object) -> SimulationParams:
    """Return a quick configuration that runs a fixed number of generations."""
    return make_params(60, loci=loci(1), d=3, mu=0.02, m=0.1, **overrides)


def test_a_single_replicate_is_a_result_of_length_one() -> None:
    """There is one return shape, whatever the replicate count."""
    result = _result(_small())
    assert len(result.replicates) == 1
    assert result.only() is result.replicates[0]
    assert result.batch_stop == "single"
    assert len(result.summary) == 0


def test_only_refuses_a_batch() -> None:
    """`only()` says how many replicates there were."""
    result = _result(_small(n_replicates=3))
    with pytest.raises(ValueError, match="3 replicates"):
        result.only()


def test_report_is_a_read_only_mapping_of_catalog_statistics() -> None:
    """Report keys are catalog IDs and its dict form is the report.json shape."""
    replicate = _result(_small()).only()
    report = replicate.report
    assert set(report) <= set(report_keys())
    assert {"D", "G_ST", "H_S", "H_T"} <= set(report)
    assert (
        report["D"],
        report["G_ST"],
        report["H_S"],
        report["H_T"],
    ) == (report.D, report.G_ST, report.H_S, report.H_T)
    final = report.to_dict()
    assert final["run_id"] == replicate.run_id
    assert final["generation"] == replicate.stop.generation
    with pytest.raises(TypeError):
        report["D"] = 1.0  # type: ignore[index]


def test_an_unknown_statistic_names_the_valid_ones() -> None:
    """A typo fails with the catalog's IDs in the message."""
    report = _result(_small()).only().report
    with pytest.raises(KeyError, match=r"valid IDs: .*G_ST"):
        report["Dd"]


@pytest.mark.parametrize(
    "report_path",
    sorted(EXAMPLES.glob("*/report.json"))
    + sorted(EXAMPLES.glob("*/replicate-001/report.json")),
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_report_round_trips_every_shipped_example_report(report_path: Path) -> None:
    """Wrapping a committed report.json and writing it back changes nothing."""
    original = json.loads(report_path.read_text(encoding="utf-8"))
    assert Report(original).to_dict() == original


def test_stop_and_histories_describe_the_replicate() -> None:
    """The stop and the aligned histories come from the engine's result."""
    replicate = _result(_small()).only()
    assert replicate.stop.generation == replicate.report.to_dict()["generation"]
    assert replicate.histories.generations
    for name in replicate.histories.names:
        assert len(replicate.histories[name]) == len(replicate.histories.generations)
    assert {"D", "G_ST", "H_S", "H_T"} <= set(replicate.histories.names)


def test_the_trajectory_reader_reads_the_stored_rows() -> None:
    """Rows come back through a reader that has no way to write."""
    replicate = _result(_small()).only()
    rows = list(replicate.trajectory.rows())
    assert rows
    assert {row["run_id"] for row in rows} == {replicate.run_id}
    assert not hasattr(replicate.trajectory, "write_generation")


def test_a_batch_has_replicates_with_their_own_seeds_and_a_summary() -> None:
    """Replicates are seeded from the run's seed; the summary has intervals."""
    params = _small(n_replicates=3, seed=100)
    result = _result(params)
    assert [r.seed for r in result.replicates] == [100, 101, 102]
    assert [r.index for r in result.replicates] == [0, 1, 2]
    assert {"D", "H_S"} <= set(result.summary)
    assert result.summary["D"]["sample_count"] == 3
    assert result.batch_stop == "replicate_cap"


def test_a_batch_that_reaches_its_precision_says_so() -> None:
    """Stopping before the replicate cap is reported as precision reached."""
    params = make_params(
        1200,
        loci=loci(1),
        d=3,
        n_replicates=6,
        mu=0.02,
        m=0.1,
        stop_batch_early=True,
        precision=0.08,
        replicate_minimum=3,
    )
    result = _result(params)
    assert len(result.replicates) < 6
    assert result.batch_stop == "precision_reached"


def test_provenance_records_the_backend_that_ran() -> None:
    """`auto` is resolved to a real backend in the provenance."""
    result = _result(replace(_small(), engine_backend="auto"))
    assert result.provenance.requested.backend == "auto"
    assert result.provenance.backend in {"generational", "generational-vector"}
    assert result.provenance.software_version
    assert result.provenance.started_at <= result.provenance.ended_at
    assert result.provenance.derived["relaxation_time"] > 0
