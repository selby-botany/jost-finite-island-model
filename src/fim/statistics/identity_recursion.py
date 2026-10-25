"""Compute expected identity-based statistics through time.

The recursion evolves within- and between-deme identity expectations with an
affine transition. `identity_recursion` provides a two-variable solution for
symmetric island models; `matrix_identity_trajectory` supports unequal deme
sizes and arbitrary migration matrices. Both use a shared mutation
probability. Results are expected values; individual trajectories vary due to
drift. See `doc/finite-island-model-introduction.md` for model context.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from operator import index as integer_index
from typing import SupportsIndex

import numpy as np
from numpy.typing import NDArray

from fim.config.limits import MAXIMUM_RECURSION_DEMES
from fim.config.numerics import (
    DEGENERACY_TOLERANCE,
    MAXIMUM_CONDITION,
    MINIMUM_DEMES,
    RECONSTRUCTION_TOLERANCE,
    SINGULAR_EIGENVECTORS,
    SINGULAR_FIXED_POINT,
)

# Statistics that are a function of the two identities alone. `E_ST`,
# `K_ST` and the effective-allele family need the allele structure itself.
IDENTITY_STATISTIC_NAMES = ("D", "G_ST", "H_S", "H_T", "H_ST")


@dataclass(frozen=True)
class IdentityRecursion:
    """The solved two-variable identity recursion for one configuration.

    Attributes:
        deme_count: Number of demes `d`.
        fixed_point: `(within, between)` the recursion settles to.
        eigenvalues: The two real eigenvalues of the linear part `A`.
        eigenvectors: `V` as rows: `eigenvectors[row][k]` is component
            `row` of eigenvector `k`.
        inverse: `V^-1`, same layout.
    """

    deme_count: int
    fixed_point: tuple[float, float]
    eigenvalues: tuple[float, float]
    eigenvectors: tuple[tuple[float, float], tuple[float, float]]
    inverse: tuple[tuple[float, float], tuple[float, float]]

    def identities_after(
        self, steps: float, within: float, between: float
    ) -> tuple[float, float]:
        """Return `(within, between)` after `steps` generations.

        Args:
            steps: Generations elapsed since the starting state (an
                integer in practice; any non-negative number works).
            within: Starting within-deme identity.
            between: Starting between-deme identity.

        Returns:
            The expected `(within, between)` identities.
        """
        offset = (within - self.fixed_point[0], between - self.fixed_point[1])
        coefficients = [
            self.inverse[k][0] * offset[0] + self.inverse[k][1] * offset[1]
            for k in range(2)
        ]
        decayed = [coefficients[k] * self.eigenvalues[k] ** steps for k in range(2)]
        return (
            self.fixed_point[0]
            + self.eigenvectors[0][0] * decayed[0]
            + self.eigenvectors[0][1] * decayed[1],
            self.fixed_point[1]
            + self.eigenvectors[1][0] * decayed[0]
            + self.eigenvectors[1][1] * decayed[1],
        )

    def statistics_after(
        self, steps: float, within: float, between: float
    ) -> dict[str, float]:
        """Return the identity-based statistics after `steps` generations.

        Args:
            steps: Generations elapsed since the starting state.
            within: Starting within-deme identity.
            between: Starting between-deme identity.

        Returns:
            One value per `IDENTITY_STATISTIC_NAMES`.
        """
        current_within, current_between = self.identities_after(steps, within, between)
        return identities_to_statistics(
            current_within, current_between, self.deme_count
        )


def identities_from_heterozygosities(
    h_s: float, h_t: float, deme_count: int
) -> tuple[float, float]:
    r"""Return `(within, between)` identities implied by $H_S$ and $H_T$.

    Inverts $H_S = 1 - \mathrm{within}$ and
    $H_T = 1 - (\mathrm{within} + (d - 1) \mathrm{between}) / d$.
    For equal demes and symmetric migration, only these two averages
    matter: the recursion commutes with permuting demes, so an uneven
    starting state relaxes exactly as its symmetrized average does.

    Args:
        h_s: Mean within-deme heterozygosity.
        h_t: Pooled heterozygosity.
        deme_count: Number of demes, at least 2.

    Returns:
        The `(within, between)` pair.
    """
    within = 1.0 - h_s
    between = (deme_count * (1.0 - h_t) - within) / (deme_count - 1)
    return within, between


def identities_to_statistics(
    within: float, between: float, deme_count: int
) -> dict[str, float]:
    """Return the identity-based statistics for one `(within, between)`.

    The pooled forms of `h_s`, `h_t`, `g_st` and `jost_d` written in terms
    of the two identities. A zero denominator (no diversity left) gives
    `0.0` for $G_{ST}$ and $D$ rather than `nan`.

    Args:
        within: Within-deme identity.
        between: Between-deme identity.
        deme_count: Number of demes `d`.

    Returns:
        `{"D", "G_ST", "H_S", "H_T", "H_ST"}` as floats.
    """
    d = deme_count
    h_s = 1.0 - within
    h_t = 1.0 - (within + (d - 1) * between) / d
    h_st = h_t - h_s
    g_st = h_st / h_t if h_t > 0.0 else 0.0
    jost_d = 1.0 - between / within if within > 0.0 else 0.0
    return {"D": jost_d, "G_ST": g_st, "H_S": h_s, "H_T": h_t, "H_ST": h_st}


def identity_recursion(
    population_size: int, m: float, mu: float, d: int
) -> IdentityRecursion:
    r"""Solve the engine's identity recursion for one configuration.

    One generation is migrate, drift, mutate — the textbook Wright-Fisher
    island model. Migration maps the two identities by fixed coefficients
    of $m$ and $d$; drift adds $1/N$ to within-deme identity (the chance
    two copies drawn with replacement are the same copy) and keeps
    $1 - 1/N$ of the rest; mutation then keeps two distinct copies
    identical only if neither mutated, a factor $(1 - \mu)^2$ (one copy
    compared with itself stays identical, so the $1/N$ term is not
    scaled):

    ```math
    \begin{array}{rl}
    \mathrm{within}^\prime
        &= \frac{1}{N} + \left(1 - \frac{1}{N}\right)(1 - \mu)^2
            \mathrm{within}_{\mathrm{migrated}}, \\
    \mathrm{between}^\prime
        &= (1 - \mu)^2 \mathrm{between}_{\mathrm{migrated}}.
    \end{array}
    ```

    Without migration, and for two *distinct* copies
    with $F = (\mathrm{within} - 1/N) / (1 - 1/N)$, this is the textbook

    ```math
    F^\prime = (1 - \mu)^2
        \left[\frac{1}{N} + \left(1 - \frac{1}{N}\right)F\right].
    ```

    Args:
        population_size: Gene copies `N` per deme.
        m: Symmetric migration rate.
        mu: Per-copy mutation probability.
        d: Number of demes, at least 2.

    Returns:
        The solved recursion.

    Raises:
        ValueError: If `d < 2`, an input is out of range, or the
            configuration has no unique closed form (no migration and no
            mutation leaves no fixed point; two equal eigenvalues make
            the eigenvector matrix singular).
    """
    if d < MINIMUM_DEMES:
        raise ValueError("identity recursion needs at least two demes")
    if population_size < 1 or not 0.0 <= m <= 1.0 or not 0.0 <= mu <= 1.0:
        raise ValueError("identity recursion inputs are out of range")

    # Migration coefficients (retained fraction `1 - m`, `m / (d - 1)` to
    # each other deme), then the mutation and drift steps folded in.
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
    inverse_size = 1.0 / population_size
    survival = (1.0 - mu) ** 2
    keep = (1.0 - inverse_size) * survival
    a11 = keep * within_from_within
    a12 = keep * within_from_between
    a21 = survival * between_from_within
    a22 = survival * between_from_between

    # Fixed point: solve (I - A) x = c with c = (1/N, 0).
    determinant = (1.0 - a11) * (1.0 - a22) - a12 * a21
    if not abs(determinant) > SINGULAR_FIXED_POINT:
        raise ValueError(
            "no closed-form trajectory: the configuration has no unique "
            "fixed point (no migration and no mutation)"
        )
    fixed_within = inverse_size * (1.0 - a22) / determinant
    fixed_between = inverse_size * a21 / determinant

    # Eigen-decomposition of the 2 by 2 matrix. The discriminant
    # `(a11 - a22)^2 + 4 a12 a21` is never negative for a nonnegative
    # matrix, so both eigenvalues are real.
    trace = a11 + a22
    discriminant = (a11 - a22) ** 2 + 4.0 * a12 * a21
    root = math.sqrt(max(discriminant, 0.0))
    high = (trace + root) / 2.0
    low = (trace - root) / 2.0
    if root <= DEGENERACY_TOLERANCE * max(abs(high), abs(low), SINGULAR_EIGENVECTORS):
        raise ValueError(
            "no closed-form trajectory: the recursion has a repeated "
            "eigenvalue for this configuration"
        )

    def eigenvector(value: float) -> tuple[float, float]:
        # Both `(a12, value - a11)` and `(value - a22, a21)` satisfy
        # `A v = value v`; take whichever is farther from zero.
        first = (a12, value - a11)
        second = (value - a22, a21)
        return first if math.hypot(*first) >= math.hypot(*second) else second

    v_high = eigenvector(high)
    v_low = eigenvector(low)
    matrix_determinant = v_high[0] * v_low[1] - v_low[0] * v_high[1]
    if not abs(matrix_determinant) > SINGULAR_EIGENVECTORS:
        raise ValueError("no closed-form trajectory: singular eigenvectors")
    return IdentityRecursion(
        deme_count=d,
        fixed_point=(fixed_within, fixed_between),
        eigenvalues=(high, low),
        eigenvectors=((v_high[0], v_low[0]), (v_high[1], v_low[1])),
        inverse=(
            (v_low[1] / matrix_determinant, -v_low[0] / matrix_determinant),
            (-v_high[1] / matrix_determinant, v_high[0] / matrix_determinant),
        ),
    )


type LocusFrequencies[AlleleKey: SupportsIndex] = Sequence[
    Sequence[Mapping[AlleleKey, float]]
]


def identity_matrix_from_frequencies[AlleleKey: SupportsIndex](
    frequencies: LocusFrequencies[AlleleKey],
) -> NDArray[np.float64]:
    r"""Return the $d$ by $d$ identity matrix of a starting population.

    Entry `[i][j]` is $\sum_k x_{ik} x_{jk}$ averaged over loci: the chance that
    one gene copy drawn from deme `i` and one from deme `j` are the same
    allele. The diagonal is the with-replacement within-deme identity,
    $1 - H_S$'s per-deme term.

    Args:
        frequencies: `frequencies[deme][locus]` maps allele to frequency
            (`ModelState.frequencies`).

    Returns:
        The averaged symmetric `d` by `d` matrix.
    """
    demes = len(frequencies)
    loci = len(frequencies[0])
    total = np.zeros((demes, demes))
    for locus in range(loci):
        alleles = sorted(
            {a for deme in frequencies for a in deme[locus]}, key=integer_index
        )
        table = np.array(
            [[deme[locus].get(a, 0.0) for a in alleles] for deme in frequencies]
        )
        total += table @ table.T
    return total / loci


def matrix_identity_trajectory(
    *,
    deme_sizes: Sequence[int],
    migration: Sequence[Sequence[float]] | NDArray[np.float64],
    mutation: float,
    initial_identities: NDArray[np.float64],
    generations: Sequence[int],
) -> dict[str, list[float]]:
    r"""Return the expected statistics at each of `generations`.

    One generation (migrate, drift, mutate) is

    ```math
    J \mapsto (1 - \mu)^2 M J M^{\mathsf{T}}
    ```

    off the diagonal and

    ```math
    J_{ii} \mapsto \frac{1}{N_i}
        + \left(1 - \frac{1}{N_i}\right)(1 - \mu)^2
            (M J M^{\mathsf{T}})_{ii}
    ```

    on it, with $M$ the row-stochastic migration matrix: drift's same-copy
    term $1/N_i$ is a copy compared with itself, which mutation cannot make
    differ. That is affine in the flattened matrix,
    $x^\prime = Ax + c$, so

    ```math
    x_t = x^* + V \operatorname{diag}(\lambda^t) V^{-1} (x_0 - x^*)
    ```

    (eigenvalues may be
    complex; the result is real). $D$, $G_{ST}$, $H_S$, $H_T$ and $H_{ST}$ use
    equal deme weights, as the engine's reports do.

    Args:
        deme_sizes: Gene copies in every deme.
        migration: `d` by `d` row-stochastic migration matrix.
        mutation: Per-copy mutation probability.
        initial_identities: Starting `d` by `d` identity matrix.
        generations: Generations since the starting state, each `>= 0`.

    Returns:
        One list per `IDENTITY_STATISTIC_NAMES`, aligned with
        `generations`.

    Raises:
        ValueError: If the inputs are out of range, `d` is outside
            `[2, MAXIMUM_RECURSION_DEMES]`, or the operator has no fixed
            point or no reliable eigen-decomposition.
    """
    d = len(deme_sizes)
    if not MINIMUM_DEMES <= d <= MAXIMUM_RECURSION_DEMES:
        raise ValueError(
            f"matrix identity trajectory needs between {MINIMUM_DEMES} and "
            f"{MAXIMUM_RECURSION_DEMES} demes"
        )
    if not 0.0 <= mutation <= 1.0 or min(deme_sizes) < 1:
        raise ValueError("matrix identity trajectory inputs are out of range")
    matrix = np.asarray(migration, dtype=np.float64)
    if matrix.shape != (d, d):
        raise ValueError("migration matrix does not match the number of demes")

    # Operator on the row-major flattened identity matrix.
    inverse_size = 1.0 / np.asarray(deme_sizes, dtype=np.float64)
    survival = (1.0 - mutation) ** 2
    coefficient = np.full((d, d), survival)
    diagonal = np.diag_indices(d)
    coefficient[diagonal] = (1.0 - inverse_size) * survival
    operator = coefficient.ravel()[:, None] * np.kron(matrix, matrix)
    drift = np.zeros((d, d))
    drift[diagonal] = inverse_size
    constant = drift.ravel()

    # Fixed point and eigen-decomposition, each checked before use.
    size = d * d
    try:
        fixed = np.linalg.solve(np.eye(size) - operator, constant)
        values, vectors = np.linalg.eig(operator)
        weights = np.linalg.solve(vectors, (initial_identities.ravel() - fixed))
    except np.linalg.LinAlgError as error:
        raise ValueError("no closed-form trajectory: singular operator") from error
    scale = max(float(np.max(np.abs(operator))), 1.0)
    reconstruction = np.max(np.abs(operator @ vectors - vectors * values))
    if (
        not np.all(np.isfinite(fixed))
        or np.linalg.cond(vectors) > MAXIMUM_CONDITION
        or reconstruction > RECONSTRUCTION_TOLERANCE * scale
    ):
        raise ValueError("no closed-form trajectory: unreliable eigen-decomposition")

    steps = np.asarray(generations, dtype=np.float64)
    decayed = weights[:, None] * values[:, None] ** steps[None, :]
    identities = (fixed[:, None] + (vectors @ decayed).real).reshape(d, d, -1)
    within = np.trace(identities, axis1=0, axis2=1) / d
    pooled = identities.sum(axis=(0, 1)) / (d * d)
    h_s = 1.0 - within
    h_t = 1.0 - pooled
    h_st = h_t - h_s
    with np.errstate(divide="ignore", invalid="ignore"):
        g_st = np.where(h_t > 0.0, h_st / h_t, 0.0)
        jost_d = np.where(within > 0.0, h_st / within * d / (d - 1), 0.0)
    return {
        "D": jost_d.tolist(),
        "G_ST": g_st.tolist(),
        "H_S": h_s.tolist(),
        "H_T": h_t.tolist(),
        "H_ST": h_st.tolist(),
    }
