"""The dense per-replicate state of Backend V, and its two boundaries.

`VectorBlock` holds one replicate's allele frequencies for every locus at
once in plain NumPy arrays (`fim.model.vector_kernels` documents the
layout and the exactness rules), advances them one generation per call
with the compiled kernel, and converts to the rest of the program's
shapes only where something outside Backend V needs them: trajectory
rows, a `ModelState` for the final report, per-locus frequency maps for
the statistics the kernel does not compute, and the allele registry
counter.

Backend V's reproducibility contract, which this class exists to keep:
for the same configuration and seed, V under either mutation model
produces the same trajectory rows, report and final state as Backends L
and G (every allele identity and every frequency bit), on the same
platform. Only `RunManifest.engine_backend` records which backend ran.
"Same platform" carries the caveat Backend G with `jit` already has: the
compiled `lgamma`, `log`, `log1p` and `exp` are verified to match CPython
only on the development platform, so a result is exactly reproducible
across backends on one machine, and statistically (not bitwise) across
machines.

Memory. The block is `loci x demes x width` cells of 16 bytes (a
frequency and a gene-copy count). Under infinite alleles `width` follows
the live allele count: it doubles when a locus needs more columns, extinct
columns are dropped every generation, and the width is halved again after
a long stretch of low use. Under finite alleles `width` is the largest
locus capacity (`4 ** length`) for the whole run. Either way the size is
checked against a ceiling before any allocation, so a configuration that
cannot fit fails at once with a message that names the remedies, not
partway through a long run with an out-of-memory kill. The ceiling is per
replicate: `max_concurrent_replicates` replicates can be alive at once.

This module imports only NumPy. The compiled kernels (Numba) are imported
on first use, so importing `fim` never requires Numba.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from types import ModuleType
from typing import Final

import numpy as np

from fim.model.allele import AlleleId, AlleleRegistry
from fim.model.locus import LocusSpec, finite_allele_capacity
from fim.model.state import ModelState

MIGRATION_NONE: Final = 0
"""Migration kind: no blending (a zero rate, or a single deme)."""

MIGRATION_SCALAR: Final = 1
"""Migration kind: one rate, every other deme in the pool (continuous)."""

MIGRATION_MATRIX: Final = 2
"""Migration kind: a full row-stochastic source-weight matrix (continuous)."""

MIGRATION_SCALAR_STOCHASTIC: Final = 3
"""Migration kind: `MIGRATION_SCALAR` with a drawn migrant count per deme."""

MIGRATION_MATRIX_STOCHASTIC: Final = 4
"""Migration kind: `MIGRATION_MATRIX` with a drawn migrant count per deme."""

MINIMUM_WIDTH: Final = 8
"""The fewest columns an infinite-alleles block is ever given."""

SHRINK_CHECK_INTERVAL: Final = 256
"""Generations between looks at whether the block is far wider than needed."""

SHRINK_FACTOR: Final = 4
"""The block shrinks when its width is at least this many times the need."""

DEFAULT_MEMORY_CEILING_BYTES: Final = 2 * 1024**3
"""Default per-replicate ceiling on the block, in bytes (2 GiB)."""

MEMORY_CEILING_ENVIRONMENT_VARIABLE: Final = "FIM_VECTOR_MEMORY_CEILING_BYTES"
"""Environment variable that overrides `DEFAULT_MEMORY_CEILING_BYTES`."""

MODE_CACHE_LIMIT_ENTRIES: Final = 8 * 1024 * 1024
"""Most entries (8 bytes each) the mode-PMF cache may hold; above it, uncached."""

_BYTES_PER_CELL: Final = 16
_BYTES_PER_ID: Final = 8
_BYTES_PER_MINTED_ENTRY: Final = 9
_GIBIBYTE: Final = 1024**3
_ABSURD_BYTES: Final = 2**70
_ABSURD_COUNT: Final = 10**12


class VectorMemoryCeilingError(RuntimeError):
    """Backend V's allele table would exceed the per-replicate memory ceiling.

    Raised before the memory is allocated. The message names the size
    that was needed, the ceiling, and what to change.
    """


@dataclass(frozen=True, slots=True)
class VectorMigration:
    """How one generation's migration step runs inside the kernel.

    Args:
        kind: `MIGRATION_NONE`, `MIGRATION_SCALAR`, `MIGRATION_MATRIX`,
            `MIGRATION_SCALAR_STOCHASTIC` or `MIGRATION_MATRIX_STOCHASTIC`.
        rate: The scalar migration rate (scalar kinds).
        weights: The `(demes, demes)` row-stochastic source-weight matrix
            (matrix kinds); an empty `(0, 0)` array otherwise.

    The stochastic kinds (`migrant_sampling: stochastic`) draw one
    binomial migrant count per destination deme per generation, shared by
    every locus, before any locus is blended. The migrant pool itself
    stays the deterministic weighted average of the other demes, exactly
    as `fim.model.operators.migrate` does with a generator.
    """

    kind: int
    rate: float = 0.0
    weights: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))

    @classmethod
    def from_parameter(
        cls,
        migration: float | Sequence[Sequence[float]],
        deme_count: int,
        *,
        stochastic: bool = False,
    ) -> VectorMigration:
        """Build the plan for a `SimulationParams.m` value.

        Args:
            migration: A scalar rate or a full migration matrix.
            deme_count: The number of demes.
            stochastic: Whether `migrant_sampling` is `"stochastic"`.

        Returns:
            The matching plan. A scalar rate of zero, or a single deme,
            blends nothing and draws nothing, exactly as
            `operators.migrate` returns the state unchanged in those
            cases, whatever the sampling. A matrix always blends, even an
            identity one, because `operators` normalizes its rows.
        """
        if isinstance(migration, int | float):
            rate = float(migration)
            if rate == 0.0 or deme_count == 1:
                return cls(MIGRATION_NONE)
            kind = MIGRATION_SCALAR_STOCHASTIC if stochastic else MIGRATION_SCALAR
            return cls(kind, rate=rate)
        weights = np.ascontiguousarray(np.asarray(migration, dtype=np.float64))
        kind = MIGRATION_MATRIX_STOCHASTIC if stochastic else MIGRATION_MATRIX
        return cls(kind, weights=weights)


def kernels() -> ModuleType:
    """Import and return the compiled kernel module (needs Numba).

    Imported here, not at module level, so that Numba stays optional for
    everything that does not run Backend V.

    Raises:
        ImportError: If Numba is not installed.
    """
    from fim.model import vector_kernels  # noqa: PLC0415 -- optional dependency

    return vector_kernels


def estimated_block_bytes(
    loci: int, demes: int, width: int, *, finite: bool = False
) -> int:
    """Return the memory a block of this shape needs, in bytes.

    Counts the frequency and count cells, the id table and, for finite
    alleles, the minted-state bookkeeping. The kernel's own per-call
    scratch is a few vectors of `width` entries and is not counted.

    Args:
        loci: Number of loci.
        demes: Number of demes.
        width: Columns per locus.
        finite: Whether the finite-alleles bookkeeping is included.

    Returns:
        The size in bytes (an `int`, which may exceed 64 bits for an
        astronomically wide finite-alleles locus).
    """
    total = loci * demes * width * _BYTES_PER_CELL + loci * width * _BYTES_PER_ID
    if finite:
        total += loci * width * _BYTES_PER_MINTED_ENTRY
    return total


def resolve_memory_ceiling(explicit: int | None = None) -> int:
    """Return the per-replicate memory ceiling in bytes.

    Args:
        explicit: A ceiling given by the caller, which wins.

    Returns:
        `explicit` when given; otherwise the value of the
        `FIM_VECTOR_MEMORY_CEILING_BYTES` environment variable when set;
        otherwise `DEFAULT_MEMORY_CEILING_BYTES`.

    Raises:
        ValueError: If the value is not a positive whole number of bytes.
    """
    if explicit is not None:
        value: object = explicit
        source = "memory_ceiling_bytes"
    elif MEMORY_CEILING_ENVIRONMENT_VARIABLE in os.environ:
        value = os.environ[MEMORY_CEILING_ENVIRONMENT_VARIABLE]
        source = MEMORY_CEILING_ENVIRONMENT_VARIABLE
    else:
        return DEFAULT_MEMORY_CEILING_BYTES
    try:
        ceiling = int(str(value))
    except ValueError:
        ceiling = 0
    if isinstance(value, bool) or ceiling < 1:
        raise ValueError(f"{source} must be a positive whole number of bytes")
    return ceiling


def _check_memory(
    *,
    loci: int,
    demes: int,
    width: int,
    finite: bool,
    ceiling: int,
    reason: str,
) -> None:
    """Raise `VectorMemoryCeilingError` when a block would not fit.

    Args:
        loci: Number of loci.
        demes: Number of demes.
        width: Columns per locus the block needs.
        finite: Whether the model is finite alleles.
        ceiling: The per-replicate ceiling in bytes.
        reason: A short phrase saying what needs the columns, for the
            message.
    """
    needed = estimated_block_bytes(loci, demes, width, finite=finite)
    if needed <= ceiling:
        return
    if finite:
        remedies = (
            "Shorten the locus (its capacity is 4 ** length), reduce the "
            "number of loci or demes, "
        )
    else:
        remedies = (
            "Reduce the number of loci or demes, lower the mutation rate or "
            "the deme sizes so fewer alleles are alive at once, "
        )
    raise VectorMemoryCeilingError(
        f"engine_backend 'generational-vector' needs about "
        f"{_describe_bytes(needed)} per replicate for {loci} loci x "
        f"{demes} demes x {_describe_count(width)} columns ({reason}), above "
        f"the ceiling of {_describe_bytes(ceiling)}. {remedies}"
        f"choose engine_backend 'generational' (the dictionary-based engine, "
        f"which gives identical results), or raise the ceiling with the "
        f"{MEMORY_CEILING_ENVIRONMENT_VARIABLE} environment variable (bytes) "
        f"if the memory is available. With several replicates running at "
        f"once, each holds its own table: max_concurrent_replicates bounds "
        f"how many."
    )


def _describe_bytes(size: int) -> str:
    """Return a byte count for a message, as GiB (or 'more than any machine')."""
    if size >= _ABSURD_BYTES:
        return "far more memory than any machine has"
    return f"{size / _GIBIBYTE:.2f} GiB"


def _describe_count(count: int) -> str:
    """Return a column count for a message, abbreviating astronomical ones."""
    if count < _ABSURD_COUNT:
        return str(count)
    return f"about 10^{len(str(count)) - 1}"


def _mode_cache_for(
    mutation_rates: np.ndarray, sizes: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Build the per-rate cache of binomial mode probabilities.

    A locus's mutation probability never changes during a run and its
    gene-copy counts never exceed the largest deme, so each mutation-count
    draw's mode probability depends on the count alone. One cache row per
    distinct nonzero rate holds it, indexed by count (`nan` = not yet
    computed); `rate_class[locus]` names the row, or is `-1` for a locus
    that never mutates. When the cache would exceed
    `MODE_CACHE_LIMIT_ENTRIES` it is disabled (every class `-1`, an empty
    cache) and the draws are made without it: identical, only slower.

    Args:
        mutation_rates: `(loci,)` per-copy mutation probability per locus.
        sizes: `(demes,)` gene copies per deme.

    Returns:
        `(rate_class, mode_cache)` as `kernels.run_generation` takes them.
    """
    rates = sorted({float(rate) for rate in mutation_rates.tolist() if rate > 0.0})
    columns = int(sizes.max()) + 1
    if not rates or len(rates) * columns > MODE_CACHE_LIMIT_ENTRIES:
        return (
            np.full(mutation_rates.shape[0], -1, dtype=np.int64),
            np.zeros((0, 0), dtype=np.float64),
        )
    index = {rate: position for position, rate in enumerate(rates)}
    rate_class = np.array(
        [index.get(float(rate), -1) for rate in mutation_rates.tolist()],
        dtype=np.int64,
    )
    return rate_class, np.full((len(rates), columns), np.nan, dtype=np.float64)


def _power_of_two_at_least(value: int) -> int:
    """Return the smallest power of two that is at least `value`."""
    width = 1
    while width < value:
        width *= 2
    return width


class VectorBlock:
    """One replicate's dense allele table for every locus, and its stepping.

    Build one with `from_model_state`, advance it with `advance`, and read
    it through `rows`, `to_model_state`, `frequency_maps` and
    `locus_statistics`. The arrays are public by design (the kernels and
    tests read them), but only this class changes them.

    Attributes:
        loci: The tracked loci, in column order of the first axis.
        mutation_model: `"infinite_alleles"` or `"finite_alleles"`.
        freq: `(loci, demes, width)` float64 frequencies.
        counts: `(loci, demes, width)` int64 gene-copy counts.
        ids: `(loci, width)` int64 allele id of each column.
        ncol: `(loci,)` int64 live columns per locus.
        sizes: `(demes,)` int64 gene copies per deme.
        mutation_rates: `(loci,)` float64 per-copy mutation probability.
        migration: The migration plan.
        generation: The generation the table currently holds.
        grow_events: How many times the width has doubled.
        shrink_events: How many times the width has been halved or more.
    """

    def __init__(
        self,
        *,
        loci: tuple[LocusSpec, ...],
        mutation_model: str,
        freq: np.ndarray,
        counts: np.ndarray,
        ids: np.ndarray,
        ncol: np.ndarray,
        sizes: np.ndarray,
        mutation_rates: np.ndarray,
        migration: VectorMigration,
        next_id: int,
        generation: int,
        memory_ceiling_bytes: int,
        capacities: np.ndarray | None = None,
        minted_mask: np.ndarray | None = None,
        minted_list: np.ndarray | None = None,
        minted_count: np.ndarray | None = None,
    ) -> None:
        """Wrap already-built arrays; use `from_model_state` to build them.

        Args:
            loci: The tracked loci.
            mutation_model: `"infinite_alleles"` or `"finite_alleles"`.
            freq: `(loci, demes, width)` frequencies.
            counts: `(loci, demes, width)` gene-copy counts (scratch).
            ids: `(loci, width)` allele id per column.
            ncol: Live columns per locus.
            sizes: Gene copies per deme.
            mutation_rates: Per-copy mutation probability per locus.
            migration: The migration plan.
            next_id: The next unused allele id (infinite alleles).
            generation: The generation the arrays hold.
            memory_ceiling_bytes: Per-replicate ceiling used when growing.
            capacities: Finite alleles only: locus capacities (`4 ** length`).
            minted_mask: Finite alleles only: `(loci, width)` bool, minted.
            minted_list: Finite alleles only: `(loci, width)` minted order.
            minted_count: Finite alleles only: minted states per locus.
        """
        self.loci = loci
        self.mutation_model = mutation_model
        self.freq = freq
        self.counts = counts
        self.ids = ids
        self.ncol = ncol
        self.sizes = sizes
        self.mutation_rates = mutation_rates
        self.migration = migration
        self.generation = generation
        self.memory_ceiling_bytes = memory_ceiling_bytes
        self.grow_events = 0
        self.shrink_events = 0
        self._finite = capacities is not None
        self._capacities = (
            capacities if capacities is not None else np.zeros(0, dtype=np.int64)
        )
        self._minted_mask = (
            minted_mask if minted_mask is not None else np.zeros((0, 0), np.bool_)
        )
        self._minted_list = (
            minted_list if minted_list is not None else np.zeros((0, 0), np.int64)
        )
        self._minted_count = (
            minted_count if minted_count is not None else np.zeros(0, np.int64)
        )
        self._mutants = np.zeros((freq.shape[0], freq.shape[1]), dtype=np.int64)
        self._rate_class, self._mode_cache = _mode_cache_for(mutation_rates, sizes)
        self._next_id = np.array([next_id], dtype=np.int64)
        self._locus_ids = np.array([locus.locus_id for locus in loci], dtype=np.int64)
        self._steps_since_shrink_check = 0

    @classmethod
    def from_model_state(
        cls,
        state: ModelState,
        *,
        sizes: Sequence[int],
        mutation_rates: Sequence[float],
        migration: VectorMigration,
        mutation_model: str,
        next_id: int,
        memory_ceiling_bytes: int | None = None,
    ) -> VectorBlock:
        """Build a block holding exactly the frequencies of `state`.

        Under infinite alleles each locus's columns are the sorted union
        of the allele ids present in any deme. Under finite alleles the
        column of an allele is its id, every locus gets its full capacity
        of columns, and the minted bookkeeping starts from the ids present
        now, as `FiniteAlleleSpace` does.

        Args:
            state: The generation to hold (typically generation zero).
            sizes: Gene copies per deme.
            mutation_rates: Per-copy mutation probability per locus.
            migration: The migration plan.
            mutation_model: `"infinite_alleles"` or `"finite_alleles"`.
            next_id: The next unused allele id (the registry counter).
            memory_ceiling_bytes: Per-replicate ceiling; `None` uses
                `resolve_memory_ceiling`.

        Returns:
            The block.

        Raises:
            ValueError: For an unknown mutation model, or (finite alleles)
                an allele id outside `0 .. capacity - 1`.
            VectorMemoryCeilingError: If the block would not fit.
        """
        if mutation_model not in {"infinite_alleles", "finite_alleles"}:
            raise ValueError(f"unknown mutation model: {mutation_model!r}")
        ceiling = resolve_memory_ceiling(memory_ceiling_bytes)
        finite = mutation_model == "finite_alleles"
        loci_count = state.locus_count
        demes = state.deme_count
        present: list[list[int]] = [
            sorted(
                {
                    int(allele_id)
                    for deme in state.frequencies
                    for allele_id in deme[locus_index]
                }
            )
            for locus_index in range(loci_count)
        ]
        capacities: list[int] = []
        if finite:
            capacities = [finite_allele_capacity(locus.length) for locus in state.loci]
            width = max(capacities)
            reason = "the largest locus capacity, 4 ** length"
        else:
            widest = max(len(ids) for ids in present)
            width = max(MINIMUM_WIDTH, _power_of_two_at_least(2 * widest))
            reason = "twice the live alleles at generation zero"
        _check_memory(
            loci=loci_count,
            demes=demes,
            width=width,
            finite=finite,
            ceiling=ceiling,
            reason=reason,
        )
        freq = np.zeros((loci_count, demes, width), dtype=np.float64)
        ids = np.zeros((loci_count, width), dtype=np.int64)
        ncol = np.zeros(loci_count, dtype=np.int64)
        extra: dict[str, np.ndarray] = {}
        if finite:
            minted_mask = np.zeros((loci_count, width), dtype=np.bool_)
            minted_list = np.zeros((loci_count, width), dtype=np.int64)
            minted_count = np.zeros(loci_count, dtype=np.int64)
            for locus_index, capacity in enumerate(capacities):
                if present[locus_index] and (
                    present[locus_index][0] < 0 or present[locus_index][-1] >= capacity
                ):
                    raise ValueError(
                        f"allele id outside 0..{capacity - 1} at locus "
                        f"{state.loci[locus_index].locus_id}"
                    )
                ids[locus_index, :capacity] = np.arange(capacity, dtype=np.int64)
                ncol[locus_index] = capacity
                count = len(present[locus_index])
                minted_list[locus_index, :count] = present[locus_index]
                minted_mask[locus_index, present[locus_index]] = True
                minted_count[locus_index] = count
            extra = {
                "capacities": np.array(capacities, dtype=np.int64),
                "minted_mask": minted_mask,
                "minted_list": minted_list,
                "minted_count": minted_count,
            }
        else:
            for locus_index, allele_ids in enumerate(present):
                ids[locus_index, : len(allele_ids)] = allele_ids
                ncol[locus_index] = len(allele_ids)
        for deme_index, deme in enumerate(state.frequencies):
            for locus_index in range(loci_count):
                column_of = {
                    allele_id: column
                    for column, allele_id in enumerate(
                        ids[locus_index, : ncol[locus_index]].tolist()
                    )
                }
                for allele_id, frequency in deme[locus_index].items():
                    freq[locus_index, deme_index, column_of[int(allele_id)]] = frequency
        return cls(
            loci=state.loci,
            mutation_model=mutation_model,
            freq=freq,
            counts=np.zeros(freq.shape, dtype=np.int64),
            ids=ids,
            ncol=ncol,
            sizes=np.asarray(sizes, dtype=np.int64),
            mutation_rates=np.asarray(mutation_rates, dtype=np.float64),
            migration=migration,
            next_id=next_id,
            generation=state.generation,
            memory_ceiling_bytes=ceiling,
            **extra,
        )

    @property
    def width(self) -> int:
        """Columns allocated per locus."""
        return int(self.freq.shape[2])

    @property
    def deme_count(self) -> int:
        """Number of demes."""
        return int(self.freq.shape[1])

    @property
    def next_id(self) -> int:
        """The next allele id the block would hand out (infinite alleles)."""
        return int(self._next_id[0])

    @property
    def nbytes(self) -> int:
        """Memory held by the block's own arrays, in bytes."""
        return int(
            self.freq.nbytes
            + self.counts.nbytes
            + self.ids.nbytes
            + self._minted_mask.nbytes
            + self._minted_list.nbytes
        )

    def advance(self, rng: np.random.Generator) -> None:
        """Advance one generation: migrate, drift, then mutate, all loci.

        Args:
            rng: The run's random generator; the draws are those
                `operators.step` would make from the same stream position.

        Raises:
            VectorMemoryCeilingError: If an infinite-alleles locus needs
                more columns than the ceiling allows.
            RuntimeError: If a finite-alleles locus has no unminted state
                left to target (the same guard `FiniteAlleleSpace` has).
        """
        compiled = kernels()
        status = int(
            compiled.run_generation(
                self.freq,
                self.counts,
                self.ids,
                self.ncol,
                self.sizes,
                self.mutation_rates,
                self._mutants,
                rng,
                self.migration.kind,
                self.migration.rate,
                self.migration.weights,
                self._rate_class,
                self._mode_cache,
                self._capacities,
                self._minted_mask,
                self._minted_list,
                self._minted_count,
                self._next_id,
            )
        )
        if status < 0:
            locus = self.loci[-status - 1]
            raise RuntimeError(
                "finite allele space has no unminted state left to target "
                f"at locus {locus.locus_id}"
            )
        if status > 0:
            # The columns after compaction cannot hold the new mutants:
            # grow, then finish the generation by minting.
            self._grow(status)
            compiled.mint_columns(
                self.freq,
                self.counts,
                self.ids,
                self.ncol,
                self.sizes,
                self._mutants,
                self._next_id,
            )
        self.generation += 1
        if not self._finite:
            self._steps_since_shrink_check += 1
            if self._steps_since_shrink_check >= SHRINK_CHECK_INTERVAL:
                self._steps_since_shrink_check = 0
                self._shrink_if_sparse()

    def _grow(self, needed: int) -> None:
        """Double the width until `needed` columns fit, within the ceiling."""
        width = self.width
        while width < needed:
            width *= 2
        _check_memory(
            loci=int(self.freq.shape[0]),
            demes=self.deme_count,
            width=width,
            finite=False,
            ceiling=self.memory_ceiling_bytes,
            reason=f"{needed} columns are alive or newly mutated at one locus",
        )
        pad = width - self.width
        self.freq = np.pad(self.freq, ((0, 0), (0, 0), (0, pad)))
        self.counts = np.pad(self.counts, ((0, 0), (0, 0), (0, pad)))
        self.ids = np.pad(self.ids, ((0, 0), (0, pad)))
        self.grow_events += 1

    def _shrink_if_sparse(self) -> None:
        """Halve the width while the live columns use a small fraction of it."""
        widest = int(self.ncol.max())
        target = max(MINIMUM_WIDTH, _power_of_two_at_least(2 * widest))
        if self.width < SHRINK_FACTOR * target:
            return
        self.freq = np.ascontiguousarray(self.freq[:, :, :target])
        self.counts = np.ascontiguousarray(self.counts[:, :, :target])
        self.ids = np.ascontiguousarray(self.ids[:, :target])
        self.shrink_events += 1

    def minted_states(self, locus_index: int) -> tuple[int, ...]:
        """Return every state ever minted at a finite-alleles locus, in order.

        The same list `FiniteAlleleSpace` keeps: the ids present at
        generation zero (ascending), then each state first reached by a
        mutation, in the order reached. A state that has since gone
        extinct stays in it, which is what makes the K-allele
        recurrence probability depend on history, not on the state alone.

        Args:
            locus_index: Zero-based locus position.

        Raises:
            ValueError: Under infinite alleles, which has no such list.
        """
        if not self._finite:
            raise ValueError("minted states are tracked only under finite alleles")
        count = int(self._minted_count[locus_index])
        return tuple(self._minted_list[locus_index, :count].tolist())

    def present_entries(self) -> tuple[np.ndarray, ...]:
        """Return `(deme, locus_id, allele_id, frequency)` arrays of present alleles.

        In `ModelState.to_rows` order: deme-major, then locus, then
        ascending allele id. Demes are one-based.
        """
        return tuple(
            kernels().present_entries(self.freq, self.ids, self.ncol, self._locus_ids)
        )

    def rows(self, run_id: str) -> list[dict[str, int | float | str]]:
        """Return the trajectory rows `ModelState.to_rows` would return.

        Same rows, same field order, same order of rows, so a store cannot
        tell which produced them.

        Args:
            run_id: Stable identifier grouping rows from one simulation.

        Raises:
            ValueError: If `run_id` is empty.
        """
        if not run_id:
            raise ValueError("run_id must not be empty")
        demes, locus_ids, allele_ids, frequencies = self.present_entries()
        generation = self.generation
        return [
            {
                "run_id": run_id,
                "generation": generation,
                "deme": deme,
                "locus_id": locus_id,
                "allele_id": allele_id,
                "frequency": frequency,
            }
            for deme, locus_id, allele_id, frequency in zip(
                demes.tolist(),
                locus_ids.tolist(),
                allele_ids.tolist(),
                frequencies.tolist(),
                strict=True,
            )
        ]

    def frequency_maps(self, locus_index: int) -> list[dict[int, float]]:
        """Return one locus's per-deme `{allele_id: frequency}` maps.

        Present alleles only, in ascending id order: the input
        `statistics_report` takes.

        Args:
            locus_index: Zero-based locus position.
        """
        columns = int(self.ncol[locus_index])
        ids = self.ids[locus_index, :columns]
        maps: list[dict[int, float]] = []
        for deme in range(self.deme_count):
            row = self.freq[locus_index, deme, :columns]
            present = np.flatnonzero(row)
            maps.append(
                dict(zip(ids[present].tolist(), row[present].tolist(), strict=True))
            )
        return maps

    def locus_statistics(self) -> tuple[np.ndarray, np.ndarray]:
        """Compute `H_S`, `H_T`, `H_ST`, `G_ST` and `D` for every locus.

        Returns:
            `(table, status)`: `table` is `(loci, 5)` float64 in that
            column order (`G_ST` is `nan` where undefined); `status` is
            `(loci,)` int64, non-zero where `statistics_report` would have
            raised (the caller then uses it, to raise the real error).
        """
        loci = int(self.freq.shape[0])
        table = np.zeros((loci, 5), dtype=np.float64)
        status = np.zeros(loci, dtype=np.int64)
        kernels().locus_statistics(self.freq, self.ncol, table, status)
        return table, status

    def to_model_state(self) -> ModelState:
        """Return the table as a `ModelState`, ascending ids in every map."""
        demes: list[tuple[dict[AlleleId, float], ...]] = []
        for deme in range(self.deme_count):
            locus_maps: list[dict[AlleleId, float]] = []
            for locus_index in range(int(self.freq.shape[0])):
                columns = int(self.ncol[locus_index])
                row = self.freq[locus_index, deme, :columns]
                present = np.flatnonzero(row)
                locus_maps.append(
                    {
                        AlleleId(allele_id): frequency
                        for allele_id, frequency in zip(
                            self.ids[locus_index, :columns][present].tolist(),
                            row[present].tolist(),
                            strict=True,
                        )
                    }
                )
            demes.append(tuple(locus_maps))
        return ModelState(
            loci=self.loci, frequencies=tuple(demes), generation=self.generation
        )

    def sync_registry(self, registry: AlleleRegistry) -> None:
        """Bring `registry`'s counter up to the ids this block has handed out.

        Only meaningful under infinite alleles; a no-op under finite
        alleles, which never mints identities.
        """
        if not self._finite:
            registry.advance_to(self.next_id)
