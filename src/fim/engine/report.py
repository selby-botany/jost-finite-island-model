"""The final report of a run and report-level statistic access."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, cast

from fim.convergence.monitor import BurnInMonitor
from fim.convergence.window_statistics import EstimateForms
from fim.engine.records import FinalReport
from fim.engine.tracking import (
    ALWAYS_TRACKED_STATISTICS,
    derived_fields,
    mean,
    nei_all_demes_fields,
    pooled_g_st_and_d,
    statistics_for_locus,
)
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.statistics.catalog import report_keys
from fim.statistics.differentiation import gd_from_within_and_total


def window_statistics_payload(
    monitor: BurnInMonitor,
) -> dict[str, dict[str, Any]]:
    """Compute `report_for_state`'s own `window_statistics` from a stopped monitor.

    Every recorded statistic, watched or not, gets its statistics over the
    same evidence window, from the monitor's window start to the stop (design
    6.1), so the report is consistent whichever statistic decided the stop.

    Args:
        monitor: The just-stopped monitor driving this run.

    Returns:
        One entry per statistic whose evidence window held at least three
        defined values, keyed by name: the window mean, standard error,
        standard deviation, effective sample size, length, the generation the
        window starts and ends at, the burn-in, the effective-sample-size
        floor and standard-error target it was judged against, the estimator
        that produced it, `noise_adequate` (whether the requested precision
        was reached), Geweke's `z` for the window's start against its end
        (when the window is long enough and the `z` finite) and both
        expected-value forms (`_estimate_forms_payload`). A
        statistic with no such window (the run ended inside its burn-in) is
        simply omitted: nothing to show, nothing fabricated.
    """
    payload: dict[str, dict[str, Any]] = {}
    start = monitor.window_start_generation
    for name in monitor.histories:
        stats = monitor.evidence_statistics(name)
        if stats is None:
            continue
        forms = monitor.estimate_forms(name)
        geweke = monitor.evidence_geweke_z(name)
        payload[name] = {
            "mean": stats.mean,
            "standard_error": stats.standard_error,
            "standard_deviation": stats.standard_deviation,
            "effective_sample_size": stats.effective_sample_size,
            "window": stats.window,
            "window_start": start if start is not None else 0,
            "window_end": monitor.window_end_generation or 0,
            "burn_in": monitor.burn_in_generation,
            "minimum_ess": monitor.minimum_effective_sample_size,
            "target_standard_error": monitor.target_standard_error_for(
                name, stats.mean
            ),
            "estimator": "geyer",
            "noise_adequate": stats.meets(
                monitor.target_standard_error_for(name, stats.mean),
                monitor.minimum_effective_sample_size,
            ),
        }
        # An infinite `z` (two exactly known segments that differ) has no JSON
        # spelling, so it is left out rather than written as a non-number.
        if geweke is not None and math.isfinite(geweke):
            payload[name]["geweke_z"] = geweke
        # A capped run says how long it would have needed (design 6.10).
        if not monitor.outcome().converged:
            projected = monitor.projected_generations_for(name)
            if projected is not None:
                payload[name]["projected_generations"] = projected
        if forms is not None:
            payload[name].update(_estimate_forms_payload(forms))
    return payload


def _estimate_forms_payload(forms: EstimateForms) -> dict[str, Any]:
    """Describe both expected-value forms of one statistic for the report.

    Args:
        forms: The statistic's forms over the evidence window.

    Returns:
        `selected_form`, `undefined_generations` and, for each form that
        exists, `{"mean", "standard_error"}` under `mean_of_values` and
        `value_of_means` (design 6.11).
    """
    payload: dict[str, Any] = {
        "selected_form": forms.selected,
        "undefined_generations": forms.undefined_generations,
    }
    for name in ("mean_of_values", "value_of_means"):
        stats = getattr(forms, name)
        if stats is not None:
            payload[name] = {
                "mean": stats.mean,
                "standard_error": stats.standard_error,
            }
    return payload


def report_for_state(
    state: ModelState,
    params: SimulationParams,
    *,
    run_id: str,
    converged: bool,
    reason: str,
    window_statistics: Mapping[str, dict[str, Any]] | None = None,
    converged_statistics: Sequence[str] | None = None,
) -> FinalReport:
    """Compute the final report independently of the run loop.

    This is the function that actually builds a `FinalReport` (see that
    class's own docstring for what each field in the result means) from
    a simulated population state — reading off its allele frequencies at
    every locus, computing every named differentiation statistic at each
    one (`statistics_for_locus`), and averaging each statistic across
    loci into the single reported number. `run_one` calls this once, at
    the very end of a run, to build that run's own final report; the
    GUI also calls it directly to describe the *starting* population
    (generation zero, before any migration/mutation/drift has happened
    yet) for its live preview — hence "independently of the run loop" in
    this function's own name: it only needs a state and the parameters
    that produced it, not a whole run in progress.

    Args:
        state: The population state to summarize.
        params: The run's own parameters — read here only for which
            statistic(s) were being watched (the shape of
            `converged_on`, in the result) and how demes should be
            weighted when averaging (`SimulationParams.deme_weighting`;
            see `statistics_for_locus`).
        run_id: This run's own identifier, copied into the report
            verbatim so the report is self-describing on its own,
            without needing to be paired with anything else to know
            which run it came from.
        converged: Whether this state was reached because the watched
            statistic(s) settled down on their own, or because a run
            simply hit its generation cap — see `FinalReport.converged`.
        reason: The short, human-readable phrase explaining why the run
            stopped where it did — see `FinalReport.reason`.
        window_statistics: `window_statistics_payload`'s own result — how
            precisely each recorded statistic's trailing-window mean was
            actually known when the run stopped (`fim.convergence.window_
            statistics`), keyed by statistic name; `None` for a caller with
            no monitored run behind this state at all (a GUI preview of
            generation zero, a re-analysis of a persisted trajectory, most
            of this function's own tests) — `FinalReport.window_statistics`
            is then `{}`, not absent, so every caller can iterate it
            unconditionally rather than checking for `None` twice.
        converged_statistics: The watched statistics that had passed
            when the run stopped (`ConvergenceMonitor.
            stable_statistics`), for a caller with the run's own monitor.
            `None` for a caller without one (a re-analysis of a saved
            run's final generation): a converged state is then reported
            as having converged on every watched statistic, which is
            exact for one statistic or `convergence_combinator: all`.
            Ignored when `converged` is `False` (`converged_on` is then
            `None`).

    Returns:
        A `FinalReport`: the run's own bookkeeping (id, generation,
        whether/why it stopped where it did) plus every named
        differentiation/heterozygosity and supplemental literature
        statistic, each already averaged across every genetic locus the
        run tracked.
    """
    locus_reports = tuple(
        statistics_for_locus(state, params, locus_index)
        for locus_index in range(state.locus_count)
    )
    mean_h_s = mean(tuple(report["H_S"] for report in locus_reports))
    mean_h_t = mean(tuple(report["H_T"] for report in locus_reports))
    g_st, d = pooled_g_st_and_d(
        locus_reports,
        mean_h_s=mean_h_s,
        mean_h_t=mean_h_t,
        deme_count=state.deme_count,
        locus_aggregation=params.locus_aggregation,
    )
    return {
        "run_id": run_id,
        "generation": state.generation,
        "converged": converged,
        "converged_on": _converged_on(
            params, converged=converged, passed=converged_statistics
        ),
        "reason": reason,
        "G_ST": g_st,
        "D": d,
        "E_ST": mean(tuple(report["E_ST"] for report in locus_reports)),
        "K_ST": mean(tuple(report["K_ST"] for report in locus_reports)),
        "H_S": mean_h_s,
        "H_T": mean_h_t,
        "H_ST": mean(tuple(report["H_ST"] for report in locus_reports)),
        "A_CGD": mean(tuple(report["A_CGD"] for report in locus_reports)),
        "Delta": mean(tuple(report["Delta"] for report in locus_reports)),
        "MI": mean(tuple(report["MI"] for report in locus_reports)),
        "Gs": 1.0 - mean_h_s,
        "Gd": gd_from_within_and_total(mean_h_s, mean_h_t, state.deme_count),
        **derived_fields(mean_h_s, mean_h_t, state.deme_count),
        **nei_all_demes_fields(state),
        "window_statistics": dict(window_statistics) if window_statistics else {},
    }


def _converged_on(
    params: SimulationParams,
    *,
    converged: bool,
    passed: Sequence[str] | None,
) -> str | list[str] | None:
    """Return `FinalReport.converged_on`: what the run actually converged on.

    Args:
        params: The run's parameters; a bare-string
            `convergence_statistic` gives a bare-string result, a list
            gives a list.
        converged: Whether the run stopped by converging.
        passed: The watched statistics that had passed at the stop
            (`ConvergenceMonitor.stable_statistics`), or `None` when the
            caller has no monitor, which counts every watched statistic.

    Returns:
        `None` when the run did not converge; otherwise the passing
        statistic(s), shaped like `params.convergence_statistic`.
    """
    if not converged:
        return None
    watched = (
        (params.convergence_statistic,)
        if isinstance(params.convergence_statistic, str)
        else tuple(params.convergence_statistic)
    )
    names = watched if passed is None else tuple(passed)
    if isinstance(params.convergence_statistic, str):
        return names[0] if names else None
    return list(names)


def replicate_statistic(report: FinalReport, statistic: str) -> float | None:
    """Read one statistic's value for a replicate of a batch.

    A replicate that averaged contributes its window mean, in the form it
    selected, not the last generation's value: the last generation is one
    noisy draw, the window mean is what the replicate measured (design 6.7).
    For a statistic that is recorded every generation ($D$, $G_{ST}$, $H_S$,
    $H_T$, $H_{ST}$ and anything watched) a replicate with a window but no
    defined window mean contributes nothing, as an undefined replicate always
    has. Any other statistic, and any report with no window at all (a live
    progress report), contributes its final-state value.

    Args:
        report: The replicate's report.
        statistic: A statistic name.

    Returns:
        The value, or `None` when the replicate leaves it undefined.
    """
    windows = report["window_statistics"]
    entry = windows.get(statistic)
    if entry is not None:
        return cast("float", entry["mean"])
    if windows and statistic in ALWAYS_TRACKED_STATISTICS:
        return None
    return final_report_statistic(report, statistic)


def final_report_statistic(report: FinalReport, statistic: str) -> float | None:
    """Read one named statistic from a final run report.

    Checked against `fim.statistics.catalog.report_keys` rather than
    indexing the `TypedDict` directly: `statistic` is an arbitrary string
    (ultimately from user-supplied configuration, by way of
    `SimulationParams.convergence_statistics`), and a name that is not a
    report field should raise this specific, named-statistic error, not
    Python's own generic `KeyError`.
    """
    if statistic not in report_keys():
        raise ValueError(f"unsupported statistic: {statistic}")
    value = cast("Mapping[str, float | None]", report)[statistic]
    return value


def report_statistic(report: FinalReport, statistic: str) -> float | None:
    """Return one named global statistic from a final report.

    The public form of the engine's own lookup, for callers that iterate
    `fim.statistics.catalog.report_keys` rather than naming each field.

    Args:
        report: A `FinalReport`.
        statistic: A key from `fim.statistics.catalog.report_keys`.

    Returns:
        The value; ``None`` where the statistic is undefined ($G_{ST}$ at a
        monomorphic locus) or infinite (a Nei distance with no allele
        shared).

    Raises:
        ValueError: If `statistic` is not a report statistic.
    """
    return final_report_statistic(report, statistic)
