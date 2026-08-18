"""Functional tests for researcher-facing command workflows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

import pytest
import yaml

from fim import __version__, cli


def _write_config(path: Path, **updates: object) -> None:
    """Write a tiny deterministic YAML config."""
    config: dict[str, object] = {
        "N": 20,
        "d": 2,
        "m": 0.1,
        "mu": 0.01,
        "seed": 20260814,
        "loci": [{"locus_id": 1, "length": 200}],
        "initial_allele_count": 2,
        "initial_concentration": 1.0,
        "deme_weighting": "size",
        "convergence_statistic": "D",
        "convergence_window": 4,
        "convergence_tolerance": 1.0,
        "max_generations": 10,
    }
    config.update(updates)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def test_run_writes_exactly_four_documented_artifacts(tmp_path: Path) -> None:
    """A real seeded run produces the complete v1 output set."""
    config = tmp_path / "run.yaml"
    output = tmp_path / "output"
    _write_config(config)

    status = cli.main(["run", str(config), "--output", str(output), "--quiet"])

    assert status == 0
    assert {path.name for path in output.iterdir()} == {
        "trajectory.jsonl",
        "manifest.json",
        "report.json",
        "scatter.png",
    }
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert set(report) >= {
        "run_id",
        "generation",
        "converged",
        "converged_on",
        "G_ST",
        "D",
        "E_ST",
        "K_ST",
        "H_S",
        "H_T",
    }


def test_run_accepts_per_deme_population_sizes(tmp_path: Path) -> None:
    """A config with a per-deme N list runs end to end through the CLI."""
    config = tmp_path / "run.yaml"
    output = tmp_path / "output"
    _write_config(config, N=[12, 30])

    status = cli.main(["run", str(config), "--output", str(output), "--quiet"])

    assert status == 0
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["parameters"]["N"] == [12, 30]
    assert (output / "scatter.png").exists()


def test_run_accepts_an_asymmetric_migration_matrix(tmp_path: Path) -> None:
    """A config with a full d x d migration matrix runs end to end through the CLI."""
    config = tmp_path / "run.yaml"
    output = tmp_path / "output"
    matrix = [
        [0.9, 0.05, 0.05],
        [0.1, 0.8, 0.1],
        [0.0, 0.2, 0.8],
    ]
    _write_config(config, d=3, m=matrix)

    status = cli.main(["run", str(config), "--output", str(output), "--quiet"])

    assert status == 0
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["parameters"]["m"] == matrix
    assert (output / "scatter.png").exists()


def test_run_accepts_loci_with_unequal_lengths(tmp_path: Path) -> None:
    """A config with genuinely different per-locus lengths runs end to end."""
    config = tmp_path / "run.yaml"
    output = tmp_path / "output"
    _write_config(
        config,
        loci=[
            {"locus_id": 1, "length": 50},
            {"locus_id": 2, "length": 8_000},
        ],
    )

    status = cli.main(["run", str(config), "--output", str(output), "--quiet"])

    assert status == 0
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["parameters"]["loci"] == [
        {"locus_id": 1, "length": 50},
        {"locus_id": 2, "length": 8_000},
    ]
    assert (output / "scatter.png").exists()


def test_run_accepts_several_convergence_statistics(tmp_path: Path) -> None:
    """A config watching several statistics with a combinator runs end to end."""
    config = tmp_path / "run.yaml"
    output = tmp_path / "output"
    _write_config(
        config,
        convergence_statistic=["D", "G_ST"],
        convergence_combinator="any",
    )

    status = cli.main(["run", str(config), "--output", str(output), "--quiet"])

    assert status == 0
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["parameters"]["convergence_statistic"] == ["D", "G_ST"]
    assert manifest["parameters"]["convergence_combinator"] == "any"
    assert manifest["convergence"]["statistic"] == ["D", "G_ST"]
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["converged_on"] == ["D", "G_ST"]
    assert (output / "scatter.png").exists()


def test_two_runs_have_identical_trajectory_and_report(tmp_path: Path) -> None:
    """Wall-clock output naming never enters persisted scientific values."""
    config = tmp_path / "run.yaml"
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_config(config)

    assert cli.main(["run", str(config), "-o", str(first), "--quiet"]) == 0
    assert cli.main(["run", str(config), "-o", str(second), "--quiet"]) == 0

    for filename in ("trajectory.jsonl", "report.json"):
        assert (first / filename).read_bytes() == (second / filename).read_bytes()


def test_stats_reanalysis_matches_live_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Persisted final state reproduces the run's scalar report."""
    config = tmp_path / "run.yaml"
    output = tmp_path / "output"
    _write_config(config)
    assert cli.main(["run", str(config), "-o", str(output), "--quiet"]) == 0

    status = cli.main(
        [
            "stats",
            str(output / "trajectory.jsonl"),
            "--q",
            "2",
        ]
    )
    printed = json.loads(capsys.readouterr().out)
    live = json.loads((output / "report.json").read_text(encoding="utf-8"))

    assert status == 0
    assert {key: printed[key] for key in live} == live
    assert printed["Differentiation_q"]["2.0"] == pytest.approx(live["D"])


def test_config_validation_names_unknown_key(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A misspelled key fails with a precise researcher-facing message."""
    config = tmp_path / "bad.yaml"
    _write_config(config, migraiton=0.5)

    status = cli.main(["run", str(config), "-o", str(tmp_path / "output")])

    assert status == 2
    assert "migraiton" in capsys.readouterr().err


def test_init_writes_parseable_starter_config(tmp_path: Path) -> None:
    """The initialization command creates the documented starter file."""
    output = tmp_path / "example-run.yaml"

    assert cli.main(["init", "--output", str(output)]) == 0

    params = cli.load_config(output)
    assert params.seed == 20260814
    assert params.N == 450


def test_default_paths_use_project_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default configuration and run outputs stay under project-root/results."""
    monkeypatch.setattr(cli, "_project_root", lambda: tmp_path)

    assert cli._results_directory() == tmp_path / "results"
    assert cli.main(["init"]) == 0
    assert (tmp_path / "results" / "example-run.yaml").is_file()
    assert cli._default_output_directory().parent == tmp_path / "results"


def test_project_root_falls_back_to_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Installed and frozen applications never write inside their package."""
    working_directory = tmp_path / "working"
    working_directory.mkdir()
    monkeypatch.chdir(working_directory)
    monkeypatch.setattr(
        cli,
        "__file__",
        str(tmp_path / "installed" / "site-packages" / "fim" / "cli.py"),
    )

    assert cli._project_root() == working_directory


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("v1.1.0", "newer fim release"),
        (f"v{__version__}", "is current"),
        ("v0.9.0", "is newer than"),
    ],
)
def test_update_check_messages_are_fully_mocked(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tag: str,
    expected: str,
) -> None:
    """Newer, equal, and older responses never require live network access."""

    def release() -> tuple[str, str]:
        return tag, "https://example.invalid/release"

    monkeypatch.setattr(cli, "_latest_release", release)

    assert cli.main(["update", "--check"]) == 0
    assert expected in capsys.readouterr().out


def test_version_reads_single_source_of_truth(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The global version flag reports the bundled package version."""
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])

    assert exit_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_load_config_requires_a_mapping_root(tmp_path: Path) -> None:
    """YAML documents that are not objects fail before parameter parsing."""
    path = tmp_path / "invalid.yaml"
    path.write_text("- not-a-mapping\n", encoding="utf-8")
    with pytest.raises(ValueError, match="root must be a mapping"):
        cli.load_config(path)


def test_init_refuses_existing_file_unless_forced(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The starter command protects existing user configuration by default."""
    output = tmp_path / "example.yaml"
    assert cli.main(["init", "--output", str(output)]) == 0
    assert cli.main(["init", "--output", str(output)]) == 2
    assert "already exists" in capsys.readouterr().err
    assert cli.main(["init", "--output", str(output), "--force"]) == 0


def test_run_rejects_replicates_and_existing_artifacts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI runs remain scalar and never overwrite scientific artifacts."""
    config = tmp_path / "run.yaml"
    _write_config(config, n_replicates=2)
    output = tmp_path / "output"
    assert cli.main(["run", str(config), "-o", str(output)]) == 2
    assert "n_replicates" in capsys.readouterr().err

    _write_config(config)
    output.mkdir()
    (output / "report.json").write_text("{}", encoding="utf-8")
    assert cli.main(["run", str(config), "-o", str(output)]) == 2
    assert "already contains" in capsys.readouterr().err


def test_run_progress_output_describes_result(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Non-quiet runs print the stable identity, result, and artifact paths."""
    config = tmp_path / "run.yaml"
    output = tmp_path / "output"
    _write_config(config)
    assert cli.main(["run", str(config), "-o", str(output)]) == 0
    rendered = capsys.readouterr().out
    assert "Running run-" in rendered
    assert "Trajectory" in rendered
    assert "Report" in rendered


def test_stats_supports_explicit_generation_and_json_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Re-analysis can target a generation and persist its report."""
    config = tmp_path / "run.yaml"
    output = tmp_path / "output"
    report_path = tmp_path / "stats.json"
    _write_config(config)
    assert cli.main(["run", str(config), "-o", str(output), "--quiet"]) == 0
    assert (
        cli.main(
            [
                "stats",
                str(output / "trajectory.jsonl"),
                "--generation",
                "0",
                "--output",
                str(report_path),
            ]
        )
        == 0
    )
    rendered = json.loads(report_path.read_text(encoding="utf-8"))
    assert rendered["reason"] == "re-analysis"
    assert capsys.readouterr().out


def test_stats_reports_empty_and_unknown_generations(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Stats errors distinguish missing run rows from missing generations."""
    config = tmp_path / "run.yaml"
    output = tmp_path / "output"
    _write_config(config)
    assert cli.main(["run", str(config), "-o", str(output), "--quiet"]) == 0
    trajectory = output / "trajectory.jsonl"
    corrupted = trajectory.read_text(encoding="utf-8").replace(
        '"run_id":"run-',
        '"run_id":"other-',
    )
    trajectory.write_text(
        corrupted,
        encoding="utf-8",
    )
    assert cli.main(["stats", str(trajectory)]) == 2
    assert "no rows" in capsys.readouterr().err

    assert cli.main(["run", str(config), "-o", str(output), "--quiet"]) == 2
    output = tmp_path / "second"
    assert cli.main(["run", str(config), "-o", str(output), "--quiet"]) == 0
    assert (
        cli.main(["stats", str(output / "trajectory.jsonl"), "--generation", "999"])
        == 2
    )
    assert "no generation" in capsys.readouterr().err


class _ReleaseResponse:
    """Minimal context-managed response for the urllib release client."""

    def __init__(self, payload: object) -> None:
        self._payload = payload

    def __enter__(self) -> _ReleaseResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, *_args: object) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


@pytest.mark.parametrize(
    "error",
    [HTTPError("https://example.invalid", 500, "bad", {}, None), URLError("offline")],
)
def test_fetch_latest_release_wraps_network_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    """Network failures become the documented runtime error contract."""

    def fail(*args: object, **kwargs: object) -> Any:
        raise error

    monkeypatch.setattr(cli, "urlopen", fail)
    with pytest.raises(RuntimeError, match="update check failed"):
        cli._fetch_latest_release()


def test_fetch_latest_release_rejects_non_object_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful HTTP response still requires an object payload."""
    monkeypatch.setattr(
        cli,
        "urlopen",
        lambda _request, **_kwargs: _ReleaseResponse(["not", "an", "object"]),
    )
    with pytest.raises(RuntimeError, match="non-object"):
        cli._fetch_latest_release()


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "tag_name"),
        ({"tag_name": "v1.0.0"}, "html_url"),
        ({"tag_name": "v1", "html_url": "x"}, "not semantic"),
        ({"tag_name": "v1.0.x", "html_url": "x"}, "not semantic"),
        ({"tag_name": "v-1.0.0", "html_url": "x"}, "not semantic"),
    ],
)
def test_latest_release_validates_release_fields(
    payload: dict[str, object],
    message: str,
) -> None:
    """Update checks reject incomplete and malformed release metadata."""
    with pytest.raises(RuntimeError, match=message):
        cli._latest_release(lambda: payload)


@pytest.mark.parametrize(
    ("current", "latest", "expected"),
    [("1.0.0", "1.0.0", 0), ("1.0.0", "1.0.1", -1), ("1.0.1", "1.0.0", 1)],
)
def test_version_comparison_and_format_helpers_are_stable(
    current: str,
    latest: str,
    expected: int,
) -> None:
    """Semantic versions and optional report values have deterministic output."""
    assert cli._compare_versions(current, latest) == expected
    assert cli._format_optional(None) == "undefined"
    assert cli._format_optional(1.23456789) == "1.23457"


@pytest.mark.parametrize("value", ["1.0", "1.0.0.0", "1.a.0", "-1.0.0"])
def test_version_parser_rejects_non_semantic_values(value: str) -> None:
    """Release version parsing requires exactly three non-negative integers."""
    with pytest.raises(RuntimeError, match="not semantic"):
        cli._version_parts(value)


def test_update_requires_explicit_opt_in() -> None:
    """The update command never accesses the network without --check."""
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["update"])
    assert exit_info.value.code == 2
