"""The scientific model: population, migration, mutation, loci, and start.

These frozen dataclasses describe *what is simulated*. They hold the values
a person chose, in the units a paper would use (individuals and ploidy, not
gene copies), and are checked field by field when built. Rules that need
several parts at once, or the engine's own limits, are enforced when a
`RunSpec` is compiled to the engine's parameters.

Java readers: `Mutation` and `InitialCondition` are *unions of frozen
dataclasses*, the Python counterpart of a sealed interface with record
implementations. Dispatch on them with `match`; a new variant is a new
class added to the union.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from fim.config.defaults import DEFAULT_LOCUS_LENGTH
from fim.errors import ConfigurationError
from fim.model.params import (
    ALLOWED_PLOIDIES,
    InitialFrequencies,
    MigrantSampling,
    Migration,
)
from fim.spec.validation import (
    check_choice,
    check_finite,
    check_int,
    check_probability,
)

Ploidy: TypeAlias = Literal[1, 2, 3, 4]
"""Gene copies carried by one individual (haploid through tetraploid)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Population:
    """The size of each deme and the ploidy of its individuals.

    Args:
        individuals: Individuals per deme: one number shared by every deme,
            or one number per deme.
        ploidy: Gene copies per individual (1 to 4). Required: the number of
            gene copies simulated is `individuals * ploidy`, so it is chosen
            and never guessed.
    """

    individuals: int | tuple[int, ...]
    ploidy: Ploidy

    def __post_init__(self) -> None:
        """Normalize a list of sizes to a tuple and check every value."""
        if not isinstance(self.individuals, int):
            object.__setattr__(self, "individuals", tuple(self.individuals))
        for index, size in enumerate(
            (self.individuals,)
            if isinstance(self.individuals, int)
            else self.individuals
        ):
            check_int(f"population.individuals[{index}]", size, minimum=1)
        if isinstance(self.individuals, tuple) and not self.individuals:
            raise ConfigurationError("population.individuals must not be empty")
        if self.ploidy not in ALLOWED_PLOIDIES or isinstance(self.ploidy, bool):
            legal = ", ".join(str(item) for item in ALLOWED_PLOIDIES)
            raise ConfigurationError(f"population.ploidy must be one of {legal}")

    @property
    def gene_copies(self) -> int | tuple[int, ...]:
        """Return the gene copies per deme: individuals times ploidy."""
        if isinstance(self.individuals, int):
            return self.individuals * self.ploidy
        return tuple(size * self.ploidy for size in self.individuals)


@dataclass(frozen=True, slots=True, kw_only=True)
class Locus:
    """One tracked locus.

    Args:
        id: Positive identifier, unique within a model.
        length: Length in base pairs. Under finite alleles the number of
            possible alleles is `4 ** length`.
    """

    id: int
    length: int = DEFAULT_LOCUS_LENGTH

    def __post_init__(self) -> None:
        """Check the identifier and length."""
        check_int("locus.id", self.id, minimum=1)
        check_int("locus.length", self.length, minimum=1)


def _normalize_rate(
    path: str, rate: float | Sequence[float]
) -> float | tuple[float, ...]:
    """Check a mutation rate and return a float or a tuple of floats.

    Args:
        path: Dotted field path for messages.
        rate: One rate for every locus, or one per locus.

    Returns:
        The rate as given (a float), or the per-locus rates as a tuple.
    """
    if isinstance(rate, bool):
        raise ConfigurationError(f"{path} must be a number or a sequence of numbers")
    if isinstance(rate, int | float):
        check_probability(path, rate)
        return float(rate)
    rates = tuple(rate)
    if not rates:
        raise ConfigurationError(f"{path} must not be empty")
    for index, value in enumerate(rates):
        check_probability(f"{path}[{index}]", value)
    return tuple(float(value) for value in rates)


@dataclass(frozen=True, slots=True, kw_only=True)
class InfiniteAlleles:
    """Mutation to a brand-new allele each time (the infinite-alleles model).

    Args:
        rate: Probability that one gene copy mutates in one generation: one
            value for every locus, or one value per locus.
    """

    rate: float | tuple[float, ...]

    def __post_init__(self) -> None:
        """Check the rate."""
        object.__setattr__(self, "rate", _normalize_rate("mutation.rate", self.rate))


@dataclass(frozen=True, slots=True, kw_only=True)
class FiniteAlleles:
    """Mutation among a fixed set of alleles (the K-allele model).

    The number of alleles at a locus is `4 ** length` (see `Locus`).

    Args:
        rate: Probability that one gene copy mutates in one generation: one
            value for every locus, or one value per locus.
    """

    rate: float | tuple[float, ...]

    def __post_init__(self) -> None:
        """Check the rate."""
        object.__setattr__(self, "rate", _normalize_rate("mutation.rate", self.rate))


Mutation: TypeAlias = InfiniteAlleles | FiniteAlleles
"""The mutation model: one of the variants above."""


@dataclass(frozen=True, slots=True, kw_only=True)
class FoundingAlleles:
    """Start every deme from the same founding alleles (the default).

    Args:
        count: Number of founding alleles per locus.
        concentration: Symmetric Dirichlet concentration of their
            frequencies; larger values make them more nearly equal.
    """

    count: int = 2
    concentration: float = 1.0

    def __post_init__(self) -> None:
        """Check the count and concentration."""
        check_int("initial.count", self.count, minimum=1)
        check_finite("initial.concentration", self.concentration, above=0.0)


@dataclass(frozen=True, slots=True, kw_only=True)
class ExplicitFrequencies:
    """Start from stated allele frequencies.

    Args:
        frequencies: For each deme, for each locus, a mapping from allele ID
            to frequency (the configuration file's `p_0`).
    """

    frequencies: InitialFrequencies

    def __post_init__(self) -> None:
        """Require a non-empty table."""
        if not self.frequencies:
            raise ConfigurationError("initial.frequencies must not be empty")


@dataclass(frozen=True, slots=True, kw_only=True)
class EquilibriumSplit:
    """Start from an ancestral population at equilibrium, then split it.

    One ancestral deme runs until its heterozygosity settles, and is then
    divided into the model's demes.

    Args:
        window: Generations over which the ancestral phase must be stable.
        tolerance: Stability tolerance for that phase.
        max_generations: Cap on the ancestral phase.
        founding: How the ancestral deme starts.
    """

    window: int
    tolerance: float
    max_generations: int
    founding: FoundingAlleles = field(default_factory=FoundingAlleles)

    def __post_init__(self) -> None:
        """Check the three stopping values against one another."""
        check_int("initial.window", self.window, minimum=2)
        check_finite("initial.tolerance", self.tolerance, above=0.0)
        check_int("initial.max_generations", self.max_generations, minimum=1)
        if self.window > self.max_generations:
            raise ConfigurationError(
                "initial.window cannot exceed initial.max_generations"
            )


InitialCondition: TypeAlias = FoundingAlleles | ExplicitFrequencies | EquilibriumSplit
"""How generation zero is built: one of the variants above."""


def _normalize_migration(value: Migration | Sequence[Sequence[float]]) -> Migration:
    """Check a migration value and return a float or a tuple-of-tuples matrix.

    Args:
        value: A migration rate, or a square matrix of rates.

    Returns:
        The rate as a float, or the matrix as nested tuples.
    """
    if isinstance(value, bool):
        raise ConfigurationError("model.migration must be a rate or a square matrix")
    if isinstance(value, int | float):
        check_probability("model.migration", value)
        return float(value)
    rows = tuple(tuple(row) for row in value)
    if not rows or any(len(row) != len(rows) for row in rows):
        raise ConfigurationError("model.migration must be a rate or a square matrix")
    for i, row in enumerate(rows):
        for j, entry in enumerate(row):
            check_probability(f"model.migration[{i}][{j}]", entry)
    return tuple(tuple(float(entry) for entry in row) for row in rows)


@dataclass(frozen=True, slots=True, kw_only=True)
class Model:
    """The finite island model being simulated.

    Args:
        demes: Number of demes (islands), at least 2.
        population: Deme sizes and ploidy.
        migration: A symmetric migration rate, or a row-stochastic matrix.
        mutation: The mutation model and rate.
        loci: The tracked loci (default: one locus of the default length).
        migrant_sampling: ``"continuous"`` moves expected fractions of each
            deme; ``"stochastic"`` draws whole migrant counts.
        initial: How generation zero is built.
    """

    demes: int
    population: Population
    migration: Migration
    mutation: Mutation
    loci: tuple[Locus, ...] = (Locus(id=1),)
    migrant_sampling: MigrantSampling = "continuous"
    initial: InitialCondition = field(default_factory=FoundingAlleles)

    def __post_init__(self) -> None:
        """Normalize sequences and check each part and the sizes that must agree."""
        check_int("model.demes", self.demes, minimum=2)
        object.__setattr__(self, "migration", _normalize_migration(self.migration))
        object.__setattr__(self, "loci", tuple(self.loci))
        check_choice("model.migrant_sampling", self.migrant_sampling, MigrantSampling)
        if not isinstance(self.population, Population):
            raise ConfigurationError("model.population must be a Population")
        if not isinstance(self.mutation, InfiniteAlleles | FiniteAlleles):
            raise ConfigurationError(
                "model.mutation must be InfiniteAlleles or FiniteAlleles"
            )
        if not isinstance(
            self.initial, FoundingAlleles | ExplicitFrequencies | EquilibriumSplit
        ):
            raise ConfigurationError(
                "model.initial must be an initial-condition variant"
            )
        if not self.loci or not all(isinstance(item, Locus) for item in self.loci):
            raise ConfigurationError("model.loci must be a non-empty tuple of Locus")
        if len({item.id for item in self.loci}) != len(self.loci):
            raise ConfigurationError("model.loci must have unique ids")
        sizes = self.population.individuals
        if isinstance(sizes, tuple) and len(sizes) != self.demes:
            raise ConfigurationError(
                "population.individuals must have one entry per deme"
            )
        if isinstance(self.migration, tuple) and len(self.migration) != self.demes:
            raise ConfigurationError("model.migration matrix must be demes by demes")
        rate = self.mutation.rate
        if isinstance(rate, tuple) and len(rate) != len(self.loci):
            raise ConfigurationError("mutation.rate must have one entry per locus")
