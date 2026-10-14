"""The benchmark tool's sweep axes reach the parameters they name.

Nothing here times anything: `_median_of` and `_run_once` are replaced by
recorders, so the test asserts which `SimulationParams` each backend would
have been handed, which is a pure function of the commit.
"""

from __future__ import annotations

import sys
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from types import ModuleType

import pytest

from fim.model.params import SimulationParams

SCRIPT = Path(__file__).resolve().parents[2] / "dev" / "bin" / "benchmark-engines"


def _tool() -> ModuleType:
    """Load the extension-less `benchmark-engines` script as a module."""
    loader = SourceFileLoader("benchmark_engines_under_test", str(SCRIPT))
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    # Its dataclasses resolve annotations through `sys.modules` while it runs.
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


def _measure(
    monkeypatch: pytest.MonkeyPatch, field: str, value: float
) -> list[SimulationParams]:
    """Run `_measure_point` with recorders and return every params it timed."""
    tool = _tool()
    seen: list[SimulationParams] = []

    def record(
        _backend: str, params: SimulationParams, _trials: int = 1, **_: object
    ) -> float:
        seen.append(params)
        return 1.0

    monkeypatch.setattr(tool, "_median_of", record)

    def run_once(_backend: str, params: SimulationParams, **_: object) -> float:
        seen.append(params)
        return 1.0

    monkeypatch.setattr(tool, "_run_once", run_once)
    tool._measure_point(
        field,
        value,
        baseline=tool._load_fixed_baseline(None),
        replicates=8,
        generations=2,
        trials=1,
        seed=1,
        warmed_up_generational_numba=[True],
        warmed_up_vector=[True],
    )
    return seen


def test_the_window_axis_is_a_named_sweep_field() -> None:
    assert "max-concurrent-replicates" in _tool().SWEEP_FIELDS


def test_the_window_axis_sets_the_lane_window_on_the_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _measure(monkeypatch, "max-concurrent-replicates", 4)

    batches = [params for params in seen if params.n_replicates == 8]
    assert batches
    assert {params.max_concurrent_replicates for params in batches} == {4}
    # The single-lineage reference is never windowed.
    assert {p.max_concurrent_replicates for p in seen if p.n_replicates == 1} == {None}


def test_every_other_axis_leaves_the_window_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _measure(monkeypatch, "d", 6)

    assert {params.max_concurrent_replicates for params in seen} == {None}


def test_the_rss_worker_receives_the_lane_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = _tool()
    commands: list[list[str]] = []

    class Done:
        returncode = 0
        stdout = "1"

    def fake_run(command: list[str], **_: object) -> Done:
        commands.append(command)
        return Done()

    monkeypatch.setattr(tool.subprocess, "run", fake_run)
    params = tool._base_params(
        n_replicates=8, max_generations=2, seed=1, d=4, N=10, mu=0.01, m=0.1
    )
    tool._measure_rss("generational", tool.replace(params, max_concurrent_replicates=3))

    command = commands[0]
    assert command[command.index("--max-concurrent-replicates") + 1] == "3"


def test_the_discarding_store_keeps_no_rows() -> None:
    store = _tool()._DiscardingStore()

    store.write_generation("run", 0, iter([{"run_id": "run"}]))

    assert list(store.read("run")) == []
    assert store.equilibrium_store("run") is store
