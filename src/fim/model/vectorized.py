"""Bounded-K (finite-alleles), array-native migrate/mutate/drift.

Scope, deliberately: the finite-alleles mutation model only (bounded
`K = finite_allele_capacity(length)` per locus — see `fim.model.locus`).
Infinite alleles is out of scope here, matching the vector design's own
"V2 before V1" recommendation: under finite alleles, every allele
identity at a locus is already a fixed integer in `0 .. K - 1` for the
whole run, so a dense `(d, K)` array can be allocated once and reused
generation after generation with no reindexing at all — the genuinely
hard problem (an unbounded, per-generation-ragged identity space) that
infinite alleles would require simply does not arise here. Deterministic
("continuous") migration only — `SimulationParams.migrant_sampling ==
"stochastic"` is not supported by this path yet, the same kind of
documented, deliberate scope boundary `fim.model.operators.step`'s own
`jit` argument already draws around `migrate`/`mutate`.

`ModelState`'s own public shape is untouched by anything in this module:
`VectorizedState`, below, is a compute-only, internal representation,
built once from a real `ModelState` and converted back only when a real
`ModelState` is actually needed (`RunResult.final_state`, a persisted
trajectory row). `migrate_vectorized`, `mutate_vectorized`, and
`drift_vectorized` all operate on this same dense representation in
sequence, with no `ModelState` round-trip between them — which is the
entire point: the Python-level marshaling to and from `ModelState`'s own
sparse dict-of-dicts representation, not the random draw itself, is what
`20260901-claude-sonnet-5-fim-engine-backend-factory-design.md` §5.3
found actually dominates `fim.model.operators.drift`'s own wall-clock
time when it is vectorized or JIT-compiled in isolation, sandwiched
between two still-dict-based stages. Fusing all three stages here is
this project's own first real test of that document's §11 open question
("fusing migrate -> mutate -> drift across stage boundaries").

Statistical, not bit-identical, parity with the dict-based operators in
`fim.model.operators` is this module's own correctness bar throughout
(vector design §6) — every function below names, in its own docstring,
exactly where and why its own random-draw sequence diverges from the
dict-based original.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

import numpy as np

from fim.model.allele import AlleleId
from fim.model.locus import LocusSpec, finite_allele_capacity
from fim.model.state import ModelState

_JIT_MUTATE_TARGETS_BATCHED: (
    Callable[
        [np.random.Generator, np.ndarray, int, np.ndarray, np.ndarray, int, int],
        tuple[np.ndarray, np.ndarray, np.ndarray, int, int],
    ]
    | None
) = None


@dataclass(slots=True)
class VectorizedLocusState:
    """One locus's own dense working state, mid-batch.

    Mirrors `fim.model.allele.FiniteAlleleSpace`'s own bookkeeping
    (`_minted`/`_minted_set`/`_next_unminted`) in array form instead of a
    Python list/set, so it can be threaded through a JIT-compiled target-
    selection call (`_jit_mutate_targets_batched`) — this state is
    entirely independent of any real `FiniteAlleleSpace` instance;
    `build_vectorized_state`, below, reconstructs it directly from a
    `ModelState`'s own generation-zero allele ids, the same way
    `FiniteAlleleSpace.__init__` does.
    """

    frequencies: np.ndarray  # (d, capacity) float64, each row sums to 1.0
    capacity: int
    minted_mask: np.ndarray  # (capacity,) bool — has this state ever appeared
    minted_list: np.ndarray  # (capacity,) int64 — minted ids, mint order
    minted_count: int
    next_unminted: int


@dataclass(slots=True)
class VectorizedState:
    """A whole state's own dense working representation, one locus at a time."""

    loci: tuple[LocusSpec, ...]
    locus_states: tuple[VectorizedLocusState, ...]
    generation: int


def build_vectorized_state(state: ModelState) -> VectorizedState:
    """Build a `VectorizedState` from a real `ModelState`.

    The one place this module's own array representation is built from
    (or, via `vectorized_state_to_model_state`, converted back to)
    `ModelState`'s own sparse shape — everything between the two stays
    array-native. Every locus's own `capacity` is fixed for the run
    (`finite_allele_capacity`); the initial minted set is exactly the
    allele ids already present at generation zero, mirroring
    `fim.engine._build_finite_allele_spaces`'s own construction.

    Raises:
        ValueError: If any allele id already present is outside
            `0 .. capacity - 1` — the same bounds `FiniteAlleleSpace`
            itself enforces at construction.
    """
    locus_states = []
    for locus_index, locus in enumerate(state.loci):
        capacity = finite_allele_capacity(locus.length)
        deme_count = state.deme_count
        frequencies = np.zeros((deme_count, capacity), dtype=np.float64)
        minted_ids: set[int] = set()
        for deme_index, deme in enumerate(state.frequencies):
            for allele_id, frequency in deme[locus_index].items():
                if not (0 <= int(allele_id) < capacity):
                    raise ValueError(
                        f"allele id {int(allele_id)} outside 0..{capacity - 1} "
                        f"at locus {locus.locus_id}"
                    )
                frequencies[deme_index, int(allele_id)] = frequency
                minted_ids.add(int(allele_id))
        sorted_minted = sorted(minted_ids)
        minted_mask = np.zeros(capacity, dtype=np.bool_)
        minted_mask[sorted_minted] = True
        minted_list = np.zeros(capacity, dtype=np.int64)
        minted_list[: len(sorted_minted)] = sorted_minted
        locus_states.append(
            VectorizedLocusState(
                frequencies=frequencies,
                capacity=capacity,
                minted_mask=minted_mask,
                minted_list=minted_list,
                minted_count=len(sorted_minted),
                next_unminted=0,
            )
        )
    return VectorizedState(
        loci=state.loci,
        locus_states=tuple(locus_states),
        generation=state.generation,
    )


def vectorized_state_to_model_state(state: VectorizedState) -> ModelState:
    """Convert back to `ModelState`'s own public, sparse shape.

    Called only when a real `ModelState` is actually needed (a run's own
    `final_state`, or a caller outside this module) — not on every
    generation, which is what keeps `migrate_vectorized`/
    `mutate_vectorized`/`drift_vectorized` genuinely fused.
    """
    deme_count = state.locus_states[0].frequencies.shape[0] if state.locus_states else 0
    demes = []
    for deme_index in range(deme_count):
        locus_maps = []
        for locus_state in state.locus_states:
            row = locus_state.frequencies[deme_index]
            locus_maps.append(
                {
                    AlleleId(allele_id): float(frequency)
                    for allele_id, frequency in enumerate(row)
                    if frequency
                }
            )
        demes.append(tuple(locus_maps))
    return ModelState(
        loci=state.loci,
        frequencies=tuple(demes),
        generation=state.generation,
    )


def vectorized_state_to_rows(
    state: VectorizedState, run_id: str
) -> list[dict[str, int | float | str]]:
    """Serialize directly to the public trajectory row schema.

    The array-native counterpart to `ModelState.to_rows` — deliberately
    bypasses `vectorized_state_to_model_state` (no sparse dict-of-dicts
    construction just to immediately flatten it back into rows), the
    same "stay array-native as long as possible" principle this whole
    module is built around. Row shape, field order, and the same
    one-based `deme`/`locus_id` numbering match `ModelState.to_rows`
    exactly, so a caller (a `TrajectoryStore`) cannot tell which path
    produced a given row.
    """
    if not run_id:
        raise ValueError("run_id must not be empty")
    rows: list[dict[str, int | float | str]] = []
    deme_count = state.locus_states[0].frequencies.shape[0] if state.locus_states else 0
    for deme_index in range(deme_count):
        for locus, locus_state in zip(state.loci, state.locus_states, strict=True):
            row = locus_state.frequencies[deme_index]
            nonzero = np.flatnonzero(row)
            rows.extend(
                {
                    "run_id": run_id,
                    "generation": state.generation,
                    "deme": deme_index + 1,
                    "locus_id": locus.locus_id,
                    "allele_id": int(allele_id),
                    "frequency": float(row[allele_id]),
                }
                for allele_id in nonzero
            )
    return rows


def _vectorized_multinomial_rows(
    rng: np.random.Generator, sizes: np.ndarray, probabilities: np.ndarray
) -> np.ndarray:
    """Draw one multinomial sample per row, each row its own `p` vector.

    NumPy has no batched multinomial with a different `pvals` per row —
    only a shared one across `size` independent draws (`drift`'s own
    `fim.model.operators` docstring covers this same gap for the
    per-pair, dict-based case). This reproduces the same distribution,
    per row, via the multinomial-as-sequential-conditional-binomial
    identity (`fim.model.operators._multinomial_via_binomial`'s own
    docstring — the same identity, applied here across an entire `(d,
    capacity)` array at once via array-valued `rng.binomial` calls,
    rather than to one row at a time). Array-valued `rng.binomial` does
    not compile under Numba (confirmed directly during Stage F5's own
    spike), so this stays plain NumPy — already fast, since each of the
    `capacity - 1` calls is one native, C-level, vectorized draw across
    every row at once, not a Python-level loop over rows.

    Args:
        rng: The run's explicitly threaded random generator.
        sizes: `(d,)` int64 — each row's own total count.
        probabilities: `(d, capacity)` row-stochastic (a row with no
            gene copies at all may be all zero).

    Returns:
        `(d, capacity)` int64 counts, each row summing to that row's
        own `sizes` entry.
    """
    deme_count, capacity = probabilities.shape
    remaining_n = sizes.astype(np.int64).copy()
    remaining_p = np.ones(deme_count, dtype=np.float64)
    counts = np.empty((deme_count, capacity), dtype=np.int64)
    for column in range(capacity - 1):
        column_p = np.divide(
            probabilities[:, column],
            remaining_p,
            out=np.zeros(deme_count, dtype=np.float64),
            where=remaining_p > 0,
        )
        drawn = rng.binomial(remaining_n, np.clip(column_p, 0.0, 1.0))
        counts[:, column] = drawn
        remaining_n -= drawn
        remaining_p -= probabilities[:, column]
    counts[:, -1] = remaining_n
    return counts


def migrate_vectorized(
    locus_state: VectorizedLocusState, weights: np.ndarray
) -> VectorizedLocusState:
    """Blend every deme with the migrant pool via one dense matmul.

    `F_next = W @ F` — `weights` (`W`, `(d, d)`, row-stochastic:
    self-retention on the diagonal, migrant weights off-diagonal) is
    exactly what `fim.model.operators._migrate_matrix`'s own deterministic
    branch already computes row by row (`blended[allele] =
    sum_source(weight * source_frequency[allele])`); this is the
    identical computation, as the one matrix product the vector design's
    own §5.1 names as the natural generalization. Deterministic
    ("continuous") migration only — see this module's own docstring.
    """
    return replace(locus_state, frequencies=weights @ locus_state.frequencies)


def symmetric_migration_weights(rate: float, sizes: np.ndarray) -> np.ndarray:
    """Build the dense `(d, d)` weight matrix for a scalar migration rate.

    Derived directly from `fim.model.operators._migrate_symmetric`'s own
    deterministic formula: `pool_i(a) = (total_mass(a) - size_i *
    local_i(a)) / (total_size - size_i)`, `blended_i(a) = (1 - rate) *
    local_i(a) + rate * pool_i(a)`. Expanding `pool_i` in terms of every
    other deme's own frequency gives `W[i, i] = 1 - rate` and, for `j !=
    i`, `W[i, j] = rate * size_j / (total_size - size_i)` — row `i` sums
    to exactly 1 by construction (the migrant weights alone sum to
    `rate`, since `sum_{j != i} size_j == total_size - size_i`).
    """
    deme_count = sizes.shape[0]
    total_size = float(sizes.sum())
    weights = np.zeros((deme_count, deme_count), dtype=np.float64)
    for destination in range(deme_count):
        other_weight = total_size - sizes[destination]
        for source in range(deme_count):
            if source == destination:
                weights[destination, source] = 1.0 - rate
            else:
                weights[destination, source] = rate * sizes[source] / other_weight
    return weights


def mutate_vectorized(
    locus_state: VectorizedLocusState,
    sizes: np.ndarray,
    rate: float,
    rng: np.random.Generator,
) -> VectorizedLocusState:
    """Replace a binomially sampled number of copies with new-or-recurring alleles.

    The array-native counterpart to `fim.model.operators.mutate`'s own
    finite-alleles branch — same three steps (event count, source
    attribution, target selection), same underlying distributions, but
    not the same random-draw *sequence*: event counts and source
    attribution are drawn with array-valued calls across every deme at
    once rather than one deme at a time, and target selection processes
    every mutating copy across the *whole locus* in one JIT-compiled pass
    (`_jit_mutate_targets_batched`) rather than one dict-based call per
    event. Statistically equivalent, not bit-identical — this module's
    own correctness bar throughout (see the module docstring).

    Target selection specifically preserves `FiniteAlleleSpace.
    mutate_target`'s own real, load-bearing choice — a recurrence
    probability that grows as more of the bounded state space fills up,
    which is what lets the finite-alleles model recover infinite-alleles
    behavior as capacity grows (its own docstring) — while replacing its
    "filter the minted list, then index" mechanics with an equivalent
    rejection-sampling form (draw uniformly among currently-minted
    states, redraw only on the rare hit against the excluded source):
    provably the same uniform-over-the-remaining-states distribution,
    array/JIT-friendly where the original's own Python-list filtering is
    not.
    """
    event_counts = rng.binomial(sizes, rate)
    if not event_counts.any():
        return locus_state
    retained_mass = 1.0 - event_counts / sizes
    new_frequencies = locus_state.frequencies * retained_mass[:, None]

    source_counts = _vectorized_multinomial_rows(
        rng, event_counts, locus_state.frequencies
    )
    event_deme, event_source = np.nonzero(source_counts)
    counts = source_counts[event_deme, event_source]
    event_deme = np.repeat(event_deme, counts)
    event_source = np.repeat(event_source, counts)

    targets, minted_mask, minted_list, minted_count, next_unminted = (
        _jit_mutate_targets_batched(
            rng,
            event_source.astype(np.int64),
            locus_state.capacity,
            locus_state.minted_mask.copy(),
            locus_state.minted_list.copy(),
            locus_state.minted_count,
            locus_state.next_unminted,
        )
    )
    event_frequency = 1.0 / sizes[event_deme]
    np.add.at(new_frequencies, (event_deme, targets), event_frequency)

    return VectorizedLocusState(
        frequencies=new_frequencies,
        capacity=locus_state.capacity,
        minted_mask=minted_mask,
        minted_list=minted_list,
        minted_count=minted_count,
        next_unminted=next_unminted,
    )


def _mutate_targets_batched(
    rng: np.random.Generator,
    sources: np.ndarray,
    capacity: int,
    minted_mask: np.ndarray,
    minted_list: np.ndarray,
    minted_count: int,
    next_unminted: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int, int]:
    """Choose one mutation target per event, in order, updating minted state as it goes.

    A direct array/loop translation of `FiniteAlleleSpace.mutate_target`,
    called once per event in `sources` rather than once from `mutate`'s
    own dict-based loop — see `mutate_vectorized`'s own docstring for
    what is, and is not, preserved exactly.

    Args:
        rng: The run's explicitly threaded random generator.
        sources: `(events,)` int64 — each mutating copy's own source
            allele id, in visiting order.
        capacity: This locus's own fixed state-space size, `K`.
        minted_mask: `(capacity,)` bool, mutated in place — pass a copy
            if the caller's own array must stay unchanged.
        minted_list: `(capacity,)` int64, mutated in place — same caveat.
        minted_count: How many of `minted_list`'s own entries are valid.
        next_unminted: The next not-yet-tried candidate state.

    Returns:
        `(targets, minted_mask, minted_list, minted_count, next_unminted)`
        — `targets` is `(events,)` int64, one target per event;
        the rest are `minted_mask`/`minted_list`/`minted_count`/
        `next_unminted` as they stand after every event.
    """
    event_count = sources.shape[0]
    targets = np.empty(event_count, dtype=np.int64)
    for event_index in range(event_count):
        current = sources[event_index]
        recurrence_probability = (minted_count - 1) / (capacity - 1)
        if recurrence_probability > 0.0 and rng.random() < recurrence_probability:
            while True:
                candidate_index = int(rng.integers(0, minted_count))
                candidate = minted_list[candidate_index]
                if candidate != current:
                    targets[event_index] = candidate
                    break
        else:
            while minted_mask[next_unminted]:
                next_unminted += 1
            target = next_unminted
            next_unminted += 1
            minted_list[minted_count] = target
            minted_mask[target] = True
            minted_count += 1
            targets[event_index] = target
    return targets, minted_mask, minted_list, minted_count, next_unminted


def _jit_mutate_targets_batched(
    rng: np.random.Generator,
    sources: np.ndarray,
    capacity: int,
    minted_mask: np.ndarray,
    minted_list: np.ndarray,
    minted_count: int,
    next_unminted: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int, int]:
    """`_mutate_targets_batched`, JIT-compiled with `nogil=True`.

    `numba` is an optional dependency (`pip install fim[jit]`), imported
    here and nowhere else in this module — importing `fim.model.
    vectorized` never requires it. Compiled once, on first call, and
    cached at module level, exactly like `fim.model.operators`'s own
    lazy-JIT helpers.

    Raises:
        ImportError: If `numba` is not installed.
    """
    global _JIT_MUTATE_TARGETS_BATCHED  # noqa: PLW0603
    if _JIT_MUTATE_TARGETS_BATCHED is None:
        import numba  # noqa: PLC0415 -- lazy, optional-dependency import

        _JIT_MUTATE_TARGETS_BATCHED = numba.jit(nogil=True)(_mutate_targets_batched)
    return _JIT_MUTATE_TARGETS_BATCHED(
        rng, sources, capacity, minted_mask, minted_list, minted_count, next_unminted
    )


def drift_vectorized(
    locus_state: VectorizedLocusState, sizes: np.ndarray, rng: np.random.Generator
) -> VectorizedLocusState:
    """Resample `N` gene copies per deme, across every deme in one pass.

    The array-native counterpart to `fim.model.operators.drift` —
    `_vectorized_multinomial_rows` above is the same conditional-binomial
    identity `fim.model.operators._multinomial_via_binomial` uses,
    applied across every row of one dense `(d, capacity)` array at once
    instead of one `rng.multinomial` call per (deme, locus) pair.
    Statistically, not bit-identically, equivalent — see this module's
    own docstring.
    """
    counts = _vectorized_multinomial_rows(rng, sizes, locus_state.frequencies)
    return replace(locus_state, frequencies=counts / sizes[:, None])


def step_vectorized(
    state: VectorizedState,
    weights_per_locus: tuple[np.ndarray, ...],
    mutation_rates: tuple[float, ...],
    sizes: np.ndarray,
    rng: np.random.Generator,
) -> VectorizedState:
    """Advance one generation: migrate, then mutate, then drift, fused.

    The array-native counterpart to `fim.model.operators.step` — every
    locus stays a dense array from this call's own start to its own end,
    across all three stages, with no `ModelState` reconstructed in
    between (the actual thing this whole module exists to test — see the
    module's own docstring). `weights_per_locus`/`mutation_rates` are
    already resolved per locus by the caller (`symmetric_migration_weights`
    or an already-row-stochastic matrix; `SimulationParams.mutation_rates`
    itself already resolves a scalar `mu` to one rate per locus).
    """
    new_locus_states = []
    for locus_state, weights, rate in zip(
        state.locus_states, weights_per_locus, mutation_rates, strict=True
    ):
        migrated = migrate_vectorized(locus_state, weights)
        mutated = mutate_vectorized(migrated, sizes, rate, rng)
        drifted = drift_vectorized(mutated, sizes, rng)
        new_locus_states.append(drifted)
    return VectorizedState(
        loci=state.loci,
        locus_states=tuple(new_locus_states),
        generation=state.generation + 1,
    )
