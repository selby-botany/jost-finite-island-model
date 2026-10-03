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

The Nei distance family (`nei_pair_identity`, `nei_pair_distance`,
`nei_all_demes_identity`, `nei_all_demes_distance`) generalizes Eq. 1-4
along two independent choices:

- **Denominator.** ``"geometric"`` is Nei's own ``sqrt(J_X * J_Y)``, and
  for ``d`` demes the geometric mean ``(J_1 * ... * J_d)^(1/d)``.
  ``"arithmetic"`` is ``(J_X + J_Y) / 2``, and for ``d`` demes the mean
  within-deme identity ``J_within`` (Jost, L. (2026) private
  communication). The numerator is ``J_XY`` for a pair and, for all
  demes, ``J_between``: the mean identity over every pair of demes, so
  the all-demes form is the pair form exactly when ``d = 2``.
- **Locus rule.** ``"pooled"`` is Nei's own multi-locus rule: average
  every identity across loci first, then take one ratio (Eq. 2).
  ``"locus_mean"`` takes one ratio per locus and averages the per-locus
  distances (Eq. 4'), so the reported identity is the geometric mean of
  the per-locus identities.

Two facts follow and are relied on by tests and documentation:

- The arithmetic all-demes identity is ``J_between / J_within``, which is
  ``1 - D`` for Jost's ``D``, so its distance is ``-ln(1 - D)``.
- The geometric all-demes identity is *not* bounded by 1. When demes
  differ greatly in diversity, the pair-averaged numerator can exceed the
  geometric mean of the within-deme identities, and the distance is
  negative. It is reported as computed, never clamped; see
  `nei_all_demes_identity`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import exp, fsum, inf, isfinite, log, sqrt
from typing import Any, Final, Literal, TypeAlias

from .differentiation import (
    _MINIMUM_DEMES,
    _TOLERANCE,
    FrequencyTable,
    _bounded,
    _validate_deme,
    _validate_table,
)

__all__ = [
    "NEI_DENOMINATORS",
    "NEI_LOCUS_RULES",
    "NeiDenominator",
    "NeiLocusRule",
    "cross_identity",
    "nei_all_demes_distance",
    "nei_all_demes_identity",
    "nei_d",
    "nei_d_prime",
    "nei_distance_from_identity",
    "nei_family_from_identities",
    "nei_founder_identity",
    "nei_geometric_distance",
    "nei_geometric_identity",
    "nei_identity",
    "nei_mean_distance",
    "nei_pair_distance",
    "nei_pair_identity",
    "nei_standard_distance",
]

NeiDenominator: TypeAlias = Literal["geometric", "arithmetic"]
NeiLocusRule: TypeAlias = Literal["pooled", "locus_mean"]

NEI_DENOMINATORS: Final[tuple[NeiDenominator, ...]] = ("geometric", "arithmetic")
"""Every denominator the Nei distance family accepts, Nei's own first."""

NEI_LOCUS_RULES: Final[tuple[NeiLocusRule, ...]] = ("pooled", "locus_mean")
"""Every multi-locus rule the Nei distance family accepts, Nei's own first."""


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


def _within_deme_identities(table: Sequence[Mapping[int, float]]) -> list[float]:
    """Return each deme's own gene identity ``J_k = sum_i p_k,i^2``."""
    return [fsum(value * value for value in deme.values()) for deme in table]


def _between_deme_identity(
    table: Sequence[Mapping[int, float]], within: Sequence[float]
) -> float:
    """Return ``J_between``, the mean cross identity over every pair of demes.

    Computed through the pooled identity rather than by visiting every
    pair: with ``p_bar`` the mean frequency across ``d`` demes,
    ``d^2 * sum(p_bar^2) = sum_k J_k + 2 * sum_{k<l} J_kl``. That is
    O(d * alleles) instead of O(d^2 * alleles), which matters at the deme
    counts this project supports.
    """
    deme_count = len(table)
    pooled: dict[int, float] = {}
    for deme in table:
        for allele_id, value in deme.items():
            pooled[allele_id] = pooled.get(allele_id, 0.0) + value
    pooled_square_sum = fsum(value * value for value in pooled.values())
    pair_sum = (pooled_square_sum - fsum(within)) / 2.0
    pair_count = deme_count * (deme_count - 1) / 2.0
    return max(0.0, pair_sum / pair_count)


def _nei_ratio(
    between: float, within: Sequence[float], denominator: NeiDenominator
) -> float:
    """Return ``between`` over the chosen mean of ``within`` (never clamped).

    Every ``within`` entry is strictly positive (a normalized frequency
    vector has ``sum p^2 >= 1 / alleles``), so the geometric mean's
    logarithms are always defined.
    """
    if denominator == "geometric":
        scale = exp(fsum(log(value) for value in within) / len(within))
    elif denominator == "arithmetic":
        scale = fsum(within) / len(within)
    else:
        raise ValueError(
            f"denominator must be one of {NEI_DENOMINATORS!r}, got {denominator!r}"
        )
    return between / scale


def nei_distance_from_identity(identity_value: float) -> float:
    """Return Nei's distance ``-ln(I)`` for an identity ``I``.

    ``I == 0`` (no allele shared) is an infinite distance; ``I`` within
    rounding of 1 is exactly 0. ``I > 1`` is legitimate only for the
    geometric all-demes form and gives a negative distance.

    Args:
        identity_value: A Nei identity, ``>= 0``.

    Returns:
        The distance, possibly ``math.inf`` or negative as described.
    """
    if identity_value <= 0.0:
        return inf
    if abs(identity_value - 1.0) <= _TOLERANCE:
        return 0.0
    return -log(identity_value)


def nei_family_from_identities(
    within_by_locus: Sequence[Sequence[float]],
    between_by_locus: Sequence[float],
) -> dict[tuple[NeiDenominator, NeiLocusRule], float]:
    """Return all four Nei identities from already-computed gene identities.

    The shared core of every Nei family function, public so a caller that
    already holds the within- and between-deme identities (the engine,
    once per generation) pays for nothing else.

    Args:
        within_by_locus: One sequence per locus of every deme's own
            ``J_k``, all of the same length (the deme count).
        between_by_locus: One ``J_between`` (or ``J_XY`` for a pair) per
            locus, in the same locus order.

    Returns:
        ``{(denominator, locus_rule): identity}`` for every combination in
        `NEI_DENOMINATORS` x `NEI_LOCUS_RULES`. The geometric value for
        more than two demes may exceed 1 (see the module docstring).

    Raises:
        ValueError: If the sequences are empty or do not line up.
    """
    locus_count = len(within_by_locus)
    if locus_count == 0 or locus_count != len(between_by_locus):
        raise ValueError("within_by_locus and between_by_locus must align, non-empty")
    deme_count = len(within_by_locus[0])
    if deme_count == 0 or any(len(row) != deme_count for row in within_by_locus):
        raise ValueError("every locus must list the same, non-zero number of demes")

    pooled_within = [
        fsum(row[deme] for row in within_by_locus) / locus_count
        for deme in range(deme_count)
    ]
    pooled_between = fsum(between_by_locus) / locus_count
    family: dict[tuple[NeiDenominator, NeiLocusRule], float] = {}
    for denominator in NEI_DENOMINATORS:
        family[(denominator, "pooled")] = _nei_ratio(
            pooled_between, pooled_within, denominator
        )
        # Geometric mean of the per-locus identities: the identity whose
        # distance is the mean of the per-locus distances.
        log_sum = 0.0
        for within, between in zip(within_by_locus, between_by_locus, strict=True):
            per_locus = _nei_ratio(between, within, denominator)
            if per_locus <= 0.0:
                log_sum = -inf
                break
            log_sum += log(per_locus)
        family[(denominator, "locus_mean")] = (
            0.0 if log_sum == -inf else exp(log_sum / locus_count)
        )
    return family


def _check_choice(denominator: str, locus_rule: str) -> None:
    """Reject an unknown denominator or locus rule with a named error."""
    if denominator not in NEI_DENOMINATORS:
        raise ValueError(
            f"denominator must be one of {NEI_DENOMINATORS!r}, got {denominator!r}"
        )
    if locus_rule not in NEI_LOCUS_RULES:
        raise ValueError(
            f"locus_rule must be one of {NEI_LOCUS_RULES!r}, got {locus_rule!r}"
        )


def nei_pair_identity(
    loci_x: Sequence[Mapping[Any, Any]] | Mapping[Any, Any],
    loci_y: Sequence[Mapping[Any, Any]] | Mapping[Any, Any],
    *,
    denominator: NeiDenominator = "geometric",
    locus_rule: NeiLocusRule = "pooled",
) -> float:
    """Return a Nei identity between two populations.

    With the defaults this equals `nei_identity` (Nei 1972 Eq. 1-2). See
    the module docstring for the two choices.

    Args:
        loci_x: Allele frequencies for population X across loci.
        loci_y: Allele frequencies for population Y across loci.
        denominator: ``"geometric"`` (Nei) or ``"arithmetic"``.
        locus_rule: ``"pooled"`` (Nei) or ``"locus_mean"``.

    Returns:
        The identity in ``[0, 1]``: 1 when the populations are identical,
        0 when no allele is shared (at any locus, for ``"locus_mean"``; at
        every locus, for ``"pooled"``).
    """
    _check_choice(denominator, locus_rule)
    table_x, table_y = _validate_paired_loci(loci_x, loci_y)
    within_by_locus: list[list[float]] = []
    between_by_locus: list[float] = []
    for deme_x, deme_y in zip(table_x, table_y, strict=True):
        within_by_locus.append(_within_deme_identities((deme_x, deme_y)))
        shared = set(deme_x).intersection(deme_y)
        between_by_locus.append(fsum(deme_x[a] * deme_y[a] for a in shared))
    value = nei_family_from_identities(within_by_locus, between_by_locus)[
        (denominator, locus_rule)
    ]
    # Cauchy-Schwarz and AM-GM bound both pair forms by 1, so anything
    # above it is rounding.
    return _bounded(value, "Nei pair identity")


def nei_pair_distance(
    loci_x: Sequence[Mapping[Any, Any]] | Mapping[Any, Any],
    loci_y: Sequence[Mapping[Any, Any]] | Mapping[Any, Any],
    *,
    denominator: NeiDenominator = "geometric",
    locus_rule: NeiLocusRule = "pooled",
) -> float:
    """Return a Nei distance ``-ln(I)`` between two populations.

    The arithmetic form is never smaller than the geometric one, and the
    two agree exactly when the populations are equally diverse.

    Args:
        loci_x: Allele frequencies for population X across loci.
        loci_y: Allele frequencies for population Y across loci.
        denominator: ``"geometric"`` (Nei) or ``"arithmetic"``.
        locus_rule: ``"pooled"`` (Nei) or ``"locus_mean"``.

    Returns:
        The distance in ``[0, +inf)``; ``math.inf`` when no allele is shared.
    """
    return nei_distance_from_identity(
        nei_pair_identity(
            loci_x, loci_y, denominator=denominator, locus_rule=locus_rule
        )
    )


def nei_all_demes_identity(
    tables: Sequence[FrequencyTable],
    *,
    denominator: NeiDenominator = "geometric",
    locus_rule: NeiLocusRule = "pooled",
) -> float:
    """Return the all-demes Nei identity ``J_between / mean(J_k)``.

    The arithmetic form is bounded in ``[0, 1]`` (``J_between`` never
    exceeds ``J_within``) and equals ``1 - D`` for Jost's ``D``.

    The geometric form is **not bounded by 1**. Example: one locus, demes
    1 and 2 fixed for allele A, deme 3 spread evenly over 1000 alleles
    including A. Then ``J = (1, 1, 0.001)``, ``J_between = 0.334``, the
    geometric mean of ``J`` is ``0.1`` and the identity is ``3.34``. The
    value is returned as computed: a negative distance is the honest
    answer to "how does mean between-deme sharing compare with the
    geometric mean of within-deme sharing" when one deme is far more
    diverse than the rest.

    Args:
        tables: One frequency table per locus, each listing every deme
            in the same order (at least two demes).
        denominator: ``"geometric"`` or ``"arithmetic"``.
        locus_rule: ``"pooled"`` (Nei) or ``"locus_mean"``.

    Returns:
        The identity, ``>= 0``.

    Raises:
        ValueError: For no loci, fewer than two demes, or loci listing
            different deme counts.
    """
    _check_choice(denominator, locus_rule)
    if not isinstance(tables, Sequence) or not tables:
        raise ValueError("tables must be a non-empty sequence of frequency tables")
    within_by_locus: list[list[float]] = []
    between_by_locus: list[float] = []
    deme_count: int | None = None
    for table in tables:
        demes = _validate_table(table)
        if len(demes) < _MINIMUM_DEMES:
            raise ValueError("the all-demes Nei identity needs at least two demes")
        if deme_count is not None and len(demes) != deme_count:
            raise ValueError("every locus must list the same number of demes")
        deme_count = len(demes)
        within = _within_deme_identities(demes)
        within_by_locus.append(within)
        between_by_locus.append(_between_deme_identity(demes, within))
    value = nei_family_from_identities(within_by_locus, between_by_locus)[
        (denominator, locus_rule)
    ]
    if denominator == "arithmetic":
        return _bounded(value, "Nei all-demes arithmetic identity")
    return 1.0 if abs(value - 1.0) <= _TOLERANCE else value


def nei_all_demes_distance(
    tables: Sequence[FrequencyTable],
    *,
    denominator: NeiDenominator = "geometric",
    locus_rule: NeiLocusRule = "pooled",
) -> float:
    """Return the all-demes Nei distance ``-ln(I)``.

    See `nei_all_demes_identity`: the geometric form can be negative.

    Args:
        tables: One frequency table per locus, demes in the same order.
        denominator: ``"geometric"`` or ``"arithmetic"``.
        locus_rule: ``"pooled"`` (Nei) or ``"locus_mean"``.

    Returns:
        The distance; ``math.inf`` when no allele is shared between any
        pair of demes.
    """
    return nei_distance_from_identity(
        nei_all_demes_identity(tables, denominator=denominator, locus_rule=locus_rule)
    )
