"""The trajectory store backed by the binary log.

`BinaryLogStore` is what a run uses to keep its trajectory: one compact,
checksummed log file per run (`fim.persistence.tlog`), written by a
background thread, in place of one JSON line per allele frequency. It
implements the same protocols as the JSON Lines store
(`fim.persistence.store.TrajectoryStore`, the companion store of an
equilibrium-split run, closing) and also `FrameStore`: the engine hands it
whole generations as `TrajectoryFrame`s, which is what makes it fast.

A store holds exactly one run. The log's header names the run, the demes'
gene copies and the loci, so the first frame (or `begin_run`) fixes them; a
second run id is refused with an explanation (a batch uses one store per
replicate, through a store factory). `read` yields the run's rows exactly as
the JSON Lines store would, so every reader of rows keeps working; the
canonical `trajectory.tlog` is something to *export*
(`fim.persistence.tlog_export`).
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path
from types import TracebackType
from typing import Any, Final

from fim.persistence import tlog
from fim.persistence.frame import (
    FrameLayout,
    TrajectoryFrame,
    frame_to_rows,
    rows_to_frame,
)
from fim.persistence.jsonl_store import JSONLTrajectoryStore
from fim.persistence.store import TrajectoryRow, TrajectoryStore

logger = logging.getLogger(__name__)

TRAJECTORY_LOG_FILENAME: Final = "trajectory.tlog"
"""The log of a run's own trajectory, in the run's directory."""

EQUILIBRIUM_LOG_FILENAME: Final = "equilibrium_trajectory.tlog"
"""The log of an equilibrium-split run's ancestral phase, beside the main one."""


class BinaryLogStore:
    """Append and read one run's trajectory through a binary log file.

    Args:
        path: The log file; it must not exist when the first generation is
            written (its parent directory is created then).
        background: Whether a writer thread checksums, writes and syncs, so
            the producing thread only encodes.
        block_generations: Generations per block, at most.
        block_seconds: Seconds an open block may wait before being written, or
            `None` (the default) to write on count and size only; see
            `tlog.DEFAULT_BLOCK_SECONDS` for why that is the default.
        sync: Durability policy (`tlog.SyncMode`); `"auto"` uses
            `F_FULLFSYNC` on macOS and `fsync` elsewhere.
        sync_seconds: Seconds between group-commit syncs.
        buffer_bytes: Size of a block buffer.
        key_every: Generations between keyframes in sparse mode.
        mode: `"sparse"` (the default: a keyframe every `key_every`
            generations and, between them, only the pairs that changed) or
            `"dense"` (every generation in full).
        queue_depth: Sealed blocks that may wait for the writer thread.
        clock: Monotonic clock deciding when to seal and sync (tests inject
            one); no time enters any byte of the log.
        fault: A `tlog.FaultHook` for tests.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        background: bool = True,
        block_generations: int = tlog.DEFAULT_BLOCK_GENERATIONS,
        block_seconds: float | None = tlog.DEFAULT_BLOCK_SECONDS,
        sync: tlog.SyncMode = "auto",
        sync_seconds: float = tlog.DEFAULT_SYNC_SECONDS,
        buffer_bytes: int = tlog.DEFAULT_BUFFER_BYTES,
        key_every: int = tlog.DEFAULT_KEY_EVERY,
        mode: tlog.LogMode = "sparse",
        queue_depth: int = tlog.DEFAULT_QUEUE_DEPTH,
        clock: Callable[[], float] = time.monotonic,
        fault: tlog.FaultHook | None = None,
    ) -> None:
        """Bind the store to one log file; nothing is created until a write."""
        self.path = Path(path)
        self._options: dict[str, Any] = {
            "background": background,
            "block_generations": block_generations,
            "block_seconds": block_seconds,
            "sync": sync,
            "sync_seconds": sync_seconds,
            "buffer_bytes": buffer_bytes,
            "key_every": key_every,
            "mode": mode,
            "queue_depth": queue_depth,
            "clock": clock,
            "fault": fault,
        }
        self._lock = threading.RLock()
        self._run_id: str | None = None
        self._layout: FrameLayout | None = None
        self._writer: tlog.LogWriter | None = None
        self._finished = False
        self._equilibrium: BinaryLogStore | None = None

    def __enter__(self) -> BinaryLogStore:
        """Return this store, for use as a context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the store when the `with` block ends."""
        self.close()

    def __del__(self) -> None:
        """Release a writer its owner forgot to close, without a warning.

        A safety net only: it keeps a forgotten `close` from leaving a
        thread and a file descriptor behind. Whatever was committed stays
        committed either way.
        """
        writer = getattr(self, "_writer", None)
        if writer is not None:
            with contextlib.suppress(Exception):
                writer.close()

    def __getstate__(self) -> dict[str, Any]:
        """Close the log, then drop everything that cannot be pickled.

        `RunResult.store` crosses a process boundary under
        `LinealBackend`'s `max_workers` path. A writer thread and a lock mean
        nothing in another process, so the log is closed first (everything
        submitted reaches the file) and the copy that arrives holds only the
        path and the options; it reads the finished file.
        """
        self.close()
        state = self.__dict__.copy()
        for name in ("_lock", "_writer"):
            del state[name]
        options = dict(state["_options"])
        # An injected clock or fault hook is a test double bound to this process.
        options["clock"] = time.monotonic
        options["fault"] = None
        state["_options"] = options
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        """Restore the plain attributes and rebuild a lock; no writer is open."""
        self.__dict__.update(state)
        self._lock = threading.RLock()
        self._writer = None

    def begin_run(self, run_id: str, layout: FrameLayout) -> None:
        """Fix the run this log holds and its layout; repeating it is harmless.

        Raises:
            ValueError: If the store holds another run or another layout.
        """
        with self._lock:
            self._bind(run_id)
            if self._layout is not None and self._layout != layout:
                raise ValueError(
                    f"run {run_id!r} was already begun with another layout"
                )
            self._layout = layout

    def wants_frames(self, run_id: str) -> bool:
        """Return `True`: frames are what this store is fastest with."""
        del run_id
        return True

    def write_frame(self, run_id: str, frame: TrajectoryFrame) -> None:
        """Encode one generation into the log.

        Raises:
            ValueError: If `begin_run` was not called, the run differs, or the
                generation does not follow the last one.
            RuntimeError: If the store was closed.
        """
        with self._lock:
            self._bind(run_id)
            self._ensure_writer().submit(frame)

    def write_generation(
        self,
        run_id: str,
        generation: int,
        rows: Iterable[Mapping[str, Any]],
        *,
        validate: bool = True,
    ) -> None:
        """Encode one generation given as rows.

        The rows become a frame (in pair order). Without a prior `begin_run`
        the layout is inferred from these rows, with unknown deme sizes, so
        every frequency is stored as a raw float; telling the store the real
        sizes (the engine does) is what makes the log compact.

        Raises:
            ValueError: If the rows are malformed, empty, or of another run
                or generation.
        """
        with self._lock:
            self._bind(run_id)
            materialized = list(rows)
            if self._layout is None:
                self._layout = FrameLayout.infer(materialized)
            frame = rows_to_frame(
                materialized,
                self._layout,
                run_id=run_id,
                generation=generation,
                validate=validate,
            )
            self._ensure_writer().submit(frame)

    def read(self, run_id: str) -> Iterator[TrajectoryRow]:
        """Yield the rows of `run_id` that are committed to the log, oldest first.

        Everything written so far is committed first (a flush barrier), so a
        reader sees every generation already submitted. A run other than the
        one in the log has no rows here.

        Raises:
            FileNotFoundError: If there is no log file.
        """
        self.flush()
        if not self.path.is_file():
            raise FileNotFoundError(f"trajectory does not exist: {self.path}")
        logger.debug("reading trajectory log %s for run %s", self.path, run_id)

        def iterate() -> Iterator[TrajectoryRow]:
            """Stream the committed frames of the log as rows."""
            scan = tlog.recover(self.path, truncate=False)
            if scan.header.run_id != run_id:
                return
            layout = scan.header.layout
            for frame in tlog.iter_frames(self.path, scan=scan):
                yield from frame_to_rows(frame, layout, run_id, validate=False)

        return iterate()

    def frames(self) -> Iterator[TrajectoryFrame]:
        """Yield every committed generation as a frame, oldest first.

        Raises:
            FileNotFoundError: If there is no log file.
        """
        self.flush()
        if not self.path.is_file():
            raise FileNotFoundError(f"trajectory does not exist: {self.path}")
        return tlog.iter_frames(self.path)

    def discard(self, run_id: str) -> None:
        """Remove the log if it holds `run_id`; a no-op otherwise.

        Closes the writer first (an open file blocks removal on Windows).
        """
        with self._lock:
            holds = self._run_id == run_id
            self._close_writer(raise_errors=False)
            if not holds and self.path.is_file():
                try:
                    holds = (
                        tlog.recover(self.path, truncate=False).header.run_id == run_id
                    )
                except tlog.TlogError:
                    holds = False
            if holds and self.path.is_file():
                self.path.unlink()
            if holds:
                self._run_id = None
                self._layout = None
                self._finished = False
        if holds:
            logger.debug("discarded run %s from %s", run_id, self.path)

    def flush(self, *, sync: bool = False) -> None:
        """Commit everything written so far (a barrier); optionally make it durable.

        Args:
            sync: Also sync the file now (a checkpoint or a pause).
        """
        with self._lock:
            writer = self._writer
            if writer is not None:
                writer.flush(sync=sync)

    def snapshot(self) -> tuple[int, int, int]:
        """Return `(committed generation, committed bytes, chain checksum)`.

        `(-1, 0, 0)` before anything is written.
        """
        with self._lock:
            writer = self._writer
            return writer.snapshot() if writer is not None else (-1, 0, 0)

    def is_open(self) -> bool:
        """Whether this log's writer is currently open (for tests and diagnostics)."""
        return self._writer is not None

    def close(self) -> None:
        """Commit and close the log, and its ancestral-phase companion if any.

        Idempotent. A closed store is finished: writing again raises.
        """
        with self._lock:
            self._close_writer(raise_errors=True)
            equilibrium = self._equilibrium
        if equilibrium is not None:
            equilibrium.close()

    def equilibrium_store(self, run_id: str) -> BinaryLogStore:
        """Return the store of this run's ancestral phase, beside this file.

        `EQUILIBRIUM_LOG_FILENAME` in this file's directory, with the same
        options, so it is staged and published with the run's other
        artifacts. One instance per store.
        """
        del run_id
        with self._lock:
            if self._equilibrium is None:
                self._equilibrium = BinaryLogStore(
                    self.path.with_name(EQUILIBRIUM_LOG_FILENAME),
                    **self._options,
                )
            return self._equilibrium

    def _bind(self, run_id: str) -> None:
        """Fix the run on first use and refuse a different one afterwards."""
        if self._run_id is None:
            self._run_id = run_id
        elif self._run_id != run_id:
            raise ValueError(
                f"{self.path.name} holds run {self._run_id!r}, cannot also hold "
                f"{run_id!r}; give every replicate its own store (a store factory)"
            )

    def _ensure_writer(self) -> tlog.LogWriter:
        """Return the writer, creating the file on first use."""
        if self._finished:
            raise RuntimeError("the trajectory log is closed")
        if self._writer is not None:
            return self._writer
        if self._layout is None or self._run_id is None:
            raise ValueError("begin_run was not called, so the log has no layout")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._writer = tlog.LogWriter(
            self.path, self._run_id, self._layout, **self._options
        )
        return self._writer

    def _close_writer(self, *, raise_errors: bool) -> None:
        """Close the writer, marking the store finished.

        Args:
            raise_errors: Whether a failure the writer thread stored is raised.
        """
        writer, self._writer = self._writer, None
        if writer is None:
            return
        self._finished = True
        try:
            writer.close()
        except BaseException:
            if raise_errors:
                raise
            logger.warning("closing %s failed", self.path, exc_info=True)

    def exists(self) -> bool:
        """Whether the log file exists on disk."""
        return self.path.is_file()


def open_trajectory(path: Path | str) -> TrajectoryStore:
    """Open a trajectory file for reading, whichever form it is in.

    A `.tlog` file is a binary log (a run's own trajectory); anything else
    is read as JSON Lines (an export, or a file from elsewhere). Both give
    the same rows through `read`.

    Args:
        path: The trajectory file.

    Returns:
        A store whose `read(run_id)` yields the file's rows.
    """
    if Path(path).suffix == ".tlog":
        return BinaryLogStore(path)
    return JSONLTrajectoryStore(path)
