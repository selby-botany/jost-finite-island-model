"""Validated, replayable simulation configuration.

`SimulationParams`, below, is the single object that fully describes
one finite-island simulation run — every number a config file (or the
CLI's own flags) can set, gathered together, checked for validity once
at construction time, and then held immutable for the rest of the
run. "Replayable" means that the exact same `SimulationParams` (in
particular, the same `seed`) always reproduces the exact same run bit
for bit — this is what makes it possible to re-run, share, or audit a
specific past result, rather than every run being a one-off that can
never be reconstructed.

Most of this module's private helper functions (the many small
``_parse_*``/``_normalize_*`` functions below `SimulationParams`
itself) exist to support `from_mapping`, which is what actually turns
a loosely typed YAML/JSON config file — where every value could in
principle be the wrong type, missing, or out of range — into a fully
validated `SimulationParams`. See `doc/configuration.md` for what each
configuration field means and its accepted range, in plain language,
independent of this file's own more code-oriented documentation.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final, Literal, cast

from fim.model.allele import AlleleId
from fim.model.identifiers import parse_integer_identifier
from fim.model.locus import LocusSpec, finite_allele_capacity
from fim.model.topology import (
    Topology,
    dense_matrix_from_neighbors,
    stepping_stone_neighbors,
)

PopulationSize = int | tuple[int, ...]
Migration = float | tuple[tuple[float, ...], ...]
MutationRate = float | tuple[float, ...]
DemeWeighting = Literal["equal", "size"]
LocusAggregation = Literal["ratio_of_means", "mean_of_ratios"]
ConvergenceStatistic = str | tuple[str, ...]
ConvergenceCombinator = Literal["any", "all"]
MigrantSampling = Literal["continuous", "stochastic"]
MutationModel = Literal["infinite_alleles", "finite_alleles"]
EngineBackend = Literal["lineal", "generational", "generational-vector", "auto"]
Jit = Literal["off", "numba"]
InitialFrequencies = tuple[tuple[Mapping[AlleleId, float], ...], ...]

DEFAULT_LOCUS_LENGTH: Final = 200
DEFAULT_AUTO_VECTOR_MIN_D: Final = 35
"""`"auto"`'s own default deme-count cutover, below which it never picks
`"generational-vector"` even when the config is otherwise eligible for it.

Lives here, not in `fim.engine`, because it is a `SimulationParams` field
default like any other (`convergence_window`'s own `50`, `max_generations`'s
own `10_000`) — `fim.engine` imports it from here rather than the other way
around, matching this project's own one-directional dependency rule (the
engine depends on the model; the model depends on nothing in the engine).

Measured, not guessed — the generation-first design's own Stage 4/vector
design's own Stage V3 deme-axis sweep found Backend V crosses over from
slower than Backend L to clearly faster somewhere between `d=30` and
`d=40` on the primary benchmarking machine. **This default has not been
re-measured since a later correctness fix
(`20260901-claude-sonnet-5-fim-engine-backend-factory-design.md` §10
Stage F8) changed the underlying performance picture materially — a
2026-09-02 re-measurement (`dev/bin/benchmark-engines`) found
`"generational-vector"` already ahead at `d=4`, the smallest value
tested, not just past this threshold.** Kept at `35` rather than changed
alongside that finding: altering a shipped default needs its own
deliberate confirmation, not a silent edit. `auto_vector_min_d` stays a
caller-supplied `SimulationParams` field for exactly this kind of
drift — see `dev/bin/benchmark-engines --sweep d` to re-characterize it
on any given machine.
"""
DEFAULT_AUTO_VECTOR_MAX_CAPACITY: Final = 1024
"""`"auto"`'s own default per-locus capacity ceiling for `"generational-
vector"` — above it, `"auto"` picks `"generational"` instead, regardless
of `d`/`auto_vector_min_d`.

Closes a real, previously-unaddressed gap: `"auto"`'s own resolution
used to read `params.d` alone, never any locus's own capacity
(`20260901-claude-sonnet-5-fim-engine-backend-factory-design.md` §10
item 10b — "a large-`d`, large-capacity config could pick the wrong
engine"). Measured, not guessed, the same way `auto_vector_min_d`
itself was: that same document's own loci-length sweep found
`"generational-vector"` winning through capacity `1024` (locus length
`2`-`5`) and losing to `"generational"` + `jit="numba"` at capacity
`4096` (length `6`, `71.3s` vs `92.4s`) — the array-native path touches
every cell of a locus's own `(d, capacity)` grid every generation
regardless of how much of it is actually occupied, where the dict-based
backends only ever touch what is present. `1024`, not a value strictly
between the two, because a real capacity is always `4 ** length` for
some integer `length` — there is no config that could ever land between
`1024` and `4096`, so the boundary sits exactly at the last *tested,
winning* value rather than an interpolated one nothing could reach
anyway. Applies to the largest capacity across every locus in `params.
loci` — one large-capacity locus already pays this cost even if every
other locus in the same run is small, the same "one disqualifying
property anywhere disqualifies the whole choice" logic `mutation_model`/
`migrant_sampling` eligibility already uses. Not yet re-measured on
different hardware, and not yet re-measured against the same-day
`ThreadedAdvancer`/`migrate_vectorized` fixes that already made
`auto_vector_min_d`'s own default doubly stale — see that constant's
own docstring for the precedent this one inherits, and `dev/bin/
benchmark-engines --sweep loci-length` to re-characterize it.
"""
DEFAULT_N_REPLICATES: Final = 200
"""How many independently seeded replicates a run tries by default.

Not `1` — the most useful ordinary use of this tool is a measurement
*with* a confidence interval (`replicate_tolerance`, below), not a
single point estimate, so that is what an unconfigured run now does by
default: run up to `DEFAULT_N_REPLICATES` replicates, stopping early
once `DEFAULT_REPLICATE_TOLERANCE` is reached. `200` is a generous cap,
not an expectation of always reaching it — chosen to match this
project's own worked examples and test scenarios that already use a
comparable count for a real confidence interval, giving the adaptive
stop (`replicate_minimum` onward) real room to tighten before the cap
would ever bind. A caller who wants the old single-run behavior back
sets `n_replicates: 1` explicitly, same as always; nothing about what an
explicit `n_replicates` means has changed, only what an *absent* one now
means.
"""
DEFAULT_REPLICATE_TOLERANCE: Final = 0.01
"""Default early-stopping half-width for a replicate batch.

Matches `convergence_tolerance`'s own default (`0.01`) deliberately —
the same tightness applied one layer up, to the across-replicate mean
instead of the within-run trailing window. Paired with
`DEFAULT_N_REPLICATES` above: together they make an unconfigured run
compute a real confidence interval by default rather than a single,
uncertainty-free-looking point estimate.
"""

PARAMETER_DEFAULTS: Final[dict[str, object]] = {
    "n_loci": 1,
    "locus_lengths": DEFAULT_LOCUS_LENGTH,
    "initial_allele_count": 2,
    "initial_concentration": 1.0,
    "deme_weighting": "size",
    "locus_aggregation": "ratio_of_means",
    "convergence_statistic": "D",
    "convergence_combinator": "all",
    "convergence_window": 50,
    "convergence_tolerance": 0.01,
    "max_generations": 10_000,
    "n_replicates": DEFAULT_N_REPLICATES,
    "replicate_tolerance": DEFAULT_REPLICATE_TOLERANCE,
    "replicate_minimum": 10,
    "replicate_confidence": 0.95,
    "migrant_sampling": "continuous",
    "mutation_model": "infinite_alleles",
    "engine_backend": "lineal",
    "jit": "off",
    "auto_vector_min_d": DEFAULT_AUTO_VECTOR_MIN_D,
    "auto_vector_max_capacity": DEFAULT_AUTO_VECTOR_MAX_CAPACITY,
    "max_concurrent_replicates": None,
}

_CONFIG_KEYS: Final = frozenset(
    {
        "N",
        "d",
        "m",
        "mu",
        "mu_b",
        "seed",
        "loci",
        "n_loci",
        "locus_lengths",
        "initial_allele_count",
        "initial_concentration",
        "deme_weighting",
        "locus_aggregation",
        "convergence_statistic",
        "convergence_combinator",
        "convergence_window",
        "convergence_tolerance",
        "max_generations",
        "n_replicates",
        "replicate_tolerance",
        "replicate_minimum",
        "replicate_confidence",
        "engine_backend",
        "jit",
        "auto_vector_min_d",
        "auto_vector_max_capacity",
        "max_concurrent_replicates",
        "migrant_sampling",
        "mutation_model",
        "p_0",
    }
)

_CONVERGENCE_STATISTICS: Final = frozenset(
    {"D", "G_ST", "E_ST", "K_ST", "H_S", "H_T", "H_ST"}
)
"""Every statistic name `convergence_statistic` may watch.

`H_ST` was missing here until this project's own multi-model engine
review, 2026-09-04 (`FIM-51`/finding Gemini M-03/finding Grok P3-1):
`fim.engine.FinalReport`/`replicate_summary` already report `H_ST`
alongside every other differentiation measure, but a caller could not
actually *watch* it for convergence — the one statistic reportable but
not watchable, an inconsistency with no principled reason behind it.
`fim.engine._report_statistic` is this set's own convergence-time
counterpart; keep both in sync.
"""


@dataclass(frozen=True, slots=True)
class SimulationParams:
    """Store all values needed to reproduce a finite-island-model run.

    This is an immutable (``frozen=True``) dataclass: once constructed,
    none of its fields can be reassigned, so a `SimulationParams` handed
    to `fim.engine`'s run loop is guaranteed to describe the exact same
    run throughout, with no risk of some other part of the code
    changing a setting partway through. `__post_init__`, below, is
    where every field actually gets checked for validity and, where
    needed, expanded into its full internal shape (for example, a
    single shared `N` value becomes one value per deme) — so a
    `SimulationParams` that exists at all is guaranteed already valid
    everywhere else it is used.

    See `doc/configuration.md` for a plain-language explanation of
    every field below, including its default value and accepted range
    — the Args section here documents the same fields from this
    project's own code, more tersely and with cross-references to the
    functions that actually use each one.

    Args:
        N: Gene-copy count shared by all demes, or one count per deme.
        m: Symmetric migration rate, or a row-stochastic migration matrix.
        mu: Per-copy mutation probability per generation — shared by every
            locus, or one rate per locus. `SimulationParams.from_mapping`
            can derive this from a single per-base rate instead (`mu_b`);
            `mu` itself always holds the resolved per-locus probability.
        d: Number of demes.
        seed: Required PCG64 seed. Must be non-negative — NumPy's PCG64
            rejects a negative seed with no equivalent upper bound (its
            `SeedSequence` hashes an arbitrarily large non-negative
            integer into a fixed-size entropy pool rather than rejecting
            it), so this is the whole legal range. A batch replicate's
            seed is `seed + replicate_index`, which is therefore also
            always non-negative and always in range: see the
            `_require_integer` call site for why no separate bound on
            that derived value is needed.
        loci: Nonempty ordered locus descriptions.
        initial_allele_count: Founding allele count per locus.
        initial_concentration: Symmetric Dirichlet concentration.
        deme_weighting: Weighting used by statistics that support it.
        locus_aggregation: How `D` and `G_ST` combine across loci in
            `fim.engine.report_for_state`'s own final report —
            `"ratio_of_means"` (the default): average `H_S`/`H_T` across
            loci first, then compute one `D`/`G_ST` from those pooled
            values, matching what the exact gene-identity recursion
            predicts and avoiding the small-denominator instability a
            per-locus ratio can have. `"mean_of_ratios"`: compute `D`/
            `G_ST` at each locus independently, then average those —
            this project's own original behavior, kept available for
            comparability with literature or prior analyses that used
            it, not because it is the better estimator: measured against
            the exact gene-identity recursion at this project's own
            reference scale, `"ratio_of_means"` landed within 0.25% of
            the recursion's own prediction where `"mean_of_ratios"` was
            off by 1.88% (CHANGELOG.md's own entry for this change has
            the full comparison). Every other
            reported statistic (`H_S`, `H_T`, `H_ST`, `E_ST`, `K_ST`) is
            unaffected — each is already a linear mean across loci, with
            no such ambiguity to resolve.
        convergence_statistic: One statistic, or several, watched by the
            convergence monitor.
        convergence_combinator: How several watched statistics combine —
            "all" (every one stable) or "any" (at least one stable).
            A single statistic makes this a no-op special case.
        convergence_window: Trailing stability-window length.
        convergence_tolerance: Maximum half-window mean difference.
        max_generations: Hard generation safety cap.
        n_replicates: Number of independently seeded runs — the hard cap
            a replicate batch runs up to. Defaults to
            `DEFAULT_N_REPLICATES` (`200`), not `1`: the ordinary useful
            result from this tool is a measurement with a confidence
            interval, not an uncertainty-free-looking single point, so an
            unconfigured run now behaves that way by default. Set to `1`
            explicitly for the old single-run behavior.
        replicate_tolerance: Early-stopping half-width, in the same units
            as each watched `convergence_statistic`. Defaults to
            `DEFAULT_REPLICATE_TOLERANCE` (`0.01`, matching
            `convergence_tolerance`'s own default) — an unconfigured run
            stops as soon as every watched statistic's across-replicate
            Student's-t confidence interval has tightened to at most this
            half-width (per `convergence_combinator`, exactly like
            within-run convergence), or `n_replicates` is reached,
            whichever comes first. Set explicitly to `None` (or, in a
            YAML/JSON config, simply omitted alongside `n_replicates: 1`)
            to run a fixed count in full with no adaptive stop.
        replicate_minimum: Fewest replicates before tightness is even
            checked, guarding against a lucky-early-tight fluke — the
            replicate-layer analog of `convergence_window`. Only
            meaningful when `replicate_tolerance` is set; silently
            clamped down to `n_replicates` if given larger, rather than
            rejected (`__post_init__`'s own comment has the reasoning).
        replicate_confidence: Two-tailed confidence level for
            `replicate_tolerance`'s interval — ``0.90``, ``0.95`` (the
            default), or ``0.99``. Only meaningful when
            `replicate_tolerance` is set.
        migrant_sampling: How many gene copies migrate each generation —
            "continuous" (default), the exact ``rate * N`` fraction used by
            every prior release, or the opt-in "stochastic", which draws a
            ``Binomial(N, rate)`` migrant count instead. Migrant
            composition is unaffected either way; see
            ``fim.model.operators.migrate``.
        mutation_model: How a mutation event picks its target — either
            "infinite_alleles" (default), where every mutation is
            globally novel, or "finite_alleles", where each locus has a
            bounded state space (`fim.model.locus.finite_allele_capacity`)
            and a mutation can recur to a state already present elsewhere
            in the run. See `fim.model.allele.FiniteAlleleSpace`.
        engine_backend: Which of `fim.engine`'s own three interchangeable
            engine implementations actually drives the run — "lineal"
            (default, the reference implementation), "generational"
            (thread-parallel, bit-identical to "lineal"), or
            "generational-vector" (array-native, fastest at a large
            deme count, requires `mutation_model="finite_alleles"` and
            `migrant_sampling="continuous"`), or "auto" to pick between
            "generational"/"generational-vector" from `d` and
            `auto_vector_min_d` — never "lineal", see that field's own
            entry. This never changes what a run converges to, only how
            it gets there; see `doc/fim-simulator-design.md`'s own §4.6
            for the full "what/why/how".
        jit: Whether the chosen `engine_backend` should JIT-compile its
            own random draws via the optional `numba` dependency — "off"
            (default) or "numba". Meaningful for "lineal"/"generational"
            only; "generational-vector" always requires `numba`
            regardless of this setting, and "lineal" never accepts
            anything but "off" (a permanent restriction — see
            `fim.engine.LinealBackend`'s own docstring).
        auto_vector_min_d: The deme-count threshold `engine_backend=
            "auto"` uses to choose "generational-vector" over
            "generational". Only meaningful when `engine_backend` is
            "auto"; ignored otherwise. Defaults to
            `DEFAULT_AUTO_VECTOR_MIN_D` — see that constant's own
            docstring for why it is a considered default, not a
            portable physical constant, and how to re-measure it on a
            given machine.
        auto_vector_max_capacity: The per-locus capacity ceiling
            `engine_backend="auto"` uses alongside `auto_vector_min_d`
            — "generational-vector" is only chosen when `d >=
            auto_vector_min_d` *and* every locus's own capacity is at
            most this value; exceeding it at even one locus falls back
            to "generational" regardless of `d`. Only meaningful when
            `engine_backend` is "auto"; ignored otherwise. Defaults to
            `DEFAULT_AUTO_VECTOR_MAX_CAPACITY` — see that constant's
            own docstring for the same "considered default, not a
            portable constant" caveat `auto_vector_min_d` already
            carries.
        max_concurrent_replicates: Caps how many replicate lanes
            `fim.engine.run_batch` (the `"generational"`/
            `"generational-vector"` path) advances at once — `None` (the
            default) advances every requested replicate together,
            exactly like every prior release. `run_batch` builds each
            lane lazily now, on demand, rather than all `n_replicates`
            of them up front: only this many are ever alive
            simultaneously, and a finished lane's slot is handed to the
            next not-yet-started replicate rather than every replicate
            starting at once. Meaningful for any `engine_backend` that
            reaches `run_batch`; matters most for `"generational-
            vector"`, whose own per-lane cached `VectorizedState` is a
            dense `(deme_count, capacity)` array per locus — held by
            every concurrently active lane at once, so an unbounded
            batch's steady-state memory scales with `n_replicates`
            directly (`dev/doc/apps/selby/jost-finite-island-model/
            20260904-claude-sonnet-5-fim-engine-review-remediations.md`,
            `FIM-48`). Silently clamped down to `n_replicates` if given
            larger, the same reasoning `replicate_minimum`'s own clamp
            already uses. Ignored by `"lineal"`, which never calls
            `run_batch` at all (see `fim.engine.LinealBackend`'s own
            docstring).
        initial_frequencies: Optional explicit deme/locus frequency table.
    """

    N: PopulationSize
    m: Migration
    mu: MutationRate
    d: int
    seed: int
    loci: tuple[LocusSpec, ...] = field(
        default_factory=lambda: (LocusSpec(1, DEFAULT_LOCUS_LENGTH),)
    )
    initial_allele_count: int = 2
    initial_concentration: float = 1.0
    deme_weighting: DemeWeighting = "size"
    locus_aggregation: LocusAggregation = "ratio_of_means"
    convergence_statistic: ConvergenceStatistic = "D"
    convergence_combinator: ConvergenceCombinator = "all"
    convergence_window: int = 50
    convergence_tolerance: float = 0.01
    max_generations: int = 10_000
    n_replicates: int = DEFAULT_N_REPLICATES
    replicate_tolerance: float | None = DEFAULT_REPLICATE_TOLERANCE
    replicate_minimum: int = 10
    replicate_confidence: float = 0.95
    migrant_sampling: MigrantSampling = "continuous"
    mutation_model: MutationModel = "infinite_alleles"
    engine_backend: EngineBackend = "lineal"
    jit: Jit = "off"
    auto_vector_min_d: int = DEFAULT_AUTO_VECTOR_MIN_D
    auto_vector_max_capacity: int = DEFAULT_AUTO_VECTOR_MAX_CAPACITY
    max_concurrent_replicates: int | None = None
    initial_frequencies: InitialFrequencies | None = None

    def __post_init__(self) -> None:
        """Normalize sequence inputs and validate every parameter.

        Runs automatically right after every field is assigned. Two
        things happen here, together, for every field: validate it
        (reject a value that is the wrong type, out of range, or
        inconsistent with another field), and normalize it (expand a
        convenient shorthand, like one shared `N` for every deme, into
        its full internal shape) — a `SimulationParams` that survives
        construction at all can therefore always be trusted downstream
        without re-checking any of this.
        """
        _require_integer("d", self.d, minimum=2)
        # A negative seed previously passed this validation and was only
        # ever caught deep inside `fim()`, when NumPy's PCG64 raises —
        # potentially after CLI output directories already exist. Reject
        # it here instead, at construction time. This one bound is also
        # sufficient for every batch replicate's derived seed
        # (`seed + replicate_index` in `fim.engine`): `replicate_index`
        # ranges over `0 .. n_replicates - 1`, `n_replicates` is already
        # required to be at least 1 below, and Python integers never
        # overflow, so `seed >= 0` guarantees `seed + replicate_index >=
        # 0` for every replicate without a further check. PCG64 has no
        # enforced upper bound to check against in turn (verified up to
        # a 1024-bit seed): `SeedSequence` hashes any non-negative
        # integer into a fixed-size entropy pool rather than rejecting
        # large values, so non-negativity is the entire legal range.
        _require_integer("seed", self.seed, minimum=0)

        population_sizes = _normalize_population_sizes(self.N, self.d)
        migration = _normalize_migration(self.m, self.d)
        loci = tuple(self.loci)
        if not loci:
            raise ValueError("loci must not be empty")
        if len({locus.locus_id for locus in loci}) != len(loci):
            raise ValueError("locus IDs must be unique")
        mutation_rates = _normalize_mutation_rate(self.mu, len(loci))

        _require_integer(
            "initial_allele_count",
            self.initial_allele_count,
            minimum=1,
        )
        if self.initial_allele_count > min(population_sizes):
            raise ValueError("initial_allele_count cannot exceed the smallest deme N")
        if (
            not math.isfinite(self.initial_concentration)
            or self.initial_concentration <= 0.0
        ):
            raise ValueError("initial_concentration must be greater than 0")
        _validate_weighting_and_aggregation(self.deme_weighting, self.locus_aggregation)
        convergence_statistics = _normalize_convergence_statistic(
            self.convergence_statistic
        )
        if self.convergence_combinator not in {"any", "all"}:
            raise ValueError("convergence_combinator must be 'any' or 'all'")
        _require_integer(
            "convergence_window",
            self.convergence_window,
            minimum=2,
        )
        if (
            not math.isfinite(self.convergence_tolerance)
            or self.convergence_tolerance < 0.0
        ):
            raise ValueError("convergence_tolerance must be non-negative")
        _require_integer(
            "max_generations",
            self.max_generations,
            minimum=1,
        )
        _require_integer("n_replicates", self.n_replicates, minimum=1)
        if self.replicate_tolerance is not None and (
            not math.isfinite(self.replicate_tolerance)
            or self.replicate_tolerance < 0.0
        ):
            raise ValueError("replicate_tolerance must be finite and non-negative")
        _require_integer("replicate_minimum", self.replicate_minimum, minimum=2)
        _validate_stopping_rules(
            convergence_window=self.convergence_window,
            max_generations=self.max_generations,
        )
        object.__setattr__(
            self,
            "replicate_minimum",
            _clamp_replicate_minimum(self.replicate_minimum, self.n_replicates),
        )
        if self.replicate_confidence not in {0.90, 0.95, 0.99}:
            raise ValueError("replicate_confidence must be 0.90, 0.95, or 0.99")
        if self.migrant_sampling not in {"continuous", "stochastic"}:
            raise ValueError("migrant_sampling must be 'continuous' or 'stochastic'")
        if self.mutation_model not in {"infinite_alleles", "finite_alleles"}:
            raise ValueError(
                "mutation_model must be 'infinite_alleles' or 'finite_alleles'"
            )
        _validate_engine_backend(
            engine_backend=self.engine_backend,
            jit=self.jit,
            auto_vector_min_d=self.auto_vector_min_d,
            auto_vector_max_capacity=self.auto_vector_max_capacity,
            mutation_model=self.mutation_model,
            migrant_sampling=self.migrant_sampling,
        )
        if self.max_concurrent_replicates is not None:
            _require_integer(
                "max_concurrent_replicates",
                self.max_concurrent_replicates,
                minimum=1,
            )
            object.__setattr__(
                self,
                "max_concurrent_replicates",
                _clamp_max_concurrent_replicates(
                    self.max_concurrent_replicates, self.n_replicates
                ),
            )

        initial_frequencies = _normalize_initial_frequencies(
            self.initial_frequencies,
            d=self.d,
            loci=loci,
            population_sizes=population_sizes,
        )
        if self.mutation_model == "finite_alleles":
            _validate_finite_allele_capacity(
                loci, self.initial_allele_count, initial_frequencies
            )

        object.__setattr__(
            self,
            "N",
            population_sizes[0]
            if len(set(population_sizes)) == 1
            else population_sizes,
        )
        object.__setattr__(self, "m", migration)
        object.__setattr__(
            self,
            "mu",
            mutation_rates[0] if len(set(mutation_rates)) == 1 else mutation_rates,
        )
        object.__setattr__(self, "loci", loci)
        object.__setattr__(self, "initial_frequencies", initial_frequencies)
        object.__setattr__(
            self,
            "convergence_statistic",
            convergence_statistics[0]
            if len(convergence_statistics) == 1
            else convergence_statistics,
        )

    @property
    def convergence_statistics(self) -> tuple[str, ...]:
        """Return every statistic watched by the convergence monitor.

        `convergence_statistic` itself may be stored as either a single
        string (the common case) or a tuple of several — this property
        is the convenient, always-a-tuple form every caller that just
        wants to iterate over "whichever statistics are being watched"
        actually uses, instead of handling both shapes itself.
        """
        if isinstance(self.convergence_statistic, str):
            return (self.convergence_statistic,)
        return self.convergence_statistic

    @property
    def population_sizes(self) -> tuple[int, ...]:
        """Return one gene-copy count per deme.

        `N` itself may be stored as either a single shared integer (the
        common case, when every deme has the same size) or a tuple of
        per-deme values — this property is the always-fully-expanded
        form (always exactly `d` values, one per deme) code elsewhere
        actually iterates over, so it never needs to special-case the
        "every deme is the same size" shorthand itself.
        """
        if isinstance(self.N, int):
            return (self.N,) * self.d
        return self.N

    @property
    def mutation_rates(self) -> tuple[float, ...]:
        """Return one mutation-probability rate per locus.

        Same shorthand-expansion pattern as `population_sizes`, above,
        but for `mu` instead of `N`, and per-locus instead of per-deme.
        """
        if isinstance(self.mu, float):
            return (self.mu,) * len(self.loci)
        return self.mu

    def to_dict(self) -> dict[str, object]:
        """Return a JSON/YAML-serializable, lossless configuration mapping.

        This is the inverse of `from_mapping`, below: given a fully
        constructed `SimulationParams`, produces the equivalent plain
        ``dict`` of JSON/YAML-safe values (nested lists and floats
        instead of tuples and `numpy`/custom types) that, fed back
        through `from_mapping`, reconstructs an identical
        `SimulationParams` — used, for example, to write a run's own
        parameters into its manifest (`fim.persistence.manifest`) so a
        completed run's configuration can be recovered exactly, later,
        without needing the original config file at all.
        """
        serialized_n: int | list[int] = (
            self.N if isinstance(self.N, int) else list(self.N)
        )

        serialized_m: float | list[list[float]]
        if isinstance(self.m, float):
            serialized_m = self.m
        else:
            serialized_m = [list(row) for row in self.m]

        serialized_mu: float | list[float] = (
            self.mu if isinstance(self.mu, float) else list(self.mu)
        )

        result: dict[str, object] = {
            "N": serialized_n,
            "d": self.d,
            "m": serialized_m,
            "mu": serialized_mu,
            "seed": self.seed,
            "loci": [
                {"locus_id": locus.locus_id, "length": locus.length}
                for locus in self.loci
            ],
            "initial_allele_count": self.initial_allele_count,
            "initial_concentration": self.initial_concentration,
            "deme_weighting": self.deme_weighting,
            "locus_aggregation": self.locus_aggregation,
            "convergence_statistic": (
                self.convergence_statistic
                if isinstance(self.convergence_statistic, str)
                else list(self.convergence_statistic)
            ),
            "convergence_combinator": self.convergence_combinator,
            "convergence_window": self.convergence_window,
            "convergence_tolerance": self.convergence_tolerance,
            "max_generations": self.max_generations,
            "n_replicates": self.n_replicates,
            # Always present, unlike `initial_frequencies` below (whose
            # own default is still `None`, so omitting a `None` value
            # there is still lossless): `replicate_tolerance`'s own
            # default is a real number now (`DEFAULT_REPLICATE_
            # TOLERANCE`), so an absent key and an explicit `None` no
            # longer mean the same thing to `from_mapping` — omitting
            # `None` here would silently turn a caller's own explicit
            # "disabled" into "use the default" on the next round trip
            # through `from_mapping`. Always including it, `null` for
            # `None`, keeps `from_mapping(params.to_dict()) == params`
            # true unconditionally, matching this method's own
            # docstring.
            "replicate_tolerance": self.replicate_tolerance,
            "replicate_minimum": self.replicate_minimum,
            "replicate_confidence": self.replicate_confidence,
            "migrant_sampling": self.migrant_sampling,
            "mutation_model": self.mutation_model,
            "engine_backend": self.engine_backend,
            "jit": self.jit,
            "auto_vector_min_d": self.auto_vector_min_d,
            "auto_vector_max_capacity": self.auto_vector_max_capacity,
        }
        if self.max_concurrent_replicates is not None:
            # Omitted rather than always written, unlike `replicate_
            # tolerance` above: this field's own default is already
            # `None`, so an absent key and an explicit `None` mean
            # exactly the same thing to `from_mapping` — the round-trip
            # hazard `replicate_tolerance`'s own comment describes
            # cannot arise here. Matches `initial_frequencies`, below,
            # for the same reason.
            result["max_concurrent_replicates"] = self.max_concurrent_replicates
        if self.initial_frequencies is not None:
            result["p_0"] = [
                [
                    {str(int(allele)): frequency for allele, frequency in locus.items()}
                    for locus in deme
                ]
                for deme in self.initial_frequencies
            ]
        return result

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> SimulationParams:
        """Validate a config-file mapping and construct simulation parameters.

        This is the actual entry point for turning a YAML or JSON
        config file into a validated `SimulationParams` — everything
        below (`_parse_*`/`_normalize_*`, and this method's own
        up-front key checks) exists to support this one method. It is
        deliberately stricter than plain `SimulationParams(**config)`
        would be: an unrecognized key (very likely a typo) is rejected
        outright rather than silently accepted and ignored, and a few
        cross-field rules that only make sense at the config-file
        layer — like `mu` and `mu_b` being mutually exclusive
        shorthands for the same underlying setting — are checked here,
        before construction, rather than inside `__post_init__`.

        Args:
            config: Parsed YAML or JSON object.

        Returns:
            A validated immutable parameter object.

        Raises:
            ValueError: If a key is unknown, required, malformed, or conflicting.
        """
        unknown = set(config) - _CONFIG_KEYS
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(f"unknown configuration key(s): {names}")
        if "mu" in config and "mu_b" in config:
            raise ValueError("mu cannot be combined with mu_b")
        missing = {"N", "d", "m", "seed"} - set(config)
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"missing required configuration key(s): {names}")
        if "mu" not in config and "mu_b" not in config:
            raise ValueError("missing required configuration key(s): mu or mu_b")

        loci = _loci_from_config(config)
        d = _parse_int("d", config["d"])
        return cls(
            N=_parse_population_size(config["N"]),
            d=d,
            m=_parse_migration(config["m"], d),
            mu=_mutation_rate_from_config(config, loci),
            seed=_parse_int("seed", config["seed"]),
            loci=loci,
            initial_allele_count=_parse_int(
                "initial_allele_count",
                config.get(
                    "initial_allele_count",
                    PARAMETER_DEFAULTS["initial_allele_count"],
                ),
            ),
            initial_concentration=_parse_float(
                "initial_concentration",
                config.get(
                    "initial_concentration",
                    PARAMETER_DEFAULTS["initial_concentration"],
                ),
            ),
            deme_weighting=_parse_deme_weighting(
                config.get(
                    "deme_weighting",
                    PARAMETER_DEFAULTS["deme_weighting"],
                )
            ),
            locus_aggregation=_parse_locus_aggregation(
                config.get(
                    "locus_aggregation",
                    PARAMETER_DEFAULTS["locus_aggregation"],
                )
            ),
            convergence_statistic=_parse_convergence_statistic(
                config.get(
                    "convergence_statistic",
                    PARAMETER_DEFAULTS["convergence_statistic"],
                ),
            ),
            convergence_combinator=_parse_convergence_combinator(
                config.get(
                    "convergence_combinator",
                    PARAMETER_DEFAULTS["convergence_combinator"],
                ),
            ),
            convergence_window=_parse_int(
                "convergence_window",
                config.get(
                    "convergence_window",
                    PARAMETER_DEFAULTS["convergence_window"],
                ),
            ),
            convergence_tolerance=_parse_float(
                "convergence_tolerance",
                config.get(
                    "convergence_tolerance",
                    PARAMETER_DEFAULTS["convergence_tolerance"],
                ),
            ),
            max_generations=_parse_int(
                "max_generations",
                config.get(
                    "max_generations",
                    PARAMETER_DEFAULTS["max_generations"],
                ),
            ),
            n_replicates=_parse_int(
                "n_replicates",
                config.get(
                    "n_replicates",
                    PARAMETER_DEFAULTS["n_replicates"],
                ),
            ),
            replicate_tolerance=_parse_optional_float(
                "replicate_tolerance",
                config["replicate_tolerance"]
                if "replicate_tolerance" in config
                # Absent means "use the default", not "disabled" — those
                # are different things now that the default is a real
                # number (`DEFAULT_REPLICATE_TOLERANCE`), not `None`.
                # Write `replicate_tolerance: null` explicitly in a YAML/
                # JSON config for the old "run n_replicates in full, no
                # adaptive stop" behavior; omitting the key entirely now
                # means "use the default tolerance," not "disable it."
                else PARAMETER_DEFAULTS["replicate_tolerance"],
            ),
            replicate_minimum=_parse_int(
                "replicate_minimum",
                config.get(
                    "replicate_minimum",
                    PARAMETER_DEFAULTS["replicate_minimum"],
                ),
            ),
            replicate_confidence=_parse_float(
                "replicate_confidence",
                config.get(
                    "replicate_confidence",
                    PARAMETER_DEFAULTS["replicate_confidence"],
                ),
            ),
            migrant_sampling=_parse_migrant_sampling(
                config.get(
                    "migrant_sampling",
                    PARAMETER_DEFAULTS["migrant_sampling"],
                ),
            ),
            mutation_model=_parse_mutation_model(
                config.get(
                    "mutation_model",
                    PARAMETER_DEFAULTS["mutation_model"],
                ),
            ),
            engine_backend=_parse_engine_backend(
                config.get(
                    "engine_backend",
                    PARAMETER_DEFAULTS["engine_backend"],
                ),
            ),
            jit=_parse_jit(
                config.get(
                    "jit",
                    PARAMETER_DEFAULTS["jit"],
                ),
            ),
            auto_vector_min_d=_parse_int(
                "auto_vector_min_d",
                config.get(
                    "auto_vector_min_d",
                    PARAMETER_DEFAULTS["auto_vector_min_d"],
                ),
            ),
            auto_vector_max_capacity=_parse_int(
                "auto_vector_max_capacity",
                config.get(
                    "auto_vector_max_capacity",
                    PARAMETER_DEFAULTS["auto_vector_max_capacity"],
                ),
            ),
            max_concurrent_replicates=_parse_optional_int(
                "max_concurrent_replicates",
                config.get(
                    "max_concurrent_replicates",
                    PARAMETER_DEFAULTS["max_concurrent_replicates"],
                ),
            ),
            initial_frequencies=_parse_initial_frequencies(config.get("p_0")),
        )


# Everything below this point is a private helper supporting
# `SimulationParams.from_mapping`, above: each one parses and validates
# one specific configuration field (or one shared value shape reused by
# several fields), rejecting the field's own particular ways of being
# malformed with a specific error message. None of these are meant to
# be called from outside this module — `from_mapping` is the public
# entry point that calls all of them together.


def _loci_from_config(config: Mapping[str, Any]) -> tuple[LocusSpec, ...]:
    """Build locus specifications from either supported config shape."""
    if "loci" in config:
        conflicts = {"n_loci", "locus_lengths"} & set(config)
        if conflicts:
            names = ", ".join(sorted(conflicts))
            raise ValueError(f"loci cannot be combined with {names}")
        raw_loci = config["loci"]
        if not isinstance(raw_loci, Sequence) or isinstance(raw_loci, str):
            raise ValueError("loci must be a list of mappings")
        loci: list[LocusSpec] = []
        for index, raw_locus in enumerate(raw_loci, start=1):
            if not isinstance(raw_locus, Mapping):
                raise ValueError(f"loci[{index - 1}] must be a mapping")
            unknown = set(raw_locus) - {"locus_id", "length"}
            if unknown:
                names = ", ".join(sorted(str(key) for key in unknown))
                raise ValueError(f"unknown loci[{index - 1}] key(s): {names}")
            if "length" not in raw_locus:
                raise ValueError(f"loci[{index - 1}] is missing 'length'")
            locus_id = _parse_int(
                f"loci[{index - 1}].locus_id",
                raw_locus.get("locus_id", index),
            )
            length = _parse_int(
                f"loci[{index - 1}].length",
                raw_locus["length"],
            )
            loci.append(LocusSpec(locus_id, length))
        return tuple(loci)

    n_loci = _parse_int(
        "n_loci",
        config.get("n_loci", PARAMETER_DEFAULTS["n_loci"]),
    )
    lengths_value = config.get(
        "locus_lengths",
        PARAMETER_DEFAULTS["locus_lengths"],
    )
    if isinstance(lengths_value, Sequence) and not isinstance(lengths_value, str):
        lengths = tuple(
            _parse_int(f"locus_lengths[{index}]", value)
            for index, value in enumerate(lengths_value)
        )
        if len(lengths) != n_loci:
            raise ValueError("locus_lengths must contain exactly n_loci values")
    else:
        length = _parse_int("locus_lengths", lengths_value)
        lengths = (length,) * n_loci
    return tuple(
        LocusSpec(index, length) for index, length in enumerate(lengths, start=1)
    )


def _migration_from_sparse_map(value: Mapping[Any, Any], d: int) -> Migration:
    """Parse a one-based sparse neighbor map into a full matrix.

    Config shape: ``{deme: {neighbor: weight, ...}, ...}``, one-based, with
    every deme's self-retention left implicit as the complement of its
    listed weights — the same convention ``stepping_stone_neighbors``
    already returns. A deme absent from the map migrates with nobody.
    """
    parsed: dict[int, dict[int, float]] = {}
    for raw_deme, raw_row in value.items():
        deme = _parse_deme_key("m", raw_deme, d)
        if not isinstance(raw_row, Mapping):
            raise ValueError(f"m[{raw_deme}] must be a mapping of neighbor to weight")
        row: dict[int, float] = {}
        for raw_neighbor, raw_weight in raw_row.items():
            neighbor = _parse_deme_key(f"m[{raw_deme}]", raw_neighbor, d)
            row[neighbor] = _parse_float(f"m[{raw_deme}][{raw_neighbor}]", raw_weight)
        parsed[deme] = row
    return dense_matrix_from_neighbors(parsed, d)


def _migration_from_topology(value: Mapping[str, Any], d: int) -> Migration:
    """Expand a compact ``{topology, rate}`` mapping into a full matrix."""
    unknown = set(value) - {"topology", "rate"}
    if unknown:
        names = ", ".join(sorted(str(key) for key in unknown))
        raise ValueError(f"unknown m topology key(s): {names}")
    missing = {"topology", "rate"} - set(value)
    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(f"m topology mapping is missing {names}")
    topology = _parse_string("m.topology", value["topology"])
    rate = _parse_float("m.rate", value["rate"])
    if topology not in {"ring", "linear"}:
        raise ValueError("m.topology must be 'ring' or 'linear'")
    validated_topology = cast(Topology, topology)
    neighbors = stepping_stone_neighbors(d, topology=validated_topology, rate=rate)
    return dense_matrix_from_neighbors(neighbors, d)


def _mutation_rate_from_config(
    config: Mapping[str, Any],
    loci: tuple[LocusSpec, ...],
) -> MutationRate:
    """Resolve ``mu`` or ``mu_b`` into the rate `SimulationParams` stores.

    ``mu`` (a per-locus probability, scalar or one value per locus) and
    ``mu_b`` (a single per-base-pair probability, from which each locus's
    own rate is derived via its own ``length``, per
    `fim.model.locus.finite_allele_capacity`'s companion Eq. 5 relation
    ``mu = 1 - (1 - mu_b) ** length``) are mutually exclusive; the caller
    has already confirmed exactly one is present.
    """
    if "mu" in config:
        return _parse_mutation_rate(config["mu"])
    mu_b = _parse_float("mu_b", config["mu_b"])
    _require_probability("mu_b", mu_b)
    return tuple(1.0 - (1.0 - mu_b) ** locus.length for locus in loci)


def _normalize_convergence_statistic(
    value: ConvergenceStatistic,
) -> tuple[str, ...]:
    """Validate one or several convergence statistics and return them as a tuple.

    A list is accepted for the "several statistics needed to agree before
    stopping" extension (design §9): every name must be a recognized
    statistic, and no name may repeat. A single statistic — the default —
    is the resulting tuple's one-element case; nothing downstream needs to
    special-case it.
    """
    candidates: tuple[str, ...] = (value,) if isinstance(value, str) else tuple(value)
    if not candidates:
        raise ValueError("convergence_statistic must not be empty")
    for statistic in candidates:
        if statistic not in _CONVERGENCE_STATISTICS:
            allowed = ", ".join(sorted(_CONVERGENCE_STATISTICS))
            raise ValueError(f"convergence_statistic must be one of: {allowed}")
    if len(set(candidates)) != len(candidates):
        raise ValueError("convergence_statistic must not repeat a statistic")
    return candidates


def _normalize_initial_frequencies(
    value: InitialFrequencies | None,
    *,
    d: int,
    loci: tuple[LocusSpec, ...],
    population_sizes: tuple[int, ...],
) -> InitialFrequencies | None:
    """Validate and make explicit initial frequencies immutable."""
    if value is None:
        return None
    if len(value) != d:
        raise ValueError("p_0 must contain exactly d demes")
    normalized_demes: list[tuple[Mapping[AlleleId, float], ...]] = []
    for deme_index, (deme, size) in enumerate(
        zip(value, population_sizes, strict=True),
        start=1,
    ):
        if len(deme) != len(loci):
            raise ValueError(
                f"p_0 deme {deme_index} must contain exactly {len(loci)} loci"
            )
        normalized_loci: list[Mapping[AlleleId, float]] = []
        for locus, frequency_map in zip(loci, deme, strict=True):
            if len(frequency_map) > size:
                raise ValueError(
                    f"p_0 deme {deme_index}, locus {locus.locus_id} support "
                    f"exceeds N={size}"
                )
            normalized: dict[AlleleId, float] = {}
            for allele_id, frequency in frequency_map.items():
                identity = parse_integer_identifier(
                    f"p_0 allele ID {allele_id!r} must be an integer", allele_id
                )
                if identity < 0:
                    raise ValueError(
                        f"p_0 allele ID {allele_id!r} must be a non-negative integer"
                    )
                numeric_frequency = float(frequency)
                if not math.isfinite(numeric_frequency) or numeric_frequency < 0.0:
                    raise ValueError("p_0 frequencies must be finite and non-negative")
                if numeric_frequency > 0.0:
                    normalized[AlleleId(identity)] = numeric_frequency
            if not normalized or not math.isclose(
                math.fsum(normalized.values()),
                1.0,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise ValueError(
                    f"p_0 deme {deme_index}, locus {locus.locus_id} "
                    "frequencies must sum to 1"
                )
            normalized_loci.append(MappingProxyType(normalized))
        normalized_demes.append(tuple(normalized_loci))
    return tuple(normalized_demes)


def _normalize_migration(value: Migration, d: int) -> Migration:
    """Validate scalar or matrix migration and normalize numeric values."""
    # `_parse_migration` already rejects a bool `m` before this function
    # ever runs; the explicit reject here (rather than folding it into the
    # `int | float` branch below) keeps the numeric-tower compatibility of
    # `bool` with `float` from leaving a `bool` on the matrix-iteration
    # path in mypy's eyes.
    if isinstance(value, bool):
        raise ValueError("m must be a number or a d x d matrix")
    if isinstance(value, int | float):
        _require_probability("m", float(value))
        return float(value)
    rows = tuple(tuple(float(item) for item in row) for row in value)
    if len(rows) != d or any(len(row) != d for row in rows):
        raise ValueError("migration matrix m must have shape d x d")
    for index, row in enumerate(rows):
        for item in row:
            _require_probability(f"m[{index}]", item)
        if not math.isclose(
            math.fsum(row),
            1.0,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError(f"migration matrix row {index} must sum to 1")
    return rows


def _normalize_mutation_rate(value: MutationRate, n_loci: int) -> tuple[float, ...]:
    """Validate and expand a scalar or per-locus mutation-rate probability."""
    if isinstance(value, bool):
        raise ValueError("mu must be a number or a list of numbers")
    if isinstance(value, int | float):
        _require_probability("mu", float(value))
        return (float(value),) * n_loci
    rates = tuple(float(item) for item in value)
    if len(rates) != n_loci:
        raise ValueError("mu must contain exactly one rate per locus")
    for index, rate in enumerate(rates):
        _require_probability(f"mu[{index}]", rate)
    return rates


def _normalize_population_sizes(
    value: PopulationSize,
    d: int,
) -> tuple[int, ...]:
    """Validate scalar or per-deme gene-copy counts."""
    if isinstance(value, int):
        if isinstance(value, bool):
            raise ValueError("N must be an integer")
        _require_integer("N", value, minimum=1)
        return (value,) * d
    values = tuple(value)
    if len(values) != d:
        raise ValueError("N must contain exactly d values")
    for index, item in enumerate(values):
        _require_integer(f"N[{index}]", item, minimum=1)
    return values


def _parse_convergence_combinator(value: Any) -> ConvergenceCombinator:
    """Parse the two supported convergence-combinator values."""
    parsed = _parse_string("convergence_combinator", value)
    if parsed == "any":
        return "any"
    if parsed == "all":
        return "all"
    raise ValueError("convergence_combinator must be 'any' or 'all'")


def _parse_convergence_statistic(value: Any) -> ConvergenceStatistic:
    """Parse a scalar or list convergence-statistic configuration value."""
    if isinstance(value, str):
        return _parse_string("convergence_statistic", value)
    if not isinstance(value, Sequence):
        raise ValueError("convergence_statistic must be a string or a list of strings")
    return tuple(
        _parse_string(f"convergence_statistic[{index}]", item)
        for index, item in enumerate(value)
    )


def _parse_deme_key(context: str, raw_key: Any, d: int) -> int:
    """Parse one 1-based deme identifier from a sparse migration-map key.

    Accepts either a native integer (the common YAML case) or a numeric
    string (JSON object keys are always strings), matching how ``p_0``'s
    allele keys are already coerced.
    """
    deme = parse_integer_identifier(
        f"{context} deme identifiers must be integers", raw_key
    )
    if not 1 <= deme <= d:
        raise ValueError(f"{context} deme {deme} is outside 1..{d}")
    return deme


def _parse_deme_weighting(value: Any) -> DemeWeighting:
    """Parse the two supported deme-weighting values."""
    parsed = _parse_string("deme_weighting", value)
    if parsed == "equal":
        return "equal"
    if parsed == "size":
        return "size"
    raise ValueError("deme_weighting must be 'equal' or 'size'")


def _parse_locus_aggregation(value: Any) -> LocusAggregation:
    """Parse the two supported locus-aggregation values."""
    parsed = _parse_string("locus_aggregation", value)
    if parsed == "ratio_of_means":
        return "ratio_of_means"
    if parsed == "mean_of_ratios":
        return "mean_of_ratios"
    raise ValueError("locus_aggregation must be 'ratio_of_means' or 'mean_of_ratios'")


def _parse_float(name: str, value: Any) -> float:
    """Parse a finite config float without accepting booleans."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be finite")
    return parsed


def _parse_initial_frequencies(value: Any) -> InitialFrequencies | None:
    """Parse the nested ``p_0`` config structure."""
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise ValueError("p_0 must be a list of demes")
    demes: list[tuple[Mapping[AlleleId, float], ...]] = []
    for deme_index, raw_deme in enumerate(value):
        if not isinstance(raw_deme, Sequence) or isinstance(raw_deme, str):
            raise ValueError(f"p_0[{deme_index}] must be a list of loci")
        loci: list[Mapping[AlleleId, float]] = []
        for locus_index, raw_locus in enumerate(raw_deme):
            if not isinstance(raw_locus, Mapping):
                raise ValueError(f"p_0[{deme_index}][{locus_index}] must be a mapping")
            frequencies: dict[AlleleId, float] = {}
            for raw_allele, raw_frequency in raw_locus.items():
                identity = parse_integer_identifier(
                    f"p_0 allele ID {raw_allele!r} must be an integer", raw_allele
                )
                if identity < 0:
                    raise ValueError(
                        f"p_0 allele ID {raw_allele!r} must be a non-negative integer"
                    )
                allele_id = AlleleId(identity)
                frequencies[allele_id] = _parse_float(
                    (f"p_0[{deme_index}][{locus_index}][{raw_allele!r}]"),
                    raw_frequency,
                )
            loci.append(frequencies)
        demes.append(tuple(loci))
    return tuple(demes)


def _parse_int(name: str, value: Any) -> int:
    """Parse a config integer without coercing floats or booleans."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return int(value)


def _parse_migrant_sampling(value: Any) -> MigrantSampling:
    """Parse the two supported migrant-sampling values."""
    parsed = _parse_string("migrant_sampling", value)
    if parsed == "continuous":
        return "continuous"
    if parsed == "stochastic":
        return "stochastic"
    raise ValueError("migrant_sampling must be 'continuous' or 'stochastic'")


def _parse_migration(value: Any, d: int) -> Migration:
    """Parse scalar, dense-matrix, sparse-map, or topology-sugar migration config."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return _parse_float("m", value)
    if isinstance(value, Mapping):
        # A deme identifier is always integer-like, so a bare "topology" or
        # "rate" key can never be a legitimate sparse-map deme — route a
        # mapping using either to the topology-sugar parser even if one of
        # the two required keys was left out, so a config that meant
        # {topology, rate} but mistyped it gets that mistake's own clear
        # error instead of a confusing "deme identifiers must be integers".
        if "topology" in value or "rate" in value:
            return _migration_from_topology(value, d)
        return _migration_from_sparse_map(value, d)
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise ValueError(
            "m must be a number, a d x d matrix, a sparse neighbor map, "
            "or a {topology, rate} mapping"
        )
    rows: list[tuple[float, ...]] = []
    for row_index, raw_row in enumerate(value):
        if not isinstance(raw_row, Sequence) or isinstance(raw_row, str):
            raise ValueError(f"m[{row_index}] must be a list")
        rows.append(
            tuple(
                _parse_float(f"m[{row_index}][{column_index}]", item)
                for column_index, item in enumerate(raw_row)
            )
        )
    return tuple(rows)


def _parse_mutation_model(value: Any) -> MutationModel:
    """Parse the two supported mutation-model values."""
    parsed = _parse_string("mutation_model", value)
    if parsed == "infinite_alleles":
        return "infinite_alleles"
    if parsed == "finite_alleles":
        return "finite_alleles"
    raise ValueError("mutation_model must be 'infinite_alleles' or 'finite_alleles'")


def _parse_engine_backend(value: Any) -> EngineBackend:
    """Parse the four supported engine-backend choices."""
    parsed = _parse_string("engine_backend", value)
    if parsed in {"lineal", "generational", "generational-vector", "auto"}:
        return cast(EngineBackend, parsed)
    raise ValueError(
        "engine_backend must be 'lineal', 'generational', "
        "'generational-vector', or 'auto'"
    )


def _parse_jit(value: Any) -> Jit:
    """Parse the two supported JIT settings."""
    parsed = _parse_string("jit", value)
    if parsed in {"off", "numba"}:
        return cast(Jit, parsed)
    raise ValueError("jit must be 'off' or 'numba'")


def _parse_mutation_rate(value: Any) -> MutationRate:
    """Parse a scalar or per-locus list of mutation-rate probabilities.

    Length-against-locus-count validation happens later, in
    `_normalize_mutation_rate`, matching `_parse_population_size`'s own
    division of labor against `_normalize_population_sizes`.
    """
    if isinstance(value, bool):
        raise ValueError("mu must be a number or a list of numbers")
    if isinstance(value, int | float):
        return _parse_float("mu", value)
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise ValueError("mu must be a number or a list of numbers")
    return tuple(_parse_float(f"mu[{index}]", item) for index, item in enumerate(value))


def _parse_optional_float(name: str, value: Any) -> float | None:
    """Parse a finite config float, or ``None`` when the key is absent."""
    if value is None:
        return None
    return _parse_float(name, value)


def _parse_optional_int(name: str, value: Any) -> int | None:
    """Parse a config integer, or ``None`` when the key is absent/explicit ``null``."""
    if value is None:
        return None
    return _parse_int(name, value)


def _parse_population_size(value: Any) -> PopulationSize:
    """Parse scalar or per-deme gene-copy counts."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise ValueError("N must be an integer or a list of integers")
    return tuple(_parse_int(f"N[{index}]", item) for index, item in enumerate(value))


def _parse_string(name: str, value: Any) -> str:
    """Parse a nonempty config string."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _require_integer(name: str, value: int, minimum: int | None = None) -> None:
    """Validate an integer parameter."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")


def _require_probability(name: str, value: float) -> None:
    """Validate a finite probability."""
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")


def _validate_finite_allele_capacity(
    loci: tuple[LocusSpec, ...],
    initial_allele_count: int,
    initial_frequencies: InitialFrequencies | None,
) -> None:
    """Reject a finite-alleles configuration too small for its initial state.

    Every locus's finite state space (`finite_allele_capacity`) must hold
    whichever allele IDs generation zero actually uses there — the founding
    range ``0 .. initial_allele_count - 1``, or, when an explicit ``p_0`` is
    given, whatever specific IDs it names.
    """
    for locus_index, locus in enumerate(loci):
        capacity = finite_allele_capacity(locus.length)
        if initial_frequencies is not None:
            observed_ids = {
                int(allele_id)
                for deme in initial_frequencies
                for allele_id in deme[locus_index]
            }
            if any(allele_id >= capacity for allele_id in observed_ids):
                raise ValueError(
                    f"locus {locus.locus_id}: an initial allele ID exceeds "
                    f"the finite_alleles capacity ({capacity}) for length "
                    f"{locus.length}"
                )
        elif initial_allele_count > capacity:
            raise ValueError(
                f"locus {locus.locus_id}: initial_allele_count exceeds the "
                f"finite_alleles capacity ({capacity}) for length "
                f"{locus.length}"
            )


def _validate_weighting_and_aggregation(
    deme_weighting: DemeWeighting,
    locus_aggregation: LocusAggregation,
) -> None:
    """Validate the two statistics-aggregation choices together.

    Factored out of `__post_init__` itself purely to stay under ruff's
    own branch-count limit for that method — `deme_weighting` and
    `locus_aggregation` share nothing behaviorally (one controls how
    demes are weighted, the other how loci are combined), grouped here
    only because each is a plain two-value string check.
    """
    if deme_weighting not in {"equal", "size"}:
        raise ValueError("deme_weighting must be 'equal' or 'size'")
    if locus_aggregation not in {"ratio_of_means", "mean_of_ratios"}:
        raise ValueError(
            "locus_aggregation must be 'ratio_of_means' or 'mean_of_ratios'"
        )


def _validate_engine_backend(
    *,
    engine_backend: EngineBackend,
    jit: Jit,
    auto_vector_min_d: int,
    auto_vector_max_capacity: int,
    mutation_model: MutationModel,
    migrant_sampling: MigrantSampling,
) -> None:
    """Reject an engine_backend/jit combination the engine would refuse anyway.

    Checked here, at config-parse time, rather than only once a run
    actually starts and `fim.engine.LinealBackend`/`VectorizedAdvancer`
    raise the same complaint deep inside a call stack: a config this
    obviously self-contradictory should never get far enough to start a
    run at all. Mirrors two different real counterparts, not one single
    function — corrected here, found stale while reading this docstring
    against the current code rather than trusting it
    (`20260903-claude-sonnet-5-fim-vg-performance-campaign-design.md`
    §6.1 item 2): the `jit`-related rejections mirror `fim.engine.
    build_engine_backend`'s own identical checks
    (`20260901-claude-sonnet-5-fim-engine-backend-factory-design.md`
    §7.5); the `mutation_model`/`migrant_sampling` rejection mirrors
    `fim.engine.VectorizedAdvancer.advance`'s own runtime check instead
    — `build_engine_backend` itself never checks either field, it only
    ever raises on `jit`. **No automated cross-check enforces either
    stays in sync — that claim, previously made here, did not hold**:
    checked directly against the test suite, not merely assumed
    correct; no such test exists anywhere under `test/`. Kept as a
    plain code comment, not a verified guarantee, until a real test is
    built.
    `auto_vector_min_d`/`auto_vector_max_capacity` are a different kind
    of check entirely — validated here only as plain positive integers,
    never cross-referenced against `d`/any locus's own capacity; that
    cross-referencing is `fim.engine._resolve_auto_engine_backend`'s
    own job, at resolution time, not a construction-time rejection at
    all (an out-of-range `auto_vector_min_d`/`auto_vector_max_capacity`
    changes what `"auto"` picks, it never makes construction fail).
    """
    if engine_backend not in {"lineal", "generational", "generational-vector", "auto"}:
        raise ValueError(
            "engine_backend must be 'lineal', 'generational', "
            "'generational-vector', or 'auto'"
        )
    if jit not in {"off", "numba"}:
        raise ValueError("jit must be 'off' or 'numba'")
    _require_integer("auto_vector_min_d", auto_vector_min_d, minimum=1)
    _require_integer("auto_vector_max_capacity", auto_vector_max_capacity, minimum=1)
    if engine_backend == "lineal" and jit != "off":
        raise ValueError("engine_backend 'lineal' only accepts jit='off'")
    if engine_backend == "generational-vector" and jit != "off":
        raise ValueError(
            "engine_backend 'generational-vector' only accepts jit='off' "
            "(it requires numba unconditionally, regardless of jit)"
        )
    if engine_backend == "generational-vector" and not (
        mutation_model == "finite_alleles" and migrant_sampling == "continuous"
    ):
        raise ValueError(
            "engine_backend 'generational-vector' requires "
            "mutation_model='finite_alleles' and migrant_sampling='continuous'"
        )


def _validate_stopping_rules(
    *,
    convergence_window: int,
    max_generations: int,
) -> None:
    """Reject a within-run stopping rule that structurally can never fire.

    Compares the trailing-window size against the largest sample that
    criterion could ever see — not whether convergence is *likely*,
    only whether it is *possible* at all. Accepting the misconfiguration
    would silently and permanently change nothing about the run's own
    actual behavior (it already always hits its cap), while reporting
    that outcome as an ordinary, unremarkable non-convergence rather
    than the unreachable stopping condition it actually is.

    The replicate-layer analog of this check (`replicate_minimum`
    exceeding `n_replicates`) used to live here too, as a hard
    rejection — removed (`__post_init__` now silently clamps
    `replicate_minimum` down to `n_replicates` instead, see its own
    comment there) once `replicate_tolerance` stopped defaulting to
    `None`: rejecting became a landmine for any caller who set a small
    `n_replicates` without separately thinking about `replicate_
    minimum` at all, not just the deliberate-opt-in misconfiguration it
    was built to catch — found live, not assumed (every GUI batch test
    that only ever sets `n_replicates` failed exactly this way in CI,
    `jost-finite-island-model` run 33656031751, the first time this
    code path ran anywhere other than this project's own test suite,
    which had — by then — already been taught to set every relevant
    field explicitly everywhere it cared about batch size).

    Args:
        convergence_window: Trailing within-run stability-window length.
        max_generations: Hard generation safety cap.

    Raises:
        ValueError: If the window could never fill before the cap.
    """
    # Generation 0 is always recorded before the run loop's first step, so
    # a run watching `max_generations` records at most `max_generations +
    # 1` generations (0 .. max_generations inclusive) before the hard cap
    # stops it -- one more than `max_generations` itself, not equal to
    # it. A trailing window needing more observations than that can never
    # fill, so `TrailingWindowCriterion.is_stable` can never return True.
    if convergence_window > max_generations + 1:
        raise ValueError(
            "convergence_window cannot exceed max_generations + 1 (a "
            "window this large can never fill before the generation cap "
            "stops the run, so convergence could never be detected)"
        )


_MIN_MEANINGFUL_REPLICATE_COUNT: Final = 2


def _clamp_replicate_minimum(replicate_minimum: int, n_replicates: int) -> int:
    """Cap replicate_minimum at n_replicates, silently, not by rejecting the config.

    A `replicate_minimum` above `n_replicates` would make the adaptive
    stop structurally unreachable (the hard cap always ends the batch
    first) -- clamped here rather than raised, since this project's own
    CI found it can arise from nothing more deliberate than a caller
    setting `n_replicates` to something small without separately
    thinking about `replicate_minimum` at all (every GUI batch test
    that only ever sets `n_replicates` failed exactly this way,
    `jost-finite-island-model` run 33656031751, the first time this code
    path ran anywhere other than this project's own test suite — which
    had, by then, already been taught to set every relevant field
    explicitly). The ordinary case now that `replicate_tolerance`
    defaults to a real value, not `None` — this interaction used to be
    a deliberate opt-in combination worth flagging as a likely mistake;
    it no longer reliably is one. A no-op whenever `replicate_minimum`
    is already `<= n_replicates`, and at `n_replicates == 1` regardless
    of this field's own value (adaptive stopping is already inert
    there, since `fim()` returns via `_run_one` before ever
    constructing a replicate monitor).
    """
    if n_replicates < _MIN_MEANINGFUL_REPLICATE_COUNT:
        return replicate_minimum
    return min(replicate_minimum, n_replicates)


def _clamp_max_concurrent_replicates(
    max_concurrent_replicates: int, n_replicates: int
) -> int:
    """Cap max_concurrent_replicates at n_replicates, silently, not by rejecting.

    A window wider than the whole batch is not wrong, just meaningless —
    `fim.engine.run_batch` already never has more than `n_replicates`
    lanes to advance in the first place, so a caller who names a
    generously large window (or one sized for a *different*, larger
    `n_replicates` on a config reused across runs) gets exactly the same
    behavior a plain `None` would give, not a config-time error. Mirrors
    `_clamp_replicate_minimum`'s own reasoning, above, for the identical
    shape of mismatch between two `SimulationParams` fields.
    """
    return min(max_concurrent_replicates, n_replicates)
