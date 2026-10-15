"""Result records and shared aliases for the engine."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeAlias, TypedDict

from fim.model.params import EngineBackend as ModelEngineBackend
from fim.model.params import Jit as ModelJit
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.persistence.manifest import RunManifest
from fim.persistence.store import TrajectoryStore

Clock: TypeAlias = Callable[[], datetime]


class FinalReport(TypedDict):
    """Final run-level scalar report.

    The one-paragraph summary of a finished run: how differentiated the
    simulated population had become when the run stopped, plus the
    bookkeeping needed to explain *why* it stopped there. `report_for_
    state` builds one of these from whatever generation a run actually
    ended on (the last generation reached, whether that was because the
    statistics stabilized or because the hard generation cap was hit);
    `RunResult.report` carries it as part of a completed run's full
    result. This is a `TypedDict` (a plain Python dictionary, but one
    whose exact set of keys and each key's value type is declared up
    front) rather than a class with named attributes, because it is
    written to disk as JSON verbatim (see
    `fim.persistence.report.write_report`) — a dictionary already has
    the shape JSON needs, with nothing extra to convert.

    Fields:
        run_id: This run's own identifier — either supplied by the
            caller, or (see `deterministic_run_id`) computed from the
            run's own parameters, so the exact same configuration always
            gets the exact same id.
        generation: The generation number the run actually stopped at —
            not necessarily `SimulationParams.max_generations`; see
            `converged` and `reason` below for why it may have stopped
            earlier or later.
        converged: Whether the run stopped because its watched
            statistic(s) settled down on their own (`True`), or because
            it hit the hard generation cap without ever settling
            (`False`) — see this module's own docstring, above, for
            what "settled down" (convergence) means here.
        converged_on: Which watched statistic(s) had actually settled
            when the run stopped — not merely which were watched (that
            is `SimulationParams.convergence_statistic`, recorded in the
            manifest). The configured statistic's own name when a single
            statistic is watched; a list when several are, holding every
            one under `convergence_combinator: all` but only the ones
            that had passed under `any`. `None` whenever `converged` is
            `False`: a run that hit its cap converged on nothing, and a
            state with no run behind it (a preview, a progress tick, an
            earlier generation re-analyzed) has no stop to describe.
        reason: A short, human-readable phrase naming the specific
            reason the run stopped (e.g. "statistic converged" or "hit
            the cap") — meant to be read directly by a person looking at
            a results table, not parsed by code.
        G_ST, D, E_ST, K_ST, H_S, H_T, H_ST: The core differentiation/
            heterozygosity measures this project reports (see this
            module's own docstring for what each name means, in outline,
            and the linked
            [differentiation-measures guide](../../doc/jost-differentiation-measures.md)
            for the full explanation), each averaged across every
            genetic locus the run tracked (see `report_for_state`).
            `G_ST` alone can be `None`: it is undefined at a locus with
            no genetic variation left to measure (see
            `mean_g_st_across_loci`), and if *every* tracked locus is in
            that state, there is no defined value left to average at
            all. Every other field is always a real number.
        Gs, Gd: The within- and between-deme gene identities (Ryman &
            Leimar's `J_0`/`J_1`) `H_S`/`H_T`/`G_ST`/`D` are themselves
            derived from — `fim.statistics.differentiation.gs`/`gd`'s
            own docstrings have the exact formulas and `fim`'s own
            with-replacement identity convention. Added by this
            project's own Ryman & Leimar remediation, `R4`
            (`dev/doc/apps/selby/jost-finite-island-model/20260903-
            claude-opus-5-gene-identity-recursion-fim-implications.md`
            §9). Both linear in `H_S`/`H_T`, so — unlike `G_ST`/`D` —
            averaging across loci or replicates never needs a ratio-of-
            means-versus-mean-of-ratios decision; there is only one
            answer.
        A_CGD, Delta, MI: Literature-derived supplemental statistics
            added for Phase 4 GUI visual interpretation: Caballero-
            Garcia-Dorado allelic distance, Gregorius delta, and Sherwin
            mutual information.
        D_m, R_ST, G_ST_NEI_LOG, G_ST_HEDRICK, F_ST: Nei's (1973) mean
            pairwise between-deme diversity and its ratio to `H_S`, Nei's
            logarithmic and Hedrick's standardized `G'_ST`, and coancestry
            `F_ST` (`fim.statistics.differentiation.
            derived_differentiation`), each one ratio of the pooled
            `H_S`/`H_T` (whatever `locus_aggregation` says, which governs
            `D` and `G_ST` only). ``None`` where undefined.
        NEI_D_ALL_GEO, NEI_D_ALL_GEO_LOCUS_MEAN, NEI_D_ALL_ARITH,
            NEI_D_ALL_ARITH_LOCUS_MEAN: Nei's genetic distance across all
            demes, ``-ln(J_between / mean(J_k))``, with the geometric
            (Nei 1972) or arithmetic (Jost, L. (2026) private
            communication) mean of the within-deme identities, loci
            combined by Nei's pooled rule or by averaging per-locus
            distances (`fim.statistics.genetic_distance`). ``None`` when
            infinite (no allele shared between any pair of demes), since
            the report is written as strict JSON. The geometric forms can
            be negative; see `nei_all_demes_identity`.
        NEI_I_ALL_GEO, NEI_I_ALL_GEO_LOCUS_MEAN, NEI_I_ALL_ARITH,
            NEI_I_ALL_ARITH_LOCUS_MEAN: The matching identities ``I``
            (``D = -ln(I)``). Always finite, so they are the lossless
            record when a distance is ``None``. The arithmetic pooled
            identity equals ``Gd / Gs``.
        window_statistics: How precisely each recorded statistic's own
            trailing-window mean was actually known at the generation this
            run stopped at — `{name: {"mean", "standard_error",
            "standard_deviation", "effective_sample_size", "window",
            "noise_adequate"}}`, one entry per statistic `report_for_state`
            was given a monitor history for (`fim.convergence.window_
            statistics.WindowStatistics`, `window_statistics_payload`).
            Each entry also carries `selected_form` (`"mean_of_values"` or
            `"value_of_means"`, the form the headline `mean` and the stop
            use), `undefined_generations` (window generations in which the
            statistic had no value, dropped by the first form), and
            `mean_of_values`/`value_of_means`, each `{"mean",
            "standard_error"}` — `value_of_means` only for `D` and `G_ST`,
            the statistics that are functions of `H_S` and `H_T`. Every entry
            describes the one evidence window all statistics share:
            `window_start`, `window_end`, `burn_in`, the `minimum_ess` floor
            and `target_standard_error` the window was judged against, and
            `geweke_z` (start of the window against its end; a large
            absolute value means the burn-in may have been too short).
            Empty for a state with no monitored run behind it at all (a GUI
            preview, a re-analysis) — this is *not* the same thing as `D`/
            `G_ST`/etc. above, which are always this state's own point
            values, computed directly from its allele frequencies; this
            field is instead the *history leading up to* that state, the
            answer to "how much can this run's own last window of noise be
            trusted," which the point value alone cannot say. `"statistic
            converged"` (see `reason`, above) means the watched statistic's
            own `noise_adequate` was `True` at the stopping generation
            (`fim.convergence.monitor.ConvergenceMonitor`'s own noise-
            adequacy gate, `20260927-claude-sonnet-5-noise-aware-
            convergence-design.md`, `selby/restricted`); `converged=False`
            (the generation cap was hit first) can still show a real,
            informative `standard_error` here for whichever statistic was
            watched — "capped, and here is how far off the estimate still
            is" — even though `noise_adequate` for it is `False`.
    """

    run_id: str
    generation: int
    converged: bool
    converged_on: str | list[str] | None
    reason: str
    G_ST: float | None
    D: float
    E_ST: float
    K_ST: float
    H_S: float
    H_T: float
    H_ST: float
    A_CGD: float
    Delta: float
    MI: float
    Gs: float
    Gd: float
    D_m: float
    R_ST: float | None
    G_ST_NEI_LOG: float | None
    G_ST_HEDRICK: float | None
    F_ST: float | None
    NEI_D_ALL_GEO: float | None
    NEI_D_ALL_GEO_LOCUS_MEAN: float | None
    NEI_D_ALL_ARITH: float | None
    NEI_D_ALL_ARITH_LOCUS_MEAN: float | None
    NEI_I_ALL_GEO: float
    NEI_I_ALL_GEO_LOCUS_MEAN: float
    NEI_I_ALL_ARITH: float
    NEI_I_ALL_ARITH_LOCUS_MEAN: float
    window_statistics: dict[str, dict[str, Any]]


@dataclass(frozen=True, slots=True)
class RunResult:
    """Everything one finished simulation run produced.

    Whatever calls `simulate_params()` gets back one of these per run (one, or a
    tuple of them for a multi-replicate batch — see `fim`'s own
    docstring). It bundles four different things a caller might want,
    each aimed at a different next step:

    Fields:
        run_id: This run's own identifier — the same value as
            `report["run_id"]`, repeated here directly so a caller never
            has to reach into `report` just to find out which run this
            was.
        params: The exact, fully validated configuration this run
            actually used (population size, migration rate, mutation
            rate, and everything else) — kept alongside the result so
            the run remains self-describing without a caller needing to
            have hung onto the original configuration separately.
        final_state: The complete simulated population as it stood at
            the very last generation — every deme's allele frequencies,
            at every locus. This is what a scatter plot of the final
            state (`fim.viz.scatter`) is drawn from.
        report: The `FinalReport` summarizing that final state into the
            handful of named numbers a person actually wants to read —
            see `FinalReport`'s own docstring.
        convergence_generations: Every generation number at which the
            convergence check actually recorded an observation (see
            `run_one`) — together with `convergence_history`/
            `convergence_histories` below, this is the run's own
            "trajectory," letting a caller plot how a watched statistic
            moved over time on its way to its final value, not just what
            that final value turned out to be.
        convergence_history: The single watched statistic's own value at
            each of those generations, in the common case of watching
            just one statistic — see `SimulationParams.
            convergence_statistic`.
        convergence_histories: The same per-generation history as
            `convergence_history`, but keyed by statistic name — no
            longer only the watched subset. `D`/`G_ST`/`H_S`/`H_T` are
            always present (`fim.engine.ALWAYS_TRACKED_STATISTICS`),
            regardless of what `SimulationParams.convergence_statistic`
            actually watches, since all four cost nothing extra to
            compute or aggregate either way; `E_ST`/`K_ST` are present
            when either is actually watched, or when
            `SimulationParams.track_expensive_statistics` opts into
            paying their own real, per-generation cost for a display
            value (that field's own docstring has the measured
            tradeoff). Only the watched subset ever influenced *why* the
            run stopped (see `FinalReport.converged_on`) — every other
            name here is present for a caller (a GUI trajectory panel,
            design doc §6.2) that wants to plot more than just the
            statistic that happened to be watched, never because it
            factored into the stop decision itself. A run's own
            `manifest.software_version` predating this change has
            neither guarantee: its own persisted trajectory (were one
            read back some other way) genuinely has no history at all
            for a statistic that was not watched at the time, which is
            not a defect to guard against, only an honest reflection of
            what that older run actually recorded.
        manifest: The `RunManifest` recording this run's own bookkeeping
            metadata — when it started and ended, what software version
            produced it, and (once written to disk) the checksums
            proving the persisted files have not been altered since.
            See `fim.persistence.manifest`.
        store: The `TrajectoryStore` this run wrote its per-generation
            state to as it went — kept here so a caller that wants to
            read that trajectory back later (to animate it, or re-
            analyze an earlier generation) already has a handle to it,
            without needing to separately track down which store this
            particular run used.
        equilibrium_store: Where an equilibrium-split run streamed its
            ancestral phase (`fim.model.initial.
            EquilibriumSplitInitialCondition`): every generation of the
            one-deme ancestral population, in `store`'s own row schema,
            read back with `equilibrium_store.read(run_id)`. Numbered by
            the ancestral phase's own counter, `0` through
            `manifest.equilibrium_generation_count`; the split demes'
            generation zero follows its last generation. Beside
            `trajectory.tlog` as `equilibrium_trajectory.tlog` when
            `store` is a file (`fim.persistence.store.
            equilibrium_store_for`). `None` for every other initial
            condition.
    """

    run_id: str
    params: SimulationParams
    final_state: ModelState
    report: FinalReport
    convergence_generations: tuple[int, ...]
    convergence_history: tuple[float, ...]
    convergence_histories: Mapping[str, tuple[float, ...]]
    manifest: RunManifest
    store: TrajectoryStore
    equilibrium_store: TrajectoryStore | None = None


SimulationOutput: TypeAlias = RunResult | tuple[RunResult, ...]


# `EngineBackendChoice`/`JitOption`/`DEFAULT_AUTO_VECTOR_MIN_D` are
# defined in `fim.model.params` (as `EngineBackend`/`Jit`/the same
# constant name), not here, and re-exported under these names for every
# existing importer — `SimulationParams.engine_backend`/`.jit`/
# `.auto_vector_min_d` are real fields there now (config-file/CLI
# exposed), so their defaults belong beside every other `SimulationParams`
# field default, not duplicated here. This module still owns everything
# about what each choice *does*; `fim.model.params` only owns the closed
# set of legal values and their defaults — matching this project's own
# one-directional dependency rule (the engine depends on the model, the
# model depends on nothing in the engine).
EngineBackendChoice = ModelEngineBackend

JitOption = ModelJit


def format_timestamp(value: datetime) -> str:
    """Return an unambiguous UTC ISO-8601 timestamp.

    Used for the `started_at`/`ended_at` fields `run_one` records in a
    run's own manifest. ISO-8601 (`2026-08-26T12:00:00Z`) is a widely
    used, sortable, unambiguous way to write a specific moment in time,
    including its timezone — writing every timestamp in UTC (Coordinated
    Universal Time, the "Z" suffix) specifically means a run started on
    one machine, in one timezone, is directly comparable to a run
    started on a different machine in a different timezone, with no
    conversion needed and no risk of the two being silently
    misinterpreted as if they shared a timezone when they do not.
    """
    if value.tzinfo is None:
        raise ValueError("manifest clock must return a timezone-aware datetime")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def utc_now() -> datetime:
    """Return the current UTC time for manifest metadata only.

    `fim`'s own default for its `clock` argument (see that function's
    docstring for why a caller — almost always just a test — would ever
    supply a different one). Wrapped in its own named function, rather
    than passing `datetime.now` directly with `UTC` bound some other
    way, specifically so this remains a plain, ordinary, picklable
    module-level function — required whenever `max_workers` is set (see
    `require_picklable`), since this is exactly the kind of value that
    check exists to accept.
    """
    return datetime.now(UTC)
