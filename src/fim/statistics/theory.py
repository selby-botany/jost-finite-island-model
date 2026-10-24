"""Closed-form predictions for an island model at equilibrium.

Every function takes plain numbers and returns plain numbers, like the rest
of this package. The GUI's Explore screen and sweep results card, and the
sweep export's theory columns, all read their predictions from here, so a
quantity added once appears in all of them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from math import exp
from typing import Any, Final

from fim.config.limits import MINIMUM_EQUILIBRIUM_DEMES, MINIMUM_EQUILIBRIUM_N
from fim.statistics.differentiation import (
    equilibrium_d,
    equilibrium_g_st,
    equilibrium_heterozygosity_isolated,
    equilibrium_heterozygosity_total,
    equilibrium_shannon_differentiation,
    equilibrium_shannon_entropy_subpopulation,
    equilibrium_shannon_entropy_total,
    identity_recovery_equilibrium,
    identity_recovery_half_life,
    identity_recovery_rate,
    mutation_negligible_equilibrium,
)

THEORY_STATISTICS: Final = ("D", "G_ST", "E_ST", "H_S", "H_T")
"""The predicted quantities that are also reported statistics of a run."""


def equilibrium_numeric_predictions(
    n: int, m: float, mu: float, d: int
) -> dict[str, float | bool | None]:
    """Evaluate every Explore prediction at one `(N, m, mu, d)` point.

    The single source of truth for *which* analytical quantities Explore
    knows about. Every presentation reads from here: the GUI's
    `_equilibrium_prediction_payload` formats these for the table,
    `Api.get_equilibrium_sweep` collects one of these per swept point for
    the chart, and the sweep export reads them as its theory columns. None
    maintains its own second list of statistic names, so a quantity added
    here appears everywhere at once.

    Every one of these is a closed-form function of `(N, m, mu, d)`
    alone, so every one of them is defined along every one of the four
    sweep axes. Several do not depend on every parameter -- $D$ has no
    `N` term at all (`equilibrium_d`'s own docstring: only the
    migration-to-mutation ratio sets where it settles), $H_S$ depends on
    `N` and `mu` only, and the whole Whitlock identity-recovery family
    depends on `N` and `m` only. Swept against a parameter they do not
    contain, those produce a flat line. That flat line is the point, not
    a degenerate case to suppress: $D$ level across two orders of
    magnitude of `N` while $G_{ST}$ falls away beneath it is this
    project's central claim, drawn rather than asserted.

    Args:
        n: Population size in gene copies per deme.
        m: Symmetric migration rate.
        mu: Infinite-alleles mutation rate.
        d: Number of equal demes.

    Returns:
        One entry per predicted quantity, as a plain number (or, for
        `mutation_negligible_equilibrium`, a bool). `None` marks a
        quantity this particular configuration leaves undefined -- every
        mutation-dependent entry at `mu == 0`, for instance. Callers
        plotting these must break the line at a `None` rather than
        reading it as a zero.
    """

    def value_of(function: Callable[..., float], *args: float | int) -> float | None:
        try:
            return function(*args)
        except ValueError:
            return None

    s_s_value = value_of(equilibrium_shannon_entropy_subpopulation, n, m, mu, d)
    s_t_value = value_of(equilibrium_shannon_entropy_total, n, m, mu, d)
    return {
        "D": value_of(equilibrium_d, m, mu, d),
        "G_ST": value_of(equilibrium_g_st, n, m, mu, d),
        "E_ST": value_of(equilibrium_shannon_differentiation, n, m, mu, d),
        "H_S": value_of(equilibrium_heterozygosity_isolated, n, mu),
        "H_T": value_of(equilibrium_heterozygosity_total, n, m, mu, d),
        "S_S": s_s_value,
        "S_T": s_t_value,
        # `exp` of the full-precision entropy, never of an already
        # display-rounded one -- `format_statistic` is applied to this
        # result, not to its input.
        "A_S": exp(s_s_value) if s_s_value is not None else None,
        "A_T": exp(s_t_value) if s_t_value is not None else None,
        "identity_recovery_rate": value_of(identity_recovery_rate, n, m),
        "identity_recovery_equilibrium": value_of(identity_recovery_equilibrium, n, m),
        "identity_recovery_half_life": value_of(identity_recovery_half_life, n, m),
        "mutation_negligible_equilibrium": mutation_negligible_equilibrium(m, mu, n),
    }


def theory_for_configuration(
    configuration: Mapping[str, Any],
) -> dict[str, float | None]:
    """Return the closed-form prediction of each reported statistic.

    The formulas are functions of gene copies per deme, and a configuration's
    `N` counts individuals, so `N` is multiplied by the ploidy word the
    configuration must carry (`"haploid"` to `"tetraploid"`).

    Args:
        configuration: A run configuration with at least `N`, `ploidy`, `m`,
            `mu` and `d`, as a sweep point's mapping.

    Returns:
        A value for each of `THEORY_STATISTICS`; all `None` where the closed
        form does not cover the configuration (a missing or non-numeric
        parameter, an unknown ploidy, or `N` or `d` below the smallest the
        formulas accept).
    """
    # Imported here because the model package imports this one.
    from fim.model.params import PLOIDY_WORDS  # noqa: PLC0415

    undefined: dict[str, float | None] = dict.fromkeys(THEORY_STATISTICS)
    try:
        n, m, mu, d = (
            configuration["N"],
            configuration["m"],
            configuration["mu"],
            configuration["d"],
        )
        ploidy_word = configuration["ploidy"]
    except KeyError:
        return undefined
    scalars = all(
        isinstance(value, int | float) and not isinstance(value, bool)
        for value in (n, m, mu, d)
    )
    if not scalars or n < MINIMUM_EQUILIBRIUM_N or d < MINIMUM_EQUILIBRIUM_DEMES:
        return undefined
    ploidy = next(
        (number for number, word in PLOIDY_WORDS.items() if word == ploidy_word), None
    )
    if ploidy is None:
        return undefined
    predictions = equilibrium_numeric_predictions(
        int(n) * ploidy, float(m), float(mu), int(d)
    )
    result: dict[str, float | None] = {}
    for name in THEORY_STATISTICS:
        value = predictions.get(name)
        result[name] = (
            float(value)
            if isinstance(value, float | int) and not isinstance(value, bool)
            else None
        )
    return result


def theory_at(
    base: Mapping[str, Any], coordinates: Mapping[str, Any]
) -> dict[str, float | None]:
    """Return the predictions for a sweep's base configuration at coordinates.

    Args:
        base: The sweep's base configuration.
        coordinates: Axis key to value, applied to `base` the way a sweep
            point is.

    Returns:
        As `theory_for_configuration`; all `None` if the coordinates do not
        apply to the base.
    """
    from fim.sweep import apply_coordinates  # noqa: PLC0415 - sweep imports engine

    try:
        configuration = apply_coordinates(base, coordinates)
    except ValueError:
        return dict.fromkeys(THEORY_STATISTICS)
    return theory_for_configuration(configuration)
