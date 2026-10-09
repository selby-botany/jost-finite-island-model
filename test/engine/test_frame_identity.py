"""The engine hands a store the same generation whichever way it is spelled.

Stage 1 of the trajectory writer: a store may take a generation as a
`TrajectoryFrame` instead of rows. Three things must hold, and each is
checked on the complete stream of a real run, never on a summary:

1. Backends L, G and V produce identical frames (counts, allele ids and
   frequency bits, every generation) and the same layout.
2. A frame equals the frame rebuilt from the rows the engine writes on
   the rows path, so the two paths cannot disagree.
3. A JSON Lines store fed frames writes the same bytes as one fed rows,
   including the ancestral companion of an equilibrium-split run.

No test depends on timing; every run is seeded.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from fim.engine import fim
from fim.model.params import SimulationParams
from fim.persistence.frame import (
    FrameLayout,
    TrajectoryFrame,
    frame_to_rows,
    rows_to_frame,
)
from fim.persistence.jsonl_store import JSONLTrajectoryStore
from fim.persistence.store import InMemoryTrajectoryStore, TrajectoryStore

RUN_ID = "run-frames"


class _FrameRecorder:
    """A store that wants frames and keeps a copy of each one it is given."""

    def __init__(self) -> None:
        """Start empty."""
        self.layouts: dict[str, FrameLayout] = {}
        self.frames: dict[str, list[TrajectoryFrame]] = {}
        self.companion: _FrameRecorder | None = None

    def begin_run(self, run_id: str, layout: FrameLayout) -> None:
        """Record the layout, refusing a second, different one."""
        assert self.layouts.setdefault(run_id, layout) == layout

    def wants_frames(self, run_id: str) -> bool:
        """Ask for frames."""
        del run_id
        return True

    def write_frame(self, run_id: str, frame: TrajectoryFrame) -> None:
        """Keep a copy (a frame is a view of a backend's own arrays)."""
        assert run_id in self.layouts, "begin_run must come first"
        self.frames.setdefault(run_id, []).append(
            TrajectoryFrame(
                generation=frame.generation,
                counts=frame.counts.copy(),
                allele_ids=frame.allele_ids.copy(),
                frequencies=frame.frequencies.copy(),
            )
        )

    def write_generation(self, *_args: object, **_kwargs: object) -> None:
        """Fail: a store that wants frames must not be given rows."""
        raise AssertionError("rows were written to a frame-native store")

    def read(self, run_id: str) -> object:
        """Unused."""
        raise NotImplementedError

    def discard(self, run_id: str) -> None:
        """Unused."""

    def equilibrium_store(self, run_id: str) -> _FrameRecorder:
        """Return the ancestral-phase recorder."""
        del run_id
        if self.companion is None:
            self.companion = _FrameRecorder()
        return self.companion


class _FrameJsonlStore(JSONLTrajectoryStore):
    """A JSON Lines store that asks the engine for frames."""

    def wants_frames(self, run_id: str) -> bool:
        """Ask for frames, so the engine takes the frame path."""
        del run_id
        return True

    def equilibrium_store(self, run_id: str) -> JSONLTrajectoryStore:
        """Return a frame-asking companion beside this file."""
        del run_id
        with self._lock:
            if self._equilibrium is None:
                self._equilibrium = _FrameJsonlStore(
                    self.path.with_name("equilibrium_trajectory.jsonl")
                )
            return self._equilibrium


def _params(**updates: object) -> SimulationParams:
    """Return a small run with several loci, demes and mutations."""
    config: dict[str, object] = {
        "N": 30,
        "ploidy": "haploid",
        "m": 0.1,
        "mu": 0.02,
        "d": 3,
        "seed": 20261009,
        "loci": [{"locus_id": 4, "length": 200}, {"locus_id": 9, "length": 200}],
        "initial_allele_count": 3,
        "convergence_window": 4,
        "convergence_tolerance": 1e-12,
        "max_generations": 30,
        "n_replicates": 1,
        "replicate_tolerance": None,
    }
    config.update(updates)
    return SimulationParams.from_mapping(config)


def _frames_of(params: SimulationParams, backend: str) -> _FrameRecorder:
    """Run `params` on `backend` into a frame recorder and return it."""
    recorder = _FrameRecorder()
    fim(
        params.gene_copies,
        params.m,
        params.mu,
        params.d,
        params=params,
        store=recorder,  # type: ignore[arg-type]
        run_id=RUN_ID,
        engine_backend=backend,  # type: ignore[arg-type]
    )
    return recorder


def _same_frames(first: list[TrajectoryFrame], second: list[TrajectoryFrame]) -> None:
    """Require two frame streams to be equal, bit for bit."""
    assert [f.generation for f in first] == [f.generation for f in second]
    for left, right in zip(first, second, strict=True):
        assert np.array_equal(left.counts, right.counts)
        assert np.array_equal(left.allele_ids, right.allele_ids)
        assert left.frequencies.tobytes() == right.frequencies.tobytes()


CASES: dict[str, Callable[[], SimulationParams]] = {
    "infinite alleles": _params,
    "unequal sizes and a matrix": lambda: _params(
        N=[20, 30, 40],
        m=[[0.8, 0.1, 0.1], [0.2, 0.6, 0.2], [0.1, 0.1, 0.8]],
    ),
    "finite alleles": lambda: _params(
        mutation_model="finite_alleles",
        loci=[{"locus_id": 2, "length": 2}, {"locus_id": 5, "length": 2}],
        mu=0.1,
    ),
}


@pytest.mark.parametrize("name", list(CASES))
def test_backends_produce_identical_frames_and_layouts(name: str) -> None:
    """L, G and V write the same frames, generation by generation."""
    pytest.importorskip("numba")
    params = CASES[name]()
    lineal = _frames_of(params, "lineal")
    layout = lineal.layouts[RUN_ID]
    assert layout.locus_ids == tuple(locus.locus_id for locus in params.loci)
    assert layout.deme_sizes == tuple(params.population_sizes)
    assert len(lineal.frames[RUN_ID]) == 31
    for backend in ("generational", "generational-vector"):
        other = _frames_of(params, backend)
        assert other.layouts[RUN_ID] == layout
        _same_frames(other.frames[RUN_ID], lineal.frames[RUN_ID])


@pytest.mark.parametrize("backend", ["lineal", "generational-vector"])
def test_a_frame_equals_the_rows_the_row_path_writes(backend: str) -> None:
    """Every frame equals the frame rebuilt from that generation's rows."""
    pytest.importorskip("numba")
    params = CASES["infinite alleles"]()
    recorder = _frames_of(params, backend)
    layout = recorder.layouts[RUN_ID]
    rows_store = InMemoryTrajectoryStore()
    fim(
        params.gene_copies,
        params.m,
        params.mu,
        params.d,
        params=params,
        store=rows_store,
        run_id=RUN_ID,
        engine_backend=backend,  # type: ignore[arg-type]
    )
    by_generation: dict[int, list[dict[str, object]]] = {}
    for row in rows_store.read(RUN_ID):
        by_generation.setdefault(row["generation"], []).append(dict(row))
    frames = recorder.frames[RUN_ID]
    assert sorted(by_generation) == [frame.generation for frame in frames]
    for frame in frames:
        rebuilt = rows_to_frame(by_generation[frame.generation], layout, run_id=RUN_ID)
        _same_frames([frame], [rebuilt])
        assert frame_to_rows(frame, layout, RUN_ID) == by_generation[frame.generation]


def _run_to_file(
    path: Path,
    params: SimulationParams,
    backend: str,
    store_type: type[JSONLTrajectoryStore],
) -> JSONLTrajectoryStore:
    """Run into a JSON Lines store of `store_type` and close it."""
    store = store_type(path)
    fim(
        params.gene_copies,
        params.m,
        params.mu,
        params.d,
        params=params,
        store=store,
        run_id=RUN_ID,
        engine_backend=backend,  # type: ignore[arg-type]
    )
    store.close()
    return store


@pytest.mark.parametrize("backend", ["lineal", "generational-vector"])
def test_jsonl_fed_frames_writes_the_same_bytes_as_jsonl_fed_rows(
    tmp_path: Path, backend: str
) -> None:
    """The frame path and the row path produce byte-identical files."""
    pytest.importorskip("numba")
    params = CASES["infinite alleles"]()
    _run_to_file(tmp_path / "rows.jsonl", params, backend, JSONLTrajectoryStore)
    _run_to_file(tmp_path / "frames.jsonl", params, backend, _FrameJsonlStore)
    assert (tmp_path / "rows.jsonl").read_bytes() == (
        tmp_path / "frames.jsonl"
    ).read_bytes()
    assert (tmp_path / "rows.jsonl").stat().st_size > 0


def test_equilibrium_split_companion_gets_frames_of_one_ancestral_deme(
    tmp_path: Path,
) -> None:
    """The ancestral phase is one deme of every deme's gene copies together."""
    params = _params(
        equilibrium_convergence_window=2,
        equilibrium_convergence_tolerance=0.5,
        equilibrium_max_generations=200,
    )
    recorder = _frames_of(params, "lineal")
    companion = recorder.equilibrium_store(RUN_ID)
    layout = companion.layouts[RUN_ID]
    assert layout.deme_sizes == (sum(params.population_sizes),)
    assert layout.locus_ids == recorder.layouts[RUN_ID].locus_ids
    assert len(companion.frames[RUN_ID]) > 1
    # The ancestral file's bytes agree between the two paths as well.
    for kind, store_type in (
        ("rows", JSONLTrajectoryStore),
        ("frames", _FrameJsonlStore),
    ):
        (tmp_path / kind).mkdir()
        _run_to_file(tmp_path / kind / "trajectory.jsonl", params, "lineal", store_type)
    for name in ("trajectory.jsonl", "equilibrium_trajectory.jsonl"):
        rows_bytes = (tmp_path / "rows" / name).read_bytes()
        assert rows_bytes
        assert rows_bytes == (tmp_path / "frames" / name).read_bytes()


def test_a_batch_gives_every_replicate_its_own_frames() -> None:
    """With a store factory, each replicate's recorder gets its own stream."""
    pytest.importorskip("numba")
    params = _params(n_replicates=3, engine_backend="generational-vector")
    recorders: dict[str, _FrameRecorder] = {}

    def factory(run_id: str) -> TrajectoryStore:
        """Build (once) the recorder of one replicate."""
        return recorders.setdefault(run_id, _FrameRecorder())  # type: ignore[return-value]

    fim(
        params.gene_copies,
        params.m,
        params.mu,
        params.d,
        params=params,
        store_factory=factory,
    )
    assert len(recorders) == 3
    for run_id, recorder in recorders.items():
        assert set(recorder.frames) == {run_id}
        assert len(recorder.frames[run_id]) > 1


def test_begin_run_refuses_a_second_different_layout() -> None:
    """A run's layout is fixed once told."""
    store = InMemoryTrajectoryStore()
    first = FrameLayout(locus_ids=(1,), deme_sizes=(10, 10))
    store.begin_run("a", first)
    store.begin_run("a", first)
    with pytest.raises(ValueError, match="another layout"):
        store.begin_run("a", FrameLayout(locus_ids=(1,), deme_sizes=(10, 20)))


def test_write_frame_requires_begin_run(tmp_path: Path) -> None:
    """Frames cannot become rows without a layout."""
    frame = TrajectoryFrame(
        generation=0,
        counts=np.array([1], dtype=np.int32),
        allele_ids=np.array([1], dtype=np.int64),
        frequencies=np.array([1.0]),
    )
    stores: tuple[InMemoryTrajectoryStore | JSONLTrajectoryStore, ...] = (
        InMemoryTrajectoryStore(),
        JSONLTrajectoryStore(tmp_path / "t.jsonl"),
    )
    for store in stores:
        with pytest.raises(ValueError, match="begin_run"):
            store.write_frame("a", frame)
