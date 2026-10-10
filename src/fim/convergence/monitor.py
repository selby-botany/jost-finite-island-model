"""Stateful convergence monitoring with an explicit hard-cap outcome.

Two monitors share one result type. `BurnInMonitor` drives a single run: it
waits out the burn-in, then averages each watched statistic over an evidence
window that grows until its standard error is small enough and its effective
sample size large enough (design `20261005-claude-opus-5-5-simplified-
convergence-rule-design.md`, `selby/restricted`, 6.1). `ConvergenceMonitor`
drives the replicate batch: it remembers one value per completed replicate
and asks a `fim.convergence.criteria.ConvergenceCriterion` whether the
across-replicate interval is tight enough.

Both always enforce a hard generation (or replicate) cap, so a statistic that
genuinely never settles still cannot run a simulation forever.
`ConvergenceOutcome` is the small, immutable record they hand back describing
which of the two things happened, if either yet has.
"""

from __future__ import annotations

import logging
import math
from array import array
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from statistics import NormalDist
from typing import Literal

import numpy as np
import numpy.typing as npt

from fim.config.convergence import (
    CHECK_GROWTH,
    FRACTIONAL_BURN_IN,
    MINIMUM_EFFECTIVE_SAMPLE_SIZE,
)
from fim.config.numerics import MINIMUM_WINDOW_VALUES
from fim.config.statistics import ESTIMATE_AUTO_DENOMINATOR, ESTIMATE_AUTO_FRACTION
from fim.convergence.criteria import ConvergenceCriterion
from fim.convergence.window_statistics import (
    EstimateForms,
    IdentityStatistic,
    WindowStatistics,
    degenerate_share,
    geweke_z,
    geyer_window_statistics,
    select_form,
    value_of_means_statistics,
)

Combinator = Literal["any", "all"]

logger = logging.getLogger(__name__)


class StopReason(StrEnum):
    """Reason a simulation stopped.

    A run always stops for exactly one of these two reasons: there is no third
    way for the simulation loop to exit. `STATISTIC_CONVERGED` means the
    watched statistic(s) reached the requested precision before the generation
    cap; `MAX_GENERATIONS` means the cap was hit first. Reaching the cap is
    reported as a valid, non-error outcome (see `ConvergenceOutcome.converged`):
    some parameter combinations genuinely never reach a given precision in any
    reasonable number of generations, and that is itself a useful finding.
    """

    STATISTIC_CONVERGED = "statistic converged"
    MAX_GENERATIONS = "hit the cap"


@dataclass(frozen=True, slots=True)
class ConvergenceOutcome:
    """Describe a monitor's terminal decision.

    Returned by `record` after every observation and retrievable at any time
    via `outcome`. While a run is in progress this is a "not stopped"
    placeholder with every other field `None`/`False`; once the run stops, the
    four fields together are its complete, permanent answer to "why, and at
    which generation."

    Args:
        stopped: Whether the monitor has reached a terminal decision at all
            (once `True` it stays `True` and no further observation can be
            recorded).
        converged: Whether the watched statistic(s) reached the requested
            precision (`True`), as opposed to the hard cap stopping the run
            (`False`). Only meaningful once `stopped` is `True`.
        reason: Which of the two `StopReason` values applies, or `None` while
            the run is still in progress.
        generation: The generation number at which the run stopped, or `None`
            while still in progress.
    """

    stopped: bool
    converged: bool
    reason: StopReason | None
    generation: int | None


def _check_generation(generation: object, previous: int | None) -> int:
    """Validate a generation number and its order against the previous one.

    Args:
        generation: The candidate generation.
        previous: The last recorded generation, or `None` for the first.

    Returns:
        The generation, as an `int`.

    Raises:
        ValueError: If it is not a non-negative integer, or does not follow
            `previous`.
    """
    # `bool` is a subclass of `int`, so `True` would otherwise pass as 1.
    if isinstance(generation, bool) or not isinstance(generation, int):
        raise ValueError("generation must be a non-negative integer")
    if generation < 0:
        raise ValueError("generation must be non-negative")
    if previous is not None and generation <= previous:
        raise ValueError("generations must be recorded in increasing order")
    return generation


def _check_number(statistic: str, number: object) -> None:
    """Require a finite real number for `statistic`.

    Raises:
        ValueError: If it is not a finite real number (a `bool` is not one).
    """
    if isinstance(number, bool) or not isinstance(number, int | float):
        raise ValueError(f"convergence statistic {statistic!r} must be a real number")
    if not math.isfinite(number):
        raise ValueError(f"convergence statistic {statistic!r} must be finite")


def _check_names(
    statistics: Sequence[str],
    extra_statistics: Sequence[str],
    combinator: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Validate the watched and display-only statistic names and the combinator.

    Raises:
        ValueError: If `statistics` is empty, any name repeats, or
            `combinator` is not `"any"` or `"all"`.
    """
    statistic_names = tuple(statistics)
    extra_names = tuple(extra_statistics)
    if not statistic_names:
        raise ValueError("statistics must not be empty")
    all_names = statistic_names + extra_names
    if len(set(all_names)) != len(all_names):
        raise ValueError("statistics and extra_statistics must not repeat a name")
    if combinator not in {"any", "all"}:
        raise ValueError("combinator must be 'any' or 'all'")
    return statistic_names, extra_names


def _resolve_values(
    value: float | Mapping[str, float],
    watched: Sequence[str],
    known: Sequence[str],
) -> dict[str, float]:
    """Normalize a bare float or a per-statistic mapping into full form.

    A mapping may be a partial or even empty subset of the configured
    statistics (a statistic with no defined value this round simply
    contributes nothing to its own history); every key it includes must name a
    configured statistic. A bare float is legal only while watching exactly one
    statistic.

    Raises:
        ValueError: If a mapping names an unconfigured statistic, or a bare
            float is given while watching several.
    """
    if isinstance(value, Mapping):
        unknown = set(value) - set(known)
        if unknown:
            expected = ", ".join(sorted(known))
            names = ", ".join(sorted(unknown))
            raise ValueError(
                f"record() values named unconfigured statistic(s) "
                f"{names}; configured: {expected}"
            )
        return dict(value)
    if len(watched) != 1:
        raise ValueError(
            "record() requires a mapping of statistic name to value "
            "while watching several statistics"
        )
    return {watched[0]: value}


class BurnInMonitor:
    """Burn in, then average: the stopping rule of a single run.

    Until the burn-in generation nothing can stop the run (except the cap).
    From then on the evidence window is `[start, t]`, where `start` is the
    burn-in. The first check comes `first_check` generations after `start`;
    at each check every watched statistic's window mean gets a standard error
    from `geyer_window_statistics`, and a statistic passes when that standard
    error is at most `precision / z(confidence)` and its effective sample size
    is at least `minimum_effective_sample_size`. The run stops when the
    watched statistics pass under the combinator. The next check comes when
    the window has grown by `growth` (doubling by default).

    Every watched statistic is judged at every check, so the stop generation
    never depends on the order the statistics are listed in. Nothing but
    appending to the histories happens between checks.

    With no burn-in (`burn_in=None`, for a model with no relaxation time) the
    window starts at `floor(fractional_burn_in * t)` at each check.

    `extra_statistics` are recorded for display and reporting but never decide
    the stop.

    A statistic named in `identity_statistics` is a function of `H_S` and
    `H_T`, so it has two expected-value forms (design 6.11): the mean of its
    values and the value of the mean identities. `estimate` selects the form
    each check judges and the report headlines; `"auto"` applies its rule to
    the window as it stands at that check, so the form reported at the stop is
    the form the stop was judged on. Such a monitor must record `H_S` and
    `H_T` every generation (as extras or watched).
    """

    def __init__(
        self,
        *,
        max_generations: int,
        precision: float,
        confidence: float = 0.95,
        burn_in: int | None,
        first_check: int,
        statistics: Sequence[str] = ("value",),
        combinator: Combinator = "all",
        extra_statistics: Sequence[str] = (),
        minimum_effective_sample_size: float = MINIMUM_EFFECTIVE_SAMPLE_SIZE,
        growth: float = CHECK_GROWTH,
        fractional_burn_in: float = FRACTIONAL_BURN_IN,
        identity_statistics: Mapping[str, IdentityStatistic] | None = None,
        estimate: str = "mean_of_values",
        auto_denominator: float = ESTIMATE_AUTO_DENOMINATOR,
        auto_fraction: float = ESTIMATE_AUTO_FRACTION,
    ) -> None:
        """Initialize an empty monitor.

        Args:
            max_generations: Hard generation cap.
            precision: Plus or minus, in each statistic's own units.
            confidence: Two-tailed confidence level of `precision`.
            burn_in: Generations discarded before averaging starts (at least
                1, so generation 0 is never averaged), or `None` for the
                fractional burn-in.
            first_check: Generations after the burn-in before the first check.
            statistics: The watched statistics, which decide the stop.
            combinator: `"all"` requires every watched statistic to pass;
                `"any"` only one.
            extra_statistics: Statistics recorded without ever deciding.
            minimum_effective_sample_size: The effective-sample-size floor.
            growth: Factor by which the window grows between checks.
            fractional_burn_in: Share of the run discarded when `burn_in` is
                `None`.
            identity_statistics: Recorded statistics that are functions of
                `H_S` and `H_T`, by name; they get both expected-value forms.
            estimate: `"mean_of_values"`, `"value_of_means"` or `"auto"`.
            auto_denominator: Under `"auto"`, the denominator below which a
                generation counts as degenerate.
            auto_fraction: Under `"auto"`, the degenerate share above which
                the value of means is used.

        Raises:
            ValueError: If a number is out of range, a name repeats, or an
                identity statistic's `H_S`/`H_T` are not recorded.
        """
        if max_generations < 1:
            raise ValueError("max_generations must be at least 1")
        if not (math.isfinite(precision) and precision >= 0.0):
            raise ValueError("precision must be finite and non-negative")
        if confidence not in (0.90, 0.95, 0.99):
            raise ValueError("confidence must be 0.90, 0.95, or 0.99")
        if burn_in is not None and burn_in < 1:
            raise ValueError("burn_in must be at least 1, or None")
        if first_check < MINIMUM_WINDOW_VALUES:
            raise ValueError(f"first_check must be at least {MINIMUM_WINDOW_VALUES}")
        if not growth > 1.0:
            raise ValueError("growth must be greater than 1")
        if not 0.0 < fractional_burn_in < 1.0:
            raise ValueError("fractional_burn_in must be between 0 and 1")
        if not minimum_effective_sample_size > 0.0:
            raise ValueError("minimum_effective_sample_size must be positive")
        self._statistics, extra_names = _check_names(
            statistics, extra_statistics, combinator
        )
        self._all_statistics = self._statistics + extra_names
        self._identity = dict(identity_statistics or {})
        if estimate not in ("mean_of_values", "value_of_means", "auto"):
            raise ValueError(f"unknown estimate {estimate!r}")
        unknown = [name for name in self._identity if name not in self._all_statistics]
        if unknown:
            raise ValueError(f"identity statistics not recorded: {unknown}")
        if self._identity and not {"H_S", "H_T"} <= set(self._all_statistics):
            raise ValueError("identity statistics need H_S and H_T recorded")
        self._estimate = estimate
        self._auto_denominator = auto_denominator
        self._auto_fraction = auto_fraction
        self._combinator = combinator
        self._max_generations = max_generations
        self._burn_in = burn_in
        self._first_check = first_check
        self._growth = growth
        self._fractional = fractional_burn_in
        self._minimum_ess = minimum_effective_sample_size
        self._target_standard_error = precision / NormalDist().inv_cdf(
            0.5 + confidence / 2.0
        )
        self._generations = array("q")
        self._values: dict[str, array[float]] = {
            name: array("d") for name in self._all_statistics
        }
        self._value_generations: dict[str, array[int]] = {
            name: array("q") for name in self._all_statistics
        }
        self._next_check = (burn_in or 0) + first_check
        self._window_start = burn_in
        self._last_verdicts: tuple[bool, ...] = tuple(False for _ in self._statistics)
        self._outcome = ConvergenceOutcome(False, False, None, None)

    @property
    def generations(self) -> tuple[int, ...]:
        """Return recorded generations in order, parallel to `histories`' rounds."""
        return tuple(self._generations)

    @property
    def history(self) -> tuple[float, ...]:
        """Return the first watched statistic's recorded values."""
        return tuple(self._values[self._statistics[0]])

    @property
    def histories(self) -> Mapping[str, tuple[float, ...]]:
        """Return every recorded statistic's values, by name.

        Covers the watched statistics and the display-only extras alike.
        """
        return {name: tuple(values) for name, values in self._values.items()}

    @property
    def value_generations(self) -> Mapping[str, tuple[int, ...]]:
        """Return the generation each recorded value belongs to, by statistic.

        A statistic can be undefined in a generation (it then has no value for
        it), so its history can be shorter than `generations`.
        """
        return {name: tuple(gens) for name, gens in self._value_generations.items()}

    @property
    def window_start_generation(self) -> int | None:
        """Return the generation the evidence window starts at.

        The burn-in for a fixed burn-in; for the fractional burn-in, the start
        used at the latest check (or `None` before the first check). It is the
        start of the window the stop was judged on once the run has stopped.
        """
        return self._window_start

    def outcome(self) -> ConvergenceOutcome:
        """Return the current terminal or running outcome.

        Safe to call at any time; never changes anything.
        """
        return self._outcome

    def reason(self) -> StopReason | None:
        """Return the terminal reason, or `None` while running."""
        return self._outcome.reason

    def should_stop(self) -> bool:
        """Return whether the precision was reached or the cap hit."""
        return self._outcome.stopped

    def record(
        self, generation: int, value: float | Mapping[str, float]
    ) -> ConvergenceOutcome:
        """Record one generation's value(s) and update the stop decision.

        Args:
            generation: Non-negative generation number, above the last one.
            value: A bare float (only while watching one statistic) or a
                mapping of statistic name to finite value. A statistic the
                mapping omits has no defined value this generation and
                contributes nothing to its history. Every key must name a
                configured statistic.

        Returns:
            The updated outcome.

        Raises:
            RuntimeError: If called after the monitor already stopped.
            ValueError: If `generation` or a value is invalid.
        """
        if self._outcome.stopped:
            raise RuntimeError("cannot record after convergence monitoring stopped")
        previous = self._generations[-1] if self._generations else None
        generation = _check_generation(generation, previous)
        values = _resolve_values(value, self._statistics, self._all_statistics)
        for statistic, number in values.items():
            _check_number(statistic, number)
        self._generations.append(generation)
        for statistic, number in values.items():
            self._values[statistic].append(number)
            self._value_generations[statistic].append(generation)
        if generation >= self._next_check and self._check(generation):
            self._outcome = ConvergenceOutcome(
                stopped=True,
                converged=True,
                reason=StopReason.STATISTIC_CONVERGED,
                generation=generation,
            )
        elif generation >= self._max_generations:
            # A run that reaches the cap is reported as capped, with the
            # evidence window it did average so far (the caller computes it).
            self._outcome = ConvergenceOutcome(
                stopped=True,
                converged=False,
                reason=StopReason.MAX_GENERATIONS,
                generation=generation,
            )
            if self._burn_in is None:
                self._window_start = int(self._fractional * generation)
        return self._outcome

    def stable_statistics(self) -> tuple[str, ...]:
        """Return the watched statistics that passed at the stopping check.

        Empty before any check and for a run that hit its cap. Under `"all"` a
        converged run names every watched statistic; under `"any"`, those that
        had passed when it stopped.
        """
        if not self._outcome.converged:
            return ()
        return tuple(
            name
            for name, ok in zip(self._statistics, self._last_verdicts, strict=True)
            if ok
        )

    @property
    def target_standard_error(self) -> float:
        """Return the largest standard error that meets the requested precision."""
        return self._target_standard_error

    @property
    def minimum_effective_sample_size(self) -> float:
        """Return the effective-sample-size floor a window must meet."""
        return self._minimum_ess

    def evidence_statistics(self, name: str) -> WindowStatistics | None:
        """Return `name`'s statistics over the evidence window, as it stands.

        The window runs from `window_start_generation` to the last recorded
        generation, so once the run has stopped it is the window the report
        describes, for a watched statistic and a display-only one alike.

        Args:
            name: A configured statistic name.

        Returns:
            `None` when the window holds fewer than three defined values (the
            run ended inside its burn-in, or the statistic was mostly
            undefined).
        """
        start = self._window_start
        if start is None:
            start = int(
                self._fractional * (self._generations[-1] if self._generations else 0)
            )
        return self._statistics_over(name, start)

    @property
    def burn_in_generation(self) -> int:
        """Return the generation the averaging began at.

        The configured burn-in, or, for the fractional burn-in, the window
        start the run ended on.
        """
        if self._burn_in is not None:
            return self._burn_in
        return self._window_start or 0

    @property
    def window_end_generation(self) -> int | None:
        """Return the last recorded generation, where the evidence window ends."""
        return int(self._generations[-1]) if self._generations else None

    def evidence_geweke_z(self, name: str) -> float | None:
        """Return Geweke's `z` for `name`'s evidence window, as it stands.

        Compares the start of the window with its end (`geweke_z`), the
        diagnostic for a burn-in that was too short. It reads the statistic's
        own per-generation values, whichever expected-value form is selected.

        Args:
            name: A configured statistic name.

        Returns:
            `None` when the window is too short for both segments to hold
            `MINIMUM_WINDOW_VALUES` values.
        """
        start = self._window_start
        if start is None:
            start = int(self._fractional * (self.window_end_generation or 0))
        window = self._window_values(name, start)
        try:
            return geweke_z(window)
        except ValueError:
            return None

    def estimate_forms(self, name: str) -> EstimateForms | None:
        """Return `name`'s expected-value forms over the evidence window.

        Args:
            name: A configured statistic name.

        Returns:
            `None` when no form has enough defined values. A statistic that is
            not a function of the identities has only `mean_of_values`.
        """
        start = self._window_start
        if start is None:
            start = int(
                self._fractional * (self._generations[-1] if self._generations else 0)
            )
        return self._forms_over(name, start)

    def _check(self, generation: int) -> bool:
        """Judge every watched statistic over `[start, generation]`.

        Returns:
            Whether the watched statistics pass under the combinator. Also
            schedules the next check.
        """
        start = self._burn_in
        if start is None:
            start = int(self._fractional * generation)
        self._window_start = start
        verdicts = []
        for name in self._statistics:
            stats = self._statistics_over(name, start)
            if stats is None:
                verdicts.append(False)
                continue
            verdicts.append(stats.meets(self._target_standard_error, self._minimum_ess))
        self._last_verdicts = tuple(verdicts)
        length = generation + 1 - start
        self._next_check = max(generation + 1, start + math.ceil(self._growth * length))
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "convergence check: generation=%d window_start=%d verdicts=%s",
                generation,
                start,
                self._last_verdicts,
            )
        return all(verdicts) if self._combinator == "all" else any(verdicts)

    def _forms_over(self, name: str, start: int) -> EstimateForms | None:
        """Return `name`'s expected-value forms from generation `start`."""
        first = bisect_left(self._value_generations[name], start)
        window = np.frombuffer(self._values[name], dtype=np.float64)[first:]
        mean_of_values = (
            geyer_window_statistics(window)
            if window.shape[0] >= MINIMUM_WINDOW_VALUES
            else None
        )
        statistic = self._identity.get(name)
        if statistic is None:
            if mean_of_values is None:
                return None
            return EstimateForms(mean_of_values, None, "mean_of_values", 0)
        h_s = self._window_values("H_S", start)
        h_t = self._window_values("H_T", start)
        undefined = max(0, h_s.shape[0] - window.shape[0])
        form = select_form(
            self._estimate,
            undefined_generations=undefined,
            degenerate=degenerate_share(h_s, h_t, statistic, self._auto_denominator)
            if self._estimate == "auto" and h_s.shape == h_t.shape
            else 0.0,
            auto_fraction=self._auto_fraction,
        )
        value_of_means = value_of_means_statistics(h_s, h_t, statistic)
        if mean_of_values is None and value_of_means is None:
            return None
        # A form with nothing to report cannot decide: fall back to the other,
        # which the report then names as the selected one.
        if form == "value_of_means" and value_of_means is None:
            form = "mean_of_values"
        elif form == "mean_of_values" and mean_of_values is None:
            form = "value_of_means"
        return EstimateForms(mean_of_values, value_of_means, form, undefined)

    def _statistics_over(self, name: str, start: int) -> WindowStatistics | None:
        """Return `name`'s selected-form statistics from `start`, if enough data."""
        forms = self._forms_over(name, start)
        return None if forms is None else forms.selected_statistics

    def _window_values(self, name: str, start: int) -> npt.NDArray[np.float64]:
        """Return `name`'s recorded values from generation `start` on."""
        first = bisect_left(self._value_generations[name], start)
        return np.frombuffer(self._values[name], dtype=np.float64)[first:]


class ConvergenceMonitor:
    """Record one or more watched statistics and report why a batch should stop.

    The replicate batch's own monitor: give it each completed replicate's
    value(s) via `record`, and it remembers the whole history, asks the
    configured `fim.convergence.criteria.ConvergenceCriterion` whether the
    sample has settled, and separately enforces the hard cap
    (`max_generations`, the replicate cap here).

    With several statistics each keeps its own history, the same criterion is
    judged on each, and `combinator` decides whether stopping needs every one
    (`"all"`) or one (`"any"`). A statistic can be undefined in a round (see
    `record`): that round contributes nothing to its history. `extra_statistics`
    are recorded without ever affecting the stop.
    """

    def __init__(
        self,
        criterion: ConvergenceCriterion,
        *,
        max_generations: int,
        statistics: Sequence[str] = ("value",),
        combinator: Combinator = "all",
        extra_statistics: Sequence[str] = (),
    ) -> None:
        """Initialize an empty monitor.

        Args:
            criterion: The stability rule, applied to each watched history.
            max_generations: Hard cap on recorded rounds.
            statistics: The watched statistics, which decide the stop.
            combinator: `"all"` or `"any"`.
            extra_statistics: Statistics recorded without ever deciding.

        Raises:
            ValueError: If `max_generations`, a name or the combinator is
                invalid.
        """
        if max_generations < 1:
            raise ValueError("max_generations must be at least 1")
        self._statistics, extra_names = _check_names(
            statistics, extra_statistics, combinator
        )
        self._all_statistics = self._statistics + extra_names
        self._criterion = criterion
        self._max_generations = max_generations
        self._combinator = combinator
        self._generations: list[int] = []
        self._histories: dict[str, list[float]] = {
            name: [] for name in self._all_statistics
        }
        self._last_verdicts: tuple[bool, ...] = tuple(False for _ in self._statistics)
        self._outcome = ConvergenceOutcome(False, False, None, None)

    @property
    def generations(self) -> tuple[int, ...]:
        """Return the number recorded alongside each `record` call, in order."""
        return tuple(self._generations)

    @property
    def history(self) -> tuple[float, ...]:
        """Return the first watched statistic's recorded values."""
        return tuple(self._histories[self._statistics[0]])

    @property
    def histories(self) -> Mapping[str, tuple[float, ...]]:
        """Return every recorded statistic's values, by name."""
        return {name: tuple(values) for name, values in self._histories.items()}

    def outcome(self) -> ConvergenceOutcome:
        """Return the current terminal or running outcome; never changes anything."""
        return self._outcome

    def reason(self) -> StopReason | None:
        """Return the terminal reason, or `None` while running."""
        return self._outcome.reason

    def should_stop(self) -> bool:
        """Return whether the criterion was met or the cap hit."""
        return self._outcome.stopped

    def record(
        self, generation: int, value: float | Mapping[str, float]
    ) -> ConvergenceOutcome:
        """Record one ordered observation and update the stop decision.

        Args:
            generation: Non-negative number, above the last one.
            value: A bare float (only while watching one statistic) or a
                mapping of statistic name to finite value; a statistic the
                mapping omits is undefined this round.

        Returns:
            The updated outcome.

        Raises:
            RuntimeError: If called after the monitor already stopped.
            ValueError: If `generation` or a value is invalid.
        """
        if self._outcome.stopped:
            raise RuntimeError("cannot record after convergence monitoring stopped")
        previous = self._generations[-1] if self._generations else None
        generation = _check_generation(generation, previous)
        values = _resolve_values(value, self._statistics, self._all_statistics)
        for statistic, number in values.items():
            _check_number(statistic, number)
        self._generations.append(generation)
        for statistic, number in values.items():
            self._histories[statistic].append(number)
        # A list, not a generator: every watched statistic is judged every
        # round, so the verdicts do not depend on the order they are listed in.
        verdicts = [
            self._criterion.is_stable(self._histories[name])
            for name in self._statistics
        ]
        self._last_verdicts = tuple(verdicts)
        stable = all(verdicts) if self._combinator == "all" else any(verdicts)
        # Convergence is checked before the cap: a statistic that settles on the
        # very round the cap would also fire is reported as converged.
        if stable:
            self._outcome = ConvergenceOutcome(
                stopped=True,
                converged=True,
                reason=StopReason.STATISTIC_CONVERGED,
                generation=generation,
            )
        elif generation >= self._max_generations:
            self._outcome = ConvergenceOutcome(
                stopped=True,
                converged=False,
                reason=StopReason.MAX_GENERATIONS,
                generation=generation,
            )
        return self._outcome

    def stable_statistics(self) -> tuple[str, ...]:
        """Return the watched statistics that passed on the most recent round."""
        return tuple(
            name
            for name, ok in zip(self._statistics, self._last_verdicts, strict=True)
            if ok
        )
