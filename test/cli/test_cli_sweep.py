"""Tests for `fim sweep` (plan, run, resume, report)."""

from __future__ import annotations

from pathlib import Path

import pytest

from fim import cli
from fim.persistence import groups

_SWEEP_FILE = """\
N: 16
ploidy: 2
d: 2
m: 0.1
mu: 0.01
seed: 7
n_replicates: 1
max_generations: 10
convergence_window: 3
sweep:
  name: Demes
  axes:
    d: [2, 3]
"""


@pytest.fixture
def sweep_file(tmp_path: Path) -> Path:
    """Write a two-point sweep file and return its path."""
    path = tmp_path / "sweep.yaml"
    path.write_text(_SWEEP_FILE, encoding="utf-8")
    return path


def _main(tmp_path: Path, *arguments: str) -> int:
    """Run `fim` with a temporary results directory."""
    return cli.main(["--results-directory", str(tmp_path / "results"), *arguments])


def test_plan_lists_the_points_and_runs_nothing(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = _main(tmp_path, "sweep", "plan", str(sweep_file))

    output = capsys.readouterr().out
    assert status == 0
    assert "2 valid point(s), 0 invalid" in output
    assert not (tmp_path / "results").exists()


def test_plan_reports_an_invalid_point_with_its_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "torus.yaml"
    path.write_text(
        _SWEEP_FILE.replace(
            "m: 0.1", "m: {topology: torus, rate: 0.1, rows: 3, columns: 4}"
        )
        .replace("d: 2\n", "d: 12\n", 1)
        .replace("[2, 3]", "[12, 10]"),
        encoding="utf-8",
    )

    status = _main(tmp_path, "sweep", "plan", str(path))

    output = capsys.readouterr().out
    assert status == 0
    assert "1 valid point(s), 1 invalid" in output
    assert "INVALID" in output


def test_run_creates_a_study_runs_every_point_and_report_reads_them(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = _main(tmp_path, "sweep", "run", str(sweep_file))

    output = capsys.readouterr().out
    assert status == 0
    assert "2 run, 0 reused" in output
    (study,) = groups.list_studies(results=tmp_path / "results")
    assert study.name == "Demes"
    assert study.run_count == 2

    status = _main(tmp_path, "sweep", "report", study.study_id, "--csv")

    lines = capsys.readouterr().out.splitlines()
    assert status == 0
    assert lines[0] == "d,state,replicates,D,low,high"
    assert [line.split(",")[0:3] for line in lines[1:]] == [
        ["2", "done", "1"],
        ["3", "done", "1"],
    ]


def test_resume_computes_nothing_once_every_point_exists(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _main(tmp_path, "sweep", "run", str(sweep_file), "--study", "Named")
    (study,) = groups.list_studies(results=tmp_path / "results")
    capsys.readouterr()

    status = _main(tmp_path, "sweep", "resume", study.study_id)

    assert status == 0
    assert "0 run, 0 reused, 2 already present" in capsys.readouterr().out
    assert study.name == "Named"


def test_a_large_sweep_needs_confirmation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "big.yaml"
    path.write_text(
        _SWEEP_FILE.replace("d: [2, 3]", "m: {start: 0.001, stop: 0.1, count: 100}"),
        encoding="utf-8",
    )

    status = _main(tmp_path, "sweep", "run", str(path))

    assert status == 2
    assert "--yes" in capsys.readouterr().err
    assert groups.list_studies(results=tmp_path / "results") == []


def test_a_sweep_file_without_a_sweep_block_is_a_plain_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "plain.yaml"
    path.write_text(_SWEEP_FILE.split("sweep:", maxsplit=1)[0], encoding="utf-8")

    status = _main(tmp_path, "sweep", "plan", str(path))

    assert status == 2
    assert "needs a 'sweep:' block" in capsys.readouterr().err


def test_report_on_a_study_that_is_not_a_sweep_is_a_plain_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    study = groups.create_study("By hand", results=tmp_path / "results")

    status = _main(tmp_path, "sweep", "report", study.study_id)

    assert status == 2
    assert "not a sweep" in capsys.readouterr().err
