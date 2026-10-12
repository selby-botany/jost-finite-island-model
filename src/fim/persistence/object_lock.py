"""Cross-process exclusive file locks for Run object bookkeeping.

A lock is an ordinary file locked with the operating system's advisory
lock (`fcntl.flock` on POSIX, `msvcrt.locking` on Windows). The operating
system releases it when the holding process exits for any reason, including
SIGKILL, so a crashed process never leaves an object locked.

Lock files live outside the objects they protect, so an object directory can
be renamed or deleted while its lock is held on every platform.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

if sys.platform == "win32":  # pragma: no cover - exercised on Windows CI
    import msvcrt

    def _lock(handle: IO[bytes], *, blocking: bool) -> bool:
        """Lock the first byte of `handle`; return whether it was acquired."""
        handle.seek(0)
        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        try:
            msvcrt.locking(handle.fileno(), mode, 1)
        except OSError:
            if blocking:
                raise
            return False
        return True

    def _unlock(handle: IO[bytes]) -> None:
        """Release the lock taken by `_lock`."""
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(handle: IO[bytes], *, blocking: bool) -> bool:
        """Lock `handle` exclusively; return whether it was acquired."""
        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(handle.fileno(), flags)
        except BlockingIOError:
            return False
        return True

    def _unlock(handle: IO[bytes]) -> None:
        """Release the lock taken by `_lock`."""
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class LockUnavailableError(Exception):
    """A non-blocking lock request found the lock already held."""


@contextmanager
def exclusive_lock(
    path: Path, *, blocking: bool = True, create: bool = True
) -> Iterator[None]:
    """Hold an exclusive cross-process lock on `path` for the block.

    Args:
        path: The lock file.
        blocking: Wait for the lock (default) or fail at once if held.
        create: Create the lock file, with its parent, if missing
            (default). Without it a missing file raises instead, so a
            lock its owner already deleted is never recreated and taken.

    Yields:
        Nothing; the lock is held until the block exits.

    Raises:
        LockUnavailableError: `blocking` is false and the lock is held.
        FileNotFoundError: `create` is false and the lock file is missing.
    """
    flags = os.O_RDWR
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        flags |= os.O_CREAT
    descriptor = os.open(path, flags, 0o666)
    with os.fdopen(descriptor, "r+b") as handle:
        if not _lock(handle, blocking=blocking):
            raise LockUnavailableError(f"lock is held: {path}")
        try:
            yield
        finally:
            _unlock(handle)


def is_lock_held(path: Path) -> bool:
    """Return whether another holder currently has the lock on `path`.

    A missing lock file means nobody holds it. The probe takes and at once
    releases the lock, so it never blocks.
    """
    if not path.exists():
        return False
    try:
        with exclusive_lock(path, blocking=False):
            return False
    except LockUnavailableError:
        return True
