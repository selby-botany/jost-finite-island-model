"""Tests for `fim sweep` (plan, run, resume, report)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fim import cli
from fim.persistence import groups

_SWEEP_FILE = """\
N: 16
ploidy: diploid
d: 2
m: 0.1
mu: 0.01
seed: 7
n_replicates: 1
max_generations: 10
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
    assert lines[0] == "d,state,replicates,D,low,high,health"
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


def test_resuming_under_a_new_software_version_recomputes_and_reports_a_match(
    tmp_path: Path,
    sweep_file: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fim import sweep_run  # noqa: PLC0415

    _main(tmp_path, "sweep", "run", str(sweep_file))
    (study,) = groups.list_studies(results=tmp_path / "results")
    capsys.readouterr()
    real = sweep_run.run_sweep
    monkeypatch.setattr(
        "fim.cli_sweep.run_sweep",
        lambda *args, **kwargs: real(*args, software_version="9.9.9", **kwargs),
    )

    status = _main(tmp_path, "sweep", "resume", study.study_id)

    output = capsys.readouterr().out
    assert status == 0
    assert "2 point(s) recomputed and matched" in output
    assert "0 differed" in output
    # The recomputed points replace the originals; each is a Run object.
    assert len(list((tmp_path / "results").glob("objects/*/manifest.json"))) == 2


def test_concurrency_flags_are_accepted_and_the_sweep_still_runs(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = _main(
        tmp_path,
        "sweep",
        "run",
        str(sweep_file),
        "--points-at-once",
        "2",
        "--workers",
        "1",
    )

    assert status == 0
    assert "2 run, 0 reused" in capsys.readouterr().out


def test_sequential_runs_one_point_at_a_time(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = _main(tmp_path, "sweep", "run", str(sweep_file), "--sequential")

    assert status == 0
    assert "2 run, 0 reused" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (["--sequential", "--workers", "2"], "cannot be combined"),
        (["--sequential", "--points-at-once", "2"], "cannot be combined"),
        (["--points-at-once", "0"], "at least 1"),
        (["--points-at-once", "many"], "at least 1"),
        (["--workers", "0"], "at least 1"),
    ],
)
def test_bad_concurrency_flags_are_plain_errors(
    tmp_path: Path,
    sweep_file: Path,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
    message: str,
) -> None:
    status = _main(tmp_path, "sweep", "run", str(sweep_file), *flags)

    assert status == 2
    assert message in capsys.readouterr().err


def _object_directories(tmp_path: Path) -> set[str]:
    """Return the names of the Run objects the results directory holds."""
    manifests = (tmp_path / "results" / "objects").glob("*/manifest.json")
    return {manifest.parent.name for manifest in manifests}


def test_resuming_a_finished_sweep_runs_nothing(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _main(tmp_path, "sweep", "run", str(sweep_file))
    (study,) = groups.list_studies(results=tmp_path / "results")
    before = _object_directories(tmp_path)
    capsys.readouterr()

    status = _main(tmp_path, "sweep", "resume", study.study_id)

    assert status == 0
    assert "0 run, 0 reused, 2 already present" in capsys.readouterr().out
    assert _object_directories(tmp_path) == before


def test_rerun_recomputes_every_completed_point_and_replaces_it(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _main(tmp_path, "sweep", "run", str(sweep_file))
    (study,) = groups.list_studies(results=tmp_path / "results")
    before = _object_directories(tmp_path)
    capsys.readouterr()

    status = _main(tmp_path, "sweep", "resume", study.study_id, "--rerun")

    output = capsys.readouterr().out
    after = _object_directories(tmp_path)
    assert status == 0
    assert "0 already present" in output
    assert "2 point(s) recomputed and matched the earlier result" in output
    assert "0 differed" in output
    # Each point is a new run; the identical old one was replaced, not kept.
    assert len(after) == 2
    assert after.isdisjoint(before)
    refreshed = groups.get_study(study.study_id, results=tmp_path / "results")
    assert len(refreshed.run_directories) == 2


def test_rerun_is_offered_by_resume_only(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        _main(tmp_path, "sweep", "run", str(sweep_file), "--rerun")

    assert raised.value.code == 2
    assert "--rerun" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("flags", "expected"),
    [([], 2), (["--max-attempts", "5"], 5)],
)
def test_max_attempts_defaults_to_two_and_reaches_the_sweep(
    tmp_path: Path,
    sweep_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    flags: list[str],
    expected: int,
) -> None:
    from fim import sweep_run  # noqa: PLC0415

    seen: list[int] = []
    real = sweep_run.run_sweep

    def spy(*args: object, **kwargs: int) -> object:
        seen.append(kwargs["max_attempts"])
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("fim.cli_sweep.run_sweep", spy)

    assert _main(tmp_path, "sweep", "run", str(sweep_file), *flags) == 0
    assert seen == [expected]


def test_fewer_than_one_attempt_is_a_plain_error(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = _main(tmp_path, "sweep", "run", str(sweep_file), "--max-attempts", "0")

    assert status == 2
    assert "max_attempts" in capsys.readouterr().err


def test_a_sweep_that_is_running_elsewhere_is_a_plain_error(
    tmp_path: Path,
    sweep_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from fim.persistence.object_lock import exclusive_lock  # noqa: PLC0415
    from fim.sweep_run import sweep_lock_path  # noqa: PLC0415

    _main(tmp_path, "sweep", "run", str(sweep_file))
    (study,) = groups.list_studies(results=tmp_path / "results")
    capsys.readouterr()

    with exclusive_lock(
        sweep_lock_path(study.study_id, results=tmp_path / "results"),
        blocking=False,
    ):
        status = _main(tmp_path, "sweep", "resume", study.study_id)

    assert status == 2
    assert "another process is running" in capsys.readouterr().err


def test_replan_reports_a_dry_run_then_repairs_a_stale_plan(
    tmp_path: Path,
    sweep_file: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fim.engine import deterministic_run_id  # noqa: PLC0415

    _main(tmp_path, "sweep", "run", str(sweep_file))
    (study,) = groups.list_studies(results=tmp_path / "results")
    monkeypatch.setattr(
        "fim.sweep.deterministic_run_id", lambda p: "v2-" + deterministic_run_id(p)
    )
    assert _main(tmp_path, "sweep", "resume", study.study_id) == 2
    assert f"fim sweep replan {study.study_id}" in capsys.readouterr().err

    assert _main(tmp_path, "sweep", "replan", study.study_id, "--dry-run") == 0
    preview = capsys.readouterr().out
    assert "Would change: 2 point(s) got a new run id" in preview
    assert "Continue with" not in preview

    assert _main(tmp_path, "sweep", "replan", study.study_id) == 0
    changed = capsys.readouterr().out
    assert "Changed: 2 point(s) got a new run id" in changed
    assert f"Continue with: fim sweep resume {study.study_id}" in changed


def _run_sweep_file(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> str:
    """Run the two-point sweep and return its study id."""
    assert _main(tmp_path, "sweep", "run", str(sweep_file)) == 0
    (study,) = groups.list_studies(results=tmp_path / "results")
    capsys.readouterr()
    return study.study_id


def test_export_writes_the_table_and_its_sidecar(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    study_id = _run_sweep_file(tmp_path, sweep_file, capsys)
    out = tmp_path / "table.csv"

    status = _main(tmp_path, "sweep", "export", study_id, str(out))

    assert status == 0
    assert "table.meta.json" in capsys.readouterr().out
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("sweep_id,point_index,state,d,run_id,seed,statistic")
    assert len(lines) == 1 + 2 * 2  # two points, D and G_ST
    meta = json.loads((tmp_path / "table.meta.json").read_text(encoding="utf-8"))
    assert meta["sweep"]["study_id"] == study_id
    assert meta["spec"]["axes"]


def test_export_refuses_to_overwrite_unless_forced(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    study_id = _run_sweep_file(tmp_path, sweep_file, capsys)
    out = tmp_path / "table.csv"
    assert _main(tmp_path, "sweep", "export", study_id, str(out)) == 0
    capsys.readouterr()

    assert _main(tmp_path, "sweep", "export", study_id, str(out)) == 2
    assert "already exists" in capsys.readouterr().err
    assert _main(tmp_path, "sweep", "export", study_id, str(out), "--force") == 0


def test_export_options_choose_statistics_layout_and_theory(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    study_id = _run_sweep_file(tmp_path, sweep_file, capsys)
    out = tmp_path / "wide.csv"

    status = _main(
        tmp_path,
        "sweep",
        "export",
        study_id,
        str(out),
        "--wide",
        "--statistic",
        "D",
        "--statistic",
        "H_S",
        "--with-theory",
    )

    assert status == 0
    header = out.read_text(encoding="utf-8").splitlines()[0].split(",")
    assert {"D_mean", "H_S_mean", "H_S_theory", "H_S_residual"} <= set(header)
    assert "G_ST_mean" not in header


def test_export_all_statistics_and_jsonl_by_file_name(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    study_id = _run_sweep_file(tmp_path, sweep_file, capsys)
    out = tmp_path / "table.jsonl"

    assert (
        _main(tmp_path, "sweep", "export", study_id, str(out), "--all-statistics") == 0
    )

    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert {row["statistic"] for row in rows} > {"D", "G_ST", "H_S", "H_T"}
    assert all(row["state"] == "done" for row in rows)


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (["--statistic", "not_a_statistic"], "no statistic 'not_a_statistic'"),
        (["--format", "jsonl", "--wide"], "CSV only"),
    ],
)
def test_bad_export_requests_are_plain_errors(
    tmp_path: Path,
    sweep_file: Path,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
    message: str,
) -> None:
    study_id = _run_sweep_file(tmp_path, sweep_file, capsys)
    out = tmp_path / "table.csv"

    status = _main(tmp_path, "sweep", "export", study_id, str(out), *flags)

    assert status == 2
    assert message in capsys.readouterr().err
    assert not out.exists()
