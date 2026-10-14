"""A replicate's report describes its own final state, on every backend.

A replicate past the first wave of a batch pauses until the wave has been
measured, then is handed its averaging window; if that window ends at the
generation where it paused, its monitor stops without another step. These
tests pin that the `report.json` content and the final state of such a
replicate are those of the generation it stopped at, and that the trajectory
log holds that generation's row.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from conftest import FAST_EXPERT_SETTINGS
from test_engine import _tiny_config

from fim.engine import (
    GenerationalBackend,
    LinealBackend,
    RunResult,
    SequentialAdvancer,
    VectorizedAdvancer,
)
from fim.model.params import SimulationParams
from fim.persistence.store import InMemoryTrajectoryStore


def _clock() -> datetime:
    """Return a fixed manifest timestamp."""
    return datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def _params() -> SimulationParams:
    """Return a six-replicate batch whose first wave is two replicates."""
    config: dict[str, object] = {
        **_tiny_config(),
        "precision": 0.05,
        "max_generations": 400,
        "n_replicates": 6,
        "expert": {**FAST_EXPERT_SETTINGS, "batch_width": 2},
    }
    return SimulationParams.from_mapping(config)


def _run(backend: LinealBackend | GenerationalBackend) -> tuple[RunResult, ...]:
    """Run the batch on `backend` and return every replicate's result."""
    store = InMemoryTrajectoryStore()
    output = backend.run(_params(), store, None, _clock)
    assert isinstance(output, tuple)
    return output


@pytest.mark.parametrize(
    "backend_factory",
    [
        LinealBackend,
        lambda: GenerationalBackend(SequentialAdvancer()),
        lambda: GenerationalBackend(VectorizedAdvancer()),
    ],
    ids=["lineal", "generational", "generational-vector"],
)
def test_every_replicate_reports_the_generation_it_stopped_at(
    backend_factory: Callable[[], LinealBackend | GenerationalBackend],
) -> None:
    """Report, state and manifest agree on the stopping generation."""
    output = _run(backend_factory())

    for result in output:
        generation = result.final_state.generation
        assert generation > 0, result.run_id
        assert result.report["generation"] == generation, result.run_id
        assert result.manifest.generation == generation, result.run_id
        assert result.convergence_generations[-1] == generation, result.run_id


def test_vectorized_batch_is_identical_to_the_lineal_batch() -> None:
    """Final states and reports match the lineal batch replicate for replicate."""
    lineal = _run(LinealBackend())
    vector = _run(GenerationalBackend(VectorizedAdvancer()))

    assert [r.final_state for r in vector] == [r.final_state for r in lineal]
    assert [r.report for r in vector] == [r.report for r in lineal]


def test_vectorized_trajectory_ends_at_the_stopping_generation() -> None:
    """The log of every replicate holds the row of its final generation."""
    for backend in (
        LinealBackend(),
        GenerationalBackend(SequentialAdvancer()),
        GenerationalBackend(VectorizedAdvancer()),
    ):
        store = InMemoryTrajectoryStore()
        output = backend.run(_params(), store, None, _clock)
        assert isinstance(output, tuple)
        for result in output:
            rows = list(store.read(result.run_id))
            last = max(int(row["generation"]) for row in rows)
            assert last == result.final_state.generation, result.run_id
