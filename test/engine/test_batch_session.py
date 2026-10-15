"""Tests for `fim.batch_session.BatchSession`.

A batch that is checkpointed, abandoned and restored must finish exactly as
a batch that never stopped, and both must match the blocking `fim.engine.fim`
call: the same kept replicates in the same order, the same reports and
manifests, and the same per-replicate trajectory logs. Abandoning is
simulated in process: the session runs past the checkpoint (finishing,
building and even adaptively stopping lanes the checkpoint does not
describe) and its logs are closed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from conftest import FAST_EXPERT_SETTINGS

from fim.batch_session import (
    BatchSession,
    check_batch_session_support,
)
from fim.engine import RunResult, fim
from fim.engine.identity import derive_replicate_run_id
from fim.execution import (
    ScalarSession,
    SessionRestoreError,
    UnsupportedSessionError,
)
from fim.model.params import SimulationParams
from fim.persistence.binary_store import BinaryLogStore
from fim.persistence.run_checkpoint import (
    LoadedCheckpoint,
    load_checkpoint,
    publish_checkpoint,
)
from fim.persistence.run_lifecycle import RunPhase
from fim.persistence.store import InMemoryTrajectoryStore
from fim.persistence.tlog import iter_frames

AT = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)

_TINY: dict[str, Any] = {
    "N": 20,
    "ploidy": "haploid",
    "m": 0.1,
    "mu": 0.01,
    "d": 2,
    "seed": 20260814,
    "loci": [{"locus_id": 1, "length": 200}],
    "precision": 1.0,
    "convergence_burn_in": 1,
    "expert": dict(FAST_EXPERT_SETTINGS),
    "max_generations": 40,
    "n_replicates": 4,
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
}

_MATCHED: dict[str, Any] = {
    "precision": 0.05,
    "max_generations": 400,
    "n_replicates": 6,
    "expert": {**FAST_EXPERT_SETTINGS, "batch_width": 2},
}

_CASES: dict[str, dict[str, Any]] = {
    "plain": {},
    "lineal": {"engine_backend": "lineal"},
    "adaptive": _ADAPTIVE,
    "adaptive-window": {**_ADAPTIVE, "max_concurrent_replicates": 2},
    "matched": _MATCHED,
    "matched-window": {**_MATCHED, "max_concurrent_replicates": 2},
    "thinned": {
        "trajectory_retention": "thinned",
        "trajectory_stride": 3,
        "trajectory_thinning_start": 4,
    },
}

_VECTOR_CASES: dict[str, dict[str, Any]] = {
    "vector-adaptive": {**_ADAPTIVE, "engine_backend": "generational-vector"},
    "vector-matched-window": {
        **_MATCHED,
        "max_concurrent_replicates": 2,
        "engine_backend": "generational-vector",
    },
}


def _clock() -> datetime:
    """Return a fixed manifest timestamp."""
    return AT


def _params(**changes: Any) -> SimulationParams:
    """Return a small batch configuration, with `changes` applied."""
    return SimulationParams.from_mapping({**_TINY, **changes})


def _factory(directory: Path) -> Callable[[str], BinaryLogStore]:
    """Return a store factory writing one log per replicate under `directory`.

    Block boundaries never depend on time, so two runs write the same bytes.
    """

    def build(run_id: str) -> BinaryLogStore:
        """Build one replicate's log in its own directory."""
        (directory / run_id).mkdir(parents=True, exist_ok=True)
        return BinaryLogStore(
            directory / run_id / "trajectory.tlog",
            background=False,
            block_seconds=None,
            block_generations=4,
            key_every=4,
        )

    return build


def _whole(params: SimulationParams, directory: Path) -> tuple[RunResult, ...]:
    """Run `params` as one uninterrupted batch session."""
    with BatchSession.create(params, _factory(directory), clock=_clock) as session:
        session.advance()
        return session.finalize()


def _publish(session: BatchSession, object_directory: Path) -> LoadedCheckpoint:
    """Capture `session`, publish it, and read it back verified."""
    capture = session.capture()
    reference = publish_checkpoint(
        object_directory,
        payload=capture.payload,
        arrays=capture.arrays,
        committed_generation=capture.committed_generation,
        checkpoint_id=f"ckpt-{capture.committed_generation}",
    )
    return load_checkpoint(object_directory, reference)


def _interrupted(
    params: SimulationParams, directory: Path, *, checkpoint_at: int, lost: int
) -> LoadedCheckpoint:
    """Run `checkpoint_at` ticks, checkpoint, run `lost` more, then abandon."""
    with BatchSession.create(
        params, _factory(directory / "logs"), clock=_clock
    ) as session:
        session.advance(checkpoint_at)
        loaded = _publish(session, directory / "object")
        session.advance(lost)
    return loaded


def _resume(
    params: SimulationParams, directory: Path, loaded: LoadedCheckpoint
) -> tuple[RunResult, ...]:
    """Restore the batch from `loaded` and finish it."""
    with BatchSession.restore(
        params, loaded, _factory(directory / "logs"), clock=_clock
    ) as session:
        session.advance()
        return session.finalize()


def _reference(params: SimulationParams) -> tuple[RunResult, ...]:
    """Return what the blocking call produces for `params`."""
    output = fim(
        params.gene_copies, params.m, params.mu, params.d, params=params, clock=_clock
    )
    assert isinstance(output, tuple)
    return output


def _assert_same_results(
    left: tuple[RunResult, ...], right: tuple[RunResult, ...]
) -> None:
    """Assert two batches kept the same replicates with the same outcomes."""
    assert [result.run_id for result in left] == [result.run_id for result in right]
    for one, other in zip(left, right, strict=True):
        assert one.params == other.params
        assert one.final_state == other.final_state
        assert one.report == other.report
        assert one.convergence_generations == other.convergence_generations
        assert one.convergence_histories == other.convergence_histories
        assert one.manifest == other.manifest


def _assert_same_logs(left: Path, right: Path) -> None:
    """Assert two batch directories hold the same replicate logs."""
    left_logs = sorted(path.relative_to(left) for path in left.rglob("*.tlog"))
    right_logs = sorted(path.relative_to(right) for path in right.rglob("*.tlog"))
    assert left_logs == right_logs
    for name in left_logs:
        left_frames = list(iter_frames(left / name))
        right_frames = list(iter_frames(right / name))
        assert [frame.generation for frame in left_frames] == [
            frame.generation for frame in right_frames
        ]
        for one, other in zip(left_frames, right_frames, strict=True):
            np.testing.assert_array_equal(one.counts, other.counts)
            np.testing.assert_array_equal(one.allele_ids, other.allele_ids)


def _ticks(params: SimulationParams, directory: Path) -> int:
    """Return how many ticks an uninterrupted batch of `params` takes."""
    with BatchSession.create(params, _factory(directory), clock=_clock) as session:
        session.advance()
        return session.ticks


def _case_params(name: str) -> SimulationParams:
    """Return the configuration of the named case."""
    changes = {**_CASES, **_VECTOR_CASES}[name]
    return _params(**changes)


def _vector_ready(name: str) -> None:
    """Skip a vector case when numba is not installed."""
    if name in _VECTOR_CASES:
        pytest.importorskip("numba")


@pytest.mark.parametrize("name", [*_CASES, *_VECTOR_CASES])
def test_an_uninterrupted_batch_session_matches_the_blocking_call(
    name: str, tmp_path: Path
) -> None:
    """Advanced to its stop, a session returns what `fim()` returns."""
    _vector_ready(name)
    params = _case_params(name)
    _assert_same_results(_whole(params, tmp_path), _reference(params))


def test_the_adaptive_case_stops_early_and_the_matched_case_matches_windows(
    tmp_path: Path,
) -> None:
    """The cases exercise an adaptive stop and a matched averaging window."""
    adaptive = _whole(_case_params("adaptive"), tmp_path / "adaptive")
    assert 3 <= len(adaptive) < _ADAPTIVE["n_replicates"]
    matched = _whole(_case_params("matched-window"), tmp_path / "matched")
    windows = [result.params.replicate_averaging_window for result in matched]
    assert windows[0] != windows[-1]


@pytest.mark.parametrize("name", [*_CASES, *_VECTOR_CASES])
@pytest.mark.parametrize("fraction", [0.0, 0.3, 0.7])
@pytest.mark.parametrize("lost", [1, 6, 1000])
def test_a_restored_batch_finishes_as_one_that_never_stopped(
    name: str, fraction: float, lost: int, tmp_path: Path
) -> None:
    """Checkpointed, abandoned and restored, a batch ends exactly the same.

    `lost` ticks run past the checkpoint before abandoning: one, several,
    or through the batch's own stop, so the abandoned attempt has finished
    lanes, built lanes and (adaptively) stopped where the checkpoint has not.
    """
    _vector_ready(name)
    params = _case_params(name)
    expected = _whole(params, tmp_path / "whole")
    total = _ticks(params, tmp_path / "count")
    loaded = _interrupted(
        params,
        tmp_path / "resumed",
        checkpoint_at=int(total * fraction),
        lost=lost,
    )
    _assert_same_results(_resume(params, tmp_path / "resumed", loaded), expected)
    _assert_same_logs(tmp_path / "whole", tmp_path / "resumed" / "logs")


def test_a_checkpoint_taken_at_the_stop_finalizes_the_same_batch(
    tmp_path: Path,
) -> None:
    """A FINALIZING checkpoint restores, finalizes, and finalizes again."""
    params = _case_params("adaptive-window")
    expected = _whole(params, tmp_path / "whole")
    with BatchSession.create(
        params, _factory(tmp_path / "resumed" / "logs"), clock=_clock
    ) as session:
        session.advance()
        capture = session.capture()
        assert capture.phase is RunPhase.FINALIZING
        loaded = _publish(session, tmp_path / "resumed" / "object")
        first = session.finalize()
    _assert_same_results(first, expected)
    # The attempt finalized (discarding the lanes past the stop) and was
    # lost before recording completion; the checkpoint still finishes.
    second = _resume(params, tmp_path / "resumed", loaded)
    _assert_same_results(second, expected)
    _assert_same_logs(tmp_path / "whole", tmp_path / "resumed" / "logs")


def test_a_restored_stopped_batch_checkpoints_again(tmp_path: Path) -> None:
    """A restored FINALIZING batch captures the same coordinator once more."""
    params = _case_params("adaptive")
    expected = _whole(params, tmp_path / "whole")
    with BatchSession.create(
        params, _factory(tmp_path / "logs"), clock=_clock
    ) as session:
        session.advance()
        loaded = _publish(session, tmp_path / "first")
    with BatchSession.restore(
        params, loaded, _factory(tmp_path / "logs"), clock=_clock
    ) as session:
        again = session.capture()
        assert again.payload["accepted_count"] == loaded.payload["accepted_count"]
        assert (
            again.payload["discarded_run_ids"] == (loaded.payload["discarded_run_ids"])
        )
        assert again.payload["lanes"] == loaded.payload["lanes"]
        reloaded = _publish(session, tmp_path / "second")
    with BatchSession.restore(
        params, reloaded, _factory(tmp_path / "logs"), clock=_clock
    ) as session:
        assert session.stopped
        _assert_same_results(session.finalize(), expected)
    _assert_same_logs(tmp_path / "whole", tmp_path / "logs")


def test_the_coordinator_records_the_accepted_prefix_in_replicate_order(
    tmp_path: Path,
) -> None:
    """Admission follows replicate order, never finishing order."""
    params = _case_params("adaptive")
    with BatchSession.create(
        params, _factory(tmp_path / "logs"), clock=_clock
    ) as session:
        while not session.stopped:
            session.advance(1)
            payload = session.capture().payload
            finished = [
                entry["index"]
                for entry in payload["lanes"]
                if entry["status"] == "finished"
            ]
            admitted = payload["admitted_count"]
            assert set(range(admitted)) <= set(finished)
            assert admitted == len(finished) or admitted not in finished
        accepted = session.capture().payload["accepted_count"]
        results = session.finalize()
    assert [result.run_id for result in results] == [
        derive_replicate_run_id(params, index, None) for index in range(accepted)
    ]


def test_restoring_under_another_configuration_is_refused(tmp_path: Path) -> None:
    """A batch continues only with the configuration it started with."""
    params = _case_params("plain")
    loaded = _interrupted(params, tmp_path, checkpoint_at=2, lost=0)
    with pytest.raises(SessionRestoreError, match="another configuration"):
        BatchSession.restore(
            replace(params, seed=params.seed + 1),
            loaded,
            _factory(tmp_path / "logs"),
            clock=_clock,
        )


def test_a_scalar_checkpoint_is_not_a_batch_checkpoint(tmp_path: Path) -> None:
    """The payload kind separates scalar and batch checkpoints."""
    scalar = _params(n_replicates=1)
    store = BinaryLogStore(tmp_path / "scalar.tlog", background=False)
    with ScalarSession.create(scalar, store, clock=_clock) as session:
        capture = session.capture()
        reference = publish_checkpoint(
            tmp_path / "object",
            payload=capture.payload,
            arrays=capture.arrays,
            committed_generation=capture.committed_generation,
        )
    loaded = load_checkpoint(tmp_path / "object", reference)
    with pytest.raises(SessionRestoreError, match="is not 'batch-session'"):
        BatchSession.restore(scalar, loaded, _factory(tmp_path / "logs"))


_TAMPERING: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "unstopped": lambda payload: {**payload, "accepted_count": None},
    "stop-moved": lambda payload: {
        **payload,
        "accepted_count": payload["accepted_count"] - 1,
        "admitted_count": payload["admitted_count"] - 1,
        "lanes": payload["lanes"][:-1],
    },
    "lane-dropped": lambda payload: {**payload, "lanes": payload["lanes"][:-1]},
    "kept-discarded": lambda payload: {
        **payload,
        "discarded_run_ids": [
            *payload["discarded_run_ids"],
            payload["lanes"][0]["run_id"],
        ],
    },
}


@pytest.mark.parametrize("tampering", _TAMPERING)
def test_a_tampered_coordinator_is_refused(tampering: str, tmp_path: Path) -> None:
    """A coordinator inconsistent with its lanes or its replay is refused."""
    params = _case_params("adaptive")
    with BatchSession.create(
        params, _factory(tmp_path / "logs"), clock=_clock
    ) as session:
        session.advance()
        capture = session.capture()
    payload = _TAMPERING[tampering](capture.payload)
    reference = publish_checkpoint(
        tmp_path / "object",
        payload=payload,
        arrays=capture.arrays,
        committed_generation=capture.committed_generation,
    )
    loaded = load_checkpoint(tmp_path / "object", reference)
    with pytest.raises(SessionRestoreError, match="cannot be restored"):
        BatchSession.restore(params, loaded, _factory(tmp_path / "logs"))


def test_a_single_replicate_is_refused() -> None:
    """One replicate runs as a scalar session, not a batch."""
    with pytest.raises(UnsupportedSessionError, match="two or more replicates"):
        check_batch_session_support(_params(n_replicates=1))


def test_an_in_memory_batch_runs_but_cannot_checkpoint() -> None:
    """Only binary logs record a position a checkpoint can name."""
    params = _case_params("plain")
    with BatchSession.create(
        params, lambda _run_id: InMemoryTrajectoryStore(), clock=_clock
    ) as session:
        session.advance(2)
        with pytest.raises(UnsupportedSessionError, match="binary trajectory log"):
            session.capture()
        session.advance()
        _assert_same_results(session.finalize(), _reference(params))


def test_advance_refuses_negative_ticks_and_finalize_an_unstopped_batch(
    tmp_path: Path,
) -> None:
    """The stepping contract is checked."""
    with BatchSession.create(
        _case_params("plain"), _factory(tmp_path), clock=_clock
    ) as session:
        with pytest.raises(ValueError, match="non-negative"):
            session.advance(-1)
        with pytest.raises(ValueError, match="has not stopped"):
            session.finalize()
