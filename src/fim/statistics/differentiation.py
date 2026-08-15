"""Allele-frequency diversity and differentiation statistics.

All functions operate on normalized allele-frequency tables and are
independent of model state, persistence, and the engine. Allele identifiers
must be integer-like; a table contains one mapping per deme.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import exp, expm1, fsum, isfinite, log
from numbers import Real
from operator import index as integer_index
from typing import Any, TypeAlias, TypedDict

FrequencyTable: TypeAlias = Sequence[Mapping[Any, Any]]
DemeWeights: TypeAlias = Sequence[Any] | None

_MINIMUM_DEMES = 2
_TOLERANCE = 1e-12


class DifferentiationReport(TypedDict):
    """Scalar statistics computed from a frequency table."""

    H_S: float
    H_T: float
    H_ST: float
    G_ST: float | None
    D: float
    E_ST: float
    K_ST: float


def _bounded(value: float, name: str) -> float:
    """Return a unit-interval value, tolerating floating-point roundoff."""
    if -_TOLERANCE <= value <= 1.0 + _TOLERANCE:
        return min(1.0, max(0.0, value))
    message = f"{name} is outside its mathematical range [0, 1]: {value!r}"
    raise ArithmeticError(message)


def _coerce_frequency(value: object, location: str) -> float:
    """Validate and convert one frequency to a finite non-negative float."""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{location} must be a real number")
    frequency = float(value)
    if not isfinite(frequency) or frequency < 0.0:
        raise ValueError(f"{location} must be finite and non-negative")
    return frequency


def _validate_deme(deme: Mapping[Any, Any], index: int) -> dict[int, float]:
    """Validate one normalized deme mapping and canonicalize allele IDs."""
    if not isinstance(deme, Mapping):
        raise TypeError(f"deme {index} must be a mapping of allele IDs to frequencies")
    if not deme:
        raise ValueError(f"deme {index} must contain at least one allele")

    normalized: dict[int, float] = {}
    for allele_id, value in deme.items():
        if isinstance(allele_id, bool):
            message = f"deme {index} allele ID {allele_id!r} must be integer-like"
            raise TypeError(message)
        try:
            canonical_id = integer_index(allele_id)
        except TypeError as error:
            message = f"deme {index} allele ID {allele_id!r} must be integer-like"
            raise TypeError(message) from error
        if canonical_id in normalized:
            raise ValueError(
                f"deme {index} contains duplicate allele ID {canonical_id}"
            )
        normalized[canonical_id] = _coerce_frequency(
            value,
            f"deme {index} frequency for allele {canonical_id}",
        )

    total = fsum(normalized.values())
    if abs(total - 1.0) > _TOLERANCE:
        message = f"deme {index} frequencies must sum to 1, got {total!r}"
        raise ValueError(message)
    return normalized


def _validate_table(table: FrequencyTable) -> tuple[dict[int, float], ...]:
    """Validate a non-empty sequence of normalized deme frequency mappings."""
    if not isinstance(table, Sequence):
        raise TypeError("frequency table must be a sequence of deme mappings")
    if not table:
        raise ValueError("frequency table must contain at least one deme")
    return tuple(_validate_deme(deme, index) for index, deme in enumerate(table))


def _validate_weights(count: int, deme_weights: DemeWeights) -> tuple[float, ...]:
    """Return normalized, strictly positive deme weights."""
    if deme_weights is None:
        return (1.0 / count,) * count
    if not isinstance(deme_weights, Sequence):
        raise TypeError("deme weights must be a sequence of real numbers")
    if len(deme_weights) != count:
        raise ValueError(f"expected {count} deme weights, got {len(deme_weights)}")

    weights = tuple(
        _coerce_frequency(weight, f"deme weight {index}")
        for index, weight in enumerate(deme_weights)
    )
    if any(weight == 0.0 for weight in weights):
        raise ValueError("deme weights must be strictly positive")
    total = fsum(weights)
    if total == 0.0:
        raise ValueError("deme weights must have a positive sum")
    return tuple(weight / total for weight in weights)


def _entropy(frequencies: Mapping[int, float]) -> float:
    """Return Shannon entropy, omitting zero-frequency alleles."""
    return -fsum(
        frequency * log(frequency)
        for frequency in frequencies.values()
        if frequency > 0.0
    )


def _pooled(
    demes: Sequence[Mapping[int, float]],
    weights: Sequence[float],
) -> dict[int, float]:
    """Return the weighted pooled allele-frequency mapping."""
    pooled: dict[int, float] = {}
    for deme, weight in zip(demes, weights, strict=True):
        for allele_id, frequency in deme.items():
            pooled[allele_id] = pooled.get(allele_id, 0.0) + weight * frequency
    return pooled


def _hill(frequencies: Mapping[int, float], order: float) -> float:
    """Return a Hill number from a validated frequency mapping."""
    positive = tuple(frequency for frequency in frequencies.values() if frequency > 0.0)
    if order == 0.0:
        return float(len(positive))
    if order == 1.0:
        return exp(-fsum(frequency * log(frequency) for frequency in positive))
    power_sum = fsum(frequency**order for frequency in positive)
    return float(power_sum ** (1.0 / (1.0 - order)))


def _validate_order(order: float | int) -> float:
    """Validate a finite non-negative Hill-number order."""
    if isinstance(order, bool) or not isinstance(order, int | float):
        raise TypeError("Hill-number order must be a real number")
    validated = float(order)
    if not isfinite(validated) or validated < 0.0:
        raise ValueError("Hill-number order must be finite and non-negative")
    return validated


def _require_multiple_demes(demes: Sequence[Mapping[int, float]]) -> None:
    """Reject differentiation requests with fewer than two demes."""
    if len(demes) < _MINIMUM_DEMES:
        raise ValueError("a differentiation statistic requires at least two demes")


def heterozygosity(frequencies: Mapping[Any, Any]) -> float:
    """Return expected heterozygosity ``H = 1 - sum(p_i ** 2)`` for one deme."""
    deme = _validate_deme(frequencies, 0)
    return _bounded(1.0 - fsum(value * value for value in deme.values()), "H")


def identity(frequencies: Mapping[Any, Any]) -> float:
    """Return Nei gene identity ``J = sum(p_i ** 2)`` for one deme."""
    deme = _validate_deme(frequencies, 0)
    return _bounded(fsum(value * value for value in deme.values()), "J")


def hill_number(frequencies: Mapping[Any, Any], order: float | int) -> float:
    """Return the Hill number of the requested non-negative order for one deme."""
    deme = _validate_deme(frequencies, 0)
    return _hill(deme, _validate_order(order))


def h_s(table: FrequencyTable, deme_weights: DemeWeights = None) -> float:
    """Return weighted mean within-deme expected heterozygosity ``H_S``."""
    demes = _validate_table(table)
    weights = _validate_weights(len(demes), deme_weights)
    value = fsum(
        weight * (1.0 - fsum(freq * freq for freq in deme.values()))
        for deme, weight in zip(demes, weights, strict=True)
    )
    return _bounded(value, "H_S")


def h_t(table: FrequencyTable, deme_weights: DemeWeights = None) -> float:
    """Return expected heterozygosity ``H_T`` of the weighted pooled table."""
    demes = _validate_table(table)
    weights = _validate_weights(len(demes), deme_weights)
    pooled = _pooled(demes, weights)
    return _bounded(1.0 - fsum(freq * freq for freq in pooled.values()), "H_T")


def h_st(table: FrequencyTable, deme_weights: DemeWeights = None) -> float:
    """Return correctly partitioned between-deme heterozygosity ``H_ST``."""
    within = h_s(table, deme_weights)
    total = h_t(table, deme_weights)
    return _bounded((total - within) / (1.0 - within), "H_ST")


def total_hill_number(
    table: FrequencyTable,
    order: float | int,
    deme_weights: DemeWeights = None,
) -> float:
    """Return pooled Hill diversity ``^q D_T`` with optional deme weights."""
    demes = _validate_table(table)
    weights = _validate_weights(len(demes), deme_weights)
    return _hill(_pooled(demes, weights), _validate_order(order))


def within_hill_number(
    table: FrequencyTable,
    order: float | int,
    deme_weights: DemeWeights = None,
) -> float:
    """Return alpha Hill diversity ``^q D_S`` with optional deme weights."""
    demes = _validate_table(table)
    weights = _validate_weights(len(demes), deme_weights)
    validated_order = _validate_order(order)
    if validated_order == 0.0:
        return fsum(
            weight * sum(frequency > 0.0 for frequency in deme.values())
            for deme, weight in zip(demes, weights, strict=True)
        )
    if validated_order == 1.0:
        return exp(
            fsum(
                weight * _entropy(deme)
                for deme, weight in zip(demes, weights, strict=True)
            )
        )
    power_sum = fsum(
        weight
        * fsum(frequency**validated_order for frequency in deme.values() if frequency)
        for deme, weight in zip(demes, weights, strict=True)
    )
    return float(power_sum ** (1.0 / (1.0 - validated_order)))


def g_st(table: FrequencyTable, deme_weights: DemeWeights = None) -> float | None:
    """Return ``G_ST`` or ``None`` when total heterozygosity is zero."""
    demes = _validate_table(table)
    _require_multiple_demes(demes)
    within = h_s(demes, deme_weights)
    total = h_t(demes, deme_weights)
    if total == 0.0:
        return None
    return _bounded((total - within) / total, "G_ST")


def jost_d(table: FrequencyTable) -> float:
    """Return Jost's ``D`` using the required equal weighting of demes."""
    demes = _validate_table(table)
    _require_multiple_demes(demes)
    within = h_s(demes)
    total = h_t(demes)
    value = ((total - within) / (1.0 - within)) * len(demes) / (len(demes) - 1)
    return _bounded(value, "D")


def e_st(table: FrequencyTable, deme_weights: DemeWeights = None) -> float:
    """Return entropy differentiation ``E_ST`` with optional size weights."""
    demes = _validate_table(table)
    _require_multiple_demes(demes)
    weights = _validate_weights(len(demes), deme_weights)
    total_entropy = _entropy(_pooled(demes, weights))
    within_entropy = fsum(
        weight * _entropy(deme) for deme, weight in zip(demes, weights, strict=True)
    )
    weight_entropy = -fsum(weight * log(weight) for weight in weights)
    return _bounded((total_entropy - within_entropy) / weight_entropy, "E_ST")


def k_st(table: FrequencyTable) -> float:
    """Return allele-number differentiation ``K_ST`` with equal deme weights."""
    demes = _validate_table(table)
    _require_multiple_demes(demes)
    mean_allele_count = fsum(
        sum(frequency > 0.0 for frequency in deme.values()) for deme in demes
    ) / len(demes)
    total_allele_count = len(
        {
            allele_id
            for deme in demes
            for allele_id, frequency in deme.items()
            if frequency
        }
    )
    value = 1.0 - (total_allele_count / mean_allele_count - len(demes)) / (
        1.0 - len(demes)
    )
    return _bounded(value, "K_ST")


def differentiation_q(
    table: FrequencyTable,
    order: float | int,
    deme_weights: DemeWeights = None,
) -> float:
    """Return the normalized general differentiation family at order ``q``.

    Equal weighting is required for every order except ``q = 1``. At
    ``q = 1`` optional weights represent relative deme sizes and produce
    ``E_ST``. The endpoints ``q = 0`` and ``q = 2`` equal ``K_ST`` and
    Jost's ``D`` respectively.
    """
    validated_order = _validate_order(order)
    if validated_order == 1.0:
        return e_st(table, deme_weights)
    if deme_weights is not None:
        raise ValueError("deme weights are only defined for Differentiation_q at q=1")
    if validated_order == 0.0:
        return k_st(table)

    demes = _validate_table(table)
    _require_multiple_demes(demes)
    within = within_hill_number(demes, validated_order)
    total = total_hill_number(demes, validated_order)
    exponent = validated_order - 1.0
    log_ratio = log(within / total)
    log_inverse_deme_count = -log(len(demes))
    numerator = exp(exponent * log_inverse_deme_count) * expm1(
        exponent * (log_ratio - log_inverse_deme_count)
    )
    denominator = -expm1(exponent * log_inverse_deme_count)
    value = 1.0 - numerator / denominator
    return _bounded(value, f"Differentiation_{validated_order:g}")


def equilibrium_d(m: float, mu: float, d: int) -> float:
    """Return the finite-island equilibrium approximation for Jost's D.

    Args:
        m: Symmetric per-generation migration rate.
        mu: Infinite-alleles mutation rate.
        d: Number of equal demes.

    Returns:
        ``1 / (1 + m / (mu * (d - 1)))``.
    """
    _validate_equilibrium_inputs(population_size=1, m=m, mu=mu, d=d)
    if mu == 0.0:
        raise ValueError("equilibrium D requires mu greater than 0")
    return _bounded(1.0 / (1.0 + m / (mu * (d - 1))), "equilibrium D")


def equilibrium_g_st(
    population_size: int,
    m: float,
    mu: float,
    d: int,
) -> float:
    """Return the equilibrium G_ST approximation for gene-copy ``N``.

    The source formula uses ``4 * N_individuals`` for diploids. This
    application defines ``N`` as the number of gene copies directly, so both
    terms use ``2 * N``.

    Args:
        population_size: Gene-copy count in each equal deme.
        m: Symmetric per-generation migration rate.
        mu: Infinite-alleles mutation rate.
        d: Number of equal demes.

    Returns:
        The finite-island equilibrium approximation.
    """
    _validate_equilibrium_inputs(
        population_size=population_size,
        m=m,
        mu=mu,
        d=d,
    )
    finite_deme_factor = d / (d - 1)
    denominator = (
        finite_deme_factor**2 * 2.0 * population_size * m
        + finite_deme_factor * 2.0 * population_size * mu
        + 1.0
    )
    return _bounded(1.0 / denominator, "equilibrium G_ST")


def statistics_report(
    table: FrequencyTable,
    deme_weights: DemeWeights = None,
) -> DifferentiationReport:
    """Return the scalar statistics block consumed by an engine report.

    ``H_S``, ``H_T``, ``H_ST``, ``G_ST``, ``D``, and ``K_ST`` use equal
    deme weighting as specified by their definitions. ``deme_weights`` is
    applied only to ``E_ST``; pass relative deme sizes to request its native
    size-weighted form.
    """
    demes = _validate_table(table)
    _require_multiple_demes(demes)
    within = h_s(demes)
    total = h_t(demes)
    return {
        "H_S": within,
        "H_T": total,
        "H_ST": _bounded((total - within) / (1.0 - within), "H_ST"),
        "G_ST": g_st(demes),
        "D": jost_d(demes),
        "E_ST": e_st(demes, deme_weights),
        "K_ST": k_st(demes),
    }


def _validate_equilibrium_inputs(
    *,
    population_size: int,
    m: float,
    mu: float,
    d: int,
) -> None:
    """Validate the equal-deme equilibrium approximation inputs."""
    if (
        isinstance(population_size, bool)
        or not isinstance(population_size, int)
        or population_size < 1
    ):
        raise ValueError("N must be a positive gene-copy count")
    if isinstance(d, bool) or not isinstance(d, int) or d < _MINIMUM_DEMES:
        raise ValueError("d must be at least 2")
    for name, value in (("m", m), ("mu", mu)):
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not isfinite(value)
            or not 0.0 <= value <= 1.0
        ):
            raise ValueError(f"{name} must be between 0 and 1")
