"""The one function that runs a simulation: `simulate`."""

from __future__ import annotations

from pathlib import Path

from fim.engine import simulate_params
from fim.engine.identity import deterministic_run_id
from fim.engine.records import Clock
from fim.errors import ConfigurationError, UnsupportedError
from fim.persistence.saved_run import save_run
from fim.persistence.store import InMemoryTrajectoryStore
from fim.results import Result, result_from_runs
from fim.spec.codec import RunConfig, to_params
from fim.spec.execution import Execution, Recording
from fim.spec.run import RunSpec


def simulate(
    spec: RunSpec | RunConfig,
    *,
    execution: Execution | None = None,
    recording: Recording | None = None,
    clock: Clock | None = None,
) -> Result:
    """Run a simulation to convergence and return everything it produced.

    The run is the finite island model described by `spec`, repeated for each
    independently seeded replicate until the batch reaches its precision or
    its replicate cap. A run of one replicate is a `Result` of length one;
    `Result.only()` returns it.

    Args:
        spec: What to simulate. A `RunConfig` (from `load_config`) also
            carries its own `Execution` and `Recording`, which the keyword
            arguments below override.
        execution: How to run it. Never changes the numbers.
        recording: What to keep. With `directory` set, the run is published
            there (which must not exist yet) in the layout `fim run` writes,
            and `open_run` reads it back; otherwise it stays in memory.
        clock: Source of "now" for timestamps; a test seam.

    Returns:
        The `Result`.

    Raises:
        ConfigurationError: The specification is invalid, or worker
            processes were requested without a recording directory.
        UnsupportedError: Valid, but no available engine can run it (for
            example a backend that needs `numba`, without it installed).
    """
    if isinstance(spec, RunConfig):
        execution = execution or spec.execution
        recording = recording or spec.recording
        spec = spec.spec
    execution = execution or Execution()
    recording = recording or Recording()
    if execution.workers is not None and recording.directory is None:
        raise ConfigurationError(
            "execution.workers needs recording.directory: worker processes "
            "cannot share an in-memory store"
        )
    params = to_params(spec, execution, recording)
    run_id = deterministic_run_id(params)
    if recording.directory is not None:
        try:
            runs = save_run(
                params,
                Path(recording.directory),
                max_workers=execution.workers,
                clock=clock,
            )
        except ValueError as error:
            raise UnsupportedError(str(error)) from error
        return result_from_runs(runs, run_id=run_id, params=params, spec=spec)
    try:
        output = simulate_params(
            params=params,
            store=InMemoryTrajectoryStore(),
            run_id=run_id,
            clock=clock,
        )
    except ValueError as error:
        raise UnsupportedError(str(error)) from error
    return result_from_runs(output, run_id=run_id, params=params, spec=spec)
