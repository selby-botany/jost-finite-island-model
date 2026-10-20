"""Background-thread scalar-run orchestration (`doc/fim-gui-design.md` §7.1).

`fim.engine.simulate_params` is a single blocking call; a multi-thousand-generation
run would freeze the Tk main thread for its whole duration. `start_run`
runs it on a `threading.Thread` instead, and gets progress and
cancellation for free from `fim.gui.store.GuiProgressStore` — no change
to `fim.engine` at all.

The worker does its work inside `fim.paths.atomic_directory`, the exact
context manager `cli._command_run_scalar` already uses (`fim.paths` was
extracted from the CLI for precisely this shared use — §12): every
write lands in a
hidden temporary sibling of `output_directory`, published with one
atomic rename only if the `with` block exits normally. A cancelled run
raises `RunCancelledError` out of that block; an unexpected engine error
raises one of `_EXPECTED_ENGINE_ERRORS`. Either way, `atomic_directory`'s
own `except BaseException` clause discards the temporary directory and
`output_directory` is never created — no GUI-specific cleanup code is
needed for either outcome.

Writes the same four artifacts, in the same order, as
`fim.persistence.artifacts.write_run_artifacts`: `trajectory.tlog` streamed
generation-by-generation by the `TrajectoryStore` passed into `fim`,
then `report.json` and `scatter.png` once the run finishes, then —
last, and only once both are flushed — `manifest.json`, augmented with
each artifact's SHA-256 digest, the record
`fim.persistence.manifest.verify_trajectory_integrity` later checks
against.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final, Literal

from fim import paths
from fim.config.defaults import DEFAULT_PAIRWISE_MAX_DEMES
from fim.engine import (
    FinalReport,
    RunResult,
    deterministic_run_id,
    report_for_state,
    simulate_params,
)
from fim.gui.literature_visuals import (
    allele_composition_payload,
    frequency_spectrum_payload,
)
from fim.gui.store import LIVE_BLOCK_SECONDS, GuiProgressStore, RunCancelledError
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.persistence.artifacts import run_artifact_targets, write_run_artifacts
from fim.persistence.binary_store import (
    BinaryLogStore,
    log_options,
)
from fim.persistence.pairwise import (
    SLOW_PAIRWISE_DEMES,
)
from fim.viz.scatter import (
    FloatArray,
    frequency_points,
    panels_from_points,
)

# The wall-clock throttle interval: skip posting a progress tick if
# under ~50 ms have passed since the last post.
PROGRESS_THROTTLE_INTERVAL_SECONDS: Final = 0.05

# Mirrors `fim.cli.main`'s own catch-all, scoped to what a validated
# `params` and a real `simulate_params` call can actually raise here — `pickle.
# PicklingError` and `yaml.YAMLError` are catch-all members that apply
# only to `max_workers` batches and config parsing, neither of which
# this scalar, already-validated worker ever exercises.
_EXPECTED_ENGINE_ERRORS: Final = (
    ArithmeticError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)

# The trailing `ModelState` lets the message drain compute the
# statistics of whichever deme pair the scatter shows this tick.
ProgressMessage = tuple[
    Literal["progress"],
    int,
    list[dict[str, object]] | None,
    FloatArray | None,
    FinalReport,
    dict[str, Any] | None,
    ModelState,
]
DoneMessage = tuple[Literal["done"], RunResult]
CancelledMessage = tuple[Literal["cancelled"], int]
# A saved Run (`fim.gui.object_runner`) that published a checkpoint at this
# generation and stopped; it can be resumed later.
PausedMessage = tuple[Literal["paused"], int]
ErrorMessage = tuple[Literal["error"], str]
# A non-terminal note for the Run card's status line, such as "saving
# every deme pair's statistics" for a large run.
StatusMessage = tuple[Literal["status"], str]
RunMessage = (
    ProgressMessage
    | DoneMessage
    | CancelledMessage
    | PausedMessage
    | ErrorMessage
    | StatusMessage
)

logger = logging.getLogger(__name__)


class ProgressThrottle:
    """Decide which generation numbers reach the UI, by wall clock.

    Posting every generation is cheap for the queue but would flood a
    1500+-generation run's UI with redraws; throttling by a fixed
    generation stride would need `max_generations` up front in a way
    that scales badly for a very short or very long run. Instead this
    skips a report if under `interval_seconds` has elapsed since the
    last one — except `generation == max_generations`, which is always
    reported so a run that reaches the hard cap never appears stuck
    short of 100%. A run that instead stops early via convergence still
    gets a correct final state: the worker's own "done"/"cancelled"
    message carries its own generation number independent of this
    throttle.
    """

    def __init__(
        self,
        *,
        interval_seconds: float = PROGRESS_THROTTLE_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Start with no prior report, so the very first call always reports."""
        self._interval_seconds = interval_seconds
        self._clock = clock
        self._last_reported_at: float | None = None

    def should_report(self, generation: int, max_generations: int) -> bool:
        """Return whether `generation` should be posted to the UI now."""
        if generation >= max_generations:
            self._last_reported_at = self._clock()
            return True
        now = self._clock()
        if (
            self._last_reported_at is None
            or now - self._last_reported_at >= self._interval_seconds
        ):
            self._last_reported_at = now
            return True
        return False


def pairwise_status_text(deme_count: int, run_count: int) -> str:
    """Return the status line shown while large `pairwise.json` files are saved.

    Args:
        deme_count: The run's deme count.
        run_count: How many runs (replicates) are being saved.

    Returns:
        A short sentence for the Run card's status line.
    """
    pairs = deme_count * (deme_count - 1) // 2
    files = "file" if run_count == 1 else f"files for {run_count} replicates"
    return (
        f"Saving every deme pair's statistics ({pairs:,} pairs of {deme_count} "
        f"demes) to the pairwise {files}…"
    )


def progress_message(
    state: ModelState,
    generation: int,
    params: SimulationParams,
    run_id: str,
    *,
    view_level: Literal["full", "summary"] = "full",
) -> ProgressMessage:
    """Build a full or compact progress message for the current job view.

    Shared by this module's worker and `fim.gui.object_runner`, so a
    saved Run and an ordinary one draw identical ticks. Summary messages
    retain the generation, statistic estimates, and state while omitting
    panels, points, and literature visuals.

    Args:
        state: The population at `generation`.
        generation: The generation just recorded.
        params: The run's parameters.
        run_id: The run's scientific run ID.

    Returns:
        The message to put on the run's queue.
    """
    # The six named statistics for this tick's state, the same
    # `report_for_state` call `Api.get_initial_state_panels` makes for
    # p_0. `converged`/`reason` are placeholders; nothing downstream
    # reads them off a progress tick, only the six numeric fields.
    report = report_for_state(
        state, params, run_id=run_id, converged=False, reason="in progress"
    )
    if view_level == "summary":
        return ("progress", generation, None, None, report, None, state)
    # `points` rides along raw (not only the already-reduced `panels`)
    # so `fim.gui.app._drain_run_messages` can compute one caller-chosen
    # deme-pair panel per tick too, for the Progress screen's live
    # "Compare demes directly" selector.
    points = frequency_points(state)
    panels = panels_from_points(points, state.deme_count)
    visuals = {
        "alleleComposition": allele_composition_payload(state),
        "frequencySpectrum": frequency_spectrum_payload(state, params),
    }
    return ("progress", generation, panels, points, report, visuals, state)


def start_run(
    params: SimulationParams,
    output_directory: Path,
    message_queue: queue.Queue[RunMessage],
    cancel_event: threading.Event,
    *,
    clock: Callable[[], float] = time.monotonic,
    pairwise_max_demes: int = DEFAULT_PAIRWISE_MAX_DEMES,
    view_level: Callable[[], Literal["full", "summary"]] = lambda: "full",
) -> threading.Thread:
    """Resolve targets, guard the existing target, and start the worker thread.

    Args:
        params: Already-validated parameters — the screen calling this
            never hands it an unvalidated payload.
        output_directory: The run's target artifact directory, passed
            straight to `fim.paths.atomic_directory` by the worker
            thread. Checked for existence synchronously here too, so a
            pre-existing target is reported to the caller immediately —
            before a thread starts or a progress screen appears —
            rather than only discovered later via the message queue.
        message_queue: Every `RunMessage` the worker posts lands here;
            the caller drains it (typically from a Tk `root.after`
            poll).
        cancel_event: Set by the UI's Cancel button; checked before
            every generation write.
        clock: Injectable wall clock for `ProgressThrottle`, for tests.
        view_level: Read on each reported generation; summary avoids
            building visualization payloads for background jobs.

    Returns:
        The started (not yet joined) worker thread.

    Raises:
        FileExistsError: If `output_directory` already exists.
    """
    if output_directory.exists():
        raise FileExistsError(f"output directory already exists: {output_directory}")
    run_id = deterministic_run_id(params)
    thread = threading.Thread(
        target=_run_worker,
        args=(params, run_id, output_directory, message_queue, cancel_event, clock),
        kwargs={
            "pairwise_max_demes": pairwise_max_demes,
            "view_level": view_level,
        },
    )
    thread.start()
    logger.debug("scalar run worker thread started: %s -> %s", run_id, output_directory)
    return thread


def _run_worker(
    params: SimulationParams,
    run_id: str,
    output_directory: Path,
    message_queue: queue.Queue[RunMessage],
    cancel_event: threading.Event,
    clock: Callable[[], float],
    *,
    pairwise_max_demes: int = DEFAULT_PAIRWISE_MAX_DEMES,
    view_level: Callable[[], Literal["full", "summary"]] = lambda: "full",
) -> None:
    """Run one scalar simulation and post its outcome to `message_queue`.

    Every write happens inside `fim.paths.atomic_directory(output_directory)`
    — the same hidden-temporary-sibling-then-atomic-rename mechanism
    `cli._command_run_scalar` uses. `RunCancelledError` and every member
    of `_EXPECTED_ENGINE_ERRORS` both propagate out of the `with` block,
    so `atomic_directory`'s own exception handling discards the
    temporary directory in either case: no `shutil.rmtree` call, and no
    other cleanup, appears anywhere in this function. `("done", result)`
    is posted only after the `with` block exits — that is, only once
    `output_directory` has already been published by the atomic rename
    — so a screen reacting to "done" can rely on every artifact already
    being on disk at that path.
    """
    throttle = ProgressThrottle(clock=clock)

    def on_generation(generation: int, rows: list[Mapping[str, Any]]) -> None:
        if throttle.should_report(generation, params.max_generations):
            state = ModelState.from_rows(rows, params.loci)
            message_queue.put(
                progress_message(
                    state, generation, params, run_id, view_level=view_level()
                )
            )

    try:
        with paths.atomic_directory(output_directory) as working_directory:
            targets = run_artifact_targets(working_directory)
            # Closed here as well as by the engine, before
            # `atomic_directory` renames (or, on a cancel or failure,
            # removes) the directory: an open handle blocks both on
            # Windows.
            with contextlib.closing(
                GuiProgressStore(
                    BinaryLogStore(
                        targets["trajectory"],
                        block_seconds=LIVE_BLOCK_SECONDS,
                        **log_options(params.expert),
                    ),
                    on_generation=on_generation,
                    cancel_event=cancel_event,
                )
            ) as store:
                result = simulate_params(
                    params=params,
                    store=store,
                    run_id=run_id,
                )
            if not isinstance(result, RunResult):
                # n_replicates == 1 is enforced by every path that can
                # reach this worker — multi-replicate runs are out of
                # scope for the scalar GUI runner (`doc/fim-gui-design.md`
                # §7.2 covers the batch path instead), so
                # `simulate_params()` always takes its scalar branch here — this
                # guards the invariant rather than silently mishandling
                # a batch. Raised inside the `with` block so
                # `atomic_directory` discards the temporary directory
                # exactly as it would for any other engine error.
                raise RuntimeError("unexpected batch result from a scalar run")
            if SLOW_PAIRWISE_DEMES <= params.d <= pairwise_max_demes:
                message_queue.put(("status", pairwise_status_text(params.d, 1)))
            write_run_artifacts(
                result, working_directory, pairwise_max_demes=pairwise_max_demes
            )
    except RunCancelledError as cancelled:
        message_queue.put(("cancelled", cancelled.generation))
        return
    except _EXPECTED_ENGINE_ERRORS as error:
        message_queue.put(("error", str(error)))
        return
    message_queue.put(("done", result))
