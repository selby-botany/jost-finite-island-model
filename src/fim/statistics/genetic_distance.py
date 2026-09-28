"""Pairwise genetic distance and identity statistics between populations.

This module implements genetic distance measures between pairs of
populations, following Nei (1972, *American Naturalist* 106(949):283-292,
DOI 10.1086/282771). Unlike `fim.statistics.differentiation`, which
measures diversity and differentiation across an entire collection of demes
pooled together, this module measures the accumulated genetic divergence
specifically between two named populations.

Key measures implemented:

- `cross_identity` — the cross-population gene identity
  ``J_XY = sum(x_i * y_i)`` at a single locus (the numerator of Nei 1972
  Eq. 1).
- `nei_identity` — normalized genetic identity ``I`` across one or more
  loci (Nei 1972 Eq. 1 and Eq. 2). Bounded in ``[0, 1]``.
- `nei_standard_distance` (alias `nei_d`) — Nei's standard genetic
  distance ``D = -ln(I)`` (Nei 1972 Eq. 3). Bounded in ``[0, +inf)``;
  equals 0 iff allele frequencies are identical at every locus.
- `nei_geometric_identity` — normalized genetic identity ``I'`` computed
  via geometric means across loci (Nei 1972 Eq. 4).
- `nei_geometric_distance` — genetic distance ``D' = -ln(I')`` under
  locus-varying substitution rates, via geometric means (Nei 1972 Eq. 4).
- `nei_mean_distance` (alias `nei_d_prime`) — genetic distance ``D'``
  under locus-varying substitution rates, via the arithmetic mean of
  per-locus distances (Nei 1972 Eq. 4'). Algebraically identical to
  `nei_geometric_distance`.
- `nei_founder_identity` — expected genetic identity ``I_0`` immediately
  after a parental population splits into two isolated populations of
  finite size (Nei 1972 Eq. 9).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import exp, fsum, inf, isfinite, log, sqrt
from typing import Any

from .differentiation import _bounded, _validate_deme

__all__ = [
    "cross_identity",
    "nei_d",
    "nei_d_prime",
    "nei_founder_identity",
    "nei_geometric_distance",
    "nei_geometric_identity",
    "nei_identity",
    "nei_mean_distance",
    "nei_standard_distance",
]


def _validate_locus_sequence(
    loci: Sequence[Mapping[Any, Any]] | Mapping[Any, Any],
    location: str,
) -> tuple[dict[int, float], ...]:
    """Validate a single locus mapping or a sequence of locus mappings."""
    if isinstance(loci, Mapping):
        return (_validate_deme(loci, 0),)
    if isinstance(loci, Sequence):
        if not loci:
            raise ValueError(f"{location} must contain at least one locus")
        return tuple(_validate_deme(deme, idx) for idx, deme in enumerate(loci))
    raise TypeError(
        f"{location} must be a sequence of locus frequency mappings or a single mapping"
    )


def _validate_paired_loci(
    loci_x: Sequence[Mapping[Any, Any]] | Mapping[Any, Any],
    loci_y: Sequence[Mapping[Any, Any]] | Mapping[Any, Any],
) -> tuple[tuple[dict[int, float], ...], tuple[dict[int, float], ...]]:
    """Validate and align locus profiles for two populations."""
    table_x = _validate_locus_sequence(loci_x, "population X")
    table_y = _validate_locus_sequence(loci_y, "population Y")
    if len(table_x) != len(table_y):
        raise ValueError(
            f"locus count mismatch: population X has {len(table_x)} loci "
            f"but population Y has {len(table_y)} loci"
        )
    return table_x, table_y


def cross_identity(
    frequencies_x: Mapping[Any, Any],
    frequencies_y: Mapping[Any, Any],
) -> float:
    """Return Nei cross-population gene identity ``J_XY = sum(x_i * y_i)``.

    Computes the probability that two gene copies, one drawn at random
    from population X and one drawn at random from population Y, are the
    same allele. This is the numerator of Nei (1972) Eq. 1.

    Args:
        frequencies_x: Normalized allele frequencies in population X at one locus.
        frequencies_y: Normalized allele frequencies in population Y at one locus.

    Returns:
        Cross gene identity J_XY in [0, 1]. Returns 0.0 if the populations share
        no alleles; equals within-deme identity if frequencies are identical.
    """
    deme_x = _validate_deme(frequencies_x, 0)
    deme_y = _validate_deme(frequencies_y, 1)
    shared = set(deme_x).intersection(deme_y)
    total = fsum(deme_x[allele_id] * deme_y[allele_id] for allele_id in shared)
    return _bounded(total, "J_XY")


def nei_d(
    loci_x: Sequence[Mapping[Any, Any]] | Mapping[Any, Any],
    loci_y: Sequence[Mapping[Any, Any]] | Mapping[Any, Any],
) -> float:
    """Return Nei's standard genetic distance ``D`` (alias for `nei_standard_distance`).

    Args:
        loci_x: Allele frequencies for population X across loci.
        loci_y: Allele frequencies for population Y across loci.

    Returns:
        Genetic distance D in [0, +inf).
    """
    return nei_standard_distance(loci_x, loci_y)


def nei_d_prime(
    loci_x: Sequence[Mapping[Any, Any]] | Mapping[Any, Any],
    loci_y: Sequence[Mapping[Any, Any]] | Mapping[Any, Any],
) -> float:
    """Return Nei's distance ``D'`` under varying rates (alias for `nei_mean_distance`).

    Args:
        loci_x: Allele frequencies for population X across loci.
        loci_y: Allele frequencies for population Y across loci.

    Returns:
        Genetic distance D' in [0, +inf).
    """
    return nei_mean_distance(loci_x, loci_y)


def nei_founder_identity(j_z: float, n_x: float, n_y: float) -> float:
    """Return expected genetic identity ``I_0`` after splitting (Nei 1972 Eq. 9).

    Computes the expected normalized genetic identity between two isolated
    populations X and Y immediately after they are founded from a common
    parental population Z of homozygosity `j_z`:

    .. math::

        I_0 = \\frac{J_Z}{\\sqrt{\\left[J_Z + \\frac{1 - J_Z}{2 N_X}\\right]
              \\left[J_Z + \\frac{1 - J_Z}{2 N_Y}\\right]}}

    Args:
        j_z: Homozygosity (gene identity sum(z_i ** 2)) in the parental
            population Z, in (0, 1].
        n_x: Effective population size of population X (strictly positive,
            may be math.inf).
        n_y: Effective population size of population Y (strictly positive,
            may be math.inf).

    Returns:
        Expected genetic identity I_0 in [0, 1].

    Raises:
        TypeError: If an argument is not a real number.
        ValueError: If `j_z` is outside (0, 1] or population sizes are
            non-positive.
    """
    if isinstance(j_z, bool) or not isinstance(j_z, int | float):
        raise TypeError("parental homozygosity j_z must be a real number")
    if isinstance(n_x, bool) or not isinstance(n_x, int | float):
        raise TypeError("population size n_x must be a real number")
    if isinstance(n_y, bool) or not isinstance(n_y, int | float):
        raise TypeError("population size n_y must be a real number")

    jz_val = float(j_z)
    nx_val = float(n_x)
    ny_val = float(n_y)

    if not isfinite(jz_val) or jz_val <= 0.0 or jz_val > 1.0:
        raise ValueError("parental homozygosity j_z must be in (0, 1]")
    if nx_val <= 0.0 or ny_val <= 0.0:
        raise ValueError("population sizes n_x and n_y must be strictly positive")

    drift_x = (1.0 - jz_val) / (2.0 * nx_val) if isfinite(nx_val) else 0.0
    drift_y = (1.0 - jz_val) / (2.0 * ny_val) if isfinite(ny_val) else 0.0

    denom = sqrt((jz_val + drift_x) * (jz_val + drift_y))
    return _bounded(jz_val / denom, "I_0")


def nei_geometric_distance(
    loci_x: Sequence[Mapping[Any, Any]] | Mapping[Any, Any],
    loci_y: Sequence[Mapping[Any, Any]] | Mapping[Any, Any],
) -> float:
    """Return genetic distance ``D' = -ln(I')`` via geometric means (Nei 1972 Eq. 4).

    Measures accumulated gene differences when substitution rates vary
    substantially among loci, taking geometric means of per-locus identities.
    Algebraically identical to `nei_mean_distance` (Eq. 4').

    Args:
        loci_x: Allele frequencies for population X across loci.
        loci_y: Allele frequencies for population Y across loci.

    Returns:
        Genetic distance D' in [0, +inf). Returns 0.0 if identical; math.inf
        if any locus has no shared alleles.
    """
    ident = nei_geometric_identity(loci_x, loci_y)
    if ident <= 0.0:
        return inf
    if ident >= 1.0:
        return 0.0
    return -log(ident)


def nei_geometric_identity(
    loci_x: Sequence[Mapping[Any, Any]] | Mapping[Any, Any],
    loci_y: Sequence[Mapping[Any, Any]] | Mapping[Any, Any],
) -> float:
    """Return normalized identity ``I'`` via geometric means (Nei 1972 Eq. 4).

    Computes ``I' = J'_XY / sqrt(J'_X * J'_Y)``, where ``J'_X``, ``J'_Y``,
    and ``J'_XY`` are the geometric means of per-locus identities across loci.

    Args:
        loci_x: Allele frequencies for population X across loci.
        loci_y: Allele frequencies for population Y across loci.

    Returns:
        Normalized identity I' in [0, 1]. Returns 0.0 if any locus has no
        shared alleles.
    """
    table_x, table_y = _validate_paired_loci(loci_x, loci_y)
    locus_count = len(table_x)
    log_j_x = 0.0
    log_j_y = 0.0
    log_j_xy = 0.0
    for deme_x, deme_y in zip(table_x, table_y, strict=True):
        j_x = fsum(v * v for v in deme_x.values())
        j_y = fsum(v * v for v in deme_y.values())
        shared = set(deme_x).intersection(deme_y)
        j_xy = fsum(deme_x[a] * deme_y[a] for a in shared)
        if j_xy <= 0.0:
            return 0.0
        log_j_x += log(j_x)
        log_j_y += log(j_y)
        log_j_xy += log(j_xy)

    mean_log_j_x = log_j_x / locus_count
    mean_log_j_y = log_j_y / locus_count
    mean_log_j_xy = log_j_xy / locus_count
    geom_j_x = exp(mean_log_j_x)
    geom_j_y = exp(mean_log_j_y)
    geom_j_xy = exp(mean_log_j_xy)
    denom = sqrt(geom_j_x * geom_j_y)
    if denom <= 0.0:
        return 0.0
    return _bounded(geom_j_xy / denom, "I'")


def nei_identity(
    loci_x: Sequence[Mapping[Any, Any]] | Mapping[Any, Any],
    loci_y: Sequence[Mapping[Any, Any]] | Mapping[Any, Any],
) -> float:
    """Return normalized genetic identity ``I`` (Nei 1972 Eq. 1 and Eq. 2).

    For a single locus, ``I = j_XY / sqrt(j_X * j_Y)`` (Eq. 1). For multiple
    loci, ``I = J_XY / sqrt(J_X * J_Y)`` (Eq. 2), where ``J_X``, ``J_Y``, and
    ``J_XY`` are the arithmetic means across all loci examined.

    Args:
        loci_x: Allele frequencies for population X across loci.
        loci_y: Allele frequencies for population Y across loci.

    Returns:
        Normalized identity I in [0, 1]. Returns 1.0 if allele frequencies
        are identical at all loci; returns 0.0 if no alleles are shared at
        any locus.
    """
    table_x, table_y = _validate_paired_loci(loci_x, loci_y)
    sum_j_x = 0.0
    sum_j_y = 0.0
    sum_j_xy = 0.0
    for deme_x, deme_y in zip(table_x, table_y, strict=True):
        j_x = fsum(v * v for v in deme_x.values())
        j_y = fsum(v * v for v in deme_y.values())
        shared = set(deme_x).intersection(deme_y)
        j_xy = fsum(deme_x[a] * deme_y[a] for a in shared)
        sum_j_x += j_x
        sum_j_y += j_y
        sum_j_xy += j_xy

    if sum_j_xy <= 0.0:
        return 0.0
    denom = sqrt(sum_j_x * sum_j_y)
    return _bounded(sum_j_xy / denom, "I")


def nei_mean_distance(
    loci_x: Sequence[Mapping[Any, Any]] | Mapping[Any, Any],
    loci_y: Sequence[Mapping[Any, Any]] | Mapping[Any, Any],
) -> float:
    """Return genetic distance ``D' = mean(d_j)`` (Nei 1972 Eq. 4').

    Computes the arithmetic mean of per-locus distances ``d_j = -ln(I_j)``.
    Algebraically identical to `nei_geometric_distance` (Eq. 4).

    Args:
        loci_x: Allele frequencies for population X across loci.
        loci_y: Allele frequencies for population Y across loci.

    Returns:
        Genetic distance D' in [0, +inf). Returns 0.0 if identical; math.inf
        if any locus has no shared alleles.
    """
    table_x, table_y = _validate_paired_loci(loci_x, loci_y)
    locus_count = len(table_x)
    distances: list[float] = []
    for deme_x, deme_y in zip(table_x, table_y, strict=True):
        j_x = fsum(v * v for v in deme_x.values())
        j_y = fsum(v * v for v in deme_y.values())
        shared = set(deme_x).intersection(deme_y)
        j_xy = fsum(deme_x[a] * deme_y[a] for a in shared)
        if j_xy <= 0.0:
            return inf
        denom = sqrt(j_x * j_y)
        i_j = _bounded(j_xy / denom, "I_j")
        if i_j <= 0.0:
            return inf
        if i_j >= 1.0:
            distances.append(0.0)
        else:
            distances.append(-log(i_j))
    return fsum(distances) / locus_count


def nei_standard_distance(
    loci_x: Sequence[Mapping[Any, Any]] | Mapping[Any, Any],
    loci_y: Sequence[Mapping[Any, Any]] | Mapping[Any, Any],
) -> float:
    """Return Nei's standard genetic distance ``D = -ln(I)`` (Nei 1972 Eq. 3).

    Measures accumulated codon differences per locus under a steady-state
    infinite-alleles neutral mutation model.

    Args:
        loci_x: Allele frequencies for population X across loci.
        loci_y: Allele frequencies for population Y across loci.

    Returns:
        Genetic distance D in [0, +inf). Returns 0.0 if allele frequencies
        are identical at all loci; returns math.inf if no alleles are shared.
    """
    ident = nei_identity(loci_x, loci_y)
    if ident <= 0.0:
        return inf
    if ident >= 1.0:
        return 0.0
    return -log(ident)
