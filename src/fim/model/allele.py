"""Opaque allele identities and mutant-allele allocation.

An "allele" here is just one distinguishable variant of a gene at one
locus — the simulation never needs to know what that variant's actual
DNA sequence is, only that it is different from every other variant
currently present, so every allele is represented by a plain integer
identifier (`AlleleId`) rather than an actual sequence. What varies
between the project's two mutation models is how a *new* allele ID
gets assigned when a mutation happens:

- The infinite-alleles model assumes every mutation event produces a
  variant that has never existed before anywhere in the run (a
  reasonable approximation once the number of possible DNA sequences
  at a locus vastly exceeds the number of mutation events that will
  ever occur — see `fim.model.locus.finite_allele_capacity`).
  `AlleleRegistry`, below, is this model's allocator: a bare counter
  that simply hands out the next never-used integer each time.
- The finite-alleles ("K-allele") model instead fixes a specific,
  bounded number of possible states per locus up front, so a mutation
  can land on a state some other gene copy elsewhere already carries
  (a "recurrence") rather than always minting something brand new.
  `FiniteAlleleSpace`/`FiniteAlleleRegistry`, below, implement that
  bounded, per-locus allocation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import NewType

import numpy as np

AlleleId = NewType("AlleleId", int)

MINTED_ID_START = 1 << 32

# A single-state space has no "other" state for a mutation to target.
_MINIMUM_FINITE_ALLELE_CAPACITY = 2

# `numpy.random.Generator.integers`'s own documented range: the largest
# exclusive `high` it accepts is `2**63` (signed int64's own ceiling).
# `FiniteAlleleSpace.mutate_target`'s own `capacity` can be far larger —
# `finite_allele_capacity(200)` is `4**200`, the whole reason this
# class's own module docstring calls an astronomical capacity a real,
# intended case, never merely a large `int64` — so drawing a fresh
# mutation target uniformly needs a fallback for capacities beyond what
# `Generator.integers` can be asked for directly.
_MAX_DIRECT_SAMPLE_CAPACITY = 1 << 63


def _uniform_integer_below(rng: np.random.Generator, capacity: int) -> int:
    """Return one integer drawn uniformly from ``0 .. capacity - 1``, any size.

    `rng.integers(0, capacity)` directly whenever `capacity` fits within
    `Generator.integers`'s own supported range — identical draw shape to
    every prior release, unchanged, for every capacity this project's
    own tests exercise before `FIM-46` (this project's own multi-model
    engine review, 2026-09-04) motivated this function's own existence.

    Beyond that range, draws `capacity.bit_length()` random bits at a
    time (`rng.bytes`, an unbiased byte stream) and rejects any value
    `>= capacity` — the standard rejection-sampling construction for a
    uniform integer over an arbitrary-precision range: drawing exactly
    `bit_length` bits gives a candidate range of `[0, 2**bit_length)`,
    strictly less than `2 * capacity`, so better than even odds of
    acceptance on every attempt, and each accepted value is exactly as
    likely as every other by construction (no value in `[0, capacity)`
    is ever preferred over another by this procedure). Needed at all
    only because Python's own arbitrary-precision integers can express
    a `capacity` no fixed-width sampler (`int64` included) can accept as
    an argument, not because uniform sampling over a huge range is
    otherwise difficult.
    """
    if capacity <= _MAX_DIRECT_SAMPLE_CAPACITY:
        return int(rng.integers(0, capacity))
    bit_length = capacity.bit_length()
    byte_length = (bit_length + 7) // 8
    padding_bits = byte_length * 8 - bit_length
    while True:
        candidate = int.from_bytes(rng.bytes(byte_length), "big") >> padding_bits
        if candidate < capacity:
            return candidate


def founding_allele_ids(count: int) -> tuple[AlleleId, ...]:
    """Return the locus-relative founding allele identifiers.

    "Founding" alleles are the variants a population starts with at
    generation zero, before any mutation has had a chance to introduce
    a new one — every gene copy in the starting population's frequency
    table is one of these `count` identifiers, never a mutant one.

    Args:
        count: Number of founding alleles at a locus.

    Returns:
        The identifiers ``0`` through ``count - 1``.

    Raises:
        ValueError: If ``count`` is less than one or reaches the mutant range.
    """
    if count < 1:
        raise ValueError("founding allele count must be at least 1")
    if count >= MINTED_ID_START:
        raise ValueError("founding allele count overlaps the mutant ID range")
    return tuple(AlleleId(index) for index in range(count))


class AlleleRegistry:
    """Allocate globally unique identities for alleles created by mutation.

    The infinite-alleles model's allocator (see this module's own
    docstring, above): every call to `next_id` returns an integer that
    has never been returned before, from this registry or from
    `founding_allele_ids`, so a mutant allele's ID alone is always
    enough to tell it apart from every founding allele and from every
    other mutant, with no possibility of two different mutation events
    ever colliding on the same identifier.
    """

    def __init__(self, start: int = MINTED_ID_START) -> None:
        """Initialize a registry at the first mutant-only identifier.

        Args:
            start: First integer that may be minted.

        Raises:
            ValueError: If ``start`` overlaps the founding-allele range.
        """
        if start < MINTED_ID_START:
            raise ValueError(
                f"mutant allele IDs must start at or above {MINTED_ID_START}"
            )
        self._next = start

    def next_id(self) -> AlleleId:
        """Return a new allele identity that has never been returned before."""
        allele_id = AlleleId(self._next)
        self._next += 1
        return allele_id

    def next_k_ids(self, k: int) -> np.ndarray:
        """Reserve ``k`` consecutive, never-before-returned allele identities.

        The vectorized counterpart to `next_id`, designed but never
        built in `20260829-claude-sonnet-5-fim-vector-design.md` §5.2 —
        built here for `20260901-claude-sonnet-5-fim-engine-backend-
        factory-design.md` §10 item 10e's own stage 3 (batching
        `mutate`'s own infinite-alleles minting across a whole
        generation instead of one `next_id()` call per event). The
        correctness guarantee `next_id`'s own docstring states —
        "every call returns something no earlier call ever returned" —
        constrains only the *reservation*, a single, genuinely
        sequential integer read-modify-write, not how the reserved
        block of `k` identities gets constructed afterward: `k`
        sequential `next_id()` calls and one `next_k_ids(k)` call return
        bit-identical values, in the same order, since both are exactly
        ``base, base + 1, ..., base + k - 1`` — the only difference is
        that this method pays the sequential counter's own cost once,
        not `k` times.

        Args:
            k: Number of identities to reserve. `0` is valid and
                reserves nothing, advancing the counter by zero.

        Returns:
            A `(k,)` `int64` array of consecutive identities, the same
            values `k` consecutive `next_id()` calls would have
            returned, in the same order.

        Raises:
            ValueError: If `k` is negative.

        Noted, not fixed (this project's own multi-model engine review,
        2026-09-04, `FIM-20`/finding Kimi-FIM-20, calls this "minor
        (theoretical)" and prescribes no fix): `base + np.arange(k,
        dtype=np.int64)` computes in fixed-width `int64`, which silently
        wraps around to a negative identity on overflow rather than
        raising, unlike `self._next` itself (a plain Python `int`, never
        fixed-width). Reaching it needs roughly `2**63` calls to `next_
        id`/`next_k_ids` combined over one run's own lifetime — an
        allele-minting rate no realistic configuration this project
        validates for could approach before every other resource
        (wall-clock time not least) already made the run practically
        impossible to finish.
        """
        if k < 0:
            raise ValueError("next_k_ids() requires a non-negative k")
        base = self._next
        self._next += k
        return base + np.arange(k, dtype=np.int64)


class FiniteAlleleSpace:
    """One locus's bounded allele-state space under the K-allele model.

    Implements the standard finite-alleles mutation kernel: every mutation
    event lands uniformly on one of the ``capacity - 1`` states other than
    its source — never the source itself, whether or not that other state
    has already arisen elsewhere in the run. Unlike the infinite-alleles
    model, a target can be a *recurrence* (a state that already exists
    somewhere in the population) rather than always a fresh label.

    The full state space is never materialized, even when astronomically
    large: a target is decided as "one specific already-minted state" or
    "any not-yet-minted state" via a single float probability, computed as
    plain Python division rather than a fixed-width integer draw, so it
    never overflows regardless of ``capacity``. For a large enough
    ``capacity`` relative to how many states have actually been minted so
    far, that probability underflows all the way to an exact ``0.0`` and
    every mutation mints fresh, indistinguishable from the infinite-alleles
    model — recovering it in the limit, as the differentiation-measures
    guide's own approximation argument predicts. At the more moderate
    capacities where this model actually changes anything, the same
    computation instead gives a real, honestly nonzero recurrence
    probability.
    """

    def __init__(self, capacity: int, initial_ids: Iterable[AlleleId]) -> None:
        """Seed one locus's finite state space from its generation-zero alleles.

        Args:
            capacity: Number of possible states, ``K``, at this locus.
            initial_ids: Every allele identity present anywhere (any deme)
                in generation zero at this locus.

        Raises:
            ValueError: If ``capacity`` is fewer than two states (a single
                state has no "other" for a mutation to target), too small
                to hold every initial ID, or an initial ID falls outside
                ``0 .. capacity - 1``.
        """
        if capacity < _MINIMUM_FINITE_ALLELE_CAPACITY:
            raise ValueError(
                f"finite allele capacity must be at least "
                f"{_MINIMUM_FINITE_ALLELE_CAPACITY}"
            )
        minted = sorted({int(allele_id) for allele_id in initial_ids})
        if len(minted) > capacity or any(
            identity < 0 or identity >= capacity for identity in minted
        ):
            raise ValueError(
                "finite allele capacity is too small for the initial allele "
                "IDs present at this locus"
            )
        self._capacity = capacity
        self._minted: list[AlleleId] = [AlleleId(identity) for identity in minted]
        self._minted_set: set[int] = set(minted)
        self._next_unminted = 0

    @property
    def capacity(self) -> int:
        """This locus's own fixed state-space size, ``K``."""
        return self._capacity

    def to_arrays(self) -> tuple[np.ndarray, np.ndarray, int, int]:
        """Export this space's own minted state in dense, array-native form.

        The counterpart `fim.model.vectorized`'s own `_mutate_targets_
        batched`/`_jit_mutate_targets_batched` (built for Backend V,
        proven exactly matching `mutate_target` above) already expects
        as input — see `20260901-claude-sonnet-5-fim-engine-backend-
        factory-design.md` §10 item 10e's own stage 3 entry for why
        `fim.model.operators` keeps its own duplicate of that kernel
        rather than importing it directly (`fim.model.vectorized`
        already imports from `fim.model.operators`, so the reverse
        import would be circular).

        **Only ever call this when `capacity` is small enough that a
        `capacity`-sized array is actually reasonable to build** — both
        returned arrays are `O(capacity)`, not `O(minted_count)`, and
        this class exists specifically to support capacities where that
        distinction matters (this module's own docstring names
        astronomical ones as a real, intended case). The caller owns
        that eligibility decision; this method does not gate it.

        Returns:
            `minted_mask` (`(capacity,)` bool, `True` at every minted
            state), `minted_list` (`(capacity,)` int64, the first
            `minted_count` entries holding every minted state in the
            order they were minted, the rest unused padding),
            `minted_count`, and `next_unminted` — the exact argument
            shape `_jit_mutate_targets_batched` expects. `next_unminted`
            itself is inert since `FIM-46`'s fix (`mutate_target`'s own
            docstring): neither this class nor `_jit_mutate_targets_
            batched` still uses it to choose a mint target (both now
            sample uniformly instead), so it is carried through this
            tuple unchanged from whatever `__init__` set it to (`0`),
            kept only so this method's own return shape — and `_jit_
            mutate_targets_batched`'s matching argument shape — did not
            need to change everywhere either already reaches.
        """
        minted_mask = np.zeros(self._capacity, dtype=np.bool_)
        minted_mask[np.asarray(self._minted, dtype=np.int64)] = True
        minted_list = np.zeros(self._capacity, dtype=np.int64)
        minted_list[: len(self._minted)] = np.asarray(self._minted, dtype=np.int64)
        return minted_mask, minted_list, len(self._minted), self._next_unminted

    def restore_from_arrays(
        self,
        minted_mask: np.ndarray,
        minted_list: np.ndarray,
        minted_count: int,
        next_unminted: int,
    ) -> None:
        """Replace this space's own minted state from `to_arrays`-shaped output.

        The write-back half of `to_arrays`, above — call after a batched
        `_jit_mutate_targets_batched` call with this space's own
        `to_arrays()` output (mutated by that call) to make its own
        effect on this space visible to whatever calls `mutate_target`
        or `to_arrays` next, exactly as `mutate_target` itself already
        mutates this space's own internal state as a side effect.

        Args:
            minted_mask: Ignored beyond its own role in producing
                `minted_list`/`minted_count` — this space's own
                `_minted_set` is rebuilt from `minted_list[:minted_
                count]` directly, not from this mask, since the mask
                alone cannot recover minting *order* (needed for
                `_minted`'s own list form) the way `minted_list` already
                preserves it.
            minted_list: `(capacity,)` int64, the first `minted_count`
                entries holding every minted state, in minting order.
            minted_count: How many of `minted_list`'s own entries are
                valid.
            next_unminted: Inert since `FIM-46`'s fix (`to_arrays`'s own
                docstring) — stored back verbatim, never read for a
                mint decision.
        """
        del minted_mask  # see docstring: order-preserving minted_list suffices
        self._minted = [
            AlleleId(int(identity)) for identity in minted_list[:minted_count]
        ]
        self._minted_set = {int(identity) for identity in self._minted}
        self._next_unminted = next_unminted

    def mutate_target(
        self,
        current: AlleleId,
        rng: np.random.Generator,
    ) -> AlleleId:
        """Return one state other than ``current``, uniformly at random.

        Args:
            current: The mutating gene copy's existing allele identity.
            rng: The run's explicitly threaded random generator.

        Returns:
            A state drawn uniformly from the ``capacity - 1`` others: an
            already-minted one (a recurrence), chosen uniformly among the
            tracked minted set excluding ``current``, or a fresh one,
            chosen uniformly among every not-yet-minted state — not
            merely the smallest one.

            **Corrected 2026-09-05** (this project's own multi-model
            engine review, 2026-09-04, `FIM-46`/finding C-05/finding
            P1.1/finding P1-5, independently found by three of four
            reviewers): the mint branch below used to always return
            ``self._next_unminted``, the smallest not-yet-minted state,
            deterministically — a specific unminted state other than
            that one had probability exactly zero of ever being this
            call's own return value, contradicting this docstring's own
            "uniformly at random" claim and this class's own module
            docstring. `_mutate_targets_batched` (`fim.model.vectorized`
            and `fim.model.operators`, kept as duplicate implementations
            for a documented circular-import reason) shared the
            identical defect and is fixed the same way, so cross-backend
            parity tests could never have caught this on their own —
            they validate agreement between two implementations of the
            same wrong distribution. A new, independent target-identity
            oracle test (`test_mutate_target_mint_branch_is_uniform_
            over_every_unminted_state`, `test/model/test_allele.py`)
            checks the actual distribution directly, not merely
            cross-backend agreement.

            Uniform rejection sampling over ``0 .. capacity - 1``,
            retrying only on an already-minted draw: correct regardless
            of ``capacity``'s own magnitude, and cheap in the regime this
            class exists for (`capacity` far larger than ``minted_
            count`` — this class's own module docstring), since the
            rejection probability is exactly ``minted_count / capacity``.
            Never materializes a ``capacity``-sized structure, matching
            this class's own documented "the full state space is never
            materialized" contract, even at an astronomical ``capacity``
            no array could hold at all.

        Raises:
            RuntimeError: If every state in ``0 .. capacity - 1`` is already
                minted. Unreachable in practice: once ``minted_count ==
                capacity``, ``recurrence_probability`` is exactly ``1.0``
                and this branch is never taken; the guard exists so a
                capacity overrun fails loudly instead of looping forever
                (rejection sampling has no other way to notice "there is
                nothing left to draw" once every state is minted).
        """
        minted_count = len(self._minted)
        recurrence_probability = (minted_count - 1) / (self._capacity - 1)
        if recurrence_probability > 0.0 and rng.random() < recurrence_probability:
            others = [allele_id for allele_id in self._minted if allele_id != current]
            return others[int(rng.integers(0, len(others)))]
        if minted_count >= self._capacity:
            raise RuntimeError(
                "finite allele space has no unminted state left to target"
            )
        while True:
            candidate = _uniform_integer_below(rng, self._capacity)
            if candidate not in self._minted_set:
                break
        target = AlleleId(candidate)
        self._minted.append(target)
        self._minted_set.add(int(target))
        return target


class FiniteAlleleRegistry:
    """Every tracked locus's `FiniteAlleleSpace` for one run.

    A thin, locus-keyed wrapper so `fim.model.operators.mutate` can look up
    the right space without knowing how many loci a run tracks.
    """

    def __init__(self, spaces: Mapping[int, FiniteAlleleSpace]) -> None:
        """Store one finite-allele space per locus, keyed by `LocusSpec.locus_id`.

        Args:
            spaces: One `FiniteAlleleSpace` per tracked locus.
        """
        self._spaces = dict(spaces)

    def mutate_target(
        self,
        locus_id: int,
        current: AlleleId,
        rng: np.random.Generator,
    ) -> AlleleId:
        """Return a mutation target for ``locus_id`` under the K-allele model.

        Args:
            locus_id: The mutating gene copy's locus.
            current: The mutating gene copy's existing allele identity.
            rng: The run's explicitly threaded random generator.

        Returns:
            One state other than ``current``, drawn uniformly at random.
        """
        return self._spaces[locus_id].mutate_target(current, rng)

    def space_for(self, locus_id: int) -> FiniteAlleleSpace:
        """Return `locus_id`'s own `FiniteAlleleSpace` directly.

        For callers that need more than one draw at a time — `fim.
        model.operators.mutate`'s own batched, `nogil`-JIT-compiled
        target-selection path needs the space's own `capacity` (to
        decide eligibility) and `to_arrays`/`restore_from_arrays`
        (to batch a whole pair's own events in one call) — where
        `mutate_target`, above, only ever exposes one draw at a time.

        Args:
            locus_id: The locus whose own space to return.

        Returns:
            That locus's own `FiniteAlleleSpace`, the same live object
            `mutate_target` itself already mutates as a side effect —
            not a copy.
        """
        return self._spaces[locus_id]
