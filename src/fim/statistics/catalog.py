"""The one list of every statistic fim computes, captures or shows.

Every statistic's name used to be spelled out by hand in a dozen places:
the engine's tracked sets, the convergence choices, the GUI's result
list, the Compare history sampler, the statistics table's rows, the
results tables' columns, the tooltips. Two recorded bugs came from those
copies drifting apart (`A_CGD` missing from one list raised `KeyError`
in Compare; `H_ST` missing from another left it with no history). This
module is the single definition the others now derive from: one
`StatisticSpec` per statistic, in display order.

Each spec answers four separate questions:

- **What is it?** `key`, the labels, `description`, `help`.
- **What does it describe?** `scope`: one value per generation
  (``"global"``) or one value per pair of demes (``"pair"``).
- **When is it computed?** `history`: tracked every generation always
  (``"always"``), every generation only when
  `SimulationParams.track_expensive_statistics` opts in (``"opt_in"``),
  or not tracked per generation (``"none"``: computed for each report,
  each displayed frame and each saved result instead).
- **Is it shown by default?** `default_shown`. What a researcher
  actually sees is their own choice (the GUI's "Statistics shown"
  setting); this is only the starting point. Showing or hiding a
  statistic never changes what is computed or saved.

This module imports nothing from the simulator or the GUI. It sits in
`fim.statistics` beside the formulas it describes.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass
from typing import Final, Literal, TypeAlias

from .genetic_distance import (
    NEI_DENOMINATORS,
    NEI_LOCUS_RULES,
    NeiDenominator,
    NeiLocusRule,
)

__all__ = [
    "CATALOG",
    "DEFAULT_PAIRWISE_MAX_DEMES",
    "History",
    "Measure",
    "Scope",
    "StatisticSpec",
    "catalog_payload",
    "convergence_statistic_keys",
    "default_shown_keys",
    "history_keys",
    "nei_key",
    "pair_keys",
    "report_keys",
    "spec",
]

Scope: TypeAlias = Literal["global", "pair"]
History: TypeAlias = Literal["always", "opt_in", "none"]
Measure: TypeAlias = Literal["distance", "identity"]

DEFAULT_PAIRWISE_MAX_DEMES: Final = 1024
"""Largest deme count whose full all-pairs matrices are saved by default.

At d = 1024 one run's `pairwise.json` is about 52 MB (five matrices of
523,776 values) and takes about a second to compute and write. Above the
limit the file records only that the matrices were skipped; any specific
pair can still be recomputed from the saved trajectory. A researcher can raise or
lower the limit in Settings or with `fim run --pairwise-max-demes`.
"""

Bounds: TypeAlias = tuple[float | None, float | None]

_PROPORTION: Final[Bounds] = (0.0, 1.0)
_NON_NEGATIVE: Final[Bounds] = (0.0, None)
_UNBOUNDED: Final[Bounds] = (None, None)


@dataclass(frozen=True, slots=True)
class StatisticSpec:
    """One statistic's identity, scope, cost and presentation.

    Args:
        key: Stable identifier. The JSON key in every saved result and
            the name every caller uses.
        label_html: Display name with HTML subscripts, for table rows.
        label_text: The same name as plain text, for tooltips and files.
        description: One sentence a researcher sees on hover.
        group: Section the Settings dialog lists it under.
        scope: ``"global"`` or ``"pair"``.
        history: ``"always"``, ``"opt_in"`` or ``"none"`` (module
            docstring).
        bounds: ``(lower, upper)``; ``None`` for an open end. A
            ``(0, 1)`` statistic is drawn on a fixed proportion axis.
        convergence_eligible: Whether a run may stop on it. Only measures
            the convergence monitor was designed for are eligible.
        default_shown: Whether a fresh install shows it.
        default_plotted: Whether its trajectory curve starts visible
            (only meaningful for a statistic with a per-generation
            history).
        help: Longer explanation for Settings and Help, or ``""``.
        nei: For the Nei family, ``(measure, denominator, locus_rule)``;
            ``None`` otherwise.
    """

    key: str
    label_html: str
    label_text: str
    description: str
    group: str
    scope: Scope
    history: History
    bounds: tuple[float | None, float | None]
    convergence_eligible: bool
    default_shown: bool
    default_plotted: bool = True
    help: str = ""
    nei: tuple[Measure, NeiDenominator, NeiLocusRule] | None = None


def _core(
    key: str,
    label_html: str,
    label_text: str,
    description: str,
    group: str,
    history: History,
    bounds: tuple[float | None, float | None],
    *,
    default_plotted: bool = True,
) -> StatisticSpec:
    """Return one of the ten original, convergence-eligible statistics."""
    return StatisticSpec(
        key=key,
        label_html=label_html,
        label_text=label_text,
        description=description,
        group=group,
        scope="global",
        history=history,
        bounds=bounds,
        convergence_eligible=True,
        default_shown=True,
        default_plotted=default_plotted,
    )


_ORIGINAL: Final[tuple[StatisticSpec, ...]] = (
    _core(
        "D",
        "D",
        "D",
        "allelic differentiation, weighting alleles by frequency (Jost's D, q=2)",
        "differentiation",
        "always",
        _PROPORTION,
    ),
    _core(
        "G_ST",
        "G<sub>ST</sub>",
        "G_ST",
        "nearness to fixation (Nei's G_ST), not a measure of differentiation",
        "differentiation",
        "always",
        _PROPORTION,
    ),
    _core(
        "E_ST",
        "E<sub>ST</sub>",
        "E_ST",
        "allelic differentiation, weighting every allele by its information (q=1)",
        "differentiation",
        "opt_in",
        _PROPORTION,
    ),
    _core(
        "K_ST",
        "K<sub>ST</sub>",
        "K_ST",
        "allelic differentiation, counting alleles unique to a deme (q=0)",
        "differentiation",
        "opt_in",
        _PROPORTION,
    ),
    _core(
        "H_S",
        "H<sub>S</sub>",
        "H_S",
        "within-deme heterozygosity",
        "diversity",
        "always",
        _PROPORTION,
    ),
    _core(
        "H_T",
        "H<sub>T</sub>",
        "H_T",
        "pooled heterozygosity across all demes",
        "diversity",
        "always",
        _PROPORTION,
    ),
    _core(
        "H_ST",
        "H<sub>ST</sub>",
        "H_ST",
        "nearness to fixation, Hedrick's maximum-standardized form",
        "differentiation",
        "always",
        _PROPORTION,
    ),
    _core(
        "A_CGD",
        "A<sub>CGD</sub>",
        "A_CGD",
        "coancestry-based genetic distance",
        "distance",
        "opt_in",
        _NON_NEGATIVE,
        default_plotted=False,
    ),
    _core(
        "Delta",
        "δ<sub>G</sub>",
        "δ_G",
        "Gregorius's δ — mean pairwise allelic differentiation over deme pairs",
        "differentiation",
        "opt_in",
        _NON_NEGATIVE,
        default_plotted=False,
    ),
    _core(
        "MI",
        "I",
        "I",
        "Sherwin's mutual information between allele and deme",
        "differentiation",
        "opt_in",
        _NON_NEGATIVE,
        default_plotted=False,
    ),
)

_IDENTITIES: Final[tuple[StatisticSpec, ...]] = (
    StatisticSpec(
        key="Gs",
        label_html="G<sub>s</sub>",
        label_text="Gs",
        description="within-deme gene identity: the chance two gene copies "
        "from one deme are the same allele (1 - H_S)",
        group="identity",
        scope="global",
        history="none",
        bounds=_PROPORTION,
        convergence_eligible=False,
        default_shown=False,
    ),
    StatisticSpec(
        key="Gd",
        label_html="G<sub>d</sub>",
        label_text="Gd",
        description="between-deme gene identity: the chance two gene copies "
        "from different demes are the same allele",
        group="identity",
        scope="global",
        history="none",
        bounds=_PROPORTION,
        convergence_eligible=False,
        default_shown=False,
    ),
)

_DENOMINATOR_WORDS: Final[dict[NeiDenominator, str]] = {
    "geometric": "geometric",
    "arithmetic": "arithmetic",
}
_DENOMINATOR_FORMULAS: Final[dict[tuple[Scope, NeiDenominator], str]] = {
    ("pair", "geometric"): "sqrt(J_X J_Y)",
    ("pair", "arithmetic"): "((J_X + J_Y) / 2)",
    ("global", "geometric"): "(J_1 J_2 ... J_d)^(1/d)",
    ("global", "arithmetic"): "((J_1 + J_2 + ... + J_d) / d)",
}
_ATTRIBUTION: Final[dict[NeiDenominator, str]] = {
    "geometric": "Nei (1972)",
    "arithmetic": "Jost, L. (2026) private communication",
}

_NEGATIVE_NOTE: Final = (
    " Unlike every other form, this one can be negative: when one deme is "
    "far more diverse than the others, the mean between-deme identity can "
    "exceed the geometric mean of the within-deme identities. A negative "
    "value is not an error; see Help, 'Nei distances'."
)


def nei_key(
    measure: Measure,
    scope: Scope,
    denominator: NeiDenominator,
    locus_rule: NeiLocusRule,
) -> str:
    """Return the catalog key of one Nei family member.

    Args:
        measure: ``"distance"`` or ``"identity"``.
        scope: ``"pair"`` or ``"global"`` (all demes).
        denominator: ``"geometric"`` or ``"arithmetic"``.
        locus_rule: ``"pooled"`` (Nei's rule) or ``"locus_mean"``.

    Returns:
        A key such as ``"NEI_D_PAIR_ARITH"`` or
        ``"NEI_I_ALL_GEO_LOCUS_MEAN"``.
    """
    letter = "D" if measure == "distance" else "I"
    where = "PAIR" if scope == "pair" else "ALL"
    how = "GEO" if denominator == "geometric" else "ARITH"
    suffix = "_LOCUS_MEAN" if locus_rule == "locus_mean" else ""
    return f"NEI_{letter}_{where}_{how}{suffix}"


def _nei_spec(
    measure: Measure,
    scope: Scope,
    denominator: NeiDenominator,
    locus_rule: NeiLocusRule,
) -> StatisticSpec:
    """Build the spec for one member of the Nei family."""
    letter = "D" if measure == "distance" else "I"
    where = "pair" if scope == "pair" else "all"
    how = _DENOMINATOR_WORDS[denominator]
    rule_text = ", per-locus mean" if locus_rule == "locus_mean" else ""
    label_text = f"Nei {letter} ({where}, {how}{rule_text})"
    label_html = f"Nei {letter}<sub>{where}</sub> ({how}{rule_text})"
    subject = (
        "between the two demes chosen for the scatter plot"
        if scope == "pair"
        else "across all demes"
    )
    rule_sentence = (
        "Loci are combined by averaging each locus's own distance."
        if locus_rule == "locus_mean"
        else "Loci are combined by Nei's rule: identities are averaged "
        "over loci first, then one ratio is taken."
    )
    numerator = "J_XY" if scope == "pair" else "J_between"
    formula = f"I = {numerator} / {_DENOMINATOR_FORMULAS[(scope, denominator)]}" + (
        "" if measure == "identity" else ", D = -ln(I)"
    )
    unbounded = scope == "global" and denominator == "geometric"
    description = (
        f"Nei's genetic {'distance' if measure == 'distance' else 'identity'} "
        f"{subject}, {how} denominator ({_ATTRIBUTION[denominator]}). "
        f"{formula}. {rule_sentence}" + (_NEGATIVE_NOTE if unbounded else "")
    )
    bounds: Bounds
    if measure == "distance":
        bounds = _UNBOUNDED if unbounded else _NON_NEGATIVE
    else:
        bounds = _NON_NEGATIVE if unbounded else _PROPORTION
    return StatisticSpec(
        key=nei_key(measure, scope, denominator, locus_rule),
        label_html=label_html,
        label_text=label_text,
        description=description,
        group="nei-distance" if measure == "distance" else "nei-identity",
        scope=scope,
        history="none",
        bounds=bounds,
        convergence_eligible=False,
        default_shown=False,
        nei=(measure, denominator, locus_rule),
    )


def _nei_family() -> tuple[StatisticSpec, ...]:
    """Every Nei family spec, distances first, all demes before pairs."""
    measures: tuple[Measure, ...] = ("distance", "identity")
    scopes: tuple[Scope, ...] = ("global", "pair")
    return tuple(
        _nei_spec(measure, scope, denominator, locus_rule)
        for measure in measures
        for scope in scopes
        for denominator in NEI_DENOMINATORS
        for locus_rule in NEI_LOCUS_RULES
    )


def _derived(
    key: str,
    label_html: str,
    label_text: str,
    description: str,
    bounds: Bounds,
    scope: Scope = "global",
) -> StatisticSpec:
    """A hidden-by-default, not-eligible measure computed from shared quantities."""
    return StatisticSpec(
        key=key,
        label_html=label_html,
        label_text=label_text,
        description=description,
        group="differentiation",
        scope=scope,
        history="none",
        bounds=bounds,
        convergence_eligible=False,
        default_shown=False,
    )


# Measures that are functions of quantities every report already has
# (`fim.statistics.differentiation.derived_differentiation`, and the pair
# identities for pairwise F_ST), so capturing them costs nothing.
_DERIVED: Final[tuple[StatisticSpec, ...]] = (
    _derived(
        "D_m",
        "D<sub>m</sub>",
        "D_m",
        "Nei's (1973) mean pairwise between-deme gene diversity: how much a "
        "typical pair of demes differs, in the same units as heterozygosity "
        "rather than rescaled to 0-1",
        _NON_NEGATIVE,
    ),
    _derived(
        "R_ST",
        "R<sub>ST</sub>",
        "R_ST",
        "Nei's (1973) D_m relative to within-deme diversity (D_m / H_S): the "
        "between-deme signal measured against the within-deme diversity",
        _NON_NEGATIVE,
    ),
    _derived(
        "G_ST_NEI_LOG",
        "G′<sub>ST</sub> (Nei)",  # noqa: RUF001 - the prime is meant
        "G'_ST (Nei)",
        "Nei's (1973) logarithmic G'_ST, offered for large differentiation, "
        "where ordinary G_ST crowds toward 1. Not Hedrick's G'_ST",
        _PROPORTION,
    ),
    _derived(
        "G_ST_HEDRICK",
        "G′<sub>ST</sub> (Hedrick)",  # noqa: RUF001 - the prime is meant
        "G'_ST (Hedrick)",
        "Hedrick's (2005) standardized G'_ST: G_ST divided by the largest "
        "value it could take at this H_S, so complete differentiation reads 1. "
        "Not Nei's logarithmic G'_ST",
        _PROPORTION,
    ),
    _derived(
        "F_ST",
        "F<sub>ST</sub> (coancestry)",
        "F_ST (coancestry)",
        "coancestry F_ST (Goudet & Weir 2023): how much more alike two gene "
        "copies from the same deme are than two from different demes",
        _PROPORTION,
    ),
    _derived(
        "F_ST_PAIR",
        "F<sub>ST</sub><sub>pair</sub>",
        "F_ST (pair)",
        "pairwise F_ST (Goudet & Weir 2023 Eq. 10) between the two demes chosen "
        "for the scatter plot, from those two demes' data alone",
        _PROPORTION,
        scope="pair",
    ),
)

CATALOG: Final[tuple[StatisticSpec, ...]] = (
    _ORIGINAL + _IDENTITIES + _DERIVED + _nei_family()
)
"""Every statistic, in display order. Keys are unique."""

_BY_KEY: Final[dict[str, StatisticSpec]] = {entry.key: entry for entry in CATALOG}
if len(_BY_KEY) != len(CATALOG):  # pragma: no cover - guarded by a test
    raise RuntimeError("duplicate key in fim.statistics.catalog.CATALOG")


def spec(key: str) -> StatisticSpec:
    """Return the spec for `key`.

    Raises:
        KeyError: Naming the unknown key.
    """
    try:
        return _BY_KEY[key]
    except KeyError:
        raise KeyError(f"unknown statistic {key!r}") from None


def _keys(entries: Iterator[StatisticSpec]) -> tuple[str, ...]:
    """Return the keys of `entries`, in catalog order."""
    return tuple(entry.key for entry in entries)


def history_keys(history: History) -> tuple[str, ...]:
    """Return the keys tracked per generation under one `history` policy."""
    return _keys(entry for entry in CATALOG if entry.history == history)


def report_keys() -> tuple[str, ...]:
    """Return every global statistic a `FinalReport` carries, in catalog order."""
    return _keys(entry for entry in CATALOG if entry.scope == "global")


def pair_keys() -> tuple[str, ...]:
    """Return every pair-scope statistic, in catalog order."""
    return _keys(entry for entry in CATALOG if entry.scope == "pair")


def convergence_statistic_keys() -> tuple[str, ...]:
    """Return every statistic a run may stop on, in catalog order."""
    return _keys(entry for entry in CATALOG if entry.convergence_eligible)


def default_shown_keys() -> tuple[str, ...]:
    """Return what a fresh install shows, in catalog order."""
    return _keys(entry for entry in CATALOG if entry.default_shown)


def catalog_payload() -> list[dict[str, object]]:
    """Return the whole catalog as JSON-ready dictionaries, in order.

    The GUI receives exactly this (`Api.get_statistics_catalog`) and builds
    its rows, columns, checkboxes and tooltips from it.
    """
    payload: list[dict[str, object]] = []
    for entry in CATALOG:
        row = asdict(entry)
        row["bounds"] = list(entry.bounds)
        row["nei"] = list(entry.nei) if entry.nei is not None else None
        payload.append(row)
    return payload
