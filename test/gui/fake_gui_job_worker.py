"""Deterministic subprocess worker used by GUI job-supervision tests."""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path


def _wait_for_file(path: Path) -> None:
    """Wait until the test releases this worker through a marker file."""
    while not path.exists():
        time.sleep(0.01)


def main() -> int:
    """Run one controlled message, crash, or held-worker scenario."""
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("held", "progress", "crash"))
    parser.add_argument("object_id")
    parser.add_argument("--release")
    parser.add_argument("--summary-release")
    parser.add_argument("--finish-release")
    parser.add_argument("--results-directory", type=Path)
    parser.add_argument("--pairwise-max-demes", type=int, default=1000)
    arguments = parser.parse_args()

    from fim.gui.jobs import connect_message_channel  # noqa: PLC0415

    connection = connect_message_channel()
    if arguments.mode == "held":
        _wait_for_file(Path(arguments.release))
        connection.send(("cancelled", 0))
    elif arguments.mode == "progress":
        from fim.engine import deterministic_run_id  # noqa: PLC0415
        from fim.gui.object_bridge import prepare_start  # noqa: PLC0415
        from fim.gui.runner import progress_message  # noqa: PLC0415
        from fim.model.initial import generate_initial_state  # noqa: PLC0415

        run_object, params = prepare_start(
            arguments.object_id, results=arguments.results_directory
        )
        run_id = run_object.record.scientific_run_id or deterministic_run_id(params)
        message = progress_message(
            generate_initial_state(params),
            0,
            params,
            run_id,
            view_level="summary",
        )
        connection.send(message)
        _wait_for_file(Path(arguments.summary_release))
        connection.send(message)
        _wait_for_file(Path(arguments.finish_release))
        connection.send(("cancelled", 0))
    else:
        from fim.persistence.run_objects import read_run_object  # noqa: PLC0415
        from fim.persistence.run_ownership import start_run_object  # noqa: PLC0415

        run_object = read_run_object(
            arguments.object_id, results=arguments.results_directory
        )
        start_run_object(
            arguments.object_id,
            expected_revision=run_object.record.revision,
            results=arguments.results_directory,
        )
        os._exit(7)

    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
