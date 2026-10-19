"""Tests for `fim sweep` (plan, run, resume, report)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

import pytest
import yaml

from fim import cli
from fim.persistence import groups
from fim.sweep import enumerate_points, spec_from_config
from fim.sweep_cost import Throughput
from fim.sweep_run import LocalPointRunner, create_sweep_study

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


class _FastProbe:
    """Stands in for `EngineProbe`: fixed figures, nothing run, calls recorded."""

    asked: ClassVar[list[int]] = []

    def measure(self, params: Any, generations: int) -> Throughput:
        del params
        self.asked.append(generations)
        return Throughput(0.001, 50.0, 5.0, 4.0)


@pytest.fixture(autouse=True)
def _no_real_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> type[_FastProbe]:
    """Never time the real engine from a CLI test, or touch the user's cache."""
    monkeypatch.setenv("FIM_PREFERENCES_FILE", str(tmp_path / "prefs" / "p.json"))
    _FastProbe.asked = []
    monkeypatch.setattr("fim.cli_sweep.EngineProbe", _FastProbe)
    return _FastProbe


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


_COMPARE_FILE = """\
N: {n}
ploidy: diploid
d: 2
m: 0.1
mu: 0.01
seed: 7
n_replicates: 3
max_generations: 10
sweep:
  name: {name}
  axes:
    m: [0.05, 0.1]
"""


def _two_sweeps(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> tuple[str, str]:
    """Run two small sweeps that differ only in N; return their study ids."""
    for n, name in ((16, "Small"), (32, "Large")):
        path = tmp_path / f"{name}.yaml"
        path.write_text(_COMPARE_FILE.format(n=n, name=name), encoding="utf-8")
        assert _main(tmp_path, "sweep", "run", str(path)) == 0
    by_name = {
        study.name: study.study_id
        for study in groups.list_studies(results=tmp_path / "results")
    }
    capsys.readouterr()
    return by_name["Small"], by_name["Large"]


def test_compare_prints_the_join_the_difference_and_the_seed_note(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    small, large = _two_sweeps(tmp_path, capsys)

    status = _main(tmp_path, "sweep", "compare", small, large, "--statistic", "D")

    output = capsys.readouterr().out
    assert status == 0
    assert "Comparing D across Small, Large, joined on m" in output
    assert "base differs in N: Small: 16; Large: 32" in output
    assert "the runs used the same seeds, so they are paired" in output
    assert "difference is Small minus Large, with a Welch interval" in output
    assert "Small: mean" in output and "diff low" in output


def test_compare_labels_writes_a_csv_and_will_not_overwrite_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    small, large = _two_sweeps(tmp_path, capsys)
    out = tmp_path / "compare.csv"

    status = _main(
        tmp_path,
        "sweep",
        "compare",
        f"{small}=this year",
        f"{large}=last year",
        "--csv",
        str(out),
    )

    assert status == 0
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("m,this year: mean,this year: low")
    assert "last year: seed" in lines[0] and lines[0].endswith("diff high")
    assert len(lines) == 1 + 2  # one row per shared m value
    capsys.readouterr()

    args = ("sweep", "compare", small, large, "--csv", str(out))
    assert _main(tmp_path, *args) == 2
    assert "already exists" in capsys.readouterr().err
    assert _main(tmp_path, *args, "--force") == 0


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--statistic", "not_a_statistic"], "has statistic 'not_a_statistic'"),
        (["--on", "nope"], "cannot join on 'nope'"),
    ],
)
def test_a_comparison_that_cannot_be_made_is_a_plain_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    extra: list[str],
    message: str,
) -> None:
    small, large = _two_sweeps(tmp_path, capsys)

    status = _main(tmp_path, "sweep", "compare", small, large, *extra)

    assert status == 2
    assert message in capsys.readouterr().err


def test_comparing_one_sweep_is_a_plain_error(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    study_id = _run_sweep_file(tmp_path, sweep_file, capsys)

    assert _main(tmp_path, "sweep", "compare", study_id) == 2
    assert "at least two sweeps" in capsys.readouterr().err


def test_plan_states_the_work_as_a_range(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _main(tmp_path, "sweep", "plan", str(sweep_file)) == 0

    output = capsys.readouterr().out
    assert "2 valid point(s), 0 invalid" in output
    work = next(line for line in output.splitlines() if line.startswith("work"))
    assert "1 replicate(s) each" in work  # a single run: nothing to range over
    assert "generations each" in work and "replicate-generations in all" in work
    assert "nobody knows in advance how soon a run settles" in work


def test_plan_measures_the_engine_and_states_time_disk_and_probe(
    tmp_path: Path,
    sweep_file: Path,
    capsys: pytest.CaptureFixture[str],
    _no_real_probe: type[_FastProbe],
) -> None:
    assert _main(tmp_path, "sweep", "plan", str(sweep_file)) == 0

    lines = capsys.readouterr().out.splitlines()
    time_line = next(line for line in lines if line.startswith("time"))
    disk_line = next(line for line in lines if line.startswith("disk"))
    probe_line = next(line for line in lines if line.startswith("probe"))
    assert time_line.startswith("time   about ") and "point(s) at once" in time_line
    assert "with the full trajectory log" in disk_line
    assert "statistics only" in disk_line
    # d = 2 and d = 3 are two shape classes, each measured with the default burst.
    assert probe_line.startswith("probe  2 shape class(es) measured")
    assert _no_real_probe.asked == [200, 200]
    assert (tmp_path / "prefs" / "sweep-throughput.json").is_file()


def test_a_repeated_plan_uses_the_cached_measurement(
    tmp_path: Path,
    sweep_file: Path,
    capsys: pytest.CaptureFixture[str],
    _no_real_probe: type[_FastProbe],
) -> None:
    _main(tmp_path, "sweep", "plan", str(sweep_file))
    _main(tmp_path, "sweep", "plan", str(sweep_file))

    assert _no_real_probe.asked == [200, 200]  # measured once, then remembered
    assert capsys.readouterr().out.count("probe  2 shape class(es) measured") == 2


def test_no_probe_prints_the_work_range_only(
    tmp_path: Path,
    sweep_file: Path,
    capsys: pytest.CaptureFixture[str],
    _no_real_probe: type[_FastProbe],
) -> None:
    assert _main(tmp_path, "sweep", "plan", str(sweep_file), "--no-probe") == 0

    output = capsys.readouterr().out
    assert "work   " in output
    assert "time   not estimated" in output
    assert "disk   " not in output
    assert _no_real_probe.asked == []


def test_run_prints_the_same_cost_lines_before_it_starts(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _main(tmp_path, "sweep", "run", str(sweep_file)) == 0

    output = capsys.readouterr().out
    assert output.index("time   about") < output.index("Created sweep study")


def test_the_points_at_once_flag_reaches_the_time_estimate(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _main(tmp_path, "sweep", "plan", str(sweep_file), "--sequential")

    assert "(1 point(s) at once on" in capsys.readouterr().out


def test_a_finished_points_line_says_how_long_it_took(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _main(tmp_path, "sweep", "run", str(sweep_file), "--no-probe") == 0

    done = [line for line in capsys.readouterr().out.splitlines() if ": done" in line]
    assert len(done) == 2
    assert all(" in " in line and line.rstrip().endswith(("s", "min")) for line in done)


def test_status_shows_the_counts_the_lock_and_the_time_left(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    study_id = _run_sweep_file(tmp_path, sweep_file, capsys)

    assert _main(tmp_path, "sweep", "status", study_id) == 0

    output = capsys.readouterr().out
    assert f"Sweep Demes ({study_id}): 2 of 2 points done" in output
    assert "2 done" in output
    assert "no process is running this sweep" in output
    assert "last point finished: 20" in output  # an ISO date, this century
    assert "time left   none: every point is done" in output


def test_status_lists_failures_with_the_command_to_retry_them(
    tmp_path: Path,
    sweep_file: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fim.sweep_run import PointFailure  # noqa: PLC0415

    class Failing:
        def run_point(self, *args: Any, **kwargs: Any) -> PointFailure:
            del args, kwargs
            return PointFailure("the engine exploded")

    monkeypatch.setattr("fim.cli_sweep.LocalPointRunner", Failing)
    assert _main(tmp_path, "sweep", "run", str(sweep_file), "--no-probe") == 1
    (study,) = groups.list_studies(results=tmp_path / "results")
    capsys.readouterr()

    assert _main(tmp_path, "sweep", "status", study.study_id) == 0

    output = capsys.readouterr().out
    assert "failed: point 0 (d=2): the engine exploded" in output
    assert (
        f"retry them with: fim sweep resume {study.study_id} --retry-failed" in output
    )


def test_watch_repeats_until_the_sweep_is_finished(
    tmp_path: Path,
    sweep_file: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    study_id = _run_sweep_file(tmp_path, sweep_file, capsys)
    # Make the sweep look unfinished by holding its lock, then release it
    # when the loop first sleeps.
    from fim.persistence.object_lock import exclusive_lock  # noqa: PLC0415
    from fim.sweep_run import sweep_lock_path  # noqa: PLC0415

    results = tmp_path / "results"
    lock = exclusive_lock(sweep_lock_path(study_id, results=results), blocking=False)
    lock.__enter__()
    slept: list[float] = []

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        lock.__exit__(None, None, None)

    monkeypatch.setattr("fim.cli_sweep.time.sleep", fake_sleep)

    assert _main(tmp_path, "sweep", "status", study_id, "--watch", "7") == 0

    output = capsys.readouterr().out
    assert slept == [7.0]
    assert output.count("points done") == 2
    assert "a process is running this sweep now" in output
    assert "no process is running this sweep" in output


def test_a_pilot_runs_the_costliest_then_asks_and_continues_on_yes(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = _main(
        tmp_path, "sweep", "run", str(sweep_file), "--pilot", "1", "--yes", "--no-probe"
    )

    output = capsys.readouterr().out
    assert status == 0
    assert "Pilot: running the 1 costliest of 2 point(s)" in output
    assert "Pilot result" in output and "planned " in output and "actual " in output
    assert "time left   about " in output or "time left   none" in output
    assert output.count(": done") == 2  # the pilot point, then the rest
    (study,) = groups.list_studies(results=tmp_path / "results")
    assert study.run_count == 2


def test_a_pilot_stops_when_the_answer_is_no(
    tmp_path: Path,
    sweep_file: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[str] = []

    def say_no(prompt: str = "") -> str:
        asked.append(prompt)
        return "n"

    monkeypatch.setattr("builtins.input", say_no)

    status = _main(
        tmp_path, "sweep", "run", str(sweep_file), "--pilot", "1", "--no-probe"
    )

    output = capsys.readouterr().out
    assert status == 0
    assert asked == ["Run the remaining points? [y/N] "]
    assert "Stopped after the pilot. Continue with: fim sweep resume" in output
    (study,) = groups.list_studies(results=tmp_path / "results")
    assert study.run_count == 1  # the pilot point is a real, stored result


def test_a_pilot_with_no_answer_does_not_continue(
    tmp_path: Path,
    sweep_file: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def end_of_input(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", end_of_input)

    assert (
        _main(tmp_path, "sweep", "run", str(sweep_file), "--pilot", "1", "--no-probe")
        == 0
    )
    assert "Stopped after the pilot" in capsys.readouterr().out


def test_a_pilot_of_nothing_is_a_plain_error(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = _main(
        tmp_path, "sweep", "run", str(sweep_file), "--pilot", "0", "--no-probe"
    )

    assert status == 2
    assert "--pilot must be at least 1" in capsys.readouterr().err


def test_a_new_point_limit_ends_the_run_with_its_own_status_and_resume_finishes(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = _main(
        tmp_path, "sweep", "run", str(sweep_file), "--no-probe", "--max-new-points", "1"
    )

    output = capsys.readouterr().out
    assert status == 3  # not 0 (done), 1 (failed), 2 (usage) or 130 (cancelled)
    assert "the limit of 1 new point(s) was reached" in output
    assert "Stopped: the limit of 1 new point(s) was reached" in output
    (study,) = groups.list_studies(results=tmp_path / "results")
    assert study.run_count == 1
    assert _main(tmp_path, "sweep", "resume", study.study_id) == 0
    assert groups.get_study(study.study_id, results=tmp_path / "results").run_count == 2


def test_resume_takes_the_same_limits(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    study_id = _run_sweep_file(tmp_path, sweep_file, capsys)

    # Everything is already computed, so a limit of zero new points is never reached.
    status = _main(
        tmp_path,
        "sweep",
        "resume",
        study_id,
        "--max-new-points",
        "1",
        "--max-disk",
        "5GB",
    )

    assert status == 0
    assert "Stopped" not in capsys.readouterr().out


def test_a_disk_limit_is_read_with_units(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = _main(
        tmp_path, "sweep", "run", str(sweep_file), "--no-probe", "--max-disk", "1B"
    )

    assert status == 3
    assert "the disk limit of 1 B was reached" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (["--max-hours", "0"], "max_wall_seconds must be positive"),
        (["--max-new-points", "0"], "max_new_points must be positive"),
        (["--max-disk", "lots"], "is not a size"),
        (["--max-disk", "0"], "not a positive size"),
    ],
)
def test_a_budget_that_makes_no_sense_is_a_plain_error(
    tmp_path: Path,
    sweep_file: Path,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
    message: str,
) -> None:
    status = _main(tmp_path, "sweep", "run", str(sweep_file), "--no-probe", *flags)

    assert status == 2
    assert message in capsys.readouterr().err
    assert groups.list_studies(results=tmp_path / "results") == []  # nothing created


def test_plan_prints_the_experiments_digest_and_it_is_stable(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _main(tmp_path, "sweep", "plan", str(sweep_file), "--no-probe")
    first = capsys.readouterr().out
    _main(tmp_path, "sweep", "plan", str(sweep_file), "--no-probe")
    second = capsys.readouterr().out

    digest = next(line for line in first.splitlines() if line.startswith("digest "))
    assert (
        digest.startswith("digest sha256:")
        and len(digest) == len("digest sha256:") + 64
    )
    assert digest in second


def test_spec_writes_the_file_that_regenerates_the_sweep(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    study_id = _run_sweep_file(tmp_path, sweep_file, capsys)
    _main(tmp_path, "sweep", "plan", str(sweep_file), "--no-probe")
    original = next(
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("digest ")
    )

    assert _main(tmp_path, "sweep", "spec", study_id) == 0
    regenerated = tmp_path / "regenerated.yaml"
    regenerated.write_text(capsys.readouterr().out, encoding="utf-8")
    _main(tmp_path, "sweep", "plan", str(regenerated), "--no-probe")

    again = next(
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("digest ")
    )
    assert again == original  # the supplement's file is the same experiment
    assert "name: Demes" in regenerated.read_text(encoding="utf-8")


def test_verify_says_in_one_line_that_everything_matched_and_records_it(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    study_id = _run_sweep_file(tmp_path, sweep_file, capsys)

    status = _main(tmp_path, "sweep", "verify", study_id)

    lines = capsys.readouterr().out.strip().splitlines()
    assert status == 0
    assert len(lines) == 1
    assert lines[0].startswith(
        "Verified: all 2 point(s) recomputed to exactly the stored"
    )
    record = json.loads(
        (
            tmp_path
            / "results"
            / ".fim"
            / "studies"
            / "sweep-status"
            / f"{study_id}.verified.json"
        ).read_text(encoding="utf-8")
    )
    assert record["result"] == "identical"


def test_verify_with_nothing_finished_says_so(
    tmp_path: Path, sweep_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec, name = spec_from_config(yaml.safe_load(sweep_file.read_text("utf-8")))
    study = create_sweep_study(
        spec, enumerate_points(spec), name or "x", results=tmp_path / "results"
    )

    assert _main(tmp_path, "sweep", "verify", study.study_id) == 0

    # The verify run computed the points itself, so there was nothing to check.
    assert "Nothing to verify" in capsys.readouterr().out


def test_verify_reports_a_difference_and_exits_nonzero(
    tmp_path: Path,
    sweep_file: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    study_id = _run_sweep_file(tmp_path, sweep_file, capsys)

    class Tampering:
        def run_point(self, *args: Any, **kwargs: Any) -> Any:
            result = LocalPointRunner().run_point(*args, **kwargs)
            assert isinstance(result, Path)
            manifest_path = result / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifacts"]["trajectory"]["sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            return result

    monkeypatch.setattr("fim.cli_sweep.LocalPointRunner", Tampering)

    status = _main(tmp_path, "sweep", "verify", study_id)

    output = capsys.readouterr().out
    assert status == 1
    assert output.startswith("DIFFERS: 2 of 2 point(s) recomputed to different results")


def test_keys_lists_the_curated_keys_first_then_the_generic_ones(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _main(tmp_path, "sweep", "keys") == 0
    lines = capsys.readouterr().out.splitlines()

    names = [line.split()[0] for line in lines]
    assert names[:6] == ["N", "d", "m", "mu", "topology", "deme_weighting"]
    assert {"n_loci", "mutation_model", "precision"} <= set(names)
    mutation = next(line for line in lines if line.startswith("mutation_model"))
    assert "infinite_alleles | finite_alleles" in mutation


def test_keys_json_describes_each_key_with_its_type(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _main(tmp_path, "sweep", "keys", "--json") == 0
    entries = {entry["key"]: entry for entry in json.loads(capsys.readouterr().out)}

    assert entries["N"]["generic"] is False and entries["N"]["unit"] == "individuals"
    assert entries["n_loci"]["generic"] is True
    assert entries["n_loci"]["kind"] == "int"
    assert entries["track_expensive_statistics"]["choices"] == ["true", "false"]


_HEADER = _SWEEP_FILE.split("sweep:", maxsplit=1)[0]


def test_plan_reads_a_zip_sweep_and_a_points_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    zipped = tmp_path / "zip.yaml"
    zipped.write_text(
        _HEADER + "sweep:\n  strategy: zip\n  axes:\n    d: [2, 3, 4]\n"
        "    m: [0.1, 0.05, 0.01]\n",
        encoding="utf-8",
    )
    (tmp_path / "design.csv").write_text("d,m\n4,0.01\n2,0.1\n", encoding="utf-8")
    listed = tmp_path / "list.yaml"
    listed.write_text(_HEADER + "sweep:\n  points_file: design.csv\n", encoding="utf-8")

    assert _main(tmp_path, "sweep", "plan", str(zipped), "--no-probe") == 0
    zip_out = capsys.readouterr().out
    assert _main(tmp_path, "sweep", "plan", str(listed), "--no-probe") == 0
    list_out = capsys.readouterr().out

    assert "3 valid point(s), 0 invalid" in zip_out
    assert "2 valid point(s), 0 invalid" in list_out
    rows = [
        line.split() for line in list_out.splitlines() if line[:5].strip().isdigit()
    ]
    assert [row[1:3] for row in rows] == [["4", "0.01"], ["2", "0.1"]]


def test_a_zip_sweep_of_unequal_axes_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text(
        _HEADER + "sweep:\n  strategy: zip\n  axes:\n    d: [2, 3]\n    m: [0.1]\n",
        encoding="utf-8",
    )

    status = _main(tmp_path, "sweep", "plan", str(broken), "--no-probe")

    assert status != 0
    assert "equal lengths" in capsys.readouterr().err
