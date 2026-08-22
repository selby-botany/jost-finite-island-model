"""Unit and integration tests for `fim.gui.batch_runner`.

No Tk import and no display needed anywhere in this file — real
background threads, real `fim.engine.fim` batch calls (in parallel, real
OS processes, since design §0.5), and the real filesystem, the same
technical shape as `test/gui/test_runner.py`.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from itertools import combinations
from pathlib import Path

import pytest

from fim.engine import Clock, SimulationOutput, replicate_summary
from fim.engine import fim as engine_fim
from fim.gui import batch_runner
from fim.gui.store import read_progress_sidecar
from fim.model.params import Migration, MutationRate, PopulationSize, SimulationParams
from fim.persistence.manifest import hash_file, read_batch_manifest
from fim.persistence.store import TrajectoryStore


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
        batch_params, output_directory, message_queue, threading.Event()
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
        # `.progress` is a GUI-only sidecar (design §0.5) removed once a
        # replicate's real artifacts are written — the published set
        # stays exactly the CLI's own four-file contract, nothing extra
        # left behind.
        assert {path.name for path in replicate_directory.iterdir()} == {
            "trajectory.jsonl",
            "manifest.json",
            "report.json",
            "scatter.png",
        }
    # Progress no longer travels through `message_queue` at all (design
    # §0.5, §3.4): it is entirely file-mediated now, so a successful
    # batch posts exactly two messages — `"started"` (the parent-side
    # poller's own only way to learn the hidden working directory, since
    # its random `mkdtemp` suffix is not derivable from `output_
    # directory`) and its terminal outcome.
    messages = _drain(message_queue)
    assert len(messages) == 2
    assert messages[0][0] == "started"
    assert messages[1][0] == "done"
    assert len(messages[1][1]) == 3


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


def test_start_batch_run_prunes_orphan_replicate_directories(
    tmp_path: Path,
    tiny_params: SimulationParams,
) -> None:
    """The published `replicate-*` set matches the manifest, adaptive stop included.

    Direct mirror of `cli.py`'s own
    `test_run_batch_parallel_adaptive_stop_leaves_no_orphan_replicate_
    directories` (regression fix S1): under real parallelism (design
    §0.5), `fim.engine._run_batch_parallel` applies an adaptive
    `replicate_tolerance` stop only after a whole concurrent worker wave
    completes, in ascending replicate order — a worker beyond the
    replicate that triggered the stop still runs to completion and fully
    writes its own `replicate-*` directory before its result is
    discarded. A generous tolerance and a low `replicate_minimum` with
    `max_workers=4` reliably stops mid-batch here, exactly as it does for
    the CLI's own equivalent test; without `_prune_orphan_replicate_
    directories`, the extra workers' directories would publish complete,
    present on disk, and absent from `summary.json` and `manifest.json`
    — a bug class the Tk-era sequential-only runner could never hit.
    """
    batch_params = replace(
        tiny_params,
        n_replicates=10,
        replicate_minimum=2,
        replicate_tolerance=1000.0,
    )
    output_directory = tmp_path / "output"
    message_queue: queue.Queue[batch_runner.BatchMessage] = queue.Queue()

    thread = batch_runner.start_batch_run(
        batch_params, output_directory, message_queue, threading.Event(), max_workers=4
    )
    thread.join(timeout=30)

    manifest = read_batch_manifest(output_directory / "manifest.json")
    expected_directories = {
        batch_runner.replicate_output_directory(
            output_directory, manifest.run_id, run_id
        ).name
        for run_id in manifest.replicate_run_ids
    }
    published_directories = {
        entry.name
        for entry in output_directory.iterdir()
        if entry.name.startswith("replicate-")
    }
    assert published_directories == expected_directories
    assert len(published_directories) == manifest.replicate_count


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
    started = message_queue.get_nowait()
    assert started[0] == "started"
    message = message_queue.get_nowait()
    assert message[0] == "cancelled"
    assert message[1] == 1
    assert message[2] == 0


def test_default_max_workers_matches_cpu_count() -> None:
    """The GUI's default batch worker count matches `cli._cpu_count()`'s own logic.

    Direct regression test for H5 (design §0.5): the GUI's default is
    never silently weaker than the CLI's own default.
    """
    assert batch_runner.default_max_workers() == (os.cpu_count() or 1)


def _capture_max_workers(
    monkeypatch: pytest.MonkeyPatch, captured: dict[str, object]
) -> None:
    """Patch `batch_runner.fim` to record its `max_workers` kwarg, then delegate.

    Typed to match `fim.engine.fim`'s own signature exactly, rather than
    a generic `*args`/`**kwargs` passthrough — `fim.engine.fim` already
    is this project's mypy-strict-checked public API, so a wrapper with
    its exact signature costs nothing extra to keep correct and avoids
    a wrapper's own type ever silently drifting from the real one.
    Imported directly from `fim.engine` (not read off `batch_runner.fim`
    as a re-exported attribute) — `batch_runner.py`'s own `from fim.engine
    import ... fim ...` is a private implementation import, not a
    published re-export, exactly what `mypy --strict`'s
    `no_implicit_reexport` exists to flag if read from outside the
    module that way.
    """
    real_fim = engine_fim

    def _capturing_fim(
        n: PopulationSize,
        m: Migration,
        mu: MutationRate,
        d: int,
        *,
        params: SimulationParams,
        store: TrajectoryStore | None = None,
        run_id: str | None = None,
        clock: Clock | None = None,
        max_workers: int | None = None,
        store_factory: Callable[[str], TrajectoryStore] | None = None,
    ) -> SimulationOutput:
        captured["max_workers"] = max_workers
        return real_fim(
            n,
            m,
            mu,
            d,
            params=params,
            store=store,
            run_id=run_id,
            clock=clock,
            max_workers=max_workers,
            store_factory=store_factory,
        )

    monkeypatch.setattr(batch_runner, "fim", _capturing_fim)


def test_start_batch_run_passes_a_real_worker_count_to_fim(
    tmp_path: Path,
    batch_params: SimulationParams,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`max_workers=None` resolves to `default_max_workers()`, never stays `None`.

    Direct regression test for the sequential-only gap this whole
    reconsideration started from (design §0.5): `fim.engine.fim`'s own
    `max_workers=None` means "run sequentially, in-process" — the exact
    behavior `start_batch_run` must never silently fall back to.
    """
    captured: dict[str, object] = {}
    _capture_max_workers(monkeypatch, captured)
    output_directory = tmp_path / "output"

    thread = batch_runner.start_batch_run(
        batch_params, output_directory, queue.Queue(), threading.Event()
    )
    thread.join(timeout=30)

    assert captured["max_workers"] == batch_runner.default_max_workers()


def test_start_batch_run_respects_an_explicit_max_workers_override(
    tmp_path: Path,
    batch_params: SimulationParams,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit `max_workers` argument is used verbatim, not the default."""
    captured: dict[str, object] = {}
    _capture_max_workers(monkeypatch, captured)
    output_directory = tmp_path / "output"

    thread = batch_runner.start_batch_run(
        batch_params,
        output_directory,
        queue.Queue(),
        threading.Event(),
        max_workers=2,
    )
    thread.join(timeout=30)

    assert captured["max_workers"] == 2


def test_batch_replicates_actually_run_concurrently(
    tmp_path: Path,
    batch_params: SimulationParams,
) -> None:
    """Direct regression test for H5: replicates genuinely overlap in real time.

    Not a wall-clock-duration/ratio assertion — this project's
    determinism rules forbid a timing race — a structural fact about one
    real run: while the batch is still in flight, poll every replicate's
    own `.progress` sidecar (`fim.gui.store`) and record the timestamp it
    reports; if at least two replicates' observed windows overlap in
    real time, they were genuinely running at once, something purely
    sequential (one-replicate-at-a-time) execution could never produce
    no matter how fast each replicate ran. A sequential regression here
    (`max_workers` silently dropped back to `None`) makes every window
    strictly disjoint and fails this test every time, not intermittently.

    Overrides `batch_params`'s own `convergence_tolerance`/
    `max_generations` to force a genuinely multi-generation run, rather
    than using the shared fixture's own loose tolerance (which this test
    alone does not want widened — many sibling tests in this file want
    `batch_params` to stay fast). Found deterministically broken on real
    Linux (reproduced 5/5 on native, non-emulated arm64 and x86_64-under-
    QEMU Docker containers; never on macOS): `batch_params`'s own
    `convergence_tolerance=1.0` converges within the first few
    generations, and Linux's `fork()`-based `multiprocessing` start
    method (versus macOS's `spawn`) launches each worker process fast
    enough that a whole tiny replicate can start and finish between two
    of this test's own 5ms polls — every replicate's own observed window
    collapses to a single instant, and three near-simultaneous instants
    can easily land as non-overlapping by pure scheduling luck, exactly
    as this test's own pre-existing docstring already anticipated
    ("widen the polling window or slow tiny_params down if this ever
    flakes"). A real convergence run over many more generations gives
    each replicate a genuinely wide window to be observed within,
    independent of any one platform's own process-startup speed.
    """
    output_directory = tmp_path / "output"
    message_queue: queue.Queue[batch_runner.BatchMessage] = queue.Queue()
    slow_batch_params = replace(
        batch_params, convergence_tolerance=1e-9, max_generations=200
    )

    thread = batch_runner.start_batch_run(
        slow_batch_params, output_directory, message_queue, threading.Event()
    )
    try:
        windows: dict[int, list[datetime]] = {1: [], 2: [], 3: []}
        deadline = time.monotonic() + 30
        while thread.is_alive() and time.monotonic() < deadline:
            working_directories = list(tmp_path.glob(".output.*"))
            if working_directories:
                working_directory = working_directories[0]
                for index in (1, 2, 3):
                    sidecar = read_progress_sidecar(
                        working_directory / f"replicate-{index:03}" / ".progress"
                    )
                    if sidecar is not None:
                        written_at = sidecar["written_at"]
                        windows[index].append(
                            datetime.fromisoformat(written_at.replace("Z", "+00:00"))
                        )
            time.sleep(0.001)
    finally:
        thread.join(timeout=30)

    observed = {
        index: (min(stamps), max(stamps)) for index, stamps in windows.items() if stamps
    }
    assert len(observed) >= 2, (
        "not enough replicates observed in flight to prove overlap — "
        "widen the polling window or slow tiny_params down if this ever "
        "flakes, rather than accepting a sequential-looking result"
    )
    overlap_found = any(
        first_start <= second_end and second_start <= first_end
        for (first_start, first_end), (second_start, second_end) in combinations(
            observed.values(), 2
        )
    )
    assert overlap_found


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
