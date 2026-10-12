"""Background-thread execution of a saved Run object for the GUI.

The counterpart to `fim.gui.runner` for a Run saved with "Save for later"
(`fim.persistence.run_objects`). Execution goes through the same lifecycle
service the CLI's `fim object start/resume` uses
(`fim.run_service.execute_run_object`), so the GUI never applies lifecycle
rules of its own. Pause and Cancel are durable requests recorded in the
object's `run.json` (`request_pause`, `cancel_run_object`); the worker
honors them at its next safe point.

The worker posts the same `runner.RunMessage` shapes `fim.gui.runner`
does, plus `("paused", generation)`, so `fim.gui.app._drain_run_messages`
draws both kinds of run identically. Only one run is in flight at a time,
and each run has its own queue, so a message from an earlier attempt can
never reach a later attempt's view.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Final

from fim.config.defaults import DEFAULT_PAIRWISE_MAX_DEMES
from fim.engine import RunResult
from fim.execution import ScalarSession, check_session_support
from fim.gui.runner import (
    ProgressThrottle,
    RunMessage,
    progress_message,
    run_artifact_targets,
    write_run_artifacts,
)
from fim.persistence.run_lifecycle import LifecycleError, RunState
from fim.run_service import (
    DEFAULT_CHECKPOINT_GENERATIONS,
    DEFAULT_CHECKPOINT_SECONDS,
    CheckpointCadence,
    DraftRunError,
    Session,
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
    message_queue: queue.Queue[RunMessage],
    *,
    results: Path | None = None,
    cadence: CheckpointCadence = GUI_CADENCE,
    clock: Callable[[], float] = time.monotonic,
    pairwise_max_demes: int = DEFAULT_PAIRWISE_MAX_DEMES,
    observer: Callable[[ScalarSession], None] | None = None,
) -> threading.Thread:
    """Start or resume a saved Run object on a background thread.

    The thread is not a daemon: an application that is closing waits for
    it, so a Pause requested at close is checkpointed before the process
    exits.

    Args:
        object_id: The saved Run to execute.
        message_queue: Receives every `runner.RunMessage` the run posts.
        results: Results directory (default: `results_directory()`).
        cadence: Checkpoint and control cadence.
        clock: Injectable wall clock for the progress throttle.
        pairwise_max_demes: Largest deme count whose `pairwise.json` is
            written when the run completes.
        observer: Test-only hook, called with the session at every
            control point, after its progress tick.

    Returns:
        The started (not yet joined) worker thread.
    """
    thread = threading.Thread(
        target=_object_worker,
        args=(object_id, message_queue),
        kwargs={
            "results": results,
            "cadence": cadence,
            "clock": clock,
            "pairwise_max_demes": pairwise_max_demes,
            "observer": observer,
        },
        name=f"fim-object-{object_id}",
    )
    thread.start()
    logger.debug("saved run worker thread started: %s", object_id)
    return thread


def _object_worker(
    object_id: str,
    message_queue: queue.Queue[RunMessage],
    *,
    results: Path | None,
    cadence: CheckpointCadence,
    clock: Callable[[], float],
    pairwise_max_demes: int,
    observer: Callable[[ScalarSession], None] | None,
) -> None:
    """Execute one saved Run and post its outcome to `message_queue`."""
    throttle = ProgressThrottle(clock=clock)
    completed: list[RunResult] = []

    def observe(session: Session) -> None:
        # `check_session_support` below refuses batches, so every session
        # here is scalar.
        assert isinstance(session, ScalarSession)
        # The lineal and generation-first scalar sessions keep
        # `lane.state` current after every advance.
        lane = session.lane
        if throttle.should_report(session.generation, lane.params.max_generations):
            message_queue.put(
                progress_message(
                    lane.state, session.generation, lane.params, session.run_id
                )
            )
        if observer is not None:
            observer(session)

    def write(result: RunResult, directory: Path) -> None:
        completed.append(result)
        write_run_artifacts(
            result,
            run_artifact_targets(directory),
            pairwise_max_demes=pairwise_max_demes,
        )

    try:
        finished = execute_run_object(
            object_id,
            write_artifacts=write,
            results=results,
            cadence=cadence,
            observer=observe,
            check_support=check_session_support,
        )
    except DraftRunError as error:
        issues = "; ".join(issue.message for issue in error.issues)
        message_queue.put(("error", f"this saved run is a draft: {issues}"))
        return
    except LifecycleError as error:
        message_queue.put(("error", str(error)))
        return
    except _EXPECTED_ENGINE_ERRORS as error:
        message_queue.put(("error", str(error)))
        return
    record = finished.record
    generation = record.committed_generation or 0
    if record.state is RunState.COMPLETED and completed:
        message_queue.put(("done", completed[0]))
    elif record.state is RunState.PAUSED:
        message_queue.put(("paused", generation))
    elif record.state is RunState.CANCELLED:
        message_queue.put(("cancelled", generation))
    else:
        # A replayed request (another window already ran this attempt).
        message_queue.put(("error", f"the saved run is {record.state.value}"))
