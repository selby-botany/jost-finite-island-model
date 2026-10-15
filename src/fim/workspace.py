"""A results directory holding saved runs that can be paused and resumed.

`simulate` runs once, in memory or into a directory you name. A `Workspace`
is for work worth keeping: each run is a saved *Run object* with a lifecycle
(planned, running, paused, completed, ...), checkpointed as it goes, so it
survives a closed laptop or a crash and resumes to exactly the numbers an
uninterrupted run would have given.

    ws = fim.Workspace("results")
    run = ws.plan(spec)          # saved, not started
    result = run.start()         # a Result, or None if it was paused
    run.resume()                 # continue after a pause or a crash
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from fim.engine.records import RunResult
from fim.errors import ConfigurationError, StoreError
from fim.persistence.artifacts import write_run_artifacts
from fim.persistence.run_lifecycle import RunState
from fim.persistence.run_metadata import RunLabels
from fim.persistence.run_objects import (
    RunObject,
    RunObjectNotFoundError,
    create_run_object,
    delete_run_object,
    list_run_objects,
    read_run_object,
)
from fim.persistence.run_ownership import reconcile_run_object, request_pause
from fim.results import Result, result_from_runs
from fim.run_service import (
    Session,
    cancel_run_object,
    execute_run_object,
    load_params,
)
from fim.spec.codec import RunConfig, to_params
from fim.spec.execution import Execution, Recording
from fim.spec.run import RunSpec
from fim.stored import StoredRun, open_run

__all__ = ["RunHandle", "Workspace"]


@dataclass(frozen=True, slots=True)
class RunHandle:
    """One saved run in a workspace.

    The handle holds only the run's identity; every property and method reads
    the saved record, so it is always current and can be used from another
    process or after a restart.

    Args:
        workspace: The workspace that holds the run.
        object_id: The run's saved identity.
    """

    workspace: Workspace
    object_id: str

    def _read(self) -> RunObject:
        """Read the run's saved record."""
        return read_run_object(self.object_id, results=self.workspace.path)

    @property
    def state(self) -> RunState:
        """Return the run's lifecycle state."""
        return self._read().record.state

    @property
    def directory(self) -> Path:
        """Return the run's directory."""
        return self._read().directory

    def start(
        self,
        *,
        observer: Callable[[Session], None] | None = None,
        stop_requested: Callable[[], bool] | None = None,
    ) -> Result | None:
        """Run to completion, or until paused or cancelled.

        Starts a planned run and resumes a paused, interrupted, or failed one
        from its last checkpoint; the numbers are those an uninterrupted run
        gives.

        Args:
            observer: Called at each safe point with the live session.
            stop_requested: Polled at each safe point; once it returns true
                the run pauses there.

        Returns:
            The `Result` if the run completed in this call, else `None`
            (read `state` to see whether it paused or was cancelled; a run
            recovered as already complete is read with `open`).
        """
        collected: list[RunResult] = []

        def write(result: RunResult, directory: Path) -> None:
            collected.append(result)
            write_run_artifacts(result, directory)

        finished = execute_run_object(
            self.object_id,
            results=self.workspace.path,
            write_artifacts=write,
            observer=observer,
            stop_requested=stop_requested,
        )
        if finished.record.state is not RunState.COMPLETED or not collected:
            return None
        params = load_params(finished)
        return result_from_runs(
            collected,
            run_id=finished.record.scientific_run_id or finished.object_id,
            params=params,
        )

    resume = start

    def pause(self) -> None:
        """Ask the run to pause at its next safe point."""
        current = self._read()
        request_pause(
            self.object_id,
            expected_revision=current.record.revision,
            results=self.workspace.path,
        )

    def cancel(self) -> None:
        """Cancel the run; partial data is kept."""
        current = reconcile_run_object(self.object_id, results=self.workspace.path)
        cancel_run_object(
            self.object_id,
            expected_revision=current.record.revision,
            results=self.workspace.path,
        )

    def delete(self) -> None:
        """Delete the run, which must not be executing."""
        current = reconcile_run_object(self.object_id, results=self.workspace.path)
        delete_run_object(
            self.object_id,
            expected_revision=current.record.revision,
            results=self.workspace.path,
        )

    def open(self) -> StoredRun:
        """Open the completed run's files.

        Raises:
            StoreError: The run has not completed.
        """
        current = self._read()
        if current.record.state is not RunState.COMPLETED:
            raise StoreError(
                f"run {self.object_id} is {current.record.state.value}, not completed"
            )
        return open_run(current.directory)


class Workspace:
    """A results directory of saved, resumable runs.

    Args:
        path: The directory. It is created on first use, and always passed
            explicitly: a library never writes to the current directory
            unasked.
    """

    __slots__ = ("_path",)

    def __init__(self, path: Path | str) -> None:
        """Remember the directory."""
        self._path = Path(path)

    @property
    def path(self) -> Path:
        """Return the workspace directory."""
        return self._path

    def __repr__(self) -> str:
        """Return the workspace's path."""
        return f"Workspace({str(self._path)!r})"

    def plan(
        self,
        spec: RunSpec | RunConfig,
        *,
        execution: Execution | None = None,
        recording: Recording | None = None,
    ) -> RunHandle:
        """Save a run without starting it.

        Args:
            spec: What to simulate (a `RunConfig` also carries its own
                execution and recording, which the keywords override).
            execution: How to run it.
            recording: Trajectory retention; `directory` must be unset, since
                the workspace decides where a run lives.

        Returns:
            A handle to the planned run.

        Raises:
            ConfigurationError: The specification is invalid, or `directory`
                or `workers` was given.
        """
        if isinstance(spec, RunConfig):
            execution = execution or spec.execution
            recording = recording or spec.recording
            labels = spec.labels
            spec = spec.spec
        else:
            labels = RunLabels()
        execution = execution or Execution()
        recording = recording or Recording()
        if recording.directory is not None:
            raise ConfigurationError(
                "recording.directory must be unset: a workspace decides where "
                "its runs live"
            )
        if execution.workers is not None:
            raise ConfigurationError(
                "execution.workers does not apply to saved runs, which run in "
                "checkpointed sessions"
            )
        config = RunConfig(
            spec=spec, execution=execution, recording=recording, labels=labels
        )
        to_params(spec, execution, recording)  # validate before saving
        text = yaml.safe_dump(config.to_mapping(), sort_keys=False)
        created = create_run_object(text, results=self._path)
        return RunHandle(self, created.object_id)

    def run(
        self,
        spec: RunSpec | RunConfig,
        *,
        execution: Execution | None = None,
        recording: Recording | None = None,
    ) -> Result:
        """Save a run and run it to completion.

        Args:
            spec: What to simulate.
            execution: How to run it.
            recording: Trajectory retention.

        Returns:
            The `Result`.
        """
        handle = self.plan(spec, execution=execution, recording=recording)
        result = handle.start()
        if result is None:
            raise StoreError(f"run {handle.object_id} did not complete")
        return result

    def handle(self, object_id: str) -> RunHandle:
        """Return the handle of a saved run.

        Raises:
            StoreError: There is no such run.
        """
        try:
            read_run_object(object_id, results=self._path)
        except RunObjectNotFoundError as error:
            raise StoreError(f"no saved run {object_id!r} in {self._path}") from error
        return RunHandle(self, object_id)

    def handles(self) -> tuple[RunHandle, ...]:
        """Return a handle to every readable saved run."""
        return tuple(
            RunHandle(self, saved.object_id)
            for saved in list_run_objects(self._path).objects
        )
