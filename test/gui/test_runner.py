"""Unit and integration tests for `fim.gui.runner`.

No Tk import and no display needed anywhere in this file: `ProgressThrottle`
is a pure clock-driven predicate, and `start_run`'s tests exercise a
real background thread, a real `fim.engine.fim` call, and the real
filesystem directly — the `gui` pytest marker (this project's own,
"constructs real Tk widgets; needs a display") does not apply to any of it.
Nothing here sleeps or races on wall-clock timing (the determinism
contract, `doc/fim-gui-design.md` §7.1).

`test_cancel_during_run_leaves_no_output_directory`, a dedicated
integration test, lives in its own commit and is not part of this file.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path

import pytest

from fim.engine import RunResult
from fim.gui import runner
from fim.model.params import SimulationParams
from fim.persistence.manifest import hash_file, read_manifest


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


def test_start_run_writes_the_four_documented_artifacts_on_success(
    tmp_path: Path,
    tiny_params: SimulationParams,
) -> None:
    """A real, uncancelled run produces the same four artifacts `fim run` does."""
    output_directory = tmp_path / "output"
    message_queue: queue.Queue[runner.RunMessage] = queue.Queue()

    thread = runner.start_run(
        tiny_params, output_directory, message_queue, threading.Event()
    )
    thread.join(timeout=30)

    assert not thread.is_alive()
    assert {path.name for path in output_directory.iterdir()} == {
        "trajectory.jsonl",
        "manifest.json",
        "report.json",
        "scatter.png",
    }
    messages = _drain(message_queue)
    assert messages[-1][0] == "done"
    assert isinstance(messages[-1][1], RunResult)
    progress_messages = messages[:-1]
    # Each progress message carries that generation's own live scatter
    # data — `d == 2` for `tiny_params`, so exactly one
    # direct panel per message, never empty. The `if` (not a bare
    # `assert message[0] == ...` followed by indexing) is what lets
    # mypy narrow `message` from the full `RunMessage` union down to
    # `ProgressMessage` before `message[2]`/`message[3]` is indexed.
    checked = 0
    for message in progress_messages:
        assert message[0] == "progress"
        if message[0] == "progress":
            panels = message[2]
            assert len(panels) == 1
            assert panels[0]["points"]
            # `points` (the live "Compare demes directly"
            # selector, `fim.gui.app._drain_run_messages`'s own
            # `deme_pair_panel(message[3], ...)` call): the same raw,
            # not-yet-reduced array `panels` was itself built from, one
            # column per deme.
            points = message[3]
            assert points.shape[1] == 2
            # `report` (the running-state view's live stats-table field):
            # the same six named statistics `report_for_state` computes
            # for p_0/a finished run, computed fresh for this tick's
            # own state so the running-state table has something to
            # show before the run itself finishes.
            report = message[4]
            assert {"D", "G_ST", "E_ST", "K_ST", "H_S", "H_T"} <= report.keys()
            checked += 1
    assert checked == len(progress_messages)


def test_start_run_records_matching_digests_in_the_published_manifest(
    tmp_path: Path,
    tiny_params: SimulationParams,
) -> None:
    """`manifest.json`'s recorded digests match the published artifacts' own.

    The same guarantee `cli._write_run_artifacts` gives `fim run`'s own
    output (`test/cli/test_cli.py`'s manifest-digest assertions) — the
    record `fim.persistence.manifest.verify_trajectory_integrity` later
    checks against.
    """
    output_directory = tmp_path / "output"
    message_queue: queue.Queue[runner.RunMessage] = queue.Queue()

    thread = runner.start_run(
        tiny_params, output_directory, message_queue, threading.Event()
    )
    thread.join(timeout=30)

    manifest = read_manifest(output_directory / "manifest.json")
    assert manifest.artifacts is not None
    for name, filename in (
        ("trajectory", "trajectory.jsonl"),
        ("report", "report.json"),
        ("scatter", "scatter.png"),
    ):
        assert manifest.artifacts[name] == hash_file(output_directory / filename)


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


def test_cancel_during_run_leaves_no_output_directory(
    tmp_path: Path,
    tiny_params: SimulationParams,
) -> None:
    """A run cancelled before it ever writes leaves no output directory at all.

    The one true integration test in this layer: `cancel_event` is set
    *before* `start_run` is
    even called, so the worker's very first `write_generation` call —
    generation 0, made unconditionally before the convergence loop
    begins — already observes it and raises `RunCancelledError`
    deterministically, without any wall-clock race. This test constructs
    no Tk widget and needs no display — a real background thread, a real
    `fim.engine.fim` call, and the real filesystem are the whole test —
    so it stays here, unmarked, alongside every other test in this file
    that shares exactly that same technical shape, rather than
    acquiring a marker whose own documented meaning ("needs a display")
    would not describe it.

    Also confirms the property the module docstring and the previous
    commit's message both claim: no `shutil.rmtree` or other bespoke
    cleanup code runs anywhere in `fim.gui.runner` — this test would
    fail exactly the same way whether that claim were true or not,
    because `fim.paths.atomic_directory` alone is responsible for it.
    """
    output_directory = tmp_path / "output"
    message_queue: queue.Queue[runner.RunMessage] = queue.Queue()
    cancel_event = threading.Event()
    cancel_event.set()

    thread = runner.start_run(
        tiny_params, output_directory, message_queue, cancel_event
    )
    thread.join(timeout=30)

    assert not thread.is_alive()
    assert not output_directory.exists()
    assert {path.name for path in tmp_path.iterdir()} == set()
    message = message_queue.get_nowait()
    assert message[0] == "cancelled"
    assert message[1] == 0


def _drain(message_queue: queue.Queue[runner.RunMessage]) -> list[runner.RunMessage]:
    """Return every message currently queued, in order."""
    messages: list[runner.RunMessage] = []
    while True:
        try:
            messages.append(message_queue.get_nowait())
        except queue.Empty:
            return messages
