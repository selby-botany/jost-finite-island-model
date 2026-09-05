"""Seeded initial-condition strategies.

Before a simulation can run at all, every deme needs a starting set of
allele frequencies at generation zero — this module is where that
starting point comes from. Two strategies are provided, both reachable
through `generate_initial_state`: `DirichletInitialCondition` (the
default) draws a random starting frequency for each deme/locus from a
symmetric Dirichlet distribution (a standard way of picking a random
set of proportions that add up to 1, used throughout population
genetics for exactly this purpose), while `ExplicitInitialCondition`
instead uses a frequency table the caller supplied directly in
`SimulationParams` (``p_0`` in a config file), for reproducing a
specific known starting condition rather than a random one.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Protocol

import numpy as np

from fim.model.allele import AlleleId, founding_allele_ids
from fim.model.params import InitialFrequencies, SimulationParams
from fim.model.state import ModelState


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
        demes: list[tuple[Mapping[AlleleId, float], ...]] = []
        for _deme in range(params.d):
            locus_maps: list[Mapping[AlleleId, float]] = []
            for _locus in params.loci:
                frequencies = rng.dirichlet(concentration)
                locus_maps.append(
                    {
                        allele_id: float(frequency)
                        for allele_id, frequency in zip(
                            allele_ids,
                            frequencies,
                            strict=True,
                        )
                    }
                )
            demes.append(tuple(locus_maps))
        state = ModelState(
            loci=params.loci,
            frequencies=tuple(demes),
            generation=0,
        )
        state.validate_support(params.population_sizes)
        return state


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


def generate_initial_state(
    params: SimulationParams,
    rng: np.random.Generator | None = None,
) -> ModelState:
    """Generate generation zero with the configured strategy.

    This is the one function most callers actually use — it picks
    `DirichletInitialCondition` or `ExplicitInitialCondition`
    automatically, based on whether `params` has an explicit ``p_0``
    table configured, so a caller never needs to choose between the two
    itself.

    Args:
        params: Validated simulation parameters.
        rng: Optional run generator. A PCG64 generator is created from
            ``params.seed`` only when called outside the engine — "PCG64"
            is the specific, high-quality pseudo-random number algorithm
            NumPy recommends by default; passing the same ``params.seed``
            always produces the exact same generator state, which is what
            makes a run reproducible.

    Returns:
        A reproducible generation-zero state.
    """
    run_rng = (
        rng if rng is not None else np.random.Generator(np.random.PCG64(params.seed))
    )
    generator: InitialConditionGenerator
    if params.initial_frequencies is None:
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
