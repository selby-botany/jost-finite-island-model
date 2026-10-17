"""Tests for `fim.sweep_table`: point cells, health, and the health cache.

Health is derived from the manifests a run already wrote, so most tests build
those few files by hand: no simulation runs, and the result depends only on
the files.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from fim import paths
from fim.persistence import groups
from fim.sweep import SweepSpec, enumerate_points, expand_axis
from fim.sweep_run import (
    create_sweep_study,
    health_cache_path,
    run_sweep,
    sweep_point_results,
)
from fim.sweep_table import (
    DEFAULT_HEALTH_POLICY,
    HealthCache,
    HealthPolicy,
    PointHealth,
    StatisticCell,
    read_point_health,
)

CAP = "hit the cap"


def _convergence(
    *, converged: bool, reason: str, watched: tuple[str, ...] = ("D", "G_ST")
) -> dict[str, Any]:
    """A replicate manifest's convergence record."""
    return {
        "converged": converged,
        "generation": 10,
        "generation_count": 11,
        "reason": reason,
        "statistic": list(watched),
    }


def _write(path: Path, payload: object) -> None:
    """Write `payload` as JSON, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _batch(
    directory: Path,
    outcomes: list[tuple[bool, str]],
    *,
    half_width: float = 0.001,
    precision: float = 0.01,
) -> Path:
    """Build a batch run directory whose replicates ended as `outcomes`."""
    _write(
        directory / "manifest.json",
        {"run_id": directory.name, "parameters": {"precision": precision}},
    )
    for number, (converged, reason) in enumerate(outcomes, start=1):
        _write(
            directory / f"replicate-{number:03d}" / "manifest.json",
            {"convergence": _convergence(converged=converged, reason=reason)},
        )
    _write(
        directory / "summary.json",
        {"D": {"mean": 0.1, "half_width": half_width, "sample_count": len(outcomes)}},
    )
    return directory


@pytest.fixture
def results(tmp_path: Path) -> Iterator[Path]:
    """Point the results directory at a temporary one for the test."""
    root = tmp_path / "results"
    root.mkdir()
    paths.set_results_directory_override(root)
    try:
        yield root
    finally:
        paths.set_results_directory_override(None)


def test_all_settled_within_precision_is_healthy(tmp_path: Path) -> None:
    run = _batch(tmp_path / "run-a", [(True, "statistic converged")] * 3)

    health = read_point_health(run)

    assert health is not None
    assert (health.replicates, health.converged, health.hit_cap) == (3, 3, 0)
    assert health.classify() == "healthy"


def test_some_capped_replicates_make_the_point_degraded(tmp_path: Path) -> None:
    run = _batch(
        tmp_path / "run-b", [(True, "statistic converged"), (False, CAP), (True, "x")]
    )

    health = read_point_health(run)

    assert health is not None
    assert (health.converged, health.hit_cap, health.other) == (2, 1, 0)
    assert health.classify() == "degraded"


def test_every_replicate_capped_is_unreliable(tmp_path: Path) -> None:
    run = _batch(tmp_path / "run-c", [(False, CAP)] * 3)

    health = read_point_health(run)

    assert health is not None
    assert health.classify() == "unreliable"


def test_an_interval_wider_than_the_precision_is_degraded(tmp_path: Path) -> None:
    run = _batch(
        tmp_path / "run-d",
        [(True, "averaging window complete")] * 2,
        half_width=0.05,
        precision=0.01,
    )

    health = read_point_health(run)

    assert health is not None
    assert health.achieved_half_width == {"D": 0.05}
    assert health.classify() == "degraded"
    assert health.classify(HealthPolicy(precision_slack=10.0)) == "healthy"


def test_a_replicate_that_ended_another_way_is_degraded_not_healthy(
    tmp_path: Path,
) -> None:
    run = _batch(tmp_path / "run-e", [(True, "x"), (False, "cancelled")])

    health = read_point_health(run)

    assert health is not None
    assert (health.converged, health.hit_cap, health.other) == (1, 0, 1)
    assert health.classify() == "degraded"


def test_a_single_run_reads_its_own_manifest(tmp_path: Path) -> None:
    run = tmp_path / "run-f"
    _write(
        run / "manifest.json",
        {
            "run_id": "run-f",
            "parameters": {"precision": 0.01},
            "convergence": _convergence(converged=False, reason=CAP),
        },
    )

    health = read_point_health(run)

    assert health is not None
    assert (health.replicates, health.hit_cap) == (1, 1)
    assert health.achieved_half_width == {}
    assert health.classify() == "unreliable"


def test_an_unreadable_run_has_no_health(tmp_path: Path) -> None:
    assert read_point_health(tmp_path / "missing") is None
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "manifest.json").write_text("{not json", encoding="utf-8")
    assert read_point_health(broken) is None


def test_the_cache_gives_the_same_answer_and_is_rebuilt_when_deleted(
    tmp_path: Path,
) -> None:
    run = _batch(tmp_path / "run-g", [(True, "x"), (False, CAP)])
    path = tmp_path / "health.json"
    cache = HealthCache(path)
    uncached = read_point_health(run)

    first = read_point_health(run, cache=cache)
    cache.save()
    second = read_point_health(run, cache=HealthCache(path))
    path.unlink()
    rebuilt = read_point_health(run, cache=HealthCache(path))

    assert first == second == rebuilt == uncached


def test_a_cache_entry_is_not_trusted_after_the_run_changes(tmp_path: Path) -> None:
    run = _batch(tmp_path / "run-h", [(True, "x"), (True, "x")])
    path = tmp_path / "health.json"
    cache = HealthCache(path)
    read_point_health(run, cache=cache)
    cache.save()
    # The manifest changes (the run was recomputed): its digest no longer
    # matches, so the replicates are read again.
    _write(
        run / "manifest.json", {"run_id": run.name, "parameters": {"precision": 0.5}}
    )
    _write(
        run / "replicate-001" / "manifest.json",
        {"convergence": _convergence(converged=False, reason=CAP)},
    )

    health = read_point_health(run, cache=HealthCache(path))

    assert health is not None
    assert (health.converged, health.hit_cap) == (1, 1)


@pytest.mark.parametrize("content", ["not json", "[1, 2]", '{"run-i": 5}', ""])
def test_a_damaged_cache_file_is_an_empty_cache(tmp_path: Path, content: str) -> None:
    run = _batch(tmp_path / "run-i", [(True, "x")])
    path = tmp_path / "health.json"
    path.write_text(content, encoding="utf-8")

    health = read_point_health(run, cache=HealthCache(path))

    assert health is not None
    assert health.converged == 1


def test_a_cache_that_cannot_be_written_is_not_an_error(tmp_path: Path) -> None:
    run = _batch(tmp_path / "run-j", [(True, "x")])
    blocked = tmp_path / "blocked"
    blocked.write_text("a file where the cache's folder should be", encoding="utf-8")
    cache = HealthCache(blocked / "health.json")

    assert read_point_health(run, cache=cache) is not None
    cache.save()


def test_the_default_policy_asks_for_the_requested_precision() -> None:
    assert DEFAULT_HEALTH_POLICY.precision_slack == 1.0
    exact = PointHealth(2, 2, 0, 0, {"D": 0.01}, 0.01)
    assert exact.classify() == "healthy"
    assert PointHealth(2, 2, 0, 0, {"D": 0.0101}, 0.01).classify() == "degraded"
    assert PointHealth(0, 0, 0, 0).classify() == "unreliable"


def test_a_cell_keeps_every_field_of_a_summary_entry() -> None:
    cell = StatisticCell.from_summary_entry(
        {
            "mean": 0.5,
            "low": 0.4,
            "high": 0.6,
            "half_width": 0.1,
            "sample_std": 0.05,
            "sample_count": 4,
            "confidence": 0.95,
        }
    )

    assert cell == StatisticCell(0.5, 0.4, 0.6, 0.1, 0.05, 4, 0.95)


def test_a_cell_without_an_interval_has_none_for_it() -> None:
    cell = StatisticCell.from_summary_entry({"mean": 0.5, "sample_count": 1})

    assert (cell.low, cell.high, cell.half_width, cell.confidence) == (
        None,
        None,
        None,
        None,
    )


def test_a_sweep_reads_back_rows_with_provenance_and_health(results: Path) -> None:
    spec = SweepSpec(
        base={
            "N": 16,
            "ploidy": "diploid",
            "d": 2,
            "m": 0.1,
            "mu": 0.01,
            "seed": 7,
            "n_replicates": 2,
            "max_generations": 10,
        },
        axes=(expand_axis("d", [2, 3]),),
    )
    study_id = create_sweep_study(spec, enumerate_points(spec), "Rows").study_id
    run_sweep(
        study_id, _local_runner(), lambda _e: None, _no_cancel(), points_at_once=1
    )

    rows = sweep_point_results(groups.get_study(study_id))

    assert [row.coordinates for row in rows] == [{"d": 2}, {"d": 3}]
    first = rows[0]
    assert first.state == "done"
    assert first.seed == 7
    assert first.n_replicates == 2
    assert first.engine_backend is not None
    assert first.software_version is not None
    assert first.started_at is not None
    assert first.parameters["N"] == 16
    assert first.statistics["D"].sample_count == 2
    assert first.statistics["D"].half_width is not None
    # The tiny runs stop at the generation cap, so the point is flagged.
    assert first.health is not None
    assert first.health.hit_cap == 2
    assert first.health.classify() == "unreliable"
    assert health_cache_path(study_id).is_file()
    health_cache_path(study_id).unlink()
    assert sweep_point_results(groups.get_study(study_id)) == rows


def _local_runner() -> Any:
    """A `LocalPointRunner`, imported here to keep the test's imports short."""
    from fim.sweep_run import LocalPointRunner  # noqa: PLC0415

    return LocalPointRunner()


def _no_cancel() -> Any:
    """An unset cancel event."""
    import threading  # noqa: PLC0415

    return threading.Event()
