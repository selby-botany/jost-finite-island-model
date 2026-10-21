"""Background execution for saved Run objects in the desktop interface.

Execution and lifecycle transitions use the same services as `fim object
start` and `fim object resume`. The worker reports progress and responds to
pause and cancellation requests at safe points.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final, Literal

import yaml

from fim.batch_session import BatchSession, check_batch_session_support
from fim.config.defaults import DEFAULT_PAIRWISE_MAX_DEMES
from fim.engine import RunResult
from fim.execution import ScalarSession, check_session_support
from fim.gui.batch_runner import BatchMessage
from fim.gui.presets import split_configuration
from fim.gui.runner import (
    ProgressThrottle,
    RunMessage,
    progress_message,
)
from fim.gui.store import LIVE_BLOCK_SECONDS, write_progress_sidecar
from fim.model.params import SimulationParams
from fim.persistence.artifacts import write_run_artifacts
from fim.persistence.binary_store import BinaryLogStore, log_options
from fim.persistence.run_lifecycle import (
    LifecycleError,
    RunLifecycleRecord,
    RunState,
)
from fim.persistence.run_objects import read_run_object
from fim.run_service import (
    DEFAULT_CHECKPOINT_GENERATIONS,
    DEFAULT_CHECKPOINT_SECONDS,
    CheckpointCadence,
    DraftRunError,
    Session,
    cadence_for_params,
    execute_run_object,
)

# Pause and Cancel are checked, and the Run card is offered a progress
# tick, every this many generations: small enough that a Pause click is
# acknowledged promptly, large enough that the check costs nothing.
GUI_CONTROL_GENERATIONS: Final = 10

GUI_CADENCE: Final = CheckpointCadence(
    generations=DEFAULT_CHECKPOINT_GENERATIONS,
    seconds=DEFAULT_CHECKPOINT_SECONDS,
    control_generations=GUI_CONTROL_GENERATIONS,
)

# Mirrors `fim.gui.runner._EXPECTED_ENGINE_ERRORS`: what an execution can
# raise once it has started. The lifecycle service has already recorded
# the run as `failed` (with the error) before any of these reach here.
_EXPECTED_ENGINE_ERRORS: Final = (
    ArithmeticError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)

logger = logging.getLogger(__name__)


def start_object_run(
    object_id: str,
    message_queue: queue.Queue[RunMessage] | queue.Queue[BatchMessage],
    *,
    results: Path | None = None,
    cadence: CheckpointCadence | None = None,
    clock: Callable[[], float] = time.monotonic,
    pairwise_max_demes: int = DEFAULT_PAIRWISE_MAX_DEMES,
    view_level: Callable[[], Literal["full", "summary"]] = lambda: "full",
    observer: Callable[[Session], None] | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> threading.Thread:
    """Start or resume a saved Run object on a background thread.

    The thread is not a daemon: an application that is closing waits for
    it, so a Pause requested at close is checkpointed before the process
    exits.

    Args:
        object_id: The saved Run to execute.
        message_queue: Receives every `runner.RunMessage` or
            `batch_runner.BatchMessage` the run posts.
        results: Results directory (default: `results_directory()`).
        cadence: Checkpoint and control cadence.
        clock: Injectable wall clock for the progress throttle.
        pairwise_max_demes: Largest deme count whose `pairwise.json` is
            written when the run completes.
        view_level: Read on each progress tick; summary avoids building
            visualization payloads for background jobs.
        observer: Test-only hook, called with the session at every
            control point, after its progress tick.
        stop_requested: Polled at safe points; true asks the run to pause.

    Returns:
        The started (not yet joined) worker thread.
    """
    started = threading.Event()

    def work() -> None:
        """Run the worker and always release the startup handshake."""
        try:
            _object_worker(
                object_id,
                message_queue,
                results=results,
                cadence=cadence,
                clock=clock,
                pairwise_max_demes=pairwise_max_demes,
                view_level=view_level,
                observer=observer,
                stop_requested=stop_requested,
                started=started,
            )
        finally:
            started.set()

    thread = threading.Thread(target=work, name=f"fim-object-{object_id}")
    thread.start()
    started.wait()
    logger.debug("saved run worker thread started: %s", object_id)
    return thread


def _inspect_batch_target(
    object_id: str, results: Path | None
) -> tuple[Path | None, bool]:
    """Return the run object's directory and whether its configuration is a batch."""
    try:
        run_object = read_run_object(object_id, results=results)
        model, _ = split_configuration(yaml.safe_load(run_object.config_text))
        return run_object.directory, int(model.get("n_replicates", 1)) > 1
    except Exception:
        return None, False


def _post_terminal_outcome(
    record: RunLifecycleRecord,
    completed: list[RunResult],
    *,
    is_batch: bool,
    message_queue: queue.Queue[Any],
) -> None:
    """Post the final result, pause, cancel, or error message to the queue."""
    generation = record.committed_generation or 0
    if record.state is RunState.COMPLETED and completed:
        message_queue.put(("done", tuple(completed) if is_batch else completed[0]))
    elif record.state is RunState.PAUSED:
        message_queue.put(("paused", generation))
    elif record.state is RunState.CANCELLED:
        message_queue.put(
            ("cancelled", 0, generation) if is_batch else ("cancelled", generation)
        )
    elif record.state is RunState.COMPLETED:
        message_queue.put(
            ("error", "this saved run had already finished; its results are saved")
        )
    else:
        message_queue.put(("error", f"the saved run is {record.state.value}"))


def _object_worker(
    object_id: str,
    message_queue: queue.Queue[Any],
    *,
    results: Path | None,
    cadence: CheckpointCadence | None,
    clock: Callable[[], float],
    pairwise_max_demes: int,
    view_level: Callable[[], Literal["full", "summary"]],
    observer: Callable[[Session], None] | None,
    stop_requested: Callable[[], bool] | None,
    started: threading.Event,
) -> None:
    """Execute one saved Run and post its outcome to `message_queue`."""
    throttle = ProgressThrottle(clock=clock)
    completed: list[RunResult] = []
    run_dir, is_batch = _inspect_batch_target(object_id, results)

    if is_batch and run_dir is not None:
        message_queue.put(("started", run_dir))

    try:
        target_obj = read_run_object(object_id, results=results)
        model_dict, _ = split_configuration(yaml.safe_load(target_obj.config_text))
        run_params = SimulationParams.from_mapping(model_dict)
        effective_cadence = (
            cadence
            if cadence is not None
            else cadence_for_params(
                run_params, control_generations=GUI_CONTROL_GENERATIONS
            )
        )
    except Exception:
        effective_cadence = cadence or GUI_CADENCE

    def observe(session: Session) -> None:
        if isinstance(session, BatchSession) and run_dir is not None:
            for lane in session.lanes:
                rep_dir = run_dir / f"replicate-{lane.replica_index + 1:03d}"
                if rep_dir.is_dir():
                    write_progress_sidecar(rep_dir / ".progress", lane.state.generation)
        elif isinstance(session, ScalarSession):
            lane = session.lane
            if throttle.should_report(session.generation, lane.params.max_generations):
                if run_dir is not None:
                    write_progress_sidecar(run_dir / ".progress", session.generation)
                message_queue.put(
                    progress_message(
                        lane.state,
                        session.generation,
                        lane.params,
                        session.run_id,
                        view_level=view_level(),
                    )
                )
        if observer is not None:
            observer(session)

    def write(result: RunResult, directory: Path) -> None:
        completed.append(result)
        write_run_artifacts(
            result,
            directory,
            pairwise_max_demes=pairwise_max_demes,
        )

    def store_factory(path: Path, params: SimulationParams) -> BinaryLogStore:
        return BinaryLogStore(
            path, block_seconds=LIVE_BLOCK_SECONDS, **log_options(params.expert)
        )

    try:
        finished = execute_run_object(
            object_id,
            write_artifacts=write,
            results=results,
            cadence=effective_cadence,
            observer=observe,
            check_support=(
                check_batch_session_support if is_batch else check_session_support
            ),
            store_factory=store_factory,
            stop_requested=stop_requested,
            on_started=started.set,
        )
    except DraftRunError as error:
        issues = "; ".join(issue.message for issue in error.issues)
        message_queue.put(("error", f"this saved run is a draft: {issues}"))
        return
    except LifecycleError as error:
        try:
            current = read_run_object(object_id, results=results)
        except (LifecycleError, OSError, ValueError) as read_error:
            logger.error(
                "could not read Run object %s after a lifecycle error: %s",
                object_id,
                read_error,
            )
            message_queue.put(("error", str(error)))
            return
        if current.record.state is RunState.CANCELLED:
            message_queue.put(("cancelled", current.record.committed_generation or 0))
            return
        message_queue.put(("error", str(error)))
        return
    except _EXPECTED_ENGINE_ERRORS as error:
        message_queue.put(("error", str(error)))
        return

    _post_terminal_outcome(
        finished.record, completed, is_batch=is_batch, message_queue=message_queue
    )
