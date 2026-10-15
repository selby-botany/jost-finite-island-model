"""`fim.simulate`, the `fim` facade, and the exception hierarchy."""

from __future__ import annotations

import subprocess
import sys

import pytest

import fim
from fim.engine import backends as engine_backends
from fim.engine.identity import deterministic_run_id
from fim.errors import ConfigurationError, FimError, StoreError, UnsupportedError
from fim.execution import SessionRestoreError, UnsupportedSessionError
from fim.group_lifecycle import GroupBusyError
from fim.model.vector_block import VectorMemoryCeilingError
from fim.persistence.groups import ClosedGroupError, ReadOnlyError
from fim.persistence.object_lock import LockUnavailableError
from fim.persistence.run_lifecycle import LifecycleError
from fim.persistence.scalar_snapshot import SnapshotError
from fim.persistence.tlog import TlogError
from fim.spec import to_params


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
        "stopping": fim.Stopping(burn_in=5, max_generations=40),
        "replication": fim.Replication(n=3, stop_early=False),
    }
    fields.update(overrides)
    return fim.RunSpec(**fields)  # type: ignore[arg-type]


def test_simulate_returns_a_result_with_the_runs_identity() -> None:
    """The result names the run by its science and keeps the spec as written."""
    spec = _spec()
    result = fim.simulate(spec)
    assert (
        result.run_id == deterministic_run_id(to_params(spec)) == fim.run_identity(spec)
    )
    assert result.spec == spec
    assert [replicate.seed for replicate in result.replicates] == [5, 6, 7]


def test_every_backend_gives_the_same_numbers_and_the_same_run_id() -> None:
    """Public parity: the engine choice cannot change a result or its ID."""
    spec = _spec()
    reference = fim.simulate(spec, execution=fim.Execution(backend="lineal"))
    pytest.importorskip("numba")
    for backend in ("generational", "generational-vector", "auto"):
        other = fim.simulate(spec, execution=fim.Execution(backend=backend))
        assert other.run_id == reference.run_id
        assert [r.report.to_dict() for r in other.replicates] == [
            r.report.to_dict() for r in reference.replicates
        ]
        assert other.provenance.requested.backend == backend


def test_a_run_config_supplies_its_own_execution_unless_overridden() -> None:
    """Keyword arguments win over what a configuration file asked for."""
    config = fim.RunConfig(
        spec=_spec(), execution=fim.Execution(backend="generational")
    )
    assert fim.simulate(config).provenance.backend == "generational"
    overridden = fim.simulate(config, execution=fim.Execution(backend="lineal"))
    assert overridden.provenance.backend == "lineal"


def test_recording_to_a_directory_is_refused_until_it_is_supported() -> None:
    """The error says so, rather than silently keeping the run in memory."""
    with pytest.raises(ConfigurationError, match=r"recording\.directory"):
        fim.simulate(_spec(), recording=fim.Recording(directory="somewhere"))
    with pytest.raises(ConfigurationError, match=r"execution\.workers"):
        fim.simulate(_spec(), execution=fim.Execution(workers=2))


def test_an_engine_rejection_is_a_configuration_error() -> None:
    """Cross-field rules surface with the engine's message."""
    spec = _spec(
        model=fim.Model(
            demes=2,
            population=fim.Population(individuals=3, ploidy=1),
            migration=0.1,
            mutation=fim.InfiniteAlleles(rate=0.01),
            initial=fim.FoundingAlleles(count=9),
        )
    )
    with pytest.raises(ConfigurationError, match="initial_allele_count"):
        fim.simulate(spec)


def test_a_missing_dependency_is_an_unsupported_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid spec that no available engine can run says so, as a ValueError."""
    monkeypatch.setattr(engine_backends, "numba_is_available", lambda: False)
    with pytest.raises(UnsupportedError) as caught:
        fim.simulate(_spec(), execution=fim.Execution(backend="generational-vector"))
    assert isinstance(caught.value, ValueError)
    assert isinstance(caught.value, FimError)


def test_the_facade_loads_lazily_and_every_name_resolves() -> None:
    """`import fim` stays light; each exported name is a real object."""
    probe = (
        "import sys, fim\n"
        "assert 'fim.engine' not in sys.modules, 'engine loaded at import'\n"
        "assert callable(fim.simulate)\n"
        "assert 'fim.engine' in sys.modules\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    for name in fim.__all__:
        assert getattr(fim, name) is not None, name
    assert {"simulate", "RunSpec", "Result"} <= set(dir(fim))
    with pytest.raises(AttributeError, match="no attribute"):
        fim.nonexistent  # noqa: B018


@pytest.mark.parametrize(
    ("error", "bases"),
    [
        (ConfigurationError, (FimError, ValueError)),
        (UnsupportedError, (FimError, ValueError)),
        (StoreError, (FimError,)),
        (UnsupportedSessionError, (UnsupportedError, ValueError)),
        (VectorMemoryCeilingError, (UnsupportedError, RuntimeError)),
        (SessionRestoreError, (StoreError, ValueError)),
        (TlogError, (StoreError, ValueError)),
        (SnapshotError, (StoreError, ValueError)),
        (ReadOnlyError, (StoreError, ValueError)),
        (ClosedGroupError, (StoreError, ValueError)),
        (GroupBusyError, (FimError, ValueError)),
        (LifecycleError, (FimError,)),
        (LockUnavailableError, (FimError,)),
    ],
)
def test_every_package_exception_is_a_fim_error(
    error: type[BaseException], bases: tuple[type, ...]
) -> None:
    """One root catches everything; ValueError compatibility is kept."""
    assert issubclass(error, FimError)
    for base in bases:
        assert issubclass(error, base)
