"""Unit tests for `fim.reanalyze`, extracted from `fim.cli._command_stats`.

`test/cli/test_cli.py`'s own `stats`-command tests keep exercising
`cli.main(["stats", ...])` end to end, unmodified by this extraction
(confirmed: they pass unchanged against the new import path); these
tests instead call `fim.reanalyze` directly, the way `fim.gui`'s
"open an existing run" and animated-trajectory paths do
(`doc/fim-gui-design.md` §8, §9).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from fim import cli, reanalyze
from fim.persistence.manifest import read_manifest


@pytest.fixture(autouse=True)
def _isolate_logging(log_isolation: None) -> None:
    """Opt every test in this file into `test/conftest.py`'s own `log_isolation`.

    The one `cli.main(["run", ...])` call below (building a real
    trajectory to re-analyze) reaches `fim.logging_setup.configure()`
    the same as any other real `cli.main` call — see `log_isolation`'s
    own docstring for why that matters here.
    """


def _write_run(tmp_path: Path, **overrides: object) -> Path:
    """Write a tiny deterministic config, run it, and return its output directory."""
    config: dict[str, object] = {
        "N": 20,
        "d": 2,
        "m": 0.1,
        "mu": 0.01,
        "seed": 20260814,
        "loci": [{"locus_id": 1, "length": 200}],
        "convergence_window": 4,
        "convergence_tolerance": 1.0,
        "max_generations": 10,
        "n_replicates": 1,
        "replicate_tolerance": None,
    }
    config.update(overrides)
    config_path = tmp_path / "run.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    output_directory = tmp_path / "output"
    assert (
        cli.main(["run", str(config_path), "-o", str(output_directory), "--quiet"]) == 0
    )
    return output_directory


def test_reanalyze_trajectory_matches_the_live_report(tmp_path: Path) -> None:
    """Re-analyzing the final generation reproduces the run's own report.json."""
    output = _write_run(tmp_path)

    result = reanalyze.reanalyze_trajectory(output / "trajectory.jsonl")

    live = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert result.report == live


def test_reanalyze_trajectory_supports_an_explicit_earlier_generation(
    tmp_path: Path,
) -> None:
    """A non-final generation reports "re-analysis", not the run's own outcome."""
    output = _write_run(tmp_path)

    result = reanalyze.reanalyze_trajectory(output / "trajectory.jsonl", generation=0)

    assert result.state.generation == 0
    assert result.report["reason"] == "re-analysis"
    assert result.report["converged"] is False


def test_reanalyze_trajectory_computes_a_differentiation_q_sweep(
    tmp_path: Path,
) -> None:
    """`differentiation_orders` populates "Differentiation_q" keyed by order."""
    output = _write_run(tmp_path)

    result = reanalyze.reanalyze_trajectory(
        output / "trajectory.jsonl", differentiation_orders=(0.0, 1.0, 2.0)
    )

    swept = result.report["Differentiation_q"]
    assert isinstance(swept, dict)
    assert swept["0.0"] == pytest.approx(result.report["K_ST"])
    assert swept["1.0"] == pytest.approx(result.report["E_ST"])
    assert swept["2.0"] == pytest.approx(result.report["D"])


def test_reanalyze_trajectory_rejects_a_tampered_trajectory(tmp_path: Path) -> None:
    """A trajectory edited after the run completed fails the digest check."""
    output = _write_run(tmp_path)
    trajectory = output / "trajectory.jsonl"
    corrupted = trajectory.read_text(encoding="utf-8").replace(
        '"run_id":"run-', '"run_id":"other-'
    )
    trajectory.write_text(corrupted, encoding="utf-8")

    with pytest.raises(ValueError, match="does not match its manifest"):
        reanalyze.reanalyze_trajectory(trajectory)


def test_reanalyze_trajectory_rejects_an_unknown_generation(tmp_path: Path) -> None:
    """An out-of-range generation is a clear error, not a silent empty result."""
    output = _write_run(tmp_path)

    with pytest.raises(ValueError, match="no generation 999"):
        reanalyze.reanalyze_trajectory(output / "trajectory.jsonl", generation=999)


def test_differentiation_q_for_state_agrees_with_e_st_under_size_weighting(
    tmp_path: Path,
) -> None:
    """`q = 1` under size weighting matches `E_ST`, not the unweighted value.

    Regression coverage carried over from `cli.py`'s own S2 test:
    `differentiation_q_for_state` must pass size weights at `q = 1`
    exactly as `report_for_state` does, or the two silently disagree
    whenever `deme_weighting` is `"size"` and demes are unequal.
    """
    output = _write_run(tmp_path, N=[12, 30])

    result = reanalyze.reanalyze_trajectory(
        output / "trajectory.jsonl", differentiation_orders=(1.0,)
    )

    swept = result.report["Differentiation_q"]
    assert isinstance(swept, dict)
    assert swept["1.0"] == pytest.approx(result.report["E_ST"])


def test_group_rows_by_generation_groups_every_persisted_generation(
    tmp_path: Path,
) -> None:
    """Every persisted generation appears, keyed by its own generation number."""
    output = _write_run(tmp_path)
    trajectory = output / "trajectory.jsonl"
    manifest = read_manifest(output / "manifest.json")

    grouped = reanalyze.group_rows_by_generation(trajectory, manifest.run_id)

    assert set(grouped) == set(range(manifest.generation + 1))
    assert all(
        row["generation"] == generation
        for generation, rows in grouped.items()
        for row in rows
    )
