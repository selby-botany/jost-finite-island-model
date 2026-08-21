"""Unit and integration tests for `fim.gui.runner`.

No Tk import and no display needed anywhere in this file: `ProgressThrottle`
is a pure clock-driven predicate (§6.3), and `start_run`'s tests exercise a
real background thread, a real `fim.engine.fim` call, and the real
filesystem directly — the `gui` pytest marker (this project's own,
"constructs real Tk widgets; needs a display") does not apply to any of it.
Nothing here sleeps or races on wall-clock timing (the determinism
contract, design doc §6.1).

`test_cancel_during_run_leaves_no_output_directory`, the dedicated
integration test design §7.4's fourth bullet names, lives in its own
commit and is not part of this file.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path

import pytest

from fim.engine import RunResult
from fim.gui import runner
from fim.model.params import SimulationParams


def test_progress_throttle_always_reports_the_final_generation() -> None:
    """`generation == max_generations` reports even with a clock that never moves."""
    throttle = runner.ProgressThrottle(clock=lambda: 0.0)

    assert throttle.should_report(10, 10) is True


def test_progress_throttle_skips_a_report_within_the_interval() -> None:
    """Two calls with no elapsed wall-clock time report at most once."""
    throttle = runner.ProgressThrottle(interval_seconds=0.05, clock=lambda: 1.0)

    assert throttle.should_report(1, 10) is True
    assert throttle.should_report(2, 10) is False


def test_progress_throttle_reports_again_once_the_interval_elapses() -> None:
    """A call past the interval reports again, driven by an injected fake clock."""
    times = iter([1.0, 1.06])
    throttle = runner.ProgressThrottle(interval_seconds=0.05, clock=lambda: next(times))

    assert throttle.should_report(1, 10) is True
    assert throttle.should_report(2, 10) is True


def test_run_artifact_targets_matches_the_documented_four_filenames(
    tmp_path: Path,
) -> None:
    """The four target names match `cli._run_artifact_targets`'s own set."""
    targets = runner.run_artifact_targets(tmp_path)

    assert {path.name for path in targets.values()} == {
        "trajectory.jsonl",
        "manifest.json",
        "report.json",
        "scatter.png",
    }


def test_start_run_raises_when_output_directory_already_exists(
    tmp_path: Path,
    tiny_params: SimulationParams,
) -> None:
    """The pre-existing-target guard fires synchronously, before any thread."""
    output_directory = tmp_path / "existing"
    output_directory.mkdir()

    with pytest.raises(FileExistsError, match="already exists"):
        runner.start_run(
            tiny_params, output_directory, queue.Queue(), threading.Event()
        )


def test_start_run_streams_the_trajectory_and_publishes_on_success(
    tmp_path: Path,
    tiny_params: SimulationParams,
) -> None:
    """A real, uncancelled run publishes a trajectory via one atomic rename.

    Only `trajectory.jsonl` exists yet — `report.json`, `scatter.png`,
    and `manifest.json` are added by Milestone G3 (§7.5), inside the
    same `with paths.atomic_directory(...)` block this worker already
    uses, once the results screen exists to display them.
    """
    output_directory = tmp_path / "output"
    message_queue: queue.Queue[runner.RunMessage] = queue.Queue()

    thread = runner.start_run(
        tiny_params, output_directory, message_queue, threading.Event()
    )
    thread.join(timeout=30)

    assert not thread.is_alive()
    assert {path.name for path in output_directory.iterdir()} == {"trajectory.jsonl"}
    messages = _drain(message_queue)
    assert messages[-1][0] == "done"
    assert isinstance(messages[-1][1], RunResult)
    assert all(kind == "progress" for kind, _payload in messages[:-1])


def test_start_run_leaves_no_temporary_sibling_after_a_successful_publish(
    tmp_path: Path,
    tiny_params: SimulationParams,
) -> None:
    """The hidden `.output.<random>` working directory never survives success."""
    output_directory = tmp_path / "output"
    message_queue: queue.Queue[runner.RunMessage] = queue.Queue()

    thread = runner.start_run(
        tiny_params, output_directory, message_queue, threading.Event()
    )
    thread.join(timeout=30)

    assert {path.name for path in tmp_path.iterdir()} == {"output"}


def _drain(message_queue: queue.Queue[runner.RunMessage]) -> list[runner.RunMessage]:
    """Return every message currently queued, in order."""
    messages: list[runner.RunMessage] = []
    while True:
        try:
            messages.append(message_queue.get_nowait())
        except queue.Empty:
            return messages
