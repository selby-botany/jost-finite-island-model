"""Background-thread batch-run orchestration (`doc/fim-gui-design.md` §7.2).

Runs a multi-replicate batch in parallel, as real OS processes, via
`fim.engine.fim(..., max_workers=N, store_factory=...)` — the same call
shape `cli._command_run_batch`'s own default (non-`--sequential`) path
already makes. This reverses the Tk-era design's "sequential-only,
deliberately" decision: that constraint belonged to `GuiProgressStore`'s
in-process `threading.Event`/callback pair, which cannot cross a process
boundary — not to the engine, which has supported real parallel replicate
execution since before any GUI existed.

Progress and cancellation for this parallel path are entirely
file-mediated (`fim.gui.store.LiveProgressStore`), not posted through
`message_queue` per generation the way the Tk-era sequential runner did:
a lightweight poller elsewhere (the pywebview bridge, not yet built)
discovers progress by reading each in-flight replicate's own `.progress`
sidecar, and requests cancellation by creating one shared file every
worker's `LiveProgressStore` checks before each write. `message_queue`
still carries the batch's terminal outcome — done, cancelled, or error —
exactly as before, since those remain discrete events worth queueing;
only per-generation progress moved off the queue and onto the filesystem.

Writes the same artifacts `cli._command_run_batch`'s own default
(parallel) path does, including the same orphan-replicate-directory
pruning `cli._prune_orphan_replicate_directories` performs: under
`max_workers`, an adaptive `replicate_tolerance` stop is applied only
after a whole concurrent worker batch completes
(`fim.engine._run_batch_parallel`), so a worker beyond the replicate that
triggered the stop can still have fully written its own `replicate-NNN/`
directory even though its result is discarded, never appearing in the
tuple `fim` returns. Without pruning, `fim.paths.atomic_directory` would
publish that orphan directory verbatim — a bug class the Tk-era sequential
runner could never hit (sequential execution never overshoots), mirrored
here now that real parallelism reopens it for the GUI too.
"""

from __future__ import annotations

import functools
import os
import queue
import shutil
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

from fim import __version__, paths
from fim.engine import RunResult, deterministic_run_id, fim, replicate_summary
from fim.gui.runner import run_artifact_targets, write_run_artifacts
from fim.gui.store import LiveProgressStore, RunCancelledError
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

# How often the cancellation watcher thread checks `cancel_event` before
# translating it into `cancel_path`'s existence. Coarse on purpose: this
# bounds only the "user clicked Cancel" -> "cancel_path exists" latency,
# not per-generation cancellation detection, which each worker's own
# `LiveProgressStore` already checks immediately on its own next write.
_CANCEL_WATCH_INTERVAL_SECONDS: Final = 0.1

StartedMessage = tuple[Literal["started"], Path]
DoneMessage = tuple[Literal["done"], tuple[RunResult, ...]]
CancelledMessage = tuple[Literal["cancelled"], int, int]
ErrorMessage = tuple[Literal["error"], str]
BatchMessage = StartedMessage | DoneMessage | CancelledMessage | ErrorMessage


def default_max_workers() -> int:
    """Return the GUI's own default batch worker count.

    Matches `cli._cpu_count()` — the CLI's own default (non-`--sequential`)
    parallel batch worker count — so the GUI's default batch behavior is
    never silently weaker than the CLI's own. Still
    overridable per call via `start_batch_run`'s `max_workers` argument.
    """
    return os.cpu_count() or 1


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
    max_workers: int | None = None,
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
            `fim.engine.fim`'s own `max_workers=None` — it means "use
            this GUI's own default" (`default_max_workers()`, matching
            the CLI's own default), a deliberate divergence from the
            engine's convention worth stating explicitly rather than
            leaving implicit, since batch execution is parallel by
            default here.

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
    )
    thread.start()
    return thread


def _batch_worker(
    params: SimulationParams,
    run_id: str,
    output_directory: Path,
    message_queue: queue.Queue[BatchMessage],
    cancel_event: threading.Event,
    max_workers: int,
) -> None:
    """Run one replicate batch in parallel and post its outcome to `message_queue`.

    Every write happens inside `fim.paths.atomic_directory(output_directory)`,
    exactly as `fim.gui.runner._run_worker` does for a scalar run: a
    `RunCancelledError` from any replicate, or any member of
    `_EXPECTED_ENGINE_ERRORS`, propagates out of the `with` block and
    `atomic_directory` discards the whole temporary tree — no
    partial-batch save point exists to preserve, matching "Cancel batch"
    stopping the batch, not one replicate.
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

    started_at = _format_timestamp(_utc_now())
    try:
        with paths.atomic_directory(output_directory) as working_directory:
            # `working_directory` is `atomic_directory`'s own hidden
            # temporary sibling of `output_directory` (a random `mkdtemp`
            # suffix, not derivable from `output_directory` alone) —
            # posted here, first, so a parent-side poller
            # (`doc/fim-gui-design.md` §7.2) knows where each replicate's
            # `.progress` sidecar and `trajectory.jsonl` actually live
            # while the batch is still running, not only once it is
            # published at `output_directory` — an event no reader
            # outside this worker could otherwise ever observe.
            message_queue.put(("started", working_directory))
            results = fim(
                params.N,
                params.m,
                params.mu,
                params.d,
                params=params,
                run_id=run_id,
                max_workers=max_workers,
                store_factory=functools.partial(
                    _replicate_store_factory, working_directory, run_id, cancel_path
                ),
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
        # Whichever replicate's cancellation surfaced first — under real
        # parallelism, several replicates can be mid-write when
        # `cancel_path` appears, and this reports only the one whose
        # `RunCancelledError` this call happened to observe first, not
        # necessarily the only one in flight. `ProcessPoolExecutor`'s own
        # context manager still waits for every other in-flight worker in
        # the same wave to finish (each raising, or completing, on its
        # own) before this exception fully propagates out of `fim(...)`.
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
            cancel_path.touch()
            return


def _replicate_store_factory(
    working_directory: Path,
    batch_run_id: str,
    cancel_path: Path,
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
        JSONLTrajectoryStore(directory / "trajectory.jsonl"),
        progress_path=directory / ".progress",
        cancel_path=cancel_path,
    )


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


def _prune_orphan_replicate_directories(
    working_directory: Path,
    batch_run_id: str,
    published_run_ids: frozenset[str],
) -> None:
    """Remove any replicate directory not among the batch's published results.

    Direct parallel of `cli._prune_orphan_replicate_directories`
    (`src/fim/cli.py:355`) — not a shared
    import, per this module's established front-end-boundary convention.
    Necessary now that this module calls `fim(..., max_workers=N)`: under
    real parallelism, `fim.engine._run_batch_parallel` submits a whole
    worker wave and applies an adaptive `replicate_tolerance` stop only
    afterward, in ascending replicate order, so a worker beyond the
    replicate that triggered the stop can still run to completion — its
    `store_factory` call has already created its `replicate-NNN/`
    directory and streamed a full `trajectory.jsonl` into it — even
    though its result never appears in the tuple `fim` returns. Without
    this pass, `fim.paths.atomic_directory` would publish that orphan
    directory verbatim: complete, present on disk, and absent from both
    `summary.json` and `manifest.json`. The Tk-era sequential-only runner
    could never hit this (sequential execution never overshoots the
    adaptive stop), which is why it never needed this pass at all.

    Args:
        working_directory: The batch's hidden temporary build directory.
        batch_run_id: The batch's own run ID.
        published_run_ids: Every replicate run ID actually returned by
            `fim` — the set that will appear in `manifest.json`.
    """
    for entry in sorted(working_directory.glob("replicate-*")):
        if not entry.is_dir():
            continue
        replicate_run_id = f"{batch_run_id}-r{entry.name.removeprefix('replicate-')}"
        if replicate_run_id not in published_run_ids:
            shutil.rmtree(entry)


def _write_batch_artifacts(
    results: tuple[RunResult, ...],
    working_directory: Path,
    run_id: str,
    params: SimulationParams,
    started_at: str,
    ended_at: str,
) -> None:
    """Write every replicate's artifacts, then the batch-level summary and manifest.

    Mirrors `cli._command_run_batch`'s own default (parallel) artifact-
    writing pass: prune any orphan replicate directory first
    (`_prune_orphan_replicate_directories`), then each published
    replicate's own four-file scalar-run contract
    (`fim.gui.runner.write_run_artifacts`, reused rather than duplicated
    — both modules live in the same `fim.gui` package), removing that
    replicate's now-superfluous `.progress` sidecar as its artifacts are
    written: the published `results/` tree stays exactly
    the CLI's own four-file-per-replicate contract, with no GUI-only
    file left behind once a batch completes successfully — a cancelled
    batch needs no such cleanup, since `atomic_directory` discards the
    whole temporary tree instead). Then `summary.json`
    (`fim.engine.replicate_summary`), then — last, only once every
    sibling artifact is flushed — the batch-level `manifest.json`,
    augmented with each replicate's own manifest digest plus
    `summary.json`'s own.
    """
    published_run_ids = frozenset(result.run_id for result in results)
    _prune_orphan_replicate_directories(working_directory, run_id, published_run_ids)
    artifact_digests: dict[str, ArtifactDigest] = {}
    for result in results:
        directory = replicate_output_directory(working_directory, run_id, result.run_id)
        write_run_artifacts(result, run_artifact_targets(directory))
        (directory / ".progress").unlink(missing_ok=True)
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
