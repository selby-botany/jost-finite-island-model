"""Which settings change a run's numbers, checked on the real engine.

A run's ID leaves out the settings that cannot change its results
(`SimulationParams.identity_dict`). That is only honest if they truly cannot,
so each exclusion is verified here by running the same science both ways. The
one setting that looked neutral and is not, the lane window of an adaptive
batch, is pinned in the other direction.
"""

from __future__ import annotations

import functools
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from vector_support import loci, make_params

from fim.engine import RunResult, fim
from fim.model.params import SimulationParams
from fim.persistence.binary_store import BinaryLogStore
from fim.persistence.store import InMemoryTrajectoryStore

Outcome = list[tuple[str, tuple[int, ...], dict[str, tuple[float, ...]]]]


def _clock() -> datetime:
    """Return a fixed manifest time so results compare exactly."""
    return datetime(2026, 10, 15, tzinfo=UTC)


def _file_store(directory: str, run_id: str) -> BinaryLogStore:
    """Open one replicate's log under `directory` (picklable via `partial`)."""
    return BinaryLogStore(Path(directory) / f"{run_id}.tlog")


def _outcome(results: tuple[RunResult, ...] | RunResult) -> Outcome:
    """Reduce results to what a run reports: reports and histories."""
    runs = results if isinstance(results, tuple) else (results,)
    return [
        (
            json.dumps(run.report, sort_keys=True),
            run.convergence_generations,
            dict(run.convergence_histories),
        )
        for run in runs
    ]


def _run(params: SimulationParams, **keywords: object) -> Outcome:
    """Run `params` on the generational engine, in memory."""
    return _outcome(
        fim(
            params.gene_copies,
            params.m,
            params.mu,
            params.d,
            params=params,
            store=InMemoryTrajectoryStore(),
            run_id="run",
            clock=_clock,
            engine_backend="generational",
            **keywords,  # type: ignore[arg-type]
        )
    )


def _adaptive() -> SimulationParams:
    """Return an adaptive batch, where replicates stop on their interval."""
    return make_params(
        1200,
        loci=loci(1),
        d=3,
        n_replicates=6,
        mu=0.02,
        m=0.1,
        stop_batch_early=True,
        precision=0.08,
        replicate_minimum=3,
    )


def test_thinned_retention_leaves_every_reported_number_unchanged() -> None:
    """Keeping fewer stored generations never changes what is reported."""
    full = make_params(
        300, loci=loci(1), d=3, n_replicates=3, mu=0.01, m=0.05, stop_batch_early=False
    )
    thinned = replace(
        full,
        trajectory_retention="thinned",
        trajectory_stride=7,
        trajectory_thinning_start=50,
    )
    assert _run(thinned) == _run(full)


def test_process_count_leaves_an_adaptive_batch_unchanged(tmp_path: Path) -> None:
    """The `lineal` process pool judges the stop in replicate order."""
    params = _adaptive()

    def run(workers: int | None) -> Outcome:
        directory = tmp_path / f"workers-{workers}"
        directory.mkdir()
        return _outcome(
            fim(
                params.gene_copies,
                params.m,
                params.mu,
                params.d,
                params=params,
                run_id="run",
                clock=_clock,
                max_workers=workers,
                store_factory=functools.partial(_file_store, str(directory)),
                engine_backend="lineal",
            )
        )

    assert run(3) == run(None)


def test_the_lane_window_changes_an_adaptive_batch() -> None:
    """The reason `max_concurrent_replicates` is part of a run's identity.

    The first wave of replicates sets the averaging window of the later ones,
    so a smaller wave gives different numbers from the same seed. If the
    planner is ever made independent of the window, this test fails and the
    setting can move out of the identity.
    """
    params = _adaptive()
    assert _run(replace(params, max_concurrent_replicates=2)) != _run(params)


def test_the_lane_window_leaves_a_fixed_size_batch_unchanged() -> None:
    """Without adaptive stopping, no replicate waits on another."""
    params = make_params(
        300, loci=loci(1), d=3, n_replicates=3, mu=0.01, m=0.05, stop_batch_early=False
    )
    assert _run(replace(params, max_concurrent_replicates=1)) == _run(params)
