"""Construction of convergence monitors and batch window planners."""

from __future__ import annotations

import math
from collections.abc import Sequence

from fim.config.numerics import MINIMUM_REPLICATE_COUNT
from fim.convergence.batch_window import (
    BatchWindowPlanner,
    WindowNoise,
    target_replicate_count,
)
from fim.convergence.criteria import ConfidenceIntervalCriterion
from fim.convergence.monitor import BurnInMonitor, ConvergenceMonitor
from fim.engine.records import FinalReport, RunResult
from fim.engine.report import replicate_statistic
from fim.engine.tracking import (
    UNBOUNDED_STATISTICS,
    extra_tracked_statistics,
    identity_statistics,
)
from fim.model.params import SimulationParams


def burn_in_monitor(
    params: SimulationParams, *, awaiting_window: bool = False
) -> BurnInMonitor:
    """Build the burn-in-then-average monitor one run is judged by.

    Args:
        params: The run's own configuration: `convergence_burn_in` (zero means
            the fractional burn-in, for a model with no relaxation time),
            `precision`, `confidence`, the watched statistics and their
            combinator, `max_generations`, and the Expert Settings. A
            positive `replicate_averaging_window` makes the run a fixed-window
            run (burn in, average that long, stop).
        awaiting_window: Build a window-mode monitor whose window is not known
            yet, for a replicate waiting for its batch's first wave.

    Returns:
        An empty monitor.
    """
    expert = params.expert
    first_check = expert.first_check_minimum
    if params.relaxation_time is not None:
        first_check = max(
            first_check,
            math.ceil(expert.first_check_relaxation_times * params.relaxation_time),
        )
    return BurnInMonitor(
        max_generations=params.max_generations,
        precision=params.precision,
        confidence=params.confidence,
        burn_in=params.convergence_burn_in or None,
        first_check=first_check,
        statistics=params.convergence_statistics,
        combinator=params.convergence_combinator,
        extra_statistics=extra_tracked_statistics(params),
        minimum_effective_sample_size=expert.minimum_effective_sample_size,
        growth=expert.check_growth,
        fractional_burn_in=expert.fractional_burn_in,
        identity_statistics=identity_statistics(params),
        estimate=params.convergence_estimate,
        auto_denominator=expert.estimate_auto_denominator,
        auto_fraction=expert.estimate_auto_fraction,
        averaging_window=params.replicate_averaging_window or None,
        awaiting_window=awaiting_window,
        statistic_precision=dict(params.statistic_precision),
        relative_statistics=[
            name
            for name in params.convergence_statistics
            if name in UNBOUNDED_STATISTICS
        ],
    )


def batch_window_planner(params: SimulationParams) -> BatchWindowPlanner | None:
    """Build the planner that sets each replicate's averaging window.

    A batch's replicates burn in and then average for a window matched to the
    batch (`fim.convergence.batch_window`). A model with no burn-in (no
    relaxation time, so only the fractional burn-in exists) cannot do that, and
    neither can a batch with no relaxation time to size the first wave by; its
    replicates keep the within-run rule.

    How many replicates "run at once" sets the first wave the windows are
    matched from. It comes from the configuration (`max_concurrent_replicates`,
    else the Expert Setting `batch_width`), never from the machine, so the
    same configuration gives the same windows, and the same results, on any
    hardware and under every backend.

    Args:
        params: The batch's configuration.

    Returns:
        The planner, or `None` for a single run or when the replicates keep
        the within-run rule.
    """
    if params.n_replicates < MINIMUM_REPLICATE_COUNT or params.convergence_burn_in == 0:
        return None
    fixed = params.replicate_averaging_window or None
    if fixed is None and params.relaxation_time is None:
        return None
    expert = params.expert
    width = max(
        1,
        min(
            params.max_concurrent_replicates or expert.batch_width,
            params.n_replicates,
        ),
    )
    if params.precision_method == "planned_replicates":
        replicates = params.n_replicates
    else:
        replicates = max(
            MINIMUM_REPLICATE_COUNT,
            min(
                params.n_replicates,
                target_replicate_count(
                    minimum=params.replicate_minimum,
                    wave_multiple=expert.replicate_wave_multiple,
                    width=width,
                ),
            ),
        )
    return BatchWindowPlanner(
        relaxation_time=params.relaxation_time or 1.0,
        statistics=params.convergence_statistics,
        precision=params.precision,
        confidence=params.confidence,
        replicates=replicates,
        first_wave=width,
        first_wave_multiple=expert.first_wave_averaging_multiple,
        multiple_minimum=expert.averaging_multiple_minimum,
        multiple_maximum=expert.averaging_multiple_maximum,
        fixed_window=fixed,
        window_limit=max(1, params.max_generations - params.convergence_burn_in),
    )


def window_noise(
    report: FinalReport, statistics: Sequence[str]
) -> dict[str, WindowNoise]:
    """Return how noisy each watched statistic was over a replicate's window.

    Args:
        report: A finished replicate's report.
        statistics: The watched statistics.

    Returns:
        The standard deviation and integrated autocorrelation time per
        statistic the report has a window for; the planner measures the first
        wave from these.
    """
    noise: dict[str, WindowNoise] = {}
    for name in statistics:
        entry = report["window_statistics"].get(name)
        if entry is not None:
            noise[name] = WindowNoise(
                standard_deviation=entry["standard_deviation"],
                tau_int=entry["window"] / entry["effective_sample_size"],
            )
    return noise


def replicate_monitor(params: SimulationParams) -> ConvergenceMonitor | None:
    """Return the adaptive replicate-batch monitor, or ``None`` when unused.

    This is the *other* kind of stopping this module implements — see
    this module's own docstring, above, for why "convergence" (one
    population's own generations settling down) and "the adaptive
    replicate stop" (a whole batch of independent replicates deciding
    it has run enough of them) are two different, unrelated ideas,
    despite both being built out of the same underlying `ConvergenceMonitor`
    machinery here (a `ConfidenceIntervalCriterion` in place of the
    `TrailingWindowCriterion` `run_one` uses for the first kind — the
    same monitor class, watching a different kind of stability).

    Returns ``None`` whenever `SimulationParams.stop_batch_early` is off
    (`SimulationParams.batch_precision` is `None`): always run exactly
    `n_replicates` replicates, never stop early.
    """
    precision = params.batch_precision
    if precision is None:
        return None
    criterion = ConfidenceIntervalCriterion(
        minimum_count=params.replicate_minimum,
        tolerance=precision,
        confidence=params.confidence,
    )
    return ConvergenceMonitor(
        criterion,
        max_generations=params.n_replicates,
        statistics=params.convergence_statistics,
        combinator=params.convergence_combinator,
    )


def replicate_stopping_values(
    result: RunResult,
    statistics: Sequence[str],
) -> dict[str, float]:
    """Return each watched statistic's value for this replicate, for the batch monitor.

    Feeds `replicate_monitor`'s own monitor exactly the way
    `convergence_values` feeds `run_one`'s within-run monitor — the
    same "one observation per unit being watched" pattern, just applied
    to a whole finished replicate's own final report here instead of one
    generation's own current state.

    A statistic this replicate leaves undefined (only ``G_ST``, and only
    when every one of this replicate's tracked loci was monomorphic — see
    `mean_g_st_across_loci`) is omitted rather than raised or
    substituted: this replicate simply contributes nothing to that
    statistic's own stopping-criterion window this round, exactly as
    `ConvergenceMonitor.record` already treats any statistic missing from
    its ``value`` mapping. This is deliberately the same rule
    `replicate_summary` uses for the *published* interval — drop an
    undefined replicate from that statistic's own sample rather than
    fabricate a value — so the stopping decision is judged against the
    same sample the summary will report, never a different one.
    """
    values: dict[str, float] = {}
    for statistic in statistics:
        value = replicate_statistic(result.report, statistic)
        if value is not None:
            values[statistic] = value
    return values
