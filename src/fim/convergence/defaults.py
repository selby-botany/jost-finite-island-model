"""Derive convergence defaults from the modeled population's own timescale.

A fixed trailing window (say 50 generations) cannot tell "the statistic has
stopped changing" from "the statistic is changing too slowly to see in 50
generations". How long a run must be watched depends on how fast the
population forgets its starting state: its *relaxation time*, `tau`. This
module estimates `tau` from the migration, mutation and deme-size
parameters and turns it into a default `convergence_window` and
`max_generations`.

Why `tau` has this form, and the numerical check behind it, is written up in
the design document `20260925-claude-sonnet-5-convergence-defaults-derived-
from-model-design.md` (Appendix A) and, for users, in `doc/convergence.md`.
In short: every watched statistic is a smooth function of the pairwise
identity probabilities `J`, `J` obeys a linear recurrence, and the run is
settled only once that recurrence's slowest mode has decayed.

Two routes give `tau`:

- `island_relaxation_time` is a closed form, exact to about 1% for the
  symmetric island model with equal deme sizes (the default `m` scalar).
- `recursion_relaxation_time` builds the recurrence's linear part as a
  `d² by d²` matrix and takes its spectral radius. It handles any migration
  matrix and unequal deme sizes, at a cost that limits it to small `d`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np

WINDOW_RELAXATION_MULTIPLE: Final = 3.0
"""Default `convergence_window`, in units of the relaxation time `tau`.

Set by `dev/bin/calibrate-convergence-defaults` and recorded in
`test/validation/convergence-defaults-evidence.json`. The noise-free
analysis (design Appendix A.6) already accepts a residual of a third of
`convergence_tolerance` at `2 tau`, but a single stochastic run also
carries sampling noise. Golden Part VI (60 replicates, 8 loci) stops
0.14 below its analytic D at `1 tau`, 0.060 at `2 tau` (outside the 0.05
acceptance) and 0.040 at `3 tau`; a longer window does not improve on that
(0.039 at `4 tau`), because the remaining offset comes from estimating D
over a finite number of loci, not from stopping early. The slower regimes
measured (Dear-Nolan low, ring, unequal mutation rates) are within 0.025
at `2 tau` and within 0.01 at `4 tau`.
"""

CAP_RELAXATION_MULTIPLE: Final = 15.0
"""Default `max_generations`, in units of `tau`.

A run needs its window plus the time to settle. The slowest stop measured
was `10.2 tau` (Golden Part VI at a window of `4 tau`; `9.0 tau` at the
shipped `3 tau`), so `15 tau` leaves a margin of about 1.5 and no measured
run ended at the cap.
"""

MINIMUM_WINDOW: Final = 50
"""Smallest derived window: the historical default, kept as a floor."""

MINIMUM_MAX_GENERATIONS: Final = 10_000
"""Smallest derived cap: the historical default, kept as a floor."""

ABSOLUTE_MAX_GENERATIONS: Final = 10_000_000
"""Ceiling on a derived cap, so a nearly isolated system stays finite."""

MAXIMUM_RECURSION_DEMES: Final = 24
"""Largest `d` for which the `d² by d²` eigenvalue route is used.

A 24-deme system is a 576 by 576 eigenproblem, well under a second. The
cost grows as `d⁶`, so larger explicit migration matrices must be given
explicit convergence values.
"""

MigrationInput = float | Sequence[Sequence[float]]


@dataclass(frozen=True, slots=True)
class DerivedConvergence:
    """Convergence settings derived from a model's relaxation time.

    Args:
        window: Trailing stability-window length, in generations.
        max_generations: Hard generation cap.
        relaxation_time: The estimated `tau`, in generations, for display.
    """

    window: int
    max_generations: int
    relaxation_time: float


def derive_convergence_defaults(
    *,
    deme_sizes: Sequence[int],
    migration: MigrationInput,
    mutation_rates: Sequence[float],
) -> DerivedConvergence:
    """Return the default window and cap for one model.

    Args:
        deme_sizes: Gene-copy count of every deme, so `d` values.
        migration: A scalar symmetric rate `m`, or a `d` by `d`
            row-stochastic matrix.
        mutation_rates: Per-locus mutation probabilities.

    Returns:
        The derived window, cap and relaxation time.

    Raises:
        ValueError: If the model has no relaxation time (no migration and
            no mutation), or is an explicit matrix with more than
            `MAXIMUM_RECURSION_DEMES` demes.
    """
    tau = relaxation_time(
        deme_sizes=deme_sizes,
        migration=migration,
        mutation_rates=mutation_rates,
    )
    cap = min(
        ABSOLUTE_MAX_GENERATIONS,
        max(MINIMUM_MAX_GENERATIONS, math.ceil(CAP_RELAXATION_MULTIPLE * tau)),
    )
    # The monitor rejects a window larger than `max_generations + 1`, so the
    # window can never exceed the cap even when the cap is clamped.
    window = min(cap, max(MINIMUM_WINDOW, math.ceil(WINDOW_RELAXATION_MULTIPLE * tau)))
    return DerivedConvergence(window=window, max_generations=cap, relaxation_time=tau)


def describe_derived_convergence(
    *, window: int, max_generations: int, relaxation_time: float
) -> str:
    """Return the one-line, plain-language statement of derived settings.

    Shared by the command line and the desktop app so the two say the same
    thing.

    Args:
        window: The derived trailing window, in generations.
        max_generations: The derived generation cap.
        relaxation_time: The estimated relaxation time, in generations.

    Returns:
        A sentence such as "Convergence: window 59,078 generations, cap
        295,390 (derived; this model needs about 19,693 generations to
        forget its starting state)".
    """
    return (
        f"Convergence: window {window:,} generations, cap {max_generations:,} "
        f"(derived; this model needs about {relaxation_time:,.0f} generations "
        "to forget its starting state)"
    )


def island_relaxation_time(
    *, total_size: float, deme_count: int, migration: float, mutation: float
) -> float:
    """Return `tau` for the symmetric island model, in closed form.

    `T = N_total + (d - 1) / (2 m)` is the mean pairwise coalescence time
    (the time for two gene copies to reach one deme, plus the time for the
    whole population to coalesce). Mutation destroys identity at an
    independent rate `2 mu`, and independent rates add, so
    `tau = 1 / (2 mu + 1 / T)`.

    Args:
        total_size: Sum of every deme's gene-copy count.
        deme_count: Number of demes `d`.
        migration: Scalar migration rate `m`.
        mutation: Mean per-locus mutation probability.

    Returns:
        The relaxation time in generations.

    Raises:
        ValueError: If `migration` and `mutation` are both zero.
    """
    # No migration means the two copies never meet, so that term of the
    # rate vanishes rather than the time diverging.
    coalescence_rate = (
        0.0
        if migration <= 0.0
        else 1.0 / (total_size + (deme_count - 1) / (2.0 * migration))
    )
    return _time_from_rate(2.0 * mutation + coalescence_rate)


def recursion_relaxation_time(
    *,
    deme_sizes: Sequence[int],
    migration: Sequence[Sequence[float]],
    mutation: float,
) -> float:
    """Return `tau` from the identity recursion's slowest mode.

    The pairwise identity matrix `J` updates as migrate (`M J Mᵀ`), mutate
    (scale by `(1 - mu)²`), then drift (each diagonal entry `J[i][i]` keeps
    a fraction `1 - 1/N_i`). Flattening `J` makes this a `d² by d²` matrix;
    its spectral radius `rho` gives `tau = 1 / (1 - rho)`.

    Args:
        deme_sizes: Gene-copy count of every deme.
        migration: A `d` by `d` row-stochastic migration matrix.
        mutation: Mean per-locus mutation probability.

    Returns:
        The relaxation time in generations.

    Raises:
        ValueError: If `d` exceeds `MAXIMUM_RECURSION_DEMES`, or the model
            has no relaxation time.
    """
    # Size guard: the eigenproblem is d² by d², so cost grows as d⁶.
    deme_count = len(deme_sizes)
    if deme_count > MAXIMUM_RECURSION_DEMES:
        raise ValueError(
            f"convergence defaults cannot be derived for an explicit migration "
            f"matrix with more than {MAXIMUM_RECURSION_DEMES} demes; set "
            f"convergence_window and max_generations explicitly"
        )
    matrix = np.asarray(migration, dtype=np.float64)
    # Linear part of the recursion, acting on the flattened identity matrix.
    survival = (1.0 - mutation) ** 2
    damping = np.ones((deme_count, deme_count))
    damping[np.diag_indices(deme_count)] = 1.0 - 1.0 / np.asarray(
        deme_sizes, dtype=np.float64
    )
    operator = np.diag(damping.ravel()) @ (survival * np.kron(matrix, matrix))
    spectral_radius = float(np.max(np.abs(np.linalg.eigvals(operator))))
    return _time_from_rate(1.0 - spectral_radius)


def relaxation_time(
    *,
    deme_sizes: Sequence[int],
    migration: MigrationInput,
    mutation_rates: Sequence[float],
) -> float:
    """Return the relaxation time `tau` for one model, choosing the route.

    Equal deme sizes with a scalar `m` use the closed form. Everything else
    (an explicit matrix, or unequal sizes, where the scalar `m` is a
    size-weighted migrant pool) uses the recursion's eigenvalue.

    Args:
        deme_sizes: Gene-copy count of every deme.
        migration: A scalar `m`, or a `d` by `d` row-stochastic matrix.
        mutation_rates: Per-locus mutation probabilities.

    Returns:
        The relaxation time in generations.

    Raises:
        ValueError: If the model has no relaxation time, or the eigenvalue
            route is needed but `d` is too large.
    """
    mutation = math.fsum(mutation_rates) / len(mutation_rates)
    sizes = tuple(deme_sizes)
    if isinstance(migration, int | float) and len(set(sizes)) == 1:
        return island_relaxation_time(
            total_size=math.fsum(sizes),
            deme_count=len(sizes),
            migration=float(migration),
            mutation=mutation,
        )
    matrix = (
        _island_matrix(sizes, float(migration))
        if isinstance(migration, int | float)
        else migration
    )
    return recursion_relaxation_time(
        deme_sizes=sizes, migration=matrix, mutation=mutation
    )


def _island_matrix(sizes: Sequence[int], migration: float) -> list[list[float]]:
    """Return the scalar-`m` migration matrix for unequal deme sizes.

    Each deme keeps `1 - m` and receives `m` from the size-weighted average
    of every other deme, as the configuration reference defines the scalar
    form.

    Args:
        sizes: Gene-copy count of every deme.
        migration: Scalar migration rate.

    Returns:
        A `d` by `d` row-stochastic matrix.
    """
    total = math.fsum(sizes)
    return [
        [
            1.0 - migration
            if row == column
            else migration * size / (total - sizes[row])
            for column, size in enumerate(sizes)
        ]
        for row in range(len(sizes))
    ]


def _time_from_rate(rate: float) -> float:
    """Return `1 / rate`, rejecting a rate that leaves no dynamics to wait for.

    Args:
        rate: Combined per-generation relaxation rate.

    Returns:
        The time constant in generations.

    Raises:
        ValueError: If `rate` is not positive.
    """
    if not rate > 0.0:
        raise ValueError(
            "no relaxation time: the model has no migration and no mutation "
            "(or too little of either to measure); set convergence_window "
            "and max_generations explicitly"
        )
    return 1.0 / rate
