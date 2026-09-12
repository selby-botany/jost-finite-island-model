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
import weakref
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from fim import cli, reanalyze
from fim.persistence.jsonl_store import JSONLTrajectoryStore
from fim.persistence.manifest import hash_file, read_manifest, write_manifest


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


def _build_large_synthetic_trajectory(
    tmp_path: Path, *, generation_count: int
) -> tuple[Path, Path, int]:
    """Replicate one real generation's own rows across many synthetic generations.

    Re-analyzing a single generation never needs to care which
    generation's own frequencies it is looking at to prove the
    memory-liveness claim below — only that the row *shapes* are real,
    schema-valid `TrajectoryRow`s, and that the manifest they are paired
    with matches the file's real digest and generation count exactly
    (the two checks `reanalyze_trajectory` must never skip). Taking one
    small real run's own generation-0 rows and replaying them under
    ``generation_count`` different generation numbers satisfies both,
    without needing to actually simulate hundreds of generations.

    Args:
        tmp_path: Test-owned scratch directory.
        generation_count: How many synthetic generations to write.

    Returns:
        The synthetic trajectory path, its paired manifest path, and how
        many rows each synthetic generation contains.
    """
    template_directory = tmp_path / "template"
    template_directory.mkdir()
    template_output = _write_run(template_directory)
    template_manifest = read_manifest(template_output / "manifest.json")
    template_rows = reanalyze.group_rows_by_generation(
        template_output / "trajectory.jsonl", template_manifest.run_id
    )[0]

    run_id = "synthetic-large-trajectory"
    trajectory_path = tmp_path / "synthetic-trajectory.jsonl"
    store = JSONLTrajectoryStore(trajectory_path)
    for generation_index in range(generation_count):
        store.write_generation(
            run_id,
            generation_index,
            [
                {**row, "run_id": run_id, "generation": generation_index}
                for row in template_rows
            ],
        )

    manifest_path = tmp_path / "synthetic-manifest.json"
    write_manifest(
        manifest_path,
        replace(
            template_manifest,
            run_id=run_id,
            generation=generation_count - 1,
            generation_count=generation_count,
            artifacts={"trajectory": hash_file(trajectory_path)},
        ),
    )
    return trajectory_path, manifest_path, len(template_rows)


def _count_row_liveness(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[int], list[int]]:
    """Patch `JSONLTrajectoryStore.read` to track simultaneously live rows.

    Real-issue-9 regression proof (see `doc/20260906-gpt-5.6-open-
    issues.md` item 9): rather than a wall-clock timing measurement
    (non-deterministic across machines, forbidden by this project's own
    testing discipline), this wraps every row `JSONLTrajectoryStore.read`
    yields in a `dict` subclass (a plain `dict` cannot hold a weak
    reference) and registers a `weakref.finalize` callback on each one.
    CPython frees an object the instant its reference count reaches
    zero — no `gc.collect()` needed — so `alive_after_each_row[i]`
    deterministically reflects exactly how many rows were still
    reachable immediately after the `i`-th row was yielded, for any
    given commit of the code under test: a real, reproducible
    allocation-liveness measurement, not a timing one, matching this
    project's own `b12679b` precedent for this kind of performance claim.

    Returns:
        Two same-length lists, appended to live as rows are read:
        ``alive_after_each_row`` (a running snapshot of how many
        tracked rows are still alive right after each row is yielded)
        and ``total_yielded`` (just a 1, 2, 3, ... counter, so a test
        can report how many rows were actually read without needing a
        separate counter of its own).
    """
    alive = 0
    alive_after_each_row: list[int] = []
    total_yielded: list[int] = []
    real_read = JSONLTrajectoryStore.read

    class _TrackedRow(dict[str, object]):
        """A `dict` subclass (plain `dict` instances cannot hold a weakref)."""

    def _tracking_read(self: JSONLTrajectoryStore, run_id: str) -> object:
        nonlocal alive

        def _mark_collected(_reference: object = None) -> None:
            nonlocal alive
            alive -= 1

        for row in real_read(self, run_id):
            wrapped = _TrackedRow(row)
            weakref.finalize(wrapped, _mark_collected)
            alive += 1
            yield wrapped
            del wrapped
            alive_after_each_row.append(alive)
            total_yielded.append(len(alive_after_each_row))

    monkeypatch.setattr(JSONLTrajectoryStore, "read", _tracking_read)
    return alive_after_each_row, total_yielded


def test_reanalyze_trajectory_does_not_hold_every_row_live_for_an_early_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit early generation never keeps later generations' rows alive.

    The worst case for a `rows = list(store.read(...))`-style
    implementation: re-analyzing generation 0 out of many still means
    every later generation gets read (the integrity/consistency checks
    require it), so the old code would hold the *entire* trajectory's
    rows live simultaneously even though only generation 0's own rows
    are ever used. This proves the streaming rewrite does not.
    """
    generation_count = 300
    trajectory_path, manifest_path, rows_per_generation = (
        _build_large_synthetic_trajectory(tmp_path, generation_count=generation_count)
    )
    alive_after_each_row, total_yielded = _count_row_liveness(monkeypatch)

    result = reanalyze.reanalyze_trajectory(
        trajectory_path, manifest_path=manifest_path, generation=0
    )

    assert result.state.generation == 0
    total_rows = generation_count * rows_per_generation
    assert total_yielded[-1] == total_rows
    # The real claim: peak simultaneous liveness stays near one
    # generation's own row count, not anywhere near the trajectory's
    # total row count -- proving every row is never materialized at once.
    peak_alive = max(alive_after_each_row)
    assert peak_alive <= rows_per_generation + 4
    assert peak_alive < total_rows / 10


def test_reanalyze_trajectory_does_not_hold_every_row_live_for_the_final_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default ("final generation") path is equally memory-bounded.

    `generation=None` cannot know which generation is the maximum until
    the stream ends (`reanalyze_trajectory`'s own docstring on
    `JSONLTrajectoryStore.read`'s ordering guarantee), so this exercises
    the rolling running-max buffer specifically: every earlier
    generation's buffered rows must be dropped, not accumulated, each
    time a higher generation number is seen.
    """
    generation_count = 300
    trajectory_path, manifest_path, rows_per_generation = (
        _build_large_synthetic_trajectory(tmp_path, generation_count=generation_count)
    )
    alive_after_each_row, total_yielded = _count_row_liveness(monkeypatch)

    result = reanalyze.reanalyze_trajectory(
        trajectory_path, manifest_path=manifest_path
    )

    assert result.state.generation == generation_count - 1
    total_rows = generation_count * rows_per_generation
    assert total_yielded[-1] == total_rows
    peak_alive = max(alive_after_each_row)
    assert peak_alive <= rows_per_generation + 4
    assert peak_alive < total_rows / 10


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
