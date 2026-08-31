"""Functional tests for researcher-facing command workflows."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from fim import __version__, cli, paths, update
from fim.persistence.jsonl_store import JSONLTrajectoryStore
from fim.persistence.manifest import hash_file, read_batch_manifest


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


def test_run_accepts_stepping_stone_topology_sugar_for_m(tmp_path: Path) -> None:
    """A config with a compact ring topology for `m` runs end to end."""
    config = tmp_path / "run.yaml"
    output = tmp_path / "output"
    _write_config(config, d=8, m={"topology": "ring", "rate": 0.2})

    status = cli.main(["run", str(config), "--output", str(output), "--quiet"])

    assert status == 0
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    parameters_m = manifest["parameters"]["m"]
    assert isinstance(parameters_m, list)
    assert len(parameters_m) == 8
    assert all(abs(sum(row) - 1.0) < 1e-9 for row in parameters_m)
    assert (output / "scatter.png").exists()


def test_run_accepts_stochastic_migrant_sampling(tmp_path: Path) -> None:
    """A config opting in to random migrant counts runs end to end and reproduces."""
    config = tmp_path / "run.yaml"
    first_output = tmp_path / "first"
    second_output = tmp_path / "second"
    _write_config(config, migrant_sampling="stochastic")

    first_status = cli.main(
        ["run", str(config), "--output", str(first_output), "--quiet"]
    )
    second_status = cli.main(
        ["run", str(config), "--output", str(second_output), "--quiet"]
    )

    assert first_status == 0
    assert second_status == 0
    manifest = json.loads((first_output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["parameters"]["migrant_sampling"] == "stochastic"
    first_report = (first_output / "report.json").read_text(encoding="utf-8")
    second_report = (second_output / "report.json").read_text(encoding="utf-8")
    assert first_report == second_report
    assert (first_output / "scatter.png").exists()


def test_run_accepts_finite_alleles_mutation_model(tmp_path: Path) -> None:
    """A config opting in to a bounded allele state space runs end to end."""
    config = tmp_path / "run.yaml"
    first_output = tmp_path / "first"
    second_output = tmp_path / "second"
    _write_config(
        config,
        mu=0.1,
        loci=[{"locus_id": 1, "length": 1}],
        mutation_model="finite_alleles",
    )

    first_status = cli.main(
        ["run", str(config), "--output", str(first_output), "--quiet"]
    )
    second_status = cli.main(
        ["run", str(config), "--output", str(second_output), "--quiet"]
    )

    assert first_status == 0
    assert second_status == 0
    manifest = json.loads((first_output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["parameters"]["mutation_model"] == "finite_alleles"
    first_report = (first_output / "report.json").read_text(encoding="utf-8")
    second_report = (second_output / "report.json").read_text(encoding="utf-8")
    assert first_report == second_report
    trajectory = (first_output / "trajectory.jsonl").read_text(encoding="utf-8")
    allele_ids = {
        json.loads(line)["allele_id"] for line in trajectory.splitlines() if line
    }
    assert allele_ids <= set(range(4))
    assert (first_output / "scatter.png").exists()


def test_run_accepts_a_per_base_mutation_rate(tmp_path: Path) -> None:
    """A config using `mu_b` instead of `mu` runs end to end.

    `mu_b` and `mu` are mutually exclusive, so this config is built
    directly rather than through `_write_config`'s `mu`-including base.
    """
    config = tmp_path / "run.yaml"
    output = tmp_path / "output"
    config_body: dict[str, object] = {
        "N": 20,
        "d": 2,
        "m": 0.1,
        "mu_b": 0.0001,
        "seed": 20260822,
        "loci": [
            {"locus_id": 1, "length": 5},
            {"locus_id": 2, "length": 50},
        ],
        "convergence_window": 4,
        "convergence_tolerance": 1.0,
        "max_generations": 10,
    }
    config.write_text(yaml.safe_dump(config_body, sort_keys=False), encoding="utf-8")

    status = cli.main(["run", str(config), "--output", str(output), "--quiet"])

    assert status == 0
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    parameters_mu = manifest["parameters"]["mu"]
    assert isinstance(parameters_mu, list)
    assert len(parameters_mu) == 2
    assert parameters_mu[0] != parameters_mu[1]
    assert "mu_b" not in manifest["parameters"]
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


def test_stats_q1_agrees_with_e_st_under_size_weighting_and_unequal_demes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`fim stats --q 1` agrees with the run's own `E_ST` when demes are
    unequal in size and `deme_weighting` is `"size"`.

    Regression test for S2: `q = 0` and `q = 2` already matched `K_ST`
    and `D` exactly, which isolated `deme_weighting` as the sole cause
    of a 32% live discrepancy between `Differentiation_1` and `E_ST` —
    `_differentiation_q_for_state` called `differentiation_q` with no
    weights at all, while `report_for_state` passed size weights
    whenever `deme_weighting` was `"size"`, the default. Unequal `N` is
    required to expose it: with every deme the same size, `"size"` and
    `"equal"` weighting are numerically identical.
    """
    config = tmp_path / "run.yaml"
    output = tmp_path / "output"
    _write_config(config, N=[12, 30])
    assert cli.main(["run", str(config), "-o", str(output), "--quiet"]) == 0

    status = cli.main(
        [
            "stats",
            str(output / "trajectory.jsonl"),
            "--q",
            "0",
            "--q",
            "1",
            "--q",
            "2",
        ]
    )
    printed = json.loads(capsys.readouterr().out)
    live = json.loads((output / "report.json").read_text(encoding="utf-8"))

    assert status == 0
    assert printed["Differentiation_q"]["0.0"] == pytest.approx(live["K_ST"])
    assert printed["Differentiation_q"]["1.0"] == pytest.approx(live["E_ST"])
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


def test_run_rejects_negative_seed_before_creating_the_output_directory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A negative `seed` fails at config load, before any run artifact exists.

    Regression test: `load_config` runs before
    `output_directory.mkdir(...)` in both `_command_run_scalar` and
    `_command_run_batch`, so a config-level rejection here means the CLI
    never creates an output directory for a run that could not possibly
    start — unlike the prior behavior, where an invalid seed passed
    config validation and only failed once NumPy's PCG64 rejected it deep
    inside `fim()`.
    """
    config = tmp_path / "bad.yaml"
    _write_config(config, seed=-1)
    output = tmp_path / "output"

    status = cli.main(["run", str(config), "-o", str(output), "--quiet"])

    assert status == 2
    assert "seed must be at least 0" in capsys.readouterr().err
    assert not output.exists()


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
    """Default configuration and run outputs stay under project-root/results.

    `_project_root`/`_results_directory`/`_default_output_directory`'s own
    resolution logic is tested directly in `test/test_paths.py`
    (Milestone G0, `doc/fim-gui-design.md` §12,13) against `fim.paths`
    — this test only confirms `cli._command_init`
    still calls through to it correctly, by patching the one shared root
    resolver both front ends now use.
    """
    monkeypatch.setattr(paths, "project_root", lambda: tmp_path)

    assert cli.main(["init"]) == 0
    assert (tmp_path / "results" / "example-run.yaml").is_file()


def _newer_version(version: str) -> str:
    """Return a three-part semantic version that compares as newer.

    Args:
        version: A ``major.minor.patch`` version string.

    Returns:
        The same version with its patch component incremented — always
        greater under `fim.cli._compare_versions`, regardless of what
        `version` actually is. Bumping only the patch (never hardcoding a
        specific version) keeps this test from going stale the next time
        the project's own version changes, which is exactly what broke
        the previous hardcoded `"v1.1.0"` the moment `1.1.0` shipped.
    """
    major, minor, patch = (int(part) for part in version.split("."))
    return f"{major}.{minor}.{patch + 1}"


def _older_version(version: str) -> str:
    """Return a three-part semantic version that compares as older.

    Args:
        version: A ``major.minor.patch`` version string.

    Returns:
        A version guaranteed to compare as less than `version` under
        `fim.update.compare_versions`: the patch component decremented, or
        the minor/major component decremented and reset below it when
        patch (and minor) are already zero.
    """
    major, minor, patch = (int(part) for part in version.split("."))
    if patch > 0:
        return f"{major}.{minor}.{patch - 1}"
    if minor > 0:
        return f"{major}.{minor - 1}.0"
    return f"{major - 1}.0.0"


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        (f"v{_newer_version(__version__)}", "newer fim release"),
        (f"v{__version__}", "is current"),
        (f"v{_older_version(__version__)}", "is newer than"),
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

    monkeypatch.setattr(update, "latest_release", release)

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


def test_run_scalar_never_overwrites_an_existing_output_directory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A scalar run refuses any pre-existing output directory outright.

    `_atomic_directory` rejects `output_directory` if it already
    exists at all — stricter than the prior contract, which only
    rejected the four specific artifact filenames already being
    present. Populating the directory with something else entirely is
    still enough to trigger it, without depending on the exact
    filenames the run would otherwise write.
    """
    config = tmp_path / "run.yaml"
    _write_config(config)
    output = tmp_path / "output"
    output.mkdir()
    (output / "report.json").write_text("{}", encoding="utf-8")

    assert cli.main(["run", str(config), "-o", str(output)]) == 2
    assert "already exists" in capsys.readouterr().err


def test_run_scalar_rejects_an_empty_pre_existing_output_directory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An empty pre-existing directory is rejected too, not just a populated one.

    Regression test for the stricter output-directory contract: before atomic
    publishing, an empty `-o` directory the caller had already created
    (e.g. via `mkdir -p`) was silently accepted and written into in
    place. `_atomic_directory` now requires the final path to not exist
    at all, so its single rename is unambiguous.
    """
    config = tmp_path / "run.yaml"
    _write_config(config)
    output = tmp_path / "output"
    output.mkdir()

    assert cli.main(["run", str(config), "-o", str(output)]) == 2
    assert "already exists" in capsys.readouterr().err
    assert not any(output.iterdir())


def test_run_scalar_leaves_no_trace_when_interrupted_mid_trajectory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure while still writing generations leaves no output directory.

    Failure-injection test for the write boundary: the third
    `write_generation` call (well after the temporary directory has a
    real, partial `trajectory.jsonl` on disk) raises, simulating an
    interruption mid-run. `output_directory` must not exist afterward —
    `_atomic_directory` never publishes a directory the `with` block
    didn't finish populating, regardless of how far into it the failure
    happened.
    """
    config = tmp_path / "run.yaml"
    _write_config(config, max_generations=10)
    output = tmp_path / "output"
    original_write_generation = JSONLTrajectoryStore.write_generation
    calls = {"count": 0}

    def flaky_write_generation(self: Any, *args: Any, **kwargs: Any) -> None:
        calls["count"] += 1
        if calls["count"] == 3:
            raise RuntimeError("simulated write failure")
        original_write_generation(self, *args, **kwargs)

    monkeypatch.setattr(
        JSONLTrajectoryStore, "write_generation", flaky_write_generation
    )

    assert cli.main(["run", str(config), "-o", str(output), "--quiet"]) == 2
    assert calls["count"] == 3
    assert not output.exists()
    assert not list(tmp_path.glob(".output.*"))


def test_run_scalar_leaves_no_trace_when_the_report_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure writing `report.json` leaves no output directory.

    Failure-injection test for the report boundary: by this point the
    temporary directory already has a real, complete `trajectory.jsonl`
    on disk (the run itself finished), but the failure still means
    `output_directory` must not exist afterward.
    """
    config = tmp_path / "run.yaml"
    _write_config(config)
    output = tmp_path / "output"
    original_write_report = cli.write_report

    def flaky_write_report(path: Path, value: Mapping[str, object]) -> None:
        if Path(path).name == "report.json":
            raise RuntimeError("simulated report write failure")
        original_write_report(path, value)

    monkeypatch.setattr(cli, "write_report", flaky_write_report)

    assert cli.main(["run", str(config), "-o", str(output), "--quiet"]) == 2
    assert not output.exists()
    assert not list(tmp_path.glob(".output.*"))


def test_run_scalar_leaves_no_trace_when_the_plot_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure rendering `scatter.png` leaves no output directory.

    Failure-injection test for the plot boundary: `trajectory.jsonl`
    and `report.json` are both already real and complete in the
    temporary directory when this fails, but the whole run still must
    not appear at `output_directory`.
    """
    config = tmp_path / "run.yaml"
    _write_config(config)
    output = tmp_path / "output"

    def failing_plot(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("simulated plot failure")

    monkeypatch.setattr(cli, "plot_frequency_scatter", failing_plot)

    assert cli.main(["run", str(config), "-o", str(output), "--quiet"]) == 2
    assert not output.exists()
    assert not list(tmp_path.glob(".output.*"))


def test_run_batch_produces_replicate_and_summary_artifacts(
    tmp_path: Path,
) -> None:
    """`n_replicates > 1` writes one subdirectory per replicate plus a summary."""
    config = tmp_path / "run.yaml"
    _write_config(config, n_replicates=3)
    output = tmp_path / "output"

    status = cli.main(
        ["run", str(config), "-o", str(output), "--sequential", "--quiet"]
    )

    assert status == 0
    assert {path.name for path in output.iterdir()} == {
        "replicate-001",
        "replicate-002",
        "replicate-003",
        "summary.json",
        "manifest.json",
    }
    for replicate in ("replicate-001", "replicate-002", "replicate-003"):
        assert {path.name for path in (output / replicate).iterdir()} == {
            "trajectory.jsonl",
            "manifest.json",
            "report.json",
            "scatter.png",
        }
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["D"]["sample_count"] == 3
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["replicate_count"] == 3
    assert len(manifest["replicate_run_ids"]) == 3


def test_run_batch_manifest_is_schema_versioned_and_digest_verified(
    tmp_path: Path,
) -> None:
    """The batch `manifest.json` parses with `read_batch_manifest` and its
    recorded digests match the real on-disk `summary.json` and each
    replicate's own `manifest.json`.

    Regression test for S10: the batch manifest used to be a raw,
    unversioned dict with no artifact digests — `read_manifest` (and
    now `read_batch_manifest`) rejected it outright, and nothing
    detected an edited or truncated `summary.json` or child manifest
    after the fact.
    """
    config = tmp_path / "run.yaml"
    _write_config(config, n_replicates=3)
    output = tmp_path / "output"

    status = cli.main(
        ["run", str(config), "-o", str(output), "--sequential", "--quiet"]
    )

    assert status == 0
    manifest = read_batch_manifest(output / "manifest.json")
    assert manifest.schema_version >= 1
    assert manifest.artifacts is not None
    assert manifest.artifacts["summary"] == hash_file(output / "summary.json")
    for replicate_run_id in manifest.replicate_run_ids:
        directory = cli._replicate_output_directory(
            output, manifest.run_id, replicate_run_id
        )
        assert manifest.artifacts[directory.name] == hash_file(
            directory / "manifest.json"
        )


def test_run_batch_defaults_to_parallel_workers(tmp_path: Path) -> None:
    """Omitting `--sequential`/`--workers` runs the batch through a real pool."""
    config = tmp_path / "run.yaml"
    _write_config(config, n_replicates=2)
    output = tmp_path / "output"

    status = cli.main(["run", str(config), "-o", str(output), "--quiet"])

    assert status == 0
    assert (output / "replicate-001" / "trajectory.jsonl").exists()
    assert (output / "replicate-002" / "trajectory.jsonl").exists()


def test_run_batch_respects_an_explicit_worker_count(tmp_path: Path) -> None:
    """`--workers` overrides the default CPU-count worker pool size."""
    config = tmp_path / "run.yaml"
    _write_config(config, n_replicates=2)
    output = tmp_path / "output"

    status = cli.main(
        ["run", str(config), "-o", str(output), "--workers", "1", "--quiet"]
    )

    assert status == 0
    assert (output / "replicate-002" / "report.json").exists()


def test_run_batch_rejects_zero_workers(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--workers 0` must reach the engine's own validation, not become
    the CPU-count default.

    `0 or _cpu_count()` treated `0` as falsy and silently substituted the
    default; only a genuinely unset `--workers` (`None`) should fall
    back to `_cpu_count()`.
    """
    config = tmp_path / "run.yaml"
    _write_config(config, n_replicates=2)
    output = tmp_path / "output"

    status = cli.main(
        ["run", str(config), "-o", str(output), "--workers", "0", "--quiet"]
    )

    assert status == 2
    assert "max_workers must be at least 1" in capsys.readouterr().err
    assert not output.exists()


def test_run_rejects_workers_combined_with_sequential(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--workers` and `--sequential` are declared mutually exclusive in
    argparse, rather than one silently taking precedence over the other
    inside the handler.
    """
    config = tmp_path / "run.yaml"
    _write_config(config, n_replicates=2)
    output = tmp_path / "output"

    with pytest.raises(SystemExit) as exit_info:
        cli.main(
            [
                "run",
                str(config),
                "-o",
                str(output),
                "--workers",
                "2",
                "--sequential",
                "--quiet",
            ]
        )

    assert exit_info.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_run_batch_adaptive_tolerance_can_stop_before_n_replicates(
    tmp_path: Path,
) -> None:
    """A generous `replicate_tolerance` writes fewer than `n_replicates` dirs."""
    config = tmp_path / "run.yaml"
    _write_config(
        config,
        n_replicates=10,
        replicate_minimum=2,
        replicate_tolerance=1000.0,
    )
    output = tmp_path / "output"

    status = cli.main(
        ["run", str(config), "-o", str(output), "--sequential", "--quiet"]
    )

    assert status == 0
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["replicate_count"] == 2


def test_run_batch_parallel_adaptive_stop_leaves_no_orphan_replicate_directories(
    tmp_path: Path,
) -> None:
    """A parallel batch's published `replicate-*` set exactly matches the manifest.

    Regression test for S1: `fim.engine._run_batch_parallel` applies an
    adaptive `replicate_tolerance` stop only after a whole concurrent
    worker batch completes, in ascending replicate order — a worker
    beyond the replicate that triggered the stop still runs to
    completion and fully writes its own `replicate-*` directory before
    its result is discarded. `--workers 4` with a generous tolerance
    and a low `replicate_minimum` reliably stops mid-batch here, so
    without pruning, the extra workers' directories would publish
    complete, present on disk, and absent from `summary.json` and
    `manifest.json`.
    """
    config = tmp_path / "run.yaml"
    _write_config(
        config,
        n_replicates=10,
        replicate_minimum=2,
        replicate_tolerance=1000.0,
    )
    output = tmp_path / "output"

    status = cli.main(
        ["run", str(config), "-o", str(output), "--workers", "4", "--quiet"]
    )

    assert status == 0
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    replicate_run_ids = manifest["replicate_run_ids"]
    expected_directories = {
        cli._replicate_output_directory(output, manifest["run_id"], run_id).name
        for run_id in replicate_run_ids
    }
    published_directories = {
        entry.name for entry in output.iterdir() if entry.name.startswith("replicate-")
    }
    assert published_directories == expected_directories
    assert len(published_directories) == manifest["replicate_count"]


def test_run_batch_rejects_a_nonempty_output_directory(tmp_path: Path) -> None:
    """A batch run never writes into an already-populated directory."""
    config = tmp_path / "run.yaml"
    _write_config(config, n_replicates=2)
    output = tmp_path / "output"
    output.mkdir()
    (output / "stray-file").write_text("", encoding="utf-8")

    status = cli.main(["run", str(config), "-o", str(output), "--quiet"])

    assert status == 2


def test_run_batch_leaves_no_trace_when_a_replicate_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A batch failure partway through replicates leaves no output directory.

    Failure-injection test for the batch write boundary: the second
    replicate's artifact write raises, after the first replicate's four
    files are already real and complete in the temporary directory.
    `output_directory` — including `summary.json`, `manifest.json`, and
    every replicate subdirectory — must not exist afterward.
    """
    config = tmp_path / "run.yaml"
    _write_config(config, n_replicates=3)
    output = tmp_path / "output"
    original_write_run_artifacts = cli._write_run_artifacts
    calls = {"count": 0}

    def flaky_write_run_artifacts(*args: Any, **kwargs: Any) -> dict[str, Path]:
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("simulated replicate write failure")
        return original_write_run_artifacts(*args, **kwargs)

    monkeypatch.setattr(cli, "_write_run_artifacts", flaky_write_run_artifacts)

    status = cli.main(
        ["run", str(config), "-o", str(output), "--sequential", "--quiet"]
    )

    assert status == 2
    assert calls["count"] == 2
    assert not output.exists()
    assert not list(tmp_path.glob(".output.*"))


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


def test_stats_reports_a_tampered_trajectory_and_unknown_generations(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Stats errors distinguish a tampered trajectory from a missing generation.

    Regression test: editing the trajectory after the run
    completed — even a content-preserving edit like retagging every
    row's ``run_id`` — no longer re-analyzes silently. It now fails the
    manifest's recorded SHA-256 digest
    check before `_command_stats` ever gets to read a row, superseding
    the weaker "no rows for this run_id" diagnosis a retag used to
    produce.
    """
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
    assert "does not match its manifest" in capsys.readouterr().err

    assert cli.main(["run", str(config), "-o", str(output), "--quiet"]) == 2
    output = tmp_path / "second"
    assert cli.main(["run", str(config), "-o", str(output), "--quiet"]) == 0
    assert (
        cli.main(["stats", str(output / "trajectory.jsonl"), "--generation", "999"])
        == 2
    )
    assert "no generation" in capsys.readouterr().err


def test_format_optional_helper_is_stable() -> None:
    """The optional-statistic terminal formatter has deterministic output.

    The network-error, non-object-payload, release-field-validation,
    version-comparison, and version-parsing cases this test file used to
    carry moved out to `test/test_update.py`'s own direct unit tests
    against `fim.update` (Milestone G0, `doc/fim-gui-design.md` §12) —
    they relied on monkeypatching `urlopen` as a `cli.py`
    module attribute, which the extracted code no longer reads from this
    location. `_format_optional` stays here: it is `cli.py`'s own
    terminal-formatting helper, untouched by the extraction.
    """
    assert cli._format_optional(None) == "undefined"
    assert cli._format_optional(1.23456789) == "1.23457"


def test_update_requires_explicit_opt_in() -> None:
    """The update command never accesses the network without --check."""
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["update"])
    assert exit_info.value.code == 2
