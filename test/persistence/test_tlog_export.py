"""Byte-identity tests for the derived `trajectory.jsonl`.

The export is only acceptable if its bytes are exactly what
`json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False)`
and a newline give for every row. These tests build the expected text
independently from the frames that were written (never from the decoded
log), over adversarial inputs: arbitrary floats from subnormal to huge,
mixed counted and raw pairs, uneven deme sizes including one above the
table cap and one unknown, large allele identifiers, and a run id that
needs escaping. They then check real runs against the JSON Lines store's own
file, sharded against single-process derivation, and the size pass against
the written size. All seeded; nothing depends on timing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from fim.engine import fim
from fim.model.params import SimulationParams
from fim.persistence.frame import FrameLayout, TrajectoryFrame, frame_to_rows
from fim.persistence.jsonl_store import JSONLTrajectoryStore
from fim.persistence.store import TrajectoryRow
from fim.persistence.tlog import LogWriter, TlogError, recover
from fim.persistence.tlog_export import (
    LUT_MAX_SIZE,
    check_free_space,
    derive_jsonl,
    free_space_needed,
    plan_shards,
)

NASTY_RUN_ID = 'run-"quoted"-\\-é-☃-😀'


def _expected(frames: list[TrajectoryFrame], layout: FrameLayout, run_id: str) -> bytes:
    """The canonical text, built with `json.dumps` from the original frames."""
    lines = [
        json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        for frame in frames
        for row in frame_to_rows(frame, layout, run_id, validate=False)
    ]
    return "".join(lines).encode("utf-8")


def _adversarial_frames(
    layout: FrameLayout, count: int, seed: int
) -> list[TrajectoryFrame]:
    """Frames mixing count-coded pairs with arbitrary floats and big ids."""
    rng = np.random.default_rng(seed)
    special = [
        5e-324,
        1e-300,
        1.0 / 3.0,
        0.1,
        0.9999999999999999,
        2.2250738585072014e-308,
    ]
    frames: list[TrajectoryFrame] = []
    for generation in range(count):
        nal: list[int] = []
        ids: list[int] = []
        fr: list[float] = []
        for deme_size in layout.deme_sizes:
            for _locus in layout.locus_ids:
                n = int(rng.integers(1, 5))
                pick = rng.random()
                if deme_size > 0 and pick < 0.5:
                    # Count-coded pair: each frequency is c / size for the
                    # deme's real size, so the codec stores a small integer.
                    top = min(deme_size, 1000)
                    n = min(n, top)
                    cuts = np.sort(rng.choice(np.arange(1, top), n - 1, replace=False))
                    counts = np.diff(np.concatenate(([0], cuts, [top])))
                    values = [c / float(deme_size) for c in counts.tolist()]
                elif pick < 0.75:
                    values = rng.random(n).tolist()
                else:
                    values = [special[int(i)] for i in rng.integers(0, len(special), n)]
                nal.append(n)
                ids.extend(
                    (
                        int(rng.choice([0, 2**32, 2**40]))
                        + np.sort(rng.choice(900, n, replace=False))
                    )
                    .astype(np.int64)
                    .tolist()
                )
                fr.extend(float(v) for v in values)
        frames.append(
            TrajectoryFrame(
                generation=generation * 3,
                counts=np.asarray(nal, dtype=np.int32),
                allele_ids=np.asarray(ids, dtype=np.int64),
                frequencies=np.asarray(fr, dtype=np.float64),
            )
        )
    return frames


def _write(
    path: Path,
    frames: list[TrajectoryFrame],
    layout: FrameLayout,
    run_id: str,
    **options: object,
) -> None:
    """Write `frames` to a log and close it."""
    writer = LogWriter(path, run_id, layout, **options)  # type: ignore[arg-type]
    for frame in frames:
        writer.submit(frame)
    writer.close()


@pytest.mark.parametrize(
    ("deme_sizes", "run_id"),
    [
        ((100, 64, 7), "run-plain"),
        ((100, 0, 25), NASTY_RUN_ID),
        ((LUT_MAX_SIZE + 5, 100, 3), "run-big-deme"),
    ],
)
def test_derived_jsonl_is_what_json_dumps_gives(
    tmp_path: Path, deme_sizes: tuple[int, ...], run_id: str
) -> None:
    """Every byte equals `json.dumps` of the original rows, digest included."""
    layout = FrameLayout(locus_ids=(3, 1, 2**20), deme_sizes=deme_sizes)
    frames = _adversarial_frames(layout, 40, seed=len(run_id))
    log = tmp_path / "t.tlog"
    _write(log, frames, layout, run_id, block_generations=7)
    expected = _expected(frames, layout, run_id)
    out = tmp_path / "t.jsonl"
    result = derive_jsonl(log, out)
    assert out.read_bytes() == expected
    assert result.size == len(expected)
    assert result.sha256 == hashlib.sha256(expected).hexdigest()
    assert result.generations == 40
    assert result.rows == sum(frame.rows for frame in frames)
    assert result.run_id == run_id
    assert result.path == out


def test_digest_only_derivation_matches_the_file_derivation(tmp_path: Path) -> None:
    """With no output path nothing is stored but digest and size are the same."""
    layout = FrameLayout(locus_ids=(1, 2), deme_sizes=(100, 50))
    frames = _adversarial_frames(layout, 12, seed=2)
    log = tmp_path / "t.tlog"
    _write(log, frames, layout, "run-d")
    stored = derive_jsonl(log, tmp_path / "t.jsonl")
    counted = derive_jsonl(log)
    assert counted.path is None
    assert (counted.sha256, counted.size) == (stored.sha256, stored.size)
    assert not list(tmp_path.glob("*.jsonl.*"))


def test_the_size_pass_equals_the_written_size(tmp_path: Path) -> None:
    """`free_space_needed` knows the exact size without formatting anything."""
    layout = FrameLayout(locus_ids=(1, 2, 3), deme_sizes=(100, 0, 40))
    frames = _adversarial_frames(layout, 25, seed=9)
    log = tmp_path / "t.tlog"
    _write(log, frames, layout, NASTY_RUN_ID, block_generations=4)
    result = derive_jsonl(log, tmp_path / "t.jsonl")
    assert (
        free_space_needed(log) == result.size == (tmp_path / "t.jsonl").stat().st_size
    )
    needed, free = check_free_space(log, tmp_path)
    assert needed == result.size
    assert free > 0


def test_sharded_derivation_equals_single_process_derivation(tmp_path: Path) -> None:
    """Shards sized, written to their offsets and hashed give the same file."""
    layout = FrameLayout(locus_ids=(1, 2), deme_sizes=(100, 64, 9))
    frames = _adversarial_frames(layout, 60, seed=5)
    log = tmp_path / "t.tlog"
    _write(log, frames, layout, NASTY_RUN_ID, block_generations=5)
    single = derive_jsonl(log, tmp_path / "single.jsonl")
    sharded = derive_jsonl(log, tmp_path / "sharded.jsonl", workers=2, shard_blocks=3)
    assert (tmp_path / "sharded.jsonl").read_bytes() == (
        tmp_path / "single.jsonl"
    ).read_bytes()
    assert sharded.sha256 == single.sha256
    assert sharded.size == single.size
    assert sharded.rows == single.rows


def test_plan_shards_start_at_keyframe_blocks_and_cover_every_block(
    tmp_path: Path,
) -> None:
    """Shard boundaries are block indexes; together they cover the log once."""
    layout = FrameLayout(locus_ids=(1,), deme_sizes=(100,))
    frames = _adversarial_frames(layout, 30, seed=1)
    log = tmp_path / "t.tlog"
    _write(log, frames, layout, "run-s", block_generations=3)
    scan = recover(log, truncate=False)
    shards = plan_shards(scan, 4)
    assert shards[0][0] == 0
    assert shards[-1][1] == len(scan.blocks)
    assert all(a < b for a, b in shards)
    assert [a for a, _ in shards[1:]] == [b for _, b in shards[:-1]]
    assert plan_shards(scan, 1000) == [(0, len(scan.blocks))]


def test_a_log_with_no_blocks_derives_an_empty_file(tmp_path: Path) -> None:
    """No generations means no lines, and the SHA-256 of the empty string."""
    layout = FrameLayout(locus_ids=(1,), deme_sizes=(10,))
    log = tmp_path / "t.tlog"
    _write(log, [], layout, "run-e")
    result = derive_jsonl(log, tmp_path / "t.jsonl")
    assert (tmp_path / "t.jsonl").read_bytes() == b""
    assert result.sha256 == hashlib.sha256(b"").hexdigest()
    assert free_space_needed(log) == 0


def test_derivation_refuses_a_missing_or_foreign_file(tmp_path: Path) -> None:
    """A missing file and a non-log file raise clear errors."""
    with pytest.raises(FileNotFoundError):
        derive_jsonl(tmp_path / "nope.tlog")
    with pytest.raises(FileNotFoundError):
        free_space_needed(tmp_path / "nope.tlog")
    (tmp_path / "foreign.tlog").write_bytes(b"x" * 100)
    with pytest.raises(TlogError):
        derive_jsonl(tmp_path / "foreign.tlog")


class _FrameTap:
    """A store that wants frames and keeps a copy of every frame it gets."""

    def __init__(self) -> None:
        """Start empty."""
        self.layouts: dict[str, FrameLayout] = {}
        self.frames: list[TrajectoryFrame] = []
        self.companion: _FrameTap | None = None

    def begin_run(self, run_id: str, layout: FrameLayout) -> None:
        """Remember the layout."""
        self.layouts[run_id] = layout

    def wants_frames(self, run_id: str) -> bool:
        """Ask for frames."""
        del run_id
        return True

    def write_frame(self, run_id: str, frame: TrajectoryFrame) -> None:
        """Keep a copy of the frame."""
        del run_id
        self.frames.append(
            TrajectoryFrame(
                frame.generation,
                frame.counts.copy(),
                frame.allele_ids.copy(),
                frame.frequencies.copy(),
            )
        )

    def write_generation(self, *_args: object, **_kwargs: object) -> None:
        """Fail: a store that wants frames must not be given rows."""
        raise AssertionError("rows were written to a frame-native store")

    def read(self, run_id: str) -> Iterator[TrajectoryRow]:
        """Unused."""
        raise NotImplementedError

    def discard(self, run_id: str) -> None:
        """Unused."""

    def equilibrium_store(self, run_id: str) -> _FrameTap:
        """Return the ancestral-phase tap."""
        del run_id
        if self.companion is None:
            self.companion = _FrameTap()
        return self.companion


def _params(**updates: object) -> SimulationParams:
    """A small multi-locus, multi-deme run that goes to its generation cap."""
    config: dict[str, object] = {
        "N": 30,
        "ploidy": "haploid",
        "m": 0.1,
        "mu": 0.02,
        "d": 3,
        "seed": 20261009,
        "loci": [{"locus_id": 1, "length": 200}, {"locus_id": 2, "length": 200}],
        "initial_allele_count": 3,
        "convergence_window": 4,
        "convergence_tolerance": 1e-12,
        "max_generations": 40,
        "n_replicates": 1,
        "replicate_tolerance": None,
    }
    config.update(updates)
    return SimulationParams.from_mapping(config)


def _run_both(
    tmp_path: Path, params: SimulationParams, backend: str
) -> tuple[_FrameTap, Path]:
    """Run once into the JSON Lines store and once into a frame tap (same seed)."""
    (tmp_path / "rows").mkdir()
    store = JSONLTrajectoryStore(tmp_path / "rows" / "trajectory.jsonl")
    fim(
        params.gene_copies,
        params.m,
        params.mu,
        params.d,
        params=params,
        store=store,
        run_id="run-real",
        engine_backend=backend,  # type: ignore[arg-type]
    )
    store.close()
    tap = _FrameTap()
    fim(
        params.gene_copies,
        params.m,
        params.mu,
        params.d,
        params=params,
        store=tap,
        run_id="run-real",
        engine_backend=backend,  # type: ignore[arg-type]
    )
    return tap, tmp_path / "rows"


@pytest.mark.parametrize("backend", ["lineal", "generational-vector"])
def test_a_real_run_derives_the_jsonl_store_file_byte_for_byte(
    tmp_path: Path, backend: str
) -> None:
    """The log of a run, derived, is the file the JSON Lines store wrote."""
    pytest.importorskip("numba")
    params = _params()
    tap, rows_dir = _run_both(tmp_path, params, backend)
    log = tmp_path / "t.tlog"
    _write(log, tap.frames, tap.layouts["run-real"], "run-real")
    derived = derive_jsonl(log, tmp_path / "derived.jsonl")
    reference = (rows_dir / "trajectory.jsonl").read_bytes()
    assert (tmp_path / "derived.jsonl").read_bytes() == reference
    assert derived.sha256 == hashlib.sha256(reference).hexdigest()


def test_a_real_equilibrium_split_run_derives_both_files_byte_for_byte(
    tmp_path: Path,
) -> None:
    """The ancestral companion (one deme of every deme's copies) is exact too."""
    params = _params(
        equilibrium_convergence_window=2,
        equilibrium_convergence_tolerance=0.5,
        equilibrium_max_generations=200,
    )
    tap, rows_dir = _run_both(tmp_path, params, "lineal")
    companion = tap.equilibrium_store("run-real")
    assert companion.frames
    for name, source in (
        ("trajectory.jsonl", tap),
        ("equilibrium_trajectory.jsonl", companion),
    ):
        log = tmp_path / f"{name}.tlog"
        _write(log, source.frames, source.layouts["run-real"], "run-real")
        derive_jsonl(log, tmp_path / f"derived-{name}")
        assert (tmp_path / f"derived-{name}").read_bytes() == (
            rows_dir / name
        ).read_bytes()
