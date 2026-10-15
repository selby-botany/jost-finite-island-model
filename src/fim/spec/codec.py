"""Conversion between the grouped specification and the engine's parameters.

`SimulationParams` stays the engine's flat, validated, resolved parameters
and the single home of every cross-field rule. This module only moves
values between it and the grouped types:

- `to_params` compiles a `RunSpec` (with its `Execution` and `Recording`);
- `from_params` reads one back;
- `RunConfig` is the contents of a configuration file, and parses and
  writes it through `SimulationParams.from_mapping` and `to_dict`, so the
  file format and its quirks keep exactly one parser.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from fim.errors import ConfigurationError
from fim.model.locus import LocusSpec
from fim.model.params import AUTO_CONVERGENCE, SimulationParams
from fim.persistence.run_metadata import RunLabels
from fim.spec.execution import Execution, Recording
from fim.spec.fields import FIELD_MAP
from fim.spec.model import (
    EquilibriumSplit,
    ExplicitFrequencies,
    FiniteAlleles,
    FoundingAlleles,
    InfiniteAlleles,
    Locus,
    Model,
    Population,
)
from fim.spec.policy import Analysis, Replication, Stopping
from fim.spec.run import RunSpec


def _groups(
    spec: RunSpec, execution: Execution, recording: Recording
) -> dict[str, object]:
    """Return the objects a `FieldMap` group name refers to."""
    return {
        "run": spec,
        "model": spec.model,
        "stopping": spec.stopping,
        "replication": spec.replication,
        "analysis": spec.analysis,
        "execution": execution,
        "recording": recording,
    }


def to_params(
    spec: RunSpec,
    execution: Execution | None = None,
    recording: Recording | None = None,
    *,
    read_only: bool = False,
) -> SimulationParams:
    """Compile a specification into the engine's validated parameters.

    Args:
        spec: What to simulate.
        execution: How to run it (default: `Execution()`).
        recording: What to keep (default: `Recording()`).
        read_only: Mark the run read-only, as the shipped examples are.

    Returns:
        The resolved `SimulationParams`, with every `"auto"` derived.

    Raises:
        ConfigurationError: The engine rejected the combination; the message
            is the engine's own.
    """
    groups = _groups(spec, execution or Execution(), recording or Recording())
    fields: dict[str, Any] = {}
    for entry in FIELD_MAP:
        value = getattr(groups[entry.group], entry.attribute)
        fields[entry.key] = (
            AUTO_CONVERGENCE if entry.auto and value == "auto" else value
        )

    model = spec.model
    fields.update(
        gene_copies=model.population.gene_copies,
        ploidy=model.population.ploidy,
        d=model.demes,
        m=model.migration,
        mu=model.mutation.rate,
        mutation_model=(
            "finite_alleles"
            if isinstance(model.mutation, FiniteAlleles)
            else "infinite_alleles"
        ),
        loci=tuple(LocusSpec(item.id, item.length) for item in model.loci),
        convergence_statistic=spec.stopping.statistics,
        statistic_precision=spec.stopping.per_statistic_precision,
        read_only=read_only,
    )
    match model.initial:
        case FoundingAlleles(count=count, concentration=concentration):
            fields.update(
                initial_allele_count=count, initial_concentration=concentration
            )
        case ExplicitFrequencies(frequencies=frequencies):
            fields.update(initial_frequencies=frequencies)
        case EquilibriumSplit(
            window=window, tolerance=tolerance, max_generations=cap, founding=founding
        ):
            fields.update(
                equilibrium_convergence_window=window,
                equilibrium_convergence_tolerance=tolerance,
                equilibrium_max_generations=cap,
                initial_allele_count=founding.count,
                initial_concentration=founding.concentration,
            )
    try:
        return SimulationParams(**fields)
    except ValueError as error:
        raise ConfigurationError(str(error)) from error


def from_params(
    params: SimulationParams,
) -> tuple[RunSpec, Execution, Recording]:
    """Read a specification back out of resolved engine parameters.

    The result is canonical: a population whose demes all have the same size
    is a single number, and a derived burn-in or cap is reported as the
    number the engine derived rather than as ``"auto"``.

    Args:
        params: Validated engine parameters.

    Returns:
        The `RunSpec`, `Execution`, and `Recording` that compile to them.
    """
    values: dict[str, dict[str, Any]] = {
        name: {}
        for name in (
            "run",
            "model",
            "stopping",
            "replication",
            "analysis",
            "execution",
            "recording",
        )
    }
    for entry in FIELD_MAP:
        value = getattr(params, entry.key)
        values[entry.group][entry.attribute] = (
            "auto" if entry.auto and value == AUTO_CONVERGENCE else value
        )

    copies = params.gene_copies
    individuals: int | tuple[int, ...] = (
        copies // params.ploidy
        if isinstance(copies, int)
        else tuple(size // params.ploidy for size in copies)
    )
    mutation = (
        FiniteAlleles(rate=params.mu)
        if params.mutation_model == "finite_alleles"
        else InfiniteAlleles(rate=params.mu)
    )
    founding = FoundingAlleles(
        count=params.initial_allele_count, concentration=params.initial_concentration
    )
    initial: FoundingAlleles | ExplicitFrequencies | EquilibriumSplit = founding
    if params.initial_frequencies is not None:
        initial = ExplicitFrequencies(frequencies=params.initial_frequencies)
    elif params.equilibrium_convergence_window is not None:
        assert params.equilibrium_convergence_tolerance is not None
        assert params.equilibrium_max_generations is not None
        initial = EquilibriumSplit(
            window=params.equilibrium_convergence_window,
            tolerance=params.equilibrium_convergence_tolerance,
            max_generations=params.equilibrium_max_generations,
            founding=founding,
        )
    statistics = params.convergence_statistics
    spec = RunSpec(
        model=Model(
            demes=params.d,
            population=Population(individuals=individuals, ploidy=params.ploidy),  # type: ignore[arg-type]
            migration=params.m,
            mutation=mutation,
            loci=tuple(
                Locus(id=item.locus_id, length=item.length) for item in params.loci
            ),
            initial=initial,
            **values["model"],
        ),
        stopping=Stopping(
            statistics=statistics,
            per_statistic_precision=params.statistic_precision,
            **values["stopping"],
        ),
        replication=Replication(**values["replication"]),
        analysis=Analysis(**values["analysis"]),
        **values["run"],
    )
    return spec, Execution(**values["execution"]), Recording(**values["recording"])


def read_mapping(path: Path | str) -> Mapping[str, Any]:
    """Read a YAML configuration file into its raw mapping.

    Args:
        path: The file to read.

    Returns:
        The parsed top-level mapping, not yet validated.

    Raises:
        ConfigurationError: The root of the document is not a mapping.
    """
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, Mapping):
        raise ConfigurationError("configuration root must be a mapping")
    return payload


@dataclass(frozen=True, slots=True, kw_only=True)
class RunConfig:
    """The contents of a configuration file, as grouped objects.

    Args:
        spec: What to simulate.
        execution: How to run it.
        recording: What to keep.
        labels: The run's name, description, and class.
        read_only: Whether the run is a shipped, read-only example.
    """

    spec: RunSpec
    execution: Execution = field(default_factory=Execution)
    recording: Recording = field(default_factory=Recording)
    labels: RunLabels = field(default_factory=RunLabels)
    read_only: bool = False

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> RunConfig:
        """Build a configuration from a parsed configuration mapping.

        Args:
            config: The parsed file, with the flat configuration keys.

        Returns:
            The grouped configuration.

        Raises:
            ConfigurationError: A value is invalid; the message is the
                parser's own.
        """
        try:
            params = SimulationParams.from_mapping(config)
            labels = RunLabels.from_config(config)
        except ValueError as error:
            raise ConfigurationError(str(error)) from error
        return cls.from_params(params, labels=labels)

    @classmethod
    def from_params(
        cls, params: SimulationParams, *, labels: RunLabels | None = None
    ) -> RunConfig:
        """Build a configuration from resolved engine parameters.

        Args:
            params: Validated engine parameters.
            labels: Optional labels to carry.

        Returns:
            The grouped configuration.
        """
        spec, execution, recording = from_params(params)
        return cls(
            spec=spec,
            execution=execution,
            recording=recording,
            labels=labels or RunLabels(),
            read_only=params.read_only,
        )

    def to_params(self) -> SimulationParams:
        """Compile to the engine's validated parameters."""
        return to_params(
            self.spec, self.execution, self.recording, read_only=self.read_only
        )

    def to_mapping(self) -> dict[str, object]:
        """Return the flat configuration mapping that `from_mapping` accepts.

        Labels are included when set, so the file round-trips.
        """
        mapping = self.to_params().to_dict()
        if self.labels.name is not None:
            mapping["name"] = self.labels.name
        if self.labels.description is not None:
            mapping["description"] = self.labels.description
        if self.labels.run_class is not None:
            mapping["class"] = self.labels.run_class
        return mapping


def load_config(path: Path | str) -> RunConfig:
    """Read and validate a YAML configuration file.

    Args:
        path: The file to read.

    Returns:
        The grouped configuration.

    Raises:
        ConfigurationError: The file is not a valid configuration.
    """
    return RunConfig.from_mapping(read_mapping(path))
