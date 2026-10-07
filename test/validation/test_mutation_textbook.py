"""Seeded validation of the textbook Wright-Fisher mutation step.

`fim.model.operators.step` runs migrate, drift, mutate: the new
generation's `N` gene copies are drawn from the post-migration pool and
each copy then mutates on its own with probability `mu`. For two gene
copies drawn with replacement from one deme (`within`, `sum_k x_k^2`) and
one copy from each of two demes (`between`), the textbook recursions are

    within'  = 1/N + (1 - 1/N) (1 - mu)^2 within_migrated
    between' = (1 - mu)^2 between_migrated

(infinite alleles), or the same with `(1 - mu)^2` replaced by the
symmetric K-allele pair factors (finite alleles). These tests simulate
the real operators and compare:

- the long-run mean identity of a panmictic population (infinite and
  finite alleles) and of an island model with the textbook fixed point,
  within a few standard errors (batch means over the stationary run, so
  autocorrelation is accounted for);
- the mean trajectory over many independent loci with the closed forms
  `panmictic_equilibration` and `identity_recursion` produce.

Each panmictic check also asserts that the earlier, non-textbook
recursion (`J' = 1/N + (1 - 1/N)[((1 - mu)^2 + mu(1 - mu)/N) J + mu/N]`,
the proportional-mass mutation step this project used to run) is
excluded, so a regression back to it would fail here.

Every test is seeded, so its result is a pure function of the commit.
Parameters are small (`N = 20`) on purpose: the gap between the textbook
and the earlier recursion is `O(mu / (1 + 2 N mu))`, largest at small `N`.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
import pytest

from fim.convergence.defaults import panmictic_equilibration
from fim.model.allele import (
    AlleleId,
    AlleleRegistry,
    FiniteAlleleRegistry,
    FiniteAlleleSpace,
)
from fim.model.locus import LocusSpec, finite_allele_capacity
from fim.model.operators import drift, mutate, step
from fim.model.params import SimulationParams
from fim.model.state import ModelState
from fim.statistics import identity_recursion

pytestmark = pytest.mark.statistical

# Width of every band, in standard errors.
_BAND = 4.0

# Number of batches for the batch-means standard error of a long-run mean.
_BATCHES = 40


def _fixed_state(loci: tuple[LocusSpec, ...], d: int) -> ModelState:
    """Return `d` demes, every locus fixed for one shared allele (identity 1)."""
    return ModelState(
        loci=loci,
        frequencies=tuple(tuple({AlleleId(0): 1.0} for _ in loci) for _ in range(d)),
    )


def _identities(state: ModelState) -> tuple[np.ndarray, np.ndarray]:
    """Return per-locus mean within- and between-deme identity.

    Args:
        state: Any state.

    Returns:
        `(within, between)`, one entry per locus: `sum_k x_k^2` averaged
        over demes, and `sum_k x_k y_k` averaged over unordered deme
        pairs (zeros for one deme).
    """
    d = state.deme_count
    within = np.zeros(state.locus_count)
    between = np.zeros(state.locus_count)
    for locus in range(state.locus_count):
        maps = [state.frequency_map(deme, locus) for deme in range(d)]
        within[locus] = sum(sum(v * v for v in m.values()) for m in maps) / d
        if d > 1:
            pairs = [
                sum(v * maps[j].get(a, 0.0) for a, v in maps[i].items())
                for i in range(d)
                for j in range(i + 1, d)
            ]
            between[locus] = sum(pairs) / len(pairs)
    return within, between


def _simulate_panmictic(
    *,
    size: int,
    mu: float,
    loci: int,
    generations: int,
    seed: int,
    rng: Callable[[int], np.random.Generator],
    capacity_length: int | None = None,
) -> np.ndarray:
    """Run one deme through drift then mutate; return per-locus identities.

    The single-deme case of `step` (no migration), exactly as the
    equilibrium-split ancestral phase runs it.

    Args:
        size: Gene copies `N`.
        mu: Per-copy mutation probability.
        loci: Independent loci (replicates within one run).
        generations: Generations to run.
        seed: Seed for the run's generator.
        rng: The test suite's sanctioned generator factory.
        capacity_length: Locus length for the finite-alleles model
            (`4 ** length` states); `None` for infinite alleles.

    Returns:
        `(generations + 1, loci)` within-deme identities, generation 0
        first.
    """
    length = capacity_length if capacity_length is not None else 100
    specs = tuple(LocusSpec(index + 1, length) for index in range(loci))
    state = _fixed_state(specs, 1)
    registry = AlleleRegistry()
    finite_alleles = (
        FiniteAlleleRegistry(
            {
                spec.locus_id: FiniteAlleleSpace(
                    finite_allele_capacity(length), [AlleleId(0)]
                )
                for spec in specs
            }
        )
        if capacity_length is not None
        else None
    )
    generator = rng(seed)
    history = np.empty((generations + 1, loci))
    history[0] = _identities(state)[0]
    for generation in range(1, generations + 1):
        state = drift(state, size, generator)
        state = mutate(
            state, mu, size, registry, generator, finite_alleles=finite_alleles
        )
        history[generation] = _identities(state)[0]
    return history


def _simulate_island(
    *,
    params: SimulationParams,
    generations: int,
    rng: Callable[[int], np.random.Generator],
) -> tuple[np.ndarray, np.ndarray]:
    """Run `fim.model.operators.step`; return per-locus identities.

    Args:
        params: An infinite-alleles island configuration.
        generations: Generations to run.
        rng: The test suite's sanctioned generator factory.

    Returns:
        `(within, between)`, each `(generations + 1, loci)`.
    """
    state = _fixed_state(params.loci, params.d)
    registry = AlleleRegistry()
    generator = rng(params.seed)
    loci = len(params.loci)
    within = np.empty((generations + 1, loci))
    between = np.empty((generations + 1, loci))
    within[0], between[0] = _identities(state)
    for generation in range(1, generations + 1):
        state = step(state, params, registry, generator)
        within[generation], between[generation] = _identities(state)
    return within, between


def _batch_mean(series: np.ndarray) -> tuple[float, float]:
    """Return a stationary series' mean and its batch-means standard error."""
    batches = np.array([chunk.mean() for chunk in np.array_split(series, _BATCHES)])
    return float(series.mean()), float(batches.std(ddof=1) / math.sqrt(_BATCHES))


def _earlier_panmictic_fixed_point(size: int, mu: float) -> float:
    """Return the fixed point of the earlier, non-textbook mutation step."""
    survival = (1.0 - mu) ** 2 + mu * (1.0 - mu) / size
    keep = 1.0 - 1.0 / size
    return (1.0 / size + keep * mu / size) / (1.0 - keep * survival)


def _kam_panmictic_fixed_point(size: int, mu: float, capacity: int) -> float:
    """Return the textbook symmetric K-allele panmictic fixed point.

    Each copy keeps its state with probability `1 - mu`, else moves to
    one of the other `K - 1` uniformly. Two distinct copies in state
    identity `J` are identical afterward with probability `t + (s - t) J`,
    `s = (1 - mu)^2 + mu^2 / (K - 1)`, `t = 2 mu (1 - mu) / (K - 1) +
    mu^2 (K - 2) / (K - 1)^2`; so `J' = 1/N + (1 - 1/N)(t + (s - t) J`.
    """
    other = capacity - 1
    same = (1.0 - mu) ** 2 + mu**2 / other
    cross = 2.0 * mu * (1.0 - mu) / other + mu**2 * (capacity - 2) / other**2
    keep = 1.0 - 1.0 / size
    return (1.0 / size + keep * cross) / (1.0 - keep * (same - cross))


def test_panmictic_long_run_identity_matches_the_textbook_recursion(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Infinite alleles: long-run mean `H` is `1 - F*` of the textbook recursion.

    `panmictic_equilibration` supplies the textbook prediction
    (`1 - F*`, `F* = (1/N) / (1 - (1 - 1/N)(1 - mu)^2)`); the earlier
    recursion's prediction lies outside the band.
    """
    size, mu, burn_in = 20, 0.025, 300
    history = _simulate_panmictic(
        size=size, mu=mu, loci=40, generations=6000, seed=20261007, rng=rng
    )
    mean_identity, error = _batch_mean(history[burn_in:].mean(axis=1))
    prediction = panmictic_equilibration(
        total_size=size, mutation_rates=[mu], tolerance=0.01
    )

    assert 1.0 - mean_identity == pytest.approx(
        prediction.expected_heterozygosity, abs=_BAND * error
    )
    assert abs(_earlier_panmictic_fixed_point(size, mu) - mean_identity) > (
        _BAND * error
    )


def test_panmictic_finite_alleles_identity_matches_the_k_allele_recursion(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Finite alleles (`K = 4`): long-run identity in state is the K-allele one.

    A one-base locus has four states; each mutant copy moves to one of
    the other three uniformly (`FiniteAlleleSpace.mutate_target`).
    """
    size, mu, burn_in = 20, 0.025, 300
    history = _simulate_panmictic(
        size=size,
        mu=mu,
        loci=40,
        generations=6000,
        seed=20261008,
        rng=rng,
        capacity_length=1,
    )
    mean_identity, error = _batch_mean(history[burn_in:].mean(axis=1))

    assert mean_identity == pytest.approx(
        _kam_panmictic_fixed_point(size, mu, finite_allele_capacity(1)),
        abs=_BAND * error,
    )


def test_island_long_run_identities_match_the_textbook_recursion(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """Island model: long-run within and between identity are the fixed point.

    `identity_recursion`'s fixed point is the textbook island-model one
    (migrate, drift, then per-copy mutation); the simulation runs the
    engine's own `step`.
    """
    size, m, mu, d = 20, 0.05, 0.0125, 4
    params = SimulationParams(
        gene_copies=size,
        m=m,
        mu=mu,
        d=d,
        seed=20261009,
        loci=tuple(LocusSpec(index + 1, 100) for index in range(15)),
    )
    burn_in = 500
    within, between = _simulate_island(params=params, generations=4000, rng=rng)
    mean_within, within_error = _batch_mean(within[burn_in:].mean(axis=1))
    mean_between, between_error = _batch_mean(between[burn_in:].mean(axis=1))
    fixed_within, fixed_between = identity_recursion(size, m, mu, d).fixed_point

    assert mean_within == pytest.approx(fixed_within, abs=_BAND * within_error)
    assert mean_between == pytest.approx(fixed_between, abs=_BAND * between_error)


def test_closed_form_trajectories_match_the_simulated_mean(
    rng: Callable[[int], np.random.Generator],
) -> None:
    """The closed forms track the mean over independent loci, generation by generation.

    Every locus starts fixed for one allele (identity 1). At each checked
    generation the mean over loci is compared with the closed form, its
    standard error taken across loci (independent replicates).
    """
    checked = (1, 2, 5, 10, 20, 40, 80)

    # Panmictic: `F_t = F* + (1 - F*) rho^t` from `panmictic_equilibration`.
    size, mu = 20, 0.025
    panmictic = _simulate_panmictic(
        size=size, mu=mu, loci=400, generations=max(checked), seed=20261010, rng=rng
    )
    prediction = panmictic_equilibration(
        total_size=size, mutation_rates=[mu], tolerance=0.01
    )
    rho = 1.0 - 1.0 / prediction.relaxation_time
    fixed = 1.0 - prediction.expected_heterozygosity
    for generation in checked:
        values = panmictic[generation]
        error = values.std(ddof=1) / math.sqrt(values.shape[0])
        expected = fixed + (1.0 - fixed) * rho**generation
        assert values.mean() == pytest.approx(expected, abs=_BAND * error), generation

    # Island: `identity_recursion`'s own closed form, from (1, 1).
    island_m, island_mu, island_d = 0.1, 0.0125, 3
    params = SimulationParams(
        gene_copies=size,
        m=island_m,
        mu=island_mu,
        d=island_d,
        seed=20261011,
        loci=tuple(LocusSpec(index + 1, 100) for index in range(150)),
    )
    within, between = _simulate_island(params=params, generations=max(checked), rng=rng)
    recursion = identity_recursion(size, island_m, island_mu, island_d)
    for generation in checked:
        expected_within, expected_between = recursion.identities_after(
            generation, 1.0, 1.0
        )
        for values, expected in (
            (within[generation], expected_within),
            (between[generation], expected_between),
        ):
            error = values.std(ddof=1) / math.sqrt(values.shape[0])
            assert values.mean() == pytest.approx(expected, abs=_BAND * error), (
                generation
            )
