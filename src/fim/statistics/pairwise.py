"""Every deme pair's Nei identities at once, vectorized.

`fim.statistics.genetic_distance.nei_pair_identity` answers for one pair
in pure Python, which is right for the one pair a plot shows. Capturing
*every* pair for a run (`pairwise.json`) needs ``d * (d - 1) / 2`` of
them: half a million at ``d = 1024``. This module computes all of them
from one matrix product per locus.

For one locus, put the frequencies in a ``d x A`` matrix ``P`` (one row
per deme, one column per allele seen anywhere at that locus). Then
``G = P @ P.T`` holds every cross identity ``J_kl`` off the diagonal and
every within-deme identity ``J_k`` on it. Every Nei identity is an
element-wise function of ``G`` and its diagonal, so the whole family for
all pairs costs one BLAS matrix product plus a few element-wise passes
per locus: ``O(d^2 * A)`` arithmetic and ``O(d^2)`` memory (a few
megabytes per working array at ``d = 1024``).

numpy is used here, unlike the rest of this package, because the
pure-Python loop over pairs is about a thousand times slower at the
deme counts this is for. The inputs are still plain numbers: one
frequency table per locus.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from numpy.typing import NDArray

from .genetic_distance import NEI_DENOMINATORS, NeiDenominator, NeiLocusRule

__all__ = [
    "locus_frequency_matrix",
    "pairwise_nei_identities",
    "upper_triangle",
]

FloatMatrix = NDArray[np.float64]


def locus_frequency_matrix(table: Sequence[Mapping[Any, float]]) -> FloatMatrix:
    """Return one locus's ``d x A`` frequency matrix, alleles in first-seen order.

    Args:
        table: Every deme's allele-frequency mapping at one locus.

    Returns:
        A dense float matrix, one row per deme. An allele absent from a
        deme is 0 in that row.
    """
    columns: dict[Any, int] = {}
    for deme in table:
        for allele_id in deme:
            columns.setdefault(allele_id, len(columns))
    matrix = np.zeros((len(table), len(columns)), dtype=np.float64)
    for row, deme in enumerate(table):
        for allele_id, value in deme.items():
            matrix[row, columns[allele_id]] = value
    return matrix


def _ratio(
    between: FloatMatrix, within: NDArray[np.float64], denominator: NeiDenominator
) -> FloatMatrix:
    """Divide every ``J_kl`` by the chosen mean of ``J_k`` and ``J_l``."""
    if denominator == "geometric":
        scale = np.sqrt(np.outer(within, within))
    else:
        scale = (within[:, None] + within[None, :]) / 2.0
    result: FloatMatrix = between / scale
    return result


def pairwise_nei_identities(
    locus_tables: Sequence[Sequence[Mapping[Any, float]]],
) -> dict[tuple[NeiDenominator, NeiLocusRule], FloatMatrix]:
    """Return all four Nei identity matrices for every pair of demes.

    Element ``[k, l]`` equals `nei_pair_identity` for demes ``k`` and
    ``l`` with the same denominator and locus rule; the diagonal is 1.

    Args:
        locus_tables: One frequency table per locus, demes in the same
            order at every locus.

    Returns:
        ``{(denominator, locus_rule): d x d matrix}``, symmetric, every
        value in ``[0, 1]``.

    Raises:
        ValueError: For no loci, no demes, or loci with different deme
            counts.
    """
    if not locus_tables:
        raise ValueError("locus_tables must contain at least one locus")
    deme_count = len(locus_tables[0])
    if deme_count == 0 or any(len(table) != deme_count for table in locus_tables):
        raise ValueError("every locus must list the same, non-zero number of demes")

    locus_count = len(locus_tables)
    between_sum = np.zeros((deme_count, deme_count), dtype=np.float64)
    within_sum = np.zeros(deme_count, dtype=np.float64)
    log_sums = {
        denominator: np.zeros((deme_count, deme_count), dtype=np.float64)
        for denominator in NEI_DENOMINATORS
    }
    with np.errstate(divide="ignore"):
        for table in locus_tables:
            frequencies = locus_frequency_matrix(table)
            identities = frequencies @ frequencies.T
            within = np.diagonal(identities).copy()
            between_sum += identities
            within_sum += within
            for denominator in NEI_DENOMINATORS:
                # log(0) is -inf, and stays -inf through the sum: one
                # locus with nothing shared makes that pair's locus-mean
                # identity 0, exactly as `nei_family_from_identities` does.
                log_sums[denominator] += np.log(
                    np.clip(_ratio(identities, within, denominator), 0.0, 1.0)
                )

    family: dict[tuple[NeiDenominator, NeiLocusRule], FloatMatrix] = {}
    for denominator in NEI_DENOMINATORS:
        pooled = _ratio(
            between_sum / locus_count, within_sum / locus_count, denominator
        )
        locus_mean = np.exp(log_sums[denominator] / locus_count)
        by_rule: tuple[tuple[NeiLocusRule, FloatMatrix], ...] = (
            ("pooled", pooled),
            ("locus_mean", locus_mean),
        )
        for locus_rule, matrix in by_rule:
            # Both pair forms are bounded by 1 (Cauchy-Schwarz, AM-GM);
            # clipping removes only rounding, and the diagonal is exact.
            bounded = np.clip(matrix, 0.0, 1.0)
            np.fill_diagonal(bounded, 1.0)
            family[(denominator, locus_rule)] = bounded
    return family


def upper_triangle(matrix: FloatMatrix) -> list[float]:
    """Return the strict upper triangle, row by row, as plain floats.

    The order is ``[0,1], [0,2], ..., [0,d-1], [1,2], ...``: the compact
    form `pairwise.json` stores for a symmetric matrix with a known
    diagonal.

    Args:
        matrix: A square matrix.

    Returns:
        ``d * (d - 1) / 2`` floats.
    """
    rows, columns = np.triu_indices(matrix.shape[0], k=1)
    values: list[float] = matrix[rows, columns].tolist()
    return values
