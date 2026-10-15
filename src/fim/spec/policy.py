"""How a run decides it is done and how its numbers are summarized.

`Stopping` says when one replicate has converged, `Replication` says how many
replicates to run and when to stop adding them, and `Analysis` says how the
reported statistics are computed. Defaults are the engine's own: every
`"auto"` is derived from the model when the run is compiled.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias

from fim.config.defaults import DEFAULT_N_REPLICATES, DEFAULT_PRECISION
from fim.errors import ConfigurationError
from fim.model.params import (
    ConvergenceCombinator,
    ConvergenceEstimate,
    DemeWeighting,
    LocusAggregation,
    PrecisionMethod,
)
from fim.spec.validation import (
    check_bool,
    check_choice,
    check_finite,
    check_int,
    check_int_or_auto,
    check_strings,
)

CONFIDENCE_LEVELS: tuple[float, ...] = (0.90, 0.95, 0.99)
"""The confidence levels the engine supports for its intervals."""

Auto: TypeAlias = Literal["auto"]
"""The string that asks the engine to derive a value from the model."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Stopping:
    """When one replicate counts as converged.

    Args:
        statistics: The statistic(s) watched, by catalog ID (for example
            ``"D"``). A single name is accepted and becomes a one-item tuple.
        combinator: ``"all"`` stops when every watched statistic is stable;
            ``"any"`` when at least one is.
        estimate: Which expected value is reported for `D` and `G_ST`.
        burn_in: Generations discarded before averaging, or ``"auto"``.
        max_generations: Hard cap on generations, or ``"auto"``.
        precision: Target half-width of the interval around each watched
            statistic.
        per_statistic_precision: Overrides of `precision` by statistic ID.
        confidence: Confidence level of the intervals (0.90, 0.95, or 0.99).
    """

    statistics: tuple[str, ...] | str = ("D", "G_ST")
    combinator: ConvergenceCombinator = "all"
    estimate: ConvergenceEstimate = "mean_of_values"
    burn_in: int | Auto = "auto"
    max_generations: int | Auto = "auto"
    precision: float = DEFAULT_PRECISION
    per_statistic_precision: Mapping[str, float] | tuple[tuple[str, float], ...] = ()
    confidence: float = 0.95

    def __post_init__(self) -> None:
        """Normalize names and precision overrides, then check every value."""
        names = (
            (self.statistics,)
            if isinstance(self.statistics, str)
            else tuple(self.statistics)
        )
        if not names:
            raise ConfigurationError("stopping.statistics must not be empty")
        check_strings("stopping.statistics", names)
        object.__setattr__(self, "statistics", names)
        check_choice("stopping.combinator", self.combinator, ConvergenceCombinator)
        check_choice("stopping.estimate", self.estimate, ConvergenceEstimate)
        check_int_or_auto("stopping.burn_in", self.burn_in, minimum=1)
        check_int_or_auto("stopping.max_generations", self.max_generations, minimum=1)
        check_finite("stopping.precision", self.precision, at_least=0.0)
        if self.confidence not in CONFIDENCE_LEVELS or isinstance(
            self.confidence, bool
        ):
            raise ConfigurationError("stopping.confidence must be 0.90, 0.95, or 0.99")
        overrides = (
            tuple(self.per_statistic_precision.items())
            if isinstance(self.per_statistic_precision, Mapping)
            else tuple(self.per_statistic_precision)
        )
        for name, value in overrides:
            check_strings("stopping.per_statistic_precision", (name,))
            check_finite(
                f"stopping.per_statistic_precision[{name}]", value, at_least=0.0
            )
        object.__setattr__(
            self,
            "per_statistic_precision",
            tuple((name, float(value)) for name, value in overrides),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Replication:
    """How many independent replicates to run.

    Args:
        n: Most replicates to run.
        minimum: Fewest replicates before the batch may stop early.
        stop_early: Stop adding replicates once every watched statistic's
            interval across replicates reaches the precision.
        method: ``"interval"`` stops on the observed interval;
            ``"planned_replicates"`` runs a count planned from a pilot.
        averaging_window: Generations averaged per replicate, or ``"auto"``.
        max_concurrent: Replicates in flight at once under the generational
            backends, or `None` for all together. It belongs here, not in
            `Execution`: in an adaptive batch the first wave of replicates
            sets the averaging window of the later ones, so the window size
            changes the numbers (the verification behind `run_identity`).
    """

    n: int = DEFAULT_N_REPLICATES
    minimum: int = 10
    stop_early: bool = True
    method: PrecisionMethod = "interval"
    averaging_window: int | Auto = "auto"
    max_concurrent: int | None = None

    def __post_init__(self) -> None:
        """Check every value."""
        check_int("replication.n", self.n, minimum=1)
        check_int("replication.minimum", self.minimum, minimum=2)
        check_bool("replication.stop_early", self.stop_early)
        check_choice("replication.method", self.method, PrecisionMethod)
        check_int_or_auto(
            "replication.averaging_window", self.averaging_window, minimum=1
        )
        if self.max_concurrent is not None:
            check_int("replication.max_concurrent", self.max_concurrent, minimum=1)


@dataclass(frozen=True, slots=True, kw_only=True)
class Analysis:
    """How the reported statistics are computed.

    Args:
        deme_weighting: ``"equal"`` or by deme ``"size"``.
        locus_aggregation: ``"ratio_of_means"`` pools `H_S` and `H_T` across
            loci before forming `D` and `G_ST`; ``"mean_of_ratios"`` forms
            them per locus and averages.
        track_expensive: Also track the costly opt-in statistics every
            generation, for display.
    """

    deme_weighting: DemeWeighting = "equal"
    locus_aggregation: LocusAggregation = "ratio_of_means"
    track_expensive: bool = False

    def __post_init__(self) -> None:
        """Check every value."""
        check_choice("analysis.deme_weighting", self.deme_weighting, DemeWeighting)
        check_choice(
            "analysis.locus_aggregation", self.locus_aggregation, LocusAggregation
        )
        check_bool("analysis.track_expensive", self.track_expensive)
