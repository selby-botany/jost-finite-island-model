"""The benchmark tools share the sweep layer's axes and grid, nothing else.

`benchmark-engines` reads its `--values` through `fim.sweep.expand_axis` and
`generate-heatmap-queue` takes its (locus length, d) grid from
`fim.sweep.enumerate_values`. These tests pin that: every benchmark axis is a
registry key, the values come out exactly as they did when the tool parsed
them by hand, a value that does not fit an axis is refused with the sweep
layer's own message, and the heatmap queue has the jobs it always had.
"""

from __future__ import annotations

import json
import sys
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from types import ModuleType

import pytest

from fim.sweep import (
    SWEEPABLE_KEYS,
    SweepSpec,
    enumerate_values,
    expand_axis,
    sweepable_key_names,
)

_BIN = Path(__file__).resolve().parents[2] / "dev" / "bin"


def _load(name: str) -> ModuleType:
    """Load an extension-less script of `dev/bin` as a module."""
    loader = SourceFileLoader(f"{name.replace('-', '_')}_grid_test", str(_BIN / name))
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


def test_every_benchmark_axis_is_a_sweep_axis() -> None:
    tool = _load("benchmark-engines")

    assert set(tool.SWEEP_AXIS_KEYS) == set(tool.SWEEP_FIELDS)
    assert set(tool.SWEEP_AXIS_KEYS.values()) <= set(sweepable_key_names())
    assert {"d", "N", "mu", "m"} <= set(SWEEPABLE_KEYS)


@pytest.mark.parametrize(
    ("field", "text", "expected"),
    [
        ("d", "4,10,20,35,50,80,120", [4, 10, 20, 35, 50, 80, 120]),
        ("N", "100,500,2000", [100, 500, 2000]),
        ("mu", "0.0001,0.001,0.01", [0.0001, 0.001, 0.01]),
        ("m", "0.01,0.05", [0.01, 0.05]),
        ("loci-length", "3,4,5", [3, 4, 5]),
        ("replicates", "2,16", [2, 16]),
        ("max-concurrent-replicates", "1,4", [1, 4]),
    ],
)
def test_values_expand_to_what_the_tool_always_swept(
    field: str, text: str, expected: list[float]
) -> None:
    tool = _load("benchmark-engines")

    values = tool._sweep_values(field, text)

    assert values == expected
    assert values == [float(v) for v in text.split(",")]  # equal to the old parse


@pytest.mark.parametrize(
    ("field", "text", "message"),
    [
        ("d", "4,2.5", "whole numbers"),
        ("d", "1,4", "at least 2"),
        ("d", "4,4", "more than once"),
        ("replicates", "0,4", "at least 1"),
        ("m", "0.1,2", "at most 1"),
        ("N", "ten", "could not convert"),
    ],
)
def test_a_value_that_does_not_fit_an_axis_is_refused_up_front(
    field: str, text: str, message: str
) -> None:
    tool = _load("benchmark-engines")

    with pytest.raises(ValueError, match=message):
        tool._sweep_values(field, text)


def test_the_heatmap_queue_has_one_job_per_length_each_sweeping_every_d(
    tmp_path: Path,
) -> None:
    tool = _load("generate-heatmap-queue")

    queue = tool._build_queue(
        d_values=(10, 35, 70),
        length_values=(5, 6),
        out_dir=tmp_path,
        replicates=4,
        generations=20,
        trials=1,
        seed=1,
        fixed_n=500,
        fixed_m=0.05,
        fixed_mu=0.001,
    )

    (stage,) = queue["stages"]
    assert [job["label"] for job in stage] == ["heatmap-len5", "heatmap-len6"]
    for job in stage:
        command = job["command"]
        assert command[command.index("--values") + 1] == "10,35,70"
        assert command[command.index("--sweep") + 1] == "d"
    assert json.dumps(queue)  # serializable as before


def test_the_grid_layer_yields_the_positions_without_building_a_configuration() -> None:
    spec = SweepSpec(
        base={},
        axes=(expand_axis("locus_lengths", [5, 6]), expand_axis("d", [10, 35])),
    )

    assert list(enumerate_values(spec)) == [
        {"locus_lengths": 5, "d": 10},
        {"locus_lengths": 5, "d": 35},
        {"locus_lengths": 6, "d": 10},
        {"locus_lengths": 6, "d": 35},
    ]
    zipped = SweepSpec(
        base={},
        axes=(expand_axis("d", [2, 4]), expand_axis("N", [10, 20])),
        strategy="zip",
    )
    assert list(enumerate_values(zipped)) == [{"d": 2, "N": 10}, {"d": 4, "N": 20}]
