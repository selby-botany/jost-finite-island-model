"""Unit tests for `GuiProgressStore` and `RunCancelledError`.

No display, no Tk import, no thread — the decorator's contract is
exercised against an `InMemoryTrajectoryStore` fake, one method call at
a time (design doc §6.3).
"""

from __future__ import annotations

import threading

import pytest

from fim.gui.store import GuiProgressStore, RunCancelledError
from fim.persistence.store import InMemoryTrajectoryStore


def _rows(generation: int) -> list[dict[str, object]]:
    """Build one minimal, valid trajectory row for the given generation."""
    return [
        {
            "run_id": "run-1",
            "generation": generation,
            "deme": 1,
            "locus_id": 1,
            "allele_id": 0,
            "frequency": 1.0,
        }
    ]


def test_gui_progress_store_calls_on_generation_once_per_write() -> None:
    """Every non-cancelled write reports exactly its own generation, in order."""
    reported: list[int] = []
    store = GuiProgressStore(
        InMemoryTrajectoryStore(),
        on_generation=reported.append,
        cancel_event=threading.Event(),
    )

    store.write_generation("run-1", 0, _rows(0))
    store.write_generation("run-1", 1, _rows(1))

    assert reported == [0, 1]


def test_gui_progress_store_delegates_to_the_inner_store() -> None:
    """A non-cancelled write reaches the wrapped store, not just the callback."""
    inner = InMemoryTrajectoryStore()
    store = GuiProgressStore(
        inner, on_generation=lambda _generation: None, cancel_event=threading.Event()
    )

    store.write_generation("run-1", 0, _rows(0))

    assert [row["generation"] for row in inner.read("run-1")] == [0]


def test_gui_progress_store_raises_run_cancelled_when_event_is_set() -> None:
    """A set cancel event turns the next write into `RunCancelledError`, not a write."""
    cancel_event = threading.Event()
    cancel_event.set()
    store = GuiProgressStore(
        InMemoryTrajectoryStore(),
        on_generation=lambda _generation: None,
        cancel_event=cancel_event,
    )

    with pytest.raises(RunCancelledError) as exc_info:
        store.write_generation("run-1", 7, _rows(7))

    assert exc_info.value.run_id == "run-1"
    assert exc_info.value.generation == 7


def test_gui_progress_store_never_delegates_after_cancellation() -> None:
    """A cancelled write reaches neither the inner store nor `on_generation`."""
    inner = InMemoryTrajectoryStore()
    reported: list[int] = []
    cancel_event = threading.Event()
    cancel_event.set()
    store = GuiProgressStore(
        inner, on_generation=reported.append, cancel_event=cancel_event
    )

    with pytest.raises(RunCancelledError):
        store.write_generation("run-1", 0, _rows(0))

    assert not reported
    assert not list(inner.read("run-1"))


def test_gui_progress_store_read_delegates_to_the_inner_store() -> None:
    """`read` is a pure passthrough — nothing about it needs decorating."""
    inner = InMemoryTrajectoryStore()
    inner.write_generation("run-1", 0, _rows(0))
    store = GuiProgressStore(
        inner, on_generation=lambda _generation: None, cancel_event=threading.Event()
    )

    assert [row["generation"] for row in store.read("run-1")] == [0]
