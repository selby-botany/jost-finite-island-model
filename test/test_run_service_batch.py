"""Tests for executing batch Run objects through `fim.run_service`.

A batch object runs a `fim.batch_session.BatchSession` between the same
safe points a scalar object uses, and publishes the layout `fim run` gives
a batch: one `replicate-NNN/` directory per kept replicate, `summary.json`,
then the batch `manifest.json`.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml
from conftest import FAST_EXPERT_SETTINGS

from fim import run_service
from fim.engine import RunResult, replicate_summary, simulate_params
from fim.examples import classes
from fim.execution import UnsupportedSessionError
from fim.model.params import SimulationParams
from fim.persistence.artifacts import write_run_artifacts
from fim.persistence.binary_store import BinaryLogStore
from fim.persistence.manifest import (
    hash_file,
    read_batch_manifest,
    read_manifest,
    verify_trajectory_integrity,
)
from fim.persistence.report import write_report
from fim.persistence.run_lifecycle import RunKind, RunState
from fim.persistence.run_objects import (
    RunObject,
    create_run_object,
    read_run_object,
)
from fim.persistence.run_ownership import request_cancel, request_pause
from fim.persistence.run_receipt import receipt_problem
from fim.persistence.tlog import iter_frames
from fim.run_service import (
    CheckpointCadence,
    Session,
    completion_was_recovered,
    execute_run_object,
    replicate_directory,
)

_CLASSES_FIXTURE = (
    Path(__file__).resolve().parent / "persistence" / "fixtures" / "classes.yaml"
)
AT = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
OBJECT_ID = "obj-" + "2" * 32

_CONFIG: dict[str, Any] = {
    "N": 20,
    "ploidy": "haploid",
    "d": 2,
    "m": 0.1,
    "mu": 0.01,
    "seed": 20261012,
    "loci": [{"locus_id": 1, "length": 200}],
    "precision": 1.0,
    "convergence_burn_in": 1,
    "expert": dict(FAST_EXPERT_SETTINGS),
    "max_generations": 30,
    "n_replicates": 3,
    "stop_batch_early": False,
    "engine_backend": "generational",
}

_ADAPTIVE: dict[str, Any] = {
    "convergence_statistic": "D",
    "max_generations": 60,
    "n_replicates": 12,
    "replicate_minimum": 3,
    "precision": 0.2,
    "stop_batch_early": True,
    "max_concurrent_replicates": 2,
}

# Requests are checked every 5 ticks; time never triggers a checkpoint.
_CADENCE = CheckpointCadence(generations=10, seconds=1e9, control_generations=5)

Observer = Callable[[Session], None]


@pytest.fixture(autouse=True)
def _fixture_class_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check every `class` against the test fixture, never the shipped file."""
    monkeypatch.setattr(classes, "default_classes_path", lambda: _CLASSES_FIXTURE)


class _Writer:
    """An artifact writer that remembers what it was asked to write."""

    def __init__(self) -> None:
        """Start with nothing written."""
        self.results: list[RunResult] = []

    def __call__(self, result: RunResult, directory: Path) -> None:
        """Remember `result` and write the standard artifacts."""
        self.results.append(result)
        write_run_artifacts(result, directory)


def _attempts() -> Callable[[], str]:
    """Return a deterministic attempt ID factory."""
    counter = itertools.count(1)
    return lambda: f"att-{next(counter):032x}"


def _clock() -> datetime:
    """Return the fixed test time."""
    return AT


def _config(**changes: Any) -> dict[str, Any]:
    """Return `_CONFIG` with `changes` applied."""
    return {**_CONFIG, **changes}


def _create(results: Path, **changes: Any) -> RunObject:
    """Save a batch Run object from `_CONFIG` with `changes` applied."""
    text = yaml.safe_dump(_config(**changes), sort_keys=False)
    return create_run_object(
        text, results=results, clock=_clock, id_factory=lambda: OBJECT_ID
    )


def _store(path: Path, _params: SimulationParams) -> BinaryLogStore:
    """Return a log whose block boundaries never depend on time."""
    return BinaryLogStore(
        path,
        background=False,
        block_seconds=None,
        block_generations=4,
        key_every=4,
    )


def _execute(
    results: Path, writer: _Writer, observer: Observer | None = None
) -> RunObject:
    """Execute the test object with deterministic stores and identities."""
    return execute_run_object(
        OBJECT_ID,
        write_artifacts=writer,
        results=results,
        clock=_clock,
        cadence=_CADENCE,
        monotonic=lambda: 0.0,
        store_factory=_store,
        attempt_factory=_attempts(),
        observer=observer,
    )


def _at(tick: int, action: Callable[[], object]) -> Observer:
    """Return an observer that performs `action` once, at `tick`."""
    done: list[bool] = []

    def observe(session: Session) -> None:
        if session.generation >= tick and not done:
            done.append(True)
            action()

    return observe


def _revision(results: Path) -> int:
    """Return the stored lifecycle revision of the test object."""
    return read_run_object(OBJECT_ID, results=results).record.revision


def _reference(run_id: str | None, **changes: Any) -> tuple[RunResult, ...]:
    """Return what the blocking call produces for the configuration."""
    params = SimulationParams.from_mapping(_config(**changes))
    output = simulate_params(
        params=params,
        clock=_clock,
        run_id=run_id,
    )
    assert isinstance(output, tuple)
    return output


def _assert_same_results(left: list[RunResult], right: tuple[RunResult, ...]) -> None:
    """Assert two batches kept the same replicates with the same outcomes."""
    assert [result.run_id for result in left] == [result.run_id for result in right]
    for one, other in zip(left, right, strict=True):
        assert one.final_state == other.final_state
        assert one.report == other.report
        assert one.convergence_histories == other.convergence_histories
        assert one.manifest == other.manifest


def _published(directory: Path) -> dict[str, object]:
    """Return every published file under `directory`, by relative path.

    Lifecycle records and checkpoints are bookkeeping, not output, so they
    are left out. A log's blocks are sealed at each checkpoint, so its bytes
    (and every digest that covers them) depend on when the run paused;
    logs are compared frame by frame instead, and manifests are compared
    with those digests removed.
    """
    published: dict[str, object] = {}
    paths = [directory / "summary.json", directory / "manifest.json"]
    paths.extend(
        path
        for path in directory.glob("replicate-*/*")
        if path.is_file() and path.suffix != ".tlog"
    )
    for path in sorted(paths):
        name = path.relative_to(directory).as_posix()
        if path.name != "manifest.json":
            published[name] = path.read_bytes()
            continue
        manifest = json.loads(path.read_text())
        artifacts = manifest["artifacts"]
        artifacts.pop("trajectory", None)
        for key in [key for key in artifacts if key.startswith("replicate-")]:
            del artifacts[key]
        published[name] = manifest
    return published


def _assert_same_logs(left: Path, right: Path) -> None:
    """Assert two batch objects hold the same replicate logs."""
    left_logs = sorted(path.relative_to(left) for path in left.rglob("*.tlog"))
    right_logs = sorted(path.relative_to(right) for path in right.rglob("*.tlog"))
    assert left_logs == right_logs
    assert left_logs
    for name in left_logs:
        left_frames = list(iter_frames(left / name))
        right_frames = list(iter_frames(right / name))
        assert [frame.generation for frame in left_frames] == [
            frame.generation for frame in right_frames
        ]
        for one, other in zip(left_frames, right_frames, strict=True):
            np.testing.assert_array_equal(one.counts, other.counts)
            np.testing.assert_array_equal(one.allele_ids, other.allele_ids)


def _replicate_directories(directory: Path) -> list[str]:
    """Return the names of the replicate directories in a batch object."""
    return sorted(path.name for path in directory.glob("replicate-*") if path.is_dir())


def test_a_batch_object_executes_to_completion_as_fim_run_lays_it_out(
    tmp_path: Path,
) -> None:
    """Every replicate, the summary, and the batch manifest are published."""
    _create(tmp_path)
    writer = _Writer()

    finished = _execute(tmp_path, writer)

    assert finished.record.state is RunState.COMPLETED
    run_id = finished.record.scientific_run_id
    assert run_id is not None
    reference = _reference(run_id)
    _assert_same_results(writer.results, reference)
    directory = finished.directory
    names = [
        replicate_directory(directory, run_id, result.run_id).name
        for result in reference
    ]
    assert names == ["replicate-001", "replicate-002", "replicate-003"]
    assert _replicate_directories(directory) == names
    expected_summary = tmp_path / "expected-summary.json"
    write_report(expected_summary, replicate_summary(reference))
    assert (directory / "summary.json").read_bytes() == expected_summary.read_bytes()
    manifest = read_batch_manifest(directory / "manifest.json")
    assert manifest.run_id == run_id
    assert manifest.replicate_run_ids == tuple(r.run_id for r in reference)
    assert manifest.started_at == min(r.manifest.started_at for r in reference)
    assert manifest.artifacts is not None
    for name in names:
        replicate_manifest = directory / name / "manifest.json"
        assert manifest.artifacts[name] == hash_file(replicate_manifest)
        verify_trajectory_integrity(
            directory / name / "trajectory.tlog", read_manifest(replicate_manifest)
        )
    assert manifest.artifacts["summary"] == hash_file(directory / "summary.json")


def test_pause_then_resume_publishes_what_an_uninterrupted_batch_does(
    tmp_path: Path,
) -> None:
    """A batch paused mid-run resumes to the same files and logs."""
    whole_results = tmp_path / "whole"
    _create(whole_results)
    whole = _execute(whole_results, _Writer())
    paused_results = tmp_path / "paused"
    _create(paused_results)
    writer = _Writer()
    pause = _at(
        15,
        lambda: request_pause(
            OBJECT_ID,
            expected_revision=_revision(paused_results),
            results=paused_results,
        ),
    )

    paused = _execute(paused_results, writer, observer=pause)

    assert paused.record.state is RunState.PAUSED
    assert paused.record.committed_generation == 15
    assert not writer.results
    assert not (paused.directory / "manifest.json").exists()

    resumed = _execute(paused_results, writer)

    assert resumed.record.state is RunState.COMPLETED
    assert _published(resumed.directory) == _published(whole.directory)
    _assert_same_logs(resumed.directory, whole.directory)


def test_cancel_keeps_every_replicate_partial_trajectory(tmp_path: Path) -> None:
    """Cancel commits each replicate's log and publishes nothing."""
    _create(tmp_path)
    writer = _Writer()
    cancel = _at(
        10,
        lambda: request_cancel(
            OBJECT_ID, expected_revision=_revision(tmp_path), results=tmp_path
        ),
    )

    cancelled = _execute(tmp_path, writer, observer=cancel)

    assert cancelled.record.state is RunState.CANCELLED
    assert cancelled.record.committed_generation == 10
    assert not writer.results
    directory = cancelled.directory
    assert not (directory / "manifest.json").exists()
    assert not (directory / "summary.json").exists()
    names = _replicate_directories(directory)
    assert names == ["replicate-001", "replicate-002", "replicate-003"]
    for name in names:
        frames = list(iter_frames(directory / name / "trajectory.tlog"))
        assert [frame.generation for frame in frames] == list(range(11))


def test_an_adaptive_batch_publishes_only_the_replicates_it_kept(
    tmp_path: Path,
) -> None:
    """Directories past the stop, or left by another attempt, are removed."""
    created = _create(tmp_path, **_ADAPTIVE)
    stale = created.directory / "replicate-099"
    stale.mkdir()
    (stale / "trajectory.tlog").write_bytes(b"left by an interrupted attempt")
    writer = _Writer()

    finished = _execute(tmp_path, writer)

    assert finished.record.state is RunState.COMPLETED
    reference = _reference(finished.record.scientific_run_id, **_ADAPTIVE)
    assert 3 <= len(reference) < _ADAPTIVE["n_replicates"]
    _assert_same_results(writer.results, reference)
    expected = [f"replicate-{index:03}" for index in range(1, len(reference) + 1)]
    assert _replicate_directories(finished.directory) == expected
    manifest = read_batch_manifest(finished.directory / "manifest.json")
    assert manifest.artifacts is not None
    assert set(manifest.artifacts) == {*expected, "summary"}


def test_a_refused_configuration_is_not_recorded_as_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A batch the session cannot build leaves the object planned."""

    def refuse(_params: SimulationParams) -> None:
        raise UnsupportedSessionError("refused for the test")

    monkeypatch.setattr(run_service, "check_batch_session_support", refuse)
    _create(tmp_path)

    with pytest.raises(UnsupportedSessionError, match="refused for the test"):
        _execute(tmp_path, _Writer())

    record = read_run_object(OBJECT_ID, results=tmp_path).record
    assert record.state is RunState.PLANNED
    assert record.revision == 1


def _batch_problem(finished: RunObject) -> str | None:
    """Check the batch object's completion receipt."""
    run_id = finished.record.scientific_run_id
    assert run_id is not None
    return receipt_problem(finished.directory, kind=RunKind.BATCH, run_id=run_id)


def _rewrite_batch_receipt(directory: Path, change: Callable[[Any], object]) -> None:
    """Apply `change` to the parsed batch receipt and write it back."""
    path = directory / "manifest.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    change(document)
    path.write_text(json.dumps(document), encoding="utf-8")


def test_a_published_batch_receipt_verifies_recursively(tmp_path: Path) -> None:
    """The batch receipt, its summary, and every replicate verify; damage does not."""
    _create(tmp_path)
    finished = _execute(tmp_path, _Writer())
    assert _batch_problem(finished) is None
    report = finished.directory / "replicate-002" / "report.json"
    report.write_bytes(report.read_bytes()[:-1])
    assert _batch_problem(finished) == (
        f"artifact {report} does not have its recorded size"
    )


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (
            lambda document: document["artifacts"].pop("summary"),
            "lists unexpected artifacts",
        ),
        (
            lambda document: document["replicate_run_ids"].__setitem__(0, "run-x-r001"),
            "names a foreign replicate 'run-x-r001'",
        ),
    ],
)
def test_a_batch_receipt_with_the_wrong_contents_is_rejected(
    tmp_path: Path, change: Callable[[Any], object], expected: str
) -> None:
    """Every replicate must be one of this batch's, each listed once."""
    _create(tmp_path)
    finished = _execute(tmp_path, _Writer())
    _rewrite_batch_receipt(finished.directory, change)
    problem = _batch_problem(finished)
    assert problem is not None
    assert expected in problem


def test_a_batch_killed_after_its_receipt_is_recovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A batch whose receipt was published completes without running again."""
    _create(tmp_path)
    publish = run_service.write_batch_manifest

    def publish_then_die(path: Path, manifest: Any) -> None:
        publish(path, manifest)
        raise KeyboardInterrupt

    monkeypatch.setattr(run_service, "write_batch_manifest", publish_then_die)
    with pytest.raises(KeyboardInterrupt):
        _execute(tmp_path, _Writer())
    monkeypatch.setattr(run_service, "write_batch_manifest", publish)
    writer = _Writer()

    recovered = _execute(tmp_path, writer)

    assert completion_was_recovered(recovered.record)
    assert not writer.results


def test_a_batch_killed_before_its_receipt_finalizes_again(tmp_path: Path) -> None:
    """Replicates published before the kill do not complete the batch."""
    _create(tmp_path)

    class _DieOnSecond(_Writer):
        def __call__(self, result: RunResult, directory: Path) -> None:
            if self.results:
                raise KeyboardInterrupt
            super().__call__(result, directory)

    with pytest.raises(KeyboardInterrupt):
        _execute(tmp_path, _DieOnSecond())
    writer = _Writer()

    finished = _execute(tmp_path, writer)

    assert finished.record.state is RunState.COMPLETED
    assert not completion_was_recovered(finished.record)
    assert len(writer.results) == 3
    assert _batch_problem(finished) is None
