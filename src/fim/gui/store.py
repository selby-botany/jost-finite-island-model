"""Progress reporting and cancellation for a background run (design §3.4).

`fim.persistence.store.TrajectoryStore` is a `Protocol` (structural
typing, not an ABC), and `fim.engine._run_one`'s generation loop already
calls `store.write_generation(...)` unconditionally, every generation,
with no `try`/`except` around it — a clean, pre-existing extension point
`GuiProgressStore`/`LiveProgressStore` decorate rather than a change to
`fim.engine` itself.

Named `RunCancelledError`, not the design doc's illustrative
`RunCancelled` — ruff's `N818` (exception names end in `Error`) is part
of this project's lint gate; the design's code block is a decision
sketch, not a literal source requirement (§4's own "wireframes ... not
final visuals" framing applies here too).

Two decorators live here, for two execution shapes (design
`20260821-claude-sonnet-5-graphical-interface.md` §0.5, §3.4):

- `GuiProgressStore` — a scalar run, one in-process background thread.
  Holds a `threading.Event` and a callback closure; both are real
  Python objects the calling thread can read/write directly, because
  nothing here ever crosses a process boundary.
- `LiveProgressStore` — a batch replicate, running inside its own
  `ProcessPoolExecutor` worker process. A `threading.Event` and a
  closure cannot be pickled across that boundary, so this decorator
  holds only plain, picklable `Path`s instead: it *writes* a small
  progress sidecar file after each generation and *checks* a shared
  cancellation file before each write, rather than calling back into
  the parent process directly (which nothing here could safely do).
  The parent discovers progress by polling the sidecar, and requests
  cancellation by creating the cancellation file — both plain
  filesystem operations, needing no cross-process synchronization
  primitive at all.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fim.model.locus import LocusSpec
from fim.model.state import ModelState
from fim.persistence.store import TrajectoryRow, TrajectoryStore
from fim.reanalyze import group_rows_by_generation


class RunCancelledError(Exception):
    """Raised from `write_generation` to unwind an in-progress run.

    Args:
        run_id: The cancelled run's identifier.
        generation: The generation `write_generation` was about to write
            when cancellation was observed — one generation past the
            last one actually persisted.
    """

    def __init__(self, run_id: str, generation: int) -> None:
        super().__init__(run_id, generation)
        self.run_id = run_id
        self.generation = generation


class GuiProgressStore:
    """Decorate a `TrajectoryStore` with progress reporting and cancellation.

    Structurally satisfies `TrajectoryStore` (a `Protocol`), so it drops
    into `fim.engine.fim(..., store=...)` exactly where the real
    `JSONLTrajectoryStore` would — the run loop cannot tell the
    difference.
    """

    def __init__(
        self,
        inner: TrajectoryStore,
        *,
        on_generation: Callable[[int, list[Mapping[str, Any]]], None],
        cancel_event: threading.Event,
    ) -> None:
        """Wrap `inner`, reporting each write and honoring `cancel_event`.

        Args:
            inner: The real store every non-cancelled write delegates to.
            on_generation: Called with the generation number and that
                generation's own rows (design §0.5: the caller's own
                live-scatter push needs the actual frequency data, not
                just a bare count — re-reading it back from `inner`
                would need a path this decorator has no reason to know)
                after each successful delegated write — never before,
                and never for a write that raised `RunCancelledError`
                instead.
            cancel_event: Set by the UI's Cancel button; checked before
                every write.
        """
        self._inner = inner
        self._on_generation = on_generation
        self._cancel_event = cancel_event

    def write_generation(
        self,
        run_id: str,
        generation: int,
        rows: Iterable[Mapping[str, Any]],
    ) -> None:
        """Delegate one generation's write, or raise `RunCancelledError` instead.

        Checked before delegating, not after: a cancellation observed
        here never reaches the real store at all, so a cancelled run's
        `trajectory.jsonl` never gains the generation that triggered the
        cancellation — only the generations already written before it.

        `rows` is materialized into a plain `list` before delegating,
        not passed through as whatever `Iterable` the caller handed in:
        `self._inner.write_generation` (a real `JSONLTrajectoryStore`)
        already fully consumes it to write the file, and only a concrete,
        already-realized `list` is safe to hand to `on_generation`
        *afterward* — a one-shot iterator would come back empty on this
        second read. Every real caller in this codebase already passes a
        `list` (`ModelState.to_rows`'s own return type), so this costs
        nothing extra in practice; it exists so the decorator's own
        contract does not silently depend on that happening to be true.
        """
        if self._cancel_event.is_set():
            raise RunCancelledError(run_id, generation)
        materialized_rows = list(rows)
        self._inner.write_generation(run_id, generation, materialized_rows)
        self._on_generation(generation, materialized_rows)

    def read(self, run_id: str) -> Iterator[TrajectoryRow]:
        """Delegate straight to the wrapped store; nothing to decorate here."""
        return self._inner.read(run_id)


class LiveProgressStore:
    """Decorate a `TrajectoryStore` with file-mediated progress and cancellation.

    The cross-process counterpart to `GuiProgressStore` (see this
    module's docstring): safe to construct inside a `ProcessPoolExecutor`
    worker because it holds only plain `Path`s, never a `threading.Event`
    or a callback. Structurally satisfies `TrajectoryStore`, exactly like
    `GuiProgressStore` — the run loop cannot tell the difference.
    """

    def __init__(
        self,
        inner: TrajectoryStore,
        *,
        progress_path: Path,
        cancel_path: Path,
    ) -> None:
        """Wrap `inner`, recording progress in and honoring cancellation from disk.

        Args:
            inner: The real store every non-cancelled write delegates to.
            progress_path: Overwritten atomically after each successful
                delegated write with the generation just written and a
                wall-clock timestamp (`write_progress_sidecar`). Always
                inside this replicate's own output directory, so it
                travels with — and is removed alongside — that
                replicate's other artifacts once the batch is done with
                it.
            cancel_path: Checked for existence before every write; one
                file shared by every replicate in the same batch, so
                creating it once cancels all of them, matching "Cancel
                batch" stopping the batch, not one replicate (carried
                forward from design §4.0 #6).
        """
        self._inner = inner
        self._progress_path = progress_path
        self._cancel_path = cancel_path

    def write_generation(
        self,
        run_id: str,
        generation: int,
        rows: Iterable[Mapping[str, Any]],
    ) -> None:
        """Delegate one generation's write, or raise `RunCancelledError` instead.

        Checked before delegating, not after — the same ordering
        `GuiProgressStore.write_generation` uses, for the same reason: a
        cancellation observed here never reaches the real store at all.
        """
        if self._cancel_path.exists():
            raise RunCancelledError(run_id, generation)
        self._inner.write_generation(run_id, generation, rows)
        write_progress_sidecar(self._progress_path, generation)

    def read(self, run_id: str) -> Iterator[TrajectoryRow]:
        """Delegate straight to the wrapped store; nothing to decorate here."""
        return self._inner.read(run_id)


def write_progress_sidecar(progress_path: Path, generation: int) -> None:
    """Atomically write one replicate's `.progress` sidecar.

    Args:
        progress_path: The file to (over)write.
        generation: The generation just persisted.

    A plain JSON object, `{"generation": N, "written_at": "..."}` — the
    timestamp exists only so a test can prove two replicates' write
    windows actually overlap in real time (a structural fact, not a
    timing race — see this module's own test suite), not because the
    live-polling reader needs it. Written to a temp file in the same
    directory, then `os.replace`d into place: `os.replace` is atomic on
    every platform this project ships to, so a concurrent reader always
    sees either the previous complete sidecar or the new one, never a
    torn write — no lock needed on either side.
    """
    payload = json.dumps({"generation": generation, "written_at": _iso_now()})
    descriptor, temp_name = tempfile.mkstemp(
        dir=progress_path.parent, prefix=".progress-"
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temp_file:
            temp_file.write(payload)
        temp_path.replace(progress_path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def read_progress_sidecar(progress_path: Path) -> dict[str, Any] | None:
    """Read one replicate's `.progress` sidecar, or `None` if it has none yet.

    A replicate that has not yet completed its first generation has no
    sidecar at all — a normal, expected state for a just-started
    worker, not an error. `os.replace`'s atomicity (see
    `write_progress_sidecar`) means a sidecar that does exist is always
    a complete, valid write; no partial-read handling is needed here.
    """
    try:
        text = progress_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    result: dict[str, Any] = json.loads(text)
    return result


def read_live_state(
    trajectory_path: Path,
    run_id: str,
    generation: int,
    loci: Sequence[LocusSpec],
) -> ModelState | None:
    """Reconstruct an in-flight replicate's state at a sidecar-confirmed generation.

    Design §3.4, §7.6 — the live-batch counterpart to `fim.reanalyze.
    reanalyze_trajectory`: that function requires a completed run's own
    `manifest.json` (written only once, at the very end), so it cannot
    read a replicate that is still running. This reads the same
    `trajectory.jsonl` directly instead, with no manifest at all, and
    is meant to be called only with a `generation` already confirmed by
    that replicate's own `.progress` sidecar
    (`read_progress_sidecar`/`write_progress_sidecar`):
    `LiveProgressStore.write_generation` writes and flushes a
    generation's rows *before* updating the sidecar, so a sidecar-
    reported generation's own rows are always already safe to read —
    this function does not, by itself, guard against reading a
    generation still being written.

    Args:
        trajectory_path: The replicate's own `trajectory.jsonl`.
        run_id: The replicate's own run id (not the batch's).
        generation: The generation to reconstruct — normally
            `read_progress_sidecar(...)`'s own `"generation"` value.
        loci: The batch's own `params.loci`, in order.

    Returns:
        The reconstructed state, or `None` if `trajectory_path` does
        not exist yet, or `generation`'s own rows are not (or are no
        longer, or not yet fully) present — a transient filesystem-
        visibility race a live poller's own next call simply retries,
        never an error to raise partway through a still-running batch.
    """
    try:
        grouped = group_rows_by_generation(trajectory_path, run_id)
    except (FileNotFoundError, ValueError):
        # `FileNotFoundError`: the replicate has not created its own
        # directory/file yet. `ValueError`: a malformed *complete* line
        # (`JSONLTrajectoryStore.read`'s own distinct case from a
        # tolerated trailing partial one) — vanishingly unlikely against
        # a store this project's own code writes, but this function's
        # whole contract is "never interrupt a still-running batch's
        # live display over a read glitch," so it is treated the same
        # as any other transient failure here.
        return None
    rows = grouped.get(generation)
    if not rows:
        return None
    try:
        return ModelState.from_rows(rows, loci)
    except ValueError:
        return None


def _iso_now() -> str:
    """Return the current UTC time as an unambiguous ISO-8601 string.

    Matches `RunManifest`'s own timestamp format
    (`cli._format_timestamp`) — a direct parallel, not a shared import,
    per this package's established front-end-boundary convention.
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
