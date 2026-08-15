"""Opaque allele identities and globally unique mutant-allele allocation."""

from __future__ import annotations

from typing import NewType

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
