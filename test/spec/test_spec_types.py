"""The grouped run specification: defaults, validation, and value semantics.

The spec types are a new front for the engine's `SimulationParams`, so the
first guard is that every default means the same thing in both. The rest
checks what makes the types safe to pass around: they are immutable, picklable
(process pools need that), compared by value, and report a bad value by its
field path.
"""

from __future__ import annotations

import dataclasses
import pickle
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from fim.config.expert import ExpertSettings
from fim.errors import ConfigurationError, FimError
from fim.model.params import AUTO_CONVERGENCE, SimulationParams
from fim.spec import (
    Analysis,
    EquilibriumSplit,
    Execution,
    ExplicitFrequencies,
    FiniteAlleles,
    FoundingAlleles,
    InfiniteAlleles,
    Locus,
    Model,
    Population,
    Recording,
    Replication,
    RunSpec,
    Stopping,
)


def _model(**overrides: Any) -> Model:
    """Return the Dear-Nolan low-migration shape, with fields replaced."""
    fields: dict[str, Any] = {
        "demes": 5,
        "population": Population(individuals=100, ploidy=1),
        "migration": 1e-4,
        "mutation": InfiniteAlleles(rate=1e-6),
    }
    fields.update(overrides)
    return Model(**fields)


def _spec(**overrides: Any) -> RunSpec:
    """Return a minimal valid specification, with fields replaced."""
    fields: dict[str, Any] = {"model": _model(), "seed": 1}
    fields.update(overrides)
    return RunSpec(**fields)


def _defaults(params_field: str) -> object:
    """Return the engine's own default for a `SimulationParams` field."""
    for field in dataclasses.fields(SimulationParams):
        if field.name == params_field:
            return field.default
    raise AssertionError(params_field)


# (spec type, spec attribute, SimulationParams field, auto-encoded?)
DEFAULT_PAIRS = [
    (Stopping, "combinator", "convergence_combinator", False),
    (Stopping, "estimate", "convergence_estimate", False),
    (Stopping, "burn_in", "convergence_burn_in", True),
    (Stopping, "max_generations", "max_generations", True),
    (Stopping, "precision", "precision", False),
    (Stopping, "confidence", "confidence", False),
    (Stopping, "statistics", "convergence_statistic", False),
    (Replication, "n", "n_replicates", False),
    (Replication, "minimum", "replicate_minimum", False),
    (Replication, "stop_early", "stop_batch_early", False),
    (Replication, "method", "precision_method", False),
    (Replication, "averaging_window", "replicate_averaging_window", True),
    (Analysis, "deme_weighting", "deme_weighting", False),
    (Analysis, "locus_aggregation", "locus_aggregation", False),
    (Analysis, "track_expensive", "track_expensive_statistics", False),
    (Execution, "backend", "engine_backend", False),
    (Execution, "jit", "jit", False),
    (Execution, "auto_vector_min_d", "auto_vector_min_d", False),
    (Execution, "auto_vector_max_capacity", "auto_vector_max_capacity", False),
    (Execution, "max_concurrent_replicates", "max_concurrent_replicates", False),
    (Recording, "retention", "trajectory_retention", False),
    (Recording, "stride", "trajectory_stride", False),
    (Recording, "thinning_start", "trajectory_thinning_start", True),
]


@pytest.mark.parametrize(("kind", "attribute", "params_field", "auto"), DEFAULT_PAIRS)
def test_defaults_match_the_engine_parameters(
    kind: type, attribute: str, params_field: str, auto: bool
) -> None:
    """A default in the new types must mean what it means in `SimulationParams`."""
    spec_default = getattr(kind(), attribute)
    engine_default = _defaults(params_field)
    if auto:
        assert spec_default == "auto"
        assert engine_default == AUTO_CONVERGENCE
    else:
        assert spec_default == engine_default


def test_model_defaults_match_the_engine_parameters() -> None:
    """The founding-allele and sampling defaults agree with the engine's."""
    founding = FoundingAlleles()
    assert founding.count == _defaults("initial_allele_count")
    assert founding.concentration == _defaults("initial_concentration")
    assert _model().migrant_sampling == _defaults("migrant_sampling")


def test_expert_default_is_the_engine_default() -> None:
    """A spec built without expert settings uses the engine's own defaults."""
    assert _spec().expert == ExpertSettings()


def test_specification_types_are_immutable_and_keyword_only() -> None:
    """Fields are named at the call site and cannot be reassigned."""
    spec = _spec()
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.seed = 2  # type: ignore[misc]
    with pytest.raises(TypeError):
        Population(100, 1)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        Locus(1, 200)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "value",
    [
        Population(individuals=(10, 20), ploidy=2),
        Locus(id=3, length=50),
        InfiniteAlleles(rate=(1e-6, 2e-6)),
        FiniteAlleles(rate=1e-3),
        FoundingAlleles(count=4, concentration=2.0),
        EquilibriumSplit(window=10, tolerance=1e-3, max_generations=100),
        Stopping(statistics="D", per_statistic_precision={"D": 0.02}),
        Replication(n=5, averaging_window=30),
        Analysis(track_expensive=True),
        Execution(backend="auto", workers=2),
        Recording(directory="out", retention="thinned", stride=5),
        _model(),
        _spec(),
    ],
)
def test_every_type_pickles_and_compares_by_value(value: object) -> None:
    """Process pools need picklable specs; equal specs must compare equal."""
    clone = pickle.loads(pickle.dumps(value))
    assert clone == value
    assert type(clone) is type(value)


def test_specification_without_explicit_frequencies_is_hashable() -> None:
    """Equal specifications hash alike, so they can key a dictionary."""
    assert hash(_spec()) == hash(_spec())
    assert {_spec(): 1}[_spec()] == 1


def test_sequences_are_normalized_to_tuples() -> None:
    """Lists given for sizes, rates, loci and statistics become tuples."""
    population = Population(individuals=[10, 20], ploidy=1)  # type: ignore[arg-type]
    assert population.individuals == (10, 20)
    assert InfiniteAlleles(rate=[1e-3, 2e-3]).rate == (1e-3, 2e-3)  # type: ignore[arg-type]
    model = _model(
        loci=[Locus(id=1), Locus(id=2)],
        migration=[[0.5, 0.5], [0.5, 0.5]],
        demes=2,
        population=Population(individuals=10, ploidy=1),
    )
    assert isinstance(model.loci, tuple)
    assert model.migration == ((0.5, 0.5), (0.5, 0.5))
    assert Stopping(statistics="D").statistics == ("D",)
    assert Stopping(per_statistic_precision={"D": 1}).per_statistic_precision == (
        ("D", 1.0),
    )


def test_gene_copies_multiply_individuals_by_ploidy() -> None:
    """Ploidy turns individuals into the gene copies the engine simulates."""
    assert Population(individuals=100, ploidy=2).gene_copies == 200
    assert Population(individuals=(10, 20), ploidy=3).gene_copies == (30, 60)


def test_recording_directory_becomes_a_path() -> None:
    """A string directory is accepted and normalized."""
    assert Recording(directory="results/x").directory == Path("results/x")


@pytest.mark.parametrize(
    ("build", "path"),
    [
        (lambda: Population(individuals=0, ploidy=1), "population.individuals"),
        (lambda: Population(individuals=10, ploidy=5), "population.ploidy"),  # type: ignore[arg-type]
        (lambda: Population(individuals=(), ploidy=1), "population.individuals"),
        (lambda: Locus(id=0), "locus.id"),
        (lambda: Locus(id=1, length=0), "locus.length"),
        (lambda: InfiniteAlleles(rate=1.5), "mutation.rate"),
        (lambda: InfiniteAlleles(rate=True), "mutation.rate"),
        (lambda: FiniteAlleles(rate=()), "mutation.rate"),
        (lambda: FoundingAlleles(count=0), "initial.count"),
        (lambda: FoundingAlleles(concentration=0.0), "initial.concentration"),
        (lambda: ExplicitFrequencies(frequencies=()), "initial.frequencies"),
        (
            lambda: EquilibriumSplit(window=1, tolerance=1e-3, max_generations=10),
            "initial.window",
        ),
        (
            lambda: EquilibriumSplit(window=20, tolerance=1e-3, max_generations=10),
            "initial.window",
        ),
        (lambda: Stopping(statistics=()), "stopping.statistics"),
        (lambda: Stopping(combinator="some"), "stopping.combinator"),  # type: ignore[arg-type]
        (lambda: Stopping(burn_in="soon"), "stopping.burn_in"),  # type: ignore[arg-type]
        (lambda: Stopping(max_generations=0), "stopping.max_generations"),
        (lambda: Stopping(precision=0.0), "stopping.precision"),
        (lambda: Stopping(per_statistic_precision={"D": -1.0}), "stopping.per_"),
        (lambda: Replication(n=0), "replication.n"),
        (lambda: Replication(stop_early=1), "replication.stop_early"),  # type: ignore[arg-type]
        (lambda: Replication(method="guess"), "replication.method"),  # type: ignore[arg-type]
        (lambda: Analysis(deme_weighting="rank"), "analysis.deme_weighting"),  # type: ignore[arg-type]
        (lambda: Execution(backend="fast"), "execution.backend"),  # type: ignore[arg-type]
        (lambda: Execution(workers=0), "execution.workers"),
        (lambda: Recording(stride=0), "recording.stride"),
        (lambda: Recording(retention="some"), "recording.retention"),  # type: ignore[arg-type]
        (lambda: _model(demes=1), "model.demes"),
        (lambda: _model(migration=1.5), "model.migration"),
        (lambda: _model(migration=[[0.5, 0.5], [0.5]]), "model.migration"),
        (lambda: _model(loci=()), "model.loci"),
        (lambda: _model(loci=(Locus(id=1), Locus(id=1))), "model.loci"),
        (lambda: _model(migrant_sampling="random"), "model.migrant_sampling"),
        (
            lambda: _model(population=Population(individuals=(1, 2), ploidy=1)),
            "population.individuals",
        ),
        (lambda: _model(mutation=InfiniteAlleles(rate=(1e-3, 2e-3))), "mutation.rate"),
        (lambda: _spec(seed=-1), "seed"),
        (lambda: _spec(stopping="D"), "stopping"),
    ],
)
def test_a_bad_value_names_its_field_path(
    build: Callable[[], object], path: str
) -> None:
    """Every rejection is a `ConfigurationError` that says which field."""
    with pytest.raises(ConfigurationError, match=path.replace("[", r"\[")):
        build()


def test_configuration_error_is_a_value_error_and_a_fim_error() -> None:
    """Callers may catch it as `ValueError`, or all package errors as `FimError`."""
    with pytest.raises(ValueError):
        Locus(id=0)
    with pytest.raises(FimError):
        Locus(id=0)
