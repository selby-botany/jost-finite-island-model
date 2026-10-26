"""Tests for `fim.sweep_compact`: making a finished sweep smaller afterwards."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from fim import cli, paths
from fim.persistence import groups
from fim.persistence.history import read_history_columns
from fim.persistence.manifest import hash_file, read_manifest
from fim.persistence.object_lock import exclusive_lock
from fim.persistence.retention import tapered_generations
from fim.persistence.run_lifecycle import RunKind
from fim.persistence.run_receipt import receipt_problem
from fim.persistence.tlog import iter_frames
from fim.sweep import SweepSpec, enumerate_points, expand_axis
from fim.sweep_compact import (
    CompactTarget,
    compact_sweep,
    kept_points,
    parse_keep,
)
from fim.sweep_run import (
    LocalPointRunner,
    SweepLockedError,
    create_sweep_study,
    pending_sweep_point_count,
    run_sweep,
    sweep_lock_path,
    sweep_point_results,
)

_BASE: dict[str, Any] = {
    "N": 16,
    "ploidy": "diploid",
    "d": 2,
    "m": 0.1,
    "mu": 0.01,
    "seed": 7,
    "n_replicates": 1,
    "max_generations": 40,
    "convergence_burn_in": 5,
    "history_format": "exact",
}


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


def _sweep(name: str = "Compact", **base: Any) -> str:
    """Run a four-point sweep (d by m) and return its study id."""
    spec = SweepSpec(
        base={**_BASE, **base},
        axes=(expand_axis("d", [2, 3]), expand_axis("m", [0.05, 0.1])),
    )
    study = create_sweep_study(spec, enumerate_points(spec), name)
    run_sweep(
        study.study_id,
        LocalPointRunner(),
        lambda _event: None,
        threading.Event(),
        points_at_once=1,
    )
    return study.study_id


def _rows(study_id: str) -> list[Any]:
    return sweep_point_results(groups.get_study(study_id))


def _problem(row: Any) -> str | None:
    batch = (row.directory / "replicate-001").exists()
    return receipt_problem(
        row.directory,
        kind=RunKind.BATCH if batch else RunKind.SCALAR,
        run_id=row.run_id,
    )


# --- choosing the points to leave alone --------------------------------------

_AXES = {"d": (2, 3, 4), "m": (0.01, 0.1)}
_POINTS = {f"run-{d}-{m}": {"d": d, "m": m} for d in (2, 3, 4) for m in (0.01, 0.1)}


def test_the_keep_rules_pick_the_corners_every_kth_or_nothing() -> None:
    assert kept_points(_POINTS, _AXES, "none") == set()
    assert kept_points(_POINTS, _AXES, "corners") == {
        "run-2-0.01",
        "run-2-0.1",
        "run-4-0.01",
        "run-4-0.1",
    }
    assert kept_points(_POINTS, _AXES, "every=2") == {
        "run-2-0.01",
        "run-3-0.01",
        "run-4-0.01",
    }
    assert kept_points(_POINTS, _AXES, "every=1") == set(_POINTS)


@pytest.mark.parametrize("text", ["all", "every=0", "every=x", "corner", ""])
def test_a_bad_keep_rule_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match="--keep must be"):
        parse_keep(text)
    assert parse_keep("every=3") == "every=3"


def test_a_target_must_be_one_compaction_can_make() -> None:
    with pytest.raises(ValueError, match="targets are"):
        CompactTarget(level="full")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="keep_last"):
        CompactTarget(level="tapered", keep_last=0)


# --- reducing a sweep --------------------------------------------------------


def test_compacting_to_statistics_drops_the_logs_and_keeps_every_answer(
    results: Path,
) -> None:
    study_id = _sweep()
    before = {
        row.run_id: (row.directory / "report.json").read_bytes()
        for row in _rows(study_id)
    }
    size = sum(path.stat().st_size for path in results.rglob("*") if path.is_file())

    report = compact_sweep(study_id)

    assert len(report.changed) == 4 and report.bytes_after < report.bytes_before
    for row in _rows(study_id):
        folder = row.directory
        assert not (folder / "trajectory.tlog").exists()
        assert not (folder / "convergence.jsonl").exists()
        assert (folder / "history.json").is_file()
        assert (folder / "report.json").read_bytes() == before[row.run_id]
        manifest = read_manifest(folder / "manifest.json")
        assert manifest.parameters["trajectory_retention"] == "statistics"
        assert manifest.artifacts is not None
        assert set(manifest.artifacts) == {"report", "scatter", "pairwise", "history"}
        assert _problem(row) is None
    assert sum(p.stat().st_size for p in results.rglob("*") if p.is_file()) < size


def test_compacting_to_tapered_gives_the_log_a_tapered_run_would_have_written(
    results: Path,
) -> None:
    # The tapered sweep goes first: a full run would be reused for it, and the
    # two studies would then share their runs.
    tapered_study = _sweep(
        "Tapered", trajectory_retention="tapered", trajectory_keep_last=6
    )
    full = _sweep("Full")

    report = compact_sweep(full, target=CompactTarget(level="tapered", keep_last=6))

    assert len(report.changed) == 4
    for row, other in zip(_rows(full), _rows(tapered_study), strict=True):
        assert row.run_id == other.run_id
        log = row.directory / "trajectory.tlog"
        assert log.read_bytes() == (other.directory / "trajectory.tlog").read_bytes()
        manifest = read_manifest(row.directory / "manifest.json")
        assert [f.generation for f in iter_frames(log)] == tapered_generations(
            manifest.generation, 6, 5
        )
        assert manifest.artifacts is not None
        assert manifest.artifacts["trajectory"] == hash_file(log)
        assert _problem(row) is None


def test_compacting_twice_changes_nothing_the_second_time(results: Path) -> None:
    study_id = _sweep()
    compact_sweep(study_id, target=CompactTarget(level="tapered", keep_last=6))
    once = {
        r.run_id: (r.directory / "trajectory.tlog").read_bytes()
        for r in _rows(study_id)
    }

    again = compact_sweep(study_id, target=CompactTarget(level="tapered", keep_last=6))

    assert again.changed == () and len(again.already_small) == 4
    assert once == {
        r.run_id: (r.directory / "trajectory.tlog").read_bytes()
        for r in _rows(study_id)
    }
    # A lighter target still reduces what is already tapered.
    lighter = compact_sweep(study_id)
    assert len(lighter.changed) == 4


def test_compact_then_resume_finds_nothing_pending(results: Path) -> None:
    """Regression test for the 2026-10-25 project review's finding P-2.

    `run_satisfies` asks a compacted point's run to hold what the
    sweep's own stored `recording:` block asks for, and that block
    used to stay `full` forever: a compacted point always looked
    `short`, so a routine `fim sweep resume` (no flag needed; `short`
    is folded into `rerun_ids` unconditionally) recomputed every
    compacted point in full, reverting the compaction it had just
    done. `compact_sweep` now lowers the stored block to the target
    level once it actually changes a point, so the sweep's own
    definition of "enough" tracks what compaction left behind.
    """
    study_id = _sweep()
    assert pending_sweep_point_count(study_id) == 0

    report = compact_sweep(study_id, target=CompactTarget(level="tapered", keep_last=6))
    assert len(report.changed) == 4
    before = {
        r.run_id: (r.directory / "trajectory.tlog").read_bytes()
        for r in _rows(study_id)
    }

    assert pending_sweep_point_count(study_id) == 0

    # The real resume path, not just its own prediction: nothing recomputed.
    run_sweep(
        study_id,
        LocalPointRunner(),
        lambda _event: None,
        threading.Event(),
        points_at_once=1,
    )
    after = {
        r.run_id: (r.directory / "trajectory.tlog").read_bytes()
        for r in _rows(study_id)
    }
    assert before == after


def test_keep_corners_leaves_those_points_as_they_were(results: Path) -> None:
    study_id = _sweep()

    report = compact_sweep(study_id, keep="corners")

    # A two-by-two grid is all corners.
    assert report.changed == () and len(report.kept) == 4
    report = compact_sweep(study_id, keep="every=2")
    assert len(report.kept) == 2 and len(report.changed) == 2
    kept = [
        row for row in _rows(study_id) if (row.directory / "trajectory.tlog").exists()
    ]
    assert len(kept) == 2


def test_a_dry_run_reports_and_writes_nothing(results: Path) -> None:
    study_id = _sweep()

    def snapshot() -> dict[Path, bytes]:
        # (Reading a sweep's results writes a disposable health cache.)
        return {
            p: p.read_bytes()
            for p in results.rglob("*")
            if p.is_file() and not p.name.endswith(".health.json")
        }

    before = snapshot()

    report = compact_sweep(study_id, dry_run=True)

    assert report.dry_run and len(report.changed) == 4
    assert before == snapshot()


def test_a_run_another_study_lists_is_refused_unless_all_studies_is_given(
    results: Path,
) -> None:
    study_id = _sweep()
    rows = _rows(study_id)
    other = groups.create_study("Other")
    groups.add_run_to_study(other.study_id, rows[0].directory)

    refused = compact_sweep(study_id)
    allowed = compact_sweep(study_id, all_studies=True)

    assert list(refused.refused) == [rows[0].run_id]
    assert refused.refused[rows[0].run_id] == (other.study_id,)
    assert len(refused.changed) == 3
    assert allowed.changed == (rows[0].run_id,)
    assert not (rows[0].directory / "trajectory.tlog").exists()


def test_a_batch_is_reduced_replicate_by_replicate(results: Path) -> None:
    study_id = _sweep("Batch", n_replicates=2)

    compact_sweep(study_id, target=CompactTarget(level="tapered", keep_last=6))

    for row in _rows(study_id):
        for replicate in sorted(row.directory.glob("replicate-*")):
            frames = [f.generation for f in iter_frames(replicate / "trajectory.tlog")]
            final = read_manifest(replicate / "manifest.json").generation
            assert frames == tapered_generations(final, 6, 5)
        assert _problem(row) is None
    compact_sweep(study_id)
    for row in _rows(study_id):
        assert not list(row.directory.glob("replicate-*/trajectory.tlog"))
        assert _problem(row) is None


def test_a_thinned_run_cannot_be_tapered_but_can_be_reduced_to_statistics(
    results: Path,
) -> None:
    study_id = _sweep(
        "Thinned",
        trajectory_retention="thinned",
        trajectory_stride=3,
        trajectory_thinning_start=4,
    )

    with pytest.raises(ValueError, match="was thinned"):
        compact_sweep(study_id, target=CompactTarget(level="tapered"))
    report = compact_sweep(study_id)

    assert len(report.changed) == 4


def test_a_sweep_being_run_cannot_be_compacted(results: Path) -> None:
    study_id = _sweep()

    with (
        exclusive_lock(sweep_lock_path(study_id), blocking=False),
        pytest.raises(SweepLockedError, match="stop it before compacting"),
    ):
        compact_sweep(study_id)


def test_the_results_table_reads_a_compacted_sweep(results: Path) -> None:
    from fim.sweep_table import ResultTable  # noqa: PLC0415

    study_id = _sweep()
    before = list(ResultTable.read(study_id).long_rows())

    compact_sweep(study_id)

    assert list(ResultTable.read(study_id).long_rows()) == before


def test_the_trajectory_overlay_reads_a_statistics_sweep(results: Path) -> None:
    from fim.sweep_table import ResultTable  # noqa: PLC0415
    from fim.sweep_trajectory import table_trajectories  # noqa: PLC0415

    study_id = _sweep()
    compact_sweep(study_id)

    curves = table_trajectories(ResultTable.read(study_id), by="d", held={"m": 0.1})

    assert [curve.by_value for curve in curves] == [2, 3]
    for curve in curves:
        (entry,) = curve.entries
        assert entry.trajectory.generations[0] == 0
        generations, columns = read_history_columns(
            entry.point.row.directory / "history.json"  # type: ignore[union-attr]
        )
        assert generations[-1] == entry.trajectory.generations[-1]
        assert "D" in columns


# --- the command -------------------------------------------------------------


def test_the_command_reports_what_it_reduced(
    results: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    study_id = _sweep()
    capsys.readouterr()

    status = cli.main(["sweep", "compact", study_id, "--dry-run"])
    preview = capsys.readouterr().out
    done = cli.main(
        ["sweep", "compact", study_id, "--to", "tapered", "--keep-last", "6"]
    )
    output = capsys.readouterr().out

    assert status == 0 and done == 0
    assert preview.startswith("Would reduce 4 point(s) to statistics")
    assert output.startswith("Reduced 4 point(s) to tapered")
    assert "disk from" in output and " to " in output.splitlines()[1]


def test_the_command_exits_nonzero_when_a_run_is_shared(
    results: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    study_id = _sweep()
    other = groups.create_study("Other")
    groups.add_run_to_study(other.study_id, _rows(study_id)[0].directory)
    capsys.readouterr()

    status = cli.main(["sweep", "compact", study_id])

    assert status == 1
    assert "also in" in capsys.readouterr().err
    assert json  # silence an unused import in some configurations
