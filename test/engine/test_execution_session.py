"""Tests for `fim.execution`: stepwise scalar sessions match the blocking run."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from fim.engine import RunResult, fim
from fim.execution import ScalarSession, UnsupportedSessionError
from fim.model.params import SimulationParams
from fim.persistence.binary_store import BinaryLogStore
from fim.persistence.store import InMemoryTrajectoryStore


def _clock() -> datetime:
    """Return a fixed manifest timestamp."""
    return datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def _params(**changes: Any) -> SimulationParams:
    """Return a small single-replicate configuration, with `changes` applied."""
    config: dict[str, Any] = {
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
    config.update(changes)
    return SimulationParams.from_mapping(config)


def _blocking(params: SimulationParams, store: Any = None) -> RunResult:
    """Run `params` through the blocking `fim` call."""
    result = fim(
        params.gene_copies,
        params.m,
        params.mu,
        params.d,
        params=params,
        store=store if store is not None else InMemoryTrajectoryStore(),
        clock=_clock,
    )
    assert isinstance(result, RunResult)
    return result


def _stepwise(
    params: SimulationParams, steps: list[int], store: Any = None
) -> RunResult:
    """Run `params` through a session in the given step sizes, then to the end."""
    with ScalarSession.create(
        params,
        store if store is not None else InMemoryTrajectoryStore(),
        clock=_clock,
    ) as session:
        for count in steps:
            session.advance(count)
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


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"mutation_model": "finite_alleles", "loci": [{"locus_id": 1, "length": 1}]},
        {
            "trajectory_retention": "thinned",
            "trajectory_stride": 5,
            "trajectory_thinning_start": 8,
        },
        {"engine_backend": "generational"},
    ],
    ids=["infinite", "finite", "thinned", "generational"],
)
@pytest.mark.parametrize("steps", [[], [1], [0, 3, 1, 7], [1] * 12])
def test_stepwise_session_matches_blocking_run(
    changes: dict[str, Any], steps: list[int]
) -> None:
    """Any split of the generations into steps reproduces the blocking run."""
    params = _params(**changes)

    _assert_same(_stepwise(params, steps), _blocking(params))


def test_stepwise_session_writes_identical_log_bytes(tmp_path: Path) -> None:
    """The binary trajectory log a session writes is byte-identical."""
    params = _params()
    blocking_path = tmp_path / "blocking.tlog"
    session_path = tmp_path / "session.tlog"

    _blocking(params, BinaryLogStore(blocking_path))
    _stepwise(params, [2, 5], BinaryLogStore(session_path))

    assert session_path.read_bytes() == blocking_path.read_bytes()


def test_advance_reports_the_stop_and_then_does_nothing() -> None:
    """`advance` returns `True` once stopped and runs no further generations."""
    with ScalarSession.create(_params(max_generations=6), clock=_clock) as session:
        assert session.generation == 0
        assert session.advance(2) is False
        assert session.generation == 2
        assert session.advance() is True
        assert session.generation == 6
        assert session.advance(5) is True
        assert session.generation == 6


def test_finalize_is_refused_before_the_run_stops() -> None:
    """A running session cannot produce a result."""
    with (
        ScalarSession.create(_params(), clock=_clock) as session,
        pytest.raises(ValueError, match="has not stopped"),
    ):
        session.finalize()


def test_finalize_returns_the_same_result_twice_and_blocks_advance() -> None:
    """A finalized session is terminal."""
    session = ScalarSession.create(_params(max_generations=3), clock=_clock)
    session.advance()
    result = session.finalize()

    assert session.finalize() is result
    with pytest.raises(ValueError, match="already finalized"):
        session.advance(1)


def test_a_closed_session_cannot_advance() -> None:
    """Closing abandons the session."""
    session = ScalarSession.create(_params(), clock=_clock)
    session.close()
    session.close()

    with pytest.raises(ValueError, match="closed"):
        session.advance(1)


def test_negative_step_is_refused() -> None:
    """A step count cannot be negative."""
    with (
        ScalarSession.create(_params(), clock=_clock) as session,
        pytest.raises(ValueError, match="non-negative"),
    ):
        session.advance(-1)


def test_batches_are_refused() -> None:
    """A session runs exactly one replicate."""
    with pytest.raises(UnsupportedSessionError, match="exactly one replicate"):
        ScalarSession.create(_params(n_replicates=2), clock=_clock)


def test_the_vector_backend_is_refused() -> None:
    """The dense vector backend has no resumable session yet."""
    with pytest.raises(UnsupportedSessionError, match="generational-vector"):
        ScalarSession.create(
            _params(engine_backend="generational-vector"), clock=_clock
        )
