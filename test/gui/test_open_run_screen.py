"""Headless functional tests for Screen 6, the open-a-run screen.

Every test constructs a real `OpenRunScreen` (needs a display, hence the
`gui` marker — design doc §6.2/§6.4) and drives it via direct method
calls / `.invoke()`, never `mainloop()` (design §6.1). `list_recent_runs`
and `open_dialog` are always injected so no test touches the real
filesystem picker or the real `results/` directory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from fim import cli
from fim.gui.app import Application
from fim.gui.recent_runs import RecentRun
from fim.gui.screens.open_run_screen import OpenRunScreen
from fim.reanalyze import ReanalyzedGeneration

pytestmark = pytest.mark.gui


def _write_run(tmp_path: Path, name: str = "output", **overrides: object) -> Path:
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
    }
    config.update(overrides)
    config_path = tmp_path / f"{name}.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    output_directory = tmp_path / name
    arguments = ["run", str(config_path), "-o", str(output_directory), "--quiet"]
    if config.get("n_replicates", 1) != 1:
        arguments.append("--sequential")
    assert cli.main(arguments) == 0
    return output_directory


def test_open_run_screen_reproduces_cli_stats_report(
    root: Application,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Design doc's own named test: Screen 6's report matches `cli stats`'s.

    Given a trajectory written by a real run, assert the screen's
    rendered report matches `cli._command_stats`'s output for the same
    generation — the GUI/CLI parity requirement (G6) as a direct
    equality assertion.
    """
    output = _write_run(tmp_path)
    trajectory = output / "trajectory.jsonl"
    received: list[tuple[ReanalyzedGeneration, Path]] = []
    screen = OpenRunScreen(
        root,
        on_open=lambda r, d: received.append((r, d)),
        open_dialog=lambda: str(trajectory),
    )

    screen._on_browse_clicked()
    screen._on_open_clicked()

    assert cli.main(["stats", str(trajectory)]) == 0
    cli_report = json.loads(capsys.readouterr().out)
    assert len(received) == 1
    assert received[0][0].report == cli_report
    assert received[0][1] == trajectory.parent


def test_open_run_screen_rejects_a_tampered_trajectory(
    root: Application,
    tmp_path: Path,
) -> None:
    """Design doc's own named test: a tampered trajectory shows the integrity error.

    Mutating a persisted `trajectory.jsonl` byte after the run completed
    shows the same integrity-check message `fim stats` would raise
    rather than rendering a report from the tampered file (§3.8, §4.7).
    """
    output = _write_run(tmp_path)
    trajectory = output / "trajectory.jsonl"
    corrupted = trajectory.read_text(encoding="utf-8").replace(
        '"run_id":"run-', '"run_id":"other-'
    )
    trajectory.write_text(corrupted, encoding="utf-8")
    received: list[tuple[ReanalyzedGeneration, Path]] = []
    screen = OpenRunScreen(
        root,
        on_open=lambda r, d: received.append((r, d)),
        open_dialog=lambda: str(trajectory),
    )

    screen._on_browse_clicked()
    screen._on_open_clicked()

    assert not received
    assert "does not match its manifest" in screen._banner["text"]


def test_open_run_screen_recent_run_selection_sets_the_trajectory(
    root: Application,
    tmp_path: Path,
) -> None:
    """Selecting a recent-runs row is enough for "Open" to find its trajectory."""
    output = _write_run(tmp_path)
    fixture = [
        RecentRun(
            run_id="run-x",
            directory=output,
            ended_at="2026-01-01T00:00:00Z",
            label="statistic converged",
            is_batch=False,
        )
    ]
    received: list[tuple[ReanalyzedGeneration, Path]] = []
    screen = OpenRunScreen(
        root,
        on_open=lambda r, d: received.append((r, d)),
        list_recent_runs=lambda: fixture,
    )

    screen._recent_runs_tree.selection_set("run-x")
    screen._on_recent_run_selected()
    screen._on_open_clicked()

    assert len(received) == 1
    assert received[0][1] == output


def test_open_run_screen_refuses_a_batch_row_selection(
    root: Application,
    tmp_path: Path,
) -> None:
    """Selecting a batch row shows a banner and never sets a trajectory to open.

    Design §0, §4.0 #9, §4.6: a batch has no single trajectory of its
    own — Screen 4's "Open replicate" is the actual path to any one
    replicate's trajectory.
    """
    output = _write_run(tmp_path)
    fixture = [
        RecentRun(
            run_id="run-batch",
            directory=output,
            ended_at="2026-01-01T00:00:00Z",
            label="batch (3/3)",
            is_batch=True,
        )
    ]
    received: list[tuple[ReanalyzedGeneration, Path]] = []
    screen = OpenRunScreen(
        root,
        on_open=lambda r, d: received.append((r, d)),
        list_recent_runs=lambda: fixture,
    )

    screen._recent_runs_tree.selection_set("run-batch")
    screen._on_recent_run_selected()

    assert screen._trajectory_path is None
    assert "open a replicate" in screen._banner["text"]

    screen._on_open_clicked()

    assert not received


def test_open_run_screen_browse_overrides_the_recent_run_selection(
    root: Application,
    tmp_path: Path,
) -> None:
    """Browsing for a file takes precedence over a prior recent-runs selection."""
    selected_output = _write_run(tmp_path, "selected", seed=1)
    browsed_output = _write_run(tmp_path, "browsed", seed=2)
    fixture = [
        RecentRun(
            run_id="run-x",
            directory=selected_output,
            ended_at="2026-01-01T00:00:00Z",
            label="statistic converged",
            is_batch=False,
        )
    ]
    browsed_trajectory = browsed_output / "trajectory.jsonl"
    received: list[tuple[ReanalyzedGeneration, Path]] = []
    screen = OpenRunScreen(
        root,
        on_open=lambda r, d: received.append((r, d)),
        list_recent_runs=lambda: fixture,
        open_dialog=lambda: str(browsed_trajectory),
    )

    screen._recent_runs_tree.selection_set("run-x")
    screen._on_recent_run_selected()
    screen._on_browse_clicked()
    screen._on_open_clicked()

    assert len(received) == 1
    assert received[0][1] == browsed_output


def test_open_run_screen_open_with_nothing_selected_shows_a_banner(
    root: Application,
) -> None:
    """ "Open" with no trajectory chosen reports an error and never calls on_open."""
    received: list[tuple[ReanalyzedGeneration, Path]] = []
    screen = OpenRunScreen(root, on_open=lambda r, d: received.append((r, d)))

    screen._on_open_clicked()

    assert not received
    assert screen._banner["text"] != ""


def test_open_run_screen_explicit_generation_reaches_reanalyze_trajectory(
    root: Application,
    tmp_path: Path,
) -> None:
    """ "Generation: choose" with a valid number selects that generation."""
    output = _write_run(tmp_path)
    trajectory = output / "trajectory.jsonl"
    received: list[tuple[ReanalyzedGeneration, Path]] = []
    screen = OpenRunScreen(
        root,
        on_open=lambda r, d: received.append((r, d)),
        open_dialog=lambda: str(trajectory),
    )
    screen._on_browse_clicked()
    screen._generation_mode.set("choose")
    screen._generation_entry.insert(0, "0")

    screen._on_open_clicked()

    assert len(received) == 1
    assert received[0][0].state.generation == 0
    assert received[0][0].report["reason"] == "re-analysis"


def test_open_run_screen_malformed_q_sweep_shows_a_banner(
    root: Application,
    tmp_path: Path,
) -> None:
    """A non-numeric differentiation-q entry is a validation error, not a crash."""
    output = _write_run(tmp_path)
    trajectory = output / "trajectory.jsonl"
    received: list[tuple[ReanalyzedGeneration, Path]] = []
    screen = OpenRunScreen(
        root,
        on_open=lambda r, d: received.append((r, d)),
        open_dialog=lambda: str(trajectory),
    )
    screen._on_browse_clicked()
    screen._q_entry.insert(0, "not-a-number")

    screen._on_open_clicked()

    assert not received
    assert screen._banner["text"] != ""


def test_open_run_screen_q_sweep_appears_in_the_result(
    root: Application,
    tmp_path: Path,
) -> None:
    """A valid differentiation-q sweep reaches the re-analyzed report."""
    output = _write_run(tmp_path)
    trajectory = output / "trajectory.jsonl"
    received: list[tuple[ReanalyzedGeneration, Path]] = []
    screen = OpenRunScreen(
        root,
        on_open=lambda r, d: received.append((r, d)),
        open_dialog=lambda: str(trajectory),
    )
    screen._on_browse_clicked()
    screen._q_entry.insert(0, "0, 1, 2")

    screen._on_open_clicked()

    assert len(received) == 1
    swept = received[0][0].report["Differentiation_q"]
    assert isinstance(swept, dict)
    assert set(swept) == {"0.0", "1.0", "2.0"}


def test_open_run_screen_reanalyze_failure_shows_the_message_verbatim(
    root: Application,
    tmp_path: Path,
) -> None:
    """A `reanalyze_trajectory` failure is shown verbatim, never calling on_open.

    Design §4.7: the caught error's message is shown as-is, without a
    `fim: error:`-style prefix — the GUI/CLI parity requirement (G8).
    """
    output = _write_run(tmp_path)
    trajectory = output / "trajectory.jsonl"
    received: list[tuple[ReanalyzedGeneration, Path]] = []
    screen = OpenRunScreen(
        root,
        on_open=lambda r, d: received.append((r, d)),
        open_dialog=lambda: str(trajectory),
    )
    screen._on_browse_clicked()
    screen._generation_mode.set("choose")
    screen._generation_entry.insert(0, "999")

    screen._on_open_clicked()

    assert not received
    assert "no generation 999" in screen._banner["text"]
