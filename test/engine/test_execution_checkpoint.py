"""Tests for checkpointing and restoring `fim.execution.ScalarSession`.

A run that is checkpointed, abandoned and restored must finish exactly as a
run that never stopped: the same generations, report, manifest and
trajectory. Abandoning is simulated in process (the session advances past
the checkpoint and its log is closed, so the file holds generations the
checkpoint does not describe) and by a child process that exits without
closing anything.
"""

from __future__ import annotations

import itertools
import json
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

from fim.engine import RunResult
from fim.examples import classes
from fim.execution import (
    EQUILIBRATION_HISTORY,
    ScalarSession,
    SessionRestoreError,
    UnsupportedSessionError,
)
from fim.model.params import SimulationParams
from fim.persistence.binary_store import EQUILIBRIUM_LOG_FILENAME, BinaryLogStore
from fim.persistence.run_checkpoint import (
    LoadedCheckpoint,
    checkpoints_directory,
    load_checkpoint,
    publish_checkpoint,
)
from fim.persistence.run_lifecycle import (
    AttemptMismatchError,
    CheckpointReference,
    RunPhase,
)
from fim.persistence.run_objects import create_run_object, read_run_object
from fim.persistence.run_ownership import (
    ExecutionLease,
    reconcile_run_object,
    resume_run_object,
    start_run_object,
)
from fim.persistence.store import InMemoryTrajectoryStore
from fim.persistence.tlog import iter_frames
from fim.persistence.tlog_export import derive_jsonl

_CLASSES_FIXTURE = (
    Path(__file__).resolve().parents[1] / "persistence" / "fixtures" / "classes.yaml"
)
BACKSTOP_SECONDS = 120.0
AT = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)

_CONFIG: dict[str, Any] = {
    "N": 20,
    "ploidy": "haploid",
    "d": 2,
    "m": 0.1,
    "mu": 0.05,
    "seed": 20261011,
    "loci": [{"locus_id": 1, "length": 200}],
    "precision": 0.0,
    "max_generations": 30,
    "n_replicates": 1,
    "stop_batch_early": False,
    "engine_backend": "lineal",
}

_CASES: dict[str, dict[str, Any]] = {
    "infinite": {},
    "finite": {
        "mutation_model": "finite_alleles",
        "loci": [{"locus_id": 1, "length": 1}],
    },
    "thinned": {
        "trajectory_retention": "thinned",
        "trajectory_stride": 5,
        "trajectory_thinning_start": 8,
    },
    "tapered": {"trajectory_retention": "tapered", "trajectory_keep_last": 6},
    "generational": {"engine_backend": "generational"},
}


def _clock() -> datetime:
    """Return a fixed manifest timestamp."""
    return AT


def _params(**changes: Any) -> SimulationParams:
    """Return a small single-replicate configuration, with `changes` applied."""
    return SimulationParams.from_mapping({**_CONFIG, **changes})


def _equilibrium_split(params: SimulationParams) -> SimulationParams:
    """Return `params` founded by a short equilibrium-split ancestral phase."""
    return replace(
        params,
        equilibrium_convergence_window=2,
        equilibrium_convergence_tolerance=0.5,
        equilibrium_max_generations=200,
    )


def _store(path: Path) -> BinaryLogStore:
    """Return a log whose block boundaries never depend on time."""
    return BinaryLogStore(
        path,
        background=False,
        block_seconds=None,
        block_generations=4,
        key_every=4,
    )


def _whole(params: SimulationParams, path: Path) -> RunResult:
    """Run `params` without interruption into the log at `path`."""
    with ScalarSession.create(params, _store(path), clock=_clock) as session:
        session.advance()
        return session.finalize()


def _publish(
    session: ScalarSession, object_directory: Path, checkpoint_id: str
) -> LoadedCheckpoint:
    """Capture `session`, publish it, and read it back verified."""
    capture = session.capture()
    reference = publish_checkpoint(
        object_directory,
        payload=capture.payload,
        arrays=capture.arrays,
        committed_generation=capture.committed_generation,
        checkpoint_id=checkpoint_id,
    )
    return load_checkpoint(object_directory, reference)


def _interrupted(
    params: SimulationParams,
    directory: Path,
    *,
    checkpoint_at: int,
    lost: int,
) -> LoadedCheckpoint:
    """Run to `checkpoint_at`, checkpoint, run `lost` more, then abandon.

    The abandoned log is closed, so it holds generations past the
    checkpoint that the resumed run must drop and write again.
    """
    with ScalarSession.create(
        params, _store(directory / "trajectory.tlog"), clock=_clock
    ) as session:
        session.advance(checkpoint_at)
        loaded = _publish(session, directory / "object", "ckpt-a")
        session.advance(lost)
    return loaded


def _resume(
    params: SimulationParams, directory: Path, loaded: LoadedCheckpoint
) -> RunResult:
    """Restore the run from `loaded` and finish it."""
    with ScalarSession.restore(
        params, loaded, _store(directory / "trajectory.tlog"), clock=_clock
    ) as session:
        session.advance()
        return session.finalize()


def _assert_same(left: RunResult, right: RunResult) -> None:
    """Assert two results agree on everything a run produces."""
    assert left.run_id == right.run_id
    assert left.params == right.params
    assert left.final_state == right.final_state
    assert left.report == right.report
    assert left.convergence_generations == right.convergence_generations
    assert left.convergence_histories == right.convergence_histories
    assert left.manifest == right.manifest
    assert list(left.store.read(left.run_id)) == list(right.store.read(right.run_id))


def _assert_same_log(left: Path, right: Path) -> None:
    """Assert two logs hold the same generations and the same export."""
    left_frames = list(iter_frames(left))
    right_frames = list(iter_frames(right))
    assert [frame.generation for frame in left_frames] == [
        frame.generation for frame in right_frames
    ]
    for one, other in zip(left_frames, right_frames, strict=True):
        np.testing.assert_array_equal(one.counts, other.counts)
        np.testing.assert_array_equal(one.allele_ids, other.allele_ids)
        np.testing.assert_array_equal(one.frequencies, other.frequencies)
    left_export = derive_jsonl(left)
    right_export = derive_jsonl(right)
    assert (left_export.sha256, left_export.rows) == (
        right_export.sha256,
        right_export.rows,
    )


@pytest.fixture(autouse=True)
def _fixture_class_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check every `class` against the test fixture, never the shipped file."""
    monkeypatch.setattr(classes, "default_classes_path", lambda: _CLASSES_FIXTURE)


@pytest.mark.parametrize("case", list(_CASES))
@pytest.mark.parametrize(("checkpoint_at", "lost"), [(0, 3), (7, 5), (13, 9)])
def test_a_restored_session_finishes_as_the_uninterrupted_run(
    tmp_path: Path, case: str, checkpoint_at: int, lost: int
) -> None:
    """Checkpoint, lose later work, restore: the same run, generation for generation."""
    params = _params(**_CASES[case])
    whole = _whole(params, tmp_path / "whole.tlog")
    resumed_directory = tmp_path / "resumed"

    loaded = _interrupted(
        params, resumed_directory, checkpoint_at=checkpoint_at, lost=lost
    )
    resumed = _resume(params, resumed_directory, loaded)

    _assert_same(resumed, whole)
    _assert_same_log(resumed_directory / "trajectory.tlog", tmp_path / "whole.tlog")


def test_a_checkpoint_on_a_block_boundary_resumes_to_identical_bytes(
    tmp_path: Path,
) -> None:
    """Generations 0-7 fill two blocks exactly, so the bytes match too."""
    params = _params()
    _whole(params, tmp_path / "whole.tlog")
    resumed_directory = tmp_path / "resumed"

    loaded = _interrupted(params, resumed_directory, checkpoint_at=7, lost=6)
    _resume(params, resumed_directory, loaded)

    assert (resumed_directory / "trajectory.tlog").read_bytes() == (
        tmp_path / "whole.tlog"
    ).read_bytes()


@pytest.mark.parametrize("backend", ["lineal", "generational"])
def test_an_equilibrium_split_run_resumes_with_its_ancestral_log(
    tmp_path: Path, backend: str
) -> None:
    """The ancestral phase is not run again; its log and outcome are kept."""
    params = _equilibrium_split(_params(engine_backend=backend))
    whole = _whole(params, tmp_path / "whole" / "trajectory.tlog")
    resumed_directory = tmp_path / "resumed"

    loaded = _interrupted(params, resumed_directory, checkpoint_at=4, lost=6)
    resumed = _resume(params, resumed_directory, loaded)

    assert loaded.payload["equilibration"] is not None
    assert EQUILIBRATION_HISTORY in loaded.arrays
    _assert_same(resumed, whole)
    assert resumed.manifest.equilibrium_generation_count is not None
    assert resumed.equilibrium_store is not None
    assert whole.equilibrium_store is not None
    assert list(resumed.equilibrium_store.read(resumed.run_id)) == list(
        whole.equilibrium_store.read(whole.run_id)
    )
    _assert_same_log(
        resumed_directory / EQUILIBRIUM_LOG_FILENAME,
        tmp_path / "whole" / EQUILIBRIUM_LOG_FILENAME,
    )


def test_a_restored_session_can_be_checkpointed_and_restored_again(
    tmp_path: Path,
) -> None:
    """Resuming twice in a row still finishes as the uninterrupted run."""
    params = _params()
    whole = _whole(params, tmp_path / "whole.tlog")
    directory = tmp_path / "resumed"
    first = _interrupted(params, directory, checkpoint_at=5, lost=4)

    with ScalarSession.restore(
        params, first, _store(directory / "trajectory.tlog"), clock=_clock
    ) as session:
        session.advance(6)
        second = _publish(session, directory / "object", "ckpt-b")
        session.advance(3)
    resumed = _resume(params, directory, second)

    assert second.reference.committed_generation == 11
    _assert_same(resumed, whole)


def test_a_checkpoint_taken_after_the_stop_resumes_straight_to_finalization(
    tmp_path: Path,
) -> None:
    """A run interrupted while finalizing restores stopped and finalizes."""
    params = _params()
    whole = _whole(params, tmp_path / "whole.tlog")
    directory = tmp_path / "resumed"
    with ScalarSession.create(
        params, _store(directory / "trajectory.tlog"), clock=_clock
    ) as session:
        session.advance()
        assert session.capture().phase is RunPhase.FINALIZING
        loaded = _publish(session, directory / "object", "ckpt-a")

    with ScalarSession.restore(
        params, loaded, _store(directory / "trajectory.tlog"), clock=_clock
    ) as restored:
        assert restored.stopped
        result = restored.finalize()

    _assert_same(result, whole)


def test_a_running_capture_is_in_the_simulating_phase(tmp_path: Path) -> None:
    """Before the stop, a capture names the simulating phase and its generation."""
    with ScalarSession.create(
        _params(), _store(tmp_path / "t.tlog"), clock=_clock
    ) as session:
        session.advance(3)
        capture = session.capture()

    assert capture.phase is RunPhase.SIMULATING
    assert capture.committed_generation == 3
    assert capture.payload["trajectory"]["generation"] == 3


def test_capturing_an_in_memory_store_is_refused() -> None:
    """Only a binary log has a committed position a checkpoint can record."""
    with (
        ScalarSession.create(_params(), InMemoryTrajectoryStore()) as session,
        pytest.raises(UnsupportedSessionError, match="binary trajectory log"),
    ):
        session.capture()


def test_a_finalized_session_cannot_be_captured(tmp_path: Path) -> None:
    """After `finalize` there is no safe point left to capture."""
    with ScalarSession.create(
        _params(max_generations=3), _store(tmp_path / "t.tlog"), clock=_clock
    ) as session:
        session.advance()
        session.finalize()
        with pytest.raises(ValueError, match="already finalized"):
            session.capture()


def test_restoring_under_another_configuration_is_refused(tmp_path: Path) -> None:
    """A run continues only with the configuration it was started with."""
    params = _params()
    loaded = _interrupted(params, tmp_path, checkpoint_at=3, lost=0)

    with pytest.raises(SessionRestoreError, match="another configuration"):
        ScalarSession.restore(
            _params(seed=1), loaded, _store(tmp_path / "trajectory.tlog")
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"kind": "vector-session"}, "not 'scalar-session'"),
        ({"version": 99}, "version 99"),
        ({"engine_backend": "vector"}, "not one a session can resume"),
    ],
)
def test_a_foreign_or_newer_payload_is_refused(
    tmp_path: Path, change: dict[str, Any], message: str
) -> None:
    """The payload must be a scalar session's, in a version this build reads."""
    params = _params()
    loaded = _interrupted(params, tmp_path, checkpoint_at=2, lost=0)
    altered = replace(loaded, payload={**loaded.payload, **change})

    with pytest.raises(SessionRestoreError, match=message):
        ScalarSession.restore(params, altered, _store(tmp_path / "trajectory.tlog"))


def test_a_log_that_does_not_match_the_recorded_position_is_refused(
    tmp_path: Path,
) -> None:
    """A trajectory that is not the one checkpointed cannot be continued."""
    params = _params()
    loaded = _interrupted(params, tmp_path, checkpoint_at=6, lost=0)
    trajectory = dict(loaded.payload["trajectory"])
    trajectory["chain_crc"] ^= 1
    altered = replace(loaded, payload={**loaded.payload, "trajectory": trajectory})

    with pytest.raises(SessionRestoreError, match="cannot be restored"):
        ScalarSession.restore(params, altered, _store(tmp_path / "trajectory.tlog"))


def test_a_payload_whose_generation_disagrees_is_refused(tmp_path: Path) -> None:
    """The population, monitor and committed generation must agree."""
    params = _params()
    loaded = _interrupted(params, tmp_path, checkpoint_at=4, lost=0)
    altered = replace(loaded, payload={**loaded.payload, "generation": 5})

    with pytest.raises(SessionRestoreError, match="generation"):
        ScalarSession.restore(params, altered, _store(tmp_path / "trajectory.tlog"))


def _start(tmp_path: Path) -> tuple[str, ExecutionLease]:
    """Create and start a run object for `_CONFIG`; return its id and lease."""
    planned = create_run_object(
        yaml.safe_dump(_CONFIG, sort_keys=False),
        results=tmp_path,
        clock=_clock,
        id_factory=lambda: "obj-" + "1" * 32,
    )
    counter = itertools.count(1)
    started = start_run_object(
        planned.object_id,
        expected_revision=planned.record.revision,
        results=tmp_path,
        clock=_clock,
        attempt_factory=lambda: f"att-{next(counter):032x}",
    )
    assert started.lease is not None
    return planned.object_id, started.lease


def test_checkpoint_publishes_under_the_lease_and_restores(tmp_path: Path) -> None:
    """`checkpoint` records the reference in `run.json`; it restores the run."""
    params = _params()
    whole = _whole(params, tmp_path / "whole.tlog")
    object_id, lease = _start(tmp_path)
    directory = lease.current().directory
    with ScalarSession.create(
        params, _store(directory / "trajectory.tlog"), clock=_clock
    ) as session:
        session.advance(9)
        reference = session.checkpoint(lease)
        session.advance(2)

    record = read_run_object(object_id, results=tmp_path).record
    assert record.latest_checkpoint == reference
    assert record.committed_generation == 9
    assert record.phase is RunPhase.SIMULATING
    resumed = _resume(params, directory, load_checkpoint(directory, reference))
    _assert_same(resumed, whole)


def test_a_stale_lease_cannot_checkpoint(tmp_path: Path) -> None:
    """After ownership is lost, a session's checkpoint is refused and removed."""
    object_id, lease = _start(tmp_path)
    directory = lease.current().directory
    with ScalarSession.create(
        _params(), _store(directory / "trajectory.tlog"), clock=_clock
    ) as session:
        session.advance(2)
        first = session.checkpoint(lease)
        session.advance(2)
        # Simulate a lost owner whose process is somehow still alive.
        stale_lock = lease._lock
        assert stale_lock is not None
        lease._lock = None
        stale_lock.close()
        interrupted = reconcile_run_object(object_id, results=tmp_path, clock=_clock)
        resumed = resume_run_object(
            object_id,
            expected_revision=interrupted.record.revision,
            results=tmp_path,
            clock=_clock,
            attempt_factory=lambda: "att-" + "f" * 32,
        )
        assert resumed.lease is not None
        lease._lock = stale_lock
        with pytest.raises(AttemptMismatchError):
            session.checkpoint(lease)
        resumed.lease.release()

    assert read_run_object(object_id, results=tmp_path).record.latest_checkpoint == (
        first
    )
    assert sorted(
        entry.name for entry in checkpoints_directory(directory).iterdir()
    ) == [first.checkpoint_id]


KILL_PROGRAM = """
import os, sys
from datetime import UTC, datetime
from pathlib import Path
from fim.execution import ScalarSession
from fim.model.params import SimulationParams
from fim.persistence.binary_store import BinaryLogStore
from fim.persistence.run_checkpoint import publish_checkpoint
import json

config = json.loads(sys.argv[1])
directory = Path(sys.argv[2])
store = BinaryLogStore(directory / 'trajectory.tlog', background=True,
                       block_seconds=None, block_generations=4, key_every=4)
session = ScalarSession.create(
    SimulationParams.from_mapping(config), store,
    clock=lambda: datetime(2026, 10, 11, 12, 0, tzinfo=UTC))
session.advance(11)
capture = session.capture()
reference = publish_checkpoint(
    directory / "object", payload=capture.payload, arrays=capture.arrays,
    committed_generation=capture.committed_generation, checkpoint_id="ckpt-a")
(directory / "reference.json").write_text(json.dumps(reference.to_dict()))
session.advance(10)
store.flush(sync=True)
os._exit(0)
"""


def test_a_process_killed_after_a_checkpoint_resumes_to_the_uninterrupted_run(
    tmp_path: Path,
) -> None:
    """A real abrupt exit past a checkpoint, then restore: the whole run."""
    params = _params()
    whole = _whole(params, tmp_path / "whole.tlog")
    directory = tmp_path / "killed"
    directory.mkdir()

    completed = subprocess.run(
        [sys.executable, "-c", KILL_PROGRAM, json.dumps(_CONFIG), str(directory)],
        capture_output=True,
        text=True,
        check=False,
        timeout=BACKSTOP_SECONDS,
    )
    assert completed.returncode == 0, completed.stderr
    frames = list(iter_frames(directory / "trajectory.tlog"))
    assert frames[-1].generation > 11  # work past the checkpoint reached disk

    reference = CheckpointReference.from_dict(
        json.loads((directory / "reference.json").read_text())
    )
    loaded = load_checkpoint(directory / "object", reference)
    resumed = _resume(params, directory, loaded)

    _assert_same(resumed, whole)
    _assert_same_log(directory / "trajectory.tlog", tmp_path / "whole.tlog")


_VECTOR_CASES: dict[str, dict[str, Any]] = {
    "infinite": {},
    "finite": {
        "mutation_model": "finite_alleles",
        "loci": [{"locus_id": 1, "length": 1}],
    },
    "stochastic": {"migrant_sampling": "stochastic"},
    "matrix-unequal-loci": {
        "N": [12, 28],
        "m": [[0.8, 0.2], [0.05, 0.95]],
        "mutation_model": "finite_alleles",
        "loci": [{"locus_id": 1, "length": 2}, {"locus_id": 2, "length": 1}],
    },
    "thinned": {
        "trajectory_retention": "thinned",
        "trajectory_stride": 5,
        "trajectory_thinning_start": 8,
    },
    "tapered": {"trajectory_retention": "tapered", "trajectory_keep_last": 6},
}


def _vector(**changes: Any) -> SimulationParams:
    """Return a vector-backend configuration, skipping without numba."""
    pytest.importorskip("numba")
    return _params(engine_backend="generational-vector", **changes)


@pytest.mark.parametrize("case", list(_VECTOR_CASES))
@pytest.mark.parametrize(("checkpoint_at", "lost"), [(0, 3), (7, 5), (13, 9)])
def test_a_restored_vector_session_finishes_as_the_uninterrupted_run(
    tmp_path: Path, case: str, checkpoint_at: int, lost: int
) -> None:
    """The dense block is checkpointed exactly: the same run after a restore."""
    params = _vector(**_VECTOR_CASES[case])
    whole = _whole(params, tmp_path / "whole.tlog")
    resumed_directory = tmp_path / "resumed"

    loaded = _interrupted(
        params, resumed_directory, checkpoint_at=checkpoint_at, lost=lost
    )
    resumed = _resume(params, resumed_directory, loaded)

    assert (loaded.payload["vector"] is None) == (checkpoint_at == 0)
    _assert_same(resumed, whole)
    _assert_same_log(resumed_directory / "trajectory.tlog", tmp_path / "whole.tlog")


def test_a_vector_checkpoint_holds_the_block_not_the_population(
    tmp_path: Path,
) -> None:
    """Past the first generation the payload carries only the block."""
    with ScalarSession.create(
        _vector(), _store(tmp_path / "t.tlog"), clock=_clock
    ) as session:
        session.advance(4)
        capture = session.capture()

    assert capture.payload["population"] is None
    assert capture.payload["registries"] is None
    assert capture.payload["vector"]["generation"] == 4
    assert {"vector.freq", "vector.ids", "vector.ncol"} <= set(capture.arrays)


def test_a_vector_checkpoint_taken_after_the_stop_finalizes(tmp_path: Path) -> None:
    """A vector run interrupted while finalizing restores stopped and finalizes."""
    params = _vector()
    whole = _whole(params, tmp_path / "whole.tlog")
    directory = tmp_path / "resumed"
    with ScalarSession.create(
        params, _store(directory / "trajectory.tlog"), clock=_clock
    ) as session:
        session.advance()
        assert session.capture().phase is RunPhase.FINALIZING
        loaded = _publish(session, directory / "object", "ckpt-a")

    with ScalarSession.restore(
        params, loaded, _store(directory / "trajectory.tlog"), clock=_clock
    ) as restored:
        assert restored.stopped
        result = restored.finalize()

    _assert_same(result, whole)


def test_a_vector_block_under_another_backend_is_refused(tmp_path: Path) -> None:
    """A payload naming a scalar backend cannot carry a dense block."""
    params = _vector()
    loaded = _interrupted(params, tmp_path, checkpoint_at=3, lost=0)
    altered = replace(
        loaded, payload={**loaded.payload, "engine_backend": "generational"}
    )

    with pytest.raises(SessionRestoreError, match="holds a vector block"):
        ScalarSession.restore(params, altered, _store(tmp_path / "trajectory.tlog"))


def test_a_version_1_scalar_payload_still_restores(tmp_path: Path) -> None:
    """Checkpoints written before vector support are still read."""
    params = _params()
    whole = _whole(params, tmp_path / "whole.tlog")
    directory = tmp_path / "resumed"
    loaded = _interrupted(params, directory, checkpoint_at=5, lost=2)
    payload = {key: value for key, value in loaded.payload.items() if key != "vector"}
    older = replace(loaded, payload={**payload, "version": 1})

    _assert_same(_resume(params, directory, older), whole)
