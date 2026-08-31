"""Background-thread scalar-run orchestration (`doc/fim-gui-design.md` §7.1).

`fim.engine.fim` is a single blocking call; a multi-thousand-generation
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
`cli._write_run_artifacts`: `trajectory.jsonl` streamed
generation-by-generation by the `TrajectoryStore` passed into `fim`,
then `report.json` and `scatter.png` once the run finishes, then —
last, and only once both are flushed — `manifest.json`, augmented with
each artifact's SHA-256 digest, the record
`fim.persistence.manifest.verify_trajectory_integrity` later checks
against.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, Final, Literal

from matplotlib import pyplot as plt

from fim import paths
from fim.engine import (
    FinalReport,
    RunResult,
    deterministic_run_id,
    fim,
    report_for_state,
)
from fim.gui.store import GuiProgressStore, RunCancelledError
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.persistence.jsonl_store import JSONLTrajectoryStore
from fim.persistence.manifest import hash_file, write_manifest
from fim.persistence.report import write_report
from fim.viz.scatter import (
    FloatArray,
    frequency_points,
    panels_from_points,
    plot_frequency_scatter,
)

# The wall-clock throttle interval: skip posting a progress tick if
# under ~50 ms have passed since the last post.
PROGRESS_THROTTLE_INTERVAL_SECONDS: Final = 0.05

# Mirrors `fim.cli.main`'s own catch-all, scoped to what a validated
# `params` and a real `fim.engine.fim` call can actually raise here — `pickle.
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

ProgressMessage = tuple[
    Literal["progress"], int, list[dict[str, object]], FloatArray, FinalReport
]
DoneMessage = tuple[Literal["done"], RunResult]
CancelledMessage = tuple[Literal["cancelled"], int]
ErrorMessage = tuple[Literal["error"], str]
RunMessage = ProgressMessage | DoneMessage | CancelledMessage | ErrorMessage

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


def run_artifact_targets(directory: Path) -> dict[str, Path]:
    """Return the four documented scalar-run artifact paths in one directory.

    Deliberately the same four names `cli._run_artifact_targets` uses
    — the exact same four calls, same target filenames, same directory
    — a direct parallel, not a shared import, since
    `cli._run_artifact_targets` is a private module-level function of
    the CLI's own front end.
    """
    return {
        "trajectory": directory / "trajectory.jsonl",
        "manifest": directory / "manifest.json",
        "report": directory / "report.json",
        "scatter": directory / "scatter.png",
    }


def start_run(
    params: SimulationParams,
    output_directory: Path,
    message_queue: queue.Queue[RunMessage],
    cancel_event: threading.Event,
    *,
    clock: Callable[[], float] = time.monotonic,
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
            # `points` rides along raw (not only the already-reduced
            # `panels`) so `fim.gui.app._drain_run_messages` can compute
            # one caller-chosen deme-pair panel per tick too, for the
            # Progress screen's own live "Compare demes directly"
            # selector — `scatter_panels(state)`'s own body is exactly
            # this same `frequency_points` then `panels_from_points`
            # pair, computed here directly instead so `points` is not
            # thrown away after producing `panels`.
            points = frequency_points(state)
            panels = panels_from_points(points, state.deme_count)
            # The six named statistics for *this* tick's state, the
            # same `report_for_state` call `Api.get_initial_state_panels`
            # makes for p_0 — the running state's own stats table
            # updates live from this instead of sitting blank until the
            # run finishes. `converged`/`reason` are both
            # placeholders; nothing downstream reads them off a
            # progress tick's own report, only the six numeric fields.
            report = report_for_state(
                state, params, run_id=run_id, converged=False, reason="in progress"
            )
            message_queue.put(("progress", generation, panels, points, report))

    try:
        with paths.atomic_directory(output_directory) as working_directory:
            targets = run_artifact_targets(working_directory)
            store = GuiProgressStore(
                JSONLTrajectoryStore(targets["trajectory"]),
                on_generation=on_generation,
                cancel_event=cancel_event,
            )
            result = fim(
                params.N,
                params.m,
                params.mu,
                params.d,
                params=params,
                store=store,
                run_id=run_id,
            )
            if not isinstance(result, RunResult):
                # n_replicates == 1 is enforced by every path that can
                # reach this worker — multi-replicate runs are out of
                # scope for the scalar GUI runner (`doc/fim-gui-design.md`
                # §7.2 covers the batch path instead), so
                # `fim()` always takes its scalar branch here — this
                # guards the invariant rather than silently mishandling
                # a batch. Raised inside the `with` block so
                # `atomic_directory` discards the temporary directory
                # exactly as it would for any other engine error.
                raise RuntimeError("unexpected batch result from a scalar run")
            write_run_artifacts(result, targets)
    except RunCancelledError as cancelled:
        message_queue.put(("cancelled", cancelled.generation))
        return
    except _EXPECTED_ENGINE_ERRORS as error:
        message_queue.put(("error", str(error)))
        return
    message_queue.put(("done", result))


def write_run_artifacts(result: RunResult, targets: dict[str, Path]) -> None:
    """Write `report.json`, `scatter.png`, and — last — `manifest.json`.

    Mirrors `cli._write_run_artifacts` exactly: `trajectory.jsonl` is
    not written here, since it was already streamed
    generation-by-generation by the `TrajectoryStore` passed into
    `fim`; every other artifact is written and flushed first, and
    `manifest.json` is written only once every sibling artifact is
    flushed, augmented with each one's SHA-256 digest. The returned
    `Figure` is closed immediately — the caller's worker thread never
    displays it, unlike the CLI's own `scatter.png` render, which the
    caller keeps alive and is responsible for closing itself. Public
    rather than
    module-private: `fim.gui.batch_runner` reuses this same call, once
    per replicate, rather than duplicating it — both modules live in
    the same `fim.gui` package, unlike the CLI/GUI front-end boundary
    `run_artifact_targets`'s own docstring keeps deliberately parallel
    instead of shared.
    """
    write_report(targets["report"], result.report)
    figure = plot_frequency_scatter(
        result.final_state, result.params, targets["scatter"]
    )
    plt.close(figure)
    manifest = replace(
        result.manifest,
        artifacts={
            name: hash_file(targets[name])
            for name in ("trajectory", "report", "scatter")
        },
    )
    write_manifest(targets["manifest"], manifest)
