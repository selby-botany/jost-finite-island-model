"""Tests of `BurnInMonitor.export_state` and `restore_state`.

A checkpoint must let a run resume as though it had never stopped, so every
test here splits a fixed, seeded series at some generation, moves the
monitor's state into a freshly built monitor (through JSON and `numpy.savez`
where it matters), and requires the continuation to match an uninterrupted
monitor exactly: the same stop generation, reason, histories, and window.
"""

from __future__ import annotations

import io
import json
import math
import random
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest

from fim.convergence.monitor import BurnInMonitor, StopReason

STATISTICS = ("F_ST", "H_S")


def _monitor(**changes: object) -> BurnInMonitor:
    """A two-statistic monitor with small, exact settings."""
    settings: dict[str, object] = {
        "max_generations": 1_500,
        "precision": 0.01,
        "burn_in": 10,
        "first_check": 20,
        "minimum_effective_sample_size": 10.0,
        "statistics": STATISTICS,
        "extra_statistics": ("H_T",),
    }
    settings.update(changes)
    return BurnInMonitor(**settings)  # type: ignore[arg-type]


def _series(length: int, seed: int) -> list[dict[str, float]]:
    """A seeded AR(1) series per statistic; `H_S` is undefined every 7th step."""
    rng = random.Random(seed)
    level = {"F_ST": 0.3, "H_S": 0.6, "H_T": 0.7}
    value = dict(level)
    out: list[dict[str, float]] = []
    sigma = 0.05 * math.sqrt(1.0 - 0.8 * 0.8)
    for generation in range(length):
        row = dict(value)
        if generation % 7 == 3:
            del row["H_S"]
        out.append(row)
        value = {
            name: level[name] + 0.8 * (v - level[name]) + rng.gauss(0.0, sigma)
            for name, v in value.items()
        }
    return out


def _feed(
    monitor: BurnInMonitor, series: list[dict[str, float]], start: int, stop: int
) -> None:
    """Record `series[start:stop]` until the monitor stops."""
    for generation in range(start, stop):
        if monitor.should_stop():
            return
        monitor.record(generation, series[generation])


def _through_files(
    meta: dict[str, object], arrays: dict[str, npt.NDArray[np.generic]]
) -> tuple[dict[str, Any], dict[str, npt.NDArray[np.generic]]]:
    """Round-trip exported state through JSON text and an `.npz` archive."""
    buffer = io.BytesIO()
    np.savez(buffer, **arrays)  # type: ignore[arg-type]
    buffer.seek(0)
    with np.load(buffer, allow_pickle=False) as archive:
        loaded = {name: archive[name] for name in archive.files}
    return json.loads(json.dumps(meta, sort_keys=True)), loaded


def _snapshot(monitor: BurnInMonitor) -> tuple[object, ...]:
    """Every observable a continuation must reproduce."""
    return (
        monitor.outcome(),
        monitor.generations,
        monitor.histories,
        monitor.value_generations,
        monitor.window_start_generation,
        monitor.projected_generations,
        monitor.stable_statistics(),
        monitor.averaging_window,
    )


@pytest.mark.parametrize("split", [0, 1, 15, 30, 31, 47, 64, 100])
def test_a_restored_monitor_continues_exactly(split: int) -> None:
    """Split anywhere, including before, at, and after checks."""
    series = _series(2_000, seed=11)
    reference = _monitor()
    _feed(reference, series, 0, len(series))
    assert reference.outcome().stopped
    original = _monitor()
    _feed(original, series, 0, split)
    meta, arrays = _through_files(*original.export_state())
    restored = _monitor()
    restored.restore_state(meta, arrays)
    assert _snapshot(restored) == _snapshot(original)
    _feed(restored, series, split, len(series))
    assert _snapshot(restored) == _snapshot(reference)


def test_a_stopped_monitor_restores_as_stopped() -> None:
    """A checkpoint taken at the stop still refuses further records."""
    series = _series(2_000, seed=5)
    original = _monitor()
    _feed(original, series, 0, len(series))
    restored = _monitor()
    restored.restore_state(*_through_files(*original.export_state()))
    assert restored.should_stop()
    assert _snapshot(restored) == _snapshot(original)
    with pytest.raises(RuntimeError, match="cannot record"):
        restored.record(10_000, series[0])


def test_a_capped_fractional_monitor_continues_exactly() -> None:
    """No burn-in and a cap: the fractional window start survives restore."""
    series = _series(300, seed=3)
    settings = {"burn_in": None, "max_generations": 120, "precision": 1e-9}
    reference = _monitor(**settings)
    _feed(reference, series, 0, len(series))
    assert reference.reason() is StopReason.MAX_GENERATIONS
    original = _monitor(**settings)
    _feed(original, series, 0, 77)
    restored = _monitor(**settings)
    restored.restore_state(*_through_files(*original.export_state()))
    _feed(restored, series, 77, len(series))
    assert _snapshot(restored) == _snapshot(reference)


def test_an_awaiting_window_monitor_takes_its_window_after_restore() -> None:
    """A batch replicate checkpointed before its window was known."""
    series = _series(200, seed=8)
    reference = _monitor(awaiting_window=True)
    _feed(reference, series, 0, 40)
    reference.set_averaging_window(25)
    _feed(reference, series, 40, len(series))
    original = _monitor(awaiting_window=True)
    _feed(original, series, 0, 40)
    restored = _monitor(awaiting_window=True)
    restored.restore_state(*_through_files(*original.export_state()))
    assert restored.awaiting_window
    restored.set_averaging_window(25)
    _feed(restored, series, 40, len(series))
    assert _snapshot(restored) == _snapshot(reference)
    assert reference.reason() is StopReason.AVERAGING_COMPLETE


def test_an_assigned_window_survives_restore() -> None:
    """A window assigned before the checkpoint is part of the state."""
    series = _series(200, seed=9)
    reference = _monitor(awaiting_window=True)
    reference.set_averaging_window(50)
    _feed(reference, series, 0, len(series))
    original = _monitor(awaiting_window=True)
    original.set_averaging_window(50)
    _feed(original, series, 0, 33)
    restored = _monitor(awaiting_window=True)
    restored.restore_state(*_through_files(*original.export_state()))
    assert restored.averaging_window == 50
    _feed(restored, series, 33, len(series))
    assert _snapshot(restored) == _snapshot(reference)


def test_exported_metadata_is_json_safe_and_arrays_are_typed() -> None:
    """Metadata survives canonical JSON; arrays are int64 or float64 only."""
    monitor = _monitor()
    _feed(monitor, _series(50, seed=1), 0, 50)
    meta, arrays = monitor.export_state()
    assert json.loads(json.dumps(meta)) == meta
    assert {array.dtype for array in arrays.values()} == {
        np.dtype(np.int64),
        np.dtype(np.float64),
    }


def test_restore_refuses_a_used_monitor() -> None:
    """State is only ever restored into a fresh monitor."""
    original = _monitor()
    _feed(original, _series(20, seed=2), 0, 20)
    used = _monitor()
    used.record(0, {"F_ST": 0.1, "H_S": 0.1, "H_T": 0.1})
    with pytest.raises(RuntimeError, match="fresh"):
        used.restore_state(*original.export_state())


def _exported() -> tuple[dict[str, Any], dict[str, npt.NDArray[np.generic]]]:
    """State of a monitor partway through a run, as loaded from files."""
    monitor = _monitor()
    _feed(monitor, _series(60, seed=4), 0, 60)
    return _through_files(*monitor.export_state())


def _break_meta(key: str, value: object) -> dict[str, Any]:
    """Exported metadata with one field replaced."""
    meta, _ = _exported()
    meta[key] = value
    return meta


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("statistics", ["F_ST"], "different statistics"),
        ("last_verdicts", [True], "last_verdicts"),
        ("last_verdicts", [1, 0], "last_verdicts"),
        ("next_check", "30", "next_check"),
        ("next_check", True, "next_check"),
        ("cap_warned", None, "cap_warned"),
        ("window_start", 1.5, "window_start"),
        ("projection", "x", "projection"),
        ("averaging_window", 10, "window-mode"),
        ("outcome", [], "outcome"),
        (
            "outcome",
            {"stopped": True, "converged": True, "reason": "x", "generation": 4},
            "unknown",
        ),
        (
            "outcome",
            {"stopped": True, "converged": True, "reason": None, "generation": 4},
            "outcome",
        ),
    ],
)
def test_malformed_metadata_is_refused(key: str, value: object, message: str) -> None:
    """Each malformed field is named in the error."""
    _, arrays = _exported()
    with pytest.raises(ValueError, match=message):
        _monitor().restore_state(_break_meta(key, value), arrays)


@pytest.mark.parametrize(
    ("key", "replacement", "message"),
    [
        ("monitor.generations", None, "monitor.generations"),
        ("monitor.generations", np.array([0, 2, 1]), "strictly increasing"),
        ("monitor.generations", np.zeros(3, dtype=np.float64), "malformed"),
        ("monitor.values.0", None, "values for F_ST"),
        ("monitor.values.1", np.zeros(2, dtype=np.int64), "values for H_S"),
        ("monitor.value_generations.2", np.array([0]), "generations for H_T"),
        (
            "monitor.value_generations.0",
            np.arange(60, dtype=np.int64) + 1000,
            "generations for F_ST",
        ),
    ],
)
def test_malformed_arrays_are_refused(
    key: str, replacement: npt.NDArray[np.generic] | None, message: str
) -> None:
    """A missing, mistyped, or inconsistent history array is refused."""
    meta, arrays = _exported()
    if replacement is None:
        del arrays[key]
    else:
        arrays[key] = replacement
    with pytest.raises(ValueError, match=message):
        _monitor().restore_state(meta, arrays)
