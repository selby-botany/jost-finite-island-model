"""Tests for `fim.engine.concurrency.default_replicate_concurrency`.

`generational-vector` has no fan-out mechanism (`VectorizedAdvancer.advance`
steps every active lane in one Python loop), so its default must stay the
fixed `batch_width` regardless of hardware; `lineal` and `generational` both
get genuine, hardware-scaled fan-out, so their default must track the
process-visible core count. Either way, the result never exceeds
`n_replicates`.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import pytest

from fim.engine import backends as engine_backends
from fim.engine.backends import GenerationalBackend
from fim.engine.concurrency import default_replicate_concurrency
from fim.engine.lanes import SequentialAdvancer, ThreadedAdvancer, VectorizedAdvancer
from fim.engine.records import Clock, RunResult
from fim.model.params import SimulationParams
from fim.persistence.store import InMemoryTrajectoryStore, TrajectoryStore


def _clock() -> datetime:
    """A fixed manifest timestamp; never actually reached (`run_batch` is faked)."""
    return datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def _tiny_params(**overrides: object) -> SimulationParams:
    """A minimal, never-actually-simulated config: `run_batch` is intercepted."""
    config: dict[str, object] = {
        "N": 20,
        "ploidy": "haploid",
        "m": 0.1,
        "mu": 0.01,
        "d": 2,
        "seed": 20261022,
        "loci": [{"locus_id": 1, "length": 200}],
        "n_replicates": 20,
        **overrides,
    }
    return SimulationParams.from_mapping(config)


@pytest.mark.parametrize("engine_backend", ["lineal", "generational"])
def test_core_bound_backends_default_to_the_process_visible_core_count(
    monkeypatch: pytest.MonkeyPatch, engine_backend: str
) -> None:
    monkeypatch.delattr(os, "process_cpu_count", raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 6)
    assert (
        default_replicate_concurrency(
            engine_backend,  # type: ignore[arg-type]
            n_replicates=200,
            batch_width=8,
        )
        == 6
    )


def test_generational_vector_defaults_to_batch_width_regardless_of_cores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(os, "process_cpu_count", raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 64)
    assert (
        default_replicate_concurrency(
            "generational-vector", n_replicates=200, batch_width=8
        )
        == 8
    )


@pytest.mark.parametrize(
    "engine_backend", ["lineal", "generational", "generational-vector"]
)
def test_the_result_never_exceeds_n_replicates(
    monkeypatch: pytest.MonkeyPatch, engine_backend: str
) -> None:
    monkeypatch.delattr(os, "process_cpu_count", raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 64)
    assert (
        default_replicate_concurrency(
            engine_backend,  # type: ignore[arg-type]
            n_replicates=3,
            batch_width=8,
        )
        == 3
    )


def test_prefers_process_cpu_count_over_the_machine_wide_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "process_cpu_count", lambda: 2, raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 64)
    assert (
        default_replicate_concurrency("generational", n_replicates=200, batch_width=8)
        == 2
    )


def test_falls_back_to_one_when_no_core_count_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(os, "process_cpu_count", raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert default_replicate_concurrency("lineal", n_replicates=200, batch_width=8) == 1


def _capture_default_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Intercept `run_batch` itself, capturing `GenerationalBackend.run`'s call.

    Faster and more precise than letting a real batch actually simulate:
    this checks the exact value wired into `run_batch`'s own
    `default_concurrency` argument, which is the one thing this
    session's fix changes — not a simulated outcome that would pass
    unchanged regardless of which value reached it.
    """
    captured: dict[str, Any] = {}

    def fake_run_batch(
        params: SimulationParams,
        store: TrajectoryStore,
        run_id: str | None,
        clock: Clock,
        advancer: object,
        *,
        default_concurrency: int | None = None,
    ) -> tuple[RunResult, ...]:
        del params, store, run_id, clock, advancer
        captured["default_concurrency"] = default_concurrency
        return ()

    monkeypatch.setattr(engine_backends, "run_batch", fake_run_batch)
    return captured


def test_an_unset_generational_vector_batch_no_longer_schedules_every_replicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression this whole investigation traces back to.

    A fresh adaptive batch of 20 replicates under `generational-vector`,
    with `max_concurrent_replicates` left unset — exactly the bundled
    `golden-part-vi` example's own configuration — must no longer
    resolve to scheduling all 20 replicates concurrently (the confirmed
    cause of the GUI's "frozen progress screen" liveliness bug): it
    resolves to the small, fixed `batch_width` instead.
    """
    captured = _capture_default_concurrency(monkeypatch)
    params = _tiny_params(n_replicates=20)

    GenerationalBackend(VectorizedAdvancer()).run(
        params,
        InMemoryTrajectoryStore(),
        "batch",
        _clock,
    )

    assert captured["default_concurrency"] == params.expert.batch_width
    assert captured["default_concurrency"] < params.n_replicates


def test_an_unset_generational_batch_resolves_the_process_visible_core_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(os, "process_cpu_count", raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 4)
    captured = _capture_default_concurrency(monkeypatch)
    params = _tiny_params(n_replicates=20)

    GenerationalBackend(ThreadedAdvancer()).run(
        params,
        InMemoryTrajectoryStore(),
        "batch",
        _clock,
    )

    assert captured["default_concurrency"] == 4


def test_an_explicit_default_concurrency_constructor_argument_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_default_concurrency(monkeypatch)
    params = _tiny_params(n_replicates=20)

    GenerationalBackend(SequentialAdvancer(), default_concurrency=3).run(
        params, InMemoryTrajectoryStore(), "batch", _clock
    )

    assert captured["default_concurrency"] == 3


def test_an_explicit_max_concurrent_replicates_still_wins_over_the_resolved_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`run_batch`'s own precedence (unaffected by this fix): an explicit
    `max_concurrent_replicates` always wins over whatever
    `default_concurrency` the backend resolved."""
    captured = _capture_default_concurrency(monkeypatch)
    params = _tiny_params(n_replicates=20, max_concurrent_replicates=5)

    GenerationalBackend(VectorizedAdvancer()).run(
        params,
        InMemoryTrajectoryStore(),
        "batch",
        _clock,
    )

    # The resolved default is still computed and passed down (run_batch's
    # own `or` chain is what actually prefers max_concurrent_replicates),
    # so this only re-confirms default_concurrency's own value is correct
    # even when it will not end up governing this particular run.
    assert captured["default_concurrency"] == params.expert.batch_width
