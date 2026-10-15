"""The codec between the grouped specification and the engine's parameters.

`SimulationParams.from_mapping` stays the only parser of the configuration
file, so the strongest check is differential: for every shipped example the
new route must produce parameters equal to the old route, field for field and
in their serialized (run identity) form. Property tests then cover specs the
examples do not.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, reject, settings
from hypothesis import strategies as st

from fim.errors import ConfigurationError
from fim.model.params import _CONFIG_KEYS, SimulationParams
from fim.spec import (
    EquilibriumSplit,
    Execution,
    FiniteAlleles,
    FoundingAlleles,
    InfiniteAlleles,
    Locus,
    Model,
    Population,
    Recording,
    Replication,
    RunConfig,
    RunSpec,
    Stopping,
    from_params,
    load_config,
    to_params,
)
from fim.spec.codec import read_mapping
from fim.spec.fields import CONVERTED_KEYS, FIELD_MAP, PATHS

EXAMPLES = sorted(
    path
    for path in (Path(__file__).resolve().parents[2] / "doc" / "examples").iterdir()
    if (path / "config.yaml").is_file()
)


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda path: path.name)
def test_example_compiles_to_the_same_parameters_as_the_old_route(
    example: Path,
) -> None:
    """New route equals `from_mapping`: fields, run-identity form, and file."""
    mapping = read_mapping(example / "config.yaml")
    expected = SimulationParams.from_mapping(mapping)

    config = RunConfig.from_mapping(mapping)
    compiled = config.to_params()

    assert compiled == expected
    assert compiled.to_dict() == expected.to_dict()
    assert compiled.read_only == expected.read_only
    assert RunConfig.from_mapping(config.to_mapping()) == config


def test_load_config_reads_a_file() -> None:
    """`load_config` is `read_mapping` plus `RunConfig.from_mapping`."""
    path = EXAMPLES[0] / "config.yaml"
    assert load_config(path) == RunConfig.from_mapping(read_mapping(path))


def test_a_labelled_example_keeps_its_labels() -> None:
    """Name, description, and class travel with the configuration."""
    config = load_config(
        next(p for p in EXAMPLES if p.name == "dear-nolan-high") / "config.yaml"
    )
    assert config.labels.name == "Dear-Nolan high migration"
    assert config.labels.run_class == "calibration-dear-nolan"
    assert config.read_only is True
    assert config.to_mapping()["name"] == "Dear-Nolan high migration"


def test_field_map_agrees_with_the_engine_parameters_and_the_spec() -> None:
    """Every mapped key is an engine field and a real spec attribute."""
    engine_fields = {field.name for field in dataclasses.fields(SimulationParams)}
    config = RunConfig(
        spec=RunSpec(
            model=Model(
                demes=2,
                population=Population(individuals=10, ploidy=1),
                migration=0.1,
                mutation=InfiniteAlleles(rate=0.001),
            ),
            seed=1,
        )
    )
    objects = {
        "run": config.spec,
        "model": config.spec.model,
        "stopping": config.spec.stopping,
        "replication": config.spec.replication,
        "analysis": config.spec.analysis,
        "execution": config.execution,
        "recording": config.recording,
    }
    for entry in FIELD_MAP:
        assert entry.key in engine_fields, entry.key
        assert hasattr(objects[entry.group], entry.attribute), entry
    assert len({entry.key for entry in FIELD_MAP}) == len(FIELD_MAP)
    assert PATHS["convergence_burn_in"] == "stopping.burn_in"
    assert PATHS["seed"] == "seed"


def test_every_configuration_key_is_accounted_for() -> None:
    """A new key in the parser must be added to the table or the converted list."""
    mapped = {entry.key for entry in FIELD_MAP}
    accounted = mapped | set(CONVERTED_KEYS)
    assert not set(_CONFIG_KEYS) - accounted
    assert not set(CONVERTED_KEYS) & mapped


def test_an_engine_rejection_becomes_a_configuration_error() -> None:
    """Cross-field rules stay in the engine; callers still see `ConfigurationError`."""
    spec = RunSpec(
        model=Model(
            demes=2,
            population=Population(individuals=3, ploidy=1),
            migration=0.1,
            mutation=InfiniteAlleles(rate=0.001),
            initial=FoundingAlleles(count=9),
        ),
        seed=1,
    )
    with pytest.raises(ConfigurationError, match="initial_allele_count"):
        to_params(spec)


def test_a_bad_file_value_becomes_a_configuration_error() -> None:
    """The parser's own message is preserved under the new exception type."""
    with pytest.raises(ConfigurationError, match="unknown configuration key"):
        RunConfig.from_mapping(
            {"N": 10, "d": 2, "m": 0.1, "mu": 0.01, "seed": 1, "bogus": 1}
        )


def test_auto_values_resolve_and_come_back_as_numbers() -> None:
    """`"auto"` compiles to the engine's derived numbers, not to a sentinel."""
    spec = RunSpec(
        model=Model(
            demes=3,
            population=Population(individuals=50, ploidy=1),
            migration=0.05,
            mutation=InfiniteAlleles(rate=0.001),
        ),
        seed=2,
    )
    params = to_params(spec)
    assert params.convergence_burn_in > 0
    assert params.max_generations > 0
    assert "convergence_burn_in" in params.auto_derived
    recovered, _, _ = from_params(params)
    assert recovered.stopping.burn_in == params.convergence_burn_in
    assert to_params(recovered) == params


@st.composite
def _specs(draw: st.DrawFn) -> tuple[RunSpec, Execution, Recording]:
    """Draw a small, valid specification with its execution and recording."""
    demes = draw(st.integers(2, 4))
    ploidy = draw(st.sampled_from([1, 2]))
    per_deme = draw(st.booleans())
    sizes = (
        tuple(draw(st.integers(10, 40)) for _ in range(demes))
        if per_deme
        else draw(st.integers(10, 40))
    )
    loci = tuple(
        Locus(id=i + 1, length=draw(st.integers(2, 8)))
        for i in range(draw(st.integers(1, 2)))
    )
    rate = draw(st.floats(1e-5, 0.05))
    mutation: InfiniteAlleles | FiniteAlleles = (
        FiniteAlleles(rate=rate) if draw(st.booleans()) else InfiniteAlleles(rate=rate)
    )
    initial = draw(
        st.one_of(
            st.builds(
                FoundingAlleles,
                count=st.integers(1, 4),
                concentration=st.floats(0.5, 3.0),
            ),
            st.builds(
                EquilibriumSplit,
                window=st.integers(2, 20),
                tolerance=st.floats(1e-4, 1e-2),
                max_generations=st.integers(200, 2000),
            ),
        )
    )
    model = Model(
        demes=demes,
        population=Population(individuals=sizes, ploidy=ploidy),  # type: ignore[arg-type]
        migration=draw(st.floats(0.0, 0.3)),
        mutation=mutation,
        loci=loci,
        migrant_sampling=draw(st.sampled_from(["continuous", "stochastic"])),
        initial=initial,
    )
    replicates = draw(st.integers(2, 6))
    spec = RunSpec(
        model=model,
        seed=draw(st.integers(0, 10**6)),
        stopping=Stopping(
            statistics=draw(st.sampled_from([("D",), ("D", "G_ST"), ("H_S",)])),
            combinator=draw(st.sampled_from(["any", "all"])),
            precision=draw(st.floats(0.001, 0.1)),
            confidence=draw(st.sampled_from([0.90, 0.95, 0.99])),
            burn_in=draw(st.one_of(st.just("auto"), st.integers(1, 50))),
            max_generations=draw(st.one_of(st.just("auto"), st.integers(500, 5000))),
        ),
        replication=Replication(
            n=replicates,
            minimum=draw(st.integers(2, replicates)),
            stop_early=draw(st.booleans()),
        ),
    )
    execution = Execution(
        backend=draw(st.sampled_from(["lineal", "generational", "auto"]))
    )
    recording = Recording(retention=draw(st.sampled_from(["full", "thinned"])))
    return spec, execution, recording


@settings(max_examples=60, deadline=None)
@given(_specs())
def test_compiling_is_idempotent_through_the_parameters(
    drawn: tuple[RunSpec, Execution, Recording],
) -> None:
    """Read a spec back out of its parameters and compile again: no change."""
    spec, execution, recording = drawn
    try:
        params = to_params(spec, execution, recording)
    except ConfigurationError:
        reject()  # an engine rule the strategy does not model; discard the draw
    again_spec, again_execution, again_recording = from_params(params)
    assert to_params(again_spec, again_execution, again_recording) == params
    config = RunConfig(spec=spec, execution=execution, recording=recording)
    assert RunConfig.from_mapping(config.to_mapping()).to_params() == params


def test_explicit_zero_burn_in_is_not_representable() -> None:
    """Zero means auto in the engine, so the spec refuses it rather than lose it."""
    with pytest.raises(ConfigurationError, match=r"stopping\.burn_in"):
        Stopping(burn_in=0)


def test_values_flow_through_unchanged() -> None:
    """A fully explicit specification compiles to exactly the stated values."""
    spec = RunSpec(
        model=Model(
            demes=3,
            population=Population(individuals=(10, 20, 30), ploidy=2),
            migration=0.02,
            mutation=FiniteAlleles(rate=(0.001, 0.002)),
            loci=(Locus(id=1, length=3), Locus(id=2, length=4)),
            migrant_sampling="stochastic",
        ),
        seed=7,
        stopping=Stopping(
            statistics="D", burn_in=5, max_generations=400, precision=0.05
        ),
        replication=Replication(n=4, minimum=3, stop_early=False),
    )
    params = to_params(
        spec,
        Execution(backend="generational"),
        Recording(retention="thinned", stride=3),
    )
    assert params.gene_copies == (20, 40, 60)
    assert params.ploidy == 2
    assert params.mu == (0.001, 0.002)
    assert params.mutation_model == "finite_alleles"
    assert [(item.locus_id, item.length) for item in params.loci] == [(1, 3), (2, 4)]
    assert params.convergence_statistic == "D"
    assert (params.convergence_burn_in, params.max_generations) == (5, 400)
    assert (params.n_replicates, params.replicate_minimum) == (4, 3)
    assert params.engine_backend == "generational"
    assert (params.trajectory_retention, params.trajectory_stride) == ("thinned", 3)


def test_labels_are_not_part_of_the_parameters() -> None:
    """Renaming a run never changes which run it is."""
    mapping: dict[str, Any] = {
        "N": 10,
        "ploidy": "haploid",
        "d": 2,
        "m": 0.1,
        "mu": 0.01,
        "seed": 1,
    }
    plain = RunConfig.from_mapping(mapping)
    named = RunConfig.from_mapping({**mapping, "name": "x", "description": "y"})
    assert named.labels.name == "x"
    assert named.to_params().to_dict() == plain.to_params().to_dict()
