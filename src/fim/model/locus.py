"""Locus metadata used by a simulation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LocusSpec:
    """Describe one tracked locus.

    Args:
        locus_id: Positive, run-local identifier.
        length: Positive locus length in base pairs.
    """

    locus_id: int
    length: int

    def __post_init__(self) -> None:
        """Validate the immutable locus description."""
        if isinstance(self.locus_id, bool) or self.locus_id < 1:
            raise ValueError("locus_id must be a positive integer")
        if isinstance(self.length, bool) or self.length < 1:
            raise ValueError("locus length must be a positive integer")
