"""Shared deterministic fixtures for the simulator test suite."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest
from hypothesis import settings

from fim.model.locus import LocusSpec
from fim.model.params import SimulationParams

settings.register_profile(
    "deterministic",
    derandomize=True,
    deadline=None,
    max_examples=100,
)
settings.load_profile("deterministic")


@pytest.fixture
def rng() -> Callable[[int], np.random.Generator]:
    """Return the only sanctioned deterministic RNG factory for tests."""

    def factory(seed: int) -> np.random.Generator:
        return np.random.Generator(np.random.PCG64(seed))

    return factory


@pytest.fixture
def tiny_params() -> SimulationParams:
    """Return a small, fast configuration for integration tests."""
    return SimulationParams(
        N=20,
        m=0.1,
        mu=0.01,
        d=2,
        seed=20260814,
        loci=(LocusSpec(1, 200),),
        convergence_window=4,
        convergence_tolerance=1.0,
        max_generations=10,
    )
