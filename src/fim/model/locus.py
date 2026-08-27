"""Locus metadata used by a simulation.

A "locus" is one specific position (or short stretch) in the genome
being tracked — the simulation can watch several loci at once, each
with its own independent set of alleles and its own history, and
`LocusSpec` is the small, fixed description of one such locus: which
one it is (`locus_id`) and how long a DNA sequence it covers
(`length`).
"""

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


def finite_allele_capacity(length: int) -> int:
    """Return the finite-alleles model's state-space size at a locus.

    Args:
        length: A locus's length in base pairs (`LocusSpec.length`).

    Returns:
        ``4 ** length`` — the number of distinct fixed-length nucleotide
        sequences a locus of this length admits. Matches the
        differentiation-measures guide's own worked reasoning ("a
        single-character locus admits at most four alleles"): this is the
        ceiling the infinite-alleles model's "every mutation is novel"
        assumption approximates, exactly, once it stops being astronomically
        larger than any realistic count of mutation events.
    """
    return int(4**length)
