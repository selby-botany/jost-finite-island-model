"""Per-generation statistic extraction for the convergence monitor."""

from __future__ import annotations

import math
from collections.abc import Callable, Collection, Mapping, Sequence
from typing import Any, Final, TypedDict, cast

import numpy as np

from fim.convergence.window_statistics import IdentityStatistic
from fim.model.params import LocusAggregation, SimulationParams
from fim.model.state import ModelState
from fim.model.vector_block import VectorBlock
from fim.statistics.catalog import history_keys, nei_key, report_keys, unbounded_keys
from fim.statistics.differentiation import (
    DifferentiationReport,
    derived_differentiation,
    g_st_from_demes,
    gd_from_within_and_total,
    jost_d_from_within_and_total,
    statistics_report,
)
from fim.statistics.genetic_distance import (
    deme_gene_identities,
    nei_distance_from_identity,
    nei_family_from_identities,
)


def identity_statistics(params: SimulationParams) -> dict[str, IdentityStatistic]:
    """Describe `D` and `G_ST` as functions of `H_S` and `H_T` for `params`.

    Args:
        params: The run's configuration.

    Returns:
        `identity_statistics_for` its deme count.
    """
    return identity_statistics_for(len(params.population_sizes))


def identity_statistics_for(deme_count: int) -> dict[str, IdentityStatistic]:
    """Describe `D` and `G_ST` as functions of `H_S` and `H_T` (design 6.11).

    The values are the engine's own formulas (`jost_d_from_within_and_total`,
    `g_st_from_demes`), so "value of means" is exactly what a generation's own
    `D`/`G_ST` would be had the window's averaged identities been the state.
    The gradients are those formulas' derivatives, for the delta-method
    standard error; a test checks them against finite differences.

    Args:
        deme_count: The number of demes, which sets `D`'s scaling.

    Returns:
        `{"D": ..., "G_ST": ...}`.
    """
    demes = deme_count
    scale = demes / (demes - 1) if demes > 1 else 1.0

    def d_value(within: float, total: float) -> float | None:
        if within >= 1.0:
            return None
        return jost_d_from_within_and_total(demes, within, total)

    def d_gradient(within: float, total: float) -> tuple[float, float]:
        spread = 1.0 - within
        return scale * (total - 1.0) / spread**2, scale / spread

    def d_denominator(within: float, total: float) -> float:
        del total
        return 1.0 - within

    def g_denominator(within: float, total: float) -> float:
        del within
        return total

    def g_value(within: float, total: float) -> float | None:
        return g_st_from_demes(total, within)

    def g_gradient(within: float, total: float) -> tuple[float, float]:
        return -1.0 / total, within / total**2

    return {
        "D": IdentityStatistic(d_value, d_gradient, d_denominator),
        "G_ST": IdentityStatistic(g_value, g_gradient, g_denominator),
    }


class _DerivedFields(TypedDict):
    """The five pooled-heterozygosity measures `report_for_state` merges in."""

    D_m: float
    R_ST: float | None
    G_ST_NEI_LOG: float | None
    G_ST_HEDRICK: float | None
    F_ST: float | None


def derived_fields(h_s: float, h_t: float, deme_count: int) -> _DerivedFields:
    """Return `derived_differentiation` for the pooled heterozygosities."""
    values = derived_differentiation(h_s, h_t, deme_count)
    d_m_value = values["D_m"]
    if d_m_value is None:  # pragma: no cover - D_m is always defined
        raise ValueError("D_m is unexpectedly undefined")
    return {
        "D_m": d_m_value,
        "R_ST": values["R_ST"],
        "G_ST_NEI_LOG": values["G_ST_NEI_LOG"],
        "G_ST_HEDRICK": values["G_ST_HEDRICK"],
        "F_ST": values["F_ST"],
    }


class _NeiAllDemesFields(TypedDict):
    """The eight all-demes Nei fields `report_for_state` merges in."""

    NEI_D_ALL_GEO: float | None
    NEI_D_ALL_GEO_LOCUS_MEAN: float | None
    NEI_D_ALL_ARITH: float | None
    NEI_D_ALL_ARITH_LOCUS_MEAN: float | None
    NEI_I_ALL_GEO: float
    NEI_I_ALL_GEO_LOCUS_MEAN: float
    NEI_I_ALL_ARITH: float
    NEI_I_ALL_ARITH_LOCUS_MEAN: float


def nei_all_demes_fields(state: ModelState) -> _NeiAllDemesFields:
    """Return the all-demes Nei identities and distances for `state`.

    One O(d * alleles) pass per locus (`deme_gene_identities`) feeds all
    eight values (`nei_family_from_identities`). An infinite distance is
    reported as ``None`` because reports are strict JSON; the identity
    beside it is always finite.
    """
    within_by_locus: list[list[float]] = []
    between_by_locus: list[float] = []
    for locus_index in range(state.locus_count):
        table = [
            state.frequency_map(deme_index, locus_index)
            for deme_index in range(state.deme_count)
        ]
        within, between = deme_gene_identities(table)
        within_by_locus.append(within)
        between_by_locus.append(between)
    family = nei_family_from_identities(within_by_locus, between_by_locus)

    def distance(identity_value: float) -> float | None:
        value = nei_distance_from_identity(identity_value)
        return None if math.isinf(value) else value

    geo = family[("geometric", "pooled")]
    geo_mean = family[("geometric", "locus_mean")]
    arith = family[("arithmetic", "pooled")]
    arith_mean = family[("arithmetic", "locus_mean")]
    return {
        "NEI_D_ALL_GEO": distance(geo),
        "NEI_D_ALL_GEO_LOCUS_MEAN": distance(geo_mean),
        "NEI_D_ALL_ARITH": distance(arith),
        "NEI_D_ALL_ARITH_LOCUS_MEAN": distance(arith_mean),
        "NEI_I_ALL_GEO": geo,
        "NEI_I_ALL_GEO_LOCUS_MEAN": geo_mean,
        "NEI_I_ALL_ARITH": arith,
        "NEI_I_ALL_ARITH_LOCUS_MEAN": arith_mean,
    }


ALWAYS_TRACKED_STATISTICS: Final[tuple[str, ...]] = history_keys("always")
"""Derived from `fim.statistics.catalog` (`history == "always"`).

Statistics `_watched_statistic_values` always returns, regardless of
`params.convergence_statistics`/`track_expensive_statistics`.

`fim.statistics.differentiation.statistics_report`'s own `statistics`
parameter already computes these five unconditionally, no matter what is
passed in (each is either the shared `H_S`/`H_T` input every other field
derives from, or an O(1) step once those are known) — before this
constant existed, `_watched_statistic_values`'s own final filtering step
still discarded whichever of these were not actually watched, so a GUI
trajectory panel/live statistics table watching only `D` (this project's
own stated default) never had a real history to show for `G_ST`/`H_S`/
`H_T` even though computing them cost nothing extra. Design doc §6.2
(`20260907-claude-sonnet-5-botanist-gui-redesign.md`) already describes
the trajectory panel as plotting "all six report statistics" — this
constant is what makes five of those six always real, for free, closing
that gap. `H_ST` joined this tuple later than the other four (found
live: a real, reported "most statistics stopped scrubbing" investigation
that turned out to be `track_expensive_statistics` correctly gating
`E_ST`/`K_ST`/`A_CGD`/`Delta`/`MI`, plus this one genuine, separate gap
— `H_ST` fell into neither this tuple nor `_EXPENSIVE_OPT_IN_STATISTICS`,
so it had no per-generation history at all unless directly watched, the
trajectory-history-side twin of the exact watchability gap `FIM-51`
already closed for it once). `E_ST`/`K_ST` are deliberately excluded: each
is a genuine, independent O(total allele entries) pass `statistics_
report` only pays for statistics named in the collection passed to it
(commit `b12679b`, `FIM-24`/`FIM-32`, measured a ~38% reduction skipping
both at a many-alleles configuration) — `SimulationParams.track_expensive_
statistics` is the opt-in a caller sets to pay that cost back
deliberately, in exchange for a real, continuously updated display value
for those two as well (see `_statistics_to_compute`, below).
"""


_EXPENSIVE_OPT_IN_STATISTICS: Final[frozenset[str]] = frozenset(history_keys("opt_in"))


UNBOUNDED_STATISTICS: Final[frozenset[str]] = frozenset(unbounded_keys())
"""Statistics with an open end, judged by a relative precision by default."""


_POOLED_STATISTICS: Final[frozenset[str]] = frozenset(
    {"Gs", "Gd", "D_m", "R_ST", "G_ST_NEI_LOG", "G_ST_HEDRICK", "F_ST"}
)
"""Watchable statistics computed from the pooled `H_S`, `H_T` and deme count.

Cheap: no pass over the allele frequencies, only `derived_fields` and the
`Gs`/`Gd` identities of the two heterozygosities every generation already has.
"""


_NEI_ALL_DEMES_STATISTICS: Final[frozenset[str]] = frozenset(
    key for key in report_keys() if key.startswith(("NEI_D_ALL", "NEI_I_ALL"))
)
"""Watchable all-demes Nei identities and distances.

They need the whole state (one pass over every deme's allele frequencies per
locus), so a vectorized lane rebuilds a `ModelState` each generation it watches
one.
"""


_NON_REPORT_WATCHED: Final[frozenset[str]] = (
    _POOLED_STATISTICS | _NEI_ALL_DEMES_STATISTICS
)
"""Watchable statistics that `statistics_report` itself knows nothing about."""
"""Derived from `fim.statistics.catalog` (`history == "opt_in"`).

Statistics `params.track_expensive_statistics` opts into computing.

All five are genuinely expensive per-generation and watchable —
`_CONVERGENCE_STATISTICS` (`fim.model.params`) includes every one of
them, so a run can also stop *on* one, not merely display it. `E_ST`/
`K_ST` are each an O(total allele entries) pass. `A_CGD`/`Delta`/`MI`
(Caballero-García-Dorado allelic distance, Gregorius's delta, Sherwin
mutual information) are similarly real, generation-scale costs
(`A_CGD`: O(deme pairs); `Delta`: O(deme pairs*alleles); `MI`: pools
every deme's own frequencies, an extra full pass `D`/`G_ST` never
pay) — genuine "bonus" measurements from the differentiation
literature, opt-in the same way `E_ST`/`K_ST` already are, and
watchable the same way too on a real, reported request (an earlier
version of this set's own docstring said they were never legal
`convergence_statistic` choices; that restriction did not survive
contact with an actual user). `D`/`G_ST`/`H_S`/`H_T`/`H_ST` are never
in this set — always computed regardless (`_ALWAYS_TRACKED_
STATISTICS`'s own docstring), since none of them costs anything extra.
"""


def _statistics_to_compute(params: SimulationParams) -> frozenset[str]:
    """Return every statistic name this generation's convergence check must compute.

    Forwarded to `statistics_for_locus`/`_statistics_for_locus_
    vectorized`'s own `statistics` parameter, which controls only whether
    the genuinely expensive fields (`_EXPENSIVE_OPT_IN_STATISTICS`) are
    computed at all this call, or left `math.nan` — `D`/`G_ST`/`H_S`/
    `H_T`/`H_ST` are always computed by `statistics_report` regardless of
    what this returns (`ALWAYS_TRACKED_STATISTICS`'s own docstring), so
    this function only ever needs to decide the expensive fields' own
    fate.

    Always includes `params.convergence_statistics` (a watched `E_ST`/
    `K_ST` must be computed for real regardless of the opt-in below, or
    the run could never detect it converging); additionally includes
    every `_EXPENSIVE_OPT_IN_STATISTICS` name whenever `params.track_
    expensive_statistics` is set, even if none is actually watched — the
    display-only opt-in `SimulationParams.track_expensive_statistics`'s
    own docstring describes.
    """
    statistics = set(params.convergence_statistics) - _NON_REPORT_WATCHED
    if params.track_expensive_statistics:
        statistics |= _EXPENSIVE_OPT_IN_STATISTICS
    return frozenset(statistics)


def extra_tracked_statistics(params: SimulationParams) -> tuple[str, ...]:
    """Return the `ConvergenceMonitor.extra_statistics` for a within-run monitor.

    `build_replica_lane`/`run_one` both construct their own within-run
    `ConvergenceMonitor` with `statistics=params.convergence_statistics`
    (the subset that actually gates stopping) — this returns every other
    name `_watched_statistic_values` can hand that monitor's own
    `record()` this run (`ALWAYS_TRACKED_STATISTICS`, always;
    `_EXPENSIVE_OPT_IN_STATISTICS` too, when `params.track_expensive_
    statistics` opts in), minus whichever of those are already in
    `statistics` (a name cannot appear in both — `ConvergenceMonitor.
    __init__` rejects a repeat). Kept as its own function, not inlined at
    each of those two call sites, so the two can never independently
    drift out of sync with `_watched_statistic_values`'s own actual
    return keys.
    """
    watched = set(params.convergence_statistics)
    universe = set(ALWAYS_TRACKED_STATISTICS)
    if params.track_expensive_statistics:
        universe |= _EXPENSIVE_OPT_IN_STATISTICS
    return tuple(sorted(universe - watched))


def convergence_values(
    state: ModelState,
    params: SimulationParams,
) -> dict[str, float]:
    """Return every currently-defined tracked statistic's value, averaged across loci.

    Called once per generation by `run_one`'s own main loop, to feed
    the convergence monitor whatever it needs to judge "has this
    settled down yet" (see this module's own docstring, above, for what
    convergence means here) — and, since `ALWAYS_TRACKED_STATISTICS`
    below, also to give a GUI a real, continuously updated history for
    `D`/`G_ST`/`H_S`/`H_T`/`H_ST` regardless of what `params.
    convergence_statistics` actually watches (design doc §6.2). Only the
    watched subset (`params.convergence_statistics`) — usually just one
    (`D`, most commonly), but this project also supports watching several
    at once and requiring either all of them, or any one of them, to
    settle before calling the run converged (`SimulationParams.
    convergence_combinator`, design §9) — ever influences the stop
    decision; every other name this function returns is along for the
    ride, never gating anything (`ConvergenceMonitor`'s own
    `extra_statistics`).

    Computes each locus's full differentiation report exactly once and reads
    every tracked statistic from that same cached set of reports, rather than
    recomputing per-locus statistics once per tracked name — the several-
    statistic case costs one extra dictionary lookup per statistic per
    locus this way, not another whole pass over the state.

    A tracked statistic that is undefined this generation (only ``G_ST``
    can be, and only when every tracked locus is currently
    "monomorphic" — has only a single allele left, with no genetic
    variation remaining to measure at all; see `mean_g_st_across_loci`)
    is omitted from the returned mapping rather than represented by a
    substitute value; `ConvergenceMonitor` treats an omitted statistic as
    simply not advancing its own trailing window this generation, which
    is honest about there being no observation to add, rather than
    fabricating one.

    ``D``/``G_ST`` are aggregated across loci per `params.
    locus_aggregation` (`_watched_statistic_values`), the identical
    estimator `report_for_state` uses for its own ``D``/``G_ST`` fields —
    every other tracked statistic (``E_ST``, ``K_ST``, ``H_S``, ``H_T``)
    is unaffected by that choice and stays a plain per-locus mean either
    way (`pooled_g_st_and_d`'s own docstring).
    """
    locus_reports = tuple(
        statistics_for_locus(
            state, params, locus_index, statistics=_statistics_to_compute(params)
        )
        for locus_index in range(state.locus_count)
    )
    values = _watched_statistic_values(
        locus_reports, params, deme_count=state.deme_count
    )
    return _with_watched_extras(values, params, state.deme_count, lambda: state)


def tracked_statistic_values(
    state: ModelState,
    params: SimulationParams,
) -> dict[str, float]:
    """Return one state's tracked statistic values, as a live run records them.

    The public entry point to `convergence_values`, for callers
    rebuilding a convergence history from a persisted trajectory rather
    than watching one being produced (`fim.reanalyze.replicate_
    convergence_history`). Sharing the private function outright, rather
    than reimplementing the same aggregation, is the point: a
    reconstructed history has to be comparable with a live one, and two
    separate implementations of "which statistics, aggregated how"
    would diverge the first time either changed.

    Args:
        state: The population state to summarize.
        params: The run's own validated parameters -- `convergence_
            statistics`/`track_expensive_statistics` decide which names
            beyond `ALWAYS_TRACKED_STATISTICS` are computed at all.

    Returns:
        Every currently-defined tracked statistic's value, keyed by
        name. A statistic that is undefined for this state (`G_ST` when
        every tracked locus is monomorphic, say) is absent rather than
        present as a placeholder, matching what the live monitor
        records for that same generation.
    """
    return convergence_values(state, params)


def convergence_values_vectorized(
    block: VectorBlock,
    params: SimulationParams,
) -> dict[str, float]:
    """`convergence_values`'s array-native counterpart, mostly inside the kernel.

    `VectorizedAdvancer.advance` calls this instead of `_convergence_
    values`, so a whole generation's convergence check never has to
    rebuild a `ModelState`.

    `D`, `G_ST`, `H_S`, `H_T` and `H_ST`, the statistics every run tracks
    and the ones the convergence monitor usually watches, are computed for
    every locus by one compiled call (`VectorBlock.locus_statistics`),
    which reproduces `statistics_report` bit for bit; the cross-locus
    aggregation then follows `_watched_statistic_values` exactly. The
    expensive opt-in statistics (`E_ST`, `K_ST`, `A_CGD`, `Delta`, `MI`)
    have no kernel form, so a run that watches or tracks any of them
    builds each locus's frequency maps and uses `statistics_report`. So
    does the one generation in which the kernel flags a value that
    `statistics_report` would have refused (outside `[0, 1]`), so the
    Python implementation raises its own, specific error.

    Args:
        block: The replicate's current table.
        params: The run's parameters (`convergence_statistics`,
            `track_expensive_statistics`, `locus_aggregation`).

    Returns:
        The same mapping `convergence_values` returns for the same state.
    """
    statistics = _statistics_to_compute(params)
    if not statistics - set(ALWAYS_TRACKED_STATISTICS):
        table, status = block.locus_statistics()
        if not status.any():
            return _with_watched_extras(
                _watched_values_from_table(table, params, block.deme_count),
                params,
                block.deme_count,
                block.to_model_state,
            )
    reports = tuple(
        _statistics_for_locus_vectorized(
            block, params, locus_index, statistics=statistics
        )
        for locus_index in range(len(block.loci))
    )
    return _with_watched_extras(
        _watched_statistic_values(reports, params, deme_count=block.deme_count),
        params,
        block.deme_count,
        block.to_model_state,
    )


def _with_watched_extras(
    values: dict[str, float],
    params: SimulationParams,
    deme_count: int,
    state: Callable[[], ModelState],
) -> dict[str, float]:
    """Add the watched statistics that are not per-locus report fields.

    `Gs`, `Gd`, `D_m`, `R_ST`, `G_ST_NEI_LOG`, `G_ST_HEDRICK` and `F_ST` come
    from the pooled `H_S` and `H_T` already in `values`; the all-demes Nei
    identities and distances need the state, which `state` supplies only when
    one is watched. A value that is undefined (a Nei distance between demes
    that share no allele, or an `R_ST` with no variation) is left out, as an
    undefined `G_ST` always has been, and the monitor counts the generation.

    Args:
        values: The mapping `_watched_statistic_values` (or its table form)
            returned.
        params: Supplies the watched statistics.
        deme_count: Number of demes.
        state: Builds the generation's `ModelState` on demand.

    Returns:
        `values` with the watched extras added (the same dictionary).
    """
    watched = set(params.convergence_statistics)
    pooled = watched & _POOLED_STATISTICS
    if pooled:
        within, total = values["H_S"], values["H_T"]
        candidates: dict[str, float | None] = {
            "Gs": 1.0 - within,
            "Gd": gd_from_within_and_total(within, total, deme_count),
            **cast(
                "Mapping[str, float | None]",
                derived_fields(within, total, deme_count),
            ),
        }
        for name in pooled:
            value = candidates[name]
            if value is not None:
                values[name] = value
    nei = watched & _NEI_ALL_DEMES_STATISTICS
    if nei:
        fields = cast("Mapping[str, float | None]", nei_all_demes_fields(state()))
        for name in nei:
            value = fields[name]
            if value is not None:
                values[name] = value
    return values


def _watched_values_from_table(
    table: np.ndarray,
    params: SimulationParams,
    deme_count: int,
) -> dict[str, float]:
    """Aggregate per-locus kernel statistics as `_watched_statistic_values` does.

    The same arithmetic in the same order as `_watched_statistic_values`
    for the always-tracked statistics, starting from the kernel's table
    instead of one `DifferentiationReport` per locus (the dictionaries
    would cost more than the kernel at hundreds of loci). A test compares
    the two on seeded states, bit for bit, under both
    `locus_aggregation` choices.

    Args:
        table: `(loci, 5)` columns `H_S`, `H_T`, `H_ST`, `G_ST`, `D`;
            `G_ST` is `nan` where undefined.
        params: Supplies `locus_aggregation`.
        deme_count: Number of demes.

    Returns:
        `D`, `H_S`, `H_T`, `G_ST` (omitted when undefined) and `H_ST`.
    """
    h_s = table[:, 0].tolist()
    h_t = table[:, 1].tolist()
    mean_h_s = mean(h_s)
    mean_h_t = mean(h_t)
    g_st: float | None
    if params.locus_aggregation == "mean_of_ratios":
        defined = [value for value in table[:, 3].tolist() if not math.isnan(value)]
        g_st = mean(defined) if defined else None
        d = mean(table[:, 4].tolist())
    else:
        g_st = g_st_from_demes(mean_h_t, mean_h_s)
        d = jost_d_from_within_and_total(deme_count, mean_h_s, mean_h_t)
    values: dict[str, float] = {"D": d, "H_S": mean_h_s, "H_T": mean_h_t}
    if g_st is not None:
        values["G_ST"] = g_st
    values["H_ST"] = mean(table[:, 2].tolist())
    return values


def _watched_statistic_values(
    locus_reports: Sequence[DifferentiationReport],
    params: SimulationParams,
    *,
    deme_count: int,
) -> dict[str, float]:
    """`convergence_values`/`convergence_values_vectorized`'s own shared core.

    Before this function existed, both callers aggregated every watched
    statistic — including `D`/`G_ST` — via `_mean_statistic_across_loci`
    alone, a plain per-locus mean (`"mean_of_ratios"`) regardless of
    `params.locus_aggregation`. `report_for_state`'s own final `D`/`G_ST`
    fields, by contrast, already respect `locus_aggregation` via
    `pooled_g_st_and_d` — under the default `"ratio_of_means"`, a
    materially different estimator (this project's own `params.py`
    docstring measures the two differing by 0.25% vs. 1.88% from the
    exact gene-identity recursion at reference scale). A multi-locus run
    at the default aggregation could therefore stop on a trailing-window
    `D` the final report never actually shows — a real, reachable
    inconsistency between why a run stopped and what it reports (this
    project's own multi-model engine review, 2026-09-04, `FIM-10`/
    finding C-02/finding P1-2, independently found by three of four
    reviewers, one of them naming it the only finding among all four
    reviews that changes a scientific result).

    `D`/`G_ST` now go through the identical `pooled_g_st_and_d` call
    `report_for_state` itself makes, always (`ALWAYS_TRACKED_STATISTICS`
    — no longer gated on either being watched, since both cost nothing
    extra to compute or aggregate regardless); `H_S`/`H_T` are likewise
    always returned, being `pooled_g_st_and_d`'s own two inputs, already
    computed either way. `H_ST` is also always returned now (`_ALWAYS_
    TRACKED_STATISTICS`'s own docstring has the "found live" account of
    when and why it joined the other four) but, unlike them, is not
    `locus_aggregation`-sensitive — a plain per-locus mean via `_mean_
    statistic_across_loci`, unconditionally now rather than only when
    directly watched. `E_ST`/`K_ST` (and any other less common watched
    statistic) are the same plain-per-locus-mean shape, included only
    when actually watched or (`E_ST`/`K_ST` only) opted into via
    `params.track_expensive_statistics` (`_statistics_to_compute`'s own
    docstring — every other name here is either always present or never
    computed at all this generation, so there is nothing to look up for
    it).
    """
    mean_h_s = mean(tuple(report["H_S"] for report in locus_reports))
    mean_h_t = mean(tuple(report["H_T"] for report in locus_reports))
    g_st, d = pooled_g_st_and_d(
        locus_reports,
        mean_h_s=mean_h_s,
        mean_h_t=mean_h_t,
        deme_count=deme_count,
        locus_aggregation=params.locus_aggregation,
    )
    values: dict[str, float] = {"D": d, "H_S": mean_h_s, "H_T": mean_h_t}
    if g_st is not None:
        values["G_ST"] = g_st
    # `H_ST` joined `ALWAYS_TRACKED_STATISTICS` alongside `D`/`G_ST`/
    # `H_S`/`H_T` (that constant's own docstring has the full "found
    # live" account) but, unlike those four, is not `locus_aggregation`-
    # sensitive (this function's own docstring) — a plain per-locus mean
    # via `_mean_statistic_across_loci`, the identical computation the
    # `extra` loop below already used whenever `H_ST` happened to be
    # watched, just no longer gated on that.
    h_st = _mean_statistic_across_loci(locus_reports, "H_ST")
    if h_st is not None:
        values["H_ST"] = h_st
    extra = _statistics_to_compute(params) - set(ALWAYS_TRACKED_STATISTICS)
    for statistic in extra:
        value = _mean_statistic_across_loci(locus_reports, statistic)
        if value is not None:
            values[statistic] = value
    return values


def _mean_statistic_across_loci(
    locus_reports: Sequence[DifferentiationReport],
    statistic: str,
) -> float | None:
    """Return one statistic's per-locus reports averaged into a single value.

    Only ``G_ST`` can be undefined at a locus — a monomorphic locus's
    total heterozygosity is zero, leaving nothing to differentiate; every
    other field in `DifferentiationReport` is always defined for valid
    input. ``G_ST`` delegates to `mean_g_st_across_loci`, which drops any
    undefined locus from the average and returns ``None`` only when every
    locus is undefined — exactly the rule `report_for_state` uses for the
    final report, so the within-run convergence watcher's notion of
    "G_ST this generation" never disagrees with what the report will say.
    """
    if statistic == "G_ST":
        return mean_g_st_across_loci(locus_reports)
    values: list[float] = []
    for report in locus_reports:
        value = _report_statistic(report, statistic)
        if value is None:
            # Unreachable given DifferentiationReport's own contract
            # (every field but G_ST is always defined); guarded rather
            # than asserted so a future statistic that can legitimately
            # go undefined is forced to update this function, not
            # silently mis-averaged.
            raise ValueError(f"{statistic} is unexpectedly undefined")
        values.append(value)
    return mean(tuple(values))


def pair_statistic_values(
    state: ModelState, first: int, second: int
) -> dict[str, float | None]:
    """Return every pair-scope statistic for two demes of `state`.

    The statistics the GUI shows for the deme pair chosen for the scatter
    plot: the four identities `NEI_I_PAIR_*`, the four distances
    `NEI_D_PAIR_*` and pairwise `F_ST_PAIR` (`fim.statistics.catalog`).
    One O(alleles) pass per locus. A deme compared with itself gives
    identity 1, distance 0 and pairwise F_ST 0 (undefined, `None`, if
    that deme is fixed).

    Args:
        state: The population state.
        first: Zero-based index of one deme.
        second: Zero-based index of the other (may equal `first`).

    Returns:
        ``{key: value}``; a distance is ``None`` where it is infinite (no
        allele shared), matching how reports store it.

    Raises:
        ValueError: For a deme index outside the state.
    """
    for index in (first, second):
        if not 0 <= index < state.deme_count:
            raise ValueError(f"deme {index + 1} is outside 1..{state.deme_count}")
    within_by_locus: list[list[float]] = []
    between_by_locus: list[float] = []
    for locus_index in range(state.locus_count):
        deme_x = state.frequency_map(first, locus_index)
        deme_y = state.frequency_map(second, locus_index)
        within_by_locus.append(
            [
                math.fsum(value * value for value in deme_x.values()),
                math.fsum(value * value for value in deme_y.values()),
            ]
        )
        between_by_locus.append(
            math.fsum(
                value * deme_y[allele]
                for allele, value in deme_x.items()
                if allele in deme_y
            )
        )
    family = nei_family_from_identities(within_by_locus, between_by_locus)
    values: dict[str, float | None] = {}
    # Pairwise F_ST (Goudet & Weir 2023 Eq. 10), loci pooled like Nei's
    # rule: ((J_X + J_Y) / 2 - J_XY) / (1 - J_XY); undefined when both
    # demes are fixed for the same allele (J_XY = 1).
    locus_count = len(between_by_locus)
    mean_within = math.fsum(sum(pair) / 2.0 for pair in within_by_locus) / locus_count
    mean_between = math.fsum(between_by_locus) / locus_count
    values["F_ST_PAIR"] = (
        None
        if mean_between >= 1.0
        else max(0.0, (mean_within - mean_between) / (1.0 - mean_between))
    )
    for (denominator, locus_rule), raw in family.items():
        # Both pair forms are bounded by 1 (Cauchy-Schwarz, AM-GM); the
        # clamp removes only rounding.
        identity_value = min(1.0, max(0.0, raw))
        distance = nei_distance_from_identity(identity_value)
        values[nei_key("identity", "pair", denominator, locus_rule)] = identity_value
        values[nei_key("distance", "pair", denominator, locus_rule)] = (
            None if math.isinf(distance) else distance
        )
    return values


def history_free_statistic_values(state: ModelState) -> dict[str, float | None]:
    """Return the global statistics that have no per-generation history.

    `Gs`, `Gd`, the five pooled-heterozygosity measures (`D_m`, `R_ST`,
    both `G'_ST`, coancestry `F_ST`) and the eight all-demes Nei fields:
    what a scrubbed frame
    of a completed run needs to show those rows at that frame's
    generation. Computed directly and cheaply (O(d * alleles) per locus),
    not through `report_for_state`, which would also pay for every
    expensive statistic. `Gs` and `Gd` equal `report_for_state`'s.

    Args:
        state: The population state.

    Returns:
        ``{key: value}`` keyed as in `FinalReport`.
    """
    within_sum = 0.0
    between_sum = 0.0
    for locus_index in range(state.locus_count):
        table = [
            state.frequency_map(deme_index, locus_index)
            for deme_index in range(state.deme_count)
        ]
        within, between = deme_gene_identities(table)
        within_sum += math.fsum(within) / len(within)
        between_sum += between
    gs_value = within_sum / state.locus_count
    gd_value = between_sum / state.locus_count
    values: dict[str, float | None] = {"Gs": gs_value, "Gd": gd_value}
    # The pooled heterozygosities, from the identities: `H_S = 1 - Gs`,
    # and `1 - H_T` is the pooled identity `(Gs + (d - 1) Gd) / d`.
    deme_count = state.deme_count
    h_s_value = 1.0 - gs_value
    h_t_value = 1.0 - (gs_value + (deme_count - 1) * gd_value) / deme_count
    values.update(derived_differentiation(h_s_value, h_t_value, deme_count))
    values.update(cast("Mapping[str, float | None]", nei_all_demes_fields(state)))
    return values


def mean(values: Sequence[float]) -> float:
    """Return an accurate mean of a nonempty float sequence.

    Uses `math.fsum` rather than Python's plain `sum`, then divides:
    ordinary floating-point addition accumulates small rounding errors
    as more numbers are added together, and `fsum` specifically avoids
    that by tracking the arithmetic more carefully internally — for the
    typically small handful of values averaged in this module (loci, or
    replicates), the difference is negligible either way, but there is
    no real cost to always using the more careful version.
    """
    if not values:
        raise ValueError("cannot average no values")
    return math.fsum(values) / len(values)


def mean_g_st_across_loci(
    locus_reports: Sequence[DifferentiationReport],
) -> float | None:
    """Return ``G_ST`` averaged across loci, dropping any locus where it is undefined.

    ``g_st`` is undefined at a locus whose total heterozygosity (``H_T``)
    is zero — a fully monomorphic locus has nothing to differentiate. Such
    a locus contributes nothing to the average, rather than a fabricated
    ``0.0`` (which would understate real differentiation at the other,
    genuinely polymorphic, loci) or voiding the whole multi-locus average
    over a single monomorphic locus among several. Returns ``None`` only
    when *every* locus is undefined, since there is then no defined value
    left to average — the honest reading of "this run/replicate has no
    G_ST", matching the rule `fim.engine.replicate_summary` already
    applies when building the published cross-replicate sample.
    """
    defined = [report["G_ST"] for report in locus_reports if report["G_ST"] is not None]
    if not defined:
        return None
    return mean(tuple(defined))


def pooled_g_st_and_d(
    locus_reports: Sequence[DifferentiationReport],
    *,
    mean_h_s: float,
    mean_h_t: float,
    deme_count: int,
    locus_aggregation: LocusAggregation,
) -> tuple[float | None, float]:
    """Return ``(G_ST, D)``, aggregated across loci per `locus_aggregation`.

    With `"ratio_of_means"`, compute each statistic from pooled `H_S`
    and `H_T` values supplied by the caller. With `"mean_of_ratios"`,
    average the per-locus `G_ST` and `D` values. `E_ST`, `K_ST`, `H_S`,
    `H_T`, and `H_ST` are averaged per locus regardless of this option.
    """
    if locus_aggregation == "mean_of_ratios":
        return (
            mean_g_st_across_loci(locus_reports),
            mean(tuple(report["D"] for report in locus_reports)),
        )
    return (
        g_st_from_demes(mean_h_t, mean_h_s),
        jost_d_from_within_and_total(deme_count, mean_h_s, mean_h_t),
    )


def statistics_for_locus(
    state: ModelState,
    params: SimulationParams,
    locus_index: int,
    *,
    statistics: Collection[str] | None = None,
) -> DifferentiationReport:
    """Compute the requested differentiation statistics for one locus.

    Builds a normalized allele-frequency table from ``state`` and passes
    it to ``fim.statistics.differentiation.statistics_report``. When
    ``params.deme_weighting`` is ``"size"``, population-size weights are
    supplied for ``E_ST``, ``Delta``, and ``MI`` only. Other statistics,
    including ``H_S``, ``H_T``, ``H_ST``, ``G_ST``, ``D``, ``K_ST``,
    ``A_CGD``, ``Gs``, and ``Gd``, use equal deme treatment.

    Args:
        state: Population state to summarize.
        params: Validated model parameters, including deme weighting.
        locus_index: Zero-based locus position in the state.
        statistics: Optional names of statistics to calculate.

    Returns:
        A ``DifferentiationReport`` for the selected locus.
    """
    table: list[Mapping[Any, float]] = [
        {
            int(allele_id): frequency
            for allele_id, frequency in state.frequency_map(
                deme_index,
                locus_index,
            ).items()
        }
        for deme_index in range(state.deme_count)
    ]
    weights: Sequence[float] | None = (
        params.population_sizes if params.deme_weighting == "size" else None
    )
    return statistics_report(table, weights, validate=False, statistics=statistics)


def _statistics_for_locus_vectorized(
    block: VectorBlock,
    params: SimulationParams,
    locus_index: int,
    *,
    statistics: Collection[str] | None = None,
) -> DifferentiationReport:
    """`statistics_for_locus` for one locus of a `VectorBlock`.

    Builds the per-deme frequency maps `statistics_report` takes straight
    from the block's arrays (`VectorBlock.frequency_maps`) and hands them
    to it; computes nothing statistical itself. `statistics` is forwarded
    as in `statistics_for_locus`.

    Args:
        block: The replicate's current table.
        params: Supplies `deme_weighting` and the population sizes.
        locus_index: Zero-based locus position.
        statistics: Names forwarded to `statistics_report`.
    """
    table: list[Mapping[Any, float]] = list(block.frequency_maps(locus_index))
    weights: Sequence[float] | None = (
        params.population_sizes if params.deme_weighting == "size" else None
    )
    return statistics_report(table, weights, validate=False, statistics=statistics)


def _report_statistic(
    report: DifferentiationReport,
    statistic: str,
) -> float | None:
    """Read one validated convergence-statistic field.

    The `convergence_values`/`_mean_statistic_across_loci` counterpart
    to `final_report_statistic` above, reading from one locus's own
    live `DifferentiationReport` (computed this generation) rather than
    a whole run's already-finished `FinalReport` — otherwise the exact
    same "look up one named statistic safely" job, for the same reason:
    a plain `report[statistic]` would raise Python's own generic
    `KeyError` for an unrecognized name, rather than the specific,
    named-statistic error this function raises instead. `H_ST` was
    missing from this lookup until this project's own multi-model engine
    review, 2026-09-04 (`FIM-51`) — `fim.model.params.
    _CONVERGENCE_STATISTICS` is this function's own config-time
    counterpart, and this field set is kept a 1:1 mirror of it: all ten
    named statistics are watchable, so every name reaching this function
    as a watched `convergence_statistic` is also one of this dict's own
    keys. `A_CGD`/`Delta`/`MI` reach this function either way, watched
    or only opted into for display via `track_expensive_statistics`
    (`extra_tracked_statistics`'s own opt-in extras) — the same path
    `E_ST`/`K_ST` already take when neither is actually watched.
    """
    fields: Mapping[str, float | None] = {
        "D": report["D"],
        "G_ST": report["G_ST"],
        "E_ST": report["E_ST"],
        "K_ST": report["K_ST"],
        "H_S": report["H_S"],
        "H_T": report["H_T"],
        "H_ST": report["H_ST"],
        "A_CGD": report["A_CGD"],
        "Delta": report["Delta"],
        "MI": report["MI"],
    }
    if statistic not in fields:
        raise ValueError(f"unsupported convergence statistic: {statistic}")
    return fields[statistic]
