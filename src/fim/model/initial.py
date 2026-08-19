"""Seeded initial-condition strategies."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

import numpy as np

from fim.model.allele import AlleleId, founding_allele_ids
from fim.model.params import SimulationParams
from fim.model.state import ModelState


class InitialConditionGenerator(Protocol):
    """Generate generation zero from validated parameters and one RNG."""

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
    """Use the frequency table supplied in ``SimulationParams`` verbatim."""

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

    Args:
        params: Validated simulation parameters.
        rng: Optional run generator. A PCG64 generator is created from
            ``params.seed`` only when called outside the engine.

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
