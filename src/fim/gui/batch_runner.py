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

Writes the same artifacts `cli._command_run_batch --sequential` does:
each replicate's own four-file scalar-run contract (reusing
`fim.gui.runner.write_run_artifacts`, the same call the scalar runner
uses), then a batch-level `summary.json`
(`fim.engine.replicate_summary`) and `manifest.json`
(`fim.persistence.manifest.write_batch_manifest`), all still inside the
one `fim.paths.atomic_directory` publish (design §3.7).
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

from fim import __version__, paths
from fim.engine import RunResult, deterministic_run_id, fim, replicate_summary
from fim.gui.runner import ProgressThrottle, run_artifact_targets, write_run_artifacts
from fim.gui.store import GuiProgressStore, RunCancelledError
from fim.model.params import SimulationParams
from fim.persistence.jsonl_store import JSONLTrajectoryStore
from fim.persistence.manifest import (
    CURRENT_BATCH_SCHEMA_VERSION,
    ArtifactDigest,
    BatchManifest,
    hash_file,
    write_batch_manifest,
)
from fim.persistence.report import write_report

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

    started_at = _format_timestamp(_utc_now())
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
            ended_at = _format_timestamp(_utc_now())
            _write_batch_artifacts(
                results, working_directory, run_id, params, started_at, ended_at
            )
    except RunCancelledError as cancelled:
        message_queue.put(("cancelled", current_replicate, cancelled.generation))
        return
    except _EXPECTED_ENGINE_ERRORS as error:
        message_queue.put(("error", str(error)))
        return
    message_queue.put(("done", results))


def _format_timestamp(value: datetime) -> str:
    """Return an unambiguous UTC ISO-8601 timestamp, matching `RunManifest`.

    Duplicated from `cli._format_timestamp` — a private CLI helper, not
    a shared import, per the same front-end-boundary convention
    `_EXPECTED_ENGINE_ERRORS` already follows.
    """
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _utc_now() -> datetime:
    """Return the current UTC time for the batch manifest's timestamps."""
    return datetime.now(UTC)


def _write_batch_artifacts(
    results: tuple[RunResult, ...],
    working_directory: Path,
    run_id: str,
    params: SimulationParams,
    started_at: str,
    ended_at: str,
) -> None:
    """Write every replicate's artifacts, then the batch-level summary and manifest.

    Mirrors `cli._command_run_batch`'s own artifact-writing pass
    exactly: each replicate's four-file scalar-run contract
    (`fim.gui.runner.write_run_artifacts`, reused rather than
    duplicated — both modules live in the same `fim.gui` package), then
    `summary.json` (`fim.engine.replicate_summary`), then — last, only
    once every sibling artifact is flushed — the batch-level
    `manifest.json`, augmented with each replicate's own manifest
    digest plus `summary.json`'s own. Unlike
    `cli._command_run_batch`'s `max_workers` (parallel) path, the GUI
    batch runner is always sequential (`max_workers=None`), so every
    replicate `fim()` returns was actually run in full — there is no
    orphan-replicate-directory case to prune here (`cli.
    _prune_orphan_replicate_directories`'s own docstring: that pass
    exists only for a worker whose replicate result was discarded by an
    adaptive stop decided *after* a whole concurrent worker batch
    completed, a shape sequential execution never produces).
    """
    artifact_digests: dict[str, ArtifactDigest] = {}
    for result in results:
        directory = replicate_output_directory(working_directory, run_id, result.run_id)
        write_run_artifacts(result, run_artifact_targets(directory))
        artifact_digests[directory.name] = hash_file(directory / "manifest.json")
    write_report(working_directory / "summary.json", replicate_summary(results))
    artifact_digests["summary"] = hash_file(working_directory / "summary.json")
    write_batch_manifest(
        working_directory / "manifest.json",
        BatchManifest(
            schema_version=CURRENT_BATCH_SCHEMA_VERSION,
            run_id=run_id,
            replicate_run_ids=tuple(result.run_id for result in results),
            parameters=params.to_dict(),
            started_at=started_at,
            ended_at=ended_at,
            software_version=__version__,
            artifacts=artifact_digests,
        ),
    )
