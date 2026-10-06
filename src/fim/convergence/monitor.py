"""Stateful convergence monitoring with an explicit hard-cap outcome.

`fim.convergence.criteria` defines the individual *rules* for deciding
whether a statistic's history has settled down; this module is the
class that actually drives a run using one of those rules, generation
by generation (or replicate by replicate, for a batch): it remembers
every value recorded so far, asks the configured criterion whether
things have stabilized after each new one arrives, and — separately —
always enforces a hard generation cap regardless of what the criterion
says, so that a statistic that genuinely never settles (a legitimate,
if unwanted, outcome for some parameter combinations) still cannot
run a simulation forever. `ConvergenceOutcome` is the small, immutable
record this class hands back describing which of those two things
happened, if either yet has.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from fim.convergence.criteria import (
    ConvergenceCriterion,
    TrailingWindowCriterion,
    TrailingWindowTracker,
)
from fim.convergence.window_statistics import (
    MINIMUM_NOISE_CHECK_WINDOW,
    WindowStatistics,
    window_statistics,
)

Combinator = Literal["any", "all"]

logger = logging.getLogger(__name__)


class StopReason(StrEnum):
    """Reason a simulation stopped.

    A run always stops for exactly one of these two reasons — there is
    no third way for the simulation loop to exit. `STATISTIC_CONVERGED`
    means the watched statistic(s) satisfied the configured
    `fim.convergence.criteria.ConvergenceCriterion` before the
    generation cap was reached; `MAX_GENERATIONS` means the cap was hit
    first. Reaching the cap is reported as a valid, non-error outcome
    (see `ConvergenceOutcome.converged`) — some parameter combinations
    genuinely never settle within any reasonable number of generations,
    and that is itself a real, useful finding about those parameters,
    not a failure of the tool.
    """

    STATISTIC_CONVERGED = "statistic converged"
    MAX_GENERATIONS = "hit the cap"


@dataclass(frozen=True, slots=True)
class ConvergenceOutcome:
    """Describe a monitor's terminal decision.

    Returned by `ConvergenceMonitor.record` after every observation,
    and retrievable at any time via `ConvergenceMonitor.outcome`. While
    a run is still in progress (neither converged nor capped yet) this
    is a "not stopped" placeholder with every other field `None`/
    `False`; once the run does stop, the four fields together are its
    complete, permanent answer to "why, and at which generation."

    Args:
        stopped: Whether the monitor has reached a terminal decision at
            all (``False`` for every observation until the run
            actually stops; once ``True``, it stays ``True`` and no
            further observations can be recorded — see
            `ConvergenceMonitor.record`).
        converged: Whether the watched statistic(s) actually stabilized
            (``True``), as opposed to the run instead being stopped by
            the hard generation cap (``False``). Only meaningful once
            `stopped` is ``True``.
        reason: Which of the two `StopReason` values applies, or
            ``None`` while the run is still in progress.
        generation: The generation number at which the run stopped, or
            ``None`` while still in progress.
    """

    stopped: bool
    converged: bool
    reason: StopReason | None
    generation: int | None


class ConvergenceMonitor:
    """Record one or more watched statistics and report why a run should stop.

    This is the class the run loop actually calls, once per generation
    (or, for a replicate batch, once per completed replicate): give it
    the newest value(s) via `record`, and it remembers the whole
    history, asks the configured `fim.convergence.criteria.
    ConvergenceCriterion` whether things have settled, and separately
    checks the hard generation cap — see this module's own docstring
    for that split of responsibility. Everything the caller needs to
    know about the result of that decision (whether the run should
    stop yet, and if so why) comes back as a `ConvergenceOutcome`.

    A single statistic (the default) is this class's ordinary mode: every
    method behaves exactly as it did before several-statistic support
    existed. Passing more than one name in ``statistics`` is additive —
    each statistic keeps its own independent history, the same criterion is
    applied to each one separately, and ``combinator`` decides whether
    stopping requires every statistic to be simultaneously stable
    (``"all"``, design §9's "several statistics needed to agree") or just
    one of them (``"any"``). With exactly one statistic, ``all`` and
    ``any`` of a single Boolean are the same value, so the combinator is a
    genuine no-op in that case rather than a separately tested path.

    A statistic can be legitimately undefined on a given round (see
    `record`'s ``value`` argument): rather than raise or invent a
    substitute number, that round simply contributes nothing to that
    statistic's own history, so its stability is judged once enough
    *defined* rounds have accumulated — never sooner, from a padded
    history, and never blocked by a round where a different statistic
    happened to have no value.

    ``extra_statistics`` (constructor-only) names statistics this monitor
    also records a history for, alongside ``statistics``, without ever
    letting them affect the stop decision — this class does not need to
    know, and a caller never has to tell it twice, which of its own
    recorded histories is the subset actually deciding convergence versus
    which are merely along for the ride (recorded for display purposes
    only). ``history``/``histories`` return every recorded statistic's
    values either way; only the internal stability check (`record`,
    below) ever distinguishes the two groups.
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

        Nothing has been recorded yet immediately after construction —
        `outcome()` returns a "still running" placeholder, and
        `record()` must be called at least once before any stop
        decision can be made.

        Args:
            criterion: Statistical stability rule, applied independently to
                each watched statistic's own history.
            max_generations: Hard generation safety cap.
            statistics: Names of the statistic(s) to watch — these, and
                only these, drive the stop decision (see `combinator`).
                Defaults to one unnamed statistic, matching ``record()``'s
                bare-float form.
            combinator: ``"all"`` requires every statistic to be stable
                before stopping; ``"any"`` requires only one.
            extra_statistics: Names of additional statistics to record a
                history for, alongside ``statistics``, without those
                names ever influencing the stop decision — this monitor
                does not need to know, and never needs to be told again,
                which of its own recorded histories is the subset
                actually deciding convergence versus which are merely
                along for the ride (`fim.engine._watched_statistic_
                values`'s own "D/G_ST/H_S/H_T always present for display,
                only the watched subset gates stopping" design is exactly
                what this parameter exists to carry). Empty by default —
                every existing caller, unaffected. A name repeated
                between ``statistics`` and ``extra_statistics`` (or
                within either one) is rejected, the same as a repeat
                within ``statistics`` alone always has been.

        Raises:
            ValueError: If ``max_generations``, ``statistics``,
                ``extra_statistics``, or ``combinator`` is invalid.
        """
        if max_generations < 1:
            raise ValueError("max_generations must be at least 1")
        statistic_names = tuple(statistics)
        extra_names = tuple(extra_statistics)
        if not statistic_names:
            raise ValueError("statistics must not be empty")
        all_names = statistic_names + extra_names
        if len(set(all_names)) != len(all_names):
            raise ValueError("statistics and extra_statistics must not repeat a name")
        if combinator not in {"any", "all"}:
            raise ValueError("combinator must be 'any' or 'all'")
        self._criterion = criterion
        self._max_generations = max_generations
        # `_statistics` is the subset `record`'s own stability check
        # (below) ever reads — `_all_statistics` (`_statistics` plus
        # `extra_names`) is only ever used to validate incoming keys and
        # to size `_histories`, never to decide whether to stop.
        self._statistics = statistic_names
        self._all_statistics = all_names
        self._combinator = combinator
        self._generations: list[int] = []
        self._histories: dict[str, list[float]] = {name: [] for name in all_names}
        # A trailing-window criterion is judged incrementally, in O(1) per
        # generation, rather than by re-summing the whole window each time
        # (`TrailingWindowTracker` returns the identical decision). Any other
        # criterion is asked against the full history as before.
        self._trackers: dict[str, TrailingWindowTracker] = (
            {name: criterion.tracker() for name in statistic_names}
            if isinstance(criterion, TrailingWindowCriterion)
            else {}
        )
        # The noise-adequacy gate (`_gated_stable`, below) needs a window
        # length and a tolerance, which only a `TrailingWindowCriterion`
        # carries — duck-typed via `getattr`, not `isinstance`, so a test
        # double exposing the same two attributes participates identically
        # (`test/convergence/test_tracker.py`'s own `_PlainCriterion`, which
        # this module has no reason to import). `None` for any other
        # criterion (`ConfidenceIntervalCriterion`, an across-replicate
        # concept with no single run's own trailing window to be noisy
        # about) — that criterion's own signal passes through ungated below.
        window = getattr(criterion, "window", None)
        tolerance = getattr(criterion, "tolerance", None)
        self._noise_window: int | None = (
            window
            if isinstance(window, int) and window >= MINIMUM_NOISE_CHECK_WINDOW
            else None
        )
        self._noise_tolerance: float | None = tolerance
        # Where the current evidence window began (a position in `name`'s
        # own history list), for whichever statistics `_gated_stable` is
        # actively growing one for; absent while the trend is not
        # currently stable. The window at which the *next* noise check is
        # due, doubling after each inadequate one — see `_gated_stable`'s
        # own docstring for why both exist. The statistics already judged
        # noise-adequate, whose verdict is cached rather than recomputed.
        self._noise_window_start: dict[str, int] = {}
        self._noise_next_check_length: dict[str, int] = {}
        self._noise_adequate: set[str] = set()
        self._last_window_statistics: dict[str, WindowStatistics] = {}
        # Each watched statistic's own (gated) stability verdict from the
        # most recent `record` call, in `_statistics` order, so the
        # statistics that actually passed on the stopping round can be
        # named afterward (`stable_statistics`).
        self._last_verdicts: tuple[bool, ...] = tuple(False for _ in statistic_names)
        self._outcome = ConvergenceOutcome(False, False, None, None)

    @property
    def generations(self) -> tuple[int, ...]:
        """Return recorded generations in order.

        The generation number recorded alongside each `record()` call,
        in the same order they were recorded — parallel to `history`
        (or each series in `histories`), so pairing up
        ``zip(monitor.generations, monitor.history)`` reconstructs
        exactly what was passed to `record` each round.
        """
        return tuple(self._generations)

    @property
    def history(self) -> tuple[float, ...]:
        """Return the primary (first-configured) statistic's recorded values.

        With one watched statistic — the ordinary case — this is that
        statistic's complete history. With several, it is only the first
        one named in ``statistics``; use ``histories`` for every statistic.
        """
        return tuple(self._histories[self._statistics[0]])

    @property
    def histories(self) -> Mapping[str, tuple[float, ...]]:
        """Return every recorded statistic's values, by name.

        Covers both ``statistics`` (the watched subset actually deciding
        convergence) and ``extra_statistics`` (recorded for display only,
        never gating the stop decision) — the two are indistinguishable
        from this property alone, by design; a caller that needs to know
        which is which already has that answer from its own configured
        ``statistics``/``extra_statistics``, not from this monitor.
        """
        return {name: tuple(values) for name, values in self._histories.items()}

    def outcome(self) -> ConvergenceOutcome:
        """Return the current terminal or running outcome.

        Safe to call at any time, including before the first `record`
        call (see `ConvergenceOutcome`'s own docstring for what the
        "still running" placeholder looks like) and any number of
        times after the monitor has stopped — unlike `record`, calling
        this again never raises and never changes anything.
        """
        return self._outcome

    def reason(self) -> StopReason | None:
        """Return the terminal reason, or ``None`` while running.

        A convenience for reading just `outcome().reason` without
        needing the rest of the outcome — used, for example, when only
        the human-readable stop reason is needed for a report.
        """
        return self._outcome.reason

    def record(
        self,
        generation: int,
        value: float | Mapping[str, float],
    ) -> ConvergenceOutcome:
        """Record one ordered observation and update the stop decision.

        Args:
            generation: Non-negative generation number.
            value: The watched statistic's finite value. A bare float is
                only accepted while watching exactly one statistic; with
                several, pass a mapping. The mapping need not cover every
                configured name: a statistic it omits simply is not
                appended to that statistic's own history this round —
                the caller's way of reporting "this statistic has no
                defined value for this round" without fabricating one or
                blocking the round's other, defined statistics. Every
                key the mapping *does* include, however, must name a
                configured statistic; an unrecognized name is far more
                likely a typo than an intentional omission, so it still
                raises.

        Returns:
            The updated outcome.

        Raises:
            RuntimeError: If called after the monitor already stopped.
            ValueError: If ``generation`` or ``value`` is invalid.
        """
        if self._outcome.stopped:
            raise RuntimeError("cannot record after convergence monitoring stopped")
        # `isinstance(generation, bool)` checked explicitly, first: `bool`
        # is a subclass of `int` in Python, so a bare `not isinstance(
        # generation, int)` alone would silently accept `True`/`False` as
        # generation `1`/`0` — the same `bool`-is-not-really-an-int
        # discipline every integer-shaped field elsewhere in this project
        # already applies (`fim.model.params._require_integer`, for one).
        # Neither check existed at all before this project's own multi-
        # model engine review, 2026-09-04 (`FIM-06`/finding Kimi-FIM-06):
        # a non-`int` `generation` reached `generation < 0` directly,
        # which raises a raw `TypeError` for anything not orderable
        # against `0` (a string, `None`, ...) instead of this function's
        # own documented `ValueError`.
        if isinstance(generation, bool) or not isinstance(generation, int):
            raise ValueError("generation must be a non-negative integer")
        if generation < 0:
            raise ValueError("generation must be non-negative")
        if self._generations and generation <= self._generations[-1]:
            raise ValueError("generations must be recorded in increasing order")

        values = self._resolve_values(value)
        for statistic, number in values.items():
            # Same reasoning as `generation`, above, for `number`: this
            # project's own multi-model engine review, 2026-09-04
            # (`FIM-06`), found a non-numeric `value` (or mapping entry)
            # reached `math.isfinite(number)` directly, which raises a
            # raw `TypeError` for anything `math.isfinite` cannot accept
            # at all (a string, `None`, ...) instead of this function's
            # own documented `ValueError`.
            if isinstance(number, bool) or not isinstance(number, int | float):
                raise ValueError(
                    f"convergence statistic {statistic!r} must be a real number"
                )
            if not math.isfinite(number):
                raise ValueError(f"convergence statistic {statistic!r} must be finite")

        self._generations.append(generation)
        for statistic, number in values.items():
            self._histories[statistic].append(number)
            if statistic in self._trackers:
                self._trackers[statistic].push(number)

        # Each watched statistic's own history is judged by the same
        # criterion, independently — one statistic's history says
        # nothing about another's — and `combinator` then decides
        # whether every one of those independent yes/no answers must
        # agree ("all") or just one of them needs to ("any"). See
        # `ConvergenceMonitor`'s own class docstring for why this is a
        # genuine no-op with only one watched statistic.
        #
        # A list, not a generator: `_gated_stable` is stateful (it anchors
        # each statistic's evidence window the first round its trend is
        # stable, and schedules that statistic's next noise check), so
        # every watched statistic must be judged every round. Handing a
        # generator to `all`/`any` would short-circuit — once one answer
        # decides the round, the statistics listed after it would never
        # be judged, their evidence windows anchored late (or never), and
        # the stop generation would depend on the order the statistics
        # happen to be listed in.
        per_statistic_stable = [
            self._gated_stable(
                name,
                self._trackers[name].is_stable()
                if name in self._trackers
                else self._criterion.is_stable(self._histories[name]),
            )
            for name in self._statistics
        ]
        self._last_verdicts = tuple(per_statistic_stable)
        is_stable = (
            all(per_statistic_stable)
            if self._combinator == "all"
            else any(per_statistic_stable)
        )
        # Guarded like `fim.engine._run_one`'s own per-generation loop
        # (`doc/fim-logging-design.md` §9): `record` is called once per
        # generation (or, for the replicate-batch monitor, once per
        # replicate) for the life of a run, so this is only formatted
        # when DEBUG is actually enabled. The terminal outcome itself is
        # logged by each caller (`fim.engine`), not here, to avoid
        # logging the same "why did this stop" fact twice.
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "convergence: generation=%d combinator=%s stable=%s",
                generation,
                self._combinator,
                is_stable,
            )
        # Convergence is checked before the hard cap: if a statistic
        # happens to stabilize on the very generation the cap would
        # also have fired, the run is reported as having converged,
        # not as having merely run out of generations — the more
        # informative and more accurate of the two true facts about
        # what happened.
        if is_stable:
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
        """Return the watched statistics that passed on the most recent round.

        Every watched statistic is judged every round (`record`), so this
        is complete: under ``combinator="all"`` a converged run names
        every watched statistic; under ``"any"`` it names only the ones
        that had actually passed when the run stopped — one or more. A
        statistic that passed on an earlier round and is cached as
        noise-adequate (`_gated_stable`) still counts. Before any
        `record` call, or on a round where nothing passed (every round of
        a run that hit its cap), it is empty. `extra_statistics` are never
        judged and so never appear.

        Returns:
            The passing statistic names, in configured (``statistics``)
            order.
        """
        return tuple(
            name
            for name, stable in zip(self._statistics, self._last_verdicts, strict=True)
            if stable
        )

    def should_stop(self) -> bool:
        """Return whether statistical convergence or the hard cap fired.

        A convenience for the run loop's own stop check — equivalent to
        `outcome().stopped`, without needing `converged`/`reason` too.
        """
        return self._outcome.stopped

    def window_statistics(self, name: str) -> WindowStatistics | None:
        """Return the most recent noise-adequacy check computed for `name`.

        Set only as a side effect of `_gated_stable` actually running the
        expensive check (below) — for a statistic that reached
        noise-adequate, at the generation it first did so, after which
        its verdict is cached and this value no longer changes. For the
        statistic(s) that decided the stop, that is the stop generation
        itself (`record`'s own `is_stable` branch fires in the same call
        that just set this); under ``combinator="all"``, a statistic that
        passed before the others keeps the window it passed with while
        the run waits on the rest (`_gated_stable`'s own docstring). A
        statistic that never reached noise-adequate holds its most recent,
        inadequate check instead.

        A caller building a final report reads this once, after the run
        has stopped, to say not just *that* a statistic converged but how
        precisely its own (possibly grown well past the criterion's own
        configured `window`) trailing evidence window was actually known
        — `WindowStatistics.window` carries however long that evidence
        window actually ended up being, not the configured one.

        Args:
            name: A configured statistic name (watched or extra).

        Returns:
            `None` when no check has run yet for `name` — the monitor never
            reached a trend-stable candidate at all (most commonly: the run
            hit `max_generations` while `name`'s own trailing window was
            still visibly trending), `name`'s own criterion is not a
            `TrailingWindowCriterion`-shaped one, or its configured window
            is shorter than `fim.convergence.window_statistics.
            MINIMUM_NOISE_CHECK_WINDOW`.
        """
        return self._last_window_statistics.get(name)

    def _gated_stable(self, name: str, trend_stable: bool) -> bool:
        """Confirm a trend-stable signal against `name`'s own noise floor.

        `trend_stable` (the criterion's or tracker's own O(1) half-window
        judgment) answers "has this stopped trending" — a real, useful, but
        noise-blind question: a single-locus statistic that is still
        wobbling by drift alone, with no trend left at all, will still
        satisfy it the instant a wobble happens to land the two halves
        close together, whether or not that means anything. This method
        adds the second half of the answer: once the trend has flattened,
        it keeps accumulating that same evidence window forward — not the
        fixed `window` length the trend check itself uses — until `fim.
        convergence.window_statistics.window_statistics`'s own
        correlation-corrected standard error of the (growing) window's
        mean is at most half the configured tolerance
        (`WindowStatistics.noise_adequate`), or the run's own generation
        cap arrives first.

        A fixed window multiple cannot serve every model: measured
        directly (`20260927-...-noise-aware-convergence-design.md`,
        `selby/restricted`, this design's own follow-up note), one
        single-locus scenario needed a window some 500 times longer than
        the model's own derived one to reach noise adequacy, while a
        30-locus scenario already needed on the derived window exactly —
        no single constant serves both without wasting enormous time on
        the second to (barely) help the first. Growing the window instead
        needs no such guess: it keeps exactly as much evidence as the
        statistic's own noise demands, whatever that turns out to be, and
        cannot be slower than the old fixed-window rule when a single
        window's worth is already enough (the very first check still
        happens at the same generation as before).

        Throttled, not run every generation: `window_statistics` is
        `O(window)`, and `TrailingWindowTracker`'s whole reason to exist
        is keeping the per-generation cost `O(1)` for windows that can
        reach millions of generations. `_noise_next_check_length[name]`
        starts at `window` and doubles after each inadequate check — the
        standard amortized-growth doubling discipline, the same one a
        growing array uses to keep total copying `O(final size)` rather
        than `O(size^2)`: `k` doublings of an eventual length `L` cost
        `O(L)` in total, not `O(L log L)`, let alone one `O(window)` check
        every single generation on the way there. The one accuracy cost:
        with several statistics and `combinator="all"`, a statistic that
        reaches noise-adequate before the others simply stops growing and
        keeps returning that one cached verdict while waiting on the
        rest, rather than continuing to accumulate evidence it no longer
        needs.

        The evidence window's own start, once first anchored, is **not**
        reset just because `trend_stable` next reads `False` for one
        generation — a real, load-bearing decision, not an oversight: a
        genuinely stationary but noisy statistic's own half-window
        trend check itself wobbles in and out of `True` by chance (the
        same noise this whole gate exists to average past), and a design
        that discarded all accumulated evidence on every such flicker was
        tried first and confirmed, directly, to never grow past the base
        `window` at all on a real 200,000-generation run — the flicker
        happens more often than the doubling interval, so growth never
        survives to the next check. Anchoring once and never resetting
        does still give up something: if the anchor point turns out to
        have landed slightly before the transient genuinely finished, the
        growing window's mean carries a shrinking bias from those first
        few, still-transient values, diluted by an ever-larger share of
        later, settled ones as the window grows — exactly the same
        "average away the bad estimate" arithmetic this whole gate
        already relies on for noise, self-correcting rather than
        permanent. A genuinely still-drifting statistic (not just noisy)
        continues to resist `noise_adequate` regardless, since a real
        drift keeps inflating the window's own measured autocorrelation
        as fast as new points are added, matching this method's own
        already-established "never falsely stop, worst case run longer"
        guarantee.

        Args:
            name: The statistic whose trend signal this confirms.
            trend_stable: `name`'s own fast half-window judgment this round.

        Returns:
            `trend_stable` unchanged when `name` has no window/tolerance to
            check against (`self._noise_window` is `None`) or its history
            has not yet reached that window length; otherwise, once an
            evidence window has been anchored, the (possibly cached)
            noise-adequacy verdict alone — `trend_stable` no longer gates
            the answer from that point on (see above).
        """
        if self._noise_window is None:
            return trend_stable
        if name in self._noise_adequate:
            # Already judged noise-adequate on an earlier round: the cached
            # verdict stands (see above). No further `O(window)` check, and
            # the evidence window `window_statistics(name)` reports stops
            # growing here.
            return True
        history = self._histories[name]
        start = self._noise_window_start.get(name)
        if start is None:
            if not trend_stable or len(history) < self._noise_window:
                return trend_stable
            # The trend has just become stable (or this is the first
            # `record()` call reached with it already stable) -- the
            # evidence window starts at exactly the `window`-length slice
            # that made the trend check pass, and will grow from here,
            # anchored for the rest of this run (see above).
            start = len(history) - self._noise_window
            self._noise_window_start[name] = start
            self._noise_next_check_length[name] = self._noise_window
        current_length = len(history) - start
        if current_length < self._noise_next_check_length[name]:
            # Not due for a check yet -- the window has not doubled since
            # the last (inadequate) one, so it is assumed still
            # inadequate rather than paying for another `O(window)` check
            # that would almost certainly repeat the same verdict.
            return False
        stats = window_statistics(history[start:])
        self._last_window_statistics[name] = stats
        assert self._noise_tolerance is not None  # set alongside self._noise_window
        if stats.noise_adequate(self._noise_tolerance):
            self._noise_adequate.add(name)
            return True
        self._noise_next_check_length[name] = current_length * 2
        return False

    def _resolve_values(
        self,
        value: float | Mapping[str, float],
    ) -> dict[str, float]:
        """Normalize a bare float or a per-statistic mapping into full form.

        A mapping may be a partial or even empty subset of the configured
        statistics (see `record`'s ``value`` argument) — every key it
        includes is validated against the configured names, but no key is
        required to be present. A bare float is only legal while watching
        exactly one statistic, in which case it is treated as that one
        statistic's own value for this round — the shorthand every
        single-statistic caller (the ordinary case) uses instead of
        writing out a one-entry mapping every time.
        """
        if isinstance(value, Mapping):
            unknown = set(value) - set(self._all_statistics)
            if unknown:
                expected = ", ".join(sorted(self._all_statistics))
                names = ", ".join(sorted(unknown))
                raise ValueError(
                    f"record() values named unconfigured statistic(s) "
                    f"{names}; configured: {expected}"
                )
            return dict(value)
        if len(self._statistics) != 1:
            raise ValueError(
                "record() requires a mapping of statistic name to value "
                "while watching several statistics"
            )
        return {self._statistics[0]: value}
