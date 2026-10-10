"""Shared configurations and checks for Backend V's exactness tests.

Backend V promises the same rows, report and final state as Backends L
and G for the same seed (`fim.model.vector_block`). The parity tests in
`test/model/test_vector_block.py` and `test/engine/test_vector_parity.py`
prove it over one shared matrix of configurations, defined here once so
the operator-level and engine-level tests cannot drift apart:
multi-locus with migration, more than 8 and more than 128 alleles per
deme, no migration, unequal deme sizes, per-locus mutation rates, 20
demes, a full migration matrix, the dear-nolan-low shape, and the
finite-alleles counterparts.

Test modules import this with `from vector_support import ...`, as they
import `conftest`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from fim.model.locus import LocusSpec
from fim.model.params import SimulationParams
from fim.model.topology import dense_matrix_from_neighbors, stepping_stone_neighbors


def loci(count: int, length: int = 200) -> tuple[LocusSpec, ...]:
    """Return `count` loci of the same length, numbered from 1."""
    return tuple(LocusSpec(index + 1, length) for index in range(count))


def ring_matrix(demes: int, rate: float) -> tuple[tuple[float, ...], ...]:
    """Return a stepping-stone ring migration matrix."""
    neighbors = stepping_stone_neighbors(demes, topology="ring", rate=rate)
    matrix = dense_matrix_from_neighbors(neighbors, demes)
    return tuple(tuple(row) for row in matrix)


def make_params(generations: int, **overrides: object) -> SimulationParams:
    """Build a seeded configuration that runs exactly `generations` generations.

    The convergence tolerance is tiny, so the monitor never stops a run
    early on its own (`max_generations` ends it); the defaults describe
    the dear-nolan-low shape at three loci.

    Args:
        generations: The run length.
        **overrides: `SimulationParams` fields replacing the defaults.
    """
    fields: dict[str, object] = {
        "gene_copies": 100,
        "m": 0.0001,
        "mu": 0.000001,
        "d": 5,
        "seed": 20261008,
        "loci": loci(3),
        "precision": 1e-15,
        "max_generations": generations,
        "n_replicates": 1,
        "stop_batch_early": False,
    }
    fields.update(overrides)
    return SimulationParams(**fields)  # type: ignore[arg-type]


INFINITE_CASES: dict[str, Callable[[int], SimulationParams]] = {
    "dear-nolan-low shape": make_params,
    "multi-locus with migration": lambda g: make_params(g, mu=0.01, m=0.05),
    "more than 8 alleles per deme": lambda g: make_params(
        g, loci=loci(2), mu=0.05, m=0.1
    ),
    "more than 128 alleles per deme": lambda g: make_params(
        g, loci=loci(1), gene_copies=400, mu=0.4, m=0.2, d=3
    ),
    "no migration": lambda g: make_params(g, loci=loci(4), mu=0.01, m=0.0),
    "unequal deme sizes": lambda g: make_params(
        g, gene_copies=(50, 80, 100, 120, 150), mu=0.01, m=0.02
    ),
    "per-locus mutation rates": lambda g: make_params(
        g,
        loci=(LocusSpec(1, 50), LocusSpec(2, 100), LocusSpec(3, 150)),
        mu=(0.0001, 0.0005, 0.002),
        m=0.03,
    ),
    "twenty demes": lambda g: make_params(g, loci=loci(2), d=20, mu=0.005, m=0.01),
    "migration matrix": lambda g: make_params(
        g, mu=0.01, m=ring_matrix(5, 0.05), loci=loci(2)
    ),
    "one locus": lambda g: make_params(g, loci=loci(1), mu=0.02, m=0.05),
    "zero mutation": lambda g: make_params(g, loci=loci(2), mu=0.0, m=0.05),
}
"""Infinite-alleles configurations, each a function of the run length."""

FINITE_CASES: dict[str, Callable[[int], SimulationParams]] = {
    "finite, one base": lambda g: make_params(
        g,
        loci=loci(2, 1),
        mu=0.02,
        m=0.05,
        mutation_model="finite_alleles",
    ),
    "finite, 16 states": lambda g: make_params(
        g,
        loci=loci(2, 2),
        mu=0.02,
        m=0.05,
        mutation_model="finite_alleles",
    ),
    "finite, 64 states": lambda g: make_params(
        g,
        loci=loci(3, 3),
        mu=0.02,
        m=0.05,
        mutation_model="finite_alleles",
    ),
    "finite, 4096 states": lambda g: make_params(
        g,
        loci=loci(2, 6),
        mu=0.02,
        m=0.05,
        mutation_model="finite_alleles",
    ),
    "finite, no migration": lambda g: make_params(
        g,
        loci=loci(2, 3),
        mu=0.02,
        m=0.0,
        mutation_model="finite_alleles",
    ),
    "finite, mixed lengths and rates": lambda g: make_params(
        g,
        loci=(LocusSpec(1, 2), LocusSpec(2, 4)),
        mu=(0.01, 0.03),
        m=0.05,
        d=4,
        mutation_model="finite_alleles",
    ),
    "finite, unequal sizes": lambda g: make_params(
        g,
        loci=loci(2, 3),
        gene_copies=(50, 80, 100),
        d=3,
        mu=0.03,
        m=0.05,
        mutation_model="finite_alleles",
    ),
    "finite, migration matrix": lambda g: make_params(
        g,
        loci=loci(2, 3),
        mu=0.02,
        m=ring_matrix(5, 0.05),
        mutation_model="finite_alleles",
    ),
    "finite, twenty demes": lambda g: make_params(
        g,
        loci=loci(2, 3),
        d=20,
        mu=0.01,
        m=0.02,
        mutation_model="finite_alleles",
    ),
}
"""Finite-alleles configurations, each a function of the run length."""


DENSE_MATRIX_4: tuple[tuple[float, ...], ...] = (
    (0.7, 0.1, 0.1, 0.1),
    (0.2, 0.5, 0.2, 0.1),
    (0.05, 0.15, 0.6, 0.2),
    (0.1, 0.1, 0.3, 0.5),
)
"""A dense asymmetric row-stochastic matrix over four demes."""

EDGE_MATRIX_4: tuple[tuple[float, ...], ...] = (
    (1.0, 0.0, 0.0, 0.0),
    (0.1, 0.6, 0.2, 0.1),
    (0.0, 0.5, 0.0, 0.5),
    (0.2, 0.2, 0.2, 0.4),
)
"""Row 0 keeps everything (no migrant weight, so no draw and no
normalization); row 2 has no self-weight (migrant weight 1, so no uniform is
consumed and every copy is replaced)."""


def stochastic(
    case: Callable[[int], SimulationParams],
) -> Callable[[int], SimulationParams]:
    """Return `case` with stochastic migrant counts switched on."""

    def build(generations: int) -> SimulationParams:
        """Rebuild the case's parameters with `migrant_sampling` stochastic."""
        return replace(case(generations), migrant_sampling="stochastic")

    return build


STOCHASTIC_INFINITE_CASES: dict[str, Callable[[int], SimulationParams]] = {
    "dear-nolan-low shape": stochastic(make_params),
    "multi-locus, m 0.05": stochastic(lambda g: make_params(g, mu=0.01, m=0.05)),
    "high migration": stochastic(
        lambda g: make_params(g, loci=loci(2), mu=0.05, m=0.3)
    ),
    "all migrants (no draw)": stochastic(
        lambda g: make_params(g, loci=loci(2), mu=0.02, m=1.0)
    ),
    "no migration (no draw)": stochastic(
        lambda g: make_params(g, loci=loci(2), mu=0.01, m=0.0)
    ),
    "more than 128 alleles per deme": stochastic(
        lambda g: make_params(g, loci=loci(1), gene_copies=400, mu=0.4, m=0.2, d=3)
    ),
    "unequal deme sizes": stochastic(
        lambda g: make_params(g, gene_copies=(50, 80, 100, 120, 150), mu=0.01, m=0.02)
    ),
    "per-locus mutation rates": stochastic(
        lambda g: make_params(
            g,
            loci=(LocusSpec(1, 50), LocusSpec(2, 100), LocusSpec(3, 150)),
            mu=(0.0001, 0.0005, 0.002),
            m=0.03,
        )
    ),
    "twenty demes": stochastic(
        lambda g: make_params(g, loci=loci(2), d=20, mu=0.005, m=0.01)
    ),
    "ring matrix": stochastic(
        lambda g: make_params(g, mu=0.01, m=ring_matrix(5, 0.05), loci=loci(2))
    ),
    "dense asymmetric matrix": stochastic(
        lambda g: make_params(g, mu=0.01, m=DENSE_MATRIX_4, d=4, loci=loci(2))
    ),
    "dense matrix, unequal sizes": stochastic(
        lambda g: make_params(
            g,
            mu=0.01,
            m=DENSE_MATRIX_4,
            d=4,
            gene_copies=(60, 90, 120, 150),
            loci=loci(2),
        )
    ),
    "matrix with edge rows": stochastic(
        lambda g: make_params(g, mu=0.01, m=EDGE_MATRIX_4, d=4, loci=loci(2))
    ),
}
"""Infinite-alleles configurations with `migrant_sampling: stochastic`."""

STOCHASTIC_FINITE_CASES: dict[str, Callable[[int], SimulationParams]] = {
    "finite, 16 states": stochastic(FINITE_CASES["finite, 16 states"]),
    "finite, 64 states, m 0.3": stochastic(
        lambda g: make_params(
            g,
            loci=loci(3, 3),
            mu=0.02,
            m=0.3,
            mutation_model="finite_alleles",
        )
    ),
    "finite, unequal sizes": stochastic(FINITE_CASES["finite, unequal sizes"]),
    "finite, migration matrix": stochastic(FINITE_CASES["finite, migration matrix"]),
    "finite, dense matrix": stochastic(
        lambda g: make_params(
            g,
            loci=loci(2, 3),
            mu=0.02,
            m=DENSE_MATRIX_4,
            d=4,
            mutation_model="finite_alleles",
        )
    ),
    "finite, twenty demes": stochastic(FINITE_CASES["finite, twenty demes"]),
}
"""Finite-alleles configurations with `migrant_sampling: stochastic`."""
