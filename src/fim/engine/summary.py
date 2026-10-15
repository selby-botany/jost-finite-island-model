"""Across-replicate summaries and pooled histories."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import numpy as np

from fim.config.numerics import MINIMUM_REPLICATE_COUNT
from fim.convergence.window_statistics import IdentityStatistic
from fim.engine.records import FinalReport, RunResult
from fim.engine.report import replicate_statistic
from fim.engine.tracking import identity_statistics_for, mean
from fim.statistics.catalog import report_keys
from fim.statistics.catalog import spec as catalog_spec
from fim.statistics.differentiation import (
    g_st_from_demes,
    jost_d_from_within_and_total,
)
from fim.statistics.interval import ConfidenceInterval, confidence_interval


def reports_summary(
    reports: Sequence[FinalReport],
    *,
    confidence: float = 0.95,
    deme_count: int | None = None,
) -> dict[str, ConfidenceInterval]:
    """Return each named statistic's across-report confidence interval.

    A **confidence interval** is a range around a sample's own average
    that expresses how much uncertainty is left after averaging only a
    finite number of independent draws — the same idea as reporting a
    poll result as "52% ± 3%" rather than a single bare number, where
    the "± 3%" is exactly this kind of interval. Here, each element of
    `reports` is one independent replicate's own final value for a given
    statistic (see this module's own docstring, above, for what a
    "replicate" is and why running several of them matters); this
    function averages each statistic across every replicate that defines
    it and reports how much that average could plausibly still be off,
    given only this many replicates. `confidence=0.95` (the default)
    means: if this same batch of replicates were run over and over, the
    true underlying average would fall inside the reported interval
    about 95% of the time — the conventional choice in most sciences,
    not a value with any special significance to this project.

    This is the shared math behind `replicate_summary` (a completed
    batch's own final reports), extracted so the GUI's live batch
    progress panel (`fim.gui.app._push_batch_progress`) can summarize
    each currently-reporting replicate's *live*, in-progress report the
    same way — the math does not care whether a report is final or
    mid-run.

    Unlike `replicate_summary`, this never raises for fewer than two
    reports: a live tick's own reporting-replicate count is expected to
    start below two and grow as the batch proceeds, not something to
    treat as invalid input. Zero or one report simply summarizes to
    nothing yet (an empty dict), the same "omitted, not blank" contract
    `replicate_summary`'s own per-statistic dropping already establishes
    for a monomorphic ``G_ST``, extended here to the "not enough reports
    at all" case too.

    Args:
        reports: Zero or more independently seeded reports.
        confidence: Two-tailed confidence level; see
            `fim.statistics.interval.confidence_interval`.
        deme_count: The number of demes, which `D`'s value of means needs. When
            given and any replicate selected the value of means for `D` or
            `G_ST` (`convergence_estimate`), that statistic's interval pools the
            replicates' window means of `H_S` and `H_T` first
            (`_pooled_value_of_means`); `None` leaves every statistic as a
            plain mean of replicate values.

    Returns:
        One `ConfidenceInterval` per statistic name in `FinalReport`
        (``D``, ``G_ST``, ``E_ST``, ``K_ST``, ``H_S``, ``H_T``, ``H_ST``,
        ``A_CGD``, ``Delta``, ``MI``, ``Gs``, ``Gd``) with at least two
        defined values across
        `reports`; a statistic short of that (including every statistic,
        given fewer than two reports overall) is omitted entirely.
        `Gs`/`Gd` are both linear in `H_S`/`H_T` (`fim.statistics.
        differentiation.gs`/`gd`'s own docstrings), so — unlike `D`/
        `G_ST`, whose own across-replicate treatment this project's own
        Ryman & Leimar remediation, `R3` part 3, still owes a dedicated
        derivation — the ordinary Student's-t interval on the sample mean
        this function already computes for every other field is already
        the mathematically correct interval for them too, not a
        placeholder standing in for one.
    """
    summary: dict[str, ConfidenceInterval] = {}
    # Every global statistic in `fim.statistics.catalog`, so a statistic
    # added there is summarized here with no further edit.
    for statistic in report_keys():
        raw = [replicate_statistic(report, statistic) for report in reports]
        if None in raw and _is_nei_distance(statistic):
            # `None` for a Nei distance means infinite, not undefined: a
            # mean over the finite replicates only would understate it,
            # and a mean including infinity has no finite value. Omitted;
            # its identity (always finite) is still summarized.
            continue
        values = [value for value in raw if value is not None]
        if len(values) < MINIMUM_REPLICATE_COUNT:
            continue
        summary[statistic] = confidence_interval(values, confidence=confidence)
    if deme_count is not None:
        identity = identity_statistics_for(deme_count)
        for statistic, function in identity.items():
            pooled = _pooled_value_of_means(reports, statistic, function, confidence)
            if pooled is not None:
                summary[statistic] = pooled
    return summary


def _pooled_value_of_means(
    reports: Sequence[FinalReport],
    statistic: str,
    function: IdentityStatistic,
    confidence: float,
) -> ConfidenceInterval | None:
    """Pool the replicates' identities, then take the statistic (design 6.11).

    Averaging the statistic inside each replicate and then across replicates
    gives a number whose target depends on the window length; the value of
    the means does not, so the replicates' window means of `H_S` and `H_T` are
    averaged first, `f` is applied once, and the interval comes from the
    delta method over replicates: the spread of
    `f_S * H_S,r + f_T * H_T,r`, the linearized replicate values.

    Args:
        reports: The batch's reports.
        statistic: `"D"` or `"G_ST"`.
        function: Its `f(H_S, H_T)` with gradient.
        confidence: Two-tailed confidence level.

    Returns:
        The interval, or `None` when no replicate selected the value of
        means for `statistic`, fewer than two replicates have window means of
        both heterozygosities, or `f` is undefined at the pooled means.
    """
    selected = any(
        report["window_statistics"].get(statistic, {}).get("selected_form")
        == "value_of_means"
        for report in reports
    )
    if not selected:
        return None
    pairs = [
        (windows["H_S"]["mean"], windows["H_T"]["mean"])
        for windows in (report["window_statistics"] for report in reports)
        if "H_S" in windows and "H_T" in windows
    ]
    if len(pairs) < MINIMUM_REPLICATE_COUNT:
        return None
    mean_s = math.fsum(pair[0] for pair in pairs) / len(pairs)
    mean_t = math.fsum(pair[1] for pair in pairs) / len(pairs)
    estimate = function.function(mean_s, mean_t)
    if estimate is None:
        return None
    slope_s, slope_t = function.gradient(mean_s, mean_t)
    linear = confidence_interval(
        [slope_s * within + slope_t * total for within, total in pairs],
        confidence=confidence,
    )
    return {
        **linear,
        "mean": estimate,
        "low": estimate - linear["half_width"],
        "high": estimate + linear["half_width"],
    }


def _is_nei_distance(statistic: str) -> bool:
    """Whether `statistic` is a Nei distance, whose `None` means infinite."""
    entry = catalog_spec(statistic)
    return entry.nei is not None and entry.nei[0] == "distance"


def replicate_summary(
    results: Sequence[RunResult],
    *,
    confidence: float = 0.95,
) -> dict[str, ConfidenceInterval]:
    """Return each reported statistic's across-replicate confidence interval.

    See `reports_summary`'s own docstring, just above, for what a
    confidence interval is and why it matters here; this function is the
    version of that same idea meant for a *completed* batch's own final
    results, rather than a batch still in progress.

    Each replicate is an independent draw of its own final ``D``, ``G_ST``,
    and so on; this is a closed-form Student's-t interval on the sample
    mean of those draws (`fim.statistics.interval.confidence_interval`) —
    a standard, textbook statistical method for exactly this situation
    (a small number of independent measurements, whose own true
    variability is not known in advance and must be estimated from the
    measurements themselves), computed directly from a formula rather
    than by any kind of simulation or repeated resampling — the
    replicates are already independent by construction (each one used
    its own distinct random seed; see this module's own docstring,
    above), so nothing further is needed to treat them as a valid
    statistical sample. A thin wrapper over `reports_summary`'s own
    shared math, adding only
    the "fewer than two results at all is invalid input" check a
    completed batch's own results warrant (unlike a live, still-growing
    tick's reporting-replicate count — see that function's own
    docstring for why it does not raise this same case itself).

    Args:
        results: Two or more independently seeded replicate results, as
            returned by `fim` when `SimulationParams.n_replicates` is
            greater than one.
        confidence: Two-tailed confidence level; see
            `fim.statistics.interval.confidence_interval`.

    Returns:
        One `ConfidenceInterval` per statistic name in `FinalReport`
        (``D``, ``G_ST``, ``E_ST``, ``K_ST``, ``H_S``, ``H_T``, ``H_ST``,
        ``Gs``, ``Gd`` — the same nine `reports_summary` itself lists,
        restated here rather than abbreviated, since this list had
        silently kept naming only the first seven after ``Gs``/``Gd``
        were added).
        ``G_ST`` is undefined for a replicate whose locus is monomorphic
        across every deme (``H_T == 0``); such replicates are dropped
        from ``G_ST``'s own sample rather than papered over with a
        substitute value, so its `ConfidenceInterval.sample_count` can be
        smaller than the other statistics' — and a statistic left with
        fewer than two defined replicates is omitted entirely rather
        than raising, since a single point has no interval.

    Raises:
        ValueError: If fewer than two results are supplied.
    """
    if len(results) < MINIMUM_REPLICATE_COUNT:
        raise ValueError("replicate_summary requires at least two results")
    return reports_summary(
        [result.report for result in results],
        confidence=confidence,
        deme_count=len(results[0].params.population_sizes),
    )


def pooled_convergence_histories(
    results: Sequence[RunResult],
    *,
    confidence: float = 0.95,
) -> dict[str, tuple[dict[str, float], ...]]:
    """Return each statistic's own across-replicate history, generation by generation.

    A batch's own counterpart to `replicate_summary` (batch trajectory
    panel design `20260912-claude-sonnet-5-batch-trajectory-panel-
    design.md`, `selby/restricted`, commit 2): that function pools
    every replicate's own single *final* value into one confidence
    interval; this pools every replicate's own *entire* recorded
    history (`RunResult.convergence_generations`/`convergence_
    histories`, already computed as a byproduct of each replicate's own
    `ConvergenceMonitor`, never before pooled across replicates) into
    one confidence interval *per generation*, reusing the identical
    `confidence_interval` math `reports_summary` already established.

    Replicates stop at different generations by construction (an
    adaptive early stop, or simply different random
    walks reaching their own criterion at different times). A first
    version of this function counted, at each generation, only the
    replicates whose own history actually reached that far — reported
    live as a real defect, not the intended "honest, shrinking sample"
    the design doc's own first draft described: a replicate stopping is
    usually *because it converged*, not a random dropout, so the
    replicates still running at a later generation are systematically
    the stragglers, not a representative subset of the original cohort
    — confirmed live against a real batch, where 14 of 20 replicates
    converged together at generation 7 and the very next generation's
    own interval, computed from only the 6 remaining stragglers, was
    several times wider than generation 7's own, for no reason related
    to the population's actual behavior. This function instead holds
    each replicate's own final value constant for every later
    generation too, once it stops — every one of `results` contributes
    at every generation from 0 through the slowest replicate's own
    final generation, so the interval's own width reflects genuine
    cross-replicate spread throughout, not how many stragglers happen
    to remain.

    Each statistic's own returned sequence carries its own `generation`
    per point rather than sharing one external generation list the way
    `RunResult.convergence_generations` does for a single replicate:
    a statistic can go undefined for some *interior* stretch of a
    replicate's own history (`G_ST`, whenever every tracked locus is
    currently monomorphic — `convergence_values`'s own docstring), not
    only trail off at the end the way a stopped replicate does: without
    knowing *which* generations were skipped, this function cannot
    safely reconstruct a generation-aligned history for that one
    replicate's own contribution to that one statistic at all, so a
    replicate whose own `convergence_histories[name]` is shorter than
    its own `convergence_generations` is dropped from `name`'s own pool
    entirely (never guessed at, and never allowed to corrupt every
    other replicate's own alignment) — `ConvergenceMonitor` does not
    currently expose a per-statistic generation list that would let a
    future version place these correctly instead of dropping them.

    Args:
        results: Two or more independently seeded replicate results —
            the same input `replicate_summary` takes, from a completed
            batch's own final results.
        confidence: Two-tailed confidence level; see
            `fim.statistics.interval.confidence_interval`.

    Returns:
        One entry per statistic name that has at least one generation
        with two or more defined replicates, each a tuple of `{"generation",
        "mean", "low", "high", "sample_count"}` dicts in ascending
        generation order. A statistic with no such generation at all
        (every replicate dropped it, or fewer than two replicates ever
        recorded it without an interior gap) is omitted from the
        returned mapping entirely, matching `reports_summary`'s own
        "short of two defined values, omitted" contract, applied here
        per generation rather than once.

    Raises:
        ValueError: If fewer than two results are supplied — the same
            "a single replicate has no interval to compute" guard
            `replicate_summary` already applies to its own single-value
            case.
    """
    return pooled_convergence_histories_from_pairs(
        [
            (result.convergence_generations, result.convergence_histories)
            for result in results
        ],
        confidence=confidence,
    )


def pooled_convergence_histories_from_pairs(
    histories: Sequence[tuple[Sequence[int], Mapping[str, Sequence[float]]]],
    *,
    confidence: float = 0.95,
) -> dict[str, tuple[dict[str, float], ...]]:
    """`pooled_convergence_histories`'s own core, over bare history pairs.

    Identical semantics -- see that function's own docstring for every
    rule this applies, none of which differ here. Split out because a
    reopened batch has no `RunResult` objects to hand: its replicates'
    histories are rebuilt from their persisted trajectories
    (`fim.reanalyze.replicate_convergence_history`), which produces
    exactly this pair and nothing else a `RunResult` would carry.

    Args:
        histories: One `(generations, {name: values})` pair per
            replicate, each pair shaped exactly like `RunResult.
            convergence_generations`/`convergence_histories`.
        confidence: Two-tailed confidence level; see
            `fim.statistics.interval.confidence_interval`.

    Returns:
        The identical shape `pooled_convergence_histories` returns.

    Raises:
        ValueError: If fewer than two replicates are supplied.
    """
    if len(histories) < MINIMUM_REPLICATE_COUNT:
        raise ValueError("pooled_convergence_histories requires at least two results")
    statistic_names = sorted(
        {name for _, replicate_histories in histories for name in replicate_histories}
    )
    # Built once per replicate, not re-scanned per generation: an O(1)
    # generation -> value lookup per statistic, plus that same
    # replicate's own last (highest-generation) recorded value for
    # `name` -- the value later generations carry forward once this
    # replicate's own real history for `name` has ended. A replicate
    # whose own `values` is shorter than `convergence_generations` (an
    # interior gap, not just stopping early -- see this function's own
    # docstring) contributes no lookup entry for `name` at all, the
    # "drop rather than guess" choice that same docstring explains.
    per_replicate_lookup: list[dict[str, dict[int, float]]] = []
    per_replicate_last_value: list[dict[str, float]] = []
    for generations, replicate_histories in histories:
        lookup: dict[str, dict[int, float]] = {}
        last_value: dict[str, float] = {}
        for name, values in replicate_histories.items():
            if len(values) != len(generations):
                continue
            by_generation = dict(zip(generations, values, strict=True))
            lookup[name] = by_generation
            last_value[name] = by_generation[max(by_generation)]
        per_replicate_lookup.append(lookup)
        per_replicate_last_value.append(last_value)
    max_generation = max(
        generation for generations, _ in histories for generation in generations
    )
    pooled: dict[str, list[dict[str, float]]] = {name: [] for name in statistic_names}
    for generation in range(max_generation + 1):
        for name in statistic_names:
            pooled_values = [
                lookup[name].get(generation, per_replicate_last_value[index][name])
                for index, lookup in enumerate(per_replicate_lookup)
                if name in lookup
            ]
            if len(pooled_values) < MINIMUM_REPLICATE_COUNT:
                continue
            interval = confidence_interval(pooled_values, confidence=confidence)
            pooled[name].append(
                {
                    "generation": generation,
                    "mean": interval["mean"],
                    "low": interval["low"],
                    "high": interval["high"],
                    "sample_count": interval["sample_count"],
                }
            )
    return {name: tuple(points) for name, points in pooled.items() if points}


def _grand_ratio_of_means(
    reports: Sequence[FinalReport], deme_count: int
) -> tuple[float | None, float]:
    """Return `(G_ST, D)` pooled across every report's own `Gs`/`Gd`, as one ratio.

    `mean(Gs_i)`/`mean(Gd_i)` across every report, converted back to
    `H_S`/`H_T` (inverting `fim.statistics.differentiation.gs`/`gd`'s
    own formulas) and then through `g_st_from_demes`/`_jost_d_from_
    within_and_total` — the "ratio of means across everything" this
    project's own Ryman & Leimar remediation, `R3` part 3
    (`dev/doc/apps/selby/jost-finite-island-model/20260903-claude-opus-
    5-gene-identity-recursion-fim-implications.md` §4, §9), found the
    identity recursion itself predicts — genuinely different from
    averaging each report's own already-computed `G_ST`/`D` (a *mean of
    ratios*, subject to a real, measured Jensen-gap bias `Gs`/`Gd`
    themselves, being linear, never have). `Gs`/`Gd` are already each
    report's own ratio-of-means across loci (`report_for_state`'s own
    `mean_h_s`/`mean_h_t`), so averaging them again across reports
    reaches the identical grand pooled mean a direct re-derivation from
    every underlying frequency table would, without needing one.
    """
    mean_gs = mean(tuple(report["Gs"] for report in reports))
    mean_gd = mean(tuple(report["Gd"] for report in reports))
    within = 1.0 - mean_gs
    total = 1.0 - ((deme_count - 1) * mean_gd + mean_gs) / deme_count
    g_st = g_st_from_demes(total, within)
    d_value = jost_d_from_within_and_total(deme_count, within, total)
    return g_st, d_value


def _bootstrap_interval(
    point_estimate: float,
    bootstrap_values: Sequence[float],
    confidence: float,
    sample_count: int,
) -> ConfidenceInterval:
    """Build a percentile bootstrap `ConfidenceInterval` around a point estimate.

    `low`/`high` are the bootstrap distribution's own `(1 - confidence)
    / 2` / `1 - (1 - confidence) / 2` percentiles — the standard
    "percentile method" for a bootstrap interval, used here rather than
    a Student's-t interval on the bootstrap mean specifically because a
    ratio-of-means statistic's own bootstrap distribution is not
    generally symmetric, unlike the linear statistics `confidence_
    interval` already builds Student's-t intervals for. `half_width` is
    `(high - low) / 2`, a symmetrized summary kept only for display
    consistency with every other `ConfidenceInterval` this project
    reports — `low`/`high` are the authoritative bounds whenever the
    true interval is asymmetric, not `mean` plus or minus this value.
    `sample_std` is `None` for the same reason, stated one step more
    strongly: this constructor never sees a per-replicate sample of the
    statistic at all — only a point estimate and a distribution of
    *resampled grand ratios* — so there is no sample whose standard
    deviation could be reported. (The standard deviation of
    `bootstrap_values` itself is a bootstrap standard error of the point
    estimate, a different quantity that would read identically under
    that name; it is deliberately not substituted here.) `None` is
    therefore also the signal a display layer reads as "this interval
    has no honest symmetric summary at all — show `low`/`high`, not
    `mean` plus or minus anything."

    `sample_count` is the number of *original* replicates the point
    estimate and every resample were drawn from, matching `confidence_
    interval`'s own meaning for that field — not the number of
    bootstrap resamples, an internal computational parameter with no
    comparable statistical meaning.
    """
    tail = (1.0 - confidence) / 2.0
    low = float(np.quantile(bootstrap_values, tail))
    high = float(np.quantile(bootstrap_values, 1.0 - tail))
    return {
        "mean": point_estimate,
        "half_width": (high - low) / 2.0,
        "low": low,
        "high": high,
        "sample_std": None,
        "sample_count": sample_count,
        "confidence": confidence,
    }


def bootstrap_replicate_summary(
    results: Sequence[RunResult],
    *,
    rng: np.random.Generator,
    confidence: float = 0.95,
    bootstrap_samples: int = 2000,
) -> dict[str, ConfidenceInterval]:
    """Return a bootstrap confidence interval for the batch's own pooled `D`/`G_ST`.

    `replicate_summary` treats each replicate's own final `D`/`G_ST` as
    one independent draw and builds a closed-form Student's-t interval
    on their plain mean — correct for every *linear* statistic
    (`H_S`, `H_T`, `H_ST`, `E_ST`, `K_ST`, `Gs`, `Gd`), but `D`/`G_ST`
    are *ratios* of `Gs`/`Gd`, and the mean of a ratio is not, in
    general, the ratio of the means: averaging each replicate's own
    already-computed `D` carries a real, measured Jensen-gap bias
    relative to what the identity recursion itself predicts (this
    project's own Ryman & Leimar remediation, `R3` part 3, quantifies
    it directly: +4.84% of `D`, 3.4 standard errors from zero, on one
    measured run).
    This function instead bootstraps the *grand* ratio-of-means estimate
    (`_grand_ratio_of_means`: pool `Gs`/`Gd` across every replicate
    first, then take one ratio) — resampling replicates with
    replacement `bootstrap_samples` times, recomputing that same grand
    ratio each time, and reading off a percentile interval from the
    resulting distribution (`_bootstrap_interval`) — since no closed-form
    variance for a ratio-of-means statistic sampled from a multi-
    generation stochastic recursion has been derived (it would need
    delta-method propagation through the recursion's own higher
    moments); resampling sidesteps deriving one at all.

    Every other statistic `replicate_summary` reports is deliberately
    absent here — this function exists only for the two that need a
    fundamentally different treatment, not as a general replacement.

    Args:
        results: Two or more independently seeded replicate results,
            all sharing the same `SimulationParams.d` (this function
            reads it once, from `results[0]`).
        rng: The bootstrap's own explicitly threaded random generator —
            required, not defaulted, so a caller cannot accidentally
            get a non-reproducible interval the way an unseeded default
            would silently allow, unlike every other source of
            randomness in this project.
        confidence: Two-tailed confidence level for the percentile
            interval.
        bootstrap_samples: How many resampled batches to draw. `2000`
            (the default) is a conventional bootstrap sample size, ample
            for a stable 95% percentile estimate; deterministic and
            reproducible for a given `rng` state regardless of the
            value chosen.

    Returns:
        `{"D": ..., "G_ST": ...}`, both as `ConfidenceInterval`s built
        by `_bootstrap_interval` — `"G_ST"` omitted if it is undefined
        (`g_st_from_demes` returns `None`) for the observed batch's own
        point estimate, or for every one of its bootstrap resamples.

    Raises:
        ValueError: If fewer than two results are supplied, `confidence`
            is not in `(0, 1)`, or `bootstrap_samples` is not positive.
    """
    if len(results) < MINIMUM_REPLICATE_COUNT:
        raise ValueError("bootstrap_replicate_summary requires at least two results")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between 0 and 1")
    if bootstrap_samples < 1:
        raise ValueError("bootstrap_samples must be at least 1")
    reports = [result.report for result in results]
    replicate_count = len(reports)
    deme_count = results[0].params.d
    point_g_st, point_d = _grand_ratio_of_means(reports, deme_count)

    bootstrap_g_st: list[float] = []
    bootstrap_d: list[float] = []
    for _ in range(bootstrap_samples):
        resampled_indices = rng.integers(0, replicate_count, size=replicate_count)
        resampled_reports = [reports[index] for index in resampled_indices]
        resampled_g_st, resampled_d = _grand_ratio_of_means(
            resampled_reports, deme_count
        )
        if resampled_g_st is not None:
            bootstrap_g_st.append(resampled_g_st)
        bootstrap_d.append(resampled_d)

    summary: dict[str, ConfidenceInterval] = {
        "D": _bootstrap_interval(point_d, bootstrap_d, confidence, replicate_count)
    }
    if point_g_st is not None and bootstrap_g_st:
        summary["G_ST"] = _bootstrap_interval(
            point_g_st, bootstrap_g_st, confidence, replicate_count
        )
    return summary
