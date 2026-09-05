"""Engine-level validation of scientific behavior, plus the internal oracle.

Scope against definition-of-done item 4 and test-plan sections 7.3-7.4.

This file mixes two kinds of test, deliberately labeled rather than
separated into different files (see `doc/fim-simulator-functional-api.md`
and `doc/fim-simulator-detailed-test-plan.md` for the full three-way
taxonomy this project uses -- fim functional, fim-gui functional, and
internal/deep):

* **Functional** tests run the real :func:`fim.engine.fim` simulator and
  assert its output directly against a published literature value or a
  public closed-form formula (``equilibrium_g_st``/``equilibrium_d`` and
  siblings, in `fim.statistics`) -- nothing in the assertion itself
  depends on any detail of the engine's current internal pipeline, so
  these assertions are expected to keep passing after a future core
  refactor that preserves the model's own scientific behavior. Each
  such test's own docstring says "**Functional**" explicitly.
* **Internal** tests and helpers assume today's specific Migrate ->
  Mutate -> Drift pipeline order: the exact per-generation identity
  recursion (:func:`_iterate_identities` and everything built on it --
  :func:`_pipeline_identity_dynamics`, :func:`_identity_fixed_point`,
  :func:`_iterate_pairwise_identities`, :func:`_pairwise_identity_fixed_
  point`) mirrors the engine's own current mechanics deliberately, so it
  can validate them precisely -- but that same specificity means these
  checks are expected to need re-deriving, not necessarily to signal an
  engine regression, once a future core refactor changes that internal
  order. Two of the five engine-level scenario tests (Dear-Nolan-high;
  see its own docstring) keep one internal, implementation-coupled
  assertion deliberately alongside a functional one, since it catches a
  regression class (a biased operator drifting away from a known fixed
  point) the functional check alone would not; every such assertion is
  labeled `# INTERNAL:` in place, distinct from the `# FUNCTIONAL:`
  assertions beside it.

Tolerance bands are derived analytically before any seed is chosen: each band
is ``k`` standard errors wide (``k = 5``), with the per-replicate spread taken
from an independent characterization pass and rounded up conservatively (the
``_SIGMA_*`` constants below). All statistical tests are seeded and therefore
bit-reproducible; the bands document the scientific margin, they are not tuned
to a particular realized draw.

The characterization pass behind every ``_SIGMA_*`` constant is versioned: the
program is :mod:`dev/bin/calibrate-statistical-bands`, and its raw output,
seeds, and environment fingerprint are retained in
``test/validation/statistical-calibration-evidence.json``, not merely
summarized here in a comment. An analytic bound was considered and is not
currently available -- the per-replicate ``G_ST``/``D`` estimate is a
ratio-of-means statistic sampled from a multi-generation stochastic
recursion, and a closed-form variance would need delta-method propagation
through that recursion's higher moments, which has not been derived. Re-run
the calibration script (never hand-edit the constants) if a scenario's
configuration changes enough that its characterized spread might no longer
be current; deliberately not wired into ``build`` or ``ci.yml`` -- a
characterization pass is itself stochastic by design, so it stays out of the
deterministic PR gate.
"""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, TypeAlias

import numpy as np
import pytest
from numpy.typing import NDArray

from fim.engine import EngineBackendChoice, fim, report_for_state
from fim.model.allele import AlleleId
from fim.model.locus import LocusSpec
from fim.model.params import InitialFrequencies, Migration, SimulationParams
from fim.model.state import ModelState
from fim.model.topology import dense_matrix_from_neighbors, stepping_stone_neighbors
from fim.persistence.store import TrajectoryRow
from fim.statistics import (
    equilibrium_d,
    equilibrium_g_st,
    equilibrium_shannon_differentiation,
    equilibrium_shannon_entropy_isolated,
    equilibrium_shannon_entropy_subpopulation,
    equilibrium_shannon_entropy_total,
    h_s,
    h_t,
    identity_recovery_trajectory,
    total_hill_number,
    within_hill_number,
)

# A dense d-by-d matrix, as opposed to `Migration`'s own scalar/sparse/
# topology-sugar variants -- what `_iterate_pairwise_identities` and its
# own siblings actually operate on (already-densified, e.g. by
# `_crow_aoki_torus_matrix`).
_FloatMatrix: TypeAlias = NDArray[np.float64]

# Width, in standard errors, of every statistical band (test-plan 7.1).
_BAND_SIGMA = 5.0

# Largest tolerated gap between the exact finite-N identity recursion and the
# O(1/N) diffusion formulas Eq. 2 / Eq. 4; the residual is O(1/N) and is at
# most ~0.0034 for the smallest scenario (N=100, d=4).
_ONE_OVER_N_TOL = 0.005

# Largest tolerated gap between `_iterate_identities` at a large but finite
# `d` and Whitlock (1992)'s own infinite-island (`d -> infinity`) closed-form
# trajectory; the residual is O(1/d) and is ~2.075e-6 at d=100,000 for the
# scenario `test_identity_recursion_reduces_to_whitlock_1992_infinite_island_
# trajectory` uses (see that test's own docstring for the full six-row
# sweep this was measured from).
_ONE_OVER_D_TOL = 1e-5

_CALIBRATION_DATA_PATH = Path(__file__).with_name(
    "statistical-calibration-evidence.json"
)


def _load_sigma_constants() -> dict[str, tuple[float, float]]:
    """Load assertion sigma constants from the generated calibration artifact."""
    payload = json.loads(_CALIBRATION_DATA_PATH.read_text(encoding="utf-8"))
    scenarios = payload["scenarios"]
    required = ("part_vi", "dear_nolan_low", "dear_nolan_high", "crow_aoki_torus")
    sigmas: dict[str, tuple[float, float]] = {}
    for scenario_name in required:
        scenario = scenarios[scenario_name]
        sigma_g = float(scenario["assertion_sigma_g"])
        sigma_d = float(scenario["assertion_sigma_d"])
        sigmas[scenario_name] = (sigma_g, sigma_d)
    return sigmas


try:
    _SIGMA_CONSTANTS = _load_sigma_constants()
except Exception as exc:  # pragma: no cover - hard failure before tests run
    raise RuntimeError(
        "Missing or invalid calibration artifact at "
        f"{_CALIBRATION_DATA_PATH}; regenerate with "
        "dev/bin/calibrate-statistical-bands"
    ) from exc

_SIGMA_PART_VI_G, _SIGMA_PART_VI_D = _SIGMA_CONSTANTS["part_vi"]
_SIGMA_DEAR_NOLAN_LOW_G, _SIGMA_DEAR_NOLAN_LOW_D = _SIGMA_CONSTANTS["dear_nolan_low"]
_SIGMA_DEAR_NOLAN_HIGH_G, _SIGMA_DEAR_NOLAN_HIGH_D = _SIGMA_CONSTANTS["dear_nolan_high"]
_SIGMA_CROW_AOKI_TORUS_G, _SIGMA_CROW_AOKI_TORUS_D = _SIGMA_CONSTANTS["crow_aoki_torus"]


def _load_shannon_sigma_constants() -> tuple[float, float, float]:
    """Load the Chao et al. Shannon-equilibrium scenario's own three sigmas.

    That scenario's own evidence entry uses a different schema from the
    four `G_ST`/`D` scenarios above (three named statistics, not two --
    see `dev/bin/calibrate-statistical-bands`'s own
    `_characterize_chao_shannon_equilibrium` docstring for why), so it is
    not one of `_load_sigma_constants`'s own `required` scenarios and
    gets this small, separate loader instead.
    """
    payload = json.loads(_CALIBRATION_DATA_PATH.read_text(encoding="utf-8"))
    scenario = payload["scenarios"]["chao_shannon_equilibrium"]
    return (
        float(scenario["total_entropy"]["recommended_sigma"]),
        float(scenario["subpopulation_entropy"]["recommended_sigma"]),
        float(scenario["shannon_differentiation"]["recommended_sigma"]),
    )


try:
    (
        _SIGMA_CHAO_SHANNON_TOTAL,
        _SIGMA_CHAO_SHANNON_SUBPOPULATION,
        _SIGMA_CHAO_SHANNON_DIFFERENTIATION,
    ) = _load_shannon_sigma_constants()
except Exception as exc:  # pragma: no cover - hard failure before tests run
    raise RuntimeError(
        "Missing or invalid calibration artifact at "
        f"{_CALIBRATION_DATA_PATH}; regenerate with "
        "dev/bin/calibrate-statistical-bands"
    ) from exc


class _DiscardingStore:
    """A trajectory store that accepts and drops every generation.

    Long multi-locus runs emit one row per nonzero allele frequency per
    generation; retaining them (as ``InMemoryTrajectoryStore`` does) would make
    memory and time grow with the horizon. These tests only read
    ``result.final_state``, so the trajectory is discarded.
    """

    def write_generation(
        self,
        run_id: str,
        generation: int,
        rows: Iterable[Mapping[str, Any]],
        *,
        validate: bool = True,
    ) -> None:
        """Accept and discard one generation's rows."""
        del run_id, generation, rows, validate

    def read(self, run_id: str) -> Iterator[TrajectoryRow]:
        """Yield nothing; no trajectory is retained."""
        del run_id
        return iter(())

    def discard(self, run_id: str) -> None:
        """Nothing to discard; nothing is ever retained in the first place."""
        del run_id


def _band(sigma: float, replicates: int) -> float:
    """Return the half-width of a ``_BAND_SIGMA``-sigma band on a mean.

    Args:
        sigma: Characterized per-replicate standard deviation.
        replicates: Number of independently seeded replicates averaged.

    Returns:
        ``_BAND_SIGMA * sigma / sqrt(replicates)``.
    """
    return _BAND_SIGMA * sigma / math.sqrt(replicates)


def _identity_coefficients(m: float, d: int) -> tuple[float, float, float, float]:
    """Return the symmetric-migration map coefficients for the identities.

    Symmetric island migration (retained fraction ``a = 1 - m``, per-other
    share ``b = m / (d - 1)``) is linear in frequencies, so it maps the
    quadratic identities ``jw`` and ``jb`` by fixed coefficients.

    Args:
        m: Symmetric migration rate.
        d: Number of demes.

    Returns:
        ``(within_from_within, within_from_between, between_from_within,
        between_from_between)``.
    """
    retained = 1.0 - m
    shared = m / (d - 1)
    within_from_within = retained * retained + shared * shared * (d - 1)
    within_from_between = 2.0 * retained * m + shared * shared * (d - 1) * (d - 2)
    between_from_within = 2.0 * retained * shared + shared * shared * (d - 2)
    between_from_between = (
        retained * retained
        + 2.0 * retained * shared * (d - 2)
        + shared * shared * ((d - 1) ** 2 - (d - 2))
    )
    return (
        within_from_within,
        within_from_between,
        between_from_within,
        between_from_between,
    )


def _mutation_survival(mu: float, population_size: int) -> float:
    """Return the exact per-generation mutation-survival factor.

    The identity recursion's mutation step scales existing pairwise
    identity mass by ``1 - k/N``, where ``k ~ Binomial(N, mu)`` counts
    this generation's mutating gene copies. Because the recursion tracks
    a *pairwise* (two-lineage) quantity, it needs this factor's second
    moment, ``E[(1 - k/N)^2]``, not just its mean ``1 - mu``.

    ``k/N`` is the sample mean of ``N`` i.i.d. Bernoulli(mu) mutation
    indicators, so ``1 - k/N`` is the sample mean of ``N`` i.i.d.
    Bernoulli(1 - mu) "no mutation" indicators. Squaring a sample mean is
    a degree-2 polynomial in those indicators, and by linearity its
    expectation depends on nothing beyond the mean and variance of one
    indicator -- both closed-form for a Bernoulli:

        E[(1 - k/N)^2] = (1 - mu)^2 + mu(1 - mu)/N

    This is an *exact* identity for every finite ``N >= 1``, not a
    truncated ``O(1/N)`` asymptotic series: there is no ``O(1/N^2)`` or
    higher term missing from it. (Were a higher power of this factor ever
    needed -- e.g. a three-lineage identity requiring
    ``E[(1 - k/N)^3]`` -- the exact value would still be a *finite*
    polynomial in ``1/N``, of degree one less than the power, not an
    infinite series; the second moment used here is that general
    pattern's ``m=2`` case, hence degree 1.)

    Args:
        mu: Per-copy mutation probability.
        population_size: Gene-copy count ``N``.

    Returns:
        ``E[(1 - k/N)^2]``, exactly.
    """
    return (1.0 - mu) ** 2 + mu * (1.0 - mu) / population_size


def _iterate_identities(
    *,
    population_size: int,
    m: float,
    mu: float,
    d: int,
    within_identity: float,
    between_identity: float,
    generations: int | None,
) -> tuple[float, float]:
    """Return ``(jw, jb)`` after the engine's exact identity recursion.

    Tracks the expected pairwise identity-in-state within one deme
    (``jw = E[sum_k x_k^2]``) and between two demes (``jb = E[sum_k x_k y_k]``)
    through one Migrate -> Mutate -> Drift generation, matching the operator
    order in :func:`fim.model.operators.step`.

    Migration maps the identities by the fixed coefficients of
    :func:`_identity_coefficients`. Mutation scales existing mass by the
    exact second moment :func:`_mutation_survival`, so the mutation step
    carries no ``O(1/N)`` residual of its own. Drift is exact
    Wright-Fisher multinomial resampling, for which
    ``E[sum x'^2] = 1/N + (1 - 1/N) E[sum x^2]`` and between-deme identity is
    unchanged in expectation.

    Args:
        population_size: Gene-copy count ``N`` per deme.
        m: Symmetric migration rate.
        mu: Per-copy mutation probability.
        d: Number of demes.
        within_identity: Initial ``jw``.
        between_identity: Initial ``jb``.
        generations: Fixed step count, or ``None`` to iterate to a fixed point.

    Returns:
        The ``(jw, jb)`` pair after the requested number of generations.
    """
    (
        within_from_within,
        within_from_between,
        between_from_within,
        between_from_between,
    ) = _identity_coefficients(m, d)
    survival = _mutation_survival(mu, population_size)
    inverse = 1.0 / population_size

    within = within_identity
    between = between_identity
    step = 0
    while True:
        migrated_within = within_from_within * within + within_from_between * between
        migrated_between = between_from_within * within + between_from_between * between
        next_within = inverse + (1.0 - inverse) * survival * migrated_within
        next_between = survival * migrated_between
        converged = (
            abs(next_within - within) < 1e-16 and abs(next_between - between) < 1e-16
        )
        within, between = next_within, next_between
        step += 1
        if generations is None:
            if converged or step >= 5_000_000:
                break
        elif step >= generations:
            break
    return within, between


def _identities_to_statistics(
    within: float, between: float, d: int
) -> tuple[float, float]:
    """Return pooled ``(G_ST, D)`` from within/between identity-in-state.

    The forms are the pooled-table expressions of ``fim.statistics``'s ``g_st``
    and ``jost_d`` written in terms of ``jw`` and ``jb``.

    Args:
        within: Within-deme identity ``jw``.
        between: Between-deme identity ``jb``.
        d: Number of demes.

    Returns:
        The pooled ``(G_ST, D)`` pair.
    """
    g_st = (
        ((d - 1) / d)
        * (within - between)
        / (1.0 - within / d - ((d - 1) / d) * between)
    )
    jost_d = 1.0 - between / within
    return g_st, jost_d


def _pipeline_identity_dynamics(
    *,
    population_size: int,
    m: float,
    mu: float,
    d: int,
    within_identity: float,
    between_identity: float,
    generations: int | None,
) -> tuple[float, float]:
    """Return ``(G_ST, D)`` from the engine's exact identity recursion.

    Thin wrapper: iterate the identities with :func:`_iterate_identities`, then
    convert to the pooled statistics with :func:`_identities_to_statistics`.

    Args:
        population_size: Gene-copy count ``N`` per deme.
        m: Symmetric migration rate.
        mu: Per-copy mutation probability.
        d: Number of demes.
        within_identity: Initial ``jw``.
        between_identity: Initial ``jb``.
        generations: Fixed step count, or ``None`` to iterate to a fixed point.

    Returns:
        The ``(G_ST, D)`` pair implied by the identities after the run.
    """
    within, between = _iterate_identities(
        population_size=population_size,
        m=m,
        mu=mu,
        d=d,
        within_identity=within_identity,
        between_identity=between_identity,
        generations=generations,
    )
    return _identities_to_statistics(within, between, d)


def _identity_fixed_point(
    *,
    population_size: int,
    m: float,
    mu: float,
    d: int,
) -> tuple[float, float]:
    """Return the recursion fixed point ``(jw*, jb*)`` of the identities.

    Iterates :func:`_iterate_identities` from the undifferentiated founding
    identities ``(jw, jb) = (1, 0)`` to convergence. These raw identities (not
    the derived ``G_ST`` / ``D``) are what :func:`_dn2_equilibrium_start` needs
    to build a near-equilibrium initial state.

    Args:
        population_size: Gene-copy count ``N`` per deme.
        m: Symmetric migration rate.
        mu: Per-copy mutation probability.
        d: Number of demes.

    Returns:
        The fixed-point ``(jw*, jb*)`` pair.
    """
    return _iterate_identities(
        population_size=population_size,
        m=m,
        mu=mu,
        d=d,
        within_identity=1.0,
        between_identity=0.0,
        generations=None,
    )


def _iterate_pairwise_identities(
    *,
    population_size: int,
    migration_matrix: Sequence[Sequence[float]],
    mu: float,
    initial_identity: _FloatMatrix,
    generations: int | None,
) -> _FloatMatrix:
    """Return the full pairwise identity-in-state matrix after one recursion run.

    The general-topology counterpart to :func:`_iterate_identities`,
    above: that function's own ``(jw, jb)`` pair is only a valid state
    representation because the symmetric island model makes every deme
    exchangeable with every other one, collapsing "identity between any
    two distinct demes" down to one shared scalar. A migration matrix
    without that full exchangeability -- the Crow & Aoki torus in
    particular, where a deme's four nearest neighbors are not
    interchangeable with its five more-distant ones -- has no such
    shortcut: this tracks the full ``d`` by ``d`` symmetric matrix of
    pairwise identities instead, one entry per ordered pair of demes
    (the diagonal holding each deme's own within-deme identity).

    Derivation, one Migrate -> Mutate -> Drift generation at a time,
    matching :func:`fim.model.operators.step`'s own order exactly like
    :func:`_iterate_identities` already does:

    * **Migrate**: post-migration deme ``i``'s pool is a weighted mix of
      every deme's pre-migration pool, weighted by migration matrix row
      ``i``. Two independent gene copies drawn after migration, one from
      deme ``i`` and one from deme ``j``, are each drawn from that mix
      independently, so their joint identity is the bilinear form
      ``migrated[i, j] = sum_k sum_l M[i, k] * M[j, l] * identity[k,
      l]`` -- exactly ``M @ identity @ M.T`` in matrix form. Specialized
      to the symmetric island model's own migration matrix, this
      bilinear form reduces to precisely
      :func:`_identity_coefficients`'s own four scalar coefficients (
      confirmed directly, not assumed, before this function was written
      -- see :func:`test_pairwise_identity_recursion_matches_the_island_
      model_oracle`, below).
    * **Mutate**: scales every pairwise identity by the same
      :func:`_mutation_survival` factor :func:`_iterate_identities`
      already applies uniformly to both its own ``jw`` and ``jb`` --
      including this function's own off-diagonal (between-deme) entries,
      which is a mild approximation (the exact between-deme survival
      factor has no same-deme mutation-count covariance term to correct
      for, unlike the diagonal) already implicit in the existing,
      already-published-value-validated ``_iterate_identities``, carried
      forward here unchanged rather than "fixed" and made inconsistent
      with it.
    * **Drift**: only the diagonal (within-deme) entries gain the
      ``1/population_size`` collision term -- two copies drawn from
      *different* demes can never be the same physical gene copy, so
      only same-deme identity gets that correction, matching
      :func:`_iterate_identities`'s own ``next_within``/``next_between``
      split exactly.

    Args:
        population_size: Gene-copy count ``N``, equal across every deme.
        migration_matrix: A ``d`` by ``d`` row-stochastic migration
            matrix (any topology, not only symmetric island or torus).
        mu: Per-copy mutation probability.
        initial_identity: The starting ``d`` by ``d`` symmetric identity
            matrix (the identity matrix itself, i.e. every deme fixed
            for a distinct private allele with no identity between any
            two of them, is the usual undifferentiated starting point --
            see :func:`_pairwise_identity_fixed_point`).
        generations: Fixed step count, or ``None`` to iterate to a fixed
            point (the same convergence tolerance and step cap as
            :func:`_iterate_identities`).

    Returns:
        The ``d`` by ``d`` pairwise identity-in-state matrix after the
        requested number of generations.
    """
    migration = np.asarray(migration_matrix, dtype=np.float64)
    survival = _mutation_survival(mu, population_size)
    inverse = 1.0 / population_size
    deme_count = migration.shape[0]
    diagonal = np.diag_indices(deme_count)

    identity = initial_identity.copy()
    step = 0
    while True:
        migrated = migration @ identity @ migration.T
        next_identity = survival * migrated
        next_identity[diagonal] = (
            inverse + (1.0 - inverse) * survival * migrated[diagonal]
        )
        converged = bool(np.max(np.abs(next_identity - identity)) < 1e-16)
        identity = next_identity
        step += 1
        if generations is None:
            if converged or step >= 5_000_000:
                break
        elif step >= generations:
            break
    result: _FloatMatrix = identity
    return result


def _pairwise_identity_fixed_point(
    *,
    population_size: int,
    migration_matrix: Sequence[Sequence[float]],
    mu: float,
) -> _FloatMatrix:
    """Return the recursion fixed point of the full pairwise identity matrix.

    The general-topology counterpart to :func:`_identity_fixed_point`,
    above: starts from every deme fixed for its own distinct private
    allele (the identity matrix -- diagonal ``1``, off-diagonal ``0``,
    the same undifferentiated founding condition
    :func:`_identity_fixed_point` starts from in its own two-scalar
    form) and iterates :func:`_iterate_pairwise_identities` to
    convergence.
    """
    deme_count = len(migration_matrix)
    return _iterate_pairwise_identities(
        population_size=population_size,
        migration_matrix=migration_matrix,
        mu=mu,
        initial_identity=np.eye(deme_count),
        generations=None,
    )


def _pooled_statistics_from_identity_matrix(
    identity: np.ndarray, d: int
) -> tuple[float, float]:
    """Return pooled ``(G_ST, D)`` from a full pairwise identity matrix.

    Averages the matrix's own diagonal (within-deme identity) and
    off-diagonal (between-deme identity) entries down to the same two
    scalars :func:`_identities_to_statistics` already turns into
    ``(G_ST, D)`` for the symmetric island model -- the natural pooling
    for a topology with translation symmetry but not full pairwise
    exchangeability (the torus in particular: every deme's own four
    nearest neighbors are closer, in the migration-matrix sense, than
    its remaining five, so no single off-diagonal entry represents "the"
    between-deme identity the way the symmetric island model's own single
    shared value does) -- the same averaging
    :func:`_pooled_g_st_d` already applies across loci for real simulated
    data, applied here across deme pairs instead.
    """
    mean_within = float(np.mean(np.diag(identity)))
    off_diagonal_mask = ~np.eye(d, dtype=bool)
    mean_between = float(np.mean(identity[off_diagonal_mask]))
    return _identities_to_statistics(mean_within, mean_between, d)


def _dn2_equilibrium_start(
    *,
    within_fixed_point: float,
    between_fixed_point: float,
    d: int,
    shared_count: int,
) -> InitialFrequencies:
    """Build a derived near-equilibrium start for the high-migration scenario.

    Constructs, with no tuning, a single-locus state whose within- and
    between-deme identity-in-state equal the recursion fixed point
    ``(jw*, jb*)``:

    * ``shared_count`` alleles are common to every deme at frequency
      ``fs = sqrt(jb* / shared_count)``, so the between-deme identity is
      ``shared_count * fs^2 = jb*`` exactly (distinct demes share exactly this
      set, and private alleles below contribute nothing between demes).
    * ``private_count`` alleles are unique to each deme at frequency
      ``fp = (1 - shared_count * fs) / private_count`` so every deme's
      frequencies sum to one. Matching the within-deme identity
      ``jw* = jb* + private_count * fp^2`` then fixes
      ``private_count = round((1 - shared_count * fs)^2 / (jw* - jb*))``.

    ``shared_count`` is chosen upstream so both per-allele copy counts
    (``fs * N`` shared, ``fp * N`` private) sit far above the one-copy
    drift-loss boundary, keeping the state drift-stable.

    Args:
        within_fixed_point: The recursion fixed point ``jw*``.
        between_fixed_point: The recursion fixed point ``jb*``.
        d: Number of demes.
        shared_count: Number of alleles shared by all demes.

    Returns:
        A single-locus ``InitialFrequencies`` with the derived allele masses.
    """
    shared_freq = math.sqrt(between_fixed_point / shared_count)
    shared_mass = shared_count * shared_freq
    private_mass = 1.0 - shared_mass
    private_count = round(
        private_mass * private_mass / (within_fixed_point - between_fixed_point)
    )
    private_freq = private_mass / private_count

    demes: list[tuple[Mapping[AlleleId, float], ...]] = []
    for deme_index in range(d):
        allele_freqs: dict[AlleleId, float] = {
            AlleleId(allele): shared_freq for allele in range(shared_count)
        }
        base = shared_count + deme_index * private_count
        for offset in range(private_count):
            allele_freqs[AlleleId(base + offset)] = private_freq
        demes.append((allele_freqs,))
    return tuple(demes)


def _dn1_equilibrium_start(
    *,
    within_fixed_point: float,
    between_fixed_point: float,
    d: int,
) -> InitialFrequencies:
    """Build a derived equilibrium ensemble for the low-migration scenario.

    At ``N=100`` no single locus can hold the fixed-point within-identity
    ``jw* = 0.999``. A multi-locus ensemble realizes it instead:

    * Most loci are globally fixed for one shared allele.
    * One locus is fixed for a distinct private allele in every deme.
    * One locus has a shared two-allele polymorphism whose identity makes the
      pooled within-identity equal ``jw*``.

    The number of loci is derived as ``round(1 / (jw* - jb*))``. The private
    locus therefore realizes the fixed-point within/between identity difference
    to the nearest whole-locus resolution.

    Args:
        within_fixed_point: Recursion fixed point ``jw*``.
        between_fixed_point: Recursion fixed point ``jb*``.
        d: Number of demes.

    Returns:
        Multi-locus initial frequencies approximating the identity fixed point.
    """
    total_loci = round(1.0 / (within_fixed_point - between_fixed_point))
    shared_loci = total_loci - 2
    polymorphic_identity = total_loci * within_fixed_point - (total_loci - 1)
    major = (1.0 + math.sqrt(2.0 * polymorphic_identity - 1.0)) / 2.0

    demes: list[tuple[Mapping[AlleleId, float], ...]] = []
    for deme_index in range(d):
        loci: list[Mapping[AlleleId, float]] = [
            {AlleleId(0): 1.0} for _ in range(shared_loci)
        ]
        loci.append({AlleleId(deme_index): 1.0})
        loci.append({AlleleId(0): major, AlleleId(1): 1.0 - major})
        demes.append(tuple(loci))
    return tuple(demes)


def _pooled_g_st_d(state: ModelState, d: int, n_loci: int) -> tuple[float, float]:
    """Return multi-locus pooled ``(G_ST, D)`` for one final state.

    Uses ratio-of-means pooling across loci, which stays defined whenever any
    locus is polymorphic (the per-locus report's ``G_ST`` is ``None`` under
    global fixation). The forms match ``fim.statistics``'s ``g_st`` and
    ``jost_d`` on the mean within- and total-heterozygosities.

    This is also exactly what `fim.engine.report_for_state` now computes by
    default (`SimulationParams.locus_aggregation == "ratio_of_means"`, R3 in
    `20260903-claude-opus-5-gene-identity-recursion-fim-implications.md`'s
    own remediation sequencing) — production and this file's own oracle used
    to differ here (production averaged per-locus ratios instead), and
    `test_report_for_state_ratio_of_means_matches_the_pooled_oracle`, right
    below, pins that the two now agree on the same simulated state rather
    than leaving it as something every scenario in this file merely happens
    to be consistent with.

    Args:
        state: Final state of one replicate.
        d: Number of demes.
        n_loci: Number of tracked loci.

    Returns:
        The pooled ``(G_ST, D)`` estimate.
    """
    within: list[float] = []
    total: list[float] = []
    for locus_index in range(n_loci):
        table = [
            {
                int(allele): freq
                for allele, freq in state.frequency_map(deme, locus_index).items()
            }
            for deme in range(d)
        ]
        within.append(h_s(table))
        total.append(h_t(table))
    mean_within = statistics.fmean(within)
    mean_total = statistics.fmean(total)
    g_st = (mean_total - mean_within) / mean_total if mean_total > 0 else 0.0
    jost_d = (
        (mean_total - mean_within) / (1.0 - mean_within) * d / (d - 1)
        if mean_within < 1.0
        else 0.0
    )
    return g_st, jost_d


def test_report_for_state_ratio_of_means_matches_the_pooled_oracle() -> None:
    """Production's default ``locus_aggregation`` agrees with this file's own oracle.

    `_pooled_g_st_d`, above, is `_run_engine_pooled`'s independent
    pooled-across-loci ``(G_ST, D)`` oracle — built directly from `fim.
    statistics`'s own per-locus `h_s`/`h_t` primitives, never from `fim.
    engine.report_for_state`, specifically so this file's own published-
    value comparisons never depend on production's own cross-locus
    aggregation being correct. Before R3 (see
    `20260903-claude-opus-5-gene-identity-recursion-fim-implications.md`),
    production instead averaged each locus's own `D`/`G_ST` ratio — a
    different, measurably biased number (see CHANGELOG.md's own entry for
    this change) that this test would have failed against.

    This test runs one short, deterministic simulation and asserts
    `report_for_state`'s own `"G_ST"`/`"D"` fields, under the
    `locus_aggregation="ratio_of_means"` default, equal `_pooled_g_st_d`'s
    own numbers for the identical final state — exactly, not approximately,
    since both now do the identical arithmetic (mean `H_S`/`H_T` across
    loci, then one `G_ST`/`D` from those pooled values). A regression in
    either function's own pooling arithmetic would eventually show up as
    drift in some published-value scenario elsewhere in this file anyway,
    but only this test names the two functions and reports the failure as
    "production disagrees with the validation oracle" rather than as an
    unexplained accuracy regression somewhere else.
    """
    d, n_loci = 4, 6
    loci = tuple(LocusSpec(index + 1, 400) for index in range(n_loci))
    params = SimulationParams(
        N=200,
        m=0.05,
        mu=1e-4,
        d=d,
        seed=20260904,
        loci=loci,
        initial_allele_count=2,
        max_generations=50,
    )
    result = fim(
        params.N,
        params.m,
        params.mu,
        params.d,
        params=params,
        store=_DiscardingStore(),
    )
    state = (result if isinstance(result, tuple) else (result,))[0].final_state

    assert params.locus_aggregation == "ratio_of_means"
    oracle_g_st, oracle_d = _pooled_g_st_d(state, d, n_loci)
    report = report_for_state(
        state, params, run_id="r3-cross-check", converged=True, reason="test"
    )

    assert report["G_ST"] == pytest.approx(oracle_g_st, abs=1e-12)
    assert report["D"] == pytest.approx(oracle_d, abs=1e-12)


def _pooled_shannon_statistics(
    state: ModelState, d: int, n_loci: int
) -> tuple[float, float, float]:
    """Return multi-locus pooled ``(H_T, H_S, Shannon differentiation)``.

    The Shannon-entropy counterpart to `_pooled_g_st_d`, above — same
    per-locus-then-averaged pooling, reading `H_T`/`H_S` off `within_
    hill_number`/`total_hill_number` at ``order=1`` (already exact:
    `within_hill_number` and `total_hill_number` return ``exp(entropy)``
    at that order by construction, so `log(...)` recovers Chao et al.
    (2015)'s own `¹H_S`/`¹H_T` exactly, not an approximation of them) and
    combining them via the same Eq. 10 `(H_T - H_S) / log(d)` `fim.
    statistics.differentiation.equilibrium_shannon_differentiation`
    predicts the equilibrium value of.

    Args:
        state: Final state of one replicate.
        d: Number of demes.
        n_loci: Number of tracked loci.

    Returns:
        The pooled ``(H_T, H_S, Shannon differentiation)`` estimate.
    """
    total_entropies: list[float] = []
    subpopulation_entropies: list[float] = []
    for locus_index in range(n_loci):
        table = [
            {
                int(allele): freq
                for allele, freq in state.frequency_map(deme, locus_index).items()
            }
            for deme in range(d)
        ]
        total_entropies.append(math.log(total_hill_number(table, 1)))
        subpopulation_entropies.append(math.log(within_hill_number(table, 1)))
    mean_total = statistics.fmean(total_entropies)
    mean_subpopulation = statistics.fmean(subpopulation_entropies)
    shannon_differentiation = (mean_total - mean_subpopulation) / math.log(d)
    return mean_total, mean_subpopulation, shannon_differentiation


def _crow_aoki_torus_matrix(
    side_length: int, rate: float, *, pool_size: int = 4
) -> tuple[tuple[float, ...], ...]:
    """Return the dense migration matrix for an `L`-by-`L` toroidal lattice.

    Crow & Aoki (1984)'s own "two-dimensional stepping-stone model"
    (`pnas00620-0169.pdf`, §"Other migration patterns"): demes arranged
    on a rectangular lattice, each exchanging migrants only with its
    four nearest neighbors (up/down/left/right), with the two edges of
    the lattice identified in each direction — an "abstract torus," so
    every deme has exactly four neighbors, none of them an edge case.
    This is a genuinely different migration topology from every other
    scenario in this file (the symmetric island model) and from
    `fim.model.topology`'s own `"ring"`/`"linear"` topology sugar (both
    one-dimensional); no existing helper builds it, so this one does,
    reusing `dense_matrix_from_neighbors` — whose own docstring already
    permits a hand-built sparse neighbor map, not only one from
    `stepping_stone_neighbors` — for the actual sparse-to-dense
    conversion rather than re-deriving that validation and normalization
    logic here.

    Demes are numbered row-major, one-based (`row * side_length + column
    + 1`), matching `dense_matrix_from_neighbors`'s own one-based
    convention. `rate`'s total outgoing migration fraction is split
    evenly across all four neighbors, `rate / pool_size` each, mirroring
    how `fim.model.topology._neighbor_weights` already splits a
    topology's total rate across however many neighbors a deme actually
    has.

    `pool_size` (default 4, `fim`'s own convention: migrants drawn only
    from the four neighbors) exists for exactly one other caller,
    `test_crow_aoki_torus_under_the_papers_own_migration_convention` —
    the R&L Nei/Li convention redraws `rate` from a pool that *includes*
    the home deme (`20260903-claude-opus-5-gene-identity-recursion-fim-
    implications.md` §3.3/§9), which for this topology means a five-way
    pool (self plus four neighbors), each getting `rate / 5`. Passing
    `pool_size=5` here does not need a fifth, self-referencing entry in
    the neighbor map at all: `dense_matrix_from_neighbors` already
    derives a deme's own diagonal as `1 - sum(off-diagonal weights)`, so
    four off-diagonal entries of `rate / 5` each already leave exactly
    `1 - 4 * (rate / 5) = 1 - 4 * rate / 5` on the diagonal — algebraically
    identical to `(1 - rate) + rate / 5`, the home deme's own retained
    share plus its own equal slice of the redrawn pool. No other change
    to this function is needed for the different convention.

    Args:
        side_length: `L`, the lattice's side length; the deme count is
            `L * L` (Crow & Aoki's own `n`).
        rate: Every deme's total outgoing migration fraction, matching
            the meaning `m` already has in the symmetric island model.
        pool_size: How many-way the redrawn `rate` fraction is split —
            4 (default) for `fim`'s own neighbors-only convention, 5 for
            R&L's own neighbors-plus-self convention.

    Returns:
        An `(L*L)`-by-`(L*L)` row-stochastic dense migration matrix.
    """
    neighbor_weight = rate / pool_size
    neighbors: dict[int, dict[int, float]] = {}
    for row in range(side_length):
        for column in range(side_length):
            deme = row * side_length + column + 1
            lattice_neighbors = (
                ((row - 1) % side_length, column),
                ((row + 1) % side_length, column),
                (row, (column - 1) % side_length),
                (row, (column + 1) % side_length),
            )
            neighbors[deme] = {
                neighbor_row * side_length + neighbor_column + 1: neighbor_weight
                for neighbor_row, neighbor_column in lattice_neighbors
            }
    return dense_matrix_from_neighbors(neighbors, side_length * side_length)


def _run_engine_replicates(
    *,
    population_size: int,
    m: Migration,
    mu: float,
    d: int,
    n_loci: int,
    horizon: int,
    replicates: int,
    seed: int,
    initial_frequencies: InitialFrequencies | None = None,
    engine_backend: EngineBackendChoice = "lineal",
) -> tuple[ModelState, ...]:
    """Run the real engine and return every replicate's own final state.

    The convergence monitor is effectively disabled so the run is a
    deterministic fixed-horizon integration: ``convergence_tolerance = 0``
    requires an exact match between the two half-window means, which a live
    drift/migration/mutation trajectory essentially never produces, so the
    run always stops exactly at ``max_generations``. ``convergence_window =
    horizon + 1`` is the largest window `SimulationParams` accepts for this
    ``max_generations`` (validation rejects anything larger as structurally
    unable to fill before the cap) — it still cannot fill until the very last
    recorded generation, one step too late to preempt the cap.

    The shared engine-running step behind both `_run_engine_pooled`
    (heterozygosity-based `G_ST`/`D`) and `_run_engine_pooled_shannon`
    (Shannon-entropy-based `H_T`/`H_S`/Shannon differentiation), below —
    factored out so a scenario's own simulated trajectory is never run
    twice just because two different statistic families both want to read
    it.

    Args:
        population_size: Gene-copy count ``N``.
        m: Migration — a scalar symmetric rate for every scenario this
            file used before the Crow & Aoki torus scenario, or a full
            dense matrix for that one (see
            `_crow_aoki_torus_matrix`) — passed through unexamined to
            `SimulationParams`, which already accepts either.
        mu: Per-copy mutation probability.
        d: Number of demes.
        n_loci: Number of independently tracked loci.
        horizon: Exact number of generations to integrate.
        replicates: Number of independently seeded replicates.
        seed: Base seed; replicate ``i`` uses ``seed + i``.
        initial_frequencies: Optional explicit start; otherwise the default
            Dirichlet founding condition is used.
        engine_backend: Which backend actually runs this scenario —
            `"lineal"` (every existing caller's own default, unchanged)
            or `"generational"`. Never `"generational-vector"`/`"auto"`
            here: every scenario in this file uses the default
            `mutation_model="infinite_alleles"`, which `Vectorized
            Advancer` does not support at all (an enforced `ValueError`,
            not a gap to work around) — `"generational"` is the one
            other backend actually reachable from this helper's own
            scenarios, added specifically to let a caller confirm a
            published-value comparison holds for it too, not just for
            `"lineal"`.

    Returns:
        Every replicate's own final `ModelState`, in replicate order.
    """
    loci = tuple(LocusSpec(index + 1, 400) for index in range(n_loci))
    params = SimulationParams(
        N=population_size,
        m=m,
        mu=mu,
        d=d,
        seed=seed,
        loci=loci,
        initial_allele_count=2,
        convergence_tolerance=0.0,
        convergence_window=horizon + 1,
        max_generations=horizon,
        n_replicates=replicates,
        replicate_tolerance=None,
        initial_frequencies=initial_frequencies,
    )
    results = fim(
        population_size,
        m,
        mu,
        d,
        params=params,
        store=_DiscardingStore(),
        engine_backend=engine_backend,
    )
    replicate_results = results if isinstance(results, tuple) else (results,)
    return tuple(result.final_state for result in replicate_results)


def _run_engine_pooled(
    *,
    population_size: int,
    m: Migration,
    mu: float,
    d: int,
    n_loci: int,
    horizon: int,
    replicates: int,
    seed: int,
    initial_frequencies: InitialFrequencies | None = None,
    engine_backend: EngineBackendChoice = "lineal",
) -> tuple[list[float], list[float]]:
    """Run the real engine and return per-replicate pooled ``(G_ST, D)``.

    See `_run_engine_replicates`, above, for every argument's own
    meaning and the deterministic-horizon integration this builds on.

    Returns:
        Two parallel lists, the pooled ``G_ST`` and ``D`` per replicate.
    """
    final_states = _run_engine_replicates(
        population_size=population_size,
        m=m,
        mu=mu,
        d=d,
        n_loci=n_loci,
        horizon=horizon,
        replicates=replicates,
        seed=seed,
        initial_frequencies=initial_frequencies,
        engine_backend=engine_backend,
    )
    g_values: list[float] = []
    d_values: list[float] = []
    for state in final_states:
        g_st, jost_d = _pooled_g_st_d(state, d, n_loci)
        g_values.append(g_st)
        d_values.append(jost_d)
    return g_values, d_values


def test_engine_reproduces_part_vi_equilibrium_via_generational_backend() -> None:
    """`engine_backend="generational"` reproduces the same published-value comparison.

    Every one of this file's own `@pytest.mark.slow` published-value
    scenarios below runs through the default `"lineal"` backend only —
    reasonably, since `fim.engine`'s own design already gives Backend L
    and Backend G a *general*, structural bit-identity proof
    (`test_generational_backend_matches_lineal_for_scalar_run`/`_for_
    batch`, `test/engine/test_engine.py`): G calls the identical dict-
    based operators as L, in the identical per-deme order, only
    reordering *across* replicas — a config-independent guarantee, not
    one that needs re-confirming scenario by scenario. Re-running all
    five slow scenarios again through `"generational"` would add real
    CI time (this project's own `test/bin` history already fought one
    slow-suite timeout) for zero new information.

    This one spot-check exists for what the general proof does *not*
    automatically cover: the multi-locus (8 independently tracked loci),
    real-differentiation-statistics shape this file's own helpers
    actually exercise, which the smaller, single-locus `tiny_params`-
    based tests never do. Deliberately small and fast (2 replicates, 20
    generations — not `@pytest.mark.slow`) and deliberately an *exact*
    comparison, not a statistical one: since L and G are already proven
    bit-identical in general, there is nothing to band here — either
    this config reproduces that identically too, or the general proof
    has a real, narrower exception this specific shape exposes.
    """
    lineal_states = _run_engine_replicates(
        population_size=100,
        m=0.01,
        mu=0.005,
        d=4,
        n_loci=8,
        horizon=20,
        replicates=2,
        seed=707000,
        engine_backend="lineal",
    )
    generational_states = _run_engine_replicates(
        population_size=100,
        m=0.01,
        mu=0.005,
        d=4,
        n_loci=8,
        horizon=20,
        replicates=2,
        seed=707000,
        engine_backend="generational",
    )
    assert generational_states == lineal_states


def _run_engine_pooled_shannon(
    *,
    population_size: int,
    m: Migration,
    mu: float,
    d: int,
    n_loci: int,
    horizon: int,
    replicates: int,
    seed: int,
    initial_frequencies: InitialFrequencies | None = None,
) -> tuple[list[float], list[float], list[float]]:
    """Run the real engine and return per-replicate pooled Shannon statistics.

    The Shannon-entropy counterpart to `_run_engine_pooled`, above — same
    deterministic-horizon integration, same arguments, but reading
    `H_T`/`H_S`/Shannon differentiation off each replicate's own final
    state (`_pooled_shannon_statistics`) instead of `G_ST`/`D`.

    Returns:
        Three parallel lists, the pooled total-population entropy,
        subpopulation entropy, and Shannon differentiation per replicate.
    """
    final_states = _run_engine_replicates(
        population_size=population_size,
        m=m,
        mu=mu,
        d=d,
        n_loci=n_loci,
        horizon=horizon,
        replicates=replicates,
        seed=seed,
        initial_frequencies=initial_frequencies,
    )
    total_values: list[float] = []
    subpopulation_values: list[float] = []
    differentiation_values: list[float] = []
    for state in final_states:
        total, subpopulation, differentiation = _pooled_shannon_statistics(
            state, d, n_loci
        )
        total_values.append(total)
        subpopulation_values.append(subpopulation)
        differentiation_values.append(differentiation)
    return total_values, subpopulation_values, differentiation_values


@pytest.mark.parametrize(
    ("mu", "population_size"),
    [
        (0.001, 100),
        (0.1, 50),
        (0.4, 3),
        (0.9, 2),
    ],
)
def test_mutation_survival_matches_brute_force_binomial_second_moment(
    mu: float,
    population_size: int,
) -> None:
    """The exact formula agrees with the full binomial second moment.

    Independently sums ``E[(1 - k/N)^2]`` over every possible mutant count
    ``k`` weighted by its exact binomial probability, rather than
    re-deriving the same algebra :func:`_mutation_survival` already uses,
    including a high-``mu``, small-``population_size`` case
    (``mu=0.4, population_size=3``) where the omitted ``mu(1-mu)/N`` term
    is ``0.08`` against a base of ``0.36`` -- large enough that the
    plain ``(1 - mu) ** 2`` approximation this exact term corrects would
    fail this comparison outright, not merely drift outside a
    statistical tolerance.
    """
    brute_force = math.fsum(
        math.comb(population_size, k)
        * mu**k
        * (1.0 - mu) ** (population_size - k)
        * (1.0 - k / population_size) ** 2
        for k in range(population_size + 1)
    )

    assert _mutation_survival(mu, population_size) == pytest.approx(
        brute_force, abs=1e-12
    )


@pytest.mark.parametrize(
    (
        "population_size",
        "d",
        "m",
        "mu",
        "published_g_st",
        "published_d",
    ),
    [
        (100, 5, 0.0001, 0.000001, 0.97, 0.04),
        (2000, 100, 0.01, 0.001, 0.02, 0.905),
    ],
)
def test_identity_recursion_oracle_matches_formula_and_published(
    population_size: int,
    d: int,
    m: float,
    mu: float,
    published_g_st: float,
    published_d: float,
) -> None:
    """The exact recursion equilibrium reproduces Eq. 2 / Eq. 4 and Dear-Nolan.

    This self-validates the oracle used by the seeded engine tests: its
    fixed point agrees with the closed-form equilibria to ``O(1/N)`` and with
    the published Dear-Nolan values. It is a cross-check, not a substitute for
    running the simulator (done in the tests below).
    """
    oracle_g_st, oracle_d = _pipeline_identity_dynamics(
        population_size=population_size,
        m=m,
        mu=mu,
        d=d,
        within_identity=1.0,
        between_identity=0.0,
        generations=None,
    )

    assert oracle_g_st == pytest.approx(
        equilibrium_g_st(population_size, m, mu, d), abs=_ONE_OVER_N_TOL
    )
    assert oracle_d == pytest.approx(equilibrium_d(m, mu, d), abs=_ONE_OVER_N_TOL)
    assert oracle_g_st == pytest.approx(published_g_st, abs=0.011)
    assert oracle_d == pytest.approx(published_d, abs=0.02)


def _symmetric_island_matrix(d: int, m: float) -> tuple[tuple[float, ...], ...]:
    """Return the dense symmetric-island migration matrix for `d` demes, rate `m`."""
    retained = 1.0 - m
    shared = m / (d - 1)
    return tuple(
        tuple(retained if row == col else shared for col in range(d))
        for row in range(d)
    )


@pytest.mark.parametrize(
    ("population_size", "m", "mu", "d"),
    [
        (100, 0.0001, 0.000001, 5),
        (2000, 0.01, 0.001, 100),
        (100, 0.01, 0.005, 4),
    ],
)
def test_pairwise_identity_recursion_matches_the_island_model_oracle(
    population_size: int, m: float, mu: float, d: int
) -> None:
    """The general matrix recursion exactly reproduces the island-specific one.

    `_iterate_pairwise_identities`'s own derivation is re-derived from
    the model's mechanics directly (see its own docstring), not
    transcribed from `_identity_coefficients`'s own island-specific
    algebra -- so this is a real independent check, not a restatement of
    the same formula in different variable names. Given a symmetric
    island migration matrix, `_identity_coefficients`'s own four scalar
    coefficients are provably the same bilinear form specialized to that
    matrix's own symmetric structure (worked out by hand before writing
    `_iterate_pairwise_identities`'s own docstring), and this test
    confirms that algebra holds in the running code, across the same
    three scenarios (Part VI, both Dear-Nolan configurations) the
    existing island-specific oracle is already validated against.
    """
    matrix = _symmetric_island_matrix(d, m)
    identity = _pairwise_identity_fixed_point(
        population_size=population_size, migration_matrix=matrix, mu=mu
    )
    general_g_st, general_d = _pooled_statistics_from_identity_matrix(identity, d)

    within_star, between_star = _identity_fixed_point(
        population_size=population_size, m=m, mu=mu, d=d
    )
    island_g_st, island_d = _identities_to_statistics(within_star, between_star, d)

    assert general_g_st == pytest.approx(island_g_st, abs=1e-9)
    assert general_d == pytest.approx(island_d, abs=1e-9)


def test_pairwise_identity_recursion_applied_to_the_crow_aoki_torus() -> None:
    """The general recursion's own torus result, and an open discrepancy it found.

    Applies the same general, now cross-validated (see the test above)
    pairwise-identity recursion to the actual Crow & Aoki torus matrix
    (`_crow_aoki_torus_matrix`) at the paper's own `n=9, N=20, m=0.05
    (M=1.0), mu=1e-5` parameters -- the deterministic, exact-math answer
    to "what does this project's own Migrate -> Mutate -> Drift model
    predict for this topology," with no stochastic noise, no seed, and
    no replicate count involved at all (unlike
    `test_crow_aoki_torus_scenario_via_engine`, this cannot flake and
    needed no calibration).

    This does **not** assert a match to the published `G_ST=0.172` --
    it does not match, and forcing an assertion that it does would be
    exactly the "pick parameters until the test passes" pattern this
    project's own testing rules forbid. What the recursion actually
    gives, at this project's already-established migration-rate
    convention (`m = M/N = 1.0/20 = 0.05`, split evenly across each
    deme's four neighbors -- the same convention already used to build
    `_crow_aoki_torus_matrix` for the calibrated engine test), is
    `G_ST ~= 0.324` -- notably *higher* than both the published value and
    `test_crow_aoki_torus_scenario_via_engine`'s own characterized
    engine mean (`0.272`), not lower, which revises rather than confirms
    that test's own attribution of the gap to a small-`N`, `O(1/N)`
    equilibration residual: an exact, infinite-generations recursion has
    no equilibration lag left to attribute anything to, and it lands
    even further from `0.172` than the finite-horizon engine mean did.

    A honest exploration (not committed as its own assertion, since it
    is not resolved) found that quadrupling the migration rate to
    `m = 0.2` (one full `M/N` fraction *per neighbor*, rather than split
    across all four) undershoots to `G_ST ~= 0.115`, and an intermediate
    `m ~= 0.12` reproduces `0.172` almost exactly -- suggesting the real
    explanation is most likely an undocumented difference between this
    project's own migration-rate convention for the torus and whatever
    Crow & Aoki's own unpublished "numerical calculations" (their own
    words -- the paper states no explicit stepping-stone formula) used,
    not a bug in this recursion (independently cross-validated against
    three already-published, already-engine-validated island scenarios,
    exactly, just above) or in `test_crow_aoki_torus_scenario_via_engine`
    itself (which reproduces *this same* `G_ST ~= 0.32`-ish
    neighborhood, not the published `0.172`, when run long enough that
    its own equilibration lag genuinely shrinks).
    """
    side_length = 3
    d = side_length * side_length
    matrix = _crow_aoki_torus_matrix(side_length, 0.05)

    identity = _pairwise_identity_fixed_point(
        population_size=20, migration_matrix=matrix, mu=1e-5
    )
    g_st, jost_d = _pooled_statistics_from_identity_matrix(identity, d)

    # Deterministic and reproducible: re-running from scratch gives the
    # exact same fixed point, bit for bit -- no seed, no stochasticity.
    identity_again = _pairwise_identity_fixed_point(
        population_size=20, migration_matrix=matrix, mu=1e-5
    )
    g_st_again, jost_d_again = _pooled_statistics_from_identity_matrix(
        identity_again, d
    )
    assert g_st == g_st_again
    assert jost_d == jost_d_again

    # A real, deterministic, in-range value -- not the published 0.172
    # (see this test's own docstring for why that is not asserted here).
    assert 0.0 <= g_st <= 1.0
    assert g_st == pytest.approx(0.324, abs=0.01)


def test_crow_aoki_torus_under_the_papers_own_migration_convention() -> None:
    """R9: does the published `G_ST=0.172` discrepancy trace to a migration-
    convention mismatch, the same failure mode already found and fixed for
    Ryman & Leimar (2008)?

    `20260903-claude-opus-5-gene-identity-recursion-fim-implications.md`
    §8/§9's own proposed check: the test above computes `fim`'s own exact
    recursion under `fim`'s own migration convention (`rate` redrawn only
    from a deme's four neighbors, `_crow_aoki_torus_matrix`'s own default)
    and finds `G_ST ~= 0.324`, not the published `0.172` -- a long-standing,
    documented, unresolved gap (`doc/fim-simulator-test-plan.md`, Appendix
    A). Part 3.3 of the implications document found and numerically
    verified the *identical* failure mode for Ryman & Leimar's own island-
    model comparison: an unmapped migration convention (`fim`'s "redraw
    from the other demes only" vs. Nei/Li's "redraw from a pool including
    the home deme") produced up to 58% relative error, resolved to about
    0.1% once mapped. This test asks whether the same fix moves Crow &
    Aoki's own number the same way.

    `pool_size=5` (`_crow_aoki_torus_matrix`'s own docstring has the exact
    derivation) builds the torus matrix under that same "pool includes the
    home deme" convention, spread over the home deme's own four neighbors
    -- the direct spatial analogue of the island-model mapping, not a new
    assumption.

    This does **not** assert a match to `0.172`, for the identical reason
    the test above does not: forcing an assertion that a chosen parameter
    reproduces a target number is exactly the pattern this project's own
    testing rules forbid. What the recursion actually gives under this
    convention is recorded here as a finding, either resolving the
    discrepancy or narrowing what remains unexplained -- both are useful,
    and the assertion below pins whichever this run actually produces so a
    future change to either function is caught, not silently absorbed.

    **The finding is negative, and informative for exactly that reason.**
    `G_ST ~= 0.374` under this convention -- *further* from `0.172` than
    `fim`'s own convention's `0.324`, not closer. The "pool includes the
    home deme" mapping that resolved Ryman & Leimar's own island-model gap
    to about 0.1% (Part 3.3) does not resolve this one; it makes it worse.
    This narrows rather than confirms Part 8's own hypothesis that both
    gaps share one cause: the island-model mismatch is specifically about
    *whether the redraw pool includes the home deme*, and correcting only
    that dimension for the torus moves the wrong direction, so whatever
    Crow & Aoki's own unpublished "numerical calculations" actually did is
    apparently not simply "the R&L pool-includes-self convention, applied
    to four spatial neighbors" either. The old test's own honest
    exploration (`m ~= 0.12` reproduces `0.172` almost exactly, found by
    varying the *rate* rather than the pool composition) remains the
    closer lead; this result rules out one specific, principled alternative
    explanation rather than supplying a new one.
    """
    side_length = 3
    d = side_length * side_length
    matrix = _crow_aoki_torus_matrix(side_length, 0.05, pool_size=5)

    identity = _pairwise_identity_fixed_point(
        population_size=20, migration_matrix=matrix, mu=1e-5
    )
    g_st, jost_d = _pooled_statistics_from_identity_matrix(identity, d)

    identity_again = _pairwise_identity_fixed_point(
        population_size=20, migration_matrix=matrix, mu=1e-5
    )
    g_st_again, jost_d_again = _pooled_statistics_from_identity_matrix(
        identity_again, d
    )
    assert g_st == g_st_again
    assert jost_d == jost_d_again

    # Pin whatever this run actually finds -- not a target, a regression
    # guard. Update this value, with a fresh comment recording the new
    # finding, if `_crow_aoki_torus_matrix`'s own convention math ever
    # changes deliberately.
    assert g_st == pytest.approx(0.374, abs=0.01)


def _paper_identity_coefficients(m: float, s: int) -> tuple[float, float]:
    """Return Ryman & Leimar (2008)'s own migration coefficients, Eq. 2/3's `a`, `b`.

    ``a`` is the probability that two gene copies drawn from the *same*
    island both effectively trace to that island this generation
    (their own migration convention: each copy either stays, probability
    ``1 - m``, or is redrawn from the pool of all ``s`` islands
    *including the home one*, probability ``m`` -- see `doc/migration-
    conventions.md`); ``b`` is the same probability for two copies drawn
    from two *different* islands. Transcribed literally from
    `20260903-claude-opus-5-ryman-leimar-gene-identity-recursions.md`
    Part 4.2 (a private companion document, not part of this repository
    -- see `doc/migration-conventions.md`'s own "Who this document is
    for" section), not re-derived from `_identity_coefficients`, above --
    the entire point of R2 is an independently published derivation to
    check `fim`'s own recursion against, not a restatement of the same
    algebra under new names.

    Args:
        m: The paper's own migration rate -- not `fim`'s; see
            `doc/migration-conventions.md`'s ``m_paper = m_fim * d /
            (d - 1)`` mapping before comparing against `fim`'s own
            recursion, which uses a different rate for the identical
            physical migration process.
        s: Number of islands.

    Returns:
        ``(a, b)``.
    """
    a = (1.0 - m) ** 2 + m * (2.0 - m) / s
    b = m * (2.0 - m) / s
    return a, b


def _iterate_paper_identities(
    *,
    population_size: int,
    m: float,
    mu: float,
    s: int,
    within_identity: float,
    between_identity: float,
    generations: int,
    mutation_survival: float | None = None,
) -> tuple[float, float]:
    """Return ``(J0, J1)`` after ``generations`` steps of the paper's own Eq. 2/3.

    A direct, term-for-term transcription of Ryman & Leimar (2008)'s
    Equations 2 and 3 (see Part 4 of the companion math document cited
    in `_paper_identity_coefficients`, above, for the full derivation),
    reading Drift -> Migrate -> Mutate each step -- the paper's own
    order, the reverse of `fim`'s own Migrate -> Mutate -> Drift (see
    `_iterate_identities`). Part 3.3 of the implications document (also
    private, same caveat) found this ordering difference is not a real
    difference once the with-replacement/distinct-pair identity
    conversion is applied: `test_ryman_leimar_equations_2_and_3_match_
    fims_own_recursion`, below, is the check that finding rests on.

    Args:
        population_size: Gene-copy count -- `fim`'s own `population_
            size` convention, and *already* the paper's own ``2N``
            directly (their ``N`` is diploid individuals, `fim`'s
            `population_size` is gene copies -- the same count, not
            related by an extra factor of 2; doubling it here would be
            a real bug, not a convention nuance, and was caught exactly
            this way while writing this function).
        m: The paper's own migration rate (see `_paper_identity_
            coefficients`'s own docstring for the mapping needed before
            comparing against `fim`'s recursion).
        mu: Per-copy mutation probability.
        s: Number of islands.
        within_identity: Initial ``J0``.
        between_identity: Initial ``J1``.
        generations: Number of steps to take -- always a fixed count.
            Unlike `_iterate_identities`, this recursion has no
            "iterate to a fixed point" mode: R2's whole point is a
            *trajectory* comparison, not an equilibrium one (Part 7 of
            the companion math document explains why a trajectory is
            the stronger test).
        mutation_survival: Override for the paper's own ``(1 - u)^2``
            mutation-survival prefactor. ``None`` (the default) uses
            the paper's own factor unmodified; `test_ryman_leimar_
            equations_2_and_3_match_fims_own_recursion` also calls this
            with `_mutation_survival`'s own exact-second-moment factor
            substituted in, to isolate how much of the row-2 residual
            in Part 3.2's own table is purely the documented mutation-
            model difference (Part 3.3's "mutation factor" paragraph)
            and not a bug in either recursion.

    Returns:
        The ``(J0, J1)`` pair after ``generations`` steps.
    """
    a, b = _paper_identity_coefficients(m, s)
    survival = mutation_survival if mutation_survival is not None else (1.0 - mu) ** 2
    inverse = 1.0 / population_size
    within = within_identity
    between = between_identity
    for _ in range(generations):
        drift_term = inverse + (1.0 - inverse) * within
        next_within = survival * (a * drift_term + (1.0 - a) * between)
        next_between = survival * (b * drift_term + (1.0 - b) * between)
        within, between = next_within, next_between
    return within, between


def _fim_identities_to_paper_convention(
    within: float, between: float, population_size: int
) -> tuple[float, float]:
    """Convert `fim`'s own with-replacement identities to the paper's distinct-pair `J`.

    Exact conversion (`doc/migration-conventions.md` §2.1), applied to
    the within-deme identity only: `fim` tracks ``E[sum p_k^2]``, the
    probability two copies drawn *with replacement* match (including
    drawing the same physical copy twice); Ryman & Leimar's ``J`` is
    the probability two *distinct* copies match. Two copies from
    different demes are already distinct by construction, so the
    between-deme identity needs no conversion.

    Args:
        within: `fim`'s own within-deme identity, ``jw``.
        between: `fim`'s own between-deme identity, ``jb`` -- returned
            unchanged; accepted only so callers can convert both halves
            of a `_iterate_identities` result in one call.
        population_size: Gene-copy count ``N`` per deme.

    Returns:
        ``(Gs_paper, Gd_paper)``, ready to compare directly against
        `_iterate_paper_identities`'s own ``(J0, J1)`` or to pass to
        `_identities_to_statistics` (whose ``(G_ST, D)`` forms are the
        same algebra as the paper's own Part 5.2/5.3, convention-
        agnostic in the within/between pair fed to them -- verified by
        hand before this function was written, not assumed).
    """
    gs_paper = (population_size * within - 1.0) / (population_size - 1.0)
    return gs_paper, between


# Part 3.2's own tolerances, measured over a 240-combination grid
# (`N in {200, 2000, 20000}`, `d in {2, 5, 10, 50}`,
# `m in {0, 1e-4, 1e-3, 1e-2, 1e-1}`, `u in {0, 1e-6, 1e-4, 1e-3}`) sampled
# at 714 generations across each 5000-generation trajectory. This test
# re-checks a small, representative slice of that same grid on every run,
# not the full sweep -- these are the measured *maxima* from the larger
# sweep, not values freshly calibrated for the smaller slice below (which
# measures comfortably inside them; see this module's own git history for
# the exploration that picked the slice).
_RYMAN_LEIMAR_ROW2_TOL_G = 1.11e-3
_RYMAN_LEIMAR_ROW2_TOL_D = 4.54e-3
_RYMAN_LEIMAR_ROW3_TOL_G = 3.4e-7
_RYMAN_LEIMAR_ROW3_TOL_D = 1.9e-9


@pytest.mark.parametrize("generations", [1, 10, 100, 1000, 5000])
@pytest.mark.parametrize(
    ("population_size", "m", "mu", "d"),
    [
        (100, 0.0001, 0.000001, 5),
        (2000, 0.01, 0.001, 100),
        (100, 0.01, 0.005, 4),
    ],
)
def test_ryman_leimar_equations_2_and_3_match_fims_own_recursion(
    population_size: int, m: float, mu: float, d: int, generations: int
) -> None:
    """R2: an independently published derivation reproduces `fim`'s own recursion.

    `_iterate_identities` was derived from `fim`'s own operators (its
    docstring says so). A recursion derived from the implementation
    shares the implementation's assumptions, so it cannot catch a wrong
    one -- only an inconsistency between the code and itself.
    `_iterate_paper_identities` is a literal transcription of Ryman &
    Leimar (2008)'s own published Equations 2 and 3, derived by
    different authors from different premises; agreement between the
    two, under the R1 migration/identity mapping, is a genuinely
    external check that the current suite otherwise has no equivalent
    of for this recursion.

    Two comparisons, each against its own tolerance above:

    1. Migration mapped and identities converted to the distinct-pair
       convention, but each recursion keeps its own real mutation
       model -- `fim`'s exact second moment (`_mutation_survival`)
       against the paper's own ``(1 - u)^2``. The residual is the
       *real, documented* difference between two correct but different
       mutation models (Part 3.3's "mutation factor" paragraph: exact
       for `fim`'s own binomial-count operator, exact for the paper's
       own per-lineage infinite-alleles model), not an error in either
       recursion.
    2. The same, but `_iterate_paper_identities` also substitutes
       `fim`'s own `_mutation_survival` factor via its own
       ``mutation_survival`` override -- isolating whether the two
       recursions are the *same recursion* once every convention and
       every model difference is accounted for. What is left is float
       noise accumulated over up to 5000 generations, not a structural
       residual.
    """
    m_paper = m * d / (d - 1)

    fim_within, fim_between = _iterate_identities(
        population_size=population_size,
        m=m,
        mu=mu,
        d=d,
        within_identity=1.0,
        between_identity=0.0,
        generations=generations,
    )
    converted_within, converted_between = _fim_identities_to_paper_convention(
        fim_within, fim_between, population_size
    )
    fim_g_st, fim_d = _identities_to_statistics(converted_within, converted_between, d)

    paper_within, paper_between = _iterate_paper_identities(
        population_size=population_size,
        m=m_paper,
        mu=mu,
        s=d,
        within_identity=1.0,
        between_identity=0.0,
        generations=generations,
    )
    paper_g_st, paper_d = _identities_to_statistics(paper_within, paper_between, d)

    assert paper_g_st == pytest.approx(fim_g_st, abs=_RYMAN_LEIMAR_ROW2_TOL_G)
    assert paper_d == pytest.approx(fim_d, abs=_RYMAN_LEIMAR_ROW2_TOL_D)

    matched_within, matched_between = _iterate_paper_identities(
        population_size=population_size,
        m=m_paper,
        mu=mu,
        s=d,
        within_identity=1.0,
        between_identity=0.0,
        generations=generations,
        mutation_survival=_mutation_survival(mu, population_size),
    )
    matched_g_st, matched_d = _identities_to_statistics(
        matched_within, matched_between, d
    )
    assert matched_g_st == pytest.approx(fim_g_st, abs=_RYMAN_LEIMAR_ROW3_TOL_G)
    assert matched_d == pytest.approx(fim_d, abs=_RYMAN_LEIMAR_ROW3_TOL_D)


@pytest.mark.parametrize(
    ("mu", "expected_h_s"),
    [
        (1e-8, 0.000040),
        (1e-6, 0.003984),
        (1e-4, 0.285745),
        (1e-3, 0.800240),
    ],
)
def test_ryman_leimar_equation_4_reproduces_figure_1(
    mu: float, expected_h_s: float
) -> None:
    """R2: the paper's own published Figure 1 heterozygosities, from their Eq. 4.

    Their Equation 4 gives the isolated mutation-drift equilibrium
    identity, ``J0* = (1 - u)^2 / (2N - (2N - 1)(1 - u)^2)`` -- the
    fixed point `_iterate_paper_identities` at ``m=0`` would converge
    to under enough generations (no migration ever needed to reach it;
    drift and mutation alone determine an isolated island's own
    equilibrium), but computed here from the closed form directly, both
    because the paper states
    it as a closed form and to keep this a genuinely independent check
    of `_iterate_paper_identities`'s own drift/mutation arithmetic
    rather than a test that would pass even if that function's per-step
    logic were subtly wrong. At ``2N = 2000`` (their ``N = 1000``
    diploid individuals -- see `_iterate_paper_identities`'s own
    docstring for why this is `fim`'s `population_size`-style gene-copy
    count directly, not `1000 * 2`), it reproduces all four
    heterozygosities the paper's own Figure 1 discussion quotes;
    `expected_h_s` here is Part 6.5's own "recomputed" column (compare
    the paper's own less-precise printed values there), and the ``abs``
    tolerance is generous next to the ``~1e-7`` agreement actually
    measured while writing this test.
    """
    two_n = 2000
    j0_star = (1.0 - mu) ** 2 / (two_n - (two_n - 1) * (1.0 - mu) ** 2)
    h_s = 1.0 - j0_star

    assert h_s == pytest.approx(expected_h_s, abs=1e-6)


@pytest.mark.parametrize(
    ("t", "expected_g_st"),
    [
        (10, 0.0045),
        (100, 0.0441),
        (1000, 0.3687),
        (5000, 0.9097),
    ],
)
def test_ryman_leimar_equation_5_reproduces_the_published_g_st_trajectory(
    t: int, expected_g_st: float
) -> None:
    """R2: the paper's own published mutation-free `G_ST` trajectory, their Eq. 5.

    Complete isolation (``m=0``) and no mutation (``u=0``), starting
    from ``(J0, J1) = (0, 0)`` -- an ancestral population with no
    identity-by-descent yet, the infinite-alleles-model founding
    condition (contrast `_identity_fixed_point`'s own ``(1, 0)``, a
    *different* founding condition this project uses elsewhere; the two
    are not interchangeable, and using the wrong one here would not
    reproduce the paper's own numbers). Under this convention, `J1`
    stays frozen at its founding value forever -- migration is what
    lets identity flow between islands, and there is none -- so this is
    exactly `_iterate_paper_identities` at those parameter values, not
    a separate closed form Part 6.5 states one for. At ``s=10``,
    ``2N=2000``, it reproduces the paper's own published landmark
    table (Part 6.5), including "at `t=100`, `G_ST` is close to 0.04
    for all mutation rates" -- the ``0.0441`` row.
    """
    two_n = 2000
    s = 10
    within, between = _iterate_paper_identities(
        population_size=two_n,
        m=0.0,
        mu=0.0,
        s=s,
        within_identity=0.0,
        between_identity=0.0,
        generations=t,
    )
    g_st, _ = _identities_to_statistics(within, between, s)

    assert g_st == pytest.approx(expected_g_st, abs=5e-5)


@pytest.mark.parametrize(
    ("population_size", "m", "mu", "d"),
    [
        (100, 0.01, 0.001, 3),
        (100, 0.01, 0.001, 5),
        (100, 0.01, 0.001, 10),
        (100, 0.1, 0.001, 5),
        (100, 0.001, 0.0001, 5),
        (20, 0.05, 1e-5, 9),
        (2000, 0.01, 0.001, 20),
    ],
)
def test_stepping_stone_differentiation_is_at_least_the_island_models(
    population_size: int, m: float, mu: float, d: int
) -> None:
    """Kimura & Weiss (1964): stepping-stone G_ST/D >= the island model's.

    Cited by Whitlock & McCauley (1999, *Heredity* 82:117-125, read in
    full for the Crow & Aoki torus work): "the genetic differentiation
    of stepping stone systems is substantially greater for the same
    number of migrants coming into a deme per generation" than the
    island model. Kimura & Weiss (1964) itself was not obtained this
    session -- this is Whitlock & McCauley's own account of it, the
    same secondhand standing this project already gives Wright (1931)
    via the same source, not an independent check against the 1964
    text.

    A directional claim, not a numeric one, so no calibration band or
    stochastic engine run is needed: this project's own already-
    validated exact-recursion oracles settle it directly. Both the
    island fixed point (`_identity_fixed_point`) and the stepping-stone
    one (`_pairwise_identity_fixed_point` on a ring matrix from
    `fim.model.topology.stepping_stone_neighbors`) use the same total
    outgoing migration rate `m` -- "the same number of migrants coming
    into a deme per generation" is exactly what matching `m` across
    both topologies already means, since both builders split it evenly
    across however many neighbors a deme has.

    At `d=3` a ring and the island model are the *same* graph -- each
    deme's two neighbors are already the only two other demes there
    are -- so this scenario's own `G_ST`/`D` come out exactly equal, not
    strictly greater; `>=`, not `>`, is the correct assertion, and this
    is why `d=3` stays in the parametrization rather than being dropped
    as a redundant case. Every scenario here was computed and checked
    directly before being written down.
    """
    within_star, between_star = _identity_fixed_point(
        population_size=population_size, m=m, mu=mu, d=d
    )
    island_g_st, island_d = _identities_to_statistics(within_star, between_star, d)

    neighbors = stepping_stone_neighbors(d, topology="ring", rate=m)
    matrix = dense_matrix_from_neighbors(neighbors, d)
    identity = _pairwise_identity_fixed_point(
        population_size=population_size, migration_matrix=matrix, mu=mu
    )
    ring_g_st, ring_d = _pooled_statistics_from_identity_matrix(identity, d)

    assert ring_g_st >= island_g_st - 1e-9
    assert ring_d >= island_d - 1e-9


def test_identity_recursion_reduces_to_whitlock_infinite_island_trajectory() -> None:
    """`_iterate_identities`, at large `d` and `mu=0`, is Whitlock's own Eq. 1.

    Whitlock (1992, *Evolution* 46:608-615) derives the trajectory of
    within-population identity by descent, `f_0`, in Wright's classical
    infinite-island model: infinitely many demes, migrants drawn from an
    outside gene pool with zero identity, mutation set aside as
    negligible. This project's own `_iterate_identities` is a *finite*-
    `d` symmetric-island recursion -- so Whitlock's own closed form
    (`identity_recovery_trajectory`) should be exactly what `_iterate_
    identities` converges to as `d -> infinity`, with an `O(1/d)`
    residual at any finite `d`, the same shape of approximation
    `equilibrium_g_st`/`equilibrium_d` already carry as `O(1/N)`
    residuals against the exact finite-`N` recursion.

    Checked here at `d=100,000` (negligible runtime -- `_iterate_
    identities` only takes 10 fixed steps regardless of `d`; only its
    per-step *coefficients* depend on `d`), well inside `_ONE_OVER_D_
    TOL`. This single large-`d` point is a spot check, not the full
    sweep: the `O(1/d)` shape of the residual was confirmed separately
    across a six-row `d` sweep (10 through 100,000) alongside a
    from-scratch algebraic derivation of the reduction itself, not
    reproduced here.
    """
    population_size = 100
    m = 0.05
    f0_initial = 0.8
    generations = 10
    d = 100_000

    within, _between = _iterate_identities(
        population_size=population_size,
        m=m,
        mu=0.0,
        d=d,
        within_identity=f0_initial,
        between_identity=0.0,
        generations=generations,
    )
    closed_form = identity_recovery_trajectory(
        f0_initial, population_size, m, generations
    )

    assert within == pytest.approx(closed_form, abs=_ONE_OVER_D_TOL)


def test_shannon_entropy_isolated_theta_convention_matches_identity_recursion() -> None:
    """`equilibrium_shannon_entropy_isolated`'s `theta = 2*N*mu` is the right one.

    There is no exact recursion for Shannon entropy itself in this
    project (the pairwise-identity recursion these `_pipeline_identity_
    dynamics`-family functions track is a heterozygosity-scale quantity,
    not an entropy-scale one) -- and no independent "published" isolated-
    population Shannon-entropy value in this project's own literature
    trail either. What *is* checkable, and is the one thing every
    equilibrium Shannon-entropy formula in
    `fim.statistics.differentiation` shares: the ploidy conversion from
    Chao et al. (2015)'s own diploid-individual `N` to this project's
    gene-copy `population_size`, `theta = 2*population_size*mu`. Checked
    here the same way it was checked
    before ever writing `equilibrium_shannon_entropy_isolated`'s own
    docstring: run this module's own exact finite-N identity recursion
    isolated (`m=0`), convert its fixed-point identity to heterozygosity
    (`1 - within`), and compare to what `theta = 2*population_size*mu`
    implies via Eq. 1 (`theta / (theta + 1)`) -- the same `O(1/N)`-scale
    residual `equilibrium_g_st`/`equilibrium_d` already carry, shrinking
    as `N` grows. `equilibrium_shannon_entropy_isolated`'s own digamma
    arithmetic is verified independently, exactly, against textbook
    closed forms in `test/validation/test_equilibrium.py`.
    """
    for population_size, mu in ((100, 0.001), (1_000, 0.001), (10_000, 0.001)):
        within, _between = _iterate_identities(
            population_size=population_size,
            m=0.0,
            mu=mu,
            d=2,
            within_identity=1.0,
            between_identity=0.0,
            generations=None,
        )
        exact_heterozygosity = 1.0 - within
        theta = 2.0 * population_size * mu
        formula_heterozygosity = theta / (theta + 1.0)

        assert exact_heterozygosity == pytest.approx(
            formula_heterozygosity, abs=_ONE_OVER_N_TOL
        )


@pytest.mark.parametrize(
    ("population_size", "m", "mu", "d"),
    [
        (100, 0.01, 0.001, 4),
        (2000, 0.01, 0.001, 100),
        (100, 0.0001, 0.000001, 5),
    ],
)
def test_shannon_entropy_total_theta_convention_matches_identity_recursion(
    population_size: int, m: float, mu: float, d: int
) -> None:
    """`equilibrium_shannon_entropy_total`'s own `theta_T` formula, cross-checked.

    The total-population counterpart to the isolated-population check
    above, over three of this project's own existing scenarios (Part VI
    and both Dear-Nolan configurations). `_identity_fixed_point`'s own
    pooled total-population identity, `(1/d)*within + ((d-1)/d)*between`,
    converted to heterozygosity, is compared against `equilibrium_
    shannon_entropy_total`'s own `theta_T` via Eq. 1.
    """
    within_star, between_star = _identity_fixed_point(
        population_size=population_size, m=m, mu=mu, d=d
    )
    pooled_identity = (1.0 / d) * within_star + ((d - 1) / d) * between_star
    exact_heterozygosity = 1.0 - pooled_identity

    migration_star = m * d / (d - 1)
    theta_total = 2.0 * population_size * d * mu + (d - 1) * mu / (migration_star + mu)
    formula_heterozygosity = theta_total / (theta_total + 1.0)

    assert exact_heterozygosity == pytest.approx(
        formula_heterozygosity, abs=_ONE_OVER_N_TOL
    )


def test_shannon_entropy_isolated_and_total_are_computed_and_finite() -> None:
    """Both equilibrium Shannon-entropy functions run end to end and return sane values.

    Not a numeric cross-check (those are above) -- just confirms the
    public functions themselves are wired correctly and importable from
    `fim.statistics` (`__init__.py`'s own re-export), the same "does it
    actually run" floor every other public equilibrium function already
    has.
    """
    isolated = equilibrium_shannon_entropy_isolated(100, 0.001)
    total = equilibrium_shannon_entropy_total(100, 0.01, 0.001, 4)

    assert math.isfinite(isolated)
    assert math.isfinite(total)
    assert isolated > 0.0
    assert total > 0.0


def test_identity_recursion_d_and_g_st_are_non_increasing_in_migration() -> None:
    """The exact finite-N oracle's D and G_ST never rise as migration rises.

    Same underlying claim as `test-manifest.yaml`'s own `M1`-`M5`
    migration panel (same `N`, `mu`, `d`, and even the same five `m`
    values), but checked against this module's own exact identity-
    recursion oracle (`_identity_fixed_point`) rather than a chain of
    single-seeded `fim()` engine runs compared with a fixed `1e-9`
    tolerance. That design cannot be ported as specified: it compares
    single stochastic realizations, not expectations, and gives each
    of the five runs its own independent convergence horizon.
    The oracle used here has neither problem — it is iterated to its
    own exact fixed point every time, with no seed and no replicate
    count involved, so this assertion can never flake — while still
    being *stronger* than a check on the closed-form diffusion
    approximation alone (`equilibrium_d`/`equilibrium_g_st`, checked
    separately in `test/statistics/test_properties.py`): this is the
    finite-N truth those formulas are themselves only shown (just
    above, `_ONE_OVER_N_TOL`) to approximate.
    """
    population_size = 100
    mu = 0.001
    d = 4
    m_values = (0.0, 0.0001, 0.001, 0.01, 0.1)

    g_st_values = []
    d_values = []
    for m in m_values:
        within_star, between_star = _identity_fixed_point(
            population_size=population_size, m=m, mu=mu, d=d
        )
        g_st, jost_d_value = _identities_to_statistics(within_star, between_star, d)
        g_st_values.append(g_st)
        d_values.append(jost_d_value)

    assert g_st_values == sorted(g_st_values, reverse=True)
    assert d_values == sorted(d_values, reverse=True)


def test_identity_recursion_d_and_g_st_move_opposite_ways_in_mutation() -> None:
    """The exact finite-N oracle's D rises and G_ST falls as mutation rises.

    The deterministic-oracle counterpart to the migration test above,
    for `test-manifest.yaml`'s own `MU1`-`MU5` mutation panel (same
    `N`, `m`, `d`, and the same five `mu` values) — which itself never
    asserted a direction at all, only "is finite" at each step. The
    direction actually implied by the model (Part VI's own headline
    point, "Why this kills the standard inference") is that `D` and
    `G_ST` move opposite ways as mutation rate rises, checked here
    exactly, with the same non-flaking, no-seed, no-calibration
    oracle as the migration test above.
    """
    population_size = 100
    m = 0.01
    d = 4
    mu_values = (0.0, 0.000001, 0.0001, 0.001, 0.01)

    g_st_values = []
    d_values = []
    for mu in mu_values:
        within_star, between_star = _identity_fixed_point(
            population_size=population_size, m=m, mu=mu, d=d
        )
        g_st, jost_d_value = _identities_to_statistics(within_star, between_star, d)
        g_st_values.append(g_st)
        d_values.append(jost_d_value)

    assert d_values == sorted(d_values)
    assert g_st_values == sorted(g_st_values, reverse=True)


@pytest.mark.slow
@pytest.mark.statistical
def test_engine_reproduces_part_vi_equilibrium() -> None:
    """The simulator approaches the Part VI equilibrium (test-plan 7.3).

    **Functional (public-API-only):** runs the real engine to equilibrium
    for a general moderate-migration case (``N=100, m=0.01, mu=0.005,
    d=4``) and checks the pooled ``G_ST``/``D`` directly against the
    public closed-form diffusion formulas (``equilibrium_g_st``/
    ``equilibrium_d``, Eq. 2 / Eq. 4) -- no dependence on this file's own
    internal-recursion oracle (`_pipeline_identity_dynamics` and its
    siblings) or on any detail of the engine's current internal pipeline
    order, so this assertion's validity survives a future core refactor
    that preserves the model's own scientific behavior. The tolerance
    combines two already-established, independently-derived error
    sources by the triangle inequality rather than a new one picked to
    make this specific comparison pass: the statistical replicate band
    (`_band`, from the versioned characterization pass) plus the
    diffusion-formula's own `O(1/N)` residual (`_ONE_OVER_N_TOL`, itself
    already validated against this same file's internal-recursion oracle
    elsewhere -- see `_ONE_OVER_N_TOL`'s own module-level comment, and
    `test_identity_recursion_oracle_matches_formula_and_published`).

    Configuration: 8 loci, 6 replicates, horizon 1000 generations (the
    between-deme identity equilibrates by ~500), base seed 707000. Runtime is
    ~60 s. Band derivation (before seed selection, from the versioned
    characterization pass -- module docstring,
    ``test/validation/statistical-calibration-evidence.json``): per-replicate
    spread from ``assertion_sigma_*`` and band ``= 5 * sigma / sqrt(6)``.
    """
    replicates = 6
    g_values, d_values = _run_engine_pooled(
        population_size=100,
        m=0.01,
        mu=0.005,
        d=4,
        n_loci=8,
        horizon=1000,
        replicates=replicates,
        seed=707000,
    )
    mean_g = statistics.fmean(g_values)
    mean_d = statistics.fmean(d_values)

    assert mean_g == pytest.approx(
        equilibrium_g_st(100, 0.01, 0.005, 4),
        abs=_band(_SIGMA_PART_VI_G, replicates) + _ONE_OVER_N_TOL,
    )
    assert mean_d == pytest.approx(
        equilibrium_d(0.01, 0.005, 4),
        abs=_band(_SIGMA_PART_VI_D, replicates) + _ONE_OVER_N_TOL,
    )


@pytest.mark.slow
@pytest.mark.statistical
def test_dear_nolan_low_migration_scenario_via_engine() -> None:
    """The simulator reproduces both low-migration Dear-Nolan values (7.4).

    **Functional (public-API-only):** scenario ``N=100, d=5, m=0.0001,
    mu=0.000001`` (published ``G_ST ~= 0.97, D ~= 0.04``). Configuration:
    26 loci, 12 replicates, horizon 100, base seed 884000, and a derived
    equilibrium start whose pooled identities approximate the recursion
    fixed point. Runtime is about 12 seconds. This test's own pass/fail
    criterion is the direct comparison against the two *published*
    values below -- implementation-independent, and expected to survive
    a future core refactor that preserves the model's own scientific
    behavior. (`_identity_fixed_point`, used only to derive the warm
    ``equilibrium_start`` state below, is test-setup convenience, not an
    assertion: a core refactor changing internal mechanics would at
    worst make this specific starting point a slightly less perfect
    warm start, not invalidate the published-value comparison itself.)

    The multi-locus ensemble resolves a fixed point that is not representable
    at a single locus with ``N=100``.

    Band derivation before seed selection (versioned characterization pass
    -- module docstring, ``test/validation/statistical-calibration-evidence.json``):
    ``assertion_sigma_*`` values loaded from
    ``test/validation/statistical-calibration-evidence.json``.
    """
    replicates = 12
    d = 5
    horizon = 100
    within_star, between_star = _identity_fixed_point(
        population_size=100,
        m=0.0001,
        mu=0.000001,
        d=d,
    )
    equilibrium_start = _dn1_equilibrium_start(
        within_fixed_point=within_star,
        between_fixed_point=between_star,
        d=d,
    )
    n_loci = len(equilibrium_start[0])
    g_values, d_values = _run_engine_pooled(
        population_size=100,
        m=0.0001,
        mu=0.000001,
        d=d,
        n_loci=n_loci,
        horizon=horizon,
        replicates=replicates,
        seed=884000,
        initial_frequencies=equilibrium_start,
    )
    mean_g = statistics.fmean(g_values)
    mean_d = statistics.fmean(d_values)

    assert mean_g == pytest.approx(0.97, abs=_band(_SIGMA_DEAR_NOLAN_LOW_G, replicates))
    assert mean_d == pytest.approx(0.04, abs=_band(_SIGMA_DEAR_NOLAN_LOW_D, replicates))


@pytest.mark.slow
@pytest.mark.statistical
def test_dear_nolan_high_migration_scenario_via_engine() -> None:
    """The simulator reproduces the high-migration Dear-Nolan equilibrium (7.4).

    **Functional (public-API-only) claim:** ``N=2000, d=100, m=0.01,
    mu=0.001`` (published ``G_ST ~= 0.02, D ~= 0.90/0.91``) -- the direct
    comparison against these two published values, at the end of this
    function, is this test's own implementation-independent pass/fail
    criterion, expected to survive a future core refactor that preserves
    the model's own scientific behavior.

    **Internal (implementation-coupled) claim, kept in the same test
    deliberately rather than deleted:** the engine, started from a
    *derived* near-equilibrium state (:func:`_dn2_equilibrium_start`),
    is also shown to HOLD that state -- a stationarity check that a
    biased operator (for example the mutation defect regressed in
    ``test/model/test_operators.py``) would fail by drifting away from
    the fixed point, catching a class of regression the published-value
    check alone would not (a biased operator could coincidentally still
    land near 0.02/0.90 from a *different* starting point). This check's
    own oracle (`_identity_fixed_point`) assumes today's specific
    Migrate -> Mutate -> Drift pipeline order, so it is expected to need
    re-deriving -- not necessarily to signal an engine regression -- once
    a core refactor changes that order; see the labeled assertions below.

    Forward-integrating to this equilibrium from an undifferentiated start is
    not compute-feasible: the between-deme identity relaxes over ~1800
    generations while the infinite-alleles pool grows to ~``10^4`` distinct
    ids, pushing per-generation cost past 0.4 s (>13 min/replicate). Both
    checks above therefore start from the same derived near-equilibrium
    state rather than an undifferentiated one.

    Derivation (no tuning): ``(jw*, jb*)`` is the exact identity fixed point of
    the Migrate -> Mutate -> Drift recursion (:func:`_identity_fixed_point`).
    Each deme is given ``S = 41`` alleles shared by all demes at frequency
    ``fs = sqrt(jb*/S)`` and ``P`` private alleles at
    ``fp = (1 - S*fs)/P``, with ``P = round((1 - S*fs)^2 / (jw* - jb*))``
    matching the within-deme identity. ``S = 41`` yields ``fs*N ~= 15`` shared
    and ``fp*N ~= 64`` private copies, both far above the one-copy drift-loss
    boundary, so the state is drift-stable rather than a differentiated
    transient.

    Configuration: 1 locus (``d=100`` already self-averages), 5 replicates,
    horizon 30, base seed 992000. Runtime ~130 s. Band derivation (before
    seed selection, from the versioned characterization pass -- module
    docstring, ``test/validation/statistical-calibration-evidence.json``,
    characterization
    seed 602000): per-replicate spread from the generated
    ``assertion_sigma_*`` values in
    ``test/validation/statistical-calibration-evidence.json``.
    """
    replicates = 5
    d = 100
    horizon = 30
    within_star, between_star = _identity_fixed_point(
        population_size=2000, m=0.01, mu=0.001, d=d
    )
    equilibrium_start = _dn2_equilibrium_start(
        within_fixed_point=within_star,
        between_fixed_point=between_star,
        d=d,
        shared_count=41,
    )
    g_values, d_values = _run_engine_pooled(
        population_size=2000,
        m=0.01,
        mu=0.001,
        d=d,
        n_loci=1,
        horizon=horizon,
        replicates=replicates,
        seed=992000,
        initial_frequencies=equilibrium_start,
    )
    mean_g = statistics.fmean(g_values)
    mean_d = statistics.fmean(d_values)

    # FUNCTIONAL: direct reproduction of BOTH published headline values --
    # implementation-independent; this is the assertion pair that must
    # keep passing after any future core refactor.
    assert mean_g == pytest.approx(0.02, abs=0.005)
    assert 0.89 <= mean_d <= 0.92

    # INTERNAL: the engine holds today's exact-recursion fixed point
    # within the derived band -- see the docstring's own "Internal"
    # paragraph for what this catches and why it is expected to need
    # re-deriving, not necessarily to fail outright, after a future core
    # refactor changes the pipeline's internal operator order.
    oracle_g_st, oracle_d = _identities_to_statistics(within_star, between_star, d)
    assert mean_g == pytest.approx(
        oracle_g_st, abs=_band(_SIGMA_DEAR_NOLAN_HIGH_G, replicates)
    )
    assert mean_d == pytest.approx(
        oracle_d, abs=_band(_SIGMA_DEAR_NOLAN_HIGH_D, replicates)
    )


@pytest.mark.slow
@pytest.mark.statistical
def test_crow_aoki_torus_scenario_via_engine() -> None:
    """The simulator reproduces Crow & Aoki (1984)'s own torus G_ST (Table 1).

    **Functional (public-API-only):** this test's own assertion compares
    the engine directly against the literal published number, with no
    internal-recursion oracle involved at all -- the cleanest of this
    file's five engine-level scenario tests from an implementation-
    independence standpoint (a direct consequence of the gap the next
    paragraph describes: there was no oracle available to lean on).

    Scenario: a 3-by-3 toroidal stepping-stone lattice (`_crow_aoki_torus_
    matrix`), ``N=20`` gene copies, ``mu=1e-5``, migration rate ``m=0.05``
    (``Nm=1.0``, matching Crow & Aoki's own ``M``) -- Crow & Aoki (1984)
    Table 1's ``n=9, N=20, M=1.0, u=10⁻⁵`` row, published ``G_ST=0.172``.
    Source and citation confirmed directly against the paper's own full
    text (`pnas00620-0169.pdf`); see this project's own commits
    `d3adcf0`/`2dd1ea3` for the fuller citation history.

    Unlike the three scenarios above, this one has no independent exact-
    recursion oracle to cross-check against: `_identity_fixed_point`'s own
    `_identity_coefficients` are specific to the symmetric island model
    (every deme migrates with every other deme equally), not to a torus
    lattice's four-nearest-neighbor structure, and deriving the
    torus-specific equivalent was scoped separately and not built here.
    This test is therefore only a two-way check
    (engine against the literal published number, not engine-against-
    oracle-against-formula-against-published the way the three scenarios
    above are) -- a real, acknowledged gap in rigor relative to them, not
    an oversight.

    Configuration and horizon were both found empirically this session,
    not guessed: unlike the three scenarios above, there is no equilibrium-
    start construction available for this scenario (see above), so the
    only lever against slow convergence is horizon and locus count, and
    both needed real measurement, not a guess, to get right. A first
    attempt at 100 loci / horizon 2000 landed systematically *high*
    (multiple single-replicate trials in the 0.20-0.36 range, none below
    0.20, against a published 0.172) -- not noise around the right
    answer, an actual convergence lag: re-running the same seed at
    horizon 4000 and then 8000 showed `G_ST` trending steadily down
    (0.361 -> 0.255 -> 0.213), confirming the scenario itself was sound
    and the horizon was simply too short. 60 loci, separately, proved too
    thin to reliably avoid all-loci global fixation (`G_ST` undefined)
    over these horizons. 150 loci / horizon 6000 is the configuration
    that came out of this search: four independent trial seeds landed at
    0.198, 0.088, 0.318, and 0.218 (mean 0.206, straddling 0.172 from
    both sides rather than sitting uniformly above it) with no fixation
    failures -- see this session's own exploration log for the fuller
    numeric record.

    6 replicates, base seed 845000, distinct from every other scenario's
    own seed in this file. Runtime is around 23 minutes -- the highest
    per-replicate cost of any scenario in this file (150 loci at horizon
    6000, versus, e.g., Dear-Nolan-high's 1 locus at horizon 30), a direct
    consequence of having no equilibrium-start shortcut available. Band
    derivation (before seed selection, from the versioned characterization
    pass -- module docstring, `test/validation/statistical-calibration-
    evidence.json`, characterization seed 603000): per-replicate spread
    from the generated `assertion_sigma_g` value in that same file, from a
    10-replicate characterization pass -- smaller than the other three
    scenarios' own 20-100-replicate passes, a direct, acknowledged
    consequence of this scenario's per-replicate cost; see the evidence
    file's own `elapsed_seconds` for the specific trade-off made. No `D`
    assertion: Crow & Aoki's Table 1 reports only `G_ST` for the
    stepping-stone model, never `D`.
    """
    replicates = 6
    side_length = 3
    d = side_length * side_length
    torus_matrix = _crow_aoki_torus_matrix(side_length, 0.05)

    g_values, _d_values = _run_engine_pooled(
        population_size=20,
        m=torus_matrix,
        mu=1e-5,
        d=d,
        n_loci=150,
        horizon=6000,
        replicates=replicates,
        seed=845000,
    )
    mean_g = statistics.fmean(g_values)

    assert mean_g == pytest.approx(
        0.172, abs=_band(_SIGMA_CROW_AOKI_TORUS_G, replicates)
    )


@pytest.mark.slow
@pytest.mark.statistical
def test_chao_shannon_equilibrium_scenario_via_engine() -> None:
    """The simulator reproduces Chao et al. (2015)'s equilibrium Shannon predictions.

    **Functional (public-API-only):** this test's own assertions compare
    the engine directly against `equilibrium_shannon_entropy_total`/
    `_subpopulation`/`equilibrium_shannon_differentiation` -- public
    closed-form formulas, not this file's own internal-recursion oracle
    -- so, like the Crow & Aoki torus test above, this one is already
    fully implementation-independent.

    Scenario: this file's own Part VI scenario (`N=100, m=0.01, mu=0.005,
    d=4`, 8 loci) -- already proven to converge `G_ST`/`D` well at
    horizon 1000 (`test_engine_reproduces_part_vi_equilibrium`, above) --
    but at horizon 2000, double that. Not arbitrary: Chao et al. (2015)
    state directly, in their own Discussion, that "the measure GST in
    FIM converges very quickly ... whereas the normalized mutual
    information based on Shannon entropy converges relatively slowly,"
    and a quick single-seed check across horizons 1000-8000 confirmed it
    for this exact scenario before committing to a horizon: `G_ST`-style
    convergence was already essentially flat by 1000, but the
    Shannon-based statistics were still measurably settling.

    Compares the engine's own simulated `H_T`/`H_S`/Shannon
    differentiation (`_pooled_shannon_statistics`, reading `log(within_
    hill_number(table, 1))`/`log(total_hill_number(table, 1))` off each
    replicate's final state -- already-exact quantities, not an
    approximation of Chao et al.'s own `¹H_S`/`¹H_T`) against
    `equilibrium_shannon_entropy_total`/`_subpopulation`/
    `equilibrium_shannon_differentiation`'s own closed-form (Eq. 6/7D/10)
    predictions -- the same "real engine against closed-form theory"
    structure `test_engine_reproduces_part_vi_equilibrium` already uses
    for `G_ST`/`D`, applied here to a genuinely independent statistic
    family (Shannon entropy, not heterozygosity) for the first time in
    this file.

    Unlike the Crow & Aoki torus scenario, this one has no migration-
    convention ambiguity to contend with (same `N`/`m`/`mu`/`d` as the
    already-validated Part VI scenario, only the horizon differs) and no
    fixation risk (8 loci at `mu=0.005` is comfortably far from the
    torus's own `mu=1e-5` regime) -- a 10-replicate characterization pass
    landed tight and stable (`H_S`'s own empirical mean within `0.008` of
    its theoretical prediction; `H_T` and Shannon differentiation both
    within about `1.5` characterized standard deviations, comfortably
    inside the wider band this test's own fewer assertion replicates
    produce). 6 replicates, base seed 715000 (distinct from every other
    scenario's own seed in this file). Runtime is under two minutes --
    far cheaper than the torus scenario's own ~23 minutes, since 8 loci
    at horizon 2000 is a much smaller integration than 150 loci at
    horizon 6000.
    """
    replicates = 6
    total_values, subpopulation_values, differentiation_values = (
        _run_engine_pooled_shannon(
            population_size=100,
            m=0.01,
            mu=0.005,
            d=4,
            n_loci=8,
            horizon=2000,
            replicates=replicates,
            seed=715000,
        )
    )
    mean_total = statistics.fmean(total_values)
    mean_subpopulation = statistics.fmean(subpopulation_values)
    mean_differentiation = statistics.fmean(differentiation_values)

    expected_total = equilibrium_shannon_entropy_total(100, 0.01, 0.005, 4)
    expected_subpopulation = equilibrium_shannon_entropy_subpopulation(
        100, 0.01, 0.005, 4
    )
    expected_differentiation = equilibrium_shannon_differentiation(100, 0.01, 0.005, 4)

    assert mean_total == pytest.approx(
        expected_total, abs=_band(_SIGMA_CHAO_SHANNON_TOTAL, replicates)
    )
    assert mean_subpopulation == pytest.approx(
        expected_subpopulation,
        abs=_band(_SIGMA_CHAO_SHANNON_SUBPOPULATION, replicates),
    )
    assert mean_differentiation == pytest.approx(
        expected_differentiation,
        abs=_band(_SIGMA_CHAO_SHANNON_DIFFERENTIATION, replicates),
    )
