"""Human-readable incremental JSON Lines trajectory storage.

"JSON Lines" (the ``.jsonl`` extension) is a simple file format where
each line of the file is its own complete, independent JSON object —
unlike a single big JSON array, a new line can be appended to the end
of the file at any time without rewriting anything already there, and
a reader can process the file one line at a time without first loading
the whole thing into memory. That is exactly what a running simulation
needs: `write_generation`, below, appends one generation's own rows to
the file the moment that generation finishes, so the trajectory
survives on disk even if the run is later interrupted, and a very long
run's trajectory file never needs to be held entirely in memory at
once, either to write it or to read it back.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from types import TracebackType
from typing import Any, Final, TextIO, cast

from fim.persistence.store import TrajectoryRow, normalize_row

logger = logging.getLogger(__name__)

EQUILIBRIUM_TRAJECTORY_FILENAME: Final = "equilibrium_trajectory.jsonl"
"""The ancestral-phase trajectory of an equilibrium-split run.

Written beside the run's own `trajectory.jsonl` (`JSONLTrajectoryStore.
equilibrium_store`), in the same row schema, numbered by the ancestral
phase's own generation counter.
"""


class JSONLTrajectoryStore:
    """Append and read validated trajectory rows in JSON Lines format.

    This is the real, file-backed implementation of the
    `fim.persistence.store.TrajectoryStore` protocol — the one actually
    used by `fim.engine` for a real run (as opposed to
    `fim.persistence.store.InMemoryTrajectoryStore`, a lighter-weight
    stand-in used by library calls and unit tests that never need a
    file on disk at all).

    The store keeps one append handle open between generations, opened
    on the first write. Re-opening a just-written file costs several
    milliseconds on some filesystems — more than encoding a whole
    generation of a small model — so `write_generation` writes through
    the kept handle and still flushes once per generation. `close`
    (also run by leaving a `with` block) releases the handle. A closed
    store is not finished: the next write quietly re-opens the file in
    append mode, so closing is always safe and never loses data. Every
    owner of a store that writes to a directory later renamed into place
    (`fim.paths.atomic_directory`) must close it first, because an open
    handle blocks a rename on Windows.
    """

    def __init__(self, path: Path | str) -> None:
        """Bind the store to one trajectory file.

        Args:
            path: JSON Lines file path. Its parent is created on first write.
        """
        self.path = Path(path)
        # One lock per store instance, held for the whole open-write-flush
        # block in `write_generation` — without it, two threads writing
        # concurrently (`fim.engine.GenerationalBackend`'s own
        # `ThreadedAdvancer`) could interleave their own `handle.write()`
        # calls on the same underlying file descriptor, garbling lines.
        self._lock = threading.Lock()
        self._equilibrium: JSONLTrajectoryStore | None = None
        # The kept-open append handle; `None` until the first write and
        # again after `close`. Guarded by `_lock`.
        self._handle: TextIO | None = None

    def __enter__(self) -> JSONLTrajectoryStore:
        """Return this store, for use as a context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the store's file handles when the `with` block ends."""
        self.close()

    def __del__(self) -> None:
        """Release a handle its owner forgot to close, without a warning.

        A safety net only: every flushed generation is already on disk,
        so nothing is lost either way. It keeps a forgotten `close` from
        surfacing as a `ResourceWarning` from the file object itself.
        """
        handle = getattr(self, "_handle", None)
        if handle is not None:
            with contextlib.suppress(OSError):
                handle.close()

    def __getstate__(self) -> dict[str, Any]:
        """Drop `_lock` and the open handle before pickling.

        `RunResult.store` crosses a real process boundary under
        `fim.engine.LinealBackend`'s own `max_workers` path
        (`ProcessPoolExecutor` pickles a worker's returned `RunResult`,
        store included, to send it back to the parent process) — a
        `threading.Lock` cannot be pickled at all, and would not mean
        anything in a different process even if it could be.
        `__setstate__` rebuilds a fresh lock on the other side instead.
        """
        state = self.__dict__.copy()
        del state["_lock"]
        # An open file handle cannot be pickled and would mean nothing in
        # another process. Every generation is flushed as it is written,
        # so dropping it loses nothing; the unpickled copy re-opens the
        # file in append mode on its first write.
        state["_handle"] = None
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        """Restore everything but `_lock`, then rebuild a fresh one."""
        self.__dict__.update(state)
        self._lock = threading.Lock()

    def write_generation(
        self,
        run_id: str,
        generation: int,
        rows: Iterable[Mapping[str, Any]],
        *,
        validate: bool = True,
    ) -> None:
        """Append and flush all rows for one generation.

        Every row is validated (via `fim.persistence.store.
        normalize_row`) before anything is written by default, so a
        malformed row is rejected up front rather than partially
        written to disk — `validate=False` skips that for a caller
        that already vouches for its own rows (`fim.persistence.store`'s
        own top docstring has the full reasoning and which callers this
        applies to; `json.dumps`'s own ``allow_nan=False`` below still
        catches a non-finite frequency either way, as a last resort,
        not a substitute for real validation on an untrusted row).
        ``handle.flush()`` hands this generation's bytes from Python's
        own internal buffer to the operating system right away, rather
        than leaving them sitting in memory until the file is
        eventually closed — this generation is written to disk as soon
        as this call returns, instead of remaining vulnerable to being
        lost entirely if the process is interrupted or crashes before
        the file handle would otherwise have been closed. The handle
        itself stays open for the next generation (see this class's own
        docstring); a reader of the file sees every generation already
        flushed.

        Every line is encoded before the first byte is written, so a
        row that cannot be encoded (a non-finite frequency) leaves the
        file untouched instead of holding a partial generation.
        """
        if validate:
            normalized_rows = [
                normalize_row(row, run_id=run_id, generation=generation) for row in rows
            ]
        else:
            normalized_rows = [cast("TrajectoryRow", dict(row)) for row in rows]
        if not normalized_rows:
            raise ValueError("a generation must contain at least one row")
        payload = "".join(
            json.dumps(
                row,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
            for row in normalized_rows
        )
        with self._lock:
            handle = self._open_handle()
            try:
                handle.write(payload)
                handle.flush()
            except BaseException:
                # A failed write may leave unwritten bytes buffered in the
                # handle; drop it so they cannot be appended later, out of
                # order, by a retry. The next write re-opens the file.
                self._close_handle()
                raise
        # Guarded like `fim.engine._run_one`'s own per-generation loop
        # (`doc/fim-logging-design.md` §9): this is called once per
        # generation for the life of a run, so the message is only
        # formatted when DEBUG is actually enabled.
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "wrote %d row(s) for %s generation %d to %s",
                len(normalized_rows),
                run_id,
                generation,
                self.path,
            )

    def close(self) -> None:
        """Release this store's open file handles; safe to call repeatedly.

        Closes the handle of this file and of its ancestral-phase
        companion (`equilibrium_store`), if either is open. The store is
        not unusable afterwards: the next `write_generation` re-opens
        the file in append mode, and `read`/`discard` never needed the
        handle. Every generation is flushed as it is written, so closing
        loses nothing; it exists so a directory can be renamed or removed
        (an open handle blocks both on Windows) and so no file descriptor
        outlives its run.
        """
        with self._lock:
            self._close_handle()
            equilibrium = self._equilibrium
        if equilibrium is not None:
            equilibrium.close()

    def is_open(self) -> bool:
        """Whether this file's own append handle is currently open.

        Reports this file only, not its ancestral-phase companion. For
        diagnostics and for tests that prove no run leaves a file open.
        """
        handle = self._handle
        return handle is not None and not handle.closed

    def _close_handle(self) -> None:
        """Close and forget the kept-open handle, if any; caller holds `_lock`."""
        handle, self._handle = self._handle, None
        if handle is not None:
            handle.close()

    def _open_handle(self) -> TextIO:
        """Return the append handle, opening it if needed; caller holds `_lock`.

        A kept handle is reused only while it still names the file at
        `path`: if something deleted the file meanwhile (a link count of
        zero), writing on would silently feed an unlinked file, so the
        handle is dropped and the file re-created, exactly as the
        open-per-generation writer this replaced would have done.
        """
        handle = self._handle
        if handle is not None:
            if not handle.closed and os.fstat(handle.fileno()).st_nlink > 0:
                return handle
            self._close_handle()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a", encoding="utf-8", newline="\n")
        self._handle = handle
        return handle

    def discard(self, run_id: str) -> None:
        """Rewrite this file without ``run_id``'s own rows; a no-op if there are none.

        See `fim.persistence.store.TrajectoryStore.discard`'s own
        docstring for why this exists at all. A plain, whole-file
        read-filter-rewrite under `_lock` — this store's own file can in
        principle hold more than one run's rows (`write_generation`/
        `read` both filter by `run_id` rather than assuming one file,
        one run), so discarding one run's own rows cannot simply be
        "delete the file." If nothing survives the filter, the file is
        removed entirely rather than left behind empty — matching this
        project's own "a published run directory is complete or absent,
        never empty" precedent (`_atomic_directory`, `fim.engine`)
        applied here to one file instead of one directory.

        A missing file, or a file that already has none of ``run_id``'s
        own rows, is a no-op either way — this is "make sure this run's
        data is gone," not "assert it was there first."
        """
        with self._lock:
            # The file is about to be rewritten or removed: release the
            # kept handle first (a removal is blocked by it on Windows,
            # and a rewrite would leave it pointing at stale content).
            # The next write re-opens the file.
            self._close_handle()
            if not self.path.is_file():
                return
            kept_lines: list[str] = []
            discarded_any = False
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    payload = json.loads(stripped)
                    if payload.get("run_id") == run_id:
                        discarded_any = True
                    else:
                        kept_lines.append(line if line.endswith("\n") else line + "\n")
            if not discarded_any:
                return
            if kept_lines:
                with self.path.open("w", encoding="utf-8", newline="\n") as handle:
                    handle.writelines(kept_lines)
            else:
                self.path.unlink()
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("discarded run %s from %s", run_id, self.path)

    def equilibrium_store(self, run_id: str) -> JSONLTrajectoryStore:
        """Return the store for this run's ancestral phase, beside this file.

        `EQUILIBRIUM_TRAJECTORY_FILENAME` in this file's own directory,
        so it is staged and published with the rest of the run's
        artifacts (`fim.paths.atomic_directory`). Like this file, it can
        hold several runs' rows, told apart by `run_id`
        (`fim.persistence.store.EquilibriumStoreProvider`). One instance
        per store, built on first use, so every writer of that file
        shares its one lock.
        """
        del run_id
        with self._lock:
            if self._equilibrium is None:
                self._equilibrium = JSONLTrajectoryStore(
                    self.path.with_name(EQUILIBRIUM_TRAJECTORY_FILENAME)
                )
            return self._equilibrium

    def read(self, run_id: str) -> Iterator[TrajectoryRow]:
        """Yield complete rows matching ``run_id``, oldest first.

        A generator (built via the inner `iterate` function, below,
        rather than returning a plain list) so a large trajectory file
        is streamed one row at a time instead of being fully loaded
        into memory before the caller sees any of it.

        A final partial line from an interrupted append is ignored. Any malformed
        complete line is reported as corruption.

        This tolerance is a deliberate scope boundary, not a completeness
        guarantee: this method alone cannot tell an interrupted-append
        trailing partial line from a trajectory that is simply short a
        generation for some other reason, since it has no manifest to
        compare against. Detecting that a trajectory doesn't have as
        many generations as it claims to is a manifest-level guarantee —
        `fim.persistence.manifest.verify_trajectory_integrity`'s SHA-256
        digest check, and `fim.reanalyze.reanalyze_trajectory`'s own
        generation-count cross-check against `RunManifest.
        generation_count` — not one this store makes on its own.
        """
        if not self.path.is_file():
            raise FileNotFoundError(f"trajectory does not exist: {self.path}")
        logger.debug("reading trajectory %s for run %s", self.path, run_id)

        def iterate() -> Iterator[TrajectoryRow]:
            with self.path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError as error:
                        if not line.endswith("\n"):
                            return
                        raise ValueError(
                            f"invalid JSON on trajectory line {line_number}"
                        ) from error
                    if not isinstance(payload, dict):
                        raise ValueError(
                            f"trajectory line {line_number} is not an object"
                        )
                    row = normalize_row(payload)
                    if row["run_id"] == run_id:
                        yield row

        return iterate()
