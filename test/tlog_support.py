"""Shared frame generators and checks for the binary log's tests.

Test modules import this with `from tlog_support import ...`, as they import
`conftest` and `vector_support`.
"""

from __future__ import annotations

import numpy as np

from fim.persistence.frame import FrameLayout, TrajectoryFrame

LAYOUT = FrameLayout(locus_ids=(3, 1, 8, 9), deme_sizes=(100, 64, 25))
PAIRS = LAYOUT.pairs


def fresh_pair(
    rng: np.random.Generator, size: int, wide: bool
) -> tuple[list[int], list[float]]:
    """Alleles (ids, frequencies) of one pair; `wide` allows many alleles."""
    n = int(rng.integers(1, 30 if wide else 4))
    n = min(n, size)
    cuts: np.ndarray = (
        np.sort(rng.choice(np.arange(1, size), n - 1, replace=False))
        if n > 1
        else np.zeros(0, dtype=np.int64)
    )
    counts = np.diff(np.concatenate(([0], cuts, [size])))
    ids = np.sort(rng.choice(2**34, n, replace=False)).tolist()
    return ids, [c / float(size) for c in counts.tolist()]


def walk(
    count: int, *, change: float, seed: int, step: int = 1, wide: bool = False
) -> list[TrajectoryFrame]:
    """Frames where each generation changes about `change` of the pairs."""
    rng = np.random.default_rng(seed)
    sizes = [size for size in LAYOUT.deme_sizes for _ in LAYOUT.locus_ids]
    pairs = [fresh_pair(rng, size, wide) for size in sizes]
    frames = []
    for index in range(count):
        for p in range(PAIRS):
            if index == 0 or rng.random() < change:
                pairs[p] = fresh_pair(rng, sizes[p], wide)
        frames.append(
            TrajectoryFrame(
                generation=index * step,
                counts=np.array([len(ids) for ids, _ in pairs], dtype=np.int32),
                allele_ids=np.array(
                    [i for ids, _ in pairs for i in ids], dtype=np.int64
                ),
                frequencies=np.array([f for _, freq in pairs for f in freq]),
            )
        )
    return frames


def same_frames(got: list[TrajectoryFrame], want: list[TrajectoryFrame]) -> None:
    """Require two frame lists to be equal, with exact frequency bits."""
    assert [f.generation for f in got] == [f.generation for f in want]
    for left, right in zip(got, want, strict=True):
        assert np.array_equal(left.counts, right.counts)
        assert np.array_equal(left.allele_ids, right.allele_ids)
        assert left.frequencies.tobytes() == right.frequencies.tobytes()
