"""Seeded initial-condition strategies.

Before a simulation can run at all, every deme needs a starting set of
allele frequencies at generation zero — this module is where that
starting point comes from. Three strategies are provided, all reachable
through `generate_initial_state`: `DirichletInitialCondition` (the
default) draws a random starting frequency for each deme/locus from a
symmetric Dirichlet distribution (a standard way of picking a random
set of proportions that add up to 1, used throughout population
genetics for exactly this purpose); `ExplicitInitialCondition` instead
uses a frequency table the caller supplied directly in
`SimulationParams` (``p_0`` in a config file), for reproducing a
specific known starting condition rather than a random one;
`EquilibriumSplitInitialCondition` instead simulates one ancestral
population to mutation-drift equilibrium and founds every deme by
sampling from it (see that class's own docstring).

Every strategy but the last shares one contract, stated precisely by
`DirichletInitialCondition`'s own docstring: generation zero is a
*continuous belief*, not yet a sampled population — `fim.model.
operators.drift`'s first application is what turns it into the model's
first actual `N`-gene-copy realization. `EquilibriumSplitInitialCondition`
does not fit that contract, and its own docstring explains why not: its
generation zero is already sampled, finite, and real by the time it is
returned.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from fim.convergence.criteria import TrailingWindowCriterion
from fim.convergence.monitor import ConvergenceMonitor
from fim.model.allele import (
    MINTED_ID_START,
    AlleleId,
    AlleleRegistry,
    founding_allele_ids,
)
from fim.model.locus import LocusSpec
from fim.model.operators import drift, mutate
from fim.model.params import InitialFrequencies, SimulationParams
from fim.model.state import ModelState
from fim.statistics.differentiation import h_s


class InitialConditionGenerator(Protocol):
    """Generate generation zero from validated parameters and one RNG.

    A "protocol" here means any object with this one `generate` method
    — this class is never instantiated itself; it exists only so that
    `generate_initial_state`, below, can hold either
    `DirichletInitialCondition` or `ExplicitInitialCondition`
    interchangeably, without needing to know which one it actually has.
    """

    def generate(
        self,
        params: SimulationParams,
        rng: np.random.Generator,
    ) -> ModelState:
        """Return a validated generation-zero state."""
        ...


class DirichletInitialCondition:
    """Draw each deme/locus vector from a symmetric Dirichlet distribution.

    A symmetric Dirichlet is continuous, so the generation-zero state
    this produces almost surely does not land on the model's own
    ``1/N`` lattice — the set of frequencies ``N`` gene copies can
    actually realize. That is deliberate: generation zero represents
    the *belief* a starting frequency is drawn from, not a sampled
    population state; `fim.model.operators.drift`'s first application
    (producing generation one) is what turns that belief into the
    model's first actual `N`-gene-copy realization. See the design
    doc's §3.3 for the full contract this deliberately documents rather
    than resolves in code.
    """

    def generate(
        self,
        params: SimulationParams,
        rng: np.random.Generator,
    ) -> ModelState:
        """Draw a reproducible random initial state.

        Args:
            params: Validated simulation parameters.
            rng: The run's explicitly threaded random generator.

        Returns:
            A generation-zero model state.
        """
        allele_ids = founding_allele_ids(params.initial_allele_count)
        concentration = np.full(
            params.initial_allele_count,
            params.initial_concentration,
            dtype=np.float64,
        )
        demes = tuple(
            _dirichlet_locus_maps(params.loci, allele_ids, concentration, rng)
            for _deme in range(params.d)
        )
        state = ModelState(
            loci=params.loci,
            frequencies=demes,
            generation=0,
        )
        state.validate_support(params.population_sizes)
        return state


def _dirichlet_locus_maps(
    loci: Sequence[LocusSpec],
    allele_ids: tuple[AlleleId, ...],
    concentration: np.ndarray,
    rng: np.random.Generator,
) -> tuple[Mapping[AlleleId, float], ...]:
    """Draw one deme's own independent Dirichlet frequency map at every locus.

    Factored out of `DirichletInitialCondition.generate` so
    `EquilibriumSplitInitialCondition`, below, can draw its own single
    ancestral deme's starting distribution the identical way, rather
    than a second, separately maintained copy of this loop — same draw
    order (one `rng.dirichlet(concentration)` call per locus, in `loci`
    order), so neither caller's own RNG draw sequence changed when this
    was extracted.
    """
    return tuple(
        {
            allele_id: float(frequency)
            for allele_id, frequency in zip(
                allele_ids, rng.dirichlet(concentration), strict=True
            )
        }
        for _locus in loci
    )


class ExplicitInitialCondition:
    """Use the frequency table supplied in ``SimulationParams`` verbatim.

    Chosen automatically by `generate_initial_state` whenever a config
    supplies its own ``p_0`` frequency table, instead of the default
    `DirichletInitialCondition` random draw — for reproducing an exact,
    specific starting population (matching a real observed sample, or
    replaying a scenario from another tool) rather than a randomly
    generated one.
    """

    def generate(
        self,
        params: SimulationParams,
        rng: np.random.Generator,
    ) -> ModelState:
        """Return the configured explicit state.

        Args:
            params: Parameters containing validated ``initial_frequencies``.
            rng: Unused shared generator, accepted by the strategy contract.

        Returns:
            A generation-zero model state.

        Raises:
            ValueError: If no explicit frequencies were configured.
        """
        del rng
        if params.initial_frequencies is None:
            raise ValueError("explicit initial conditions require p_0")
        state = ModelState(
            loci=params.loci,
            frequencies=params.initial_frequencies,
            generation=0,
        )
        state.validate_support(params.population_sizes)
        return state


@dataclass(frozen=True, slots=True)
class EquilibrationOutcome:
    """One panmictic ancestral phase's own recorded outcome.

    Returned alongside the split generation-zero state by
    `EquilibriumSplitInitialCondition.generate_with_outcome` — never by
    the bare `generate` (the `InitialConditionGenerator` Protocol
    method), which every other strategy also satisfies and which has no
    return-shape room for this. `fim.engine`'s own run orchestration is
    the one caller that needs this: it threads these three values into
    `RunManifest`'s own `equilibrium_generation_count`/`equilibrium_
    final_heterozygosity` fields and persists `history` in full as the
    `equilibrium_trajectory.jsonl` sibling artifact
    (`20260907-claude-sonnet-5-equilibrium-split-design.md` §5).

    Args:
        generation_count: The generation at which the ancestral phase's
            own `H_S` trailing window stabilized —
            `ConvergenceMonitor.outcome().generation`.
        final_heterozygosity: The ancestral population's own `H_S` at
            that generation — the actual value split into `d` demes,
            not a value the caller chose.
        history: `H_S` at every recorded generation of the ancestral
            phase, oldest first, generation zero (the pre-drift
            Dirichlet draw) included — `ConvergenceMonitor.history`.
    """

    generation_count: int
    final_heterozygosity: float
    history: tuple[float, ...]


class EquilibriumSplitInitialCondition:
    """Simulate one ancestral population to equilibrium, then found every deme from it.

    P1 item 5 of the 2026-09-06 open-issues doc, and its own design doc
    (`20260907-claude-sonnet-5-equilibrium-split-design.md`): "run a
    single panmictic population of size N·d forward
    until convergence (verified via the Ht parameter), then split it
    into d demes." Two real simulation phases, not one:

    1. **Equilibrate.** Build one deme of size `sum(params.
       population_sizes)` — the same total gene-copy count the real
       `d`-deme run will have, just concentrated in one deme — starting
       from the identical continuous Dirichlet draw
       `DirichletInitialCondition` uses (`_dirichlet_locus_maps`,
       above), then repeatedly apply `fim.model.operators.mutate`/
       `drift` (no `migrate` — there is nothing to migrate between with
       one deme) until this ancestral population's own mean `H_S`
       (identical to `H_T` at one deme; every *differentiation*
       statistic is undefined there) stabilizes under a
       `TrailingWindowCriterion`, or raise if it never does within
       `max_generations` (the design doc's own decision 4: unlike the
       main run's own benign generation-cap outcome, this cap is fatal
       — a `d`-deme run silently founded from a non-equilibrium ancestral
       population would defeat the one thing this mode exists to
       guarantee).
    2. **Split.** By the time equilibration stops, the ancestral
       population is already a real, finite, `drift`-realized
       population — not the continuous "belief" every other strategy's
       own generation zero is (this class's own module docstring). Each
       output deme draws its own `N` gene copies *without replacement*
       from that one finite pool, independently per locus (this model
       has no notion of an individual's genotype linking its loci
       together — every locus is already independent everywhere else in
       this codebase, so there is no "same individuals" for a joint
       draw to preserve), via `numpy.random.Generator.
       multivariate_hypergeometric`, deme by deme in order, each draw
       depleting what remains for the next. The last deme receives
       exactly what is left, so the total gene count is conserved
       exactly, not just in expectation — a genuine founder effect: real
       divergence between demes at generation zero, from finite-sampling
       noise at the moment of founding, which no other strategy in this
       module can produce.

    Runs on its own random-number stream, independent of the real run's
    own — `numpy.random.SeedSequence(params.seed).spawn(1)[0]`, not
    `params.seed` directly. Reusing the bare seed here would make the
    founder split's own randomness a shifted echo of what the real
    run's own early drift would have drawn (two `PCG64` generators
    built from the identical seed integer produce the identical draw
    sequence); a spawned child stream is decorrelated from its own
    plain-seed sibling by design, while remaining fully deterministic —
    the same parent seed always yields the same child, so reproducibility
    is unaffected, and it composes for free with a batch's own
    per-replicate seed (`params.seed + replicate_index`): each
    replicate's own equilibration derives from *that replicate's own*
    seed, so a batch's replicates stay genuinely independent trials.

    Supports `mutation_model="infinite_alleles"` (the default) only.
    `"finite_alleles"` needs `fim.engine._build_finite_allele_spaces`,
    which `fim.engine` itself imports `generate_initial_state` from this
    module — importing it back here would be circular. Raises a clear
    `ValueError` for that combination rather than silently ignoring the
    configured mutation model; lifting this is future work, gated on
    moving that function to a shared module both sides can import.
    """

    def __init__(
        self,
        *,
        convergence_window: int,
        convergence_tolerance: float,
        max_generations: int,
    ) -> None:
        """Configure one ancestral-equilibration phase.

        Args:
            convergence_window: Trailing-window size for the ancestral
                phase's own `H_S` stability check
                (`TrailingWindowCriterion`) — independent of the real
                run's own `convergence_window`, since the two phases run
                at different population scales with no principled reason
                to share a threshold.
            convergence_tolerance: Trailing-window tolerance for the
                same check.
            max_generations: Hard cap on the ancestral phase's own
                generation count. Reaching it without the trailing
                window stabilizing is fatal (`generate_with_outcome`
                raises `ValueError`), not the main run's own benign
                cap outcome.

        Raises:
            ValueError: If `convergence_window`/`convergence_tolerance`
                is invalid (`TrailingWindowCriterion`'s own validation),
                or `max_generations` is not a positive integer.
        """
        self._criterion = TrailingWindowCriterion(
            convergence_window, convergence_tolerance
        )
        if isinstance(max_generations, bool) or not isinstance(max_generations, int):
            raise ValueError("max_generations must be a positive integer")
        if max_generations < 1:
            raise ValueError("max_generations must be a positive integer")
        self._max_generations = max_generations

    def generate(
        self,
        params: SimulationParams,
        rng: np.random.Generator,
    ) -> ModelState:
        """Return the split generation-zero state, discarding its own outcome.

        The `InitialConditionGenerator` Protocol method — satisfied so
        every caller that only ever wants a bare `ModelState` (the GUI's
        own initial-state preview endpoints among them) transparently
        supports this mode too. `fim.engine`'s own run orchestration
        calls `generate_with_outcome` directly instead, to persist what
        this method throws away.
        """
        state, _outcome = self.generate_with_outcome(params, rng)
        return state

    def generate_with_outcome(
        self,
        params: SimulationParams,
        rng: np.random.Generator,
    ) -> tuple[ModelState, EquilibrationOutcome]:
        """Equilibrate one ancestral population, then split it into `params.d` demes.

        Args:
            params: The real, validated `d`-deme run's own parameters —
                never a second, invalid `SimulationParams(d=1, ...)`;
                `d` must always be at least 2 (`SimulationParams.
                __post_init__`), so the ancestral phase runs directly
                against a bare `ModelState`/`fim.model.operators`
                primitives instead, extracting only the scalar fields it
                needs (`mu`, `loci`, `initial_allele_count`, `initial_
                concentration`, `population_sizes`) from `params`.
            rng: Unused directly — accepted by the strategy contract,
                but every draw this method makes comes from its own
                independent, `params.seed`-derived stream (this class's
                own docstring). Kept as a parameter anyway so this
                method's signature matches every other strategy's
                `generate`, and so a future caller cannot accidentally
                assume it is unused by inspecting the signature alone.

        Returns:
            The split, `params.d`-deme generation-zero state, and the
            ancestral phase's own recorded outcome.

        Raises:
            ValueError: If `params.mutation_model != "infinite_alleles"`,
                or the ancestral phase does not converge within
                `max_generations`.
        """
        del rng
        if params.mutation_model != "infinite_alleles":
            raise ValueError(
                "equilibrium-split initial conditions currently support "
                "only mutation_model='infinite_alleles'"
            )
        equilibrium_rng = np.random.Generator(
            np.random.PCG64(np.random.SeedSequence(params.seed).spawn(1)[0])
        )
        total_population = sum(params.population_sizes)

        allele_ids = founding_allele_ids(params.initial_allele_count)
        concentration = np.full(
            params.initial_allele_count,
            params.initial_concentration,
            dtype=np.float64,
        )
        ancestral_locus_maps = _dirichlet_locus_maps(
            params.loci, allele_ids, concentration, equilibrium_rng
        )
        state = ModelState(
            loci=params.loci,
            frequencies=(ancestral_locus_maps,),
            generation=0,
        )
        state.validate_support((total_population,))

        highest_initial_id = max(
            (int(allele_id) for locus in ancestral_locus_maps for allele_id in locus),
            default=MINTED_ID_START - 1,
        )
        registry = AlleleRegistry(start=max(MINTED_ID_START, highest_initial_id + 1))
        monitor = ConvergenceMonitor(
            self._criterion, max_generations=self._max_generations
        )

        def _mean_h_s(candidate: ModelState) -> float:
            return (
                math.fsum(
                    h_s([candidate.frequency_map(0, locus_index)])
                    for locus_index in range(candidate.locus_count)
                )
                / candidate.locus_count
            )

        monitor.record(state.generation, _mean_h_s(state))
        while not monitor.should_stop():
            state = mutate(
                state, params.mu, total_population, registry, equilibrium_rng
            )
            state = drift(state, total_population, equilibrium_rng)
            monitor.record(state.generation, _mean_h_s(state))

        outcome = monitor.outcome()
        if not outcome.converged or outcome.generation is None:
            raise ValueError(
                "ancestral population did not reach equilibrium within "
                f"max_generations={self._max_generations} generations "
                f"(H_S trailing history: {monitor.history[-5:]!r}); raise "
                "equilibrium_max_generations and retry"
            )

        split_state = self._split(state, params, equilibrium_rng)
        equilibration_outcome = EquilibrationOutcome(
            generation_count=outcome.generation,
            final_heterozygosity=monitor.history[-1],
            history=monitor.history,
        )
        return split_state, equilibration_outcome

    @staticmethod
    def _split(
        ancestral: ModelState,
        params: SimulationParams,
        rng: np.random.Generator,
    ) -> ModelState:
        """Partition the converged ancestral population into `params.d` demes.

        One independent `multivariate_hypergeometric` draw per locus,
        deme by deme in order, depleting that locus's own remaining pool
        each time; the last deme receives exactly what remains. Every
        locus's own pool is independent of every other locus's — this
        model has no notion of one individual's genotype linking its
        loci together, so there is no "same individuals" for a joint,
        cross-locus draw to preserve.
        """
        population_sizes = params.population_sizes
        total_population = sum(population_sizes)
        deme_locus_maps: list[list[Mapping[AlleleId, float]]] = [
            [] for _ in population_sizes
        ]
        for locus_index in range(ancestral.locus_count):
            ancestral_map = ancestral.frequency_map(0, locus_index)
            # Ascending allele-id order — the same canonical visiting
            # order `fim.model.operators.drift`'s own docstring
            # establishes, so a future vectorized/JIT counterpart to
            # this method can agree with this one exactly, the same way
            # `drift`/`mutate` already do across this project's backends.
            ordered_alleles = sorted(ancestral_map, key=int)
            remaining = np.array(
                [
                    round(ancestral_map[allele] * total_population)
                    for allele in ordered_alleles
                ],
                dtype=np.int64,
            )
            if int(remaining.sum()) != total_population:
                raise RuntimeError(
                    "equilibrium split: reconstructed allele counts "
                    f"({int(remaining.sum())}) do not match the ancestral "
                    f"population size ({total_population}) at locus index "
                    f"{locus_index} -- the ancestral state was not a real "
                    "drift realization"
                )
            for deme_index, deme_size in enumerate(population_sizes[:-1]):
                drawn = rng.multivariate_hypergeometric(remaining, deme_size)
                remaining = remaining - drawn
                deme_locus_maps[deme_index].append(
                    {
                        allele: int(count) / deme_size
                        for allele, count in zip(ordered_alleles, drawn, strict=True)
                        if count > 0
                    }
                )
            last_deme_size = population_sizes[-1]
            deme_locus_maps[-1].append(
                {
                    allele: int(count) / last_deme_size
                    for allele, count in zip(ordered_alleles, remaining, strict=True)
                    if count > 0
                }
            )
        split_state = ModelState(
            loci=ancestral.loci,
            frequencies=tuple(tuple(locus_maps) for locus_maps in deme_locus_maps),
            generation=0,
        )
        split_state.validate_support(population_sizes)
        return split_state


def generate_initial_state(
    params: SimulationParams,
    rng: np.random.Generator | None = None,
) -> ModelState:
    """Generate generation zero with the configured strategy.

    This is the one function most callers actually use — it picks
    `EquilibriumSplitInitialCondition`, `DirichletInitialCondition`, or
    `ExplicitInitialCondition` automatically, based on whether `params`
    has the `equilibrium_*` fields or an explicit ``p_0`` table
    configured, so a caller never needs to choose between the three
    itself. A caller that needs `EquilibriumSplitInitialCondition`'s own
    richer `EquilibrationOutcome` (`fim.engine`'s own run orchestration,
    to persist it) calls that class directly instead — this function
    always returns a bare `ModelState`, discarding it, exactly like
    `EquilibriumSplitInitialCondition.generate` itself does.

    Args:
        params: Validated simulation parameters.
        rng: Optional run generator. A PCG64 generator is created from
            ``params.seed`` only when called outside the engine — "PCG64"
            is the specific, high-quality pseudo-random number algorithm
            NumPy recommends by default; passing the same ``params.seed``
            always produces the exact same generator state, which is what
            makes a run reproducible. Unused by `EquilibriumSplitInitialCondition`
            (see its own docstring for why); still accepted here so every
            strategy shares one dispatch signature.

    Returns:
        A reproducible generation-zero state.
    """
    run_rng = (
        rng if rng is not None else np.random.Generator(np.random.PCG64(params.seed))
    )
    generator: InitialConditionGenerator
    if (
        params.equilibrium_convergence_window is not None
        and params.equilibrium_convergence_tolerance is not None
        and params.equilibrium_max_generations is not None
    ):
        generator = EquilibriumSplitInitialCondition(
            convergence_window=params.equilibrium_convergence_window,
            convergence_tolerance=params.equilibrium_convergence_tolerance,
            max_generations=params.equilibrium_max_generations,
        )
    elif params.initial_frequencies is None:
        generator = DirichletInitialCondition()
    else:
        generator = ExplicitInitialCondition()
    return generator.generate(params, run_rng)


def founding_condition_for_heterozygosity(
    heterozygosity: float,
    *,
    deme_count: int,
    locus_count: int = 1,
) -> InitialFrequencies:
    """Build a `p_0` table where every deme is an identical ancestral copy.

    Ryman & Leimar (2008)'s own gene-identity recursion starts every
    trajectory from `Gs(0) = Gd(0) = 1 - H_S(0)` — every deme founded as
    an identical copy of one ancestral population at a specified
    heterozygosity, before any migration/mutation/drift has had a chance
    to make the demes diverge from each other. `fim`'s own two existing
    founding strategies (`DirichletInitialCondition`'s independent random
    draw per deme, `ExplicitInitialCondition`'s arbitrary caller-supplied
    table) can realize this only by accident, never by construction —
    this project's own Ryman & Leimar remediation, `R7`
    (`dev/doc/apps/selby/jost-finite-island-model/20260903-claude-opus-
    5-gene-identity-recursion-fim-implications.md` §9), exists
    specifically so a trajectory comparison against that recursion
    (`R5`) never has to argue about whether an observed early-generation
    gap is a real engine defect or just a founding-condition mismatch.

    The returned table plugs directly into `SimulationParams.
    initial_frequencies`; `generate_initial_state` then dispatches it to
    `ExplicitInitialCondition` exactly like any other explicit `p_0`.

    How the target is realized: `heterozygosity == 0.0` is a single
    fixed allele, trivially. Otherwise, the fewest alleles that can
    reach the target at all is `ceil(1 / (1 - heterozygosity))` — the
    same identity a uniform draw over that many equally common alleles
    would give — split into that many `- 1` "minor" alleles at one
    shared frequency and one "major" allele holding the remainder,
    solved for the exact minor frequency that reaches `heterozygosity`
    precisely (a straightforward quadratic; see `_ancestral_allele_
    frequencies`'s own docstring for the derivation). Every deme, and
    every locus within each deme, gets an independent copy of the
    identical distribution — matching `founding_allele_ids`'s own
    per-locus-relative allele-identity convention, the same one
    `DirichletInitialCondition` already uses.

    A high target heterozygosity needs proportionally many alleles
    (`heterozygosity=0.99` needs 100) — an intrinsic property of what
    heterozygosity means, not a limitation of this construction:
    reaching high heterozygosity at all requires many, comparably
    common alleles, by definition.

    Args:
        heterozygosity: The ancestral population's own expected
            heterozygosity, `H_S(0)`. Must be in `[0, 1)` — `1` itself
            is the unreachable supremum every finite allele count only
            ever approaches (`heterozygosity.heterozygosity`'s own
            docstring).
        deme_count: How many identical deme copies to build.
        locus_count: How many loci to build the identical distribution
            for, independently at each. Defaults to `1`.

    Returns:
        An `InitialFrequencies` table: `deme_count` identical copies,
        each `locus_count` independent copies of the same distribution.

    Raises:
        ValueError: If `heterozygosity` is not in `[0, 1)`, or
            `deme_count`/`locus_count` is not a positive integer.
    """
    if isinstance(heterozygosity, bool) or not isinstance(heterozygosity, int | float):
        raise ValueError("heterozygosity must be a real number")
    if not math.isfinite(heterozygosity) or not 0.0 <= heterozygosity < 1.0:
        raise ValueError("heterozygosity must be in [0, 1)")
    if (
        isinstance(deme_count, bool)
        or not isinstance(deme_count, int)
        or deme_count < 1
    ):
        raise ValueError("deme_count must be a positive integer")
    if (
        isinstance(locus_count, bool)
        or not isinstance(locus_count, int)
        or locus_count < 1
    ):
        raise ValueError("locus_count must be a positive integer")
    # A fresh `_ancestral_allele_frequencies` call per (deme, locus) pair,
    # not one shared tuple reused across demes: every dict below is
    # already read-only in practice once `ModelState` wraps it, but this
    # function's own docstring promises independent copies, not shared
    # objects a caller could accidentally alias.
    return tuple(
        tuple(
            _ancestral_allele_frequencies(float(heterozygosity))
            for _ in range(locus_count)
        )
        for _ in range(deme_count)
    )


def _ancestral_allele_frequencies(heterozygosity: float) -> dict[AlleleId, float]:
    """Return one locus's own ancestral frequency map at a given heterozygosity.

    `minor_count = ceil(1 / (1 - heterozygosity)) - 1` equally frequent
    "minor" alleles, each at frequency `q`, plus one "major" allele
    holding `1 - minor_count * q` — chosen so that even the smallest
    allele count admitting a real solution is used, never more. Summing
    squared frequencies (`fim.statistics.differentiation.identity`'s own
    `Σp²`) and setting it equal to `1 - heterozygosity` gives one
    quadratic in `q`:

    ``minor_count * (minor_count + 1) * q² - 2 * minor_count * q
    + heterozygosity = 0``

    (the `+ heterozygosity` constant term is `1 - (1 - heterozygosity)`,
    the target identity's own complement) — solved directly via the
    quadratic formula, taking the smaller of its two real roots (the
    other root's own `q` exceeds `1 / minor_count`, giving a negative
    major-allele frequency, not a second valid solution). Verified
    numerically to reproduce the requested `heterozygosity` to float
    precision across a broad sweep before being written here — see
    `test_founding_condition_for_heterozygosity_matches_the_target`.
    """
    if heterozygosity == 0.0:
        return {AlleleId(0): 1.0}
    target_identity = 1.0 - heterozygosity
    minor_count = math.ceil(1.0 / target_identity) - 1
    quadratic_a = minor_count * (minor_count + 1)
    quadratic_b = -2.0 * minor_count
    quadratic_c = heterozygosity
    discriminant = quadratic_b * quadratic_b - 4.0 * quadratic_a * quadratic_c
    minor_frequency = (-quadratic_b - math.sqrt(max(discriminant, 0.0))) / (
        2.0 * quadratic_a
    )
    major_frequency = 1.0 - minor_count * minor_frequency
    frequencies = {AlleleId(index): minor_frequency for index in range(minor_count)}
    frequencies[AlleleId(minor_count)] = major_frequency
    return frequencies
