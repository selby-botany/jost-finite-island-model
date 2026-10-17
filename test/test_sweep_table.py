"""Tests for `fim.sweep_table`: point cells, health, and the health cache.

Health is derived from the manifests a run already wrote, so most tests build
those few files by hand: no simulation runs, and the result depends only on
the files.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from fim import paths
from fim.persistence import groups
from fim.statistics.theory import theory_at
from fim.sweep import SweepSpec, enumerate_points, expand_axis
from fim.sweep_run import (
    create_sweep_study,
    health_cache_path,
    run_sweep,
    sweep_point_results,
)
from fim.sweep_table import (
    ALL_STATISTICS,
    DEFAULT_HEALTH_POLICY,
    HealthCache,
    HealthPolicy,
    PointHealth,
    PointRow,
    ResultTable,
    StatisticCell,
    TablePoint,
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


# --- ResultTable ----------------------------------------------------------

_SPEC = SweepSpec(
    base={
        "N": 225,
        "ploidy": "diploid",
        "d": 20,
        "m": 0.001,
        "mu": 3e-05,
        "seed": 7,
        "n_replicates": 3,
        "max_generations": 1000,
    },
    axes=(expand_axis("N", [100, 225]), expand_axis("m", [0.001, 0.01])),
)
_STAMP = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def _row(
    index: int,
    coordinates: dict[str, Any],
    statistics: dict[str, StatisticCell],
    health: PointHealth | None = None,
) -> PointRow:
    """A finished point's row, built without running anything."""
    return PointRow(
        index=index,
        coordinates=coordinates,
        run_id=f"run-{index:04d}",
        state="done",
        directory=Path(f"/nowhere/{index}"),
        n_replicates=3,
        seed=100 + index,
        software_version="1.2.0",
        engine_backend="lineal",
        started_at=None,
        ended_at=None,
        statistics=statistics,
        health=health,
        parameters={},
    )


_CELL = StatisticCell(0.25, 0.2, 0.3, 0.05, 0.04, 3, 0.95)
_HEALTHY = PointHealth(3, 3, 0, 0, {"D": 0.005}, 0.01)
_DEGRADED = PointHealth(3, 2, 1, 0, {"D": 0.02}, 0.01)


def _table() -> ResultTable:
    """Four points: two finished, one single run, one still waiting."""
    points = [
        TablePoint(
            0,
            {"N": 100, "m": 0.001},
            "run-0000",
            "done",
            _row(
                0,
                {"N": 100, "m": 0.001},
                {"D": _CELL, "G_ST": _CELL, "S_S": _CELL},
                _HEALTHY,
            ),
        ),
        TablePoint(
            1,
            {"N": 100, "m": 0.01},
            "run-0001",
            "done",
            _row(
                1,
                {"N": 100, "m": 0.01},
                {"D": StatisticCell(0.5), "G_ST": _CELL},
                _DEGRADED,
            ),
        ),
        TablePoint(2, {"N": 225, "m": 0.001}, "run-0002", "waiting", None),
        TablePoint(
            3,
            {"N": 225, "m": 0.01},
            "run-0003",
            "done",
            _row(3, {"N": 225, "m": 0.01}, {"D": _CELL, "G_ST": _CELL}, None),
        ),
    ]
    return ResultTable("study-1", "Demo", _SPEC, points)


def _read_csv(path: Path) -> list[dict[str, str]]:
    """Read a CSV table back as dictionaries."""
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


GOLDEN_LONG_HEADER = (
    "sweep_id,point_index,state,N,m,gene_copies_per_deme,run_id,seed,statistic,"
    "mean,ci_low,ci_high,ci_half_width,ci_level,sample_std,sample_count,"
    "replicates,replicates_converged,replicates_hit_cap,health,"
    "achieved_half_width,software_version,engine_backend"
)


def test_the_long_header_is_fixed(tmp_path: Path) -> None:
    out = tmp_path / "t.csv"

    _table().to_csv(out)

    assert out.read_text(encoding="utf-8").splitlines()[0] == GOLDEN_LONG_HEADER
    with_theory = _table().columns(with_theory=True)
    assert with_theory == [*GOLDEN_LONG_HEADER.split(","), "theory", "residual"]


def test_the_default_statistics_are_d_and_g_st_for_finished_points(
    tmp_path: Path,
) -> None:
    out = tmp_path / "t.csv"

    _table().to_csv(out)

    rows = _read_csv(out)
    assert [(r["point_index"], r["statistic"]) for r in rows] == [
        ("0", "D"),
        ("0", "G_ST"),
        ("1", "D"),
        ("1", "G_ST"),
        ("3", "D"),
        ("3", "G_ST"),
    ]


def test_a_single_run_has_empty_interval_fields(tmp_path: Path) -> None:
    out = tmp_path / "t.csv"

    _table().to_csv(out)

    single = next(
        r for r in _read_csv(out) if r["point_index"] == "1" and r["statistic"] == "D"
    )
    assert single["mean"] == "0.5"
    assert (single["ci_low"], single["ci_high"], single["ci_half_width"]) == (
        "",
        "",
        "",
    )


def test_a_point_that_is_not_healthy_is_flagged_not_dropped(tmp_path: Path) -> None:
    out = tmp_path / "t.csv"

    _table().to_csv(out)

    rows = _read_csv(out)
    health = {r["point_index"]: r["health"] for r in rows}
    assert health == {"0": "healthy", "1": "degraded", "3": ""}
    flagged = next(r for r in rows if r["point_index"] == "1" and r["statistic"] == "D")
    assert flagged["replicates_hit_cap"] == "1"
    assert flagged["achieved_half_width"] == "0.02"


def test_unfinished_points_are_listed_only_on_request(tmp_path: Path) -> None:
    out = tmp_path / "t.csv"
    _table().to_csv(out, include_unfinished=True)

    waiting = [r for r in _read_csv(out) if r["state"] == "waiting"]

    assert len(waiting) == 1
    assert waiting[0]["point_index"] == "2"
    assert (waiting[0]["statistic"], waiting[0]["mean"]) == ("", "")
    assert all(r["state"] == "done" for r in _read_csv_without_unfinished(tmp_path))


def _read_csv_without_unfinished(tmp_path: Path) -> list[dict[str, str]]:
    out = tmp_path / "default.csv"
    _table().to_csv(out)
    return _read_csv(out)


def test_n_axis_adds_gene_copies_per_deme(tmp_path: Path) -> None:
    out = tmp_path / "t.csv"
    _table().to_csv(out)

    copies = {r["N"]: r["gene_copies_per_deme"] for r in _read_csv(out)}

    assert copies == {"100": "200", "225": "450"}


def test_long_and_wide_forms_hold_the_same_numbers(tmp_path: Path) -> None:
    long_out, wide_out = tmp_path / "long.csv", tmp_path / "wide.csv"
    _table().to_csv(long_out, statistics=["D", "G_ST"])
    _table().to_csv(wide_out, statistics=["D", "G_ST"], wide=True)

    long_rows = _read_csv(long_out)
    wide_rows = {r["point_index"]: r for r in _read_csv(wide_out)}

    assert set(wide_rows) == {"0", "1", "3"}
    for row in long_rows:
        wide = wide_rows[row["point_index"]]
        name = row["statistic"]
        assert wide[f"{name}_mean"] == row["mean"]
        assert wide[f"{name}_ci_low"] == row["ci_low"]
        assert wide[f"{name}_ci_high"] == row["ci_high"]
        assert wide[f"{name}_ci_half_width"] == row["ci_half_width"]
        assert wide[f"{name}_sample_std"] == row["sample_std"]
        assert wide["health"] == row["health"]


def test_all_statistics_writes_every_statistic_a_point_has(tmp_path: Path) -> None:
    out = tmp_path / "t.csv"
    _table().to_csv(out, statistics=ALL_STATISTICS)

    names = {r["statistic"] for r in _read_csv(out)}

    assert names == {"D", "G_ST", "S_S"}


def test_an_unknown_statistic_is_refused_with_the_available_ones(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match=r"no statistic .nope.*D, G_ST, S_S"):
        _table().to_csv(tmp_path / "t.csv", statistics=["nope"])
    assert not (tmp_path / "t.csv").exists()


def test_theory_and_residual_columns_follow_the_closed_form(tmp_path: Path) -> None:
    out = tmp_path / "t.csv"
    _table().to_csv(out, statistics=["D", "S_S"], with_theory=True)

    rows = _read_csv(out)
    expected = theory_at(_SPEC.base, {"N": 100, "m": 0.001})
    first = next(r for r in rows if r["point_index"] == "0" and r["statistic"] == "D")
    assert float(first["theory"]) == expected["D"]
    assert float(first["residual"]) == 0.25 - expected["D"]
    no_formula = next(r for r in rows if r["statistic"] == "S_S")
    assert (no_formula["theory"], no_formula["residual"]) == ("", "")


def test_the_wide_table_carries_theory_per_statistic(tmp_path: Path) -> None:
    out = tmp_path / "t.csv"
    _table().to_csv(out, statistics=["D"], wide=True, with_theory=True)

    columns = _read_csv(out)[0].keys()

    assert {"D_theory", "D_residual"} <= set(columns)


def test_the_sidecar_reproduces_the_spec_and_names_the_columns(tmp_path: Path) -> None:
    out = tmp_path / "t.csv"

    sidecar = _table().to_csv(out, clock=lambda: _STAMP)

    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sidecar == tmp_path / "t.meta.json"
    assert SweepSpec.from_dict(meta["spec"]) == _SPEC
    assert meta["sweep"] == {"study_id": "study-1", "name": "Demo"}
    assert meta["created"] == "2026-10-17T12:00:00Z"
    assert meta["table"]["statistics"] == ["D", "G_ST"]
    assert list(meta["columns"]) == GOLDEN_LONG_HEADER.split(",")
    assert all(meta["columns"].values())
    assert meta["statistics"]["D"]["label"]
    assert [p["run_id"] for p in meta["points"]] == [
        "run-0000",
        "run-0001",
        "run-0002",
        "run-0003",
    ]


def test_an_existing_table_or_sidecar_is_not_overwritten_unless_asked(
    tmp_path: Path,
) -> None:
    out = tmp_path / "t.csv"
    _table().to_csv(out)

    with pytest.raises(FileExistsError, match=r"t\.csv"):
        _table().to_csv(out)
    out.unlink()
    with pytest.raises(FileExistsError, match="meta"):
        _table().to_csv(out)
    _table().to_csv(out, overwrite=True)
    assert out.is_file()


def test_jsonl_rows_equal_the_long_rows(tmp_path: Path) -> None:
    out = tmp_path / "t.jsonl"
    table = _table()

    table.to_jsonl(out, statistics=["D"], with_theory=True)

    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    expected = list(table.long_rows(["D"], with_theory=True))
    columns = table.columns(with_theory=True)
    assert lines == [{c: row.get(c) for c in columns} for row in expected]
    assert lines[1]["ci_low"] is None  # the single run: null, not an empty string


@given(
    st.lists(
        st.floats(allow_nan=False, allow_infinity=False, width=64),
        min_size=1,
        max_size=6,
    )
)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)
def test_a_csv_round_trip_gives_back_every_float_exactly(
    tmp_path: Path, values: list[float]
) -> None:
    cells = {
        f"S{i}": StatisticCell(v, v, v, abs(v), abs(v), 2, 0.95)
        for i, v in enumerate(values)
    }
    row = _row(0, {"N": 100, "m": 0.001}, cells)
    table = ResultTable(
        "s", "n", _SPEC, [TablePoint(0, row.coordinates, "r", "done", row)]
    )
    out = tmp_path / "p.csv"

    table.to_csv(out, statistics=ALL_STATISTICS, overwrite=True)

    back = {r["statistic"]: r for r in _read_csv(out)}
    for i, v in enumerate(values):
        assert float(back[f"S{i}"]["mean"]) == v
        assert float(back[f"S{i}"]["ci_half_width"]) == abs(v)


def test_every_wide_column_is_described_in_the_sidecar(tmp_path: Path) -> None:
    out = tmp_path / "w.csv"

    sidecar = _table().to_csv(out, statistics=["D"], wide=True, with_theory=True)

    columns = json.loads(sidecar.read_text(encoding="utf-8"))["columns"]
    assert {"D_mean", "D_theory", "D_residual", "N", "m"} <= set(columns)
    assert all(text and not text.startswith("Column ") for text in columns.values())


def test_a_healthy_point_has_nothing_to_explain() -> None:
    assert PointHealth(3, 3, 0, 0, {"D": 0.005}, 0.01).describe() == ""


def test_the_reasons_for_a_flag_are_named() -> None:
    capped = PointHealth(3, 2, 1, 0, {"D": 0.005}, 0.01)
    mixed = PointHealth(4, 1, 2, 1, {"D": 0.5}, 0.01)

    assert capped.describe() == (
        "1 of 3 replicates stopped at the generation limit before settling"
    )
    assert mixed.describe() == (
        "2 of 4 replicates stopped at the generation limit before settling; "
        "1 ended another way; the interval is wider than the precision asked for"
    )
    assert PointHealth(3, 0, 3, 0).describe().startswith("3 of 3 replicates")
