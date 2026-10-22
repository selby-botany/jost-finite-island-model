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
from typing import Any, cast

from fim.execution_priority import Priority, apply_priority

logger = logging.getLogger(__name__)


class _ProcessMessageSink:
    """Adapt worker messages to objects safe for the process queue."""

    def __init__(self, connection: Any, snapshot_result: Any) -> None:
        """Keep the authenticated parent connection and result converter."""
        self._connection = connection
        self._snapshot_result = snapshot_result
        self._connected = True

    def put(self, message: Any) -> None:
        """Send a pickle-safe message unless the GUI has disconnected."""
        if not self._connected:
            return
        if message[0] == "done":
            result = message[1]
            if isinstance(result, tuple):
                result = tuple(self._snapshot_result(item) for item in result)
            else:
                result = self._snapshot_result(result)
            message = ("done", result)
        try:
            self._connection.send(message)
        except (BrokenPipeError, EOFError, OSError) as error:
            logger.debug("GUI job message channel closed: %s", error)
            self._connection.close()
            self._connected = False


def _apply_process_priority(priority: Priority) -> None:
    """Apply this child process's priority before importing the engine."""
    apply_priority(priority)


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
    parser = argparse.ArgumentParser(prog="fim --gui-job", add_help=False)
    parser.add_argument("object_id")
    parser.add_argument("--results-directory", type=Path)
    parser.add_argument("--pairwise-max-demes", type=int)
    parser.add_argument(
        "--priority", choices=("normal", "lower", "lowest"), default="lower"
    )
    parser.add_argument("--view-level", choices=("full", "summary"), default="full")
    arguments = parser.parse_args(sys.argv[1:] if argv is None else list(argv))
    if arguments.pairwise_max_demes is not None and arguments.pairwise_max_demes < 1:
        parser.error("--pairwise-max-demes must be at least 1")
    _apply_process_priority(cast("Priority", arguments.priority))

    from fim.config.defaults import DEFAULT_PAIRWISE_MAX_DEMES  # noqa: PLC0415
    from fim.gui.job_results import GuiRunResult  # noqa: PLC0415
    from fim.gui.jobs import connect_message_channel  # noqa: PLC0415
    from fim.gui.object_runner import start_object_run  # noqa: PLC0415
    from fim.persistence.run_lifecycle import RunState  # noqa: PLC0415
    from fim.persistence.run_objects import read_run_object  # noqa: PLC0415

    stop_requested = threading.Event()
    _watch_lifeline(stop_requested)
    connection = connect_message_channel() if "FIM_GUI_JOB_HOST" in os.environ else None
    messages: Any = (
        _ProcessMessageSink(connection, GuiRunResult.from_result)
        if connection is not None
        else queue.Queue()
    )
    worker = start_object_run(
        arguments.object_id,
        cast("queue.Queue[Any]", messages),
        results=arguments.results_directory,
        pairwise_max_demes=(
            arguments.pairwise_max_demes
            if arguments.pairwise_max_demes is not None
            else DEFAULT_PAIRWISE_MAX_DEMES
        ),
        view_level=lambda: arguments.view_level,
        stop_requested=stop_requested.is_set,
    )
    if connection is None:
        while worker.is_alive():
            try:
                messages.get(timeout=0.1)
            except queue.Empty:
                continue
        while not messages.empty():
            messages.get_nowait()
    worker.join()
    if connection is not None:
        connection.close()

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
