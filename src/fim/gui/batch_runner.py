"""Background-thread batch-run orchestration (design §3.4, §3.7, §2.2).

`fim.gui.runner` runs one scalar simulation on a background thread;
this module runs a multi-replicate batch the same way, sequentially
(`max_workers=None`, always — §2.2: the GUI's progress/cancellation
mechanism, `GuiProgressStore`, is an in-process decorator around one
`threading.Thread`, and `max_workers` parallelism runs replicates in
separate OS processes that cannot share it). This mirrors
`cli._command_run_batch --sequential`'s own call shape exactly:
`fim.engine.fim(..., store_factory=..., max_workers=None)`.

Every replicate gets its own `GuiProgressStore`, all sharing one
`cancel_event` — Cancel stops the whole batch, not one replicate
(design §4.0 #6): there is no partial-batch save point, since the
whole tree is built inside one `fim.paths.atomic_directory` publish,
exactly as `fim.gui.runner` does for a scalar run.

Progress posts as `("replicate", replicate_index, generation)` instead
of `fim.gui.runner`'s `("progress", generation)` alone, so Screen 2 can
render both the outer (replicate count) and inner (generation count)
progress axes (design §3.4, §4.2).

Writes only each replicate's `trajectory.jsonl` so far; `report.json`,
`scatter.png`, and `manifest.json` per replicate, plus the batch-level
`summary.json` and `manifest.json`, are added by this milestone's third
bullet (§7.6), inside the same `with` block, once the batch results
screen exists to display them.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Final, Literal

from fim import paths
from fim.engine import RunResult, deterministic_run_id, fim
from fim.gui.runner import ProgressThrottle
from fim.gui.store import GuiProgressStore, RunCancelledError
from fim.model.params import SimulationParams
from fim.persistence.jsonl_store import JSONLTrajectoryStore

# Mirrors `fim.gui.runner`'s own catch-all — see its definition for the
# full rationale. Duplicated rather than imported: `fim.gui.runner`'s
# own `run_artifact_targets`/`cli._run_artifact_targets` precedent is "a
# direct parallel, not a shared import" for exactly this kind of private
# front-end-local constant.
_EXPECTED_ENGINE_ERRORS: Final = (
    ArithmeticError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)

ReplicateProgressMessage = tuple[Literal["replicate"], int, int]
DoneMessage = tuple[Literal["done"], tuple[RunResult, ...]]
CancelledMessage = tuple[Literal["cancelled"], int, int]
ErrorMessage = tuple[Literal["error"], str]
BatchMessage = ReplicateProgressMessage | DoneMessage | CancelledMessage | ErrorMessage


def replicate_index(batch_run_id: str, replicate_run_id: str) -> int:
    """Return one replicate's 1-based ordinal within its batch.

    `replicate_run_id` is always exactly ``f"{batch_run_id}-r{index:03}"``
    (`fim.engine.fim`'s own batch run-ID convention, matching
    `cli._replicate_output_directory`'s identical parsing), so the
    zero-padded index is recovered from it directly.
    """
    return int(replicate_run_id.removeprefix(f"{batch_run_id}-r"))


def replicate_output_directory(
    base: Path, batch_run_id: str, replicate_run_id: str
) -> Path:
    """Return one replicate's own artifact subdirectory.

    The same ``replicate-NNN`` naming `cli._replicate_output_directory`
    uses — a direct parallel, not a shared import, since that function
    is private to the CLI's own front end.
    """
    return base / f"replicate-{replicate_index(batch_run_id, replicate_run_id):03}"


def start_batch_run(
    params: SimulationParams,
    output_directory: Path,
    message_queue: queue.Queue[BatchMessage],
    cancel_event: threading.Event,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> threading.Thread:
    """Resolve targets, guard the existing target, and start the worker thread.

    Args:
        params: Already-validated parameters with `n_replicates > 1` —
            the screen calling this routes to the scalar runner instead
            whenever `n_replicates == 1` (design §4.1: "there is no
            separate 'batch mode' toggle; `n_replicates` *is* the
            toggle").
        output_directory: The batch's target directory, passed straight
            to `fim.paths.atomic_directory` by the worker thread.
            Checked for existence synchronously here too, exactly like
            `fim.gui.runner.start_run`.
        message_queue: Every `BatchMessage` the worker posts lands
            here; the caller drains it (typically from a Tk
            `root.after` poll).
        cancel_event: Set by the UI's "Cancel batch" button; checked
            before every generation write, in whichever replicate is
            currently running.
        clock: Injectable wall clock for `ProgressThrottle`, for tests.

    Returns:
        The started (not yet joined) worker thread.

    Raises:
        FileExistsError: If `output_directory` already exists.
    """
    if output_directory.exists():
        raise FileExistsError(f"output directory already exists: {output_directory}")
    run_id = deterministic_run_id(params)
    thread = threading.Thread(
        target=_batch_worker,
        args=(params, run_id, output_directory, message_queue, cancel_event, clock),
    )
    thread.start()
    return thread


def _batch_worker(
    params: SimulationParams,
    run_id: str,
    output_directory: Path,
    message_queue: queue.Queue[BatchMessage],
    cancel_event: threading.Event,
    clock: Callable[[], float],
) -> None:
    """Run one replicate batch sequentially and post its outcome to `message_queue`.

    Every write happens inside `fim.paths.atomic_directory(output_directory)`,
    exactly as `fim.gui.runner._run_worker` does for a scalar run: a
    `RunCancelledError` from whichever replicate is currently running,
    or any member of `_EXPECTED_ENGINE_ERRORS`, propagates out of the
    `with` block and `atomic_directory` discards the whole temporary
    tree — no partial-batch save point exists to preserve, matching
    "Cancel batch" stopping the batch, not one replicate (design §4.0
    #6).
    """
    throttle = ProgressThrottle(clock=clock)
    current_replicate: int = 0

    def on_generation(generation: int) -> None:
        if throttle.should_report(generation, params.max_generations):
            message_queue.put(("replicate", current_replicate, generation))

    def replicate_store_factory(replicate_run_id: str) -> GuiProgressStore:
        nonlocal current_replicate
        current_replicate = replicate_index(run_id, replicate_run_id)
        directory = replicate_output_directory(
            working_directory, run_id, replicate_run_id
        )
        directory.mkdir(parents=True, exist_ok=True)
        return GuiProgressStore(
            JSONLTrajectoryStore(directory / "trajectory.jsonl"),
            on_generation=on_generation,
            cancel_event=cancel_event,
        )

    try:
        with paths.atomic_directory(output_directory) as working_directory:
            results = fim(
                params.N,
                params.m,
                params.mu,
                params.d,
                params=params,
                run_id=run_id,
                max_workers=None,
                store_factory=replicate_store_factory,
            )
            if not isinstance(results, tuple):
                # n_replicates > 1 is enforced by every path that can
                # reach this worker (see `start_batch_run`'s own
                # docstring), so `fim()` always takes its batch branch
                # here — this guards the invariant rather than
                # silently mishandling a scalar result. Raised inside
                # the `with` block so `atomic_directory` discards the
                # temporary directory exactly as it would for any
                # other engine error.
                raise RuntimeError("unexpected scalar result from a batch run")
    except RunCancelledError as cancelled:
        message_queue.put(("cancelled", current_replicate, cancelled.generation))
        return
    except _EXPECTED_ENGINE_ERRORS as error:
        message_queue.put(("error", str(error)))
        return
    message_queue.put(("done", results))
