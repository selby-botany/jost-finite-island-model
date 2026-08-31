"""Unit tests for `GuiProgressStore`, `LiveProgressStore`, and `RunCancelledError`.

No display, no Tk import, no thread and no real subprocess — each
decorator's contract is exercised against an `InMemoryTrajectoryStore`
fake (or, for `LiveProgressStore`, plain files under `tmp_path`), one
method call at a time (`doc/fim-gui-design.md` §7).
"""

from __future__ import annotations

import pickle
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fim.gui.store import (
    GuiProgressStore,
    LiveProgressStore,
    RunCancelledError,
    read_live_state,
    read_progress_sidecar,
    write_progress_sidecar,
)
from fim.model.locus import LocusSpec
from fim.model.state import ModelState
from fim.persistence.jsonl_store import JSONLTrajectoryStore
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
        on_generation=lambda generation, _rows: reported.append(generation),
        cancel_event=threading.Event(),
    )

    store.write_generation("run-1", 0, _rows(0))
    store.write_generation("run-1", 1, _rows(1))

    assert reported == [0, 1]


def test_gui_progress_store_passes_the_generation_own_rows_to_on_generation() -> None:
    """`on_generation` receives that generation's real rows, not just its number.

    Direct regression test: the scalar run screen's live
    scatter needs the actual frequency data, and there is no trajectory
    file path the caller could otherwise re-read it from (the temporary
    working directory `fim.paths.atomic_directory` builds is private to
    `runner.py`'s own worker).
    """
    reported: list[list[Mapping[str, object]]] = []
    store = GuiProgressStore(
        InMemoryTrajectoryStore(),
        on_generation=lambda _generation, rows: reported.append(list(rows)),
        cancel_event=threading.Event(),
    )

    store.write_generation("run-1", 3, _rows(3))

    assert reported == [_rows(3)]


def test_gui_progress_store_materializes_a_one_shot_rows_iterator() -> None:
    """A one-shot iterator (not just a `list`) still reaches `on_generation` intact.

    `write_generation`'s own `rows: Iterable[...]` type is broader than
    "always a `list`" — this proves the decorator does not silently
    depend on every caller happening to pass a re-iterable one.
    """
    reported: list[list[Mapping[str, object]]] = []
    store = GuiProgressStore(
        InMemoryTrajectoryStore(),
        on_generation=lambda _generation, rows: reported.append(list(rows)),
        cancel_event=threading.Event(),
    )

    store.write_generation("run-1", 0, iter(_rows(0)))

    assert reported == [_rows(0)]


def test_gui_progress_store_delegates_to_the_inner_store() -> None:
    """A non-cancelled write reaches the wrapped store, not just the callback."""
    inner = InMemoryTrajectoryStore()
    store = GuiProgressStore(
        inner,
        on_generation=lambda _generation, _rows: None,
        cancel_event=threading.Event(),
    )

    store.write_generation("run-1", 0, _rows(0))

    assert [row["generation"] for row in inner.read("run-1")] == [0]


def test_gui_progress_store_raises_run_cancelled_when_event_is_set() -> None:
    """A set cancel event turns the next write into `RunCancelledError`, not a write."""
    cancel_event = threading.Event()
    cancel_event.set()
    store = GuiProgressStore(
        InMemoryTrajectoryStore(),
        on_generation=lambda _generation, _rows: None,
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
        inner,
        on_generation=lambda generation, _rows: reported.append(generation),
        cancel_event=cancel_event,
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
        inner,
        on_generation=lambda _generation, _rows: None,
        cancel_event=threading.Event(),
    )

    assert [row["generation"] for row in store.read("run-1")] == [0]


def test_live_progress_store_writes_a_progress_sidecar_every_generation(
    tmp_path: Path,
) -> None:
    """Every non-cancelled write updates the sidecar to that generation."""
    progress_path = tmp_path / ".progress"
    store = LiveProgressStore(
        InMemoryTrajectoryStore(),
        progress_path=progress_path,
        cancel_path=tmp_path / "cancel",
    )

    store.write_generation("run-1", 0, _rows(0))
    first = read_progress_sidecar(progress_path)
    assert first is not None
    assert first["generation"] == 0
    assert "written_at" in first

    store.write_generation("run-1", 1, _rows(1))
    second = read_progress_sidecar(progress_path)
    assert second is not None
    assert second["generation"] == 1


def test_live_progress_store_delegates_to_the_inner_store(tmp_path: Path) -> None:
    """A non-cancelled write reaches the wrapped store, not just the sidecar."""
    inner = InMemoryTrajectoryStore()
    store = LiveProgressStore(
        inner,
        progress_path=tmp_path / ".progress",
        cancel_path=tmp_path / "cancel",
    )

    store.write_generation("run-1", 0, _rows(0))

    assert [row["generation"] for row in inner.read("run-1")] == [0]


def test_live_progress_store_raises_when_the_shared_cancel_file_exists(
    tmp_path: Path,
) -> None:
    """A cancel file's mere existence turns the next write into `RunCancelledError`.

    Direct regression test for the cross-process cancellation contract:
    unlike `GuiProgressStore`'s `threading.Event`,
    the signal here is a plain file another process created — its
    *content* is never inspected, only whether it exists.
    """
    cancel_path = tmp_path / "cancel"
    cancel_path.touch()
    store = LiveProgressStore(
        InMemoryTrajectoryStore(),
        progress_path=tmp_path / ".progress",
        cancel_path=cancel_path,
    )

    with pytest.raises(RunCancelledError) as exc_info:
        store.write_generation("run-1", 7, _rows(7))

    assert exc_info.value.run_id == "run-1"
    assert exc_info.value.generation == 7


def test_live_progress_store_never_delegates_after_cancellation(
    tmp_path: Path,
) -> None:
    """A cancelled write reaches neither the inner store nor the sidecar."""
    inner = InMemoryTrajectoryStore()
    progress_path = tmp_path / ".progress"
    cancel_path = tmp_path / "cancel"
    cancel_path.touch()
    store = LiveProgressStore(
        inner, progress_path=progress_path, cancel_path=cancel_path
    )

    with pytest.raises(RunCancelledError):
        store.write_generation("run-1", 0, _rows(0))

    assert not list(inner.read("run-1"))
    assert read_progress_sidecar(progress_path) is None


def test_live_progress_store_read_delegates_to_the_inner_store(
    tmp_path: Path,
) -> None:
    """`read` is a pure passthrough — nothing about it needs decorating."""
    inner = InMemoryTrajectoryStore()
    inner.write_generation("run-1", 0, _rows(0))
    store = LiveProgressStore(
        inner, progress_path=tmp_path / ".progress", cancel_path=tmp_path / "cancel"
    )

    assert [row["generation"] for row in store.read("run-1")] == [0]


def test_live_progress_store_is_picklable(tmp_path: Path) -> None:
    """`LiveProgressStore` survives a real pickle round trip.

    The exact property `fim.engine._require_picklable` checks on a
    `store_factory` before ever spawning a `ProcessPoolExecutor` worker
    — a `LiveProgressStore` built inside one worker never itself crosses
    the process boundary, but this proves the class *could*, and is a
    much faster, more direct signal than discovering a pickling failure
    three layers away inside a real subprocess.
    """
    store = LiveProgressStore(
        InMemoryTrajectoryStore(),
        progress_path=tmp_path / ".progress",
        cancel_path=tmp_path / "cancel",
    )

    restored = pickle.loads(pickle.dumps(store))

    restored.write_generation("run-1", 0, _rows(0))
    assert read_progress_sidecar(tmp_path / ".progress") is not None


def test_read_progress_sidecar_returns_none_for_a_missing_file(tmp_path: Path) -> None:
    """A not-yet-started replicate has no sidecar at all — not an error."""
    assert read_progress_sidecar(tmp_path / "never-written") is None


def test_write_progress_sidecar_records_a_real_wall_clock_timestamp(
    tmp_path: Path,
) -> None:
    """The sidecar's timestamp brackets the actual write, for concurrency proofs.

    Not a race-prone timing assertion (project CLAUDE.md's determinism
    contract) — a generous bound proving the recorded timestamp is a
    real observation of *this* write, which is what
    `test_batch_replicates_actually_run_concurrently`-style tests rely
    on to prove real concurrency structurally.
    """
    progress_path = tmp_path / ".progress"
    before = datetime.now(UTC)

    write_progress_sidecar(progress_path, 3)

    after = datetime.now(UTC)
    sidecar = read_progress_sidecar(progress_path)
    assert sidecar is not None
    written_at = sidecar["written_at"]
    # `datetime.fromisoformat` (not a hand-rolled `%f`-style format
    # string): `datetime.isoformat()` omits the fractional-second
    # component entirely when it is exactly zero, so a fixed strptime
    # format would intermittently fail to parse a genuinely valid
    # timestamp — the same class of non-determinism this project's own
    # rules single out.
    parsed = datetime.fromisoformat(written_at.replace("Z", "+00:00"))
    assert before <= parsed <= after


def test_read_live_state_reconstructs_a_sidecar_confirmed_generation(
    tmp_path: Path,
) -> None:
    """A generation already written and flushed is safely readable mid-run."""
    trajectory_path = tmp_path / "trajectory.jsonl"
    store = JSONLTrajectoryStore(trajectory_path)
    store.write_generation("run-1", 0, _rows(0))
    store.write_generation("run-1", 1, _rows(1))

    state = read_live_state(trajectory_path, "run-1", 1, (LocusSpec(1, 200),))

    assert state == ModelState.from_rows(_rows(1), (LocusSpec(1, 200),))


def test_read_live_state_returns_none_for_a_not_yet_created_trajectory(
    tmp_path: Path,
) -> None:
    """A replicate that has not created its own directory yet is not an error."""
    never_written = tmp_path / "trajectory.jsonl"

    assert read_live_state(never_written, "run-1", 0, (LocusSpec(1, 200),)) is None


def test_read_live_state_returns_none_for_a_generation_not_yet_written(
    tmp_path: Path,
) -> None:
    """Asking for a generation ahead of what has been persisted is not an error.

    Guards against a real, if narrow, race: a caller reading a slightly
    stale `.progress` sidecar snapshot (generation N) against a
    trajectory that has already advanced past it is safe (the "not yet
    written" case never actually applies there); this is the true
    "haven't gotten there yet" case instead.
    """
    trajectory_path = tmp_path / "trajectory.jsonl"
    JSONLTrajectoryStore(trajectory_path).write_generation("run-1", 0, _rows(0))

    result = read_live_state(trajectory_path, "run-1", 5, (LocusSpec(1, 200),))

    assert result is None


def test_read_live_state_returns_none_for_a_different_run_id(tmp_path: Path) -> None:
    """Rows from a different run id are never mistaken for this replicate's own."""
    trajectory_path = tmp_path / "trajectory.jsonl"
    JSONLTrajectoryStore(trajectory_path).write_generation("run-1", 0, _rows(0))

    result = read_live_state(trajectory_path, "run-2", 0, (LocusSpec(1, 200),))

    assert result is None
