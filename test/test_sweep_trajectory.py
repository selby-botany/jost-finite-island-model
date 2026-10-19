"""Tests for `fim.sweep_trajectory`: the fixed-size view of a point's history.

The histories are built by hand (a few JSON lines and a manifest per
replicate), so no simulation runs and every figure is known in advance.
"""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from fim.persistence.history import iter_history_rows
from fim.sweep_trajectory import (
    MAX_SAMPLES,
    PointTrajectory,
    TrajectoryCache,
    point_trajectory,
    trajectory_grid,
)


def _replicate(
    folder: Path,
    values: Callable[[int], float],
    final: int,
    *,
    digest: str = "d0",
    burn_in: int | None = None,
) -> None:
    """Write one replicate: `values(g)` for D at each generation 0..final."""
    folder.mkdir(parents=True, exist_ok=True)
    rows = "".join(
        json.dumps({"generation": g, "D": values(g), "H_S": 0.5}) + "\n"
        for g in range(final + 1)
    )
    (folder / "convergence.jsonl").write_text(rows, encoding="utf-8")
    parameters: dict[str, Any] = {"confidence": 0.95}
    if burn_in is not None:
        parameters["convergence_burn_in"] = burn_in
    (folder / "manifest.json").write_text(
        json.dumps(
            {
                "parameters": parameters,
                "convergence": {"generation": final},
                "artifacts": {"convergence": {"sha256": digest, "bytes": 1}},
            }
        ),
        encoding="utf-8",
    )


def _batch(
    root: Path, runs: Sequence[tuple[Callable[[int], float], int]], **options: Any
) -> Path:
    """Build a batch directory with one replicate per `(values, final)`."""
    for number, (values, final) in enumerate(runs, start=1):
        _replicate(root / f"replicate-{number:03d}", values, final, **options)
    return root


# --- the sampling grid ------------------------------------------------------


@pytest.mark.parametrize("final", [0, 1, 7, 511, 512, 513, 10_000, 2_600_000])
def test_the_grid_starts_at_zero_ends_at_the_final_generation_and_is_capped(
    final: int,
) -> None:
    grid = trajectory_grid(final)

    assert grid[0] == 0 and grid[-1] == final
    assert list(grid) == sorted(set(grid))
    assert len(grid) <= MAX_SAMPLES


def test_a_short_run_is_sampled_at_every_generation() -> None:
    assert trajectory_grid(100) == tuple(range(101))


def test_a_long_run_shows_the_early_transient_and_the_plateau() -> None:
    grid = trajectory_grid(1_000_000)

    assert {1, 2, 3} <= set(grid)  # logarithmic spacing resolves the start
    assert any(90 <= g <= 110 for g in grid)
    assert any(900 <= g <= 1100 for g in grid)
    steps = [b - a for a, b in zip(grid[-100:], grid[-99:], strict=False)]
    assert max(steps) <= 1_000_000 // 200  # linear spacing near the end


def test_the_grid_is_a_pure_function_of_the_final_generation_and_cap() -> None:
    assert trajectory_grid(123_456) == trajectory_grid(123_456)
    assert trajectory_grid(123_456, 64) != trajectory_grid(123_456, 512)
    assert len(trajectory_grid(123_456, 64)) <= 64
    with pytest.raises(ValueError, match="at least two"):
        trajectory_grid(10, 1)


# --- reducing replicates ----------------------------------------------------


def test_the_mean_at_each_sample_is_the_mean_of_the_replicates_alive_then(
    tmp_path: Path,
) -> None:
    batch = _batch(
        tmp_path / "run",
        [(lambda g: 1.0 + g, 4), (lambda g: 3.0 + g, 4), (lambda g: 5.0 + g, 2)],
    )

    found = point_trajectory(batch, "run-x")

    assert found is not None
    series = found.statistics["D"]
    assert found.generations == (0, 1, 2, 3, 4)
    assert found.replicates == 3
    assert series.count == (3, 3, 3, 2, 2)
    assert series.mean[0] == pytest.approx((1 + 3 + 5) / 3)
    assert series.mean[3] == pytest.approx((4 + 6) / 2)  # the third has stopped
    assert series.std[3] == pytest.approx(math.sqrt(2))
    assert series.low[3] < series.mean[3] < series.high[3]


def test_a_sample_from_few_replicates_lacks_a_quorum(tmp_path: Path) -> None:
    batch = _batch(
        tmp_path / "run",
        [(float, 4)] + [(float, 1)] * 3,
    )

    found = point_trajectory(batch, "run-x")

    assert found is not None
    assert [found.has_quorum(i, "D") for i in range(5)] == [
        True,
        True,
        False,
        False,
        False,
    ]


def test_a_scalar_run_is_one_replicate_with_no_interval(tmp_path: Path) -> None:
    _replicate(tmp_path / "scalar", lambda g: 0.1 * g, 3, burn_in=2)

    found = point_trajectory(tmp_path / "scalar", "run-s")

    assert found is not None
    series = found.statistics["D"]
    assert found.replicates == 1 and found.burn_in == 2
    assert series.low == series.mean == series.high
    assert series.std == (0.0,) * 4


def test_every_statistic_in_the_history_gets_a_series(tmp_path: Path) -> None:
    batch = _batch(tmp_path / "run", [(float, 2)] * 2)

    found = point_trajectory(batch, "run-x")

    assert found is not None
    assert sorted(found.statistics) == ["D", "H_S"]
    assert found.statistics["H_S"].mean == (0.5, 0.5, 0.5)


def test_a_run_without_a_history_has_no_trajectory(tmp_path: Path) -> None:
    (tmp_path / "run" / "replicate-001").mkdir(parents=True)
    (tmp_path / "run" / "replicate-001" / "manifest.json").write_text("{}")

    assert point_trajectory(tmp_path / "run", "run-x") is None


def test_a_long_history_streams_to_a_fixed_size_answer(tmp_path: Path) -> None:
    rows = 200_000
    folder = tmp_path / "scalar"
    folder.mkdir()
    with (folder / "convergence.jsonl").open("w", encoding="utf-8") as handle:
        for g in range(rows):
            handle.write(json.dumps({"generation": g, "D": g / rows}) + "\n")
    (folder / "manifest.json").write_text(
        json.dumps({"convergence": {"generation": rows - 1}}), encoding="utf-8"
    )

    found = point_trajectory(folder, "run-big")

    assert found is not None
    assert len(found.generations) <= MAX_SAMPLES
    assert found.statistics["D"].mean[-1] == pytest.approx((rows - 1) / rows)


def test_the_history_reader_names_a_bad_row(tmp_path: Path) -> None:
    path = tmp_path / "convergence.jsonl"
    path.write_text('{"D": 1}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="integer 'generation'"):
        list(iter_history_rows(path))


# --- the cache --------------------------------------------------------------


def test_a_cached_trajectory_is_used_until_a_history_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    batch = _batch(tmp_path / "run", [(float, 3)] * 2)
    cache = TrajectoryCache(tmp_path / "cache")
    first = point_trajectory(batch, "run-x", cache=cache)

    def refuse(*_: Any, **__: Any) -> Any:
        raise AssertionError("the history was read again")

    monkeypatch.setattr("fim.sweep_trajectory.sample_history", refuse)
    second = point_trajectory(batch, "run-x", cache=cache)

    assert second == first
    # A replicate whose recorded checksum changes invalidates the entry.
    _replicate(batch / "replicate-001", lambda g: 2.0 * g, 3, digest="changed")
    with pytest.raises(AssertionError, match="read again"):
        point_trajectory(batch, "run-x", cache=cache)


def test_a_point_without_recorded_checksums_is_never_cached(tmp_path: Path) -> None:
    folder = tmp_path / "scalar"
    folder.mkdir()
    (folder / "convergence.jsonl").write_text('{"generation":0,"D":1.0}\n')
    (folder / "manifest.json").write_text('{"convergence":{"generation":0}}')
    cache = TrajectoryCache(tmp_path / "cache")

    assert point_trajectory(folder, "run-s", cache=cache) is not None
    assert not (tmp_path / "cache").exists()


def test_the_stored_form_round_trips(tmp_path: Path) -> None:
    batch = _batch(tmp_path / "run", [(float, 3)] * 3, burn_in=1)
    found = point_trajectory(batch, "run-x")

    assert found is not None
    assert PointTrajectory.from_dict(found.to_dict()) == found
    with pytest.raises(ValueError, match="schema"):
        PointTrajectory.from_dict({**found.to_dict(), "schema": 99})


# --- a real sweep -----------------------------------------------------------


def test_a_sweeps_trajectories_are_read_grouped_and_written_as_csv(
    tmp_path: Path,
) -> None:
    import threading  # noqa: PLC0415

    from fim import paths  # noqa: PLC0415
    from fim.sweep import SweepSpec, enumerate_points, expand_axis  # noqa: PLC0415
    from fim.sweep_run import (  # noqa: PLC0415
        LocalPointRunner,
        create_sweep_study,
        run_sweep,
    )
    from fim.sweep_table import ResultTable  # noqa: PLC0415
    from fim.sweep_trajectory import (  # noqa: PLC0415
        table_trajectories,
        write_trajectories_csv,
    )

    paths.set_results_directory_override(tmp_path / "results")
    try:
        spec = SweepSpec(
            base={
                "N": 16,
                "ploidy": "haploid",
                "d": 2,
                "m": 0.1,
                "mu": 0.01,
                "seed": 7,
                "n_replicates": 2,
                "max_generations": 12,
                "convergence_burn_in": 4,
            },
            axes=(expand_axis("m", [0.05, 0.2]),),
        )
        study = create_sweep_study(spec, enumerate_points(spec), "Trajectories")
        run_sweep(
            study.study_id,
            LocalPointRunner(),
            lambda _event: None,
            threading.Event(),
            points_at_once=1,
        )
        table = ResultTable.read(study.study_id)

        curves = table_trajectories(table, by="m")
        target = tmp_path / "trajectories.csv"
        written = write_trajectories_csv(table, "D", target)
    finally:
        paths.set_results_directory_override(None)

    assert [curve.by_value for curve in curves] == [0.05, 0.2]
    assert all(len(curve.entries) == 1 for curve in curves)
    trajectory = curves[0].entries[0].trajectory
    assert trajectory.replicates == 2 and trajectory.burn_in == 4
    assert trajectory.generations[0] == 0
    with target.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert (
        len(rows)
        == written
        == sum(len(curve.entries[0].trajectory.generations) for curve in curves)
    )
    assert {row["m"] for row in rows} == {"0.05", "0.2"}
    assert rows[0]["statistic"] == "D" and rows[0]["generation"] == "0"
    with pytest.raises(ValueError, match="not an axis"):
        table_trajectories(table, by="d")
