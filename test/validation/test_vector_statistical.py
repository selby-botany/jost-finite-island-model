"""Backend V reproduces the textbook infinite-alleles recursions, statistically.

The exactness tests (`test/model/test_vector_block.py`,
`test/engine/test_vector_parity.py`) prove Backend V equals Backends L and
G bit for bit. This file checks the other half: that the model all three
implement is the textbook one. Each case runs many independent loci in one
block (every locus is an independent replicate of the same process),
estimates an identity probability, and compares it with the exact expected
value, in units of its standard error across loci.

1. One population (`d = 1`), three mutation rates: the probability that two
   distinct gene copies are identical, `sum n_i (n_i - 1) / (N (N - 1))`,
   time-averaged at equilibrium, against the fixed point of
   `F' = (1 - mu)^2 [1/N + (1 - 1/N) F]`.
2. An island model (`d = 5`, `N = 100`, `m = 0.01`, `mu = 0.001`) from a
   monomorphic start: mean within- and between-deme identity at several
   generations against `fim.statistics.identity_recursion`, and the pooled
   `D = 1 - between / within`.
3. The dear-nolan-low worked example's own transient (`d = 5`,
   `m = 0.0001`, `mu = 0.000001`, two founding alleles with a
   Dirichlet(1, 1) frequency start).

Every run is seeded, so each outcome is a pure function of the commit; the
bound, |z| below 4, is wide enough that a correct implementation passes
for any reasonable seed and a biased one (a mutation step that adds or
loses identity at the `1 / N` scale) fails.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("numba")

from fim.model.allele import MINTED_ID_START, AlleleId
from fim.model.locus import LocusSpec
from fim.model.state import ModelState
from fim.model.vector_block import VectorBlock, VectorMigration
from fim.statistics.identity_recursion import identity_recursion

SEED = 20261008
Z_BOUND = 4.0
GENE_COPIES = 100

pytestmark = pytest.mark.statistical


def _block(
    frequencies: list[list[dict[int, float]]], rate: float, mu: float
) -> VectorBlock:
    """Build a block of independent loci from `frequencies[deme][locus]` maps."""
    demes = len(frequencies)
    loci = len(frequencies[0])
    state = ModelState(
        loci=tuple(LocusSpec(index + 1, 200) for index in range(loci)),
        frequencies=tuple(
            tuple({AlleleId(a): f for a, f in locus.items()} for locus in deme)
            for deme in frequencies
        ),
    )
    return VectorBlock.from_model_state(
        state,
        sizes=[GENE_COPIES] * demes,
        mutation_rates=[mu] * loci,
        migration=VectorMigration.from_parameter(rate, demes),
        mutation_model="infinite_alleles",
        next_id=MINTED_ID_START,
    )


def _monomorphic(loci: int, demes: int) -> list[list[dict[int, float]]]:
    """Every deme fixed for founding allele 0 at every locus."""
    return [[{0: 1.0} for _ in range(loci)] for _ in range(demes)]


def _within_between(block: VectorBlock) -> tuple[np.ndarray, np.ndarray]:
    """Per-locus mean within-deme and mean between-deme identity."""
    freq = block.freq
    demes = freq.shape[1]
    within = (freq * freq).sum(axis=2).mean(axis=1)
    pair_sums = np.einsum("ldc,lec->lde", freq, freq)
    off_diagonal = pair_sums.sum(axis=(1, 2)) - np.trace(pair_sums, axis1=1, axis2=2)
    return within, off_diagonal / (demes * (demes - 1))


def _z(estimate: float, standard_error: float, expected: float) -> float:
    """Return `(estimate - expected) / standard_error`."""
    return (estimate - expected) / standard_error


@pytest.mark.parametrize("mu", [0.0005, 0.0025, 0.01])
def test_one_population_identity_matches_the_textbook_fixed_point(mu: float) -> None:
    """Two distinct copies are identical with the recursion's equilibrium odds."""
    loci = 1000
    block = _block(_monomorphic(loci, 1), 0.0, mu)
    rng = np.random.Generator(np.random.PCG64(SEED))
    accumulated = np.zeros(loci)
    samples = 0
    for generation in range(1, 4001):
        block.advance(rng)
        if generation > 1000 and generation % 5 == 0:
            squares = (block.freq * block.freq).sum(axis=2)[:, 0]
            accumulated += (GENE_COPIES * squares - 1.0) / (GENE_COPIES - 1.0)
            samples += 1
    per_locus = accumulated / samples
    survival = (1.0 - mu) ** 2
    expected = survival / GENE_COPIES / (1.0 - survival * (1.0 - 1.0 / GENE_COPIES))
    standard_error = per_locus.std(ddof=1) / np.sqrt(loci)
    assert abs(_z(per_locus.mean(), standard_error, expected)) < Z_BOUND


def _check_island_trajectory(
    block: VectorBlock,
    rng: np.random.Generator,
    *,
    m: float,
    mu: float,
    start_within: float,
    start_between: float,
    checkpoints: tuple[int, ...],
) -> list[float]:
    """Return every |z| for within, between and pooled `D` at the checkpoints."""
    demes = block.deme_count
    loci = block.freq.shape[0]
    recursion = identity_recursion(GENE_COPIES, m, mu, demes)
    deviations: list[float] = []
    generation = 0
    for checkpoint in checkpoints:
        while generation < checkpoint:
            block.advance(rng)
            generation += 1
        within, between = _within_between(block)
        expected_within, expected_between = recursion.identities_after(
            generation, start_within, start_between
        )
        for values, expected in (
            (within, expected_within),
            (between, expected_between),
        ):
            standard_error = values.std(ddof=1) / np.sqrt(loci)
            deviations.append(abs(_z(values.mean(), standard_error, expected)))
        # Pooled D (ratio of means); delta-method standard error.
        mean_within, mean_between = within.mean(), between.mean()
        estimate = 1.0 - mean_between / mean_within
        gradient = np.stack(
            [
                mean_between / mean_within**2 * np.ones(loci),
                -1.0 / mean_within * np.ones(loci),
            ]
        )
        residual = np.stack([within - mean_within, between - mean_between])
        standard_error = np.sqrt(((gradient * residual).sum(axis=0) ** 2).sum()) / loci
        deviations.append(
            abs(_z(estimate, standard_error, 1.0 - expected_between / expected_within))
        )
    return deviations


def test_island_model_identities_follow_the_recursion_from_a_monomorphic_start() -> (
    None
):
    """Within, between and pooled `D` track `identity_recursion` at 3 checkpoints."""
    loci, m, mu = 1000, 0.01, 0.001
    block = _block(_monomorphic(loci, 5), m, mu)
    rng = np.random.Generator(np.random.PCG64(SEED + 1))
    deviations = _check_island_trajectory(
        block,
        rng,
        m=m,
        mu=mu,
        start_within=1.0,
        start_between=1.0,
        checkpoints=(50, 200, 1000),
    )
    assert max(deviations) < Z_BOUND, deviations


def test_dear_nolan_low_transient_follows_the_recursion() -> None:
    """The worked example's own start and rates: transient to generation 5,000."""
    loci, m, mu, demes = 1000, 0.0001, 0.000001, 5
    rng = np.random.Generator(np.random.PCG64(SEED + 2))
    dirichlet = rng.dirichlet([1.0, 1.0], size=(demes, loci))
    start = [
        [
            {0: float(dirichlet[i, locus, 0]), 1: float(dirichlet[i, locus, 1])}
            for locus in range(loci)
        ]
        for i in range(demes)
    ]
    block = _block(start, m, mu)
    deviations = _check_island_trajectory(
        block,
        rng,
        m=m,
        mu=mu,
        start_within=2.0 / 3.0,
        start_between=0.5,
        checkpoints=(1, 1000, 5000),
    )
    assert max(deviations) < Z_BOUND, deviations
