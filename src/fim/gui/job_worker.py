"""Run one resumable GUI job in a separate process."""

from __future__ import annotations

import argparse
import logging
import os
import queue
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

logger = logging.getLogger(__name__)


def _lower_process_priority() -> None:
    """Lower this process before importing the simulation engine or starting threads."""
    try:
        if sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            process = kernel32.GetCurrentProcess()
            if not kernel32.SetPriorityClass(process, 0x00004000):
                raise OSError(ctypes.get_last_error(), "SetPriorityClass failed")
        elif hasattr(os, "setpriority") and hasattr(os, "PRIO_PROCESS"):
            os.setpriority(os.PRIO_PROCESS, 0, 10)
    except OSError as error:
        logger.debug("could not lower GUI job priority: %s", error)


def _watch_lifeline(stop_requested: threading.Event) -> threading.Thread:
    """Pause the job at its next safe point when the parent closes stdin."""
    reader_ready = threading.Event()

    def watch() -> None:
        """Read until the parent closes the pipe, then request a pause."""
        reader_ready.set()
        try:
            while os.read(sys.stdin.fileno(), 4096):
                pass
        except OSError as error:
            logger.debug("GUI job lifeline ended with a read error: %s", error)
        finally:
            stop_requested.set()

    reader = threading.Thread(target=watch, name="fim-gui-job-lifeline", daemon=True)
    reader.start()
    reader_ready.wait()
    return reader


def main(argv: Sequence[str] | None = None) -> int:
    """Execute one Run object until it completes, pauses, or is cancelled."""
    _lower_process_priority()

    parser = argparse.ArgumentParser(prog="fim --gui-job", add_help=False)
    parser.add_argument("object_id")
    parser.add_argument("--results-directory", type=Path)
    parser.add_argument("--pairwise-max-demes", type=int)
    parser.add_argument("--view-level", choices=("full", "summary"), default="full")
    arguments = parser.parse_args(
        sys.argv[1:] if argv is None else list(argv)
    )
    if arguments.pairwise_max_demes is not None and arguments.pairwise_max_demes < 1:
        parser.error("--pairwise-max-demes must be at least 1")

    from fim.config.defaults import DEFAULT_PAIRWISE_MAX_DEMES  # noqa: PLC0415
    from fim.gui.object_runner import start_object_run  # noqa: PLC0415
    from fim.persistence.run_lifecycle import RunState  # noqa: PLC0415
    from fim.persistence.run_objects import read_run_object  # noqa: PLC0415

    stop_requested = threading.Event()
    _watch_lifeline(stop_requested)
    messages: queue.Queue[object] = queue.Queue()
    worker = start_object_run(
        arguments.object_id,
        messages,
        results=arguments.results_directory,
        pairwise_max_demes=(
            arguments.pairwise_max_demes
            if arguments.pairwise_max_demes is not None
            else DEFAULT_PAIRWISE_MAX_DEMES
        ),
        view_level=lambda: arguments.view_level,
        stop_requested=stop_requested.is_set,
    )
    while worker.is_alive():
        try:
            messages.get(timeout=0.1)
        except queue.Empty:
            continue
    worker.join()
    while not messages.empty():
        messages.get_nowait()

    run_object = read_run_object(
        arguments.object_id, results=arguments.results_directory
    )
    if run_object.record.state in {
        RunState.COMPLETED,
        RunState.PAUSED,
        RunState.CANCELLED,
    }:
        return 0
    logger.error(
        "GUI job %s exited in state %s",
        arguments.object_id,
        run_object.record.state.value,
    )
    return 1
