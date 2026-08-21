"""Unit and integration tests for `fim.gui.batch_runner`.

No Tk import and no display needed anywhere in this file — real
background threads, real `fim.engine.fim` batch calls, and the real
filesystem, the same technical shape as `test/gui/test_runner.py`.
"""

from __future__ import annotations

import itertools
import json
import queue
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from fim.engine import replicate_summary
from fim.gui import batch_runner
from fim.model.params import SimulationParams
from fim.persistence.manifest import hash_file, read_batch_manifest


def _always_reporting_clock() -> Callable[[], float]:
    """Return a fake clock that advances by a full second every call.

    `ProgressThrottle`'s default interval is ~50ms; a real wall clock
    against `tiny_params`-scale replicates can finish an entire batch
    faster than that, throttling every generation but the first down to
    nothing and making which replicate indices get reported a function
    of machine speed rather than of the code under test — exactly the
    non-determinism this project's test discipline forbids (see
    `test_progress_throttle_*` in `test/gui/test_runner.py`, which
    inject a fake clock for the same reason). Every call here reports.
    """
    counter = itertools.count()
    return lambda: float(next(counter))


@pytest.fixture
def batch_params(tiny_params: SimulationParams) -> SimulationParams:
    """A small, fast three-replicate batch configuration."""
    return replace(tiny_params, n_replicates=3)


def test_replicate_index_recovers_the_ordinal_from_the_run_id(
    batch_params: SimulationParams,
) -> None:
    """The 1-based ordinal is parsed straight out of the replicate run ID."""
    assert batch_runner.replicate_index("run-abc", "run-abc-r001") == 1
    assert batch_runner.replicate_index("run-abc", "run-abc-r003") == 3


def test_replicate_output_directory_uses_the_zero_padded_ordinal(
    tmp_path: Path,
) -> None:
    """The directory name matches `cli._replicate_output_directory`'s own."""
    directory = batch_runner.replicate_output_directory(
        tmp_path, "run-abc", "run-abc-r002"
    )

    assert directory == tmp_path / "replicate-002"


def test_start_batch_run_raises_when_output_directory_already_exists(
    tmp_path: Path,
    batch_params: SimulationParams,
) -> None:
    """The pre-existing-target guard fires synchronously, before any thread."""
    output_directory = tmp_path / "existing"
    output_directory.mkdir()

    with pytest.raises(FileExistsError, match="already exists"):
        batch_runner.start_batch_run(
            batch_params, output_directory, queue.Queue(), threading.Event()
        )


def test_start_batch_run_writes_every_replicate_and_batch_artifact_on_success(
    tmp_path: Path,
    batch_params: SimulationParams,
) -> None:
    """A real, uncancelled batch publishes the full documented artifact tree."""
    output_directory = tmp_path / "output"
    message_queue: queue.Queue[batch_runner.BatchMessage] = queue.Queue()

    thread = batch_runner.start_batch_run(
        batch_params,
        output_directory,
        message_queue,
        threading.Event(),
        clock=_always_reporting_clock(),
    )
    thread.join(timeout=30)

    assert not thread.is_alive()
    assert {path.name for path in output_directory.iterdir()} == {
        "replicate-001",
        "replicate-002",
        "replicate-003",
        "summary.json",
        "manifest.json",
    }
    for index in (1, 2, 3):
        replicate_directory = output_directory / f"replicate-{index:03}"
        assert {path.name for path in replicate_directory.iterdir()} == {
            "trajectory.jsonl",
            "manifest.json",
            "report.json",
            "scatter.png",
        }
    messages = _drain(message_queue)
    assert messages[-1][0] == "done"
    assert len(messages[-1][1]) == 3
    assert all(message[0] == "replicate" for message in messages[:-1])
    assert {message[1] for message in messages[:-1]} == {1, 2, 3}


def test_start_batch_run_records_matching_digests_in_the_published_manifest(
    tmp_path: Path,
    batch_params: SimulationParams,
) -> None:
    """The batch manifest's digests match every actually-published artifact.

    The same guarantee `test/gui/test_runner.py`'s equivalent test
    checks for a scalar run — the record
    `fim.persistence.manifest.verify_trajectory_integrity` (per
    replicate) and any future batch-level integrity check would rely
    on.
    """
    output_directory = tmp_path / "output"
    message_queue: queue.Queue[batch_runner.BatchMessage] = queue.Queue()

    thread = batch_runner.start_batch_run(
        batch_params, output_directory, message_queue, threading.Event()
    )
    thread.join(timeout=30)

    manifest = read_batch_manifest(output_directory / "manifest.json")
    assert manifest.artifacts is not None
    assert manifest.artifacts["summary"] == hash_file(output_directory / "summary.json")
    for index in (1, 2, 3):
        replicate_directory = output_directory / f"replicate-{index:03}"
        assert manifest.artifacts[replicate_directory.name] == hash_file(
            replicate_directory / "manifest.json"
        )


def test_start_batch_run_summary_matches_replicate_summary(
    tmp_path: Path,
    batch_params: SimulationParams,
) -> None:
    """`summary.json` matches `fim.engine.replicate_summary` over the same results."""
    output_directory = tmp_path / "output"
    message_queue: queue.Queue[batch_runner.BatchMessage] = queue.Queue()

    thread = batch_runner.start_batch_run(
        batch_params, output_directory, message_queue, threading.Event()
    )
    thread.join(timeout=30)

    done = _drain(message_queue)[-1]
    assert done[0] == "done"
    expected = replicate_summary(done[1])
    summary = json.loads((output_directory / "summary.json").read_text())
    assert set(summary) == set(expected)
    for name, interval in expected.items():
        assert summary[name]["mean"] == pytest.approx(interval["mean"])
        assert summary[name]["sample_count"] == interval["sample_count"]


def test_start_batch_run_leaves_no_temporary_sibling_after_a_successful_publish(
    tmp_path: Path,
    batch_params: SimulationParams,
) -> None:
    """The hidden `.output.<random>` working directory never survives success."""
    output_directory = tmp_path / "output"
    message_queue: queue.Queue[batch_runner.BatchMessage] = queue.Queue()

    thread = batch_runner.start_batch_run(
        batch_params, output_directory, message_queue, threading.Event()
    )
    thread.join(timeout=30)

    assert {path.name for path in tmp_path.iterdir()} == {"output"}


def test_cancel_during_batch_leaves_no_output_directory(
    tmp_path: Path,
    batch_params: SimulationParams,
) -> None:
    """A batch cancelled before it ever writes leaves no output directory at all.

    The batch-level parallel to `test/gui/test_runner.py`'s
    `test_cancel_during_run_leaves_no_output_directory` (design doc
    §6.4, plan §7.6's fifth and final bullet): `cancel_event` is set
    *before* `start_batch_run` is even called, so the first
    replicate's very first `write_generation` call — generation 0,
    made unconditionally before that replicate's convergence loop
    begins — already observes it and raises `RunCancelledError`
    deterministically, without any wall-clock race (§6.1). "Cancel
    batch" stops the whole batch, not one replicate (design §4.0 #6):
    there is no partial-batch save point to preserve, so this asserts
    the same "nothing at all" outcome a mid-first-replicate
    cancellation and a mid-third-replicate cancellation would both
    produce — `fim.paths.atomic_directory` does not distinguish
    between them.
    """
    output_directory = tmp_path / "output"
    message_queue: queue.Queue[batch_runner.BatchMessage] = queue.Queue()
    cancel_event = threading.Event()
    cancel_event.set()

    thread = batch_runner.start_batch_run(
        batch_params, output_directory, message_queue, cancel_event
    )
    thread.join(timeout=30)

    assert not thread.is_alive()
    assert not output_directory.exists()
    assert {path.name for path in tmp_path.iterdir()} == set()
    message = message_queue.get_nowait()
    assert message[0] == "cancelled"
    assert message[1] == 1
    assert message[2] == 0


def _drain(
    message_queue: queue.Queue[batch_runner.BatchMessage],
) -> list[batch_runner.BatchMessage]:
    """Return every message currently queued, in order."""
    messages: list[batch_runner.BatchMessage] = []
    while True:
        try:
            messages.append(message_queue.get_nowait())
        except queue.Empty:
            return messages
