"""How long each replicate of a batch averages: the matched window.

A batch of replicates reaches a requested precision by averaging each
replicate over time after its burn-in and then across replicates. How long a
replicate should average is a trade: a long window makes each replicate's own
mean precise, a short one needs more replicates. The window here is *matched*
to the batch (design 9.1): sized so that the number of replicates the batch
will run anyway, a small multiple of how many run at once, reaches the
precision.

The first wave of replicates runs before anything is known about the noise, so
it averages for a guess. When that wave ends, its windows give the statistic's
standard deviation `sigma` and integrated autocorrelation time `tau_int`; the
standard error of one replicate's window mean is `sigma * sqrt(tau_int / A)`
for a window of `A` generations, so the window that reaches a per-replicate
standard error `SE` is `A = tau_int * (sigma / SE)**2`. Every later replicate
uses it. Replicates with different windows are pooled with equal weight: each
window mean estimates the same long-run value after the burn-in.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from fim.config.numerics import MINIMUM_REPLICATE_COUNT
from fim.statistics.interval import student_t_critical_value


@dataclass(frozen=True, slots=True)
class WindowNoise:
    """The noise of one statistic over one replicate's averaging window.

    Attributes:
        standard_deviation: The window's sample standard deviation.
        tau_int: The window's integrated autocorrelation time (at least 1).
    """

    standard_deviation: float
    tau_int: float


def target_replicate_count(*, minimum: int, wave_multiple: float, width: int) -> int:
    """Return how many replicates the matched window is sized for.

    `max(minimum, ceil(wave_multiple * width))`: a small multiple of how many
    replicates run at once, never below the fewest a batch may stop at.

    Args:
        minimum: `replicate_minimum`.
        wave_multiple: The Expert Setting `replicate_wave_multiple`.
        width: How many replicates run at once.

    Returns:
        The target replicate count.
    """
    return max(minimum, math.ceil(wave_multiple * width))


def lane_standard_error(
    *, precision: float, confidence: float, replicates: int
) -> float:
    """Return the standard error each replicate's mean needs.

    With `R` replicates of equal standard error `SE`, the across-replicate
    interval has half-width `t(R - 1) * SE / sqrt(R)`; setting that to
    `precision` gives `SE = precision * sqrt(R) / t(R - 1)`.

    Args:
        precision: The requested plus or minus, in the statistic's units.
        confidence: Two-tailed confidence level (0.90, 0.95 or 0.99).
        replicates: The number of replicates the batch will run (at least 2).

    Returns:
        The per-replicate standard error, zero when `precision` is zero.

    Raises:
        ValueError: If `replicates` is below 2.
    """
    if replicates < MINIMUM_REPLICATE_COUNT:
        raise ValueError("a batch interval needs at least 2 replicates")
    critical = student_t_critical_value(replicates - 1, confidence)
    return precision * math.sqrt(replicates) / critical


def matched_averaging_multiple(
    wave: Sequence[Mapping[str, WindowNoise]],
    *,
    statistics: Sequence[str],
    lane_error: float,
    multiple_minimum: float,
    multiple_maximum: float,
    relaxation_time: float,
) -> float:
    """Return the averaging window, in relaxation times, the first wave implies.

    For each watched statistic the wave's windows are pooled with equal
    weight (`sigma**2` and `tau_int` each averaged over the wave), the window
    that reaches `lane_error` is computed, and the slowest statistic decides.
    The result is clamped to `[multiple_minimum, multiple_maximum]`.

    Args:
        wave: One mapping per first-wave replicate, statistic to its noise.
            A replicate that lacks a statistic does not count for it.
        statistics: The watched statistics.
        lane_error: Target standard error of one replicate's mean.
        multiple_minimum: Lower clamp, in relaxation times.
        multiple_maximum: Upper clamp, in relaxation times.
        relaxation_time: The model's relaxation time, in generations.

    Returns:
        The clamped window, in relaxation times. The upper clamp when
        `lane_error` is zero; the lower when every statistic is exactly known.
    """
    needed = 0.0
    for name in statistics:
        noises = [replicate[name] for replicate in wave if name in replicate]
        if not noises:
            continue
        variance = math.fsum(n.standard_deviation**2 for n in noises) / len(noises)
        tau_int = math.fsum(n.tau_int for n in noises) / len(noises)
        if variance == 0.0:
            continue
        if lane_error == 0.0:
            needed = math.inf
            continue
        needed = max(needed, tau_int * variance / lane_error**2 / relaxation_time)
    return min(multiple_maximum, max(multiple_minimum, needed))


class BatchWindowPlanner:
    """Decide each replicate's averaging window, measuring the first wave.

    One planner serves one batch. `window_for(index)` gives the window of
    replicate `index` (zero-based): the first-wave guess for the first
    `first_wave` replicates and the matched window afterwards, or `None` for a
    later replicate until the first wave has been recorded. A fixed window,
    chosen by the user, is returned for every replicate and nothing is
    measured.
    """

    def __init__(
        self,
        *,
        relaxation_time: float,
        statistics: Sequence[str],
        precision: float,
        confidence: float,
        replicates: int,
        first_wave: int,
        first_wave_multiple: float,
        multiple_minimum: float,
        multiple_maximum: float,
        fixed_window: int | None = None,
        window_limit: int | None = None,
    ) -> None:
        """Initialize a planner.

        Args:
            relaxation_time: The model's relaxation time, in generations.
            statistics: The watched statistics.
            precision: The requested batch precision.
            confidence: Two-tailed confidence level.
            replicates: The replicate count the matched window targets (at
                least 2).
            first_wave: How many replicates run before anything is measured
                (at least 1).
            first_wave_multiple: The first wave's window, in relaxation times.
            multiple_minimum: Lower clamp of the matched window, in
                relaxation times.
            multiple_maximum: Upper clamp, in relaxation times.
            fixed_window: A user-chosen window in generations, used for every
                replicate; `None` to match.
            window_limit: The longest window a guessed or matched window may
                be, in generations (the room the generation cap leaves after
                the burn-in); `None` for no limit. A fixed window is never
                clamped: it is the user's choice, and a replicate that
                reaches the cap first reports it.

        Raises:
            ValueError: If a count is out of range.
        """
        if first_wave < 1:
            raise ValueError("first_wave must be at least 1")
        if not relaxation_time > 0.0:
            raise ValueError("relaxation_time must be positive")
        if fixed_window is not None and fixed_window < 1:
            raise ValueError("fixed_window must be at least 1")
        if window_limit is not None and window_limit < 1:
            raise ValueError("window_limit must be at least 1")
        self._relaxation_time = relaxation_time
        self._statistics = tuple(statistics)
        self._lane_error = lane_standard_error(
            precision=precision, confidence=confidence, replicates=replicates
        )
        self._first_wave = first_wave
        self._limit = window_limit
        self._guess = self._limited(
            max(1, math.ceil(first_wave_multiple * relaxation_time))
        )
        self._minimum = multiple_minimum
        self._maximum = multiple_maximum
        self._fixed = fixed_window
        self._wave: dict[int, Mapping[str, WindowNoise]] = {}
        self._matched: int | None = None

    @property
    def first_wave(self) -> int:
        """Return how many replicates form the first wave."""
        return self._first_wave

    @property
    def matched_window(self) -> int | None:
        """Return the matched window in generations, once the wave is recorded."""
        return self._matched

    @property
    def earliest_window(self) -> int:
        """Return the shortest window any replicate can be assigned.

        A matched window is at least `multiple_minimum` relaxation times
        (or the limit, if lower), so a replicate that is still waiting for
        its window can safely average this long without overshooting it.
        """
        return self._limited(max(1, math.ceil(self._minimum * self._relaxation_time)))

    def window_for(self, index: int) -> int | None:
        """Return the averaging window of replicate `index`, in generations.

        Args:
            index: The replicate's zero-based index.

        Returns:
            The window, or `None` when the replicate is past the first wave
            and the wave has not been fully recorded yet.
        """
        if self._fixed is not None:
            return self._fixed
        if index < self._first_wave:
            return self._guess
        return self._matched

    def record(self, index: int, noise: Mapping[str, WindowNoise]) -> int | None:
        """Record one first-wave replicate's noise.

        Args:
            index: The replicate's zero-based index; ignored past the wave.
            noise: Its watched statistics' noise over its window.

        Returns:
            The matched window if this record completed the wave, else `None`.
        """
        if self._fixed is not None or self._matched is not None:
            return None
        if index >= self._first_wave:
            return None
        self._wave[index] = noise
        if len(self._wave) < self._first_wave:
            return None
        multiple = matched_averaging_multiple(
            [self._wave[key] for key in sorted(self._wave)],
            statistics=self._statistics,
            lane_error=self._lane_error,
            multiple_minimum=self._minimum,
            multiple_maximum=self._maximum,
            relaxation_time=self._relaxation_time,
        )
        self._matched = self._limited(
            max(1, math.ceil(multiple * self._relaxation_time))
        )
        return self._matched

    def _limited(self, window: int) -> int:
        """Return `window` clamped to the limit, if there is one."""
        return window if self._limit is None else min(window, self._limit)
