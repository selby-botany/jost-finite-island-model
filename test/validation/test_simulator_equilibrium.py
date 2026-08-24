"""Engine-level validation of scientific behavior against the design oracle.

Scope against definition-of-done item 4 and test-plan sections 7.3-7.4. These
tests run the real :func:`fim.engine.fim` simulator (not the closed-form
formulas) and check its emitted differentiation statistics:

* Part VI equilibrium (test-plan 7.3): the engine is integrated to equilibrium
  and its pooled ``G_ST`` and ``D`` land in a derived band around the exact
  finite-``N`` recursion (which itself bridges to the diffusion formulas).
* Dear-Nolan high migration (``N=2000, d=100, m=0.01, mu=0.001``; published
  ``G_ST ~= 0.02``, ``D ~= 0.90/0.91``): the engine DIRECTLY reproduces BOTH
  published values. It is started from a *derived* near-equilibrium state (see
  :func:`_dn2_equilibrium_start`) and shown to hold the published equilibrium,
  a stationarity check a biased operator would fail.
* Dear-Nolan low migration (``N=100, d=5, m=0.0001, mu=0.000001``; published
  ``G_ST ~= 0.97``, ``D ~= 0.04``): the engine DIRECTLY reproduces BOTH
  published values. It starts from a derived 26-locus ensemble (see
  :func:`_dn1_equilibrium_start`) whose pooled identities approximate the
  recursion fixed point at the available locus resolution, then holds the
  published equilibrium.

Two oracles are used, both derived from first principles rather than fitted to
the simulator:

* The closed-form diffusion equilibria ``equilibrium_g_st`` / ``equilibrium_d``
  (documentation Eq. 2 / Eq. 4). These are ``O(1/N)`` approximations.
* An exact per-generation identity recursion for the engine's own
  Migrate -> Mutate -> Drift pipeline (:func:`_pipeline_identity_dynamics`,
  built on :func:`_iterate_identities`). This is the finite-``N`` expectation
  of the very quantities the simulator samples, so it is the correct center for
  a seeded many-replicate band, it supplies the fixed point used to build the
  equilibrium starts.

Tolerance bands are derived analytically before any seed is chosen: each band
is ``k`` standard errors wide (``k = 5``), with the per-replicate spread taken
from an independent characterization pass and rounded up conservatively (the
``_SIGMA_*`` constants below). All statistical tests are seeded and therefore
bit-reproducible; the bands document the scientific margin, they are not tuned
to a particular realized draw.

The characterization pass behind every ``_SIGMA_*`` constant is versioned
(R18, ``doc/dev/20260818-claude-opus-5-project-review-rollup.md``, not
committed -- gitignored review material): the program is
:mod:`dev/bin/calibrate-statistical-bands`, and its raw output, seeds, and
environment fingerprint are retained in
``test/validation/statistical-calibration-evidence.json``, not merely summarized here in
a comment. An analytic bound was considered and is not currently available
(see that document's "Analytic bound" section) -- the per-replicate
``G_ST``/``D`` estimate is a ratio-of-means statistic sampled from a
multi-generation stochastic recursion, and a closed-form variance would
need delta-method propagation through that recursion's higher moments,
which has not been derived. Re-run the calibration script (never hand-edit
the constants) if a scenario's configuration changes enough that its
characterized spread might no longer be current; deliberately not wired
into ``build`` or ``ci.yml`` -- a characterization pass is itself
stochastic by design, so it stays out of the deterministic PR gate.
"""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from fim.engine import fim
from fim.model.allele import AlleleId
from fim.model.locus import LocusSpec
from fim.model.params import InitialFrequencies, SimulationParams
from fim.model.state import ModelState
from fim.persistence.store import TrajectoryRow
from fim.statistics import equilibrium_d, equilibrium_g_st, h_s, h_t

# Width, in standard errors, of every statistical band (test-plan 7.1).
_BAND_SIGMA = 5.0

# Largest tolerated gap between the exact finite-N identity recursion and the
# O(1/N) diffusion formulas Eq. 2 / Eq. 4; the residual is O(1/N) and is at
# most ~0.0034 for the smallest scenario (N=100, d=4).
_ONE_OVER_N_TOL = 0.005

_CALIBRATION_DATA_PATH = Path(__file__).with_name(
    "statistical-calibration-evidence.json"
)


def _load_sigma_constants() -> dict[str, tuple[float, float]]:
    """Load assertion sigma constants from the generated calibration artifact."""
    payload = json.loads(_CALIBRATION_DATA_PATH.read_text(encoding="utf-8"))
    scenarios = payload["scenarios"]
    required = ("part_vi", "dear_nolan_low", "dear_nolan_high")
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
    ) -> None:
        """Accept and discard one generation's rows."""
        del run_id, generation, rows

    def read(self, run_id: str) -> Iterator[TrajectoryRow]:
        """Yield nothing; no trajectory is retained."""
        del run_id
        return iter(())


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


def _run_engine_pooled(
    *,
    population_size: int,
    m: float,
    mu: float,
    d: int,
    n_loci: int,
    horizon: int,
    replicates: int,
    seed: int,
    initial_frequencies: InitialFrequencies | None = None,
) -> tuple[list[float], list[float]]:
    """Run the real engine and return per-replicate pooled ``(G_ST, D)``.

    The convergence monitor is effectively disabled so the run is a
    deterministic fixed-horizon integration: ``convergence_tolerance = 0``
    requires an exact match between the two half-window means, which a live
    drift/migration/mutation trajectory essentially never produces, so the
    run always stops exactly at ``max_generations``. ``convergence_window =
    horizon + 1`` is the largest window `SimulationParams` accepts for this
    ``max_generations`` (R23 rejects anything larger as structurally unable
    to fill before the cap) — it still cannot fill until the very last
    recorded generation, one step too late to preempt the cap.

    Args:
        population_size: Gene-copy count ``N``.
        m: Symmetric migration rate.
        mu: Per-copy mutation probability.
        d: Number of demes.
        n_loci: Number of independently tracked loci.
        horizon: Exact number of generations to integrate.
        replicates: Number of independently seeded replicates.
        seed: Base seed; replicate ``i`` uses ``seed + i``.
        initial_frequencies: Optional explicit start; otherwise the default
            Dirichlet founding condition is used.

    Returns:
        Two parallel lists, the pooled ``G_ST`` and ``D`` per replicate.
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
        initial_frequencies=initial_frequencies,
    )
    results = fim(population_size, m, mu, d, params=params, store=_DiscardingStore())
    replicate_results = results if isinstance(results, tuple) else (results,)
    g_values: list[float] = []
    d_values: list[float] = []
    for result in replicate_results:
        g_st, jost_d = _pooled_g_st_d(result.final_state, d, n_loci)
        g_values.append(g_st)
        d_values.append(jost_d)
    return g_values, d_values


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
    ``(1 - mu) ** 2`` approximation R3 replaced would fail this comparison
    outright, not merely drift outside a statistical tolerance.
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


@pytest.mark.slow
@pytest.mark.statistical
def test_engine_reproduces_part_vi_equilibrium() -> None:
    """The simulator approaches the Part VI equilibrium (test-plan 7.3).

    Runs the real engine to equilibrium for a general moderate-migration case
    (``N=100, m=0.01, mu=0.005, d=4``) and checks the pooled ``G_ST`` and ``D``
    against the exact finite-``N`` recursion, which in turn is shown to bridge
    to the diffusion formulas Eq. 2 / Eq. 4 within the ``O(1/N)`` residual.

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

    oracle_g_st, oracle_d = _pipeline_identity_dynamics(
        population_size=100,
        m=0.01,
        mu=0.005,
        d=4,
        within_identity=1.0,
        between_identity=0.0,
        generations=None,
    )

    # The finite-N oracle itself approaches Eq. 2 / Eq. 4 (test-plan 7.3).
    assert oracle_g_st == pytest.approx(
        equilibrium_g_st(100, 0.01, 0.005, 4), abs=_ONE_OVER_N_TOL
    )
    assert oracle_d == pytest.approx(equilibrium_d(0.01, 0.005, 4), abs=_ONE_OVER_N_TOL)

    # The simulator output lands inside the derived band around the oracle.
    assert mean_g == pytest.approx(oracle_g_st, abs=_band(_SIGMA_PART_VI_G, replicates))
    assert mean_d == pytest.approx(oracle_d, abs=_band(_SIGMA_PART_VI_D, replicates))


@pytest.mark.slow
@pytest.mark.statistical
def test_dear_nolan_low_migration_scenario_via_engine() -> None:
    """The simulator reproduces both low-migration Dear-Nolan values (7.4).

    Scenario: ``N=100, d=5, m=0.0001, mu=0.000001`` (published ``G_ST ~= 0.97,
    D ~= 0.04``). Configuration: 26 loci, 12 replicates, horizon 100, base seed
    884000, and a derived equilibrium start whose pooled identities approximate
    the recursion fixed point. Runtime is about 12 seconds.

    The multi-locus ensemble resolves a fixed point that is not representable
    at a single locus with ``N=100``. The engine holds it, providing a
    stationarity check that a biased operator fails.

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

    oracle_g_st, oracle_d = _pipeline_identity_dynamics(
        population_size=100,
        m=0.0001,
        mu=0.000001,
        d=d,
        within_identity=1.0,
        between_identity=0.0,
        generations=None,
    )

    band_g = _band(_SIGMA_DEAR_NOLAN_LOW_G, replicates)
    band_d = _band(_SIGMA_DEAR_NOLAN_LOW_D, replicates)
    assert mean_g == pytest.approx(0.97, abs=band_g)
    assert mean_d == pytest.approx(0.04, abs=band_d)
    assert mean_g == pytest.approx(oracle_g_st, abs=band_g)
    assert mean_d == pytest.approx(oracle_d, abs=band_d)


@pytest.mark.slow
@pytest.mark.statistical
def test_dear_nolan_high_migration_scenario_via_engine() -> None:
    """The simulator reproduces the high-migration Dear-Nolan equilibrium (7.4).

    Scenario: ``N=2000, d=100, m=0.01, mu=0.001`` (published ``G_ST ~= 0.02,
    D ~= 0.90/0.91``). Forward-integrating to this equilibrium from an
    undifferentiated start is not compute-feasible: the between-deme identity
    relaxes over ~1800 generations while the infinite-alleles pool grows to
    ~``10^4`` distinct ids, pushing per-generation cost past 0.4 s
    (>13 min/replicate). Instead the engine is started from a *derived*
    near-equilibrium state (:func:`_dn2_equilibrium_start`) and shown to HOLD
    both published values -- a stationarity check that a biased operator (for
    example the mutation defect regressed in ``test/model/test_operators.py``)
    would fail by drifting away from the fixed point.

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

    oracle_g_st, oracle_d = _identities_to_statistics(within_star, between_star, d)

    # The engine holds the exact recursion fixed point within the derived band.
    assert mean_g == pytest.approx(
        oracle_g_st, abs=_band(_SIGMA_DEAR_NOLAN_HIGH_G, replicates)
    )
    assert mean_d == pytest.approx(
        oracle_d, abs=_band(_SIGMA_DEAR_NOLAN_HIGH_D, replicates)
    )
    # Direct reproduction of BOTH published headline values.
    assert mean_g == pytest.approx(0.02, abs=0.005)
    assert 0.89 <= mean_d <= 0.92
