"""Tests of `BinaryLogStore`: the log behind the `TrajectoryStore` protocols.

The store must satisfy every row reader's contract, so the central tests
run real simulations into it and compare what `read` returns with what
the in-memory store holds for the same seeded run: every row, in order,
with identical float bits. The rest pin its own rules: one run per store,
the flush barrier, discard, the equilibrium companion, closing, pickling
across a process boundary, and the derived JSON Lines equalling the plain
`json.dumps` text of the rows.
"""

from __future__ import annotations

import hashlib
import itertools
import pickle
from pathlib import Path

import numpy as np
import pytest
from tlog_support import canonical_jsonl

from fim.engine import fim
from fim.model.params import SimulationParams
from fim.persistence import tlog
from fim.persistence.binary_store import (
    EQUILIBRIUM_LOG_FILENAME,
    TRAJECTORY_LOG_FILENAME,
    BinaryLogStore,
)
from fim.persistence.frame import FrameLayout, TrajectoryFrame
from fim.persistence.store import (
    ClosableStore,
    EquilibriumStoreProvider,
    FrameStore,
    InMemoryTrajectoryStore,
    TrajectoryStore,
)
from fim.persistence.tlog import recover
from fim.persistence.tlog_export import derive_jsonl

RUN_ID = "run-binary"


def _params(**updates: object) -> SimulationParams:
    """A small run with several loci, demes and mutations, capped at 30 generations."""
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
        "precision": 1e-12,
        "max_generations": 30,
        "n_replicates": 1,
        "stop_batch_early": False,
    }
    config.update(updates)
    return SimulationParams.from_mapping(config)


def _run(params: SimulationParams, store: TrajectoryStore, backend: str) -> None:
    """Run `params` into `store` on `backend` with the fixed run id."""
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


def _frame(generation: int = 0) -> TrajectoryFrame:
    """A one-pair frame."""
    return TrajectoryFrame(
        generation=generation,
        counts=np.array([2], dtype=np.int32),
        allele_ids=np.array([1, 2], dtype=np.int64),
        frequencies=np.array([0.25, 0.75]),
    )


LAYOUT = FrameLayout(locus_ids=(1,), deme_sizes=(100,))


def test_the_store_satisfies_every_store_protocol(tmp_path: Path) -> None:
    """It is a trajectory store, takes frames, has a companion and can close."""
    store = BinaryLogStore(tmp_path / "t.tlog")
    assert isinstance(store, FrameStore)
    assert isinstance(store, ClosableStore)
    assert isinstance(store, EquilibriumStoreProvider)
    assert store.wants_frames("any") is True
    assert store.path.name == "t.tlog"
    assert TRAJECTORY_LOG_FILENAME == "trajectory.tlog"
    assert EQUILIBRIUM_LOG_FILENAME == "equilibrium_trajectory.tlog"
    store.close()


@pytest.mark.parametrize("backend", ["lineal", "generational", "generational-vector"])
def test_a_real_run_reads_back_exactly_what_the_memory_store_holds(
    tmp_path: Path, backend: str
) -> None:
    """Every row of a real run, in order, with identical float bits."""
    pytest.importorskip("numba")
    params = _params()
    reference = InMemoryTrajectoryStore()
    _run(params, reference, backend)
    store = BinaryLogStore(tmp_path / "t.tlog")
    _run(params, store, backend)
    rows = list(store.read(RUN_ID))
    expected = list(reference.read(RUN_ID))
    assert rows == expected
    assert [row["frequency"].hex() for row in rows] == [
        row["frequency"].hex() for row in expected
    ]
    assert len(rows) > 100
    store.close()
    assert list(store.read(RUN_ID)) == expected


def test_an_equilibrium_split_run_keeps_its_ancestral_phase_beside_it(
    tmp_path: Path,
) -> None:
    """The companion log holds the ancestral generations, with its own counter."""
    params = _params(
        equilibrium_convergence_window=2,
        equilibrium_convergence_tolerance=0.5,
        equilibrium_max_generations=200,
    )
    reference = InMemoryTrajectoryStore()
    _run(params, reference, "lineal")
    store = BinaryLogStore(tmp_path / "trajectory.tlog")
    _run(params, store, "lineal")
    companion = store.equilibrium_store(RUN_ID)
    assert companion.path == tmp_path / EQUILIBRIUM_LOG_FILENAME
    assert companion is store.equilibrium_store(RUN_ID)
    assert list(companion.read(RUN_ID)) == list(
        reference.equilibrium_store(RUN_ID).read(RUN_ID)
    )
    assert list(store.read(RUN_ID)) == list(reference.read(RUN_ID))
    store.close()
    assert not companion.is_open()


def test_a_batch_with_a_factory_writes_one_log_per_replicate(tmp_path: Path) -> None:
    """Replicates each get their own store; the logs hold different runs."""
    pytest.importorskip("numba")
    params = _params(n_replicates=3, engine_backend="generational-vector")
    stores: dict[str, BinaryLogStore] = {}

    def factory(run_id: str) -> BinaryLogStore:
        """Build (once) one replicate's log."""
        return stores.setdefault(
            run_id, BinaryLogStore(tmp_path / f"{run_id}.tlog", background=False)
        )

    fim(
        params.gene_copies,
        params.m,
        params.mu,
        params.d,
        params=params,
        store_factory=factory,
    )
    assert len(stores) == 3
    for run_id, store in stores.items():
        scan = recover(store.path, truncate=False)
        assert scan.header.run_id == run_id
        assert scan.last_generation >= 1
        assert list(store.read(run_id))


def test_one_store_holds_one_run(tmp_path: Path) -> None:
    """A second run id is refused, with a hint about store factories."""
    store = BinaryLogStore(tmp_path / "t.tlog", background=False)
    store.begin_run("run-a", LAYOUT)
    store.write_frame("run-a", _frame())
    with pytest.raises(ValueError, match="store factory"):
        store.write_frame("run-b", _frame(1))
    with pytest.raises(ValueError, match="store factory"):
        store.begin_run("run-b", LAYOUT)
    store.close()
    assert list(store.read("run-b")) == []


def test_begin_run_is_idempotent_and_refuses_another_layout(tmp_path: Path) -> None:
    """The run's shape is fixed once told."""
    store = BinaryLogStore(tmp_path / "t.tlog", background=False)
    store.begin_run("run-a", LAYOUT)
    store.begin_run("run-a", LAYOUT)
    with pytest.raises(ValueError, match="another layout"):
        store.begin_run("run-a", FrameLayout(locus_ids=(1, 2), deme_sizes=(100,)))
    store.close()


def test_rows_without_a_layout_get_an_inferred_one_and_stay_lossless(
    tmp_path: Path,
) -> None:
    """A caller that never says the deme sizes still gets exact floats back."""
    store = BinaryLogStore(tmp_path / "t.tlog", background=False)
    rows = [
        {
            "run_id": RUN_ID,
            "generation": 0,
            "deme": deme,
            "locus_id": locus,
            "allele_id": allele,
            "frequency": frequency,
        }
        for deme, locus, allele, frequency in [
            (1, 7, 1, 0.1),
            (1, 7, 2, 0.9),
            (1, 3, 5, 1.0),
            (2, 7, 5, 1.0 / 3.0),
            (2, 7, 6, 2.0 / 3.0),
            (2, 3, 5, 1.0),
        ]
    ]
    store.write_generation(RUN_ID, 0, rows)
    store.write_generation(RUN_ID, 1, [{**row, "generation": 1} for row in rows])
    assert list(store.read(RUN_ID)) == rows + [{**row, "generation": 1} for row in rows]
    store.close()


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([], "at least one row"),
        ([{"run_id": RUN_ID}], "missing"),
    ],
)
def test_write_generation_refuses_malformed_rows(
    tmp_path: Path, rows: list[dict[str, object]], message: str
) -> None:
    """Empty and malformed generations are refused before anything is written."""
    store = BinaryLogStore(tmp_path / "t.tlog", background=False)
    store.begin_run(RUN_ID, LAYOUT)
    with pytest.raises(ValueError, match=message):
        store.write_generation(RUN_ID, 0, rows)
    store.close()


def test_a_closed_store_refuses_further_writes(tmp_path: Path) -> None:
    """A finished log is not silently reopened."""
    store = BinaryLogStore(tmp_path / "t.tlog", background=False)
    store.begin_run(RUN_ID, LAYOUT)
    store.write_frame(RUN_ID, _frame())
    store.close()
    store.close()
    assert not store.is_open()
    with pytest.raises(RuntimeError, match="closed"):
        store.write_frame(RUN_ID, _frame(1))


def test_a_frame_before_begin_run_is_refused(tmp_path: Path) -> None:
    """Without a layout the log cannot be started."""
    store = BinaryLogStore(tmp_path / "t.tlog")
    with pytest.raises(ValueError, match="begin_run"):
        store.write_frame(RUN_ID, _frame())
    assert not store.exists()
    store.close()


def test_the_context_manager_closes_the_log(tmp_path: Path) -> None:
    """Leaving the `with` block commits and closes."""
    with BinaryLogStore(tmp_path / "t.tlog") as store:
        store.begin_run(RUN_ID, LAYOUT)
        store.write_frame(RUN_ID, _frame())
        assert store.is_open()
    assert not store.is_open()
    assert recover(tmp_path / "t.tlog").last_generation == 0


def test_read_is_a_barrier_that_sees_everything_written(tmp_path: Path) -> None:
    """A reader mid-run sees every generation already submitted."""
    store = BinaryLogStore(tmp_path / "t.tlog", block_generations=1000)
    store.begin_run(RUN_ID, LAYOUT)
    for generation in range(5):
        store.write_frame(RUN_ID, _frame(generation))
    assert store.snapshot()[0] == -1  # nothing sealed yet
    assert [row["generation"] for row in store.read(RUN_ID)] == [
        g for g in range(5) for _ in range(2)
    ]
    generation, position, crc = store.snapshot()
    scan = recover(tmp_path / "t.tlog", truncate=False)
    assert (generation, position, crc) == (4, scan.valid_end, scan.chain_crc)
    assert len(list(store.frames())) == 5
    store.close()


def test_reading_a_log_that_does_not_exist_raises(tmp_path: Path) -> None:
    """A missing trajectory is an error, not an empty run."""
    store = BinaryLogStore(tmp_path / "nothing.tlog")
    with pytest.raises(FileNotFoundError):
        store.read(RUN_ID)
    with pytest.raises(FileNotFoundError):
        store.frames()


def test_discard_removes_only_the_run_it_holds(tmp_path: Path) -> None:
    """Discarding the held run deletes the log; any other run is a no-op."""
    store = BinaryLogStore(tmp_path / "t.tlog", background=False)
    store.begin_run("run-a", LAYOUT)
    store.write_frame("run-a", _frame())
    store.discard("run-other")
    assert store.exists()
    store.discard("run-a")
    assert not store.exists()
    store.discard("run-a")
    # After a discard the store can start a fresh run.
    store.begin_run("run-b", LAYOUT)
    store.write_frame("run-b", _frame())
    store.close()
    assert recover(tmp_path / "t.tlog").header.run_id == "run-b"
    reopened = BinaryLogStore(tmp_path / "t.tlog")
    reopened.discard("run-b")
    assert not (tmp_path / "t.tlog").exists()
    BinaryLogStore(tmp_path / "gone.tlog").discard("run-x")


def test_a_store_pickles_as_its_closed_log_and_reads_in_another_process(
    tmp_path: Path,
) -> None:
    """The copy that crosses a process boundary reads the finished file."""
    params = _params()
    store = BinaryLogStore(tmp_path / "t.tlog")
    _run(params, store, "lineal")
    expected = list(store.read(RUN_ID))
    copy = pickle.loads(pickle.dumps(store))
    assert not store.is_open()
    assert not copy.is_open()
    assert list(copy.read(RUN_ID)) == expected


def test_the_derived_jsonl_of_a_real_run_is_the_plain_json_dumps_text(
    tmp_path: Path,
) -> None:
    """Export of the run's log equals `json.dumps` of the rows the run produced."""
    params = _params()
    memory = InMemoryTrajectoryStore()
    _run(params, memory, "lineal")
    store = BinaryLogStore(tmp_path / "t.tlog")
    _run(params, store, "lineal")
    store.close()
    derived = derive_jsonl(tmp_path / "t.tlog", tmp_path / "derived.jsonl")
    reference = canonical_jsonl(memory.read(RUN_ID))
    assert (tmp_path / "derived.jsonl").read_bytes() == reference
    assert derived.sha256 == hashlib.sha256(reference).hexdigest()


def test_the_log_is_far_smaller_than_the_jsonl(tmp_path: Path) -> None:
    """Count coding and binary records: at least ten times smaller here."""
    params = _params(max_generations=60)
    memory = InMemoryTrajectoryStore()
    _run(params, memory, "lineal")
    store = BinaryLogStore(tmp_path / "t.tlog")
    _run(params, store, "lineal")
    store.close()
    log_bytes = (tmp_path / "t.tlog").stat().st_size
    assert log_bytes * 10 < len(canonical_jsonl(memory.read(RUN_ID)))


def test_a_run_writes_the_same_log_bytes_every_time(tmp_path: Path) -> None:
    """The log is a pure function of the run: no timing reaches a byte.

    Sealing on count and size only (the default) means the block
    boundaries, and so the file and its digest, are the same whether the
    machine was fast or slow, with or without the writer thread.
    """
    params = _params(max_generations=40)
    paths = []
    for name, options in (
        ("a", {}),
        ("b", {}),
        ("inline", {"background": False}),
        ("small-blocks", {"block_generations": 7}),
    ):
        path = tmp_path / f"{name}.tlog"
        store = BinaryLogStore(path, **options)
        _run(params, store, "lineal")
        store.close()
        paths.append(path)
    first, second, inline, small = (path.read_bytes() for path in paths)
    assert first == second == inline
    # A different block size is a different file, with the same content.
    assert small != first
    assert list(BinaryLogStore(paths[3]).read(RUN_ID)) == list(
        BinaryLogStore(paths[0]).read(RUN_ID)
    )


def test_a_store_that_seals_on_time_still_finishes_with_the_canonical_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The app's live-view setting never changes the finished file.

    A clock that moves a tenth of a second per reading makes every few
    generations a timed seal; the closed log must still equal the one written
    with no timer at all, so its digest in the manifest is reproducible.
    """
    params = _params(max_generations=60)
    plain = BinaryLogStore(tmp_path / "plain.tlog")
    _run(params, plain, "lineal")
    plain.close()
    ticks = itertools.count()
    rewrites: list[Path] = []
    original = tlog.LogWriter._rewrite_canonically

    def spy(self: tlog.LogWriter) -> None:
        rewrites.append(self.path)
        original(self)

    monkeypatch.setattr(tlog.LogWriter, "_rewrite_canonically", spy)
    timed = BinaryLogStore(
        tmp_path / "timed.tlog",
        block_seconds=1.0,
        clock=lambda: next(ticks) * 0.1,
    )
    _run(params, timed, "lineal")
    timed.close()
    assert rewrites == [tmp_path / "timed.tlog"]
    assert (tmp_path / "timed.tlog").read_bytes() == (
        tmp_path / "plain.tlog"
    ).read_bytes()
    assert not (tmp_path / "timed.tlog.canonical").exists()


def test_a_dense_store_holds_the_same_rows_as_a_sparse_one(tmp_path: Path) -> None:
    """The mode changes the file, never what `read` returns."""
    params = _params(max_generations=50)
    for mode in ("dense", "sparse"):
        store = BinaryLogStore(tmp_path / f"{mode}.tlog", mode=mode)
        _run(params, store, "lineal")
        store.close()
    dense = BinaryLogStore(tmp_path / "dense.tlog")
    sparse = BinaryLogStore(tmp_path / "sparse.tlog")
    assert list(dense.read(RUN_ID)) == list(sparse.read(RUN_ID))
