"""Validated, replayable simulation configuration."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final, Literal, cast

from fim.model.allele import AlleleId
from fim.model.locus import LocusSpec, finite_allele_capacity
from fim.model.topology import (
    Topology,
    dense_matrix_from_neighbors,
    stepping_stone_neighbors,
)

PopulationSize = int | tuple[int, ...]
Migration = float | tuple[tuple[float, ...], ...]
DemeWeighting = Literal["equal", "size"]
ConvergenceStatistic = str | tuple[str, ...]
ConvergenceCombinator = Literal["any", "all"]
MigrantSampling = Literal["continuous", "stochastic"]
MutationModel = Literal["infinite_alleles", "finite_alleles"]
InitialFrequencies = tuple[tuple[Mapping[AlleleId, float], ...], ...]

DEFAULT_LOCUS_LENGTH: Final = 200
PARAMETER_DEFAULTS: Final[dict[str, object]] = {
    "n_loci": 1,
    "locus_lengths": DEFAULT_LOCUS_LENGTH,
    "initial_allele_count": 2,
    "initial_concentration": 1.0,
    "deme_weighting": "size",
    "convergence_statistic": "D",
    "convergence_combinator": "all",
    "convergence_window": 50,
    "convergence_tolerance": 0.01,
    "max_generations": 10_000,
    "n_replicates": 1,
    "migrant_sampling": "continuous",
    "mutation_model": "infinite_alleles",
}

_CONFIG_KEYS: Final = frozenset(
    {
        "N",
        "d",
        "m",
        "mu",
        "seed",
        "loci",
        "n_loci",
        "locus_lengths",
        "initial_allele_count",
        "initial_concentration",
        "deme_weighting",
        "convergence_statistic",
        "convergence_combinator",
        "convergence_window",
        "convergence_tolerance",
        "max_generations",
        "n_replicates",
        "migrant_sampling",
        "mutation_model",
        "p_0",
    }
)

_CONVERGENCE_STATISTICS: Final = frozenset({"D", "G_ST", "E_ST", "K_ST", "H_S", "H_T"})


@dataclass(frozen=True, slots=True)
class SimulationParams:
    """Store all values needed to reproduce a finite-island-model run.

    Args:
        N: Gene-copy count shared by all demes, or one count per deme.
        m: Symmetric migration rate, or a row-stochastic migration matrix.
        mu: Per-copy mutation probability per generation.
        d: Number of demes.
        seed: Required PCG64 seed.
        loci: Nonempty ordered locus descriptions.
        initial_allele_count: Founding allele count per locus.
        initial_concentration: Symmetric Dirichlet concentration.
        deme_weighting: Weighting used by statistics that support it.
        convergence_statistic: One statistic, or several, watched by the
            convergence monitor.
        convergence_combinator: How several watched statistics combine —
            "all" (every one stable) or "any" (at least one stable).
            A single statistic makes this a no-op special case.
        convergence_window: Trailing stability-window length.
        convergence_tolerance: Maximum half-window mean difference.
        max_generations: Hard generation safety cap.
        n_replicates: Number of independently seeded runs.
        migrant_sampling: How many gene copies migrate each generation —
            "continuous" (default), the exact ``rate * N`` fraction used by
            every prior release, or the opt-in "stochastic", which draws a
            ``Binomial(N, rate)`` migrant count instead. Migrant
            composition is unaffected either way; see
            ``fim.model.operators.migrate``.
        mutation_model: How a mutation event picks its target — either
            "infinite_alleles" (default), where every mutation is
            globally novel, or "finite_alleles", where each locus has a
            bounded state space (`fim.model.locus.finite_allele_capacity`)
            and a mutation can recur to a state already present elsewhere
            in the run. See `fim.model.allele.FiniteAlleleSpace`.
        initial_frequencies: Optional explicit deme/locus frequency table.
    """

    N: PopulationSize
    m: Migration
    mu: float
    d: int
    seed: int
    loci: tuple[LocusSpec, ...] = field(
        default_factory=lambda: (LocusSpec(1, DEFAULT_LOCUS_LENGTH),)
    )
    initial_allele_count: int = 2
    initial_concentration: float = 1.0
    deme_weighting: DemeWeighting = "size"
    convergence_statistic: ConvergenceStatistic = "D"
    convergence_combinator: ConvergenceCombinator = "all"
    convergence_window: int = 50
    convergence_tolerance: float = 0.01
    max_generations: int = 10_000
    n_replicates: int = 1
    migrant_sampling: MigrantSampling = "continuous"
    mutation_model: MutationModel = "infinite_alleles"
    initial_frequencies: InitialFrequencies | None = None

    def __post_init__(self) -> None:
        """Normalize sequence inputs and validate every parameter."""
        _require_integer("d", self.d, minimum=2)
        _require_integer("seed", self.seed)
        _require_probability("mu", self.mu)

        population_sizes = _normalize_population_sizes(self.N, self.d)
        migration = _normalize_migration(self.m, self.d)
        loci = tuple(self.loci)
        if not loci:
            raise ValueError("loci must not be empty")
        if len({locus.locus_id for locus in loci}) != len(loci):
            raise ValueError("locus IDs must be unique")

        _require_integer(
            "initial_allele_count",
            self.initial_allele_count,
            minimum=1,
        )
        if self.initial_allele_count > min(population_sizes):
            raise ValueError("initial_allele_count cannot exceed the smallest deme N")
        if (
            not math.isfinite(self.initial_concentration)
            or self.initial_concentration <= 0.0
        ):
            raise ValueError("initial_concentration must be greater than 0")
        if self.deme_weighting not in {"equal", "size"}:
            raise ValueError("deme_weighting must be 'equal' or 'size'")
        convergence_statistics = _normalize_convergence_statistic(
            self.convergence_statistic
        )
        if self.convergence_combinator not in {"any", "all"}:
            raise ValueError("convergence_combinator must be 'any' or 'all'")
        _require_integer(
            "convergence_window",
            self.convergence_window,
            minimum=2,
        )
        if (
            not math.isfinite(self.convergence_tolerance)
            or self.convergence_tolerance < 0.0
        ):
            raise ValueError("convergence_tolerance must be non-negative")
        _require_integer(
            "max_generations",
            self.max_generations,
            minimum=1,
        )
        _require_integer("n_replicates", self.n_replicates, minimum=1)
        if self.migrant_sampling not in {"continuous", "stochastic"}:
            raise ValueError("migrant_sampling must be 'continuous' or 'stochastic'")
        if self.mutation_model not in {"infinite_alleles", "finite_alleles"}:
            raise ValueError(
                "mutation_model must be 'infinite_alleles' or 'finite_alleles'"
            )

        initial_frequencies = _normalize_initial_frequencies(
            self.initial_frequencies,
            d=self.d,
            loci=loci,
            population_sizes=population_sizes,
        )
        if self.mutation_model == "finite_alleles":
            _validate_finite_allele_capacity(
                loci, self.initial_allele_count, initial_frequencies
            )

        object.__setattr__(
            self,
            "N",
            population_sizes[0]
            if len(set(population_sizes)) == 1
            else population_sizes,
        )
        object.__setattr__(self, "m", migration)
        object.__setattr__(self, "mu", float(self.mu))
        object.__setattr__(self, "loci", loci)
        object.__setattr__(self, "initial_frequencies", initial_frequencies)
        object.__setattr__(
            self,
            "convergence_statistic",
            convergence_statistics[0]
            if len(convergence_statistics) == 1
            else convergence_statistics,
        )

    @property
    def convergence_statistics(self) -> tuple[str, ...]:
        """Return every statistic watched by the convergence monitor."""
        if isinstance(self.convergence_statistic, str):
            return (self.convergence_statistic,)
        return self.convergence_statistic

    @property
    def population_sizes(self) -> tuple[int, ...]:
        """Return one gene-copy count per deme."""
        if isinstance(self.N, int):
            return (self.N,) * self.d
        return self.N

    def to_dict(self) -> dict[str, object]:
        """Return a JSON/YAML-serializable, lossless configuration mapping."""
        serialized_n: int | list[int] = (
            self.N if isinstance(self.N, int) else list(self.N)
        )

        serialized_m: float | list[list[float]]
        if isinstance(self.m, float):
            serialized_m = self.m
        else:
            serialized_m = [list(row) for row in self.m]

        result: dict[str, object] = {
            "N": serialized_n,
            "d": self.d,
            "m": serialized_m,
            "mu": self.mu,
            "seed": self.seed,
            "loci": [
                {"locus_id": locus.locus_id, "length": locus.length}
                for locus in self.loci
            ],
            "initial_allele_count": self.initial_allele_count,
            "initial_concentration": self.initial_concentration,
            "deme_weighting": self.deme_weighting,
            "convergence_statistic": (
                self.convergence_statistic
                if isinstance(self.convergence_statistic, str)
                else list(self.convergence_statistic)
            ),
            "convergence_combinator": self.convergence_combinator,
            "convergence_window": self.convergence_window,
            "convergence_tolerance": self.convergence_tolerance,
            "max_generations": self.max_generations,
            "n_replicates": self.n_replicates,
            "migrant_sampling": self.migrant_sampling,
            "mutation_model": self.mutation_model,
        }
        if self.initial_frequencies is not None:
            result["p_0"] = [
                [
                    {str(int(allele)): frequency for allele, frequency in locus.items()}
                    for locus in deme
                ]
                for deme in self.initial_frequencies
            ]
        return result

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> SimulationParams:
        """Validate a config-file mapping and construct simulation parameters.

        Args:
            config: Parsed YAML or JSON object.

        Returns:
            A validated immutable parameter object.

        Raises:
            ValueError: If a key is unknown, required, malformed, or conflicting.
        """
        unknown = set(config) - _CONFIG_KEYS
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(f"unknown configuration key(s): {names}")
        missing = {"N", "d", "m", "mu", "seed"} - set(config)
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"missing required configuration key(s): {names}")

        loci = _loci_from_config(config)
        d = _parse_int("d", config["d"])
        return cls(
            N=_parse_population_size(config["N"]),
            d=d,
            m=_parse_migration(config["m"], d),
            mu=_parse_float("mu", config["mu"]),
            seed=_parse_int("seed", config["seed"]),
            loci=loci,
            initial_allele_count=_parse_int(
                "initial_allele_count",
                config.get(
                    "initial_allele_count",
                    PARAMETER_DEFAULTS["initial_allele_count"],
                ),
            ),
            initial_concentration=_parse_float(
                "initial_concentration",
                config.get(
                    "initial_concentration",
                    PARAMETER_DEFAULTS["initial_concentration"],
                ),
            ),
            deme_weighting=_parse_deme_weighting(
                config.get(
                    "deme_weighting",
                    PARAMETER_DEFAULTS["deme_weighting"],
                )
            ),
            convergence_statistic=_parse_convergence_statistic(
                config.get(
                    "convergence_statistic",
                    PARAMETER_DEFAULTS["convergence_statistic"],
                ),
            ),
            convergence_combinator=_parse_convergence_combinator(
                config.get(
                    "convergence_combinator",
                    PARAMETER_DEFAULTS["convergence_combinator"],
                ),
            ),
            convergence_window=_parse_int(
                "convergence_window",
                config.get(
                    "convergence_window",
                    PARAMETER_DEFAULTS["convergence_window"],
                ),
            ),
            convergence_tolerance=_parse_float(
                "convergence_tolerance",
                config.get(
                    "convergence_tolerance",
                    PARAMETER_DEFAULTS["convergence_tolerance"],
                ),
            ),
            max_generations=_parse_int(
                "max_generations",
                config.get(
                    "max_generations",
                    PARAMETER_DEFAULTS["max_generations"],
                ),
            ),
            n_replicates=_parse_int(
                "n_replicates",
                config.get(
                    "n_replicates",
                    PARAMETER_DEFAULTS["n_replicates"],
                ),
            ),
            migrant_sampling=_parse_migrant_sampling(
                config.get(
                    "migrant_sampling",
                    PARAMETER_DEFAULTS["migrant_sampling"],
                ),
            ),
            mutation_model=_parse_mutation_model(
                config.get(
                    "mutation_model",
                    PARAMETER_DEFAULTS["mutation_model"],
                ),
            ),
            initial_frequencies=_parse_initial_frequencies(config.get("p_0")),
        )


def _loci_from_config(config: Mapping[str, Any]) -> tuple[LocusSpec, ...]:
    """Build locus specifications from either supported config shape."""
    if "loci" in config:
        conflicts = {"n_loci", "locus_lengths"} & set(config)
        if conflicts:
            names = ", ".join(sorted(conflicts))
            raise ValueError(f"loci cannot be combined with {names}")
        raw_loci = config["loci"]
        if not isinstance(raw_loci, Sequence) or isinstance(raw_loci, str):
            raise ValueError("loci must be a list of mappings")
        loci: list[LocusSpec] = []
        for index, raw_locus in enumerate(raw_loci, start=1):
            if not isinstance(raw_locus, Mapping):
                raise ValueError(f"loci[{index - 1}] must be a mapping")
            unknown = set(raw_locus) - {"locus_id", "length"}
            if unknown:
                names = ", ".join(sorted(str(key) for key in unknown))
                raise ValueError(f"unknown loci[{index - 1}] key(s): {names}")
            if "length" not in raw_locus:
                raise ValueError(f"loci[{index - 1}] is missing 'length'")
            locus_id = _parse_int(
                f"loci[{index - 1}].locus_id",
                raw_locus.get("locus_id", index),
            )
            length = _parse_int(
                f"loci[{index - 1}].length",
                raw_locus["length"],
            )
            loci.append(LocusSpec(locus_id, length))
        return tuple(loci)

    n_loci = _parse_int(
        "n_loci",
        config.get("n_loci", PARAMETER_DEFAULTS["n_loci"]),
    )
    lengths_value = config.get(
        "locus_lengths",
        PARAMETER_DEFAULTS["locus_lengths"],
    )
    if isinstance(lengths_value, Sequence) and not isinstance(lengths_value, str):
        lengths = tuple(
            _parse_int(f"locus_lengths[{index}]", value)
            for index, value in enumerate(lengths_value)
        )
        if len(lengths) != n_loci:
            raise ValueError("locus_lengths must contain exactly n_loci values")
    else:
        length = _parse_int("locus_lengths", lengths_value)
        lengths = (length,) * n_loci
    return tuple(
        LocusSpec(index, length) for index, length in enumerate(lengths, start=1)
    )


def _migration_from_sparse_map(value: Mapping[Any, Any], d: int) -> Migration:
    """Parse a one-based sparse neighbor map into a full matrix.

    Config shape: ``{deme: {neighbor: weight, ...}, ...}``, one-based, with
    every deme's self-retention left implicit as the complement of its
    listed weights — the same convention ``stepping_stone_neighbors``
    already returns. A deme absent from the map migrates with nobody.
    """
    parsed: dict[int, dict[int, float]] = {}
    for raw_deme, raw_row in value.items():
        deme = _parse_deme_key("m", raw_deme, d)
        if not isinstance(raw_row, Mapping):
            raise ValueError(f"m[{raw_deme}] must be a mapping of neighbor to weight")
        row: dict[int, float] = {}
        for raw_neighbor, raw_weight in raw_row.items():
            neighbor = _parse_deme_key(f"m[{raw_deme}]", raw_neighbor, d)
            row[neighbor] = _parse_float(f"m[{raw_deme}][{raw_neighbor}]", raw_weight)
        parsed[deme] = row
    return dense_matrix_from_neighbors(parsed, d)


def _migration_from_topology(value: Mapping[str, Any], d: int) -> Migration:
    """Expand a compact ``{topology, rate}`` mapping into a full matrix."""
    unknown = set(value) - {"topology", "rate"}
    if unknown:
        names = ", ".join(sorted(str(key) for key in unknown))
        raise ValueError(f"unknown m topology key(s): {names}")
    missing = {"topology", "rate"} - set(value)
    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(f"m topology mapping is missing {names}")
    topology = _parse_string("m.topology", value["topology"])
    rate = _parse_float("m.rate", value["rate"])
    if topology not in {"ring", "linear"}:
        raise ValueError("m.topology must be 'ring' or 'linear'")
    validated_topology = cast(Topology, topology)
    neighbors = stepping_stone_neighbors(d, topology=validated_topology, rate=rate)
    return dense_matrix_from_neighbors(neighbors, d)


def _normalize_convergence_statistic(
    value: ConvergenceStatistic,
) -> tuple[str, ...]:
    """Validate one or several convergence statistics and return them as a tuple.

    A list is accepted for the "several statistics needed to agree before
    stopping" extension (design §9): every name must be a recognized
    statistic, and no name may repeat. A single statistic — the default —
    is the resulting tuple's one-element case; nothing downstream needs to
    special-case it.
    """
    candidates: tuple[str, ...] = (value,) if isinstance(value, str) else tuple(value)
    if not candidates:
        raise ValueError("convergence_statistic must not be empty")
    for statistic in candidates:
        if statistic not in _CONVERGENCE_STATISTICS:
            allowed = ", ".join(sorted(_CONVERGENCE_STATISTICS))
            raise ValueError(f"convergence_statistic must be one of: {allowed}")
    if len(set(candidates)) != len(candidates):
        raise ValueError("convergence_statistic must not repeat a statistic")
    return candidates


def _normalize_initial_frequencies(
    value: InitialFrequencies | None,
    *,
    d: int,
    loci: tuple[LocusSpec, ...],
    population_sizes: tuple[int, ...],
) -> InitialFrequencies | None:
    """Validate and make explicit initial frequencies immutable."""
    if value is None:
        return None
    if len(value) != d:
        raise ValueError("p_0 must contain exactly d demes")
    normalized_demes: list[tuple[Mapping[AlleleId, float], ...]] = []
    for deme_index, (deme, size) in enumerate(
        zip(value, population_sizes, strict=True),
        start=1,
    ):
        if len(deme) != len(loci):
            raise ValueError(
                f"p_0 deme {deme_index} must contain exactly {len(loci)} loci"
            )
        normalized_loci: list[Mapping[AlleleId, float]] = []
        for locus, frequency_map in zip(loci, deme, strict=True):
            if len(frequency_map) > size:
                raise ValueError(
                    f"p_0 deme {deme_index}, locus {locus.locus_id} support "
                    f"exceeds N={size}"
                )
            normalized: dict[AlleleId, float] = {}
            for allele_id, frequency in frequency_map.items():
                numeric_frequency = float(frequency)
                if not math.isfinite(numeric_frequency) or numeric_frequency < 0.0:
                    raise ValueError("p_0 frequencies must be finite and non-negative")
                if numeric_frequency > 0.0:
                    normalized[AlleleId(int(allele_id))] = numeric_frequency
            if not normalized or not math.isclose(
                math.fsum(normalized.values()),
                1.0,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise ValueError(
                    f"p_0 deme {deme_index}, locus {locus.locus_id} "
                    "frequencies must sum to 1"
                )
            normalized_loci.append(MappingProxyType(normalized))
        normalized_demes.append(tuple(normalized_loci))
    return tuple(normalized_demes)


def _normalize_migration(value: Migration, d: int) -> Migration:
    """Validate scalar or matrix migration and normalize numeric values."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        _require_probability("m", float(value))
        return float(value)
    rows = tuple(tuple(float(item) for item in row) for row in value)
    if len(rows) != d or any(len(row) != d for row in rows):
        raise ValueError("migration matrix m must have shape d x d")
    for index, row in enumerate(rows):
        for item in row:
            _require_probability(f"m[{index}]", item)
        if not math.isclose(
            math.fsum(row),
            1.0,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError(f"migration matrix row {index} must sum to 1")
    return rows


def _normalize_population_sizes(
    value: PopulationSize,
    d: int,
) -> tuple[int, ...]:
    """Validate scalar or per-deme gene-copy counts."""
    if isinstance(value, int):
        if isinstance(value, bool):
            raise ValueError("N must be an integer")
        _require_integer("N", value, minimum=1)
        return (value,) * d
    values = tuple(value)
    if len(values) != d:
        raise ValueError("N must contain exactly d values")
    for index, item in enumerate(values):
        _require_integer(f"N[{index}]", item, minimum=1)
    return values


def _parse_convergence_combinator(value: Any) -> ConvergenceCombinator:
    """Parse the two supported convergence-combinator values."""
    parsed = _parse_string("convergence_combinator", value)
    if parsed == "any":
        return "any"
    if parsed == "all":
        return "all"
    raise ValueError("convergence_combinator must be 'any' or 'all'")


def _parse_convergence_statistic(value: Any) -> ConvergenceStatistic:
    """Parse a scalar or list convergence-statistic configuration value."""
    if isinstance(value, str):
        return _parse_string("convergence_statistic", value)
    if not isinstance(value, Sequence):
        raise ValueError("convergence_statistic must be a string or a list of strings")
    return tuple(
        _parse_string(f"convergence_statistic[{index}]", item)
        for index, item in enumerate(value)
    )


def _parse_deme_key(context: str, raw_key: Any, d: int) -> int:
    """Parse one 1-based deme identifier from a sparse migration-map key.

    Accepts either a native integer (the common YAML case) or a numeric
    string (JSON object keys are always strings), matching how ``p_0``'s
    allele keys are already coerced.
    """
    if isinstance(raw_key, bool):
        raise ValueError(f"{context} deme identifiers must be integers")
    try:
        deme = int(raw_key)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{context} deme identifiers must be integers") from error
    if not 1 <= deme <= d:
        raise ValueError(f"{context} deme {deme} is outside 1..{d}")
    return deme


def _parse_deme_weighting(value: Any) -> DemeWeighting:
    """Parse the two supported deme-weighting values."""
    parsed = _parse_string("deme_weighting", value)
    if parsed == "equal":
        return "equal"
    if parsed == "size":
        return "size"
    raise ValueError("deme_weighting must be 'equal' or 'size'")


def _parse_float(name: str, value: Any) -> float:
    """Parse a finite config float without accepting booleans."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be finite")
    return parsed


def _parse_initial_frequencies(value: Any) -> InitialFrequencies | None:
    """Parse the nested ``p_0`` config structure."""
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise ValueError("p_0 must be a list of demes")
    demes: list[tuple[Mapping[AlleleId, float], ...]] = []
    for deme_index, raw_deme in enumerate(value):
        if not isinstance(raw_deme, Sequence) or isinstance(raw_deme, str):
            raise ValueError(f"p_0[{deme_index}] must be a list of loci")
        loci: list[Mapping[AlleleId, float]] = []
        for locus_index, raw_locus in enumerate(raw_deme):
            if not isinstance(raw_locus, Mapping):
                raise ValueError(f"p_0[{deme_index}][{locus_index}] must be a mapping")
            frequencies: dict[AlleleId, float] = {}
            for raw_allele, raw_frequency in raw_locus.items():
                try:
                    allele_id = AlleleId(int(raw_allele))
                except (TypeError, ValueError) as error:
                    raise ValueError(
                        f"p_0 allele ID {raw_allele!r} must be an integer"
                    ) from error
                frequencies[allele_id] = _parse_float(
                    (f"p_0[{deme_index}][{locus_index}]" f"[{raw_allele!r}]"),
                    raw_frequency,
                )
            loci.append(frequencies)
        demes.append(tuple(loci))
    return tuple(demes)


def _parse_int(name: str, value: Any) -> int:
    """Parse a config integer without coercing floats or booleans."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return int(value)


def _parse_migrant_sampling(value: Any) -> MigrantSampling:
    """Parse the two supported migrant-sampling values."""
    parsed = _parse_string("migrant_sampling", value)
    if parsed == "continuous":
        return "continuous"
    if parsed == "stochastic":
        return "stochastic"
    raise ValueError("migrant_sampling must be 'continuous' or 'stochastic'")


def _parse_migration(value: Any, d: int) -> Migration:
    """Parse scalar, dense-matrix, sparse-map, or topology-sugar migration config."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return _parse_float("m", value)
    if isinstance(value, Mapping):
        # A deme identifier is always integer-like, so a bare "topology" or
        # "rate" key can never be a legitimate sparse-map deme — route a
        # mapping using either to the topology-sugar parser even if one of
        # the two required keys was left out, so a config that meant
        # {topology, rate} but mistyped it gets that mistake's own clear
        # error instead of a confusing "deme identifiers must be integers".
        if "topology" in value or "rate" in value:
            return _migration_from_topology(value, d)
        return _migration_from_sparse_map(value, d)
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise ValueError(
            "m must be a number, a d x d matrix, a sparse neighbor map, "
            "or a {topology, rate} mapping"
        )
    rows: list[tuple[float, ...]] = []
    for row_index, raw_row in enumerate(value):
        if not isinstance(raw_row, Sequence) or isinstance(raw_row, str):
            raise ValueError(f"m[{row_index}] must be a list")
        rows.append(
            tuple(
                _parse_float(f"m[{row_index}][{column_index}]", item)
                for column_index, item in enumerate(raw_row)
            )
        )
    return tuple(rows)


def _parse_mutation_model(value: Any) -> MutationModel:
    """Parse the two supported mutation-model values."""
    parsed = _parse_string("mutation_model", value)
    if parsed == "infinite_alleles":
        return "infinite_alleles"
    if parsed == "finite_alleles":
        return "finite_alleles"
    raise ValueError("mutation_model must be 'infinite_alleles' or 'finite_alleles'")


def _parse_population_size(value: Any) -> PopulationSize:
    """Parse scalar or per-deme gene-copy counts."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise ValueError("N must be an integer or a list of integers")
    return tuple(_parse_int(f"N[{index}]", item) for index, item in enumerate(value))


def _parse_string(name: str, value: Any) -> str:
    """Parse a nonempty config string."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _require_integer(name: str, value: int, minimum: int | None = None) -> None:
    """Validate an integer parameter."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")


def _require_probability(name: str, value: float) -> None:
    """Validate a finite probability."""
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")


def _validate_finite_allele_capacity(
    loci: tuple[LocusSpec, ...],
    initial_allele_count: int,
    initial_frequencies: InitialFrequencies | None,
) -> None:
    """Reject a finite-alleles configuration too small for its initial state.

    Every locus's finite state space (`finite_allele_capacity`) must hold
    whichever allele IDs generation zero actually uses there — the founding
    range ``0 .. initial_allele_count - 1``, or, when an explicit ``p_0`` is
    given, whatever specific IDs it names.
    """
    for locus_index, locus in enumerate(loci):
        capacity = finite_allele_capacity(locus.length)
        if initial_frequencies is not None:
            observed_ids = {
                int(allele_id)
                for deme in initial_frequencies
                for allele_id in deme[locus_index]
            }
            if any(allele_id >= capacity for allele_id in observed_ids):
                raise ValueError(
                    f"locus {locus.locus_id}: an initial allele ID exceeds "
                    f"the finite_alleles capacity ({capacity}) for length "
                    f"{locus.length}"
                )
        elif initial_allele_count > capacity:
            raise ValueError(
                f"locus {locus.locus_id}: initial_allele_count exceeds the "
                f"finite_alleles capacity ({capacity}) for length "
                f"{locus.length}"
            )
