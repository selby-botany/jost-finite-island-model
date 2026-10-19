"""A run's ID names its science, not how the computer was told to run it.

Run identity hashes `SimulationParams.identity_dict`. These tests pin what is
in and out of it: the engine, JIT, trajectory retention, and storage tuning
are out, so one scientific configuration has one ID on every backend; every
setting that changes the numbers is in.
"""

from __future__ import annotations

import pytest

from fim.config.expert import ExpertSettings
from fim.engine import deterministic_run_id
from fim.model.params import IDENTITY_EXCLUDED_KEYS, SimulationParams
from fim.spec import (
    Execution,
    InfiniteAlleles,
    Model,
    Population,
    Recording,
    Replication,
    RunConfig,
    RunSpec,
    Stopping,
    run_identity,
    to_params,
)


def _spec(**overrides: object) -> RunSpec:
    """Return a small valid specification, with fields replaced."""
    fields: dict[str, object] = {
        "model": Model(
            demes=3,
            population=Population(individuals=50, ploidy=1),
            migration=0.05,
            mutation=InfiniteAlleles(rate=0.001),
        ),
        "seed": 11,
    }
    fields.update(overrides)
    return RunSpec(**fields)  # type: ignore[arg-type]


def test_the_engine_and_recording_do_not_change_the_id() -> None:
    """One configuration, one ID, whatever backend or retention runs it."""
    spec = _spec()
    base = deterministic_run_id(to_params(spec))
    assert run_identity(spec) == base
    for execution in (
        Execution(backend="generational"),
        Execution(backend="generational-vector"),
        Execution(backend="auto", auto_vector_min_d=9, auto_vector_max_capacity=8),
        Execution(backend="generational", jit="numba"),
        Execution(workers=4),
    ):
        assert deterministic_run_id(to_params(spec, execution)) == base, execution
    for recording in (
        Recording(retention="thinned", stride=9, thinning_start=100),
        Recording(directory="somewhere"),
    ):
        assert deterministic_run_id(to_params(spec, recording=recording)) == base


def test_storage_tuning_does_not_change_the_id_but_convergence_tuning_does() -> None:
    """Expert settings count only when they can change the numbers."""
    base = run_identity(_spec())
    storage = _spec(expert=ExpertSettings(log_key_every=77, checkpoint_generations=5))
    convergence = _spec(expert=ExpertSettings(check_growth=3.0))
    assert run_identity(storage) == base
    assert run_identity(convergence) != base


@pytest.mark.parametrize(
    "changes",
    [
        {"seed": 12},
        {"stopping": Stopping(precision=0.02)},
        {"stopping": Stopping(burn_in=40, max_generations=900)},
        {"replication": Replication(n=7)},
        {"replication": Replication(max_concurrent=2)},
    ],
    ids=["seed", "precision", "burn-in-and-cap", "replicates", "lane-window"],
)
def test_a_scientific_setting_changes_the_id(changes: dict[str, object]) -> None:
    """Anything that can change the numbers is part of the identity."""
    assert run_identity(_spec(**changes)) != run_identity(_spec())


def test_the_lane_window_is_identity_because_it_changes_adaptive_results() -> None:
    """`max_concurrent_replicates` is in the ID, unlike the engine choice."""
    params = to_params(_spec(replication=Replication(max_concurrent=2)))
    assert "max_concurrent_replicates" in params.identity_dict()
    assert not set(IDENTITY_EXCLUDED_KEYS) & set(params.identity_dict())


def test_the_file_route_and_the_spec_route_agree() -> None:
    """A configuration file and its `RunSpec` name the same run."""
    mapping = {"N": 50, "ploidy": "haploid", "d": 3, "m": 0.05, "mu": 0.001, "seed": 11}
    from_file = deterministic_run_id(SimulationParams.from_mapping(mapping))
    assert (
        run_identity(RunConfig.from_mapping(mapping).spec)
        == from_file
        == run_identity(_spec())
    )


def test_excluded_keys_are_real_serialized_keys() -> None:
    """A renamed field must not silently stay inside the hash."""
    serialized = to_params(_spec()).to_dict()
    assert {"engine_backend", "jit", "auto_vector_min_d"} <= set(serialized)
    assert set(IDENTITY_EXCLUDED_KEYS) - {
        "trajectory_retention",
        "trajectory_stride",
        "trajectory_thinning_start",
        "trajectory_keep_last",
        "trajectory_keep_burn_in",
        "history_format",
    } <= set(serialized)
