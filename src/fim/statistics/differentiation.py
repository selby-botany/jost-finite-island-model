"""Allele-frequency diversity and differentiation statistics.

This is where the actual mathematics behind this project's own named
statistics (`D`, `G_ST`, `E_ST`, `K_ST`, `H_S`, `H_T`, `H_ST`) lives —
every one of the formulas the
[differentiation-measures guide](../../doc/jost-differentiation-measures.md)
explains in depth, implemented here exactly as that guide (and the
paper it summarizes, Jost et al. 2018) defines them. Reading that guide
first is strongly recommended before this file — it explains, from
zero, *why* there is more than one way to measure "how different are
these populations," what each measure actually answers, and why they
can disagree about the very same data; this file only ever computes,
never explains, and the brief summaries in each function's own
docstring below assume the guide's own vocabulary rather than
re-deriving it every time.

The one-paragraph version, for orientation: every measure here starts
from **expected heterozygosity** (`heterozygosity`, `h_s`, `h_t`,
below) — the chance that two gene copies drawn at random are different
alleles — computed once *within* each deme (`H_S`) and once for all
demes *pooled together* (`H_T`). Since pooling different demes can only
add variation, `H_T` is always at least as large as `H_S`; every
differentiation measure in this file is some way of asking "how much
bigger is `H_T` than `H_S`, relative to some baseline" — and the
different measures (`G_ST`, `D`, `E_ST`, `K_ST`) are, precisely, the
different reasonable choices for what that baseline should be. None of
them is simply "more correct" than the others; each answers a
genuinely different question, which is exactly why this project reports
several side by side rather than picking one.

All functions operate on normalized allele-frequency tables and are
independent of model state, persistence, and the engine — pure
mathematics, with no idea that a "finite island model" or a "run" even
exists; anything with allele frequencies to compare could use this
module. "Normalized" means each deme's own frequencies already sum to
exactly 1 (a complete accounting of that deme's own gene pool);
"table" means a plain sequence with one entry per deme, each entry
itself a mapping from an allele's identity to its frequency in that
deme; allele identifiers must be integer-like (whole numbers, or
values that behave like them) purely as a bookkeeping convention — the
actual identity of an allele is never mathematically meaningful here,
only whether two entries share the same identity or not.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import exp, expm1, fsum, isfinite, log, sqrt
from numbers import Real
from operator import index as integer_index
from typing import Any, TypeAlias, TypedDict

FrequencyTable: TypeAlias = Sequence[Mapping[Any, Any]]
DemeWeights: TypeAlias = Sequence[Any] | None

_MINIMUM_DEMES = 2
_TOLERANCE = 1e-12

# Euler-Mascheroni constant gamma = -psi(1), to full double precision
# (Abramowitz & Stegun 1972, table 1.1) -- the additive constant every
# equilibrium Shannon-entropy formula below (`equilibrium_shannon_
# entropy_isolated` and its siblings) carries, following Chao et al.
# (2015) Eq. 2A.
_EULER_GAMMA = 0.5772156649015328606
# Threshold above which `_digamma`'s asymptotic series (Abramowitz &
# Stegun 1972, formula 6.3.18 -- the same one Chao et al.'s own S2
# Appendix cites) is accurate to within machine precision; below it,
# the recurrence psi(x+1) = psi(x) + 1/x shifts the argument up first.
_DIGAMMA_ASYMPTOTIC_THRESHOLD = 6.0


class DifferentiationReport(TypedDict):
    """Scalar statistics computed from a frequency table.

    One locus's own complete set of results from `statistics_report`,
    below — the same seven values (`fim.engine.FinalReport` reports the
    same six, minus `H_ST`, each averaged across every locus a run
    tracks). `G_ST` alone can be `None`: see `g_st`'s own docstring for
    why. Every other field is always a real number.
    """

    H_S: float
    H_T: float
    H_ST: float
    G_ST: float | None
    D: float
    E_ST: float
    K_ST: float


def _bounded(value: float, name: str) -> float:
    """Return a unit-interval value, tolerating floating-point roundoff.

    Every statistic this module computes is mathematically guaranteed
    to fall between 0 and 1 — but ordinary floating-point arithmetic
    (the way computers represent and combine non-whole numbers) can
    accumulate a tiny rounding error over a long chain of additions and
    multiplications, occasionally landing a fraction of a
    billionth outside that range (`1.0000000000003` instead of exactly
    `1.0`, for instance). This function is the one place that gets
    quietly clamped back to the true mathematical range — but only
    within `_TOLERANCE`, an extremely small margin; a value meaningfully
    outside `[0, 1]` is a real bug somewhere upstream, not rounding
    error, and is deliberately raised as an error here rather than
    silently clamped away, so that bug gets noticed instead of hidden.
    """
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
    """Validate one normalized deme mapping and canonicalize allele IDs.

    Every public function in this module ultimately calls this (via
    `_validate_table`) before doing any real mathematics, so no formula
    below ever has to defensively re-check its own input — by the time
    a formula runs, every allele ID is already a genuine, hashable
    integer identity, every frequency is already a finite number between
    0 and 1, and the whole deme's own frequencies are already confirmed
    to sum to 1 (a complete accounting of that deme's own gene pool,
    with nothing missing and nothing double-counted).
    """
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
    """Return normalized, strictly positive deme weights.

    "Normalized" here means the returned weights always sum to exactly
    1, regardless of what scale the caller's own numbers were in —
    passing raw population sizes like `(100, 400)` or the already-
    normalized fractions `(0.2, 0.8)` produces the identical result,
    since only each deme's own weight *relative to the others* actually
    matters for a weighted average. `deme_weights=None` (the default
    used throughout this module) means "give every deme equal weight" —
    each deme contributes the same amount to a weighted statistic
    regardless of its own actual population size; see the
    differentiation-measures guide's own explanation of why `E_ST`
    specifically also supports weighting by relative deme size instead.
    """
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
    """Return Shannon entropy, omitting zero-frequency alleles.

    Shannon entropy is a measure of unpredictability: how surprised you
    would be, on average, by the identity of the next gene copy drawn
    at random from this deme. It is 0 when the deme is fixed for one
    allele (drawing a copy is never surprising — you already know the
    answer) and grows the more alleles there are and the more evenly
    their frequencies are spread. This is the building block `E_ST`
    (`e_st`, below) is named after — "entropy differentiation." A
    zero-frequency allele contributes nothing to a real deme's own
    entropy (an allele that is not actually present cannot affect how
    surprising a draw is) and is skipped here purely to avoid
    `log(0)`, which is mathematically undefined.
    """
    return -fsum(
        frequency * log(frequency)
        for frequency in frequencies.values()
        if frequency > 0.0
    )


def _pooled(
    demes: Sequence[Mapping[int, float]],
    weights: Sequence[float],
) -> dict[int, float]:
    """Return the weighted pooled allele-frequency mapping.

    "Pooling" means combining every deme's own frequencies into one
    single, combined frequency table, as if every deme's gene pool had
    been mixed together into one — this is exactly how `H_T` (total
    heterozygosity, see this module's own docstring, above) is computed:
    pool everything first, then measure diversity once on the combined
    result, rather than averaging each deme's own separately measured
    diversity (which is instead what `H_S` does).
    """
    pooled: dict[int, float] = {}
    for deme, weight in zip(demes, weights, strict=True):
        for allele_id, frequency in deme.items():
            pooled[allele_id] = pooled.get(allele_id, 0.0) + weight * frequency
    return pooled


def _hill(frequencies: Mapping[int, float], order: float) -> float:
    """Return a Hill number from a validated frequency mapping.

    See this module's own docstring, above, and `hill_number`'s own
    docstring, below, for what a Hill number actually measures and why
    `order` (often written "q" in the literature) changes how much
    weight rare alleles get. The three cases handled separately below
    are not arbitrary special-casing: `order = 0` and `order = 1` are
    both limiting cases of the same one general formula that would
    otherwise divide by zero or take the logarithm of zero if evaluated
    naively, so each is computed via its own well-known closed-form
    limit instead (`order = 0` counts alleles outright; `order = 1` is
    the exponential of Shannon entropy, see `_entropy`, above) — the
    ordinary case just below handles every other, non-limiting order.
    """
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
    """Return expected heterozygosity ``H = 1 - sum(p_i ** 2)`` for one deme.

    Draw two gene copies at random (with replacement) from this one
    deme's own pool. `sum(p_i ** 2)` is the chance they happen to be the
    exact same allele, so `H` is the chance they are different — the
    standard, textbook measure of genetic diversity within a single
    group (see this module's own docstring, above, for how it becomes a
    *differentiation* measure once two or more demes are compared). `H`
    is always between 0 (the deme is fixed — every gene copy is the
    same allele, so two random draws can never differ) and just under 1
    (approaching 1 only as the number of equally common alleles grows
    without bound — `H` never actually reaches it for any finite number
    of alleles).
    """
    deme = _validate_deme(frequencies, 0)
    return _bounded(1.0 - fsum(value * value for value in deme.values()), "H")


def identity(frequencies: Mapping[Any, Any]) -> float:
    """Return Nei gene identity ``J = sum(p_i ** 2)`` for one deme.

    The exact mirror image of `heterozygosity`, above: `J = 1 - H` is
    the chance two randomly drawn gene copies from this deme *do* match,
    rather than the chance they differ. Working in `J` rather than `H`
    makes several formulas below (`jost_d` especially) considerably
    simpler to read.
    """
    deme = _validate_deme(frequencies, 0)
    return _bounded(fsum(value * value for value in deme.values()), "J")


def hill_number(frequencies: Mapping[Any, Any], order: float | int) -> float:
    """Return the Hill number of the requested non-negative order for one deme.

    A Hill number answers "how many *equally common* alleles would this
    deme need to have, to show exactly this much diversity" — a much
    more intuitive scale than a raw probability like `H` above, since a
    Hill number of, say, `6.2` genuinely means "about as diverse as 6.2
    equally common alleles," whereas `H = 0.95` on its own gives no
    similarly direct sense of scale. `order` (often written "q" in the
    literature — see this module's own docstring, above, and
    `differentiation_q`'s own docstring, below) controls how much weight
    rare alleles get: `order = 0` counts every allele actually present,
    however rare; `order = 2` is dominated almost entirely by whichever
    alleles are already common (and equals `1 / (1 - H)`, the classic
    "effective number of alleles"); `order = 1` sits in between, weighting
    each allele by its own actual frequency.
    """
    deme = _validate_deme(frequencies, 0)
    return _hill(deme, _validate_order(order))


def h_s(table: FrequencyTable, deme_weights: DemeWeights = None) -> float:
    """Return weighted mean within-deme expected heterozygosity ``H_S``.

    "How much variation does a typical deme hold internally?" — compute
    `heterozygosity` separately for each deme, then take a weighted
    average across demes (see `_validate_weights`'s own docstring for
    what the weights mean and why `None`, the default, means "every
    deme counts equally"). One of the two building blocks (alongside
    `h_t`, below) every differentiation measure in this module is built
    from.
    """
    demes = _validate_table(table)
    weights = _validate_weights(len(demes), deme_weights)
    value = fsum(
        weight * (1.0 - fsum(freq * freq for freq in deme.values()))
        for deme, weight in zip(demes, weights, strict=True)
    )
    return _bounded(value, "H_S")


def h_t(table: FrequencyTable, deme_weights: DemeWeights = None) -> float:
    """Return expected heterozygosity ``H_T`` of the weighted pooled table.

    "How much variation is there altogether?" — pool every deme's own
    frequencies into one single, combined gene pool (`_pooled`, above),
    then compute `heterozygosity` once on that combined result. Since
    pooling different demes can only ever add variation (mixing
    different populations together cannot make the mixture *less*
    diverse than any one of them was alone), `H_T` is always at least as
    large as `H_S` — the gap between them, `H_T - H_S`, is the raw
    material every differentiation measure in this module works with.
    """
    demes = _validate_table(table)
    weights = _validate_weights(len(demes), deme_weights)
    pooled = _pooled(demes, weights)
    return _bounded(1.0 - fsum(freq * freq for freq in pooled.values()), "H_T")


def h_st(table: FrequencyTable, deme_weights: DemeWeights = None) -> float:
    """Return correctly partitioned between-deme heterozygosity ``H_ST``.

    The naive way to split `H_T` into a within-deme part and a between-
    deme part would be simple subtraction (`H_T - H_S` as "the between-
    deme component"), the way it would work for many other statistics —
    but heterozygosity specifically does not partition that simply (it
    is "subadditive": the true relationship is
    ``H_T = H_S + H_ST - H_S * H_ST``, not plain addition). Solving that
    relationship for the actual between-deme component gives the
    formula below — the differentiation-measures guide's own "Part V"
    walks through why the naive version is wrong in more depth. This
    exact same expression, before the `d/(d-1)` rescaling `jost_d`
    (below) applies to stretch its maximum out to exactly 1, is also
    the very first term of Jost's `D` — `D` is, in a precise sense,
    nothing more than this correctly-partitioned quantity, normalized.
    """
    within = h_s(table, deme_weights)
    total = h_t(table, deme_weights)
    return _bounded((total - within) / (1.0 - within), "H_ST")


def total_hill_number(
    table: FrequencyTable,
    order: float | int,
    deme_weights: DemeWeights = None,
) -> float:
    """Return pooled Hill diversity ``^q D_T`` with optional deme weights.

    The multi-deme, Hill-number-family counterpart to `h_t`, above: pool
    every deme together first, then compute a Hill number (see
    `hill_number`'s own docstring for what that measures) on the
    combined result, at whichever `order` is requested.
    """
    demes = _validate_table(table)
    weights = _validate_weights(len(demes), deme_weights)
    return _hill(_pooled(demes, weights), _validate_order(order))


def within_hill_number(
    table: FrequencyTable,
    order: float | int,
    deme_weights: DemeWeights = None,
) -> float:
    """Return alpha Hill diversity ``^q D_S`` with optional deme weights.

    The multi-deme, Hill-number-family counterpart to `h_s`, above: the
    weighted average of each individual deme's own Hill number, at
    whichever `order` is requested — "alpha diversity" is this
    statistic's own standard name in the wider ecology literature (the
    average diversity *within* a typical site), as distinct from "beta
    diversity" (how much diversity is added by comparing *between*
    sites — see `differentiation_q`'s own docstring, below, for where
    that shows up in this module).
    """
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
    """Return ``G_ST`` or ``None`` when total heterozygosity is zero.

    Nei's `G_ST` — "how close to complete fixation has the differentiation
    process gone" (Wright's own original framing, quoted in the
    differentiation-measures guide) — is `(H_T - H_S) / H_T`: the gap
    between total and within-deme heterozygosity, this time as a
    fraction *of the total heterozygosity itself* (unlike `H_ST`,
    above, which divides by `1 - H_S` instead — that single choice of
    denominator is the entire difference between the two families of
    measures the guide discusses). `G_ST` genuinely has no defined value
    when `H_T` is exactly zero — every deme is fixed for the identical
    single allele, so there is no variation anywhere to measure a
    *fraction* of at all, not even zero; `None` here is the honest
    answer, not a fabricated `0.0` or `1.0`.
    """
    demes = _validate_table(table)
    _require_multiple_demes(demes)
    within = h_s(demes, deme_weights)
    total = h_t(demes, deme_weights)
    if total == 0.0:
        return None
    return _bounded((total - within) / total, "G_ST")


def d_m(table: FrequencyTable) -> float:
    """Return Nei's mean pairwise between-deme gene diversity ``D_m``.

    "How much do a typical *pair* of demes actually differ, in absolute
    terms" (Nei 1973, Eq. 10) — unlike every other between-deme measure
    in this module, `D_m` is deliberately **not** rescaled to `[0, 1]`;
    it is expressed in the same units as heterozygosity itself, so it
    stays comparable across populations with very different within-deme
    diversity (`H_S`) — the exact comparison `g_st` cannot make, since
    `G_ST` can be "very large even if the absolute gene differentiation
    is small" whenever `H_S` happens to be small (Nei's own words). It is
    `H_T - H_S` — Nei's Eq. 7 `D_ST`, the *simple* difference, not the
    subadditivity-corrected `h_st` this module also exposes — rescaled
    by `d / (d - 1)` to average only over *distinct* pairs of demes,
    excluding the `d` "compare a deme to itself" terms Eq. 7's own
    average silently includes. `H_T <= 1` and `H_S >= 0` give a loose
    universal ceiling of `d/(d-1)`, but that ceiling is not reached in
    practice: at `H_S = 0` (every deme fixed for a single allele, the
    only way to reach zero within-deme diversity at all), `H_T` itself
    tops out at exactly `(d-1)/d` — pooling `d` equally weighted, all-
    distinct point masses is the most spread-out a `d`-outcome
    distribution can be — so `D_m` reaches exactly `1` there, for any
    `d`, not `d/(d-1)`. Like `jost_d`, this follows Nei's own derivation
    in assuming equal deme weights throughout (Eq. 4's `w_i = 1/s`);
    there is no `deme_weights` parameter here for the same reason
    `jost_d` has none.
    """
    demes = _validate_table(table)
    _require_multiple_demes(demes)
    within = h_s(demes)
    total = h_t(demes)
    deme_count = len(demes)
    value = (deme_count / (deme_count - 1)) * (total - within)
    if value < 0.0:
        if value < -_TOLERANCE:
            message = f"D_m is negative beyond floating-point tolerance: {value!r}"
            raise ArithmeticError(message)
        return 0.0
    return value


def r_st(table: FrequencyTable) -> float | None:
    """Return ``D_m`` relative to within-deme diversity, or ``None``.

    Nei's Eq. 11: `D_m / H_S` — the same absolute between-deme diversity
    `d_m` computes, this time expressed *relative to* how much diversity
    a typical deme holds internally, so it can answer "how big is the
    between-deme signal compared to the within-deme noise" without
    `G_ST`'s own denominator (`H_T`, which already includes the between-
    deme signal itself). Undefined, and returned as `None` rather than a
    fabricated `inf`, when `H_S` is exactly zero — every deme
    individually fixed for a single allele, with no within-deme
    diversity to express the between-deme diversity relative to at all;
    the same honest-`None` convention `g_st` already uses for its own,
    structurally identical zero-denominator case.
    """
    demes = _validate_table(table)
    _require_multiple_demes(demes)
    within = h_s(demes)
    if within == 0.0:
        return None
    return d_m(demes) / within


def g_st_log(table: FrequencyTable, deme_weights: DemeWeights = None) -> float | None:
    """Return Nei's log-based large-differentiation ``G_ST`` estimator.

    Nei (1973)'s closing discussion, verbatim: "a better estimate of
    `G_ST` may be obtained by `-log_e(J_T/J_S) / [-log_e(J_T)]`" — offered
    as a replacement for ordinary `g_st` specifically when
    differentiation is large (subspecies-level) and `J_T` (`= 1 - H_T`)
    is much smaller than `J_S` (`= 1 - H_S`), the regime in which linear
    `g_st` saturates toward 1 and stops discriminating well between
    "very differentiated" and "almost completely fixed apart." The paper
    gives this estimator no name or equation number of its own.

    Rigorously bounded in `[0, 1]`, exactly like `g_st` — provably, not
    just empirically, from the same two facts `g_st` already relies on
    (`H_S <= H_T` always, since pooling can only add variation; both are
    probabilities). Writing the formula as
    `1 + ln(J_S) / (-ln(J_T))`: `J_S <= 1` makes `ln(J_S) <= 0`, so the
    second term is never positive, giving the `<= 1` bound; `J_S >= J_T`
    (from `H_S <= H_T`) makes `ln(J_S) >= ln(J_T)`, so the second term is
    never below `ln(J_T)/(-ln(J_T)) = -1`, giving the `>= 0` bound. See
    `dev/doc/apps/selby/jost-finite-island-model/20260830-claude-sonnet-
    5-nei-1973-gene-diversity-test-plan.md` in the `1121-citrus` project
    for the full derivation this docstring summarizes.

    Undefined, and returned as `None` rather than a fabricated value, in
    the same spirit as `g_st`'s own `None` case: when `H_T` is exactly
    zero (complete fixation on the identical allele everywhere — no
    variation anywhere to measure a fraction of, `g_st`'s own case
    exactly), or when `H_S` is exactly one (`J_S = 0`, `ln(J_S)`
    undefined — every deme individually has zero chance that two
    randomly drawn gene copies match).
    """
    demes = _validate_table(table)
    _require_multiple_demes(demes)
    within = h_s(demes, deme_weights)
    total = h_t(demes, deme_weights)
    if total == 0.0 or within == 1.0:
        return None
    within_identity = 1.0 - within
    total_identity = 1.0 - total
    value = log(within_identity / total_identity) / -log(total_identity)
    return _bounded(value, "G_ST_log")


def jost_d(table: FrequencyTable) -> float:
    """Return Jost's ``D`` using the required equal weighting of demes.

    "Do these demes hold different alleles at all" — exactly `H_ST`
    above (the correctly partitioned between-deme heterozygosity), with
    one further rescaling: multiplying by `d / (d - 1)` stretches its
    maximum possible value from `(d-1)/d` out to exactly 1, so `D = 1`
    means precisely "these demes share no alleles in common" and
    `D = 0` means precisely "these demes are genetically identical" —
    regardless of how many demes `d` there are. Equal deme weighting is
    not optional here (unlike `e_st`, below): the differentiation-
    measures guide explains why `D` gives every deme equal statistical
    weight by construction, one of the few real trade-offs it has
    relative to `E_ST`.
    """
    demes = _validate_table(table)
    _require_multiple_demes(demes)
    within = h_s(demes)
    total = h_t(demes)
    value = ((total - within) / (1.0 - within)) * len(demes) / (len(demes) - 1)
    return _bounded(value, "D")


def e_st(table: FrequencyTable, deme_weights: DemeWeights = None) -> float:
    """Return entropy differentiation ``E_ST`` with optional size weights.

    The same "how differentiated are these demes" question `jost_d`
    answers, but built from Shannon entropy (`_entropy`, above) instead
    of heterozygosity — the `order = 1` member of the same one-formula
    family `differentiation_q`, below, generates (see this module's own
    docstring for the whole family). Its two real advantages over `D`,
    per the differentiation-measures guide: it handles demes of
    genuinely unequal size natively (`deme_weights` here can carry each
    deme's own relative population size, unlike `D`, which always
    weights every deme equally), and discovering a brand-new, unique
    allele in one deme can never make `E_ST` go *down* — a property `D`
    does not always have, since `D` weights alleles by their squared
    frequency and a new rare allele can slightly dilute the relative
    weight of other, already-differentiating alleles.
    """
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
    """Return allele-number differentiation ``K_ST`` with equal deme weights.

    The simplest and most stringent member of the family: ignores
    *frequencies* entirely and only asks "does this allele exist
    anywhere in this deme, yes or no" — the `order = 0` member of the
    same family `differentiation_q`, below, generates. Read it, roughly,
    as: the fraction of a typical deme's own alleles that turn out to
    be unique to that one deme, not found in any other. Because it
    ignores frequency entirely, `K_ST` responds to even the rarest,
    single-copy private allele exactly as strongly as it would to a
    common one — the strongest sensitivity to rare, private variation of
    any measure in this module.
    """
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

    `k_st`, `e_st`, and `jost_d` are not three independent, rival
    formulas — they are three settings of one single underlying dial,
    `order` (conventionally written "q" — see this module's own
    docstring, above), and this function is that one general formula,
    evaluated at whichever `order` is requested: `order = 0` reproduces
    `k_st` exactly, `order = 1` reproduces `e_st` exactly, and
    `order = 2` reproduces `jost_d` exactly (each of the three
    functions above is really just this same formula's own special-
    cased, more efficiently computed endpoint). Reporting the whole
    family across several values of `order` at once — a
    "differentiation-q sweep," see `fim.reanalyze.differentiation_q_for_state`
    — is one way of seeing how sensitive a conclusion is to which
    particular measure happened to be chosen, since different members
    of the family can genuinely disagree about how differentiated the
    very same population actually is.

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

    Every other function in this module computes a statistic from one
    *actual* frequency table — a real snapshot, from a real (or
    simulated) population. This function is different in kind: it is a
    theoretical prediction, from a mathematical analysis of what value
    `D` should settle to, on average, after a finite island model (see
    `fim.engine`'s own docstring for what that is) has been running long
    enough for its statistics to reach a stable, "equilibrium" balance
    between migration pulling demes together and drift pushing them
    apart — no actual simulated run is needed to compute it. Used to
    check the simulator itself against known theory (see the
    differentiation-measures guide's own "Part VI" for the full
    derivation and its assumptions) — a real simulation's own
    long-run average `D` should land close to what this formula
    predicts, for the same `m`/`mu`/`d`, if the simulator's own
    mechanics are correct. Notably, the population size `N` does not
    appear in this formula at all — only the *ratio* of migration to
    mutation controls where `D` settles, a genuinely counter-intuitive
    result the guide discusses at length.

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

    The `G_ST` counterpart to `equilibrium_d`, above — see that
    function's own docstring for what "equilibrium" means here and why
    a theoretical prediction like this one is useful for checking the
    simulator against known theory. Unlike `equilibrium_d`, this
    formula genuinely does depend on population size — specifically on
    `Nm` (population size times migration rate, the absolute number of
    migrants arriving each generation), not `m` alone — which is
    exactly the property Wright originally designed `G_ST`/`F_ST` to
    have (see `g_st`'s own docstring): a statistic sensitive to
    demography (population size and migration), essentially independent
    of the mutation rate, unlike `D`.

    The `(d/(d-1))**2` finite-deme correction multiplying the migration
    term below is not this project's own invention or specific to Jost
    et al. (2018) — confirmed directly against Crow & Aoki (1984)'s own
    Eq. 7/Eq. 8, `G_ST ~= 1/(4Nm*alpha+1)`, `alpha = [n/(n-1)]**2` (see
    the differentiation-measures guide's own Part VI and Appendix D for
    the citation, added there because earlier revisions of this project
    stated the correction with no attribution at all). Crow & Aoki's own
    equation has no mutation term at all — the `mu` term below is this
    project's own retention of a term they deliberately dropped as
    negligible under their stated approximation regime, not something
    their paper itself provides; see Part VI for the fuller distinction.

    The source formula uses ``4 * N_individuals`` for diploids (each
    individual carries two gene copies). This application defines ``N``
    as the number of gene copies directly, not individuals, so both
    terms use ``2 * N`` instead — the same quantity, `4 * N_individuals
    = 2 * N_gene_copies`, just expressed in this project's own units.

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


def _validate_identity_recovery_inputs(*, population_size: int, m: float) -> None:
    """Validate the identity-recovery family's shared ``population_size``/``m``.

    Mirrors `_validate_equilibrium_inputs`'s own checks for these two
    parameters, without requiring the `mu`/`d` arguments that family
    always takes — this family (Whitlock 1992's infinite-island, zero-
    mutation recovery formulas) needs neither.
    """
    if (
        isinstance(population_size, bool)
        or not isinstance(population_size, int)
        or population_size < 1
    ):
        raise ValueError("N must be a positive gene-copy count")
    if (
        isinstance(m, bool)
        or not isinstance(m, int | float)
        or not isfinite(m)
        or not 0.0 <= m <= 1.0
    ):
        raise ValueError("m must be between 0 and 1")


def _identity_recovery_rate_value(population_size: int, m: float) -> float:
    """Return Whitlock (1992) Eq. 1's ``L``, without validating inputs."""
    return (1.0 - m) ** 2 * (1.0 - 1.0 / population_size)


def _identity_recovery_equilibrium_value(population_size: int, m: float) -> float:
    """Return Whitlock (1992)'s ``f_hat_0``, without validating inputs."""
    rate = _identity_recovery_rate_value(population_size, m)
    return 1.0 / (population_size * (1.0 - rate))


def identity_recovery_rate(population_size: int, m: float) -> float:
    """Return Whitlock (1992) Eq. 1's per-generation identity-recovery rate.

    Whitlock (1992), *Evolution* 46(3):608-615 — a genuinely different
    kind of question from every other function in this module: not "what
    value does a statistic settle to at equilibrium" but "how fast does
    it get there." `L = (1-m)^2 * (1 - 1/N)` is the fraction of a
    disturbance's own gap from equilibrium that *survives* one more
    generation — Wright's classical infinite-island model (infinitely
    many demes, migrants drawn from an outside pool with zero identity
    by descent), mutation set aside (the paper's own stated
    simplification: "The mutation rate will be assumed to be negligibly
    small"). Provably in `[0, 1)` for every valid input — `(1-m)^2 <= 1`
    and `1 - 1/N < 1` for finite `N`, both factors non-negative — so no
    `_bounded` clamp is needed the way frequency-derived statistics
    elsewhere in this module require.

    This is not merely cited from the paper: it is an exact algebraic
    reduction of this project's own already-validated `_iterate_
    identities` recursion (`test/validation/test_simulator_equilibrium.py`,
    also the Tier 1 oracle for the Crow & Aoki torus scenario) in the
    `d -> infinity`, `mu = 0` limit — worked out in full, with a six-row
    numerical confirmation, in `dev/doc/apps/selby/jost-finite-island-
    model/20260830-claude-sonnet-5-whitlock-1992-identity-recovery-test-
    plan.md` in the `1121-citrus` project.

    Args:
        population_size: Gene-copy count ``N`` (Whitlock's own "2N").
        m: Migration rate.

    Returns:
        ``(1 - m)**2 * (1 - 1/population_size)``.
    """
    _validate_identity_recovery_inputs(population_size=population_size, m=m)
    return _identity_recovery_rate_value(population_size, m)


def identity_recovery_equilibrium(population_size: int, m: float) -> float:
    """Return Whitlock (1992)'s single-population identity equilibrium.

    `f_hat_0 = 1 / [N * (1 - L)]` (cited by Whitlock to Wright 1977) —
    the value `identity_recovery_trajectory`, below, approaches as
    ``generations`` grows without bound; see `identity_recovery_rate`'s
    own docstring for what `L` is and where this whole family comes
    from.

    Args:
        population_size: Gene-copy count ``N``.
        m: Migration rate.

    Returns:
        ``1 / (population_size * (1 - identity_recovery_rate(...)))``.
    """
    _validate_identity_recovery_inputs(population_size=population_size, m=m)
    return _identity_recovery_equilibrium_value(population_size, m)


def identity_recovery_trajectory(
    f0_initial: float,
    population_size: int,
    m: float,
    generations: float | int,
) -> float:
    """Return Whitlock (1992) Eq. 3's ``f_0`` after ``generations`` steps.

    ``f_0[i] + (1 - L**generations) * (f_hat_0 - f_0[i])`` — the closed-
    form trajectory a disturbed identity-by-descent value follows back
    toward `identity_recovery_equilibrium`, at the rate `identity_
    recovery_rate` describes. At ``generations = 0`` this returns
    ``f0_initial`` exactly (no time has passed to change anything); as
    ``generations`` grows it approaches the equilibrium value regardless
    of which direction ``f0_initial`` started from (`(1 - L**t) -> 1`
    monotonically as `t` grows, since `0 <= L < 1`).

    ``generations`` accepts a non-integer value deliberately, not just as
    a looser-than-necessary check: the paper's own half-life result
    (`identity_recovery_half_life`) treats generation count as continuous
    ("Note that ... the time to half recovery is t_1/2 = ..."), and
    evaluating this function at exactly that (generally non-integer)
    value is how the half-life formula's own correctness is checked —
    see that function's own docstring.

    Args:
        f0_initial: Starting identity by descent, ``f_0[i]``.
        population_size: Gene-copy count ``N``.
        m: Migration rate.
        generations: Generations elapsed since ``f0_initial`` (may be
            fractional; see above).

    Returns:
        ``f_0`` after ``generations`` generations.
    """
    _validate_identity_recovery_inputs(population_size=population_size, m=m)
    if (
        isinstance(f0_initial, bool)
        or not isinstance(f0_initial, int | float)
        or not isfinite(f0_initial)
        or not 0.0 <= f0_initial <= 1.0
    ):
        raise ValueError("f0_initial must be between 0 and 1")
    if (
        isinstance(generations, bool)
        or not isinstance(generations, int | float)
        or not isfinite(generations)
        or generations < 0
    ):
        raise ValueError("generations must be a non-negative number")
    rate = _identity_recovery_rate_value(population_size, m)
    equilibrium = _identity_recovery_equilibrium_value(population_size, m)
    return f0_initial + (1.0 - rate**generations) * (equilibrium - f0_initial)


def identity_recovery_half_life(population_size: int, m: float) -> float:
    """Return Whitlock (1992)'s generations to halfway recovery.

    ``t_1/2 = ln(1/2) / ln(L)`` — the number of generations (treating
    time as continuous, as the paper itself does for this formula) for
    `identity_recovery_trajectory` to close half the gap between its
    starting value and `identity_recovery_equilibrium`, regardless of
    which direction it started from (`identity_recovery_trajectory`
    evaluated at this many generations always lands exactly on the
    arithmetic midpoint — see the design doc for a worked numeric check
    in both directions).

    Special-cased at ``m = 1`` (the only way `identity_recovery_rate`
    can be exactly `0`, meaning full replacement every generation, hence
    equilibrium every generation too): returns `0.0` directly rather
    than evaluating ``log(0.5) / log(0.0)``, which is the correct limit
    (`t_1/2 -> 0` as `L -> 0+`), not an arbitrary guard.

    Args:
        population_size: Gene-copy count ``N``.
        m: Migration rate.

    Returns:
        Generations to close half the gap to equilibrium.
    """
    _validate_identity_recovery_inputs(population_size=population_size, m=m)
    rate = _identity_recovery_rate_value(population_size, m)
    if rate == 0.0:
        return 0.0
    return log(0.5) / log(rate)


def _digamma(x: float) -> float:
    """Return the digamma function psi(x) for x > 0.

    The digamma function is the logarithmic derivative of the gamma
    function, `psi(x) = Gamma'(x) / Gamma(x)` — the one special function
    every equilibrium Shannon-entropy formula below
    (`equilibrium_shannon_entropy_isolated` and its siblings) is written
    in terms of (Chao et al. 2015, Eq. 2A/5A). No dependency this
    project takes on ships a digamma implementation (`numpy` does not;
    `scipy` is not a dependency at all — see this module's own docstring
    for why formulas here stay dependency-free), so this is a small,
    self-contained one: the standard recurrence `psi(x+1) = psi(x) +
    1/x` shifts a small `x` up past `_DIGAMMA_ASYMPTOTIC_THRESHOLD`,
    where the asymptotic series below is accurate to within machine
    precision.

    Args:
        x: A positive real number.

    Returns:
        `psi(x)`, accurate to within machine precision for any `x > 0`.

    Raises:
        ValueError: If `x` is not a positive, finite real number.
    """
    if (
        isinstance(x, bool)
        or not isinstance(x, int | float)
        or not isfinite(x)
        or x <= 0.0
    ):
        raise ValueError("digamma is only defined here for a positive, finite x")
    value = 0.0
    shifted = float(x)
    while shifted < _DIGAMMA_ASYMPTOTIC_THRESHOLD:
        value -= 1.0 / shifted
        shifted += 1.0
    inverse = 1.0 / shifted
    inverse_squared = inverse * inverse
    value += log(shifted) - 0.5 * inverse
    value -= inverse_squared * (
        1.0 / 12.0 - inverse_squared * (1.0 / 120.0 - inverse_squared / 252.0)
    )
    return value


def _validate_isolated_equilibrium_inputs(*, population_size: int, mu: float) -> None:
    """Validate a single-isolated-deme equilibrium formula's inputs.

    The `equilibrium_g_st`/`equilibrium_d` counterpart
    (`_validate_equilibrium_inputs`) always requires `m` and `d` too,
    since every statistic it validates for is inherently a between-deme
    comparison. A single isolated deme has neither — there is nothing to
    migrate between and nothing to compare against — so this is a
    genuinely smaller, not just a specialized, contract.
    """
    if (
        isinstance(population_size, bool)
        or not isinstance(population_size, int)
        or population_size < 1
    ):
        raise ValueError("N must be a positive gene-copy count")
    if (
        isinstance(mu, bool)
        or not isinstance(mu, int | float)
        or not isfinite(mu)
        or not 0.0 <= mu <= 1.0
    ):
        raise ValueError("mu must be between 0 and 1")


def equilibrium_shannon_entropy_isolated(population_size: int, mu: float) -> float:
    """Return the equilibrium expected Shannon entropy of one isolated deme (IAM).

    Chao, Jost, Hsieh, Ma, Sherwin & Rollins (2015) Eq. 2A: at
    mutation-drift equilibrium under the infinite-alleles model, a
    single isolated deme's expected Shannon entropy is
    `psi(theta + 1) + gamma`, where `psi` is the digamma function and
    `gamma` is the Euler-Mascheroni constant — the Shannon-entropy-scale
    counterpart to `heterozygosity`'s own equilibrium, `theta / (theta +
    1)` (the same paper's Eq. 1, which this project already implements
    identically in miniature every time `_iterate_identities`
    — `test/validation/test_simulator_equilibrium.py` — is run with
    `m=0`).

    `theta = 4*N*mu` in the paper's own notation, where the paper's own
    `N` is diploid individuals. This project's own `population_size` is
    gene copies (`2*N`), so `theta` here is `2*population_size*mu` — the
    same halving already established for `equilibrium_g_st`/
    `equilibrium_d`, but confirmed independently for this formula rather
    than assumed by analogy: run this project's own exact finite-N
    identity recursion in isolation (`_iterate_identities` with `m=0`,
    which has no equivalent of the diffusion approximation's own `theta`
    at all — it is the literal discrete Wright-Fisher-style recursion
    this project's engine implements), and `1 - within_identity` at
    increasing `N` converges to `theta / (theta + 1)` under
    `theta = 2*population_size*mu` and nothing close to it under
    `theta = 4*population_size*mu` (checked directly at `N = 100, 1000,
    10000` before writing this docstring; the residual shrinks as `N`
    grows, the same `O(1/N)` diffusion-approximation pattern already
    documented for `equilibrium_g_st`/`equilibrium_d`).

    This measures a genuinely different family of statistic from every
    other function in this module — Shannon entropy, not heterozygosity
    — so it is not directly comparable to `equilibrium_d`/
    `equilibrium_g_st`, and is unbounded above rather than confined to
    `[0, 1]` (see `Returns`, below). See `within_hill_number`/
    `total_hill_number` at `order=1` for how this project already
    computes the *simulated* (not equilibrium-predicted) version of the
    same quantity from an actual frequency table:
    `log(within_hill_number(table, 1))` recovers Shannon entropy
    exactly, since `within_hill_number` itself already returns
    `exp(entropy)` at that order.

    Args:
        population_size: Gene-copy count `N` (this project's own
            convention — see above for the conversion from the paper's
            own diploid-individual `N`).
        mu: Infinite-alleles mutation rate; must be greater than 0 (an
            isolated deme with no mutation at all never reaches a
            polymorphic equilibrium to have an entropy of).

    Returns:
        The equilibrium expected Shannon entropy, in nats (natural-log
        units). Unlike every other statistic in this module, this is
        unbounded above: entropy grows without limit as the number of
        distinct alleles actually present grows, so there is no
        `_bounded` call here — a genuinely different, not merely
        unenforced, mathematical range.

    Raises:
        ValueError: If `population_size` or `mu` is invalid, or `mu` is
            exactly 0.
    """
    _validate_isolated_equilibrium_inputs(population_size=population_size, mu=mu)
    if mu == 0.0:
        raise ValueError("equilibrium Shannon entropy requires mu greater than 0")
    theta = 2.0 * population_size * mu
    return _digamma(theta + 1.0) + _EULER_GAMMA


def equilibrium_shannon_entropy_isolated_smm(population_size: int, mu: float) -> float:
    """Return the equilibrium expected Shannon entropy of one isolated deme (SMM).

    Chao et al. (2015) Eq. 5A: the stepwise-mutation-model counterpart to
    `equilibrium_shannon_entropy_isolated`, above — same `theta =
    2*population_size*mu` (this project's own gene-copy convention; see
    that function's own docstring for the ploidy derivation), plus
    `alpha = [(1 + 2*theta)**0.5 - 1] / 2` (the paper's own Eq. 4A/4B),
    giving `psi(theta + alpha + 1) - psi(alpha + 1)`. As `alpha` tends to
    0 this reduces exactly to the infinite-alleles formula above — the
    paper's own stated "bridge" between the two mutation models — and
    `alpha` is always non-negative for `theta >= 0`, so this can never
    silently drift below that limit.

    Unlike `equilibrium_shannon_entropy_isolated` (whose infinite-alleles
    model is exactly this project's own default `mutation_model`), this
    project has no stepwise-mutation-model implementation at all — a
    finite, ordered walk over adjacent allele states (the standard model
    for a microsatellite repeat count, ±1 per mutation), genuinely
    different from this project's own `"finite_alleles"` option (a fixed
    set of K alleles, mutation uniform over the other K-1, with no
    notion of "adjacent"). This function is included because the
    formula itself is real, published, and cheap to state correctly once
    `equilibrium_shannon_entropy_isolated` already exists — but, unlike
    that function, it has no engine-level scenario in this project to
    ever validate it against; treat it as a literature reference, not a
    simulator cross-check.

    Args:
        population_size: Gene-copy count `N`.
        mu: Per-generation mutation (single-step) rate; must be greater
            than 0.

    Returns:
        The equilibrium expected Shannon entropy, in nats — unbounded
        above, the same as `equilibrium_shannon_entropy_isolated`.

    Raises:
        ValueError: If `population_size` or `mu` is invalid, or `mu` is
            exactly 0.
    """
    _validate_isolated_equilibrium_inputs(population_size=population_size, mu=mu)
    if mu == 0.0:
        raise ValueError("equilibrium Shannon entropy requires mu greater than 0")
    theta = 2.0 * population_size * mu
    alpha = (sqrt(1.0 + 2.0 * theta) - 1.0) / 2.0
    return _digamma(theta + alpha + 1.0) - _digamma(alpha + 1.0)


def equilibrium_shannon_entropy_total(
    population_size: int,
    m: float,
    mu: float,
    d: int,
) -> float:
    """Return the equilibrium expected Shannon entropy of the pooled FIM population.

    Chao et al. (2015) Eq. 6 (IAM-FIM): the total-population counterpart
    to `equilibrium_shannon_entropy_isolated`, above, under Wright's
    finite island model — the same model `equilibrium_g_st`/
    `equilibrium_d` already predict `G_ST`/`D` for. The paper's own
    closed form for the total population's own effective mutation
    parameter is `theta_T = 4*N*n*mu + (n-1)*mu / (m* + mu)`, where
    `m* = m*n/(n-1)` follows Latter (1973)'s own notation (already used
    identically in `equilibrium_g_st`'s own citation history) and the
    paper's own `N` is diploid individuals per deme — converted here to
    this project's gene-copy `population_size` the same way
    `equilibrium_shannon_entropy_isolated` already is, and confirmed the
    same way: this formula's implied `theta_T / (theta_T + 1)`
    (Eq. 1's own heterozygosity link) matches this project's own exact
    finite-N identity recursion's pooled total-population identity,
    `(1/d)*within + ((d-1)/d)*between` from `_identity_fixed_point`, to
    within the same small, `O(1/N)`-scale residual already documented
    for `equilibrium_g_st`/`equilibrium_d` (checked directly, across
    three of this project's own existing scenarios spanning
    `N=100..2000, d=4..100`, before writing this docstring).

    Unbounded above, the same as `equilibrium_shannon_entropy_isolated`
    — see that function's own `Returns` for why.

    Args:
        population_size: Gene-copy count `N` per deme (equal across
            demes, matching the finite island model's own assumption).
        m: Symmetric per-generation migration rate.
        mu: Infinite-alleles mutation rate; must be greater than 0.
        d: Number of equal demes.

    Returns:
        The equilibrium expected Shannon entropy of the pooled
        total population, in nats.

    Raises:
        ValueError: If any input is invalid, or `mu` is exactly 0.
    """
    _validate_equilibrium_inputs(population_size=population_size, m=m, mu=mu, d=d)
    if mu == 0.0:
        raise ValueError("equilibrium Shannon entropy requires mu greater than 0")
    theta_total = _theta_total_iam(population_size, m, mu, d)
    return _digamma(theta_total + 1.0) + _EULER_GAMMA


def _theta_total_iam(population_size: int, m: float, mu: float, d: int) -> float:
    """Return `theta_T`, the IAM-FIM total population's own effective `theta`.

    Chao et al. (2015)'s own closed form (Table 1, IAM total-population
    row): `theta_T = 4*N*n*mu + (n-1)*mu / (m* + mu)`, where `m* =
    m*n/(n-1)` follows Latter (1973)'s own notation. Shared by
    `equilibrium_shannon_entropy_total`, above, and `equilibrium_
    shannon_entropy_subpopulation`/`equilibrium_shannon_differentiation`,
    below — every one of them needs this same total-population parameter
    first. See `equilibrium_shannon_entropy_total`'s own docstring for
    the ploidy-conversion derivation this carries (`4*N` in the paper's
    own diploid-individual notation is `2*population_size` in this
    project's gene-copy one).
    """
    migration_star = m * d / (d - 1)
    return 2.0 * population_size * d * mu + (d - 1) * mu / (migration_star + mu)


def equilibrium_shannon_entropy_subpopulation(
    population_size: int,
    m: float,
    mu: float,
    d: int,
) -> float:
    """Return the equilibrium expected Shannon entropy of a typical FIM subpopulation.

    Chao et al. (2015) Eq. 7D: the general *approximation* formula for a
    typical subpopulation's own expected Shannon entropy under IAM-FIM —
    the subpopulation counterpart to `equilibrium_shannon_entropy_total`,
    above. Unlike every other equilibrium function in this module, there
    is no exact closed form for this one at all: the paper's own exact
    expression (its Eq. 7C) is an integral with no elementary
    antiderivative, "numerically evaluated using standard integration
    software" in the paper's own words — genuinely outside what this
    module's own dependency-free, closed-form-only style can offer (a
    hand-rolled numerical quadrature was scoped separately and not
    built here; see `1121-citrus`'s Chao-et-al-2015 findings doc, item
    5). Eq. 7D is the paper's own stated approximation to that integral,
    accurate "except for the special case of two subpopulations (`n =
    2`)" — its own words, not a caveat added here — so a two-deme
    scenario's own result from this function should be treated with
    real skepticism, not the same confidence as `d >= 3`.

    Uses the same `theta_T` (`_theta_total_iam`) every other total-
    population-dependent formula in this module already shares.

    Args:
        population_size: Gene-copy count `N` per deme.
        m: Symmetric per-generation migration rate.
        mu: Infinite-alleles mutation rate; must be greater than 0.
        d: Number of equal demes.

    Returns:
        The approximate equilibrium expected Shannon entropy of a
        typical subpopulation, in nats. Unbounded above, the same as
        `equilibrium_shannon_entropy_isolated`/`_total` — but always
        less than or equal to `equilibrium_shannon_entropy_total`'s own
        return for the same inputs (a subpopulation can never hold more
        diversity than the whole population it is part of).

    Raises:
        ValueError: If any input is invalid, or `mu` is exactly 0.
    """
    _validate_equilibrium_inputs(population_size=population_size, m=m, mu=mu, d=d)
    if mu == 0.0:
        raise ValueError("equilibrium Shannon entropy requires mu greater than 0")
    migration_star = m * d / (d - 1)
    theta_total = _theta_total_iam(population_size, m, mu, d)
    migrants_star = 2.0 * population_size * migration_star
    total_mutation_rate = 2.0 * population_size * (migration_star + mu)

    first_term = _digamma(total_mutation_rate + 1.0)
    second_term = _digamma(migrants_star / (theta_total + 1.0) + 1.0)
    ratio = migrants_star / (migrants_star + theta_total + 1.0)
    third_term = 0.5 * ratio * ratio * theta_total / (theta_total + 2.0)
    return first_term - second_term + third_term


def equilibrium_shannon_differentiation(
    population_size: int,
    m: float,
    mu: float,
    d: int,
) -> float:
    """Return the equilibrium Shannon differentiation ("`1 - C_1n`") under IAM-FIM.

    Chao et al. (2015) Eq. 10: the mutual-information-based
    differentiation measure this project's own `differentiation_q` (at
    `order=1`, equivalent to `e_st`) computes from real, simulated data
    — this is that same statistic's *equilibrium prediction* instead,
    the `E_ST` counterpart to what `equilibrium_g_st`/`equilibrium_d`
    already are for `G_ST`/`D`. Defined as the mutual information
    between total-population and subpopulation allele identity,
    normalized by `log(d)`:
    `(equilibrium_shannon_entropy_total - equilibrium_shannon_entropy_
    subpopulation) / log(d)`.

    Inherits `equilibrium_shannon_entropy_subpopulation`'s own
    Eq. 7D approximation and its own stated weak spot: the paper's own
    text says that approximation is unreliable at `d = 2`, so treat a
    two-deme result from this function the same way — with real
    skepticism, not the confidence `d >= 3` warrants. Bounded in
    `[0, 1]` in principle (zero when every subpopulation is identical to
    the whole, one when subpopulations share no alleles at all, per the
    paper's own description) but not passed through `_bounded`: it is
    built from an approximation, not an exact formula, so a small
    excursion outside `[0, 1]` reflects that approximation's own error,
    not a bug to raise on.

    Args:
        population_size: Gene-copy count `N` per deme.
        m: Symmetric per-generation migration rate.
        mu: Infinite-alleles mutation rate; must be greater than 0.
        d: Number of equal demes.

    Returns:
        The approximate equilibrium Shannon differentiation.

    Raises:
        ValueError: If any input is invalid, or `mu` is exactly 0.
    """
    total = equilibrium_shannon_entropy_total(population_size, m, mu, d)
    subpopulation = equilibrium_shannon_entropy_subpopulation(population_size, m, mu, d)
    return (total - subpopulation) / log(d)


def statistics_report(
    table: FrequencyTable,
    deme_weights: DemeWeights = None,
) -> DifferentiationReport:
    """Return the scalar statistics block consumed by an engine report.

    The one function that computes all seven statistics for one
    frequency table at once — everywhere this project reports "the
    statistics" for a single locus, this is the function that produced
    them (see `fim.engine.report_for_state`, which calls this once per
    locus and then averages each field across every locus a run
    tracks).

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
