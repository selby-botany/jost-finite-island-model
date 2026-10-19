"""A tapered run end to end: the log is cut down when the replicate finishes.

The run is the CLI's own, so the whole path is exercised: the engine writes
every frame, `write_run_artifacts` tapers the log before it takes the
digests, and the readers (`fim stats`, integrity, the run id) see a run that
is whole but has gaps in its generations.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from conftest import FAST_EXPERT_SETTINGS

from fim import cli
from fim.model.params import SimulationParams
from fim.persistence.manifest import read_manifest
from fim.persistence.retention import tapered_generations
from fim.persistence.tlog import iter_frames

_BASE: dict[str, object] = {
    "N": 20,
    "ploidy": "haploid",
    "d": 2,
    "m": 0.1,
    "mu": 0.01,
    "seed": 20260814,
    "loci": [{"locus_id": 1, "length": 200}],
    "convergence_statistic": "D",
    "precision": 1.0,
    "convergence_burn_in": 10,
    "expert": dict(FAST_EXPERT_SETTINGS),
    "max_generations": 80,
    "n_replicates": 1,
    "stop_batch_early": False,
}
_TAPERED: dict[str, object] = {
    "trajectory_retention": "tapered",
    "trajectory_keep_last": 6,
}


def _run(tmp_path: Path, name: str, **extra: object) -> Path:
    config = tmp_path / f"{name}.yaml"
    config.write_text(yaml.safe_dump({**_BASE, **extra}), encoding="utf-8")
    output = tmp_path / name
    assert cli.main(["run", str(config), "--output", str(output), "--quiet"]) == 0
    return output


def test_a_tapered_run_keeps_the_frames_the_rule_names(tmp_path: Path) -> None:
    output = _run(tmp_path, "tapered", **_TAPERED)

    manifest = read_manifest(output / "manifest.json")
    held = [frame.generation for frame in iter_frames(output / "trajectory.tlog")]
    final = manifest.generation
    assert held == tapered_generations(final, 6, 10)
    assert held[0] == 0 and 10 in held and held[-1] == final
    assert len(held) < final + 1


def test_a_tapered_runs_log_is_smaller_and_its_digest_is_recorded(
    tmp_path: Path,
) -> None:
    full = _run(tmp_path, "full")
    tapered = _run(tmp_path, "tapered", **_TAPERED)

    assert (tapered / "trajectory.tlog").stat().st_size < (
        full / "trajectory.tlog"
    ).stat().st_size
    from fim.persistence.manifest import hash_file  # noqa: PLC0415

    recorded = read_manifest(tapered / "manifest.json").artifacts
    assert recorded is not None
    assert recorded["trajectory"] == hash_file(tapered / "trajectory.tlog")


def test_tapering_does_not_change_the_run_id_or_the_statistics(
    tmp_path: Path,
) -> None:
    full = _run(tmp_path, "full")
    tapered = _run(tmp_path, "tapered", **_TAPERED)

    assert (
        read_manifest(full / "manifest.json").run_id
        == read_manifest(tapered / "manifest.json").run_id
    )
    assert (full / "report.json").read_bytes() == (tapered / "report.json").read_bytes()
    assert (full / "convergence.jsonl").read_bytes() == (
        tapered / "convergence.jsonl"
    ).read_bytes()
    parameters = read_manifest(tapered / "manifest.json").parameters
    assert parameters["trajectory_retention"] == "tapered"
    assert (
        "trajectory_retention" not in read_manifest(full / "manifest.json").parameters
    )


def test_stats_reads_the_final_generation_and_names_a_dropped_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = _run(tmp_path, "tapered", **_TAPERED)
    final = read_manifest(output / "manifest.json").generation
    dropped = next(
        g for g in range(1, final) if g not in tapered_generations(final, 6, 10)
    )

    assert cli.main(["stats", str(output / "trajectory.tlog")]) == 0
    capsys.readouterr()
    status = cli.main(
        ["stats", str(output / "trajectory.tlog"), "--generation", str(dropped)]
    )

    assert status != 0
    assert "tapered" in capsys.readouterr().err


def test_the_settings_are_validated_and_do_not_change_identity() -> None:
    plain = SimulationParams.from_mapping({**_BASE})
    tapered = SimulationParams.from_mapping({**_BASE, **_TAPERED})

    assert tapered.trajectory_retention == "tapered"
    assert tapered.identity_dict() == plain.identity_dict()
    with pytest.raises(ValueError, match="trajectory_keep_last"):
        SimulationParams.from_mapping({**_BASE, "trajectory_keep_last": 0})
    with pytest.raises(ValueError, match="trajectory_retention"):
        SimulationParams.from_mapping({**_BASE, "trajectory_retention": "sparse"})
    with pytest.raises(ValueError, match="trajectory_keep_burn_in"):
        SimulationParams.from_mapping({**_BASE, "trajectory_keep_burn_in": "yes"})


# --- the compact statistic history ---------------------------------------------

_COMPACT: dict[str, object] = {"history_format": "compact"}


def test_a_compact_run_writes_the_fixed_point_history_and_nothing_else_changes(
    tmp_path: Path,
) -> None:
    from fim.persistence.history import read_history_columns  # noqa: PLC0415
    from fim.persistence.manifest import hash_file  # noqa: PLC0415

    exact = _run(tmp_path, "exact")
    compact = _run(tmp_path, "compact", **_COMPACT)

    assert (compact / "convergence.fhist").is_file()
    assert not (compact / "convergence.jsonl").exists()
    recorded = read_manifest(compact / "manifest.json").artifacts
    assert recorded is not None
    assert recorded["convergence"] == hash_file(compact / "convergence.fhist")
    assert (exact / "report.json").read_bytes() == (
        compact / "report.json"
    ).read_bytes()
    assert (exact / "trajectory.tlog").read_bytes() == (
        compact / "trajectory.tlog"
    ).read_bytes()
    assert (
        read_manifest(exact / "manifest.json").run_id
        == read_manifest(compact / "manifest.json").run_id
    )
    step = 2.0 ** -(1 + 15)  # precision 1.0 in this fixture: 8 bits plus the guard
    exact_generations, exact_columns = read_history_columns(exact / "convergence.jsonl")
    generations, columns = read_history_columns(compact / "convergence.fhist")
    assert generations == exact_generations
    for name, values in exact_columns.items():
        assert columns[name] == pytest.approx(values, abs=max(step, 2.0**-9))


def test_a_compact_history_is_read_back_by_reanalysis(tmp_path: Path) -> None:
    from fim.reanalyze import read_persisted_convergence_history  # noqa: PLC0415

    compact = _run(tmp_path, "compact", **_COMPACT)
    manifest = read_manifest(compact / "manifest.json")

    found = read_persisted_convergence_history(compact / "trajectory.tlog", manifest)

    assert found is not None
    generations, histories = found
    assert generations[0] == 0 and generations[-1] == manifest.generation
    assert {"D", "H_S"} <= set(histories)


def test_an_edited_compact_history_no_longer_matches_its_manifest(
    tmp_path: Path,
) -> None:
    from fim.reanalyze import read_persisted_convergence_history  # noqa: PLC0415

    compact = _run(tmp_path, "compact", **_COMPACT)
    manifest = read_manifest(compact / "manifest.json")
    path = compact / "convergence.fhist"
    path.write_bytes(path.read_bytes() + b"\0")

    with pytest.raises(ValueError, match=r"convergence\.fhist does not match"):
        read_persisted_convergence_history(compact / "trajectory.tlog", manifest)


def test_a_compact_run_is_recorded_only_when_it_is_compact(tmp_path: Path) -> None:
    exact = _run(tmp_path, "exact")
    compact = _run(tmp_path, "compact", **_COMPACT)

    assert "history_format" not in read_manifest(exact / "manifest.json").parameters
    assert read_manifest(compact / "manifest.json").parameters["history_format"] == (
        "compact"
    )
    with pytest.raises(ValueError, match="history_format"):
        SimulationParams.from_mapping({**_BASE, "history_format": "tiny"})


# --- the statistics level --------------------------------------------------------

_STATISTICS: dict[str, object] = {"trajectory_retention": "statistics"}


def test_a_statistics_run_keeps_no_trajectory_and_a_decimated_history(
    tmp_path: Path,
) -> None:
    from fim.persistence.history import read_history_columns  # noqa: PLC0415

    full = _run(tmp_path, "full")
    kept = _run(tmp_path, "statistics", **_STATISTICS)

    assert {path.name for path in kept.iterdir()} == {
        "manifest.json",
        "report.json",
        "scatter.png",
        "pairwise.json",
        "history.json",
    }
    manifest = read_manifest(kept / "manifest.json")
    assert manifest.artifacts is not None
    assert set(manifest.artifacts) == {"report", "scatter", "pairwise", "history"}
    assert manifest.run_id == read_manifest(full / "manifest.json").run_id
    assert (kept / "report.json").read_bytes() == (full / "report.json").read_bytes()
    assert (kept / "pairwise.json").read_bytes() == (
        full / "pairwise.json"
    ).read_bytes()
    exact_generations, exact = read_history_columns(full / "convergence.jsonl")
    generations, columns = read_history_columns(kept / "history.json")
    assert generations[0] == 0 and generations[-1] == exact_generations[-1]
    for name, values in columns.items():
        picked = [exact[name][exact_generations.index(g)] for g in generations]
        assert values == picked  # decimated, not rounded


def test_a_statistics_batch_keeps_the_same_summary_as_a_full_one(
    tmp_path: Path,
) -> None:
    full = _run(tmp_path, "full", n_replicates=3)
    kept = _run(tmp_path, "statistics", n_replicates=3, **_STATISTICS)

    assert (full / "summary.json").read_bytes() == (kept / "summary.json").read_bytes()
    for replicate in sorted(kept.glob("replicate-*")):
        assert not (replicate / "trajectory.tlog").exists()
        assert (replicate / "history.json").is_file()
        assert (replicate / "report.json").read_bytes() == (
            full / replicate.name / "report.json"
        ).read_bytes()


def test_a_statistics_runs_receipt_verifies_without_a_trajectory(
    tmp_path: Path,
) -> None:
    from fim.persistence.run_lifecycle import RunKind  # noqa: PLC0415
    from fim.persistence.run_receipt import receipt_problem  # noqa: PLC0415

    kept = _run(tmp_path, "statistics", **_STATISTICS)
    run_id = read_manifest(kept / "manifest.json").run_id

    assert receipt_problem(kept, kind=RunKind.SCALAR, run_id=run_id) is None
    (kept / "history.json").write_text("{}")
    assert receipt_problem(kept, kind=RunKind.SCALAR, run_id=run_id) is not None


def test_stats_on_a_statistics_run_says_why_there_is_nothing_to_analyze(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    kept = _run(tmp_path, "statistics", **_STATISTICS)

    status = cli.main(["stats", str(kept / "trajectory.tlog")])

    assert status != 0
    assert "kept statistics only" in capsys.readouterr().err


def test_a_statistics_runs_history_is_read_back_for_the_graph(tmp_path: Path) -> None:
    from fim.reanalyze import read_persisted_convergence_history  # noqa: PLC0415

    kept = _run(tmp_path, "statistics", **_STATISTICS)
    manifest = read_manifest(kept / "manifest.json")

    found = read_persisted_convergence_history(kept / "trajectory.tlog", manifest)

    assert found is not None
    assert found[0][-1] == manifest.generation and "D" in found[1]


def test_the_app_opens_a_statistics_run_as_a_report_without_a_trajectory(
    tmp_path: Path,
) -> None:
    from fim.gui.app import Api  # noqa: PLC0415

    kept = _run(tmp_path, "statistics", **_STATISTICS)
    batch = _run(tmp_path, "statistics-batch", n_replicates=3, **_STATISTICS)
    api = Api(preferences_path=tmp_path / "preferences.json")

    single = api.open_run({"trajectoryPath": str(kept / "trajectory.tlog")})
    many = api.open_batch(str(batch))

    assert single["ok"] is True and single["reportOnly"] is True
    assert many["ok"] is True and many["reportOnly"] is True
