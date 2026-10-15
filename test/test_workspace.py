"""`Workspace`: saved, checkpointed runs that pause and resume exactly."""

from __future__ import annotations

from pathlib import Path

import pytest

import fim
from fim.errors import ConfigurationError, StoreError
from fim.persistence.run_lifecycle import RunState


def _spec(**overrides: object) -> fim.RunSpec:
    """Return a small spec that runs a fixed number of generations."""
    fields: dict[str, object] = {
        "model": fim.Model(
            demes=3,
            population=fim.Population(individuals=40, ploidy=1),
            migration=0.1,
            mutation=fim.InfiniteAlleles(rate=0.01),
        ),
        "seed": 5,
        "stopping": fim.Stopping(burn_in=5, max_generations=300),
        "replication": fim.Replication(n=2, stop_early=False),
    }
    fields.update(overrides)
    return fim.RunSpec(**fields)  # type: ignore[arg-type]


def _reports(result: fim.Result) -> list[dict[str, object]]:
    """Return each replicate's report as a plain dict."""
    return [replicate.report.to_dict() for replicate in result.replicates]


def test_planning_saves_a_run_without_starting_it(tmp_path: Path) -> None:
    """A planned run is on disk, listed, and has not run."""
    workspace = fim.Workspace(tmp_path / "results")
    handle = workspace.plan(_spec())
    assert handle.state is RunState.PLANNED
    assert [h.object_id for h in workspace.handles()] == [handle.object_id]
    assert workspace.handle(handle.object_id).state is RunState.PLANNED
    with pytest.raises(StoreError, match="not completed"):
        handle.open()


def test_running_in_a_workspace_gives_the_numbers_of_simulate(tmp_path: Path) -> None:
    """Saving and checkpointing never change a result."""
    spec = _spec()
    saved = fim.Workspace(tmp_path / "results").run(spec)
    assert _reports(saved) == _reports(fim.simulate(spec))
    assert saved.run_id == fim.run_identity(spec)


def test_a_completed_run_opens_from_its_handle(tmp_path: Path) -> None:
    """The handle reads the saved files back with the stored-run types."""
    workspace = fim.Workspace(tmp_path / "results")
    handle = workspace.plan(_spec())
    result = handle.start()
    assert result is not None
    assert handle.state is RunState.COMPLETED
    stored = handle.open()
    assert stored.run_id == result.run_id
    assert [r.report.to_dict() for r in stored.replicates] == _reports(result)


def test_a_paused_run_resumes_to_the_uninterrupted_numbers(tmp_path: Path) -> None:
    """Pause at a safe point, resume from the checkpoint: same result."""
    spec = _spec()
    expected = _reports(fim.simulate(spec))
    workspace = fim.Workspace(tmp_path / "results")
    handle = workspace.plan(spec)

    polls = 0

    def stop_after_a_few() -> bool:
        nonlocal polls
        polls += 1
        return polls > 2

    assert handle.start(stop_requested=stop_after_a_few) is None
    paused = handle.state
    assert paused == RunState.PAUSED

    resumed = handle.resume()
    assert resumed is not None
    completed = handle.state
    assert completed == RunState.COMPLETED
    assert _reports(resumed) == expected


def test_a_cancelled_planned_run_does_not_run(tmp_path: Path) -> None:
    """Cancelling before starting abandons the run."""
    handle = fim.Workspace(tmp_path / "results").plan(_spec())
    handle.cancel()
    assert handle.state is RunState.CANCELLED


def test_a_run_can_be_deleted_when_not_executing(tmp_path: Path) -> None:
    """Deleting removes the saved run from the workspace."""
    workspace = fim.Workspace(tmp_path / "results")
    handle = workspace.plan(_spec())
    handle.delete()
    assert workspace.handles() == ()
    with pytest.raises(StoreError):
        workspace.handle(handle.object_id)


def test_a_workspace_decides_where_runs_live(tmp_path: Path) -> None:
    """A recording directory or worker count is refused, with a reason."""
    workspace = fim.Workspace(tmp_path / "results")
    with pytest.raises(ConfigurationError, match=r"recording\.directory"):
        workspace.plan(_spec(), recording=fim.Recording(directory=tmp_path / "x"))
    with pytest.raises(ConfigurationError, match=r"execution\.workers"):
        workspace.plan(_spec(), execution=fim.Execution(workers=2))


def test_an_invalid_spec_is_refused_before_anything_is_saved(tmp_path: Path) -> None:
    """The engine's rules apply at plan time and leave no half-saved run."""
    bad = _spec(
        model=fim.Model(
            demes=2,
            population=fim.Population(individuals=3, ploidy=1),
            migration=0.1,
            mutation=fim.InfiniteAlleles(rate=0.01),
            initial=fim.FoundingAlleles(count=9),
        )
    )
    workspace = fim.Workspace(tmp_path / "results")
    with pytest.raises(ConfigurationError):
        workspace.plan(bad)
    assert workspace.handles() == ()


def test_the_workspace_path_is_explicit(tmp_path: Path) -> None:
    """The workspace remembers the path it was given and repr shows it."""
    workspace = fim.Workspace(str(tmp_path / "results"))
    assert workspace.path == tmp_path / "results"
    assert "results" in repr(workspace)
