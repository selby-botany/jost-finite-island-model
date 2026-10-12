"""Turn SIGTERM into an orderly stop for command-line runs.

Python ends at once on SIGTERM unless a handler is installed, so nothing
cleans up: a Run object stays `running` until reconciliation records it as
`interrupted`, and `fim run` leaves its hidden staging directory behind.

Two policies are offered, each for the duration of a `with` block:

- `sigterm_requests_stop` only sets a flag. The Run object worker polls it
  at safe points, publishes a checkpoint, and pauses. The handler must not
  take a lock: `flock` locks belong to an open file description, so taking
  the object lock from a handler while the interrupted code holds it would
  deadlock.
- `sigterm_raises_exit` raises `SystemExit(143)` in the main thread, so
  `fim.paths.atomic_directory` discards its staging directory.

Either handler restores the default action as soon as it runs, so a second
SIGTERM ends the process immediately. Outside the main thread, where Python
cannot install handlers, both blocks do nothing.
"""

from __future__ import annotations

import contextlib
import signal
import threading
from collections.abc import Callable, Iterator
from types import FrameType
from typing import Final, NoReturn

TERMINATED_EXIT_STATUS: Final = 128 + signal.SIGTERM
"""The exit status a shell reports for a process ended by SIGTERM."""

_Handler = Callable[[int, FrameType | None], object]


@contextlib.contextmanager
def sigterm_raises_exit() -> Iterator[None]:
    """Raise `SystemExit(143)` on the first SIGTERM within the block.

    Yields:
        Nothing.
    """

    def handle(signum: int, frame: FrameType | None) -> NoReturn:
        """Restore the default action, then unwind the main thread."""
        del frame
        signal.signal(signum, signal.SIG_DFL)
        raise SystemExit(TERMINATED_EXIT_STATUS)

    with _installed(handle):
        yield


@contextlib.contextmanager
def sigterm_requests_stop() -> Iterator[Callable[[], bool]]:
    """Record the first SIGTERM within the block as a stop request.

    Yields:
        A callable returning whether SIGTERM arrived; it never blocks.
    """
    received = False

    def handle(signum: int, frame: FrameType | None) -> None:
        """Record the request and restore the default action; nothing else."""
        nonlocal received
        del frame
        received = True
        signal.signal(signum, signal.SIG_DFL)

    def requested() -> bool:
        """Return whether SIGTERM arrived."""
        return received

    with _installed(handle):
        yield requested


@contextlib.contextmanager
def _installed(handler: _Handler) -> Iterator[None]:
    """Install `handler` for SIGTERM in the main thread, then restore."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.signal(signal.SIGTERM, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)
