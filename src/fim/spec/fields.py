"""The table that ties configuration keys to the grouped specification.

Most configuration keys are one value that lives in one attribute of one
group (`convergence_burn_in` is `RunSpec.stopping.burn_in`). Those are listed
here once, and both directions of the codec read the same rows, so adding a
setting is a one-row change. Keys that need real conversion (the population
size and ploidy, the mutation rate and model, the loci, the starting
condition, and the statistic names) are written out in `codec.py`.

The same table answers "where does this key live?" for anything that needs
to address a setting by name, such as a parameter sweep.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FieldMap:
    """One configuration key and where its value lives.

    Args:
        key: The configuration key, which is also the `SimulationParams`
            field of the same name.
        group: The object that holds it: ``"run"`` (the `RunSpec` itself),
            ``"model"``, ``"stopping"``, ``"replication"``, ``"analysis"``,
            ``"execution"``, or ``"recording"``.
        attribute: The attribute on that object.
        auto: Whether the engine stores the string ``"auto"`` as the
            integer 0 (`AUTO_CONVERGENCE`).
    """

    key: str
    group: str
    attribute: str
    auto: bool = False


FIELD_MAP: tuple[FieldMap, ...] = (
    FieldMap("seed", "run", "seed"),
    FieldMap("expert", "run", "expert"),
    FieldMap("migrant_sampling", "model", "migrant_sampling"),
    FieldMap("convergence_combinator", "stopping", "combinator"),
    FieldMap("convergence_estimate", "stopping", "estimate"),
    FieldMap("convergence_burn_in", "stopping", "burn_in", auto=True),
    FieldMap("max_generations", "stopping", "max_generations", auto=True),
    FieldMap("precision", "stopping", "precision"),
    FieldMap("confidence", "stopping", "confidence"),
    FieldMap("n_replicates", "replication", "n"),
    FieldMap("replicate_minimum", "replication", "minimum"),
    FieldMap("stop_batch_early", "replication", "stop_early"),
    FieldMap("precision_method", "replication", "method"),
    FieldMap(
        "replicate_averaging_window", "replication", "averaging_window", auto=True
    ),
    FieldMap("max_concurrent_replicates", "replication", "max_concurrent"),
    FieldMap("deme_weighting", "analysis", "deme_weighting"),
    FieldMap("locus_aggregation", "analysis", "locus_aggregation"),
    FieldMap("track_expensive_statistics", "analysis", "track_expensive"),
    FieldMap("engine_backend", "execution", "backend"),
    FieldMap("jit", "execution", "jit"),
    FieldMap("auto_vector_min_d", "execution", "auto_vector_min_d"),
    FieldMap("auto_vector_max_capacity", "execution", "auto_vector_max_capacity"),
    FieldMap("trajectory_retention", "recording", "retention"),
    FieldMap("trajectory_stride", "recording", "stride"),
    FieldMap("trajectory_thinning_start", "recording", "thinning_start", auto=True),
)
"""Every key that maps one-to-one onto a specification attribute."""

CONVERTED_KEYS: tuple[str, ...] = (
    "N",
    "ploidy",
    "d",
    "m",
    "mu",
    "mu_b",
    "mutation_model",
    "loci",
    "n_loci",
    "locus_lengths",
    "initial_allele_count",
    "initial_concentration",
    "p_0",
    "equilibrium_convergence_window",
    "equilibrium_convergence_tolerance",
    "equilibrium_max_generations",
    "convergence_statistic",
    "statistic_precision",
)
"""Keys the codec converts by hand rather than through `FIELD_MAP`."""

PATHS: dict[str, str] = {
    entry.key: f"{entry.group}.{entry.attribute}"
    if entry.group != "run"
    else entry.attribute
    for entry in FIELD_MAP
}
"""Configuration key to its dotted attribute path, for the mapped keys."""
