"""Opaque allele identities and mutant-allele allocation.

Two mutation-model allocators live here: `AlleleRegistry`, a bare counter
for the infinite-alleles model (every mutation event is globally novel),
and `FiniteAlleleSpace`/`FiniteAlleleRegistry`, a bounded, per-locus
alternative for the finite-alleles (K-allele) model, where a mutation event
can land on a state that already exists elsewhere in the run.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import NewType

import numpy as np

AlleleId = NewType("AlleleId", int)

MINTED_ID_START = 1 << 32


def founding_allele_ids(count: int) -> tuple[AlleleId, ...]:
    """Return the locus-relative founding allele identifiers.

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
    """Allocate globally unique identities for alleles created by mutation."""

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
        if capacity < 2:
            raise ValueError("finite allele capacity must be at least 2")
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
            tracked minted set excluding ``current``, or the next not-yet-
            minted one, with probability proportional to how many of each
            kind remain.

        Raises:
            RuntimeError: If every state in ``0 .. capacity - 1`` is already
                minted. Unreachable in practice: once ``minted_count ==
                capacity``, ``recurrence_probability`` is exactly ``1.0``
                and this branch is never taken; the guard exists so a
                capacity overrun fails loudly instead of minting an
                out-of-range ID.
        """
        minted_count = len(self._minted)
        recurrence_probability = (minted_count - 1) / (self._capacity - 1)
        if recurrence_probability > 0.0 and rng.random() < recurrence_probability:
            others = [allele_id for allele_id in self._minted if allele_id != current]
            return others[int(rng.integers(0, len(others)))]
        while self._next_unminted in self._minted_set:
            self._next_unminted += 1
        if self._next_unminted >= self._capacity:
            raise RuntimeError(
                "finite allele space has no unminted state left to target"
            )
        target = AlleleId(self._next_unminted)
        self._next_unminted += 1
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
