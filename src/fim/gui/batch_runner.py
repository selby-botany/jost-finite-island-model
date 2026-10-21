"""Background orchestration for multi-replicate GUI runs.

For the lineal backend, replicate workers run in separate processes. Progress
and cancellation use `LiveProgressStore` sidecar files; the message queue
carries terminal results. The runner writes the same artifacts as the CLI and
removes output from replicates discarded by an adaptive early stop.
"""

from __future__ import annotations

import functools
import logging
import os
import queue
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Final, Literal

from fim import paths
from fim.config.defaults import DEFAULT_PAIRWISE_MAX_DEMES
from fim.config.expert import ExpertSettings
from fim.engine import RunResult, deterministic_run_id, simulate_params
from fim.engine.records import format_timestamp, utc_now
from fim.gui.job_results import GuiRunResult
from fim.gui.runner import pairwise_status_text
from fim.gui.store import LIVE_BLOCK_SECONDS, LiveProgressStore, RunCancelledError
from fim.model.params import SimulationParams
from fim.persistence.binary_store import (
    TRAJECTORY_LOG_FILENAME,
    BinaryLogStore,
    log_options,
)
from fim.persistence.pairwise import SLOW_PAIRWISE_DEMES
from fim.persistence.saved_run import (
    replicate_index,
    replicate_output_directory,
    write_batch_artifacts,
)

# Mirrors `fim.gui.runner`'s own catch-all — see its definition for the
# full rationale. Duplicated rather than imported: `fim.gui.runner`'s
# own `run_artifact_targets`/`artifacts.run_artifact_targets` precedent is "a
# direct parallel, not a shared import" for exactly this kind of private
# front-end-local constant.
_EXPECTED_ENGINE_ERRORS: Final = (
    ArithmeticError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)

# How often the cancellation watcher thread checks `cancel_event` before
# translating it into `cancel_path`'s existence. Coarse on purpose: this
# bounds only the "user clicked Cancel" -> "cancel_path exists" latency,
# not per-generation cancellation detection, which each worker's own
# `LiveProgressStore` already checks immediately on its own next write.
_CANCEL_WATCH_INTERVAL_SECONDS: Final = 0.1

StartedMessage = tuple[Literal["started"], Path]
DoneMessage = tuple[Literal["done"], tuple[RunResult | GuiRunResult, ...]]
CancelledMessage = tuple[Literal["cancelled"], int, int]
PausedMessage = tuple[Literal["paused"], int]
ErrorMessage = tuple[Literal["error"], str]
# A non-terminal note for the status line (`fim.gui.runner.StatusMessage`).
StatusMessage = tuple[Literal["status"], str]
BatchMessage = (
    StartedMessage
    | DoneMessage
    | CancelledMessage
    | PausedMessage
    | ErrorMessage
    | StatusMessage
)

logger = logging.getLogger(__name__)


def default_max_workers() -> int:
    """Return the GUI's own default batch worker count.

    Matches `cli._cpu_count()` — the CLI's own default (non-`--sequential`)
    parallel batch worker count — so the GUI's default batch behavior is
    never silently weaker than the CLI's own. Still
    overridable per call via `start_batch_run`'s `max_workers` argument.
    """
    return os.cpu_count() or 1


def start_batch_run(
    params: SimulationParams,
    output_directory: Path,
    message_queue: queue.Queue[BatchMessage],
    cancel_event: threading.Event,
    *,
    max_workers: int | None = None,
    pairwise_max_demes: int = DEFAULT_PAIRWISE_MAX_DEMES,
) -> threading.Thread:
    """Resolve targets, guard the existing target, and start the worker thread.

    Args:
        params: Already-validated parameters with `n_replicates > 1` —
            the screen calling this routes to the scalar runner instead
            whenever `n_replicates == 1` (`doc/fim-gui-design.md` §7:
            there is no separate "batch mode" toggle; `n_replicates`
            *is* the toggle).
        output_directory: The batch's target directory, passed straight
            to `fim.paths.atomic_directory` by the worker thread.
            Checked for existence synchronously here too, exactly like
            `fim.gui.runner.start_run`.
        message_queue: Every `BatchMessage` the worker posts lands here;
            the caller drains it on its own timer.
        cancel_event: Set by the UI's "Cancel batch" button; translated
            internally into a shared cancellation file every replicate
            worker's `LiveProgressStore` checks before each write
            (`doc/fim-gui-design.md` §7.2) — nothing about this
            argument's own meaning changes from the Tk-era sequential
            runner.
        max_workers: Worker-process count for this batch. **`None` here
            does not mean "run sequentially in-process"** — unlike
            `fim.engine.simulate_params`'s own `max_workers=None` — it means "use
            this GUI's own default" (`default_max_workers()`, matching
            the CLI's own default), a deliberate divergence from the
            engine's convention worth stating explicitly rather than
            leaving implicit, since batch execution is parallel by
            default here. Only meaningful under `params.engine_backend
            == "lineal"` — `_batch_worker`'s own call to `simulate_params()` omits
            it entirely for any other backend, which runs through
            `fim.engine.run_batch`'s own concurrency model instead
            (`max_concurrent_replicates`, not this argument); the
            resolved worker count computed here is still logged either
            way, since it is a real GUI-level default independent of
            whether this particular batch ends up using it.

    Returns:
        The started (not yet joined) worker thread.

    Raises:
        FileExistsError: If `output_directory` already exists.
    """
    if output_directory.exists():
        raise FileExistsError(f"output directory already exists: {output_directory}")
    run_id = deterministic_run_id(params)
    resolved_workers = max_workers if max_workers is not None else default_max_workers()
    thread = threading.Thread(
        target=_batch_worker,
        args=(
            params,
            run_id,
            output_directory,
            message_queue,
            cancel_event,
            resolved_workers,
        ),
        kwargs={"pairwise_max_demes": pairwise_max_demes},
    )
    thread.start()
    logger.debug(
        "batch run worker thread started: %s -> %s (max_workers=%d)",
        run_id,
        output_directory,
        resolved_workers,
    )
    return thread


def _batch_worker(
    params: SimulationParams,
    run_id: str,
    output_directory: Path,
    message_queue: queue.Queue[BatchMessage],
    cancel_event: threading.Event,
    max_workers: int,
    *,
    pairwise_max_demes: int = DEFAULT_PAIRWISE_MAX_DEMES,
) -> None:
    """Run one replicate batch in parallel and post its outcome to `message_queue`.

    Every write happens inside `fim.paths.atomic_directory(output_directory)`,
    exactly as `fim.gui.runner._run_worker` does for a scalar run: a
    `RunCancelledError` from any replicate, or any member of
    `_EXPECTED_ENGINE_ERRORS`, propagates out of the `with` block and
    `atomic_directory` discards the whole temporary tree — no
    partial-batch save point exists to preserve, matching "Cancel batch"
    stopping the batch, not one replicate.

    `max_workers` (this function's own argument) only reaches `simulate_params()`
    under `params.engine_backend == "lineal"` — the one backend with a
    `ProcessPoolExecutor` for it to size. Any other backend runs through
    `fim.engine.run_batch`'s own concurrency model instead, driven by
    `store_factory` alone (`GenerationalBackend`'s own `ReplicateFanoutStore`
    support, `20260914-claude-sonnet-5-non-lineal-batch-execution-
    design.md`, `selby/restricted`, §5.2).
    """
    control_directory = Path(tempfile.mkdtemp(prefix="fim-batch-control-"))
    cancel_path = control_directory / "cancel"
    if cancel_event.is_set():
        # Cancelled before this worker thread ever started (the common
        # "clicked Cancel and the batch hadn't begun yet" case, and the
        # one `test_cancel_during_batch_leaves_no_output_directory`
        # exercises): create `cancel_path` synchronously, right here,
        # before any replicate worker is spawned. Without this, every
        # replicate's very first write would depend on the watcher
        # thread below having already won its first poll — a real,
        # if small (`_CANCEL_WATCH_INTERVAL_SECONDS`-bounded), race this
        # project's determinism rules do not tolerate for a condition
        # that is entirely knowable up front.
        cancel_path.touch()
    stop_watching = threading.Event()
    watcher = threading.Thread(
        target=_watch_for_cancellation,
        args=(cancel_event, cancel_path, stop_watching),
        daemon=True,
    )
    watcher.start()

    started_at = format_timestamp(utc_now())
    try:
        with paths.atomic_directory(output_directory) as working_directory:
            # `working_directory` is `atomic_directory`'s own hidden
            # temporary sibling of `output_directory` (a random `mkdtemp`
            # suffix, not derivable from `output_directory` alone) —
            # posted here, first, so a parent-side poller
            # (`doc/fim-gui-design.md` §7.2) knows where each replicate's
            # `.progress` sidecar and `trajectory.tlog` actually live
            # while the batch is still running, not only once it is
            # published at `output_directory` — an event no reader
            # outside this worker could otherwise ever observe.
            message_queue.put(("started", working_directory))
            results = simulate_params(
                params=params,
                run_id=run_id,
                max_workers=max_workers if params.engine_backend == "lineal" else None,
                store_factory=functools.partial(
                    _replicate_store_factory,
                    working_directory,
                    run_id,
                    cancel_path,
                    params.expert,
                ),
            )
            if not isinstance(results, tuple):
                # n_replicates > 1 is enforced by every path that can
                # reach this worker (see `start_batch_run`'s own
                # docstring), so `simulate_params()` always takes its batch branch
                # here — this guards the invariant rather than
                # silently mishandling a scalar result. Raised inside
                # the `with` block so `atomic_directory` discards the
                # temporary directory exactly as it would for any
                # other engine error.
                raise RuntimeError("unexpected scalar result from a batch run")
            ended_at = format_timestamp(utc_now())
            if SLOW_PAIRWISE_DEMES <= params.d <= pairwise_max_demes:
                message_queue.put(
                    ("status", pairwise_status_text(params.d, len(results)))
                )
            write_batch_artifacts(
                results,
                working_directory,
                run_id,
                params,
                started_at,
                ended_at,
                pairwise_max_demes=pairwise_max_demes,
                # A finished replicate no longer needs its progress sidecar;
                # the published tree is exactly the command line's layout.
                after_replicate=lambda directory: (directory / ".progress").unlink(
                    missing_ok=True
                ),
            )
    except RunCancelledError as cancelled:
        # Whichever replicate's cancellation surfaced first — under real
        # parallelism, several replicates can be mid-write when
        # `cancel_path` appears, and this reports only the one whose
        # `RunCancelledError` this call happened to observe first, not
        # necessarily the only one in flight. `ProcessPoolExecutor`'s own
        # context manager still waits for every other in-flight worker in
        # the same wave to finish (each raising, or completing, on its
        # own) before this exception fully propagates out of `simulate_params(...)`.
        message_queue.put(
            (
                "cancelled",
                replicate_index(run_id, cancelled.run_id),
                cancelled.generation,
            )
        )
        return
    except _EXPECTED_ENGINE_ERRORS as error:
        message_queue.put(("error", str(error)))
        return
    finally:
        stop_watching.set()
        watcher.join(timeout=1.0)
        shutil.rmtree(control_directory, ignore_errors=True)
    message_queue.put(("done", results))


def _watch_for_cancellation(
    cancel_event: threading.Event,
    cancel_path: Path,
    stop_watching: threading.Event,
) -> None:
    """Translate `cancel_event` into `cancel_path`'s existence, or stop cleanly.

    A short-timeout poll loop, not an unbounded `cancel_event.wait()`:
    this daemon thread must also notice `stop_watching` (set by
    `_batch_worker` once the batch has already finished on its own, with
    no cancellation requested) so it exits promptly instead of blocking
    forever on an event nothing will ever set. `_CANCEL_WATCH_INTERVAL_
    SECONDS` bounds only how quickly a real Cancel click becomes visible
    to every worker's `LiveProgressStore` — a worker only ever checks
    `cancel_path.exists()`, so this thread's one job is making that
    become true soon after the button click, not synchronizing anything
    else.
    """
    while not stop_watching.is_set():
        if cancel_event.wait(timeout=_CANCEL_WATCH_INTERVAL_SECONDS):
            logger.debug("batch cancellation observed; touching %s", cancel_path)
            cancel_path.touch()
            return


def _replicate_store_factory(
    working_directory: Path,
    batch_run_id: str,
    cancel_path: Path,
    expert: ExpertSettings,
    replicate_run_id: str,
) -> LiveProgressStore:
    """Build one replicate's real, file-backed progress store.

    Module-level, closed over only via `functools.partial` (never a
    closure or lambda) — the same picklability discipline
    `cli._replicate_store_factory` already established for the CLI's own
    parallel batch path (`src/fim/cli.py:404`), required here for the
    identical reason: a `max_workers` worker process must be able to
    pickle a reference to this factory to call it inside itself.
    """
    directory = replicate_output_directory(
        working_directory, batch_run_id, replicate_run_id
    )
    directory.mkdir(parents=True, exist_ok=True)
    return LiveProgressStore(
        BinaryLogStore(
            directory / TRAJECTORY_LOG_FILENAME,
            block_seconds=LIVE_BLOCK_SECONDS,
            **log_options(expert),
        ),
        progress_path=directory / ".progress",
        cancel_path=cancel_path,
    )
