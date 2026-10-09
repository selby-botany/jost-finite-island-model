"""Compact per-generation frames: the form a trajectory takes between engine and disk.

A trajectory row names one allele frequency; a generation holds thousands
of them, and building one Python dictionary per row costs far more than
the simulation that produced them. A `TrajectoryFrame` carries the same
information as flat arrays, in the compressed-sparse-row layout:

- a *pair* is one (deme, locus) combination, numbered
  `pair = deme_index * loci + locus_index` (both zero-based, demes in
  order, loci in the order of the run's own locus list);
- `counts[pair]` is how many alleles that pair has at a nonzero
  frequency;
- `allele_ids` and `frequencies` hold every pair's alleles one after
  another, pair 0 first, so a pair's entries start at the running sum of
  the counts before it.

That is exactly the order `ModelState.to_rows` and `VectorBlock.rows`
produce, so a frame and a generation's rows are two spellings of one
thing, and `frame_to_rows(rows_to_frame(rows))` returns the same rows.

A `FrameLayout` says which pair is which: the locus identifiers, and the
gene-copy count of every deme. The gene-copy counts let the binary log
store a frequency as an integer count (`count / size`); a size of `0`
means "unknown", and every frequency of that deme is then stored as the
raw float, which is larger but still exact.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from fim.model.state import ModelState
from fim.persistence.store import TrajectoryRow, normalize_row

UNKNOWN_DEME_SIZE = 0
"""A deme size of `0` means the gene-copy count is not known."""


@dataclass(frozen=True, slots=True)
class FrameLayout:
    """Which (deme, locus) pair each position of a frame means.

    Args:
        locus_ids: The locus identifiers in the order of the run's locus
            list (one-based identifiers, as in a trajectory row).
        deme_sizes: Gene copies per deme, demes in order; `0` for a deme
            whose size is not known.
    """

    locus_ids: tuple[int, ...]
    deme_sizes: tuple[int, ...]
    _locus_index: dict[int, int] = field(
        init=False, repr=False, compare=False, hash=False
    )

    def __post_init__(self) -> None:
        """Check the layout and build the locus lookup.

        Raises:
            ValueError: If there are no loci or demes, an identifier
                repeats, or a size is negative.
        """
        if not self.locus_ids:
            raise ValueError("a frame layout needs at least one locus")
        if not self.deme_sizes:
            raise ValueError("a frame layout needs at least one deme")
        if len(set(self.locus_ids)) != len(self.locus_ids):
            raise ValueError("locus identifiers must be unique")
        if any(size < 0 for size in self.deme_sizes):
            raise ValueError("deme sizes must not be negative")
        object.__setattr__(
            self,
            "_locus_index",
            {locus_id: index for index, locus_id in enumerate(self.locus_ids)},
        )

    @property
    def demes(self) -> int:
        """The number of demes."""
        return len(self.deme_sizes)

    @property
    def loci(self) -> int:
        """The number of loci."""
        return len(self.locus_ids)

    @property
    def pairs(self) -> int:
        """The number of (deme, locus) pairs, the length of `counts`."""
        return self.demes * self.loci

    def pair_index(self, deme: int, locus_id: int) -> int:
        """Return the pair number of a one-based deme and a locus identifier.

        Args:
            deme: One-based deme number, as in a trajectory row.
            locus_id: Locus identifier, as in a trajectory row.

        Returns:
            The zero-based pair number.

        Raises:
            ValueError: If the deme or the locus is not in the layout.
        """
        if not 1 <= deme <= self.demes:
            raise ValueError(f"deme {deme} is outside 1..{self.demes}")
        try:
            locus_index = self._locus_index[locus_id]
        except KeyError:
            raise ValueError(f"locus {locus_id} is not in the layout") from None
        return (deme - 1) * self.loci + locus_index

    @classmethod
    def infer(cls, rows: Iterable[Mapping[str, Any]]) -> FrameLayout:
        """Infer a layout from one generation's rows, with unknown deme sizes.

        For a caller that writes rows without telling the store the run's
        shape. The demes are `1..max(deme)` and the loci are the
        identifiers seen, in order of first appearance.

        Args:
            rows: One generation's rows.

        Returns:
            A layout whose deme sizes are all `UNKNOWN_DEME_SIZE`.

        Raises:
            ValueError: If `rows` is empty.
        """
        max_deme = 0
        locus_ids: dict[int, None] = {}
        for row in rows:
            max_deme = max(max_deme, int(row["deme"]))
            locus_ids.setdefault(int(row["locus_id"]), None)
        if not locus_ids:
            raise ValueError("a generation must contain at least one row")
        return cls(
            locus_ids=tuple(locus_ids),
            deme_sizes=(UNKNOWN_DEME_SIZE,) * max_deme,
        )


@dataclass(frozen=True, slots=True)
class TrajectoryFrame:
    """One generation of a trajectory as flat arrays.

    Args:
        generation: The generation number.
        counts: `int32[pairs]`, alleles per (deme, locus) pair.
        allele_ids: `int64[entries]`, every pair's allele identifiers,
            pair 0 first.
        frequencies: `float64[entries]`, the matching frequencies.

    The arrays are not copied or validated here; a frame is a cheap view
    of what a backend already holds, and `validate_frame` checks one that
    came from outside.
    """

    generation: int
    counts: np.ndarray
    allele_ids: np.ndarray
    frequencies: np.ndarray

    @property
    def rows(self) -> int:
        """The number of rows this frame stands for."""
        return int(self.allele_ids.shape[0])


def frame_to_rows(
    frame: TrajectoryFrame,
    layout: FrameLayout,
    run_id: str,
    *,
    validate: bool = True,
) -> list[TrajectoryRow]:
    """Return the trajectory rows a frame stands for, in pair order.

    Args:
        frame: The frame.
        layout: Its layout.
        run_id: The run the rows belong to.
        validate: Whether to check the frame first (`validate_frame`). A
            frame a backend just built passes `False`.

    Returns:
        One row per entry, deme-major then locus then the frame's own
        allele order, with the field order `ModelState.to_rows` uses.

    Raises:
        ValueError: If `run_id` is empty or the frame does not fit the
            layout.
    """
    if not run_id:
        raise ValueError("run_id must not be empty")
    if validate:
        validate_frame(frame, layout)
    generation = frame.generation
    loci = layout.loci
    locus_ids = layout.locus_ids
    counts = frame.counts.tolist()
    allele_ids = frame.allele_ids.tolist()
    frequencies = frame.frequencies.tolist()
    rows: list[TrajectoryRow] = []
    entry = 0
    for pair, count in enumerate(counts):
        if not count:
            continue
        deme = pair // loci + 1
        locus_id = locus_ids[pair % loci]
        rows.extend(
            TrajectoryRow(
                run_id=run_id,
                generation=generation,
                deme=deme,
                locus_id=locus_id,
                allele_id=allele_ids[index],
                frequency=frequencies[index],
            )
            for index in range(entry, entry + count)
        )
        entry += count
    return rows


def rows_to_frame(
    rows: Iterable[Mapping[str, Any]],
    layout: FrameLayout,
    *,
    run_id: str | None = None,
    generation: int | None = None,
    validate: bool = True,
) -> TrajectoryFrame:
    """Build the frame of one generation's rows.

    Rows already in pair order (what the engine produces) keep their
    order. Rows in any other order are put in pair order, each pair
    keeping its own entries in the order given, so the frame is always
    canonical.

    Args:
        rows: One generation's rows.
        layout: The run's layout.
        run_id: When given, every row must carry it.
        generation: When given, every row must carry it; otherwise the
            rows' own (single) generation is used.
        validate: Whether to run each row through `normalize_row`. The
            engine's own rows are well formed by construction and pass
            `False`.

    Returns:
        The frame.

    Raises:
        ValueError: If `rows` is empty, a row is malformed or belongs to
            another run or generation, a pair is not in the layout, or a
            frequency is not in `(0, 1]`.
    """
    pairs: list[int] = []
    allele_ids: list[int] = []
    frequencies: list[float] = []
    seen_generation = generation
    for row in rows:
        if validate:
            normalized = normalize_row(row, run_id=run_id, generation=generation)
            deme = normalized["deme"]
            locus_id = normalized["locus_id"]
            row_generation = normalized["generation"]
            allele_id = normalized["allele_id"]
            frequency = normalized["frequency"]
        else:
            deme = row["deme"]
            locus_id = row["locus_id"]
            row_generation = row["generation"]
            allele_id = row["allele_id"]
            frequency = row["frequency"]
        if seen_generation is None:
            seen_generation = row_generation
        elif row_generation != seen_generation:
            raise ValueError(
                f"rows of one frame must share a generation, got "
                f"{seen_generation} and {row_generation}"
            )
        pairs.append(layout.pair_index(deme, locus_id))
        allele_ids.append(allele_id)
        frequencies.append(frequency)
    if not pairs or seen_generation is None:
        raise ValueError("a generation must contain at least one row")
    pair_array = np.asarray(pairs, dtype=np.int64)
    id_array = np.asarray(allele_ids, dtype=np.int64)
    frequency_array = np.asarray(frequencies, dtype=np.float64)
    if pair_array.shape[0] > 1 and bool(np.any(pair_array[1:] < pair_array[:-1])):
        order = np.argsort(pair_array, kind="stable")
        pair_array = pair_array[order]
        id_array = id_array[order]
        frequency_array = frequency_array[order]
    counts = np.bincount(pair_array, minlength=layout.pairs).astype(np.int32)
    return TrajectoryFrame(
        generation=int(seen_generation),
        counts=counts,
        allele_ids=id_array,
        frequencies=frequency_array,
    )


def validate_frame(frame: TrajectoryFrame, layout: FrameLayout) -> None:
    """Check that a frame is consistent and fits a layout.

    Args:
        frame: The frame.
        layout: The layout it should fit.

    Raises:
        ValueError: If the arrays have the wrong shape or type, the counts
            do not add up to the number of entries, a count is negative, an
            identifier is negative, or a frequency is outside `(0, 1]`.
    """
    if frame.generation < 0:
        raise ValueError("a frame's generation must not be negative")
    counts, ids, freq = frame.counts, frame.allele_ids, frame.frequencies
    if counts.shape != (layout.pairs,):
        raise ValueError(
            f"frame has {counts.shape} counts for a layout of {layout.pairs} pairs"
        )
    if ids.shape != freq.shape or ids.ndim != 1:
        raise ValueError("frame identifiers and frequencies must be 1-D, same length")
    if bool(np.any(counts < 0)) or int(counts.sum()) != ids.shape[0]:
        raise ValueError("frame counts must be non-negative and sum to the entries")
    if ids.shape[0] == 0:
        raise ValueError("a generation must contain at least one row")
    if bool(np.any(ids < 0)):
        raise ValueError("frame allele identifiers must not be negative")
    if not bool(np.all((freq > 0.0) & (freq <= 1.0))):
        raise ValueError("frame frequencies must be in (0, 1]")


def layout_for_sizes(
    locus_ids: Sequence[int], deme_sizes: Sequence[int]
) -> FrameLayout:
    """Build a layout from plain sequences.

    Args:
        locus_ids: Locus identifiers in order.
        deme_sizes: Gene copies per deme in order.

    Returns:
        The layout.
    """
    return FrameLayout(
        locus_ids=tuple(int(x) for x in locus_ids),
        deme_sizes=tuple(int(x) for x in deme_sizes),
    )


def layout_for_state(
    state: ModelState, deme_sizes: Sequence[int] | None = None
) -> FrameLayout:
    """Return the layout of a `ModelState`'s frames.

    Args:
        state: Any generation of the run.
        deme_sizes: Gene copies per deme, when known; otherwise every deme
            is `UNKNOWN_DEME_SIZE`.

    Returns:
        The layout: the state's locus identifiers in order, and the sizes.

    Raises:
        ValueError: If `deme_sizes` does not have one entry per deme.
    """
    if deme_sizes is None:
        deme_sizes = (UNKNOWN_DEME_SIZE,) * state.deme_count
    elif len(deme_sizes) != state.deme_count:
        raise ValueError("deme_sizes must have one entry per deme")
    return layout_for_sizes([locus.locus_id for locus in state.loci], deme_sizes)


def state_to_frame(state: ModelState) -> TrajectoryFrame:
    """Return a `ModelState`'s generation as a frame, without building rows.

    The entries are in `ModelState.to_rows` order (deme-major, then locus,
    then the frequency map's own order), so `frame_to_rows` of the result
    equals `to_rows`. Backends L and G hold dictionaries, so this walks them
    once in Python; Backend V builds its frame with a compiled call
    (`VectorBlock.frame`).

    Args:
        state: The generation.

    Returns:
        The frame, numbered with the state's generation.

    Raises:
        ValueError: If the state has no allele at all.
    """
    counts: list[int] = []
    allele_ids: list[int] = []
    frequencies: list[float] = []
    for deme in state.frequencies:
        for frequency_map in deme:
            counts.append(len(frequency_map))
            allele_ids.extend(int(allele_id) for allele_id in frequency_map)
            frequencies.extend(frequency_map.values())
    if not allele_ids:
        raise ValueError("a generation must contain at least one row")
    return TrajectoryFrame(
        generation=state.generation,
        counts=np.asarray(counts, dtype=np.int32),
        allele_ids=np.asarray(allele_ids, dtype=np.int64),
        frequencies=np.asarray(frequencies, dtype=np.float64),
    )
